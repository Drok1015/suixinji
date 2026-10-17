"""Run with the server service user; all fixture data lives in a temporary database."""

import asyncio
import json
import tempfile
from self_agent.config import Settings
from self_agent.db import Database, uid, now
from self_agent.routing import JevRouter
from self_agent.llm import Intelligence
from self_agent.service import ChatService


async def main():
    with tempfile.TemporaryDirectory(dir="/opt/self-agent/data", prefix=".qa-") as temporary:
        settings = Settings(_env_file="/opt/self-agent/.env", database_path=temporary + "/qa.sqlite3")
        db = Database(settings.database_path)
        service = ChatService(db, JevRouter(settings), Intelligence(settings))

        def conversation():
            cid = uid()
            db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "QA", now(), now()))
            return cid

        async def message(cid, text):
            mid = uid()
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (mid, cid, "user", text, "complete", "{}", now()),
            )
            row = db.one("SELECT * FROM messages WHERE id=?", (mid,))
            route = await service.route(row, service.history(cid, before=mid))
            db.set_route(mid, route.dict())
            db.enqueue(mid)
            job = db.one("SELECT * FROM jobs WHERE message_id=?", (mid,))
            job["conversation_id"] = cid
            await service.consolidate(job)
            return mid, route

        cid = conversation()
        _, route = await message(cid, "我计划学习葡萄牙语，每周四次。请记住我的学习安排。")
        assert route.status == "matched", route.dict()
        _, route = await message(cid, "我喜欢简洁的中文回复，请记住这个偏好。")
        facts = db.all("SELECT * FROM memories")
        assert any(m["scope"] == "global" and "中文" in m["content"] for m in facts), facts
        assert any(m["scope"] == "topic" and ("四" in m["content"] or "4" in m["content"]) for m in facts), (
            facts
        )
        newdb = Database(settings.database_path)
        assert len(newdb.all("SELECT * FROM memories")) >= 2
        question = "你记得我计划学哪种语言，每周几次吗？"
        route = await service.router.classify(question, [], db.all("SELECT * FROM topics"))
        retrieved = service.read_memories(route.topic_ids, question)
        answer = ""
        async for chunk in service.ai.stream([{"role": "user", "content": question}], retrieved):
            answer += chunk
        assert "葡萄牙" in answer and ("四" in answer or "4" in answer), answer
        print("SERVER_MEMORY_PERSISTENCE_AND_RECALL_OK", answer)
        deferred = conversation()
        mid, route = await message(deferred, "我计划每周学两次，请记住这个安排。")
        assert route.status == "pending", route.dict()
        await message(deferred, "我前面说的学习安排是德语，我准备自学德语。")
        job = db.one("SELECT * FROM jobs WHERE message_id=?", (mid,))
        job["conversation_id"] = deferred
        await service.consolidate(job)
        updated = json.loads(db.one("SELECT route FROM messages WHERE id=?", (mid,))["route"])
        assert updated["status"] == "matched", updated
        facts = db.all("SELECT * FROM memories WHERE source_message_id=?", (mid,))
        assert any("两" in m["content"] or "2" in m["content"] for m in facts), facts
        print("SERVER_DEFERRED_CLASSIFICATION_OK", [m["content"] for m in facts])
    print("SERVER_QA_DATABASE_REMOVED")


asyncio.run(main())
