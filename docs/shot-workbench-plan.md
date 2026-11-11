# Shot Workbench 实施计划（Gate 1 审批稿）

日期：2026-10-06。用户已批准本 Gate 1 计划；以下保留审批时的基线与范围，当前状态见 [验收记录](shot-workbench-verification.md)。范围判定：large；新增文件契约、本地写入 API、预算执行入口和 Three.js 依赖，覆盖 WP1–WP7。

Research 与 Plan 已完成；本计划通过后执行 TDD → Review，提交前另行 Gate 2 确认。当前仅完成研究、基线验证和本计划，未写实现代码、未调用付费生成、未提交。

## 基线与交付

- 当前分支：`codex/automv-upgrade`；基线：`89420e3bf2c7a65e5b970bb2122a1cc70ed895f1`。
- 规格中的 `53729ce` 不在本地历史中。以当前基线实施并记录差异，不重置现有代码、不将缺失的提交当成已验证基线。
- 当前已有 AutoMV 升级的四种 `render_route`、`candidates[]` 和艺人档案基础设施，采用兼容扩展。
- 现有未跟踪的 `automv-upgrade.patch` 属于用户现有内容，保持原样，不纳入工作台补丁。
- 建议 Gate 1 通过后，基于当前 SHA 创建独立工作树和 `codex/shot-workbench` 分支，隔离本次实现。
- 基线合同测试：`1360 passed, 7 skipped`；日志：`/tmp/openmontage-shot-workbench-baseline-tests.log`。
- Gate 2 前提供未提交 diff、测试报告和拟定提交信息；批准提交后输出 `git diff --binary <上述基线> HEAD` 的 `shot-workbench.patch`，附基线 SHA、摘要和限制。不能声称该补丁以不可用的 `53729ce` 为基线。

## 架构决策与契约边界

### 文件正本和锁

- `projects/<id>/shots/<shot_id>.json` 是唯一镜头单正本；CLI、Agent 和 Backlot 使用同一读写库。浏览器仅持草稿，最终 prompt 没有可编辑入口。
- `lib/shot_spec.py` 管 schema、`from_scene`、读写和 revision。原子替换文件；保存要求调用者拥有该镜头 lease，并比较正本 revision。一次成功保存只增加一次 revision。
- `lib/shot_workbench.py` 管接管、lease、交接和 CLI；`lib/shot_execution.py` 管编译上下文快照、报价、确认、执行和来源记录。它们提供工具与持久化能力，不决定生产阶段或创作路线。
- `.lock` 保留 `holder/since/reason/base_revision`，增加内部 lease 身份及更新时间。记录交接起始镜头快照，用于 release 输出 JSON 路径级改动；未更新超过 2 小时标为失效。`steal` 必须有原因，写交接审计；正在执行的付费请求不得因普通过期检查被重复启动。
- 镜头 lease 跨进程；项目短事务锁只保护预算、asset manifest、decision log 等共享文件的读改写，绝不跨付费调用持有项目锁。Agent 的 save 和用户 API 同样检查 lease。

### 纯编译和显式快照

