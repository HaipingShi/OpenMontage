"""Discoverable zero-cost wrapper for the pure shared shot compiler."""
from tools.base_tool import BaseTool, ToolResult, ToolStability


class ShotCompiler(BaseTool):
    name = "shot_compiler"
    capability = "shot_compilation"
    stability = ToolStability.BETA
    capabilities = ["compile_shot", "lint_shot"]
    supports = {"offline": True, "deterministic": True, "structured_references": True}
    input_schema = {"type": "object", "required": ["spec", "tool_name"], "properties": {
        "spec": {"type": "object"}, "tool_name": {"type": "string"}, "context": {"type": "object"},
    }}
    best_for = ["Compile complex shot sheets before image/video generation without browser or network"]
    side_effects = []

    def execute(self, inputs):
        from lib.shot_compiler import compile

        try:
            data = compile(inputs["spec"], inputs["tool_name"], inputs.get("context"))
            return ToolResult(success=True, data=data, cost_usd=0.0)
        except (KeyError, TypeError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc), cost_usd=0.0)
