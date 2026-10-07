"""Offline workbench API contracts; tool calls never reach a provider."""
from __future__ import annotations

import base64
import copy
import io
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backlot import server
from lib.config_model import BudgetConfig, BudgetMode
from tools.base_tool import BaseTool, ToolResult


class MockGenerator(BaseTool):
    name = "mock_video"
    provider = "mock"
    capability = "video_generation"
    capabilities = ["text_to_video", "image_to_video", "reference_to_video"]
    supports = {"text_to_video": True, "reference_image": True}
    limits = {"max_reference_images": 4, "max_prompt_chars": 5000,
              "negative_prompt_field": "negative_prompt", "durations": [5],
              "first_last_frame": True, "control_inputs": [],
              "routes": ["t2v", "i2v", "multi_ref", "first_last"]}
    input_schema = {"properties": {key: {} for key in (
        "prompt", "negative_prompt", "duration", "duration_seconds", "aspect_ratio",
        "resolution", "operation", "reference_image_paths", "image_path", "end_image_url")}}

    def __init__(self):
        self.calls = []
        self.price = 1.25
        self.actual = 1.0
        self.fail = False

    def estimate_cost(self, inputs):
        return self.price

    def execute(self, inputs):
        self.calls.append(copy.deepcopy(inputs))
        path = Path(inputs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.fail:
            return ToolResult(success=False, error="charged provider failure", cost_usd=self.actual)
        path.write_bytes(b"mock generated video")
        return ToolResult(success=True, data={"output_path": str(path)},
                          artifacts=[str(path)], cost_usd=self.actual, model="mock-model")


def png_uri(color="red"):
    data = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(data, format="PNG")
    return "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    film = root / "film"
    (film / "artifacts").mkdir(parents=True)
    (film / "project.json").write_text(json.dumps({"project_id": "film", "pipeline_type": "cinematic"}))
    (film / "artifacts" / "scene_plan.json").write_text(json.dumps({"version": "1.0", "scenes": [{
        "id": "shot1", "type": "generated", "description": "A sunlit room with a closed drawer.",
        "start_seconds": 0, "end_seconds": 5, "shot_language": {"shot_size": "wide", "camera_movement": "static"}}]}))
    monkeypatch.setattr(server, "PROJECTS_DIR", root)
    monkeypatch.setattr(server, "_summary_cache", {})
    async def no_watch():
        return None
    monkeypatch.setattr(server, "_watch_projects", no_watch)
    return film


def test_workbench_reads_file_state_and_page(project):
    with TestClient(server.create_app()) as client:
        response = client.get("/api/shot/film/shot1")
        assert response.status_code == 200
        data = response.json()
        assert data["spec"]["scene_id"] == "shot1"
        assert data["read_only"] is True
        assert client.get("/w/film/shot1").status_code == 200


@pytest.fixture
def api(project, monkeypatch):
    from lib import shot_execution
    tool = MockGenerator()
    registry = SimpleNamespace(ensure_discovered=lambda: None, get=lambda name: tool if name == tool.name else None,
                               get_by_capability=lambda capability: [tool] if capability == tool.capability else [])
    monkeypatch.setattr(shot_execution, "get_registry", lambda: registry)
    monkeypatch.setattr(shot_execution, "get_budget", lambda project: BudgetConfig())
    with TestClient(server.create_app()) as client:
        read = client.get("/api/shot/film/shot1")
        assert read.status_code == 200, read.text
        lease = client.post("/api/shot/film/shot1/acquire", json={"reason": "test edit"})
        assert lease.status_code == 200, lease.text
        yield client, tool, lease.json()["lock"]["lease_id"], project


def read_spec(client):
    return client.get("/api/shot/film/shot1").json()["spec"]


def preview(client, lease_id):
    spec = read_spec(client)
    result = client.post("/api/shot/film/shot1/compile", json={
        "tool_name": "mock_video", "revision": spec["revision"], "lease_id": lease_id})
    assert result.status_code == 200, result.text
    return spec, result.json()


def render_body(client, lease_id):
    spec, compiled = preview(client, lease_id)
    assert compiled["generatable"], compiled
    return {"revision": spec["revision"], "compiled_hash": compiled["compiled_hash"],
            "tool_name": "mock_video", "lease_id": lease_id}


def test_save_conflict_preserves_current_and_requires_lease(api):
    client, tool, lease, project = api
    original = read_spec(client)
    edited = {**original, "notes": "changed"}
    body = {"spec": edited, "revision": original["revision"], "lease_id": lease}
    result = client.put("/api/shot/film/shot1", json=body)
    assert result.status_code == 200
    assert result.json()["spec"]["revision"] == original["revision"] + 1
    assert client.put("/api/shot/film/shot1", json=body).status_code == 409
    assert read_spec(client)["notes"] == "changed"
    body["revision"] += 1
    body["lease_id"] = "forged"
    assert client.put("/api/shot/film/shot1", json=body).status_code in (403, 409, 423)


def test_agent_lock_does_not_leak_lease_or_allow_user_write(project, monkeypatch):
    from lib.shot_workbench import initialize, acquire
    from lib import shot_execution
    monkeypatch.setattr(shot_execution, "catalog", lambda: [])
    initialize(project, "shot1")
    lock = acquire(project, "shot1", "agent", reason="agent edit")
    with TestClient(server.create_app()) as client:
        response = client.get("/api/shot/film/shot1").json()
        assert response["read_only"]
        assert "lease_id" not in response["lock"]
        assert lock["lease_id"] not in json.dumps(response)
        saved = client.put("/api/shot/film/shot1", json={"spec": response["spec"],
            "revision": response["spec"]["revision"], "lease_id": lock["lease_id"]})
        assert saved.status_code in (403, 409, 423)


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
def test_cross_origin_mutations_rejected(api, origin):
    client, tool, lease, project = api
    response = client.post("/api/shot/film/shot1/compile", json={"tool_name": "mock_video",
        "revision": read_spec(client)["revision"], "lease_id": lease}, headers={"Origin": origin})
    assert response.status_code == 403
    assert not tool.calls


def test_host_rebinding_rejected(api):
    client, tool, lease, project = api
    response = client.post("/api/shot/film/shot1/heartbeat", json={"lease_id": lease},
                           headers={"Host": "evil.example"})
    assert response.status_code == 403


def test_upload_content_dedup_revision_and_path_safety(api):
    client, tool, lease, project = api
    spec = read_spec(client)
    body = {"revision": spec["revision"], "lease_id": lease, "data": png_uri(), "role": "style"}
    first = client.post("/api/shot/film/shot1/references", json=body)
    assert first.status_code == 200, first.text
    ref = first.json()["reference"]
    assert ref["asset_path"].startswith("assets/refs/")
    body["revision"] = first.json()["spec"]["revision"]
    second = client.post("/api/shot/film/shot1/references", json=body)
    assert second.status_code == 200
    assert second.json()["reference"]["sha256"] == ref["sha256"]
    assert len(list((project / "assets" / "refs").glob("*.png"))) == 1
    assert client.post("/api/shot/film/shot1/references", json=body).status_code == 409
    body["revision"] = second.json()["spec"]["revision"]
    body["data"] = "data:image/png;base64," + base64.b64encode(b"fake PNG").decode()
    assert client.post("/api/shot/film/shot1/references", json=body).status_code == 422
    body["data"] = png_uri()
    body["parent_asset"] = "../../outside.png"
    assert client.post("/api/shot/film/shot1/references", json=body).status_code in (403, 422)


def test_reference_symlink_and_hash_change_rejected(api, tmp_path):
    client, tool, lease, project = api
    from lib.frames import file_sha256
    external = tmp_path / "external.png"
    external.write_bytes(base64.b64decode(png_uri().split(",")[1]))
    (project / "assets").mkdir(exist_ok=True)
    (project / "assets" / "escape.png").symlink_to(external)
    spec = read_spec(client)
    spec["references"] = [{"asset_path": "assets/escape.png", "sha256": file_sha256(external), "role": "style"}]
    result = client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease})
    assert result.status_code in (403, 422)
    assert not tool.calls