- 保留 `lib/shot_prompt_builder.py` 的五层模型和现有调用。`lib/shot_compiler.py` 在其上增加身份裁剪、参考图路由和 lint，不替换旧工具调用逻辑。
- `compile(spec, tool_name)` 只消费 spec 中显式保存的编译上下文快照；也允许测试传入等价的不可变上下文。不得读取文件、环境变量、registry discovery、`get_info()` 或实时艺人档案。
- `from_scene` 或显式 `hydrate` 准备艺人身份、locked traits、引用身份和所选工具的 backend/model、limits、估价依据快照。工具 `estimate_cost` / `dry_run` 在此执行边界调用；纯编译返回该快照报价。这样保留“使用现有 estimate_cost”要求，并保证相同输入输出一致。
- 自动 provider 选择只在 hydration 中按当前既有规则解析并固定；不静默更换付费 route、backend 或 model。执行前重新核对真实工具、报价和 refs；上下文变化须重新编译。
- 编译返回 `prompt/negative_prompt/payload/lint/compiled_hash/estimated_cost`。`payload` 是稳定的 provider 无关数据，`tool_inputs` 由服务端适配为现有工具参数；本地文件转 URL/data URI 放在执行适配层。
- `compiled_hash` 覆盖规范化的 spec 语义、工具具体 variant、limits、身份快照、实际选中 refs 的哈希及报价依据，排除无关的时间戳和 UI 状态。`reference_map` 只映射真正传入的图片，不能保留被截断的图。
- 结构与语义分开校验：保存必须通过 shot schema；compile 可以接收未保存草稿并列出结构/语义错误；render 只能读取正本，核对 revision/hash，并拒绝任意 lint error。
- `movement.cause` 对非 static 必须存在；允许空字符串作为旧 scene 的结构有效草稿，编译对空白报 `MISSING_MOVEMENT_CAUSE`。从明确的 action/来源推导原因；无法确定就留空，不猜创作意图。
- `notes` 有长度上限，原样附末尾；其中的否定句仍受最终正向 prompt lint 检查，不悄悄重写用户附加说明。
- 艺人档案现有否定表达（含 `cast_prompt` 的 “do not blend”）在生成编译片段时改写为正向身份分离规则，或放入 negative 字段；不让正常 Fati/Amor 档案被自身 lint 拦截。

### 真实能力、路由与兼容

- `BaseTool` 新增可选 `limits` 描述并在 discovery 输出；生成工具只增加 metadata，不改变内部 provider 请求逻辑。工具缺失 limits 时最多一张参考图、不启用首尾帧/control，并明确 warn。
- limits 必须描述当前实现能接收的输入。Seedance 按具体 model 区分：2.0 最多 9 refs、2.5 最多 30；Veo refs 与 1080p/4K 模式的 8 秒约束保留；Kling 现有仅 t2v/i2v 单 `image_url`，不能宣称 multi-ref/首尾帧；H3 Max 最多 9 refs、时长约束 0.92–15 秒，缺报价时为 unknown。
- 主力图片工具位于 `tools/graphics/`，不是规格中的 `tools/image/`。至少覆盖当前 Seedream、OpenAI 图像、Gemini 图像、FLUX、Imagen 和 ComfyUI；引用支持以现有调用路径为准（FLUX 现有路径不消费 refs）。
- ComfyUI 现有工具并不普遍消费 depth/pose；只有已有 workflow 和显式输入映射能够接收时，才在 hydration 后声明相应 control 能力。其他工具只用 composition 和相机文字，不承诺任意工作流兼容。
- AutoMV 映射：`story_i2v → i2v`；`performance_lipsync → lipsync`；`cast_multi_ref → multi_ref`；`continuation → i2v` 并携带 `continuation_of` 和已验证的前镜头尾帧。只有确有两张有效首尾帧且工具支持时才使用 `first_last`。
- 尚无 AutoMV 字段的 scene 从 `required_assets`、shot language、描述与 asset manifest 构建。艺人 refs 必须保持 canonical 身份；Fati 只采用 canonical v3，Amor 全部非 canonical 时显示候选图和缺正式图 warning，不把候选改标为正式。
- `scene_plan/asset_manifest/character_design/artist_profile` 只做需要的 optional 字段扩展；旧 artifact 不被强制迁移。现有 candidates 的最大 3 个、attempt 1–3 规则保持，来源字段为 optional。
- `decision_log` 的 category 及交接 `timestamp/changed_fields` 做兼容扩展；`cost_log` 对现有运行用到的 `approved_tools/budget_warning` 与新来源数据做 optional 扩展，保留 `additionalProperties: false`。

### 执行、预算和本地 API

