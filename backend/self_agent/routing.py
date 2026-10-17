import asyncio
import json
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from .config import Settings


@dataclass
class Route:
    status: str = "pending"
    topic_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    remember: bool = False
    notice: str = ""
    read_topic_ids: list[str] | None = None

    def dict(self):
        return self.__dict__.copy()


def noul(instructions, yes, no):
    return {"type": "noul", "instructions": instructions, "criteria": {"true": yes, "false": no}}


class JevRouter:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def judge(self, state, questions):
        env = os.environ.copy()
        if self.settings.jev_env_file:
            env["JEV_ENV_FILE"] = self.settings.jev_env_file
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "vendor/jev.py"),
            "raw",
            "--provider",
            "typesafe",
            "--timeout",
            "15",
            "--retries",
            "1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(
                    json.dumps(
                        {
                            "model": "jev-latest",
                            "state": state,
                            "questions": questions,
                        },
                        ensure_ascii=False,
                    ).encode()
                ),
                timeout=25,
            )
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            # Never propagate provider output: it may contain user text or credential fragments.
            raise RuntimeError("jev_unavailable")
        return json.loads(stdout)["answers"]

    async def classify(self, text, history, topics) -> Route:
        questions = {
            "general": noul(
                "最新用户消息是否只是通用个人信息、稳定偏好或问候，不需要归入具体项目话题？",
                "个人基本信息、语言或回复偏好、日常问候；没有具体项目或持续活动",
                "讨论具体项目、学习计划、工作任务或主题不明确",
            ),
            "new_topic": noul(
                "最新用户消息是否清楚表达一个值得持续记录、且不属于任何已有分类的新话题？",
                "明确的项目、活动、目标或持续关注主题，现有分类无法覆盖",
                "属于已有话题；仅问候、临时闲聊、短代词，或无法确定主题",
            ),
            "remember": noul(
                "最新用户消息是否实际录入了可供后续使用的新信息，而不是仅提问、检索或聊天？"
                "用户提供的菜谱、知识笔记等具体内容也属于信息录入。"
                "查询已有记忆、只给出检索关键词或记忆名称、指出已有记录存在，都不是新信息录入。"
                "具体日期与活动的预约或日程安排也应保存，不限于长期稳定事实。"
                "简短的日期加活动陈述无需写我或请记住；但询问、假设及引用他人安排不能当成本人事实。",
                "本人的事实、偏好、目标、项目进展、明确决定，或具体日期加活动的安排陈述",
                "仅问问题、假设、引用他人的情况、模型猜测或无长期价值的信息",
            ),
        }
        for t in topics:
            questions["recall_topic_" + t["id"]] = noul(
                "最新用户消息是否涉及此分类，因而应该读取它的已有记忆？判断话题相关性，不判断能否完整回答。"
                "即使已有记忆只有部分信息、没有用户所问的日期，也要读取相关分类。"
                "助手之前声称没有记忆不能作为排除此分类的依据，以分类和记忆线索为准。"
                "不要求用户说记得、回忆或分类名称。"
                "仅输入菜名、食材、项目名或关键词也可以查询；结合近期上下文解析简短追问。"
                "此题只决定读取，候选记忆是历史数据，不能作为新事实的归属证据。",
                f"最新消息明确涉及 {t['name']} 的主题，或具体对象与候选记忆相关。"
                "分类主题明确相关即判true，即使具体答案尚未保存；不能因缺少某个字段而拒绝检索。"
                f"分类说明：{t['description']}；已有记忆线索："
                + json.dumps(t.get("memory_candidates", []), ensure_ascii=False),
                "最新消息及会话上下文与此分类或候选记忆无关；仅泛化词汇相似、不足以帮助回答",
            )
            questions["topic_" + t["id"]] = noul(
                "最新消息是否有明确依据归入此具体话题？候选分类不是会话历史。"
                "只能依据明确实体、本会话已确定的主题，或主动查询已有记忆的请求。"
                "没有上下文的泛化学习安排不能自动归入某个具体语言学习分类。",
                f"明确提及 {t['name']} 的具体实体或专属目标；会话上下文明确承接此主题；"
                f"或主动查询相关的已有记忆。分类说明：{t['description']}",
                "仅同属学习、工作、运动等宽泛领域，没有明确对象；通用动作、频率或词汇相似不算关联；"
                "讨论其他主题，或上下文不足",
            )
        try:
            # Each topic is an independent yes/no judgement, permitting several matches.
            # Chunking bounds the request size as the category catalogue grows.
            answers = {}
            items = list(questions.items())
            state = {
                "latest_user_message": text[:12000],
                "recent_context": history[-8:],
                "existing_topics": [{"name": t["name"], "description": t["description"]} for t in topics],
            }
            for offset in range(0, len(items), 40):
                answers.update(await self.judge(state, dict(items[offset : offset + 40])))
            remember_probability = float(answers["remember"]["noul"])
            remember = remember_probability >= 0.8
            read_matches = sorted(
                [(t["id"], float(answers.get("recall_topic_" + t["id"], {}).get("noul", 0))) for t in topics],
                key=lambda x: x[1],
                reverse=True,
            )
            read_ids = [
                tid for tid, probability in read_matches[:3] if probability >= self.settings.route_threshold
            ]
            # Always retrieve relevant memory. New facts still require grounded topic membership,
            # even when the same message also asks a question or contains global personal information.
            if remember_probability <= 0.2:
                return Route("chat", [], 1 - remember_probability, False, read_topic_ids=read_ids)
            if not remember:
                return Route("pending", [], remember_probability, False, read_topic_ids=read_ids)
            prefix = "topic_"
            matches = sorted(
                [(t["id"], float(answers.get(prefix + t["id"], {}).get("noul", 0))) for t in topics],
                key=lambda x: x[1],
                reverse=True,
            )
            selected = [t for t, p in matches[:3] if p >= self.settings.route_threshold]
            confidence = matches[0][1] if selected else float(answers["new_topic"]["noul"])
            status = (
                "matched"
                if selected
                else (
                    "new"
                    if confidence >= self.settings.new_topic_threshold
                    else (
                        "general" if float(answers.get("general", {}).get("noul", 0)) >= 0.85 else "pending"
                    )
                )
            )
            return Route(status, selected, confidence, remember, read_topic_ids=read_ids)
        except (RuntimeError, ValueError, KeyError, asyncio.TimeoutError):
            return Route(notice="分类服务暂时不可用，当前内容已暂存，稍后重试归类。")
