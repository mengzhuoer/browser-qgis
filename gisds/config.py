"""配置加载。

优先级：命令行/环境变量 > config.json > 内置默认值。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


@dataclass
class Config:
    """服务运行配置。"""

    # 数据根目录：下面放 shared/（共享空间）、users/（各用户私有空间）和 gisds.db
    data_root: str = str(PROJECT_ROOT / "data")
    # 共享空间根目录（所有登录用户可读可写）
    shared_root: str = str(PROJECT_ROOT / "data" / "shared")
    # 用户私有空间根目录
    users_root: str = str(PROJECT_ROOT / "data" / "users")
    # 用户库（SQLite）
    db_path: str = str(PROJECT_ROOT / "data" / "gisds.db")
    # 是否要求登录才能取数。关掉就是「免登录模式」，所有人共用共享空间
    require_login: bool = True
    # 免口令模式：登录只要一个用户名，不要密码。
    #
    # 账号**照常存在** —— 每个人一个独立空间（data/users/<user_id>），
    # 但谁想用就填个用户名直接进，不用记密码、不用注册。
    # 同一个用户名在任何设备上都是同一个空间。
    #
    # 这是给「局域网里放着给同事/学生/客人直接用」的场景准备的，
    # **不是给公网用的**：知道用户名就能进那个人的空间。
    passwordless_login: bool = False
    # 是否开放自助注册
    allow_registration: bool = False
    # 首个管理员的用户名（GUI 首次启动时自动创建）
    admin_username: str = "admin"
    # 首个管理员的初始密码（留空则随机生成并写到用户库旁边的备忘文件）
    admin_password: str = ""
    # 内置超级账号的口令。**跟 admin_password 不是一回事**：
    #
    #   * admin_password —— 只在**库还是空的时候**用一次（首次自举），
    #     之后管理员改了密码，它就成了过期副本（启动时只会告警，不会覆盖）；
    #   * super_admin_password —— **每次启动都把这个账号的口令对齐成这个值**，
    #     账号不存在就建、角色不是管理员就升、口令对不上就重设。
    #
    # 两者配的是**同一个账号**（admin_username）。**只能设一个**：
    # 都设了以 super_admin_password 为准（它是更强的约束），
    # admin_password 那份就白配了。
    #
    # 它的存在意义是「所有用户都能用的那把万能钥匙」：同网段任何人拿这组口令
    # 登录都是管理员，能管账号、能删数据、能跑 Python。
    # **这是设计上的后门** —— 服务监听 0.0.0.0 时，它等于把管理员身份发给整栋楼。
    # 要收紧就把这项留空、或写成 off（内置账号不再被钉住），
    # 或者把服务收到内网 / 反代后面。
    super_admin_password: str = ""
    # 是否把管理员口令写成明文备忘文件（放在用户库旁边）。
    # 默认开：多机部署时（服务在数据机、管理员在另一台机器用启动器连过去），
    # 随机生成的初始密码只有这个文件和启动日志两处能找到。
    # 机器要接到不可信网络时可以关掉 —— 关掉后初始密码只在启动日志里留一次。
    password_note: bool = True
    # 监听地址：0.0.0.0 表示局域网内其它设备可访问
    host: str = "0.0.0.0"
    port: int = 8848
    # 允许跨域的来源：["*"] 表示任意网页都能取数
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    # 瓦片渲染缓存条目数。网页端按层级出瓦片，一屏就 25 块上下，
    # 太小的话平移一下缓存就被挤满、瓦片要重渲。
    tile_cache_size: int = 512
    # 单次范围查询允许采样的最大像元数（超过则降采样）
    max_sample_pixels: int = 4_000_000
    # 矢量要素分页
    default_page_size: int = 50
    max_page_size: int = 1000
    # 单个上传文件大小上限（MB）
    max_upload_mb: int = 512
    # 算法作业最长执行时间（秒）
    job_timeout: int = 1800
    # 日志级别
    log_level: str = "info"
    # 服务级 API Key：带上它就以管理员身份访问（给 MCP / 脚本用）
    api_key: str = ""
    # 数据集扫描时是否递归子目录
    recursive: bool = False
    # 元数据语言：QGIS 官方翻译包的语言名（zh-Hans / zh-Hant / en 等）；留空 = 只用英文
    language: str = "zh-Hans"
    # Python 控制台：谁能用。admin（默认，只有管理员）/ user（所有登录用户）/ off（关掉）
    # 控制台等于在数据服务器上执行任意代码，默认收紧。
    #
    # 本项目自带的 config.json 里设的是 user（全员可用）—— 那是**本机部署**的选择，
    # 不是代码默认值。代码默认保持 admin：别人拿去用、忘了改这一项时，
    # 拿到的是「只有管理员能跑代码」的收紧状态，而不是「一装上就能远程执行任意代码」。
    console_access: str = "admin"
    # Python 控制台单次执行的超时（秒）。超时后接口返回错误，但 Python 没法
    # 中断一个正在跑的线程 —— 卡住的代码会一直占着 QGIS 线程。
    console_timeout: int = 30
    # 插件目录：插件装在这儿（web 上传 zip 解压到这里）。
    # 默认放在**项目根目录**下，不放 data/ 里 —— data/ 是数据区，
    # 旧布局迁移会把 data/ 下的每个目录都搬进 data/shared/（插件会被当成
    # 上传的数据集挪走）。插件是代码，跟代码放一起才对。
    plugin_dir: str = str(PROJECT_ROOT / "plugins")
    # 插件总开关（关掉就一个都不加载）
    plugins_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _resolve(self, raw: str) -> Path:
        """把配置里的路径解析成绝对路径。

        相对路径一律相对 **项目根目录**，而不是当前工作目录 ——
        否则换个目录启动服务就会指向另一套数据目录和用户库。
        """
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    @property
    def data_root_path(self) -> Path:
        return self._resolve(self.data_root)

    @property
    def shared_root_path(self) -> Path:
        return self._resolve(self.shared_root)

    @property
    def users_root_path(self) -> Path:
        return self._resolve(self.users_root)

    @property
    def db_path_resolved(self) -> Path:
        return self._resolve(self.db_path)

    @property
    def workspace_root_path(self) -> Path:
        """旧布局里那个独立的上传区。

        新布局已经没有这个概念了，只在首次启动做一次性数据迁移时用到。
        """
        return self.data_root_path / "workspace"

    @property
    def plugin_dir_path(self) -> Path:
        """插件目录（插件装在这儿）。"""
        return self._resolve(self.plugin_dir)

    @property
    def super_admin_password_resolved(self) -> str:
        """内置超级账号的口令；配成 off/none/disabled 之类的值视为**没配**。

        为什么要留这个「关闭口令」：临时服务（自检、别人的部署）常常是
        **继承**生产配置起来的 —— 它们不该顺手把生产的共享超级账号也带上，
        更不该把那把万能钥匙的明文抄进自己的用户库。
        「写成 off 就关掉」比「留空」更好用：环境变量传空串是传不进去的
        （空值会被当成没设），传 off 才真的关得掉。
        """
        raw = (self.super_admin_password or "").strip()
        if raw.lower() in ("off", "none", "disabled", "false", "no", "0"):
            return ""
        return raw

    @property
    def super_admin_enabled(self) -> bool:
        """内置共享超级账号开没开。"""
        return bool(self.super_admin_password_resolved)


def _coerce(current: Any, raw: str) -> Any:
    """按默认值的类型转换配置字符串。"""
    if isinstance(current, bool):
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def load_config(path: str | Path | None = None) -> Config:
    """从 config.json + 环境变量装载配置。

    配置文件的取用顺序：**命令行 --config > 环境变量 GISDS_CONFIG >
    项目根目录的 config.json**。

    ``GISDS_CONFIG`` 这一层是给「免口令预设」准备的：启动器把服务起起来时
    用的是固定的 ``-m gisds serve``（不带 --config），没法在命令行上换配置；
    让它认这个环境变量，就能用另一个 .bat 设好环境变量再调启动器，
    从而把服务起在免口令模式上，而不用改启动器自己的代码。
    """
    cfg = Config()

    if path:
        cfg_path = Path(path)
    elif os.environ.get("GISDS_CONFIG"):
        cfg_path = Path(os.environ["GISDS_CONFIG"]).expanduser()
    else:
        cfg_path = DEFAULT_CONFIG_PATH
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if cfg_path.is_file():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:  # 配置坏了不该让服务起不来
            print(f"[gisds] 配置文件解析失败，忽略：{cfg_path} ({exc})")
            raw = {}
        for key, value in raw.items():
            if any(f.name == key for f in fields(cfg)):
                setattr(cfg, key, value)

    # 环境变量覆盖
    env_map = {
        "GISDS_DATA_ROOT": "data_root",
        "GISDS_SHARED_ROOT": "shared_root",
        "GISDS_USERS_ROOT": "users_root",
        "GISDS_DB_PATH": "db_path",
        "GISDS_REQUIRE_LOGIN": "require_login",
        "GISDS_PASSWORDLESS_LOGIN": "passwordless_login",
        "GISDS_ALLOW_REGISTRATION": "allow_registration",
        "GISDS_ADMIN_USERNAME": "admin_username",
        "GISDS_ADMIN_PASSWORD": "admin_password",
        "GISDS_SUPER_ADMIN_PASSWORD": "super_admin_password",
        "GISDS_PASSWORD_NOTE": "password_note",
        "GISDS_HOST": "host",
        "GISDS_PORT": "port",
        "GISDS_CORS_ORIGINS": "cors_origins",
        "GISDS_API_KEY": "api_key",
        "GISDS_LOG_LEVEL": "log_level",
        "GISDS_MAX_UPLOAD_MB": "max_upload_mb",
        "GISDS_LANGUAGE": "language",
        "GISDS_CONSOLE_ACCESS": "console_access",
        "GISDS_CONSOLE_TIMEOUT": "console_timeout",
        "GISDS_PLUGIN_DIR": "plugin_dir",
        "GISDS_PLUGINS_ENABLED": "plugins_enabled",
    }
    for env_key, field_name in env_map.items():
        raw_value = os.environ.get(env_key)
        if raw_value is None or raw_value == "":
            continue
        setattr(cfg, field_name, _coerce(getattr(cfg, field_name), raw_value))

    return cfg
