"""Real Chrome regressions for the file-backed workbench and stage export.

All media is generated locally; outbound browser requests are rejected.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

from lib.shot_spec import atomic_json, from_scene
from lib.shot_workbench import acquire
from PIL import Image

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def workbench_server(tmp_path):
    project = tmp_path / "projects" / "stage-film"
    (project / "artifacts").mkdir(parents=True)
    atomic_json(project / "project.json", {"project_id": "stage-film", "title": "舞台冒烟", "pipeline_type": "cinematic"})
    scene = {"id": "s01", "description": "Two people standing on a quiet stage", "start_seconds": 0, "end_seconds": 5,
             "shot_language": {"shot_size": "wide", "camera_movement": "static", "lens_mm": 50}, "required_assets": []}
    atomic_json(project / "artifacts/scene_plan.json", {"version": "1.0", "scenes": [scene]})
    spec = from_scene(scene, project)
    spec["target_resolution"] = "640x360"
    spec["subjects"] = [{"kind": "character", "ref_id": name, "visible_parts": ["full_body", "face"], "action": "standing"} for name in ("amor", "fati")]
    spec["target_tool"] = "mock_video"
    for name, colour in (("amor", "#c5985b"), ("fati", "#91b9ea")):
        relative = f"assets/refs/{name}.png"
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32), colour).save(target)
        spec["references"].append({"asset_path": relative, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "role": "identity", "subject_ref": name})
    atomic_json(project / "shots/s01.json", spec)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = dict(os.environ, OPENMONTAGE_PROJECTS_DIR=str(project.parent),
               SHOT_WORKBENCH_MOCK_CALLS=str(tmp_path / "mock-calls.json"))
    log = (tmp_path / "backlot.log").open("w")
    # The real server/browser run against the same offline mock tool used in
    # API contracts. No provider is discovered or executed in this process.
    startup = """
import sys
import json
import os
from pathlib import Path
from types import SimpleNamespace
from tests.contracts.test_shot_workbench_api import MockGenerator
from lib import shot_execution
from lib.config_model import BudgetConfig
import uvicorn
tool = MockGenerator()
tool.input_schema = {'properties': {**tool.input_schema['properties'],
    'model': {'type': 'string', 'default': 'mock-model'},
    'backend': {'type': 'string', 'default': 'offline'}}}
execute = tool.execute
def tracked_execute(inputs):
    if Path(os.environ['SHOT_WORKBENCH_MOCK_CALLS']).with_name('mock-unknown.flag').exists():
        tool.calls.append(inputs)
        Path(os.environ['SHOT_WORKBENCH_MOCK_CALLS']).write_text(json.dumps(tool.calls))
        raise RuntimeError('模拟费用待核对')
    result = execute(inputs)
    Path(os.environ['SHOT_WORKBENCH_MOCK_CALLS']).write_text(json.dumps(tool.calls))
    return result
tool.execute = tracked_execute
registry = SimpleNamespace(ensure_discovered=lambda: None, get=lambda name: tool if name == tool.name else None,
    get_by_capability=lambda capability: [tool] if capability == tool.capability else [])
