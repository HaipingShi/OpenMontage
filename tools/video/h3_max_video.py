"""fal's post-trained H3 Max; deliberately distinct from MiniMax H3."""

from tools.fal_media import FalMedia


class H3MaxVideo(FalMedia):
    name = "h3_max_video"
    capability = "video_generation"
    capabilities = ["text_to_video", "image_to_video", "reference_to_video"]
    limits = {
        "max_reference_images": 9, "max_prompt_chars": 50000,
        "negative_prompt_field": None, "durations": {"minimum": 0.92, "maximum": 15},
        "first_last_frame": True, "control_inputs": [],
        "routes": ["t2v", "i2v", "first_last", "multi_ref"],
    }
    supports = dict.fromkeys(
        ["text_to_video", "image_to_video", "reference_to_video", "native_audio"], True
    )
    best_for = ["Fast H3 Max drafts and final clips with reference control"]
    routes = {
        "h3-max": {op: f"minimax/h3-max/{op.replace('_', '-')}" for op in capabilities}
    }
