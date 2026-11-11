# Shot compiler TDD evidence

Source plan: [shot-workbench-plan.md](shot-workbench-plan.md), WP2 / T02 and the discoverable wrapper from T08.

The implemented journey is: an Agent or user edits a structured shot, previews a deterministic prompt and its exact reference ordering, and sees actionable lint before any generator is invoked. Tests run entirely offline with explicit identity, tool, and quote snapshots.

| Guarantees | Test target | RED evidence | GREEN evidence |
| --- | --- | --- | --- |
| Five prompt layers, bounded raw notes, reference role sorting/truncation/map, cropped identity, Fati canon normalization, exclusions, eight lint families, route/control/variant bounds, deterministic hash and zero-cost wrapper | `tests/contracts/test_shot_compiler.py` | Initial run: `1 failed, 48 errors` because the compiler and metadata did not exist (`/tmp/openmontage-shot-compiler-red.log`) | `49 passed in 0.07s` (`/tmp/openmontage-shot-compiler-green.log`) |
| Root schema palette strings / numeric camera angles, text-valued visible traits, malformed draft previews, nonfinite duration previews, complete-profile and metadata compatibility | Same target | `3 failed, 61 passed` (`/tmp/openmontage-shot-compiler-compat-red.log`) | `64 passed in 0.09s` (`/tmp/openmontage-shot-compiler-compat-green.log`) |
| Invalid framing/movement, explicit Veo resolution-tier duration constraint, initialization warnings, free-text lighting plus lighting key | Same target | `8 failed, 64 passed` (`/tmp/openmontage-shot-compiler-semantics-red.log`) | `72 passed in 0.08s` (`/tmp/openmontage-shot-compiler-semantics-green.log`) |
| Generation handles preserve timeline duration; an invalid NaN duration remains a serializable error preview | Same target | `2 failed, 72 passed` (`/tmp/openmontage-shot-compiler-duration-red.log`) | `74 passed in 0.09s` (`/tmp/openmontage-shot-compiler-duration-green.log`) |
| Real Seedance/Kling/Veo adapter inputs agree with route-specific reference provenance; unreachable identity blocks generation; same-path metadata is retained; frame/control metadata records real submissions | Same target | `9 failed, 75 passed` (`/tmp/openmontage-shot-compiler-route-red.log`) | `84 passed in 0.15s` (`/tmp/openmontage-shot-compiler-route-green.log`) |

The validation command for each cycle was `.venv/bin/python -m pytest tests/contracts/test_shot_compiler.py -q`. RED and GREEN logs were saved before and after each production change. No checkpoint commits were made because the feature pipeline's pre-commit Gate 2 remains pending.

Related existing contracts passed: `.venv/bin/python -m pytest tests/contracts/test_shot_compiler.py tests/contracts/test_atlas_tools.py tests/contracts/test_comfyui_tools.py tests/contracts/test_phase0_contracts.py tests/contracts/test_phase1_contracts.py tests/contracts/test_automv_upgrade.py -q`: `361 passed in 19.07s`. This run contained 72 compiler tests; the subsequent generation-duration cycle adds two tests, both passing. Log: `/tmp/openmontage-shot-compiler-regression.log`.

Coverage was measured using the standard-library tracer because pytest-cov/coverage was absent from the workspace runtime:

```text
.venv/bin/python -m trace --count --missing --summary --coverdir /tmp/openmontage-shot-compiler-coverage --file /tmp/openmontage-shot-compiler-trace --module pytest tests/contracts/test_shot_compiler.py -q
72 passed in 1.08s
lib.shot_compiler: 98% executable-line coverage
tools.analysis.shot_compiler: 100% executable-line coverage
```

Log: `/tmp/openmontage-shot-compiler-coverage.log`. This is line coverage, not branch coverage; the final feature-wide report may supersede it with coverage.py measurements.

The compiled hash includes semantic spec data, identity snapshots, resolved limits, concrete tool options, generated prompt/negative and selected-reference metadata. It excludes revision, updater, update time, and the monetary quote. The execution confirmation separately binds the quote and revision, so a changing quote cannot reuse a confirmation. Unknown quote snapshots remain `null`, with an explicit warning, and the execution boundary is responsible for refusing an unquoted paid call.

Current provider metadata is intentionally conservative. Kling's present wrapper supports only t2v/i2v; FLUX and bundled ComfyUI image workflows consume no image references. Gemini edit capabilities and Atlas reference bounds are model-specific. Depth/pose requires an explicitly hydrated workflow mapping. These metadata additions do not alter provider execute methods. No real artist profiles, assets, project artifacts, ledgers, or paid endpoints were changed or called.

The route-aware repair was triggered by ECC code review: video t2v submits no images; i2v consumes an explicitly designated first/composition image; first_last consumes its two frames; multi_ref consumes ordinary references. Independent identity references lost to a route produce an error requiring an explicit user route/reference change. Other unreachable refs produce a warning. `reference_images` contains unique submitted image paths; `references` and `reference_map` also preserve actually submitted last-frame and supported control metadata. Root independently reviewed this compiler repair because its author also discovered the gap. Feature-wide regressions remain root's responsibility after integration changes.
