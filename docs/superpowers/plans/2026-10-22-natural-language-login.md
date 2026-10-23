# Natural Language Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户通过自然语言完成账号密码填写与登录，自动保存本机凭据并录制，后续零 LLM 回放且可复用任意当前账号会话。

**Architecture:** 命令入口先在本机解析并遮蔽凭据，再让模型选择元素。凭据由独立 DPAPI 存储提供，录制草稿只记录引用，保存时整理为现有 LoginSpec。登录执行器先检查可复用会话，真正填写时才读取凭据。

**Tech Stack:** Windows、Python 3.14、FastAPI、SQLite、Windows DPAPI（ctypes）、Playwright Chromium、React/TypeScript、pytest。

**Spec:** `docs/superpowers/specs/2026-10-22-natural-language-login-design.md`

## Global Constraints

- 正常回放零 LLM。
- 隐藏字段、不可见字段仍不作为操作目标。
- 自然语言录入不写入 .env，不覆盖既有凭据配置。
- 账号和密码均通过 Windows DPAPI 的当前用户保护后保存。
- 不增加人工键盘输入的自动录制、验证码处理、SSO 跨站登录、多标签页或多页面分段认证。
- 用户明确允许复用浏览器当前已登录会话，不再校验当前账号是否与录制账号相同。
- 已有结果不确定导出不重试、人工接管互斥及标准流程来源限制继续生效。
- 不实现旧配置批量转换；保留既有数据库、导出文件与流程版本。

## Review Focus

1. 密码包含空格、引号、中文或类似命令的内容：完整保留字符，不被解析为附加操作（任务2）。
2. 单字段分条输入、多个登录表单：只能在同一草稿、来源和表单中唯一关联（任务3、4）。
3. 新凭据在首次事件或模型调用前泄漏：先注册脱敏，历史、嵌套 JSON 与异常均不含真实值（任务1、2）。
4. 已登录但凭据缺失或不可解密：复用成功标识时不读取凭据；需要填写才报错（任务5）。
5. 保存凭据失败、接管或网页跳转造成状态变化：不声称录制完成，不重复提交，不执行旧模型建议（任务3、6）。

## 文件结构

新增模块：
- `backend/app/local_credentials.py`：DPAPI 密文持久化、元数据及已有只读凭据的统一接口。
- `backend/app/credential_intents.py`：纯本机指令解析，脱敏意图与临时凭据上下文。
- `backend/app/credential_actions.py`：凭据指令授权、目标校验、原子填写与成功记录。
- `backend/app/workflows/login_recording.py`：分条凭据关联、登录尝试和组合步骤建议。

修改入口：`api.py`、`events.py`、`agent.py`、`browser.py`、`config.py`、`main.py`；修改录制与执行：`workflows/repository.py`、`suggestions.py`、`models.py`、`executor.py`、`login.py`；界面：`CommandPanel.tsx`、`WorkflowEditor.tsx`、`LoginPanel.tsx`、相关前端类型。

所有新模块以组合方式接入，避免将解析、持久化与元素执行堆进现有 Agent。

## Task 1: 可写本机凭据和动态脱敏

**Files:** Create `backend/app/local_credentials.py`, `backend/tests/test_local_credentials.py`; Modify `backend/app/credentials.py`, `config.py`, `main.py`, `workflows/repository.py`。

**Interfaces:**
- `LocalCredentialStore(path: Path, legacy: CredentialStore)`，暴露与旧接口兼容的 `resolve(name, scope)`、`metadata()`、`secrets()`。
- `save_field(ref: str | None, scope: str, field: Literal['username','password'], value: str) -> str`：返回随机引用；未齐全记录不可 resolve。
- `Settings.register_secrets(values: list[str]) -> None`：立即登记待处理指令值，统一脱敏及持久化拒绝读取动态集合。
- 新表 `local_credentials(id, origin, payload, created_at, updated_at)`；payload 为 DPAPI 保护的 JSON bytes，真实值不进明文列。

