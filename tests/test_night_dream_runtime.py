import sqlite3
from datetime import datetime
from unittest.mock import patch

from app.models import SCHEMA_SQL
from app.world_runtime.dream_runtime import process_night_dreams
from tools.city_tools import add_memory


def _memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        """CREATE TABLE agent_body_states (
            resident_id INTEGER PRIMARY KEY, stress REAL, fatigue REAL, sleep_debt REAL
        )"""
    )
    conn.execute(
        "INSERT INTO residents (id,name,role,personality,goal,money,location) VALUES (1,'林小夏','大学生','安静','完成课程项目',100,'清华大学双清公寓南楼')"
    )
    conn.execute("INSERT INTO agent_body_states VALUES (1, 48, 70, 32)")
    conn.commit()
    return conn


def test_dream_is_private_non_factual_memory_and_only_once_per_night():
    conn = _memory_db()
    with patch("app.world_runtime.dream_runtime.random.random", return_value=0.0):
        result = process_night_dreams(
            conn,
            datetime(2026, 8, 13, 3, 0),
            day=13,
            add_memory=add_memory,
            consume_auto_model_budget=lambda *_args, **_kwargs: False,
            ask_llm=lambda _prompt: "不应调用",
            is_llm_configured=lambda: False,
            log_model_call=lambda *_args, **_kwargs: None,
        )
    assert len(result["recorded"]) == 1
    memory = conn.execute("SELECT content, tags, source, importance FROM memories").fetchone()
    assert memory["source"] == "dream"
    assert "梦境" in memory["tags"] and "非事实" in memory["tags"]
    assert memory["importance"] == 1

    again = process_night_dreams(
        conn,
        datetime(2026, 8, 13, 4, 0),
        day=13,
        add_memory=add_memory,
        consume_auto_model_budget=lambda *_args, **_kwargs: False,
        ask_llm=lambda _prompt: "不应调用",
        is_llm_configured=lambda: False,
        log_model_call=lambda *_args, **_kwargs: None,
    )
    assert again["recorded"] == []
