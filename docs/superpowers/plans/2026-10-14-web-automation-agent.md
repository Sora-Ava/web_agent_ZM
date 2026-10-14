# 网页自动化 Agent 分阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本文只有阶段 1 是可执行任务计划；后续阶段为路线图，各自在启动前补充任务级计划。不要一次执行所有阶段。

**Goal:** 先交付可观看、可逐步指挥并导出文件的最小系统，再逐步实现低成本标准流程和批量自动化。

**Architecture:** React 前端连接 FastAPI 本地后端，后端独占一个 Playwright 浏览器会话。模型通过结构化工具调用发出操作，程序校验并执行；后续标准执行器复用同一动作接口。

**Tech Stack:** React、TypeScript、Vite、Python、FastAPI、Playwright、Pydantic；SQLite 在阶段 2 引入。

**Spec:** `docs/superpowers/specs/2026-10-14-web-automation-agent-design.md`

## Global Constraints

- Windows 本机单用户，一个内部管理系统，串行执行。
- 模型仅接收网页文本和元素信息，不接收截图；密码与密钥不进入模型请求、日志或流程定义。
- 专用浏览器由工具启动，程序读取 `.env` 自动登录。
- 画面只读观看，所有业务操作由自然语言指挥。
- 导出成功以下载完成且文件非空为准，文件名不得覆盖已有文件。
- 每阶段单独验收；通过后停下，不自动进入下一阶段。

## Review Focus

1. 页面存在重名按钮：必须返回歧义，不点击任意匹配项（任务 2）。
2. 登录后输入框含凭据：提取及错误日志不能将凭据带入模型（任务 2、3）。
3. 双击发送或刷新网页：不能并发操作浏览器，断线后可以恢复查看状态（任务 1、4）。
4. 导出按钮触发延迟下载或空文件：等待真实下载，空文件不能标记成功（任务 2）。
5. 查询遮罩延迟出现：不能把点击后的首次“不可见”当成查询已完成（任务 2）。

## 分阶段路线图

| 阶段 | 可交付能力 | 验收关口 |
| --- | --- | --- |
| 1 最小闭环 | 启动网页及浏览器、自动登录、逐步自然语言操作、只读实时画面、单次文件导出 | 模拟系统完整走通，并尝试真实系统的一次查询导出 |
| 2 标准流程 | 记录动作、参数识别和用户确认、版本保存、单次确定性回放、无数据分支 | 改用另一组参数回放成功，正常回放 LLM 调用数为零 |
| 3 批量任务 | 参数表格粘贴、串行队列、暂停/继续/停止、历史记录及文件管理 | 混合成功/无数据/失败数据执行，单行失败后继续 |
| 4 异常恢复 | 登录过期恢复、每失败步骤最多两次 LLM 恢复、修复审核与新版本 | 元素变化恢复成功，原版本不变，确认后生成新版本 |
| 5 真实系统加固 | 重启中断、重连、全局故障处理、Windows 启动体验与完整验收 | 真实业务多组数据运行、控制操作及异常场景符合设计 |

阶段 1 只证明交互到导出的链路，不承诺流程保存、批量任务或自动修复。模型工具与动作结果接口从开始保持结构化，供后续阶段复用，不提前实现通用流程引擎。

## 阶段 1 文件与接口约定

后端目录 `backend/app/`：

- `main.py`：应用入口与生命周期。
- `config.py`：读取配置，敏感配置使用 SecretStr。
- `contracts.py`：动作、定位、结果及会话状态模型。
- `sessions.py`：单浏览器生命周期、执行互斥和状态快照。
- `api.py`：HTTP 与 WebSocket 路由。
- `browser.py`：Playwright 页面观察和动作执行。
- `downloads.py`：下载保存、命名及登记。
- `agent.py`：兼容 API 调用与工具循环。
- `events.py`：状态、文字事件和画面广播。

前端目录 `frontend/src/`：`App.tsx`、`api.ts`、`components/CommandPanel.tsx`、`BrowserView.tsx`、`FileList.tsx`。测试放在 `backend/tests/`，浏览器模拟站点放在 `fixtures/internal-site/`。

核心数据类型在 `contracts.py` 定义：

- `LocatorSpec(kind: Literal['role','label','text','css'], value: str, role: str | None)`。
- `BrowserAction(kind: Literal['navigate','click','fill','select','wait','download'], target: LocatorSpec | None, value: str | None)`。
- `ActionResult(status: Literal['success','failed','ambiguous'], message: str, file_id: str | None)`。
- `PageObservation(url: str, text: str, elements: list[dict])`；不包含截图或输入框当前值。
- `SessionState(status: Literal['idle','starting','ready','running','failed','closed'], current_url: str | None)`。

