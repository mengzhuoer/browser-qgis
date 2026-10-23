# -*- coding: utf-8 -*-
"""启动器回归检查：主窗口 + 独立文件浏览器窗口。

分两段，**两段都不依赖生产服务在线，也不碰生产用户库**：
1. GUI 段 —— 自起临时服务（共享空间读生产的以便看到示例数据，用户库独立），
   真起窗口，看下拉、文件列表、弹窗；
2. REST 段 —— 自起另一个临时服务（数据目录也独立），验启动器的 HTTP 层：
   登录 / 拉用户 / 上传。

用系统 Python 跑（QGIS 自带的 Python 没打包 tkinter）::

    C:\\Users\\<you>\\AppData\\Local\\Programs\\Python\\Python311\\python.exe tests/check_launcher.py
"""
import sys
import time
from pathlib import Path

# 输出统一按 UTF-8。
#
# 系统 Python 3.11 在 Windows 上按 **GBK** 写 stdout，而调用方（PowerShell 等）
# 按自己的 OutputEncoding 解码 → 对不上就整段乱码。后果不只是难看：按
# 「结果：N 通过」统计断言数会**漏掉这一整个脚本**（QGIS 那批脚本是 UTF-8，
# 所以只有用系统 Python 的这两个受影响）—— 看着像断言没跑，其实是跑过了。
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass        # 句柄不支持 reconfigure（或者压根是 None）就算了

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import tkinter as tk
from tkinter import ttk

from gisds.launcher import api as lap
from gisds.launcher.app import Launcher
from gisds.launcher.files import FileBrowserWindow

fails = []
passed = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查（第 6 条的下半句）。
EXPECTED_CHECKS = 48


def check(label, ok, detail=""):
    global passed
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if ok:
        passed += 1
    else:
        fails.append(label)


def gui_checks(server):
    """对着自起的临时服务真起窗口。"""
    print("== 登录 ==")
    old_base = lap.BASE
    lap.BASE = server.base
    lap.set_token(None)
    data = lap.api_json("POST", "/api/auth/login",
                        body={"username": "admin", "password": "admin123"})
    lap.set_token(data.get("token"))
    check("临时服务能登录", bool(data.get("token")))

    print("\n== 独立文件浏览器窗口 ==")
    root = tk.Tk()
    win = FileBrowserWindow(root)
    root.update()
    check("窗口标题", root.title() == "Browser QGIS · 文件浏览器", root.title())
    win.browser.refresh()
    root.update()
    b = win.browser
    check("空间下拉有值", b.scope_box.get() != "", repr(b.scope_box.get()))
    # 管理员能看到的空间有三类：「全部」+ 共享空间 + **自己的个人空间**
    # （用户上传的东西不一定落在共享空间，只列共享的话会「我传的图不见了」）。
    # 所以这里钉语义，不要钉死个数 —— 普通用户没有个人空间，个数本来就不同。
    scope_vals = list(b.scope_box["values"])
    check("空间下拉列出全部可见空间（全部 + 共享 + 本人的个人空间）",
          bool(scope_vals) and scope_vals[0] == "全部"
          and "shared" in scope_vals
          and any(str(v).startswith("user:") for v in scope_vals),
          str(scope_vals))
    check("身份已显示", "admin" in b.who_label.cget("text"), b.who_label.cget("text"))
    check("文件列表已加载", len(b.file_tree.get_children()) >= 1,
          f"{len(b.file_tree.get_children())} 行")

    print("\n== 启动器主窗口 ==")
    launcher = Launcher()
    launcher.root.update()
    check("标题", launcher.root.title() == "Browser QGIS 启动器", launcher.root.title())
    check("顶栏有「文件浏览器」按钮",
          any(w.winfo_class() == "TButton" and w.cget("text") == "文件浏览器"
              for w in launcher.root.winfo_children()[0].winfo_children()))
    check("文件页面板已装载", launcher.file_browser is not None)
    check("面板能 pack（之前报错的点）", hasattr(launcher.file_browser, "pack"))
    check("有用户表格", getattr(launcher, "user_tree", None) is not None)

    print("\n== 弹出独立窗口 ==")
    popup = launcher.open_file_window()
    launcher.root.update()
    check("弹出成功", popup is not None)
    # 下面三条**不许被条件跳过**：弹窗没弹出来时，它们要**明确失败**，
    # 而不是跟着 `if popup:` 一起不跑 —— 少跑几条断言，汇总数会跟着变，
    # 那样就看不出「到底哪几条没验」（第 6 条的下半句：别让「没报错」冒充「对」）。
    if popup:
        time.sleep(1.2)
        launcher.root.update()
    title = popup.root.title() if popup else ""
    rows = len(popup.browser.file_tree.get_children()) if popup else 0
    scope = popup.browser.scope_box.get() if popup else ""
    check("弹窗标题", title == "Browser QGIS · 文件浏览器", title)
    check("弹窗内数据已加载", rows >= 1, f"{rows} 行")
    check("弹窗内空间下拉有值", scope != "", repr(scope))

    # 弹窗要**先**关掉（它自己排了刷新定时器），再关主窗口 ——
    # 顺序反了会留下孤儿定时器，窗口销毁后它照样到点触发。
    if popup:
        popup.close()
    launcher.cancel_tick()
    root.destroy()
    launcher.root.destroy()
    lap.BASE = old_base


