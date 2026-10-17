import asyncio
import json
from typing import Literal
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import settings, ROOT, Settings
from .db import Database, uid, now
from .routing import JevRouter
from .llm import Intelligence
from .service import ChatService
from .releases import harmony_release


class Login(BaseModel):
    password: str = Field(default="", max_length=300)
    client: Literal["web", "mobile"] = "web"


class Title(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class TopicInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    description: str = Field(default="", max_length=300)


class MemoryInput(BaseModel):
    scope: str = Field(pattern="^(global|topic)$")
    topic_id: str | None = None
    key: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=2000)


class ChatInput(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    retry_message_id: str | None = None


class AssignInput(BaseModel):
    topic_id: str


class MergeInput(BaseModel):
    target_id: str


def create_app(config: Settings = settings):
    db = Database(config.database_path)
    service = ChatService(db, JevRouter(config), Intelligence(config))

    @asynccontextmanager
    async def lifespan(app):
        if not config.deepseek_api_key:
            raise RuntimeError("请先通过 scripts/configure.py 配置模型凭据")
        db.recover()
        worker = asyncio.create_task(service.worker())
        yield
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker

    app = FastAPI(title="随心记", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db, app.state.service = db, service
    cookie_path = (config.app_base_path.rstrip("/") or "") + "/"

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.url.path.startswith("/api/"):
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and request.headers.get("x-self-agent") != "1"
            ):
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "请求校验失败"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def owner():
        return "owner"

    def conversation(cid):
        row = db.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not row:
            raise HTTPException(404, "会话不存在")
        return row

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/login")
    def login(body: Login):
        # Compatibility for installed mobile clients; no password or token is required.
        return {"ok": True, "token": "public", "expires_in": 0}

    @app.post("/api/logout")
    def logout():
        from fastapi.responses import JSONResponse

        response = JSONResponse({"ok": True})
        response.delete_cookie("self_agent_session", path=cookie_path)
        return response

    @app.get("/api/me", dependencies=[Depends(owner)])
    def me():
        return {"name": "我", "model": config.chat_model}

    @app.get("/api/releases/harmony")
    def latest_harmony(current: int = 0):
        return harmony_release(config.harmony_release_path, current)

    @app.get("/api/conversations", dependencies=[Depends(owner)])
    def conversations():
        return db.all("SELECT * FROM conversations ORDER BY updated_at DESC")

    @app.post("/api/conversations", dependencies=[Depends(owner)])
    def new_conversation():
        cid, stamp = uid(), now()
        db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "新的对话", stamp, stamp))
        return conversation(cid)

    @app.patch("/api/conversations/{cid}", dependencies=[Depends(owner)])
    def rename(cid: str, body: Title):
        conversation(cid)
        db.execute("UPDATE conversations SET title=? WHERE id=?", (body.title.strip(), cid))
        return {"ok": True}

    @app.delete("/api/conversations/{cid}", dependencies=[Depends(owner)])
    async def delete_conversation(cid: str):
        conversation(cid)
        if cid in service.active:
            raise HTTPException(409, "请先停止回复")
        async with service.memory_lock:
            db.execute("DELETE FROM conversations WHERE id=?", (cid,))
        return {"ok": True}

    @app.get("/api/conversations/{cid}/messages", dependencies=[Depends(owner)])
    def messages(cid: str):
        conversation(cid)
        rows = db.all("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (cid,))
        for m in rows:
            m["route"] = json.loads(m["route"])
        return {"messages": rows, "active": cid in service.active}

    @app.post("/api/conversations/{cid}/stop", dependencies=[Depends(owner)])
    def stop(cid: str):
        conversation(cid)
        if cid in service.active:
            service.active[cid].set()
        return {"ok": True}

    @app.post("/api/conversations/{cid}/chat", dependencies=[Depends(owner)])
    async def chat(cid: str, body: ChatInput):
        convo = conversation(cid)
        if cid in service.active:
            raise HTTPException(409, "当前会话正在回复")
        if not body.content.strip():
            raise HTTPException(422, "请输入消息")
        if body.retry_message_id:
            last = db.one(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1", (cid,)
            )
            previous = db.one(
                "SELECT * FROM messages WHERE id=? AND conversation_id=?", (body.retry_message_id, cid)
            )
            if (
                not last
                or last["role"] != "assistant"
                or last["status"] not in ("error", "interrupted")
                or not previous
            ):
                raise HTTPException(409, "仅能重试最后一条失败或已停止的回复")
            rows = db.all(
                "SELECT id FROM messages WHERE conversation_id=? ORDER BY created_at DESC LIMIT 2", (cid,)
            )
            if len(rows) != 2 or rows[1]["id"] != previous["id"] or previous["role"] != "user":
                raise HTTPException(409, "重试消息无效")
            db.execute("DELETE FROM messages WHERE id=?", (last["id"],))
            user_id = previous["id"]
        else:
            user_id = uid()
            db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (user_id, cid, "user", body.content.strip(), "complete", "{}", now()),
            )
        assistant_id = uid()
        db.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
            (assistant_id, cid, "assistant", "", "streaming", "{}", now()),
        )
        db.execute(
            "UPDATE conversations SET title=?,updated_at=? WHERE id=?",
            (body.content.strip()[:28] if convo["title"] == "新的对话" else convo["title"], now(), cid),
        )
        cancel = asyncio.Event()
        service.active[cid] = cancel

        def event(kind, data):
            return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        async def cancellable(awaitable):
            task = asyncio.ensure_future(awaitable)
            cancelled = asyncio.create_task(cancel.wait())
            try:
                done, _ = await asyncio.wait([task, cancelled], return_when=asyncio.FIRST_COMPLETED)
                if cancelled in done:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                    raise asyncio.CancelledError()
                return task.result()
            finally:
                cancelled.cancel()
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await cancelled

        async def events():
            content, status = "", "error"
            try:
                yield event("start", {"user_id": user_id, "assistant_id": assistant_id})
                message = db.one("SELECT * FROM messages WHERE id=?", (user_id,))
                history = service.history(cid, before=user_id)
                route = await cancellable(service.route(message, history))
                db.set_route(user_id, route.dict())
                read_ids = route.read_topic_ids if route.read_topic_ids is not None else route.topic_ids
                retrieved = service.read_memories(read_ids, message["content"])
                topic_names = [t["name"] for t in db.all("SELECT * FROM topics") if t["id"] in read_ids]
                reply_route = {
                    **route.dict(),
                    "topic_names": topic_names,
                    "memory_count": len(retrieved),
                    "memory_ids": [m["id"] for m in retrieved],
                }
                db.set_route(assistant_id, reply_route)
                yield event("route", reply_route)
                iterator = service.ai.stream(
                    history + [{"role": "user", "content": message["content"]}],
                    [{"scope": m["scope"], "key": m["key"], "content": m["content"]} for m in retrieved],
                ).__aiter__()
                try:
                    while True:
                        try:
                            chunk = await cancellable(anext(iterator))
                        except StopAsyncIteration:
                            break
                        content += chunk
                        db.execute("UPDATE messages SET content=? WHERE id=?", (content, assistant_id))
                        yield event("delta", {"text": chunk})
                finally:
                    await iterator.aclose()
                if not content:
                    raise RuntimeError("empty_reply")
                status = "complete"
                service.queue_completed(user_id, cid)
                yield event("done", {"assistant_id": assistant_id})
            except asyncio.CancelledError:
                status = "interrupted"
                # Cancellation of the HTTP task means the client has gone away.
                if not asyncio.current_task().cancelling():
                    yield event("stopped", {"assistant_id": assistant_id})
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning("Chat failed: %s", type(e).__name__)
                yield event("error", {"message": "回复失败，请重试。已保留你的消息和已生成的内容。"})
            finally:
                db.execute(
                    "UPDATE messages SET content=?,status=? WHERE id=?", (content, status, assistant_id)
                )
                service.active.pop(cid, None)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
        )

    @app.get("/api/memory", dependencies=[Depends(owner)])
    def memory():
        return {
            "topics": db.all(
                "SELECT t.*, (SELECT COUNT(*) FROM memories m WHERE m.topic_id=t.id) AS memory_count "
                "FROM topics t ORDER BY created_at"
            ),
            "memories": db.all(
                "SELECT m.*,s.content AS source_content FROM memories m "
                "LEFT JOIN messages s ON s.id=m.source_message_id ORDER BY m.updated_at DESC"
            ),
            "pending": db.all(
                "SELECT m.*,j.status AS job_status,j.error FROM messages m "
                "LEFT JOIN jobs j ON j.message_id=m.id WHERE m.role='user' AND "
                "(json_extract(m.route,'$.status')='pending' OR j.status='failed') "
                "ORDER BY m.created_at DESC LIMIT 100"
            ),
            "jobs": db.all("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status"),
        }

    @app.post("/api/topics", dependencies=[Depends(owner)])
    async def create_topic(body: TopicInput):
        async with service.memory_lock:
            return db.topic(body.name.strip(), body.description.strip())

    @app.patch("/api/topics/{tid}", dependencies=[Depends(owner)])
    async def edit_topic(tid: str, body: TopicInput):
        async with service.memory_lock:
            if not db.one("SELECT id FROM topics WHERE id=?", (tid,)):
                raise HTTPException(404, "分类不存在")
            if db.one("SELECT id FROM topics WHERE name=? AND id!=?", (body.name.strip(), tid)):
                raise HTTPException(409, "已有同名分类，请合并")
            db.execute(
                "UPDATE topics SET name=?,description=? WHERE id=?",
                (body.name.strip(), body.description.strip(), tid),
            )
        return {"ok": True}

    @app.post("/api/topics/{tid}/merge", dependencies=[Depends(owner)])
    async def merge_topic(tid: str, body: MergeInput):
        async with service.memory_lock:
            if tid == body.target_id or not db.one("SELECT id FROM topics WHERE id=?", (body.target_id,)):
                raise HTTPException(422, "目标分类无效")
            if not db.one("SELECT id FROM topics WHERE id=?", (tid,)):
                raise HTTPException(404, "分类不存在")
            with db.connect() as c:
                for memory in c.execute("SELECT * FROM memories WHERE topic_id=?", (tid,)).fetchall():
                    key = memory["key"]
                    if c.execute(
                        "SELECT id FROM memories WHERE topic_id=? AND key=?", (body.target_id, key)
                    ).fetchone():
                        key += ":" + memory["id"][:8]
                    c.execute(
                        "UPDATE memories SET topic_id=?,key=?,updated_at=? WHERE id=?",
                        (body.target_id, key, now(), memory["id"]),
                    )
                for message in c.execute("SELECT id,route FROM messages WHERE route!='{}'").fetchall():
                    route = json.loads(message["route"])
                    for field in ("topic_ids", "read_topic_ids"):
                        if route.get(field) is not None:
                            route[field] = list(
                                dict.fromkeys(body.target_id if t == tid else t for t in route[field])
                            )
                    route.pop("topic_names", None)
                    c.execute("UPDATE messages SET route=? WHERE id=?", (json.dumps(route), message["id"]))
                c.execute("DELETE FROM topics WHERE id=?", (tid,))
        return {"ok": True}

    @app.delete("/api/topics/{tid}", dependencies=[Depends(owner)])
    async def delete_topic(tid: str):
        async with service.memory_lock:
            with db.connect() as c:
                c.execute("DELETE FROM memories WHERE topic_id=?", (tid,))
                c.execute("DELETE FROM topics WHERE id=?", (tid,))
                for message in c.execute("SELECT id,route FROM messages WHERE route!='{}'").fetchall():
                    route = json.loads(message["route"])
                    changed = False
                    for field in ("topic_ids", "read_topic_ids"):
                        if tid in (route.get(field) or []):
                            route[field].remove(tid)
                            changed = True
                    if changed:
                        route.pop("topic_names", None)
                        if not route.get("topic_ids") and route.get("status") != "chat":
                            route["status"] = "pending"
                        c.execute(
                            "UPDATE messages SET route=? WHERE id=?", (json.dumps(route), message["id"])
                        )
        return {"ok": True}

    @app.post("/api/memories", dependencies=[Depends(owner)])
    async def create_memory(body: MemoryInput):
        async with service.memory_lock:
            if body.scope == "topic" and not db.one("SELECT id FROM topics WHERE id=?", (body.topic_id,)):
                raise HTTPException(422, "请选择有效分类")
            tid = body.topic_id if body.scope == "topic" else None
            if db.one(
                "SELECT id FROM memories WHERE scope=? AND topic_id IS ? AND key=?",
                (body.scope, tid, body.key),
            ):
                raise HTTPException(409, "已存在同名记忆，请编辑已有记忆")
            db.save_memory(body.scope, body.topic_id, body.key, body.content, None)
        return {"ok": True}

    @app.patch("/api/memories/{mid}", dependencies=[Depends(owner)])
    async def edit_memory(mid: str, body: MemoryInput):
        async with service.memory_lock:
            old = db.one("SELECT * FROM memories WHERE id=?", (mid,))
            if not old:
                raise HTTPException(404, "记忆不存在")
            if body.scope == "topic" and not db.one("SELECT id FROM topics WHERE id=?", (body.topic_id,)):
                raise HTTPException(422, "请选择有效分类")
            tid = body.topic_id if body.scope == "topic" else None
            if db.one(
                "SELECT id FROM memories WHERE scope=? AND topic_id IS ? AND key=? AND id!=?",
                (body.scope, tid, body.key, mid),
            ):
                raise HTTPException(409, "已存在相同记忆键，请更换名称")
            with db.connect() as c:
                c.execute(
                    "UPDATE memories SET scope=?,topic_id=?,key=?,content=?,source_message_id=NULL,updated_at=? WHERE id=?",
                    (body.scope, tid, body.key, body.content, now(), mid),
                )
                c.execute(
                    "INSERT INTO memory_events VALUES(?,?,?,?,?,?,?)",
                    (uid(), "manual_edit", mid, json.dumps(old), body.content, None, now()),
                )
        return {"ok": True}

    @app.delete("/api/memories/{mid}", dependencies=[Depends(owner)])
    async def delete_memory(mid: str):
        async with service.memory_lock:
            with db.connect() as c:
                old = c.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
                if not old:
                    raise HTTPException(404, "记忆不存在")
                c.execute(
                    "INSERT INTO memory_events VALUES(?,?,?,?,?,?,?)",
                    (uid(), "delete", mid, json.dumps(dict(old)), None, None, now()),
                )
                c.execute("DELETE FROM memories WHERE id=?", (mid,))
        return {"ok": True}

    @app.post("/api/pending/{mid}/assign", dependencies=[Depends(owner)])
    async def assign(mid: str, body: AssignInput):
        async with service.memory_lock:
            message = db.one("SELECT * FROM messages WHERE id=? AND role='user'", (mid,))
            if not message:
                raise HTTPException(404, "消息不存在")
            if not db.one("SELECT id FROM topics WHERE id=?", (body.topic_id,)):
                raise HTTPException(422, "分类不存在")
            db.assign_message(message, body.topic_id)
        return {"ok": True}

    @app.post("/api/memory/retry", dependencies=[Depends(owner)])
    def retry_memory():
        db.execute("UPDATE jobs SET status='queued',attempts=0 WHERE status IN ('failed','pending')")
        service.changed.set()
        return {"ok": True}

    dist = ROOT / "frontend/dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app
