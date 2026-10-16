# 第四阶段异常恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 对标准任务提供有限恢复、完成证据检查、登录过期恢复，以及确认后另存版本的定位修复。

**Architecture:** 恢复 Agent 只提出替代定位，执行器保留原动作、参数、等待及导出约束。失败前后用浏览器/下载证据确认完成；任务级临时定位覆盖跨行复用。SQLite 记录恢复请求次数和待确认修复，审核 API 原子新增流程版本。

**Tech Stack:** 现有 FastAPI、Playwright、sqlite3、OpenAI 兼容 Chat Completions 和 React；不增加依赖。

**Spec:** 原总体设计第8节、已确认路线图阶段4；用户已要求开始第四阶段，沿用逐项开发及GitHub交付。

## Global Constraints

- 正常步骤零 LLM；每个失败步骤最多两次请求，无自动网络重试。
- 先检查已有完成证据，再尝试程序登录，最后调用恢复模型；模型不接收截图、凭据、当前字段值或认证URL。
- 模型只能提出页面已观察到的语义定位，不能修改动作类型、参数值、等待规则、条件分支、URL或文件名；条件歧义保持失败，需用户编辑规则。
- 恢复操作与正常操作使用相同校验/互斥/控制边界；失败继续下一行并回到入口。
- 登录恢复每行最多一次，从入口重建已完成的非下载前缀；前缀包含下载时不重复执行，报告需人工检查。浏览器不可用/重新登录失败则停止该任务并阻止后续队列自动派发，用户修复后显式重启队列。
- 自动恢复的定位只在本任务内复用；经明确确认后才新增不可变版本。过期版本或冲突修复拒绝保存。
- 保留用户环境、流程/任务数据库和文件；不进入第五阶段真实系统加固。

## Review Focus

1. 文件已保存但动作反馈失败：以本步骤新增且非空登记文件为完成证据，不重复导出（任务2）。
2. 恶意网页/模型修改参数、凭据或任意CSS：结构严格限制为观察到的语义定位，原动作与值不变（任务1）。
3. 登录过期后直接重试导出会丢查询条件：先重建当前行非下载前缀，禁止重复前缀导出（任务2）。
4. 恢复期间暂停/停止：模型请求完成/超时后检查控制，不继续发起浏览器动作；调用计数和已有文件保留（任务2/3）。
5. 任务完成后流程已被编辑：审核不能覆盖新版本，原子版本冲突返回409（任务3）。

## Task 1：受约束恢复 Agent

**Files:** `backend/app/recovery/{__init__,agent}.py`、`backend/tests/test_recovery_agent.py`。
**Interface:** RecoveryAgent.propose(action,error,observation,attempt)->LocatorSpec|None。一次工具请求、上限由执行器控制；工具只接收 decision retry/abort、target、reason。

- [x] 写失败测试：仅定位建议、实际请求脱敏且无截图、网页指令不提升权限、非观察定位/改值/任意CSS/服务失败被拒绝。
- [x] 运行确认失败，实现严格schema、唯一语义目标校验与既有 MODEL_TIMEOUT_S 整体超时，测试通过提交。

## Task 2：完成证据、登录恢复与有限重试

**Files:** `recovery/evidence.py`、`workflows/executor.py`、`backend/tests/test_recovery_executor.py`。
**Interface:** execute 新增 recovery/overrides 可注入；RunResult 增加 repairs、fatal；恢复请求通过 recovery_attempt 事件计数，成功定位修复通过 repair_suggested 事件输出。

- [x] 写失败测试：改名元素恢复并跨行复用、最多两次失败、正常零调用、文件已保存不重导、填值完成检查、登录过期重建查询、失败下一行复位、恢复中暂停停止。
- [x] 确认失败后实现证据和控制边界，登录恢复不调用LLM；回归通过提交。

## Task 3：修复持久化、审核版本与队列集成

**Files:** `recovery/repository.py`、tasks/{repository,service,routes}.py、workflows/service.py、events.py、`backend/tests/test_repair_repository.py`、`test_recovery_api.py`。
**Interfaces:** GET `/api/tasks/{id}/repairs`、POST `/api/tasks/{id}/repairs/approve`（ids、confirmed=true、expected_version）。确认时从任务快照仅替换指定步骤定位，原子新增版本并记录approved_version。

- [x] 写失败测试：持久化计数/修复、未确认422、旧版本不可变、过期审核409、冲突建议拒绝；普通失败继续，致命故障阻止后续派发。
- [x] 确认失败；接入恢复事件、任务级覆盖、控制状态与审核事务；测试通过提交。

## Task 4：修复审核界面与交付

**Files:** `frontend/src/components/RepairPanel.tsx`、TaskPanel/App/tasks.ts、README、`backend/tests/test_recovery_ui.py`。

- [x] 展示真实恢复调用次数、各步骤恢复记录、原定位/建议定位/原因、选择确认另存版本；提示全局故障及显式重启队列。
- [x] 网页验收改名元素→恢复→旧版本不变→确认新版本→正常新版本零调用；完整构建和回归。
- [x] 最终独立审查，实质问题RED→GREEN修复；备份用户数据库、核对保护文件及原记录、同步本机验收。
- [ ] 提交GitHub第四阶段分支与PR。

## Execution

本计划细化已批准第四阶段，不重新请求开发授权。沿用隔离源码副本和会话内执行；不修改用户 .env。无法确定完成的点击不允许模型自报成功；无可验证定位时本行失败，用户可编辑流程。
