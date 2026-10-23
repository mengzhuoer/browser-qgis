"""测试用：拉起一个独立的服务实例。

自检脚本不依赖「外面已经跑着的服务」，自己起一个、用完关掉，
端口和数据目录都跟正式服务错开，互不干扰。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT_ROOT / "run-qgis-python.bat"

#: 正式实例的端口。临时服务**永远不能**用它。
#:
#: 端口补刀（``stop()`` 里按端口找漏掉的进程）认的是「谁占着这个端口」。
#: 一旦临时服务挑中 8848，补刀就会把**用户的正式实例**当成漏掉的残留杀掉 ——
#: 真出过事：18:08:49 正式服务没了，服务日志停在一条正常的 200 请求上，
#: 没有任何异常、没有任何报错，而当时那轮全量回归仍然全绿。
#: 正式实例永远在 8848，临时服务永远在 8851+，这条线不能让步。
PROD_PORT = 8848

# 备忘文件名的规则统一在 core.auth 里，别在这儿另写一份 —— 写两份迟早会不一致
# （本项目已经吃过一次：stem 写成 "gisds.db" 导致生产备忘换了名字）。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gisds.core.auth import password_note_path  # noqa: E402


def _pid_alive(pid: int) -> bool:
    """进程还在吗（Windows 上不能用 os.kill(pid, 0)，那会真把进程杀掉）。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
    except Exception:
        return True  # 查不到就当它活着 —— 宁可不清，也不要误删别人的
    return str(pid) in (out.stdout or "")


def _pids_listening_on(port: int) -> list[int]:
    """谁在监听这个端口（返回 pid 列表）。

    这是补刀时唯一可靠的凭据：这个端口是**我们**动态挑的，
    上面只可能是我们起的那个服务 —— 正式实例永远在 8848，永远碰不到。
    （按 pid 血缘找后代不可靠：pid 会被系统复用，可能把正式实例误认成后代。）

    **只认 LISTENING、且本地地址就是这个端口的连接。**
    netstat 里「连到这个端口的客户端」也占着一行（ESTABLISHED，出现在
    *对端*地址那一列）—— 把它当成监听者补刀，等于杀掉**测试自己**
    （httpx 客户端就在本进程里）或无头浏览器。实测过：起一个监听 + 连上去，
    宽松的匹配会把**客户端进程**一起收进补刀名单。
    """
    try:
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, errors="replace", timeout=20,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in (out.stdout or "").splitlines():
        parts = line.split()
        # TCP 行固定 5 列：Proto / Local / Foreign / State / PID
        if len(parts) < 5 or parts[0] != "TCP":
            continue
        if parts[3] != "LISTENING":
            continue
        # 只看**本地地址**那一列（对端端口相同不算监听）
        if not parts[1].endswith(f":{port}"):
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def _sweep_stale_workspace() -> None:
    """清掉上一轮跑崩/被中断留下的临时文件。

    临时文件名里带 pid（``testserver_<port>_<pid>.db``），属主进程已经不在的
    就是死文件，可以安全清掉。属主还活着的跳过 —— 那是并行会话正在跑的。
    """
    ws = PROJECT_ROOT / "tests" / "_workspace"
    if not ws.is_dir():
        return
    for path in list(ws.glob("testserver_*")):
        m = re.search(r"_(\d+)(?:\.db(?:-wal|-shm)?|_admin_password\.txt)$", path.name)
        if not m:
            continue
        pid = int(m.group(1))
        if pid == os.getpid() or _pid_alive(pid):
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            # 删不掉通常说明**还有活着的进程占着它**（多半是上次 taskkill /T
            # 漏掉的服务进程，见 stop() 里的端口补刀）。别把失败悄悄咽掉 ——
            # 留下的死文件会让下一个自检踩到同一个坑。
            print(f"[gisds] 死文件删不掉（{path.name}）：{exc}")


