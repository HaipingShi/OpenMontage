# Shot Workbench 浏览器与舞台验收

验收环境：本机 Google Chrome，Miniconda base Python 3.13.5 / Playwright 1.58.0，真实临时 Backlot/FastAPI 进程，原生 ES modules，固定本地 Three.js 0.181.2。浏览器拦截所有非 loopback 网络请求；生成器为本地 MockGenerator，不调用媒体供应商。

```sh
SHOT_WORKBENCH_BROWSER_EVIDENCE=/tmp/openmontage-shot-workbench-browser /opt/homebrew/Caskroom/miniconda/base/bin/python -m pytest tests/backlot/test_shot_workbench_browser.py -q -x -s
```

最终结果：全部功能及安全修复后的重新验证 **6 passed in 23.43s**；每个测试断言无 `pageerror`，无外部浏览器请求。临时服务与 Chrome 在 fixture teardown 关闭。

- 四区页面、人体 reaching 姿势及左肘角编辑；一人体 composition/depth/pose PNG 和真实 pose JSON/camera 来源导出。
- Amor/Fati 两人体 sitting/reaching，舞台保存与重载重建；`multi_ref` 实际编译 payload 的 `reference_map` 按 `amor`/`fati` 分开。截图中的身份 PNG 是本地测试色块，未冒充真实人物画像。
- 外部 revision 通过 SSE 通知，未保存草稿保留；保存返回 409 后仍保留草稿，明确重新加载后得到当前正本。
- Agent lease 下上传、字段、舞台、导出、保存、生成均禁用；实际点击编译返回 200 且只读 PRE 可见。
- 上传参考图后设 wardrobe/fati、选择 multi_ref 并自动选择工具；预览和费用确认展示固定 `mock-model` / `offline`。确认前 mock execute 为 0，确认后为 1，确认复用相同 request_id；再次主动生成使用不同 request_id，第二候选与 mock execute 为 2。
- 模拟生成结果/费用 unknown 后显示请求 ID、错误与待核对费用；点击查询、页面刷新、重新编译与再次查询均复用原请求，mock execute 仍为 1。
- 嵌套候选每次 attempt 单独显示，包括废片、verification notes、parent_asset、compiled_hash 与只读完整审核/生成来源；兼容无嵌套的历史 asset。

[双人舞台与编译预览截图](dual-stage.png)

```sh
node --test --experimental-test-coverage tests/backlot/stage_math.test.mjs
```

结果：**9 passed**；stage-math.mjs **100% lines、100% functions、90.91% branches**。覆盖相机 FOV/传感器裁切、相机坐标投影与裁剪、五种姿势及 FK 关节、双主体 keypoints、相机方位描述、线性深度、分辨率边界、文件中可选 transforms 与向量关节重建。首次无模块测试 RED；新增可选 transforms 测试 RED 后补默认值和向量旋转，再 GREEN。

```sh
node --check backlot/ui/workbench.js
node --check backlot/ui/workbench-stage.js
```

语法检查通过。本地依赖来源、MIT LICENSE、npm SHA512 和逐文件 SHA256 见 `backlot/ui/vendor/three-0.181.2/integrity.json` 与 README。

范围边界：简化人体骨架与五种预设，不包含手指/表情/完整人体解剖；pose JSON 为 `openmontage-joints-v1`，不宣称可直接作为 ComfyUI OpenPose 输入。舞台导出图片每边最多 4096px。真实媒体供应商生成未运行。

`logs/` 保留基线合同、最终合同与覆盖率、相关回归、浏览器和 Node 日志，以及文件/锁、编译器、API、流程接入、身份来源和费用边界的关键 RED/GREEN 证据。入库日志仅移除了行尾空白，测试结果与错误内容保留。早期 RED 日志记录修复前的预期失败；最终合同日志代表交付状态。完整结论见 [验收记录](../../shot-workbench-verification.md)。

资产解耦后的最终结果、迁移 summary 与独立校验见 `logs/asset-decoupling-*`。真实迁移完整清单只保存在用户外部数据目录；证据目录没有真实媒体、艺人资料正文或提示词。

2026-10-07 本体集成后的完整验证：`logs/primary-contracts-final.log`（1655 passed、7 skipped）与 `logs/primary-browser-final.log`（6 passed）。源码及默认数据路径已在当前应用检出验证，导入补丁来源核对见 [本体集成记录](../../shot-workbench-integration.md)。
