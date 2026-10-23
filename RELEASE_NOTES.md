# Browser QGIS 1.1.0 —— 发布说明

基于 PyQGIS 内核的通用 GIS 数据服务器。一套内核，两套外壳：
**REST API** 给网页和业务系统消费，**MCP Server** 给 Agent 当工具用，
**网页内嵌的 AI 助手**让不写代码的人也能把 GIS 当成对话来用。

---

## 一分钟上手

双击 `start.bat`（或 `launcher.bat` 走图形启动器），浏览器打开
<http://127.0.0.1:8848/>。

首次启动会：

1. 初始化 PyQGIS 内核（要十几秒，`/healthz` 期间是 200，`/readyz` 要等 `ready`）
2. 建一个管理员账号 `admin` —— **随机口令**，写在 `data\admin_password.txt` 里
   （`config.json` 里配了 `admin_password` 就用配置那份，那份不会写进备忘文件）
3. 扫一遍 `data\shared\` 与 `data\users\`，把里面的目录登记成数据集

> 口令备忘里记的**永远是当前有效口令**。登录后请尽快在
> 「启动器 → 用户 → 改密码」里改掉。

## 运行前提

* **QGIS 3.44.14**，默认装在 `C:\Program Files\QGIS 3.44.14`
  （换路径改 `run-qgis-python.bat` 里的 `QGIS_ROOT`）
* Python 依赖**已经打包在 `deps\` 里**，开箱即用，不需要 venv、不污染 QGIS 自带的 Python
* 不需要联网，不需要装任何东西

### 或者：不用装 QGIS

**standalone 版**把 QGIS 运行时也一起打包了（`qgis\` 目录），收件人本机
**不需要装 QGIS**，解压即用：

```
BrowserQGIS-1.1.0-standalone/
├── qgis/          ← 捆绑的 QGIS 运行时（可搬家）
├── deps/  gisds/  web/  ...
```

启动器会自动优先用旁边的 `qgis\`，不用改任何配置。

裁掉了「本来就没在工作」的部分：GRASS（provider 算法数是 0）、SAGA（没注册）、
Qt5 的 QML 模块（无头服务不加载）、40 多种语言的翻译。
**算法数仍是 440**，`share/proj/proj.db` 等坐标转换的硬骨头一个没少。

> standalone 版比普通版大得多（运行时 1,425 MB，压缩后仍然不小）。
> 目标机器上**已经装了 QGIS** 的话，用普通版（19 MB）就行。

## 本版新增：网页里的 AI 助手

右栏多了一个「AI 助手」标签页，像用 zcode 连 MCP 插件那样**对话即干活**，
但窗口就在网页里，大模型通过**在界面上填 API** 接进来。

* **两种协议都支持**：OpenAI 兼容（`/chat/completions`）与 Anthropic 原生（`/messages`）
* **API Key 两个来源**：管理员配的服务端默认（存在 `data\ai_config.json`）
  或用户自己填的临时配置（只存在本机浏览器里），**浏览器的优先**
* **Key 永不下发到前端** —— 接口只回 `has_key`
* **所有登录用户都能用**（产品定死，不给配置项）；免登录模式的匿名访客一律拒绝
* **写操作默认关闭** —— 改要素、算字段、建物签这类工具默认不交给模型，
  只有管理员在服务端打开 `allow_write` 才有
* AI 调工具时是**按登录用户身份**调的：B 问 AI，A 的私有数据集既列不出来也查不到

上手三步：登录 → 点「模型设置」→ 选协议、填 Base URL / 模型名 / API Key。

> 换接口地址（或换协议）时必须同时自带 Key —— 否则服务端会把自己的 Key
> 送到你指定的地址去，那等于把全机凭据交出去。

## 与 MCP Server 的关系

网页 AI 和 MCP Server **共用同一份 27 个工具的定义**（`gisds/core/gis_tools.py`），
所以工具名、参数、说明不会各写一份慢慢漂移。差别只有一处 —— 身份：

| | MCP Server | 网页 AI 助手 |
| --- | --- | --- |
| 工具 | 27 个 | 同样 27 个（同一份定义） |
| 身份 | 服务级：能看到所有空间 | 按登录用户过滤 |
| 写操作 | 有 | 默认没有 |
| 鉴权 | 不走 HTTP 鉴权 | 走会话令牌 |

给 Agent（OpenClaw「龙虾」等）接：

```json
{
  "mcpServers": {
    "gisds": {
      "command": "cmd.exe",
      "args": ["/c", "<解压目录>\\mcp.bat"],
      "env": { "GISDS_DATA_ROOT": "<解压目录>\\data" }
    }
  }
}
```

> **传输方式决定暴露面**。MCP 是服务级工具：不走鉴权、能看到所有空间。
> `stdio`（默认）只有本机能起这个进程；`streamable-http` / `sse` 等于把
> 「能看到所有空间、能改所有数据」开给整个局域网 —— 绑到 `0.0.0.0` 之前请先想清楚。

## 数据空间

* `data\shared\` —— 共享空间，所有登录用户都能读，也都能往里传
* `data\users\<用户ID>\` —— 该用户的私有空间，只有本人能看见和改

**一个数据集 = 一个目录**（里面有 `metadata.json` 和图层文件）。
管理网页上的「文件浏览器」可以上传、下载、删除。

发行包里带了 `data\shared\example\`（一份合成的示例数据）和
`data\shared\basemap\`（内置离线底图），拿到就能试。
**发行包不含任何用户数据、账号库和口令**。

## 自检

发行包里带了自检脚本（`tests\`），可以在自己的机器上验证这份代码是好的：

```powershell
.\run-qgis-python.bat tests\check_ai.py     # AI 助手（假模型服务对报文）
.\run-qgis-python.bat tests\check_mcp.py     # MCP 工具清单与行为
.\run-qgis-python.bat tests\check_api.py     # REST API
powershell -NoProfile -ExecutionPolicy Bypass -File tests\run_all_checks.ps1
```

全量回归 28 支脚本、1700 条断言。**目前有 5 条红的**（`check_ui_paths.py`）：
「添加要素」点了没反应（2 条）、刷新后现场丢失（3 条）—— 这是本版已知的
功能缺口，不影响其它部分。

`check_standalone.py` 专门验 standalone 包：把 zip 真的解开、真的起一次服务，
确认跑的是**包里的**运行时（不是本机装的 QGIS），然后用包自带的自检脚本再跑一遍。
没打发行版时它会跳过。

## 已知功能缺口

* 「添加要素」按钮点了没反应（进入不了采集模式）
* 刷新页面后，已加载的图层、选中的图层、打开的标签页不保留

## 许可

**GPL v2 或更高版本**（`LICENSE` 是 GPL v2 全文）。因为依赖 PyQGIS，
本项目必须按 GPL 分发 —— 衍生作品也得按 GPL 来。

图标来自 QGIS 官方图标集（Apache-2.0），见 README「许可与图标来源」。
