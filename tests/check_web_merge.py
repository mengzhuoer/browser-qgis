# -*- coding: utf-8 -*-
"""网页合版完整性静态检查。

web/index.html 现在有三方各自加过东西：

- 主会话：添加图层对话框（按数据空间分组、上传目标空间）、工具箱枚举参数用
  option_values 提交、状态栏、右栏面板结构、boot()
- 2-辅助A：<head> 的 icon link、顶栏 brand 的图标、四个内置底图函数
- 3-辅助：panels.js 的挂载（<script src="/static/panels.js">）

三方并行改同一个文件，最怕的是其中一方把文件整体重写、把别人的那几块悄悄弄丢
——功能少了但没人发现，等真到用的时候才暴露。这里用静态文本检查把每一块都钉住，
谁重写丢东西，自检立刻红。

纯文本检查，不需要浏览器、不需要起服务：

    run-qgis-python.bat tests/check_web_merge.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = PROJECT_ROOT / "web"
INDEX = WEB_DIR / "index.html"

_PASSED = 0
_FAILED = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 31 = 图标 4 + 内置离线底图 6 + 面板 4 + 主会话 6 + 重复与完整性 5
#            + 帮助页 6。
# **全部无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，钉子当场变红。
EXPECTED_CHECKS = 31


def check(name: str, condition: bool, detail: str = "") -> None:
    global _PASSED, _FAILED
    if condition:
        _PASSED += 1
        print(f"  [PASS] {name}")
    else:
        _FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def summary() -> int:
    """打印汇总 + 钉死断言总数。

    **每一条退出路径都得过这道钉子** —— 少了断言就当场红。
    「汇总数说谎」比「断言失败」更难查：一个断言都没跑却报「成功」是最坑的。
    """
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          _PASSED + _FAILED == EXPECTED_CHECKS, str(_PASSED + _FAILED))
    print(f"\n== 结果：{_PASSED} 通过 / {_FAILED} 失败 ==")
    return 0 if _FAILED == 0 else 1


def main() -> int:
    print("== 网页合版完整性（静态检查） ==")
    if not INDEX.is_file():
        print(f"  找不到 {INDEX}")
        check("index.html 存在", False, str(INDEX))
        # 一条断言都没跑却报「成功」是最坑的 —— 走同一道钉子，当场红。
        return summary()
    html = INDEX.read_text(encoding="utf-8")

    print("\n-- 2-辅助A：图标 --")
    check("head 有 favicon link", 'href="/static/favicon.png"' in html)
    check("head 有 1024 图标 link", 'href="/static/icon.png"' in html)
    check("head 有 apple-touch-icon", 'rel="apple-touch-icon"' in html)
    check("顶栏 brand 用图标图", '<img src="/static/icon.png"' in html)

    print("\n-- 2-辅助A：内置离线底图 --")
    for fn in ("makeBuiltinBasemap", "makeOnlineBasemap", "setBasemap", "initBasemap"):
        check(f"有 {fn}()", re.search(rf"function\s+{fn}\s*\(", html) is not None)
    check("boot() 里调用了 initBasemap()", "await initBasemap()" in html)
    check("底图指向本服务端瓦片", "/api/tiles/basemap/{z}/{x}/{y}.png" in html)

    print("\n-- 3-辅助：面板 --")
    check("引入了 panels.js", '<script src="/static/panels.js">' in html)
    check("panels.js 文件存在", (WEB_DIR / "panels.js").is_file())
    panels_js = (WEB_DIR / "panels.js")
    # 下面两条断言**无条件执行**：文件不在就让断言**失败**，而不是整段跳过
    # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
    pjs = panels_js.read_text(encoding="utf-8") if panels_js.is_file() else ""
    check("panels.js 自带启动钩子（不依赖 index.html 的 boot）",
          "DOMContentLoaded" in pjs, "文件不存在或没有钩子")
    check("panels.js 导出全局对象", "GISDS_PANELS" in pjs, "文件不存在或没有导出")

    print("\n-- 主会话：添加图层 / 工具箱 / 状态栏 --")
    check("添加图层对话框在", 'id="dlgAdd"' in html)
    check("上传目标空间可选", 'id="addScope"' in html)
    check("工具箱枚举用 option_values 提交", "option_values" in html)
    check("状态栏 QGIS 版本位在", 'id="stQgis"' in html)
    check("状态栏比例尺在", 'id="stScaleBar"' in html)
    check("工具箱面板在", 'id="paneAlgo"' in html and "algoSearch" in html)

    print("\n-- 重复与完整性 --")
    names = re.findall(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", html, re.M)
    dup = sorted({n for n in names if names.count(n) > 1})
    check("index.html 没有重复函数定义", not dup, str(dup))
    # 这条断言**无条件执行**：文件不在就判失败，而不是整段跳过
    # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
    pnames = re.findall(
        r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)",
        pjs, re.M,
    )
    pdup = sorted({n for n in pnames if pnames.count(n) > 1})
    check("panels.js 没有重复函数定义",
          panels_js.is_file() and not pdup, str(pdup))

    # index.html 引用的本地脚本/样式都得存在
    missing = []
    for ref in re.findall(r'<script[^>]*src="/static/([^"]+)"', html):
        if not (WEB_DIR / ref).is_file():
            missing.append(ref)
    for ref in re.findall(r'<link[^>]*href="/static/([^"]+)"', html):
        if not (WEB_DIR / ref).is_file():
            missing.append(ref)
    check("index.html 引用的静态文件都存在", not missing, str(missing))

    # 三个页面都得有图标声明
    for page in ("index.html", "login.html"):
        path = WEB_DIR / page
        # 这条断言**无条件执行**：文件不在就判失败，而不是整段跳过
        # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        check(f"{page} 有图标声明",
              path.is_file() and ('rel="icon"' in text or "apple-touch-icon" in text),
              str(path))

    print("\n-- 帮助页 --")
    help_path = WEB_DIR / "help.html"
    check("help.html 存在", help_path.is_file(), str(help_path))
    # 下面三条断言**无条件执行**：文件不在就判失败，而不是整段跳过
    # （跳过会让断言总数变少，钉子就看不出少了哪几条）。
    htext = help_path.read_text(encoding="utf-8") if help_path.is_file() else ""
    check("帮助页是中文的", "使用帮助" in htext or "怎么用" in htext, "文件不存在")
    # 目录里列的每个章节都得真的有对应的锚点，否则点了跳不过去
    anchors = re.findall(r'href="#(s\d+)"', htext)
    missing_anchors = [a for a in anchors if f'id="{a}"' not in htext]
    check("帮助页的目录锚点都存在",
          help_path.is_file() and not missing_anchors, str(missing_anchors))
    check("帮助页写明了常用格式",
          help_path.is_file() and ".tif" in htext and ".shp" in htext, "文件不存在")
    # 「帮助」菜单里得能打开它
    check("帮助菜单里有「使用帮助」", "使用帮助" in html)
    check("帮助菜单指向 /help", 'window.open("/help"' in html)

    return summary()


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
