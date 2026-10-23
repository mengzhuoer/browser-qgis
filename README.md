# Browser QGIS

把 **QGIS（PyQGIS）内核搬到网页上** 的一台通用 GIS 工作台。

拖一个 GeoTIFF / GeoJSON 进网页，它立刻变成可叠加、可渲染、可查询、可分析的图层；
内置的 **QGIS Processing 工具箱（440 个算法）** 直接在网页里跑；
同一份内核同时对外提供 **REST API**（给别的网页当数据源）和 **MCP Server**（给 OpenClaw 等 Agent 当工具）。

一句话：**桌面版 QGIS 能做的，这里基本都能做；只是换成浏览器打开。**

---

## 目录

- [快速开始](#快速开始)
- [图形启动器](#图形启动器)
- [用户与数据空间](#用户与数据空间)
- [网页控制台](#网页控制台)
- [Python 控制台与插件](#python-控制台与插件)
- [REST API 速查](#rest-api-速查)
- [MCP Server（接入 OpenClaw）](#mcp-server接入-openclaw)
- [网页里的 AI 助手](#网页里的-ai-助手)
- [打包发行版](#打包发行版)
- [脱离 QGIS 运行（standalone 发行版）](#脱离-qgis-运行standalone-发行版)
- [目录结构与数据约定](#目录结构与数据约定)
- [改名说明（产品名变了，契约没变）](#改名说明产品名变了契约没变)
- [配置项](#配置项)
- [自检脚本](#自检脚本)
- [测试与结论](docs/测试与结论.md)
- [已知坑与注意事项](#已知坑与注意事项)
- [许可与图标来源](#许可与图标来源)

---

## 快速开始

> **从 GitHub 拿到的代码长这样**：`config.json` **不在版本库里**（里面可能有明文口令），
> 模板是 `config.example.json`。没有 `config.json` 也能直接起 ——
> 服务用代码里的默认值，那一档是**收紧的**（控制台只有管理员能用、没有共享超级账号）。
> 想改配置就 `copy config.example.json config.json` 再改。

### 1. 依赖

需要本机装有 **QGIS 3.44.14**（本项目按 `C:\Program Files\QGIS 3.44.14` 写死，可在
`run-qgis-python.bat` 里用 `QGIS_ROOT` 环境变量覆盖）。

Web 层依赖（FastAPI / uvicorn / MCP SDK 等）装在项目自己的 `deps\` 目录里，
**不污染 QGIS 自带的 Python**。首次使用执行一次：

```powershell
cd <本项目解压后的目录>
.\setup.ps1
```

### 2. 启动服务

```powershell
# 双击 start.bat 也可以
.\start.bat
```

或者直接用底层启动器（调试时更方便）：

```powershell
.\run-qgis-python.bat -m gisds serve
```

启动后：

| 入口 | 地址 |
| --- | --- |
| 网页控制台 | http://127.0.0.1:8848/ |
| 接入示例页 | http://127.0.0.1:8848/demo |
| 接口文档（Swagger） | http://127.0.0.1:8848/docs |
| 健康检查 | http://127.0.0.1:8848/healthz（存活）/ http://127.0.0.1:8848/readyz（就绪） |

监听 `0.0.0.0:8848`，**局域网内其它机器可以直接用本机 IP 访问**（例如
`http://192.168.1.20:8848/`），前提是 Windows 防火墙放行 8848 端口。

**默认要登录**。第一次启动会自动建一个管理员 `admin`，随机密码写在
`data/admin_password.txt`（同时打印在服务日志里）。重置密码：

```powershell
.\run-qgis-python.bat -m gisds set-password admin
```

也可以直接用图形启动器，见下一节。

### 3. 塞点示例数据（可选）

```powershell
.\run-qgis-python.bat -m gisds demo-data
```

会把 `tests\sample_data\shared\example`（一张分区栅格、一张连续曲面、一个范围面）
复制到 `data\shared\` 下（共享空间），方便第一次打开时就有东西看。

---

## 图形启动器

```
双击 launcher.bat
```

一个 tkinter 窗口把整套服务管起来：

| 区域 | 能做什么 |
| --- | --- |
| 顶栏 | 启停 REST 服务、打开网页控制台、管理员登录、**服务密钥登录** |
| 用户页 | 建号、改密码、启用/停用、改角色、删除；显示每个账号的个人空间 |
| 文件页 | 浏览/上传/下载/删除所有空间里的数据集，双击在资源管理器里打开 |
| 日志页 | 实时滚动服务日志 |

**文件管理可以单独开一个窗口**：

```
双击 launcher-files.bat
```

它和启动器里的「文件」页是同一套代码，但独立成窗（顶栏还有「文件浏览器」按钮，
开出来的窗口与主窗口共享登录态）。单独双击 `launcher-files.bat` 打开时没有登录态，
窗口会**主动弹出登录框** —— 而不是给你一张空表让你以为「文件没了」。

| 列 | 是什么 |
| --- | --- |
| 数据集 | 数据集标识（就是上传时生成的那个 id） |
| 空间 | 共享空间 / 个人空间 |
| **文件** | **这个数据集里到底有哪些文件**（上传的原文件都在这儿） |
| 图层 | QGIS 认出来的图层数（栅格/矢量） |
| **状态** | **已识别 / 未识别** —— 「未识别」= 文件在磁盘上，但 QGIS 打不开（比如只传了 `.shp`、缺 `.dbf/.shx/.prj` 配套件） |
| 目录 | 在磁盘上的位置，双击直接用资源管理器打开 |

空间下拉第一项是「**全部**」（默认选中）—— 你上传的东西不一定落在共享空间，
可能传到个人空间去了，只列共享空间就会出现「我传的图不见了」。
「全部」只是个显示过滤器；真要上传时选的是它，文件会落到共享空间。

列表读的是**工作区**（`/api/uploads`，= 磁盘上真实存在的东西）而不是**注册中心**
（`/api/datasets`，= 只含 QGIS 认得出来的图层）—— 所以「传了但 QGIS 打不开」的
文件也在列表里，而且能删（它以前既不显示、也删不掉）。

**GUI 本身只依赖标准库**（tkinter + urllib），用系统 Python 跑；服务端仍然用
QGIS 的 Python 跑（`run-qgis-python.bat -m gisds serve`）。

> QGIS 自带的 Python **没有打包 tkinter**，所以启动器不能用它跑。
> `launcher.bat` 会自动找带 tkinter 的 `py -3` / `python` / `python3`；
> 找不到会提示装 Python 3（python.org 版带 tkinter）。

需要 tkinter 的页面（用户管理、文件管理）要先在顶栏「登录」。

**首次启动不用去翻日志**：服务自举时会把管理员初始口令打进启动日志，而启动器自己
起服务、日志就在它手里 —— 所以它会把口令直接捞出来，在「用户」页顶部显示一条
提示（**复制** / **直接登录** / **收起**）。点「直接登录」就进去了。

**这条提示条会自己过期**。它显示的是自举那一刻的初始口令，用户在别处改过口令
之后，它就成了「失效旧口令」，还配一个「直接登录」按钮 —— 点了只会 401，
跟服务端那个「文件在骗人」是同一类故障。所以启动器守着同一个原则：
**任何显示凭据的地方都不能留着过期的**。判据直接复用服务端那份口令备忘 ——
服务端始终让它记着**当前有效**的口令（改密时同步更新），所以
「备忘还记不记着这个口令」就是权威信号：

* 备忘不再记着这个口令 → 立刻收起，换成一句「口令已变更，请用新口令登录」；
* 点「直接登录」被服务端拒了（401）→ 同样收起，并说明口令已变更；
* 一键登录成功 → 收起来；
* 一直没用过 → 15 分钟后收起（凭据在屏幕上放太久本身就是风险）。

留着这条提示条的时候它会显示生成时间，让你知道它有多老。

**多机部署下忘了 admin 密码怎么办**：顶栏「服务密钥」按钮可以填服务端
`config.json` 里的 `api_key` 登录 —— 服务端把带这个密钥的请求按管理员算，
所以它能在**另一台机器上**重置任意账号的口令（密钥本身要用安全渠道送到管理员手上）。
没有这条路的话，管理员人在远程就只能到数据机上跑
`gisds set-password`。

---

## 用户与数据空间

**两个空间：**

| 空间 | 谁能看 | 谁能写 | 磁盘位置 |
| --- | --- | --- | --- |
| 共享空间 `shared` | 所有登录用户 | **所有登录用户** | `data/shared/<数据集>/` |
| 个人空间 `user:<用户ID>` | 只有本人 | 只有本人 | `data/users/<用户ID>/<数据集>/` |

管理员能看到所有空间。免登录模式（`require_login=false`）下所有人都只有共享空间。

**账号：**

- 密码用 **PBKDF2-HMAC-SHA256**（12 万轮，每用户独立随机盐），库里只存摘要
- 会话令牌是 32 字节随机串，库里只存它的 SHA-256
- 角色：`admin` / `user`；状态：`active` / `disabled`（停用即刻踢下线）
- **内置共享超级账号**（**默认关闭**）：在 `config.json` 的
  `super_admin_password` 里填一个口令，就等于给这个账号装了一把
  **每次启动都对齐**的万能钥匙 —— 「所有用户都能用」是字面意思：
  同网段任何人拿这组口令登录都是管理员。
  登录页上会有一块「共享超级账号」直接写着它，还带一句风险说明。
  **这是设计上的后门**，仓库里默认留空（不开）；要开就自己填一个，
  要关就清空或写成 `off`。
  细节见下面「配置项」一节里它跟 `admin_password` 的对照表。
- 账号来源由 `config.json` 的 `allow_registration` 控制：
  `false` = 只能由管理员在启动器里开号（默认）；`true` = 打开网页可自助注册
- **登录失败也会写审计**（`user.login_failed` / `user.login_disabled`），
  「登不上去」时可以在启动器的审计页看到到底试了几次、是不是被停用了
- 管理员当前口令写在 `data/admin_password.txt`（忘了密码的兜底）。
  判断要不要写它只有一条依据：**当前有效口令还有没有别的明文副本**。
  自举那一刻若配置（`config.json` / `GISDS_ADMIN_PASSWORD`）里已经写着口令，
  明文本来就在配置里，就**不写、连日志也不打**；若没人配、服务随机生成，
  那台机器上只有这个文件和启动日志两处能找到它，**两处都要留**（多机部署就靠它们）。
  改密之后（改密 API、`set-password` 两条路径）config 那份已经过期作废，
  备忘成了当前口令唯一的明文副本 → **同步更新**成新口令。
  不变式：**文件里记着的永远是当前有效口令**，绝不会躺着一个失效的旧口令
  让人照着登一直 401。
  机器要接到不可信网络时，把 `password_note` 设成 `false` 一关了之。
- 备忘文件名由 `core.auth.password_note_path()` 决定：库文件叫 `gisds.db` 时，
  备忘叫 `admin_password.txt`（就是上面那个路径）；库文件换了名字，备忘跟着改成
  `<库名>_admin_password.txt`（**仍然在库旁边**，不是数据根目录）。
  自检的临时库属于后者 —— 几个临时服务并行跑时文件名互不覆盖，清理也删得准。
  启动日志会打印这份备忘的绝对路径。

**接口：**

```
POST   /api/auth/register     注册（受 allow_registration 控制）
POST   /api/auth/login        登录，返回令牌
POST   /api/auth/logout       登出
GET    /api/auth/me           当前用户与可见空间
GET    /api/auth/config       登录页需要的开关
GET    /api/admin/users       全部账号（管理员）
POST   /api/admin/users       建号（管理员）
PATCH  /api/admin/users/{id}  改密码/角色/状态（管理员）
DELETE /api/admin/users/{id}  删号（管理员）
GET    /api/admin/spaces      各空间占用（管理员）
GET    /api/admin/audit       审计日志（管理员）
```

调用带令牌：`Authorization: Bearer <token>`。
配了 `api_key` 的服务，带 `X-API-Key: <key>` 按管理员算（给脚本用）。

**MCP 是服务级工具**，不走 HTTP 鉴权，能看到所有空间。

**哪些接口要登录、哪些不用**（免登录模式一律按「要登录」处理，缺令牌就 401）：

| 公开（不鉴权） | 说明 |
| --- | --- |
| `/healthz` `/readyz` | 存活 / 就绪探针，给外部监控用 |
| `/api/meta` | 服务元信息，登录页自己就要用 |
| `/api/auth/config` `/login` `/register` `/logout` | 登录流程本身 |
| `/api/tiles/basemap/*` | **有意公开**：底图是 Natural Earth 公共领域数据，瓦片里没有用户数据；而 Leaflet 用 `<img>` 加载瓦片，**带不了 Authorization 头** —— 一旦要求登录，登录态下底图会「401 → 拿到一段 JSON → 静默白屏」 |

其余数据接口（数据集、图层、上传、面板、算法、作业、坐标参考系、渲染、瓦片、日志…）
**一律要登录**。另外三条隔离规则：

- **算法作业按 owner 隔离**：作业的 meta 里有图层引用、输出里有服务器绝对路径，
  别人的作业读不到也下不了（看不见就回 404，不回 403 —— 403 等于告诉对方「它确实存在」）。
  管理员看得到全部，也能清空作业历史。
- **算法输出只能落在自己的作业目录里**：调用方给的 `OUTPUT` 路径只认「光秃秃的名字」，
  绝对路径 / 带目录的路径一律换成默认名 —— 否则普通用户能指定路径，
  把文件写进**别人的私有空间**。
- **图层引用按调用者过滤**：`数据集:图层` 的引用要通过空间可见性检查；
  「按磁盘路径引用图层」也只认数据根目录里面的文件。

---

## 网页控制台

http://127.0.0.1:8848/ 打开就是工作台。**界面与操作逻辑对齐 QGIS Desktop**：
同样的菜单栏、同样的图层树、同样的工具条、同样的面板分工。

**不知道从哪下手，就看使用帮助**：<http://127.0.0.1:8848/help>（控制台里「帮助 → 使用帮助」也能到）。
一页里有：三分钟上手、界面分区逐块说明、快捷键表、工具箱怎么用、数据放在哪、
常见问题（导入的图看不见、.shp 怎么传、忘了口令怎么办…）、以及怎么自己跑一遍自检。

视觉上走的是**制图工坊**路子——纸面浅灰底、1px 细线、零阴影、小圆角、
系统字体 + 等宽体跑坐标与比例尺；整套颜色只有主色（深松绿）、警示（铁锈红）
和一组类别色。像一台安静的专业工具，不像仪表盘。

```
┌ 菜单栏: 文件 编辑 视图 图层 帮助 ───────────────────────────┐
├────────────┬─────────────────────────┬───────────────────────┤
│ 图层        │  地图画布                │ 识别结果 / 图层属性 /   │
│ ＋ ⬆ ⬇ ✕   │  ┌工具条┐                │ 工具箱                │
│ (复选框)    │  平移 放大 缩小          │                       │
│            │  识别 测距 面积          │                       │
│            │  缩放至图层 刷新         │                       │
├────────────┴─────────────────────────┴───────────────────────┤
│ 坐标 │ 刻度比例尺 │ 坐标系 │ 图层数 │ QGIS 版本 │ 提示           │
└──────────────────────────────────────────────────────────────┘
```

### 视觉语言：量测台

界面按「**量测台**」的思路收拾了一遍（精修层在 `web/atelier.css`，
只在原样式之上做加法，不改任何结构与 id）。它只做三件事，每件都从这个
活儿的本身来：

1. **数字升成一等公民。** 坐标、比例尺、要素数、面积、参数值 —— 这个控制台
   说到底是个**读数的仪器**。数字统一用等宽 + 表格数字（位数对齐了才能一眼
   比大小），前面加一道短竖线当刻度。底部那条状态栏就是它的读数带。
2. **输入区是「刻进纸里的槽」**，不是贴在纸上的盒子：底色深一线、下边一道
   更重的线。不用阴影，但比阴影更像一张图纸。
3. **地图是一张有图框的图。** 内外两道细线（间隔 3px），四角是松绿色的
   定位十字 —— 印刷图纸就是这么收边的。控件浮在图框里面。

顺带修掉两个一直存在的毛病：

* 底部状态栏（坐标 / 比例尺 / 坐标系 / 图层数 / QGIS 版本）**原来被
  Python 控制台的把手条整个盖住**，读数根本看不见 —— 把手条现在跟状态栏
  同排停靠（读数条给它让出左边一段），地图最下面那行「还没有图层，点左侧
  ＋ 添加图层」的提示也不再被压掉一半。
* AI 助手面板重做成「野外记录本」：页眉 + 一行一个项的设置页 + 记录区
  （我问的 / 它答的 / 它查了什么；参数按「键 值」一行行列开，不是一坨 JSON）
  + 底下落笔的地方。空态里三个例句点一下就填进输入框。

### 菜单栏

| 菜单 | 可用动作 |
| --- | --- |
| 文件 | 添加图层… / 重新扫描数据目录 / 清空地图 / 服务信息 |
| 编辑 | 移除选中图层 / 清除识别结果 |
| 视图 | 缩放至图层 / 缩放至全部 / 刷新地图 / 在线底图 / 面板显隐 |
| 图层 | 图层属性… / 缩放至图层 / 移至最前 / 移至最后 / 移除图层 |
| 帮助 | 关于本服务 |

图标全部取自 **QGIS 官方资源**（`web/icons/qgis/`，GPL v2+），按 QGIS 3.44
`createActions()` 里的实际对应关系一一照搬，没有 emoji、没有近似重画。
只有 QGIS 桌面版本来就没有的两个功能（按比例缩放、字段统计）用自绘图标。
**许可见文末「许可与图标来源」。**

### 图层面板（左栏）

* **复选框**控制显隐（QGIS 语义），**右键菜单**：缩放至图层 / 图层属性… / 移至最前 /
  移至最后 / 移除图层。
* 面板工具条：**＋ 添加图层** / ⬆ 上移 / ⬇ 下移 / ✕ 移除。
* **顺序即叠放顺序，首项在最上层**；新加入的图层自动置顶。
* 栅格图层在矢量图层之下（与 QGIS2web 一致），同类之间严格按图层树顺序。
* 空地图上有一张引导卡片，直接告诉你下一步做什么。

### 地图工具条（浮在地图左上）

| 按钮 | 作用 |
| --- | --- |
| 平移 | 拖动平移；**轻点（未拖动）即触发识别要素**，与 QGIS 一致 |
| 放大 / 缩小 | 以光标为中心缩放；快捷键 `+` / `-` |
| 识别要素 | 快捷键 `I` |
| 测量距离 | 快捷键 `R`，单击加节点、双击结束，显示总长与分段 |
| 测量面积 | 快捷键 `T`，显示面积 |
| 缩放至图层 | 把地图缩放到选中图层的范围 |
| 刷新地图 | 重新出图 |

**右键拖动 = 框选放大**（QGIS 默认行为），`Ctrl` + 右键拖动 = 缩小。

### 工具栏（顶部，QGIS 同款）

菜单栏下面是一条**工具栏行**，按 QGIS Desktop 的方式分成五组，
图标用同一套 QGIS 官方 SVG（`<img>` 引用，零阴影）。

| 分组 | 按钮 | 作用 |
| --- | --- | --- |
| 图层 | 添加图层 | 打开添加图层对话框 |
| | 打开属性表 | 切到属性表面板并加载当前矢量图层 |
| | 字段统计 | 切到统计面板 |
| | 缩放到图层 / 缩放到选择 | 缩放到选中图层 / 选中要素的范围 |
| 选择 | 全选 / 取消 / 反选 | 操作当前矢量图层的选择集 |
| | 按表达式选择 | 输入 QGIS 表达式选要素 |
| | 按空间关系选择 | 拿当前视野或另一个图层当参照物选要素 |
| 编辑 | 切换编辑 | 进入 / 退出编辑模式 |
| | 保存编辑 / 放弃编辑 | 写回数据文件 / 丢弃改动 |
| | 添加要素 | 地图上点顶点采集新要素（点/线/面自动判定） |
| | 删除选中要素 | 删掉选择集里的要素 |
| | 字段计算器 | 按表达式给字段批量赋值 |
| 导航 | 全图 | 缩放到全部图层范围 |
| | 上一个 / 下一个范围 | 范围历史回退与前进 |
| 视图 | 地图提示 | 悬停显示要素信息（再点一次关闭） |
| | 刷新地图 | 重新出图 |

> **编辑是真的写回数据文件**：进入编辑模式后改动先落在内存里，
> 「保存编辑」才调用 `QgsVectorLayer.commitChanges()` 落盘，
> 「放弃编辑」回滚。共享空间人人可写，个人空间只有本人可写。

### 识别结果（右栏第一页）

单击地图任意位置 → 列出该点上**所有图层**的命中情况：栅格图层给取值与类别名，
矢量图层给每个命中要素的完整属性表（对应 QGIS 的 Identify Results 面板）。

### 图层属性（右栏第二页）

按 QGIS 的标签页组织：

* **常规** —— 名称、类型、几何类型、坐标系、要素数/尺寸、分辨率、NoData、范围
* **符号系统** —— 分类栅格逐类别取色、连续栅格选 Viridis/灰度/光谱拉伸、矢量改颜色线宽填充
* **透明度** —— 0~100% 滑块，实时生效
* **源** —— 图层引用、数据集、源文件、坐标系

改完点「应用到地图」，服务端按新的配色与模式重新出图
（`/api/tiles/{ds}/{layer}/{z}/{x}/{y}.png?colors=...&mode=...`）。

### 添加图层对话框（左栏 ＋）

按 **QGIS 的数据源类型分类**：

```
矢量图层
  ├ 本地文件        GeoJSON / GPKG / SHP / GML / KML，上传即成为数据集
  └ 空间数据库      暂未接入（内核没有捆绑数据库驱动，界面里写明了原因）
栅格图层
  ├ 本地文件        GeoTIFF / IMG / VRT
  ├ XYZ 瓦片        URL 模板 + 子域 + 最大层级
  └ WMS             服务 URL + 图层名 + 版本
服务器共享数据      勾选已就绪的图层，点「打开」直接加载
```

> XYZ / WMS 由远程服务器直接渲染到浏览器，不经过 QGIS 内核，因此不能作为
> Processing 算法的输入——界面上也标注了这一点。

### 文件浏览器（文件 → 文件浏览器…）

网页里管文件的地方，和桌面版启动器里的文件浏览器是同一套东西：

| 列 | 是什么 |
| --- | --- |
| 数据集 | 数据集标识（上传时生成的那个 id） |
| 空间 | 共享空间 / 个人空间 |
| **文件** | **这个数据集里到底有哪些文件** —— 文件名是链接，**点一下直接下载** |
| 大小 | 整个数据集占多少 |
| **状态** | **已识别 / 未识别** —— 「未识别」= 文件在磁盘上，但 QGIS 打不开（比如只传了 `.shp`、缺 `.dbf/.shx/.prj` 配套件） |
| 操作 | 加到地图（双击整行也行）/ 删除 |

空间下拉第一项是「**全部**」（默认选中）—— 你上传的东西不一定落在共享空间，
可能传到个人空间去了，只列共享空间就会出现「我传的图不见了」。

列表读的是**工作区**（`/api/uploads`，= 磁盘上真实存在的东西）而不是**注册中心**
（`/api/datasets`，= 只含 QGIS 认得出来的图层）—— 所以「传了但 QGIS 打不开」的
文件也在列表里，而且能删（它以前既不显示、也删不掉）。

> 网页端的文件浏览器放在 `web/files.js`（自包含，挂在 `index.html` 末尾一行
> `<script src="/static/files.js">`），和桌面版的 `gisds/launcher/files.py`
> 是同一套语义：都读工作区、默认「全部」空间、都能显示未识别的文件。

### 工具箱（右栏第三页）

1. 搜索框输入 `buffer` / `clip` / `zonal` 等关键词。
2. 点算法 → 右边自动生成**参数表单**（参数类型、默认值、枚举选项都是**从算法自省出来的**，
   不是手写的，所以 440 个算法全都自动可用）。
3. 填参数 → 点「运行算法」。
4. 作业异步执行，右侧轮询进度与日志；完成后可以**下载输出**或**一键加到地图**。

算法输出落在 `workspace\_jobs\` 下；点「加到地图」后会登记进工作区，成为普通图层。

### 面板（右栏第四～九页）

右栏除了「识别结果 / 图层属性 / 工具箱」，还补上了 QGIS Desktop 那几个
常驻面板，能力全部走 PyQGIS 内核，所以口径与桌面版一致。

| 面板 | 能干什么 |
| --- | --- |
| **属性表** | 字段定义 + 要素表格，分页、表达式过滤；编辑模式下**双击单元格直接改值** |
| **统计** | 逐字段的计数 / 唯一值 / 空值 / 最小 / 最大 / 平均 / 中位数 / 求和 / 极差 / 标准差，外加高频值 |
| **选择** | 当前选择集；全选 / 取消 / 反选 / 按表达式 / 按空间关系；选中要素在地图上高亮 |
| **空间书签** | 存书签（用当前视野）、点书签缩放回去、删除；按数据空间分开存 |
| **日志消息** | 服务端最近 800 条日志（内存环形缓冲），可按级别过滤 |
| **处理历史** | 跑过的 Processing 作业列表与状态 |

> 面板与工具栏的实现放在 `web/panels.js`，后端在 `gisds/core/panels.py`、
> `gisds/core/bookmarks.py`、`gisds/core/logbuf.py`，接口前缀 `/api/panels/*`。

### 管理员分区（只有管理员登录后才出现）

右栏最后多一页「**管理员**」——**普通用户看不到这一页签**：不是点了报错，
是压根不渲染。页面加载时先问一次 `GET /api/auth/me`，`is_admin` 为 false
就到此为止，连 DOM 都不建；后端 `/api/admin/*` 每一路也都单独要求管理员，
前端藏起来只是体面，后端才是真门槛。

进去是四块：

| 分区 | 能干什么 |
| --- | --- |
| **用户** | 列出全部账号；新建；设为管理员 / 降为普通用户；停用 / 启用；改密码；删除（两步确认，不能删自己） |
| **空间** | 各空间登记在册的数据集、图层数与磁盘路径 |
| **审计** | 最近 200 条审计日志（登录、改密、建号、删号…） |
| **配置** | 监听地址、数据根目录、共享/用户空间、上传上限、**控制台开放模式**，以及共享超级账号的风险提示 |

「用户」分区顶部有一条**醒目提示**：内置共享超级账号开着的时候，
会直接写明「同网段任何人拿它登录都是管理员」，并告诉你要收紧该改哪个配置项。
「配置」分区里也会把「控制台对所有登录用户开放」这件事写出来 ——
让人看得见自己这台机器开着什么。

> 实现放在 `web/admin.js`（自包含，挂在 `index.html` 末尾一行），
> 后端接口在 `gisds/api/routes_admin.py`，前缀 `/api/admin/*`。

### 这台机器开着什么、怎么收紧

**发行包里带的 `config.json` 是「给局域网内同事共用」的一套配置**，四条设置叠在一起时
风险是叠加的。逐条列出，**每条都有一行就能关掉**：

| 配置项 | 发行包里的值 | 意味着 | 想收紧就 |
| --- | --- | --- | --- |
| `host` | `0.0.0.0` | 局域网内**任何设备**都能访问这台服务 | 改成 `127.0.0.1`（只本机），或放到反代后面 |
| `super_admin_password` | `（空 = 不开）` | 拿到这组口令就是管理员（管账号 / 删数据 / 跑 Python） | **发行包里默认就是空的**；要开就自己填一个，要关就清空或写成 `off` |
| `console_access` | `user` | **任何登录用户**都能在这台机器上执行 Python 代码 | 改成 `admin`（仅管理员）或 `off`（整个关掉） |
| `cors_origins` | `["*"]` | 任意来源的网页都能读本服务的**免鉴权**接口（如底图瓦片） | 收成实际来源，如 `["http://127.0.0.1:8848"]` |

> ### ⚠️ `console_access` 这一条请认真对待
>
> Python 控制台执行的是**任意 Python 代码**，和这个服务进程**平权** ——
> 能跑代码就能读该账号能读的一切数据、能写文件、能起进程、能连出去。
> 它不是「调试小工具」，是**远程代码执行入口**。
>
> 发行包给的是 `user`（局域网内同事都能用），这是按「同事之间互相方便」的用途定的。
> **如果你的部署要面向更广的人群 —— 公网开放、跨单位使用、多租户 —— 请把这一项
> 当成第一件要改的事**：
>
> * `admin` —— 只有管理员能开控制台，需要放开时由管理员单独授权；
> * `off` —— 整个关掉，不给任何人留这个口子。
>
> 改 `config.json` 里的 `console_access`，然后**重启服务**才生效 ——
> 配置在进程启动时读一次就缓存住了（`gisds/api/deps.py` 里的 `get_config()`），
> 管理员分区的「配置」页是**只读展示**，那里能一眼看到当前值，但不能直接改。

代码里的**默认值**是收紧的（`console_access` 默认 `admin`、`super_admin_password` 默认空）——
`config.json` 才是「本机部署」的选择。**从 GitHub 克隆的人拿到的
`config.example.json` 里 `console_access` 就是 `admin`**（比发行包更严一档），
别人拿去用、忘了改这几项时，拿到的是「只有管理员能跑代码、没有共享万能账号」的收紧状态。

### 内置离线底图

顶栏「底图 内置」是**本服务自己渲染的离线世界底图**，不依赖外网：

* 数据：Natural Earth 50m/110m（公共领域），装在**共享空间**里，所有登录用户都能用。
* 安装（幂等，已装则跳过）：

  ```powershell
  .\run-qgis-python.bat -m gisds basemap          # 首次安装
  .\run-qgis-python.bat -m gisds basemap --force  # 重新下载
  ```

* 瓦片接口：`GET /api/tiles/basemap/{z}/{x}/{y}.png`（256×256 PNG，带 LRU 缓存），
  `GET /api/tiles/basemap/status` 用来探测装没装。
* 渲染管线是本服务自己的：矢量数据 → `QgsMapRendererParallelJob` → PNG，
  所以**任意缩放级别都是矢量渲染、放大不糊**，而不是拉一张位图。
* 配色走「制图工坊」的纸面浅色：海洋 `#DBE5EA`、陆地 `#F8F6F0`、国界 `#8D968D`、河流 `#A9C7D7`。
* 50m 数据在 **z > 9** 之后没有更多细节，网页端设了 `maxNativeZoom=9`，
  再放大只是 Leaflet 拉伸已有瓦片，不会 404。

顶栏「底图」按钮在 **内置 / 关** 之间切；「视图」菜单里还有「在线底图 OpenStreetMap」
（需要能访问外网）和「关闭底图」。

> 坐标换算的坑见文末「已知坑」——`tile_bbox_3857()` 必须用球面墨卡托，
> 否则瓦片会整体偏移、z0 全球图被压扁、某些瓦片渲成全白。

### 软件图标

* 源图：用户提供的 1024×1024 PNG（地球 + 浏览器 + 定位针）。
* 产物三份，都由 `gisds/` 的代码引用：

  | 文件 | 用在哪 |
  | --- | --- |
  | `web/icon.png` | 网页顶栏品牌位、登录页、`apple-touch-icon` |
  | `web/favicon.png` | 浏览器标签页（32×32） |
  | `assets/icon.ico` | GUI 启动器窗口图标（多尺寸） |

* **角外是抠成透明的**：源图四角填的是纯白而不是透明，直接用在任务栏/资源管理器里
  会显示成一个白色方块角。做法是量出图标实际包围盒与圆角半径（约 156 px），
  用 Qt 的圆角裁剪路径把角外连同那圈白边一起抠掉。
* **ICO 用经典 DIB 格式**（16/24/32/48/64/128/256）：Windows 资源管理器两种都认，
  但 Tk 的 `wm iconbitmap` 走 Win32 `LoadImage`，只认 BMP 条目，
  PNG 压缩条目会读不出来。启动器用的是这一份。

### 其它

* 拖文件到地图上即可直接加载（QGIS 的拖放行为）。
* 快捷键：`P` 平移 / `I` 识别 / `R` 测距 / `T` 测面积 / `A` 添加图层 / `Del` 移除图层 /
  `Esc` 关闭弹层 / `+` `-` 缩放。
* **默认启用内置离线底图**，在线的 OpenStreetMap 要手动从「视图」菜单打开。
  这个工具面向局域网 / 离线环境，不应该强依赖外网。

---

## Python 控制台与插件

这两样都是把服务端「打开」给你：控制台让你直接写 Python，插件让你往服务里加功能。
**它们都等于在数据服务器上执行任意代码** —— 所以默认只对管理员开放。

### Python 控制台

「视图 → Python 控制台」（快捷键 `Ctrl + \``）拉起一个贴底的抽屉，
和 QGIS 桌面版的 Python Console 一个位置、一套手感。

```python
>>> project().mapLayers().keys()          # 直接摸当前工程
>>> layer = QgsVectorLayer("data/shared/example/xxx.gpkg|layername", "x", "ogr")
>>> print(layer.featureCount())
>>> print(QgsProject.instance().crs().authid())
```

* **命名空间是持久的**：上一次定义的变量还在，可以接着用。
* **按人隔离**：A 用户的变量不会出现在 B 用户那儿。
* **能摸真 QGIS**：代码在 QGIS 工作线程上执行，`QgsProject` / 图层 / 要素都能用。
* **危险调用被挡住**：`os._exit()` / `os.abort()` / `os.exec*()` 会直接结束（或替换）
  服务进程，在控制台里被拦下来并报错 —— 防的是手滑，不是防攻击。

**权限**由 `config.console_access` 决定：

| 值 | 谁能用 |
| --- | --- |
| `admin`（代码默认值） | 只有管理员 |
| `user` | **所有登录用户**（本项目 `config.json` 里就是它） |
| `off` | 整个关掉 |

免登录模式（`require_login: false`）下**一律拒绝** —— 那种模式下「能访问网页」
就等于「能在服务器上跑代码」。

> **默认开着全员可用，是有代价的**：控制台等于在数据服务器上执行任意代码。
> 开着它的时候，任何一个登录用户都能在这台机器上跑 Python ——
> 管理员分区的「配置」页里会把这句写出来，别让它悄悄开着。
> 要收紧就把 `console_access` 改成 `admin` 或 `off`。

> **已知限制**：代码跑在 QGIS 工作线程上，Python 没法中断一个正在跑的线程。
> 所以死循环 / 长时间阻塞的代码**超时后接口会返回，但那个线程还占着** ——
> QGIS 相关的请求会跟着一起卡住，必要时得重启服务。

### 插件系统

插件是装在 `plugins/` 下的一个 Python 包（目录名 = 插件标识 `slug`）：

```
plugins/
  gisds_hello/
    metadata.json
    __init__.py
```

`metadata.json`：

```json
{
  "name": "示例插件",
  "version": "1.0.0",
  "description": "做什么用的",
  "author": "谁写的",
  "enabled": true
}
```

`__init__.py`：

```python
class GisdPlugin:
    def init(self, ctx):
        ctx.add_panel(id="hello", title="打个招呼",
                      endpoint="/api/plugin/gisds_hello/panel")
        ctx.add_menu_item(menu="tools", label="打个招呼",
                          endpoint="/api/plugin/gisds_hello/hello")
        ctx.add_route("/api/plugin/gisds_hello/hello", self._hello)
        ctx.add_action("hello", self._action)
        ctx.register_processing_provider(HelloProvider())

    def unload(self):
        pass
```

`ctx` 能做的事：

| 方法 | 作用 |
| --- | --- |
| `add_panel(id, title, endpoint, type)` | 往右栏加一个面板（`html` 或表格） |
| `add_menu_item(menu, label, endpoint, method, params)` | 往菜单栏加一项 |
| `add_route(path, handler, methods)` | 注册自己的 REST 接口（`handler(request, principal)`） |
| `add_action(name, fn)` | 注册一个能从网页调用的动作（`fn` 收到一个 dict） |
| `register_processing_provider(provider)` | 注册 Processing 算法，**自动出现在工具箱里** |
| `log(msg)` | 写进服务端日志 |

**装插件**有两条路：

1. 把插件目录拷进 `plugins/`（默认目录，可用 `config.json` 的 `plugin_dir` 改），
   重启服务即生效；
2. 网页上「插件 → 安装插件」上传一个 `.zip`（像 QGIS 插件管理器）。

zip 是**上传上来的任意文件**，所以安装时会逐条检查：路径穿越（`../`）、绝对路径、
符号链接、超大文件、文件数超限、缺 `metadata.json` —— 一样都不放过。

**装 / 启停 / 重载 / 卸载只有管理员能做**（插件是服务端代码）。
改完插件代码可以「重载」，不用重启服务。

**插件坏了不许拖垮服务**：插件 `init()` 抛异常只会被记成 `error`，
服务照常跑，其它插件照常加载。

项目里自带一个示例插件 `plugins/gisds_hello/`，把 SDK 的每个能力都用了一遍
（右栏面板 / 菜单项 / 自定义接口 / 动作 / 一个 Processing 算法），
改代码前可以先拿它当参照。

---

## REST API 速查

CORS 全开（`allow_origins: ["*"]`），局域网里任何网页都能直接 `fetch`。

### 元信息

```
GET  /healthz                        存活探针 + QGIS 版本（进程在就 200）
GET  /readyz                         就绪探针（没准备好就 503）
GET  /api/meta                       服务概览：数据集/图层/算法数量、运行时信息
```

**两个探针各管一件事，别混用：**

* `/healthz` —— **存活**。进程在就 200，**启动期间也是 200**。
  给外部监控做心跳用。它问的是「服务还活着吗」，不是「能干活了吗」。
  启动期间这个端点**不碰数据目录**（`datasets()` 会触发一次扫描并阻塞在 QGIS
  工作线程上），所以返回里没有 `datasets` 字段，只有 `ready: false`。
* `/readyz` —— **就绪**。服务启动要等 PyQGIS 初始化、Processing 装载、首个管理员
  建号、数据目录扫描**全部走完**；这期间返回 **503**，body 是
  `{"status":"starting","ready":false}`，就绪后返回 200。
  「什么时候可以开始取数 / 建账号」看它。

> **为什么拆成两个**：以前一个 `/healthz` 背了存活和就绪两个语义 —— 启动期回 503。
> 问题在于，配置不当的外部监控会把启动那几秒的 503 当成「服务挂了」，
> 反而触发重启循环。拆开后，存活探针永远拿到 200，要「等准备好」的一方改用
> `/readyz` 即可。**自检脚本等的是 `/readyz`** —— 早先「等 `/healthz` 返回 200 就
> 往下走」正是偶发失败的来源（服务还没建好管理员就去查口令备忘）。

### 数据集与图层

```
GET  /api/datasets                   列出全部数据集（共享库 + 上传区）
GET  /api/datasets/{id}              数据集详情（含全部图层）
GET  /api/datasets/{id}/overview     每个分类图层的地类面积概览
POST /api/datasets/rescan            重新扫描数据目录
GET  /api/layers                     跨数据集的图层扁平清单（ref = 数据集:图层）
GET  /api/layers/{ds}/{layer}/fields 矢量图层字段清单
```

### 上传

```
POST   /api/uploads                  multipart，字段名 files，可多文件
GET    /api/uploads                  列出已上传数据集
DELETE /api/uploads/{id}             删除一个上传数据集
GET    /api/uploads/{id}/files/{name} 下载原始文件
```

### 栅格

```
GET  /api/rasters/{ds}/{layer}/point?lon=&lat=      点取值
GET  /api/rasters/{ds}/{layer}/bbox?minx=&miny=&maxx=&maxy=   范围统计
POST /api/rasters/{ds}/{layer}/polygon               多边形统计（body: GeoJSON）
GET  /api/rasters/{ds}/{layer}/histogram?bins=32     直方图
GET  /api/rasters/{ds}/{layer}/area                  分类面积
GET  /api/rasters/{ds}/{layer}/stats                 波段统计
```

### 矢量

```
GET  /api/vectors/{ds}/{vector}/features?limit=&offset=   要素分页（GeoJSON, WGS84）
GET  /api/vectors/{ds}/{vector}/stats                   字段统计
GET  /api/vectors/{ds}/{vector}/hit?lon=&lat=           点命中
```

### 出图

```
GET /api/render/raster/{ds}/{layer}.png?bbox=&width=    栅格出图
GET /api/render/vector/{ds}/{vector}.png                矢量出图
GET /api/render/combined/{ds}.png                       整个数据集合成出图
GET /api/tiles/{ds}/{layer}/{z}/{x}/{y}.png             XYZ 瓦片
```

**两个瓦片端点的鉴权不一样，别混**：

* `/api/tiles/basemap/{z}/{x}/{y}.png`（内置离线底图）**刻意不鉴权** ——
  前端是用 `L.tileLayer(...)` 直接挂 `<img src>` 的，带不了请求头。
* `/api/tiles/{ds}/{layer}/{z}/{x}/{y}.png`（数据图层瓦片）**要令牌**。
  浏览器 `<img src>` 带不了 Authorization 头，但服务端**支持把令牌放查询串**：
  `.../{z}/{x}/{y}.png?token=<你的会话令牌>`（也认 `access_token=`）。
  控制台里显示自己的栅格**走的就是这条瓦片路径**（`web/index.html` 的
  `AuthTileLayer` 用 fetch 带 Authorization 头，令牌不进 URL、不进访问日志），
  所以在控制台里显示栅格不需要拼 token；
  只有**自己**在别处挂 XYZ 瓦片时才需要拼。

### 工具箱（Processing）

```
GET  /api/processing/algorithms                 列出全部算法（440 个）
GET  /api/processing/algorithms/{id}            算法参数定义
GET  /api/processing/groups                     分组
POST /api/processing/run                        提交作业，body: {algorithm, parameters, name}
GET  /api/processing/jobs                       作业列表
GET  /api/processing/jobs/{id}                  作业状态（status/progress/logs/outputs）
GET  /api/processing/jobs/{id}/output/{key}     下载输出文件
POST /api/processing/jobs/{id}/add-to-map       把输出登记进数据源
DELETE /api/processing/jobs                     清理作业记录```

**示例：跑一个缓冲区**

```bash
curl -X POST http://127.0.0.1:8848/api/processing/run \
  -H "Content-Type: application/json" \
  -d '{"algorithm":"native:buffer","parameters":{"INPUT":"example:boundary","DISTANCE":0.01}}'
```

返回 `{"job_id":"...","status":"queued"}`，再轮询 `GET /api/processing/jobs/{job_id}`。

> **图层引用统一写成 `数据集:图层`**（例如 `example:boundary`）。

### 面板（属性表 / 统计 / 选择 / 书签 / 日志）

```
GET    /api/panels/table?ref=&limit=&offset=&where=   属性表：字段 + 一页要素
GET    /api/panels/statistics?ref=&field=             字段统计
GET    /api/panels/overview?ref=                      图层信息一览
POST   /api/panels/select                             选择要素
                                                    body: {ref, expression} 按表达式
                                                    body: {ref, geometry, predicate} 按空间关系
                                                    body: {ref, target_ref, predicate} 按另一个图层
                                                    body: {ref, mode:"all"} 全选
GET    /api/panels/bookmarks?scope=                   空间书签
POST   /api/panels/bookmarks                          新建书签（body: {name, extent, crs}）
DELETE /api/panels/bookmarks?name=                   删除书签
GET    /api/panels/logs?limit=&level=                 服务端日志
DELETE /api/panels/logs                               清空日志缓冲
GET    /api/panels/capabilities                       有哪些面板、哪些矢量图层
```

**编辑接口**（会写回数据文件，按数据空间判写权限）：

```
POST   /api/panels/edit/start                        进入编辑模式
POST   /api/panels/edit/commit                       保存（写回文件）
POST   /api/panels/edit/rollback                     放弃改动
PATCH  /api/panels/edit/feature                      改属性（body: {ref, fid, attributes}）
POST   /api/panels/edit/feature                      新增要素（body: {ref, geometry, attributes}）
DELETE /api/panels/edit/feature                      删要素（body: {ref, fids}）
POST   /api/panels/edit/calculator                   字段计算器（body: {ref, field, expression}）
```

> `predicate` 可取 `intersects` / `contains` / `within` / `touches` / `crosses` /
> `overlaps` / `disjoint`，语义是「**源要素 相对 目标**」。
> `disjoint` 是负向关系，要求与目标里**所有**要素都不相交才算命中。
> 算法参数、REST 路径、MCP 工具共用这一套引用。

---

## MCP Server（接入 OpenClaw）

同一份内核再包一层 **MCP**，让 Agent（OpenClaw「龙虾」等）把 QGIS 当工具用。

```powershell
.\run-qgis-python.bat -m gisds mcp            # stdio（给 MCP 客户端用）
.\run-qgis-python.bat -m gisds mcp --transport streamable-http --port 8849
```

> **传输方式决定暴露面**。MCP 是服务级工具：**不走鉴权、能看到所有空间**（本项目的
> 明写取舍，与网页端的多租户隔离是两条线）。
> * `stdio`（默认，`mcp.bat` 用的就是这个）：只有本机能起这个进程，暴露面 = 本机用户权限。
> * `streamable-http` / `sse`：**整个网络都能调**，等于把「能看到所有空间、能改所有数据」
>   开给了局域网。用这两种传输启动时服务端会打一条醒目告警。
>   绑到 `0.0.0.0` 之前请先想清楚网络边界。

### OpenClaw 配置

```json
{
  "mcpServers": {
    "gisds": {
      "command": "cmd.exe",
      "args": ["/c", "C:\\path\\to\\browser-qgis\\mcp.bat"],
      "env": { "GISDS_DATA_ROOT": "C:\\path\\to\\browser-qgis\\data" }
    }
  }
}
```

> 把 `C:\\path\\to\\browser-qgis` 换成**你自己解压这个项目的位置**。
> `mcp.bat` 会用它自己旁边的 `run-qgis-python.bat` 找 QGIS，
> `GISDS_DATA_ROOT` 不填也能跑（默认用项目下的 `data\`）。

### 工具清单（27 个）

| 工具 | 作用 |
| --- | --- |
| `gis_list_datasets` | 列出所有数据集 |
| `gis_get_dataset` | 数据集详情（全部图层、坐标系、范围） |
| `gis_list_layers` | 跨数据集检索图层，返回可用的 `ref` |
| `gis_rescan` | 重新扫描数据目录 |
| `gis_query_point` | 查询某经纬度在栅格图层上的取值 |
| `gis_query_bbox` | 矩形范围内的像元统计 |
| `gis_layer_area` | 分类图层的地类面积 |
| `gis_layer_histogram` | 像元值直方图 |
| `gis_list_features` | 取矢量要素（GeoJSON） |
| `gis_vector_stats` | 矢量统计：要素数、几何类型、字段分布 |
| `gis_point_hit` | 点落在哪个矢量要素里 |
| `gis_render_map` | 出图，直接返回 PNG 图片内容块 |
| `gis_list_algorithms` | 搜索 Processing 算法 |
| `gis_describe_algorithm` | 查看算法参数定义 |
| `gis_run_algorithm` | 执行算法 |
| `gis_attribute_table` | 读属性表（字段 + 一页要素） |
| `gis_field_stats` | 字段统计：计数 / 唯一值 / 极值 / 均值 / 中位数 |
| `gis_select_features` | 选择要素（按表达式 / 按几何 / 按另一个图层 / 全选） |
| `gis_edit_start` | 进入编辑模式 |
| `gis_edit_commit` | 保存编辑（写回文件） |
| `gis_edit_rollback` | 放弃编辑 |
| `gis_edit_feature` | 改要素属性 |
| `gis_add_feature` | 新增要素 |
| `gis_delete_features` | 删除要素 |
| `gis_field_calculator` | 字段计算器（按表达式批量赋值） |
| `gis_list_bookmarks` | 列空间书签 |
| `gis_add_bookmark` | 新建空间书签 |

> stdio 传输时 **stdout 是协议通道**，不能重定向。MCP Server 内部所有日志都走 stderr，
> stdout 只输出协议帧。

---

## 网页里的 AI 助手

同一套工具再包一层聊天窗口，**直接嵌在网页右栏**（标签页「AI 助手」）。
和 MCP 的区别只有一处：**身份**。

| | MCP Server | 网页 AI 助手 |
| --- | --- | --- |
| 工具 | 同样 27 个 | 同样 27 个（**同一份定义**） |
| 身份 | 服务级：能看到所有空间 | **按登录用户**：只看得到自己有权看的空间 |
| 写操作 | 有 | 默认**没有**（见下） |
| 鉴权 | 不走 HTTP 鉴权 | 走会话令牌 |

工具定义收在 `gisds/core/gis_tools.py` 一处（MCP 与网页 AI 都从它取），
所以工具名、参数、说明不会各写一份慢慢漂移。

### 配一个大模型

**两种协议都支持**，在「AI 助手 → 模型设置」里选：

| 协议 | 接口 | 适合 |
| --- | --- | --- |
| **OpenAI 兼容** | `POST {base_url}/chat/completions` | 大多数服务商（DeepSeek、Moonshot、通义、智谱、硅基流动、Ollama…） |
| **Anthropic 原生** | `POST {base_url}/messages` | Claude 官方、以及说 Anthropic 协议的自建网关 |

**API Key 有两个来源，浏览器的优先**：

1. **服务端默认** —— 管理员在同一个面板里填，存到 `data/ai_config.json`（用户库旁边）。
   全机所有登录用户都能直接用，不用每人各填一遍。
2. **浏览器临时填** —— 存在**这台电脑**的 localStorage 里，随请求发上来，
   优先于服务端默认。适合「自己用自己的 key」。

> **Key 永不下发到前端**。`GET /api/ai/config` 只回 `has_key`（有没有），
> 不回明文。服务端那份存在磁盘上，是明文 —— 想收紧就管好这台机器的文件权限，
> 或者干脆不配服务端默认、让每个人自己填。

> **换了接口地址（或协议）就不能再借用服务端那份 key**。否则任何登录用户都能
> 让服务端把自己的 key 送到他指定的任意地址去 —— 那等于把全机的凭据交出去。
> 所以「换地址」必须同时「自带 key」：浏览器只改了 Base URL 而没填 Key 时，
> 服务端直接拒绝这次请求（并说清楚原因），**不会**用服务端那份 key 去发。

### 谁能用

* **所有登录用户**都能用（产品定死，不给配置项）。
* **免登录模式下的匿名访客一律拒绝** —— 那种模式下「能打开网页」就等于
  「能拿别人的数据问 AI」，不能接受。
* **写操作默认关闭**。改要素、算字段、建物签这类工具
  （`gis_edit_*` / `gis_add_feature` / `gis_delete_features` / `gis_field_calculator` /
  `gis_add_bookmark`）默认**不**给模型，只有管理员在服务端把 `allow_write` 打开才有。
  浏览器侧**不能**开 —— 能改的是全机，不该由某个用户随手打开。
* 改服务端默认配置（`PUT /api/ai/config`）**只有管理员**能做。

### 一次对话是怎么跑的

```
你 → 模型 ──要调工具──→ 服务端按**你的身份**执行工具
                ↑                    ↓
                └── 工具结果 ────────┘
                     （最多 8 轮）
```

工具调用过程在网页上可展开查看（模型到底查了什么、拿到了什么）；
`gis_render_map` 出的图会直接显示在对话里。

### 接口

| 接口 | 谁能用 | 说明 |
| --- | --- | --- |
| `GET /api/ai/config` | 所有登录用户 | 读服务端默认配置（**没有** key 明文，只有 `has_key`） |
| `PUT /api/ai/config` | **仅管理员** | 改服务端默认配置：协议、Base URL、模型、Key、是否允许写操作 |
| `GET /api/ai/tools` | 所有登录用户 | 这一轮给模型用的工具清单（含 JSON Schema） |
| `POST /api/ai/chat` | 所有登录用户 | 问一句；请求体可带 `provider` / `base_url` / `model` / `api_key` 做临时覆盖 |

```powershell
# 配一份服务端默认（管理员）
curl -X PUT http://127.0.0.1:8848/api/ai/config -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" `
  -d '{"provider":"openai","base_url":"https://api.openai.com/v1","model":"gpt-4o-mini","api_key":"sk-…"}'

# 问一句
curl -X POST http://127.0.0.1:8848/api/ai/chat -H "Authorization: Bearer $TOKEN" `
  -H "Content-Type: application/json" -d '{"message":"现在有哪些数据集？"}'
```

---

## 打包发行版

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File pack-release.ps1
# -> release\BrowserQGIS-<版本>.zip
```

打出来的东西是**解压即用**的：`deps\` 一起打进去，所以收件人不用装任何东西，
解压 → 双击 `start.bat` 就能跑（前提是本机装了 QGIS 3.44.14）。

**不打包的东西**（脚本会逐条断言它们确实不在包里）：

| 不打 | 为什么 |
| --- | --- |
| `data/users/*` | 各用户的私有数据（动辄几百 MB，而且**是别人的数据**） |
| `data/gisds.db*` | 用户库（账号、口令散列） |
| `data/admin_password.txt` | 管理员口令的明文 |
| `data/bookmarks.json` `data/jobs/*` | 书签与算法作业产物 |
| `logs/` | 运行日志 |
| `__pycache__/` `*.pyc` | 编译缓存 |
| `tests/_workspace` `tests/sample_data/jobs` `tests/sample_data/users` | 自检留下的垃圾 |

**要打的**：`gisds/` `web/` `assets/` `deps/` `plugins/` `tests/`（自检脚本，让收件人
能自己验）+ `data/shared/`（示例数据 + 内置离线底图）+ 根目录的启动脚本与文档。

打包完会**自己开包核对**：有没有 `__pycache__`、有没有用户数据、有没有凭据、
必需的文件在不在 —— 有问题就退出码 1，不会留下一个「看着像成功」的包。

---

## 脱离 QGIS 运行（standalone 发行版）

不想在目标机器上装 QGIS？把 **QGIS 运行时一起打包**即可 —— 收件人解压即用，
本机**不需要装 QGIS**。

```powershell
# 1. 做一份可搬家的 QGIS 运行时（从本机的 QGIS 拷 + 裁剪）
powershell -NoProfile -ExecutionPolicy Bypass -File prepare-qgis-runtime.ps1

# 2. 打进发行版（包里多一个 qgis/ 目录）
powershell -NoProfile -ExecutionPolicy Bypass -File pack-release.ps1 -Standalone
# -> release\BrowserQGIS-<版本>-standalone.zip
```

**为什么能搬家**：QGIS 是 OSGeo4W 安装，它的 `bin\o4w_env.bat` 从**自身位置**推导
`OSGEO4W_ROOT`，`etc\ini\*.bat` 里也全是 `%OSGEO4W_ROOT%` 相对量 —— 整棵树没有
写死的安装路径（只有几个卸载脚本写死了，那些我们不跑）。所以把树搬到任何路径都能用。

**裁掉了什么**（都是「本来就没在工作」的部分）：

| 裁掉 | 省 | 为什么安全 |
| --- | --- | --- |
| `apps/grass` | 346 MB | GRASS provider 注册了但**算法数是 0**（这个安装里 GRASS 本来就没工作） |
| `apps/saga` | 45 MB | SAGA provider **根本没注册** |
| `apps/Qt5/qml` | 302 MB | 无头服务只用 `QgsMapSettings` 出图，从不加载 QML |
| `i18n`（只留 zh-Hans + en） | 135 MB | 40 多种语言的翻译文件 |
| `Qt5/translations`（只留 zh_CN + en） | 33 MB | 同上 |
| `site-packages/wx` | 34 MB | wxPython 只有 GRASS/SAGA provider 用 |
| `__pycache__` / `*.pyc` | 若干 | 首次运行自动重新生成 |

**保留的是硬骨头**：`bin/`（DLL + python3.exe）、`share/proj`（**proj.db 缺了所有
坐标转换都会挂**）、`apps/Python312`、`apps/qgis-ltr`、`apps/Qt5`、`apps/gdal`、`apps/gs`。

裁完 **1,425 MB**（原 2,301 MB），**算法数仍是 440**（native 321 + gdal 57 +
qgis 44 + pdal 17 + 3d 1），provider 数仍是 9。

打出来的两个包：

| 包 | 体积 | 目标机器要装 QGIS 吗 |
| --- | --- | --- |
| `BrowserQGIS-1.1.0.zip` | **19 MB** | 要（本机已装 QGIS 就用这个） |
| `BrowserQGIS-1.1.0-standalone.zip` | **525 MB** | **不用**（运行时在里面） |

**怎么保证它真的能用**：打包脚本会**开包核对**运行时是否齐全；`prepare-qgis-runtime.ps1`
会当场用新位置起一次 PyQGIS，并断言 `sys.path` 里**没有**原安装路径的残留；
改完 `run-qgis-python.bat` 之后（优先用旁边的 `qgis\`），整套自检会**对着捆绑运行时
跑一遍**（`logs\run_standalone_*.txt`）。

---

### 把发行包挂到 GitHub Releases（不进 git 仓库）

`release/` 整个目录在 `.gitignore` 里 —— **二进制发行包不进版本库**，原因很实在：

| 包 | 体积 | 能不能进 git 仓库 |
| --- | --- | --- |
| `BrowserQGIS-1.1.0.zip` | 19 MB | 技术上能（< 100 MB），但每次 clone 都得多拖 19 MB，而且二进制一改，历史就永久膨胀 |
| `BrowserQGIS-1.1.0-standalone.zip` | 525 MB | **不能** —— GitHub 硬拦仓库里超过 100 MiB 的文件（要跟踪只能用 Git LFS） |

正确做法是**用 GitHub Releases 挂附件**：二进制只存一份，标签仍然指向源码，
下载的人从 Release 页面直接拿 zip。Release 的**单个附件上限是 2 GB**，525 MB 放得下。

```powershell
# 1. 打两个包
powershell -NoProfile -ExecutionPolicy Bypass -File pack-release.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File pack-release.ps1 -Standalone

# 2. 打标签（标签留在源码上）
git tag -a v1.1.0 -m "Browser QGIS 1.1.0"
git push origin v1.1.0

# 3. 挂附件到 Release
gh release create v1.1.0 `
  release\BrowserQGIS-1.1.0.zip `
  release\BrowserQGIS-1.1.0-standalone.zip `
  --title "Browser QGIS 1.1.0" `
  --notes-file RELEASE_NOTES.md
```

没装 `gh` 的话，网页上开
<https://github.com/<你的账号>/<仓库名>/releases/new> → 选 tag → 把两个 zip 拖进去即可。

---

## 目录结构与数据约定

### 仓库里有什么 / 没有什么

开源之前按「**凡是属于某台机器、某个用户的运行时产物，一律不进仓库**」整理过一遍：

| 进仓库 | 不进仓库（见 `.gitignore`） |
| --- | --- |
| 源码 `gisds/`、前端 `web/` | **用户自己的 GIS 数据** `data/users/` |
| 自检脚本 `tests/` | 账号库 `data/gisds.db`、口令备忘 `data/admin_password.txt` |
| 样例数据 `data/shared/example/`（合成） | 发行包与运行时暂存 `release/`（以 GB 计） |
| 内置离线底图 `data/shared/basemap/`（Natural Earth，公共领域） | 运行日志 `logs/`、缓存 `__pycache__/` |
| `config.example.json`（无密钥的模板） | **本机的 `config.json`**（可能有明文口令） |
| 第三方代码与其许可（`web/vendor/`、`web/icons/qgis/`） | 自检留下的临时数据 `tests/_workspace/` |

`config.json` 不进仓库是有意的：它会**被打进发行包**，里面可能有明文口令。
没有它服务照常起，用的是代码里**收紧的**默认值。

### 目录树

```
gis-data-server/
├─ gisds/
│  ├─ core/                 内核（不依赖任何 Web 框架）
│  │  ├─ qgis_env.py        PyQGIS 初始化 / Processing 插件装载
│  │  ├─ executor.py        唯一工作线程：所有 QGIS 调用串行化
│  │  ├─ registry.py        数据集注册中心（多空间 + 按调用者过滤）
│  │  ├─ db.py              用户库（SQLite）
│  │  ├─ auth.py            密码哈希、会话令牌、Principal
│  │  ├─ tenancy.py         数据空间模型
│  │  ├─ raster.py vector.py  点查询 / 范围统计 / 面积 / 直方图 / 要素
│  │  ├─ render.py          QGIS 出图、专题配色、XYZ 瓦片
│  │  ├─ processing.py      440 个算法自省 + 参数翻译 + 执行
│  │  ├─ panels.py          面板内核：属性表 / 字段统计 / 选择 / 要素编辑
│  │  ├─ bookmarks.py       空间书签（按数据空间存 JSON）
│  │  ├─ logbuf.py          服务端日志环形缓冲（日志消息面板）
│  │  ├─ storage.py         上传工作区 + 旧布局迁移
│  │  ├─ jobs.py            异步作业管理
│  │  └─ errors.py
│  ├─ api/                  FastAPI 路由层
│  ├─ mcp/server.py         MCP Server（27 个工具）
│  ├─ launcher/             图形启动器（tkinter，只用标准库）
│  │  ├─ app.py             主窗口
│  │  ├─ files.py           文件浏览器（面板 + 独立窗口）
│  │  ├─ api.py             与服务端的 HTTP 通道（主窗口和独立窗口共享登录态）
│  │  └─ theme.py           配色
│  ├─ config.py  cli.py
├─ web/                     网页控制台（index.html / panels.js / login.html / demo.html / vendor/leaflet）
├─ data/                    数据根目录
│  ├─ gisds.db              用户库
│  ├─ shared/               共享空间
│  └─ users/<用户ID>/       各用户的个人空间
├─ logs/                    后台运行时的日志（用 start.bat 前台跑时日志在窗口里）
├─ deps/                    Web 层依赖（fastapi / uvicorn / mcp / httpx ...）
├─ tests/                   自检脚本
├─ release/                 打包出来的发行版（pack-release.ps1 生成的 zip）
├─ config.json              配置
├─ launcher.bat             图形启动器（双击）
├─ launcher-files.bat       只开文件浏览器窗口（双击）
├─ run-qgis-python.bat      进 QGIS 运行时环境的 python 启动器
├─ start.bat / start.ps1    一键启动 REST
├─ mcp.bat                  一键启动 MCP（stdio）
├─ doctor.bat               环境自检
├─ pack-release.ps1         打包发行版（生成 release/BrowserQGIS-<版本>.zip）
├─ prepare-qgis-runtime.ps1 做一份可搬家的 QGIS 运行时（standalone 版用）
├─ RELEASE_NOTES.md         发布说明（跟着发行包一起发出去）
└─ setup.ps1                安装 deps 依赖
```

**数据空间：**

* `data/shared/` —— 共享空间。所有登录用户都能读，也都能往里传。
* `data/users/<用户ID>/` —— 该用户的私有空间，只有本人能看见和改。

目录结构示例：

```
data/
├── gisds.db
├── ai_config.json         AI 助手的**服务端默认**配置（管理员配的；含 API Key 明文）
├── shared/
│   └── example/            一个数据集 = 一个目录
│       ├── metadata.json   可选：名称/描述/图层标题/类别表
│       ├── classes.tif     栅格图层
│       ├── surface.tif     栅格图层
│       └── boundary.geojson 矢量图层
└── users/
    └── a0676cb9f7ae/
        └── my_upload/      某个用户上传的数据集
```

> `ai_config.json` 放在**用户库旁边**（不在 `data/shared/` 里）—— 共享空间里的东西
> 会被注册中心当成数据集扫进来。删掉它 = 清空服务端默认的 AI 配置。

> 旧版布局（`data/<数据集>/` + 独立 `workspace/`）在首次启动时会**自动迁移**到新布局，
> 两个旧目录都并入共享空间。

---

## 改名说明（产品名变了，契约没变）

产品名从 **GISDS** 改成了 **Browser QGIS**，但下面这些**内部标识符一律不动**：

| 标识符 | 出现在哪 | 为什么不能改 |
| --- | --- | --- |
| `GISDS_*` 环境变量 | `config.json` 之外的部署脚本、systemd unit、Docker `-e`、CI | 改名后存量部署**读不到配置** |
| `gisds.db` | 数据根目录、启动器、口令备忘文件名 | 同上，且旧库会被当成新库另起一份 |
| `-m gisds` | `start.bat` / `mcp.bat` / `doctor.bat` / 部署脚本 | 同上 |
| `gisds` 模块名 | `gisds/` 目录、`python -m gisds` | 同上 |
| `gisds_plugin_*` 路由名 | 插件自己注册的接口路径 | 插件生态的既有约定 |
| `gisds_hello` | 示例插件的 slug | 同上 |
| `GISDS_` 前缀的路由/环境变量 | 见上 | 同上 |

**改名不影响配置兼容性。** 上面这些名字已经**写进现有 config.json 和部署脚本里了**，
是既有部署的**契约** —— 顺手改成 `BROWSER_QGIS_*` 会让存量部署读不到配置、
**静默回落到默认值**（口令、端口、路径全变）而且**不报任何错**：
服务照常起得来，只是连到了另一个库、另一个端口。

所以：**用户可见的名字**（网页标题、菜单、启动器界面、文档标题）用 Browser QGIS；
**机器可见的名字**（环境变量、文件名、模块名、命令名）保持 `gisds`。
改这两者之前，先想清楚存量部署。

> 同样的道理适用于**将来**的任何改名：先列一份「机器可见的名字」清单，
> 逐条确认「改了会发生什么」，再动手。

---

## 配置项

`config.json`（相对项目根目录）：

```json
{
  "data_root": "data",
  "shared_root": "data/shared",
  "users_root": "data/users",
  "db_path": "data/gisds.db",
  "require_login": true,
  "allow_registration": false,
  "admin_username": "admin",
  "admin_password": "",
  "super_admin_password": "",
  "host": "0.0.0.0",
  "port": 8848,
  "cors_origins": ["*"],
  "max_upload_mb": 512,
  "job_timeout": 1800,
  "default_page_size": 50,
  "max_page_size": 1000,
  "max_sample_pixels": 4000000,
  "tile_cache_size": 256,
  "log_level": "info",
  "api_key": "",
  "console_access": "user",
  "console_timeout": 30,
  "plugin_dir": "plugins",
  "plugins_enabled": true
}
```

环境变量可以覆盖同名配置（`GISDS_DATA_ROOT`、`GISDS_REQUIRE_LOGIN`、
`GISDS_ALLOW_REGISTRATION`、`GISDS_ADMIN_PASSWORD` …），MCP 场景常用。

常用开关：

* `require_login: false` —— 免登录模式，所有人共用共享空间
* `allow_registration: true` —— 打开网页可自助注册
* `console_access` —— Python 控制台谁能用：`admin`（默认）/ `user` / `off`。
  **本项目自带的 `config.json` 里是 `user`** —— 所有登录用户都能用控制台。
  代码默认值保持 `admin` 是刻意的：别人拿去用、忘了改这一项时，
  拿到的是「只有管理员能跑代码」的收紧状态，而不是「一装上就能远程执行任意代码」。
* `console_timeout` —— 控制台单次执行超时秒数（默认 30）
* `super_admin_password` —— **内置共享超级账号**的口令（**默认留空 = 不开**）。
  跟 `admin_password` 不是一回事：

  | | `admin_password` | `super_admin_password` |
  | --- | --- | --- |
  | 什么时候生效 | **只在库还是空的时候**用一次（首次自举） | **每次启动都对齐** |
  | 后来管理员改了密码 | 配置那份变成过期副本（启动只告警，不覆盖） | 启动时被**改回**配置里的口令 |
  | 账号不存在时 | 建一个（口令随机） | 建一个（口令就是配置里这个） |
  | 角色不是 admin 时 | 升成 admin | 升成 admin |

  两者配的是**同一个账号**（`admin_username`），**只能设一个**；都设了以
  `super_admin_password` 为准（它是更强的约束）。
  写成 `off` / `none` / `disabled` 就算**没配** —— 临时服务（自检、别人的部署）
  常常是继承生产配置起来的，用这个值可以只关掉这一项而不动别的。

  **它是设计上的后门**：服务监听 `0.0.0.0` 时，同网段任何人拿这组口令登录
  都是管理员，能管账号、能删数据、能跑 Python。要收紧就把它清空（或写成 `off`），
  或者把服务收到内网 / 反代后面。
* `plugin_dir` —— 插件目录（默认 `plugins/`，**不要**放进 `data/`：
  旧布局迁移会把 `data/` 下的每个目录都搬进 `data/shared/`）
* `plugins_enabled` —— 插件总开关（关掉就一个都不加载）
* `admin_password` —— 预设首个管理员的密码（留空则随机生成）
* `password_note` —— 是否把管理员口令写成明文备忘（放在**用户库旁边**，
  正式库即 `data/admin_password.txt`；库换了名字，备忘跟着变成
  `<库名>_admin_password.txt`，仍在库旁边）。默认开：多机部署时（服务在数据机、
  管理员在另一台机器用启动器连过去），随机生成的初始密码只有这个文件和启动日志
  两处能找到。机器要接到不可信网络时可以设成 `false` —— 关掉后初始密码只在启动
  日志里留一次。
  生命周期：**自举时写**（且只在口令是随机生成、不是配置带来的情况下写；
  配置里本来就有明文副本，再复制一份是净损失）；
  **改密之后同步更新**成新口令（改密 API 与 `gisds set-password` 两条路径都更新）——
  因为那时配置里那一份已经过期，备忘就是当前有效口令唯一的明文副本。
  不变式：**文件里记着的永远是当前有效口令**，不会留下失效的旧密码。
* `api_key` —— 服务级密钥，带 `X-API-Key` 按管理员算

---

## 自检脚本

**完整的测试清单、逐支断言数、以及验出来的技术结论**，见
[docs/测试与结论.md](docs/测试与结论.md)：
34 支脚本、1844 条断言、5 条已知红（附原因），
外加几条「踩过坑才拿到的结论」（比如 Windows 上 `SO_REUSEADDR` 让 bind 探测彻底失效）。

全部用 `run-qgis-python.bat` 跑（自带 QGIS 运行时环境）：

```powershell
.\run-qgis-python.bat -m gisds doctor          # 环境自检：QGIS / Processing / 依赖版本
.\run-qgis-python.bat -m gisds scan            # 列出已注册的数据集
.\run-qgis-python.bat -m gisds set-password admin  # 重置某个账号的密码
.\run-qgis-python.bat tests\check_core.py      # 内核自检
.\run-qgis-python.bat tests\check_api.py       # REST 接口自检
.\run-qgis-python.bat tests\check_auth.py      # 用户系统与空间隔离
.\run-qgis-python.bat tests\check_mcp.py       # MCP 工具自检（进程内调用 27 个工具）
.\run-qgis-python.bat tests\check_mcp_stdio.py # MCP stdio 端到端
.\run-qgis-python.bat tests\check_mcp_panels.py # MCP 面板/编辑/书签工具自检
.\run-qgis-python.bat tests\check_new.py       # 新能力自检（上传 / 工具箱 / 作业）
.\run-qgis-python.bat tests\check_live.py      # 实时 HTTP 端到端（自起临时服务）
.\run-qgis-python.bat tests\check_server_helper.py # 测试脚手架自检（端口护栏 / 端口补刀只补监听者）
.\run-qgis-python.bat tests\check_console.py     # Python 控制台自检（执行 / 隔离 / 权限 / 安全护栏）
.\run-qgis-python.bat tests\check_plugins.py     # 插件系统自检（装 / 启停 / 贡献物 / zip 安全）
.\run-qgis-python.bat tests\check_webui.py     # 网页控制台 UI 自检（无头 Edge + CDP）
.\run-qgis-python.bat tests\check_panels.py    # 面板与编辑接口自检
.\run-qgis-python.bat tests\check_webui_panels.py # 工具栏与面板 UI 自检（无头 Edge + CDP）
.\run-qgis-python.bat tests\check_webui_login.py  # 网页登录流程自检（注册/登录/退出）
.\run-qgis-python.bat tests\check_webui_files.py  # 网页文件浏览器自检（无头 Edge + CDP）
.\run-qgis-python.bat tests\check_admin_section.py # 管理员分区 + 共享超级账号 + 全员控制台（后端）
.\run-qgis-python.bat tests\check_webui_admin.py # 管理员分区 UI 自检（管理员看得到 / 普通用户看不到）
.\run-qgis-python.bat tests\check_web_merge.py  # 网页合版完整性（静态检查）
.\run-qgis-python.bat tests\check_tiles.py     # 瓦片坐标换算回归
.\run-qgis-python.bat tests\check_password_note.py # 管理员口令备忘的命名与位置
.\run-qgis-python.bat tests\check_processing_types.py # 算法参数的图层类型（喂错类型要给中文错）
.\run-qgis-python.bat tests\check_api_surface.py   # API 全量面：每个端点 + 权限矩阵 + 参数边界
.\run-qgis-python.bat tests\check_plugin_console_mcp.py # 插件 / 控制台 / MCP 深度自检
.\run-qgis-python.bat tests\check_ui_paths.py   # 网页界面「路径」全面扫描（真实浏览器，菜单/工具栏/面板逐个点）
.\run-qgis-python.bat tests\check_ai.py         # 网页 AI 助手（假模型服务：协议转换 / 权限 / 租户隔离 / key 不外泄）
.\run-qgis-python.bat tests\check_standalone.py # standalone 发行包（解包真起一次，验「没装 QGIS 也能跑」）
.\run-qgis-python.bat tests\check_web_js_syntax.py # 网页脚本语法（反引号成对 + node --check）
```

启动器的冒烟测试要用**系统 Python**（QGIS 的 Python 没打包 tkinter）：

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" tests\check_launcher.py
```

一次跑完全部脚本（会自动挑好解释器、逐个汇总结果）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_all_checks.ps1
```

`check_webui.py` 用**无头 Edge + CDP** 真点真跑，覆盖操作逻辑：菜单栏下拉、
添加图层对话框的分类切换、勾选图层加载、图层树复选框显隐、右键菜单、
单击地图识别要素、图层属性四个分页、符号系统改色、测量距离、缩放至图层、
工具箱跑算法并加到地图、拖拽上传。共 72 项。

`check_webui_panels.py` 专门验新加的工具栏与面板：工具栏五个分组与按钮、
右栏六个面板、属性表读数、选择高亮、书签增删、日志、处理历史，
以及页面有没有脚本报错。

`check_webui_login.py` 验真人登录那一层：注册 → 登录 → 顶栏显示本人 →
退出 → 再登录 → 密码错误要有明确提示。接口层（`check_auth.py`）过了
不代表网页上登得进去，这一层专门补这个缺口。

`check_web_merge.py` 是**静态检查**（不起服务、不开浏览器）：`web/index.html`
由多个会话并行改过，谁把文件整体重写、顺手弄丢了别人的那几块（图标声明、
内置底图、panels.js 挂载、添加图层对话框、枚举提交、状态栏），这个脚本立刻报红。
它同时查重复函数定义和「引用的静态文件是否都存在」。

> 用法上与其它脚本一致；它会自己拉起一个临时无头浏览器、用完即删。

`check_server_helper.py` 验**测试脚手架自己**（`tests/server_helper.py`）：脚手架是所有
自检的公共底座，它出问题时的症状是「整套自检莫名其妙全红」或「正式实例在回归期间消失」，
很难往脚手架上想。钉两条：临时服务**永远不占正式端口 8848**；端口补刀**只补「监听者」**——
netstat 里「连到这个端口的客户端」也占一行（ESTABLISHED），把它当监听者补刀
= 杀掉测试自己（httpx 客户端在本进程里）或无头浏览器。

`check_ai.py` 验网页里的 AI 助手。它在**测试进程里起一个假模型服务**
（标准库 `http.server`，不联网），按剧本回话，并把模型服务真实收到的请求记下来逐字段核对 ——
所以「OpenAI 兼容」和「Anthropic 原生」两种格式不是靠读代码断言的，是真的对过报文。
同时钉住三件容易出事的事：**权限**（免登录模式一律拒绝、普通用户能聊但改不了配置、
改服务端默认只有管理员）、**租户隔离**（B 问 AI，A 的私有数据集既列不出来也查不到）、
**Key 不外泄**（`GET /api/ai/config` 与聊天响应里都不能出现 key 明文）。

`check_standalone.py` 验**发行包本身**。「能打包」不等于「能跑」，所以它把
`release\BrowserQGIS-*-standalone.zip` 真的解开、真的起一次服务，钉住：
跑的是**包里的**运行时（`sys.executable` / `sys.prefix` 都在包里，`sys.path` 里
没有原安装位置的残留）、网页与静态资源正常、鉴权正常、示例数据在、AI 接口正常，
最后**用包自带的自检脚本**在解包后的目录里再跑一遍。
包没打出来时它**跳过**（不算红）—— 没打发行版的人不该被它拦住。

几个脚本会**自己拉起临时服务**（端口动态挑，永远跳过正式实例的 8848），
跑完自动关掉，不依赖「外面已经跑着的服务」。这些脚本跑在正式数据目录上，
所以脚本自己会把登记进去的测试数据清掉（上传的数据集、算法输出），
不留垃圾在共享空间里。

---

## 已知坑与注意事项

**瓦片铺屏的真实耗时（z=18 实测）**
网页端按层级出瓦片（不是整层渲成一张大图），实测：

| 场景 | 耗时 |
| --- | --- |
| 单块（冷） | 17~58 ms |
| 单块（缓存命中） | 3~6 ms |
| 一屏 8×6 = 48 块（全冷） | **607~692 ms** |
| 一屏 48 块（全缓存） | **195~207 ms** |
| 同时并发 12 块 | ~20 ms/块 |

要点：

* **服务端只有一条 QGIS 工作线程**（`core/executor.py`：所有 QGIS 调用串行到那一条），
  所以并发请求**不会**更快 —— 12 块并发仍是 ~20 ms/块，总量不变。
  要更快只能少渲染。
* 瓦片缓存是 `config.json` 的 `tile_cache_size`（默认 512 条），
  回到看过的区域是秒回。
* **瓦片被移出视野时，请求会被中止**（`AuthTileLayer` 用 `AbortController` +
  Leaflet 的 `tileunload` 事件）。这一条很要紧：瓦片是用 `fetch` 取 blob 的
  （`<img>` 发不了 Authorization 头），而**把 `<img>` 摘掉并不会让 fetch 停下来** ——
  服务端照样渲完那十几毫秒，回来时已经没人要了；快速连续缩放时这些浪费会
  排在真正要的那一屏前面。
* 如果单屏铺满还是嫌慢，方向是**在 QGIS 侧预渲染金字塔**（`QgsTile pyramid`），
  让服务端变成读文件而不是现渲 —— 那是架构级的改动，不是调参。

**AI 助手调工具时必须按调用者身份过滤**
MCP 是服务级（能看到所有空间），网页 AI 助手不是 —— 它是**用当前登录用户的身份**
去调同一套工具的。工具实现里凡是碰到「按 id 取东西」的地方，都必须把 `principal`
一路传下去：`registry.get_dataset(id, principal)`、`registry.resolve_ref(ref, principal)`、
`registry.datasets(principal)`，以及**书签的空间**（`scope` 是模型自己给的参数，
不校验的话它能写进别人的私有空间）。

漏掉任何一处，症状都一样：B 问 AI「A 的数据集是什么」，AI 就把 A 的数据读出来念给他听。
`check_ai.py` 里专门钉了这条（B 列不出 A 的数据集、直查 A 的数据集报「不存在」）。

**MCP SDK 推 schema 靠 `inspect.signature`，而 `__signature__` 不会被求值**
`mcp` 的 SDK 用 `inspect.signature(fn, eval_str=True)` 从函数签名推出参数的
JSON Schema。项目里开了 `from __future__ import annotations`，所以注解在运行时
是**字符串**——SDK 自己 eval 一下就没问题。但**一旦给函数塞了 `__signature__`**，
CPython 的 `inspect.signature` 会**直接返回它、不再 eval** —— 注解就还是字符串，
pydantic 拿字符串建模型会炸。

所以 `core/gis_tools.bind()` 里必须用 `inspect.signature(handler, eval_str=True)`
**先解析成真类型**再去掉 `ctx` 那个参数。返回注解同样要带上：少了它，
26 个返回 `str` 的工具会集体丢掉结构化输出 schema —— 那是 MCP 行为的悄悄变化。

**AI 助手的配置别放进数据根目录**
`data/` 下面 `shared/` 和 `users/` 里的**目录**会被注册中心扫成数据集，
`data/shared/` 里的 `.json` 还会被当成矢量图层。所以服务端那份 AI 配置
（`ai_config.json`）放在**用户库旁边**（`<db 所在目录>/`），不在数据根目录里。

**为什么不用 venv**
OSGeo4W 把 `python312.dll` 放在 `bin\`，Windows 加载器从那里绑定 DLL 后，CPython 会把
`sys.prefix` 解析到基础解释器，venv 被**静默降级**——装进去的 fastapi 直接不可见。
所以改用项目内 `deps\` + `PYTHONPATH` 注入。

**口令备忘文件要写在用户库旁边**
`data/admin_password.txt` 是「忘了密码」的兜底，里面写的必须是**当前真实口令**。
两个坑都会让它变成假信息：

* 改了口令没人管它 → 照着文件登一直 401，而账号状态和日志看着都正常，最难查。
  所以改密码时**直接把这个文件删掉**（管理员改密 API、`set-password` 两条路径都删）——
  管理员自己设过口令，盘上那份明文副本就没必要留了。
  不变式：文件要么记着当前有效口令，要么不存在。
* 它原本写在**数据根目录**下，但用户库路径（`db_path`）是独立配置的。
  自检脚本「共用生产数据目录 + 临时用户库」时，临时库初始化会把正式库的
  备忘文件覆盖成随机口令 —— 一次跑测试就把你锁在门外。
  现在它写在**用户库所在目录**，临时库只会写自己的目录。

还有三条同源的规矩：

* **口令从哪来，决定它要不要落盘**。配置里已经写了 `admin_password`（或环境变量
  `GISDS_ADMIN_PASSWORD`）→ 明文本来就在配置里，再往磁盘复制一份是净损失，
  所以**不写文件、连日志也不打**；没配、服务自己随机生成 → 那台机器上只有
  备忘文件和启动日志两处能找到它，**两处都要留**（多机部署就靠它们）。
* **改密之后，备忘同步更新**。不变式是：**文件里记着的永远是当前有效口令** ——
  绝不会躺着一个失效的旧口令。代价是明文口令在盘上留得更久，所以留了
  `password_note` 开关给「这台机器不能存明文」的场景用。
* **配置里的口令过期了要主动告警**。管理员后来改过密码，config.json 里那份就失效了，
  而它看着完全正常 —— 照着配置登会一直 401。服务启动时会比对一次，对不上就在日志里
  说「以数据为准，别照着配置登」。

`password_note` 开关（config.json，默认 true；也可用 `GISDS_PASSWORD_NOTE`）可以把
落盘整个关掉 —— 机器要接到不可信网络时用。关掉之后随机生成的初始口令**只在启动日志里**
留一次。

**真彩色遥感影像不能拿单波段假彩色去渲**

遥感照片、Sentinel-2 这类**多波段**影像，QGIS 桌面版会配一个「红/绿/蓝 = 1/2/3 波段」的
多波段彩色渲染器。我们原来在 `build_raster_style()` 里**无条件**把栅格渲染器换成
「单波段 + viridis 渐变」—— 只用第 1 波段出图，绿色通道直接被压没
（实测平均 RGB 从 `[28.6, 33.0, 37.4]` 变成 `[50.8, 12.0, 71.0]`），
整片**发紫**（渐变第一档色 `#440154` 本身就是深紫），跟 QGIS 桌面版完全不一样。

现在：**真彩色影像保留 QGIS 自己的渲染方式**（判据是「QGIS 给它配了什么渲染器」，
不是自己数波段 —— QGIS 还认得出彩色表、带 alpha 的波段）。
网页端的图层清单会带一个 `truecolor` 标记，符号系统面板的第一项是「真彩色（QGIS 默认）」。
用户**主动**选单波段渐变时仍然照旧（那是明确的选择）。

**栅格图层必须按层级出瓦片，不能「整层渲成一张大图」**

原来网页端的栅格图层是把**整层范围**一次性渲成 1400px 的大图，再用 `imageOverlay`
铺上去。放大时这张图被反复拉伸 —— 1 个图像像素对应 6 个源像素（Sentinel-2 源尺寸
8350×6352），在 z=16 那一级实际是拿 **11×11** 个源像素放大成 256×256，
放大 23 倍，糊成一片色块。

现在改成**按层级出 XYZ 瓦片**（`/api/tiles/{ds}/{layer}/{z}/{x}/{y}.png`，
每块 256×256，原生分辨率）。实测瓦片接口 z=12/14/16/18 分别 116/31/19/18 毫秒，
不需要额外的金字塔也能扛住。

两个配套的坑：

* **瓦片要带令牌，但 `<img>` 发不了请求头** → 前端用 `AuthTileLayer`（fetch 拿 blob
  再塞给 img），令牌走 Authorization 头，**不进 URL、不进浏览器历史、不进访问日志**。
  服务端那条 `?token=` 仍然保留，给「自己在别处挂 XYZ 瓦片」用。
* **缓存键必须带上样式**：否则自定义配色的请求会命中默认样式的缓存，
  界面上改了配色却还是老样子。`render_tile()` 的键现在含 `colors` 与 `mode`。

**图层的原生分辨率是物理上限**：Sentinel-2 是 10 m/px，z=14 正好是原生，
再往里放大就是数据本身不够细了 —— 桌面版 QGIS 也一样，
需要更细得用 30 cm 或 10 cm 的高分影像。

由 `tests/check_core.py` 的「真彩色遥感影像不能被渲成一片紫」一节钉住（5 项）。

**断言总数要钉死**

每支自检脚本都会在结尾断言一次「实际跑了多少条」（`EXPECTED_CHECKS`）。
钉它是因为：**任何一条断言被条件跳过，汇总数就会跟着变** —— 那样报告里
照样显示「N 通过 / 0 失败」，**没人看得出少了哪几条**。断言空过比断言失败更难查。

所以脚本里**宁可让断言失败，也不让断言被跳过**：条件分支一律改成两分支同条数
（缺条件就判失败），「找不到浏览器 / 服务未就绪」这类提前返回也统一走汇总函数、
也要过钉子。

> **可观察的行为变化**：机器上没装 Edge/Chrome 时，`check_webui*.py` 那 4 支
> 由「静默跳过、报成功」变成「报失败」—— 这正是钉子要干的事。
> 另有一处**真分支**：`check_password_note.py` 的条数取决于正式库旁边的口令备忘
> 存不存在（9 或 8），钉子按分支取两个期望值。

**`.bat` / `.ps1` 必须纯 ASCII**
cmd.exe 按系统 ANSI 码页（中文 Windows 是 GBK）读批处理文件，UTF-8 中文注释会把
解析器搞坏。脚本里的注释一律英文。
（`tests/run_all_checks.ps1` 也算 —— 它由 PowerShell 5.1 执行，无 BOM 的 UTF-8 同样
按 ANSI 解析，中文注释里的多字节序列能把后面的引号一起吃掉，脚本表面跑完了、
输出却是乱码。改回纯英文就好。）

**跑自检：退出码可能骗你**
每个自检脚本收尾都是「flush 一下，然后 `os._exit(code)`」。如果**标准句柄无效**
（某些启动方式下 `sys.stdout` 会直接是 `None`），`sys.stdout.flush()` 抛
`AttributeError: 'NoneType' object has no attribute 'flush'` ——
异常一抛，`os._exit(code)` 根本走不到，进程以 **1** 退出：
**断言其实全过，整套却显示全红**，很容易被带偏成「代码坏了」。
现在 23 个脚本的收尾都加了防护（`for _s in (sys.stdout, sys.stderr): if _s: _s.flush()`），
退出码只反映断言结果。
另外：**跑整套自检建议用 shell 重定向**——
`powershell -ExecutionPolicy Bypass -File tests\run_all_checks.ps1 > out.txt 2>&1`，
子进程拿到的是真文件句柄，最省事。

**别在 import 时往根 logger 挂 handler**
`logging.basicConfig()` 在根 logger 已经有 handler 时会**直接返回**（不设级别、不加 handler）。
CLI 的顺序是「先 import 应用模块，再调 `basicConfig()`」——所以任何在 import 期就
`root.addHandler(...)` 的模块，都会让根级别停在默认的 WARNING，
服务自己的 INFO 日志一条都出不来（但不会报错，很难发现）。
本项目的日志缓冲（`core/logbuf.py`）因此**不在 import 时安装**，
而是由应用启动与每次读日志时各自 `install()`。

**PyQGIS 不是线程安全的**
`QgsApplication`、Processing 的 `QgsProcessingRegistry`、各 provider、图层对象
全是 QObject，有线程亲和性。在 A 线程建 registry、再到 B 线程往里挂 provider，
会触发 `QObject: Cannot create children for a parent that is in a different thread`，
进而让整个进程段错误（0xC0000005）。

所以本项目让**所有 QGIS 对象的创建与使用都发生在同一条线程**上：
`core/executor.py` 里的唯一工作线程。`QgsApplication` 本身也在那条线程上初始化
（`app.py` 的 lifespan 里 `await run_qgis(qgis_env.init_qgis)`），MCP Server 与 CLI
同理。`qgis_env.ensure_qgis_thread()` 会在跑错线程时立刻抛错，而不是等到段错误。

`registry.scan()` / `registry.forget()` 会创建、释放图层对象，因此**只能**通过
`run_qgis` / `run_qgis_sync` 调用；HTTP 层用 `await run_qgis(...)`，
轮询作业状态不占用这条线程。

**Windows 退出时的 0xC0000005**
PyQGIS 在 Windows 上退出进程时可能触发访问违例，CLI 里统一用 `os._exit(0)` 绕过。

**枚举参数**
`QgsProcessingParameterEnum` 有两种形态，取值方式完全不同：

* **下标型**（`usesStaticStrings() == False`，占绝大多数）：底层存整数下标。
  例如 `native:buffer` 的 `END_CAP_STYLE`：0=圆角(Round) / 1=扁平(Flat) / 2=方角(Square)。
* **字符串型**（`usesStaticStrings() == True`）：底层存选项文本本身。
  例如 `native:dxfexport` 的 `ENCODING`：`'CP936'`。

装了 QGIS 官方中文翻译包之后，**枚举项在算法构造时就被 `tr()` 定死了**，
`options()` 返回的已经是中文（`['圆角','扁平','方角']`），英文原名运行时取不回来。
所以接口的契约是：

* `options` —— 显示名（当前是中文）；
* `option_values` —— **真正要提交给 QGIS 的取值**（下标型是整数，字符串型是文本）；
* `default_label` —— 默认值的可读形式。

提交参数时三种写法都认：**整数下标**（推荐，与语言无关）、**接口给出的显示名**、
**QGIS 英文原名**（`"Flat"`、`"Miter"` 等，内核用 `i18n.ENUM_OPTION_ALIASES` 翻译）。
翻译逻辑在 `processing.enum_value_map` / `processing._coerce_enum`。

**GDAL 锁文件**
Windows 上 GDAL 会锁住正在被 QGIS 打开的文件，直接删会报 `WinError 32`。
删除上传数据集时先写 `.deleted` 标记并从索引里摘掉，文件在**下一次目录扫描**时
由 `Workspace.purge()` 真正清除。

**点图层的点命中**
`QgsGeometry::intersects()` 没有 `QgsPointXY` 重载，直接传点会让**点几何**图层
抛异常。`vector_hit()` 里统一用 `QgsGeometry.fromPointXY()` 构造点几何再判定。

**没有坐标系信息的 TIFF 导入后会「显示不出来」**
有些 GeoTIFF 是**不带 CRS 定义**的（从别的工具导出的、或者投影信息被剥掉了）。
这种图层在 QGIS 里是**画不出来的**：渲染要把图层变换到目标 CRS（3857/4326），
而图层自己的 CRS 无效 → 变换做不了 → **整层被跳过** → 出来一张合法的全白图。
表现是「上传成功、图层面板里也有、地图上什么都看不见」—— 最容易被误判成「文件坏了」。

现在注册图层时（`core/registry.py` 的 `_ensure_crs`）发现 CRS 无效就**按 EPSG:4326 解释**
（QGIS 桌面版对未知 CRS 的图层也是按工程 CRS 处理的），并在上传结果里带一条告警：
「图层 xxx.tif 没有坐标系信息，已按 EPSG:4326 解释」—— 界面状态栏会显示它。
栅格和矢量都走这条。

**UI 自检用无头浏览器**
内置 FilePanel 浏览器在多标签页下会把点击/按键送到别的标签页，验证操作逻辑不可靠。
所以 `tests/check_webui.py` 走 **Edge headless + CDP**：真鼠标事件、真命中测试，
不受标签页漂移影响。

**出图**
QGIS 3.44 headless 下 `QgsMapRendererSequentialJob` 会输出全黑图，
本项目用 `QgsMapRendererParallelJob` + 阻塞等待。

出图的**长宽比必须与请求范围一致**——否则 QGIS 会把范围扩成画布比例，
图边上就留出白边。所以 `render_png()` 在没给 height 时，按范围在目标坐标系里的
跨度比算高度；网页侧统一用 `dst_crs=EPSG:3857` 请求，与 Leaflet 底图同一投影，
叠加图不会有投影畸变。

**瓦片坐标换算（改之前务必看这段）**
`tile_bbox_3857()` 算 EPSG:3857 范围时，y 必须用**球面墨卡托**：

```
y = R · ln(tan(π/4 + φ/2))        # 对
y = R · radians(φ)                 # 错：等距圆柱近似，中纬度能差好几百公里
```

用错的话：底图瓦片整体偏移、z>0 的某些瓦片渲成全白、z0 全球图被纵向压扁、
跨层级拼接对不齐、和在线 OSM 图层叠也错位。

另外 `render_png()` 的 `bbox` **必须声明自己的坐标系**（`bbox_crs` 参数，默认
EPSG:4326）。以前它硬编码假定传进来的 bbox 是 4326 并会转到 `dst_crs`，而
`tile_bbox_3857()` 给的已经是 3857 —— 于是被二次转换，范围飞到天边，出的图是空的。
现在 `render_tile()` / `render_basemap_tile()` 显式传 `bbox_crs="EPSG:3857"`。

`tests/check_tiles.py` 把这两条钉住了：逐瓦片对比 QGIS 官方变换要求**偏差 0.0 m**，
另外还验跨层级对齐、栅格瓦片有内容/空白区无内容。

**网页端的 fetch 兜底**
局域网/某些浏览器环境偶发「服务端已返回 200、浏览器端 fetch 却不落定」。
网页端的 `api()` 统一带 20 秒超时 + 一次自动重试，启动阶段的数据源/图层/算法清单
并行拉取（`Promise.allSettled`），任何一份失败都不会拖死整页。

**离线优先**
在线底图默认关闭，避免工具在没有外网的局域网里表现异常。

---

## 许可与图标来源

**本项目以 GPL v2 或更高版本发布**（项目根目录的 `LICENSE` 是 GPL v2 全文）。

这不是选择，是**依赖决定的**：本项目建立在 **PyQGIS** 之上，
`qgis/core/__init__.py` 的版权头就是 GPL v2+。用了 GPL 的库，
衍生作品就得按 GPL 分发。

**界面图标取自 QGIS 官方资源**：

| 项 | 说明 |
| --- | --- |
| 来源 | QGIS 官方仓库 `release-3_44` 分支，`images/themes/default/` |
| 对应版本 | QGIS 3.44 LTR（本机 3.44.14-Solothurn） |
| 选取方式 | 按 QGIS `src/app/qgisapp.cpp` 里 `createActions()` 的 `setIcon(getThemeIcon("/xxx.svg"))` **实际对应关系**一一照搬，没有重画、没有近似 |
| 完整对照表 | `web/icons/qgis/ACTION-ICONS.txt`（156 条 action → 图标） |
| 来源与许可说明 | `web/icons/qgis/LICENSE-QGIS.txt` |

**必须用 `<img>` 引用，不能内联**：QGIS 图标是 24×24 的**填充式多灰阶**
（#ccc/#999/#000），而页面里自绘的 `svg()` 硬编码 `viewBox="0 0 16 16"` +
`fill="none"` + `stroke="currentColor"` —— 内联进去会被压成线框、彻底变形。
尺寸用内联 style（CSS 的 `.ic{width:16px}` 优先级高于 HTML 的 width 属性）。

**图标不是许可门槛**：本项目已因依赖 PyQGIS 而必须按 GPL v2+ 分发（见上）。
切回自绘只关乎**保真度与工作量**（视觉改成自绘风格、少一批外部资源），
改动集中在 `web/panels.js` 的 `QGIS_ICON` 一张表 —— 但它**不会**让项目变成闭源。

**捆绑的第三方代码**（都带各自的许可原文）：

| 组件 | 版本 | 许可 | 位置 |
| --- | --- | --- | --- |
| Leaflet | 1.9.4 | BSD-2-Clause | `web/vendor/`（`LICENSE-leaflet.txt`） |
| QGIS 官方图标 | 3.44 LTR | Apache-2.0 | `web/icons/qgis/`（`LICENSE-QGIS.txt`） |
| QGIS 官方底图数据 | Natural Earth | 公共领域 | `data/shared/basemap/` |

`deps/` 里是本项目自己的 Web 层依赖（FastAPI / uvicorn / MCP SDK / httpx 等），
**不进版本库**（`setup.ps1` 装），但**发行包里会带上**，好让收件人免安装。

---

## 与其它系统的关系

这是一个**独立的通用工具**，不绑定任何行业场景。示例数据是中性合成数据
（分区 / 曲面 / 范围面），换成任何自己的数据都能直接用。
