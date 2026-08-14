"""Low-frequency, private dream generation for sleeping residents."""

from __future__ import annotations

import logging
import os
import random

from app.db import db_savepoint

logger = logging.getLogger(__name__)


def _enabled(name, default="true"):
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _residential_location(location):
    text = str(location or "").lower()
    return any(token in text for token in ("宿舍", "公寓", "住宅", "residence"))


def _fallback_dream(name, location, stress, fatigue):
    images = [
        "走廊的灯一盏接一盏熄灭，远处像有人叫了自己的名字，又听不真切。",
        "熟悉的校园路忽然变得很长，手里似乎握着一张已经看不清字的纸。",
        "窗外的树影晃动，像一段没有结尾的对话，醒来时只剩一点模糊的情绪。",
        "梦里回到一间陌生又熟悉的教室，钟声响过，却不知道该往哪里走。",
    ]
    tone = "不安" if stress >= 60 else ("疲惫" if fatigue >= 65 else "平静")
    return f"{name}梦见{random.choice(images)}醒来后只记得一种{tone}感，无法确认它指向什么。"


def process_night_dreams(
    conn,
    world_time,
    *,
    day,
    add_memory,
    consume_auto_model_budget,
    ask_llm,
    is_llm_configured,
    log_model_call,
):
    """Record at most two non-factual dream fragments in a quiet-night tick."""
    if not _enabled("WORLD_DREAMS_ENABLED") or not (1 <= world_time.hour < 6):
        return {"eligible": 0, "recorded": [], "model_calls": 0, "reason": "outside_dream_window"}
    try:
        probability = float(os.getenv("WORLD_DREAM_PROBABILITY_PER_TICK", "0.12"))
    except ValueError:
        probability = 0.12
    probability = max(0.0, min(1.0, probability))
    try:
        cap = max(0, min(3, int(os.getenv("WORLD_DREAMS_PER_TICK", "2"))))
    except ValueError:
        cap = 2

    rows = conn.execute(
        """
        SELECT r.id, r.name, r.role, r.personality, r.goal, r.location,
               b.stress, b.fatigue, b.sleep_debt
        FROM residents r
        JOIN agent_body_states b ON b.resident_id = r.id
        WHERE r.location IS NOT NULL
        ORDER BY r.id
        """
    ).fetchall()
    eligible = [dict(row) for row in rows if _residential_location(row["location"])]
    recorded, model_calls = [], 0
    for resident in eligible:
        if len(recorded) >= cap or random.random() >= probability:
            continue
        existing = conn.execute(
            """
            SELECT 1 FROM memories
            WHERE resident_id = ? AND day = ? AND source = 'dream'
            LIMIT 1
            """,
            (resident["id"], day),
        ).fetchone()
        if existing:
            continue
        recent = conn.execute(
            """
            SELECT content FROM memories
            WHERE resident_id = ? AND source <> 'dream'
            ORDER BY id DESC LIMIT 3
            """,
            (resident["id"],),
        ).fetchall()
        fragments = "；".join(str(row["content"])[:90] for row in recent) or "没有清晰的片段"
        content = None
        used_model = False
        if model_calls < 1 and _enabled("WORLD_DREAM_USE_LLM") and is_llm_configured():
            try:
                # Dreams are optional ambience.  They must never hold the
                # world tick hostage behind a slow model response, and must
                # never abort the surrounding tick transaction either: run the
                # model step and its bookkeeping inside a savepoint so any
                # failure rolls back only this fragment.
                with db_savepoint(conn, "dream_llm"):
                    if consume_auto_model_budget(conn, "dream", resident_id=resident["id"]):
                        prompt = f"""你为校园模拟中的居民写一段梦境碎片。居民：{resident['name']}（{resident['role']}），性格：{resident['personality']}，近期牵挂：{resident['goal']}，最近真实经历片段：{fragments}。
现在是深夜，压力 {float(resident['stress']):.0f}，疲劳 {float(resident['fatigue']):.0f}。写 40-90 字中文梦境，只写断续、模糊、不确定的感官片段；不能把梦写成事实、预言、知识或行动指令。不要标题、解释或 Markdown。"""
                        raw = ask_llm(prompt, timeout_seconds=3).strip().replace("\n", " ")
                        if raw and not raw.startswith(("{", "[")):
                            content = raw[:160]
                            model_calls += 1
                            used_model = True
                            log_model_call(conn, "dream", status="success", resident_id=resident["id"], prompt_version="night-dream-v1", input_tokens=max(1, len(prompt) // 4), output_tokens=max(1, len(raw) // 4))
                        else:
                            raise ValueError("invalid dream content")
            except Exception as exc:
                # The savepoint already restored a clean transaction.  Log the
                # original cause -- a DB/model failure here must never surface
                # as a generic InFailedSqlTransaction that hides the real error
                # -- and fall back to a deterministic dream below.
                logger.warning("Dream model step failed for resident %s: %s", resident["id"], exc)
        if not content:
            content = _fallback_dream(resident["name"], resident["location"], float(resident["stress"]), float(resident["fatigue"]))
        add_memory(
            conn,
            resident["id"],
            day,
            f"梦境·{content}",
            importance=1,
            memory_type="episodic",
            tags=["梦境", "非事实", "模糊片段"],
            source="dream",
        )
        recorded.append({"resident_id": int(resident["id"]), "used_model": used_model})
    return {"eligible": len(eligible), "recorded": recorded, "model_calls": model_calls, "reason": "ok"}
