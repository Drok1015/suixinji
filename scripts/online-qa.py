"""Verify the deployed app, using a tracked disposable conversation and cleaning only its data."""

import asyncio
import json
import httpx

BASE = "https://example.com/self-agent/api"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=180, headers={"X-Self-Agent": "1"}) as client:
        assert (await client.get("/memory")).status_code == 200
        conversation = (await client.post("/conversations")).json()
        cid = conversation["id"]
        try:
            async with client.stream(
                "POST",
                f"/conversations/{cid}/chat",
                json={"content": "请计算 17 + 25，只回复结果，不用保存任何记忆。"},
            ) as response:
                assert response.status_code == 200
                reply, events = "", []
                kind = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        kind = line[7:]
                        events.append(kind)
                    elif line.startswith("data: "):
                        data = json.loads(line[6:])
                        if kind == "route":
                            assert not data.get("notice", "").startswith("分类服务暂时不可用"), (
                                "Jev unavailable on server"
                            )
                            print("ONLINE_JEV_ROUTE", data["status"])
                        if kind == "delta":
                            reply += data["text"]
                        if kind == "error":
                            raise RuntimeError(data["message"])
            assert "42" in reply and "route" in events and "done" in events
            print("ONLINE_CHAT_OK", reply)
            # Cancel during classification/model execution; the conversation must be usable afterward.
            async with client.stream(
                "POST",
                f"/conversations/{cid}/chat",
                json={"content": "请写一篇很长的童话故事，不需要保存记忆。"},
            ) as response:
                async for line in response.aiter_lines():
                    if line == "event: start":
                        stopped = await client.post(f"/conversations/{cid}/stop")
                        assert stopped.status_code == 200
                    if line == "event: stopped":
                        break
            await asyncio.sleep(0.5)
            history = (await client.get(f"/conversations/{cid}/messages")).json()
            assert not history["active"] and history["messages"][-1]["status"] == "interrupted"
            print("ONLINE_STOP_OK")
            await asyncio.sleep(3)
            memory = (await client.get("/memory")).json()
            ids = {m["id"] for m in history["messages"]}
            assert not any(m["source_message_id"] in ids for m in memory["memories"])
            print("ONLINE_AUTH_AND_NO_FALSE_MEMORY_OK")
        finally:
            await client.post(f"/conversations/{cid}/stop")
            await asyncio.sleep(0.5)
            cleaned = await client.delete(f"/conversations/{cid}")
            assert cleaned.status_code == 200
            print("ONLINE_QA_CLEANED", cid)


asyncio.run(main())
