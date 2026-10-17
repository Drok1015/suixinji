import json
from fastapi.testclient import TestClient
from self_agent.config import Settings
from self_agent.auth import password_hash
from self_agent.app import create_app
from self_agent.db import Database, now, uid
from self_agent.routing import JevRouter, Route
from self_agent.service import ChatService


def config(tmp_path):
    return Settings(
        _env_file=None,
        database_path=str(tmp_path / "test.db"),
        session_secret="test-secret",
        login_password_hash=password_hash("test-password"),
        deepseek_api_key="test-key",
        secure_cookie=False,
        app_base_path="/",
    )


def login(client):
    return client.post("/api/login", json={"password": "test-password"}, headers={"X-Self-Agent": "1"})


def test_manual_assignment_persists_with_models_unavailable(tmp_path):
    app = create_app(config(tmp_path))
    db = app.state.db
    topic = db.topic("佑佑", "宝宝用品")
    cid, mid = uid(), uid()
    text = "双十一要买奶粉，婴儿车，安全座椅，消毒柜"
    db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "宝宝", now(), now()))
    db.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
        (mid, cid, "user", text, "complete", json.dumps({"status": "pending"}), now()),
    )
    db.enqueue(mid)
    db.execute("UPDATE jobs SET status='failed',error='记忆整理失败，可重试' WHERE message_id=?", (mid,))

    async def unavailable(*args, **kwargs):
        raise AssertionError("Manual assignment must not call a model")

    app.state.service.ai.extract = unavailable
    app.state.service.router.judge = unavailable
    with TestClient(app) as client:
        url = f"/api/pending/{mid}/assign"
        headers = {"X-Self-Agent": "1"}
        assert client.post(url, headers=headers, json={"topic_id": "missing"}).status_code == 422
        assert not db.all("SELECT * FROM memories")
        for _ in range(2):
            assert client.post(url, headers=headers, json={"topic_id": topic["id"]}).status_code == 200
        data = client.get("/api/memory").json()
        assert not data["pending"]
        assert data["topics"][0]["memory_count"] == 1
        assert len(data["memories"]) == 1
        assert data["memories"][0]["content"] == text
        assert data["memories"][0]["source_message_id"] == mid
        job = db.one("SELECT * FROM jobs WHERE message_id=?", (mid,))
        assert job["status"] == "done" and job["error"] is None
    assert Database(db.path).all("SELECT * FROM memories")[0]["content"] == text


async def test_manual_assignment_retains_long_text_and_stale_worker_skips_it(tmp_path):
    db = Database(str(tmp_path / "assign.db"))
    topic = db.topic("佑佑", "宝宝用品")
    cid, mid = uid(), uid()
    text = "宝宝用品清单。" * 600
    db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "宝宝", now(), now()))
    db.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?)", (mid, cid, "user", text, "complete", "{}", now())
    )
    message = db.one("SELECT * FROM messages WHERE id=?", (mid,))
    db.assign_message(message, topic["id"])
    facts = db.all("SELECT * FROM memories ORDER BY rowid")
    assert "".join(f["content"] for f in facts) == text
    assert all(len(f["content"]) <= 2000 for f in facts)
    # Deleting a saved record must not be undone by a stale automatic job.
    db.execute("DELETE FROM memories WHERE id=?", (facts[0]["id"],))
    service = ChatService(db, None, None)
    await service.consolidate({"message_id": mid, "conversation_id": cid})
    assert len(db.all("SELECT * FROM memories")) == len(facts) - 1


async def test_routing_matches_multiple_and_never_creates_on_uncertainty(tmp_path):
    router = JevRouter(config(tmp_path))
    topics = [
        {"id": "a", "name": "Python", "description": "学习Python"},
        {"id": "b", "name": "学习计划", "description": "学习安排"},
    ]

    async def judge(state, questions):
        return {
            name: {
                "noul": {"topic_a": 0.95, "topic_b": 0.89, "new_topic": 0.2, "remember": 0.96}.get(name, 0)
            }
            for name in questions
        }

    router.judge = judge
    route = await router.classify("我计划每周三次学习Python", [], topics)
    assert route.status == "matched" and route.topic_ids == ["a", "b"] and route.remember

    async def uncertain(state, questions):
        return {name: {"noul": 0.55} for name in questions}

    router.judge = uncertain
    assert (await router.classify("那个怎么办？", [], topics)).status == "pending"

    async def recall_only(state, questions):
        values = {"recall": 0.99, "remember": 0.1, "topic_a": 0.2, "recall_topic_a": 0.95}
        return {name: {"noul": values.get(name, 0)} for name in questions}

    router.judge = recall_only
    route = await router.classify("你记得我的学习计划吗？", [], topics)
    assert route.status == "chat" and not route.topic_ids and route.read_topic_ids == ["a"]
    assert not route.remember

    async def ungrounded_fact(state, questions):
        values = {"recall": 0.1, "remember": 0.99, "topic_a": 0.2, "recall_topic_a": 0.95}
        return {name: {"noul": values.get(name, 0)} for name in questions}

    router.judge = ungrounded_fact
    route = await router.classify("我打算每周两次", [], topics)
    assert route.status == "pending" and not route.topic_ids

    async def outage(state, questions):
        raise RuntimeError("failed")

    router.judge = outage
    route = await router.classify("我的学习计划", [], topics)
    assert route.status == "pending" and not route.topic_ids and route.notice