def rest_checks():
    """启动器的 HTTP 层：对着自起的临时服务验，不碰生产服务。"""
    print("\n== 启动器只依赖标准库 ==")
    check("launcher.api 没引入 httpx", "httpx" not in dir(lap))

    sys.path.insert(0, str(PROJECT_ROOT / "tests"))
    try:
        from server_helper import TestServer, _free_port
    except Exception as exc:
        print(f"  跳过 REST 段（拿不到 TestServer）：{exc}")
        return

    import json
    import tempfile
    import urllib.request

    tmp = Path(tempfile.mkdtemp(prefix="gisds_launcher_"))
    old_base = lap.BASE
    with TestServer(
        port=_free_port(8855), data_root=tmp, extra_env={"GISDS_ADMIN_PASSWORD": "admin123"}
    ) as server:
        # 启动器连的是 api 模块里的 BASE，改那个才生效
        lap.BASE = server.base
        lap.set_token(None)

        req = urllib.request.Request(
            server.base + "/api/auth/login",
            data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
        check("临时服务能登录", bool(payload.get("token")))

        lap.set_token(payload["token"])
        users = lap.api_json("GET", "/api/admin/users")
        check("启动器能拉用户列表", "users" in (users or {}), str(users)[:120])

        content = json.dumps({
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "properties": {"n": 1},
                          "geometry": {"type": "Point", "coordinates": [112.48, 39.685]}}],
        }).encode()
        status, raw = lap.api(
            "POST", "/api/uploads", params={"scope": "shared"},
            files=[("files", "probe.geojson", content)],
        )
        check("启动器能上传文件", status == 200, f"{status} {raw[:160]}")
        # 同理：上传失败时这一条要**明确失败**，不能跟着 `if status == 200` 一起跳过
        payload = {}
        if status == 200:
            try:
                payload = json.loads(raw) or {}
            except Exception as exc:
                payload = {"_parse_error": repr(exc)}
        check("上传返回数据集", payload.get("uploaded") == 1, raw[:160])

    lap.BASE = old_base


def path_checks():
    """项目根目录不能算错。

    这里踩过：启动器里 `PROJECT_ROOT` 少退了一级，指向 `gisds/` 而不是项目根，
    于是 `run-qgis-python.bat` 找不到 → **「启动服务」按钮从来没起过服务**，
    `logs/` 也建到了 `gisds/logs`。

    之所以没人发现：自检都是外部起好服务再连，启动器自己的启动路径从没被走过。
    """
    from gisds.launcher.app import LOG_DIR, PROJECT_ROOT

    check("项目根目录指向真正的项目根",
          (PROJECT_ROOT / "run-qgis-python.bat").is_file()
          and (PROJECT_ROOT / "config.json").is_file()
          and (PROJECT_ROOT / "gisds").is_dir(),
          str(PROJECT_ROOT))
    check("日志目录在项目根下面，不在包目录里",
          LOG_DIR.parent == PROJECT_ROOT and LOG_DIR.name == "logs",
          str(LOG_DIR))


