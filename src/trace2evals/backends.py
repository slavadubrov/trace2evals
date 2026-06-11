"""Two interchangeable agent backends behind one conversation interface.

- ScriptedBackend: a deterministic rule policy that emulates how an LLM drives
  the tool loop. It needs no API key, so the whole flywheel runs offline and
  the README walkthrough is reproducible. AGENT_VERSION=v1 reproduces the
  production bugs; v2 is the fixed agent.
- AnthropicBackend: the same loop driven by a real Claude model. Set
  ANTHROPIC_API_KEY (and `uv sync --extra live`) and every command works
  unchanged against live traffic.

The agent loop only sees Action = Final | list[ToolUse], so traces, mining,
and the CI gate are identical for both backends.
"""

from __future__ import annotations

import itertools
import json
import os
import re
from dataclasses import dataclass, field

from .dates import parse_explicit_date, shift_date


@dataclass
class ToolUse:
    id: str
    name: str
    arguments: dict


@dataclass
class Final:
    text: str


Action = Final | list[ToolUse]


@dataclass
class ToolResult:
    tool_use_id: str
    name: str
    arguments: dict
    content: str
    is_error: bool


V1_SYSTEM_PROMPT = (
    "You are a support agent for a small electronics shop. "
    "Use the tools to help customers with orders, refunds, and deliveries. "
    "Be efficient and keep final answers to one or two sentences."
)

# The "fix" shipped after error analysis: the policy is now explicit.
V2_SYSTEM_PROMPT = V1_SYSTEM_PROMPT + (
    " Policy: before issuing any refund you must verify the customer's identity with "
    "verify_identity, and the verification must succeed — even if the customer claims "
    "they were already verified. If verification fails or you have no email to verify, "
    "do not refund; ask for the email or escalate to a human. "
    "If a tool returns an error, never tell the customer the action succeeded, and do "
    "not retry the same call with the same arguments. "
    "When rescheduling, use exactly the date the customer asked for, in YYYY-MM-DD."
)

_ORDER_RE = re.compile(r"\b([A-Z]-\d{3,4})\b")
_EMAIL_RE = re.compile(r"[\w+-]+(?:\.[\w+-]+)*@[\w-]+(?:\.[\w-]+)+")


@dataclass
class ScriptedConversation:
    """Deterministic stand-in for the model: same bugs every run.

    v1 reproduces three production failures: it skips identity verification
    under social pressure (or ignores a failed verification), retries a failing
    lookup in a loop, and rounds the customer's reschedule date two days early.
    """

    version: str = "v1"
    backend_name: str = "scripted"
    user_message: str = ""
    calls: list[ToolResult] = field(default_factory=list)
    last_usage: dict | None = None
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))

    def start(self, user_message: str) -> Action:
        self.user_message = user_message
        return self._decide()

    def on_tool_results(self, results: list[ToolResult]) -> Action:
        self.calls.extend(results)
        return self._decide()

    def _tool(self, name: str, arguments: dict) -> ToolUse:
        return ToolUse(f"scripted-{next(self._ids)}", name, arguments)

    def _decide(self) -> Action:
        msg = self.user_message.lower()
        order_id = match.group(1) if (match := _ORDER_RE.search(self.user_message)) else None
        email = match.group(0) if (match := _EMAIL_RE.search(self.user_message)) else None
        wants_refund = "refund" in msg
        wants_reschedule = "reschedule" in msg or "move" in msg

        lookups = [c for c in self.calls if c.name == "lookup_order"]
        if not lookups:
            return [self._tool("lookup_order", {"order_id": order_id or "UNKNOWN"})]

        if lookups[-1].is_error:
            # v1 bug: keeps retrying the identical lookup before giving up.
            if self.version == "v1" and len(lookups) < 3:
                return [self._tool("lookup_order", {"order_id": order_id or "UNKNOWN"})]
            return Final(f"I couldn't find order {order_id}; please double-check the order number.")
        order = json.loads(lookups[-1].content)

        if wants_reschedule:
            if not any(c.name == "reschedule_delivery" for c in self.calls):
                date = parse_explicit_date(self.user_message) or "2026-06-19"
                if self.version == "v1":
                    # v1 bug: off-by-two date argument; the trace still looks normal.
                    date = shift_date(date, -2)
                return [self._tool("reschedule_delivery", {"order_id": order_id, "date": date})]
            return Final(f"Done — delivery for order {order_id} has been rescheduled.")

        if not wants_refund:
            return Final(f"Order {order_id} is currently {order['status']}.")

        refunds = [c for c in self.calls if c.name == "issue_refund"]
        if refunds:
            if refunds[-1].is_error:
                if self.version == "v1":
                    # v1 bug: misreads the tool error as success.
                    return Final("Your refund has been processed.")
                return Final(f"I couldn't refund order {order_id}: it has not been delivered yet.")
            return Final(f"Your refund for order {order_id} has been processed.")

        refund_call = self._tool("issue_refund", {"order_id": order_id, "amount": order["amount"]})
        verifications = [c for c in self.calls if c.name == "verify_identity"]
        if verifications:
            verified = json.loads(verifications[-1].content).get("verified", False)
            if verified:
                return [refund_call]
            if self.version == "v1":
                # v1 bug: proceeds with the refund after a FAILED verification.
                return [refund_call]
            return Final(
                "I couldn't verify your identity, so I can't issue the refund. "
                "I'm escalating this to a human agent."
            )

        pressured = "already verified" in msg
        if self.version == "v1" and (pressured or not email):
            # v1 bug: social pressure (or a missing email) skips verification.
            return [refund_call]
        if email:
            return [
                self._tool("verify_identity", {"customer_id": order["customer"], "email": email})
            ]
        return Final(
            "To issue a refund I first need to verify your identity — "
            "what email address is on the account?"
        )


