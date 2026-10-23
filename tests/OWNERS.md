# 文件归属表

三个会话并行改同一个仓库，靠记忆对文件已经出过**多次**归属错位
（`tests/server_helper.py`、`gisds/core/auth.py`、`tests/pngutil.py`、
`tests/check_password_note.py`，以及 `core/auth.py` 里 `password_note_path()`
的 `stem`→`name` 修复、`server_helper.py` 去掉第二份命名规则这两处具体段落）。
这张表是**归属**（谁负责哪一段），而**实际改动以下面的「改动署名」流水为准** ——
只光看「谁负责这个文件」已经定位不到具体是谁写的那一段了。

## 改动署名（每次改完文件，在这里加一行）

规则：**改完任何一个文件，往「最近改动」表里加一行** —— 谁、什么时候（填该文件的
mtime，用 `Get-Item` 查，别凭印象）、动了哪个文件的哪一段、干了什么。
这样「这段是谁写的」不用靠记忆，争议当场就能查。

| 时间（mtime） | 提交人 | 文件 · 段 | 改了什么 |
| --- | --- | --- | --- |
| 09:26:29 | 3-辅助 | `core/bookmarks.py` | 新建：空间书签（按数据空间存 JSON） |
| 09:31:08 | 3-辅助 | `core/vector.py` | 修 `?offset=` 翻页 500（QGIS 3.44 没有 `QgsFeatureRequest.setOffset`）与过滤后 total 恒为全表数 |
| 09:35:15 | 3-辅助 | `api/routes_panels.py` | 新建：`/api/panels/*`（属性表/统计/选择/书签/日志/编辑） |
| 09:41:44 | 3-辅助 | `core/panels.py` | 新建：面板内核（属性表 / 字段统计 / 选择 / 要素编辑） |
| 09:41:56 | 3-辅助 | `tests/check_panels.py` | 新建：面板接口自检（79 项） |
| 09:42:27 | 3-辅助 | `web/index.html` | 末尾加一行 `<script src="/static/panels.js">`（面板挂载；head 的 icon link、brand、底图函数与 boot() 均未动） |
| 09:52:14 | 3-辅助 | `web/panels.js` | 新建：顶部工具栏行 + 右栏六个面板（自包含，`DOMContentLoaded` 自启，不依赖 index.html 的 boot） |
| 09:54:40 | 3-辅助 | `mcp/server.py` | 追加 12 个面板 / 编辑 / 书签工具（工具总数 15 → 27） |
| 09:58:26 | 3-辅助 | `tests/check_mcp.py` · `check_mcp_stdio.py` | 工具总数断言 15 → 27，并补 12 个新工具名 |
| 10:03:52 | 3-辅助 | `core/logbuf.py` | 新建：服务端日志环形缓冲（日志消息面板） |
| 10:12:20 | 3-辅助 | `run-qgis-python.bat` | 加 `cd /d "%~dp0"`，任意目录可跑 |
| 10:25:52 | 3-辅助 | `tests/check_webui_panels.py` | 新建：工具栏与面板 UI 自检（50 项，无头 Edge + CDP） |
| 10:25:58 | 3-辅助 | `tests/check_webui_login.py` | 新建：网页登录流程自检（12 项） |
| 10:46:32 | 3-辅助 | `tests/server_helper.py` | 口令备忘纳入临时文件清理组；`import core.auth.password_note_path`（去掉第二份命名规则）；死文件自愈（按文件名里的 pid 判活）+ `_pid_alive` + `import re` |
| 10:53:44 | 3-辅助 | `tests/check_webui.py` | 右栏面板断言由「三面板」改为「三面板 + 六个新面板」；CDP 端口由写死改为动态挑空闲 |
| 11:17:03 | 3-辅助 | `core/ready.py` | 新建：就绪标志（idle / starting / ready；启动中 `/healthz` 报 503） |
| 11:17:22 | 3-辅助 | `api/app.py` | 挂 `panels_router`；lifespan 里 `logbuf.install()` 与就绪标记；`/healthz` 启动期 503 的中间件 |
| 12:15:09 | 3-辅助 | `tests/run_all_checks.ps1` | 清单追加 `check_mcp_panels` / `check_panels` / `check_webui_panels` / `check_webui_login` |
| ≤12:23（文件 mtime 已被主会话 12:23 那行覆盖） | 3-辅助 | `api/routes_admin.py` · 改密分支 | 改密后同步口令备忘（受 `config.password_note` 控制） |
| ≤12:26（文件 mtime 已被主会话 12:26 那行覆盖） | 3-辅助 | `core/auth.py` · 口令备忘 | `password_note_path()` 的 **`stem` → `name` 修复**（`Path("gisds.db").stem` 是 `"gisds"`，拿它比永远匹配不上）；`write_password_note()` 参数从「目录」改为「库文件路径」；登录失败写审计（`user.login_failed`） |
| ≤12:26（文件 mtime 已被主会话 12:26 那行覆盖） | 3-辅助 | `cli.py` · `cmd_set_password()` | `set-password` 同步口令备忘（受 `config.password_note` 控制） |
| 12:23:15 | 主会话 | `api/routes_admin.py` · 改密分支 | 备忘由「删掉」改为**写**（同步更新），受 `config.password_note` 控制 |
| 12:26:04 | 主会话 | `core/auth.py` · 口令备忘 | 删掉 `delete_password_note()`；`write_password_note()` 文档改为统一判据 |
| 12:26:12 | 主会话 | `cli.py` · `cmd_set_password()` | 改为**写**备忘（受 `config.password_note` 控制） |
| 12:27:17 | 主会话 | `tests/check_password_flag.py` | 重写为「同步写入」语义，36 → 46 项 |
| 12:28:48 | 主会话 | `tests/check_password_note.py` | 措辞同步为不变式表述（判定逻辑不动） |
| ≤12:23（文件 mtime 已被下面 12:49 那行覆盖） | 主会话 | `README.md` · 三处口令备忘措辞 + `/healthz` 三态 | 统一到「同步更新」口径 |
| 12:49:09 | 2-辅助A | `README.md` · 启动器提示条那节 + 口令备忘三处 | 补「这条提示条会自己过期」那节（备忘不再记着这个口令 → 收起并说明口令已变更；401 被拒也收起；登录成功收起；15 分钟兜底）；**三处口令备忘措辞改回「同步更新」**（12:47 我曾按已废弃的「过期即删」写过一版，12:49 已回滚 —— 这就是本轮几条时延消息的源头） |
| 12:49:30 | 2-辅助A | `launcher/credentials.py` · 注释 | 措辞同步为「同步更新」口径（判断逻辑未动） |
| 12:51:30 | 2-辅助A | `tests/check_first_login.py` | 新增服务端真改密的端到端用例，27 → 40 项 |
| ≤12:56:59（文件 mtime 已被下面 2-辅助A 12:56:59 那行覆盖） | 主会话 | `launcher/app.py` · `on_login()` / `_collapse_first_credentials()` | 修 `on_login()` 里 `return        self._after_login(user)` 被解析成「登录失败反而去刷身份」的 bug；口令提示条的销毁改成**幂等**（先清引用、再逐个销毁、每个控件独立 try/except） |
| 12:56:59 | 2-辅助A | `launcher/app.py` · `Service._pump` / `Service.drain()` / `Launcher._tick` | **线程安全**：后台线程原来直接 `self.on_log(line)` → insert 日志、建口令提示条、pack 控件（**Tk 非线程安全**：真机才偶发不生效甚至崩，隔离环境复现不了）。改成后台线程只入队、界面更新由主线程 `_tick` 取出来做；`_tick` 间隔 400→200ms。（**主会话那两处修复仍在**：幂等销毁、`on_login` 的 `return` 误调） |
| 13:06:55 | 2-辅助A | `launcher/app.py` · `PROJECT_ROOT` / `LOG_DIR` | **路径修复**：`PROJECT_ROOT` 原来写 `Path(__file__).resolve().parent.parent`（少退一级，指向 `gisds/`）→ `run-qgis-python.bat` 找不到、**「启动服务」按钮从来没起过服务**、`logs/` 建到 `gisds/logs`、子进程 cwd 错、`-m gisds` 找不到包。改成 `from .api import PROJECT_ROOT`（路径只算一份） |
| 13:01:28 | 主会话 | `tests/OWNERS.md` | 加「改动署名」流水 +「口径变更记录」 |
| 13:04:16 | 主会话 | `tests/check_service_key.py` | 汇总数由写死的 5 改为真计数；补「服务密钥能重置 admin 口令 → 新口令能直接登录」这条（救砖的真正能力原来没被断言）；`BASE` 还原放进 finally |
| 13:07:26 | 2-辅助A | `tests/check_launcher.py` | 断言 21 → 27 项（补 6 条）：路径 2 条（「项目根目录指向真正的项目根」「日志目录在项目根下面，不在包目录里」）、线程 4 条（「后台线程没有直接调界面回调」「日志行进了队列，主线程取得到」「取完之后队列是空的」「主线程的 `_tick` 把队列里的行显示出来了」） |
| 13:16:44 | 主会话 | `api/routes_meta.py` | 拆探针：`/healthz` 改纯**存活**（启动期也 200，且不碰注册中心 —— `datasets()` 会触发扫描并阻塞 QGIS 线程）；新增 `/readyz` 就绪探针 |
| 13:16:50 | 主会话 | `api/app.py` · `_readiness_probe` 中间件 | 503 拦截从 `/healthz` 改到 `/readyz`（存活探针不该在启动期报 503，否则外部监控会误判服务挂了而重启循环） |
| 13:17:01 | 主会话 | `core/ready.py` | 文档改成「两个探针各管一件事」 |
| 13:17:06 | 主会话 | `tests/server_helper.py` · `TestServer.start()` | 等的探针从 `/healthz` 换成 `/readyz`（等存活会在 lifespan 跑完前就往下走 → 偶发失败） |
| 13:17:24 | 主会话 | `tests/check_api.py` | 新增 8 项：两探针语义、启动期 `/readyz` 503、`/healthz` 仍 200 且不报 datasets |
| 13:17:44 | 主会话 | `tests/check_live.py` | 新增 `/readyz` 与 ready 字段断言 |
| 13:18:13 | 主会话 | `README.md` | 「元信息」节重写为两探针说明 + 拆分理由；顶部地址表补 `/readyz` |
| 13:22:06 | 主会话 | `tests/check_webui_panels.py` | 开跑前的守卫从 `/healthz` 改成 `/readyz` + 判状态码（存活语义拦不住「还没准备好」） |
| 13:22:07 | 主会话 | `tests/check_webui.py` | 同上 |
| 13:22:13 | 主会话 | `web/index.html` · `boot()` | 开头先问 `/readyz`：启动期间显示「服务启动中」并自动重载，**不再直接调 `/api/meta`**（那会触发一次数据目录扫描并阻塞在 QGIS 线程上，页面会卡住） |
| 13:29:33 | 主会话 | `tests/check_first_login.py` | 修**自己**的空过断言：原来收集提示条文本时 `except: pass`，读不到就拿空串继续判 → 「口令不在里面」空过。改成把收集失败显式断言出来（+1 项，40 → 41） |
| 13:30:02 | 主会话 | `tests/OWNERS.md` · 第 6 条 + 新增第 16 条 | 第 6 条补下半句「别让『没报错』冒充『对』」；新增第 16 条「异常不要裸吞」（2-辅助A 提供的措辞 + 两次静默失败的附记） |
| 13:30:53 | 主会话 | `launcher/api.py` · `load_config()` | 配置文件解析失败不再裸吞，改为打印警告（与服务端 `gisds/config.py` 同一做法） |
| 13:31:05 | 主会话 | `launcher/files.py` · `_apply_icon()` | 图标设置失败不再裸吞，改为打印警告 |
| 13:34:51 | 主会话 | `launcher/theme.py` · `apply_style()` | 切 ttk 主题失败不再裸吞，改为打印警告（`launcher/` 下的裸吞到此清零） |
| 13:48:17 | 主会话 | `web/panels.js` · 顶部工具栏 | **修「undefined」显示**：按钮提示写的是 `it.group`，但分组名在**外层** `grp.group` 上（item 里没有 group 这个键）→ 悬停提示全是「undefined · 添加图层」。改为 `grp.group` |
| 13:48:50 | 主会话 | `tests/check_webui.py` | 新增 `scan_undefined()`：扫所有元素的 title/alt/placeholder/aria-label/value 与可见文本里的 undefined（**只看 innerText 抓不到** —— 提示在 title 属性、默认值在 value 里）。并加断言 |
| 13:50:21 | 主会话 | `tests/check_webui_panels.py` | 加两条：工具栏按钮提示不含 undefined、提示带分组名；收尾时扫一遍**填满内容后**的六个面板 |
| 13:59:41 | 主会话 | `tests/server_helper.py` | **修孤儿服务进程**：`taskkill /T` 偶尔漏掉孙进程（批处理中间那层 cmd 先退，python3 被重新挂到别的父进程上）→ 漏掉的继续占端口、占 SQLite 库文件（锁住 → 删不掉 → WinError 32 → 工作区堆死文件）。新增 `_descendant_pids()`：杀之前记下后代 pid，杀完挨个补刀；PowerShell 用**绝对路径**（QGIS 运行时 PATH 里没有它，subprocess 调 "powershell" 会静默失败）。`_sweep_stale_workspace` 删不掉时不再静默吞掉，改为打印 |
| 14:01:45 | 主会话 | `tests/run_all_checks.ps1` | 收尾体检：整套跑完检查 `tests/_workspace` 有无临时文件残留、有无孤儿服务进程（并清掉）。**注释一律英文** —— 无 BOM 的 UTF-8 会被 PowerShell 5.1 按 ANSI 解析，中文把引号吃掉 |
| 14:22:37 | 3-辅助 | `tests/run_all_checks.ps1` · 系统 Python 那两支 | 调 `check_launcher.py` / `check_first_login.py` 前先清掉 `PYTHONHOME`/`PYTHONPATH`：系统 Python 若继承 QGIS 运行时的这两个变量，会去加载 QGIS 的 **3.12 标准库**，`import re` 就报 `AssertionError: SRE module mismatch` 直接死 —— 两个 tkinter 脚本假红，而原因跟 tkinter 毫无关系。从干净 shell 起 runner 时没事，一旦从 QGIS 运行时里起（agent 常见路径）就中招 |
| 14:15:36 | 主会话 | `tests/check_*.py`（19 个） | 收尾 flush 加防护：`sys.stdout.flush()` 在 stdout 为 None 时抛 AttributeError → `os._exit(code)` 走不到 → **退出码 1，看着全红其实一条断言都没失败**。改为 `for _s in (sys.stdout, sys.stderr): if _s: _s.flush()`（字节级替换，各文件行尾风格保持不变） |
| 14:16:15 | 主会话 | `README.md` · 已知坑 | 新增「跑自检：退出码可能骗你」—— 收尾 flush 的失效机制 + 建议用 shell 重定向跑整套 |
| 14:30:16 | 主会话 | `gisds/core/registry.py` · `_ensure_crs()` | **修「TIFF 导入后显示不出来」**：没有 CRS 定义的图层在 QGIS 里渲染不出来（变换做不了 → 整层被跳过 → 全白图）。注册时按 EPSG:4326 解释（与 QGIS 桌面对未知 CRS 图层的处理一致）并留告警。栅格与矢量都走这条 |
| 14:32:10 | 主会话 | `tests/check_core.py` | 新增一节「没有坐标系信息的 GeoTIFF」：无 CRS → 被按 EPSG:4326 解释 + 有告警 + **出图有实际内容（像素级）**。这是那条 bug 的钉子（39 → 43 项） |
| 14:33:18 | 主会话 | `web/index.html` · `uploadFiles()` | 数据集级告警**显示给用户**（状态栏 + toast），且放在加完图层**之后**（addLayer 自己会写状态栏，先显示会被顶掉） |
| 14:33:49 | 主会话 | `gisds/api/routes_upload.py` | 上传结果带上数据集级告警；与存文件时的告警**合并**而不是互相覆盖（`dataset_warnings` 单独一个键，合并后删掉） |
| 14:42:20 | 主会话 | `gisds/api/routes_upload.py` | **修「上传假成功」**：文件没被识别成图层时，注册中心会把整个数据集丢掉、告警也跟着丢 → 接口只回一句「上传完成」，前端照着报「已上传并加入地图」。现在明确回一句原因；`.shp` 单独上传时直接点出「要连 .dbf/.shx/.prj 一起上传」 |
| 14:42:07 | 主会话 | `web/index.html` · `uploadFiles()` | 不再报假成功：有文件没被识别时 toast/状态栏说明「有 N 个文件没能识别成图层」并给出原因；返回值改成「是否**全部**都成了图层」，调用方据此决定要不要说「已上传并加入地图」 |
| 14:43:22 | 主会话 | `tests/check_webui.py` | 加两条：没被识别的文件必须带明确原因、且原因里点出 shapefile 配套文件（钉住「假成功」那个坑） |
| 14:49:42 | 主会话 | `gisds/api/routes_upload.py` · `delete_upload()` | **修「删不掉的垃圾」**：没注册成功的数据集不在注册中心里 → 删除接口 404 → 文件永久留在磁盘（用户只传一次 .shp 就留下 60 MB 且再也清不掉）。现在退回到工作区按目录找，找到照样删 |
| 14:50:00 | 主会话 | `tests/check_webui.py` | 加一条：没注册成功的上传**也能删掉**（不留磁盘垃圾） |
| 14:57:57 | 主会话 | `web/index.html` · `renderAlgoList()` | 删掉**死代码**：列表那两行 `a.name_en`/`a.group_en` 永远 undefined（列表请求没带 include_en）。按 3-辅助 的核实结论选 (b) —— 列表不显示英文名：搜索已由服务端覆盖中英、英文标识符（native:buffer）本来就在列表里、详情面板有完整中英名与说明；带 include_en 会让 440 个算法响应体 172 KB → 315 KB，信息量为零。连带删掉随之失效的 `.algoitem .n .en` 样式 |
| 15:05:18 | 主会话 | `launcher.bat` · `launcher-files.bat` | **同一个 PYTHONHOME 污染问题，启动器也会中招**：GUI 走的是**系统 Python**（要 tkinter），从 QGIS 运行时里起时会继承 `PYTHONHOME`/`PYTHONPATH` → 加载 QGIS 的 3.12 标准库 → `import tkinter` 报 `ModuleNotFoundError: No module named '_tkinter'` → 探针失败 → 界面报成「没找到带 tkinter 的 Python」（**误导**）。现在在 `setlocal` 之后先清掉这两个变量（服务子进程由 `run-qgis-python.bat` 自己再设一遍，不受影响） |
| 16:48:17 | 主会话 | `tests/check_webui_login.py` | 新增一组「登录态下栅格必须真的画到地图上」：上传栅格 → 添加图层 → 服务器共享数据 → 勾选 → 打开 → 断言叠加层 `naturalWidth > 0`。**必须在 require_login=True 的服务上跑** —— 免登录模式下 401 根本不会发生，原来所有 UI 自检都测不到 |
| 16:49:42 | 主会话 | `web/index.html` · `drawLayer()` | **修「导入后看不到图像」的真正根因**：栅格出图用的是**裸 fetch，不带 Authorization 头** → 登录态下服务端回 401 → blob() 拿到的是 JSON 错误而不是 PNG → imageOverlay 加载失败 → 地图上什么都不显示，**而且不报任何错**。改用 `api()`（带令牌、401 会抛错而不是静默） |
| 16:52:24 | 主会话 | `gisds/api/app.py` | 网页资源加 `Cache-Control: no-cache`（`/`、`/login`、`/demo`、`/static/*`）：原来没有缓存头，浏览器按启发式缓存 → **前端改了也不生效**（「我明明改了，怎么还是老样子」）。no-cache 是「每次回源校验」，配 ETag 没改就回 304 |
| 21:05:00 | 主会话 | `tests/run_all_checks.ps1` · 收尾体检 | 残留过滤放宽：原来只认 `testserver_*`，漏掉 `*.db` / `*.db-wal` / `*.db-shm` / `*_admin_password.txt` —— 工作区里躺着一个 8:39 的 `gisds.db` 也没被报出来（已清掉） |
| 17:04:22 | 主会话 | `web/index.html` · 面板大小可调 | **每个功能模块都能拖着改大小**（QGIS 桌面版那样）：左右侧栏宽度（CSS 变量 + 网格列宽）、工具箱内「算法列表/参数区」的分割高度、**「添加图层」对话框整体大小 + 左侧树宽度**。把手有 hover 高亮、可聚焦、方向键微调（无障碍）、双击恢复默认；侧栏尺寸存 localStorage；拖动时 `map.invalidateSize()` 让地图跟着重排。**两个坑**：① 把手必须完全落在面板里面 —— 侧栏是 overflow:hidden，伸出去的部分会被裁掉、也接不到鼠标事件（拖起来「按了没反应」）；② 拖动把手之后浏览器会在「按下点/松开点」的共同祖先上派发 click，那个祖先可能正是模态背景 → 拖一下就把对话框关了，所以「点背景关闭」必须同时看 pointerdown 的目标 |
| 17:05:40 | 主会话 | `tests/check_webui.py` | 新增 9 条：拖左边框改左栏宽度且地图相应变窄、拖右边框改右栏宽度且地图相应变宽、工具箱横把手改算法列表高度、刷新后尺寸还在、双击把手恢复默认、对话框可拖动改大小、对话框左侧树可拖动改宽度 |
| 17:30:34 | 主会话 | `README.md` · REST 速查 | 补**两个瓦片端点的鉴权差异**：底图瓦片 `/api/tiles/basemap/...` 刻意不鉴权（前端用 `<img src>` 挂，带不了请求头）；数据图层瓦片 `/api/tiles/{ds}/{layer}/...` 要令牌，但支持 `?token=`（`deps._token_from` 认 `token` / `access_token`）—— 已实测：不带 401、带 `?token=` 200 PNG |
| 17:14:06 | 3-辅助 | `web/index.html` · 添加图层对话框 | **「本地文件」合并成一个入口**（用户报「添加图层里面有两个本地文件」）：原来矢量图层、栅格图层下各有一个同名「本地文件」，用户得先判断文件类型才知道点哪个。改成顶层唯一一个「本地文件」，`accept` 同时列矢量与栅格扩展名，**矢量还是栅格由服务端按扩展名自动识别**（上传接口本来就返回 kind），一次还能混着传。矢量图层下只留「空间数据库」、栅格图层下只留「XYZ 瓦片 / WMS」 |
| 17:14:29 | 3-辅助 | `tests/check_webui.py` | 添加图层那节：分类断言改新集合，并加 3 条钉住这次的诉求 —— 「本地文件」只有一个入口、树里没有重名的「本地文件」、那个入口同时接受矢量与栅格扩展名 |
| 17:33:17 | 3-辅助 | `web/panels.js` · 顶部工具栏 | **改成 QGIS 桌面版那样的两排**（用户要求「把 QGIS 的两排工具都加上去」）：第一排 =「地图导航工具栏」（平移/放大/缩小/缩放到图层/缩放到选择/全图/上一范围/下一范围/**按比例缩放**/地图提示/刷新），第二排 =「图层 / 编辑 / 选择」三组。识别与测量在 QGIS 里是另一个独立浮动工具条 —— 地图左上角那组就是它，不重复。新增「按比例缩放」（1:N，算法与状态栏比例尺一致：每像素米数 × 96/25.4） |
| 17:33:34 | 3-辅助 | `tests/check_webui_panels.py` | 工具栏断言改成两排结构：两排 / 第一排=导航 / 第二排=图层·编辑·选择 / 共 4 组；按钮清单补 toolPan·toolZoomIn·toolZoomOut·zoomScale。50 → 60 项 |
| 17:44:22 | 主会话 | `web/index.html` · 空地图引导 | 「还没有图层」那条提示从**地图正中间的卡片**挪到**下沿一行淡字**（去掉卡片、边框、阴影；11.5px 小字，距底 9px）。空地图时它原来就是屏幕的主角，太吵 |
| 17:45:32 | 主会话 | `tests/check_webui_login.py` | 新增 4 条：空地图时给出提示、提示在地图**下半部分**、不是一张卡片（无背景无边框）、是小字（≤12px）。登录自检的服务数据目录是空的，正好能验 |
| 17:56:50 | 主会话 | `web/index.html` · 地图浮动工具条 | **精简成 QGIS 的 Map Tools**（只留 识别/测距/测面积）。平移/放大/缩小/缩放到图层/刷新 在**顶部第一排**（导航工具栏）里 —— 两边都放会出现**两个「平移」按钮**，既重复又不知道该点哪个。panels.js 的 `toolPan/toolZoomIn/toolZoomOut` 调的正是 index.html 的全局 `setTool()`，状态是同一套；快捷键 P/I/R/T 也照旧 |
| 17:57:17 | 主会话 | `tests/check_webui.py` | 浮动工具条断言改钉**语义**：= 识别/测距/测面积、且不与顶部导航工具栏重复；测距/缩放至图层两处点击改用顶部第一排的 `#toolbars button[data-act=toolPan|zoomLayer]`。70 → 72 项 |
| 18:07:21 | 主会话 | `web/panels.js` · 工具栏图标 | **消掉「同一个图标出现两次」**（用户报「上面的图标有重复的」）：添加图层 vs 添加要素 都是 ＋、放弃编辑 vs 删除选中 都是 🗑、地图提示 vs 地图浮动条上的识别 都是准星。新画三个图标：`addFeature`（菱形带加号）、`discard`（回退箭头）、`tip`（对话框气泡） |
| 18:08:01 | 主会话 | `tests/check_webui_panels.py` | 新增 2 条：**工具栏里没有重复图标**（按 SVG 内容比对）+ **地图浮动条不与顶部工具栏撞图标**。60 → 62 项 |
| 18:24:28 | 3-辅助 | `tests/server_helper.py` · 正式端口护栏 | **堵「正式服务在回归期间消失」这个口子**：18:08:49 正式实例没了 —— 服务日志停在一条正常的 200 请求上，**无异常、无报错**，而那轮全量回归仍然全绿（回归只看临时服务，正式实例死了它根本不知道；收尾体检当时打印的是 no orphan service processes，所以不是它杀的，**根因仍未定位**）。**加三道护栏**：① 新增 `PROD_PORT = 8848` 常量；② `_free_port()` 永远跳过 8848（临时服务只在 8851+）；③ `stop()` 的端口补刀**绝不**在正式端口上动手，`TestServer.__init__` 再加一道双保险 —— 补刀认的是「谁占着这个端口」，临时服务一旦挑中 8848，就会把用户的正式实例当成漏掉的残留杀掉。⚠️ **建议主会话**（`run_all_checks.ps1` 是主会话的独占文件，我没动）：收尾体检加一条「回归前后正式实例都还活着」，正式端口不在了要判成失败 —— 否则这类事在回归里永远是绿的 |
| 18:21:06 | 主会话 | `tests/server_helper.py` · `stop()` 补刀 | **补刀改成「按端口」而不是「按 pid 血缘」**：原来收集后代 pid 挨个杀，而 pid 会被系统复用，可能把**正式实例**误认成后代杀掉（真出过事：正式服务在回归期间消失，监听 8848 的那个被当成残留清掉）。端口才是可靠凭据 —— 我们自己动态挑的端口（8851+）上面只可能是我们的服务，正式实例永远在 8848，碰不到。删掉 `_descendant_pids()`（已无用）与 `POWERSHELL` 常量 |
| 18:30:25 | 主会话 | `tests/run_all_checks.ps1` | 补上「**回归前后正式实例都还活着**」守卫：跑之前记录 8848 在不在监听，跑完再核一次，**之前在、之后没了就当场红**（exit 1）。这类事故光看「有没有孤儿进程」发现不了 —— 补刀当时报的是「no orphan service processes」，正式实例却已经没了。注释一律英文（.ps1 必须纯 ASCII） |
| 18:38:12 | 3-辅助 | 守卫实测（**未改文件**，只验不改） | 主会话 18:30 那条守卫**实测会红，不是摆设**：用 `TcpListener` 起个假监听走了一遍判定 —— before=True → 停掉 → after=False → 分支触发 → exit 1；且端口释放能被 `Get-NetTCPConnection` 立刻反映，没有缓存延迟（否则守卫会读到过期的 true 而漏报）。`run_final34` 实跑：守卫两行都输出「production instance on 8848: alive (before/after)」，**正式服务全程存活**，20 脚本 / 825 项全绿。附一条实测到的坑：**QGIS 的 `python3.exe` 裸启动起不来**（缺 OSGeo4W 的 DLL 环境），拿它当「假监听」会静默失败、让人误判守卫有毛病 |
| 18:51:10 | 3-辅助 | **根因纠正**（未改文件） | 正式服务 18:08:49 消失的根因**不是**「`_free_port()` 挑中了 8848」—— 穷举了 `tests/` 下所有端口实参：硬编码的最小是 **8851**，唯一的非硬编码（`check_password_flag.py` 的 `free_port()`）用 `socket.bind(("127.0.0.1", 0))` 拿 OS 动态端口（49152+），**临时服务从不可能占用 8848**。真凶是主会话 13:59 加的 `_descendant_pids()` **按 pid 血缘**找后代再杀 —— pid 会被系统复用，正式实例被误认成后代。**「按端口补刀」仍然是对的**（它彻底去掉了血缘这个不可靠依据），但功劳要记在根因上：否则将来有人看到「端口补刀」就以为端口那条线验过，而它当时根本没被触发过 |
| 18:51:10 | 3-辅助 | **统计口径**（未改文件） | **全套断言总数是 831，不是 825**：按「结果：N 通过 / M 失败」统计只能抓到 18 个脚本 / 740 项 —— 漏掉 `check_launcher.py`(49) 和 `check_first_login.py`(42) 这 91 项，因为这两个是**系统 Python**跑的、stdout 按 GBK 写，PowerShell 按自己的 OutputEncoding 去解码，在 UTF-8 环境里整段乱码、连「结果：」都匹配不上。**权威口径：数 `[PASS]` 行**（纯 ASCII，不受编码影响）= 831、`[FAIL]` = 0。附两个读日志的坑：① `powershell -File x.ps1 > log.txt` 的日志是 **UTF-16LE**（PS 5.1 的 `>` 默认），用 ReadAllBytes+UTF8 读会满屏乱码，要用 Select-String 或显式 Unicode 解码；② 建议 `run_all_checks.ps1` 调系统 Python 前设 `$env:PYTHONIOENCODING='utf-8'`（该文件属主会话，已同步，未擅改） |
| 19:16:44 | 3-辅助 | netstat 补刀修复**独立复现**（未改文件） | 主会话 18:58 改 `_pids_listening_on()`：旧实现 `f":{port} " in line` 会匹配到 netstat 的**对端地址**列 → 「连到这个端口的客户端」也占一行（ESTABLISHED）→ **客户端进程被收进补刀名单** → `taskkill` 掉 httpx 客户端（**就在本进程里**）或无头浏览器；被杀的是测试自己时，收尾的 `os._exit(code)` 走不到 → **退出码非 0 但一条断言都没失败**（README 记的「退出码骗人」那个坑）。我独立复现了一遍（起监听子进程 + 本进程连上去）：netstat 同时出现 LISTENING 行与 ESTABLISHED 行，**新实现只返回监听者**，旧实现返回 `[监听者, 监听者, 本进程]` —— 定位与修复都正确。与我 18:24 的三道护栏**无冲突**（函数不同、互不重叠，护栏全在）。跑 `check_server_helper` 16/16、`check_webui_panels` 62/62、`check_webui_login` 19/19 全绿；后者还留了个活证据：补刀路径真被执行到（`端口 8865 还被 pid … 占着，已补刀`），且只补监听者、没误伤本进程。**审出一处缺口**：`check_server_helper.py` 的 `if not up: return` 会在监听子进程起不来时**跳过 3 条关键断言**，而汇总照常输出「16 通过」—— 建议按 2-辅助A 的写法加一条「断言总数 = 16」钉子。顺带发现：全套**只有 3/21 支**有「断言总数」钉子（check_first_login 41 / check_launcher 48 / check_tiles 13），其余断言被条件跳过时没人看得出来 |
| 17:20:03 | 主会话 | `tests/check_launcher.py` | 修新加断言「空间下拉列出两个空间」：管理员能看到的空间是**三类**（「全部」+ 共享 + 自己的个人空间，见 `launcher/files.py` 的 `labels = [ALL_SCOPES] + 各空间`），写死 2 是错的。改成钉**语义**（含 全部 / shared / `user:` 前缀），不钉死个数 —— 普通用户没有个人空间，个数本来就不同 |
| 17:30:11 | 主会话 | `tests/OWNERS.md` · 第 6 条 | 按 3-辅助 的提议把第 6 条**扩成两层**：「产物级断言」（要断到「真的是那个东西」：PNG 断 `naturalWidth > 0`、接口断字段类型/长度、矢量断要素数 > 0）+「**只在登录态暴露的路径，自检必须在登录态的服务上跑**」（免登录服务里 401 根本不会发生 → 那类路径被系统性漏测，「看不到导入的图像」就是这么漏的） |
| 15:13:48 | 2-辅助A | `tests/run_all_checks.ps1` · 收尾体检的孤儿判定 | **修「回归把正式实例当孤儿杀掉」**：原来按「除占 8848 的那个以外全杀」判定，而那个「占 8848 的」是**当下正在监听**的 pid —— 正式实例正好在重启、还没监听 8848（QGIS 初始化十几秒）时它是空的，于是**正在启动的正式实例被当成孤儿杀掉**，服务再也起不来（真踩到过：15:09 之后服务就没了）。改成「**只在监听 8848 以外的端口时才杀**」；并加一条自保断言：正式实例若被归进孤儿名单，回归当场失败而不是默默把服务停掉 |
| 16:58:43 | 2-辅助A | `tests/check_tiles.py` | **汇总数原来在撒谎**（写死 `13 - len(fails)`，实际只跑 9 条却报「13 通过」）→ 改真计数 + 加「断言总数」断言（少一条就说明有断言被条件跳过）。**新增 3 条**：登录态下数据接口确实要鉴权（对照组，否则下面两条是空过的）、底图瓦片**不带 token** 也能取到、带 token 也能取到 |
| 17:00:44 | 2-辅助A | `gisds/api/routes_render.py` · `basemap_tile()` 文档 | 把「**底图瓦片端点刻意免鉴权**」写进路由文档：前端用 Leaflet 的 `L.tileLayer` 加载，它发普通 `<img>` 请求、**带不了 Authorization 头**，端点一旦加鉴权，登录态下底图就是「401 → 拿到 JSON → 静默白屏」（与栅格图层那次同病）。底图是 Natural Earth 公共领域数据、瓦片里没有用户数据，公开是有意的取舍；`check_tiles.py` 有断言钉住 |
| 17:11:59 | 2-辅助A | `launcher/api.py` · `api()` | 新增 `_encode_url()`：把 URL 里的**非 ASCII 字符**按 UTF-8 百分号编码（路径结构原样保留）。数据集名可能带中文（用户传的「寿阳土地利用」），而 urllib 只能发 ASCII 的 URL —— 不编码直接 `UnicodeEncodeError`，文件浏览器里双击/下载中文数据集就崩 |
| 17:20:27 | 2-辅助A | `launcher/files.py` | **「上传的文件都在这里显示」**（用户原话）：① 列表改读**工作区** `/api/uploads`（= 磁盘上真实存在的东西）而不是**注册中心** `/api/datasets`（= 只含 QGIS 认得出来的图层）—— 传了但 QGIS 打不开的文件（比如只传 `.shp`、缺 `.dbf/.shx/.prj` 配套件）以前**既不显示、也删不掉**；② 空间下拉加「**全部**」并默认选中（用户传的东西不一定落在共享空间，可能传到个人空间去了）；③ 表格加「**文件**」列（显示上传的原文件名）和「**状态**」列（已识别/未识别）；④ 下载/打开目录改用列表里那条的 `files`/`directory`，未识别的数据集也能下载、也能双击打开目录；⑤ 未登录时身份标签给出明确指引（原来只有光秃秃的「未登录」+ 空表 + 一行日志，看着像「文件没了」）；⑥ 独立窗口打开且没登录时**主动弹登录框** |
| 17:24:35 | 2-辅助A | `tests/check_launcher.py` | 新增「文件管理器」段 13 条断言（数据目录完全独立的临时服务）：未登录有指引、独立窗口会弹登录框、上传普通/中文名/缺配套件的 .shp 三种文件、.shp 的 `registered=false`、三个都在列表里、「文件」列显示文件名、「状态」列标出未识别、中文数据集名取详情不崩、默认空间是「全部」。27 → 48 项 |
| 17:54:20 | 2-辅助A | `web/files.js` | **新建：网页端文件浏览器**（自包含，挂在 `index.html` 末尾一行 `<script src="/static/files.js">`，和 `panels.js` 一个路子，不动 index.html 的 boot 流程）。「文件」菜单加「文件浏览器…」。表格：数据集 / 空间 / **文件**（文件名是可点的下载链接）/ 大小 / **状态**（已识别·未识别）/ 加到地图·删除。空间下拉「全部 + 共享 + 本人的个人空间」，默认「全部」；数据源用**工作区** `/api/uploads`（不是注册中心 `/api/datasets`）—— 和桌面版同一套语义 |
| 17:54:29 | 2-辅助A | `tests/check_webui_files.py` | **新建**：网页文件浏览器自检（无头 Edge + CDP，19 项）—— 菜单里有这一项、脚本已加载、对话框能开、空间下拉是三类且默认「全部」、列表看得见三种上传（正常/中文名/缺配套件的 .shp）、「文件」列与「状态」列正确、文件名是下载链接、「加到地图」对未识别的禁用、切空间会变、能关掉、页面无报错 |
| 17:55:44 | 2-辅助A | `tests/run_all_checks.ps1` | 清单追加 `check_webui_files.py` |
| 17:56:50 | 2-辅助A | `web/index.html` | **只加一行** `<script src="/static/files.js"></script>`（挂载点，和 `panels.js` 并排）；文件浏览器本体全在 `web/files.js` 里，index.html 的 boot 流程没动 |
| 17:57:12 | 2-辅助A | `README.md` | 新增「文件浏览器（文件 → 文件浏览器…）」一节（列的含义、为什么默认「全部」、为什么读工作区而不是注册中心）；自检脚本清单补 `check_webui_files.py` |
| 18:28:33 | 2-辅助A | `tests/check_tiles.py` | **两处固定端口改动态**（`8857` / `8858` → `_free_port(...)`）。写死端口时和别的会话并行跑，后一个的健康检查会连到前一个的服务上；正式实例就在 8848，**差两个端口**，撞上就是「把生产实例当成自己的服务」 |
| 18:30:25 | 2-辅助A | `tests/run_all_checks.ps1` | 加**正式实例守卫**：跑套前记一次 8848 有没有在监听，跑完再记一次；「跑之前在、跑之后没了」→ 直接 `exit 1` 并说明「别用 pid 认它，pid 会复用，按端口认」。这类事故（按 pid 血缘补刀时踩到 pid 复用）光看「有没有孤儿进程」发现不了 —— 补刀那个脚本当时报的是「no orphan service processes」，正式实例却没了 |
| 18:31:37 | 2-辅助A | `tests/check_launcher.py` | 同上，`8854` / `8855` 两处固定端口改动态 |
| 18:51:52 | 2-辅助A | `tests/check_launcher.py` | **输出统一 UTF-8**（`sys.stdout.reconfigure`）。系统 Python 3.11 在 Windows 上按 **GBK** 写 stdout，调用方按自己的 OutputEncoding 解码 → 整段乱码；后果不是难看，是**按「结果：N 通过」统计断言数会漏掉这一整个脚本**（QGIS 那批是 UTF-8，只有用系统 Python 的这两个受影响）—— 看着像断言没跑，其实是跑过了 |
| 18:51:57 | 2-辅助A | `tests/check_first_login.py` | 同上，输出统一 UTF-8 |
| 18:57:14 | 2-辅助A | `launcher/theme.py` · `apply_style()` | 加 `root` 参数并**显式绑到本窗口自己的根**（`ttk.Style(root)`）：`ttk.Style()` 不带参数时绑的是「最近创建的根」（`tkinter._default_root`），可能是**别的窗口**的根 —— 样式会套到不相干的解释器上。另外同一个解释器只切一次主题（`_THEMED`），重复切主题会重发 ThemeChanged |
| 18:57:23 | 2-辅助A | `launcher/app.py` · `_style()` | `apply_style(self.ttk, self.root)` —— 把自己的根传进去 |
| 18:57:25 | 2-辅助A | `launcher/files.py` · `FileBrowserWindow` | **修孤儿定时器**：`after(900, self._maybe_prompt_login)` 原来没记句柄，窗口销毁后它照样到点触发 → Tk 报「invalid command name "..._maybe_prompt_login"」。现在记下句柄、`close()` 里一起取消 |
| 13:04:49 | 3-辅助 | `tests/OWNERS.md` · 改动署名流水 | 补签 3-辅助近期的 21 处改动（按 mtime 逐条核）；纠正备注里两处归属（`pngutil.py` 是主会话的、`check_password_note.py` 是 2-辅助A 的） |
| 13:08:52 | 2-辅助A | `tests/OWNERS.md` · 第 7 条 + 新增第 15 条 | 第 7 条补「项目根目录只能算一次、从同一处 import」（附「启动服务」按钮从来没起过服务这个实例）；新增**第 15 条「Tk 只能在主线程动」**。⚠️ 本行填的是**改动当时**观察到的 mtime，该文件此后被他人编辑，mtime 已前移 |
| 13:30:53 | 主会话 | `launcher/api.py` · `load_config()` | 配置解析失败不再裸吞，打印警告到 stderr（与服务端 `gisds/config.py` 同一做法） |
| 13:31:05 | 主会话 | `launcher/files.py` · `_apply_icon()` | 图标设置失败不再裸吞，打印警告到 stderr |
| 13:35:22 | 2-辅助A | `tests/check_launcher.py` | 按第 6 条下半句自查：把「弹窗三条」「上传返回数据集」从 `if popup:` / `if status == 200:` 里**放出来改成无条件断言**（上游失败时要明确失败，而不是跟着不跑）；新增**「断言总数 = 27」**这条 —— 任何断言被条件跳过都会显式失败。27 → 28 项 |
| 13:36:25 | 2-辅助A | `tests/check_first_login.py` | 新增**「断言总数 = 41」**（同上：钉死条数，少一条就说明有断言被跳过）。41 → 42 项。⚠️ 这条断言当场抓出我自己把期望值写成 42 的错（`checked` 在 check() 入口 +1，而实参在调用前求值，本条看到的是「其他断言数」41）—— 正是它该干的事 |
| 13:43:07 | 2-辅助A | `launcher/api.py` · `api()` | 加 `timeout` 参数（就绪探针要短超时，不能用默认的 30s） |
| 13:44:17 | 2-辅助A | `launcher/credentials.py` · `server_note_path()` | 定位备忘路径失败不再裸吞（**`launcher/` 下最后一处**）：打印警告到 stderr，并说明改用「登录被拒」判据 |
| 13:46:48 | 2-辅助A | `tests/check_first_login.py` | 销毁窗口前先 `cancel_tick()` —— 否则那个状态刷新定时器会变成孤儿，窗口销毁后照样到点触发，Tk 报「invalid command name」 |
| 14:00:28 | 2-辅助A | `launcher/tkguard.py` | 新建：Tk 根窗口留强引用。Tk 对象几乎总在引用环里，循环 GC 随时可能把它收走 —— 而 GC 是**哪个线程分配得多就在哪个线程跑**，后台线程里回收就会「Tcl_AsyncDelete: async handler deleted by the wrong thread」→ 进程 abort |
| 14:00:38 | 2-辅助A | `launcher/app.py` | **就绪三态**：`Service.state()` 返回 stopped / starting / ready（判据是「能不能干活」，不是「进程在不在」）；新增 `/readyz` 探测（后台线程问、结果回队列、主线程取）；顶栏状态栏三档文案；「启动中」时把「打开网页控制台」置灰。另修两处：`__init__` 排定时器时**把句柄记下来**（记丢了就取消不掉，窗口销毁后它照样触发）；新增 `cancel_tick()`，`on_close` 里先关弹窗再取消定时器再销毁 |
| 14:00:38 | 2-辅助A | `launcher/files.py` | `FileBrowserWindow` 记下刷新定时器句柄 + 新增 `close()`（连定时器一起取消）；根窗口也登记进 `tkguard` |
| 14:03:55 | 2-辅助A | `tests/check_launcher.py` | 新增「就绪探针」7 条断言（三态判定 / 三档文案 / 探针问的是 `/readyz` / 状态栏在没就绪时显示「启动中」/ 「打开网页控制台」置灰）；销毁顺序改成「先关弹窗再关主窗口」。28 → 35 项 |
| 18:40:09 | 主会话 | `web/help.html` | **新建：使用帮助页**（用户原话「写一份帮助」）。全中文，10 章：三分钟上手 / 界面分区 / 常用操作 / 快捷键 / 工具箱（Processing）/ 数据与空间 / 图形启动器 / 给别的系统接入 / 常见问题 / 自己跑一遍自检。目录用 `id="s1"`~`id="s10"` 锚点互跳，视觉沿用控制台的纸面浅灰 + 1px 细线 + 零阴影 |
| 18:40:20 | 主会话 | `gisds/api/app.py` | 新增 `/help` 路由（与 `/demo` 同款 FileResponse），帮助页能直接开在新标签页，不依赖控制台的 JS |
| 18:40:22 | 主会话 | `web/index.html` · `MENUS.help` | 「帮助」菜单加一项「使用帮助…」（`window.open("/help","_blank")`），控制台里一键进帮助 |
| 18:40:50 | 主会话 | `tests/check_web_merge.py` | 加 6 条断言：help.html 存在 / 是中文 / 目录锚点与章节一一对应 / 内容不空 / 菜单里有「使用帮助」/ 菜单指向 `/help`。25 → 31 项 |
| 18:42:23 | 主会话 | `README.md` · 网页控制台 | 加一段指向 `http://127.0.0.1:8848/help`，帮助页从 README 也找得到 |
| 18:51:38 | 主会话 | `README.md` · 已知坑 | **修正计数**：自检脚本实际是 **20** 支（`tests/check_*.py` 共 20 个，全在 `run_all_checks.ps1` 清单里），原文写「19 个脚本」少算了一支 |
| 18:51:44 | 主会话 | `web/help.html` · 第 10 章 | 同上：「19 支自检脚本」改成「20 支」。实测本轮 `run_final36` 是 **20 脚本 / 831 条断言**全绿（825 + 本轮新增的 6 条） |
| 18:58:36 | 主会话 | `tests/server_helper.py` · `_pids_listening_on()` | **修「端口补刀把客户端也杀了」**（3-辅助 18:51:10 提醒「端口那条线从没被触发过、也没验过」，去验就验出这个）：匹配写得太松 —— `f":{port} " in line` 会在 netstat 里匹配到**对端地址**那一列，而「连到这个端口的客户端」正好占着一行（ESTABLISHED）→ 客户端进程被收进补刀名单。**实测**：起一个监听 + 连上去，宽松匹配返回 `[监听者, 客户端]` —— 补刀会 `taskkill /F` 掉 httpx 客户端（**就在本进程里**）或无头浏览器。改成只认 **`LISTENING` 且本地地址就是该端口**的行（`parts[0]=='TCP'` / `parts[3]=='LISTENING'` / `parts[1].endswith(':port')` / pid 取 `parts[4]`） |
| 19:01:09 | 主会话 | `tests/run_all_checks.ps1` | 清单追加 `check_server_helper.py`（20 → **21** 支）。另按 3-辅助 18:51:10 的建议，在调系统 Python 前设 `$env:PYTHONIOENCODING='utf-8'`：这两个 tkinter 脚本按 ANSI 代码页（GBK）写 stdout，日志按 UTF-8 读就整段乱码，「结果：N 通过 / M 失败」那行连 grep 都匹配不上（**统计口径要改成数 `[PASS]` 行**）。该文件仍保持纯 ASCII、无 BOM、LF 行尾、PS 5.1 解析 0 错误 |
| 19:01:15 | 主会话 | `web/help.html` · 第 10 章 | 自检脚本计数 20 → **21** 支 |
| 19:01:54 | 主会话 | `README.md` | 自检脚本清单加 `check_server_helper.py`；计数 20 → **21**；新增一段讲这支脚本在验什么（脚手架是公共底座，坏时的症状是「整套莫名其妙全红」或「正式实例在回归期间消失」）。顺带修 `check_webui.py` 的「共 52 项」—— **实际 72 项**（数 `[PASS]` 行）。「临时服务端口 8851/8852/8853」改成「动态挑，永远跳过正式实例的 8848」 |
| 19:02:30 | 主会话 | `tests/check_server_helper.py` | **新建：测试脚手架自检**（16 项）。脚手架（`tests/server_helper.py`）是所有自检的公共底架，它出问题时症状是「整套自检莫名其妙全红」或「正式实例在回归期间消失」，**很难往脚手架上想** —— 之前 20 支自检没有一支在验它。钉三组：① 临时服务**永远不占正式端口 8848**（`_free_port(8848)` / `_free_port(8000)` 都不给 8848；`TestServer(port=8848)` 会被挪到 8849；再用打桩逼 `_free_port` 失效，验 `__init__` 的 `RuntimeError` 双保险真的会触发）；② 端口补刀**只补「监听者」**（真起一个监听子进程 + 本进程连上去，断言**本进程 pid 不在补刀名单里**、名单里没有陌生 pid）；③ 没人监听的端口返回空名单 |
| 19:02:30 | 主会话 | `tests/OWNERS.md` · 口径变更记录 | 加一行「`_free_port()` 挑中 8848 是根因」= **已废弃**（见下表） |
| 19:21:55 | 主会话 | `gisds/api/routes_console.py` | **新建：Python 控制台接口**（`/api/console/exec` `/state` `/reset`）。权限由 `config.console_access` 决定（admin 默认 / user / off），**免登录模式一律拒绝** —— 那种模式下「能访问网页」就等于「能在服务器上跑代码」 |
| 19:24:06 | 主会话 | `gisds/api/app.py` | 挂 `console_router` / `plugins_router`；lifespan 里在 **QGIS 线程**上加载插件（插件可能要注册 Processing provider，必须在 QGIS 线程）；退出时卸载插件 |
| 19:26:39 | 主会话 | `plugins/gisds_hello/metadata.json` | **新建：示例插件的元数据**（目录名 = slug `gisds_hello`） |
| 19:28:21 | 主会话 | `gisds/config.py` | 新增 4 个配置项：`console_access`（默认 admin）、`console_timeout`（默认 30 秒）、`plugin_dir`（默认 `plugins/`）、`plugins_enabled`。⚠️ **插件目录默认放项目根、不放 `data/`** —— `data/` 下的每个目录都会被旧布局迁移搬进 `data/shared/`（插件会被当成上传的数据集挪走，这个坑踩过） |
| 19:29:35 | 主会话 | `plugins/gisds_hello/__init__.py` | **新建：示例插件**，把 SDK 每个能力用一遍：右栏面板 / 菜单项 / 自定义接口 / 动作 / 一个 Processing 算法（`gisds_hello:featurecount`，注册后自动出现在工具箱里） |
| 19:30:35 | 主会话 | `web/plugins.js` | **新建：插件界面**（609 行）。右栏「插件」标签页（清单 + 安装 zip + 启用/停用/重载/卸载，只有管理员看得到）、插件贡献的面板（`html` / 表格两种渲染）、插件贡献的菜单项（挂到 `MENUS`，目标菜单不存在时**新建顶栏按钮**并沿用同一套开合逻辑）。**由子 agent 按主会话定下的接口契约写**，主会话复核并接手集成 |
| 19:32:33 | 主会话 | `gisds/core/pyconsole.py` | **新建：Python 控制台内核**。三件事做对：① **线程** —— 代码走 `run_qgis` 排在唯一那条 QGIS 工作线程上（自己开线程 = 跟渲染抢 QGIS 对象 = 偶发崩溃）；② **隔离** —— 每个用户一份命名空间；③ **别把服务搞死** —— `os._exit/abort/exec*` 被 `_GuardedOs` 代理挡住（`import os` 拿到的也是被拦过的那个），`exit()`/`quit()` 报错而不是退出。末行表达式按 REPL 语义回显（`displayhook` 遇 None 不打印，所以 `print(x)` 后面不会多一行 None） |
| 19:33:37 | 主会话 | `web/index.html` | 挂 `console.js` / `plugins.js` 两个脚本；「视图」菜单加「Python 控制台」（没权限时 console.js 根本不挂，这项就静默失效） |
| 19:35:57 | 主会话 | `tests/check_console.py` | **新建：控制台自检**（32 项）。执行 / 表达式回显 / 异常回溯、命名空间持久与**按人隔离**、普通用户 403、`off` 模式 403、免登录 403、`os._exit`/`os.abort`/`os.execv`/`exit()`/`sys.exit()` 全部被拦下且**服务没死**。**由子 agent 写**，主会话复核 |
| 19:38:06 | 主会话 | `gisds/api/routes_plugins.py` | **新建：插件接口**。`GET /api/plugins`（清单 everybody 可看）、`install`（multipart 上传 zip）/ `enable` / `disable` / `reload` / `uninstall`（**只有管理员**）、`action`（调用插件注册的动作）。另有 `_RouteRegistrar`：把插件的接口挂到 FastAPI 上，**插件停用时按名字前缀 `gisds_plugin_<slug>_` 摘掉**（不停用就还挂着，插件 unload 了接口却还在被调） |
| 19:38:16 | 主会话 | `gisds/core/plugins.py` | **新建：插件内核**。发现 / 加载 / 卸载 / 启停 / 重载、`PluginContext` SDK（`add_panel` / `add_menu_item` / `add_route` / `add_action` / `register_processing_provider` / `log`）、**zip 安全安装**（路径穿越 / 绝对路径 / 符号链接 / 超大 / 条目数超限 / 缺 metadata.json 一样都不放过）、插件坏掉**不许拖垮服务**（`init()` 抛异常只记成 `error`）。⚠️ `discover()` **只换元数据、保留运行时状态** —— 原来每次扫描都造一条新记录，把已加载插件的面板/菜单/实例全冲掉（网页每拉一次清单就抹一次） |
| 19:38:41 | 主会话 | `tests/run_all_checks.ps1` | 清单追加 `check_console.py` / `check_plugins.py`（21 → **23** 支） |
| 19:38:41 | 主会话 | `tests/check_plugins.py` | **新建：插件自检**（39 项）。装 / 启停 / 重载 / 卸载、贡献物（面板 / 菜单 / 动作 / 接口）、权限（普通用户 install/enable 403）、**zip 安全**（路径穿越 / 绝对路径 / 符号链接 / 缺 metadata / 坏 zip 全部被挡且没留半个目录）、坏插件不拖垮服务、**停用后它自己的接口也调不到了**。**由子 agent 写**，并由它发现「停用后路由还挂着」这个 bug（已由主会话修掉） |
| 19:44:12 | 主会话 | `web/console.js` | **新建：控制台界面**（552 行）。底部抽屉（`position:fixed` 覆盖地图，不改其它元素高度）、4px 顶边拖拽改高度并存 localStorage、`stdout` 墨色 / `stderr` 琥珀 / `error` 锈红 / `traceback` 浅锈红、Enter 执行 Shift+Enter 换行、↑↓ 历史、`Ctrl+\`` 开合、**没权限时整块不渲染也不发请求**。主会话补了一处：把手条**按状态栏实际高度让位**（原来 `bottom:0` 会盖住坐标/比例尺那条状态栏）。**由子 agent 写**，它自己写了一套 42 项无头浏览器断言跑通 |
| 19:48:08 | 主会话 | `README.md` | 新增「Python 控制台与插件」一节（控制台用法 + 权限表 + 已知限制；插件包结构 + `ctx` 能力表 + 两种安装方式 + zip 安全说明）；配置项补 4 个新开关；自检清单补两支新脚本，计数 21 → 23 |
| 19:48:09 | 主会话 | `web/help.html` | 新增第 6 章「Python 控制台与插件」（原来 6~10 章顺延为 7~11 章，目录锚点同步改）；快捷键表补 `Ctrl+\`` 与 Enter/Shift+Enter；自检脚本计数 21 → 23 |
| 19:48:37 | 主会话 | `tests/check_webui.py` | ① 菜单栏断言**放宽**：原来写死「正好 5 个」，插件往顶栏加菜单（示例插件加了「工具」）就会红 —— 改成「内置 5 个都在且相对顺序不变，多出来的合法」；② 新增一节「Python 控制台与插件（界面层）」8 条：免登录态**控制台把手条不出现**（没权限就不该有）、右栏有「插件」标签、能切过去、插件面板列出已装插件、插件贡献的面板在右栏、插件加的菜单在顶栏、**无管理权限时不显示安装按钮** |
| 19:58:22 | 主会话 | `tests/check_api.py` | **「算法总数 440」不能死钉一个数**：插件会往工具箱里加算法（示例插件贡献 1 个 → 441）。把本脚本的插件目录指向一个**空目录**，440 钉的就还是「**QGIS 自带的**那 440 个」；插件算法本身由 `check_plugins.py` 验 |
| 19:58:29 | 主会话 | `tests/check_webui.py` | **修「跑算法」那条偶发红**：页面忙的时候 `scrollIntoView` 之后立刻取坐
| 20:50:36 | 3-辅助 | `web/panels.js` + `web/icons/qgis/`（新建） | **工具栏与浮动条的图标全部换成 QGIS 官方 SVG 原文件**（用户要求「所有功能 图标都沿用 QGIS」）。图标按 QGIS 3.44 源码 `src/app/qgisapp.cpp` 的 `createActions()` 里 `setIcon(getThemeIcon("/xxx.svg"))` 的**实际对应关系**取用（42 个，源路径 `images/themes/default/`），不重画、不近似；全量「action → 图标」对照表留在 `web/icons/qgis/ACTION-ICONS.txt`（156 条）。**必须走 `<img>` 引用**：QGIS 图标是 24×24 的**填充式多灰阶**，而本项目 `svg()` 硬编码 `viewBox="0 0 16 16"` + `fill="none"` + `stroke="currentColor"`，内联进来会被压成线框、彻底变形。分组与顺序也照 QGIS：桌面版顶部从左到右是 文件→图层→数字化→地图导航→属性→选择，我们第二排改成 图层·编辑·**属性**·选择（属性表从「图层」组挪进「属性」组，对应 QGIS 的 Attributes 工具栏）；识别/测量仍在浮动条（QGIS 的 Map Tools）。**QGIS 桌面版没有的两个功能保留自绘**：「按比例缩放」和「字段统计」—— 后者在 QGIS 里是 Processing 的「基础统计」算法，本来就不在工具栏上。浮动条在 index.html（**主会话的文件，我没动**）里用 MutationObserver 运行时替换。⚠️ **许可**：QGIS 是 **GPL v2**（仓库根 COPYING），图标属 QGIS 作品的一部分 —— 本项目对外分发需整体 GPL v2+，已写 `web/icons/qgis/LICENSE-QGIS.txt`；若要闭源分发，把 `QGIS_ICON` 表切回自绘即可（改动集中在这一个表） |
| 20:59:47 | 3-辅助 | `tests/check_webui_panels.py`（mtime 20:54:45）· `tests/check_webui.py`（mtime 20:59:47）—— **这两个是主会话的文件，我改了断言** | 工具栏图标从内联 `<svg>` 变成 `<img>` 之后，原断言会**空过**：①「工具栏里没有重复图标」「浮动条不与顶部撞图标」原来按 `svg.ic` 的 innerHTML 比对，现在得按 `img.ic` 的 src 比对 —— 两种**混在一起**比（`img:` / `svg:` 前缀），只比一种就会让另一种变成空过；②「工具栏用描边 SVG 图标」改成「工具栏用 QGIS 官方图标」(≥20 张 img) +「QGIS 没有对应图标时才用自绘」(≤2 张 svg)；③「浮动条用 QGIS 官方图标」(==3)；④**新增**「三个 QGIS 图标文件真的取得到」—— 404 时 `<img>` 不报错，页面上只是一片空白，不查资源根本发现不了。断言总数 62 → **67**（钉子当场红过一轮，正是它该干的）。`check_webui.py` 那条「工具条用描边 SVG 图标」改成数 `.ic`（两种都算），**条数不变、钉子 80 不动**（尽量不与主会话 worker 的改动打架） |标会取到旧位置，鼠标事件落偏 → 作业根本没起（状态停在「—」）。现在点一次没生效就**再点一次** —— 重试的是**点击**，断言没放宽（作业仍然必须真跑成功） |
| 20:08:25 | 主会话 | `tests/check_server_helper.py` | **补「断言总数」钉子**（3-辅助 18:5x 审出的缺口）：原来 `check_sweep_targets()` 里是 `if not up: return` —— 监听子进程起不来时，**后面 3 条关键断言被直接跳过**，而结尾照样打印「16 通过 / 0 失败」，看不出少了什么。现在①那 3 条改成**无条件执行**（起不来就**失败**，不跳过）；②加 `EXPECTED_CHECKS = 16` 钉子（行为断言 7+5+4）。**少验一条比验失败更难查** |
| 20:20-20:32 | 主会话 | `tests/check_*.py`（17 支） | **全套 23 支现在都有「断言总数」钉子**（3-辅助审出的系统性缺口：任何一条断言被条件跳过，汇总数会照常输出「N 通过」，**没人看得出来**）。做法：① 先跑一遍**基线**拿到每支的权威条数，再用 ast 逐支核对源码条数，两边对上（**改完必须重新数**，基线数字只当起点）；② 条件分支里的断言**一律改成无条件**（同分支同条数，缺条件就判失败）——「宁可让断言失败，也不让断言被跳过」；③ 4 支 webui 脚本抽 `summary()`，让「找不到浏览器 / 服务未就绪」这些提前返回也过钉子。**可观察的行为变化**：没有 Edge/Chrome 时那 4 支由「静默成功」变成「失败」—— 这正是钉子的本意（「空过比失败更难查」）。**由子 agent 按主会话定下的样板做**，主会话复核并接手。⚠️ `check_password_note.py` 是**真分支**（备忘在/不在，条数 9/8 不同），钉子按分支取两个期望值。⚠️ **其中 4 支是 3-辅助的独占文件**（`check_panels.py` / `check_webui_panels.py` / `check_webui_login.py` / `check_mcp_panels.py`）—— 改动只有「加钉子 + 把条件跳过改成显式失败 + 提前返回统一走 summary()」，**没有删改任何一条原有断言的语义**；已把逐处改动发给 3-辅助 复核，它钉的行为（面板两排结构 / 图标去重 / 登录态栅格真的显示 / MCP 工具总数 27）逐条仍在且全绿 |
| 20:50:45 | 主会话 | `gisds/core/render.py` · `build_raster_style()` | **修「真彩色遥感影像整片发紫」**（用户报「打开一张遥感照片，整体发紫，和 QGIS 不一样」）。原因：这里**无条件**把栅格渲染器换成「单波段 + viridis 渐变」，只用第 1 波段出图 → 绿色通道被压没（实测平均 RGB 从 [28.6, 33.0, 37.4] 变成 [50.8, 12.0, 71.0]，**紫色像素占 74.93%**）。渐变第一档色 `#440154` 本身就是深紫。**QGIS 桌面版给多波段影像配的是「红/绿/蓝 = 1/2/3 波段」的多波段彩色渲染器，我们把它整个覆盖掉了。** 现在真彩色影像保留 QGIS 自己的渲染方式（用户**主动**选单波段渐变时仍照旧） |
| 20:52:01 | 主会话 | `gisds/core/registry.py` | ① 新增 `is_truecolor_renderer()`：判据是「**QGIS 自己给它配了什么渲染器**」而不是自己数波段（QGIS 还认得出彩色表、带 alpha 的波段）；② 建图层时把 QGIS 的默认渲染器 `clone()` 下来存进 `RasterLayer.default_renderer`（后面套配色时靠它还原）；③ 图层清单（`to_dict()` 与 `layer_refs()`）多带一个 `truecolor` 标记，网页据此默认用真彩色 |
| 20:51:36 | 主会话 | `web/index.html` | 真彩色影像的网页适配：新增 `isTruecolor()`；`styleOf()` 的默认渲染模式对真彩色是 `truecolor` 而不是 `viridis`；`drawLayer()` 不把 `truecolor` 传给后端（那是后端默认行为）；**符号系统面板**对真彩色多给一项「真彩色（QGIS 默认）」并把它排在第一；属性面板的「数据类型」显示「真彩色（RGB 合成）」 |
| 20:56:44 | 主会话 | `tests/check_core.py` | 新增一节「真彩色遥感影像不能被渲成一片紫」（5 项）：真彩色被认出来 / 套配色后仍是多波段彩色渲染器 / **出图绿通道没被压没**（像素级）/ 显式 `mode=viridis` 仍走渐变 / 单波段栅格不算真彩色（对照组）。43 → 48 项（钉子同步） |
| 20:57:05 | 主会话 | `web/help.html` | 常见问题加一条「遥感影像打开后整片发紫」：说明真彩色现在按 QGIS 的方式合成显示，符号系统面板第一项是「真彩色（QGIS 默认）」，别改成单波段 |
| 20:57:14 | 主会话 | `README.md` · 已知坑 | 记下「真彩色遥感影像不能拿单波段假彩色去渲」这一条（原因、实测数字、修法、由哪支自检钉住） |
| 21:04:15 | 主会话 | `tests/run_all_checks.ps1` | **修「失败支数是空的」**：`$bad = $results \| Where-Object {...}` 只命中一支时，它是个**单个对象**而不是数组，`.Count` 在 PSCustomObject 上取到 $null → 汇总打印成 `FAILED:  script(s)`，支数是**空白**（和「汇总数说谎」同一类毛病）。改成 `@(...)` 包起来，并顺手把支名列出来 |
| 21:23:09 | 主会话 | `tests/check_webui.py` | **修工具箱那节偶发红**（连续 3 次里红 1~2 次，单跑也会复现，不是负载问题）：① `CDP.click()` 把「滚动」和「取坐标」放在同一个表达式里 —— 元素在视野外时要先 `scrollIntoView`，紧接着量到的坐标可能对不上，鼠标事件就落到别的元素上，**而且不报任何错**（`click()` 返回 False 也没人看）。现在分成「滚 → 等 250ms → 再量」三步；② 点之前加一条断言「『运行算法』按钮可见且可用」，点不动时能一眼看出是按钮的问题；③ 「结果已登记进数据源」原来固定等 2 秒就断言，而服务端登记要**扫一遍数据目录**，机器忙时超过 2 秒 → 作业明明成功了却报红。改成**轮询**（最多 24 秒）—— 等的是异步操作，不是把断言放宽。80 → 81 项 |
| 21:39:12 | 主会话 | `tests/run_all_checks.ps1` · 残留检查 | **修收尾体检的误报**：残留过滤器被加宽到 `*.db` 之后，把 `gisds.db` 也算成残留了 —— 但那是 `check_api.py` / `check_auth.py` **故意**用的工作区库（`GISDS_DB_PATH` 指到那儿），每次跑都在，不是泄漏，天天误报。加白名单排除 `gisds.db` 这一族。⚠️ 改的过程中我自己**先写成了中文注释**（174 个非 ASCII 字节），违反了「.ps1 必须纯 ASCII」这条硬规矩（PS 5.1 按 ANSI 读无 BOM 的 UTF-8，中文注释可能把引号吃掉）—— 已改回英文，复核：0 非 ASCII / 无 BOM / 纯 LF / PS 5.1 解析 0 错误 |
| 20:51-21:23 | 3-辅助 | `web/panels.js` · `web/icons/qgis/` | **工具栏与浮动条图标换成 QGIS 官方 SVG 原文件**：拉 QGIS 3.44 源码（`src/app/qgisapp.cpp`，17491 行）读出 `createActions()` 里 `setIcon(getThemeIcon("/xxx.svg"))` 的**实际对应关系**，取 42 个图标放在 `web/icons/qgis/`（来源 `images/themes/default/`），**没有重画、没有近似**。关键：**必须用 `<img>` 引用**（QGIS 图标是 24×24 填充式多灰阶，内联进 `svg()` 会被 `viewBox 0 0 16 16` + `fill:none` 压成线框变形）。分组也照 QGIS（第二排改成 图层·编辑·属性·选择，4 → 5 组）；QGIS 桌面版本来就没有的「按比例缩放」「字段统计」保留自绘。浮动条在 index.html（主会话的文件），用 MutationObserver 运行时替换，没动 index.html。留档 `web/icons/qgis/ACTION-ICONS.txt`（156 条 action → 图标对照） |
| 20:54:45 | 3-辅助 | `tests/check_webui_panels.py` | 图标从内联 `<svg>` 变 `<img>` 后，原来那两条按 `svg.ic` 的 innerHTML 比对的断言**会空过** → 改成**两种混在一起比**（`img:` / `svg:` 前缀，只比一种会让另一种空过）。另加 4 条：工具栏用 QGIS 官方图标(≥20)、QGIS 没有对应图标时才用自绘(≤2)、浮动条用 QGIS 官方图标(==3)、**图标文件真的取得到**（404 时 `<img>` 不报错，页面上只是一片空白，不查资源发现不了）。⚠️ `EXPECTED_CHECKS` 因此 62 → **67** |
| 21:28:05 | 3-辅助 | `tests/check_webui.py` | 只改一条（「工具条用描边 SVG 图标」→ 改成数 `.ic`），条数与钉子不动，尽量不与主会话的改动打架 |
| 22:00:25 | 主会话 | `LICENSE` | **新建：GPL v2 许可证全文**（18.8 KB，从 QGIS 安装目录 `apps/qgis-ltr/doc/LICENSE` 里截出正文）。本项目建立在 **PyQGIS** 之上，`qgis/core/__init__.py` 的版权头就是 GPL v2+ → 衍生作品必须按 GPL 分发，之前项目根目录**一个许可证文件都没有**，对外分发会缺件 |
| 22:01:04 | 主会话 | `README.md` | ① 两处过时的图标描述改掉（原文说「16px 描边 SVG、跟随文字颜色」，实际已换成 QGIS 官方 24×24 填充式 `<img>`）；② 新增「许可与图标来源」一节：许可证来源与依据（PyQGIS 已是 GPL v2+，图标不是决定因素）、图标来源与选取方式、**必须用 `<img>` 不能内联**的原因、以及「要闭源该怎么切回自绘」（改动集中在 `web/panels.js` 的 `QGIS_ICON` 一张表，但 PyQGIS 本身的 GPL 才是真门槛） |
| 22:23:05 | 主会话 | `tests/run_all_checks.ps1` · 文件头 | **回归日志文件名必须每次唯一**：这个脚本自己不写日志，是调用方用 `>` 重定向的 —— 两个会话用同一个文件名会**互相覆盖**（`>` 是截断），最后谁的结果是绿的都分不清。主会话的 `run_final46.txt` 就被并行会话的同名回归覆盖过一次。脚本头写明规矩：日志名带会话标识（`run_<who>_<n>.txt`） |
| 22:56:39 | 主会话 | `tests/check_webui.py` | **修「算法作业执行成功」偶发红**（3-辅助 报的真问题 A，根因比「OUTPUT 互删」更深）：① **临时服务改用独立 data_root**（把示例数据 + 生产底图复制进临时目录）—— 一次解决两件事：不再往生产 `data/users/` 留空目录（每轮自检注册一个临时用户就留一个），并发时「加到地图」登记的 OUTPUT 数据集各在各的目录里，**不会互删**（原来删的是「第一个以 OUTPUT 开头的」，不看他是谁登记的）；② **`CDP.click()` 加命中测试** —— 那个点上如果**不是**目标元素，鼠标事件就落不到它身上：实测踩到过浏览器扩展注入的浮层 `DIV#VideoTogetherWrapper` 正好盖在「运行算法」按钮上，事件全落在浮层身上、handler 根本没跑、页面**连一条报错都没有**（事件压根没到按钮），表现是「点了没反应」且时好时坏。现在命中不了就改用 JS 触发（元素自己的逻辑照样跑），另外点之前先确认按钮可见可用、点之后用「按钮变 disabled」（handler 第一件事就是置灰）判定点击真的生效。连跑 5 轮全绿。⚠️ 顺带在清理处补了注释：现在删「第一个 OUTPUT」是安全的（独立 data_root 里只有本轮的 OUTPUT），**若哪天把 data_root 改回生产根，就必须改成「拍快照、只删新增的」**；并按 3-辅助 的建议再补一句「**改之前先问 3-辅助**」—— 那处是并发互删的唯一入口，而且改 data_root 会让「临时服务完全不碰生产目录」这个**前提**整个失效，污染和互删会一起回来 |
| 23:13:22 | 主会话 | `README.md` · 许可与图标来源 | **改掉我自己埋的第二处误导措辞**：原文写「若将来要闭源 / 非 GPL 分发…切回自绘即可」—— 只对图标这一项成立，**PyQGIS 那个门槛切图标解决不了**。改成「图标不是许可门槛；切回自绘只关乎保真度与工作量，改动集中在 `QGIS_ICON` 一张表，但它不会让项目变成闭源」。**教训**：上一轮认领这个错误时只改了消息，没回头扫磁盘上自己写的每一处 —— 改一处不够 |
| 00:16-00:21 | 主会话 | **项目改名：GISDS → Browser QGIS** | 用户要求「项目名字改成 Browser QGIS」。**只改用户可见的名字**，内部标识符一律不动：`GISDS_*` 环境变量（config / server_helper / README 示例 / 自检脚本全在用）、`gisds` 模块名与 `-m gisds` 调用、`prog="gisds"`、`gisds.db` 文件名、`window.GISDS_PANELS` 全局名 —— **改了会破坏现有部署和配置**。改的是：网页四处（index 标题+顶栏 brand+「关于」里的服务名 / login 标题+brand / demo 标题+h1 / help 标题+页眉+大标题+页脚）、桌面启动器窗口标题与顶栏、文件浏览器窗口标题、CLI 启动横幅与 `doctor` 抬头与 argparse 描述、FastAPI 的 `/docs` 标题、MCP 服务器描述、`__init__` 与控制台 `__doc__`、README 大标题、requirements 注释、底图安装的 User-Agent、示例插件的显示名与 author。**自检断言同步改了 3 条**（`check_launcher.py` 的窗口标题/启动器标题/弹窗标题）—— 名字写死在断言里，不改就会红。⚠️ 过程中我自己踩了个坑：用 PowerShell 批量替换 `.bat` 时，**嵌套数组被拍平**，`.Replace('R','E')` 把 `doctor.bat` / `mcp.bat` 里所有 R 换成了 E（REM→EEM、%ERRORLEVEL%→%EEEOELEVEL%），而预想的替换一条都没生效。已按原文重写这两个文件并复核（6 个 .bat 全部 0 非 ASCII / 纯 LF）。**教训**：批量改文件别用嵌套数组拼 map —— 一处一处改，或者先把 map 写成 JSON 再读 |
| 00:33-00:43 | 主会话 | **栅格图层改按层级出瓦片（修「放大效果很差」）** | 用户报「导入遥感图像后放大效果很差」。根因：前端把**整层范围**一次性渲成 1400px 的大图再用 `imageOverlay` 铺上去 —— 放大时这张图被反复拉伸，Sentinel-2（源 8350×6352）1 个图像像素就对应 6 个源像素，z=16 那一级实际是拿 **11×11** 个源像素放大成 256×256（放大 23 倍，糊成色块）。**服务端本来就有 XYZ 瓦片接口，只是前端没用**。改动：① `web/index.html` 栅格分支改用瓦片层；新增 `AuthTileLayer`（`<img>` 发不了请求头 → 用 fetch 拿 blob，令牌走 Authorization 头，**不进 URL/不进访问日志**），图层范围外的瓦片直接给 1×1 透明 PNG（不让服务端白渲一遍）；② `core/render.py` 的 `render_tile()` 接受 `colors`/`mode`，且**缓存键必须带上样式**（否则自定义配色的请求会命中默认样式的缓存，改了配色却还是老样子）；③ `api/routes_render.py` 瓦片路由加 `colors`/`mode` 参数；④ 自检断言跟着改（`img.leaflet-image-layer` → `img.leaflet-tile[data-layer]`，瓦片是异步加载的要等）。实测瓦片接口 z=12/14/16/18 分别 116/31/19/18 毫秒，**不需要额外金字塔也扛得住**（源影像没有 .ovr，但源只有 8350×6352，逐窗读取不慢）。README 补了这一条 |
| 00:50 | 主会话 | `gisds/core/render.py` · `gisds/config.py` · `gisds/api/app.py` | **把 `tile_cache_size` 配置项接上**（之前**根本没接** —— `render.py` 里写死 256，配置改了没用）。网页端现在按层级出瓦片，一屏 25 块上下，缓存 256 太小：平移一下就被挤满、瓦片全要重渲。默认提到 512，新增 `configure_cache()` 由应用启动时从配置设一次（不让 core 反向依赖 api） |
| 21:23:52 | 3-辅助 | `web/panels.js` · 注释补充 | 写明「按空间关系选择要素」在 **QGIS 桌面版没有对应的工具栏按钮**（「按位置选择」是 Processing 的算法），这里借的是 QGIS 官方的「按半径选择」图标（`mActionSelectRadius.svg`）—— 空间选择里最接近的一个，但**严格说是近似**。纯注释，不改行为 |
| 21:50:28 | 3-辅助 | `tests/server_helper.py` · `tests/check_server_helper.py`（**后者是主会话的文件，我加了断言**） | **断掉「自检往生产目录堆垃圾」的根因**。现象：自检的临时服务**直接用生产 data_root**（`check_webui.py` 的 `TestServer(port=8852)` 没传 `data_root` → `server_helper.env()` 也不设 `GISDS_DATA_ROOT` → 服务用 `config.py` 的默认 `PROJECT_ROOT/data`），于是每跑一轮就注册一个临时用户，在 `data/users/` 下留一个**空目录**。这些目录**不是无害垃圾**：`core/registry.py` 的 `for child in sorted(self.users_root.iterdir())` 意味着**每一次数据源扫描都要遍历它们**，而登记结果/加载图层/刷新都走这条路 —— 目录一多，扫描就慢，`check_webui.py` 的「结果已登记进数据源」就会假红（主会话 21:23 那次修的正是这条，改成轮询 24 秒 —— 那是治标）。**修法**：`TestServer.start()` 时拍下 `users/` 的目录快照，`stop()` 时删掉**本次运行新建的**且**仍然是空的**目录（「本次新建」→ 别人早先建的不动；「仍然是空的」→ 有数据的一律不碰；删掉安全，因为 `deps.get_workspace()` 每次取工作区都有 `root.mkdir(parents=True, exist_ok=True)`，目录按需重建）。**存量 77 个已清到 0**（`data/users` 现在只剩真实数据那一个 `a0676cb9f7ae`，14 项完好）。新增断言「临时服务不在生产 users/ 留空目录」钉住它，`EXPECTED_CHECKS` 16 → **17** |
| 22:05:24 | 3-辅助 | `web/icons/qgis/LICENSE-QGIS.txt` | **改掉两处会误导人的表述**。原文写「本项目若保持闭源/非 GPL 分发，**不得**分发这些 SVG —— 需要改回自绘图标」，读起来像「切回自绘就能闭源」—— **不成立**。本项目整个架在 **PyQGIS** 上，而 PyQGIS 的 Python 绑定本身就是 GPL v2+（本机 `apps/qgis-ltr/python/qgis/core/__init__.py` 的版权头就是 GPL v2+，`gui/__init__.py`、`core/contextmanagers.py` 同）—— **用了 GPL 的库，衍生作品就得按 GPL 分发**，门槛在**图标之前**。改后写明：图标只关乎**保真度与工作量**，不是许可姿态的决定因素；真要换回自绘，改动仍集中在 `QGIS_ICON` 一张表 |
| 22:15:49 | 3-辅助 | `tests/server_helper.py`（mtime 22:14:58）· `tests/check_server_helper.py` | **修并发下两条假红**（都实证过，不是推演）：① **端口挑选竞态** —— `_free_port()` 只是「挑一个**看起来**空闲的端口」（bind 一下就放开，**并不占着**），两个进程同时挑会挑中**同一个**端口，后起的服务起不来（实测：我的 `check_webui.py` 撞上另一支的 `check_webui.py` → 整支提前返回 → 钉子红 `0 ≠ 81`）。现在 `start()` 起不来就**换一个端口重试**（真起不来重试也白试，三次后照样 False —— **该红还是红**）；② **用户目录清理的并发互删** —— 「本次运行新建的」在两个并发实例之间**区分不了**，探针建在生产 `data/users/` 时会被另一支当成自己的删掉（实测红过一次：`selftest_leak_188176 还在`）。断言改用**独立的 data_root**（不碰生产目录，清理逻辑是同一段代码），并发下不再互相干扰。`check_server_helper` **19/19**、`check_webui` **82/82** 实跑全绿 |
| 22:20:05 | 3-辅助 | `tests/server_helper.py` · `_abort()` 补删临时库 | 端口重试有个自己引入的残留：`_abort()` 只杀进程不删这一轮用过的临时用户库，而库文件名里带的是**本进程的 pid**、这个进程还活着 → `_sweep_stale_workspace()` 按 pid 判活就**永远不会**去删它（重试时端口变了、库名也变了，所以上一次尝试的库是另一份文件）。补上：`_abort()` 里按 `self.port` 删掉 `.db` / `-wal` / `-shm`（带重试，文件被占着时等一下再删） |
| 22:23:15 | 3-辅助 | `web/icons/qgis/LICENSE-QGIS.txt` · 图标清单对齐 | 核了「目录里的文件」与「`QGIS_ICON` 表里引用的」是否一致：**目录 42 个、实际引用 26 个**（工具栏 23 + 浮动条 3），另 16 个是**备料**（QGIS 其它动作的图标：按表单选择/矩形·多边形·任意选择/测量角度·方位角/书签/顶点工具/编辑下的拉/属性表的三个变体/加矢量·加栅格/全部编辑/取消当前图层选择）。原文只列了 26 个却没说明另外 16 个是什么，容易让人以为「下载了但没用上 = 冗余」。补上说明并把备料逐个列出：备料不进 `QGIS_ICON` 表就不会被引用，页面只加载用到的那些 |
| 01:11:04 | 主会话 | `gisds/core/processing.py` · `run_algorithm()` | 新增 `_check_layer_type()`：算法参数要的图层类型与实际拿到的不符时，抛中文错（例：「算法 X 的参数 Y 需要矢量图层，但拿到的是栅格图层（Z）」）。**背景**：`QgsProcessingParameterFeatureSource`（type=`source`）只收矢量，不查这一下的话 QGIS 原生只报「无法加载 INPUT 源图层：无效值」 |
| 01:11:55 | 主会话 | `web/index.html` · `paramField()` | `source` 类型参数的下拉框改为只列矢量图层（`const want = p.type==="raster" ? "raster" : "vector"`）。原来把 source 当「任意图层」，下拉框里会列出栅格，用户一挑就撞上那句天书 |
| 01:24:38 | 主会话 | `gisds/api/routes_crs.py` · 四个端点全部 | **补上漏挂的鉴权依赖**（`current_principal`）。这是全项目唯一一个没挂鉴权的数据路由：实测生产实例（require_login=True）不带令牌 `/api/crs`、`/api/crs/predefined`、`/api/crs/detail`、`/api/crs/preview.png` 全部 200，而 `/api/datasets`、`/api/layers` 全部 401。**注意别误伤底图瓦片**：`/api/tiles/basemap/*` 公开是**有意**的（docstring 明写「刻意不做鉴权，别顺手加上去」—— Leaflet 用 `<img>` 加载瓦片带不了 Authorization 头，公开的是 Natural Earth 公共领域数据，且 `check_tiles.py` 有断言钉住）。`preview` 里取底图 land 图层也改成 `registry.datasets(principal)`；顺带把 docstring 里「三个端点」改成「四个端点」 |
| 01:20:44 | 主会话 | `tests/check_processing_types.py` | 新建：Processing 参数图层类型自检（13 条行为断言，数据全隔离到临时目录、跑完自删）。钉的是「参数类型对不上时给的是中文错、不是 QGIS 原生那句『无效值』」这条修复 |
| 01:30:18 | 主会话 | `gisds/core/pyconsole.py` · 护栏 | **A8**：`from os.path import join` 原来报 ImportError（护栏把 `os.path` 也换成了只转发顶层属性的代理，错误信息还指向错的模块名）。改成：`os` 顶层仍用代理，`os.path` 这种子模块用**转发全部属性**的代理，且子模块里引用的**真 os** 换成护栏那份（`os.path` 在 Windows 上是 `ntpath`，而 `ntpath` 自己 import 了 `os`，不换就能绕过去）。**A9**：真 os 从**实例属性**挪进**闭包**（`__getattr__` 只在常规查找失败时触发，实例属性是能被直接拿到的 —— 原来 `os._real._exit(0)` 当场把服务干掉）；`_BLOCKED_OS` 补 `kill` / `killpg` / `system` / `putenv` / `unsetenv` |
| 01:31:12 | 主会话 | `gisds/api/routes_meta.py` · `/healthz` | 探针改成用 `registry.dataset_count()`（**只数不扫描**）。原来调 `registry.datasets()`，而那会触发一次数据目录扫描 → 要排到那条唯一的 QGIS 工作线程上；那条线程被一段卡死的控制台代码占着时，探针跟着一起卡住（外部监控会误判服务挂了） |
| 01:32:06 | 主会话 | `gisds/api/routes_plugins.py` · `_RouteRegistrar` | **A3**：摘路由改成**按登记时记下的名字精确摘**（新增 `_by_slug`）。原来按名字前缀 `gisds_plugin_<slug>_` 猜，而 slug 本身可以含下划线 —— 停用插件 `a` 会把插件 `a_b` 的路由连坐摘掉（`a_b` 的接口变 404，而清单里它还显示 loaded=true） |
| 01:32:37 | 主会话 | `gisds/core/plugins.py` | **A4**：`install_zip` 覆盖同名插件前先 `unload()` 旧的（原来直接 `rmtree` 覆盖，旧 record 还在、旧路由还挂着，FastAPI 按注册顺序匹配 → 旧路由永远赢，「接口是旧版、动作是新版」两份代码并存）。**A5**：`discover()` 在旧记录带 `error` 而新记录没有时**留住旧错误**（原来只有 `loaded` 的记录才留运行时状态，坏插件每次拉清单都被新 record 盖掉 → 管理员再也看不到当初为什么失败）。**A10**：`unload()` 顺手把该插件的模块从 `sys.modules` 里清掉（新增 `_purge_modules`；只清属于这个插件的，别把同名标准库模块删了）—— 否则改了子模块的代码再「重载」拿到的还是旧值 |
| 01:33:28 | 主会话 | `gisds/core/registry.py` | **A2**：`_ensure_scanned()` 的判据从「扫到东西了」改成「**扫过了**」（新增 `_scanned` 标志）。原来写的是 `if self._datasets: return` —— 数据目录为空时每次查询都重扫，而扫描要排到那条唯一的 QGIS 工作线程上；那条线程被卡死的控制台代码占着时，这个同步等待会把**事件循环线程**一起焊死，连 `/healthz` 都不回。**②**：`resolve_ref()` 的「数据集:图层」分支补 `principal.can_see()` 校验（原来直接取 `self._datasets.get`，谁都能拿别人的私有图层跑算法）；「磁盘路径」分支新增 `_scope_of_path()`，**只认数据根目录里面的**文件（原来任何绝对路径都收，能指着服务器上任意文件让 QGIS 去读） |
| 01:33:55 | 主会话 | `gisds/core/processing.py` · `run_algorithm()` | **①**：输出参数（`sink` / `vectorout` / `rasterout` / `fileout` / `folderout`）的落盘路径**一律落在作业目录里**（新增 `_output_path()`，只认「光秃秃的名字」）。原来把调用者给的 OUTPUT 原样透传给 QGIS：绝对路径就写绝对路径（实测普通用户能把文件写进**别人的私有空间**），相对路径按服务端 cwd 解析（= 项目根目录，真出现过一个 `TASKOUT.gpkg` 落在这儿） |
| 01:34:07 | 主会话 | `gisds/api/routes_datasets.py` · `/datasets/rescan` | **③**：`scan()` 之后按 `principal.can_see(d.scope)` 过滤再返回。`Registry.scan()` 返回的是**全部空间**的数据集（含别人的私有数据集和服务器绝对路径），任何登录用户点一下「重新扫描」就能看到所有人的数据 |
| 01:34:21 | 主会话 | `gisds/core/jobs.py` · `JobManager` | **④⑤⑥⑦ 的底座**：`list()` 增 `owner` 过滤；新增 `clear()` 真正清掉内存里的作业记录（原来「清空」只删文件，记录原封不动 → 清完还能查到作业、下载输出却 404） |
| 01:34:35 | 主会话 | `gisds/api/routes_processing.py` · 作业四个端点 | **④⑤⑥**：作业按 owner 隔离（新增 `_own_job()`：作业的 meta 里有图层引用、outputs 里有服务器绝对路径）。看不见就当**不存在**（404）—— 回 403 等于告诉对方「这个作业确实存在」。**⑦**：`DELETE /api/processing/jobs` 改成**只有管理员能清**（原来任何登录用户都能一键删光所有人的作业输出），并且真的清内存记录 |
| 01:34:42 | 主会话 | `gisds/api/routes_panels.py` · `/logs`（GET + DELETE） | **⑧⑨**：两个端点都补 `current_principal`。原来匿名能读服务端日志（里面有绝对路径、用户名、内部报错），也能一键清空（等于匿名擦掉排障痕迹） |
| 01:35:41 | 主会话 | `gisds/api/routes_render.py` · 两个瓦片端点 | **⑩⑪**：新增 `_check_tile_range()`，z / x / y 越界回 **422**。原来是无约束路径参数，越界时 `tile_bbox_3857` 里的 `math.sinh` 直接 OverflowError → 500，traceback 还很难看懂 |
| 01:35:48 | 主会话 | `gisds/core/render.py` · `tile_bbox_3857()` | 同一处的兜底：先校验 z / x / y，越界抛 ValueError（由应用的异常处理器转 422），不再让 `math.sinh` 溢出成 500 |
| 01:29:43 | API 扫描 worker | `tests/check_api_surface.py` | 新建：API 全量面自检（347 条断言，覆盖鉴权/管理面/控制台/CRS/数据集/上传/越权矩阵/面板/工具箱/插件/栅格矢量/幂等/免登录模式）。**按「产品应有的正确行为」断言**，把 11 条缺陷钉在那儿等人修 |
| 01:38:14 | 插件 worker（主会话改断言） | `tests/check_plugin_console_mcp.py` | 新建：插件/控制台/MCP 深度自检（114 条断言）。worker 按「当前实际行为」写；主会话修完缺陷后把 8 条改成了「修好之后该是什么样」：重装热替换(A4)、坏插件错误留存(A5)、slug 前缀连坐(A3)、`from os.path import`(A8)、`os._real`/`os.kill`(A9)、控制台超时后 /healthz 仍应答(A2) |
| 01:37:38 | UI 扫描 worker | `tests/check_ui_paths.py` | 新建：网页界面「路径」全面扫描（真实浏览器，点每个菜单/工具栏/面板） |
| 01:38:23 | 主会话 | `tests/run_all_checks.ps1` | 回归清单追加 `check_processing_types` / `check_api_surface` / `check_plugin_console_mcp` |
| 01:39:23 | 主会话 | `tests/check_api_surface.py` | 按修好的行为改断言：`DELETE /api/processing/jobs` 改成**管理员专用**（普通用户 → 403），并补两条「管理员清空之后记录真的没了」；`EXPECTED_CHECKS` 347 → 349 |
| 01:43:02 | 主会话 | `gisds/mcp/server.py` · `serve_mcp()` | 非 stdio 传输（`streamable-http` / `sse`）启动时打**醒目告警**：MCP 不走鉴权、能看到所有空间，本机用没问题、对外暴露前要确认网络边界。（默认行为按 README 的明写取舍**不改**） |
| 01:43:46 | 主会话 | `README.md` · 接口表后 + MCP 节 | 新增「哪些接口要登录、哪些不用」的表（公开的只有探针 / `/api/meta` / 登录流程 / 底图瓦片，并说明底图瓦片为什么**有意**公开）；再写三条隔离规则（算法作业按 owner 隔离、算法输出只能落在自己的作业目录、图层引用按调用者过滤）；MCP 节补「传输方式决定暴露面」 |
| 01:44:39 | 主会话 | `gisds/core/plugins.py` · `PluginContext.add_action()` | 文档补：动作回调**可以多收一个 `principal`**（调用者身份），插件就能自己做权限判断；只收 `params` 的老写法照常能用 |
| 01:44:46 | 主会话 | `gisds/api/routes_plugins.py` · `plugin_action()` | 新增 `_call_action()`：按回调签名决定传不传第二个参数（`principal`）。动作回调原来只收 `params`，插件根本不知道是谁在调它 —— 跟插件自己注册的接口（收 `principal`）对不上 |
| 01:45:33 | 主会话 | `tests/check_plugin_console_mcp.py` | 断言改动：MCP 那 4 条从「缺陷」改成「**按设计如此**」（README 明写「MCP 是服务级工具，不走 HTTP 鉴权，能看到所有空间」—— 不是缺陷，是取舍）；补 1 条「动作回调能拿到调用者身份」；`EXPECTED_CHECKS` 114 → 115 |
| 01:46:37 | 主会话 | `plugins/gisds_hello/__init__.py` · `FeatureCountAlgorithm.COUNT` | 算法 id 去掉重复前缀：QGIS 的 id = `provider.id() + ":" + name()`，provider 已经是 `gisds_hello`，`name()` 再带一遍就成了 `gisds_hello:gisds_hello:featurecount`（OWNERS 里写的意图本来就是单前缀） |
| 01:47:57 | 主会话 | `tests/check_panels.py` | 日志那几行补上 `headers=mine`（其它面板调用都带了令牌，唯独日志这几行没带 —— 加上鉴权后暴露出来）；再补 1 条「匿名读日志 → 401」；`EXPECTED_CHECKS` 79 → 80 |
| 01:54:04 | 主会话 | `tests/check_panels.py` · 日志段 | 取值改成兜住（``body = r.json() if r.status_code == 200 else {}``）—— 响应不是日志载荷时**断言失败**就行，别让脚本自己 KeyError 崩掉 |
| 02:04:25 | 主会话 | `tests/run_all_checks.ps1` · 收尾的孤儿进程清理 | **不再杀别的会话的临时服务**：只有**父进程已经不在**的才算孤儿（原来只按「监听在 8848 以外的端口」判断 → 并行跑 UI 扫描时把人家的服务杀了，跟历史上「正式实例在回归期间消失」是同一类事故）。⚠️ 本文件**必须纯 ASCII**（PS 5.1 按 ANSI 读无 BOM UTF-8，中文注释会把引号吃掉）—— 这里的注释一律英文 |
| 02:05:22 | 主会话 | `gisds/core/processing.py` · `_layer_label()` | 类型不符的报错里，括号里带的是图层**名字**。`QgsMapLayer.name` 在 PyQGIS 里是**方法**不是属性，`getattr(layer, "name")` 拿到的是绑定方法 → 打出来是 `<built-in method name of QgsRasterLayer object at 0x...>`，用户根本看不出是哪个图层 |
| 02:05:38 | 主会话 | `tests/check_processing_types.py` | 新增 1 条「报错里带的是图层名字（不是方法 repr）」；`EXPECTED_CHECKS` 13 → 14；另加 `__main__` 异常护栏 |
| 02:11:47 | 主会话 | `tests/server_helper.py` · `_pid_chain()` / `TestServer._owns_port()` | **关掉「串台」**。`_free_port()` 只是「挑一个**看起来**空闲的端口」，bind 一下就放开，**并不占着** —— 两个进程同时挑会挑中同一个端口；而就绪检查（「这个端口上 /readyz 返回 200 吗」）在端口被**别人**占着时**照样通过**，于是测试连到了别人的服务：用户库是同一个 → 注册账号撞「已被占用」，页面被对方的鉴权拦到登录页。**看起来像产品坏了，其实是串台**（真踩过：同一支自检并发跑两遍，第二遍 22 条断言全红；再跑一遍又全绿 —— 典型竞态）。修法：就绪检查通过后**再确认端口的归属** —— 监听者的**祖先链**里要包含我们这个 cmd.exe（`cmd.exe /c run-qgis-python.bat` → `python3.exe`，监听的是最后那个），不是就换端口重试。整条链**一次** PowerShell 调用问完（逐层问会明显拖慢，一次自检要起好几个临时服务）；查不全时**不判死刑**（宁可放过也不误杀） |
| 02:12:32 | 主会话 | `tests/check_server_helper.py` | 新增「端口归属：不会连到别人的服务」一节（4 条：祖先链查得出、无关监听者起来、自己的端口认得出、**别人的端口认得出不是自己的**）；`EXPECTED_CHECKS` 18 → 22 |
| 01:53:16 | 主会话 | `tests/check_*.py`（20 支） | **`__main__` 加异常护栏**：`main()` 抛异常时不让它一路退到解释器退出 —— 那样会先跑 QGIS 的析构，Windows 上大概率变成 **0xC0000005**（访问违例），**真实原因（那条 KeyError 之类）反而被埋掉**。真踩过：`check_panels.py` 有一条 KeyError，进程退出码却是 0xC0000005，看着像「崩了」，实际是「某条断言失败后脚本自己没兜住」。改成：打印回溯 + 退出码 1。⚠️ 批量改写把行尾转成了 CRLF，**已全部改回 LF**（仓库约定是 LF） |
| 02:32:35 | 主会话 | `gisds/core/panels.py` | **②③ 认出 provider 的主键（FID）列**：新增 `_fid_field_indexes()`（用 `provider.pkAttributeIndexes()`，**不按名字猜** —— OGR 的 FID 列就叫 `fid`，QGIS 把它当普通属性一起返回，属性表里 fid 出现两次、还能编辑，一保存就「未能删除要素」、**整个编辑会话被回滚、改动全丢**）。`table()` 的字段清单加 `"fid"` 标记；`update_feature()` / `field_calculator()` 直接挡掉写主键（422 + 说清原因）。**④ 过滤表达式先自己解析**：`QgsExpression(where).hasParserError()` → 422（QGIS 3.44 的 `setFilterExpression` 对语法错**不抛异常**，那圈 try/except 形同虚设，用户只看到「0 个要素（已过滤）」，分不清是写错了还是真没数据） |
| 02:33:17 | 主会话 | `web/panels.js` | **②③** 属性表不再把主键列显示两遍（表头已有一列 fid）、字段计算器的目标字段里剔掉主键列；**⑤ 统计面板把下拉里选中的字段传给接口**（原来没传 → 下拉是摆设，选了也白选），下拉选项改用属性表那份完整清单（否则选了单个字段后下拉会越缩越窄） |
| 02:34:06 | 主会话 | `web/index.html` | **① 「服务信息」/「关于本服务」点不出来**：原来复用右键菜单那个 `#ctxMenu`，而菜单项的 click 会冒泡到 document 的「点外面就把弹出层收起来」监听 —— 同一个事件里刚 show 就被 remove，弹层**一帧都没显示过**。改成用独立的 `#aboutBox`，并**延到本次 click 之后再显示**。**⑧ 算法数不再写死 440**（改用 `/api/meta` 的真值，存进 `S.algoCount`）。**⑨ XYZ 的 URL 模板校验 `{z}{x}{y}` 占位符**（原来只判空 → 填个不含占位符的 URL 也「成功」，地图上多一个永远加载不出瓦片的图层，零提示） |
| 02:35:17 | UI 扫描 worker（主会话改断言 + 挪端口） | `tests/check_ui_paths.py` | 新建：网页界面「路径」全面扫描（真实浏览器 + CDP，161 条断言：菜单项 / 工具栏 / 右栏面板 / 要素编辑 / 图层属性 / 添加图层 / 文件浏览器 / 控制台 / 插件 / 报错路径 / 刷新一致性 / 界面泄漏）。主会话改两处断言（「关于」弹层改查 `#aboutBox`）并把服务端口基数从 8902 挪到 **8972**（8851~8907 是别的自检在用的那段，并行跑会撞） |
| 02:35:23 | 主会话 | `tests/run_all_checks.ps1` | 回归清单追加 `check_ui_paths`（26 支） |
| 02:39:48 | 主会话 | `gisds/api/routes_panels.py` · `/api/panels/table` | 新增 `sort` / `order` 两个查询参数（属性表按字段排序，QGIS 桌面版列头可点排序） |
| 02:40:40 | 主会话 | `web/panels.js` | **⑥ 新增「信息」面板**（右栏第一个标签）：后端 `/api/panels/overview` 早就有了，只是网页没接 —— 现在显示图层名/数据集/空间/几何类型/要素数/字段数/坐标系/数据提供程序/范围/编辑状态。**⑦ 属性表列头可点排序**：`<th>` 加 `data-sort`，点一下按该字段排、再点换升降序（`addOrderBy`），排序在分页**之前**做，翻页顺序才是对的；加了 `th.sortable` 的样式（可点 + 当前排序列高亮 + ▲▼ 标记） |
| 02:42:08 | 主会话 | `gisds/core/panels.py` · `attribute_table()` | 排序的落点：``request.addOrderBy(f'"{sort}"', 升序)``（PyQGIS 3.44 的写法 —— C++ 那套 ``OrderBy`` / ``OrderByExpression`` **没暴露**出来，踩过）；字段名加引号，中文/带空格的字段名才不会被当成表达式。排序字段不存在 → 422 |
| 02:42:50 | UI 扫描 worker（主会话改断言） | `tests/check_ui_paths.py` | 「列头可点击排序」那条改查**字段列**的表头（`th[data-sort]`）—— 原来查的是第一个 `<th>`，那是行号列「#」，本来就没有 `data-sort` |
| 03:08 | 主会话 | `README.md` · 自检脚本一节 | 补上这轮新增的 4 支：`check_processing_types` / `check_api_surface` / `check_plugin_console_mcp` / `check_ui_paths` |
| 03:08 | 主会话 | `tests/run_all_checks.ps1` | **有脚本失败时也要跑收尾卫生检查**：原来是「一有脚本红就 `exit 1`」，把整段卫生检查（正式实例回归前后都活着？有没有残留的临时服务/文件？）跳过了 —— 而那恰恰是**失败时最该看**的（失败的那支可能正是把现场弄坏的元凶）。改成先跑完卫生检查，最后再按脚本失败数决定退出码 |
| 03:40 | 主会话 | `README.md` · 改名说明 | **新增一节**：产品名从 GISDS 改成 Browser QGIS，但 `GISDS_*` 环境变量 / `gisds.db` / `-m gisds` / `gisds` 模块名 / `gisds_plugin_*` 路由前缀 **一律不动** —— 它们是写进存量 config.json 和部署脚本里的**契约**，顺手改成 `BROWSER_QGIS_*` 会让老部署读不到配置、**静默回落到默认值**（口令、端口、路径全变）且不报任何错。列了清单 + 「用户可见的名字用新名、机器可见的名字保持原样」的准则 |
| 03:42 | 主会话 | `web/index.html` · `AuthTileLayer` | **瓦片请求可中止**：给 fetch 挂 `AbortController`，Leaflet 的 `tileunload` 事件（瓦片被移出视野 / 图层被移除）时立刻中止。瓦片是用 `fetch` 取 blob 的（`<img>` 发不了 Authorization 头），而**把 `<img>` 摘掉并不会让 fetch 停下来** —— 服务端照样渲完那十几毫秒，回来时已经没人要了；快速连续缩放时这些浪费会排在真正要的那一屏前面（服务端只有一条 QGIS 线程）。中止的请求**不算失败**（`AbortError` 不报给 Leaflet，否则会被标成「加载出错」） |
| 03:43 | 主会话 | `README.md` · 已知坑 | 新增「瓦片铺屏的真实耗时（z=18 实测）」：单块冷 17~58ms、48 块全冷 607~692ms、全缓存 195~207ms、并发 12 块 ~20ms/块；并说清「并发不会更快（只有一条 QGIS 线程）、要更快只能少渲染、真要提速得在 QGIS 侧预渲染金字塔」 |
| 10:19:49 | 主会话 | `gisds/core/gis_tools.py` · **新建** | 把 27 个工具抽成**一份共享定义**（`ToolSpec` / `ToolContext` / `TOOLS` / `bind`）。三条不许漂移的硬规矩：① 工具说明 = 处理函数的 docstring 原文（不另抄）② 参数 JSON Schema **从签名自动派生**（参数名/类型/必填项不会跟实现脱节），只叠加一句中文说明 ③ 渲染结果用 core 自己的 `RenderResult`，**core 不依赖 mcp 包**。`bind()` 必须用 `inspect.signature(..., eval_str=True)` 解析注解 —— 塞了 `__signature__` 之后 CPython 不再 eval，注解会留成字符串，pydantic 建不出模型 |
| 10:19:49 | 主会话 | `gisds/mcp/server.py` · `build_server()` | **真重构**：27 个 `@server.tool()` 闭包改成从 `gis_tools.TOOLS` 登记（工具名 / 说明 / 签名 / 行为一个字节没变，`check_mcp.py` 68 项、`check_mcp_stdio.py` 31 项、`check_mcp_panels.py` 35 项全过）。`INSTRUCTIONS` 改为与 `TOOL_INSTRUCTIONS` 同一段文本（内容不变）。文件行尾由 CRLF 统一成 LF（仓库约定是 LF） |
| 10:21:46 | 主会话 | `gisds/core/llm.py` · **新建** | 双协议 LLM 客户端：OpenAI 兼容（`/chat/completions`）与 Anthropic 原生（`/messages`）。内部用**中立消息格式**（user / assistant+tool_calls / tool），由 adapter 各自转换；Anthropic 那边 system 独立成字段、工具结果并成 user 消息里的 `tool_result` 块、连续工具结果合并进同一条消息 |
| 10:24:06 | 主会话 | `gisds/core/gis_tools.py` · 租户隔离 | **补三处 principal 漏洞**：`datasets()` 原来不带 principal（列得出所有空间）、`resolve_ref` 原来不带 principal（能拿别人的图层跑算法）、书签的 `scope` 原来不校验（能写进别人的私有空间）。新增 `ToolContext.datasets()` / `resolve_ref()` / `check_scope()`；MCP（principal=None）行为不变 |
| 10:24:34 | 主会话 | `gisds/core/ai.py` · **新建** | agent 循环（LLM → tool_calls → 按**调用者身份**执行 → 回喂 → 收敛，最多 8 轮）+ 服务端默认配置存取（`AIConfigStore`，落在**用户库旁边**的 `ai_config.json`）。浏览器侧只能覆盖「用哪个模型、拿哪个 key」，**不能改权限** |
| 10:25:45 | 主会话 | `gisds/api/routes_ai.py` · **新建** | `/api/ai/config`（GET/PUT）、`/api/ai/chat`、`/api/ai/tools`。**谁能用是定死的**（产品决定，不给配置项）：所有登录用户都能用；免登录模式的匿名访客一律 403。改服务端默认配置只有管理员。**Key 永不下发到前端**（只回 `has_key`） |
| 10:25:57 | 主会话 | `gisds/api/app.py` | 挂 `ai_router`（`console_router` 之后、`plugins_router` 之前） |
| 10:27:06 | 主会话 | `web/ai.js` · **新建** | 右栏「AI 助手」聊天面板（自包含，跟 console.js 一个路子）。右栏新增标签页（没权限就**整个撤掉**，安静地当作没这功能）。聊天请求**自己写一份 fetch**，不复用全局 `api()` —— 全局那份 20 秒超时 + 自动重试，对聊天都不合适（模型慢是常态，重试就是多花一份钱） |
| 10:29:13 | 主会话 | `web/index.html` | 末尾加一行 `<script src="/static/ai.js">`；「视图」菜单加一项「AI 助手」（没挂载时点了没反应） |
| 10:32:41 | 主会话 | `tests/check_ai.py` · **新建** | AI 助手自检（77 条断言）。**在测试进程里起一个假模型服务**（标准库 `http.server`，不联网），按剧本回话并把模型服务真实收到的请求记下来逐字段核对 —— 两种协议不是靠读代码断言的，是真的对过报文。钉住三件容易出事的事：权限（匿名拒绝 / 普通用户能聊但改不了配置 / 改服务端默认仅管理员）、租户隔离（B 列不出 A 的私有数据集、直查报「不存在」）、Key 不外泄（配置与聊天响应里都不能出现明文） |
| 10:34:02 | 主会话 | `tests/run_all_checks.ps1` | 回归清单追加 `check_ai.py`（27 → 28 支） |
| 10:35:13 | 主会话 | `tests/check_ui_paths.py` | 新增「AI 助手面板」一节 4 条断言（登录态下标签页出现、面板挂上、显示配置来源、没配模型 API 时给出**明确提示**而不是静默失败）；`EXPECTED_CHECKS` 161 → 165 |
| 10:37:17 | 主会话 | `README.md` | 新增「网页里的 AI 助手」一节（两种协议、两个 Key 来源的优先级、谁能用、写操作默认关闭、接口表）；目录结构补 `data/ai_config.json`；已知坑补三条（AI 调工具必须按身份过滤 / `__signature__` 不会被求值 / AI 配置别放进数据根目录）；自检清单补 `check_ai.py` |
| 10:52:07 | 主会话 | `gisds/core/ai.py` · `merge_settings()` | **补一条凭据外泄的缝**：原来浏览器只要改 `base_url`，服务端就会**把自己的 key 送到用户指定的地址去** —— 任何登录用户都能把全机凭据送到他指定的地方。改成：浏览器换了接口地址**或协议**时，服务端那份 key **不跟着走**（必须自带 key），否则直接拒绝并说清原因。`check_ai.py` 补 3 条断言（被拒 / 模型服务一次请求都没收到 / 提示能看懂），`EXPECTED_CHECKS` 77 → 80 |
| 11:10:46 | 主会话 | `gisds/core/ai.py` · `run_tool()` | 工具失败时的归类改准确：原来 `TypeError` 一律报「参数不对」，但它**也可能来自工具实现内部**（参数名已经在调用前按 schema 校验过了）；改成「工具 X 执行失败：<类型>: <详情>」。`ok` 标记也改成**显式返回**，不再靠「结果文本是不是以某句话开头」这种字符串猜测 —— 猜错了网页上就会把失败显示成成功 |
| 11:11:03 | 主会话 | `tests/check_ai.py` | 补 2 条断言（成功的工具步骤 `ok=true`、失败的工具步骤 `ok=false`），`EXPECTED_CHECKS` 80 → 82（83 项全过） |
| 12:18:37 | 主会话 | `pack-release.ps1` · **新建** | 打包发行版。直接从源码树打进 zip（不做中转拷贝），**打完自己开包核对**：有没有 `__pycache__`、有没有用户数据（`data/users/*`）、有没有凭据（`gisds.db` / `admin_password.txt`）、必需文件在不在 —— 有问题就退出码 1，不留「看着像成功」的包。`deps/` 一起打进去（收件人不用装任何东西）；`data/shared/` 的示例数据 + 内置离线底图也带上。**纯 ASCII + LF**（PS 5.1 按 ANSI 读无 BOM UTF-8） |
| 12:18:46 | 主会话 | `gisds/__init__.py` | 版本 1.0.0 → **1.1.0**（本版新增网页 AI 助手）。`/readyz` 与 OpenAPI 里能看到。无自检断言应用版本号；`check_api_surface.py`（350）/ `check_live.py`（43）/ `check_web_merge.py`（32）复验全绿 |
| 12:19:04 | 主会话 | `RELEASE_NOTES.md` · **新建** | 发布说明（跟着发行包一起发出去）：一分钟上手、运行前提、本版新增的 AI 助手、与 MCP Server 的关系、数据空间、自检、已知功能缺口、许可 |
| 12:25:55 | 主会话 | `README.md` | 新增「打包发行版」一节（怎么打、什么打什么不打、打完自己核对）+ 目录结构补 `pack-release.ps1` / `RELEASE_NOTES.md` / `release/` |
| 13:15:08 | 主会话 | `prepare-qgis-runtime.ps1` · **新建** | 做一份**可搬家**的 QGIS 运行时（standalone 发行版用）。原理：QGIS 是 OSGeo4W 安装，`bin\o4w_env.bat` 从**自身位置**推导 `OSGEO4W_ROOT`，`etc\ini\*.bat` 里全是 `%OSGEO4W_ROOT%` 相对量 —— 整棵树没有写死的安装路径（只有几个卸载脚本写死，那些不跑）。裁掉：grass(346MB，provider **算法数是 0**)、saga(45MB，**没注册**)、Qt5/qml(302MB，无头服务不加载)、i18n 37 个语言(135MB)、Qt5/translations(33MB)、site-packages/wx(34MB)、__pycache__。**保留硬骨头**：`share/proj/proj.db`（缺了所有坐标转换都挂）。2301MB → **1425MB**，算法数仍是 440、provider 仍是 9 |
| 13:16:16 | 主会话 | `pack-release.ps1` | 加 `-Standalone`：把运行时打进包里的 `qgis/` 目录（`run-qgis-python.bat` 找的就是它）；开包核对时多查 6 个运行时关键文件 + 文件数下限 |
| 13:17:11 | 主会话 | `README.md` | 新增「脱离 QGIS 运行（standalone 发行版）」一节：为什么能搬家、裁了什么为什么安全、保留了什么、怎么保证它真的能用 |
| 13:17:31 | 主会话 | `RELEASE_NOTES.md` | 「或者：不用装 QGIS」一节：standalone 版长什么样、启动器自动优先用 `qgis\`、裁剪说明、两个版本的取舍建议 |
| 13:39:36 | 主会话 | `run-qgis-python.bat` | ① **优先用捆绑的运行时**：有 `<包>\qgis\` 就用它，收件人本机不用装 QGIS（显式设了 `QGIS_ROOT` 的一律听用户的）。② **去掉 `o4w_env.bat` 的 `pushd`**：那个脚本用 pushd/popd 包住一切，Windows 偶发抖动会让 pushd 报「The network path was not found.」，批处理目录栈失衡**把整条命令行重跑一遍**（实测有一次一支自检把输出打了两遍）。改成自己做那三件事（`OSGEO4W_ROOT` 取短路径 → 清 PATH → 逐个调 `etc\ini\*.bat`），环境变量与原写法**逐项对照一致** |
| 16:50:14 | 主会话 | `tests/check_standalone.py` · **新建** | 验**发行包本身**：「能打包」不等于「能跑」。把包真的解开、真的起一次服务，钉住：跑的是**包里的**运行时（`sys.executable`/`sys.prefix` 都在包里、`sys.path` 无原安装残留）、网页与静态资源、鉴权、示例数据、AI 接口，最后**用包自带自检**再跑一遍。包没打出来就**跳过**（不算红）。`EXPECTED_CHECKS` 17（服务起不来时第 3 节 10 条会被跳过 —— 汇总数一变就说明「没验」） |
| 16:50:17 | 主会话 | `tests/run_all_checks.ps1` | 回归清单追加 `check_standalone.py`（28 → 29 支） |
| 17:36:40 | 主会话 | `tests/check_standalone.py` | 首版有三个**自己的** bug（不是产品问题），一并记下：① 比对 `sys.executable` 时用了长路径，但 `OSGEO4W_ROOT` 走的是 8.3 **短路径** → 改成 `os.path.realpath()` 展开短名再比；② `subprocess(text=True)` 默认按系统 ANSI（GBK）解码，而 bat 设了 `PYTHONUTF8=1`（输出是 UTF-8）→ 「结果：」几个字变乱码，按它找结果行永远找不到 → 显式 `encoding="utf-8"`；③ **清理放在 `finally` 里，而第 4 节在 `finally` 之后才跑** → 跑到第 4 节时包已经被删了，脚本当然起不来 → 把清理挪到最外层，第 4 节改成「先停服务、保留包、跑完再删」 |
| 18:04:20 | 主会话 | `tests/check_standalone.py` · 收尾 | 补两处**别把服务留成孤儿**：① `terminate()` 的是 cmd.exe，**不会**连带杀掉 python3.exe 子进程（Windows 不级联终止）→ 照 `server_helper.TestServer.stop()` 的两段式来：先 `taskkill /T /F` 杀树，再按端口补刀（端口是自己挑的，上面只可能是自己起的服务）；② 包里的自检脚本起的临时服务还占着解包目录，目录先删会让它们「文件没了但还活着」→ 先等 8871~8895 那段端口空出来再删。实测：补之前卫生检查报 3 个孤儿，补之后 0 个 |
| 18:45:19 | 主会话 | `web/atelier.css` · **新建** | 「制图桌 · 精修层」：只在原样式之上做**加法**（不改结构、不改任何 id），三件事 —— ① **数字升成一等公民**：坐标 / 比例尺 / 要素数 / 面积 / 参数值统一等宽 + 表格数字，前面加一道刻度短线（读数仪器）② **输入区变凹槽**：底色深一线、下边一道更重的线（不用阴影）③ **地图是「有图框的图」**：内外两道细线 + 四角松绿定位十字，控件浮在图框里。另含按钮倒角、表格数字右对齐、标签条收紧、弹窗、焦点可见性、prefers-reduced-motion、窄屏让位 |
| 18:46:30 | 主会话 | `web/ai.js` | AI 助手面板重做成「野外记录本」：页眉（名字 + 当前用哪份配置 + 设置开关）+ 一行一个项的设置页（标签在左、控件在右）+ 记录区（我问的 / 它答的 / 它查了什么）+ 底下落笔的地方。工具调用的参数按「键 值」一行行列开（不再是一坨 JSON），结果收在凹槽里，图片直接显示。空态给一层极淡方格网 + 三个点一下就填入的例句 |
| 18:59:23 | 主会话 | `web/console.js` · `syncBottom()` | **修一个一直存在的遮挡**：把手条原来算的是「状态栏下边沿离视口底还有多远」，而状态栏本来就是 body 里最后一个元素 —— 那个距离恒等于 0，于是把手条正好压在状态栏上，**坐标 / 比例尺 / 坐标系 / 图层数 / QGIS 版本这些读数一个都看不见**；同时它还压掉了地图最下面那行「还没有图层，点左侧 ＋ 添加图层」的提示。改成：① 按**状态栏的高度**让位（`--pc-bottom` = 状态栏高度）② 把手条从通栏改成**跟状态栏同排停靠的一个签**（读数条给它让出左边一段，`--pc-left-pad`）③ 抽屉底边跟着改 |
| 20:36:56 | 主会话 | `pack-release.ps1` | **发行包不再带明文口令**。起因：2-辅助A 把 `super_admin_password` 配进了 `config.json`，而 `config.json` 是**会打进发行包的** —— 再打一次包，等于把那把共享超级口令发给每一个收件人。两处改动：① 包里的 `config.json` **洗过**再放进去（`admin_password` / `super_admin_password` / `api_key` 一律清空 —— 收件人首次启动会拿到**随机**初始口令，写进他自己的 `data\admin_password.txt`，也没有共享超级账号）② 开包核对时**把包里的 config.json 读回来**逐个字段查，发现非空就报错退出（原来只查了 `data/gisds.db` / `admin_password.txt` / `bookmarks.json`，漏了 config.json 这一项）。另：改用**共享读**加文件（`FileShare.ReadWrite`）—— 原来 `CreateEntryFromFile` 用独占方式打开，服务开着（有自检还在写样本数据）就整个包打不出来，现在扛得住 |
| 20:44:56 | 主会话 | `tests/check_web_js_syntax.py` · **新建** | 采纳 3-辅助 的建议，做成常驻自检：① **反引号成对**（纯 Python）—— 模板串没闭合个数必是奇数 ② **真语法校验**（`node --check`，能抓所有语法错，不止反引号）。起因是 `web/admin.js` 那次事故：注释里用反引号包 CSS 选择器 → 模板串提前闭合 → 整个文件不执行 → 管理员页签凭空消失，而页面上**一点迹象都没有**，只有控制台一条 SyntaxError。两个坑都踩过了：① node 装在 `C:\Program Files\nodejs`，但 **QGIS 运行时会把 PATH 整个重置**（`o4w_env.bat` 只留 QGIS 的 bin + 系统目录），`shutil.which` 找不到 → 改成从注册表取**系统真实 PATH** 合并进当前 PATH 再找 ③ `EXPECTED_CHECKS` 公式多算了一条「总数」断言（总数那条本身不计入钉子）。**验证过它真能抓**：故意塞一个坏文件进去，语法校验精确报到 `web\_syntax_probe.js:2`（就是提前闭合的那一行），文件数钉子和总数钉子同时响 |
| 21:26:11 | 主会话 | `tests/server_helper.py` · `_free_port()` / 新增 `kill_tree()` | **修一个会影响所有浏览器自检的坑**（**2-辅助A 报上来的**：它自己的 `check_webui_admin.py` 在回归里崩在第一句 evaluate，报 `ConnectionClosedError`，而单独跑是绿的）。根因：`_free_port()` 是「能不能 bind」来判断空闲的，而 **Windows 上 `SO_REUSEADDR` 允许第二个 socket 绑到一个**已经被别人 LISTEN** 的端口** —— 于是 bind 成功、端口却明明有人在用。代价是「连到了别人的服务」：① **CDP 端口** —— 新起的浏览器绑不上这个端口，而我们按 `/json/list` 连过去连到的是**上一支自检残留的浏览器**（Edge 子进程在父进程被 terminate 后还活着、端口继续被占着），它随时被回收，WebSocket 断在半路 → 症状是「**单独跑绿、回归里崩**」② **服务端口** —— 就绪检查打到别人的临时服务上 → 页面被对方鉴权拦到登录页，看起来像网页坏了。改成**两条都过**：先「连一下」（连得上 = 有人在监听 = 换端口），再「bind 一下」（**去掉 SO_REUSEADDR**，否则这条检查在 Windows 上没有意义）。另加 `kill_tree(pid)`：`taskkill /T /F` 连子进程一起收 —— 只 `terminate()` 父进程的话 Edge 子进程还活着、端口继续被占着，下一支就中招（实测一轮能剩 25 个 msedge 进程） |
| 21:32:46 | 主会话 | `tests/check_webui.py` · `check_webui_files.py` · `check_webui_login.py` · `check_webui_panels.py` · `check_webui_sidefold.py` · `check_ui_paths.py` · `check_webui_settings.py` | 浏览器收尾统一改成 `kill_tree(proc.pid)`（原来 7 处是裸 `proc.terminate()`，Edge 子进程留着、端口继续被占着）。`check_webui_admin.py` 是 2-辅助A 的，他自己已经用等效的 `taskkill /T /F` 写法，没动 |
| 21:32:46 | 主会话 | `tests/run_all_checks.ps1` · 收尾卫生块 | 加「残留无头浏览器」清理：只杀**父进程已经没了**的那些（父进程还活着的属于别的会话正在用，不能动 —— 刚实测 8 个带调试端口的 Edge 里 2 个无主、6 个父进程还活着）。这条让「每轮跑完剩 25 个 msedge」不再积累 |
| 23:13:18 | 主会话 | `pack-release.ps1` · 原子写 | **打包改成原子写**：先写 `<名字>.zip.tmp`，写完才改名成最终名字。起因：回归里 `check_standalone` 报「File is not a zip file」—— 那个包头是合法的 `PK\x03\x04`、但**只有 96.7 MB（应为 525 MB）**，是写一半被打断留下的**截断包**，而打包那一步自己却报了「成功、525 MB」。直接写最终名字时，半成品会**冒充成品**躺在那儿（头是对的、看着像正常包），直到有人拿它解一下才发现坏了。原子改名之后，半成品只会以 `.tmp` 的名字存在，永远冒充不了。顺带修了自己写注释时又踩的坑：**`.ps1` 必须是纯 ASCII**（我第一版注释里写了中文 → 480 个非 ASCII 字节 → PS 5.1 按 ANSI 读会坏掉语法），已全部改成英文并加了「非ASCII字节=0」的检查 |
| 10:23:28 | 2-辅助A | `tests/check_webui_sidefold.py` | **新建**：网页端「侧栏可收起」自检（无头 Edge + CDP，20 项）—— 两个折叠按钮在不在、收起后宽度为 0 / 出现边缘把手 / 地图变宽、另一边不受影响、点把手拉回来且**宽度还原成收起前那个值**、两边可同时收起、**刷新后收起状态还在**（localStorage） |
| 20:03:39 | 2-辅助A | `gisds/core/crs.py` | **新建：坐标参考系目录**。名录走 PROJ 自带的 `proj.db`（SQLite，QGIS 也用它）：按名称 / 编码 / 别名搜 EPSG 全表；细节问 `QgsCoordinateReferenceSystem`（WKT、PROJ 串、椭球、适用范围 `bounds()`）。**注意 QGIS 3.44 没有 `datum()` / `projectionMethod()` / `datumDescription()`** —— 基准和投影方法只能从 WKT 里抠。另外 `crs_preview_png()` 画「适用范围」预览图：陆地取内置底图的 land 图层，范围框铺底色；**范围自己算，不要用 `render.render_png`** —— 它会把传入范围和图层自身范围取并集，而陆地图层是全球，视图就被撑成全球了 |
| 10:25:22 | 2-辅助A | `web/sidefold.js` | **新建**：左右侧栏可收起（QGIS 停靠窗那样）。面板头部加折叠小箭头；收起后屏幕对应边缘留一条窄把手（点它拉回来）；两边互不影响；收起状态记在 localStorage；快捷键 `[` / `]`。收起时宽度置 0（`--w-left` / `--w-right`）并 `map.invalidateSize()`，否则瓦片错位。**把手 z-index 要 1200**：Leaflet 的控件是 1000、瓦片面板 400~700，用默认的 70 会被地图盖住（把手看不见也点不着） |
| 10:25:57 | 2-辅助A | `gisds/api/app.py` | 挂 `crs_router`（`/api/crs/*`） |
| 18:45:19 | 2-辅助A | `web/index.html` | 挂 `sidefold.js` / `settings.js` 两行（`files.js` 那行早先已挂） |
| 18:51:59 | 2-辅助A | `launcher-files.bat` · 探针 + 消息 | **修两个真问题**：① 消息里 `python.org (with the tkinter option)` 的**右括号把 `if not defined PY (` 那个块提前关掉了** → 后面三行**无条件执行**，脚本**每次都在启动 GUI 之前就 `exit /b 1`**，而且报的还是误导人的「没找到带 tkinter 的 Python」（用户截图里那行正好少个右括号，就是这个痕迹）。改成不带括号的措辞。② 探针改为**先找已知安装路径**（`%LOCALAPPDATA%\Programs\Python\Python3*`、`%ProgramFiles%\Python3*`），并**显式排除 QGIS 自带的 Python**（路径含 qgis/osgeo —— 它**没有 tkinter**）；只靠 `py -3` 不行：py 启动器可能选中 QGIS 的 3.12，而 QGIS 运行时里 PATH 又被换过、`python`/`python3` 根本不在 |
| 18:52:01 | 2-辅助A | `launcher.bat` · 探针 + 消息 | 同上（两个文件用的是同一段探针，一起改） |
| 18:59:01 | 2-辅助A | `tests/check_webui_settings.py` | **新建**：网页端「项目属性」自检（无头 Edge + CDP，26 项）—— 左侧 15 个分区、默认落在坐标参考系页、过滤器 / 无 CRS 复选框 / 预定义表、点行后详情（名称 / WKT / 属性表）、**适用范围预览图真的加载出来**（naturalWidth>0）、按名字能搜到服务端全量 EPSG 目录、没接入的分区明说「还没接入」、文件菜单里有「项目属性…」 |
| 19:05:41 | 2-辅助A | `web/settings.js` | **新建：项目属性对话框**（照 QGIS 桌面版做的：左边分区导航 + 右边内容 + 底部详情）。坐标参考系页是真能用的：搜索、预定义清单、最近使用、属性 + WKT + PROJ、**适用范围预览图**、复制 WKT/PROJ。其余分区写明「还没接入」。**预览图要带令牌取 blob** —— `<img src>` 带不了 Authorization 头，而这个接口是要登录的 |
| 19:07:52 | 2-辅助A | `tests/run_all_checks.ps1` | 清单追加 `check_webui_settings.py` |
| 19:56:39 | 2-辅助A | `gisds/core/auth.py` · `ensure_super_admin()` | **新建**：内置共享超级账号的对齐方法 —— 账号不存在就建、角色不是管理员就升、口令跟配置对不上就重设；返回 `(账号, 口令有没有被动过)`，调用方据此决定要不要同步口令备忘。**跟 `ensure_admin` 的区别是幂等且强制**：`ensure_admin` 只在库为空时创建一次，这个每次启动都跑 |
| 19:58:12 | 2-辅助A | `config.json` |  `super_admin_password: "***"`（内置共享超级账号，**所有用户都能用的那把万能钥匙**；发布前已清空，见「开源整理」一节）+ `console_access: "user"`（所有登录用户都能用 Python 控制台） |
| 20:03:04 | 2-辅助A | `web/admin.js` | **新建**：管理员分区（右栏「管理员」页签，里面 用户 / 空间 / 审计 / 配置 四块）。**只有管理员登录后才出现** —— 页面加载时问一次 `/api/auth/me`，`is_admin` 为 false 就不建 DOM（不是「点了报错」，是压根不渲染）；后端 `/api/admin/*` 每一路都单独要求管理员，前端藏起来只是体面 |
| 20:04:14 | 2-辅助A | `web/index.html` | 末尾加一行 `<script src="/static/admin.js"></script>` |
| 20:05:10 | 2-辅助A | `web/login.html` | 登录页加「共享超级账号」块（服务端配了才显示；带「填入表单」按钮 + 风险说明） |
| 20:08:28 | 2-辅助A | `gisds/api/routes_auth.py` · `/api/auth/config` | 配了内置超级账号时把凭据一并告诉登录页（「所有用户都能用」这句话得让人在登录页就看得见）；没配就一个字节都不露 |
| 20:08:30 | 2-辅助A | `gisds/api/routes_admin.py` · `/api/admin/config` | 补 `super_admin_enabled` / `super_admin_username` / `console_access` 三个字段（前端要拿它们把「这台机器开着什么」讲清楚） |
| 20:09:11 | 2-辅助A | `gisds/api/app.py` · 自举 | ① 启动时对齐内置超级账号（口令**变了**就把口令备忘同步更新 —— 不变式：文件里记的永远是当前有效口令）；② 自举时的初始口令改成「超级口令优先」，免得先生成一个随机口令、启动日志和备忘里留下一串从没生效过的死口令 |
| 20:09:23 | 2-辅助A | `gisds/config.py` | 新增 `super_admin_password`（+ 环境变量 `GISDS_SUPER_ADMIN_PASSWORD`）与两个属性 `super_admin_password_resolved` / `super_admin_enabled`；**写成 off/none/disabled 视为没配** —— 环境变量传空串是传不进去的（空值会被当成没设），要真关掉得有句「关掉」的话 |
| 20:09:35 | 2-辅助A | `tests/server_helper.py` · `TestServer.env()` | **临时服务一律不带内置共享超级账号**（`GISDS_SUPER_ADMIN_PASSWORD=off`）。跟「自检不该打开生产库」同一条道理：临时实例不该继承生产的凭据 —— 临时服务是**继承生产 config.json** 起来的，顺着继承下来，临时库的 admin 口令会被改成那把万能钥匙，自检里「用自己的口令登录」直接 401（还很难看出是被谁改的）；那把钥匙的明文还会被抄进临时库 |
| 20:11:09 | 2-辅助A | `tests/check_standalone.py` | 起服务时加 `GISDS_SUPER_ADMIN_PASSWORD=off`：发行包里的 config.json 带着生产那把钥匙，继承下来会改掉 admin 口令，害得「管理员能登录」那条假红 |
| 20:13:23 | 2-辅助A | `tests/check_admin_section.py` | **新建**：管理员分区 + 共享超级账号 + 全员控制台自检（43 项）—— 配置 6 + 超级账号与管理员接口 16 + 普通用户被挡 8 + 全员可用控制台 7 + **重启后口令被钉回去、备忘跟着同步** 6 |
| 20:16:29 | 2-辅助A | `tests/check_webui_admin.py` | **新建**：管理员分区 UI 自检（24 项，无头 Edge + CDP）。**错误收集改成真的**：以前那种「页面里有个 `window.__errs` 就查它」是**空过** —— 网页里压根没定义过这个变量，表达式恒为 `[]`，断言永远绿。改用 `Page.addScriptToEvaluateOnNewDocument` 在**任何页面脚本之前**装监听，连加载期的报错也收得到 |
| 20:17:34 | 2-辅助A | `tests/run_all_checks.ps1` | 清单追加 `check_admin_section.py` / `check_webui_admin.py` |
| 20:19:42 | 2-辅助A | `README.md` | 补「管理员分区」「共享超级账号」「全员控制台」三节 + `admin_password` 与 `super_admin_password` 的对照表 + 风险提示；配置清单补 `super_admin_password`，`console_access` 改成 `user` |
| 20:30:28 | 2-辅助A | `web/help.html` | 补一节「管理员分区与共享账号」（新 `s11`，原来「自己跑一遍自检」顺延成 `s12`，目录锚点同步）：共享超级账号是什么、为什么危险、怎么收紧；管理员分区四块各干什么。另改三处措辞：「右栏九个面板」补上管理员页（普通用户看不到）、「Python 控制台」从「默认只对管理员」改成「本机这套默认对所有登录用户开放」、「忘了管理员口令」补共享账号这条 |
| 20:43:08 | 2-辅助A | `web/admin.js` · CSS | **修「管理员面板一直占着右栏」**：原来写的是 `#paneAdmin{display:flex}` —— **ID 选择器优先级（1,0,0）压过了 `.tabpane{display:none}`（0,1,0）**，于是这个面板不管切到哪个页签都一直显示。改成两条：`#paneAdmin{display:none;...}` + `#paneAdmin.on{display:flex}`。⚠️ **这一版同时把整个文件弄成了语法错误**：那段新注释里用**反引号**包了 CSS 选择器，而反引号在 CSS 模板字符串里会**提前闭合模板串** → admin.js 整个不执行 → 管理员页签永远不出现（控制台只有一条 SyntaxError，页面上什么现象都没有）。**3-辅助 在同一分钟发现并修好了**（把注释里的反引号换成中文引号）。教训：**模板字符串里连注释都不许出现反引号**，而且这种错**只能靠机器数、不能靠眼睛看** —— 主会话已据此新建 `tests/check_web_js_syntax.py`（反引号成对 + `node --check` 真语法校验） |
| 20:53:24 | 2-辅助A | `tests/check_webui_admin.py` | ① 补 3 条断言钉上面那个 CSS 坑（面板默认收着 / 切走收回去 / 切回来又开）；② **修「回归里偶发崩」**：CDP 端口原本用 `_free_port(9373)`，跟 `check_webui_sidefold.py` **撞车**；而 `_free_port` 是「能不能 bind」判断的，**Windows 上 `SO_REUSEADDR` 允许第二个 socket 绑到别人已经 LISTEN 的端口** → 探测无效，端口明明有人监听却说它空着 → 新浏览器绑不上 → 我们连到**上一支残留的浏览器** → 它被回收时 WebSocket 断在半路（`ConnectionClosedError: no close frame received or sent`）。改用**「连一下」**判断真空（连得上=有人监听）、端口挪到不撞车的 9389~9409、起浏览器失败自动换端口重试（重试只在**连接阶段**，不重复跑断言）、收尾用 `taskkill /T` **连浏览器子进程一起收**（只 terminate 父进程的话 Edge 子进程还活着、调试端口继续被占着） |
| 22:55:06 | 2-辅助A | `README.md` · 管理员分区那节 | 补「这台机器开着什么、怎么收紧」一张表：把 `host=0.0.0.0` / `super_admin_password` / `console_access=user` / `cors_origins=["*"]` 四条逐条列出**本机值、意味着什么、以及一行就能关掉的改法**。并说明**代码里的默认值是收紧的**（console_access 默认 admin、super_admin_password 默认空）—— `config.json` 才是「本机部署」的选择，别人拿去用忘了改，拿到的是收紧状态 |
| 23:01:37 | 2-辅助A | `tests/_shot_admin.py` | **新建**：给管理员分区拍照的小工具（无头 Edge + CDP）。**不是自检脚本**，不进回归清单。沿用「连一下」判端口 + 失败换端口重试 —— 浏览器在导航后偶发退出（第一轮实测要重试一次才成），没有重试就十有八九拍不成 |
| 23:02:53 | 2-辅助A | `web/admin.js` · 版面 | **修「窄右栏里表格挤变形」**（看截图才发现的）：徽标「管理员」被压成竖排一列、操作按钮折成三行、最该点的「操作」列还被横向滚动条推出屏幕。改法：表格给 min-width + 横向滚动、徽标 nowrap、日期缩成「月-日 时:分」、按钮文案缩短（「降为普通用户」→「降为用户」）、**「操作」列 sticky 钉在右边** —— 最该点得到的一列永远够得着 |
| 23:10:26 | 2-辅助A | `tests/check_server_helper.py` · 新增一节「正在监听的端口不能被挑走」 | **补上这次根因的钉子**（3-辅助 23:0x 问的）：原来这份自检只钉了「正式端口 8848 不给」和「补刀只补监听者」，**没钉「端口有人在监听时 `_free_port` 不挑它」** —— 哪天有人把主会话 21:26 那版双探测（连一下 + 不带 SO_REUSEADDR 的 bind）改回单探测，**没有任何东西会红**。新 3 条：① 监听者子进程起来了 ② **实测：带 SO_REUSEADDR 的 bind 能绑上别人正在监听的端口**（把「为什么光靠 bind 不够」在自检里跑出来，不再是口口相传）③ 端口有人在监听时 `_free_port` 不挑它。22 → **25** 项 |
| 23:15:51 | 2-辅助A | `tests/check_webui_admin.py` · `_cdp_port()` | **删掉本地那份端口探测，改用底座的 `_free_port()`**（3-辅助 23:1x 提的「同一件事两个实现」）。我本地那份是 connect-only 的弱版，主会话 21:26 落的双探测（连一下 + 不带 SO_REUSEADDR 的 bind）两个条件都查、必然更强 —— **本地复制品既重复又更差**。端口段挪到 9435 起（`_free_port` 会往上顺延最多 30 个，9402 那支能爬到 9432）。**但重试留着**：底座的 `start(attempts=3)` 重试的是**服务**（临时 REST 服务起来失败换端口），我这份重试的是**浏览器**（CDP 端口上的 Edge 导航后自己退出）—— 两回事，底座那份救不了浏览器 |
| 23:16:02 | 2-辅助A | `tests/_shot_admin.py` | 同上：本地 `_cdp_port()` 改成调用底座的 `_free_port(9435)`。**实测**：改完之后端口不再挑到有人监听的，但浏览器**导航后自己退出**这件事照样发生（第一轮仍要重试一次）—— 正好证明「端口探测」和「浏览器会死」是**两个独立故障**，重试那一层不能省 |
| 20:43:08 | 3-辅助 | `web/admin.js`（**2-辅助A 的文件，我修了它**） | **修「管理员页签永远不出现」**：根因是 `const CSS = \`…\`` 这个**模板字符串里面那段注释用了反引号包 CSS 选择器**（`#paneAdmin` 那种写法）→ 反引号把模板串**提前闭合** → 整个 admin.js 变成语法错误、**完全不执行** → `window.GISDS_ADMIN` 是 `undefined` → 页签从来没被创建过，而浏览器控制台只有一条 SyntaxError、**页面上什么现象都没有**（自检看到的就是"页签列表里没有管理员"）。定位三步（可复用）：① 后端 `/api/auth/me` 实测返回 `is_admin: True` → 后端没问题；② 页面里 `window.GISDS_ADMIN` 是 `undefined` → 脚本没执行；③ **数文件里的反引号：18 个（奇数）→ 模板串没闭合**。修法：把那段注释里的反引号换成中文引号。⚠️ **我改的时候自己又踩了一次**：新写的说明里把示例写成了带反引号的样子，模板串照样被切断 —— 所以改完**必须用脚本数一遍**，不能靠眼睛看。改后：4 个反引号、CSS 模板串 3890 字符完整、HTML 2606 字符完整；`check_webui_admin` **28/28 通过**（改之前 27 条里红 1 条）。顺带扫了 `web/` 下所有 js 的反引号奇偶，**没有同类问题** |
| 22:59:51 | 3-辅助 | `tests/server_helper.py`（21:26 那版双探测是**主会话**落码、坑是 2-辅助A 报的；**我的六道护栏逐处核对，一件没丢**） | **独立实测确认了 Windows 上 SO_REUSEADDR 的行为，并纠正我自己之前的判断**。实测：起一个真监听 → 第二个 socket 带 SO_REUSEADDR **bind 到同一个已被 LISTEN 的端口 → 成功**；再 connect 同端口 → 也成功（确实有人在听）。所以主会话 21:26 把 `_free_port()` 改成**双探测**（先 `_port_in_use()` 连一下、再 bind 且**不带** SO_REUSEADDR）是**根治版**。⚠️ **我 22:15 那条只对了一半**：我把端口竞态的根因说成「两个进程同时挑中同一个空闲端口」，其实更深一层是「**bind 探测在 Windows 上会把已被占用的端口误报成空闲**」—— 后者才是主因，前者只是它的表现。我 22:15 加的「起不来换端口重试」因此退为**第三道兜底**（前两道：双探测挑端口、起不来换端口）。**我 18:24 / 21:50 / 22:15 加的六道护栏逐处核对全在**：`PROD_PORT = 8848`、`_free_port()` 跳过 8848、`TestServer.__init__` 双保险、`stop()` 的端口补刀绝不碰正式端口、用户目录空目录自清理、`_abort()` 补删临时库。跑 `check_server_helper` **23/23**、新增的 `check_web_js_syntax` **18/18**（反引号成对 + node --check）全绿。⚠️ **本行 22:59 初稿把 21:26 那版记成「2-辅助A 改过」—— 是我记错了**：点子是 2-辅助A 报的，代码是主会话落的（OWNERS L291 署名就是主会话），已按磁盘改正 |

> 上面 36-38 行（3-辅助）与 39-41 行（主会话）指的是**同一处的两轮改写**：先由 3-辅助
> 改成「同步写」，主会话 12:23-12:26 又把同一处按最终判据重写了一遍（所以 mtime 只留下
> 后一轮的）。**最终代码只有一份**，就是主会话那三行描述的版本。别看成两版冲突。

> 更早的**归属纠正**（别再记错）：`core/auth.py` 的 `stem`→`name` 修复、
> `server_helper.py` 去掉第二份命名规则（改成 `import core.auth.password_note_path`）——
> 这两处是 **3-辅助** 写的，已补签在上表。
> 而 `tests/pngutil.py`（10:39 建，像素级判据）是 **主会话** 写的，
> `tests/check_password_note.py`（10:52 建）是 **2-辅助A** 写的 —— 两者都**不是** 3-辅助的。
> 归属表里 `core/auth.py` 那一行的「口令备忘」段、`server_helper.py` 那一行的
> 「口令备忘清理」段归 3-辅助 —— 归属表本身**没错**，是记忆错了。

## 会话

| 代号 | 会话 ID | 负责的线 |
| --- | --- | --- |
| **主会话** | `mvs_ad3d12537b904a4bb0ae9f17ab2c6404` | 用户系统、空间模型、鉴权、枚举 i18n、REST 主体、README 收口 |
| **3-辅助** | `mvs_399613e7f1984c4c94f4982950688ebf` | QGIS 面板与工具栏网页化（属性表 / 统计 / 选择 / 书签 / 日志 / 历史） |
| **2-辅助A** | `mvs_69b6bd623e1d40f7872e3e4ebe680378` | 图形启动器、文件浏览器、内置离线底图、软件图标 |

## 独占文件（改了要告诉对应的人）

### 2-辅助A（我）

```
gisds/launcher/app.py
gisds/launcher/api.py
gisds/launcher/files.py
gisds/launcher/theme.py
gisds/launcher/credentials.py
gisds/launcher/__main__.py
gisds/core/basemap.py
gisds/core/crs.py
gisds/api/routes_render.py
gisds/api/routes_crs.py
launcher.bat
launcher-files.bat
web/icon.png
web/favicon.png
assets/icon.ico
web/admin.js
web/files.js
web/sidefold.js
web/settings.js
tests/check_tiles.py
tests/check_launcher.py
tests/check_password_note.py
tests/check_service_key.py
tests/check_admin_section.py
tests/check_webui_admin.py
tests/check_webui_files.py
tests/check_webui_sidefold.py
tests/check_webui_settings.py
```

### 3-辅助

```
gisds/core/panels.py
gisds/core/bookmarks.py
gisds/core/logbuf.py
gisds/core/vector.py
gisds/api/routes_panels.py
run-qgis-python.bat
web/panels.js
tests/check_panels.py
tests/check_mcp_panels.py
tests/check_webui_panels.py
tests/check_webui_login.py
```

### 主会话

```
gisds/core/processing.py
gisds/core/i18n.py
gisds/core/registry.py
gisds/core/storage.py
gisds/core/db.py
gisds/api/deps.py
gisds/api/routes_processing.py
gisds/api/routes_raster.py
gisds/api/routes_vector.py
gisds/api/routes_query.py
gisds/api/routes_meta.py
gisds/api/routes_upload.py
gisds/api/routes_datasets.py
tests/pngutil.py
tests/check_api.py
tests/check_mcp.py
tests/check_mcp_stdio.py
tests/check_live.py
tests/check_auth.py
tests/check_core.py
tests/check_new.py
tests/check_web_merge.py
```

## 共同维护的文件（各管各的一段，改前先说一声）

| 文件 | 主会话管 | 3-辅助管 | 2-辅助A 管 |
| --- | --- | --- | --- |
| `gisds/core/render.py` | 瓦片坐标换算（`tile_bbox_3857` 的 `merc_y`）、`render_png` 的 `bbox_crs` | — | 底图配色与 `render_basemap_tile` |
| `gisds/core/tenancy.py` | 空间模型（`SHARED` / `user_scope` 等） | — | `migrate_layout()`（旧布局并入共享空间） |
| `gisds/core/auth.py` | 密码哈希、会话令牌、`Principal` | `password_note_path()` / `write_password_note()`（口令备忘）、登录失败写审计（`user.login_failed`） | `ensure_super_admin()`（内置共享超级账号的对齐：建号 / 升角色 / 重设口令） |
| `gisds/cli.py` | `cmd_set_password()` 里**改密后同步写备忘**的那段 | `cmd_set_password()` 本身（参数解析、重置流程） | `cmd_basemap()`（内置底图安装子命令） |
| `gisds/api/app.py` | 应用装配、路由挂载主流程；**建号时的口令来源分流**（约 82-121 行：`from_config` 决定打不打明文 / 写不写文件、配置口令过期时告警） | 挂 `panels_router`、lifespan 里 `logbuf.install()` 与就绪标记、口令备忘路径 | 挂 `ai_router`（网页 AI 助手）；**内置超级账号那一段**（启动时对齐口令 + 口令备忘同步）—— 与主会话的「口令来源分流」同处一个分支，改动已签名 |
| `gisds/api/routes_admin.py` | 用户增删改查、审计 | 改密码后**同步写入**口令备忘（受 `password_note` 控制） | `/api/admin/config` 里补的三个字段（`super_admin_enabled` / `super_admin_username` / `console_access`） |
| `gisds/api/routes_auth.py` | 登录 / 注册 / 登出 / `me` | — | `/api/auth/config` 里补的 `shared_account`（登录页那块「共享超级账号」） |
| `gisds/mcp/server.py` | 协议外壳（`build_server()` / `serve_mcp()` / stdio 保护 / 传输告警）；**27 个工具的定义已移到 `core/gis_tools.py`**（MCP 与网页 AI 共用同一份） | —（工具定义不再在本文件里维护） | — |
| `gisds/config.py` | 全部配置项 | — | `_resolve()`（相对路径一律相对 `PROJECT_ROOT`）；`super_admin_password` 那一项与 `super_admin_password_resolved` / `super_admin_enabled` 两个属性 |
| `web/index.html` | 统一收口；`web/panels.js` 挂载 | `web/panels.js` 挂载那一行 | 内置底图（`initBasemap` 等）、favicon、品牌图标；`files.js` / `sidefold.js` / `settings.js` / `admin.js` 各自的挂载那一行 |
| `tests/server_helper.py` | 端口自动顺延、临时库带进程号、确定性回收 | 口令备忘清理、**死文件自愈**（按文件名里的 pid 判活，扫掉被中断运行留下的） | 临时服务**关掉内置共享超级账号**那一行（`GISDS_SUPER_ADMIN_PASSWORD=off`） |
| `tests/pngutil.py` | 像素级判据（`pixels` / `color_clusters` / `has_content`） | — | 只**引用**它，不改它 |
| `tests/check_webui.py` | 其余断言 | 右栏九个面板的断言（那一条） | 只**追加**底图/favicon 断言，不改已有段落 |
| `tests/check_password_note.py` | 「要么没有、要么记着当前有效口令」的不变式断言（后半段） | — | 建文件 + 前半段（命名 / 位置 / 不留错名文件） |
| `tests/check_password_flag.py` | 「改密后备忘同步写入」那几条断言 + 文件头说明 | 建文件 + 口令来源四档口径 | — |
| `tests/run_all_checks.ps1` | 加 `check_web_merge.py` | 加 `check_mcp_panels.py` / `check_panels.py` / `check_webui_panels.py` / `check_webui_login.py` | 加 `check_tiles.py` / `check_password_note.py` / `check_admin_section.py` / `check_webui_admin.py` / `check_webui_files.py` / `check_webui_sidefold.py` / `check_webui_settings.py` |
| `README.md` | 统一收口 | 面板 / 工具栏章节、`/api/panels/*` 速查、MCP 清单 15→27、口令备忘与日志的坑 | 底图 / 启动器 / 图标相关章节（纯追加）、**管理员分区 / 共享超级账号 / 全员控制台三节**（纯追加） |
| `tests/OWNERS.md` | 统一收口（这张表） | 提出「这张表自己也要核」并补充条目 | — |

## 几条约定

1. **改文件前先在群里说一句**。同一轮里两个人改同一个文件会互相覆盖 ——
   已经有三次「A 以为是 B 改的」了。
2. **新增文件先落盘，再改引用它的代码**。别留「用了还没 import」的中间状态给下一个人
   （踩过一次 `NameError`）。
3. **相对路径一律相对 `PROJECT_ROOT`**，不相对当前工作目录。换个目录启动服务
   就会指向另一套数据目录和用户库（踩过一次）。
4. **自检不许碰生产库**。`data/gisds.db` 和 `data/admin_password.txt` 只有正式服务能写；
   自检一律用 `TestServer` 的临时库，路径带进程号、跑完自动回收。
5. **临时库的自测要覆盖生产分支**。`db.stem` 与 `db.name` 的差别、`shared` 与
   `user:*` 的差别 —— 临时库永远走「其它」分支，生产分支得单独写断言钉住
   （`tests/check_password_note.py` 就是干这个的）。
6. **断言要么证明行为，要么别写**（下半句：别让「没报错」冒充「对」）。而且要断言
   **产物本身**对不对，不能只断言状态码或属性：
   * 「返回 200」证明不了图里有内容 —— 一张全白 PNG 同样 200；
   * 「属性里没有 undefined」证明不了拿到的数据对 —— 401 的 JSON 被 `blob()` 一吞
     照样塞进 `imageOverlay`，地图上什么都不显示，而且**不报任何错**；
   * 所以产物要断到「**真的是那个东西**」：PNG 断 `naturalWidth > 0`，
     接口断字段类型/长度一致，矢量断要素数 > 0。
   * 配套一条**自检设计**规则：**只在登录态才暴露的路径，自检必须在登录态的服务上跑**。
     免登录的临时服务里 401 根本不会发生 —— 那类路径会被系统性地漏测，
     本次「看不到导入的图像」就是这样漏掉的（所有 UI 自检都跑在免登录服务上）。
   * 前置步骤失败时（读不到控件文字、拿不到要断言的对象、收集到的是空值），
     要**把失败显式断言出来**，不能 `except: pass` 之后拿一个空值继续判 ——
     那会让断言**空过**：什么都没验，却显示 PASS。
   （`scan_undefined` 抓的是**渲染后的属性**级问题；产物级问题只能靠内容断言，
   两者互补，不重叠。）
7. **脚本里不许硬编码项目绝对路径**。一律 `PROJECT_ROOT = Path(__file__).resolve().parent.parent`
   再 `sys.path.insert(0, str(PROJECT_ROOT))`。写死了 `D:\...` 的话，整个项目挪个位置
   就不是报错，而是**静默检查了别的目录还报 PASS**（踩过一次）。
   同一个道理：**项目根目录只能算一次、从同一处 import**，别让两个模块各算各的。
   启动器里曾经自己算成 `gisds/`（少退了一级），于是 `run-qgis-python.bat` 找不到 ——
   **「启动服务」按钮从来没起过服务**，`logs/` 也建到了 `gisds/logs`。
   而自检全是外部起好服务再连，启动器自己的启动路径从没被走过，所以一直没发现。
   凡是「靠某条路径找到某个东西」的代码，都要有一条断言钉住那个东西找得到。
8. **临时服务的端口要动态挑空闲的**。写死端口时两个会话并行跑，后一个的健康检查会
   连到前一个的服务上 —— 表现是「页面被登录页拦掉、断言莫名失败」，比直接起不来更难查。
9. **失败要留痕**。自检失败时把服务日志留在 `logs/testserver_<port>.log`，
   端口被谁占了、页面为什么跳登录页，日志里一眼能看出来。
10. **改了路径相关的逻辑，要从别的目录再跑一遍**。`run-qgis-python.bat` 不改 cwd，
   所以换个 cwd 跑一次就是最省事的路径无关性验证 ——
   在 `C:\Windows\Temp` 里跑和项目根里跑，结果应该一样。
11. **这张表自己也要核**。它已经写错过一次（把 3-辅助 建的 `check_webui_panels.py`、
   `check_webui_login.py` 和口令备忘那几段记成了别人的）。
   记不准就写「这一段归谁」而不是「整个文件归谁」，颗粒度越细越不容易错。
12. **一个功能被两个人各做一半时，测试要钉住合并后的整体语义**。口令备忘就是这样：
   3-辅助 做「写在哪、叫什么、什么时候写」，主会话 做「写不写、打不打明文、
   配置口令过期时告警、改密之后怎么跟」—— 任何一半单独看都合理，合起来才有
   4 种组合（config / 随机 × 开关开 / 关）。`tests/check_password_flag.py`
   按合并后的语义写，所以**谁都别单独改其中一半**。
13. **口令备忘的生命周期由一条判据决定：当前有效口令还有没有别的明文副本**。
   * 自举那一刻配置里已经写着口令 → 有副本 → **不写**（连日志也不打明文）；
   * 自举那一刻没人配、服务随机生成 → 没有副本 → **要写**（日志里也打，多机部署就靠这两处）；
   * 改密之后配置那份已过期 → 备忘成了当前口令唯一的明文副本 → **同步更新成新口令**。
   不变式只有一条：**文件里记着的永远是当前有效口令**，绝不躺着一个失效的旧口令。
   整个落盘行为由 `password_note` 开关控制（config.json，默认 true；
   环境变量 `GISDS_PASSWORD_NOTE` 可覆盖）—— 机器要接到不可信网络时用 `false` 一关了之。
14. **「凭据类 UI 的销毁」必须幂等，且逐个控件独立失败**。启动器收起初始口令
   提示条时，要**先把引用清掉、再逐个销毁控件**：任何一个控件抛异常（控件已被父
   容器连带销毁时就会）都不能让「明文口令还挂在屏幕上」这种状态留下来 ——
   那个模块存在的意义就是不让它挂着。断言也要看**可观察的结果**（界面上还有没有
   那个口令），别只盯内部引用。
   附带一条排查经验：**自检偶发失败时，先怀疑「是不是和别的会话并行跑」** ——
   端口、临时库、CPU 都会被抢；同一份代码单跑通过、并发跑偶发失败，多半不是
   逻辑错，而是**销毁/收尾那条路径不够稳**（把它改成幂等，偶发就消失了）。
15. **Tk 只能在主线程动**。启动器的服务日志是从子进程 stdout 读的，读它的线程
   是后台线程；那些行最终要 insert 进日志控件、要建口令提示条 —— 全是 Tk 调用，
   而 Tk **不是线程安全的**：后台线程动控件，轻则偶发地不生效（提示条时有时无、
   口令标签没销毁），重则整个程序崩掉（0xC0000005）。
   约定：**后台线程只负责收行进队列，界面更新由主线程取出来做**
   （`Service._pump` → `Service.drain()` → `Launcher._tick`）。
   这类问题**隔离环境复现不了**，只有机器忙起来（并行跑全套自检）才偶发。
16. **异常不要裸吞**。`except Exception: pass` 会把「程序其实没跑成」变成
    「界面看着正常、一切都没发生」—— 比直接报错难查得多。会话 2 那次就是：
    `_show_first_credentials()` 里用了 `ttk.Frame` 但那个方法里没有 `ttk` 这个
    局部名（是 `self.ttk`）→ NameError → 被 `except Exception: pass` 吞掉 →
    提示条静默不出现。改成把异常打进日志就好了。
    推论：**自检里「没报错」不等于「跑到了那一行」** —— 断言要钉住行为，
    不要靠「没抛异常」推断（第 6 条的下半句）。
17. **Tk 根窗口要留强引用；后台线程不许把它拖住**。Tk 对象几乎总在引用环里
    （root ↔ Tk 回调 ↔ 持有它的对象），循环 GC 随时可能把它收走 —— 而 GC 是
    **哪个线程分配得够多就在哪个线程跑**。要是正好在后台线程里被回收，
    Tcl 解释器就会在**错误的线程**里被删除：
    `Tcl_AsyncDelete: async handler deleted by the wrong thread` → 进程 abort
    （退出码 0x80000003，什么都不打印）。
    两条约定：
    * 建过的根窗口登记进 `launcher/tkguard.keep_root()`，永远不被循环 GC 回收；
      窗口该关时照常 `destroy()`（那会把 Tcl 侧清干净）。
    * **后台线程不许拿到任何能引用到 Tk 对象的链**。就绪探测只往一个普通
      `queue.Queue` 里塞结果（`Service._probe_worker`），不传 `self` ——
      传了就会经 `on_log` 一路拖到 Launcher 和 Tk 窗口，线程结束时在
      **后台线程**里析构 Tcl 解释器。
    附：本项目已经吃过两次「静默失败比报错难查」—— 一次是这个 `except: pass`，
    一次是 `password_note_path` 把 `db.stem` 写成 `"gisds.db"`
    （`Path("gisds.db").stem` 是 `"gisds"`，拿它比永远不成立）：备忘被写到另一个
    文件名，**没有任何一行报错**，自检还是绿的。后者现在由
    `tests/check_password_note.py` 钉住（第 5 条），端口规则也只有一处实现
    （`server_helper._free_port`，第 8 条）—— 比各写各的强。
    落地情况：`gisds/launcher/` 下的裸吞已清零（`api.py` 配置解析 / `files.py`
    图标 / `theme.py` 主题，三处都改成带原因的打印）；`core/` 里还剩 33 处，
    多是「取不到就当没有」的尽力而为路径，不涉及凭据与安全，按「碰到就改」推进。

## 口径变更记录（读旧上下文的人先看这里）

会话消息有**严重时延**：本项目已经连着三条消息按**已被推翻的口径**要求改文件，
其中一条还带着「我改了 core/auth.py 的 docstring」的说法，而磁盘上根本没有那次改动。
**判断自己读到的是不是过期口径，只看这张表：**

| 口径 | 状态 | 时间 | 怎么一眼认出来 |
| --- | --- | --- | --- |
| 同步更新（统一判据） | **当前有效** | 2026-10-21 12:26 | `core/auth.py` 里**没有** `delete_password_note`；`routes_admin.py` / `cli.py` 调 `write_password_note` |
| `admin_password` 是「一次性初始口令」，`super_admin_password` 是「每次启动都对齐的口令」 | **当前有效** | 2026-10-22 20:09 | `gisds/config.py` 里两个字段各带一段注释；两者配的是**同一个账号**（`admin_username`），**只能设一个**，都设了以 `super_admin_password` 为准；后者写成 `off/none/disabled` 视为没配 |
| 过期即删（改密即删备忘） | **已废弃** | 2026-10-21 12:23 前 | 出现 `delete_password_note(` 就是旧口径 |
| 同步写入（最早的版本） | **已废弃** | 2026-10-21 11:40 前 | 只有「自举写、改密不写」两档，没有统一判据 |
| 「`_free_port()` 挑中了 8848」是正式服务消失的根因 | **已废弃** | 2026-10-21 18:51 | 端口实参的最小值是 **8851**（`tests/` 下全部硬编码端口：8851/8852/8854/8855/8857/8858/8864/8865/8871/8873/8877/8879/8891/9333/9364/9365/9371），唯一的非硬编码（`check_password_flag.py` 的 `free_port()`）用 `socket.bind(("127.0.0.1", 0))` 拿 OS 动态端口（Windows 49152+）—— **临时服务从不可能占用 8848**。真凶是主会话 13:59 加的 `_descendant_pids()` **按 pid 血缘**杀（pid 会被系统复用） |
| 端口补刀这条线「已经被事故验证过」 | **已废弃** | 2026-10-21 18:51 | 事故当时端口这条线**根本没被触发过**（挑不到 8848）。它是对的，但靠的是**推理**（端口是我们自己挑的、唯一可靠凭据），不是靠那次事故 —— 而且它**自己也有一个没验过的坑**：匹配太松，会把「连到这个端口的客户端」一起收进补刀名单（已于 18:58 修掉，并由 `check_server_helper.py` 钉住） |

**断言总数怎么数**（3-辅助 18:51:10 提醒）：数日志里的 **`[PASS]` 行**，
不要靠 grep「结果：N 通过」—— 那两支用系统 Python 跑的 tkinter 脚本按 GBK 写 stdout，
日志按 UTF-8 读是乱码，那行根本匹配不上（会少算 91 项）。现在
`run_all_checks.ps1` 已在调系统 Python 前设 `$env:PYTHONIOENCODING='utf-8'`。

**核验命令**（比读上下文可靠）：
```powershell
Select-String -Path gisds\core\auth.py,gisds\cli.py,gisds\api\routes_admin.py `
  -Pattern 'delete_password_note'   # 命中 0 = 当前是统一判据
Get-Item gisds\core\auth.py,README.md,tests\OWNERS.md | Select-Object Name,LastWriteTime
```
**发现口径不一致时的做法**：以磁盘为准，**不要**按消息改文件；把差异连同
`Get-Item` 的 mtime 一起发出来，由改动方确认。