- `BaseTool.execute()` 本身主要负责事件，不等于已执行预算、ledger 或 refs 核对。工作台显式走现有 `CostTracker` 的 estimate/reserve/reconcile，并调用 artist ledger 记录，不跳过预算模式或 CAP。
- render 流程：读正本并校验 → 核对 refs 路径/内容 sha256 → 固定工具与报价 → lint → dry-run → 检查单次门槛/新付费工具/CAP → 请求确认或 reserve → 持久化请求 → 工具 execute → reconcile → 写结果、候选、来源和 ledger。
- 确认 token 为一次性、带 TTL、服务端持久记录，绑定 project/shot/revision/compiled_hash/refs sha256/tool variant/报价。镜头、引用文件内容或报价改变后失效；客户端不能篡改报价或上传最终 prompt。
- unknown 报价用 `null` 和明确 lint/error 展示，阻止付费生成；不能当 `$0` 自动放行。用户确认不会绕开 CAP。
- 工具现有 idempotency key 主要是哈希而非持久去重。额外请求记录防止双击、重放和跨进程重发；状态至少区分 pending/running/succeeded/failed/unknown。进程中断后的 unknown 状态不自动重复收费，保留核对入口和审计。
- Backlot 继续只监听 `127.0.0.1`。写 API 校验 Host/Origin 与持锁身份，限制请求体；项目、shot 和 refs 路径规范化后校验真实路径及符号链接边界。
- 上传只接受经过内容验证的 png/jpg/webp，限制字节与像素尺寸，按 sha256 去重，写 `assets/refs/`。艺人正式图只读展示；引用不可让用户覆盖 artist profile/bible。

## task_list：薄纵向切片

每个切片先写新行为的失败测试，再实施到通过，随后小范围回归。WP7 验证贯穿所有切片，最后补完整验收与审查；不是最后才开始测试。

| 顺序 | 对应 WP | 可用切片 | 主要文件 | 依赖 |
| --- | --- | --- | --- | --- |
| T01 | WP1、WP3、WP7 | 无浏览器读取、初始化和编辑单镜头，revision 冲突可复现 | shot schema、`lib/shot_spec.py`、`lib/shot_workbench.py`、contract tests | 无 |
| T02 | WP2、WP7 | CLI 编译、lint 和 dry-run，用真实 scene 快照验证 | `lib/shot_compiler.py`、`lib/shot_execution.py`、BaseTool/生成工具 limits | T01 |
| T03 | WP3、WP7 | Agent/用户接管、release 差异与 decision log 交接 | lease/CLI、decision schema、跨进程锁测试 | T01 |
| T04 | WP4、WP7 | 浏览器打开同一正本，编辑保存、编译预览、SSE 刷新 | `backlot/server.py`、`backlot/ui/workbench.*`、API tests | T02–T03 |
| T05 | WP4、WP7 | 上传 refs、看来源链和艺人正式图，按真实 limits 编译 | refs 存储、workbench UI、upload/API tests | T04 |
| T06 | WP4、WP7 | mock 完整生成链含确认、预算、候选、cost/ledger 防重复 | `lib/shot_execution.py`、manifest/cost schema、render API tests | T05 |
| T07 | WP5、WP7 | 双人最小舞台→导出→同一镜头单→编译 multi_ref | 本地 Three.js、`workbench-stage.js`、纯 stage 数学模块、stage-export | T05；结果展示联调 T06 |
| T08 | WP6、WP7 | 六流程按同一规则处理复杂镜头，Agent CLI 可独立操作 | meta skill、12 director、6 manifest、`tools/analysis/shot_compiler.py` | T02–T07 |
| T09 | WP7 | Fati 三镜头、Amor/Fati 双人、真实浏览器、合同回归、审查与补丁 | fixtures、node tests、browser smoke、README/验收报告 | 全部 |

### WP1：镜头单、兼容来源和第一条 CLI 路径（T01）

1. 新建 `schemas/artifacts/shot_spec.schema.json`，覆盖所有规格字段；stage 和 compiler context 为 optional。复用 builder 景别和 movement 枚举，新增 orbit/follow/handheld；refs role 明确，notes bounded，stage 物体 ID 和 subject 引用可校验。
2. 实施 `from_scene(scene, project)` 和显式 hydration：读取 scene/manifest/artist/character，固定身份与引用快照，保留 AutoMV route 和候选关联。没有足够身份图或 movement cause 时保留可编辑草稿并报告。
3. 实施 load/save 的原子文件写、revision/lease 校验和错误对象；CLI `status/edit` 首先可用，edit 消费结构化 JSON，禁止最终 prompt 直写。
4. Tests：旧 artifact 校验保持通过；schema optional 扩展；from_scene 六流程代表 fixture；AutoMV route 映射；同 revision 双写只成功一次；不写真实生产项目。

### WP2：编译器和可核对的能力（T02）

