# Web Agent ZM

Windows 本机网页自动化工作台。通过自然语言逐步指挥 Playwright 浏览器，自动登录内部系统、填写查询条件并导出文件。

## 当前交付：阶段 1

- 专用浏览器启动、账号密码自动登录、会话复用。
- 兼容 OpenAI Chat Completions 的工具调用，一条指令执行后等待下一条。
- 网页内实时画面、文字反馈和模型请求次数。
- 批次号等参数文件命名、下载完成且非空校验、网页下载入口。
- 浏览器执行互斥；指令忙碌时拒绝第二条指令或关闭浏览器。
- 本地模拟内部系统，支持查询、暂无数据和 CSV 导出。

**尚未实现**：标准流程保存/回放、批量表格、暂停/继续/停止、LLM 异常恢复、持久化历史。这些按后续阶段单独实施，不包含在当前版本。

## 安装与启动（PowerShell）

需要 Python 3.11+、Node.js 22+、Git。已在 Windows / Python 3.14 / Node.js 24 验证。

```powershell
git clone -b phase1-minimal-agent https://github.com/Sora-Ava/web_agent_ZM.git
cd web_agent_ZM
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
.\.venv\Scripts\python.exe -m pip install -e ./backend --no-deps
.\.venv\Scripts\python.exe -m playwright install chromium
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
Copy-Item .env.example .env
```

编辑 `.env` 中的模型配置后启动；已有 `.env` 时不要重复覆盖：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

打开 <http://127.0.0.1:8000>。终端按 Ctrl+C 退出。无需额外打开浏览器窗口，自动化浏览器默认无界面运行，画面显示在工作台；`HEADLESS=false` 可以显示独立浏览器窗口，但仍应由指令操作。

## 先用模拟系统验收

默认网站与账号密码已经指向本地演示系统。点击“启动并登录”，逐条发送：

1. `点击“生产查询”按钮`
2. `输入生产批次号 PC001，物料层级选择 2`
3. `点击查询，等待结果加载完成`
4. `导出查询结果，文件名包含 PC001 和层级 2`

导出文件位于仓库的 `downloads/`，也可在网页下载。批次号 `NONE` 可验证“暂无数据”。当前阶段由指令决定后续操作，暂无标准流程自动分支。

启动和登录不依赖 LLM；自然语言操作需要配置模型。仓库不含真实 API 密钥，不提供假装真实模型已接通的演示模式。自动化测试中的模型替身仅用于验证执行链路。

## 模型配置

```dotenv
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY="your-key"
LLM_MODEL=your-tool-capable-model
```

客户端向 `LLM_BASE_URL + /chat/completions` 发请求。不要把完整 `/chat/completions` 路径写进 base URL。服务及模型必须支持 `tools`、`tool_calls`、工具结果消息及 JSON Schema（包括 `$defs` 引用）；无法使用工具时会明确报错。

可先独立检查模型配置，不启动浏览器：

```powershell
.\.venv\Scripts\python.exe scripts/check_llm.py
.\.venv\Scripts\python.exe scripts/check_llm.py --tools
```

脚本只读取项目 `.env` 的 LLM 配置，分别检查文本响应与最小工具协议，不输出 API 密钥。

模型每条指令最多 8 轮，默认每次请求超时 60 秒，不自动重试 API 请求。动作失败直接反馈，首版不会自动恢复。模型产生的总结仅作为解释，程序记录的操作/下载结果才是成功证据。

## 接入真实系统

只在本地 `.env` 配置，不把凭据发到聊天或提交仓库：

| 配置 | 含义 |
| --- | --- |
| `SITE_URL` | 登录页面完整 URL |
| `SITE_USERNAME` / `SITE_PASSWORD` | 账号、密码 |
| `LOGIN_USERNAME_SELECTOR` / `LOGIN_PASSWORD_SELECTOR` | 登录输入框的 CSS 定位 |
| `LOGIN_SUBMIT_SELECTOR` | 登录按钮 CSS 定位 |
| `LOGGED_IN_SELECTOR` | 登录后稳定可见的元素，用于判断成功 |
| `LOADING_SELECTOR` | 查询加载遮罩 CSS 定位 |
| `RESULT_SELECTOR` | 查询结果容器 CSS 定位，查询后内部应发生 DOM 更新 |
| `DOWNLOAD_DIR` | 可选绝对导出目录，默认 `<repo>/downloads` |

带空格或 `#` 等字符的配置值请使用引号。修改配置后重启服务。首版操作主页面，尚不支持 iframe 内元素或多标签切换，跨源导航被拒绝；真实系统如使用这些结构，需要先适配。查询等待要求加载元素与结果容器存在，观察到加载出现再消失，且结果容器内容发生 DOM 更新；查询全程无加载提示或仅隐藏/显示相同结果的站点需要调整等待实现。真实站点的定位配置必须实际验证后使用，示例选择器并非通用选择器。

登录密码由程序直接填写，不传给模型。页面提取不包含密码/隐藏字段、输入框当前值、脚本或隐藏文本；模型请求全文再次过滤已配置凭据。只发送页面文本和元素信息，不发送截图。必要的业务页面文本仍会发送到你配置的 LLM 服务。

实时画面经本机 WebSocket 传给工作台，不上传到模型或外部服务。应用仅绑定 `127.0.0.1`，不应部署到公网。`.env`、导出文件和测试截图均被 Git 忽略。

## 数据与限制

- 当前启动期间的指令/文件登记在内存，刷新页面可重新查看；重启后不保留历史索引，磁盘文件仍保留。
- 同名导出加唯一标识，不覆盖文件。首版仅检查文件非空，不验证 Excel/CSV 业务内容。
- 已使用真实模型在内置演示系统验证完整查询导出及无数据不导出；尚未接入用户的真实内部管理系统。

## 开发与测试

```powershell
# 后端（安装 editable 包后）；Windows 不使用 uvicorn --reload
.\.venv\Scripts\python.exe -m app.launcher
# 另一个终端
npm.cmd --prefix frontend run dev
# 构建后运行完整测试（包括网页端端到端测试）
npm.cmd --prefix frontend run build
.\.venv\Scripts\python.exe -m pytest -q
```

开发前端地址为 `http://127.0.0.1:5173`；Vite 代理本机 API 和 WebSocket。测试启动真实无界面 Chromium，模型请求使用确定性替身，不消耗真实模型额度。网页验收截图输出到 `test-results/workbench.png`。

测试覆盖登录、重名拒绝、字段信息过滤、延迟遮罩、无数据、非空下载、文件唯一性、工具参数校验、调用上限、执行互斥、API 来源检查、实时画面、网页下载和刷新恢复。GitHub Actions 在 Windows 运行相同测试。

设计与路线图见 [设计文档](docs/superpowers/specs/2026-10-14-web-automation-agent-design.md) 和 [分阶段计划](docs/superpowers/plans/2026-10-14-web-automation-agent.md)。