def test_auth_and_memory_management_with_source_retention(tmp_path):
    app = create_app(config(tmp_path))
    with TestClient(app) as c:
        assert c.get("/api/memory").status_code == 200
        assert c.post("/api/login", json={"password": "test-password"}).status_code == 403
        assert login(c).status_code == 200
        headers = {"X-Self-Agent": "1"}
        a = c.post("/api/topics", headers=headers, json={"name": "学习", "description": "语言学习"}).json()
        b = c.post("/api/topics", headers=headers, json={"name": "西语", "description": "西语学习"}).json()
        for topic in [a, b]:
            assert (
                c.post(
                    "/api/memories",
                    headers=headers,
                    json={
                        "scope": "topic",
                        "topic_id": topic["id"],
                        "key": "学习频率",
                        "content": "每周三次",
                    },
                ).status_code
                == 200
            )
        assert (
            c.post(f"/api/topics/{a['id']}/merge", headers=headers, json={"target_id": b["id"]}).status_code
            == 200
        )
        memory = c.get("/api/memory").json()
        assert len(memory["topics"]) == 1 and len(memory["memories"]) == 2
        assert all(m["topic_id"] == b["id"] for m in memory["memories"])
        assert (
            c.patch(
                "/api/memories/" + memory["memories"][0]["id"],
                headers=headers,
                json={"scope": "global", "key": "回复语言", "content": "中文"},
            ).status_code
            == 200
        )
        cid = c.post("/api/conversations", headers=headers).json()["id"]
        db = app.state.db
        mid = uid()
        db.execute(
            "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
            (mid, cid, "user", "我喜欢中文", "complete", "{}", now()),
        )
        db.save_memory("global", None, "语言", "喜欢中文", mid)
        db.save_memory("global", None, "临时偏好", "喜欢简洁", mid)
        deleted = db.one("SELECT id FROM memories WHERE key='临时偏好'")["id"]
        assert c.delete("/api/memories/" + deleted, headers=headers).status_code == 200
        db.save_memory("global", None, "换了名称的临时偏好", "喜欢简洁", mid)
        assert not db.one("SELECT id FROM memories WHERE key='换了名称的临时偏好'")
        assert c.delete("/api/conversations/" + cid, headers=headers).status_code == 200
        memory = c.get("/api/memory").json()
        assert any(m["content"] == "喜欢中文" and m["source_message_id"] is None for m in memory["memories"])
        assert c.post("/api/logout", headers=headers).status_code == 200
        assert c.get("/api/conversations").status_code == 200


