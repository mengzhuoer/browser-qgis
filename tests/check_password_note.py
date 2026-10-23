# -*- coding: utf-8 -*-
"""口令备忘路径的断言（纯函数级：不起服务、不碰任何库）。

为什么单独写一个文件：

``password_note_path`` 曾经写成 ``if db.stem == "gisds.db"`` 来判断「正式库」，
而 ``Path("gisds.db").stem`` 是 ``"gisds"``（只去掉最后一个后缀），**判断永远
不成立**。于是正式库的备忘被写到 ``data/gisds_admin_password.txt``，
而文档里写的 ``data/admin_password.txt`` 留在原地不更新 —— 用户照着文件登就是
401，**而账号状态、服务日志看上去全都正常**，最难查的那种。

教训：临时库（``testserver_*.db``）走的是「其它库」分支，那条路径一直是对的，
所以拿临时库实测会全绿、永远测不到出问题的那条。**生产分支必须单独钉住。**

跑法：``run-qgis-python.bat tests/check_password_note.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

fails: list[str] = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这个脚本有一处**真分支**：正式库旁边的备忘到底存不存在。
#   - 备忘存在：跑 2 条（内容里有用户名和密码 + 口令能对上当前账号）→ 共 9 条
#   - 备忘不存在：跑 1 条（不存在时不应有错名文件）→ 共 8 条
# 两个分支各自都是固定条数，所以钉子按分支取不同的期望值。
# （原来这里写死 9 —— 备忘不存在时实际只跑了 8 条，却打印「9 通过」。）
EXPECTED_CHECKS = 9            # 备忘存在时的条数
EXPECTED_CHECKS_NO_NOTE = 8    # 备忘不存在时的条数（少一条：没有内容可验）


def check(label: str, ok: bool, detail: str = "") -> None:
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def _password_valid(password: str) -> bool:
    """备忘里写的口令是不是当前账号的有效口令。"""
    try:
        from gisds.core.auth import verify_password
        from gisds.core.db import Database

        db = Database(PROJECT_ROOT / "data" / "gisds.db")
        user = db.get_user_by_name("admin")
        if not user:
            return False
        return verify_password(password, user["password_hash"])
    except Exception:
        return False


def main() -> int:
    from gisds.core.auth import password_note_path

    print("== 正式库：备忘必须叫 admin_password.txt，且在库旁边 ==")
    prod = password_note_path(PROJECT_ROOT / "data" / "gisds.db")
    check("正式库备忘文件名是 admin_password.txt",
          prod.name == "admin_password.txt", prod.name)
    check("正式库备忘在用户库旁边（data/ 下）",
          prod.parent == (PROJECT_ROOT / "data"), str(prod.parent))
    check("正式库备忘没有带库名后缀（不能是 gisds_admin_password.txt）",
          "gisds" not in prod.stem, prod.stem)

    print("\n== 临时库：备忘名带上库名，多个临时库互不覆盖 ==")
    tmp_a = password_note_path(PROJECT_ROOT / "tests" / "_workspace" / "testserver_8854_111.db")
    tmp_b = password_note_path(PROJECT_ROOT / "tests" / "_workspace" / "testserver_8855_222.db")
    check("临时库备忘名 = <库名>_admin_password.txt",
          tmp_a.name == "testserver_8854_111_admin_password.txt", tmp_a.name)
    check("不同临时库的备忘不冲突", tmp_a != tmp_b, f"{tmp_a.name} / {tmp_b.name}")
    check("临时库备忘与正式库备忘不冲突", tmp_a != prod, f"{tmp_a.name} / {prod.name}")

    print("\n== 生产目录里不能留错名文件 ==")
    data_dir = PROJECT_ROOT / "data"
    wrong = sorted(p.name for p in data_dir.glob("*_admin_password.txt")) if data_dir.is_dir() else []
    check("data/ 下没有 <库名>_admin_password.txt 这类错名文件",
          not wrong, ", ".join(wrong) if wrong else "无")

    print("\n== 正式库旁边的备忘：要么没有，要么记着当前有效口令 ==")
    prod_note = prod
    # 不变式：**存在的话，里面那个口令必须是当前有效的**。
    # 这才是「文件不能骗人」的真正含义（文件躺在那儿让人照着登，一直 401）。
    # 「文件不存在」也是合法状态 —— 开关关掉（password_note=false）时本来就不写。
    if prod_note.is_file():
        text = prod_note.read_text(encoding="utf-8")
        check("备忘里有用户名和密码", "用户" in text and "密码" in text,
              text.replace("\n", " | ")[:70])
        pwd = ""
        for line in text.splitlines():
            if "密码" in line and "：" in line:
                pwd = line.split("：", 1)[1].strip()
        check("备忘里的口令能对上当前账号", bool(pwd) and _password_valid(pwd),
              f"备忘里写着 {pwd!r}")
    else:
        print("  （备忘不存在 —— 开关关掉时本来就不写，属正常）")
        check("备忘不存在时不应有错名文件",
              not list(prod.parent.glob("*_admin_password.txt")), "有错名文件")

    # 断言总数必须钉死：少一条就说明有断言被条件跳过了。
    # 期望值按分支取（备忘在 / 备忘不在，两条分支的条数本来就不同）。
    expected = EXPECTED_CHECKS if prod_note.is_file() else EXPECTED_CHECKS_NO_NOTE
    check(f"断言总数 = {expected}（少一条就说明有断言被条件跳过了）",
          checked == expected, str(checked))
    print(f"\n== 结果：{checked - len(fails)} 通过 / {len(fails)} 失败 ==")
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
