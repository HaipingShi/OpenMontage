# Shot Workbench 验收与交付记录

本功能在 `codex/shot-workbench` 的独立工作树实现。实际基线是
`89420e3bf2c7a65e5b970bb2122a1cc70ed895f1`；规格中的 `53729ce` 未在本地历史中找到。
用户已确认 Gate 1，并于 2026-10-07 明确批准直接合入本体及提交。当前交付位于 `/Users/geesh/projects/OpenMontage` 的 `codex/automv-upgrade` 分支；独立工作树保留开发过程。

## 已实现范围

- WP1：严格的 shot_spec 文件契约、场景初始化、canonical 身份快照、原子保存、revision 冲突与安全项目路径。
- WP2：复用五层 prompt builder 的纯函数编译器；结构/lint、可见身体部位裁剪、实际路线参考图顺序/截断和哈希。BaseTool 与 14 个已有图像/视频工具仅增加能力描述；供应商调用实现保持兼容。新增零费用 registry 工具 `shot_compiler`。
- WP3：CLI 接管/状态/续租/释放/强制接管、两小时 lease、跨进程事务与交接字段 diff；浏览器关闭后仍能编辑/编译/干跑。
- WP4：Backlot 四区中文工作台、字段与参考板、只读正式艺人图/候选图、保存冲突/SSE 草稿保护、费用预览/明确确认、请求去重、候选及废片来源。
- WP5：本地 Three.js 0.181.2、五种简化骨架姿势、关节与物体/相机编辑、相机空间 placement；composition、线性 depth、pose PNG 和姿势/相机 JSON 来源。
- WP6：六条流程的 scene_plan/assets stage 接入、required_tools/review_focus 与 director/meta skill；既有 source-footage 路径保留。
- WP7：需求中的三场景结构及合成艺人 fixture、跨进程竞争、mock API/预算/ledger/实际工具参数契约、Node 舞台数学、真实 Chrome、独立功能与安全审查。

## 验证范围与证据

全部生成器执行均为 mock，无付费媒体供应商调用。原检出中的生产项目、艺人资料和用户原有 `automv-upgrade.patch` 保持原样。需求中的三场景在隔离临时项目运行；艺人身份、候选及图片均为合成 fixture，测试不读取仓库或用户目录的真实艺人资产。用户随后授权的资产解耦与实际复制迁移记录如下。

### TDD

镜头文件/锁初始 RED：缺少 shot_spec 实现；首轮 GREEN 15。新增 headless/signature/in-flight/max-id 回归 RED 4 后修复；加入 checkpoint 文件边界与损坏内容回归后，最终文件/CLI 契约包含 31 个用例。

编译器从 RED 49 到 GREEN 84；实际 Seedance/Kling/Veo 参数回归对照暴露路线引用丢失，新增 RED 9 后修复。详见 [compiler TDD](shot-workbench-compiler-tdd.md)。

API 初始页面读取 RED 404；首轮 GREEN 17，随后增加费用/确认/并发/来源/真实工具 adapter/损坏预算与图片快照边界。六流程接入先 RED 7，实施后 GREEN 7。Fati 的 v2 历史服装与 Amor 候选误入正式引用先 RED 2，修复后 GREEN 4。

### 浏览器与舞台

真实 Chrome 在全部修复落地后重新运行，6 passed in 23.43s：四区加载、单人与双人舞台导出/重建、多人物引用映射、dirty/SSE/409、Agent 只读编译、明确费用确认、候选废片和 unknown 请求查询。确认前 mock 调用为 0，确认后为 1；刷新和查询未知请求不重复执行。

Node 九项舞台数学用例通过；stage-math 行/函数覆盖率 100%，分支覆盖率 90.91%。[浏览器证据与截图](evidence/shot-workbench/README.md)。

### 回归、覆盖与审查

基线合同：1360 passed、7 skipped。全部修复后的最终合同：**1547 passed、7 skipped in 89.12s**，没有失败；使用 coverage 的分支统计运行。七个跳过属于既有可选能力测试，与基线一致。

| 核心模块 | 行与分支综合覆盖率 |
| --- | ---: |
| `backlot/workbench.py` | 92% |
| `lib/shot_compiler.py` | 98% |
| `lib/shot_execution.py` | 83% |
| `lib/shot_spec.py` | 85% |
| `lib/shot_workbench.py` | 88% |
| `tools/analysis/shot_compiler.py` | 100% |
| 合计 | **89%** |

