"""账号、密码与会话。

- 密码用 PBKDF2-HMAC-SHA256（标准库，不额外引依赖），每个用户独立随机盐
- 会话令牌是 32 字节随机串，库里只存它的 SHA-256，明文只在登录时返回一次
- :class:`Principal` 是「当前调用者」，决定他能看哪些空间、能不能写
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import ROLE_ADMIN, ROLE_USER, STATUS_ACTIVE, STATUS_DISABLED, Database
from .tenancy import SHARED, is_shared, is_user_scope, user_scope

PBKDF2_ROUNDS = 120_000
SESSION_TTL = 7 * 24 * 3600  # 7 天
TOKEN_BYTES = 32


class AuthError(Exception):
    """账号或密码不对。"""

    status_code = 401


class AuthzError(Exception):
    """身份不认，但权限不够。"""

    status_code = 403


class UserExists(AuthError):
    """用户名已被占用。"""


def hash_password(password: str, salt: bytes | None = None) -> str:
    """算密码摘要，格式 ``pbkdf2_sha256$轮数$盐$摘要``。"""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """校验密码。"""
    try:
        algo, rounds, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Principal:
    """当前调用者。"""

    user_id: str
    username: str
    role: str = ROLE_USER
    display_name: str = ""
    api_key: bool = False
    shared_only: bool = False   # 免登录模式：只看得到共享空间

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN or self.api_key

    # ---------------------------------------------------------------- 权限

    def can_see(self, scope: str | None) -> bool:
        """能不能看到这个空间里的数据。"""
        if self.shared_only:
            return is_shared(scope)
        if self.is_admin:
            return True
        if is_shared(scope):
            return True
        if is_user_scope(scope):
            return scope == user_scope(self.user_id)
        return False

    def can_write(self, scope: str | None) -> bool:
        """能不能往这个空间里写。"""
        if self.shared_only:
            return is_shared(scope)
        if self.is_admin:
            return True
        if is_shared(scope):
            return True          # 共享空间人人可写
        if is_user_scope(scope):
            return scope == user_scope(self.user_id)
        return False

    def own_scope(self) -> str:
        return user_scope(self.user_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name or self.username,
            "role": self.role,
            "is_admin": self.is_admin,
        }


class AuthService:
    """账号与会话服务。"""

    def __init__(self, db: Database):
        self.db = db

    # ---------------------------------------------------------------- 账号

    def register(
        self,
        username: str,
        password: str,
        role: str = ROLE_USER,
        display_name: str = "",
    ) -> dict[str, Any]:
        username = (username or "").strip()
        if not username or len(username) < 3:
            raise AuthError("用户名至少 3 个字符")
        if not password or len(password) < 6:
            raise AuthError("密码至少 6 位")
        if self.db.get_user_by_name(username):
            raise UserExists(f"用户名 {username} 已被占用")
        user = self.db.create_user(
            user_id=uuid.uuid4().hex[:12],
            username=username,
            password_hash=hash_password(password),
            role=role if role in (ROLE_ADMIN, ROLE_USER) else ROLE_USER,
            display_name=display_name or username,
        )
        self.db.log(user["id"], user["username"], "user.register", user["username"])
        return user

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        name = (username or "").strip()
        user = self.db.get_user_by_name(name)
        if not user or not verify_password(password or "", user["password_hash"]):
            # 登录失败也要留痕：否则「登不上去」这种事在审计里完全看不到痕迹，
            # 只能靠猜（是密码错？账号停了？还是令牌过期了？）
            self.db.log(user["id"] if user else None, name or None, "user.login_failed", name)
            raise AuthError("用户名或密码不对")
        if user["status"] != STATUS_ACTIVE:
            self.db.log(user["id"], user["username"], "user.login_disabled", user["username"])
            raise AuthzError("账号已被停用")
        self.db.update_user(user["id"], last_login_at=time.time())
        self.db.log(user["id"], user["username"], "user.login", user["username"])
        return user

    def set_password(self, user_id: str, new_password: str) -> bool:
        if not new_password or len(new_password) < 6:
            raise AuthError("密码至少 6 位")
        ok = self.db.update_user(user_id, password_hash=hash_password(new_password))
        if ok:
            self.db.log(user_id, None, "user.password", user_id)
        return ok
    def set_status(self, user_id: str, status: str) -> bool:
        if status not in (STATUS_ACTIVE, STATUS_DISABLED):
            raise AuthError("状态只能是 active 或 disabled")
        ok = self.db.update_user(user_id, status=status)
        if ok and status == STATUS_DISABLED:
            self.db.delete_sessions_for_user(user_id)   # 停用即刻踢下线
        return ok

    def set_role(self, user_id: str, role: str) -> bool:
        if role not in (ROLE_ADMIN, ROLE_USER):
            raise AuthError("角色只能是 admin 或 user")
        return self.db.update_user(user_id, role=role)

    def delete_user(self, user_id: str) -> bool:
        ok = self.db.delete_user(user_id)
        if ok:
            self.db.log(None, None, "user.delete", user_id)
        return ok

    def ensure_admin(self, username: str, password: str | None = None) -> tuple[dict[str, Any], str | None]:
        """确保至少有一个管理员。返回 (用户, 初始密码)。"""
        existing = self.db.get_user_by_name(username)
        if existing:
            if existing["role"] != ROLE_ADMIN:
                self.db.update_user(existing["id"], role=ROLE_ADMIN)
                existing = self.db.get_user(existing["id"]) or existing
            return existing, None
        pwd = password or secrets.token_urlsafe(12)
        user = self.register(username, pwd, role=ROLE_ADMIN)
        self.db.log(user["id"], user["username"], "user.bootstrap_admin", user["username"])
        return user, pwd

    def ensure_super_admin(self, username: str, password: str) -> tuple[dict[str, Any], bool]:
        """把内置超级账号对齐到配置里的口令。返回 (账号, 口令有没有被改动过)。

        跟 :meth:`ensure_admin` 的区别在**幂等和强制**：

        * ``ensure_admin`` 只在库为空时创建，密码只在那一次生效 ——
          管理员后来改了密码，配置里那份就成了过期副本；
        * 本方法是**每次启动都跑**的：账号不存在就建、角色不是管理员就升、
          口令跟配置对不上就重设。所以「配置里写的这组口令永远能登进去」，
          哪怕有人把它改了。

        ``changed=True`` 表示这次真的动过口令（新建 / 重设）——
        调用方据此决定要不要把口令备忘同步更新
        （备忘里记的必须永远是**当前有效**的口令，见 :func:`write_password_note`）。

        口令强度跟普通账号一样（至少 6 位），不做特殊放宽 ——
        「内置后门」这件事的风险在**知不知道**，不在长度。
        """
        username = (username or "").strip()
        password = password or ""
        if not username or len(password) < 6:
            raise AuthError("超级账号的用户名和口令都要有（口令至少 6 位）")
        existing = self.db.get_user_by_name(username)
        if not existing:
            user = self.register(username, password, role=ROLE_ADMIN)
            self.db.log(user["id"], user["username"], "user.super_admin", username)
            return user, True
        changed = False
        if existing["role"] != ROLE_ADMIN:
            self.db.update_user(existing["id"], role=ROLE_ADMIN)
            changed = True
        if not verify_password(password, existing.get("password_hash") or ""):
            self.set_password(existing["id"], password)
            changed = True
        user = self.db.get_user(existing["id"]) or existing
        if changed:
            self.db.log(user["id"], user["username"], "user.super_admin_reset", username)
        return user, changed

    # ---------------------------------------------------------------- 会话

    def login(self, username: str, password: str) -> tuple[str, dict[str, Any]]:
        user = self.authenticate(username, password)
        return self._issue_session(user)

    def claim_by_username(self, username: str) -> tuple[str, dict[str, Any]]:
        """免口令登录：**用户名就是身份**，账号不存在就当场建一个。

        这就是「不用密码」的全部含义 —— 同名即同人，
        同一个用户名在任何设备上都是同一个空间。

        两条护栏：

        * 管理员账号**不能**被免口令认领 —— 否则知道管理员用户名的人
          一进来就是管理员（能管账号、删数据、跑 Python）。
          管理员请走正常的密码登录。
        * 自动建出来的账号配一个**随机口令** —— 免口令模式是唯一入口，
          不留「哪天模式关了还能用旧密码登进来」的暗门。
        """
        name = (username or "").strip()
        if not name or len(name) < 3:
            raise AuthError("用户名至少 3 个字符")
        if len(name) > 32:
            raise AuthError("用户名最多 32 个字符")

        user = self.db.get_user_by_name(name)
        if user is None:
            user = self.register(name, secrets.token_urlsafe(16), display_name=name)
            self.db.log(user["id"], user["username"], "user.claim", name)
        if user["role"] == ROLE_ADMIN:
            raise AuthzError("管理员账号请用密码登录，不要用免口令入口")
        if user["status"] != STATUS_ACTIVE:
            self.db.log(user["id"], user["username"], "user.login_disabled", name)
            raise AuthzError("账号已被停用")
        self.db.update_user(user["id"], last_login_at=time.time())
        self.db.log(user["id"], user["username"], "user.login", user["username"])
        return self._issue_session(user)

    def _issue_session(self, user: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(TOKEN_BYTES)
        self.db.create_session(token_hash(token), user["id"], time.time() + SESSION_TTL)
        return token, user

    def logout(self, token: str) -> bool:
        return self.db.delete_session(token_hash(token))

    def principal_from_token(self, token: str | None) -> Principal | None:
        if not token:
            return None
        session = self.db.get_session(token_hash(token))
        if not session:
            return None
        user = self.db.get_user(session["user_id"])
        if not user or user["status"] != STATUS_ACTIVE:
            return None
        return Principal(
            user_id=user["id"],
            username=user["username"],
            role=user["role"],
            display_name=user.get("display_name", ""),
        )

    def principal_for_user(self, user: dict[str, Any]) -> Principal:
        return Principal(
            user_id=user["id"],
            username=user["username"],
            role=user["role"],
            display_name=user.get("display_name", ""),
        )

    def list_users(self) -> list[dict[str, Any]]:
        return self.db.list_users()


# --------------------------------------------------------------------- 口令备忘


def password_note_path(db_path) -> Path:
    """口令备忘文件该放在哪。

    正式库是 ``gisds.db``，备忘就叫 ``admin_password.txt``（约定俗成，
    文档和 README 里都写的是这个名）；**其它**库（自检的临时库）用
    ``<库名>_admin_password.txt``，这样几个临时服务并行跑时不会互相覆盖，
    清理时也能精确删掉自己那一份。

    注意比对的是 ``db.name``（带后缀）而不是 ``db.stem`` ——
    ``Path("gisds.db").stem`` 是 ``"gisds"``，用 stem 比会永远匹配不上，
    于是正式库的备忘被写成 ``gisds_admin_password.txt``，
    而文档里写的那个 ``admin_password.txt`` 留在原地不更新 ——
    又变成「文件在骗人」。
    """
    db = Path(db_path)
    if db.name == "gisds.db":
        return db.with_name("admin_password.txt")
    return db.with_name(f"{db.stem}_admin_password.txt")


def write_password_note(db_path, username: str, password: str) -> None:
    """把管理员当前口令写进用户库旁边的备忘文件。

    这个文件是给人看的兜底（忘了密码还能从磁盘上找到）。判断要不要写它，
    只有一条依据：**当前有效口令还有没有别的明文副本**。

    * 自举那一刻 —— config.json / ``GISDS_ADMIN_PASSWORD`` 里已经写着口令 →
      明文本来就在配置里，再复制一份到磁盘、再打进日志都是净损失 → **不写**；
    * 自举那一刻 —— 没人配 → 服务随机生成 → 这台机器上只有启动日志和这个文件
      两处能找到它（多机部署时管理员在另一台机器、看不到日志，这两处就是唯一的
      凭据副本）→ **要写**；
    * 改密之后 —— config 里那一份已经过期作废 → 备忘成了**当前有效口令唯一的
      明文副本** → **要写**（否则「忘了密码」就彻底没辙了）。

    所以不变式只有一条：**文件里记着的永远是当前有效的口令**。
    绝不会留下一个失效的旧口令让人照着登一直 401 —— 那是「文件在骗人」最难查的一种。
    （反过来，管理员自己设过口令后也仍然保留明文，是这条规则的代价；
    机器要接到不可信网络时，用 ``password_note=false`` 一关了之。）

    目录要跟**用户库**放一起，而不是数据根目录：两者可以各自配置
    （``db_path`` 与 ``data_root``），写错地方的话，临时库初始化时会把
    正式库的口令备忘覆盖掉 —— 那正是「照着文件登不进去」的成因。
    """
    db = Path(db_path)
    path = password_note_path(db)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"用户：{username}\n密码：{password}\n\n"
            f"登录后请尽快在「启动器 → 用户 → 改密码」里改掉。\n"
            f"也可以用命令行重置：run-qgis-python.bat -m gisds set-password {username}\n",
            encoding="utf-8",
        )
    except OSError:
        pass