async def test_deferred_memory_uses_new_context_and_rejects_hallucinated_evidence(tmp_path):
    db = Database(str(tmp_path / "memory.db"))
    topic = db.topic("西语学习", "西班牙语学习安排")
    cid, mid = uid(), uid()
    db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "学习", now(), now()))
    db.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
        (mid, cid, "user", "我想每周学三次", "complete", json.dumps({"status": "pending"}), now()),
    )
    db.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
        (uid(), cid, "user", "前面说的是西班牙语。", "complete", "{}", now()),
    )
    db.enqueue(mid)
    seen = []

    class Router:
        async def classify(self, text, context, topics):
            seen.extend(context)
            return Route("matched", [topic["id"]], 0.99, True)

        async def judge(self, state, questions):
            return {"supported": {"noul": 0.99}}

    class AI:
        async def resolve_context(self, text, subsequent):
            return {
                "resolved": True,
                "resolved_message": "我想每周学西班牙语三次",
                "source_quote": "每周学三次",
                "clarification_quote": "前面说的是西班牙语",
            }

        async def extract(self, *args):
            return {
                "memories": [
                    {
                        "scope": "topic",
                        "topic_id": topic["id"],
                        "key": "频率",
                        "content": "每周学习西语三次",
                        "evidence": "每周学三次",
                    },
                    {"scope": "global", "key": "身份", "content": "用户是医生", "evidence": "我是医生"},
                    {
                        "scope": "topic",
                        "topic_id": "nonexistent",
                        "key": "bad",
                        "content": "bad",
                        "evidence": "每周学三次",
                    },
                ]
            }

    service = ChatService(db, Router(), AI())
    job = db.one("SELECT * FROM jobs WHERE message_id=?", (mid,))
    job["conversation_id"] = cid
    await service.consolidate(job)
    assert any("西班牙语" in m["content"] for m in seen)
    facts = db.all("SELECT * FROM memories")
    assert len(facts) == 1 and facts[0]["content"] == "每周学习西语三次"
    assert facts[0]["source_message_id"] == mid
    assert db.one("SELECT * FROM jobs WHERE message_id=?", (mid,))["status"] == "done"
    # A second application process reads the persisted memory.
    assert Database(db.path).all("SELECT * FROM memories")[0]["id"] == facts[0]["id"]


def test_streaming_history_retry_and_restart_recovery(tmp_path):
    app = create_app(config(tmp_path))
    service = app.state.service

    class Router:
        async def classify(self, text, history, topics):
            return Route("pending", [], 0.3, False)

    class AI:
        calls = 0

        async def stream(self, messages, memories):
            self.calls += 1
            yield "你好"
            if self.calls == 1:
                raise RuntimeError("simulated provider disconnect")
            yield "，已恢复。"

    service.router, service.ai = Router(), AI()
    with TestClient(app) as c:
        login(c)
        headers = {"X-Self-Agent": "1"}
        cid = c.post("/api/conversations", headers=headers).json()["id"]
        response = c.post(f"/api/conversations/{cid}/chat", headers=headers, json={"content": "你好"})
        assert "event: error" in response.text
        history = c.get(f"/api/conversations/{cid}/messages").json()["messages"]
        assert len(history) == 2 and history[-1]["status"] == "error" and history[-1]["content"] == "你好"
        response = c.post(
            f"/api/conversations/{cid}/chat",
            headers=headers,
            json={"content": "你好", "retry_message_id": history[0]["id"]},
        )
        assert "event: done" in response.text
        history = c.get(f"/api/conversations/{cid}/messages").json()["messages"]
        assert len(history) == 2 and history[-1]["content"] == "你好，已恢复。"
        assert not service.active
        app.state.db.execute("UPDATE messages SET status='streaming' WHERE id=?", (history[-1]["id"],))
    app.state.db.recover()
    assert (
        app.state.db.one("SELECT status FROM messages WHERE id=?", (history[-1]["id"],))["status"]
        == "interrupted"
    )


def test_passwordless_access_without_auth_secrets(tmp_path):
    cfg = config(tmp_path)
    cfg.session_secret = ""
    cfg.login_password_hash = ""
    with TestClient(create_app(cfg)) as c:
        assert c.get("/api/me").status_code == 200
        assert c.get("/api/memory", headers={"Authorization": "Bearer expired"}).status_code == 200
        assert c.post("/api/conversations").status_code == 403
        assert c.post("/api/conversations", headers={"X-Self-Agent": "1"}).status_code == 200
        assert c.post("/api/login", headers={"X-Self-Agent": "1"}, json={}).status_code == 200


async def test_bare_ingredients_retrieve_older_recipe_without_recall_gate(tmp_path):
    db = Database(str(tmp_path / "recipes.db"))
    topic = db.topic("做饭菜谱记录", "记录家常菜的做法和料汁配比")
    db.save_memory("topic", topic["id"], "recipe_potato_beans", "土豆豆角：生抽3勺、鸡精0.5勺。", None)
    for i in range(25):
        db.save_memory("topic", topic["id"], f"其他菜谱{i}", f"鸡蛋炒饭做法{i}", None)
    router = JevRouter(config(tmp_path))

    async def judge(state, questions):
        recall = questions["recall_topic_" + topic["id"]]
        assert "土豆豆角" in recall["criteria"]["true"]
        assert "recall" not in questions
        assert "土豆豆角" not in questions["topic_" + topic["id"]]["criteria"]["true"]
        return {name: {"noul": 0.97 if name.startswith("recall_topic_") else 0.1} for name in questions}

    router.judge = judge
    service = ChatService(db, router, None)
    route = await service.route({"content": "土豆 豆角"}, [])
    assert route.read_topic_ids == [topic["id"]] and not route.remember
    memories = service.read_memories(route.read_topic_ids, "土豆 豆角")
    assert memories[0]["key"] == "recipe_potato_beans"
    assert len(memories) == 20


