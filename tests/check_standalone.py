# -*- coding: utf-8 -*-
"""standalone 发行包自检 —— 验「本机没装 QGIS 也能跑」这件事。

发行包（``release\\BrowserQGIS-<版本>-standalone.zip``）里带了一份可搬家的
QGIS 运行时。**「能打包」不等于「能跑」**，所以这支脚本把包真的解开、真的起
一次服务，逐条验：

1. 包里带齐关键东西（运行时、proj.db —— 缺了所有坐标转换都会挂）
2. 跑的是**包里的**运行时，不是本机装的 QGIS
   （``os.path.realpath(sys.executable)`` 在包里；``sys.path`` 里没有原安装
   位置的残留。注意要用 realpath：``OSGEO4W_ROOT`` 走的是 8.3 **短路径**，
   直接字符串比对会落空）
3. 服务能起来，网页 / 静态资源 / 鉴权 / 数据 / AI 接口都正常
4. **包自带的自检脚本**在解包后的目录里也能通过

包没打出来时**跳过**（不算红）—— 没打发行版的人不该被这支脚本拦住。

跑法：``run-qgis-python.bat tests\\check_standalone.py``
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

from server_helper import _pids_listening_on  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

fails: list[str] = []
checked = 0

#: 断言总数（**不含**末尾那条「总数」断言本身）。
#:
#: 钉死它是有意义的：服务起不来时，第 3 节那 10 条会被跳过，汇总数就会变 ——
#: 那样就说明「没验」，而不是「验过了」。
EXPECTED_CHECKS = 17

PORT = 8877


def check(label, ok, detail=""):
    global checked
    checked += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


def find_package() -> Path | None:
    """找 standalone 包；没打出来就返回 None（跳过）。"""
    rel = ROOT / "release"
    if not rel.is_dir():
        return None
    cands = sorted(rel.glob("BrowserQGIS-*-standalone.zip"))
    return cands[-1] if cands else None


def free_port(wanted: int) -> int:
    """找一个真正空闲的端口。

    端口被占着还去跑，探针会打到**别人的服务**上 —— 那时报出来的一堆
    500/404 全是假的（真踩过：上一轮跑剩的服务还占着端口）。
    """
    for port in range(wanted, wanted + 40):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise RuntimeError(f"找不到空闲端口（从 {wanted} 起试了 40 个）")


def _port_busy(port: int) -> bool:
    """端口上有没有人。"""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def run_bat(pkg: Path, *args: str, env=None, timeout: int = 1800):
    """跑包里的 run-qgis-python.bat。

    ``encoding="utf-8"`` 是必须的：bat 设了 PYTHONUTF8=1，输出是 UTF-8；
    而 text=True 默认按系统 ANSI（zh-CN 是 GBK）解码 —— 那样「结果：」这几个
    字会变成乱码，按它找结果行就永远找不到。
    """
    return subprocess.run(
        ["cmd.exe", "/c", str(pkg / "run-qgis-python.bat"), *args],
        cwd=str(pkg), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def main() -> int:
    pkg_zip = find_package()
    if pkg_zip is None:
        print("SKIP: 没找到 standalone 发行包（release\\BrowserQGIS-*-standalone.zip）")
        print("      先跑：pack-release.ps1 -Standalone")
        return 0

    print("== standalone 发行包自检 ==")
    print(f"   包：{pkg_zip.name}  ({pkg_zip.stat().st_size / 1024 / 1024:.1f} MB)")

    work = Path(tempfile.gettempdir()) / f"gisds_standalone_check_{os.getpid()}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    port = free_port(PORT)
    base = f"http://127.0.0.1:{port}"
    proc = None
    try:
        print("\n== 1. 解包 ==")
        t0 = time.time()
        with zipfile.ZipFile(pkg_zip) as zf:
            zf.extractall(work)
        roots = [p for p in work.iterdir() if p.is_dir()]
        pkg = roots[0] if roots else work
        print(f"   用时 {time.time() - t0:.0f}s -> {pkg}")

        check("包里带 qgis/ 运行时",
              (pkg / "qgis" / "bin" / "python3.exe").is_file())
        check("包里带 proj.db（坐标转换的命根子，缺了全挂）",
              (pkg / "qgis" / "share" / "proj" / "proj.db").is_file())
        check("包里带 web/ 与 gisds/",
              (pkg / "web" / "index.html").is_file()
              and (pkg / "gisds" / "core" / "ai.py").is_file())

        print("\n== 2. 跑的是包里的运行时（不是本机装的 QGIS）==")
        probe = pkg / "_probe.py"
        probe.write_text(
            "import os, sys\n"
            "print('exe=' + os.path.realpath(sys.executable))\n"
            "print('prefix=' + os.path.realpath(sys.prefix))\n"
            "print('hits=' + repr([p for p in sys.path if 'Program Files' in p "
            "or 'QGIS 3.' in p]))\n",
            encoding="ascii",
        )
        env = dict(os.environ)
        env.pop("QGIS_ROOT", None)     # 模拟收件人：什么都没设
        r = run_bat(pkg, str(probe), env=env, timeout=300)
        out = r.stdout + r.stderr
        exe_line = next((l for l in out.splitlines() if l.startswith("exe=")), "")
        exe_path = exe_line[4:].strip()
        check("用的是包里的 python（realpath 展开短名后比对）",
              bool(exe_path) and str(pkg).lower() in exe_path.lower(),
              exe_path or out.strip()[-160:])
        check("sys.path 里没有原安装残留", "hits=[]" in out, out.strip()[-160:])
        probe.unlink(missing_ok=True)

        print("\n== 3. 起服务 ==")
        env2 = dict(os.environ)
        env2.pop("QGIS_ROOT", None)
        env2["GISDS_PORT"] = str(port)
        env2["GISDS_HOST"] = "127.0.0.1"
        env2["GISDS_ADMIN_PASSWORD"] = "standalone123456"
        # 内置共享超级账号：**不带**。发行包里的 config.json 带着生产那把
        # 万能钥匙（super_admin_password），继承下来会把 admin 的口令改成它，
        # 下面「管理员能登录」那条就 401 了 —— 跟被测功能无关，是配置串味。
        env2["GISDS_SUPER_ADMIN_PASSWORD"] = "off"
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(pkg / "run-qgis-python.bat"), "-m", "gisds", "serve"],
            cwd=str(pkg), env=env2,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        )
        ready = False
        for _ in range(90):
            time.sleep(2)
            try:
                if httpx.get(base + "/readyz", timeout=5).status_code == 200:
                    ready = True
                    break
            except Exception:
                pass
            if proc.poll() is not None:
                break
        check("服务起来了（/readyz 200）", ready)
        if ready:
            info = httpx.get(base + "/readyz", timeout=15).json()
            check("QGIS 内核初始化了", info.get("qgis", {}).get("initialized") is True)
            check("Processing 就绪", info.get("qgis", {}).get("processing") is True)

            for path in ("/", "/login", "/static/ai.js"):
                try:
                    code = httpx.get(base + path, timeout=20).status_code
                except Exception as exc:
                    code = str(exc)
                check(f"页面/静态资源 {path}", code == 200, f"HTTP {code}")

            code = httpx.get(base + "/api/ai/config", timeout=15).status_code
            check("未登录访问 AI 接口被拒", code == 401, f"HTTP {code}")

            tok = httpx.post(
                base + "/api/auth/login", timeout=25,
                json={"username": "admin", "password": "standalone123456"},
            ).json().get("token")
            check("管理员能登录", bool(tok))
            if tok:
                h = {"Authorization": f"Bearer {tok}"}
                ids = [d["id"] for d in httpx.get(base + "/api/datasets", headers=h,
                                                 timeout=25).json().get("datasets", [])]
                check("自带示例数据在（basemap + example）",
                      set(ids) >= {"basemap", "example"}, str(ids))
                tools = httpx.get(base + "/api/ai/tools", headers=h, timeout=25).json()
                check("AI 工具清单正常", tools.get("count", 0) > 0, str(tools.get("count")))

        # 第 4 节必须在**服务停掉、但包还没删**的时候跑 ——
        # 之前把清理放在 finally 里，结果第 4 节跑的时候包已经被删了。
        print("\n== 4. 包自带的自检脚本（在解包后的目录里）==")
        if proc is not None:
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=20, text=True, errors="replace",
                )
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            proc = None
        for script in ("tests\\check_ai.py", "tests\\check_mcp.py"):
            env3 = dict(os.environ)
            env3.pop("QGIS_ROOT", None)
            rr = run_bat(pkg, script, env=env3, timeout=1800)
            blob = rr.stdout + rr.stderr
            tails = [l for l in blob.splitlines() if "结果：" in l]
            fails_in = [l for l in blob.splitlines() if "[FAIL]" in l]
            check(f"包内 {script} 通过",
                  rr.returncode == 0 and tails and "0 失败" in tails[-1],
                  (tails[-1] if tails else f"exit={rr.returncode}")
                  + ("  " + " / ".join(fails_in[:3]) if fails_in else ""))
    finally:
        # 结束服务：terminate 的是 cmd.exe，**不会**连带杀掉 python3.exe 子进程
        # （Windows 不级联终止）—— 那就会留下一个占着端口的孤儿。
        # 所以照 server_helper.TestServer.stop() 的两段式来：先 taskkill /T 杀树，
        # 再按端口补刀（这个端口是我们自己挑的，上面只可能是我们起的服务）。
        if proc is not None:
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=20, text=True, errors="replace",
                )
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            proc = None
        for pid in _pids_listening_on(port):
            try:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=20, text=True, errors="replace",
                )
                print(f"[gisds] 端口 {port} 还被 pid {pid} 占着，已补刀")
            except Exception:
                pass
        # 包里的自检脚本会起临时服务（8871 起那一段），它们退出时还占着这个目录 ——
        # 目录被先删掉的话，那些进程会「文件没了但还活着」，变成孤儿。
        # 所以先等那一段端口空出来，再删。
        for _ in range(40):
            busy = [p for p in range(8871, 8895) if _port_busy(p)]
            if not busy:
                break
            time.sleep(1)
        shutil.rmtree(work, ignore_errors=True)

    total = checked
    check(f"断言总数 = {EXPECTED_CHECKS}（少一条就说明有断言被条件跳过了）",
          total == EXPECTED_CHECKS, f"实际 {total}")

    print(f"\n== 结果：{checked - len(fails)} 通过 / {len(fails)} 失败 ==")
    for item in fails:
        print(f"  ! {item}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
