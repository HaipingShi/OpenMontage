"""Nano Banana Pro via Replicate with model fallback explicitly disabled."""

from tools.schema_media import SchemaMedia


class GeminiReplicateImage(SchemaMedia):
    name = "gemini_replicate_image"
    provider = "replicate"
    capability = "image_generation"
    credential = "REPLICATE_API_TOKEN"
    dependencies = ["env:REPLICATE_API_TOKEN"]
    install_instructions = "Configure REPLICATE_API_TOKEN."
    catalog_file = "replicate_refresh.json"
    capabilities = ["text_to_image", "image_edit"]
    limits = {
        "max_reference_images": 14, "max_prompt_chars": None,
        "negative_prompt_field": None, "durations": None,
        "first_last_frame": False, "control_inputs": [], "routes": ["t2v", "i2v", "multi_ref"],
    }
    supports = {"image_edit": True, "multiple_reference_images": True}
    best_for = ["Nano Banana Pro through Replicate"]
    models = {
        "gemini-3-pro-image": {
            "generate": "google/nano-banana-pro",
            "edit": "google/nano-banana-pro",
        },
        "gemini-nano-banana-2.1": {
            "generate": "google/nano-banana-2.1",
            "edit": "google/nano-banana-2.1",
        },
    }