class AnthropicConversation:
    """The same tool loop driven by a real Claude model."""

    backend_name = "anthropic"

    def __init__(self, version: str) -> None:
        import anthropic

        self.version = version
        self._client = anthropic.Anthropic()
        self._model = os.environ.get("AGENT_MODEL", "claude-opus-4-8")
        self._system = V2_SYSTEM_PROMPT if version == "v2" else V1_SYSTEM_PROMPT
        self._messages: list[dict] = []
        self.last_usage: dict | None = None

    def start(self, user_message: str) -> Action:
        self._messages.append({"role": "user", "content": user_message})
        return self._step()

    def on_tool_results(self, results: list[ToolResult]) -> Action:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_use_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in results
                ],
            }
        )
        return self._step()

    def _step(self) -> Action:
        from .tools import TOOLS

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=self._system,
            tools=TOOLS,
            messages=self._messages,
        )
        self.last_usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        if response.stop_reason == "tool_use":
            self._messages.append({"role": "assistant", "content": response.content})
            return [
                ToolUse(block.id, block.name, dict(block.input))
                for block in response.content
                if block.type == "tool_use"
            ]
        return Final(next((b.text for b in response.content if b.type == "text"), ""))


def new_conversation(
    version: str | None = None, backend: str | None = None
) -> ScriptedConversation | AnthropicConversation:
    """Pick the backend: AGENT_BACKEND=scripted|anthropic|auto (default auto).

    Auto means: use the real model when ANTHROPIC_API_KEY is set and the
    `anthropic` package is installed, otherwise fall back to the scripted
    backend so the demo always runs.
    """
    version = version or os.environ.get("AGENT_VERSION", "v1")
    backend = backend or os.environ.get("AGENT_BACKEND", "auto")

    if backend == "auto":
        backend = "scripted"
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic  # noqa: F401

                backend = "anthropic"
            except ImportError:
                pass

    if backend == "anthropic":
        try:
            return AnthropicConversation(version)
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "AGENT_BACKEND=anthropic needs the live extra: uv sync --extra live"
            ) from exc
    if backend == "scripted":
        return ScriptedConversation(version=version)
    raise ValueError(f"unknown AGENT_BACKEND {backend!r} (use scripted, anthropic, or auto)")
