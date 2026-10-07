"""Pure compilation of shared shot sheets into prompts and reference contracts.

Identity and tool context are explicit snapshots. File verification, discovery,
pricing and transport adaptation belong to ``shot_execution``, never this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

from lib.shot_prompt_builder import _MOVEMENT_PHRASES, _SHOT_SIZE_PHRASES, build_shot_prompt


ROLE_ORDER = {role: rank for rank, role in enumerate((
    "identity", "first_frame", "wardrobe", "environment", "style",
    "composition", "last_frame", "depth", "pose",
))}
_NEGATION = re.compile(
    r"\b(?:no|without|not)\s+(?:[\w]+)|(?:没有|不(?:要|出现|含|带|允许|是)?|无)(?=[\u4e00-\u9fff])",
    re.IGNORECASE,
)
_FINE_HANDS = re.compile(
    r"缝|针眼|穿线|穿针|绣|写字|书写|弹奏|弹琴|编织|\b(?:sew\w*|stitch\w*|thread\w*|"
    r"writ\w*|embroider\w*|knit\w*|playing\s+(?:piano|guitar|violin)|play\s+(?:piano|guitar|violin))\b",
    re.IGNORECASE,
)
_NO_PEOPLE = re.compile(r"没有人物|没有人|无人|不出现人物|\bno\s+(?:people|person|characters?)\b|\bempty\s+of\s+people\b", re.I)
_POSITIVE_EXCLUSIONS = {
    "open drawer": "drawer fully shut", "drawer opens": "drawer fully shut",
    "drawer opens automatically": "drawer remains fully shut",
    "drawer opening": "drawer remains fully shut", "drawer opens by itself": "drawer remains fully shut",
    "抽屉打开": "抽屉保持完全闭合", "抽屉自动打开": "抽屉保持完全闭合",
    "people": "empty unoccupied setting", "person": "empty unoccupied setting", "人物": "空旷安静的环境",
    "plastic skin": "natural skin texture", "bronze tan": "natural light beige skin",
    "nose ring": "clean unpierced nose", "nose stud": "clean unpierced nose",
    "nose ring or nose stud": "clean unpierced nose", "tattoos": "smooth unmarked skin",
    "watermark": "clean image edges", "text": "purely pictorial composition",
    "extra people": "the specified cast fills the scene", "merged faces": "distinct facial features per person",
    "swapped outfits": "each person wears their own designated wardrobe",
    "identical twins": "each person has distinct facial features",
    "无纹身": "皮肤光洁", "纹身": "皮肤光洁", "鼻环": "鼻部自然光洁",
}
_VISIBLE_TRAITS = {
    "hands": re.compile(r"hand|wrist|finger|bracelet|skin|sleeve|手|腕|指|肤|袖|手绳|指南针", re.I),
    "back": re.compile(r"hair|back|shoulder|wardrobe|shirt|coat|dress|hood|necklace|发|背|肩|衣|衫|外套|项链", re.I),
    "upper_body": re.compile(r"wardrobe|shirt|coat|skin|shoulder|衣|衫|肤|肩", re.I),
    "full_body": re.compile(r"build|height|wardrobe|shirt|coat|trouser|shoe|衣|衫|体型|裤|鞋", re.I),
}
_PERSON_KINDS = {"artist", "character"}
_ROUTE_NAMES = {"t2v", "i2v", "first_last", "multi_ref", "lipsync"}


def resolve_limits(limits: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """Resolve metadata-only model variants without constructing a provider tool."""
    resolved = copy.deepcopy(limits)
    variants = limits.get("variants") or {}
    for key in ("model", "model_version", "model_variant"):
        selected = options.get(key)
        if selected is not None and str(selected) in variants:
            resolved.update(copy.deepcopy(variants[str(selected)]))
    return resolved


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _hash_safe(value: Any) -> Any:
    """A rejected nonfinite draft still has a deterministic preview identity."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_number": str(value)}
    if isinstance(value, dict):
        return {key: _hash_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_hash_safe(item) for item in value]
    return value


def _number(value: Any, minimum: float, maximum: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and minimum <= value <= maximum)


def _parts(subject: dict[str, Any]) -> list[str]:
    value = subject.get("visible_parts", ["face", "full_body"])
    if isinstance(value, str):
        return [value]
    return [part for part in value if isinstance(part, str)] if isinstance(value, list) else []


