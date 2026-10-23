# -*- coding: utf-8 -*-
"""验证「首次启动 → 抓到初始口令 → 一键登录」这条路径，以及口令作废后的收起。

背景：服务自举时会把初始口令打进启动日志，而启动器自己起服务、日志就在
它手里。以前用户得自己去翻日志文件才能登录；现在启动器直接把它捞出来，
显示在用户页顶部，还能一键登录。

第二条要验的是**同一个原则**：任何显示凭据的地方都不能留着过期的。
提示条显示的是自举那一刻的初始口令 —— 用户在别处改过口令之后，
这条提示条就成了「失效旧口令」，还配一个「直接登录」按钮，点了只会 401。
所以它必须能自己发现口令已作废并收起。

服务端那侧的口令备忘现在是**改密后同步更新**（不是过期即删），所以「作废」的
两种表现都要认：文件被删了，或者文件还在但记的是新口令。
"""
import os
import re
import shutil
import sys
import tempfile
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from server_helper import TestServer, _free_port  # noqa: E402

fails = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 这个文件的 checked 在 check() 入口就 +1，而比较用的实参是在**调用前**求值的，
# 所以本条断言看到的是「其他断言的条数」= 41；跑完本条后总数是 42。
#
# 为什么要钉死：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查（第 6 条的下半句）。
EXPECTED_CHECKS = 41


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def _note_file(tmp: Path, password: str) -> Path:
    """造一份和服务端格式一样的口令备忘。"""
    path = tmp / "admin_password.txt"
    path.write_text(
        f"用户：admin\n密码：{password}\n\n"
        f"登录后请尽快在「启动器 → 用户 → 改密码」里改掉。\n",
        encoding="utf-8",
    )
    return path


def check_freshness():
    """口令新鲜度判断（纯逻辑，不碰 tkinter）。"""
    from gisds.launcher.credentials import (
        EXPIRED,
        FRESH,
        STALE,
        USED,
        CredentialFreshness,
        parse_password,
        parse_username,
        server_note_path,
    )

    check("从日志行里解析用户名", parse_username("    用户名：admin") == "admin")
    check("从日志行里解析口令", parse_password("    密码  ：AbCdEf123456") == "AbCdEf123456")
    check("口令行多个空格也认", parse_password("  密码 ：Xx9") == "Xx9")
    check("无关的行不误判", parse_password("服务已启动，正在等待就绪 …") is None)

    tmp = Path(tempfile.mkdtemp(prefix=f"gisds_firstlogin_{os.getpid()}_"))
    try:
        note = _note_file(tmp, "AbCdEf123456")

        # 备忘记着这个口令 → 还有效
        f = CredentialFreshness("admin", "AbCdEf123456", note_path=note)
        check("备忘记着这个口令 → 还有效", f.evaluate() == FRESH, f.evaluate())

        # 服务端改密 → 备忘被**同步更新**成新口令（文件还在，但不再记着这个口令）
        note.unlink()
        check("备忘被删 → 判定口令已作废", f.evaluate() == STALE, f.evaluate())

        # 改密的另一种结果：文件还在，但记的是新口令 —— 同样算作废
        # （先让备忘记着这个口令，「见过」之后它不再记着才算数）
        _note_file(tmp, "AbCdEf123456")
        f5 = CredentialFreshness("admin", "AbCdEf123456", note_path=note)
        check("改密前备忘记着这个口令 → 还有效", f5.evaluate() == FRESH, f5.evaluate())
        _note_file(tmp, "SomeOtherPassword")
        check("改密后备忘被同步更新 → 判定口令已作废", f5.evaluate() == STALE, f5.evaluate())

        # 从来没见过那份备忘（服务端关了 password_note）→ 不能凭空说作废
        f2 = CredentialFreshness("admin", "AbCdEf123456", note_path=tmp / "not_exist.txt")
        check("备忘从一开始就不存在 → 不误判作废", f2.evaluate() == FRESH, f2.evaluate())

        # 备忘记的是别的口令 → 也不是「我们的口令」
        other = tmp / "other_note.txt"
        other.write_text("用户：admin\n密码：SomeOtherPassword\n", encoding="utf-8")
        f3 = CredentialFreshness("admin", "AbCdEf123456", note_path=other)
        check("备忘记的是别的口令 → 不误判作废", f3.evaluate() == FRESH, f3.evaluate())

        # 放太久 → 收起
        f4 = CredentialFreshness("admin", "AbCdEf123456", note_path=None,
                                  ttl_seconds=1, caught_at=time.time() - 60)
        check("超过时限 → 判定为收起", f4.evaluate() == EXPIRED, f4.evaluate())

        # 外部判定
        f5 = CredentialFreshness("admin", "AbCdEf123456", note_path=None)
        f5.mark_stale()
        check("外部确认口令作废 → STALE", f5.evaluate() == STALE, f5.evaluate())
        f6 = CredentialFreshness("admin", "AbCdEf123456", note_path=None)
        f6.mark_used()
        check("登录成功后 → USED", f6.evaluate() == USED, f6.evaluate())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 服务端备忘路径：复用服务端的规则，不在启动器里另写一份
    path = server_note_path()
    check("能定位到服务端那份口令备忘", path is not None and path.name == "admin_password.txt",
          str(path))


