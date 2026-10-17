"""Regenerate only corrupted assistant replies, retaining private backups and original user messages."""

import asyncio
import json
import os
from pathlib import Path
from self_agent.config import Settings
from self_agent.db import Database
from self_agent.llm import Intelligence
from self_agent.output import contains_tool_protocol
from self_agent.routing import JevRouter
from self_agent.service import ChatService


async def main():
    config = Settings(_env_file="/opt/self-agent/.env")
    db = Database(config.database_path)
    service = ChatService(db, JevRouter(config), Intelligence(config))
    repaired = 0
    for reply in db.all("SELECT * FROM messages WHERE role='assistant' AND content LIKE '%DSML%'"):
        if not contains_tool_protocol(reply["content"]):
            continue
        if db.one(
            "SELECT id FROM messages WHERE conversation_id=? AND status='streaming'",
            (reply["conversation_id"],),
        ):
            print("SKIPPED_ACTIVE_CONVERSATION")
            continue
        history = service.history(reply["conversation_id"], before=reply["id"])
        if not history or history[-1]["role"] != "user":
            print("SKIPPED_MISSING_USER_MESSAGE")
            continue
        backup_dir = Path("/opt/self-agent/data/repairs")
        backup_dir.mkdir(mode=0o700, exist_ok=True)
        backup_file = backup_dir / (reply["id"] + ".json")
        if not backup_file.exists():
            fd = os.open(backup_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(reply, f, ensure_ascii=False)
        route = json.loads(reply["route"])
        memories = service.read_memories(route.get("topic_ids", []), history[-1]["content"])
        text = ""
        async for part in service.ai.stream(
            history, [{"scope": m["scope"], "key": m["key"], "content": m["content"]} for m in memories]
        ):
            text += part
        if not text.strip() or contains_tool_protocol(text):
            raise RuntimeError("repair_did_not_produce_normal_text")
        with db.connect() as c:
            changed = c.execute(
                "UPDATE messages SET content=?,status='complete' WHERE id=? AND content=?",
                (text, reply["id"], reply["content"]),
            ).rowcount
        repaired += changed
        if changed:
            print("REPAIRED_REPLY", text)
    print("REPAIRED_COUNT", repaired)


asyncio.run(main())