def _identity(snapshot: dict[str, Any], subject: dict[str, Any]) -> dict[str, Any]:
    if isinstance(snapshot.get("identity"), dict):
        canon = snapshot["identity"]
        looks = canon.get("looks") or []
        look = next((look for look in looks if look.get("id") == subject.get("look")), looks[0] if looks else {})
        return {
            **canon, "wardrobe": look.get("wardrobe", ""),
            "name": snapshot.get("names", {}).get("latin") or snapshot.get("names", {}).get("stage") or subject.get("ref_id", ""),
            "visible_traits": canon.get("visible_traits") or snapshot.get("visible_traits") or {},
        }
    return snapshot


def _normalize_canon(text: str) -> tuple[str, list[str]]:
    """Move explicit negative clauses out of canon; never rewrite user notes."""
    positive, excluded = [], []
    # Remove the artist helper's instruction while keeping a positive cast rule.
    text = re.sub(r"(?:do not blend their features|does not resemble any real person)", "each person retains distinct original features", text, flags=re.I)
    for clause in re.split(r"[.;。；,，]\s*", text):
        clause = clause.strip()
        if not clause:
            continue
        match = _NEGATION.search(clause)
        if match:
            prefix = clause[:match.start()].strip().rstrip(":")
            # Subject-relative predicates such as 'who is not ...' are not canon.
            prefix = re.sub(r"\b(?:who|is|are|does|do)\s*$", "", prefix, flags=re.I).strip()
            if prefix:
                positive.append(prefix)
            negative = clause[match.start():]
            negative = re.sub(r"^(?:no|without|not)\s+|^(?:没有|不要|不出现|不含|不带|无)", "", negative, flags=re.I)
            excluded.append(negative)
        else:
            positive.append(clause)
    return ". ".join(positive), excluded


def _all_text(spec: dict[str, Any]) -> str:
    fields = [spec.get("intent"), spec.get("notes")]
    env = spec.get("environment") or {}
    if isinstance(env, dict):
        fields.extend(v for v in env.values() if isinstance(v, str))
        fields.extend(_strings(env.get("palette")))
    for subject in spec.get("subjects") or []:
        if isinstance(subject, dict):
            fields.extend(subject.get(key) for key in ("action", "placement", "look"))
    return " ".join(_text(item) for item in fields if isinstance(item, str))


def _contradiction(spec: dict[str, Any]) -> list[str]:
    text = _all_text(spec)
    contradictions = []
    light_on = re.search(r"灯(?:光)?亮|灯(?:光)?开启|\b(?:lamp|light)\s+(?:is\s+)?(?:on|lit)\b", text, re.I)
    light_off = re.search(r"灯(?:光)?不亮|灯(?:光)?关闭|\b(?:lamp|light)\s+(?:is\s+)?(?:off|unlit)\b", text, re.I)
    if light_on and light_off:
        contradictions.append("灯亮与灯不亮同时出现 / the light is both on and off")
    persons = [s for s in spec.get("subjects", []) if isinstance(s, dict) and s.get("kind") in _PERSON_KINDS]
    if _NO_PEOPLE.search(text) and persons:
        contradictions.append("描述要求无人，但镜头主体中包含人物 / an empty shot includes a person")
    env = spec.get("environment") or {}
    if isinstance(env, dict):
        time = _text(env.get("time"))
        palette = " ".join(_text(v) for v in _strings(env.get("palette")))
        night = re.search(r"night|midnight|深夜|夜晚|夜间|午夜", time, re.I)
        daytime = re.search(r"daylight|midday|noon|正午|白昼|日光|太阳|阳光", palette, re.I)
        day = re.search(r"midday|noon|正午|白昼", time, re.I)
        moonlight = re.search(r"midnight|moonlight|午夜|月光", palette, re.I)
        if (night and daytime) or (day and moonlight):
            contradictions.append("色板的光照时段与环境时间冲突 / palette lighting conflicts with time of day")
    return contradictions


def _valid_duration(duration: Any, constraint: Any) -> bool:
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
        return False
    if isinstance(constraint, list):
        return any(duration == item for item in constraint)
    if isinstance(constraint, dict):
        return constraint.get("minimum", 0) <= duration <= constraint.get("maximum", math.inf)
    return True


