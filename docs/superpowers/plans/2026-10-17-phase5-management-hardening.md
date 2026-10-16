# 删除管理与第五阶段通用加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 增加流程/历史/文件删除入口，完成本机运行、重启中断、实时连接、配置诊断和统计的通用加固。

**Architecture:** 流程逻辑归档，任务终态记录事务删除，文件移动到导出目录 .trash 并持久化删除标记。删除API校验关联运行状态并发布事件。沿用当前队列和恢复执行器，补生命周期连接退出、诊断及计时统计。

**Tech Stack:** 现有 FastAPI/sqlite3/Playwright/React/PowerShell，无新增依赖。

**Spec:** 总体设计第7/9/10节、原路线图阶段5，加用户本次删除需求。用户确认文件回收目录，真实系统稍后接入；本次只交付通用加固和演示验收。

## Global Constraints

- 保留 .env、自定义检查脚本、用户既有业务数据；开发验收只删除合成测试数据。
- 用户点击删除后明确确认。流程归档全部版本但保留任务快照；删除历史仅终态，保留文件；文件移至配置导出目录下 .trash，不永久删除。
- 活动/排队/暂停任务及其流程/文件不能删除；关联状态在服务端校验。归档流程不能创建新任务/版本或通过审核复活。
- 文件移动仅限已登记、原始保存路径直接位于导出目录的文件；拒绝越界/符号链接回收目录，禁止覆盖同名回收文件。
- 重启不自动续跑，未完成任务标记中断，历史与下载登记可恢复。页面断线不终止任务，关闭服务时连接与浏览器资源有界退出。
- 正常执行零模型，恢复计数/耗时真实持久化；只发送既定脱敏文字/元素。真实站点验收明确留待用户后续配置。

## Review Focus

1. 删除最近一次单次任务导致 snapshot.current 引用缺失：清理缓存，后续页面/API仍正常（任务1/2）。
2. 删除历史后仍有导出文件：文件继续下载且脱离被删任务，避免误删除；文件删除后历史无坏链接（任务1/2）。
3. 并发运行/过期确认：事务检查任务状态；流程删除使用expected_version防止误归档刚更新版本（任务1/2）。
4. .trash 符号链接或文件被外部移走：不移动到外部路径，缺失文件可清理登记，失败不丢有效登记（任务1）。
5. 无画面/断开连接时退出：不能永远等待 screen 循环；故障队列与进程重启控制行为明确（任务3）。

## Task1 删除仓库与文件回收

**Files:** workflows/repository.py、tasks/repository.py、downloads.py、`backend/tests/test_deletion_repository.py`。
**Interfaces:** workflow.delete(id,expected_version)、tasks.delete(id)、downloads.delete(id)。归档时间/文件删除信息兼容迁移；历史删除清理行/步骤/修复，导出登记保留并解除任务关联。

- [x] 写失败测试：归档仍保留快照，终态删除保留文件，活动关联409，过期删除冲突，安全回收/缺失/越界/回收路径链接。
- [x] 确认失败后实现，重开数据库仍保持删除状态；通过提交。

## Task2 删除API和界面

**Files:** workflows/routes.py、tasks/routes.py、api.py、workflows/service.py、frontend App/TaskPanel/FileList、新增DeleteDialog、`test_deletion_api.py`、`test_deletion_ui.py`。
**Interfaces:** DELETE `/api/workflows/{id}`（confirmed、expected_version）、DELETE `/api/tasks/{id}`、DELETE `/api/files/{id}`（confirmed=true）。未知404/活动409/未确认422；事件刷新列表。

- [x] RED→GREEN测试确认/互斥/持久化/快照安全，以及网页三类删除、取消和关联保留。
- [x] 构建和针对性回归，提交。

## Task3 生命周期、诊断、统计及Windows启动

**Files:** main/api/sessions/launcher、tasks/repository/service、agent/events、scripts/start.ps1、scripts/start.cmd、`test_lifecycle_hardening.py`。
**Interfaces:** GET `/api/diagnostics`仅返回非敏感配置就绪/网站类型/本地路径与状态；任务/恢复/model请求持久化耗时，界面展示；服务停止有界释放WebSocket和浏览器。启动脚本检测依赖/.env/已占用端口，并给出准确提示。

- [x] 写失败测试：启动将排队/运行/暂停标中断，连接断开不影响运行，连接在无帧时可退出，浏览器故障资源可关闭；诊断不泄露凭据，模型统计与耗时在历史中可见。
- [x] RED→GREEN修复，补Windows双击启动和接入诊断说明。

## Task4 验收与发布

- [x] 构建、完整回归、独立审查；实质问题RED→GREEN。
- [x] 备份用户数据库并校验旧版本/历史/文件，部署本机；只对新建测试资源验收删除、重启恢复和正常零模型。
- [x] 更新第五阶段验收/真实系统接入清单，明确本次未验收真实系统；提交GitHub独立分支和PR。
