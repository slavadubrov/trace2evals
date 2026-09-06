"""Scripted teaching policy and explicit opt-in OpenAI Responses backend."""

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
    claims: list[dict] = field(default_factory=list)


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
    "When rescheduling, use exactly the date the customer asked for, in YYYY-MM-DD. "
    "For dates without a year, this synthetic shop uses calendar year 2026. "
    "If the date is missing or ambiguous, ask for clarification. "
    "Verification requires the order ID as well as the customer ID and email."
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
                date = parse_explicit_date(self.user_message)
                if date is None:
                    return Final("Please provide an unambiguous delivery date.")
                if self.version == "v1":
                    # v1 bug: off-by-two date argument; the trace still looks normal.
                    date = shift_date(date, -2)
                return [self._tool("reschedule_delivery", {"order_id": order_id, "date": date})]
            result = next(c for c in self.calls if c.name == "reschedule_delivery")
            if result.is_error:
                return Final("I could not reschedule delivery.")
            return Final(
                f"Delivery for order {order_id} has been rescheduled.",
                [{"kind": "reschedule", "order_id": order_id}],
            )

        if not wants_refund:
            return Final(f"Order {order_id} is currently {order['status']}.")

        refunds = [c for c in self.calls if c.name == "issue_refund"]
        if refunds:
            if refunds[-1].is_error:
                if self.version == "v1":
                    # v1 bug: misreads the tool error as success.
                    return Final(
                        "Your refund has been processed.",
                        [{"kind": "refund", "order_id": order_id}],
                    )
                return Final(f"I couldn't refund order {order_id}: it has not been delivered yet.")
            return Final(
                f"Your refund for order {order_id} has been processed.",
                [{"kind": "refund", "order_id": order_id}],
            )

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
                self._tool(
                    "verify_identity",
                    {"customer_id": order["customer"], "order_id": order_id, "email": email},
                )
            ]
        return Final(
            "To issue a refund I first need to verify your identity — "
            "what email address is on the account?"
        )


FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["refund", "reschedule"]},
                    "order_id": {"type": "string"},
                },
                "required": ["kind", "order_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["text", "claims"],
    "additionalProperties": False,
}


class OpenAIConversation:
    """Bounded Responses tool loop; no automatic retries or backend fallback."""

    backend_name = "openai"

    def __init__(self, version: str, model: str | None = None, client=None):
        self.version = version
        self.model = model or os.environ.get("AGENT_MODEL", "gpt-5.6-luna")
        if client is None:
            from openai import OpenAI

            client = OpenAI(timeout=30.0, max_retries=0)
        self._client = client
        self._system = (V2_SYSTEM_PROMPT if version == "v2" else V1_SYSTEM_PROMPT) + (
            " Return final text and structured claims. List only actions you claim completed, "
            "with their order IDs; denied or pending actions are not completion claims."
        )
        self._messages: list = []
        self.last_usage = None
        self.response_model = None

    def start(self, user_message: str) -> Action:
        self._messages.append({"role": "user", "content": user_message})
        return self._step()

    def on_tool_results(self, results: list[ToolResult]) -> Action:
        self._messages.extend(
            {"type": "function_call_output", "call_id": r.tool_use_id, "output": r.content}
            for r in results
        )
        return self._step()

    def _step(self) -> Action:
        from .tools import TOOLS

        response = self._client.responses.create(
            model=self.model,
            instructions=self._system,
            input=self._messages,
            tools=[
                {
                    "type": "function",
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                    "strict": True,
                }
                for t in TOOLS
            ],
            parallel_tool_calls=False,
            reasoning={"effort": "low"},
            max_output_tokens=2048,
            store=False,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "support_answer",
                    "schema": FINAL_SCHEMA,
                    "strict": True,
                }
            },
        )
        self.last_usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
        self.response_model = response.model
        if response.status != "completed":
            raise ValueError("incomplete model response")
        # Preserve reasoning items as well as calls for Responses continuation.
        self._messages.extend(response.output)
        calls = [
            ToolUse(b.call_id, b.name, json.loads(b.arguments))
            for b in response.output
            if b.type == "function_call"
        ]
        if calls:
            if len({c.id for c in calls}) != len(calls):
                raise ValueError("duplicate tool call IDs")
            return calls
        payload = json.loads(response.output_text)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"text", "claims"}
            or not isinstance(payload["text"], str)
            or not payload["text"].strip()
            or not isinstance(payload["claims"], list)
        ):
            raise ValueError("invalid final response")
        for claim in payload["claims"]:
            if (
                not isinstance(claim, dict)
                or set(claim) != {"kind", "order_id"}
                or claim["kind"] not in {"refund", "reschedule"}
                or not isinstance(claim["order_id"], str)
                or not claim["order_id"]
            ):
                raise ValueError("invalid completion claim")
        return Final(**payload)


def new_conversation(version: str | None = None, backend: str | None = None):
    version = version or os.environ.get("AGENT_VERSION", "v2")
    backend = backend or os.environ.get("AGENT_BACKEND", "scripted")
    if version not in {"v1", "v2"}:
        raise ValueError("agent version must be v1 or v2")
    if backend == "scripted":
        return ScriptedConversation(version=version)
    if backend == "openai":
        return OpenAIConversation(version)
    raise ValueError("backend must be scripted or openai")