def compile(spec: dict[str, Any], tool_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a deterministic prompt, exact reference contract and semantic lint.

    ``context`` (or ``spec.compile_context``) is a descriptor with ``limits``,
    ``tool_options``, ``capability`` and an ``estimated_cost`` snapshot. Unknown
    prices remain ``None``; the execution boundary must obtain a quote.
    """
    lint: list[dict[str, str]] = []

    def report(level: str, code: str, message: str) -> None:
        lint.append({"level": level, "code": code, "message": message})

    if not isinstance(spec, dict):
        raise ValueError("shot spec must be an object")
    spec = copy.deepcopy(spec)
    context = copy.deepcopy(context if context is not None else spec.get("compile_context") or {})
    if not isinstance(context, dict):
        raise ValueError("compile context must be an object")
    if isinstance(context.get("tools"), dict):
        context = context["tools"].get(tool_name) or {}
    options = context.get("tool_options") or {}
    if not isinstance(options, dict):
        options = {}; report("error", "INVALID_TOOL_OPTIONS", "tool_options 必须为结构化对象")
    raw_limits = context.get("limits") or {}
    if not isinstance(raw_limits, dict):
        raw_limits = {}
    if not raw_limits:
        report("warn", "MISSING_TOOL_LIMITS", "工具未声明 limits；最多使用一张参考图，不启用首尾帧或控制输入")
        raw_limits = {"max_reference_images": 1, "first_last_frame": False, "control_inputs": [], "negative_prompt_field": None}
    limits = resolve_limits(raw_limits, options)
    route = spec.get("route", "t2v")
    if route not in _ROUTE_NAMES:
        report("error", "UNSUPPORTED_ROUTE", f"未知生成方式：{route}")
    elif "routes" in limits and route not in limits["routes"]:
        report("error", "UNSUPPORTED_ROUTE", f"{tool_name} 不支持生成方式 {route}；请明确修改镜头路线")
    framing = spec.get("framing") or {}
    movement = spec.get("movement") or {}
    environment = spec.get("environment") or {}
    if not all(isinstance(v, dict) for v in (framing, movement, environment)):
        report("error", "INVALID_STRUCTURE", "framing、movement 和 environment 必须为结构化对象")
        framing = framing if isinstance(framing, dict) else {}
        movement = movement if isinstance(movement, dict) else {}
        environment = environment if isinstance(environment, dict) else {}
    if framing.get("shot_size", "medium") not in _SHOT_SIZE_PHRASES:
        report("error", "INVALID_STRUCTURE", "framing.shot_size 不是可用景别")
    for field, minimum, maximum in (("lens_mm", 1, 2000), ("camera_height", -100000, 100000)):
        if field in framing and not _number(framing[field], minimum, maximum):
            report("error", "INVALID_STRUCTURE", f"framing.{field} 必须在 {minimum} 到 {maximum} 之间")
    if movement.get("type", "static") not in {*_MOVEMENT_PHRASES, "orbit", "follow"}:
        report("error", "INVALID_STRUCTURE", "movement.type 不是可用运动类型")
    if not isinstance(movement.get("intensity", 0), int) or isinstance(movement.get("intensity", 0), bool) or not 0 <= movement.get("intensity", 0) <= 3:
        report("error", "INVALID_STRUCTURE", "movement.intensity 必须是 0 到 3 的整数")
    for warning in _strings(spec.get("initialization_warnings")):
        report("warn", "INITIALIZATION_WARNING", warning)
    subjects = spec.get("subjects") or []
    if not isinstance(subjects, list):
        report("error", "INVALID_STRUCTURE", "subjects 必须为数组"); subjects = []
    subjects = [subject for subject in subjects if isinstance(subject, dict)]
    persons = [subject for subject in subjects if subject.get("kind") in _PERSON_KINDS]
    cause = _text(movement.get("cause"))
    if movement.get("type", "static") != "static" and not cause:
        report("error", "MISSING_MOVEMENT_CAUSE", "非静止运动必须填写 cause，说明人物、环境或相机运动的来源")
    intensity = movement.get("intensity", 0)
    if isinstance(intensity, (int, float)) and intensity >= 1 and not persons:
        report("warn", "MOTION_WITHOUT_PERSON", "镜头中无人但运动强度较高；检查抽屉等道具是否会无故自行运动")
    for problem in _contradiction({**spec, "subjects": subjects, "environment": environment}):
        report("error", "CONTRADICTORY_DESCRIPTION", problem)
    shot_size = framing.get("shot_size", "medium")
    if shot_size in ("close_up", "insert", "extreme_close_up") and _FINE_HANDS.search(_all_text({**spec, "subjects": subjects, "environment": environment})):
        report("warn", "FINE_HAND_ACTION", "精细手部动作在特写中容易失真；建议放宽景别、使用首尾帧，或改用实拍素材")

    exclusions = [_text(value) for value in spec.get("exclusions", []) if isinstance(value, str)]
    identities = spec.get("identity_context") or {}
    subject_descriptions = []
    for subject in subjects:
        ref_id = _text(subject.get("ref_id"))
        snapshot = identities.get(ref_id, {}) if isinstance(identities, dict) else {}
        canon = _identity(snapshot, subject) if isinstance(snapshot, dict) else {}
        parts = _parts(subject)
        details = []
        if "face" in parts:
            details.append(_text(canon.get("prompt_core")))
        else:
            visible = canon.get("visible_traits") or {}
            for part in parts:
                traits = visible.get(part) if isinstance(visible, dict) else None
                if isinstance(traits, list):
                    details.extend(_text(trait) for trait in traits)
                elif isinstance(traits, str):
                    details.append(traits)
                elif part in _VISIBLE_TRAITS:
                    details.extend(trait for trait in canon.get("locked_traits", []) if isinstance(trait, str) and _VISIBLE_TRAITS[part].search(trait))
        wardrobe = _text(canon.get("wardrobe"))
        if wardrobe:
            details.append(f"Wardrobe: {wardrobe}")
        clean_canon, canon_exclusions = _normalize_canon(". ".join(filter(None, details)))
        exclusions.extend(canon_exclusions)
        negative = _text(canon.get("negative_prompt"))
        if negative:
            exclusions.extend(part.strip() for part in negative.split(",") if part.strip())
        name = _text(canon.get("name")) or ref_id
        description = ". ".join(filter(None, [name, clean_canon, _text(subject.get("placement")), _text(subject.get("action"))]))
        subject_descriptions.append(description)
    if len(persons) > 1:
        subject_descriptions.append("Each person retains distinct facial features and their own designated wardrobe")
    environment_description = ", ".join(filter(None, [_text(environment.get("location")), _text(environment.get("time")),
        ", ".join(_text(color) for color in _strings(environment.get("palette")))]))
    if environment.get("lighting_key") and environment.get("lighting"):
        environment_description += ", " + _text(environment["lighting"])
    scene_description = ". ".join(filter(None, [_text(spec.get("intent")), *subject_descriptions, environment_description]))
    if cause:
        scene_description += f". Movement cause: {cause}"
    camera_text = []
    if framing.get("camera_height") is not None:
        camera_text.append(f"camera height {framing['camera_height']} m")
    if framing.get("camera_angle") is not None:
        angle = framing["camera_angle"]
        camera_text.append(f"camera angle {angle}" + (" degrees" if isinstance(angle, (int, float)) else ""))
    if camera_text:
        scene_description += ". " + ", ".join(camera_text)
    negative_field = limits.get("negative_prompt_field")
    exclusions = list(dict.fromkeys(value for value in exclusions if value))
    negative_prompt = ", ".join(exclusions) if negative_field else ""
    if not negative_field:
        for exclusion in exclusions:
            key = re.sub(r"^(?:no|not|without)\s+", "", exclusion.lower().strip())
            rewritten = _POSITIVE_EXCLUSIONS.get(key)
            if rewritten:
                scene_description += ". " + rewritten
            else:
                report("warn", "EXCLUSION_DROPPED", f"工具没有 negative 字段，无法安全正向改写，已丢弃：{exclusion}")
    movement_type = {"orbit": "orbital", "follow": "steadicam"}.get(movement.get("type"), movement.get("type", "static"))
    scene = {"description": scene_description, "shot_language": {
        "shot_size": shot_size, "lens_mm": framing.get("lens_mm"),
        "depth_of_field": framing.get("depth_of_field"), "camera_movement": movement_type,
        "lighting_key": environment.get("lighting_key") or environment.get("lighting"), "color_temperature": environment.get("color_temperature"),
    }, "texture_keywords": spec.get("texture_keywords") or []}
    prompt = build_shot_prompt(scene, spec.get("style_context") or {})
    notes = spec.get("notes", "")
    if isinstance(notes, str) and notes:
        prompt += "\n" + notes
        if len(notes) > 4000:
            report("error", "NOTES_TOO_LONG", "附加说明超过 4000 字符")
    if _NEGATION.search(prompt):
        report("error", "POSITIVE_NEGATION", "最终正向提示词包含 no / without / not / 中文否定表达；请改成正向结构描述或 exclusions")
    max_prompt = limits.get("max_prompt_chars")
    if isinstance(max_prompt, int) and len(prompt) > max_prompt:
        report("error", "PROMPT_TOO_LONG", f"提示词共 {len(prompt)} 字符，超过工具上限 {max_prompt}；请缩短结构字段")
    generation_context = spec.get("generation_context") or {}
    duration = generation_context.get("generation_duration_seconds", spec.get("duration_seconds")) if isinstance(generation_context, dict) else spec.get("duration_seconds")
    constraints = [limits.get("durations")]
    constraints.append((limits.get("durations_by_route") or {}).get(route))
    constraints.append((limits.get("durations_by_resolution") or {}).get(options.get("resolution") or spec.get("target_resolution")))
    if any(not _valid_duration(duration, constraint) for constraint in constraints):
        report("error", "UNSUPPORTED_DURATION", f"镜头时长 {duration} 不符合 {tool_name} 的路线/分辨率时长要求；请明确调整")

    references = spec.get("references") or []
    if not isinstance(references, list):
        report("error", "INVALID_STRUCTURE", "references 必须为数组"); references = []
    refs = []
    for reference in references:
        if not isinstance(reference, dict) or not _text(reference.get("asset_path")) or reference.get("role") not in ROLE_ORDER:
            report("error", "INVALID_REFERENCE", "参考图必须有 asset_path 和有效 role")
        else:
            refs.append(reference)
    refs.sort(key=lambda reference: ROLE_ORDER[reference["role"]])
    controls = {}
    for reference in refs:
        if reference["role"] in ("depth", "pose"):
            if reference["role"] in (limits.get("control_inputs") or []):
                controls.setdefault(reference["role"], reference["asset_path"])
            else:
                report("warn", "UNSUPPORTED_CONTROL_INPUT", f"{tool_name} 没有明确的 {reference['role']} 输入映射，未传入：{reference['asset_path']}")
    ordinary = [reference for reference in refs if reference["role"] not in ("depth", "pose", "last_frame")]
    video = context.get("capability") == "video_generation"
    first = next((reference["asset_path"] for reference in ordinary if reference["role"] == "first_frame"), None)
    last = next((reference["asset_path"] for reference in refs if reference["role"] == "last_frame"), None) if limits.get("first_last_frame") else None
    eligible = ordinary
    if video:
        if route == "t2v":
            eligible = []
        elif route == "i2v":
            first = first or next((reference["asset_path"] for reference in ordinary if reference["role"] == "composition"), None)
            eligible = [reference for reference in ordinary if first and reference["asset_path"] == first]
        elif route == "first_last":
            frame_paths = {path for path in (first, last) if path}
            eligible = [reference for reference in refs if reference["role"] not in ("depth", "pose") and reference["asset_path"] in frame_paths]
        if route not in ("i2v", "first_last"):
            first = None
        if route != "first_last":
            last = None
        unreachable = [reference for reference in refs if reference["role"] not in ("depth", "pose") and reference not in eligible]
        if unreachable:
            report("warn", "ROUTE_REFERENCE_DROPPED", f"{route} 无法传入以下引用，实际 payload 已排除：" + ", ".join(reference["asset_path"] for reference in unreachable))
            if any(reference["role"] == "identity" for reference in unreachable):
                report("error", "ROUTE_IDENTITY_DROPPED", f"{route} 无法传入独立身份图；请明确改用 multi_ref，或将身份与人物组合到同一张首帧/构图图后重新导入")
    elif context.get("capability") != "image_generation":
        report("warn", "MISSING_TOOL_CAPABILITY", "工具能力未明确声明；参考图使用保守单图上限")
    maximum = limits.get("max_reference_images", 1)
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
        maximum = 1; report("warn", "INVALID_TOOL_LIMIT", "max_reference_images 未有效声明，使用保守上限 1")
    if context.get("capability") not in ("video_generation", "image_generation"):
        maximum = min(maximum, 1)
    paths = list(dict.fromkeys(reference["asset_path"] for reference in eligible))
    kept_paths = paths[:maximum]
    kept = [reference for reference in eligible if reference["asset_path"] in kept_paths]
    dropped = [reference for reference in eligible if reference["asset_path"] not in kept_paths]
    if dropped:
        report("warn", "REFERENCES_TRUNCATED", "参考图超过工具上限，按角色优先级丢弃：" + ", ".join(reference["asset_path"] for reference in dropped))
    for subject in persons:
        person_id = subject.get("ref_id")
        person_refs = [reference for reference in ordinary if reference.get("subject_ref") == person_id and reference["role"] == "identity"]
        if person_refs and not any(reference in kept for reference in person_refs):
            report("warn", "SUBJECT_IDENTITY_DROPPED", f"人物 {person_id} 的身份参考全部被工具上限截断")
    invisible = shot_size in ("insert", "extreme_close_up") or (persons and all("face" not in _parts(subject) for subject in persons))
    if invisible and any(reference["role"] == "identity" for reference in refs):
        report("warn", "IDENTITY_REFERENCE_OUT_OF_FRAME", "景别/可见部位不包含脸，但引用了 identity 图；考虑手部或服装参考")
    first = first if first in kept_paths else None
    if video:
        last = last if last in kept_paths else None
    if not limits.get("first_last_frame") and route == "first_last":
        report("error", "UNSUPPORTED_FIRST_LAST", "工具不支持首尾帧路线；请明确选择其他路线")
        first = None
    if not raw_limits.get("first_last_frame") and "MISSING_TOOL_LIMITS" in {item["code"] for item in lint}:
        first = None
    if route == "first_last" and (not first or not last):
        report("error", "MISSING_ROUTE_REFERENCE", "first_last 需要实际保留的有效首帧和尾帧")
    if route == "i2v" and not first:
        report("error", "MISSING_ROUTE_REFERENCE", "i2v 需要首帧或 composition 参考图")
    if route == "multi_ref" and not kept:
        report("error", "MISSING_ROUTE_REFERENCE", "multi_ref 需要工具能够接收的参考图")
    actual_controls = [reference for reference in refs if reference["role"] in controls and reference["asset_path"] == controls[reference["role"]]]
    actual = sorted(kept + actual_controls, key=lambda reference: ROLE_ORDER[reference["role"]])
    reference_map = {}
    for reference in actual:
        if reference.get("subject_ref"):
            mapped = reference_map.setdefault(reference["subject_ref"], [])
            if reference["asset_path"] not in mapped:
                mapped.append(reference["asset_path"])
    payload = {
        "reference_images": kept_paths,
        "references": actual, "reference_map": reference_map,
        "first_frame": first, "last_frame": last, "control_inputs": controls,
        "tool_options": options, "route": route,
        "duration_seconds": duration if _valid_duration(duration, None) else None,
        "aspect_ratio": spec.get("aspect_ratio"), "target_resolution": spec.get("target_resolution"),
    }
    estimated_cost = context.get("estimated_cost")
    if not isinstance(estimated_cost, (int, float)) or isinstance(estimated_cost, bool) or not math.isfinite(estimated_cost) or estimated_cost < 0:
        estimated_cost = None
        report("warn", "PRICE_QUOTE_REQUIRED", "费用尚未获得有效报价；执行前必须通过工具 estimate_cost / dry_run 报价")
    semantic_spec = {key: value for key, value in spec.items() if key not in ("revision", "updated_by", "updated_at", "compile_context")}
    hash_input = {"compiler_version": "1.0", "spec": semantic_spec, "tool_name": tool_name, "limits": limits,
                  "prompt": prompt, "negative_prompt": negative_prompt, "payload": payload}
    compiled_hash = hashlib.sha256(json.dumps(_hash_safe(hash_input), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    return {"prompt": prompt, "negative_prompt": negative_prompt, "payload": payload, "lint": lint,
            "compiled_hash": compiled_hash, "estimated_cost": estimated_cost,
            "generatable": not any(item["level"] == "error" for item in lint)}