def test_compile_server_canon_and_never_client_prompt(api):
    client, tool, lease, project = api
    spec = read_spec(client)
    spec["compile_context"] = {"limits": {"max_reference_images": 999}, "estimated_cost": 0,
                               "tool_options": {"output_path": "/tmp/hacked.mp4"}}
    result = client.post("/api/shot/film/shot1/compile", json={"spec": spec, "revision": spec["revision"],
        "tool_name": "mock_video", "lease_id": lease, "prompt": "arbitrary final prompt"})
    assert result.status_code == 200
    assert "arbitrary final prompt" not in result.json()["prompt"]
    assert result.json()["estimated_cost"] == tool.price
    assert "output_path" not in result.json()["payload"]["tool_options"]


def test_paid_render_confirmation_budget_provenance_and_dedup(api):
    client, tool, lease, project = api
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body)
    assert pending.status_code == 200, pending.text
    approval = pending.json()
    assert approval["needs_confirmation"] and approval["confirmation_token"]
    assert not tool.calls
    assert not (project / "cost_log.json").exists() or not json.loads((project / "cost_log.json").read_text())["entries"]
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    complete = client.post("/api/shot/film/shot1/render", json=body)
    assert complete.status_code == 200, complete.text
    assert complete.json()["status"] == "succeeded"
    assert len(tool.calls) == 1
    duplicate = client.post("/api/shot/film/shot1/render", json=body)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "succeeded"
    assert len(tool.calls) == 1
    from schemas.artifacts import validate_artifact
    log = json.loads((project / "cost_log.json").read_text())
    validate_artifact("cost_log", log)
    assert log["budget_spent_usd"] == tool.actual
    assert log["budget_reserved_usd"] == 0
    manifest = json.loads((project / "artifacts" / "asset_manifest.json").read_text())
    validate_artifact("asset_manifest", manifest)
    asset = manifest["assets"][0]
    assert asset["compiled_hash"] == body["compiled_hash"]
    assert asset["shot_spec_revision"] == body["revision"]
    assert asset["candidates"][0]["verification"]["status"] == "unreviewed"
    assert asset["selected_candidate"] is None
    assert Path(tool.calls[0]["output_path"]).is_relative_to(project)


def test_confirmation_stale_quote_revision_and_expiry(api, monkeypatch):
    client, tool, lease, project = api
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    tool.price += 1
    assert client.post("/api/shot/film/shot1/render", json=body).status_code == 409
    assert not tool.calls
    tool.price -= 1
    from lib import shot_execution
    monkeypatch.setattr(shot_execution, "TOKEN_TTL_SECONDS", -1)
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    assert client.post("/api/shot/film/shot1/render", json=body).status_code == 409
    assert not tool.calls


def test_first_paid_tool_confirmed_even_below_action_limit(api):
    client, tool, lease, project = api
    tool.price = 0.01
    response = client.post("/api/shot/film/shot1/render", json=render_body(client, lease)).json()
    assert response["needs_confirmation"]
    assert not tool.calls


def test_cap_cannot_be_bypassed_by_confirmation(api, monkeypatch):
    client, tool, lease, project = api
    from lib import shot_execution
    monkeypatch.setattr(shot_execution, "get_budget", lambda project: BudgetConfig(total_usd=0.1, mode=BudgetMode.CAP))
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body)
    if pending.status_code == 200:
        approval = pending.json()
        body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
        pending = client.post("/api/shot/film/shot1/render", json=body)
    assert pending.status_code == 409
    assert not tool.calls


def test_failed_paid_call_reconciles_without_retry(api):
    client, tool, lease, project = api
    tool.fail = True
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    response = client.post("/api/shot/film/shot1/render", json=body)
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    client.post("/api/shot/film/shot1/render", json=body)
    assert len(tool.calls) == 1
    log = json.loads((project / "cost_log.json").read_text())
    assert log["entries"][0]["status"] == "failed"
    assert log["budget_spent_usd"] == tool.actual
    assert log["budget_reserved_usd"] == 0


def test_unknown_quote_and_lint_error_never_execute(api):
    client, tool, lease, project = api
    tool.price = float("nan")
    spec, compiled = preview(client, lease)
    assert compiled["estimated_cost"] is None
    response = client.post("/api/shot/film/shot1/render", json={"revision": spec["revision"],
        "compiled_hash": compiled["compiled_hash"], "tool_name": "mock_video", "lease_id": lease})
    assert response.status_code in (409, 422)
    assert not tool.calls
    tool.price = 1.25
    spec["notes"] = "no people"
    saved = client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease})
    assert saved.status_code == 200
    spec, compiled = preview(client, lease)
    assert not compiled["generatable"]
    response = client.post("/api/shot/film/shot1/render", json={"revision": spec["revision"],
        "compiled_hash": compiled["compiled_hash"], "tool_name": "mock_video", "lease_id": lease})
    assert response.status_code == 422
    assert not tool.calls