def _port_in_use(port: int) -> bool:
    """这个端口上**有人在监听**吗。

    为什么不能只靠「bind 一下试试」：Windows 上 ``SO_REUSEADDR`` 允许第二个
    socket 绑到一个**已经被别人 LISTEN** 的端口 —— 于是 bind 成功，端口却明明
    有人在用。

    那个洞的代价是「连到了别人的服务」，而且现象极难查：

    * **CDP 端口**：新起的浏览器绑不上这个端口，而我们按 ``/json/list``
      连过去，连到的是**上一支自检残留的浏览器**（Edge 的子进程在父进程被
      terminate 之后还活着，端口继续被占着）；它随时被回收，我们的
      WebSocket 就断在半路 —— 症状是「单独跑绿、回归里崩」。
    * **服务端口**：就绪检查打到别人的临时服务上 → 页面被对方的鉴权拦到
      登录页，看起来像网页坏了，其实是串台。

    所以改成**连一下**：连得上 = 有人在监听 = 换端口；连不上（被拒）= 真空。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _free_port(wanted: int) -> int:
    """端口被占就往后顺延一个。

    多个自检脚本（甚至多个会话同时跑）会用同一个端口号。抢不到端口时
    硬起服务会失败得更隐蔽：TestServer 的健康检查可能连到**别人**的临时服务，
    于是页面被对方的鉴权拦到登录页，测试报「页面加载完成」失败 ——
    看起来像网页坏了，其实是串台。所以这里直接换个空闲端口。

    判断「空闲」要**两条都过**：

    1. **连一下**（``_port_in_use``）—— 没人监听
    2. **bind 一下**（不带 SO_REUSEADDR）—— 没人占着

    只做 bind 是不够的：Windows 上带 SO_REUSEADDR 的 bind 会在一个
    **已经被别人 LISTEN** 的端口上成功，于是我们挑中一个正被占用的端口
    （真踩过：浏览器自检「单独跑绿、回归里崩」，根因就是它连到了上一支
    自检残留的 Edge 子进程）。

    **8848 是正式实例的端口，永远跳过**（见 ``PROD_PORT``）：临时服务占了它，
    ``stop()`` 的端口补刀就会把用户的正式实例杀掉。
    """
    start = wanted if wanted > PROD_PORT else PROD_PORT + 1
    for candidate in range(start, start + 30):
        if candidate == PROD_PORT:
            continue
        if _port_in_use(candidate):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
        return candidate
    return start


def kill_tree(pid: int) -> None:
    """把一个进程**连它的子进程一起**收掉（``taskkill /T /F``）。

    只 ``terminate()`` 掉父进程是不够的：Edge 的子进程还活着、端口继续被
    占着，下一支自检就撞车（实测跑完一轮能剩 25 个 msedge 进程）——
    然后我们按 ``/json/list`` 连过去，连到的是那个残留浏览器，
    WebSocket 断在半路。

    浏览器自检收尾一律用这个，别用裸 ``terminate()``。
    """
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True, timeout=20, text=True, errors="replace",
        )
    except Exception:
        pass


def _pid_chain(pid: int) -> tuple[list[int], bool]:
    """某个 pid 的祖先链（含它自己），以及「链条是否查全了」。

    第二个返回值是 False 表示**查不全**（工具不可用）——
    这时不能据此断言「端口上的服务不是我们的」。

    整条链**一次** PowerShell 调用问完：一次调用 ~0.5 秒，
    而一次自检要起好几个临时服务，逐层问会明显拖慢。

    为什么非要这个：``_free_port()`` 只是「挑一个**看起来**空闲的端口」，
    bind 一下就放开，**并不占着**。两个进程同时挑，会挑中**同一个**端口。
    而 ``_start_once()`` 的就绪检查是「这个端口上 /readyz 返回 200 吗」——
    端口被**别人**占着时，这个检查照样通过，于是**测试连到了别人的服务**。
    后果是「用户库是同一个」→ 注册账号时撞「用户名已被占用」，
    或者页面被对方的鉴权拦到登录页 —— 看起来像产品坏了，其实是串台。
    （真踩过：同一支自检并发跑两遍，第二遍的 22 条断言全红，
    根因就是它连到了第一遍的临时服务。）
    """
    ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    script = (
        "$p={pid}; $chain=@($p); "
        "for ($i=0; $i -lt 16; $i++) {{ "
        '$pp=(Get-CimInstance Win32_Process -Filter "ProcessId=$p").ParentProcessId; '
        "if (-not $pp) {{ break }}; $chain+=$pp; $p=$pp }}; "
        "$chain -join ','"
    ).format(pid=int(pid))
    try:
        out = subprocess.run(
            [ps, "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=25,
        )
    except Exception:
        return [int(pid)], False
    value = (out.stdout or "").strip()
    if out.returncode != 0 or not value:
        return [int(pid)], False
    chain: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if part.isdigit():
            chain.append(int(part))
    if not chain or chain[0] != int(pid):
        return [int(pid)], False
    return chain, True


class TestServer:
    """临时服务实例。"""

    def __init__(
        self,
        port: int = 8851,
        require_login: bool = False,
        data_root: Path | None = None,
        extra_env: dict | None = None,
    ):
        self.port = _free_port(port)
        if self.port == PROD_PORT:  # 双保险：_free_port 不会给这个端口
            raise RuntimeError(f"临时服务不能用正式实例的端口 {PROD_PORT}")
        self.require_login = require_login
        self.data_root = data_root
        self.extra_env = extra_env or {}
        self.proc: subprocess.Popen | None = None
        self.base = f"http://127.0.0.1:{self.port}"
        # 跑之前 users/ 下有哪些目录（用来判断「这次运行新建了哪些」）
        self._users_before: set[str] = set()

    def _users_root(self) -> Path:
        """服务实际在用的用户空间根目录。

        ``data_root`` 没给时，服务用**生产默认**（``PROJECT_ROOT/data``）——
        这正是污染的来源：自检的临时服务直接在生产目录里注册用户。
        """
        if self.data_root:
            return Path(self.data_root) / "users"
        return PROJECT_ROOT / "data" / "users"

    def _snapshot_user_dirs(self) -> set[str]:
        try:
            return {p.name for p in self._users_root().iterdir() if p.is_dir()}
        except OSError:
            return set()

    def _cleanup_new_user_dirs(self) -> None:
        """删掉**本次运行新建的**空用户目录。

        自检的临时服务有时直接用生产 data_root（``check_webui.py`` 就是 ——
        它要读示例数据，没传 ``data_root``），每跑一轮就注册一个临时用户，
        在 ``data/users/`` 下留一个**空目录**。这些目录会一直涨，而且
        ``registry.scan()`` 每次都要遍历它们（``core/registry.py`` 里
        ``for child in sorted(self.users_root.iterdir())``）—— 直接拖慢
        **每一次**数据源扫描，登记结果/加载图层都得走这条路。

        只删「本次运行新建的」+「仍然是空的」两个条件：
          * 「本次新建」→ 别的会话/用户早先建的不动
          * 「仍然是空的」→ 里面有数据的一律不碰

        删掉是安全的：用户空间目录**按需重建** —— 取工作区时
        ``deps.get_workspace()`` 里有 ``root.mkdir(parents=True, exist_ok=True)``。
        """
        root = self._users_root()
        try:
            current = {p.name for p in root.iterdir() if p.is_dir()}
        except OSError:
            return
        for name in current - self._users_before:
            path = root / name
            try:
                if any(path.iterdir()):
                    continue  # 里面有东西 —— 不碰
                path.rmdir()
            except OSError:
                pass

    def env(self) -> dict:
        env = dict(os.environ)
        env["GISDS_PORT"] = str(self.port)
        env["GISDS_HOST"] = "127.0.0.1"
        env["GISDS_REQUIRE_LOGIN"] = "1" if self.require_login else "0"
        if self.data_root:
            env["GISDS_DATA_ROOT"] = str(self.data_root)
            env["GISDS_SHARED_ROOT"] = str(Path(self.data_root) / "shared")
            env["GISDS_USERS_ROOT"] = str(Path(self.data_root) / "users")
        # 用户库永远用独立的：自检不该打开生产库 data/gisds.db，
        # 更不该碰到里面的 admin 账号。
        #
        # 路径要**每次运行都不一样**：admin 的初始密码只在库为空（首次创建）时
        # 生效，路径固定的话第二次跑就会拿着上次的库、却用这次的初始密码去登录，
        # 于是 401 —— 而且是那种「上一轮还好好的」的玄学失败。
        db = (
            Path(self.data_root) / "gisds.db"
            if self.data_root
            else PROJECT_ROOT / "tests" / "_workspace" / f"testserver_{self.port}_{os.getpid()}.db"
        )
        db.parent.mkdir(parents=True, exist_ok=True)
        env["GISDS_DB_PATH"] = str(db)
        # 内置共享超级账号：**临时服务一律不带**。
        #
        # 临时服务是**继承生产 config.json** 起来的，而生产配置里配着共享超级账号
        # （那把「所有用户都能用的」万能钥匙）。顺着继承下来会出两件事：
        #   * 临时库的 admin 口令会被**改成**那把万能钥匙 → 自检里
        #     「用自己的口令登录」那条直接 401（还很难看出是被谁改的）；
        #   * 那把钥匙的明文会被抄进临时库、乃至临时库旁边的口令备忘。
        # 跟「自检不该打开生产库」是同一条道理：临时实例不该继承生产的凭据。
        # 要测超级账号那套的脚本，用 extra_env 显式打开（extra_env 最后覆盖）。
        env["GISDS_SUPER_ADMIN_PASSWORD"] = "off"
        env.update(self.extra_env)
        return env

    def start(self, timeout: float = 90.0, attempts: int = 3) -> bool:
        """起服务；端口被别的并发实例抢了就换一个再试。

        ``_free_port()`` 只是「挑一个**看起来**空闲的端口」—— bind 一下就放开，
        **并不占着**。所以两个进程同时挑，会挑中**同一个**端口，后起的那个
        服务直接起不来（实测：两支自检并行跑，一支撞端口，报「端口被占」，
        整支脚本跟着红）。

        这里起不来就换一个端口重试：宁可多试几次，也不要让「端口撞了」
        变成一条假红。真起不来（QGIS 初始化失败之类）重试也白试，三次后
        照样返回 False —— **该红还是红**。
        """
        for attempt in range(max(1, attempts)):
            if attempt:
                self.port = _free_port(self.port + 1)
                self.base = f"http://127.0.0.1:{self.port}"
            if self._start_once(timeout):
                return True
            self._abort()
        return False

    def _abort(self) -> None:
        """一次启动失败后的轻量清理（不做端口补刀那一整套）。"""
        if self.proc:
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                    capture_output=True, timeout=20, text=True, errors="replace",
                )
            except Exception:
                pass
            self.proc = None
        try:
            if getattr(self, "log_file", None):
                self.log_file.close()
        except Exception:
            pass
        # 这一轮尝试用过的临时用户库也要删掉 —— 否则它会**永远留在工作区**：
        # 文件名里带的是本进程的 pid，而这个进程还活着，
        # ``_sweep_stale_workspace()`` 按 pid 判活就永远不会去删它。
        # （重试时端口会变、库名会变，所以上一次尝试的库是另一份文件。）
        if not self.data_root:
            base = PROJECT_ROOT / "tests" / "_workspace" / f"testserver_{self.port}_{os.getpid()}.db"
            for path in (base, Path(str(base) + "-wal"), Path(str(base) + "-shm")):
                for _ in range(10):
                    try:
                        path.unlink()
                        break
                    except FileNotFoundError:
                        break
                    except OSError:
                        time.sleep(0.2)

    def _owns_port(self, port: int | None = None) -> bool:
        """这个端口上的监听进程，是不是**我们**起的？

        判据：监听者的**祖先链**里要包含我们这个 cmd.exe
        （cmd.exe /c run-qgis-python.bat -> python3.exe，监听的是最后那个）。
        链条查不全（工具不可用）时**不判死刑** —— 宁可放过，也不误杀。

        ``port`` 不给就是自己的端口；给了别的端口就是问「那个端口是不是我的」。
        """
        target = self.port if port is None else port
        if self.proc is None:
            return False
        listeners = _pids_listening_on(target)
        if not listeners:
            return False
        for pid in listeners:
            chain, complete = _pid_chain(pid)
            if not complete:
                return True   # 查不全就不下结论
            if self.proc.pid in chain:
                return True
        return False

    def _start_once(self, timeout: float) -> bool:
        # 记下「跑之前 users/ 下有哪些目录」—— stop() 时要靠它分清
        # 「这次运行新建的」和「本来就在的」（见 _cleanup_new_user_dirs）。
        self._users_before = self._snapshot_user_dirs()
        self.log_path = PROJECT_ROOT / "logs" / f"testserver_{self.port}.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file = open(self.log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            ["cmd.exe", "/c", str(LAUNCHER), "-m", "gisds", "serve"],
            cwd=str(PROJECT_ROOT),
            env=self.env(),
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # 等的是**就绪**（/readyz），不是存活（/healthz）。
                # /healthz 现在是纯存活语义 —— 进程在就 200，启动期也是 ——
                # 拿它当「服务能干活了」的信号，会在 lifespan 还没跑完时就往下走，
                # 于是「管理员口令备忘写了没」这类断言变成偶发失败。
                r = httpx.get(self.base + "/readyz", timeout=3)
                if r.status_code == 200:
                    # **再确认一下：这个端口上的服务是我们起的吗？**
                    # 端口被**别人**占着时，上面的就绪检查照样通过 ——
                    # 于是测试会连到别人的服务（用户库是同一个 → 注册账号撞名，
                    # 页面被对方的鉴权拦到登录页），看着像产品坏了，其实是串台。
                    if not self._owns_port():
                        return False
                    return True
            except Exception:
                pass
            if self.proc.poll() is not None:
                return False
            time.sleep(0.5)
        return False

    def stop(self):
        if not self.proc:
            return
        # Windows 上 terminate 只杀 cmd.exe，python 子进程会留下并占住端口，
        # 所以按进程树杀。
        #
        # 但 /T 也会漏：批处理中间那层 cmd 先退，python3 被重新挂到别的父进程上，
        # 进程树断了，/T 就够不着它 —— 漏掉的那个继续占端口、占着 SQLite 库文件
        # （库文件被锁 → 删不掉 → WinError 32 → 工作区堆死文件）。
        #
        # 补刀**按端口**做，不按 pid 血缘做：端口是我们自己动态挑的，
        # 上面只可能是我们这个服务；而 pid 会被系统复用 ——
        # 「按血缘找后代」可能把**正式实例**误认成后代杀掉（真出过事：
        # 正式服务在回归期间消失，监听 8848 的那个被当成残留清掉了）。
        # 端口是唯一可靠的凭据：正式实例永远在 8848，我们永远在 8851+。
        try:
            res = subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                capture_output=True, timeout=20, text=True, errors="replace",
            )
            if res.returncode != 0:
                print(f"[gisds] taskkill 没成功（{res.returncode}）：{(res.stdout or res.stderr or '').strip()}")
        except Exception as exc:
            print(f"[gisds] taskkill 异常：{exc}")
        # 补刀：谁还占着**我们这个端口**，谁就是漏掉的服务进程
        #
        # 但绝不补刀正式实例的端口 —— 那不是我们起的东西，杀了就是把用户的
        # 服务弄没了（真出过事：18:08:49 正式服务消失，无任何报错）。
        if self.port == PROD_PORT:
            print(f"[gisds] 拒绝在正式端口 {PROD_PORT} 上补刀")
        else:
            for pid in _pids_listening_on(self.port):
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True, timeout=20, text=True, errors="replace",
                    )
                    print(f"[gisds] 端口 {self.port} 还被 pid {pid} 占着，已补刀")
                except Exception:
                    pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        try:
            self.log_file.close()
        except Exception:
            pass

        # 等端口真正释放：服务进程还活着的时候，SQLite 的库文件被锁着，
        # 既删不掉也会把端口占着给下一个自检（或别的会话）造成串台。
        self._wait_port_free()

        # 自建的临时用户库用完就删（data_root 那套是调用方的，不动）
        #
        # 服务进程刚被 taskkill 掉时，SQLite 的 .db/-wal/-shm 可能还被占着，
        # 立刻 unlink 会 WinError 32 —— 所以要重试几次再放弃。
        #
        # 顺带把**口令备忘**一起清掉：服务首次启动会在用户库旁边写一份
        # admin_password.txt（记着随机生成的初始密码），那是临时服务自己的，
        # 留在工作区里既没用又会让人误以为是正式口令。备忘的文件名跟着库名走
        # （见 core.auth.password_note_path），所以删的正是本服务写的那一份。
        if not self.data_root:
            base = PROJECT_ROOT / "tests" / "_workspace" / f"testserver_{self.port}_{os.getpid()}.db"
            note = password_note_path(base)
            for path in (base, Path(str(base) + "-wal"), Path(str(base) + "-shm"), note):
                for _ in range(20):
                    try:
                        path.unlink()
                        break
                    except FileNotFoundError:
                        break
                    except OSError:
                        time.sleep(0.25)
                else:
                    print(f"[gisds] 临时文件删不掉（被占用）：{path.name}")

        # 再扫一遍上一轮跑崩留下的死文件（属主进程已经不在的）
        _sweep_stale_workspace()

        # 收掉本次运行在用户空间里新建的空目录（否则会一直堆积）
        self._cleanup_new_user_dirs()

    def _wait_port_free(self, timeout: float = 20.0) -> bool:
        """等服务端口不再响应。返回是否成功释放。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                httpx.get(self.base + "/healthz", timeout=1.5)
            except Exception:
                return True
            time.sleep(0.4)
        print(f"[gisds] 端口 {self.port} 没释放，临时服务可能还在跑")
        return False

    def __enter__(self):
        if not self.start():
            self.stop()
            raise RuntimeError("测试服务起不来")
        return self

    def __exit__(self, *exc):
        self.stop()
        return False
