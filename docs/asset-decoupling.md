# 用户数据、资产库与项目快照

代码仓库分发工具、Schema、技能、空白模板及合成测试数据。真实艺人资料、图片、音频、项目、生成结果和账本归用户的数据目录。默认布局为：

```text
~/OpenMontageData/
├── library/
│   ├── library.json                    # 稳定的 library_id
│   ├── artists/<artist-id>/            # 用户的艺人工作资料与原始历史账本
│   └── versions/artist/<id>/<version>/
│       ├── manifest.json              # library_id、固定版本、逐文件 SHA256
│       └── files/                     # 已发布版本的完整文件快照
├── projects/<project-id>/
│   ├── assets/bindings.json            # 显式库身份、版本、项目 alias 与文件哈希
│   ├── assets/artists/<alias>/         # 本项目拥有的艺人快照
│   ├── artifacts/                     # 项目创作与流程数据
│   └── assets/、renders/、shots/        # 项目素材、成片与镜头工作台数据
└── migration-reports/                 # 复制迁移报告
```

`OPENMONTAGE_DATA_DIR` 改变默认数据根；`OPENMONTAGE_PROJECTS_DIR` 和 `OPENMONTAGE_LIBRARY_DIR` 可分别显式覆盖项目与库目录。所有目录都应使用绝对路径。已有项目覆盖配置继续有效，迁移 CLI 的 `--data-dir` 则明确指定此次迁移的目标根。

辅助入口也使用同一项目根：atelier 脚手架与逐场景截图按项目名查找用户工作区，Kling 验收报告和 AutoMV 离线验收默认写在用户工作区，视频分析与角色预览的默认输出也位于该根。更换应用检出目录或调用时的工作目录，不会改变这些默认位置。

atelier 脚手架的显式 `--root /absolute/root` 保留原有 `/absolute/root/projects/<slug>` 约定；AutoMV 的显式 `--output`、工具的 `output_dir` / `output_path` 继续由调用者选择。截图脚本的显式 `--entry`、`--props`、`--public-dir` 相对路径按调用目录解析；`edit_decisions.bespoke` 内的相对路径按当前项目解析。生成的 atelier README 使用项目绝对路径和 `Path` 拼接，适用于仓库外项目。

项目生成只读取本项目的艺人快照。某个同名艺人没有项目绑定时，不会自动回退读取全局库。修改库中的工作资料或发布新版本，也不会悄悄改变已有项目。

## 发布、导入与本项目修改

资产可来自本地创建或外部引入。进入复用库时，显式发布一个固定版本；进入项目时，显式导入该版本与 alias：

```sh
python -m lib.project_assets publish /absolute/artist-source artist-id v1 --kind artist --library /absolute/OpenMontageData/library
python -m lib.project_assets import /absolute/OpenMontageData/projects/film artist-id v1 artist-id --kind artist --library /absolute/OpenMontageData/library
python -m lib.project_assets list /absolute/OpenMontageData/projects/film
```

版本包保留原图、艺人资料、候选与正式图状态以及来源哈希。导入快照包含来源 `library_id`、`asset_id`、`version`、源文件清单和项目文件清单；读取时校验文件。重复导入同一来源、版本和 alias 是幂等操作；同一 alias 不允许被另一版本、另一库或私有资料覆盖。新版本需明确使用新 alias。

导入快照的资料是只读的。需要本项目独立修改时先显式 fork：

```sh
python -m lib.project_assets fork /absolute/OpenMontageData/projects/film artist-id artist-local
```

`artist-local` 成为本项目的私有资料，仍保留源版本 lineage。项目内生成、评审和选用写入项目自己的艺人账本；不会写回库版本或另一个项目。`ledger.jsonl`、`releases.jsonl` 不参与固定素材快照哈希，原库历史账本保留在用户资料目录。通过 `--root` 或直接调用声音写入 API 同样受导入快照保护，拒绝在复制音频前生效。项目修改不会自动成为公共复用资产；需要复用时，再明确发布该目录为一个新的资产或版本。

## 从旧仓库迁移

迁移默认只做清点并写报告。它不调用生成服务、不执行源目录中的代码、不删除或修改旧文件，也不重写 Git 历史。

```sh
python -m lib.asset_migration migrate /absolute/old/OpenMontage --data-dir /absolute/OpenMontageData
```

报告位于目标根的 `migration-reports/`。核对清点结果后执行复制：

```sh
python -m lib.asset_migration migrate /absolute/old/OpenMontage --data-dir /absolute/OpenMontageData --apply
```

复制包括旧 `artists/` 中有真实 `artist.yaml` 的艺人目录，以及旧 `projects/` 中的项目目录；不复制分发模板 `_template` 和艺人根目录的说明文件。每个原文件先计算 SHA256；开始复制前重新清点，所有目标文件先统一检查冲突。新文件先写临时副本，核对 SHA256 后安装到目标路径；已有相同文件只核对，已有不同文件明确失败，不覆盖。复制完成后再次核对全部目标及源清单，才发布固定库版本并通过项目资产 API 建立绑定。

迁移版本名为 `sha256-<digest>`，由素材文件及规范化后资料的内容生成，排除共享历史账本。旧项目的 `generation_context.artist_ids` 和匹配真实艺人的 `character_actions` 用于选择绑定；引用中明确指向同一旧艺人的素材也会建立该艺人绑定。未匹配的普通角色不会被猜成库艺人；明确声明但不存在的艺人会报错。