def test_stage_export_refs_camera_and_placement_are_saved_atomically(api):
    client, tool, lease, project = api
    spec = read_spec(client)
    spec["subjects"] = [{"kind": "character", "ref_id": "actor", "placement": "", "action": "standing",
                         "visible_parts": ["full_body"], "look": ""}]
    saved = client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease})
    assert saved.status_code == 200, saved.text
    revision = saved.json()["spec"]["revision"]
    stage = {"version": "1.0", "units": "m", "objects": [{"id": "person", "kind": "human",
        "subject_ref": "actor", "position": [-1, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1], "pose": "standing"}],
        "camera": {"position": [0, 1.5, 5], "target": [0, 1, 0], "lens_mm": 35,
                   "sensor_width_mm": 36, "sensor_height_mm": 24, "near": 0.1, "far": 100}}
    body = {"revision": revision, "lease_id": lease, "stage": stage,
            "images": {"composition": png_uri(), "depth": png_uri("black"), "pose": png_uri("white")},
            "pose": {"version": "1.0", "width": 32, "height": 24, "people": []}}
    response = client.post("/api/shot/film/shot1/stage-export", json=body)
    assert response.status_code == 200, response.text
    exported = response.json()["spec"]
    assert exported["revision"] == revision + 1
    assert {"composition", "depth", "pose"}.issubset({ref["role"] for ref in exported["references"]})
    assert exported["stage"]["camera"]["lens_mm"] == 35
    assert "左" in exported["subjects"][0]["placement"]
    assert client.post("/api/shot/film/shot1/stage-export", json=body).status_code == 409


def test_heartbeat_release_and_bad_project_paths(api):
    client, tool, lease, project = api
    assert client.post("/api/shot/film/shot1/heartbeat", json={"lease_id": lease}).status_code == 200
    response = client.post("/api/shot/film/shot1/release", json={"lease_id": lease})
    assert response.status_code == 200
    assert client.get("/api/shot/film/shot1").json()["read_only"]
    assert client.get("/api/shot/film/not-a-shot").status_code == 404
    assert client.get("/api/shot/C:/shot1").status_code == 400


def test_stage_metadata_is_a_reference_parent_and_vertical_camera_works(api):
    client, tool, lease, project = api
    spec = read_spec(client)
    stage = {"units": "m", "objects": [], "camera": {"position": [0, 5, 0], "target": [0, 0, 0], "lens_mm": 35}}
    body = {"revision": spec["revision"], "lease_id": lease, "stage": stage,
            "images": {"composition": png_uri(), "depth": png_uri(), "pose": png_uri()},
            "pose": {"version": "1.0", "width": 32, "height": 24, "people": []}}
    result = client.post("/api/shot/film/shot1/stage-export", json=body)
    assert result.status_code == 200, result.text
    refs = result.json()["references"]
    metadata = json.loads((project / next(ref["parent_asset"] for ref in refs if ref["role"] == "pose")).read_text())
    assert metadata["pose"]["people"] == []
    assert metadata["camera"] == stage["camera"]


