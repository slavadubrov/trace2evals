"""Portable run evidence, independent of the agent backend and tracing SDK."""

from dataclasses import dataclass, field


@dataclass
class Trajectory:
    trace_id: str
    scenario_id: str
    user_message: str
    final_answer: str
    agent_version: str = "v2"
    tool_calls: list[dict] = field(default_factory=list)
    final_state: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)
    principal: str | None = None
    status: str = "completed"
    error: str | None = None
    metadata: dict = field(default_factory=dict)
