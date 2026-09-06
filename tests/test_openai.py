"""Exercise the actual OpenAI adapter with a fake SDK response; no model calls."""

import json
from types import SimpleNamespace

import pytest

from trace2evals.backends import Final, OpenAIConversation, ToolResult, new_conversation


def response(output=None, text=None, status="completed"):
    return SimpleNamespace(
        status=status,
        model="gpt-5.6-luna",
        output=output or [],
        output_text=text,
        usage=SimpleNamespace(input_tokens=12, output_tokens=6),
    )


class Client:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []
        self.responses = self

    def create(self, **kwargs):
        self.requests.append(kwargs.copy())
        return next(self.replies)


def test_openai_preserves_reasoning_and_call_identity():
    reasoning = SimpleNamespace(type="reasoning")
    call = SimpleNamespace(
        type="function_call", call_id="c-1", name="lookup_order", arguments='{"order_id":"A-1001"}'
    )
    client = Client(
        [
            response([reasoning, call]),
            response(text=json.dumps({"text": "Order is delivered.", "claims": []})),
        ]
    )
    convo = OpenAIConversation("v2", client=client)
    actions = convo.start("Status?")
    final = convo.on_tool_results(
        [ToolResult(actions[0].id, actions[0].name, actions[0].arguments, "{}", False)]
    )
    assert final == Final("Order is delivered.")
    request = client.requests[-1]
    assert reasoning in request["input"]
    assert any(isinstance(item, dict) and item.get("call_id") == "c-1" for item in request["input"])
    assert request["store"] is False
    assert request["parallel_tool_calls"] is False
    for tool in request["tools"]:
        assert tool["strict"]
        assert tool["parameters"]["additionalProperties"] is False
        assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])


@pytest.mark.parametrize(
    "reply",
    [
        response(status="incomplete"),
        response(text="not json"),
        response(text='{"text":"ok","claims":[{"kind":"refund"}]}'),
        response(text='{"text":"","claims":[]}'),
    ],
)
def test_invalid_responses_do_not_become_answers(reply):
    with pytest.raises((ValueError, KeyError)):
        OpenAIConversation("v2", client=Client([reply])).start("test")


def test_key_does_not_select_live_backend(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("AGENT_BACKEND", raising=False)
    assert new_conversation().backend_name == "scripted"
    with pytest.raises(ValueError):
        new_conversation(version="typo")