登录不通过模型填入密码；`BrowserSession.login()` 使用独立配置定位并直接填写。原始文本不能以任意脚本形式执行。阶段 1 会话和文件登记暂存在内存，重启不保留历史，文件本身保留。

## Task 1：可启动的本地网页与单浏览器会话

**Files:** 创建 `backend/pyproject.toml`、`backend/app/{main,config,contracts,sessions,api,events}.py`、`backend/tests/test_sessions.py`、`frontend/package.json`、`frontend/src/{App.tsx,api.ts}`、`.env.example`、`.gitignore`、`README.md`、`scripts/start.ps1`。

**Interfaces:** `BrowserSession.start() -> SessionState`、`close() -> None`、`state() -> SessionState`、`execute(action: BrowserAction) -> ActionResult`；`execute` 在任务 2 实现。HTTP：`GET /api/health`、`POST /api/session/start`、`GET /api/session`、`DELETE /api/session`。服务绑定 `127.0.0.1`，开发 CORS 仅允许本地前端。

- [ ] 检查目标仓库和工作区，读取适用 AGENTS.md；建立正确仓库工作副本，保留已确认文档，不覆盖已有文件。
- [ ] 写 `test_start_idempotent` 与 `test_close_idempotent`：重复启动只创建一个浏览器，重复关闭不报错。
- [ ] 运行 `python -m pytest backend/tests/test_sessions.py -q`，确认新行为测试失败。
- [ ] 建立最小依赖和应用入口，实现会话启动/关闭接口；提供从工作区根目录启动的 Windows 脚本及环境配置模板。
- [ ] 实现首页启动/关闭按钮和会话状态，暂不添加流程列表占位功能。
- [ ] 运行上述测试，期望通过；运行 `npm --prefix frontend run build`，期望构建成功；手动从 Windows 启动，确认浏览器打开和关闭。
- [ ] 提交本任务涉及文件，提交说明 `feat: bootstrap local app and browser session`。

## Task 2：自动登录、页面观察与文件导出

**Files:** 创建 `backend/app/{browser,downloads}.py`、`backend/tests/test_browser.py`、`backend/tests/test_downloads.py`、`fixtures/internal-site/app.py`；修改 `config.py`、`sessions.py`、`contracts.py`。

**Interfaces:** `BrowserSession.login() -> ActionResult`、`observe() -> PageObservation`、`execute(action: BrowserAction) -> ActionResult`；`DownloadStore.save(download, name_parts: list[str]) -> str` 返回文件 ID。登录 URL、用户名/密码及其字段和提交按钮的定位从环境配置读取，真实系统首次对接时校准，不假设其 DOM。

- [ ] 建立模拟站点：账号密码登录、业务导航、批次号及层级查询、延迟遮罩、暂无数据、重名按钮、下载及空文件下载。
- [ ] 写浏览器测试：登录后到达业务页；观察结果无密码/字段值；重名定位返回 `ambiguous` 且无点击；延迟遮罩查询等待后返回目标结果。
- [ ] 写下载测试：延迟下载成功；零字节下载失败；相同参数二次下载得到不同路径；文件名含参数并过滤 Windows 非法字符。
- [ ] 运行 `python -m pytest backend/tests/test_browser.py backend/tests/test_downloads.py -q`，确认失败。
- [ ] 实现动作适配器，采用唯一定位、有限等待，下载监听在点击前注册；查询等待结合遮罩出现/消失及结果状态变化，避免仅靠固定 sleep。
- [ ] 实现直接登录和敏感信息过滤；动作默认超时 30 秒、下载超时 60 秒，均允许配置。
- [ ] 重跑上述测试，期望通过；手动验证保存文件能打开且非空。
- [ ] 提交说明 `feat: add login browser actions and verified downloads`。

## Task 3：自然语言逐步指挥

**Files:** 创建 `backend/app/agent.py`、`backend/tests/test_agent.py`；修改 `api.py`、`config.py`、`events.py`。

**Interfaces:** `StepAgent.run(instruction: str, session: BrowserSession) -> ActionResult`；`POST /api/commands` 接收 `{instruction}`，返回命令 ID；忙碌时返回 HTTP 409。事件类型 `command_started`、`step_result`、`needs_clarification`、`command_finished`、`error`。WebSocket `/api/events` 首先发送会话快照。

