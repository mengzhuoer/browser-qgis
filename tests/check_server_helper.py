# -*- coding: utf-8 -*-
"""验证**测试脚手架自己**（`tests/server_helper.py`）。

脚手架是 20 多支自检的公共底座：它拉起临时服务、用完收掉。
脚手架出问题时，症状是「整套自检莫名其妙全红」或者「正式实例在回归期间消失」——
都很难往脚手架上想，所以它值得有自己的一支自检。

这轮钉的是四件事：

1. **临时服务永远不占正式端口 8848**（用户自己的实例就在那个端口上）。
2. **端口补刀只补「监听者」** —— netstat 里「连到这个端口的客户端」也占一行，
   把它当成监听者补刀 = 杀掉测试自己（httpx 客户端在本进程里）或无头浏览器。
3. `TestServer` 不接受正式端口。
4. **端口被别人占着时，不许连到别人的服务**（串台）—— 见 `check_no_cross_talk()`。
   真踩过：同一支自检并发跑两遍，第二遍 22 条断言全红，
   根因是它连到了第一遍的临时服务（用户库是同一个 → 注册账号撞「已被占用」）。

跑法：``run-qgis-python.bat tests/check_server_helper.py``
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from server_helper import (  # noqa: E402
    PROD_PORT,
    TestServer,
    _free_port,
    _pid_chain,
    _pids_listening_on,
)

fails: list[str] = []
checked = 0

# 断言总数（**不含**末尾那条「总数」断言本身）。
#
# 钉死它：任何一条断言被条件跳过，汇总数就会跟着变 —— 那样就看不出
# 「到底哪几条没验」。断言空过比断言失败更难查。
#
# 这里的 25 = 端口护栏 7 + **正在监听的端口不被挑走 3** + 端口补刀 5
#            + 真起服务 4 + 生产 users/ 目录清理 2 + 端口归属（不串台）4。
# **所有断言都必须无条件执行** —— 条件分支里漏掉的断言会让这个数对不上，
# 钉子当场变红（那正是它要干的事）。
EXPECTED_CHECKS = 25


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def _spawn_listener(port: int) -> subprocess.Popen:
    """起一个只管 listen 的子进程，当作「监听者」。"""
    code = (
        "import socket,time\n"
        "s=socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
        f"s.bind(('127.0.0.1',{port}))\n"
        "s.listen(5)\n"
        "time.sleep(30)\n"
    )
    return subprocess.Popen([sys.executable, "-c", code])


def check_port_guards() -> None:
    print("\n-- 正式端口护栏 --")
    check("正式端口常量是 8848", PROD_PORT == 8848, str(PROD_PORT))

    # 显式点名要 8848 也不行
    got = _free_port(PROD_PORT)
    check("_free_port(8848) 不给正式端口", got != PROD_PORT, f"拿到 {got}")

    # 即使有人传一个比 8848 小得多的 wanted，也不该落到 8848
    low = _free_port(8000)
    check("_free_port(8000) 也不给正式端口", low != PROD_PORT, f"拿到 {low}")
    check("_free_port(8000) 落在正式端口之上", low > PROD_PORT, f"拿到 {low}")

    # TestServer 收到正式端口时不会「照办」，而是把它挪走
    ts = TestServer(port=PROD_PORT)
    check("TestServer(port=8848) 被挪到别的端口", ts.port != PROD_PORT, f"落到 {ts.port}")
    check("挪到的端口在正式端口之上", ts.port > PROD_PORT, f"落到 {ts.port}")

    # __init__ 的双保险：万一 _free_port 哪天改坏了（真的返回 8848），
    # 必须当场拒绝起，而不是「在用户的端口上开一个临时服务」。
    # 这条分支平时不会被走到（_free_port 已经挡住了），所以只能打桩触发。
    import server_helper as sh

    origin = sh._free_port
    sh._free_port = lambda wanted: PROD_PORT  # type: ignore[assignment]
    try:
        TestServer(port=PROD_PORT)
        check("_free_port 失效时 TestServer 拒绝起", False, "居然起成了")
    except RuntimeError:
        check("_free_port 失效时 TestServer 拒绝起", True)
    except Exception as exc:  # noqa: BLE001
        check("_free_port 失效时 TestServer 拒绝起", False, f"抛的是 {exc!r}，不是 RuntimeError")
    finally:
        sh._free_port = origin  # type: ignore[assignment]


def check_sweep_targets() -> None:
    print("\n-- 端口补刀只补「监听者」 --")
    port = _free_port(8888)
    child = _spawn_listener(port)
    sock = None
    try:
        # 等它真的在监听（子进程可能因为环境没配好起不来，那要报清楚，
        # 而不是让 connect 抛一个看不懂的 ConnectionRefused）
        up = False
        for _ in range(60):
            if child.poll() is not None:
                break
            if child.pid in _pids_listening_on(port):
                up = True
                break
            time.sleep(0.2)
        check("监听者子进程起来了", up, f"pid {child.pid}，端口 {port}")
        # **这里不能 return** —— 子进程起不来时，后面的断言会被**静默跳过**，
        # 而结尾照样打印「N 通过 / 0 失败」，看报告的人根本看不出少了什么。
        # 少验一条比验失败更难查。所以：起不来就让它们**失败**，不跳过。
        found: list[int] = []
        if up:
            sock = socket.create_connection(("127.0.0.1", port))
            sock.sendall(b"ping")
            time.sleep(0.8)  # 等 netstat 把 ESTABLISHED 那行刷新出来
            found = _pids_listening_on(port)
        check("能认出监听者", up and child.pid in found, f"名单 {found}，监听者 {child.pid}")
        check(
            "**不把客户端算进补刀名单**",
            up and os.getpid() not in found,
            f"名单 {found}，本进程 {os.getpid()}",
        )
        check("补刀名单里没有别的陌生 pid", up and set(found) == {child.pid}, f"名单 {found}")
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        try:
            child.kill()
            child.wait(timeout=10)
        except Exception:
            pass

    # 没人监听的端口 → 空名单（补刀不能乱杀）
    idle = _free_port(8889)
    check("没人监听的端口返回空名单", _pids_listening_on(idle) == [], f"端口 {idle}")


def check_no_user_dir_leak() -> None:
    """临时服务不该在**生产** users/ 下留空目录。

    自检的临时服务有时直接用生产 data_root（``check_webui.py`` 就是 ——
    它要读示例数据，没传 ``data_root``），每跑一轮就注册一个临时用户，
    在 ``data/users/`` 下留一个空目录。目录会一直涨，而
    ``registry.scan()`` 每次都要遍历它们（``core/registry.py``）——
    直接拖慢每一次数据源扫描。

    清理机制在 ``TestServer`` 里：``start()`` 拍下「跑之前 users/ 下有哪些目录」，
    ``stop()`` 只删「**本次运行新建的**」+「**仍然是空的**」那两个条件的目录。
    所以这里两条规则都要验：

    1. **服务跑起来之后**新建的空目录 → stop() 之后应该没了
    2. **跑之前就在的**空目录 → **不能**被删（那是别的会话/用户建的）

    探针必须在 ``TestServer`` **起来之后**建，才算「本次运行新建的」——
    建在前面就落进快照里，按规则本来就不该动它。

    这里用**独立的 data_root**（临时目录），不碰生产 ``data/users/``：
    清理逻辑在两种情况下是同一段代码（``_users_root()`` 只决定根目录在哪），
    但用独立目录就不会和**并行跑的另一支自检**互相删探针 ——
    「本次运行新建的」在两个并发实例之间是区分不了的，探针建在生产目录里
    就会偶发假红（实测过一次：另一支自检把这支的探针删了）。
    """
    print("\n-- 临时服务不在用户空间留空目录 --")
    tmp_root = Path(tempfile.mkdtemp(prefix="gisds_userdir_"))
    users_root = tmp_root / "users"
    users_root.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    # ① 跑之前就有的空目录（故意建在 TestServer 起来之前）
    keep = users_root / ("selftest_keep_" + str(pid))
    # ② 服务跑起来之后新建的空目录（模拟服务注册了一个用户）
    fresh = users_root / ("selftest_leak_" + str(pid))
    try:
        keep.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # noqa: BLE001
        check("能在用户空间下造一个空目录", False, repr(exc))
        shutil.rmtree(tmp_root, ignore_errors=True)
        return
    try:
        with TestServer(port=_free_port(8893), data_root=tmp_root):
            fresh.mkdir(parents=True, exist_ok=True)
        check("本次运行新建的空目录被清掉了", not fresh.exists(),
              "已清掉" if not fresh.exists() else f"{fresh.name} 还在")
        check("跑之前就有的空目录没被动（别的会话/用户的不能删）", keep.exists(),
              "还在" if keep.exists() else "被误删了")
    finally:
        for path in (fresh, keep):
            try:
                path.rmdir()
            except OSError:
                pass
        shutil.rmtree(tmp_root, ignore_errors=True)


def check_real_server() -> None:
    print("\n-- 真起一个临时服务 --")
    try:
        import httpx
    except Exception as exc:  # noqa: BLE001
        check("httpx 可用", False, str(exc))
        return
    with TestServer(port=_free_port(8886)) as server:
        with httpx.Client(timeout=20) as client:
            resp = client.get(server.base + "/healthz")
            check("临时服务活着", resp.status_code == 200, str(resp.status_code))
            found = _pids_listening_on(server.port)
            check("临时服务端口能认出监听者", len(found) == 1, f"名单 {found}")
            check(
                "**连着它的客户端没被算进去**",
                os.getpid() not in found,
                f"名单 {found}，本进程 {os.getpid()}",
            )
        # 连接关掉后再看一次：只剩监听者
        found2 = _pids_listening_on(server.port)
        check("连接关掉后名单仍然只有监听者", len(found2) <= 1, f"名单 {found2}")


def check_no_cross_talk() -> None:
    """端口被**别人**占着时，不许连到别人的服务。

    ``_free_port()`` 只是「挑一个**看起来**空闲的端口」，bind 一下就放开，
    **并不占着**。两个进程同时挑会挑中同一个端口，而就绪检查
    （「这个端口上 /readyz 返回 200 吗」）在端口被**别人**占着时照样通过 ——
    于是测试连到了**别人的服务**：用户库是同一个 → 注册账号撞「已被占用」，
    页面被对方的鉴权拦到登录页。看起来像产品坏了，其实是串台。
    （真踩过：同一支自检并发跑两遍，第二遍 22 条断言全红，根因就是这个。）

    判据是「监听者的祖先链里有没有我们这个 cmd.exe」。
    """
    print("\n-- 端口归属：不会连到别人的服务 --")

    chain, complete = _pid_chain(os.getpid())
    check("能查出自己的祖先链（含自己）",
          complete and bool(chain) and chain[0] == os.getpid(), f"chain={chain} complete={complete}")

    tmp_root = Path(tempfile.mkdtemp(prefix="gisds_crosstalk_"))
    (tmp_root / "shared").mkdir(parents=True, exist_ok=True)
    (tmp_root / "users").mkdir(parents=True, exist_ok=True)
    other_port = _free_port(8896)
    child = _spawn_listener(other_port)
    sock = None
    try:
        up = False
        for _ in range(50):
            if child.pid in _pids_listening_on(other_port):
                up = True
                break
            time.sleep(0.2)
        check("无关监听者起来了", up, f"pid {child.pid}，端口 {other_port}")

        with TestServer(port=_free_port(8887), data_root=tmp_root) as server:
            check("自己的端口：认得出是自己的服务", server._owns_port(),
                  f"端口 {server.port}")
            check("别人的端口：认得出**不是**自己的服务",
                  up and not server._owns_port(other_port),
                  f"端口 {other_port}，监听者 {child.pid}")
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        try:
            child.kill()
            child.wait(timeout=10)
        except Exception:
            pass
        shutil.rmtree(tmp_root, ignore_errors=True)


def check_busy_port_not_picked() -> None:
    """**端口有人在监听时，_free_port 绝不能把它挑走。**

    这是那次事故的根因，也是双探测的钉子。

    原来 ``_free_port`` 只做「bind 一下」来判断空闲，而 **Windows 上带
    ``SO_REUSEADDR`` 的 bind 会在一个**已经被别人 LISTEN** 的端口上成功**
    → 探测说「空着」，端口却明明有人在用。真踩过：浏览器自检单独跑绿、
    回归里崩 —— 浏览器绑不上那个端口，我们按 ``/json/list`` 连过去连到的
    是**上一支自检残留的 Edge**，它被回收时 WebSocket 断在半路。

    现在 ``_free_port`` 是**双探测**：① 「连一下」（没人在听）② bind
    （**不带** SO_REUSEADDR，没被占）。**这条就是那个双探测的钉子** ——
    哪天有人改回单探测，这里当场变红。

    另附一条把「为什么光靠 bind 不够」**实测**出来 —— 不然那只是口口相传：
    带 SO_REUSEADDR 的 bind 确实能绑上别人正在监听的端口。
    """
    print("\n-- 正在监听的端口不能被挑走 --")
    busy = _free_port(8894)
    child = _spawn_listener(busy)
    try:
        up = False
        for _ in range(60):
            if child.poll() is not None:
                break
            if child.pid in _pids_listening_on(busy):
                up = True
                break
            time.sleep(0.2)
        check("监听者子进程起来了", up, f"pid {child.pid}，端口 {busy}")

        # 实测：带 SO_REUSEADDR 的 bind 能绑上别人正在监听的端口
        # （Windows 的行为。哪天这条不成立了，说明单探测也许就够了 ——
        #   但双探测依然是对的，只是这条需要跟着改文案）
        reuse_bound = False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", busy))
                reuse_bound = True
            except OSError:
                reuse_bound = False
        check("**实测：带 SO_REUSEADDR 的 bind 能绑上别人正在监听的端口**"
              "（这就是只做 bind 探测不够的原因）",
              reuse_bound, f"端口 {busy} 上 bind {'成功' if reuse_bound else '失败'}")

        picked = _free_port(busy)
        check("端口有人在监听时 _free_port 不挑它",
              up and picked != busy, f"本该跳过 {busy}，却拿到 {picked}")
    finally:
        try:
            child.kill()
            child.wait(timeout=10)
        except Exception:
            pass


def main() -> int:
    check_port_guards()
    check_busy_port_not_picked()
    check_sweep_targets()
    check_real_server()
    check_no_user_dir_leak()
    check_no_cross_talk()
    # 断言总数必须钉死：任何一条断言被条件跳过，汇总数就会跟着变 ——
    # 那样就看不出「到底哪几条没验」。断言空过比断言失败更难查。
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
