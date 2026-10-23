"""文件浏览器。

一个可复用的面板：既能嵌在启动器主界面的「文件」页里，也能单独开成一个
独立窗口（``python -m gisds.launcher.files`` 或点启动器里的「独立窗口」）。

管的是服务端的各个数据空间：

- 共享空间：所有登录用户都能看到
- 个人空间：只有本人能看到

可做：浏览、上传、下载、删除数据集、在资源管理器里打开目录。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .api import BASE, available_scopes, api, api_json, current_user, do_login, set_token
from .tkguard import keep_root
from .theme import BG, INK, MONO, PANEL, RULE, apply_style

FILE_TYPES = [
    ("地理数据", "*.tif *.tiff *.img *.vrt *.geojson *.json *.gpkg *.shp *.gml *.kml"),
    ("全部文件", "*.*"),
]

# 空间下拉里的第一项：把**所有能看到的空间**合在一起显示。
#
# 用户上传的东西不一定落在共享空间（可能传到个人空间去了），只显示共享空间
# 的话就会出现「我传的图不见了」—— 而文件其实好好地在那儿。
ALL_SCOPES = "全部"


class FileBrowser:
    """文件浏览器面板。

    刻意不继承 ttk.Frame，而是持有自己的父容器 —— 这样同一套逻辑既能
    挂进启动器的 Notebook，也能挂到一个独立的 Toplevel 上。
    """

    def __init__(self, parent, tk, ttk, log=None, on_login=None):
        self.tk = tk
        self.ttk = ttk
        self.parent = parent
        self.log = log or (lambda msg: None)
        self.on_login = on_login
        self._scopes: list[dict] = []
        self._items: dict[str, dict] = {}   # dataset_id -> /api/uploads 里的那条

        self._build()
    # ---------------------------------------------------------------- 界面

    def _build(self):
        tk, ttk = self.tk, self.ttk

        card = ttk.Frame(self.parent, style="Card.TFrame")
        card.pack(fill="both", expand=True)
        self.card = card

        bar = ttk.Frame(card, style="Card.TFrame")
        bar.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(bar, text="数据空间", style="Head.TLabel").pack(side="left")

        # 空间下拉。刻意不绑 textvariable —— ttk 在改 values 时会把它同步清空，
        # 结果框里就没字了。这里只用 Combobox 自己的 get/set。
        #
        # 第一项是「全部」：用户上传的东西不一定在共享空间里（可能传到了个人
        # 空间），只显示共享空间就等于「我传的图不见了」。默认就选「全部」。
        self.scope_box = ttk.Combobox(bar, state="readonly", width=24)
        self.scope_box.pack(side="left", padx=8)
        self.scope_box.set(ALL_SCOPES)
        self.scope_box.bind("<<ComboboxSelected>>", lambda e: self.load_files())

        ttk.Button(bar, text="上传文件", command=self.on_upload).pack(side="right")
        ttk.Button(bar, text="下载", command=self.on_download).pack(side="right", padx=4)
        ttk.Button(bar, text="删除", command=self.on_delete_dataset).pack(side="right", padx=4)
        ttk.Button(bar, text="刷新", command=self.refresh).pack(side="right", padx=4)

        head = ttk.Frame(card, style="Card.TFrame")
        head.pack(fill="x", padx=8, pady=(0, 2))
        self.who_label = ttk.Label(head, text="未登录", style="Muted.TLabel")
        self.who_label.pack(side="left")
        ttk.Button(head, text="登录", command=self.on_login_click).pack(side="right")

        cols = ("dataset", "scope", "files", "layers", "state", "path")
        self.file_tree = ttk.Treeview(card, columns=cols, show="headings", selectmode="browse")
        for key, text, width in (
            ("dataset", "数据集", 190),
            ("scope", "空间", 66),
            ("files", "文件", 250),
            ("layers", "图层", 76),
            ("state", "状态", 66),
            ("path", "目录", 190),
        ):
            self.file_tree.heading(key, text=text)
            self.file_tree.column(key, width=width, anchor="w")
        self.file_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.file_tree.bind("<Double-1>", lambda e: self.on_open_dataset())

    # ---------------------------------------------------------------- 布局

    def pack(self, **kwargs):
        """把自己摆进父容器（代理给内部卡片）。"""
        self.card.pack(**kwargs)

    def grid(self, **kwargs):
        self.card.grid(**kwargs)

    def pack_forget(self):
        self.card.pack_forget()

    # ---------------------------------------------------------------- 数据

    def refresh(self):
        """刷新空间列表与文件列表。"""
        self.refresh_scopes()
        self.load_files()

    def refresh_scopes(self):
        """按当前登录用户刷新可选空间。"""
        try:
            user = current_user()
        except Exception as exc:
            # 服务没连上 ≠ 没登录 —— 别把故障显示成「未登录」
            self.log(f"刷新身份失败：{exc}")
            self.who_label.configure(text="服务未连接")
            self._scopes = []
            self.scope_box["values"] = (ALL_SCOPES,)
            self.scope_box.set(ALL_SCOPES)
            return
        if not user:
            # 空表 + 一行日志，用户只会以为「文件没了」。把下一步说清楚。
            self.who_label.configure(text="未登录 —— 点「登录」查看已上传的文件")
            self._scopes = []
            self.scope_box["values"] = (ALL_SCOPES,)
            self.scope_box.set(ALL_SCOPES)
            return
        self.who_label.configure(
            text=f"{user.get('display_name') or user.get('username')}（{user.get('role')}）"
        )
        try:
            scopes = available_scopes()
        except Exception as exc:
            self.log(f"取数据空间列表失败：{exc}")
            scopes = []
        self._scopes = scopes
        labels = [ALL_SCOPES] + [s.get("scope", "") for s in scopes]
        current = self.scope_box.get()
        self.scope_box["values"] = tuple(labels)
        # 给 values 赋值会清掉控件当前值，所以必须无条件回写一次
        self.scope_box.set(current if current in labels else ALL_SCOPES)

    def load_files(self):
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        scope = self.scope_box.get()
        try:
            # 用 /api/uploads（**工作区** = 磁盘上真实存在的东西），
            # 不是 /api/datasets（**注册中心** = QGIS 认得出来的图层）。
            #
            # 差在哪：上传了但 QGIS 打不开的文件（比如只传了 .shp、缺 .dbf/.shx/.prj
            # 配套件），文件是**实实在在存到磁盘上了**，但注册中心里没有它 ——
            # 原来这个面板读注册中心，于是「我上传的文件」就凭空消失了。
            data = api_json("GET", "/api/uploads")
        except Exception as exc:
            self.log(f"读取数据集失败：{exc}")
            return
        items = list(data.get("uploads") or data.get("datasets") or [])
        if scope != ALL_SCOPES:
            items = [d for d in items if d.get("scope", "shared") == scope]
        self._items = {}
        for d in items:
            did = d.get("dataset_id") or d.get("id") or ""
            if not did:
                continue
            self._items[did] = d
            layers = d.get("layers") or {}
            vectors = d.get("vectors") or {}
            files = d.get("files") or []
            label = d.get("scope_label") or ("共享空间" if d.get("scope") == "shared" else "个人空间")
            self.file_tree.insert(
                "", "end",
                iid=did,
                values=(
                    did,
                    label,
                    "、".join(files) if files else "—",
                    f"栅{len(layers)}/矢{len(vectors)}",
                    "已识别" if d.get("registered", True) else "未识别",
                    d.get("directory") or d.get("path") or "",
                ),
            )
        self.log(f"{scope}：{len(items)} 个数据集"
                 if scope == ALL_SCOPES else f"{scope} 空间：{len(items)} 个数据集")

    def _selected_dataset(self):
        sel = self.file_tree.selection()
        if not sel:
            self.log("请先在列表里选中一个数据集")
            return None
        return sel[0]

    # ---------------------------------------------------------------- 操作

    def on_login_click(self):
        user = do_login(self.parent.winfo_toplevel(), self.log)
        if not user:
            self.log("未登录")
            return
        self.refresh()
        if self.on_login:
            self.on_login(user)

    def on_upload(self):
        from tkinter import filedialog

        scope = self.scope_box.get()
        if scope == ALL_SCOPES:
            # 「全部」只是个显示过滤器，不是能上传到的地方 —— 落到共享空间
            scope = "shared"
            self.log("当前选的是「全部」，文件上传到共享空间")
        paths = filedialog.askopenfilenames(
            parent=self.parent.winfo_toplevel(),
            title="选择要上传的文件",
            filetypes=FILE_TYPES,
        )
        if not paths:
            return
        files = [("files", Path(p).name, Path(p).read_bytes()) for p in paths]
        try:
            status, raw = api("POST", "/api/uploads", params={"scope": scope}, files=files)
            if status >= 400:
                raise RuntimeError(f"HTTP {status}")
            data = json.loads(raw)
        except Exception as exc:
            self.log(f"上传失败：{exc}")
            return
        for item in data.get("results", []):
            state = "已识别为图层" if item.get("registered") else item.get("warning", "未识别")
            self.log(f"上传 {item.get('filename')}：{state}")
        self.load_files()

    def on_download(self):
        from tkinter import filedialog

        dataset_id = self._selected_dataset()
        if not dataset_id:
            return
        # 文件名直接取列表里那一条（/api/uploads 已经给了 files）。
        # 不去问 /api/datasets/{id} —— 没被 QGIS 认出来的数据集在那里是 404，
        # 于是「上传了但打不开」的文件就下载不了。
        item = self._items.get(dataset_id) or {}
        names = list(item.get("files") or [])
        if not names:
            try:
                detail = api_json("GET", f"/api/datasets/{dataset_id}")
            except Exception as exc:
                self.log(f"读取数据集失败：{exc}")
                return
            names = list((detail.get("layers") or {}).keys()) + list((detail.get("vectors") or {}).keys())
        if not names:
            self.log("这个数据集里没有文件")
            return
        target = filedialog.asksaveasfilename(
            parent=self.parent.winfo_toplevel(), title="保存为", initialfile=f"{dataset_id}.bin"
        )
        if not target:
            return
        folder = Path(target).parent
        for name in names:
            try:
                status, raw = api("GET", f"/api/uploads/{dataset_id}/files/{name}")
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")
                (folder / name).write_bytes(raw)
                self.log(f"已下载 {name}")
            except Exception as exc:
                self.log(f"下载 {name} 失败：{exc}")

    def on_delete_dataset(self):
        dataset_id = self._selected_dataset()
        if not dataset_id:
            return
        if not _confirm(self.parent.winfo_toplevel(),
                        f"确定删除数据集「{dataset_id}」？\n磁盘上的文件会一并删除。"):
            return
        try:
            api_json("DELETE", f"/api/uploads/{dataset_id}")
        except Exception as exc:
            self.log(f"删除失败：{exc}")
            return
        self.log(f"已删除数据集 {dataset_id}")
        self.load_files()

    def on_open_dataset(self):
        dataset_id = self._selected_dataset()
        if not dataset_id:
            return
        # 目录同样取列表里那一条 —— 没被 QGIS 认出来的数据集在 /api/datasets 里
        # 是 404，而它的文件实实在在躺在磁盘上，双击就该能打开它的目录。
        item = self._items.get(dataset_id) or {}
        path = item.get("directory") or item.get("path") or ""
        if not path:
            try:
                detail = api_json("GET", f"/api/datasets/{dataset_id}")
            except Exception as exc:
                # 双击了没反应是最难查的一种 —— 至少要说一句
                self.log(f"打开数据集失败：{exc}")
                return
            path = detail.get("path", "")
        if path and Path(path).is_dir():
            try:
                os.startfile(path)  # type: ignore[attr-defined]
            except Exception as exc:
                self.log(f"打开目录失败：{exc}")


def _confirm(root, message: str) -> bool:
    from tkinter import messagebox

    return bool(messagebox.askyesno("确认", message, parent=root))


# --------------------------------------------------------------------- 独立窗口


class FileBrowserWindow:
    """只管文件的独立窗口。"""

    def __init__(self, root=None):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk

        self.root = keep_root(root or tk.Tk())
        self.root.title("Browser QGIS · 文件浏览器")
        self.root.geometry("960x560")
        self.root.minsize(760, 440)
        self.root.configure(bg=BG)
        _apply_icon(self.root)

        apply_style(ttk, self.root)

        head = ttk.Frame(self.root)
        head.pack(fill="x", padx=14, pady=(12, 6))
        ttk.Label(head, text="文件浏览器", style="Title.TLabel").pack(side="left")
        # 「刷新」在下面的工具条里已经有了，这里不再重复一个
        ttk.Button(head, text="在浏览器中打开", command=self.open_console).pack(side="right")

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        # 文件列表占主体，日志压在下边一条
        self.browser = FileBrowser(body, tk, ttk, log=self.log)
        self.browser.pack(fill="both", expand=True)

        self.log_text = tk.Text(body, bg=PANEL, fg=INK, font=(MONO, 9), height=4,
                                relief="flat", highlightthickness=1, highlightbackground=RULE,
                                wrap="none", state="disabled")
        self.log_text.pack(fill="x", pady=(6, 0))

        # 句柄要记下来：窗口销毁后这个定时器照样会到点触发，而它的 Tcl 命令
        # 已经没了 → Tk 报「invalid command name」。
        self._refresh_handle = self.root.after(300, self.browser.refresh)
        # 独立窗口（launcher-files.bat）刚打开时是没有登录态的 —— 弹一次登录框，
        # 别让用户对着空表以为「我传的文件没了」。从主启动器里开出来的弹窗
        # 共享登录态，这时身份标签不是「未登录」，不会弹。
        self._prompt_handle = self.root.after(900, self._maybe_prompt_login)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def _maybe_prompt_login(self):
        """没登录就把登录框弹出来。"""
        self._prompt_handle = None
        try:
            label = str(self.browser.who_label.cget("text") or "")
        except Exception:
            return
        if label.startswith("未登录"):
            self.browser.on_login_click()

    def close(self):
        """关掉这个窗口，连它排的定时器一起取消。"""
        for attr in ("_refresh_handle", "_prompt_handle"):
            handle = getattr(self, attr, None)
            setattr(self, attr, None)
            if handle is None:
                continue
            try:
                self.root.after_cancel(handle)
            except self.tk.TclError:
                pass        # 已经触发过了，取消不掉是正常的
        try:
            self.root.destroy()
        except self.tk.TclError:
            pass            # 窗口已经没了

    def log(self, line: str):
        if not line:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def open_console(self):
        import webbrowser

        webbrowser.open(BASE + "/")


def _apply_icon(root):
    try:
        ico = Path(__file__).resolve().parents[2] / "assets" / "icon.ico"
        if ico.is_file():
            root.iconbitmap(str(ico))
    except Exception as exc:
        # 图标是锦上添花，失败不该影响窗口；但**不许裸吞** ——
        # 静默失败会让人以为「图标已经设上了」。
        print(f"[gisds-launcher] 应用窗口图标失败：{exc}", file=sys.stderr)


def open_file_window(parent=None) -> FileBrowserWindow:
    """从启动器里调起独立的文件浏览器窗口。"""
    import tkinter as tk

    root = tk.Toplevel(parent) if parent else tk.Tk()
    win = FileBrowserWindow(root)
    return win


def main() -> int:
    win = FileBrowserWindow()
    win.root.mainloop()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
