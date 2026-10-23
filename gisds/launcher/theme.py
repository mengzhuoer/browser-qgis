"""启动器配色与 ttk 样式。

抽出来是为了让启动器主窗口和独立的文件浏览器窗口共用同一套外观。
"""

from __future__ import annotations

import sys

# 已经上过样式的解释器（按 ttk.Style 的 master 标识）。
# 同一个解释器重复切主题没有意义，还会重发 ThemeChanged 事件。
_THEMED: set[str] = set()

# 纸面制图配色，和网页控制台保持一致
BG = "#EEF0EB"
PANEL = "#FBFCFA"
PANEL2 = "#F4F6F1"
RULE = "#CFD5CC"
INK = "#1E2420"
INK2 = "#4C554C"
INK3 = "#7B857B"
MOSS = "#2C6B4F"
RUST = "#A33B24"
MONO = "Consolas"


def apply_style(ttk, root=None) -> None:
    """给 ttk 套上统一样式。

    ``root`` 是这个窗口自己的根（**要显式传**）：ttk.Style() 不带参数时会去绑
    「最近创建的根窗口」（tkinter._default_root），而那个可能是**别的窗口**的根 ——
    于是样式套到了不相干的解释器上，切主题时会给一堆控件发 ThemeChanged，
    其中可能已经有属于已销毁应用的 → Tcl 报
    「can't invoke "event" command: application has been destroyed」。

    同一个解释器只切一次主题：重复切主题会重发 ThemeChanged，没必要。
    """
    style = ttk.Style(root) if root is not None else ttk.Style()
    try:
        key = str(getattr(style, "master", "") or "")
        if key and key in _THEMED:
            return          # 这个解释器已经上过样式了
        if key:
            _THEMED.add(key)
        style.theme_use("clam")
    except Exception as exc:
        # 没有 clam 主题就退回系统默认主题，不影响使用；但**不许裸吞** ——
        # 静默失败会让人以为「配色已经按预期套上了」。
        print(f"[gisds-launcher] 切换 ttk 主题失败，用系统默认主题：{exc}", file=sys.stderr)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL, relief="flat")
    style.configure("TLabel", background=BG, foreground=INK, font=(MONO, 10))
    style.configure("Muted.TLabel", background=BG, foreground=INK3, font=(MONO, 9))
    style.configure("Head.TLabel", background=PANEL2, foreground=INK2, font=(MONO, 9, "bold"))
    style.configure("Title.TLabel", background=BG, foreground=INK, font=(MONO, 15, "bold"))
    # 卡片（Card.TFrame，PANEL 底）里的文字：背景要跟着卡片，不然是一块补丁
    style.configure("Card.TLabel", background=PANEL, foreground=INK, font=(MONO, 10))
    style.configure("CardHead.TLabel", background=PANEL, foreground=INK, font=(MONO, 9, "bold"))
    style.configure("CardMuted.TLabel", background=PANEL, foreground=INK3, font=(MONO, 9))
    style.configure("CardWarn.TLabel", background=PANEL, foreground=RUST, font=(MONO, 9, "bold"))
    style.configure("TButton", font=(MONO, 10), padding=(10, 5))
    style.configure("Accent.TButton", background=MOSS, foreground="#FFFFFF", font=(MONO, 10, "bold"))
    style.map("Accent.TButton", background=[("active", "#245740")])
    style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=INK,
                    font=(MONO, 9), rowheight=24, borderwidth=0)
    style.configure("Treeview.Heading", background=PANEL2, foreground=INK2,
                    font=(MONO, 9, "bold"), relief="flat")
    style.map("Treeview", background=[("selected", "#E2EEE7")], foreground=[("selected", INK)])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL2, foreground=INK2, padding=(14, 6), font=(MONO, 10))
    style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", INK)])

    # Combobox 的每个颜色都要显式给，否则 clam 会从别处继承，
    # 读起来会变成「框在但没字」。
    style.configure("TCombobox",
                    fieldbackground=PANEL, background=PANEL, foreground=INK,
                    arrowcolor=INK, bordercolor=RULE, lightcolor=PANEL, darkcolor=PANEL,
                    insertcolor=INK, selectbackground="#E2EEE7", selectforeground=INK,
                    padding=(6, 3))
    style.map("TCombobox",
              fieldbackground=[("readonly", PANEL), ("focus", PANEL), ("hover", PANEL)],
              foreground=[("readonly", INK), ("focus", INK), ("hover", INK)],
              background=[("readonly", PANEL), ("focus", PANEL), ("hover", PANEL)],
              bordercolor=[("focus", MOSS), ("hover", RULE)],
              arrowcolor=[("disabled", INK3), ("readonly", INK), ("hover", INK)])