- [ ] 写失败测试：保存后数据库、元数据与异常无合成凭据明文；重启后 resolve 保持原字符；错误来源、缺字段与解密失败给固定错误。
- [ ] 写动态脱敏测试：新值及三层 JSON 转义在首次 save 前已能被 scrub，旧 .env 引用仍解析。
- [ ] 运行 `.venv\Scripts\python.exe -m pytest backend/tests/test_local_credentials.py -q --basetemp=.superpowers/nl-login-store-red`，确认预期失败。
- [ ] 实现 Windows `CryptProtectData`/`CryptUnprotectData` 包装并正确释放 `LocalFree`；非 Windows 明确不支持，不退回明文。接入统一存储与动态脱敏。
- [ ] 同一测试使用新 basetemp 运行至通过；提交 `feat: add writable local credential storage`。

## Task 2: 命令入口的凭据解析与遮蔽

**Files:** Create `backend/app/credential_intents.py`, `backend/tests/test_credential_intents.py`; Modify `api.py`, `events.py`, `agent.py`。

**Interfaces:**
- `CredentialIntent`：字段和值、脱敏指令、可选提交意图；字段值以 SecretStr 保存，repr 与序列化不暴露。
- `parse_credential_intent(instruction: str) -> CredentialIntent | None`：普通指令返回 None；涉及凭据却边界不明确时抛固定澄清异常。
- `CommandRunner.submit(instruction, credential_intent=None)`：只能向命令记录/事件写脱敏文本，临时上下文不进入快照。

- [ ] 写失败测试：单账号、单密码、双字段加点击、引号内空格和中文正确解析；含分隔符的歧义、不闭合引号、多候选账号要求澄清。
- [ ] 为 `/commands` 写测试：模型 MockTransport、事件总线、commands 快照均不包含合成凭据；敏感指令解析失败时模型请求数为零。
- [ ] 运行解析与命令入口测试，确认失败；实现受约束的本机语法，不通过模型解析原始凭据，先注册脱敏再创建命令或发布事件。
- [ ] 运行至通过；提交 `feat: preprocess credential instructions locally`。

## Task 3: 密码框观察与受授权填写

**Files:** Create `backend/app/credential_actions.py`, `backend/tests/test_credential_actions.py`; Modify `browser.py`, `agent.py`, `events.py`。

**Interfaces:**
- `execute_credential_fill(session, intent, field, target: LocatorSpec) -> ActionResult`：只使用当前命令授权的字段和值，检查提取时来源、目标唯一性和输入类型。
- 模型只返回现有 BrowserAction 的目标与脱敏占位；服务端凭据分支不使用模型提供的 value。
- 成功事件的内部 `credential_record` 仅含字段类型、目标定位、来源、表单标识及凭据引用；对外事件剔除内部数据。

- [ ] 写失败测试：可见密码框出现在观察中但没有当前值；隐藏框仍排除；无授权普通 fill 不能写密码框。
- [ ] 测试模型替换密码、选择其他来源、多个密码框、失效节点与人工接管后旧建议均不能越权执行。
- [ ] 测试成功填写后保存引用；存储失败返回“已填写但未保存”，不记成功录制、不自动重填。
- [ ] 先运行测试至失败；实现同一 JS 执行回合检查来源并填写，保持既有浏览器原子填写方式。
- [ ] 运行新测试与现有 Agent/浏览器测试至通过；提交 `feat: support authorized credential field actions`。

## Task 4: 自动整理登录录制与成功标识确认

**Files:** Create `backend/app/workflows/login_recording.py`, `backend/tests/test_login_recording.py`; Modify `workflows/repository.py`, `suggestions.py`, `routes.py`, `frontend/src/components/WorkflowEditor.tsx`, `frontend/src/workflows.ts`。

**Interfaces:**
- `LoginRecordingTracker` 按 draft_id、来源、表单和尝试关联成功的 username/password/submit 记录，禁止跨草稿拼接。
- `build_login_proposal(records, success: LocatorSpec | None) -> LoginSpec | None`：完整记录且 success 已确认才输出现有 schema_version=2 LoginSpec；其余返回待补全状态。
- proposal 响应新增登录确认元数据，普通流程保存接口保持兼容；确认元数据不写真实凭据。