复现时在本工作树运行，使用包含 pytest、FastAPI、jsonschema、Pillow 的 Python 环境；浏览器的单独环境见证据 README：

```sh
python -m pytest tests/contracts -q
python -m coverage run --branch --source=lib.shot_spec,lib.shot_workbench,lib.shot_compiler,lib.shot_execution,backlot.workbench,tools.analysis.shot_compiler -m pytest tests/contracts -q
python -m coverage report
node --test --experimental-test-coverage tests/backlot/stage_math.test.mjs
```

实际完整回归使用仓库已有 Python 3.10.19，coverage 7.16.2 只安装在 `/tmp/openmontage-shot-qa`；未修改项目依赖。最终日志、基线和关键 RED/GREEN 证据保存在 [验收证据目录](evidence/shot-workbench/README.md)。

相关 Backlot、CostTracker 和图像工具回归：90 passed、1 subtest passed。

功能审查发现的 1 HIGH、4 MEDIUM 已修复，并用真实工具的离线参数契约和最终完整回归复核；详见 [功能审查](shot-workbench-code-review.md)。[独立安全审查](shot-workbench-security-review.md)结论 **APPROVE**，2 HIGH、2 MEDIUM 均修复并独立复核，无剩余问题。引用内容读取的竞态采用每个请求的图片快照修复；供应商执行期间不持有项目事务锁。供应商接受请求后轮询超时的真实 adapter mock 回归验证费用保留且只提交一次。

## 实施细节与边界

- 编译 hash 绑定语义、身份、工具固定 backend/model、实际 refs 和 limits；revision、更新时间与价格独立绑定在一次性确认中。价格变化仍使已确认请求失效，避免把临时价格变化当成创作内容变化。
- 视频 `t2v` 不接收视觉引用；`i2v` 只传明确首帧/构图，`first_last` 只传两帧。独立身份引用无法传入时有 error，必须由用户或 Agent 明确调整路线。图片编辑与 multi_ref 使用对应工具真实支持的引用路径。
- 生成片段的时长与剪辑镜头时长可不同；`generation_context.generation_duration_seconds` 显式记录供应商时长，不自动把 6.8 秒镜头改成别的时间长度。
- H3 Max 缺少有效实际报价时保持 unknown 并阻止生成；任何费用确认都不能绕过 CAP。
- unknown 请求保留费用预留并阻止新请求重复计费；费用核对与恢复需人工处理，没有自动重试。当前没有跨供应商事务撤销能力。
- 简化人体不包含手指/表情；pose JSON 为 `openmontage-joints-v1`，不宣称直接兼容任意 ComfyUI OpenPose 工作流。只有明确 control mapping 的工具接收 depth/pose；其他工具只接收实际可用的构图和相机说明。
- 浏览器测试的 Amor/Fati 身份图是本地色块 fixture，验证引用分组；不表示真实 Amor 候选已通过艺人身份审批。
- 图像上传限 PNG/JPEG/WebP、12 MB 与 1600 万像素；工作台请求体 24 MB；舞台导出每边最多 4096px。
- 当前尚未执行真实付费生成，供应商返回与画面质量没有被实测。所有调用映射、预算与来源链已通过离线 mock/native schema 测试。

## 交付

拟提交信息：`feat: add shot workbench with project-owned assets`。

任务原文要求输出 `shot-workbench.patch`，因此开发验收时生成了该文件并在基线临时 Git 索引验证。用户随后明确要求直接集成当前本体，当前交付以本体代码和 Git 提交为准，无需手动应用补丁。补丁只作为本地迁移记录保存在 `.git/agent-imports/`，不进入软件提交。

旧 `automv-upgrade.patch` 的 32 文件变更经 stable patch-id 与当前历史 `2bface7..89420e3` 比对完全一致，逆向应用检查通过，已完整落地。没有再次应用旧变更。两份补丁及旧审批计划保留在本地集成记录目录；来源核对和结果见 [本体集成记录](shot-workbench-integration.md)。29 个个人资产文件在本体退出索引但保留原字节，Git 历史未改写。