async def test_keyword_recall_survives_router_outage_without_assigning_new_facts(tmp_path):
    db = Database(str(tmp_path / "keyword-recall.db"))
    recipe = db.topic("做饭菜谱记录", "烹饪步骤")
    other = db.topic("学习计划", "学习安排")
    db.save_memory("topic", recipe["id"], "土豆豆角菜谱", "生抽3勺", None)
    db.save_memory("topic", other["id"], "练习频率", "每周三次", None)
    router = JevRouter(config(tmp_path))

    async def outage(state, questions):
        raise RuntimeError("jev_unavailable")

    router.judge = outage
    service = ChatService(db, router, None)
    for text in ["菜谱", "土豆豆角", "菜谱？"]:
        route = await service.route({"content": text}, [])
        assert route.read_topic_ids == [recipe["id"]]
        assert route.status == "pending" and not route.topic_ids and not route.remember
        assert "已按关键词检索" in route.notice
        memories = service.read_memories(route.read_topic_ids, text)
        assert [m["key"] for m in memories] == ["土豆豆角菜谱"]
    for text in ["明天天气", "不想看菜谱", "我计划每周学三次"]:
        route = await service.route({"content": text}, [])
        assert not route.read_topic_ids and not route.topic_ids and not route.remember


def test_memory_retrieval_independent_of_fact_scope_and_persisted_after_reload(tmp_path):
    app = create_app(config(tmp_path))
    db, service = app.state.db, app.state.service
    topic = db.topic("做饭菜谱", "做法和配比")
    db.save_memory("topic", topic["id"], "土豆豆角", "生抽3勺、鸡精0.5勺", None)
    recipe = db.one("SELECT * FROM memories")

    class Router:
        async def classify(self, text, context, topics):
            return Route("general", [], 0.1, True, read_topic_ids=[topic["id"]])

    class AI:
        async def stream(self, messages, memories):
            assert memories[0]["content"] == recipe["content"]
            yield recipe["content"]

        async def extract(self, *args):
            return {"memories": []}

    service.router, service.ai = Router(), AI()
    with TestClient(app) as c:
        login(c)
        headers = {"X-Self-Agent": "1"}
        cid = c.post("/api/conversations", headers=headers).json()["id"]
        response = c.post(
            f"/api/conversations/{cid}/chat",
            headers=headers,
            json={"content": "我喜欢简短回复，土豆豆角怎么做？"},
        )
        assert "event: done" in response.text
        messages = c.get(f"/api/conversations/{cid}/messages").json()["messages"]
        assert not messages[0]["route"]["topic_ids"]
        reply = messages[-1]["route"]
        assert reply["read_topic_ids"] == [topic["id"]]
        assert reply["topic_names"] == ["做饭菜谱"]
        assert reply["memory_count"] == 1 and reply["memory_ids"] == [recipe["id"]]
        other = c.post("/api/topics", headers=headers, json={"name": "新菜谱", "description": "做法"}).json()
        assert (
            c.post(
                f"/api/topics/{topic['id']}/merge", headers=headers, json={"target_id": other["id"]}
            ).status_code
            == 200
        )
        reply = c.get(f"/api/conversations/{cid}/messages").json()["messages"][-1]["route"]
        assert reply["read_topic_ids"] == [other["id"]]
        assert c.delete(f"/api/topics/{other['id']}", headers=headers).status_code == 200
        reply = c.get(f"/api/conversations/{cid}/messages").json()["messages"][-1]["route"]
        assert reply["read_topic_ids"] == []


def test_legacy_mobile_login_and_logout_are_passwordless(tmp_path):
    with TestClient(create_app(config(tmp_path))) as c:
        headers = {"X-Self-Agent": "1"}
        mobile = c.post("/api/login", headers=headers, json={"password": "any", "client": "mobile"})
        assert mobile.status_code == 200 and mobile.json()["token"]
        assert c.get("/api/me").status_code == 200
        assert c.get("/api/me", headers={"Authorization": "Bearer invalid"}).status_code == 200
        assert c.post("/api/logout", headers=headers).status_code == 200
        assert c.get("/api/me").status_code == 200