def check_banner():
    """提示条本身：口令作废后必须从屏幕上撤掉。"""
    import tkinter as tk

    from gisds.launcher.app import Launcher

    launcher = Launcher()
    launcher.root.withdraw()
    try:
        launcher.log("    用户名：admin")
        launcher.log("    密码  ：AbCdEf123456")
        check("从日志里抓到用户名", launcher.first_username == "admin",
              str(launcher.first_username))
        check("从日志里抓到初始口令", launcher.first_password == "AbCdEf123456",
              str(launcher.first_password))
        check("提示条已经出现", launcher._first_banner is not None
              and bool(launcher._first_banner.winfo_manager()),
              str(launcher._first_banner.winfo_manager() if launcher._first_banner else None))
        check("提示条上带生成时间", launcher._first_time is not None
              and "生成于" in launcher._first_time.cget("text"),
              launcher._first_time.cget("text") if launcher._first_time else "")

        # 口令作废 → 口令和按钮都得撤掉，只留一句说明
        launcher._collapse_first_credentials("口令已变更，请用新口令登录")
        head = launcher._first_head.cget("text") if launcher._first_head else ""
        check("作废后提示条只留说明", head == "口令已变更，请用新口令登录", head)
        check("作废后口令不再显示",
              launcher._first_pwd_label is None
              and len(launcher._first_banner.winfo_children()) == 1,
              str(launcher._first_pwd_label))
        check("作废后按钮撤掉了", launcher._first_buttons is None)
    finally:
        launcher.cancel_tick()
        launcher.root.destroy()


def check_login():
    """一键登录：能用就登进去，被拒就把提示条撤掉。"""
    from gisds.launcher.app import Launcher

    with TestServer(
        port=_free_port(8873), require_login=True,
        extra_env={"GISDS_ADMIN_PASSWORD": "firstpass123"},
    ) as server:
        from gisds.launcher import api as lap

        old_base = lap.BASE
        lap.BASE = server.base
        try:
            app2 = Launcher()
            app2.root.withdraw()
            try:
                app2.log("    密码  ：firstpass123")
                check("抓到口令后能直接登录", app2.first_password == "firstpass123",
                      str(app2.first_password))
                app2.login_with_first_password()
                who = app2.who_label.cget("text")
                check("一键登录后顶栏显示身份", "admin" in who, who)
                users = lap.api_json("GET", "/api/admin/users")
                check("登录后能拉用户列表", "users" in (users or {}), str(users)[:80])
                # 断言要看「屏幕上还有没有那个口令」，而不是只盯内部引用 ——
                # 前者才是这个模块真正要保证的事（明文口令不许继续挂着）。
                #
                # 收集文本时**不许裸吞异常**：读不到控件文字时，下面的
                # 「口令不在里面」会**空过**（什么都没读到 → 断言通过），
                # 那就是「没报错」冒充「对」—— 正是第 14 条说的那种假绿。
                shown, collect_err = "", ""
                try:
                    for w in app2._first_banner.winfo_children():
                        try:
                            shown += str(w.cget("text") or "")
                        except Exception as exc:
                            collect_err = repr(exc)
                except Exception as exc:
                    collect_err = repr(exc)
                check("提示条文本读得到（读不到就不能判「口令不在里面」）",
                      not collect_err, collect_err)
                check("登录成功后提示条里不再有口令",
                      "firstpass123" not in shown, shown[:60])
                check("登录成功后提示条收掉了", app2._first_buttons is None
                      and app2._first_pwd_label is None)
            finally:
                app2.cancel_tick()
                app2.root.destroy()

            # 口令是错的（已经被改过了）→ 服务端 401 → 提示条必须撤掉并说明
            app3 = Launcher()
            app3.root.withdraw()
            try:
                app3.log("    密码  ：thisPasswordIsStale")
                app3.login_with_first_password()
                head = app3._first_head.cget("text") if app3._first_head else ""
                check("口令被拒后提示条说明已变更", head == "口令已变更，请用新口令登录", head)
                check("口令被拒后不再显示口令", app3._first_pwd_label is None)
                check("口令被拒后按钮撤掉了", app3._first_buttons is None)
                check("口令被拒后没有登录", "未登录" in app3.who_label.cget("text"),
                      app3.who_label.cget("text"))
            finally:
                app3.cancel_tick()
                app3.root.destroy()
        finally:
            lap.BASE = old_base