def readiness_checks():
    """就绪探针：进程活着 ≠ 能干活。

    服务起来之后还要初始化 QGIS 运行时（十几秒），这段时间进程活着但接口不能用。
    启动器原来的状态只看子进程在不在，一按「启动服务」就显示「● 运行中」——
    用户这时点「打开网页控制台」会撞上阻塞的 /api/datasets，浏览器白屏等着，
    比报错还难判断。所以状态必须分三档。
    """
    from gisds.launcher.app import READY_PATH, STATE_TEXT, Service

    class FakeAlive:
        def poll(self):
            return None

    svc = Service(on_log=lambda line: None)
    check("没有子进程 → 「已停止」", svc.state() == "stopped", svc.state())
    svc.proc = FakeAlive()
    check("进程活着但没就绪 → 「启动中」", svc.state() == "starting", svc.state())
    svc._ready = True
    check("就绪之后 → 「运行中」", svc.state() == "ready", svc.state())
    check("三种状态都有对应文案",
          set(STATE_TEXT) == {"stopped", "starting", "ready"}, str(STATE_TEXT))
    check("探针问的是「就绪」而不是「存活」", READY_PATH == "/readyz", READY_PATH)

    # 界面上：没就绪时状态栏不能说「运行中」，「打开网页控制台」也得禁用
    # （免得把人送进白屏）。
    old_base = lap.BASE
    lap.BASE = "http://127.0.0.1:1"          # 探不到 → 一直「没就绪」
    launcher = Launcher()
    launcher.root.withdraw()
    try:
        launcher.service.proc = FakeAlive()
        launcher.service._ready = False
        # 手动调 _tick 会新排一个定时器；先把 __init__ 排的那个取消掉，
        # 否则它会变成孤儿 —— 窗口销毁后照样到点触发，Tk 报
        # 「invalid command name "..._tick"」。
        launcher.cancel_tick()
        launcher._tick()
        check("状态栏在「没就绪」时显示「启动中」",
              launcher.status_label.cget("text") == "● 启动中…",
              launcher.status_label.cget("text"))
        check("没就绪时「打开网页控制台」是禁用的",
              str(launcher.browser_btn.cget("state")) == "disabled",
              str(launcher.browser_btn.cget("state")))
    finally:
        launcher.cancel_tick()
        launcher.root.destroy()
        lap.BASE = old_base


