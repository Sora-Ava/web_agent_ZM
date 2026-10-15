# 第一阶段验证记录

日期：2026-10-14

## 已验证

Windows / Python 3.14.5 / Node.js 24.15.0。

- 前端生产构建：`npm.cmd --prefix frontend run build`，成功。
- 完整测试（审查修复后）：`python -m pytest -q --basetemp=.superpowers/final-run -p no:cacheprovider`，21 项通过。
- Windows 启动脚本：`powershell -ExecutionPolicy Bypass -File scripts/start.ps1`，服务启动成功；`GET /api/health` 返回 `ok=true, phase=1`。
- 网页端验收：启动并登录 → 进入生产查询 → 输入 PC001 / 层级 2 → 查询 → 导出 → 下载 CSV → 刷新后恢复四条结果 → 关闭浏览器。
- 网页端测试验证只读实时画面可见、文件含 `PC001,2,120`、模型请求不含图片或登录密码。
- 独立审查提出三项问题，均增加先失败后通过的回归测试：元素名称带出隐藏/可编辑内容、结果先清空导致等待提前结束、JSON 转义导致特殊字符凭据绕过过滤。

测试站点为内置模拟系统，工具调用来自确定性的模型替身。测试实际启动 Chromium 并操作页面、下载文件；不代表真实 LLM 的选择能力已经验证。

## 需要用户环境验收

尚未提供真实系统 URL、登录/等待定位和真实模型配置，因此尚未验证：真实登录、真实 DOM 定位、真实导出格式及模型工具调用兼容性。凭据应只填写本地 `.env`。

首版未实现流程保存、标准回放、批量任务、自动修复和持久化历史；第一阶段验收后再逐项增加。