1. 增加 BaseTool optional limits/get_info 暴露；逐个从现有 execute 输入审计 seedance/veo/kling/h3_max 与主力图像工具，记录 variant 约束。未知 limits 用最保守行为并 warn。
2. 五层 prompt 编译；visible_parts 身份裁剪；完整脸保留身份，hands/back 仅服装、手部/背部相关 locked_traits；多艺人身份保持独立。
3. identity → first_frame → wardrobe → environment → style 是首要排序；composition/last_frame/depth/pose 使用固定次序和独立语义路由，不让 depth/pose 占普通参考图位置。稳定截断并列出每张被丢图；若截断让人物失去全部 identity refs，追加针对该人物的 lint。
4. exclusions 移 negative 或通过有限、可测试的正向词表改写；无法改写明确 warn 丢弃。lint 检查最终正向文本的 no/without/not/中文否定+名词，不进行无根据的大范围自然语言重写。
5. 覆盖全部八类编译规则：cause、人像缺失却有运动、景别不含脸但 identity refs、灯亮/不亮矛盾、无人物与人物主体矛盾、色板与时间矛盾、精细手部特写、error 禁生成。中文和英文 fixture 均覆盖；避免把否定词作为普通词片段误匹配。
6. CLI `compile/dry-run` 输出 machine-readable 结果、hash、工具和费用。干跑不 reserve、不收费、不写结果资产。
7. Tests：hash 稳定与变化、禁止文件/env/registry 访问、每条 lint、各 limits/variant、route 参数映射、refs 截断后的 reference_map，Fati 历史错误样本。

### WP3：接管和交接（T03）

1. 完成 `open/status/release/steal`，增补 `edit/compile/dry-run` 与 heartbeat/holder 参数；open 初始化缺失镜头单并给 user lease，复用 Backlot 启动方式打印 URL。
2. Agent 持锁时用户只读并显示“Agent 正在编辑”；用户持锁时 Agent 只能读。允许同一持有者续租，拒绝伪造过时 lease。
3. release 输出 base_revision 到当前 revision 的结构化字段差异，并 append decision log，含 holder、时间、原因、shot、变更路径。steal 的 reason 必填且审计保留旧 holder。
4. Tests：两种 holder、同进程及跨进程竞争、过期/续租/强制接管、release 的增删改、多次交接审计、异常写入恢复。

### WP4：原生 Backlot 工作台与付费入口（T04–T06）

1. 加入规格全部 7 个路由：页面、read、PUT、refs、compile、render、stage-export；界面所需 lease/heartbeat/release 使用同一库的明确 API。read 返回 spec/锁/工具/只读身份 traits/候选和废片来源。
2. 页面 `workbench.html/.css/.js` 使用现有原生 JS/lib 和 SSE：参考图板、字段表单、3D 区、只读编译预览四区；中文文字，显式保存，409 保留用户草稿并显示当前版本，不自动覆盖。
3. 表单覆盖主体/visible_parts/造型、framing/movement、environment/exclusions/notes、route/target tool。locked_traits 和艺人正式图只读。编译展示真正传入 refs 的排序、negative、lint 与费用；error/unknown 费用禁生成。
4. 实施安全上传和来源链显示；对 sha256 变化、路径穿越、symlink、伪图片、重复内容、超大/像素炸弹均验证。图片和 subject/role 关联保存为结构数据。
5. render 执行上述预算/确认/防重复协议，输出固定项目路径，来源写入 asset_manifest candidate 和 cost_log；已有候选/废片均可读回。artist ledger 使用实际参与身份和实传 refs。
6. Tests：TestClient 409、锁只读、mutation origin、upload 校验、server-side compile、超门槛/新付费工具必须确认、CAP 不能绕过、token TTL/单次/错镜头/错 hash、双击重放、reserve/reconcile、工具失败和未知请求状态。全用 mock，无网络。

### WP5：最小 3D 舞台（T07）