def file_manager_checks():
    """文件管理器：**所有上传的文件都在这里显示**。

    这里钉的是用户在抱怨的那句话「我上传的文件不见了」。有两个独立的原因，
    各自一条断言：

    1. 列表读的是**工作区**（``/api/uploads``）而不是**注册中心**（``/api/datasets``）。
       传了但 QGIS 打不开的文件（比如只传 ``.shp``、缺 ``.dbf/.shx/.prj`` 配套件）
       实实在在存在磁盘上，却没进注册中心 —— 原来那个面板就看不见它。
    2. 空间默认「全部」：用户传的东西不一定落在共享空间（可能传到个人空间去了）。

    用一个**数据目录完全独立**的临时服务，不碰生产数据。
    """
    import json
    import tempfile

    sys.path.insert(0, str(PROJECT_ROOT / "tests"))
    from server_helper import TestServer, _free_port

    tmp = Path(tempfile.mkdtemp(prefix="gisds_fm_"))
    geo = json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {"n": 1},
                      "geometry": {"type": "Point", "coordinates": [112.48, 39.685]}}],
    }).encode()

    with TestServer(port=_free_port(8879), require_login=True, data_root=tmp,
                    extra_env={"GISDS_ADMIN_PASSWORD": "admin123"}) as server:
        old_base = lap.BASE
        lap.BASE = server.base
        lap.set_token(None)
        try:
            # --- 未登录时：给出明确指引，而不是一张空表 ---
            root0 = tk.Tk()
            win0 = FileBrowserWindow(root0)
            root0.update()
            win0.browser.refresh_scopes()      # 不等那个 300ms 的定时器
            hint = str(win0.browser.who_label.cget("text") or "")
            check("未登录时身份标签给出指引（不是光秃秃的「未登录」）",
                  "未登录" in hint and len(hint) > len("未登录"), hint)
            win0.close()

            # --- 独立窗口打开、还没登录时，会不会主动弹登录框 ---
            from gisds.launcher import files as fmod
            asked: list[bool] = []
            real_do_login = fmod.do_login
            fmod.do_login = lambda root, log=None: asked.append(True)
            try:
                root1 = tk.Tk()
                win1 = FileBrowserWindow(root1)
                deadline = time.time() + 4
                while time.time() < deadline and not asked:
                    root1.update()
                    time.sleep(0.05)
                check("独立窗口打开时没登录会主动弹登录框", bool(asked), str(asked))
                win1.close()
            finally:
                fmod.do_login = real_do_login

            data = lap.api_json("POST", "/api/auth/login",
                                body={"username": "admin", "password": "admin123"})
            lap.set_token(data.get("token"))
            check("临时服务能登录（文件管理器用）", bool(data.get("token")))

            # --- 传三种文件 ---
            # 1) 正常 geojson（个人空间）
            s1, _ = lap.api("POST", "/api/uploads", params={"scope": "me"},
                            files=[("files", "probe.geojson", geo)])
            check("上传普通文件到个人空间", s1 == 200, f"HTTP {s1}")
            # 2) **中文文件名**（数据集 id 会带中文 → URL 必须编码）
            s2, _ = lap.api("POST", "/api/uploads", params={"scope": "me"},
                            files=[("files", "寿阳测试.geojson", geo)])
            check("上传中文名文件", s2 == 200, f"HTTP {s2}")
            # 3) **只有 .shp、没有配套件** → QGIS 打不开 → registered=false
            s3, raw3 = lap.api("POST", "/api/uploads", params={"scope": "me"},
                               files=[("files", "lonely.shp", b"\x00\x01\x02dummy")])
            check("上传缺配套件的 .shp（QGIS 认不出来）", s3 == 200, f"HTTP {s3}")
            payload3 = json.loads(raw3 or b"{}")
            results3 = payload3.get("results") or []
            check("那个 .shp 没被识别为图层（registered=false）",
                  bool(results3) and results3[0].get("registered") is False,
                  str(results3[:1])[:120])

            # --- 打开面板，看列表 ---
            root = tk.Tk()
            win = FileBrowserWindow(root)
            root.update()
            win.browser.refresh()
            root.update()
            b = win.browser
            rows = list(b.file_tree.get_children())
            names = {b.file_tree.item(r, "values")[0] for r in rows}
            files_col = {b.file_tree.item(r, "values")[0]: b.file_tree.item(r, "values")[2]
                         for r in rows}
            state_col = {b.file_tree.item(r, "values")[0]: b.file_tree.item(r, "values")[4]
                         for r in rows}

            check("列表默认就是「全部」空间", b.scope_box.get() == "全部",
                  repr(b.scope_box.get()))
            check("上传的三个文件都在列表里（3 个）", len(rows) == 3,
                  f"{len(rows)} 行: {sorted(names)}")
            check("没被识别的那个 .shp 也在列表里（不是只有注册中心那批）",
                  any("lonely.shp" in str(files_col.get(n, "")) for n in names),
                  str({n: files_col.get(n) for n in names})[:160])
            check("「文件」列显示的是文件名",
                  any("寿阳测试.geojson" == str(files_col.get(n, "")) for n in names),
                  str({n: files_col.get(n) for n in names})[:160])
            check("「状态」列标出未识别",
                  any(state_col.get(n) == "未识别" for n in names),
                  str(state_col))

            # --- 没被识别的数据集也要能删 ---
            # 它不在注册中心里，但文件实实在在躺在磁盘上 —— 以前这种数据集
            # 既不显示、也删不掉（成了永久垃圾）。
            lonely = next((n for n in names if "lonely" in str(n)), "")
            del_ok, del_msg = False, lonely
            if lonely:
                try:
                    lap.api_json("DELETE", f"/api/uploads/{lonely}")
                    del_ok = True
                    del_msg = f"已删除 {lonely}"
                except Exception as exc:
                    del_msg = f"{type(exc).__name__}: {exc}"
            check("没被识别的数据集也能删掉", del_ok, del_msg)

            # --- 中文数据集名取详情不能崩（URL 编码）---
            zh_id = next((n for n in names if "寿阳" in str(n)), "")
            detail_ok, detail_msg = True, zh_id
            try:
                det = lap.api_json("GET", f"/api/datasets/{zh_id}")
                detail_msg = str(list((det.get("vectors") or {}).keys()))
            except Exception as exc:
                detail_ok = False
                detail_msg = f"{type(exc).__name__}: {exc}"
            check("中文数据集名取详情不崩（URL 编码）", detail_ok, detail_msg)

            win.close()
        finally:
            lap.set_token(None)
            lap.BASE = old_base