def check_stale_after_change():
    """端到端：服务端真的改密之后，提示条会自己收起。

    这是最容易出错的一环 —— 单元判据对了，不代表接在真服务上也对。
    这里起一个临时服务（口令随机生成），让启动器从它的日志里捞口令，
    再走改密接口把口令换掉，看提示条会不会自己察觉并收起。
    """
    import json
    import urllib.request

    from gisds.core.auth import password_note_path
    from gisds.launcher import api as lap
    from gisds.launcher.app import Launcher

    def pump(app, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            app.root.update()
            time.sleep(0.05)

    with TestServer(port=_free_port(8891), require_login=True) as server:
        old_base = lap.BASE
        lap.BASE = server.base
        try:
            log = Path(server.log_path).read_text(encoding="utf-8", errors="replace")
            m = re.search(r"密码\s*：\s*(\S+)", log)
            check("临时服务自举时打印了初始口令", m is not None)
            if not m:
                return
            initial = m.group(1)

            db = Path(server.env()["GISDS_DB_PATH"])
            note = password_note_path(db)
            check("自举后口令备忘已落盘", note.is_file(), str(note))

            app = Launcher()
            app.root.withdraw()
            try:
                for line in log.splitlines():
                    app.log(line)
                check("启动器捞到的就是服务自举那个口令",
                      app.first_password == initial, str(app.first_password))
                # 判据要指向这个临时服务的备忘（默认指向正式库那份）
                if app._freshness:
                    app._freshness.note_path = note

                # 等判据「见过」这份备忘记着这个口令
                for _ in range(40):
                    pump(app, 0.1)
                    if app._freshness and app._freshness._note_armed:
                        break
                check("判据已经就位（见过这份备忘）",
                      bool(app._freshness and app._freshness._note_armed))

                # 登录，然后改密
                data = lap.api_json("POST", "/api/auth/login",
                                    body={"username": "admin", "password": initial})
                lap.set_token(data.get("token"))
                me = lap.api_json("GET", "/api/auth/me")
                uid = (me.get("principal") or {}).get("user_id")
                check("用初始口令登录成功", bool(uid), str(uid))

                req = urllib.request.Request(
                    server.base + f"/api/admin/users/{uid}",
                    data=json.dumps({"password": "brandNewPass456"}).encode(),
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {data.get('token')}"},
                    method="PATCH")
                with urllib.request.urlopen(req, timeout=20) as resp:
                    check("改密接口返回 200", resp.status == 200, str(resp.status))

                # 服务端把备忘更新成新口令了
                text = note.read_text(encoding="utf-8") if note.is_file() else ""
                check("改密后备忘记的是新口令",
                      "brandNewPass456" in text and initial not in text,
                      text.splitlines()[:2])

                # 提示条应该自己收起
                pump(app, 2.5)
                head = app._first_head.cget("text") if app._first_head else ""
                check("改密后提示条自己收起并说明", head == "口令已变更，请用新口令登录", head)
                check("改密后不再显示旧口令", app._first_pwd_label is None)
                check("改密后按钮撤掉了", app._first_buttons is None)
            finally:
                app.cancel_tick()
                app.root.destroy()
                lap.set_token(None)
        finally:
            lap.BASE = old_base


def main() -> int:
    print("== 初始口令的新鲜度判断 ==")
    check_freshness()
    print("== 提示条的显示与收起 ==")
    check_banner()
    print("== 一键登录 ==")
    check_login()
    print("== 服务端真的改密之后 ==")
    check_stale_after_change()

    # 断言总数必须钉死：少一条就说明有断言被条件跳过了。
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被跳过了）",
          checked == EXPECTED_CHECKS, str(checked))

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
    import os as _os

    _os._exit(code)
