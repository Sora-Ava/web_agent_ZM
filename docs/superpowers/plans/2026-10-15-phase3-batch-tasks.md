# 第三阶段批量任务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在已确认流程上填写/粘贴多行参数，串行运行并控制任务，持久化每行结果和导出文件。

**Architecture:** 新增 TaskRepository 与 TaskService，复用 SQLite 和 WorkflowExecutor。步骤边界执行暂停/停止检查；任务持有浏览器 reservation，包括暂停期间。编排状态阻止队列派发，用户显式结束编排后启动队列。保留第二阶段单次运行接口。

**Tech Stack:** FastAPI、sqlite3、Playwright、React/TypeScript；无新增运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-14-web-automation-agent-design.md` 第 7/9 节，以及已确认分阶段路线图的阶段 3。

## Global Constraints

- Windows 本机单用户、一个浏览器、队列串行，标准执行零 LLM。
- 提交前全部参数校验；任务保存不可变流程版本与参数快照。
- 单行业务失败继续下一行，各行从流程入口重新执行；本阶段不做自动恢复。
- 暂停/停止等待当前操作完成或超时；暂停保留执行权，继续不重做已完成步骤。
- 不改用户 .env、已有数据库记录或自定义检查脚本；新增表兼容第二阶段数据库。
- 任务/行/步骤/文件元数据持久化；不删除用户文件。未完成任务启动时标记中断，不自动续跑。

## Review Focus

1. 暂停发生在下载中：已完成下载必须登记，继续不能重复导出（任务2）。
2. 排队期间编辑流程或参数：运行始终使用提交快照（任务1/2）。
3. 编排等待用户输入：队列不得抢占；暂停状态也不得插入其他浏览器操作（任务2/3）。
4. 表格前导零、空单元格和粘贴列数不匹配：保留字符串，整批错误提示不产生半个任务（任务1/4）。
5. 重复参数/重启/文件缺失：文件唯一且可关联原任务；缺失文件404，历史不会消失（任务1/3/4）。

## Task 1：持久化任务、参数行与文件

**Files:** `backend/app/tasks/{__init__,repository}.py`、`backend/tests/test_tasks_repository.py`、`downloads.py`。

**Interfaces:** TaskRepository.create(record, rows)->task；get(id)、list(limit)、update_task、update_row、add_step、register_file、files/get_file。任务状态 queued/running/pausing/paused/stopping/stopped/completed/failed/interrupted；行保存参数、步骤、结果和文件ID。

- [x] 写测试：重新打开库仍可读任务/文件，版本和参数快照不变，整批非法输入拒绝，前导零保留。
- [x] 运行测试确认失败；实现新增表和事务式整批创建、文件登记与恢复索引。
- [x] 测试通过并提交。

## Task 2：串行调度和动作边界控制

**Files:** `tasks/service.py`、`workflows/executor.py`、`backend/tests/test_task_service.py`。

**Interfaces:** TaskService.submit(flow_id,version,rows)、control(id,action)、release_authoring()、shutdown()；executor.execute 新增 checkpoint 回调，在入口前及每一步完成后调用，停止返回 stopped，暂停由 checkpoint 等待。

- [x] 写测试：混合成功/无数据/失败后继续、多个任务顺序、编辑后快照不变、下载期间暂停/继续/停止不重做、编排不抢占、暂停时互斥。
- [x] 运行确认失败；实现单调度器、浏览器租约和持久化状态转换，每行独立入口复位。
- [x] 重跑服务/执行器回归并提交。

## Task 3：API 与文件管理集成

**Files:** `tasks/routes.py`、`main.py`、`events.py`、`api.py`、`backend/tests/test_tasks_api.py`。

**Interfaces:** GET/POST `/api/tasks`、GET `/api/tasks/{id}`、POST `/api/tasks/{id}/{pause|resume|stop}`、POST `/api/queue/release-authoring`；snapshot 加 tasks 和 queue.authoring；GET files 在重启后仍可下载已登记文件。

- [x] 写测试：校验422、互斥409、排队控制、历史/文件关联、文件缺失404。
- [x] 确认失败；集成生命周期、事件和配置凭据过滤，现有单次运行保持兼容。
- [x] 相关测试通过并提交。

## Task 4：参数表格、任务历史与控制界面

**Files:** `frontend/src/components/{BatchPanel,TaskPanel}.tsx`、`frontend/src/tasks.ts`、App.tsx、WorkflowRunPanel.tsx、style.css、`backend/tests/test_tasks_ui.py`。

- [x] 实现手动增删行、单元格填写、TSV 粘贴（无表头，按参数列顺序），行级必填/类型提示和提交前校验。
- [x] 展示队列、编排占用提示/结束编排按钮、历史任务、版本/参数快照、行结果、步骤和关联下载；提供暂停/继续/停止状态。
- [x] 网页测试：粘贴多行→排队→混合结果→历史刷新→下载；构建和完整回归通过。
- [x] 独立全分支审查，实质问题增加先失败后通过回归；同步本机并验收，保留用户第二阶段流程和 .env。
- [ ] 提交 GitHub 第三阶段分支与 PR。

## Execution

用户已批准总体设计/路线图并明确要求开展第三阶段，沿用第二阶段的会话内逐项实现方式。本计划细化既定范围，不增加第四阶段自动恢复；不重复请求开发授权。