- [ ] 写失败测试：分条及合并输入整理为一个登录步骤；账号密码不会变成业务参数；中间普通业务动作不会被吞并。
- [ ] 写测试：多表单、登录失败、只有单字段、重置草稿、缺少成功标识不能生成完整组合；旧版本仍可读。
- [ ] 运行至失败；实现加密记录与草稿引用关联，成功点击后提出标识建议，用户在保存面板确认唯一可见标识。
- [ ] 前端展示“凭据已保存到本机”和成功标识确认，不要求创建引用；提交后清空原始输入，不写浏览器持久存储。
- [ ] 运行录制测试及 `npm.cmd --prefix frontend run build` 至通过；提交 `feat: assemble recorded login steps automatically`。

## Task 5: 当前会话复用与延迟解析凭据

**Files:** Modify `login.py`, `workflows/models.py`, `executor.py`, `login_routes.py`; Create `backend/tests/test_relaxed_login_reuse.py`; Update `test_explicit_login.py`, `test_reviewed_boundaries.py`。

**Interfaces:**
- `LoginController.execute(spec, values, scope, force=False)` 保持签名；reuse=true 先验证当前页或导航后的成功标识，再判断是否需要 resolve。
- `validate_parameters` 保留引用格式、参数类型和来源结构校验，不再预先强制读取固定凭据；真正填写时解析并验证来源。
- `reuse=false`/force 在没有表单的已登录页明确反馈无法重新登录，不伪称已填写；恢复控制与导出不确定性规则保持。

- [ ] 写失败测试：未知账号记录、不同引用、接管清空身份后均可复用合法成功标识。
- [ ] 用抛异常的 resolve 验证复用路径零解密；登录表单出现时缺凭据或解密失败必须失败。
- [ ] 测试业务页缺标识→登录 URL 重定向首页可复用；仅密码框消失不成功；跨来源仍拦截；reuse=false 不静默跳过。
- [ ] 运行至失败；移除身份相等条件，调整 eager validation，保留表单与来源检查。
- [ ] 运行登录、来源边界、过期恢复和不重复导出测试至通过；提交 `feat: reuse current authenticated session without identity checks`。

## Task 6: 集成验收、审查与交付

**Files:** Create `backend/tests/test_natural_login_ui.py`, `docs/natural-login-validation.md`; Modify `README.md`；根据集成证据修正前述模块。

- [ ] 用合成内网登录站点和确定性模型录制完整登录、查询、导出；确认模型和数据库明文扫描不含凭据。
- [ ] 重启应用并关闭模型服务，回放登录与多行查询，确认正常执行 model_calls=0；已登录的其他账号会话复用通过。
- [ ] 端到端覆盖标识确认、未保存反馈、特殊字符、人工接管、无数据分支与导出不重复；复用现有测试站点而非真实账号。
- [ ] 运行完整 pytest 与前端 build；记录实际测试数量、用时、限制和截图，独立审查整分支并解决有效问题。
- [ ] 对本机安装目录核对基线并备份；空闲时部署、构建、重启，复核用户 .env、数据库和导出文件不变。真实凭据配置和数据不提交 GitHub。
- [ ] 创建或更新仅包含本功能的 GitHub PR，按实际远程状态选择依赖分支；不自动合并。提交 `docs: record natural login validation`。

## 执行方式建议

建议 Native：在当前会话由主 Agent 按任务顺序实施，最后独立审查。六项任务共享凭据接口和录制协议，顺序执行可减少接口偏差和多份上下文成本。若用户选择 Subagent-driven，则逐项交给新实施代理并在每项后审查。

## 自检

规格第1–4节对应任务2–3；第5节对应任务2；第6节对应任务1；第7节对应任务4；第8节对应任务5；第9节对应任务4、6；第10节由任务6汇总验收。Review Focus 的五项均有明确测试归属。本文未修改产品代码或运行配置。
