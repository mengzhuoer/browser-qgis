"""初始口令提示条的「还有效吗」判断。

服务端的口令备忘遵循**同步更新**：管理员改过口令，备忘文件里记着的就换成新口令。
所以「那份备忘现在还记不记着这个口令」就是「这个口令还有效吗」的权威判据 ——
不用猜，也不用给服务端加接口。

（口径变了，判断逻辑没变：改密前备忘记着旧口令，改密后备忘记着新口令，
「不再记着这个口令」这件事本身没变 —— 它依然是「口令已作废」的信号。）

启动器自己起服务、日志就在手里，能从自举日志里捞到随机生成的初始口令。
但这个口令可能已经作废：在别处改过、或用 ``gisds set-password`` 重置过。
一条作废的口令摆在屏幕上、还配一个「直接登录」按钮，点了只会 401 ——
跟服务端那个「文件在骗人」是同一类故障，也是同一个原则：
**任何显示凭据的地方都不能留着过期的。**

这个模块只做判断，不碰 tkinter，所以没有显示器也能单测。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

# 自举日志里的两行：
#     用户名：admin
#     密码  ：AbCdEf123456
# 注意「密码」和「：」之间可能有多个空格（服务端就是这么排版的）。
RE_USERNAME = re.compile(r"用户名：\s*(\S+)")
RE_PASSWORD = re.compile(r"密码\s*：\s*(\S+)")

# 提示条最长展示多久。
#
# 凭据在屏幕上放太久本身就是风险（有人路过瞄一眼），但收得太急也有代价：
# 用户离开一会儿回来、提示条没了，就又得去日志里翻密码 —— 而这个提示条
# 存在的意义正是省掉那一步。十五分钟是个折衷。
DEFAULT_TTL_SECONDS = 15 * 60

# 判断结果
FRESH = "fresh"      # 还能用
STALE = "stale"      # 口令已经变了 —— 不能再显示
EXPIRED = "expired"  # 放太久了 —— 收起来
USED = "used"        # 凭据已经被接管（登录成功了）—— 收起来


def parse_username(line: str) -> str | None:
    """从一行自举日志里抠出用户名。"""
    m = RE_USERNAME.search(line or "")
    return m.group(1) if m else None


def parse_password(line: str) -> str | None:
    """从一行自举日志里抠出口令。"""
    m = RE_PASSWORD.search(line or "")
    return m.group(1) if m else None


def server_note_path(config_path=None) -> Path | None:
    """服务端那份「口令备忘文件」在哪。

    直接复用服务端的配置解析和路径规则（``gisds.config`` / ``gisds.core.auth``，
    两个都是纯标准库），不在启动器里另写一份 —— 路径规则只有一处实现。

    拿不到就返回 None：这时只能靠「登录被拒」来发现口令已作废。
    但**不许裸吞** —— 静默退化成「只能靠超时兜底」而没人知道，是最难查的那类。
    """
    try:
        from ..config import load_config
        from ..core.auth import password_note_path
    except Exception as exc:
        print(f"[gisds-launcher] 定位口令备忘路径失败，改用「登录被拒」判据：{exc!r}",
              file=sys.stderr)
        return None
    try:
        cfg = load_config(config_path)
        return password_note_path(cfg.db_path_resolved)
    except Exception as exc:
        print(f"[gisds-launcher] 读取配置定位口令备忘失败，改用「登录被拒」判据：{exc!r}",
              file=sys.stderr)
        return None


class CredentialFreshness:
    """判断一条初始口令现在还有效吗。

    判据有两个，都是硬的：

    1. **服务端那份备忘还记不记着这个口令**。服务端改密时备忘会同步更新成
       新口令，所以「刚才还记着、现在不记了」= 口令已经变了。备忘文件本身
       只在*管理员改过口令*时才会不再记着这个口令，所以它是个可靠的信号。
    2. **放了太久**。判据 1 用不了时（比如服务端关了 ``password_note``），
       这是兜底。

    为什么要「见过才算数」：备忘文件也可能**一开始就不存在** ——
    服务端配置里把 ``password_note`` 关掉就是那样，那样「文件不见了」
    说明不了任何事。所以只有在**亲眼见过**那份备忘记着这个口令之后，
    它**不再记着这个口令**才是信号。否则宁可保守（当成还有效），
    也不要凭空说口令作废。
    """

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        note_path=None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        caught_at: float | None = None,
    ):
        self.username = username
        self.password = password
        self.note_path = Path(note_path) if note_path else None
        self.ttl_seconds = ttl_seconds
        self.caught_at = float(caught_at if caught_at is not None else time.time())
        # 只有亲眼见过那份备忘记着这个口令，它才能当判据用。
        self._note_armed = False
        # 一旦判定，就不再翻案。
        self._verdict: str | None = None

    # ------------------------------------------------------------------ 判据

    def note_records_password(self) -> bool:
        """服务端那份备忘现在还记着这个口令吗。"""
        if not self.note_path or not self.password:
            return False
        try:
            if not self.note_path.is_file():
                return False
            text = self.note_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return f"密码：{self.password}" in text

    def evaluate(self) -> str:
        """返回 FRESH / STALE / EXPIRED / USED。"""
        if self._verdict:
            return self._verdict

        if self.note_path:
            if self.note_records_password():
                self._note_armed = True
            elif self._note_armed:
                # 刚才还记着，现在不记了（多半是被改密同步更新掉了）
                # → 口令已经变了。
                self._verdict = STALE
                return self._verdict

        if self.ttl_seconds and (time.time() - self.caught_at) > self.ttl_seconds:
            self._verdict = EXPIRED
            return self._verdict

        return FRESH

    # ------------------------------------------------------------------ 外部判定

    def mark_stale(self) -> None:
        """外部确认口令已作废（服务端拒了这个口令）。"""
        self._verdict = STALE

    def mark_used(self) -> None:
        """凭据已经被接管（用它登录成功了），不用再显示了。"""
        self._verdict = USED