## 已批准的资产解耦

- 应用软件、作品资产、用户复用库分别存放。默认数据根为 `~/OpenMontageData`，项目与库根可独立配置；配置读取不会自动创建目录或迁移数据。
- 项目通过显式库身份、固定版本、alias 与 SHA 清单导入本地快照；库变化不影响已有作品。需要修改时先 fork 为项目私有资产，没有全局同名回退。
- 艺人身份及环境等通用参考均验证固定版本。正式/候选状态保留；导入快照的 CLI 和直接声音写 API 拒绝修改，项目 ledger 可追加。成本、候选和输出只记录实际使用的来源。
- 软件提交与分发包排除真实艺人、项目、音乐与用户复用库；测试启动前使用隔离数据目录。
- 实际复制迁移完成：1,519 个文件，2 个作品项目，3 份用户艺人资料，3 个固定版本项目绑定，235 个明确引用改写。独立复核再次检查全部 1,519 个源/目标 SHA 和 3 个绑定，源文件未变。
- 数据目标：`/Users/geesh/OpenMontageData`；外部报告：`migration-reports/migration-5ff3abc900734b0388a59e0643ba5ce3.json`。报告仅保存路径、哈希与结果，媒体和创作正文不进入代码库。
- 1,133 个复核记录：历史文字路径 774、未结构化代码/文本 12、归档输入 173、服务商节点图 173、节点 Schema 1。它们保留原内容；再次执行旧代码或复用旧输入前按报告核对。本次成功只证明复制、哈希、明确引用映射与固定绑定，不能证明所有旧工作流已可跨安装运行。
- 独立 [功能审查](asset-decoupling-code-review.md) APPROVE（2 MEDIUM 均修复）；独立 [安全审查](asset-decoupling-security-review.md) APPROVE（2 HIGH、4 MEDIUM 均修复），未解决问题 0。审查仅用合成数据；实际迁移由主任务执行并复核。

解耦后的全量合同和覆盖率以本节后续最终记录及 `logs/asset-decoupling-*.log` 为准；上方 1547 项是解耦前已完成的历史验收，保留用于追踪范围变化。

### 解耦后最终验收

**1655 passed、7 skipped in 124.47s**；七个既有可选跳过与基线一致。Chrome **6 passed in 23.43s**；Node 舞台 **9 passed**，行/函数覆盖率 100%、分支 90.91%。相关 Backlot、watch/gate 与音乐库回归 **50 passed**。该全量运行在所有代码修复完成后执行。

| 模块 | 行与分支综合覆盖率 |
| --- | ---: |
| `backlot/workbench.py` | 92% |
| `lib/artist_profile.py` | 86% |
| `lib/asset_migration.py` | 90% |
| `lib/paths.py` | 100% |
| `lib/project_assets.py` | 86% |
| `lib/shot_compiler.py` | 98% |
| `lib/shot_execution.py` | 83% |
| `lib/shot_spec.py` | 85% |
| `lib/shot_workbench.py` | 88% |
| `scripts/export_artist_update.py` | 87% |
| `tools/analysis/shot_compiler.py` | 100% |
| 合计 | **88%** |

```sh
python -m coverage run --branch --source=lib.shot_spec,lib.shot_workbench,lib.shot_compiler,lib.shot_execution,backlot.workbench,tools.analysis.shot_compiler,lib.project_assets,lib.asset_migration,lib.artist_profile,lib.paths,scripts.export_artist_update -m pytest tests/contracts -q
python -m coverage report
```

完整迁移 receipt 仅位于用户数据目录，独立校验 summary 见证据目录 `logs/asset-decoupling-migration-verification.json`。本体与开发工作树中的 29 个原个人文件仍在磁盘；本体已退出其 Git 索引，只有 `_template` 保持艺人模板跟踪。生产项目和原始个人资产的内容保持原样。


### 当前本体集成验收

用户于 2026-10-07 批准直接合入本体并提交后，当前 `/Users/geesh/projects/OpenMontage` 的完整合同重新通过：**1655 passed、7 skipped in 76.09s**；真实 Chrome **6 passed in 23.65s**。默认外部数据目录与三个真实项目绑定已用本体代码校验。当前验证日志及来源分析见 [本体集成记录](shot-workbench-integration.md)。