shot_execution.get_registry = lambda: registry
shot_execution.get_budget = lambda project: BudgetConfig()
uvicorn.run('backlot.server:app', host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
"""
    server = subprocess.Popen([sys.executable, "-c", startup, str(port)], cwd=REPO, env=env, stdout=log, stderr=log)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=1):
                    break
            except Exception:
                if server.poll() is not None:
                    raise RuntimeError((tmp_path / "backlot.log").read_text())
                time.sleep(.1)
        else:
            raise RuntimeError("Backlot did not start")
        yield base, project, tmp_path
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        log.close()


@pytest.fixture
def browser_page():
    with sync_playwright() as pw:
        chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        options = {"channel": "chrome"} if chrome.exists() else {}
        browser = pw.chromium.launch(headless=True, **options)
        context = browser.new_context(viewport={"width": 1500, "height": 1000})
        external = []

        def only_loopback(route):
            if route.request.url.startswith(("http://127.0.0.1:", "data:", "blob:")):
                route.continue_()
            else:
                external.append(route.request.url)
                route.abort()

        context.route("**/*", only_loopback)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield page, errors
            assert not external, external
        finally:
            context.close()
            browser.close()


def _open(page, base):
    response = page.goto(base + "/w/stage-film/s01", wait_until="domcontentloaded")
    assert response.status == 200
    expect(page.get_by_role("heading", name="镜头工作台", exact=True)).to_be_visible()
    page.wait_for_function("document.body.dataset.readOnly !== undefined")


def _acquire(page):
    page.get_by_role("button", name="接管镜头", exact=True).click()
    page.wait_for_function("document.body.dataset.readOnly === 'false' && !document.querySelector('#save').disabled")


def test_real_browser_human_export_dual_subject_rebuild_and_compile(workbench_server, browser_page):
    base, project, tmp = workbench_server
    page, errors = browser_page
    _open(page, base)
    for heading in ("01 · 参考图板", "02 · 镜头字段", "03 · 三维舞台", "04 · 编译预览"):
        expect(page.get_by_role("heading", name=heading)).to_be_visible()
    _acquire(page)
    page.get_by_label("舞台主体", exact=True).select_option("amor")
    page.get_by_role("button", name="添加人体", exact=True).click()
    page.get_by_label("人体姿势", exact=True).select_option("reaching")
    page.get_by_label("左肘角度°", exact=True).fill("35")
    page.get_by_label("左肘角度°", exact=True).press("Tab")
    page.get_by_role("button", name="导出并保存舞台参考", exact=True).click()
    expect(page.locator("#status")).to_contain_text("舞台已导出", timeout=20000)
    first = json.loads((project / "shots/s01.json").read_text())
    assert {"composition", "depth", "pose"} <= {ref["role"] for ref in first["references"]}
    assert first["stage"]["objects"][0]["pose"] == "reaching"
    assert first["stage"]["objects"][0]["joints"]["elbow_left"] > 0
    pose_reference = next(ref for ref in first["references"] if ref["role"] == "pose")
    assert pose_reference["parent_asset"].endswith(".json")
    metadata = json.loads((project / pose_reference["parent_asset"]).read_text())
    assert len(metadata["pose"]["people"]) == 1
    assert metadata["camera"]["lens_mm"] == first["stage"]["camera"]["lens_mm"]
    page.get_by_label("舞台主体", exact=True).select_option("fati")
    page.get_by_role("button", name="添加人体", exact=True).click()
    page.get_by_label("人体姿势", exact=True).select_option("sitting")
    page.get_by_role("button", name="导出并保存舞台参考", exact=True).click()
    expect(page.locator("#status")).to_contain_text("舞台已导出", timeout=20000)
    double = json.loads((project / "shots/s01.json").read_text())
    assert {obj["subject_ref"] for obj in double["stage"]["objects"]} == {"amor", "fati"}
    assert all(subject.get("placement") for subject in double["subjects"])
    pose_reference = next(ref for ref in double["references"] if ref["role"] == "pose")
    metadata = json.loads((project / pose_reference["parent_asset"]).read_text())
    assert {person["subject_ref"] for person in metadata["pose"]["people"]} == {"amor", "fati"}
    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("document.querySelectorAll('[aria-label=\"舞台物体\"] option').length === 3")
    page.get_by_label("生成方式", exact=True).select_option("multi_ref")
    page.get_by_role("button", name="保存镜头单", exact=True).click()
    expect(page.locator("#status")).to_contain_text("已保存镜头单")
    with page.expect_response(lambda response: response.url.endswith("/compile")) as compile_response:
        page.get_by_role("button", name="编译草稿", exact=True).click()
    result = compile_response.value.json()
    assert result["generatable"], result["lint"]
    assert result["payload"]["route"] == "multi_ref"
    assert result["payload"]["reference_map"] == {"amor": ["assets/refs/amor.png"], "fati": ["assets/refs/fati.png"]}
    expect(page.locator("[data-final-prompt]")).to_be_visible(timeout=20000)
    assert page.locator("[data-final-prompt]").evaluate("node => node.tagName") == "PRE"
    evidence = Path(os.environ.get("SHOT_WORKBENCH_BROWSER_EVIDENCE", str(tmp / "evidence")))
    evidence.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(evidence / "dual-stage.png"), full_page=True)
    print(f"Shot Workbench real browser evidence: {evidence / 'dual-stage.png'}")
    assert not errors, errors


def test_dirty_draft_survives_external_revision_and_409(workbench_server, browser_page):
    base, project, _tmp = workbench_server
    page, errors = browser_page
    _open(page, base)
    _acquire(page)
    page.get_by_label("镜头意图", exact=True).fill("保留这个未保存草稿")
    current = page.request.get(base + "/api/shot/stage-film/s01").json()
    incoming = current["spec"]
    incoming["notes"] = "外部修改"
    response = page.request.put(base + "/api/shot/stage-film/s01", data={"spec": incoming, "revision": incoming["revision"], "lease_id": current["lock"]["lease_id"]})
    assert response.status == 200
    expect(page.locator("#conflict")).to_be_visible(timeout=10000)
    expect(page.get_by_label("镜头意图", exact=True)).to_have_value("保留这个未保存草稿")
    page.get_by_role("button", name="保存镜头单", exact=True).click()
    expect(page.locator("#status")).to_contain_text("保存冲突")
    expect(page.get_by_label("镜头意图", exact=True)).to_have_value("保留这个未保存草稿")
    page.get_by_role("button", name="重新加载正本", exact=True).click()
    expect(page.get_by_label("附加说明（原样附在最后）", exact=True)).to_have_value("外部修改")
    assert not errors, errors


def test_agent_lease_disables_every_mutation(workbench_server, browser_page):
    base, project, _tmp = workbench_server
    page, errors = browser_page
    acquire(project, "s01", "agent", "浏览器只读验证")
    _open(page, base)
    expect(page.locator("#status")).to_contain_text("Agent 正在编辑")
    for label in ("保存镜头单", "保存并交回 Agent", "添加人体", "添加方块", "导出并保存舞台参考", "生成这个镜头"):
        expect(page.get_by_role("button", name=label, exact=True)).to_be_disabled()
    expect(page.get_by_label("上传参考图", exact=True)).to_be_disabled()
    expect(page.get_by_label("镜头意图", exact=True)).to_be_disabled()
    with page.expect_response(lambda response: response.url.endswith("/compile")) as compiled_response:
        page.get_by_role("button", name="编译草稿", exact=True).click()
    assert compiled_response.value.status == 200
    expect(page.locator("[data-final-prompt]")).to_be_visible(timeout=10000)
    expect(page.get_by_role("button", name="生成这个镜头", exact=True)).to_be_disabled()
    assert not errors, errors


def test_upload_roles_and_explicit_mock_budget_confirmation(workbench_server, browser_page):
    base, project, tmp = workbench_server
    page, errors = browser_page
    _open(page, base)
    _acquire(page)
    image = tmp / "wardrobe.png"
    Image.new("RGB", (24, 24), "red").save(image)
    page.get_by_label("上传参考图", exact=True).set_input_files(str(image))
    expect(page.locator("#status")).to_contain_text("参考图已上传")
    page.get_by_label("参考角色", exact=True).last.select_option("wardrobe")
    page.get_by_label("参考对应主体", exact=True).last.select_option("fati")
    # Automatic selection must render through the concrete tool frozen by the
    # compiler, rather than send a null tool name back to the render API.
    page.get_by_label("目标工具", exact=True).select_option("")
    page.get_by_label("生成方式", exact=True).select_option("multi_ref")
    page.get_by_role("button", name="保存镜头单", exact=True).click()
    expect(page.locator("#status")).to_contain_text("已保存镜头单")
    with page.expect_response(lambda response: response.url.endswith("/compile")) as compiled_response:
        page.get_by_role("button", name="编译草稿", exact=True).click()
    compiled = compiled_response.value.json()
    assert compiled.get("generatable"), compiled
    expect(page.locator("[data-final-prompt]")).to_be_visible(timeout=10000)
    expect(page.locator("#compiled-preview")).to_contain_text("model: mock-model")
    expect(page.locator("#compiled-preview")).to_contain_text("backend: offline")
    with page.expect_response(lambda response: response.url.endswith("/render")) as pending_response:
        page.get_by_role("button", name="生成这个镜头", exact=True).click()
    pending = pending_response.value.json()
    first_id = pending_response.value.request.post_data_json["request_id"]
    assert first_id == pending["request_id"]
    expect(page.locator("#confirmation")).to_be_visible(timeout=10000)
    expect(page.locator("#confirmation")).to_contain_text("model: mock-model")
    expect(page.locator("#confirmation")).to_contain_text("backend: offline")
    assert not (project / "artifacts/asset_manifest.json").exists()
    assert not (tmp / "mock-calls.json").exists()
    with page.expect_response(lambda response: response.url.endswith("/render")) as confirmed_response:
        page.get_by_role("button", name="确认费用并生成", exact=True).click()
    assert confirmed_response.value.request.post_data_json["request_id"] == first_id
    expect(page.locator("#candidates .candidate-card")).to_have_count(1, timeout=10000)
    manifest = json.loads((project / "artifacts/asset_manifest.json").read_text())
    assert manifest["assets"]
    assert len(json.loads((tmp / "mock-calls.json").read_text())) == 1
    current = json.loads((project / "shots/s01.json").read_text())
    assert any(ref["role"] == "wardrobe" and ref.get("subject_ref") == "fati" for ref in current["references"])
    page.get_by_role("button", name="编译草稿", exact=True).click()
    with page.expect_response(lambda response: response.url.endswith("/render")) as next_response:
        page.get_by_role("button", name="生成这个镜头", exact=True).click()
    assert next_response.value.request.post_data_json["request_id"] != first_id
    if next_response.value.json()["needs_confirmation"]:
        page.get_by_role("button", name="确认费用并生成", exact=True).click()
    expect(page.locator("#candidates .candidate-card")).to_have_count(2, timeout=10000)
    assert len(json.loads((tmp / "mock-calls.json").read_text())) == 2
    assert not errors, errors


def test_unknown_generation_keeps_request_id_without_second_execute(workbench_server, browser_page):
    base, project, tmp = workbench_server
    page, errors = browser_page
    (tmp / "mock-unknown.flag").touch()
    _open(page, base)
    _acquire(page)
    page.get_by_label("生成方式", exact=True).select_option("multi_ref")
    page.get_by_role("button", name="保存镜头单", exact=True).click()
    expect(page.locator("#status")).to_contain_text("已保存镜头单")
    page.get_by_role("button", name="编译草稿", exact=True).click()
    page.get_by_role("button", name="生成这个镜头", exact=True).click()
    expect(page.locator("#confirmation")).to_be_visible()
    page.get_by_role("button", name="确认费用并生成", exact=True).click()
    expect(page.locator("#status")).to_contain_text("待核对费用")
    state = page.request.get(base + "/api/shot/stage-film/s01").json()
    request_id = state["requests"][0]["request_id"]
    expect(page.locator("#status")).to_contain_text(request_id)
    page.get_by_role("button", name="查看生成状态", exact=True).click()
    expect(page.locator("#status")).to_contain_text("待核对费用")
    page.reload(wait_until="domcontentloaded")
    expect(page.locator("#status")).to_contain_text(request_id)
    page.get_by_role("button", name="编译草稿", exact=True).click()
    page.get_by_role("button", name="查看生成状态", exact=True).click()
    expect(page.locator("#status")).to_contain_text("模拟费用待核对")
    assert len(json.loads((tmp / "mock-calls.json").read_text())) == 1
    assert not errors, errors


def test_candidate_attempts_show_rejected_and_verification_sources(workbench_server, browser_page):
    base, project, _tmp = workbench_server
    page, errors = browser_page
    atomic_json(project / "artifacts/asset_manifest.json", {"version": "1.0", "total_cost_usd": 2,
        "assets": [{"id": "test-candidates", "scene_id": "s01", "type": "image",
            "path": "assets/refs/fati.png", "source_tool": "mock_video", "cost_usd": 2,
            "prompt": "Offline attempts", "compiled_hash": "a" * 64, "selected_candidate": None,
            "candidates": [
                {"path": "assets/refs/amor.png", "attempt": 1, "parent_asset": "shots/s01.json", "compiled_hash": "b" * 64,
                 "verification": {"status": "fail", "eligible": False, "notes": "人物身份不符"}},
                {"path": "assets/refs/fati.png", "attempt": 2, "verification": {"status": "unreviewed", "eligible": False}}]}]})
    _open(page, base)
    expect(page.locator("#candidates .candidate-card")).to_have_count(2)
    expect(page.locator("#candidates")).to_contain_text("废片")
    expect(page.locator("#candidates")).to_contain_text("人物身份不符")
    expect(page.locator("#candidates")).to_contain_text("b" * 64)
    expect(page.locator("#candidates")).to_contain_text("shots/s01.json")
    assert not errors, errors