async def test_questions_never_create_topics_but_keep_recall_and_mixed_facts(tmp_path):
    router = JevRouter(config(tmp_path))
    topics = [{"id": "a", "name": "产检", "description": "产检日程"}]

    async def query_judge(state, questions):
        values = {"remember": 0.05, "new_topic": 0.99, "recall_topic_a": 0.99, "topic_a": 0.99}
        return {name: {"noul": values.get(name, 0)} for name in questions}

    router.judge = query_judge
    for text in ["下次产检什么时候", "产检", "记忆名称叫孕期产检提醒", "定义域", "你好"]:
        route = await router.classify(text, [], topics)
        assert route.status == "chat" and not route.topic_ids and not route.remember
        assert route.read_topic_ids == ["a"]

    async def fact_judge(state, questions):
        values = {"remember": 0.99, "new_topic": 0.1, "recall_topic_a": 0.99, "topic_a": 0.99}
        return {name: {"noul": values.get(name, 0)} for name in questions}

    router.judge = fact_judge
    route = await router.classify("我预约了10月20日的产检，需要准备什么？", [], topics)
    assert route.status == "matched" and route.remember and route.topic_ids == ["a"]


async def test_main_model_resolves_uncertain_intent_without_losing_facts(tmp_path):
    db = Database(str(tmp_path / "intent.db"))

    class Router:
        async def classify(self, *args):
            return Route("pending", [], 0, False, "分类服务暂时不可用", read_topic_ids=["a"])

    class AI:
        async def memory_intent(self, text, history):
            return "预约" in text

        async def name_topic(self, *args):
            raise AssertionError("No topic creation for questions or unresolved topic membership")

    service = ChatService(db, Router(), AI())
    route = await service.route({"content": "下次产检什么时候"}, [])
    assert route.status == "chat" and not route.remember and not route.topic_ids and not route.notice
    assert route.read_topic_ids == ["a"]
    route = await service.route({"content": "我预约了10月20日的产检"}, [])
    assert route.status == "pending" and route.remember

    class Unavailable:
        async def memory_intent(self, *args):
            raise RuntimeError("unavailable")

    service.ai = Unavailable()
    route = await service.route({"content": "我预约了10月20日的产检"}, [])
    assert route.status == "pending" and route.notice


async def test_chat_skips_memory_jobs_and_deleted_topic_does_not_make_it_pending(tmp_path):
    app = create_app(config(tmp_path))
    db, service = app.state.db, app.state.service
    topic = db.topic("产检", "产检日程")
    cid, mid = uid(), uid()
    db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, "查询", now(), now()))
    route = {"status": "chat", "remember": False, "topic_ids": [], "read_topic_ids": [topic["id"]]}
    db.execute(
        "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
        (mid, cid, "user", "下次产检什么时候", "complete", json.dumps(route), now()),
    )
    service.queue_completed(mid, cid)
    assert not db.one("SELECT * FROM jobs WHERE message_id=?", (mid,))
    # Legacy jobs can finish without reclassification or extraction.
    db.enqueue(mid)
    await service.consolidate({"message_id": mid, "conversation_id": cid})
    assert db.one("SELECT status FROM jobs WHERE message_id=?", (mid,))["status"] == "done"
    with TestClient(app) as c:
        assert c.delete(f"/api/topics/{topic['id']}", headers={"X-Self-Agent": "1"}).status_code == 200
        assert not c.get("/api/memory").json()["pending"]
    assert json.loads(db.one("SELECT route FROM messages WHERE id=?", (mid,))["route"])["status"] == "chat"


async def test_main_model_information_decision_requires_literal_evidence(tmp_path):
    from self_agent.llm import Intelligence
    import pytest

    ai = Intelligence(config(tmp_path))
    result = {"remember": True, "evidence": "我预约了10月20日"}

    async def response(*args):
        return result

    ai.json = response
    assert await ai.memory_intent("我预约了10月20日的产检，需要准备什么？", [])
    result["evidence"] = "10月21日"
    with pytest.raises(ValueError, match="evidence"):
        await ai.memory_intent("我预约了10月20日的产检", [])
    result["remember"] = "false"
    with pytest.raises(ValueError, match="intent"):
        await ai.memory_intent("产检", [])
    result["remember"] = False
    assert not await ai.memory_intent("产检", [])
