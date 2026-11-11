# 工作区默认路径收尾审查

日期：2026-10-07。范围为已批准资产解耦方案的默认路径遗漏修复，直接在当前本体开发与提交。

## 范围与结果

- atelier 脚手架、逐场景截图、Kling 验收和 AutoMV 离线验收脚本，以及视频分析、角色预览工具，统一使用 `lib.paths.PROJECTS_DIR`。
- 显式输出覆盖保留；scaffold 的显式 `root/projects` 约定保留。截图的 CLI 相对输入按调用目录解析，artifact 相对输入按本项目解析。
- 外部项目路径的日志不再依赖 `relative_to(cwd/repo)`；新 README 使用绝对 `Path` 和跨平台拼接，mock 执行验证通过。已有 README 仍保留原内容。
- 默认输出在渲染、供应商调用或写入前检查路径和已有符号链接。Kling 同时预检项目名长度及输出父目录、报告和媒体目标的类型。

## 独立审查

功能与安全审查结论为 **APPROVE**。发现 1 项 MEDIUM，已修复并独立复核，无未解决的 HIGH、CRITICAL 或 MEDIUM。

该问题是 Kling 可接受无法写入报告的目标：超长项目名、报告父路径已是普通文件，或报告目标已是目录。临时 fixture 的 mock live 函数在报告写入失败前已经执行，可能造成真实调用后无法记录结果。新增回归先 RED，再通过项目名上限、工作区错误转 CLI 错误和输出类型预检修至 GREEN。独立复现确认上述输入在 discovery/live 调用前退出。

相关回归独立验证 **89 passed**；Kling 最终切片 **27 passed**。RED/GREEN 测试证据位于 `docs/evidence/shot-workbench/logs/workspace-defaults-*.log`。提交中的日志仅移除 pytest 输出的行尾空白，原始与提交版 SHA256 记录于 `workspace-defaults-log-hashes.json`。测试使用临时素材、工作区及 mock，不调用真实供应商或渲染器。

主任务另以真实 Python 子进程从其他目录运行脚手架默认根和显式根，两次均写入临时预期路径；另外三个 CLI 的 `--help` 均通过，未依赖 `PYTHONPATH`。全量合同结果与改动行覆盖率见 `shot-workbench-verification.md` 后续验收记录。
