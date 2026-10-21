# 显式登录与凭据参数 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 以统一流程登录替代隐式登录，支持本机凭据引用、登录录制、参数化回放与恢复。
**Architecture:** CredentialStore 解析 .env 秘密并提供脱敏/元数据；LoginSpec 与 LoginController 执行组合登录步骤。流程与草稿来源独立于 SITE_URL，执行器和恢复器共享流程内登录上下文。
**Tech Stack:** 现有 Python/FastAPI/Playwright/SQLite/React，无新增依赖。
**Spec:** docs/superpowers/specs/2026-10-20-explicit-login-design-v3.md（用户已确认开发）。

## Global Constraints

- 不开发旧配置/流程转换；不自动删除用户数据或覆盖 .env。
- 密码仅在 .env 与专用填写代码内解析；数据库、模型请求、日志、事件、文件名不含秘密。
- 每个流程单一 HTTP(S) 来源；凭据来源、实际填写页面来源和流程来源一致。
- 默认复用同一凭据已验证会话；更换账号不能误复用；正常登录零模型。
- 恢复仅用本流程已执行登录，最多每行一次，导出前缀不重放。

## Review Focus

- 配置错误、转义秘密与 Pydantic 错误详情不能泄露（任务1/4）。
- 登录地址重定向、字段填写中页面跳转不得向其他来源注入凭据（任务2）。
- 换账号与未知已登录会话拒绝复用，失败登录不能留下可信身份（任务2）。
- 参数、建议、批量粘贴与文件名不能把凭据当普通文本（任务1/3/4）。
- 草稿来源切换与复制参数重名不能混合站点或改变源流程（任务3/4）。

## Task1 凭据与模型

Files: 新 app/credentials.py；修改 config.py、workflows/models.py；新增 test_credentials.py。
Interfaces: Settings.credentials -> CredentialStore；store.metadata()/resolve(ref,origin)/secrets；LoginSpec(url,url_parameter,credential_ref,credential_parameter,username,password,submit,success,reuse)；validate_parameters(definition,values,credentials=None)。
- [x] 写并运行失败测试：安全配置错误、元数据无秘密、嵌套脱敏、来源错配、url/credential 参数及互斥步骤。
- [x] 实现最小解析与 schema_version=2 模型，credential 参数不得绑定普通 fill。
- [x] 运行针对性测试通过并提交。

## Task2 登录执行与恢复

Files: 新 app/login.py；修改 sessions/browser、workflows/executor.py、recovery/runtime.py/evidence.py；新增 test_explicit_login.py。
Interfaces: LoginController(session).execute(spec,values,scope,force=False)、is_login_page(spec)、session.authenticated map；恢复器接收 execute_action/observe/relogin/is_login_page 回调。
- [x] RED 测试：独立登录、会话复用、错误凭据/错来源、重定向、换账号、不含 login 不隐式登录、过期恢复。
- [x] 去除 session.login 和全局登录判断，显式登录错误停队列；业务动作使用流程来源检查。
- [x] 运行登录/恢复回归通过并提交。

## Task3 API、录制与存储

Files: 新 login_routes.py；修改 main、events、workflows/repository/routes/service/suggestions、tasks/repository、diagnostics。
Interfaces: GET /api/credentials 元数据；POST /api/login {login,parameters}，运行并录制；reset_draft(scope=None)、record_login(spec,url)、record 根据首步绑定来源。
- [x] RED 测试：API 互斥与安全错误、其他来源流程、登录草稿生成、单来源拒绝、参数快照与文件名不含秘密。
- [x] 接入统一登录、草稿和来源校验，模型建议跳过登录配置；凭据参数提交前验证。
- [x] 测试通过并提交。

## Task4 页面与综合验收

Files: 新 LoginPanel/LoginFields/ParameterInput；修改 App、WorkflowEditor、WorkflowRunPanel、BatchPanel、workflows.ts、样式、测试fixture、README/.env.example。
Interfaces: 配置并执行登录；编辑器新增/复制登录，URL/凭据参数选择；普通字段继续原有输入。
- [x] RED 网页测试：登录录制固化、单次/批量参数、复制独立快照；更新旧登录 fixture 为显式登录。
- [x] 实现界面与文档，完整构建/回归；独立最终审查，实质问题 RED→GREEN。
- [x] 备份并部署本机（新配置由用户填写），受控演示端到端验收；提交 GitHub PR。