1. 固定 Three.js `0.181.2`，与现有 world-runtime 对齐。只本地 vendor `three.module.min.js/three.core.min.js/OrbitControls.js/TransformControls.js`，附 LICENSE、来源及完整性记录；本地 importmap，无 CDN、无构建步骤。
2. 地面网格、按主体着色的人体简化关节、站/坐/走/跪/伸手预设、关节调整；方块/圆柱/平面；对象选中/移动/旋转；相机视角预览。按当前 Three API 挂 `TransformControls.getHelper()`。
3. stage 保存米制物体/姿势/相机/subject_ref；读取正本即可重建。stage 数据有效范围和数量限制在服务端校验。
4. 36×24 mm 传感器按目标画幅居中裁切：宽画幅固定 36 mm 并裁高，窄画幅固定 24 mm 并裁宽；`fov_y = 2*atan(effective_sensor_height/(2*lens_mm))`。纯函数计算 FOV、相机基向量、投影坐标和 placement；使用 camera-space 左中右、相机距离前中后及朝向，不能用 world x 代替。
5. 导出 composition PNG、线性相机距离归一化近白远黑 depth PNG、2D 关键点 JSON/骨架 pose PNG、相机 JSON。depth 不直接采用默认透视 MeshDepthMaterial 值；遮挡/出画面关键点带可见性标志。
6. stage-export 一次 revision 检查后写图片/JSON refs、stage、subjects placement；过时导出返回 409。没有 control 能力的工具仅传 composition（明确可作为 i2v 首帧的映射）与相机文字；有明确 workflow control mapping 才传 depth/pose。
7. Tests：node --test 验证 FOV/画幅/投影/placement/序列化、固定双人姿势；实际浏览器导入、加人体、导出后读正本检查 composition/pose。系统 Chrome 可使用 Playwright `channel=chrome`，无需下载缺失 Chromium；SSE 页面使用 DOM/事件等待，不用 networkidle。
8. 限制写入文档：简化骨架不表达手指，depth/composition 是布景参考，不承诺精细人体解剖或任意 ComfyUI 控制效果。

### WP6：六流程接入（T08）

1. 新建 `skills/meta/shot-workbench.md`，明确 ≥3 refs、多人物、精确机位/走位、首尾接续、两次打回触发；Agent 给接管理由/地址，等待 release，读取 diff 后继续现有阶段。
2. 六流程 scene/asset director 加“结构化镜头单→compile/lint→既有工具 execute”指令；无需浏览器也走相同数据。animated-explainer 的实际 director 路径为 `skills/pipelines/explainer/`。
3. 新增零成本 `ShotCompiler` BaseTool 薄适配（`tools/analysis/shot_compiler.py`），仅暴露纯编译能力给 registry，不作 provider/创作/阶段选择；`required_tools` 必须实际存在的 `shot_compiler`，不能将自然语言规则伪装成工具名。
4. 六个 manifest 的 scene_plan/assets 按现有格式加入 tools_available/required_tools、required_skills、review_focus；不新增生产 pipeline、不强迫简单镜头开页面。
5. asset/candidates 来源 optional：shot spec revision、compiled hash、带 role/subject 的真实 references、parent_asset 和现有候选编号。已持有 provenance 的旧字段继续读。
6. Tests：六 manifest/tool/skill 引用真实存在，旧流程与 artifacts 校验、复杂镜头要求可发现、简单镜头不被新必填字段阻断。

### WP7：验收、审查和交付（T09）

1. 每片的新合同测试离线执行；结构、refs、锁、预算与 token 测试纳入 `tests/contracts/`，node stage 测试独立可复现。新增核心逻辑覆盖率目标 ≥80%，重点覆盖资金及并发失败路径。
2. 真实 Fati fixture 从 `projects/test-zhenjiao-fati/artifacts/scene_plan.json` 和只读 artist/manifest 抽取到隔离测试项目；实际 ID 是 `sh-s01-01/sh-s05-02/sh-s06-04`。不修改原生产 project、canonical 图、ledger 或 render。
3. `sh-s01-01`：原否定/无人物却运动触发 error/warn，改结构字段后编译通过；`sh-s05-02`：缝补 close_up/insert 触发精细手部 warn，手腕镜头不拼整段脸；`sh-s06-04`：完整身份、role 排序、工具限额截断和实际 refs map 可核对。
4. Amor/Fati 双人舞台：导出 composition/pose，compile multi_ref；reference_map 按人物分开，Amor 非 canonical 仍明确标候选。测试用固定快照和 mock 工具，不发付费请求。
5. 关闭浏览器后 CLI `edit/compile/dry-run` 同样工作，API 与 CLI 对相同正本输出相同编译结果。至少一次真实浏览器冒烟和导出证据，包含中文表单/只读预览/保存冲突/舞台重建。
6. 完整 `pytest tests/contracts`，比较基线 1360/7；列任何与本次无关且基线上复现的失败。保留基线、增量、全量、node、浏览器日志及验收摘要。
7. ECC code-reviewer 和 security-reviewer 必须执行；本变更触及用户输入、文件系统路径、付费 API 和确认授权。解决 CRITICAL/HIGH，必要时 Python reviewer，审查 optional schema 兼容、死锁、TOCTOU 和重复收费。
8. 更新 Backlot README/CLI 示例、skill index 与已知限制；向用户呈现完整 diff 摘要、验证结果和拟定 conventional commit，再等待 Gate 2。确认后提交并生成基于当前 SHA 的二进制补丁。

