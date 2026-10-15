# 第二阶段标准流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 将逐步编排的成功操作保存为经用户确认、带版本的参数化流程，并在单次回放中实现零 LLM 调用。

**Architecture:** 复用现有 BrowserSession 和浏览器动作，不改变第一阶段登录配置。新增 SQLite 流程仓库、录制草稿、参数建议服务和确定性执行器；编排与回放共享现有浏览器互斥。前端增加流程列表、保存确认编辑器和单次运行面板。

**Tech Stack:** React/TypeScript、FastAPI、Pydantic、Python sqlite3、Playwright；不新增运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-14-web-automation-agent-design.md`；`2026-10-14-web-automation-agent.md` 阶段 2。

## Global Constraints

- Windows 本机单用户、一个内部系统、串行操作，不改动用户 `.env`。
- 只录制成功动作；登录仍直接使用配置引用，不录制账号密码输入。
- 保存前用户确认参数、固定值、步骤和条件规则；版本不可覆盖。
- 回放不调用 LLM，不自动恢复；动作失败直接报告，不挑选重复匹配中的第一个。
- 无数据分支结束本次运行，不导出；导出非空并避免覆盖，文件名使用本次参数。
- 批量、任务队列、暂停/继续/停止、持久化任务历史、LLM 恢复不在本阶段实现。

## Review Focus

1. 旧编辑器覆盖新版本：使用 expected_version，冲突返回 409，旧版本内容不变（任务 1/3）。
2. 参数批次号前导零或模板字样：参数保持字符串，按字段绑定替换，不做字符串模板求值（任务 1/2）。
3. 更换网站后误运行旧流程：流程绑定 origin，执行前拒绝不匹配（任务 2）。
4. 失败指令包含已成功子操作：草稿保留成功动作并提示未完成指令，保存仍需用户确认（任务 3）。
5. 回放/编排并发，或新录制中途重置：共用 reservation，冲突 409；回放不进入录制（任务 3/4）。

## Task 1：流程模型与 SQLite 仓库

**Files:** 新增 `backend/app/workflows/{__init__,models,repository}.py`、`backend/tests/test_workflow_repository.py`；修改 `config.py`、`.gitignore`。

**Interfaces:** `WorkflowDefinition(name,entry_url,site_key,parameters,steps)`；`WorkflowParameter(key,label,kind,required,default)`；`WorkflowStep(id,name,action,value_parameter,condition)`。步骤严格二选一：BrowserAction 或 ConditionSpec；条件可选择继续、结束或 no_data，不支持回边/循环。

`WorkflowRepository(path)` 提供 `record(action,command_id,instruction,url_before)`、`draft()`、`reset_draft()`、`proposal()`、`create(definition,confirmed,source_revision)`、`update(flow_id,definition,confirmed,expected_version)`、`list()`、`get(flow_id,version=None)`。SQLite 在默认 `data/agent.sqlite3`，测试显式使用临时库。

- [x] 写失败测试：持久化重开、旧版本不可变、过期版本冲突、草稿参数建议和下载前无数据规则、缺失参数引用拒绝。
- [x] 运行 `python -m pytest backend/tests/test_workflow_repository.py -q`，确认新行为未实现。
- [x] 实现严格模型和事务式版本新增；输入/选择动作自动建议独立参数，保留样本默认值，条件规则展示供确认。
- [x] 流程保存拒绝已配置密码/密钥；记录入口移除认证查询字段，登录动作不入草稿。
- [x] 重跑测试，期望通过；提交任务。

## Task 2：确定性单次执行器

**Files:** 新增 `workflows/executor.py`、`backend/tests/test_workflow_executor.py`。

**Interfaces:** `validate_parameters(definition,values:dict[str,str])->dict[str,str]`；`WorkflowExecutor(session).execute(definition,values,emit=None)->RunResult`。RunResult 包含 status（success/no_data/failed）、步骤结果、file_ids 和 model_calls=0。

- [x] 写失败测试：不同参数两次导出内容正确、无数据不导出、缺失/未知/数值不合法参数无副作用、网站不匹配拒绝、重复定位失败；模型调用方法替换为报错仍能回放。
- [x] 运行执行器测试，确认失败。
- [x] 复用会话，进入录制入口并检查登录，必要时直接登录再进入入口；按绑定字段替换输入参数，默认值不改变批次号前导零。
- [x] 执行条件分支与动作，下载文件名使用流程名和本次参数；失败终止。回放代码不依赖 Agent。
- [x] 重跑测试，期望通过；提交任务。

## Task 3：录制、参数建议与 API

**Files:** 新增 `workflows/{service,suggestions,routes}.py`、`backend/tests/test_workflow_api.py`、`test_parameter_suggestions.py`；修改 `agent.py`、`events.py`、`main.py`、测试 fixture。

**Interfaces:** `GET /api/recording`、`POST /api/recording/reset`、`POST /api/recording/proposal`；`GET/POST /api/workflows`、`GET /api/workflows/{id}?version=`、`POST /api/workflows/{id}/versions`、`POST /api/workflows/{id}/runs`。当前 run/录制摘要放入 `/api/session` 和事件快照。

- [x] 写失败测试：只有成功动作进入录制；失败指令提示、回放不录制、运行与手动操作互斥；保存须 confirmed=true，过期草稿或版本返回 409。
- [x] 参数建议测试：一次工具调用仅允许调整已识别参数的名称/类型/必填性，不能变更动作或样本；服务失败回退录制字段建议，凭据不进入请求。
- [x] 运行上述测试，确认失败。
- [x] 实现录制事件与仓库挂接、建议工具和路由，运行前取不可变版本快照；后台回放共享 reservation，取消后释放。
- [x] 重跑相关测试及后端完整套件，期望通过；提交任务。

## Task 4：流程列表、确认编辑器与运行界面

**Files:** 新增 `frontend/src/components/{WorkflowEditor,WorkflowRunPanel}.tsx`、`frontend/src/workflows.ts`、`backend/tests/test_workflow_ui.py`；修改 `App.tsx`、`api.ts`、`style.css`、README、验证记录。

- [x] 编辑器支持流程名、入口、参数名/类型/默认值/必填、步骤名/顺序/删除、参数与固定值切换、目标定位、查询等待和条件规则。保存按钮要求明确确认。
- [x] 侧栏支持新建录制和流程选择；运行面板显示参数、当前版本、编辑另存新版本、单次运行进度/结果和 model_calls=0。
- [x] 写网页验收测试：录制→保存确认→更换参数→回放下载→无数据→编辑 v2→刷新/重开仓库仍存在。模拟模型仅用于录制/建议；回放时禁用模型，确保仍完成。
- [x] 构建 `npm.cmd --prefix frontend run build`，运行 `python -m pytest -q`，修复后要求全绿。
- [x] 独立代码审查，修复 material findings 并做回归验证。
- [x] 同步到 `E:/web_agent_ZM` 时保留用户 `.env`、数据、下载和已修改的 `scripts/check_llm.py`；服务空闲时重载，验收。
- [ ] 提交 GitHub 第二阶段分支与 PR；若第一阶段未合并，PR 基于第一阶段分支，不自行合并。

## Execution record

用户已明确要求按已确认路线图开展第二阶段并提交 GitHub，沿用当前会话逐项实现。细化计划不增加范围，不再次请求开发授权。工作在已有隔离源码副本的新分支；最终复核使用独立审查。