实际文件引用只在明确范围内改写：本项目的旧绝对路径或 `projects/<本项目>/...` 改为项目相对路径；当前项目使用的旧 `artists/<id>/...` 改为 `assets/artists/<alias>/...`。艺人资料中的自身旧绝对路径可改为该艺人目录内的相对路径。改写不改变构图、动作、提示词、候选审核状态或正式身份图标记；报告记录每个原路径、新路径、源文件哈希和目标文件哈希。

带明确 `artist_id` / `base_artist_id` 的派生造型资料，以及带 `id` 和 `identity.bible` 的完整艺人资料快照，其艺人相对路径按显式所有者映射，不凭文件名猜测。普通项目相对输入逐项检查存在性；尚未生成的相对 `output_path` 保留为计划输出。

项目根的 `history/` 结构化归档保留原字节，以 `MANUAL_REVIEW_ARCHIVED_INPUT` 标记文件引用，不从旧归档建立当前版本绑定；`history2/` 没有此豁免。服务商节点图与节点 Schema 按明确的数据结构识别，节点连接不会被误当作本地图片路径；原结构保留并记 `MANUAL_REVIEW_PROVIDER_GRAPH` / `MANUAL_REVIEW_PROVIDER_METADATA`，再次使用前核对服务商本地输入。

创作摘要、提示词、历史来源字段及未结构化的代码/文本保持原内容。发现其中有旧路径时，报告标记 `MANUAL_REVIEW_*` 警告，提示再次作为输入使用前核对；迁移不会把一段创作文字当作文件名自动替换。历史 JSONL 账本按原字节保存。图片/视频等明确输入字段中的跨项目路径、无法映射的外部本地路径、缺失素材、路径穿越，以及任何源或目标符号链接，会明确失败。

每个警告提供原文件与 JSON pointer（结构化字段适用），可从外部报告逐项追踪。保留的源码、摘要或历史文字仍可能依赖旧安装路径，其中的运行时输入没有自动迁移；再次执行旧合成代码或复用其输入前需要按警告核对。已知成功边界是所清点文件的复制哈希校验、明确素材引用的映射及固定版本绑定，不能据此宣称所有旧代码和文字都已经可跨安装运行。

报告只保存路径、大小、哈希、绑定、改写清单、警告位置和结果，不写入提示词、艺人资料正文或媒体内容。`completed` 表示本次复制、校验和绑定全部成功；若发生后续失败，报告会保留已复制/验证的数量并标记 `failed`，不会把部分完成当成成功。重跑会验证已有文件并继续；用户后来修改过的目标资料仍受冲突保护。

如需严格应用某次 dry-run 的既定清单，可用 Python API `apply_plan(report)`；它会重新检查清单、范围和哈希，源有变化或清单被改动时拒绝执行。复制完成后仍保留旧目录，待用户自行核对与管理。把真实数据从当前 Git 索引中移除属于另一个明确操作；这不会清除旧提交中的内容，迁移程序本身不运行任何 Git 命令。

## 测试证据

```sh
python -m pytest tests/contracts/test_asset_migration.py --cov=lib.asset_migration --cov-report=term-missing -q
```

所有测试使用模板派生的合成人物、小型图片和临时项目。首次 RED 为缺失 `lib.asset_migration` 模块；重叠目标目录的失败报告写入源目录问题也由回归测试 RED 复现后修复；历史摘要/来源路径的保留策略由新增回归先 RED 再 GREEN 验证。

当前迁移契约：46 tests passed。最新全量覆盖率与迁移执行结果见 [验收记录](shot-workbench-verification.md)。覆盖目标冲突、源变化、复制校验、符号链接、穿越与范围限制、旧路径规范化、候选/正式状态保留、项目绑定、二项目账本隔离、幂等重试、CLI 默认 dry-run、历史来源警告和源目录原件保留。独立审查仅使用合成 fixture；主任务已按用户授权完成实际目录的复制迁移及独立哈希校验，旧文件保持原样。


## 本体集成与 Git 索引

复制及独立校验成功后，已在开发工作树与当前本体对 `artists/amor`、`artists/fati`、`artists/wanyu` 执行 `git rm --cached`。29 个文件的原字节仍在磁盘，真实生产项目保持原样；`.gitignore` 允许分发的艺人目录只有 `_template`，旧提交历史没有改写。

用户于 2026-10-07 明确批准直接合入当前本体并提交。代码现位于 `/Users/geesh/projects/OpenMontage`，默认使用已迁移的 `~/OpenMontageData`，无需另行手动应用软件补丁。复用来自独立用户库的固定版本，通过项目本地绑定读取。

原任务要求生成的 `shot-workbench.patch` 与已落地的 AutoMV 导入补丁，仅保留在本地 `.git/agent-imports/` 作为迁移记录，不纳入应用软件。完整核对与集成结果见 [本体集成记录](shot-workbench-integration.md)。

用户进一步明确后续开发无需输出补丁。该约定已写入 `AGENT_GUIDE.md`：后续变更直接在当前本体开发、验证和提交，仅在用户明确要求时生成补丁。