def test_approved_model_options_are_validated_in_server_context(api):
    client, tool, lease, project = api
    tool.input_schema = copy.deepcopy(tool.input_schema)
    tool.input_schema["properties"]["model"] = {"type": "string", "enum": ["standard", "fast"], "default": "standard"}
    spec = read_spec(client)
    spec["compile_context"] = {"tool_options": {"model": "fast", "prompt": "override", "output_path": "/tmp/out"}}
    body = {"spec": spec, "revision": spec["revision"], "lease_id": lease, "tool_name": tool.name}
    result = client.post("/api/shot/film/shot1/compile", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["payload"]["tool_options"]["model"] == "fast"
    assert "output_path" not in result.json()["payload"]["tool_options"]
    body["spec"]["compile_context"]["tool_options"]["model"] = "unknown"
    assert client.post("/api/shot/film/shot1/compile", json=body).status_code == 422


def test_concurrent_confirmation_submits_once_and_reports_running(api, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from lib import shot_execution
    client, tool, lease, project = api
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=pending["confirmation_token"], request_id=pending["request_id"])
    entered, resume = threading.Event(), threading.Event()
    original = tool.execute
    def delayed(inputs):
        entered.set()
        assert resume.wait(10)
        return original(inputs)
    monkeypatch.setattr(tool, "execute", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(shot_execution.render, project, "shot1", **body)
        assert entered.wait(10)
        duplicate = pool.submit(shot_execution.render, project, "shot1", **body).result(timeout=10)
        assert duplicate["status"] == "running"
        assert duplicate["replayed"]
        resume.set()
        assert first.result(timeout=10)["status"] == "succeeded"
    assert len(tool.calls) == 1
    assert json.loads((project / "cost_log.json").read_text())["budget_spent_usd"] == tool.actual


def test_interrupted_provider_stays_unknown_without_automatic_resubmit(api, monkeypatch):
    client, tool, lease, project = api
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    calls = []
    def interrupted(inputs):
        calls.append(inputs)
        raise TimeoutError("provider accepted, response lost")
    monkeypatch.setattr(tool, "execute", interrupted)
    response = client.post("/api/shot/film/shot1/render", json=body)
    assert response.json()["status"] == "unknown"
    assert client.post("/api/shot/film/shot1/render", json=body).json()["status"] == "unknown"
    assert len(calls) == 1
    assert json.loads((project / "cost_log.json").read_text())["budget_reserved_usd"] == tool.price
    state = client.get("/api/shot/film/shot1").json()
    assert state["requests"][0]["status"] == "unknown"
    assert "token_hash" not in json.dumps(state)


def test_missing_actual_charge_retains_reservation(api):
    client, tool, lease, project = api
    tool.actual = None
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    assert client.post("/api/shot/film/shot1/render", json=body).json()["status"] == "unknown"
    assert json.loads((project / "cost_log.json").read_text())["budget_reserved_usd"] == tool.price


def test_changed_reference_content_blocks_already_confirmed_action(api):
    client, tool, lease, project = api
    spec = read_spec(client)
    uploaded = client.post("/api/shot/film/shot1/references", json={"revision": spec["revision"], "lease_id": lease,
        "role": "style", "data": png_uri()}).json()
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    (project / uploaded["reference"]["asset_path"]).write_bytes(base64.b64decode(png_uri("blue").split(",")[1]))
    assert client.post("/api/shot/film/shot1/render", json=body).status_code == 409
    assert not tool.calls


def test_candidate_batches_keep_all_attempts_and_first_paid_approval_persists(api):
    client, tool, lease, project = api
    tool.price = tool.actual = 0.01
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body).json()
    confirmed = {**body, "confirmation_token": pending["confirmation_token"], "request_id": pending["request_id"]}
    assert client.post("/api/shot/film/shot1/render", json=confirmed).json()["status"] == "succeeded"
    for _ in range(3):
        result = client.post("/api/shot/film/shot1/render", json={**body, "request_id": uuid.uuid4().hex})
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "succeeded"
    manifest = json.loads((project / "artifacts" / "asset_manifest.json").read_text())
    assert [len(asset["candidates"]) for asset in manifest["assets"]] == [3, 1]
    assert [candidate["attempt"] for candidate in manifest["assets"][0]["candidates"]] == [1, 2, 3]
    assert len(tool.calls) == 4
    assert manifest["total_cost_usd"] == pytest.approx(0.04)


def test_ledger_is_written_for_each_artist_with_real_references(api, monkeypatch):
    from tests.fixtures.artist_factory import write_artist
    client, tool, lease, project = api
    write_artist(project / "assets" / "artists", "local-performer")
    spec = read_spec(client)
    spec["subjects"] = [{"kind": "artist", "ref_id": "local-performer", "look": "main", "visible_parts": ["face"], "action": "standing"}]
    spec["route"] = "multi_ref"
    assert client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease}).status_code == 200
    spec = read_spec(client)
    uploaded = client.post("/api/shot/film/shot1/references", json={"revision": spec["revision"], "lease_id": lease,
        "role": "identity", "subject_ref": "local-performer", "data": png_uri()})
    assert uploaded.status_code == 200, uploaded.text
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    assert client.post("/api/shot/film/shot1/render", json=body).json()["status"] == "succeeded"
    ledger = [json.loads(line) for line in (project / "assets" / "artists" / "local-performer" / "ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 1
    assert ledger[0]["cost_usd"] == tool.actual
    assert ledger[0]["references"] == [uploaded.json()["reference"]["asset_path"]]
    assert ledger[0]["human_input"]["compiled_hash"] == body["compiled_hash"]


def test_bad_write_json_unknown_tool_and_type_limits(api, monkeypatch):
    from backlot import workbench
    client, tool, lease, project = api
    assert client.post("/api/shot/film/shot1/heartbeat", content='{"lease_id":NaN}', headers={"Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/shot/film/shot1/heartbeat", json=[1, 2]).status_code == 422
    assert client.post("/api/shot/film/shot1/render", json={}).status_code == 422
    assert client.post("/api/shot/film/shot1/compile", json={"revision": read_spec(client)["revision"], "tool_name": "unknown"}).status_code == 422
    spec = read_spec(client)
    body = {"revision": spec["revision"], "lease_id": lease, "role": "invalid", "data": png_uri()}
    assert client.post("/api/shot/film/shot1/references", json=body).status_code == 422
    body["role"] = "style"
    body["subject_ref"] = "missing"
    assert client.post("/api/shot/film/shot1/references", json=body).status_code == 422
    monkeypatch.setattr(workbench, "MAX_BODY_BYTES", 20)
    assert client.post("/api/shot/film/shot1/heartbeat", json={"lease_id": lease}).status_code == 413


def test_artist_browser_shows_canon_and_candidates_without_promoting(project, monkeypatch):
    from lib import shot_execution, shot_spec
    from lib.shot_workbench import initialize
    from tests.fixtures.artist_factory import write_artist
    write_artist(project / "assets" / "artists", "alpha")
    write_artist(project / "assets" / "artists", "beta", canonical=False)
    spec = initialize(project, "shot1")
    spec["subjects"] = [{"kind": "artist", "ref_id": "alpha"}, {"kind": "artist", "ref_id": "beta"}]
    shot_spec.atomic_json(project / "shots" / "shot1.json", spec)
    monkeypatch.setattr(shot_execution, "catalog", lambda: [])
    with TestClient(server.create_app()) as client:
        data = client.get("/api/shot/film/shot1").json()
    by_id = {artist["id"]: artist for artist in data["artists"]}
    assert by_id["alpha"]["references"][0]["canonical"] is True
    assert by_id["beta"]["references"][0]["candidate"] is True
    assert by_id["beta"]["references"][0]["review_status"] == "fixture_candidate"
    assert by_id["alpha"]["references"][0]["parent_asset"].startswith("assets/artists/alpha/")
    assert data["spec"]["references"] == []
    assert read_spec_file(project)["references"] == []


def read_spec_file(project):
    return json.loads((project / "shots" / "shot1.json").read_text())


def test_verified_continuation_frame_is_hydrated_and_stale_source_rejected(api, monkeypatch):
    from lib import mv_routes, shot_execution
    from lib.frames import file_sha256
    client, tool, lease, project = api
    previous = project / "assets" / "previous.mp4"
    previous.parent.mkdir(exist_ok=True)
    previous.write_bytes(b"verified previous video")
    verification = {"status": "pass", "eligible": True, "layers": {"deterministic": {"source_sha256": file_sha256(previous)}}}
    (project / "artifacts" / "asset_manifest.json").write_text(json.dumps({"version": "1.0", "assets": [{
        "id": "prev", "scene_id": "previous", "type": "video", "source_tool": "mock_video", "path": "assets/previous.mp4",
        "candidates": [{"path": "assets/previous.mp4", "attempt": 1, "verification": verification}], "selected_candidate": 0}]}))
    spec = read_spec(client)
    spec["route"] = "i2v"
    spec["continuation_of"] = "previous"
    def exact_last(candidate, path):
        assert candidate["verification"]["layers"]["deterministic"]["source_sha256"] == file_sha256(candidate["path"])
        path.write_bytes(base64.b64decode(png_uri().split(",")[1]))
        return {"path": str(path), "source_sha256": file_sha256(candidate["path"]), "source_clip": candidate["path"], "frame_number": 10, "frame_count": 11}
    monkeypatch.setattr(mv_routes, "verified_continuation_frame", exact_last)
    compiled = shot_execution.preview(spec, tool.name, project)
    assert compiled["payload"]["first_frame"].endswith("-last.png")
    assert compiled["payload"]["references"][-1]["parent_asset"] == "assets/previous.mp4"
    assert not read_spec(client).get("continuation_of")
    previous.write_bytes(b"modified clip")
    def checked(candidate, path):
        raise ValueError("verified previous clip hash is missing or changed")
    monkeypatch.setattr(mv_routes, "verified_continuation_frame", checked)
    with pytest.raises(Exception, match="changed"):
        shot_execution.preview(spec, tool.name, project)


@pytest.mark.parametrize("tool_class,route,first_field,refs_field", [
    ("seedance_video.SeedanceVideo", "first_last", "image_path", None),
    ("seedance_video.SeedanceVideo", "multi_ref", None, "reference_image_paths"),
    ("veo_video.VeoVideo", "first_last", "first_frame_path", None),
    ("veo_video.VeoVideo", "multi_ref", None, "reference_image_paths"),
    ("kling_video.KlingVideo", "i2v", "image_url", None),
    ("h3_max_video.H3MaxVideo", "multi_ref", None, "reference_image_urls"),
    ("openai_image.OpenAIImage", "multi_ref", None, "image_paths"),
    ("google_imagen.GoogleImagen", "multi_ref", None, "image_paths"),
])
def test_existing_provider_input_mappings_are_local_and_exact(tmp_path, tool_class, route, first_field, refs_field):
    import importlib
    from lib import shot_execution
    module, name = tool_class.split(".")
    package = "video" if "Video" in name else "graphics"
    tool = getattr(importlib.import_module(f"tools.{package}.{module}"), name)()
    image = tmp_path / "ref.png"
    image.write_bytes(base64.b64decode(png_uri().split(",")[1]))
    compiled = {"prompt": "closed drawer under warm light", "negative_prompt": "extra objects", "payload": {
        "route": route, "duration_seconds": 5, "target_resolution": "1920x1080", "aspect_ratio": "16:9",
        "reference_images": ["ref.png"], "first_frame": "ref.png", "last_frame": "ref.png",
        "tool_options": {"resolution": "720p"} if package == "video" else {}, "control_inputs": {}}}
    inputs = shot_execution.tool_inputs(compiled, tool, tmp_path)
    assert inputs["prompt"] == compiled["prompt"]
    if first_field:
        assert inputs[first_field] == str(image) if first_field.endswith("path") else inputs[first_field].startswith("data:image/png;base64,")
    if refs_field:
        assert inputs[refs_field] == [str(image)] if refs_field.endswith("paths") else inputs[refs_field][0].startswith("data:image/png;base64,")
    if name == "SeedanceVideo" and route == "first_last":
        assert inputs["operation"] == "image_to_video"
        assert inputs["end_image_url"].startswith("data:image/png;base64,")
    if name == "VeoVideo":
        assert inputs["duration"] == "5s"
        assert inputs["auto_fix"] is False
    if package == "graphics":
        assert inputs["generation_mode"] == "edit"


@pytest.mark.parametrize("route", ["t2v", "i2v", "first_last", "multi_ref"])
def test_h3_adapted_inputs_validate_against_actual_fal_contract(tmp_path, route):
    from lib import shot_execution
    from tools.video.h3_max_video import H3MaxVideo
    image = tmp_path / "ref.png"
    image.write_bytes(base64.b64decode(png_uri().split(",")[1]))
    tool = H3MaxVideo()
    spec = {"route": route, "target_resolution": "1920x1080", "compile_context": {"tool_options": {}}}
    options = shot_execution._defaults(tool)
    options["resolution"] = shot_execution._resolution(spec, tool)
    compiled = {"prompt": "A closed drawer under warm light", "negative_prompt": "", "payload": {
        "route": route, "duration_seconds": 5, "target_resolution": "1920x1080", "aspect_ratio": "16:9",
        "reference_images": ["ref.png"] if route == "multi_ref" else [],
        "first_frame": "ref.png" if route in ("i2v", "first_last") else None,
        "last_frame": "ref.png" if route == "first_last" else None,
        "tool_options": options, "control_inputs": {}}}
    inputs = shot_execution.tool_inputs(compiled, tool, tmp_path)
    assert inputs["duration"] == 5
    assert inputs["resolution"] == "1080P"
    # Native reference URL/data inputs avoid uploads; mock local input upload
    # only, then run the genuine build_request+schema validation offline.
    if inputs.get("image_path"):
        inputs["image_url"] = shot_execution._data_uri(Path(inputs.pop("image_path")))
    model, operation, endpoint, payload = tool.build_request(inputs)
    assert model == "h3-max"
    assert operation == ("image_to_video" if route == "first_last" else {
        "t2v": "text_to_video", "i2v": "image_to_video", "multi_ref": "reference_to_video"}[route])
    if route == "first_last":
        assert payload["end_image_url"].startswith("data:image/png;base64,")
    if route == "multi_ref":
        assert len(payload["reference_image_urls"]) == 1


@pytest.mark.parametrize("path,field,value", [
    ("references", "role", []), ("references", "subject_ref", {}),
    ("compile", "tool_name", {}), ("compile", "spec", {"shot_id": "shot1", "references": "bad"}),
    ("render", "confirmation_token", {}), ("render", "compiled_hash", []),
])
def test_malformed_write_fields_fail_cleanly_without_provider(api, path, field, value):
    client, tool, lease, project = api
    body = render_body(client, lease)
    if path == "references":
        body.update(role="style", data=png_uri())
    body[field] = value
    response = client.post(f"/api/shot/film/shot1/{path}", json=body)
    assert response.status_code in (400, 409, 422), response.text
    assert not tool.calls


def test_declared_references_cannot_be_silently_dropped_by_adapter(api):
    client, tool, lease, project = api
    spec = read_spec(client)
    spec["route"] = "multi_ref"
    assert client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease}).status_code == 200
    spec = read_spec(client)
    assert client.post("/api/shot/film/shot1/references", json={"revision": spec["revision"], "lease_id": lease,
        "role": "composition", "data": png_uri()}).status_code == 200
    tool.input_schema = copy.deepcopy(tool.input_schema)
    tool.input_schema["properties"].pop("reference_image_paths")
    spec, compiled = preview(client, lease)
    assert not compiled["generatable"]
    assert any(lint["code"] == "UNMAPPED_REFERENCE" for lint in compiled["lint"])
    assert not tool.calls


def test_veo_auto_backend_is_frozen_in_compiled_context(api, monkeypatch):
    from lib import shot_execution
    from tools.video.veo_video import VeoVideo
    client, tool, lease, project = api
    veo = VeoVideo()
    state = {"google": True}
    monkeypatch.setattr(veo, "_get_google_credentials_status", lambda: state["google"])
    monkeypatch.setattr(veo, "_get_fal_api_key", lambda: "offline-fixture")
    monkeypatch.setattr(shot_execution, "get_registry", lambda: SimpleNamespace(get=lambda name: veo))
    spec = read_spec_file(project)
    spec["duration_seconds"] = 8
    first = shot_execution.preview(spec, veo.name, project)
    assert first["payload"]["tool_options"]["backend"] == "google"
    state["google"] = False
    second = shot_execution.preview(spec, veo.name, project)
    assert second["payload"]["tool_options"]["backend"] == "fal"
    assert second["compiled_hash"] != first["compiled_hash"]
    assert first["estimated_cost"] == second["estimated_cost"]
    assert shot_execution.tool_inputs(first, veo, project)["backend"] == "google"


def test_unknown_request_blocks_new_id_until_charge_is_reconciled(api, monkeypatch):
    client, tool, lease, project = api
    body = render_body(client, lease)
    approval = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=approval["confirmation_token"], request_id=approval["request_id"])
    monkeypatch.setattr(tool, "execute", lambda inputs: (_ for _ in ()).throw(TimeoutError("accepted remotely")))
    assert client.post("/api/shot/film/shot1/render", json=body).json()["status"] == "unknown"
    attempted = {**body, "request_id": uuid.uuid4().hex}
    attempted.pop("confirmation_token")
    response = client.post("/api/shot/film/shot1/render", json=attempted)
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"
    assert response.json()["request_id"] == approval["request_id"]


