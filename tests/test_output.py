import json

import httpx
import pytest
from langchain_openai import ChatOpenAI
from self_agent.config import Settings
from self_agent.llm import Intelligence
from self_agent.output import TextGuard, UnexpectedToolOutput
from self_agent.db import Database, uid, now
from self_agent.service import ChatService


@pytest.mark.parametrize(
    "marker", ["<｜｜DSML｜｜ calls>", "<|DSML|tool_calls>", "</｜DSML｜invoke>", "< | | DSML | | calls>"]
)
def test_protocol_split_across_chunks_never_leaks(marker):
    guard = TextGuard()
    emitted = ""
    with pytest.raises(UnexpectedToolOutput):
        for char in marker:
            emitted += guard.feed(char)
        emitted += guard.finish()
    assert emitted == ""


def test_normal_markup_comparisons_and_streaming_are_preserved():
    guard = TextGuard()
    chunks = ["你好，", "a <", " b；", "<b>中文", "</b>", "。"]
    assert "".join(guard.feed(c) for c in chunks) + guard.finish() == "".join(chunks)


async def provider_stream(chunks, question):
    captured = []

    async def handler(request):
        captured.append(json.loads(request.content))
        body = ""
        for text in chunks:
            item = {
                "id": "test",
                "object": "chat.completion.chunk",
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "delta": text if isinstance(text, dict) else {"content": text},
                        "finish_reason": None,
                    }
                ],
            }
            body += "data: " + json.dumps(item) + "\n\n"
        body += "data: [DONE]\n\n"
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ai = Intelligence(Settings(_env_file=None, deepseek_api_key="test"))
        ai.model = ChatOpenAI(
            api_key="test",
            model="deepseek-v4-flash",
            base_url="https://mock.test",
            http_async_client=client,
            max_retries=0,
        )
        output = []
        try:
            async for text in ai.stream([{"role": "user", "content": question}], []):
                output.append(text)
        except UnexpectedToolOutput:
            return captured, "".join(output), True
        return captured, "".join(output), False


async def test_actual_deepagents_request_has_no_filesystem_prompts_or_tools():
    captured, text, failed = await provider_stream(["好的，", "我会简洁地用中文回复。"], "请记住我的偏好。")
    assert not failed and text == "好的，我会简洁地用中文回复。"
    assert len(captured) == 1
    payload = captured[0]
    assert payload["tool_choice"] == "none" and not payload.get("tools")
    system = "\n".join(m["content"] for m in payload["messages"] if m["role"] == "system")
    assert system.startswith("你是随心记")
    assert not any(word in system for word in ["write_file", "read_file", "write_todos", "general-purpose"])


async def test_provider_protocol_is_blocked_before_any_sse_text_is_returned():
    captured, text, failed = await provider_stream(["<", "｜｜DSM", "L｜｜ calls>", "\n"], "请记住我的偏好。")
    assert captured and failed and text == ""


def test_legacy_protocol_reply_does_not_contaminate_history(tmp_path):
    db = Database(str(tmp_path / "history.db"))
    cid = uid()
    db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "test", now(), now()))
    for role, text in [
        ("user", "请记住我的偏好"),
        ("assistant", "<｜｜DSML｜｜ calls>bad</｜｜DSML｜｜ calls>"),
        ("user", "继续聊"),
        ("assistant", "你好"),
    ]:
        db.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?)", (uid(), cid, role, text, "complete", "{}", now())
        )
    history = ChatService(db, None, None).history(cid)
    assert len(history) == 3 and all("DSML" not in m["content"] for m in history)


async def test_structured_tool_calls_are_not_accepted_in_chat():
    captured, text, failed = await provider_stream(
        [
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-test",
                        "type": "function",
                        "function": {"name": "ls", "arguments": "{}"},
                    }
                ]
            },
        ],
        "请记住我的偏好。",
    )
    assert captured and failed and text == ""
