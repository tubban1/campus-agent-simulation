"""Automated end-to-end test suite for Weather Simulation Closed-Loop Integration."""

import json
import sqlite3
import pytest
from datetime import datetime, timezone

from app.world_runtime.environment_config import derive_environment_from_real_time
from app.spatial.service import update_spatial_weather_factors
from app.spatial.models import metadata as spatial_metadata
from app.body_runtime import _is_residential_sleep_space, _night_sleep_state, advance_body_states
from app.world_runtime.causal_actions import is_residential_rest_location
from app.world_runtime.state_environment import apply_realism_constraints_to_decision
from app.schema import CAMPUS_STATE_SQL, DEFAULT_ENV


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")

    # Initialize basic schemas
    conn.executescript(CAMPUS_STATE_SQL)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS system_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS residents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        role TEXT NOT NULL,
        location TEXT NOT NULL,
        goal TEXT NOT NULL,
        personality TEXT NOT NULL,
        money REAL NOT NULL DEFAULT 100.0
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_profiles (
        resident_id INTEGER PRIMARY KEY,
        energy INTEGER NOT NULL DEFAULT 80,
        FOREIGN KEY (resident_id) REFERENCES residents(id)
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_body_states (
        resident_id INTEGER PRIMARY KEY,
        hunger REAL NOT NULL DEFAULT 10.0,
        fatigue REAL NOT NULL DEFAULT 10.0,
        sleep_debt REAL NOT NULL DEFAULT 10.0,
        stress REAL NOT NULL DEFAULT 10.0,
        attention REAL NOT NULL DEFAULT 80.0,
        social_energy REAL NOT NULL DEFAULT 80.0,
        health REAL NOT NULL DEFAULT 100.0,
        weather_exposure REAL NOT NULL DEFAULT 0.0,
        last_updated_at TEXT,
        last_updated_tick INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (resident_id) REFERENCES residents(id)
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS spatial_nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        node_type TEXT NOT NULL,
        properties TEXT NOT NULL DEFAULT '{}'
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS spatial_edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_node_id INTEGER NOT NULL,
        to_node_id INTEGER NOT NULL,
        distance_meters REAL NOT NULL,
        base_minutes REAL NOT NULL,
        congestion_factor REAL NOT NULL DEFAULT 1.0,
        weather_factor REAL NOT NULL DEFAULT 1.0,
        properties TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (from_node_id) REFERENCES spatial_nodes(id),
        FOREIGN KEY (to_node_id) REFERENCES spatial_nodes(id)
    );
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS agent_spatial_states (
        resident_id INTEGER PRIMARY KEY,
        movement_status TEXT NOT NULL DEFAULT 'idle',
        FOREIGN KEY (resident_id) REFERENCES residents(id)
    );
    """)
    conn.commit()
    yield conn
    conn.close()


def add_test_event(conn, day, event_type, content):
    conn.execute("INSERT INTO system_events (day, event_type, content) VALUES (?, ?, ?)", (day, event_type, content))
    conn.commit()


def test_unified_environment_derivation():
    """Test 1: Environment derivation accurately reflects rain and wind without time overrides."""
    base_clear = dict(DEFAULT_ENV)
    base_clear.update({"weather": "晴", "rainfall": 0, "wind_speed_10m": 5.0, "temperature": 24})
    env_clear = derive_environment_from_real_time(base_clear, now=datetime(2026, 8, 12, 16, 0))

    assert env_clear["playground_crowd"] >= 40
    assert env_clear["traffic_status"] == "正常"
    assert env_clear["campus_mood"] == "平稳" or env_clear["campus_mood"] == "活跃"

    base_storm = dict(DEFAULT_ENV)
    base_storm.update({"weather": "暴雨", "rainfall": 80, "wind_speed_10m": 35.0, "temperature": 18})
    env_storm = derive_environment_from_real_time(base_storm, now=datetime(2026, 8, 12, 16, 0))

    assert env_storm["playground_crowd"] <= 10
    assert env_storm["library_crowd"] > env_clear["library_crowd"]
    assert env_storm["canteen_crowd"] >= env_clear["canteen_crowd"]
    assert env_storm["traffic_status"] == "拥堵"
    assert env_storm["campus_mood"] == "严酷"


def test_reversible_weather_factor_updates(memory_db):
    """Test 2: Outdoor edge weather factors scale with weather severity and reset when clear."""
    conn = memory_db
    conn.execute("INSERT INTO spatial_nodes (id, code, name, node_type) VALUES (1, 'n1', '图书馆', 'building')")
    conn.execute("INSERT INTO spatial_nodes (id, code, name, node_type) VALUES (2, 'n2', '教学楼', 'building')")
    conn.execute("INSERT INTO spatial_nodes (id, code, name, node_type) VALUES (3, 'n3', '中央主干道', 'road')")
    conn.execute("INSERT INTO spatial_nodes (id, code, name, node_type) VALUES (4, 'n4', '东门广场', 'square')")

    # Edge 1: Indoor connector (building to building)
    conn.execute("INSERT INTO spatial_edges (id, from_node_id, to_node_id, distance_meters, base_minutes, weather_factor) VALUES (1, 1, 2, 50, 1.0, 1.0)")
    # Edge 2: Outdoor road (road to square)
    conn.execute("INSERT INTO spatial_edges (id, from_node_id, to_node_id, distance_meters, base_minutes, weather_factor) VALUES (2, 3, 4, 200, 4.0, 1.0)")
    conn.commit()

    # Apply storm weather
    storm_data = {"weather": "暴雨", "rainfall": 70, "wind_speed_10m": 30.0, "temperature": 16}
    res = update_spatial_weather_factors(conn, storm_data, day=1, add_event_func=add_test_event)

    assert res["updated_edges"] == 1
    assert res["outdoor_factor"] > 4.0

    e1 = conn.execute("SELECT weather_factor FROM spatial_edges WHERE id = 1").fetchone()
    e2 = conn.execute("SELECT weather_factor FROM spatial_edges WHERE id = 2").fetchone()

    assert e1["weather_factor"] == 1.0  # Indoor remains untouched
    assert e2["weather_factor"] > 4.0   # Outdoor scaled up

    # Check audit log
    event = conn.execute("SELECT * FROM system_events WHERE event_type = 'real_weather_edge_factor_updated'").fetchone()
    assert event is not None
    assert "weather_factor 调整" in event["content"]

    # Reversibility test: Apply clear weather
    clear_data = {"weather": "晴", "rainfall": 0, "wind_speed_10m": 4.0, "temperature": 24}
    res_clear = update_spatial_weather_factors(conn, clear_data, day=1, add_event_func=add_test_event)

    assert res_clear["updated_edges"] == 1
    assert res_clear["outdoor_factor"] == 1.0

    e2_reset = conn.execute("SELECT weather_factor FROM spatial_edges WHERE id = 2").fetchone()
    assert e2_reset["weather_factor"] == 1.0


def test_agent_indoor_redirection():
    """Test 3: Agents avoid outdoor activities during heavy weather."""
    agent = {"id": 1, "role": "学生", "location": "宿舍区"}
    decision = {"action": "move", "location": "操场"}
    perception = {"environment": {"weather": "暴雨", "rainfall": 80}}
    now = datetime(2026, 8, 12, 16, 0)

    redirect_count = 0
    for _ in range(100):
        adjusted = apply_realism_constraints_to_decision(None, agent, decision, perception, now)
        if adjusted["location"] != "操场":
            redirect_count += 1

    assert redirect_count >= 85  # Over 85% redirection rate enforced


def test_body_weather_exposure_accumulation(memory_db):
    """Test 4: Outdoor movement in rain/wind increases weather exposure."""
    conn = memory_db
    conn.execute("INSERT INTO residents (id, name, role, location, goal, personality) VALUES (1, '张三', '学生', '中央主干道', '学习', '外向')")
    conn.execute("INSERT INTO agent_profiles (resident_id, energy) VALUES (1, 80)")
    conn.execute("INSERT INTO agent_body_states (resident_id, weather_exposure, last_updated_at) VALUES (1, 0.0, '2026-08-12T15:00:00+00:00')")
    conn.execute("INSERT INTO agent_spatial_states (resident_id, movement_status) VALUES (1, 'moving')")
    conn.commit()

    now = datetime(2026, 8, 12, 16, 0, tzinfo=timezone.utc)
    env = {"rainfall": 60, "wind_speed_10m": 30.0, "temperature": 15}

    results = advance_body_states(conn, now, tick_number=1, environment=env)
    assert len(results) == 1
    assert results[0]["weather_exposure"] > 5.0


def test_real_world_apartment_node_is_a_sleep_space():
    """Imported OSM apartments must receive the same night recovery as legacy dorms."""
    assert _is_residential_sleep_space(
        "清华大学双清公寓南楼",
        "building",
        {"osm_tags": {"building": "apartments"}},
    )


def test_real_world_apartment_forces_night_rest_instead_of_observe():
    for role in ("学生", "食堂商家"):
        agent = {"id": 1, "role": role, "location": "清华大学双清公寓南楼"}
        decision = {"action": "observe", "location": "清华大学双清公寓南楼"}
        adjusted = apply_realism_constraints_to_decision(
            None,
            agent,
            decision,
            {"environment": {"weather": "晴", "rainfall": 0}},
            datetime(2026, 8, 13, 4, 0),
        )
        assert adjusted["action"] == "rest"
        assert adjusted["location"] == "清华大学双清公寓南楼"
        assert adjusted["goal"] == "夜间休息，恢复精力"


def test_real_world_apartment_is_valid_for_rest_settlement():
    assert is_residential_rest_location("清华大学双清公寓南楼")


def test_night_sleep_states_distinguish_deep_sleep_and_insomnia():
    base = {
        "resident_id": 1,
        "location": "清华大学双清公寓南楼",
        "role": "学生",
        "health": 80,
        "stress": 20,
    }
    night = datetime(2026, 8, 13, 3, 0)
    assert _night_sleep_state(base, night, tick_number=1, moving=False, waiting=False) == "deep_sleep"
    assert _night_sleep_state({**base, "stress": 90}, night, tick_number=1, moving=False, waiting=False) == "insomnia_discomfort"
    assert _night_sleep_state({**base, "location": "清华大学主干道"}, night, tick_number=1, moving=False, waiting=False) == "night_activity"