## 关键风险与控制

| 风险 | 控制与验收证据 |
| --- | --- |
| 缺失旧基线，补丁误混 AutoMV | 当前 SHA 独立工作树；补丁元数据记录实际基线；不触碰用户现有 patch |
| 编译表面纯函数但隐读环境/档案 | 显式快照；测试禁止 open/env/discover；variant 和估价配置纳入 hash |
| 工具 limits 与真实 execute 能力不一致 | 源码与官方契约对照；每工具输入适配 mock 断言；不宣称 Kling/FLUX/ComfyUI 不存在能力 |
| stale lease 与 shared artifact 并发丢更新 | 镜头跨进程 lease、revision、短项目事务；真实多进程测试；付费过程不持项目锁 |
| 未知报价/确认重放造成收费 | unknown 阻断；token TTL/hash/报价绑定并原子消费；持久请求状态，不自动重发 unknown |
| 身份引用截断串脸或改用候选正式图 | reference_map 仅实传 refs；缺身份 lint；canonical 规则和双人 fixture 验证 |
| 静默改 route 或 parent 来源不可信 | AutoMV 显式映射；前镜头 tail 和所有 refs hash 核对；compile 展示降级/丢图 |
| 上传或 Stage-export 写出项目目录 | realpath/符号链接校验、格式/尺寸/体积限制、revision 一次事务与安全审查 |
| SSE 覆盖未保存编辑或 lock 状态过时 | 保留本地 dirty 草稿并提示外部更新；刷新只读锁；冲突显式 409 |
| 舞台视角/深度图具有误导性 | 可复算 FOV/相机空间投影、线性 depth；明确粗骨架限制；真实浏览器导出测试 |

## Gate 1 要批准的内容

批准本计划即授权实施全部 WP1–WP7、使用当前 SHA 隔离工作树、本地 vendor 固定 Three.js、离线 fixture/mock 验证及一次真实浏览器冒烟。实施过程中不调用付费生成，也不改真实 Fati/Amor 生产资产。提交和最终 HEAD 补丁仍在 Gate 2 前等待确认。

## Approved scope amendment: asset ownership

The user identified application/user-data coupling and explicitly approved the proposed decoupling. Before Gate 2: centralize external data paths; implement project-private assets and explicit fixed-version library publication/import/fork; scope workbench identity reads and ledger writes to the project; replace real artist test dependencies with synthetic fixtures; copy and hash-verify legacy data before removing personal artists from software tracking; update exporter/docs and rerun contracts/browser/security review. Source data is retained. No history rewrite or paid generation is authorized. The original implementation baseline remains `89420e3bf2c7a65e5b970bb2122a1cc70ed895f1`.


## Gate 2 approval and direct integration

On 2026-10-07 the user explicitly instructed: merge into the active application checkout and commit, then clarified that standalone patch distribution was unnecessary and asked for the imported-patch provenance to be checked. Gate 2 is approved. The existing AutoMV patch matches already committed history; Shot Workbench and approved asset ownership changes are integrated into `/Users/geesh/projects/OpenMontage` on its current `codex/automv-upgrade` branch. Production data and legacy personal files are retained. See the integration record for the source checks and primary-checkout validation.