def thread_checks():
    """后台线程不许碰界面（Tk 不是线程安全的）。

    服务日志是从子进程 stdout 读的，读它的线程是后台线程。那些行最终要
    insert 进日志控件、要建口令提示条 —— 全是 Tk 调用。Tk 只允许主线程动：
    后台线程动控件，轻则偶发地不生效（提示条时有时无、口令标签没销毁），
    重则整个程序崩掉。这种问题**隔离环境复现不了**，只有机器忙起来
    （并行跑全套自检）才偶发 —— 最难查的那一类。

    所以约定是：**后台线程只负责收行，主线程取出来再显示**。
    """
    import threading

    from gisds.launcher.app import Service

    calls = []

    def on_log(line):
        calls.append((threading.current_thread().name, line))

    svc = Service(on_log=on_log)

    class FakeProc:
        stdout = iter(["行一\n", "行二\n"])

        def poll(self):
            return None

    svc.proc = FakeProc()
    t = threading.Thread(target=svc._pump, name="pump")
    t.start()
    t.join(5)
    check("后台线程没有直接调界面回调", calls == [], str(calls))
    got = svc.drain()
    check("日志行进了队列，主线程取得到", got == ["行一", "行二"], str(got))
    check("取完之后队列是空的", svc.drain() == [], str(svc.drain()))

    # 主线程那一环：_tick 要真的把队列里的行取出来显示
    launcher = Launcher()
    launcher.root.withdraw()
    try:
        launcher.service._buffer.put("来自后台线程的一行")
        # 手动调 _tick 会新排一个定时器；先把 __init__ 排的那个取消掉，
        # 否则它会变成孤儿 —— 窗口销毁后照样到点触发，Tk 报
        # 「invalid command name "..._tick"」。
        launcher.cancel_tick()
        launcher._tick()
        check("主线程的 _tick 把队列里的行显示出来了",
              "来自后台线程的一行" in launcher.log_lines,
              str(launcher.log_lines[-3:]))
    finally:
        launcher.cancel_tick()
        launcher.root.destroy()


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))
    try:
        from server_helper import TestServer, _free_port
    except Exception as exc:
        print(f"  跳过（拿不到 TestServer）：{exc}")
        return 0

    print("== 路径 ==")
    path_checks()
    print("== 文件管理器：上传的文件都在这里显示 ==")
    file_manager_checks()
    print("== 就绪探针 ==")
    readiness_checks()
    print("== 线程安全 ==")
    thread_checks()

    prod_db = PROJECT_ROOT / "data" / "gisds.db"
    # 端口要**动态挑空闲的**：写死的话，和别的会话并行跑时后一个的健康检查会
    # 连到前一个的服务上（表现是「莫名 401 / 页面跳登录页」），而且正式实例
    # 正好在 8848 —— 差两个端口，撞上就是生产实例被当成自己的服务操作。
    gui_port = _free_port(8854)
    # 临时库由 server_helper 负责隔离（路径带进程号，每次都是新库，
    # stop() 时连同 -wal/-shm 一起删），所以这里不用手动清理。

    # GUI 段：共享空间读生产的（只读，才看得到示例数据），用户库用临时的
    with TestServer(
        port=gui_port, require_login=True, extra_env={"GISDS_ADMIN_PASSWORD": "admin123"}
    ) as server:
        check("GUI 段没打开生产用户库",
              Path(server.env()["GISDS_DB_PATH"]).resolve() != prod_db.resolve(),
              server.env()["GISDS_DB_PATH"])
        gui_checks(server)

    rest_checks()

    # 断言总数必须钉死：少一条就说明有断言被条件跳过了。
    total = passed + len(fails)
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被跳过了）",
          total == EXPECTED_CHECKS, str(total))

    print(f"\n== 结果：{passed} 通过 / {len(fails)} 失败 ==")
    if fails:
        print("  失败项:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException:  # noqa: BLE001
        # main() 抛异常时**不能**让它一路退到解释器退出 ——
        # 那样会先跑 QGIS 的析构，Windows 上大概率变成 0xC0000005（访问违例），
        # 真实原因（那条 KeyError 之类）反而被埋了。改成：打印回溯 + 退出码 1。
        import traceback

        traceback.print_exc()
        code = 1

    # 收尾的 flush 不能抛异常：万一 stdout/stderr 是 None（标准句柄无效的
    # 启动方式下会发生），flush 一抛，下面 os._exit(code) 就走不到 ——
    # 进程以 1 退出，看着「整套全红」，其实一条断言都没失败。
    for _s in (sys.stdout, sys.stderr):
        if _s:
            _s.flush()
    import os

    os._exit(code)
