# -*- coding: utf-8 -*-
"""web/ 下 JS 的语法自检 —— 抓「某个功能凭空消失」这一类问题。

起因：``web/admin.js`` 里那段 CSS 模板串（``const CSS = `…` ``）的**注释中
用了反引号**去包 CSS 选择器 → 反引号把模板串**提前闭合** → 整个文件成了
语法错误、**完全不执行** → ``window.GISDS_ADMIN`` 是 ``undefined`` →
管理员页签从来没被创建过。

这类问题的现象极具迷惑性：页面上**什么迹象都没有**（不是权限判断错，
是脚本压根没跑），浏览器控制台只有一条 SyntaxError。靠人眼数反引号不靠谱
（写注释的时候手一抖就中招），所以这里**用机器挡**：

1. **反引号成对**（纯 Python，不依赖任何东西）——
   模板串没闭合，个数必是奇数。这条正好覆盖上面那个事故。
2. **真语法校验**（``node --check``）—— 能抓出**所有**语法错，不止反引号。

第 2 条需要 node。没装 node 就**整支脚本跳过**（跟 ``check_launcher.py``
缺系统 Python 时的做法一样），而不是「悄悄少验了一半」。

跑法：``run-qgis-python.bat tests\\check_web_js_syntax.py``
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

#: 参与检查的 js 文件**个数**。
#:
#: 钉死它是有意的：加了新的 web/*.js 忘了纳入检查，这个数就会变、钉子当场红，
#: 逼着做决定（纳入检查 / 还是明确排除，比如 web\\vendor 那种第三方代码）。
EXPECTED_JS_FILES = 8

#: 断言总数（**不含**末尾那条「总数」断言本身）。
#:
#: = 1（文件集本身）+ 2 × 每个文件（反引号 + 语法）。
EXPECTED_CHECKS = 1 + EXPECTED_JS_FILES * 2

fails: list[str] = []
checked = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def js_files() -> list[Path]:
    """参与检查的 js 文件。

    ``web\\vendor`` 跳过：那是第三方代码（Leaflet），我们不替上游做语法担保，
    也不该被上游的写法弄红自己的检查。
    """
    return sorted(p for p in WEB.rglob("*.js") if "vendor" not in p.parts)


def find_node() -> str | None:
    """找 node。

    为什么不能直接 ``shutil.which("node")``：QGIS 运行时（``o4w_env.bat``）会把
    **PATH 整个重置**成「QGIS 的 bin + 几个系统目录」，node 装在
    ``C:\\Program Files\\nodejs`` 就不在 PATH 里了 —— 于是「明明装了 node」
    却说没装。

    所以这里从注册���把**系统真实的 PATH** 捞出来，合并进当前 PATH 再找；
    常见的安装位置也直接试一遍。
    """
    found = shutil.which("node") or shutil.which("nodejs")
    if found:
        return found

    extra = []
    try:
        import winreg

        for hive, path in (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            try:
                with winreg.OpenKey(hive, path) as key:
                    val = winreg.QueryValueEx(key, "Path")[0]
                    if val:
                        extra.append(val)
            except OSError:
                continue
    except Exception:
        pass

    if extra:
        merged = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(extra + ([merged] if merged else []))
        found = shutil.which("node") or shutil.which("nodejs")
        if found:
            return found

    for guess in (
        r"C:\Program Files\nodejs\node.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "nodejs" / "node.exe"),
        str(Path(os.environ.get("ProgramFiles", "")) / "nodejs" / "node.exe"),
    ):
        if guess and Path(guess).is_file():
            return guess
    return None


def main() -> int:
    node = find_node()
    if not node:
        print("SKIP: 没装 node，语法校验做不了（装个 node 才有这条保证）")
        return 0

    print("== 网页脚本语法自检 ==")
    print(f"   node : {node}")
    files = js_files()
    check(f"参与检查的 js 文件是 {EXPECTED_JS_FILES} 个",
          len(files) == EXPECTED_JS_FILES,
          f"实际 {len(files)}: {', '.join(p.name for p in files)}")

    for path in files:
        rel = path.relative_to(ROOT)
        try:
            src = path.read_text(encoding="utf-8")
        except OSError as exc:
            check(f"{rel}：读得到", False, str(exc))
            check(f"{rel}：语法合法", False, "文件读不到")
            continue

        ticks = src.count("`")
        check(f"{rel}：反引号成对（模板串闭合）", ticks % 2 == 0, f"{ticks} 个")

        res = subprocess.run(
            [node, "--check", str(path)],
            capture_output=True, text=True, errors="replace", timeout=60,
        )
        detail = (res.stderr or res.stdout or "").strip()
        # node 的报错里带绝对路径，翻成项目相对路径好读
        detail = detail.replace(str(ROOT) + "\\", "").replace(str(ROOT) + "/", "")
        check(f"{rel}：语法合法", res.returncode == 0, detail[:300])

    total = checked
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          total == EXPECTED_CHECKS, f"实际 {total}")

    print(f"\n== 结果：{checked - len(fails)} 通过 / {len(fails)} 失败 ==")
    for item in fails:
        print(f"  ! {item}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
