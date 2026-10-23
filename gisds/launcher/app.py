"""图形启动器（tkinter）。

一个窗口把服务端管起来：

- 启停 REST 服务，实时看日志
- 打开网页控制台
- 用户管理：建号、改密码、启用/停用、改角色、删除
- 文件管理：浏览各空间的数据集，上传 / 下载 / 删除

文件浏览器既嵌在这里的「文件」页，也能单独开成独立窗口
（见 ``gisds.launcher.files``）。

只依赖标准库（tkinter + urllib），不需要 QGIS，也不装任何第三方包；
服务端仍然用 QGIS 的 Python 跑（见 项目根目录的「launcher.bat」）。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .api import (
    BASE,
    CFG,
    PROJECT_ROOT,
    api,
    api_json,
    do_login,
    load_config,
    login_with_service_key,
    set_token,
)
from .credentials import (
    EXPIRED,
    FRESH,
    STALE,
    USED,
    CredentialFreshness,
    parse_password,
    parse_username,
    server_note_path,
)
from .files import FileBrowser
from .tkguard import keep_root
from .theme import (
    BG,
    INK,
    INK2,
    INK3,
    MONO,
    MOSS,
    PANEL,
    PANEL2,
    RULE,
    RUST,
    apply_style,
)

# 项目根目录**直接用 api.py 里那一份**，不再自己算。
#
# 这里原来写的是 `Path(__file__).resolve().parent.parent` —— 少退了一级，
# 指向 `gisds/` 而不是项目根，于是：
#   * `run-qgis-python.bat` 找不到 → **「启动服务」按钮从来没起过服务**；
#   * `logs/` 建到了 `gisds/logs`；
#   * 子进程的 cwd 也错了（`-m gisds` 要靠它找包）。
# 路径只算一份、且从同一处 import，两个模块就不会再各算各的。
LOG_DIR = PROJECT_ROOT / "logs"


def _warn(message: str) -> None:
    """把异常报到 stderr。

    启动器里有几处没法把错误显示到界面上（控件写不进去、日志控件本身坏了），
    至少要留个痕迹 —— 静悄悄地失败比报错更难查（本项目已经吃过两次这种亏：
    一次是 NameError 被吞掉，一次是路径判断写错却全程没报错）。
    """
    try:
        print(f"[gisds-launcher] {message}", file=sys.stderr)
    except OSError:
        pass        # 连 stderr 都写不进去，那就真没地方说了


# --------------------------------------------------------------------- 服务进程

# 就绪探针用的端点。
#
# 服务起来之后还要初始化 QGIS 运行时（十几秒），这段时间**进程活着但接口不能用**。
# 启动器原来的状态只看子进程在不在，所以一按「启动服务」就显示「● 运行中」，
# 用户这时点「打开网页控制台」会撞上 /api/datasets —— 它会触发一次全目录扫描、
# 阻塞在 QGIS 工作线程上，浏览器就白屏等着，比报错还难判断。
#
# 服务端把「存活」和「就绪」拆成了两个端点，这里用**就绪**那个。
READY_PATH = "/readyz"

# 状态 → 顶栏显示。分三档，别把「进程活着」当成「能干活」。
STATE_TEXT = {
    "stopped": "● 已停止",
    "starting": "● 启动中…",
    "ready": "● 运行中",
}


def _probe_worker(results: "queue.Queue") -> None:
    """后台探测：问一次 /readyz，把结果丢进队列。

    **这个函数只能拿到队列，拿不到 Service** —— 这不是洁癖，是硬约束：
    后台线程的栈帧只要引用了 Service，就会一路引用到 Launcher 的绑定方法
    → Launcher → Tk 窗口。线程结束时这一串会在**后台线程**里被析构，
    于是 Tcl 解释器在错误的线程里被删除 →
    「Tcl_AsyncDelete: async handler deleted by the wrong thread」，进程直接崩。

    所以只往里传一个普通队列，结果由主线程取走（见 Service.drain_probe）。
    """
    ready = False
    try:
        status, _ = api("GET", READY_PATH, timeout=2)
        ready = status == 200
    except (OSError, ValueError):
        # 连不上 / 超时 → 还没就绪。这不是「出错」，是「还没好」——
        # 服务启动期间端口还没监听，每次探测都会走到这里，所以**不能**打警告。
        ready = False
    except Exception as exc:
        # 别的异常就不是「还没好」了，得让人看见
        _warn(f"就绪探测异常：{exc!r}")
        ready = False
    try:
        results.put(ready)
    except Exception as exc:
        _warn(f"探测结果入队失败：{exc!r}")


class Service:
    """管理 REST 服务子进程。"""

    def __init__(self, on_log):
        self.on_log = on_log
        self.proc: subprocess.Popen | None = None
        self._buffer: queue.Queue = queue.Queue()
        self._probe_results: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._ready = False        # 最近一次探测：就绪了吗
        self._probe_busy = False   # 有没有探测在飞
        self._last_probe = 0.0

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def state(self) -> str:
        """当前状态：stopped / starting / ready。

        判据是「**能不能干活**」，不是「进程在不在」：
        进程活着但还没初始化完 QGIS → starting（不是 ready）。
        """
        if self.is_running():
            return "ready" if self._ready else "starting"
        # 我们起的进程没了。别人起的服务另说 —— 探针说就绪就还算就绪。
        return "ready" if self._ready else "stopped"

    def maybe_probe(self, min_interval: float = 1.0) -> None:
        """该问就问一次服务就绪没有。

        探测要发 HTTP 请求，**不能在 Tk 主线程里做**（会把界面卡住），
        所以放后台线程，结果回队列、由主线程取（见 _probe_worker 的说明）。
        """
        now = time.time()
        with self._lock:
            stale = (now - self._last_probe) > 5.0    # 上一次的探测飞丢了
            if ((self._probe_busy and not stale)
                    or (now - self._last_probe) < min_interval):
                return
            self._probe_busy = True
            self._last_probe = now
        # 只把队列传进去 —— 传 self 就会把 Tk 窗口拖进后台线程
        threading.Thread(target=_probe_worker, args=(self._probe_results,),
                         daemon=True).start()

    def drain_probe(self) -> None:
        """（主线程）把后台探测的结果取回来。"""
        while True:
            try:
                self._ready = self._probe_results.get_nowait()
            except queue.Empty:
                return
            self._probe_busy = False

    def start(self):
        if self.is_running():
            return
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        bat = PROJECT_ROOT / "run-qgis-python.bat"
        if not bat.is_file():
            self.on_log(f"找不到启动脚本：{bat}")
            return
        self.proc = subprocess.Popen(
            ["cmd.exe", "/c", str(bat), "-m", "gisds", "serve"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )
        threading.Thread(target=self._pump, daemon=True).start()
        self.on_log("服务已启动，正在等待就绪 …")

    def _pump(self):
        proc = self.proc
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            # **只入队，不碰界面。**
            #
            # 这一行原来直接 `self.on_log(line)`，而 on_log 最终会去 insert 日志、
            # 建口令提示条、pack 控件 —— 全是 Tk 调用，而 Tk **不是线程安全的**：
            # 后台线程动控件，轻则偶发地不生效（提示条时有时无、口令标签没销毁），
            # 重则整个程序崩掉（0xC0000005）。而且这种问题**隔离环境复现不了**，
            # 只有机器忙起来（并行跑全套自检）才偶发 —— 最难查的那一类。
            #
            # 改成：后台线程只负责收行，主线程在 _tick 里取出来再显示。
            self._buffer.put(line.rstrip())

    def drain(self) -> list[str]:
        """把攒着的日志行取出来。**必须由主线程调用**（里面才有 Tk 调用）。"""
        out: list[str] = []
        while True:
            try:
                out.append(self._buffer.get_nowait())
            except queue.Empty:
                break
        return out

    def stop(self):
        if not self.is_running():
            self.proc = None
            with self._lock:
                self._ready = False
            return
        self.on_log("正在停止服务 …")
        try:
            self.proc.terminate()
            self.proc.wait(timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            # 进程已经不在了，或者等超时 → 补一刀
            try:
                self.proc.kill()
            except Exception as exc:
                _warn(f"强制结束服务进程失败：{exc!r}")
        self.proc = None
        with self._lock:
            self._ready = False
        self.on_log("服务已停止")


# --------------------------------------------------------------------- 界面

class Launcher:
    def __init__(self):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk

        self.root = keep_root(tk.Tk())
        self.root.title("Browser QGIS 启动器")
        self.root.geometry("980x640")
        self.root.minsize(860, 560)
        self.root.configure(bg=BG)
        self._apply_icon()

        self.log_lines: list[str] = []
        self.service = Service(self.log)
        # 首次启动时服务会把初始口令打进日志，捞出来直接用
        self.first_username: str | None = None
        self.first_password: str | None = None
        self._first_banner = None
        self._first_pwd_label = None
        self._first_head = None       # 标题标签（收起后改成提示文案）
        self._first_time = None       # 「生成于 HH:MM」标签
        self._first_buttons = None    # 按钮组（收起时整体销毁）
        self._freshness: CredentialFreshness | None = None
        self._first_poll = None       # 轮询用的 after 句柄
        self._tick_handle = None      # 状态刷新定时器的 after 句柄
        self._popups: list = []       # 开出去的文件浏览器窗口（关窗时要先关它们）

        self._style()
        self._build()
        # 句柄**一定要记下来** —— 记丢了，关闭窗口时就取消不掉这个定时器，
        # 它会在窗口销毁后到点触发，而它的 Tcl 命令已经没了 →
        # Tk 报「invalid command name "..._tick"」。
        self._tick_handle = self.root.after(200, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------------------------------------------------------- 样式

    def _style(self):
        apply_style(self.ttk, self.root)

    # ---------------------------------------------------------------- 布局

    def _build(self):
        tk = self.tk
        ttk = self.ttk

        # 顶栏
        head = ttk.Frame(self.root)
        head.pack(fill="x", padx=14, pady=(12, 8))
        ttk.Label(head, text="Browser QGIS", style="Title.TLabel").pack(side="left")
        self.status_label = ttk.Label(head, text="● 已停止", style="Muted.TLabel")
        self.status_label.pack(side="left", padx=12)
        self.who_label = ttk.Label(head, text="未登录", style="Muted.TLabel")
        self.who_label.pack(side="left", padx=(0, 12))
        ttk.Button(head, text="登录", command=self.on_login).pack(side="right")
        ttk.Button(head, text="服务密钥", command=self.on_service_key).pack(side="right")
        ttk.Button(head, text="文件浏览器", command=self.open_file_window).pack(side="right")
        self.browser_btn = ttk.Button(head, text="打开网页控制台", command=self.open_browser)
        self.browser_btn.pack(side="right")
        self.start_btn = ttk.Button(head, text="启动服务", style="Accent.TButton", command=self.toggle_service)
        self.start_btn.pack(side="right", padx=6)

        # 主体
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        # 左：用户
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tabs = ttk.Notebook(body)
        tabs.pack(side="right", fill="both", expand=True)

        # ---- 用户页
        users_card = ttk.Frame(left, style="Card.TFrame")
        users_card.pack(fill="both", expand=True)
        self.users_card = users_card
        bar = ttk.Frame(users_card, style="Card.TFrame")
        bar.pack(fill="x", padx=8, pady=(8, 4))
        self.users_bar = bar
        ttk.Label(bar, text="用户", style="Head.TLabel").pack(side="left")
        ttk.Button(bar, text="新建", command=self.on_new_user).pack(side="right")
        ttk.Button(bar, text="刷新", command=self.load_users).pack(side="right", padx=4)
        ttk.Button(bar, text="启用/停用", command=self.on_toggle_user).pack(side="right", padx=4)
        ttk.Button(bar, text="改密码", command=self.on_reset_password).pack(side="right", padx=4)
        ttk.Button(bar, text="改角色", command=self.on_change_role).pack(side="right", padx=4)
        ttk.Button(bar, text="删除", command=self.on_delete_user).pack(side="right", padx=4)

        cols = ("username", "role", "status", "scope", "created")
        self.user_tree = ttk.Treeview(users_card, columns=cols, show="headings", selectmode="browse")
        for key, text, width in (
            ("username", "用户名", 150),
            ("role", "角色", 70),
            ("status", "状态", 70),
            ("scope", "个人空间", 140),
            ("created", "创建时间", 150),
        ):
            self.user_tree.heading(key, text=text)
            self.user_tree.column(key, width=width, anchor="w")
        self.user_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # ---- 文件页（文件浏览器面板，可另开独立窗口）
        files_card = ttk.Frame(tabs, style="Card.TFrame")
        tabs.add(files_card, text="  文件  ")
        self.file_browser = FileBrowser(
            files_card, tk, ttk, log=self.log, on_login=self._after_login
        )
        self.file_browser.pack(fill="both", expand=True)

        # ---- 日志页
        log_card = ttk.Frame(tabs, style="Card.TFrame")
        tabs.add(log_card, text="  日志  ")
        log_bar = ttk.Frame(log_card, style="Card.TFrame")
        log_bar.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(log_bar, text="服务日志", style="Head.TLabel").pack(side="left")
        ttk.Button(log_bar, text="清空", command=self.clear_log).pack(side="right")
        self.log_text = tk.Text(log_card, bg="#FBFCFA", fg=INK, font=(MONO, 9),
                                relief="flat", highlightthickness=1, highlightbackground=RULE,
                                wrap="none", state="disabled")
        self.log_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # ---------------------------------------------------------------- 日志

    def log(self, line: str):
        if not line:
            return
        self.log_lines.append(line)
        if len(self.log_lines) > 2000:
            self.log_lines = self.log_lines[-1500:]
        self._catch_first_credentials(line)
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception as exc:
            # 日志控件写不进去时，界面会「静悄悄地什么都不发生」——
            # 那比报错更难查，所以至少报到 stderr。
            _warn(f"日志控件写入失败：{exc!r}")

    # ------------------------------------------------- 首次启动的初始口令
    #
    # 这条提示条显示的是服务自举那一刻生成的初始口令。它**可能已经作废**：
    # 用户在别处改过口令、或者用 gisds set-password 重置过。
    # 一条作废的口令摆在屏幕上、还配一个「直接登录」按钮，点了只会 401 ——
    # 跟服务端那个「文件在骗人」是同一类故障，同一个原则：
    # **任何显示凭据的地方都不能留着过期的。**
    #
    # 判据在 credentials.py：服务端始终让那份备忘记着当前有效口令
    # （改密时同步更新），所以「那份备忘还记不记着这个口令」就是权威信号；
    # 再加「放太久就收起」兜底。

    def _catch_first_credentials(self, line: str):
        """服务自举时会把初始口令打进日志，这里把它捞出来直接用。

        目的：双击 launcher.bat -> 启动服务 -> 一键登录，不用去翻日志文件。
        只在服务**由这个启动器起**的时候有效（别的窗口起的服务捞不到），
        拿不到就当没这回事，不影响任何功能。
        """
        if self.first_password:
            return
        try:
            if not self.first_username:
                name = parse_username(line)
                if name:
                    self.first_username = name
            pwd = parse_password(line)
            if pwd:
                self.first_password = pwd
                self._freshness = CredentialFreshness(
                    username=self.first_username,
                    password=pwd,
                    note_path=server_note_path(),
                )
                self._show_first_credentials()
                self._start_first_poll()
        except Exception as exc:
            self.log(f"[调试] 解析初始口令失败：{exc!r}")

    def _first_time_text(self) -> str:
        caught = getattr(self._freshness, "caught_at", None)
        if not caught:
            return ""
        return f"生成于 {time.strftime('%H:%M', time.localtime(caught))}"

    def _show_first_credentials(self):
        ttk = self.ttk
        try:
            if not self._first_banner:
                self._first_banner = ttk.Frame(self.users_card, style="Card.TFrame")
                self._first_head = ttk.Label(self._first_banner, text="首次启动：管理员初始口令",
                                             style="CardHead.TLabel")
                self._first_head.pack(side="left", padx=(8, 6))
                self._first_time = ttk.Label(self._first_banner, text=self._first_time_text(),
                                             style="CardMuted.TLabel")
                self._first_time.pack(side="left", padx=(0, 8))
                self._first_pwd_label = ttk.Label(self._first_banner, text=self.first_password or "",
                                                  style="Card.TLabel")
                self._first_pwd_label.pack(side="left", padx=(0, 8))
                self._first_buttons = ttk.Frame(self._first_banner, style="Card.TFrame")
                self._first_buttons.pack(side="left")
                ttk.Button(self._first_buttons, text="复制",
                           command=self.copy_first_password).pack(side="left")
                ttk.Button(self._first_buttons, text="直接登录",
                           command=self.login_with_first_password).pack(side="left", padx=4)
                ttk.Button(self._first_buttons, text="收起",
                           command=self.hide_first_credentials).pack(side="left")
            self._first_banner.pack(fill="x", padx=8, pady=(8, 0), before=self.users_bar)
        except Exception as exc:
            self.log(f"[调试] 显示初始口令提示条失败：{exc!r}")

    # ------------------------------------------------- 提示条的新鲜度

    def _start_first_poll(self):
        """定期看一眼这条口令还有效吗。"""
        self._stop_first_poll()
        try:
            self._first_poll = self.root.after(1000, self._poll_first_credentials)
        except Exception as exc:
            self.log(f"[调试] 启动初始口令巡检失败：{exc!r}")

    def _stop_first_poll(self):
        if self._first_poll is None:
            return
        try:
            self.root.after_cancel(self._first_poll)
        except self.tk.TclError:
            pass        # 句柄已经触发过了，取消不掉是正常的
        self._first_poll = None

    def _poll_first_credentials(self):
        """口令可能已经作废了 —— 过期就别再显示。"""
        self._first_poll = None
        if not self._freshness:
            return
        try:
            verdict = self._freshness.evaluate()
        except Exception as exc:
            self.log(f"[调试] 检查初始口令有效性失败：{exc!r}")
            verdict = FRESH

        if verdict == STALE:
            self._collapse_first_credentials("口令已变更，请用新口令登录")
            return
        if verdict == EXPIRED:
            minutes = int((self._freshness.ttl_seconds or 0) // 60)
            self._collapse_first_credentials(f"提示条已收起（超过 {minutes} 分钟未使用）")
            return
        if verdict == USED:
            self._collapse_first_credentials("")
            return
        self._start_first_poll()

    def _collapse_first_credentials(self, message: str = ""):
        """把口令从屏幕上撤掉 —— 有话说清楚为什么没了。"""
        self._stop_first_poll()
        try:
            if not self._first_banner:
                return
            # 不管因为什么收起，口令本身和按钮都得消失。
            #
            # 先把引用清掉、再销毁控件 —— 顺序反过来的话，销毁过程中任何一个控件
            # 抛异常（控件已被父容器连带销毁时就会），就会留下「引用还指着口令标签」
            # 的半截状态：那既让自检判不出「口令已经撤掉了」，也可能让明文口令
            # 继续挂在屏幕上 —— 而这个模块存在的意义就是不让它挂着。
            widgets = (self._first_pwd_label, self._first_buttons, self._first_time)
            self._first_pwd_label = None
            self._first_buttons = None
            self._first_time = None
            for widget in widgets:
                if widget is None:
                    continue
                try:
                    widget.destroy()
                except Exception as exc:
                    # 控件可能已经被父容器连带销毁了，引用已经清掉，不必刷屏
                    self.log(f"[调试] 销毁初始口令控件失败：{exc!r}")
            if message:
                # 只留一句说明，让人知道为什么没了
                if self._first_head is not None:
                    self._first_head.configure(text=message, style="CardWarn.TLabel")
                self.log(f"初始口令提示条：{message}")
            else:
                # 整条撤掉（用户自己点的「收起」，或已经登录成功）
                self._first_banner.pack_forget()
        except Exception as exc:
            self.log(f"[调试] 收起初始口令提示条失败：{exc!r}")

    def hide_first_credentials(self):
        """用户自己收起这条提示条。"""
        self._collapse_first_credentials("")

    def copy_first_password(self):
        if not self.first_password:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.first_password)
        self.log(f"已复制初始口令到剪贴板：{self.first_password}")

    def login_with_first_password(self):
        """用刚生成的初始口令直接登录，省得再输一遍。"""
        if not self.first_password:
            return
        username = self.first_username or CFG.get("admin_username", "admin")
        try:
            data = api_json("POST", "/api/auth/login",
                            body={"username": username, "password": self.first_password})
        except PermissionError as exc:
            # 服务端拒了这个口令 → 它已经作废了（多半是有人改过）。
            # 继续把它挂在屏幕上，只会让人再点一次、再 401 一次。
            if self._freshness:
                self._freshness.mark_stale()
            self._collapse_first_credentials("口令已变更，请用新口令登录")
            self.log(f"用初始口令登录失败（口令已变更）：{exc}")
            return
        except Exception as exc:
            # 网络断了、服务没起来 —— 口令本身未必作废，先别撤
            self.log(f"用初始口令登录失败：{exc}")
            return
        set_token(data.get("token"))
        self.log(f"已用初始口令登录：{username}")
        if self._freshness:
            self._freshness.mark_used()
        self._collapse_first_credentials("")
        self._after_login(data.get("user", {}) or {})

    def clear_log(self):
        self.log_lines.clear()
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except Exception as exc:
            _warn(f"清空日志失败：{exc!r}")

    # ---------------------------------------------------------------- 服务

    def toggle_service(self):
        if self.service.is_running():
            self.service.stop()
        else:
            self.service.start()

    def open_browser(self):
        webbrowser.open(BASE + "/")

    def open_file_window(self):
        """另开一个独立的文件浏览器窗口。"""
        from .files import open_file_window

        try:
            win = open_file_window(self.root)
        except Exception as exc:
            self.log(f"打开文件浏览器失败：{exc}")
            self._info(f"打开文件浏览器失败：{exc}")
            return None
        # 记下来：关启动器时要把这些弹窗**先**关掉 —— 它们各自排了定时器，
        # 跟着父窗口一起被销毁的话，定时器会变成孤儿（见 files.py 的 close()）。
        self._popups.append(win)
        return win

    # ---------------------------------------------------------------- 登录

    def on_service_key(self):
        """用**服务级密钥**登录。

        多机部署时管理员人在另一台机器上，忘了 admin 密码就登不进来
        （口令备忘在数据机上，远程拿不到）。服务端 config.json 里的
        api_key 相当于服务密钥后门，这个按钮就是那条远程救砖的路。
        """
        from tkinter import simpledialog

        key = simpledialog.askstring(
            "服务密钥", "请输入服务端 config.json 里的 api_key", show="*", parent=self.root)
        if not key:
            return
        user = login_with_service_key(key, self.log)
        if not user:
            self._info("服务密钥无效")
            return
        self._after_login(user)

    def on_login(self):
        """管理员登录。登录后才能管用户、管文件。"""
        user = do_login(self.root, self.log)
        if not user:
            return
        self._after_login(user)

    def _after_login(self, user):
        """登录成功后刷新各处状态（顶栏身份、用户列表、文件列表）。"""
        self.who_label.configure(text=f"{user.get('display_name') or user.get('username')}（{user.get('role')}）")
        self.who_label.configure(style="TLabel")
        self.load_users()
        self.file_browser.refresh()

    def _tick(self):
        try:
            # 服务日志是从后台线程收来的，这里在**主线程**取出来显示 ——
            # Tk 只能在主线程动（见 Service._pump 的说明）。
            for line in self.service.drain():
                self.log(line)
            # 就绪探测的结果由主线程取（见 Service.drain_probe）
            self.service.drain_probe()
            self.service.maybe_probe()
            state = self.service.state()
            running = self.service.is_running()
            self.status_label.configure(
                text=STATE_TEXT[state],
                style="TLabel" if state != "stopped" else "Muted.TLabel",
            )
            self.start_btn.configure(text="停止服务" if running else "启动服务")
            # 还没就绪就别把人送进白屏（/api/datasets 会阻塞在 QGIS 工作线程上）。
            # 「已停止」时不禁用 —— 可能是别人起的服务，照旧让人能打开。
            self.browser_btn.configure(
                state="disabled" if state == "starting" else "normal")
        except Exception as exc:
            _warn(f"刷新状态失败：{exc!r}")
        finally:
            # 一定要继续排下一次，否则状态栏再也不动了
            try:
                self._tick_handle = self.root.after(200, self._tick)
            except self.tk.TclError:
                self._tick_handle = None      # 窗口已经销毁了，没什么可排的

    # ---------------------------------------------------------------- 用户

    def load_users(self):
        for item in self.user_tree.get_children():
            self.user_tree.delete(item)
        try:
            data = api_json("GET", "/api/admin/users")
        except Exception as exc:
            self.log(f"读取用户失败：{exc}")
            return
        for u in data.get("users", []):
            created = u.get("created_at") or 0
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(created)) if created else "—"
            self.user_tree.insert(
                "", "end",
                iid=u["user_id"],
                values=(u["username"], u["role"], u["status"], u.get("scope", ""), when),
            )
        self.log(f"用户列表已刷新：{data.get('count', 0)} 个账号")

    def _selected_user(self):
        sel = self.user_tree.selection()
        if not sel:
            self.log("请先在列表里选中一个用户")
            return None
        uid = sel[0]
        for u in self._users_cache():
            if u["user_id"] == uid:
                return u
        return None

    def _users_cache(self):
        try:
            return api_json("GET", "/api/admin/users").get("users", [])
        except Exception as exc:
            _warn(f"取用户列表失败：{exc!r}")
            return []

    def _ask(self, title: str, prompt: str, initial: str = "", show: str = "normal"):
        from tkinter import simpledialog

        return simpledialog.askstring(title, prompt, initialvalue=initial, show=show, parent=self.root)

    def on_new_user(self):
        name = self._ask("新建用户", "用户名（至少 3 个字符）")
        if not name:
            return
        pwd = self._ask("新建用户", "初始密码（至少 6 位）", show="*")
        if not pwd:
            return
        try:
            api_json("POST", "/api/admin/users",
                     body={"username": name, "password": pwd, "role": "user"})
        except Exception as exc:
            self.log(f"新建用户失败：{exc}")
            return
        self.log(f"已创建用户 {name}")
        self.load_users()

    def on_reset_password(self):
        u = self._selected_user()
        if not u:
            return
        pwd = self._ask("改密码", f"给 {u['username']} 设置新密码（至少 6 位）", show="*")
        if not pwd:
            return
        try:
            api_json("PATCH", f"/api/admin/users/{u['user_id']}", body={"password": pwd})
        except Exception as exc:
            self.log(f"改密码失败：{exc}")
            return
        self.log(f"已重置 {u['username']} 的密码")

    def on_change_role(self):
        u = self._selected_user()
        if not u:
            return
        role = self._ask("改角色", "新角色：admin 或 user", u["role"])
        if role not in ("admin", "user"):
            return
        try:
            api_json("PATCH", f"/api/admin/users/{u['user_id']}", body={"role": role})
        except Exception as exc:
            self.log(f"改角色失败：{exc}")
            return
        self.log(f"{u['username']} 的角色已改为 {role}")
        self.load_users()

    def on_toggle_user(self):
        u = self._selected_user()
        if not u:
            return
        status = "disabled" if u["status"] == "active" else "active"
        try:
            api_json("PATCH", f"/api/admin/users/{u['user_id']}", body={"status": status})
        except Exception as exc:
            self.log(f"操作失败：{exc}")
            return
        self.log(f"{u['username']} 已{'停用' if status == 'disabled' else '启用'}")
        self.load_users()

    def on_delete_user(self):
        u = self._selected_user()
        if not u:
            return
        if not self._confirm(f"确定删除用户「{u['username']}」？\n其个人空间里的数据不会自动删除。"):
            return
        try:
            api_json("DELETE", f"/api/admin/users/{u['user_id']}")
        except Exception as exc:
            self.log(f"删除失败：{exc}")
            return
        self.log(f"已删除用户 {u['username']}")
        self.load_users()

    def _confirm(self, message: str) -> bool:
        from tkinter import messagebox

        return messagebox.askyesno("确认", message, parent=self.root)

    def _info(self, message: str):
        from tkinter import messagebox

        messagebox.showinfo("提示", message, parent=self.root)

    # ---------------------------------------------------------------- 关闭

    def _apply_icon(self):
        """给窗口装上软件图标（assets/icon.ico，多尺寸 DIB 格式）。"""
        try:
            ico = Path(__file__).resolve().parents[2] / "assets" / "icon.ico"
            if ico.is_file():
                self.root.iconbitmap(str(ico))
        except Exception as exc:
            _warn(f"图标装不上（不影响启动器本身）：{exc!r}")

    def on_close(self):
        if self.service.is_running():
            if not self._confirm("服务还在运行。关闭启动器会一并停掉服务，确定吗？"):
                return
            self.service.stop()
        # 弹窗要先关（它们各自排了定时器），再取消状态刷新定时器，
        # 最后才销毁主窗口 —— 顺序反了会留下孤儿定时器。
        for win in list(self._popups):
            try:
                win.close()
            except Exception as exc:
                _warn(f"关闭文件浏览器窗口失败：{exc!r}")
        self._popups.clear()
        self.cancel_tick()
        self.root.destroy()

    def cancel_tick(self):
        """取消还没触发的状态刷新定时器。

        窗口销毁之后，那个 `after` 事件照样会到点触发，而它的 Tcl 命令
        已经随窗口一起没了 → Tk 报 `invalid command name "..._tick"`。
        关闭前先取消掉，就不会有这种噪音（自检里建了又毁窗口，噪音尤其多）。
        """
        handle = self._tick_handle
        self._tick_handle = None
        if handle is None:
            return
        try:
            self.root.after_cancel(handle)
        except self.tk.TclError:
            pass        # 已经触发过了，取消不掉是正常的


def main() -> int:
    app = Launcher()
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