@pytest.mark.parametrize("content", ["{", '{"version":"1.0","entries":{},"budget_total_usd":10}',
                                    '{"version":"1.0","entries":[],"budget_total_usd":NaN}'])
def test_corrupt_budget_log_is_preserved_and_never_billed(api, content):
    client, tool, lease, project = api
    body = render_body(client, lease)
    (project / "cost_log.json").write_text(content)
    result = client.post("/api/shot/film/shot1/render", json=body)
    assert result.status_code == 409
    assert (project / "cost_log.json").read_text() == content
    assert not tool.calls


@pytest.mark.parametrize("operation", ["save", "upload"])
def test_official_reference_provenance_cannot_be_downgraded(api, operation):
    from lib.shot_spec import atomic_json
    client, tool, lease, project = api
    spec = read_spec(client)
    uploaded = client.post("/api/shot/film/shot1/references", json={"revision": spec["revision"],
        "lease_id": lease, "role": "identity", "data": png_uri()}).json()
    spec = uploaded["spec"]
    spec["references"][0].update(read_only=True, source_artist="fati", parent_asset="artists/fati/face.png")
    atomic_json(project / "shots" / "shot1.json", spec)
    original = copy.deepcopy(spec)
    if operation == "save":
        for key in ("read_only", "source_artist", "parent_asset"):
            spec["references"][0].pop(key)
        response = client.put("/api/shot/film/shot1", json={"spec": spec,
            "revision": spec["revision"], "lease_id": lease})
    else:
        response = client.post("/api/shot/film/shot1/references", json={"revision": spec["revision"],
            "lease_id": lease, "role": "identity", "data": png_uri()})
    assert response.status_code == 403
    assert read_spec_file(project) == original
    assert not tool.calls


