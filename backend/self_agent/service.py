import asyncio
import json
import logging
import re
from .db import Database, now
from .routing import JevRouter
from .llm import Intelligence
from .output import contains_tool_protocol

logger = logging.getLogger(__name__)


def memory_score(memory, text):
    query = re.sub(r"\s+", "", text.lower())
    terms = {query[i : i + 2] for i in range(max(0, len(query) - 1))}
    content = (memory["key"] + " " + memory["content"]).lower()
    return sum(term in content for term in terms)


class ChatService:
    def __init__(self, db: Database, router: JevRouter, intelligence: Intelligence):
        self.db, self.router, self.ai = db, router, intelligence
        self.active: dict[str, asyncio.Event] = {}
        self.changed = asyncio.Event()
        self.memory_lock = asyncio.Lock()

    def history(self, conversation_id, before=None):
        rows = self.db.all(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (conversation_id,)
        )
        if before:
            rows = rows[: next((i for i, r in enumerate(rows) if r["id"] == before), len(rows))]
        # Failed/empty replies must not become assistant knowledge.
        return [
            {"role": r["role"], "content": r["content"][:16000]}
            for r in rows
            if r["content"]
            and (
                r["role"] == "user"
                or (r["status"] in ("complete", "interrupted") and not contains_tool_protocol(r["content"]))
            )
        ][-50:]

    def read_memories(self, topic_ids, text=""):
        globals_ = self.db.all(
            "SELECT * FROM memories WHERE scope='global' ORDER BY updated_at DESC LIMIT 60"
        )
        topical = []
        if topic_ids:
            marks = ",".join("?" for _ in topic_ids)
            topical = self.db.all(
                f"SELECT * FROM memories WHERE topic_id IN ({marks}) ORDER BY updated_at DESC", topic_ids
            )
            topical.sort(key=lambda m: memory_score(m, text), reverse=True)
        selected, size = [], 0
        for m in globals_ + topical[:20]:
            if size + len(m["content"]) > 16000:
                break
            selected.append(m)
            size += len(m["content"])
        return selected

    async def route(self, message: dict, context: list):
        topics = self.db.all("SELECT * FROM topics ORDER BY created_at")
        # Give Jev actual searchable memory clues, not just a generic category label.
        memories = self.db.all(
            "SELECT topic_id,key,content,updated_at FROM memories WHERE scope='topic' "
            "ORDER BY updated_at DESC"
        )
        for topic in topics:
            candidates = [m for m in memories if m["topic_id"] == topic["id"]]
            candidates.sort(key=lambda m: memory_score(m, message["content"]), reverse=True)
            topic["memory_candidates"] = [
                {"key": m["key"], "content": m["content"][:400]} for m in candidates[:5]
            ]
        route = await self.router.classify(message["content"], context, topics)
        # Exact short keyword queries can still read memory when semantic routing
        # fails or misses a category. This never assigns or saves new facts.
        query = re.sub(r"[\s，。！？、,.!?：:]+", "", message["content"]).lower()
        if not route.read_topic_ids and 2 <= len(query) <= 20:
            keyword_ids = [
                topic["id"]
                for topic in topics
                if query in topic["name"].lower()
                or any(
                    query in memory["key"].lower()
                    for memory in memories
                    if memory["topic_id"] == topic["id"]
                )
            ][:3]
            if keyword_ids:
                route.read_topic_ids = keyword_ids
                if route.notice:
                    route.notice = "分类服务暂时不可用，已按关键词检索已有记忆；新内容稍后再归类。"
        if route.status == "pending" and not route.remember:
            # Uncertain or unavailable Jev decisions go to the main model, never
            # discard potential information just because the cheap router failed.
            try:
                remember = await self.ai.memory_intent(message["content"], context)
            except Exception:
                remember = None
            if remember is False:
                route.status = "chat"
                route.topic_ids = []
                route.notice = ""
            elif remember is True:
                route.remember = True
        if route.status == "new" and route.remember:
            try:
                name, description = await self.ai.name_topic(message["content"], context, topics)
                topic = self.db.topic(name, description)
                route.topic_ids = [topic["id"]]
                route.read_topic_ids = list(dict.fromkeys((route.read_topic_ids or []) + [topic["id"]]))[:3]
                route.status = "matched"
                route.notice = "已自动创建话题：" + name
            except Exception:
                route.status = "pending"
                route.notice = "新话题暂未命名，已保留到待归类。"
        return route

    def queue_completed(self, user_id, conversation_id):
        route = json.loads(self.db.one("SELECT route FROM messages WHERE id=?", (user_id,))["route"])
        if route.get("status") != "chat":
            self.db.enqueue(user_id)
        # Only revisit unresolved items when new context arrives, preventing endless API polling.
        self.db.execute(
            "UPDATE jobs SET status='queued',attempts=0 WHERE message_id IN "
            "(SELECT id FROM messages WHERE conversation_id=? AND role='user') "
            "AND status IN ('pending','failed')",
            (conversation_id,),
        )
        self.changed.set()

    async def worker(self):
        while True:
            job = self.db.one(
                "SELECT j.*,m.conversation_id,m.content,m.route,m.created_at FROM jobs j "
                "JOIN messages m ON m.id=j.message_id WHERE j.status='queued' "
                "ORDER BY m.created_at LIMIT 1"
            )
            if not job:
                self.changed.clear()
                try:
                    await asyncio.wait_for(self.changed.wait(), timeout=10)
                except asyncio.TimeoutError:
                    pass
                continue
            self.db.execute(
                "UPDATE jobs SET status='running',attempts=attempts+1,updated_at=? WHERE message_id=?",
                (now(), job["message_id"]),
            )
            try:
                async with self.memory_lock:
                    await self.consolidate(job)
            except asyncio.CancelledError:
                self.db.execute("UPDATE jobs SET status='queued' WHERE message_id=?", (job["message_id"],))
                raise
            except Exception as e:
                logger.warning("Memory job failed: %s", type(e).__name__)
                self.db.execute(
                    "UPDATE jobs SET status='failed',error=?,updated_at=? WHERE message_id=?",
                    ("记忆整理失败，可重试", now(), job["message_id"]),
                )

    async def consolidate(self, job):
        message = self.db.one("SELECT * FROM messages WHERE id=?", (job["message_id"],))
        if not message:
            return
        # Use newer context to resolve ambiguous earlier messages; extraction still only uses their own facts.
        context = self.history(job["conversation_id"])[-12:]
        route = json.loads(message["route"])
        if route.get("manual_assignment"):
            # A worker may already have selected this job before manual assignment.
            self.db.execute(
                "UPDATE jobs SET status='done',error=NULL,updated_at=? WHERE message_id=?",
                (now(), message["id"]),
            )
            return
        if route.get("status") == "pending" or not route:
            resolved_message = message
            subsequent = self.db.all(
                "SELECT content FROM messages WHERE conversation_id=? AND role='user' AND created_at>? "
                "ORDER BY created_at LIMIT 12",
                (message["conversation_id"], message["created_at"]),
            )
            if subsequent:
                resolution = await self.ai.resolve_context(message["content"], subsequent)
                source = resolution.get("source_quote", "")
                clarification = resolution.get("clarification_quote", "")
                resolved_text = resolution.get("resolved_message", "")
                if (
                    resolution.get("resolved") is True
                    and isinstance(source, str)
                    and source.strip()
                    and source in message["content"]
                    and isinstance(clarification, str)
                    and clarification.strip()
                    and any(clarification in m["content"] for m in subsequent)
                    and isinstance(resolved_text, str)
                    and 0 < len(resolved_text) <= 12000
                ):
                    verified = await self.router.judge(
                        {
                            "original": message["content"],
                            "clarification": clarification,
                            "resolved_message": resolved_text,
                        },
                        {
                            "supported": {
                                "type": "noul",
                                "instructions": "补全后的消息是否只使用明确的后续澄清解析旧消息指代，保留旧消息的事实和频率，"
                                "没有把无关的新话题强行归到旧消息，也没有添加没有依据的信息？",
                                "criteria": {
                                    "true": "明确澄清且补全忠实",
                                    "false": "缺乏明确关联或添加新事实",
                                },
                            }
                        },
                    )
                    if float(verified.get("supported", {}).get("noul", 0)) >= 0.9:
                        resolved_message = {**message, "content": resolved_text}
            route = (await self.route(resolved_message, context)).dict()
            self.db.set_route(message["id"], route)
        if route.get("notice", "").startswith("分类服务暂时不可用"):
            raise RuntimeError("classification_failed")
        ids = route.get("topic_ids", [])
        allowed = [t for t in self.db.all("SELECT * FROM topics") if t["id"] in ids]
        if route.get("remember"):
            existing = self.read_memories(ids, message["content"])
            extracted = await self.ai.extract(message["content"], context, allowed, existing)
            if not isinstance(extracted.get("memories"), list):
                raise ValueError("invalid_memory_response")
            for fact in extracted["memories"][:12]:
                if not isinstance(fact, dict):
                    continue
                evidence = fact.get("evidence", "")
                if (
                    not isinstance(evidence, str)
                    or not evidence.strip()
                    or evidence not in message["content"]
                ):
                    continue
                scope, tid = fact.get("scope"), fact.get("topic_id")
                if scope not in ("global", "topic") or (scope == "topic" and tid not in ids):
                    continue
                key, content = fact.get("key", ""), fact.get("content", "")
                if (
                    not isinstance(key, str)
                    or not isinstance(content, str)
                    or not key.strip()
                    or not content.strip()
                ):
                    continue
                # Verify the model's proposed claim against user evidence before persistence.
                verdict = await self.router.judge(
                    {
                        "user_message": message["content"],
                        "claim": content,
                        "evidence": evidence,
                        "scope": scope,
                        "recent_user_context": [m for m in context if m["role"] == "user"],
                    },
                    {
                        "supported": {
                            "type": "noul",
                            "instructions": "该记忆是否由用户本人的明确陈述支持，且scope正确？global只用于本人信息和稳定偏好；"
                            "topic用于具体项目或话题事实。不要把假设、他人引用、询问或模型猜测记成用户事实。",
                            "criteria": {"true": "明确可靠且归属正确", "false": "不明确、错误归属或缺乏证据"},
                        }
                    },
                )
                if float(verdict.get("supported", {}).get("noul", 0)) >= 0.85:
                    self.db.save_memory(scope, tid, key.strip()[:100], content.strip()[:2000], message["id"])
        status = "pending" if route.get("status") == "pending" else "done"
        self.db.execute(
            "UPDATE jobs SET status=?,error=NULL,updated_at=? WHERE message_id=?",
            (status, now(), message["id"]),
        )
