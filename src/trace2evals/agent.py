"""A bounded tool loop. Each trial owns its backend, shop state and evidence."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from .backends import V1_SYSTEM_PROMPT, V2_SYSTEM_PROMPT, Final, ToolResult, new_conversation
from .models import Trajectory
from .tools import TOOLS, ToolSession

MAX_STEPS = 10
MAX_TOOL_CALLS = 12


def run_agent_and_capture_trace(
    tracer: trace.Tracer,
    user_message: str,
    scenario_id: str = "adhoc",
    *,
    principal: str | None = None,
    tool_mode: str = "enforced",
    version: str | None = None,
    backend: str | None = None,
    conversation=None,
) -> tuple[str, Trajectory]:
    session = ToolSession(principal, tool_mode)
    trajectory = Trajectory("", scenario_id, user_message, "", principal=principal)
    started = time.monotonic()
    with tracer.start_as_current_span("invoke_agent support-agent") as root:
        root.set_attribute("gen_ai.operation.name", "invoke_agent")
        trajectory.trace_id = format(root.get_span_context().trace_id, "032x")
        try:
            convo = conversation or new_conversation(version, backend)
            trajectory.agent_version = convo.version
            trajectory.metadata = {
                "backend": convo.backend_name,
                "model": getattr(convo, "model", "scripted"),
                "tool_mode": tool_mode,
                "max_steps": MAX_STEPS,
                "max_tool_calls": MAX_TOOL_CALLS,
                "request_timeout_seconds": 30,
                "max_retries": 0,
                "max_output_tokens": 2048,
                "reasoning_effort": "low",
                "usage_complete": True,
                "input_tokens": 0,
                "output_tokens": 0,
                "prompt_sha256": hashlib.sha256(
                    (
                        getattr(
                            convo,
                            "_system",
                            V2_SYSTEM_PROMPT if convo.version == "v2" else V1_SYSTEM_PROMPT,
                        )
                    ).encode()
                ).hexdigest(),
                "tool_schema_sha256": hashlib.sha256(
                    json.dumps(TOOLS, sort_keys=True).encode()
                ).hexdigest(),
            }
            results = None
            for step in range(MAX_STEPS + 1):
                with tracer.start_as_current_span("chat") as chat:
                    chat.set_attribute("gen_ai.operation.name", "chat")
                    convo.last_usage = None
                    try:
                        action = (
                            convo.start(user_message)
                            if results is None
                            else convo.on_tool_results(results)
                        )
                    finally:
                        if convo.last_usage:
                            for key, value in convo.last_usage.items():
                                trajectory.metadata[key] += value
                                chat.set_attribute(f"gen_ai.usage.{key}", value)
                if isinstance(action, Final):
                    trajectory.final_answer, trajectory.claims = action.text, action.claims
                    break
                if (
                    step == MAX_STEPS
                    or not action
                    or len(trajectory.tool_calls) + len(action) > MAX_TOOL_CALLS
                ):
                    raise ValueError("agent execution budget exceeded or empty action")
                results = []
                for use in action:
                    with tracer.start_as_current_span(f"execute_tool {use.name}") as tool:
                        tool.set_attribute("gen_ai.operation.name", "execute_tool")
                        content, is_error = session.execute(use.name, use.arguments)
                        call = {
                            "name": use.name,
                            "arguments": use.arguments,
                            "result": content,
                            "is_error": is_error,
                        }
                        tool.set_attribute("trace2evals.call", json.dumps(call))
                        tool.set_attribute("trace2evals.sequence", len(trajectory.tool_calls))
                        if is_error:
                            tool.set_status(StatusCode.ERROR)
                    trajectory.tool_calls.append(call)
                    results.append(ToolResult(use.id, use.name, use.arguments, content, is_error))
            if not trajectory.final_answer.strip():
                raise ValueError("empty final answer")
            trajectory.metadata["response_model"] = getattr(convo, "response_model", None)
        except Exception as exc:
            # Persist the attempt, including state after earlier writes. Never serialize
            # provider exception text: it can contain credentials or request content.
            trajectory.metadata["usage_complete"] = False
            trajectory.status = "invalid"
            trajectory.error = type(exc).__name__
            root.set_status(StatusCode.ERROR, trajectory.error)
        trajectory.final_state = session.snapshot()
        trajectory.metadata["tool_call_count"] = len(trajectory.tool_calls)
        trajectory.metadata["duration_seconds"] = time.monotonic() - started
        root.set_attribute("trace2evals.schema_version", 2)
        payload = asdict(trajectory)
        payload.pop("tool_calls")
        root.set_attribute("trace2evals.trajectory", json.dumps(payload))
    return trajectory.final_answer, trajectory