def test_confirmed_reference_bytes_are_frozen_for_provider_consumption(api, monkeypatch):
    import hashlib
    client, tool, lease, project = api
    spec = read_spec(client)
    upload = client.post("/api/shot/film/shot1/references", json={"revision": spec["revision"],
        "lease_id": lease, "role": "style", "data": png_uri("red")}).json()
    spec = upload["spec"]
    spec["route"] = "multi_ref"
    assert client.put("/api/shot/film/shot1", json={"spec": spec,
        "revision": spec["revision"], "lease_id": lease}).status_code == 200
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=pending["confirmation_token"], request_id=pending["request_id"])
    consumed = []
    execute = tool.execute
    def consume_after_source_edit(inputs):
        (project / upload["reference"]["asset_path"]).write_bytes(base64.b64decode(png_uri("blue").split(",")[1]))
        consumed.extend(hashlib.sha256(Path(path).read_bytes()).hexdigest()
                        for path in inputs["reference_image_paths"])
        return execute(inputs)
    monkeypatch.setattr(tool, "execute", consume_after_source_edit)
    response = client.post("/api/shot/film/shot1/render", json=body)
    assert response.status_code == 200, response.text
    recorded = response.json()["asset"]["references"][0]
    assert consumed == [recorded["sha256"]] == [upload["reference"]["sha256"]]
    request = json.loads((project / "shots" / "shot1.requests" / f'{pending["request_id"]}.json').read_text())
    assert request["reference_snapshots"][0]["asset_path"] == upload["reference"]["asset_path"]
    assert ".refs/" in request["reference_snapshots"][0]["snapshot_path"]


def test_all_provider_reference_channels_are_snapshotted(api):
    import hashlib
    from lib.shot_execution import _freeze_references
    client, tool, lease, project = api
    roles = ["composition", "first_frame", "last_frame", "depth", "pose"]
    refs = []
    for i, role in enumerate(roles):
        path = project / "assets" / "refs" / f"{role}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = base64.b64decode(png_uri((i * 30, 0, 0)).split(",")[1])
        path.write_bytes(data)
        refs.append({"asset_path": path.relative_to(project).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "role": role})
    compiled = {"payload": {"references": refs, "reference_images": [refs[0]["asset_path"]],
        "first_frame": refs[1]["asset_path"], "last_frame": refs[2]["asset_path"],
        "control_inputs": {"depth": refs[3]["asset_path"], "pose": refs[4]["asset_path"]}}}
    frozen, snapshots = _freeze_references(project, "shot1", "snapshot-test", compiled)
    paths = [*frozen["payload"]["reference_images"], frozen["payload"]["first_frame"],
             frozen["payload"]["last_frame"], *frozen["payload"]["control_inputs"].values()]
    assert len(paths) == len(snapshots) == 5
    for path, ref in zip(paths, refs):
        assert ".refs/" in path
        assert hashlib.sha256((project / path).read_bytes()).hexdigest() == ref["sha256"]
    assert compiled["payload"]["references"] == refs


def test_reference_change_at_snapshot_boundary_never_reserves_or_bills(api, monkeypatch):
    from lib import shot_execution
    client, tool, lease, project = api
    spec = read_spec(client)
    upload = client.post("/api/shot/film/shot1/references", json={"revision": spec["revision"],
        "lease_id": lease, "role": "style", "data": png_uri("red")}).json()
    spec = upload["spec"]
    spec["route"] = "multi_ref"
    client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease})
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=pending["confirmation_token"], request_id=pending["request_id"])
    tracker = shot_execution._tracker
    def edit_after_quote(project):
        value = tracker(project)
        (project / upload["reference"]["asset_path"]).write_bytes(base64.b64decode(png_uri("blue").split(",")[1]))
        return value
    monkeypatch.setattr(shot_execution, "_tracker", edit_after_quote)
    response = client.post("/api/shot/film/shot1/render", json=body)
    assert response.status_code == 409
    assert not (project / "cost_log.json").exists()
    assert not tool.calls


