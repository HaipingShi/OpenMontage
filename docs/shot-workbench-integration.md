# Shot Workbench 本体集成记录

日期：2026-10-07。用户明确批准“直接合并到本体然后提交”，随后要求核对来自其他 Agent 的设计/迁移补丁。交付位置为 `/Users/geesh/projects/OpenMontage`，沿用当前分支 `codex/automv-upgrade`，实际基线为 `89420e3bf2c7a65e5b970bb2122a1cc70ed895f1`。

## 补丁来源与落地状态

| 文件 | 来源与内容 | 处理结果 |
| --- | --- | --- |
| `automv-upgrade.patch` | 此任务之前已存在的 AutoMV 导入文件，32 个文件改动 | stable patch-id 与已提交的 `2bface7..89420e3` 完全一致，`git apply --reverse --check` 通过，已完整落地，无重复应用 |
| `shot-workbench.patch` | 此任务需求的验收第 5 条要求输出，因此在开发工作树生成；包括工作台及资产解耦的软件改动 | 194 个软件路径与工作树逐字节核对后直接集成本体；29 个个人文件另行退出索引，原字节保留 |

AutoMV 输入文件 SHA256：`8f21c4329899531828812cdd5919e7fad8b0c3811bec873cd25c124f0976e465`。

AutoMV patch-id：`c6882cba3e7ae4f8cf46cec4bec37cc0869cc226`。

Shot Workbench 开发验收补丁 SHA256：`224a19ea2d4bafe5afd37e85ea8404c839614c98cecf80c9499bdf7684bb68fd`。该值对应集成前记录，不包含本记录或用户确认后的文档更新。

两份补丁、旧审批计划、集成路径与原文件哈希清单保存在本机 `.git/agent-imports/2026-10-07-shot-workbench-41369175/`。它们是导入过程的本地记录，无需在运行应用时加载，也不进入源码提交。没有改写已有提交。

## 原始文件与数据位置

本体集成前无已跟踪的未提交代码或已暂存用户修改。唯一同名冲突为此任务的未跟踪 Gate 1 计划，备份后使用已经批准的更新版本。原 AutoMV 输入文件按上述哈希留存。

29 个已验证的个人艺人文件通过 `git rm --cached` 退出本体索引，逐文件 SHA256 与集成前一致，物理文件全部保留。个人项目、已迁移的复用库和固定版本绑定没有被软件提交收入。

当前本体的 `lib.paths` 读取：

```text
DATA_DIR     = /Users/geesh/OpenMontageData
PROJECTS_DIR = /Users/geesh/OpenMontageData/projects
LIBRARY_DIR  = /Users/geesh/OpenMontageData/library
```

已用本体代码读取并校验三个真实项目绑定，固定版本与迁移 receipt 一致。复制迁移的完整清单和历史引用复核边界见 [验收记录](shot-workbench-verification.md) 与 [资产解耦说明](asset-decoupling.md)。

## 验证与提交

开发工作树的 1655 项合同、6 项 Chrome 和 9 项 Node 验收全部通过，核心综合覆盖率 88%，独立功能/安全审查未解决问题为 0。本体代码完成逐字节集成核对后，再从本体目录运行合同与真实 Chrome 测试；最终结果在本节追加。提交消息为 `feat: add shot workbench with project-owned assets`。补丁和真实生产数据不纳入提交。


最终本体验证：**1655 passed、7 skipped in 76.09s**，真实 Chrome **6 passed in 23.65s**。没有失败。当前检出完整运行的日志保存在 `docs/evidence/shot-workbench/logs/primary-contracts-final.log` 与 `primary-browser-final.log`。验证后再次确认 29 个原个人文件 SHA256 未变，艺人索引只有 `_template`，旧 AutoMV 补丁归档哈希匹配输入。
