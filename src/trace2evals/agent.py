"""A small tool-using support agent, instrumented with OTel GenAI-semconv spans.

Span tree per run: invoke_agent -> chat (one per backend step) -> execute_tool
(one per tool invocation). The manual loop (instead of an SDK tool runner) is
what lets us wrap every step in its own span.

run_agent_and_capture_trace returns the fresh Trajectory directly so the
regression suite can grade a re-run without round-tripping through the trace
store — the spans still land there as a side effect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from opentelemetry import trace
from opentelemetry.trace import StatusCode

from .backends import Final, ToolResult, new_conversation
from .tools import execute_tool, get_state, reset_state

MAX_STEPS = 10


@dataclass
class Trajectory:
    """One agent run: the path, the answer, and the final environment state."""

    trace_id: str
    scenario_id: str
    user_message: str
    final_answer: str
    agent_version: str = "v1"
    tool_calls: list[dict] = field(default_factory=list)  # {name, arguments, result, is_error}
    final_state: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def run_agent_and_capture_trace(
    tracer: trace.Tracer, user_message: str, scenario_id: str = "adhoc"
) -> tuple[str, Trajectory]:
    convo = new_conversation()
    reset_state()
    tool_calls: list[dict] = []
    final_text = ""

    with tracer.start_as_current_span("invoke_agent support-agent") as agent_span:
        agent_span.set_attribute("gen_ai.operation.name", "invoke_agent")
        agent_span.set_attribute("gen_ai.agent.name", "support-agent")
        agent_span.set_attribute("trace2evals.scenario_id", scenario_id)
        agent_span.set_attribute("trace2evals.agent_version", convo.version)
        agent_span.set_attribute("trace2evals.backend", convo.backend_name)
        agent_span.set_attribute(
            "gen_ai.input.messages", json.dumps([{"role": "user", "content": user_message}])
        )

        with tracer.start_as_current_span("chat") as chat_span:
            chat_span.set_attribute("gen_ai.operation.name", "chat")
            action = convo.start(user_message)
            _set_usage(chat_span, convo)

        for _ in range(MAX_STEPS):
            if isinstance(action, Final):
                final_text = action.text
                break
            results = []
            for tool_use in action:
                with tracer.start_as_current_span(f"execute_tool {tool_use.name}") as tool_span:
                    tool_span.set_attribute("gen_ai.operation.name", "execute_tool")
                    tool_span.set_attribute("gen_ai.tool.name", tool_use.name)
                    tool_span.set_attribute("gen_ai.tool.call.id", tool_use.id)
                    tool_span.set_attribute(
                        "gen_ai.tool.call.arguments", json.dumps(tool_use.arguments)
                    )
                    result, is_error = execute_tool(tool_use.name, tool_use.arguments)
                    tool_span.set_attribute("gen_ai.tool.call.result", result)
                    if is_error:
                        tool_span.set_status(StatusCode.ERROR)
                tool_calls.append(
                    {
                        "name": tool_use.name,
                        "arguments": tool_use.arguments,
                        "result": result,
                        "is_error": is_error,
                    }
                )
                results.append(
                    ToolResult(tool_use.id, tool_use.name, tool_use.arguments, result, is_error)
                )
            with tracer.start_as_current_span("chat") as chat_span:
                chat_span.set_attribute("gen_ai.operation.name", "chat")
                action = convo.on_tool_results(results)
                _set_usage(chat_span, convo)
        else:
            agent_span.set_status(StatusCode.ERROR, "max steps exceeded")

        final_state = get_state()
        agent_span.set_attribute("trace2evals.final_state", json.dumps(final_state))
        agent_span.set_attribute(
            "gen_ai.output.messages",
            json.dumps([{"role": "assistant", "content": final_text}]),
        )
        trace_id = format(agent_span.get_span_context().trace_id, "032x")

    trajectory = Trajectory(
        trace_id=trace_id,
        scenario_id=scenario_id,
        user_message=user_message,
        final_answer=final_text,
        agent_version=convo.version,
        tool_calls=tool_calls,
        final_state=final_state,
    )
    return final_text, trajectory


def _set_usage(chat_span: trace.Span, convo) -> None:
    if convo.last_usage:
        chat_span.set_attribute("gen_ai.usage.input_tokens", convo.last_usage["input_tokens"])
        chat_span.set_attribute("gen_ai.usage.output_tokens", convo.last_usage["output_tokens"])