def test_maximum_shot_and_request_ids_have_safe_output_filename(api):
    from lib.shot_spec import atomic_json
    client, tool, lease, project = api
    request_id = "r" * 128
    for shot in ("s" * 128, "s" * 127 + "t"):
        spec = read_spec(client)
        spec.update(shot_id=shot, scene_id=shot)
        atomic_json(project / "shots" / f"{shot}.json", spec)
        base = f"/api/shot/film/{shot}"
        lease = client.post(base + "/acquire", json={"reason": "maximum IDs"}).json()["lock"]["lease_id"]
        compiled = client.post(base + "/compile", json={"revision": spec["revision"], "tool_name": tool.name}).json()
        body = {"revision": spec["revision"], "tool_name": tool.name, "compiled_hash": compiled["compiled_hash"],
                "lease_id": lease, "request_id": request_id}
        pending = client.post(base + "/render", json=body).json()
        body["confirmation_token"] = pending["confirmation_token"]
        result = client.post(base + "/render", json=body).json()
        assert result["status"] == "succeeded", result
        assert result["request_id"] == request_id
        assert len(Path(tool.calls[-1]["output_path"]).name.encode()) <= 255
        assert Path(tool.calls[-1]["output_path"]).is_file()
    assert len({inputs["output_path"] for inputs in tool.calls}) == 2


@pytest.mark.parametrize("success", [False, True])
def test_default_zero_actual_cost_is_unknown_for_a_quoted_paid_call(api, success):
    client, tool, lease, project = api
    tool.actual = 0
    tool.fail = not success
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=pending["confirmation_token"], request_id=pending["request_id"])
    result = client.post("/api/shot/film/shot1/render", json=body).json()
    assert result["status"] == "unknown"
    assert json.loads((project / "cost_log.json").read_text())["budget_reserved_usd"] == tool.price
    result = client.post("/api/shot/film/shot1/render", json={**body, "request_id": uuid.uuid4().hex,
        "confirmation_token": None}).json()
    assert result["status"] == "unknown" and result["replayed"]
    assert len(tool.calls) == 1