- [ ] 使用模拟模型响应写测试：观察后点击正确按钮；输入及查询按工具顺序执行；歧义返回澄清；非法工具参数不执行；连续工具循环达到上限后结束。
- [ ] 写 `test_model_payload_excludes_secrets_and_images`，检查实际请求序列化体没有截图、账号密码和 API 密钥；记录日志也无敏感配置。
- [ ] 写并发测试：第二条指令返回 409，不能进入浏览器执行区。
- [ ] 运行 `python -m pytest backend/tests/test_agent.py -q`，确认失败。
- [ ] 实现兼容 Chat Completions 工具调用的客户端，配置 base URL/key/model；明确要求服务支持 tools。每条指令最多 8 次工具操作、模型请求超时 60 秒，禁止任意代码执行。
- [ ] 将当前观察与有限历史交给模型；页面文字作为不可信数据。执行结果与澄清推送前端，完成后等待下一条用户指令。
- [ ] 重跑上述测试，期望通过；使用配置的真实模型在模拟站点逐步查询导出，核实工具调用兼容性。未配置模型时明确报告，不能将模拟模型结果当作真实模型验收。
- [ ] 提交说明 `feat: execute natural language browser instructions`。

## Task 4：实时画面、文字反馈与可下载结果

**Files:** 创建 `frontend/src/components/{CommandPanel,BrowserView,FileList}.tsx`、`backend/tests/test_api.py`；修改 `App.tsx`、`api.ts`、后端 `events.py`、`api.py`、`browser.py`。

**Interfaces:** `/api/events` 推送日志/状态；`/api/screen` 推送 JPEG 帧；`GET /api/files` 返回登记文件；`GET /api/files/{id}/download` 返回文件。画面采集默认每秒 2 帧，不经过模型；前端断线时显示状态并重连。

- [ ] 写 API 测试：命令结果可查询；已登记文件可下载；未知 ID 返回 404，不能通过路径参数读取任意文件；事件重连先收到当前状态。
- [ ] 运行 `python -m pytest backend/tests/test_api.py -q`，确认失败。
- [ ] 实现文字反馈、指令忙碌禁用、实时画面和文件列表；帧采用最新帧优先，慢客户端不能阻塞浏览器任务。
- [ ] 重跑 API 测试及 `python -m pytest backend/tests -q`，期望全部通过；运行 `npm --prefix frontend run build`，期望成功。
- [ ] 手动完整验收：启动 → 自动登录 → 分步进入业务页 → 输入 → 查询 → 导出 → 网页下载。刷新前端后恢复状态，画面断线不终止执行。
- [ ] 更新 README 中的启动、配置和当前阶段限制，提交说明 `feat: show live browser feedback and exported files`。

## 阶段 1 完成关口

- [ ] 输出运行方法、测试证据、已实现范围及真实系统验证结果；未完成的真实环境验证明确列出。
- [ ] 演示一次完整查询导出，由用户验收。
- [ ] 停止开发，等待用户决定是否开始阶段 2。

## 后续阶段的任务边界（启动前细化）

### 阶段 2：标准流程最小闭环

引入 SQLite、不可变流程版本和动作记录；保存前自动提出参数映射供用户确认；增加流程列表及步骤/参数编辑。执行器直接运行已保存动作，支持等待、条件分支和无数据状态，始终从确保登录和业务入口开始。验收至少包括不同参数两次运行、零 LLM 调用、无数据不导出、重复定位拒绝执行。

### 阶段 3：批量执行与任务管理

实现表格填入/粘贴及必填校验、参数/版本快照、串行队列和每行独立结果。增加暂停/继续/停止状态机、任务历史、文件关联与唯一命名。编排持有浏览器期间不运行排队任务。验收混合三种行结果、重复参数文件不覆盖，以及当前操作结束后控制生效。

### 阶段 4：有限自动恢复

加入文本/元素恢复上下文、每失败步骤最多两次尝试、完成证据检查与临时步骤覆盖。修复作为待确认记录，确认后生成新版本。对比测试元素改名、下载已完成但反馈失败、登录过期、恢复失败后下一行回到已知状态。

### 阶段 5：真实系统与交付加固

实现进程重启将未完成任务标记中断、登录状态复用验证、浏览器不可用/持续登录失败的全局故障终止。完成真实站点定位和等待配置、模型调用耗时统计及敏感数据请求检查，完善 Windows 一键启动和完整验收说明。避免重复扩展已通过的测试；只针对真实系统新发现的风险补充。
