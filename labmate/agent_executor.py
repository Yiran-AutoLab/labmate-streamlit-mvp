from __future__ import annotations

from copy import copy
from typing import Any

from labmate.agent_registry import TOOL_REGISTRY


def execute_plan(plan: dict[str, Any], initial_context: dict[str, Any]) -> dict[str, Any]:
    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("Agent plan must contain a list of steps.")

    context = copy(initial_context)
    context["agent_plan"] = plan
    context["tool_log"] = []

    for index, step in enumerate(steps, start=1):
        if isinstance(step, str):
            tool_name = step
        elif isinstance(step, dict):
            tool_name = step.get("tool")
        else:
            raise ValueError(f"Invalid agent step at index {index}: {step!r}")

        if tool_name not in TOOL_REGISTRY:
            raise ValueError(f"Unknown agent tool: {tool_name}")

        before_keys = set(context.keys())
        context["tool_log"].append({"step": index, "tool": tool_name, "status": "started"})
        try:
            context = TOOL_REGISTRY[tool_name](context)
        except Exception as exc:
            context["tool_log"][-1] = {
                "step": index,
                "tool": tool_name,
                "status": "failed",
                "error": str(exc),
            }
            raise
        new_keys = sorted(set(context.keys()) - before_keys)
        context["tool_log"][-1] = {
            "step": index,
            "tool": tool_name,
            "status": "completed",
            "new_context_keys": new_keys,
        }

    for key in list(context.keys()):
        if key.endswith("_fn"):
            context.pop(key)

    return context