def test_real_seedance_adapter_polling_timeout_keeps_paid_reservation(api, monkeypatch):
    import requests
    from lib import shot_execution
    from tools.video.seedance_video import SeedanceVideo
    import tools.video.seedance_video as adapter
    client, mock, lease, project = api
    tool = SeedanceVideo()
    monkeypatch.setattr(tool, "_get_api_key", lambda: "offline-fixture")
    registry = SimpleNamespace(get=lambda name: tool if name == tool.name else None,
        get_by_capability=lambda capability: [tool] if capability == tool.capability else [])
    monkeypatch.setattr(shot_execution, "get_registry", lambda: registry)
    submissions = []
    def submit(url, **kwargs):
        submissions.append(kwargs["json"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {
            "status_url": "https://offline.invalid/status", "response_url": "https://offline.invalid/result"})
    def timeout(*args, **kwargs):
        raise requests.Timeout("Offline accepted submission; response unavailable")
    monkeypatch.setattr(requests, "post", submit)
    monkeypatch.setattr(requests, "get", timeout)
    monkeypatch.setattr(adapter.time, "sleep", lambda seconds: None)
    spec = read_spec(client)
    compiled = client.post("/api/shot/film/shot1/compile", json={"revision": spec["revision"],
        "tool_name": tool.name, "lease_id": lease}).json()
    assert compiled["generatable"], compiled
    body = {"revision": spec["revision"], "tool_name": tool.name, "compiled_hash": compiled["compiled_hash"], "lease_id": lease}
    pending = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(confirmation_token=pending["confirmation_token"], request_id=pending["request_id"])
    result = client.post("/api/shot/film/shot1/render", json=body).json()
    assert result["status"] == "unknown", result
    assert json.loads((project / "cost_log.json").read_text())["budget_reserved_usd"] == compiled["estimated_cost"]
    replay = client.post("/api/shot/film/shot1/render", json={**body, "request_id": uuid.uuid4().hex,
        "confirmation_token": None}).json()
    assert replay["status"] == "unknown"
    assert len(submissions) == 1


def test_imported_artist_two_generations_keep_snapshot_valid_and_ledgers_project_scoped(api):
    from lib.project_assets import publish_asset, import_asset, load_project_artist, record_project_artist_ledger
    from lib.shot_spec import from_scene, save
    from tests.fixtures.artist_factory import write_artist
    client, tool, lease, project = api
    source = project.parent / "catalog"
    write_artist(source, "solo")
    library = project.parent / "library"
    manifest = publish_asset(source / "solo", "performer", "v1", library=library)
    imported = import_asset(project, "performer", "v1", "lead", library=library)
    scene = json.loads((project / "artifacts" / "scene_plan.json").read_text())["scenes"][0]
    scene.update(generation_context={"artist_ids": ["lead"]}, render_route="cast_multi_ref")
    spec = from_scene(scene, project)
    save(project, spec, holder="user", lease_id=lease)
    expected_hash = None
    for attempt in range(2):
        body = render_body(client, lease)
        expected_hash = expected_hash or body["compiled_hash"]
        assert body["compiled_hash"] == expected_hash
        body["request_id"] = uuid.uuid4().hex
        pending = client.post("/api/shot/film/shot1/render", json=body).json()
        body["confirmation_token"] = pending["confirmation_token"]
        result = client.post("/api/shot/film/shot1/render", json=body).json()
        assert result["status"] == "succeeded", result
        assert result["asset"]["asset_bindings"][0]["library_id"] == manifest["library_id"]
        assert result["asset"]["asset_bindings"][0]["version"] == "v1"
        assert load_project_artist(project, "lead")["id"] == "lead"
    ledger_path = project / imported["snapshot_path"] / "ledger.jsonl"
    assert len(ledger_path.read_text().splitlines()) == 2
    cost = json.loads((project / "cost_log.json").read_text())
    assert all(entry["asset_bindings"][0]["sha256"] == manifest["sha256"] for entry in cost["entries"])
    other = project.parent / "other-film"
    other.mkdir()
    import_asset(other, "performer", "v1", "lead", library=library)
    assert not (other / "assets" / "artists" / "lead" / "ledger.jsonl").exists()
    record_project_artist_ledger(other, "lead", {"asset": "assets/images/other.png", "tool": "mock",
        "human_input": {"source": "other"}})
    assert len(ledger_path.read_text().splitlines()) == 2
    assert not list(library.rglob("ledger.jsonl"))


def test_http_asset_import_and_private_fork_are_explicit_and_do_not_overwrite_aliases(api, monkeypatch):
    from lib import paths
    from lib.project_assets import publish_asset
    from tests.fixtures.artist_factory import write_artist
    client, tool, lease, project = api
    source = project.parent / "catalog"
    write_artist(source, "solo")
    library = project.parent / "library"
    manifest = publish_asset(source / "solo", "performer", "v1", library=library)
    monkeypatch.setattr(paths, "LIBRARY_DIR", library)
    base = "/api/project/film/assets"
    body = {"library_id": manifest["library_id"], "asset_id": "performer", "version": "v1", "alias": "lead", "kind": "artist"}
    wrong = client.post(base + "/import", json={**body, "library_id": "wrong-library"})
    assert wrong.status_code == 409
    assert not (project / "assets" / "bindings.json").exists()
    imported = client.post(base + "/import", json=body)
    assert imported.status_code == 200, imported.text
    assert imported.json()["binding"]["alias"] == "lead"
    forked = client.post(base + "/fork", json={"alias": "lead", "new_alias": "local-look"})
    assert forked.status_code == 200, forked.text
    assert forked.json()["binding"]["mode"] == "private"
    assert client.post(base + "/fork", json={"alias": "lead", "new_alias": "local-look"}).status_code == 409
    assert len(client.get(base + "/bindings").json()["bindings"]) == 2
    assert client.post(base + "/import", json={**body, "asset_id": "../escape"}).status_code == 400
    assert client.post(base + "/fork", json={"alias": "lead", "new_alias": "evil"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert not tool.calls


def test_generic_imported_reference_is_version_bound_and_cannot_be_changed_by_resupplying_its_hash(api):
    import hashlib
    from lib.project_assets import publish_asset, import_asset
    client, tool, lease, project = api
    source = project.parent / "environment-source"
    source.mkdir()
    (source / "background.png").write_bytes(base64.b64decode(png_uri("red").split(",")[1]))
    library = project.parent / "library"
    manifest = publish_asset(source, "room", "v1", kind="environment", library=library)
    binding = import_asset(project, "room", "v1", "room", kind="environment", library=library)
    relative = binding["snapshot_path"] + "/background.png"
    spec = read_spec(client)
    spec["route"] = "multi_ref"
    spec["references"] = [{"asset_path": relative, "sha256": hashlib.sha256((project / relative).read_bytes()).hexdigest(), "role": "environment"}]
    response = client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease})
    assert response.status_code == 200, response.text
    spec, compiled = preview(client, lease)
    assert compiled["asset_bindings"][0]["kind"] == "environment"
    assert compiled["asset_bindings"][0]["sha256"] == manifest["sha256"]
    (project / relative).write_bytes(base64.b64decode(png_uri("blue").split(",")[1]))
    spec["references"][0]["sha256"] = hashlib.sha256((project / relative).read_bytes()).hexdigest()
    response = client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ASSET_CHANGED"
    assert not tool.calls


@pytest.mark.parametrize("channel", ["image", "parent"])
def test_internal_reference_symlinks_cannot_hide_an_imported_asset_binding(api, channel):
    import hashlib
    from lib.project_assets import publish_asset, import_asset
    client, tool, lease, project = api
    source = project.parent / "room-source"
    source.mkdir()
    (source / "background.png").write_bytes(base64.b64decode(png_uri("red").split(",")[1]))
    library = project.parent / "library"
    publish_asset(source, "room", "v1", kind="environment", library=library)
    binding = import_asset(project, "room", "v1", "room", kind="environment", library=library)
    imported = project / binding["snapshot_path"] / "background.png"
    alias = project / "assets" / "images" / "internal-alias.png"
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(imported)
    imported.write_bytes(base64.b64decode(png_uri("blue").split(",")[1]))
    spec = read_spec(client)
    spec["route"] = "multi_ref"
    ref = {"asset_path": alias.relative_to(project).as_posix(), "sha256": hashlib.sha256(imported.read_bytes()).hexdigest(), "role": "environment"}
    if channel == "parent":
        private = alias.parent / "private.png"
        private.write_bytes(base64.b64decode(png_uri("red").split(",")[1]))
        ref.update(asset_path=private.relative_to(project).as_posix(), sha256=hashlib.sha256(private.read_bytes()).hexdigest(),
                   parent_asset=alias.relative_to(project).as_posix())
    spec["references"] = [ref]
    response = client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease})
    assert response.status_code in (403, 409)
    assert not tool.calls


def test_internal_project_symlink_does_not_create_a_second_project_identity(api):
    client, tool, lease, project = api
    (project.parent / "linked-film").symlink_to(project, target_is_directory=True)
    assert client.get("/api/shot/linked-film/shot1").status_code == 403
    assert not tool.calls


def test_dropped_generic_media_binding_is_not_claimed_in_generated_asset_or_cost_provenance(api):
    import hashlib
    from lib.project_assets import publish_asset, import_asset
    client, tool, lease, project = api
    source = project.parent / "unused-room"
    source.mkdir()
    (source / "background.png").write_bytes(base64.b64decode(png_uri("red").split(",")[1]))
    library = project.parent / "library"
    publish_asset(source, "room", "v1", kind="environment", library=library)
    binding = import_asset(project, "room", "v1", "room", kind="environment", library=library)
    relative = binding["snapshot_path"] + "/background.png"
    spec = read_spec(client)
    spec["route"] = "t2v"
    spec["references"] = [{"asset_path": relative, "sha256": hashlib.sha256((project / relative).read_bytes()).hexdigest(), "role": "environment"}]
    assert client.put("/api/shot/film/shot1", json={"spec": spec, "revision": spec["revision"], "lease_id": lease}).status_code == 200
    spec, compiled = preview(client, lease)
    assert compiled["payload"]["references"] == []
    assert compiled["asset_bindings"] == []
    body = render_body(client, lease)
    pending = client.post("/api/shot/film/shot1/render", json=body).json()
    body.update(request_id=pending["request_id"], confirmation_token=pending["confirmation_token"])
    result = client.post("/api/shot/film/shot1/render", json=body).json()
    assert result["status"] == "succeeded"
    assert result["asset"]["asset_bindings"] == []
    assert result["asset"]["candidates"][0]["asset_bindings"] == []
    assert json.loads((project / "cost_log.json").read_text())["entries"][0]["asset_bindings"] == []
