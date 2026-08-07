import json
import hashlib
from pathlib import Path
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import random
import re
import requests
import logging
import math
import os
import time
from contextlib import contextmanager
from queue import Queue
from threading import Lock, Thread
from uuid import uuid4
from xml.etree import ElementTree
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db import get_connection, using_postgres
from app.economy.router import router as economy_router
from app.economy.service import post_money_transfer
from app.organizations.router import router as organization_router
from app.organizations.service import process_organization_runtime
from app.supply.router import router as supply_router
from app.supply.service import (
    consumption_availability,
    fulfill_runtime_consumption,
    process_supply_runtime,
    supply_runtime_available,
)
from app.labor.router import router as labor_router
from app.labor.service import process_labor_runtime
from app.budget.router import router as budget_router
from app.budget.service import (
    budget_runtime_available,
    evaluate_action_choice,
    fund_emergency_action,
    process_budget_runtime,
    record_action_choice,
)
from app.market.router import router as market_router
from app.market.service import (
    evaluate_market_choice,
    find_market_mechanism,
    fulfill_market_goods_trade,
    market_runtime_available,
    process_market_runtime,
    record_market_demand,
)
from app.credit.router import router as credit_router
from app.credit.service import process_credit_runtime
from app.public_policy.router import router as public_policy_router
from app.public_policy.service import process_public_policy_runtime
from app.social_institutions.router import router as social_institution_router
from app.social_institutions.service import process_social_institution_runtime
from app.macro.router import router as macro_router
from app.macro.service import process_macro_runtime
from app.adaptation.router import router as adaptation_router
from app.adaptation.learning import process_adaptive_learning
from app.adaptation.norms import process_norm_emergence
from app.adaptation.institutions import process_institution_evolution
from app.resilience.router import router as resilience_router
from app.resilience.service import process_resilience_runtime
from app.population.router import router as population_router
from app.population.service import (
    population_runtime_available,
    process_population_runtime,
)
from app.external_world.router import router as external_world_router
from app.external_world.service import (
    external_world_available,
    process_external_world_runtime,
)
from app.external_world.adapters import FixedRSSAdapter, OpenMeteoAdapter
from app.longitudinal.router import router as longitudinal_router
from app.longitudinal.service import process_longitudinal_runtime
from app.body_router import router as body_router
from app.capability_router import router as capability_router
from app.capability_runtime import (
    capability_action_checks,
    individualize_action_rule,
)
from app.body_runtime import (
    advance_body_states,
    apply_action_body_effects,
    body_action_checks,
    get_body_state,
)
from app.perception_router import router as perception_router
from app.perception_runtime import (
    capture_tick_observations,
    get_agent_cognitive_context,
    spatial_memory_location_factors,
)
from app.spatial.router import router as spatial_router
from app.spatial.runtime import (
    ACTIVE_MOVEMENT_STATUSES,
    advance_active_movements,
    check_action_resource,
    spatial_runtime_available,
)
from app.world_runtime.clock import (
    WORLD_TIMEZONE,
    WORLD_TZ,
    get_world_now,
    get_world_plan_window,
    parse_runtime_time,
    parse_world_datetime,
    previous_completed_world_window as get_previous_completed_world_window,
    world_slot_from_hour,
    world_tick_due,
)
from app.world_runtime.runner import environment_flag_enabled, run_world_runner_loop
from app.world_runtime.scheduler import bounded_agent_batch_size
from app.world_runtime.decision import hunger_recovery_instruction
from app.world_runtime.observer import build_world_observer_state
from app.world_runtime.read_service import (
    list_action_executions as read_action_executions,
    list_action_rules as read_action_rules,
    list_delayed_effects as read_delayed_effects,
    list_world_events as read_world_events,
)
from app.agent_read_service import (
    build_goal_system,
    build_social_hierarchy,
    list_agent_learning,
    list_long_term_goals,
    list_relationships,
)
from app.social_read_service import build_profile_activity, list_group_goals, list_organizations
from app.lifecycle_read_service import (
    lifecycle_events,
    lifecycle_groups,
    lifecycle_overview,
    lifecycle_relationships,
    lifecycle_turning_points,
)
from app.simulation_read_service import fetch_simulation_logs
from app.world_state.read_service import get_snapshot, list_branches
from app.world_state.write_service import (
    create_branch,
    create_snapshot,
    require_paused_runtime,
    restore_snapshot,
    switch_branch,
)
from app.api.world_router import router as world_api_router
from app.api.agent_router import router as agent_api_router
from app.api.campus_router import router as campus_api_router
from app.world_runtime.orchestrator import (
    run_post_tick_handlers,
    run_agent_and_learning_stage,
    run_pre_agent_subsystems,
    settle_tick_completion,
    start_world_tick,
)
from services.llm_service import ask_llm, is_llm_configured
PROJECT_ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger(__name__)


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(value, *args, **kwargs):
    kwargs.setdefault("default", _json_default)
    return json.dumps(value, *args, **kwargs)


from tools.city_tools import (
    VALID_LOCATIONS,
    add_event,
    add_memory,
    add_memory_once,
    buy_sell,
    chat_between,
    ensure_memory_columns,
    get_current_day,
    get_resident,
    move_resident,
)

app = FastAPI(title="校园封闭世界 AI-Agent 沙盘系统", version="0.2.0")
app.include_router(spatial_router)
app.include_router(body_router)
app.include_router(perception_router)
app.include_router(capability_router)
app.include_router(economy_router)
app.include_router(organization_router)
app.include_router(supply_router)
app.include_router(labor_router)
app.include_router(budget_router)
app.include_router(market_router)
app.include_router(credit_router)
app.include_router(public_policy_router)
app.include_router(social_institution_router)
app.include_router(macro_router)
app.include_router(adaptation_router)
app.include_router(resilience_router)
app.include_router(population_router)
app.include_router(external_world_router)
app.include_router(longitudinal_router)
app.include_router(world_api_router)
app.include_router(agent_api_router)
app.include_router(campus_api_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/avatars", StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "assets" / "avatars")), name="avatars")
THREE_MODULE_DIR = PROJECT_ROOT / "frontend" / "vendor" / "three"
app.mount("/three", StaticFiles(directory=str(THREE_MODULE_DIR)), name="three")
SIMULATION_JOBS = {}
SIMULATION_JOBS_LOCK = Lock()
SOCIAL_SCHEMA_LOCK = Lock()
SOCIAL_SCHEMA_READY = False
WORLD_RUNNER_LOCK = Lock()
WORLD_RUNNER_THREAD = None
WORLD_TICK_LOCK = Lock()
WORLD_SCHEMA_LOCK = Lock()
WORLD_SCHEMA_READY = False
WORLD_RUNTIME_ID = 1
WORLD_TICK_ADVISORY_LOCK_ID = 7_436_177_031
DEFAULT_WORLD_STALE_TICK_SECONDS = 30 * 60
WORLD_EXTERNAL_SYNC_INTERVAL_SECONDS = 3600
WORLD_WEATHER_SYNC_INTERVAL_SECONDS = 3600
WORLD_CAMPUS_NEWS_WINDOW_SECONDS = 8 * 3600
OBSERVER_MODEL_DETAIL_COOLDOWN_SECONDS = 300
WORLD_AUTONOMOUS_ACTIONS = {
    "move", "observe", "chat", "reflect", "attend_class", "queue", "consume",
    "rest", "club_activity", "conflict", "collaborate", "late", "request_leave",
}

DEFAULT_SCHEDULE_RULES = [
    ("student_deep_night_rest", "student", "rest", "宿舍区", 0, 6, "all", 0, 100, 9.0, 0.10, "学生深夜以宿舍休息为主"),
    ("student_breakfast", "student", "consume", "食堂", 6, 9, "all", 0, 100, 4.0, 0.25, "早餐时段学生更可能去食堂"),
    ("student_morning_class", "student", "attend_class", "教学楼", 8, 12, "weekday", 0, 100, 7.0, 0.18, "工作日上午课程活动"),
    ("student_noon_queue", "student", "queue", "食堂", 11, 13, "all", 0, 100, 5.0, 0.30, "午餐高峰排队"),
    ("student_exam_study", "student", "observe", "图书馆", 18, 23, "all", 65, 100, 6.0, 0.20, "考试压力高时晚间自习增加"),
    ("student_evening_club", "student", "club_activity", "操场", 18, 21, "all", 0, 70, 3.2, 0.35, "晚间社团与运动活动"),
    ("teacher_morning_class", "teacher", "attend_class", "教学楼", 8, 12, "weekday", 0, 100, 6.5, 0.15, "教师上午授课"),
    ("teacher_office_hour", "teacher", "collaborate", "校务处", 14, 17, "weekday", 0, 100, 3.5, 0.20, "教师下午处理教务协作"),
    ("business_lunch_service", "business", "consume", "食堂", 10, 14, "all", 0, 100, 4.5, 0.25, "商户午间服务"),
    ("business_shop_hours", "business", "consume", "商业街", 9, 22, "all", 0, 100, 6.0, 0.22, "商业街营业时段"),
    ("service_morning_patrol", "service", "observe", "校务处", 7, 10, "all", 0, 100, 4.5, 0.18, "后勤早间巡查"),
    ("service_crowd_support", "service", "collaborate", "食堂", 11, 13, "all", 0, 100, 3.6, 0.25, "后勤午间协调拥挤"),
]

DEFAULT_CAUSAL_WEIGHTS = [
    ("rain_reduces_playground", "rainfall", "location", "操场", -1.0, 0.75, 25, 0.12, "降雨降低操场吸引力"),
    ("rain_increases_library", "rainfall", "location", "图书馆", 1.0, 0.25, 25, 0.10, "降雨增加室内学习倾向"),
    ("exam_increases_library", "exam_pressure", "location", "图书馆", 1.0, 0.70, 60, 0.10, "考试压力增加图书馆密度"),
    ("exam_increases_teaching", "exam_pressure", "location", "教学楼", 1.0, 0.35, 60, 0.10, "考试压力增加教学楼学习活动"),
    ("activity_increases_playground", "activity_heat", "location", "操场", 1.0, 0.45, 60, 0.18, "活动热度提高操场活动概率"),
    ("resource_pressure_increases_queue", "resource_pressure", "action", "queue", 1.0, 0.65, 60, 0.12, "资源压力提高排队和等待行为"),
    ("crowd_increases_conflict", "campus_flow", "action", "conflict", 1.0, 0.20, 75, 0.18, "拥挤提高小冲突概率"),
    ("study_mood_increases_collaboration", "study_atmosphere", "action", "collaborate", 1.0, 0.28, 65, 0.14, "学习氛围提升协作概率"),
]

DEFAULT_WORLD_UPDATE_SCHEDULES = [
    {
        "update_key": "campus_space_activity",
        "scope": "campus",
        "cadence": "hourly",
        "interval_seconds": 3600,
        "rule_version": "multiscale-update-v1",
        "metadata": {"description": "从居民位置与行动事件聚合校园空间活动"},
    },
    {
        "update_key": "social_dynamics",
        "scope": "social",
        "cadence": "eight_hour_window",
        "interval_seconds": 8 * 3600,
        "rule_version": "multiscale-update-v1",
        "metadata": {"description": "从互动和关系变化事件聚合社会动态"},
    },
    {
        "update_key": "institutional_resource_review",
        "scope": "institution",
        "cadence": "daily",
        "interval_seconds": 24 * 3600,
        "rule_version": "multiscale-update-v1",
        "metadata": {"description": "按日汇总制度、公共资源和待处理后果"},
    },
]

DEFAULT_WORLD_ACTION_RULES = {
    "move": {
        "duration_minutes": 12,
        "success_probability": 0.98,
        "preconditions": {"location_open": True, "capacity_available": True},
        "resources": {"energy": 8, "time_budget": 12, "money": 0},
        "direct_effects": [],
        "delayed_effects": [{"delay_minutes": 20, "target_type": "campus_state", "state_key": "campus_flow", "operation": "add", "value": 1}],
    },
    "observe": {
        "duration_minutes": 8,
        "success_probability": 1.0,
        "preconditions": {},
        "resources": {"energy": 2, "time_budget": 6, "money": 0},
        "direct_effects": [],
        "delayed_effects": [],
    },
    "chat": {
        "duration_minutes": 10,
        "success_probability": 0.97,
        "preconditions": {},
        "resources": {"energy": 3, "time_budget": 8, "money": 0},
        "direct_effects": [{"target_type": "agent_profile", "state_key": "mood", "operation": "set", "value": "放松"}],
        "delayed_effects": [],
    },
    "reflect": {
        "duration_minutes": 15,
        "success_probability": 1.0,
        "preconditions": {},
        "resources": {"energy": 2, "time_budget": 8, "money": 0},
        "direct_effects": [{"target_type": "agent_profile", "state_key": "mood", "operation": "set", "value": "沉思"}],
        "delayed_effects": [],
    },
    "attend_class": {
        "duration_minutes": 45,
        "success_probability": 0.97,
        "preconditions": {"allowed_locations": ["教学楼"], "location_open": True, "capacity_available": True},
        "resources": {"energy": 7, "time_budget": 18, "money": 0},
        "direct_effects": [],
        "delayed_effects": [{"delay_minutes": 60, "target_type": "campus_state", "state_key": "study_atmosphere", "operation": "add", "value": 1}],
    },
    "queue": {
        "duration_minutes": 12,
        "success_probability": 0.99,
        "preconditions": {"allowed_locations": ["食堂", "商业街"], "location_open": True},
        "resources": {"energy": 2, "time_budget": 10, "money": 0},
        "direct_effects": [],
        "delayed_effects": [{"delay_minutes": 30, "target_type": "campus_state", "state_key": "resource_pressure", "operation": "add", "value": -1}],
    },
    "consume": {
        "duration_minutes": 20,
        "success_probability": 0.98,
        "preconditions": {"allowed_locations": ["食堂", "商业街"], "location_open": True},
        "resources": {"energy": 3, "time_budget": 12, "money": 8},
        "direct_effects": [{"target_type": "agent_profile", "state_key": "energy", "operation": "add", "value": 5}],
        "delayed_effects": [{"delay_minutes": 60, "target_type": "campus_state", "state_key": "consumption_index", "operation": "add", "value": 0.01}],
    },
    "rest": {
        "duration_minutes": 45,
        "success_probability": 1.0,
        "preconditions": {"allowed_locations": ["宿舍区"], "location_open": True},
        "resources": {"energy": 0, "time_budget": 10, "money": 0},
        "direct_effects": [
            {"target_type": "agent_profile", "state_key": "energy", "operation": "add", "value": 12},
            {"target_type": "agent_profile", "state_key": "mood", "operation": "set", "value": "恢复中"},
        ],
        "delayed_effects": [],
    },
    "club_activity": {
        "duration_minutes": 40,
        "success_probability": 0.95,
        "preconditions": {"allowed_locations": ["操场"], "location_open": True, "capacity_available": True},
        "resources": {"energy": 8, "time_budget": 16, "money": 0},
        "direct_effects": [],
        "delayed_effects": [{"delay_minutes": 60, "target_type": "campus_state", "state_key": "activity_heat", "operation": "add", "value": 1}],
    },
    "conflict": {
        "duration_minutes": 15,
        "success_probability": 0.75,
        "preconditions": {},
        "resources": {"energy": 6, "time_budget": 10, "money": 0},
        "direct_effects": [{"target_type": "agent_profile", "state_key": "mood", "operation": "set", "value": "紧张"}],
        "delayed_effects": [{"delay_minutes": 30, "target_type": "campus_state", "state_key": "activity_heat", "operation": "add", "value": -1}],
    },
    "collaborate": {
        "duration_minutes": 35,
        "success_probability": 0.93,
        "preconditions": {},
        "resources": {"energy": 6, "time_budget": 15, "money": 0},
        "direct_effects": [],
        "delayed_effects": [{"delay_minutes": 90, "target_type": "campus_state", "state_key": "study_atmosphere", "operation": "add", "value": 1}],
    },
    "late": {
        "duration_minutes": 10,
        "success_probability": 1.0,
        "preconditions": {},
        "resources": {"energy": 2, "time_budget": 8, "money": 0},
        "direct_effects": [{"target_type": "agent_profile", "state_key": "mood", "operation": "set", "value": "匆忙"}],
        "delayed_effects": [],
    },
    "request_leave": {
        "duration_minutes": 25,
        "success_probability": 0.90,
        "preconditions": {"allowed_locations": ["校务处"], "location_open": True},
        "resources": {"energy": 3, "time_budget": 12, "money": 0},
        "direct_effects": [],
        "delayed_effects": [],
    },
}

DEFAULT_AGENT_PERSONALITY_TRAITS = {
    1: {"extraversion": 82, "conscientiousness": 58, "emotional_stability": 64, "risk_tolerance": 62, "rule_orientation": 45, "social_need": 88, "competitiveness": 36, "empathy": 72, "autonomy": 60, "stress_sensitivity": 48},
    2: {"extraversion": 42, "conscientiousness": 88, "emotional_stability": 76, "risk_tolerance": 34, "rule_orientation": 82, "social_need": 38, "competitiveness": 66, "empathy": 54, "autonomy": 74, "stress_sensitivity": 42},
    3: {"extraversion": 46, "conscientiousness": 74, "emotional_stability": 38, "risk_tolerance": 48, "rule_orientation": 62, "social_need": 45, "competitiveness": 68, "empathy": 50, "autonomy": 70, "stress_sensitivity": 82},
    4: {"extraversion": 72, "conscientiousness": 86, "emotional_stability": 68, "risk_tolerance": 46, "rule_orientation": 78, "social_need": 76, "competitiveness": 52, "empathy": 82, "autonomy": 72, "stress_sensitivity": 58},
    5: {"extraversion": 58, "conscientiousness": 78, "emotional_stability": 72, "risk_tolerance": 44, "rule_orientation": 64, "social_need": 52, "competitiveness": 62, "empathy": 48, "autonomy": 76, "stress_sensitivity": 42},
    6: {"extraversion": 84, "conscientiousness": 62, "emotional_stability": 70, "risk_tolerance": 68, "rule_orientation": 48, "social_need": 82, "competitiveness": 64, "empathy": 66, "autonomy": 78, "stress_sensitivity": 36},
    7: {"extraversion": 52, "conscientiousness": 84, "emotional_stability": 82, "risk_tolerance": 32, "rule_orientation": 86, "social_need": 54, "competitiveness": 30, "empathy": 86, "autonomy": 62, "stress_sensitivity": 34},
    8: {"extraversion": 26, "conscientiousness": 82, "emotional_stability": 78, "risk_tolerance": 24, "rule_orientation": 92, "social_need": 28, "competitiveness": 26, "empathy": 54, "autonomy": 66, "stress_sensitivity": 32},
    9: {"extraversion": 86, "conscientiousness": 66, "emotional_stability": 74, "risk_tolerance": 70, "rule_orientation": 50, "social_need": 84, "competitiveness": 74, "empathy": 62, "autonomy": 72, "stress_sensitivity": 34},
    10: {"extraversion": 44, "conscientiousness": 88, "emotional_stability": 80, "risk_tolerance": 30, "rule_orientation": 90, "social_need": 46, "competitiveness": 34, "empathy": 68, "autonomy": 58, "stress_sensitivity": 36},
    11: {"extraversion": 30, "conscientiousness": 76, "emotional_stability": 62, "risk_tolerance": 30, "rule_orientation": 68, "social_need": 34, "competitiveness": 28, "empathy": 76, "autonomy": 58, "stress_sensitivity": 58},
    12: {"extraversion": 90, "conscientiousness": 36, "emotional_stability": 66, "risk_tolerance": 66, "rule_orientation": 34, "social_need": 92, "competitiveness": 42, "empathy": 62, "autonomy": 56, "stress_sensitivity": 40},
    13: {"extraversion": 36, "conscientiousness": 90, "emotional_stability": 72, "risk_tolerance": 28, "rule_orientation": 84, "social_need": 34, "competitiveness": 58, "empathy": 52, "autonomy": 84, "stress_sensitivity": 44},
    14: {"extraversion": 22, "conscientiousness": 72, "emotional_stability": 58, "risk_tolerance": 46, "rule_orientation": 52, "social_need": 24, "competitiveness": 40, "empathy": 48, "autonomy": 86, "stress_sensitivity": 56},
    15: {"extraversion": 84, "conscientiousness": 60, "emotional_stability": 68, "risk_tolerance": 64, "rule_orientation": 42, "social_need": 80, "competitiveness": 58, "empathy": 70, "autonomy": 76, "stress_sensitivity": 42},
    16: {"extraversion": 34, "conscientiousness": 92, "emotional_stability": 42, "risk_tolerance": 30, "rule_orientation": 86, "social_need": 30, "competitiveness": 88, "empathy": 42, "autonomy": 78, "stress_sensitivity": 78},
    17: {"extraversion": 54, "conscientiousness": 86, "emotional_stability": 76, "risk_tolerance": 38, "rule_orientation": 74, "social_need": 58, "competitiveness": 42, "empathy": 84, "autonomy": 72, "stress_sensitivity": 40},
    18: {"extraversion": 18, "conscientiousness": 82, "emotional_stability": 46, "risk_tolerance": 26, "rule_orientation": 70, "social_need": 20, "competitiveness": 46, "empathy": 44, "autonomy": 88, "stress_sensitivity": 74},
    19: {"extraversion": 48, "conscientiousness": 70, "emotional_stability": 40, "risk_tolerance": 24, "rule_orientation": 62, "social_need": 62, "competitiveness": 20, "empathy": 94, "autonomy": 54, "stress_sensitivity": 86},
    20: {"extraversion": 78, "conscientiousness": 54, "emotional_stability": 68, "risk_tolerance": 88, "rule_orientation": 32, "social_need": 70, "competitiveness": 78, "empathy": 50, "autonomy": 90, "stress_sensitivity": 34},
}

from app.schema import (
    CAMPUS_STATE_SQL, SPACE_SYSTEM_SQL, DEFAULT_SPACES, DEFAULT_ENV, ENV_COLUMN_TYPES,
    AGENT_NEWS_SQL, AGENT_NEWS_COLUMN_TYPES, EXTERNAL_INFORMATION_SQL, AGENT_PROFILE_SQL, PROFILE_COLUMN_TYPES,
SOCIAL_SYSTEM_SQL, BEHAVIOR_SYSTEM_SQL, RELATIONSHIP_DYNAMIC_COLUMNS,
    LONG_TERM_GOAL_COLUMNS, AGENT_INFORMATION_COLUMNS, WORLD_RUNTIME_SQL, RESEARCH_SYSTEM_SQL,
    WORLD_RUNTIME_COLUMNS, WORLD_EVENT_STREAM_COLUMNS, WORLD_SNAPSHOT_COLUMNS,
    EXPERIMENT_RUN_COLUMNS,
)


class SchemaMigrationRequired(RuntimeError):
    """Raised when the runtime database has not completed build-time migrations."""


def ensure_table_columns(conn, table_name, column_types, *, allow_ddl=False):
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if not columns:
        raise SchemaMigrationRequired(
            f"Database table '{table_name}' is missing. Run the deployment schema "
            "initialization before starting the web service."
        )
    missing_columns = [
        (column, column_type)
        for column, column_type in column_types.items()
        if column not in columns
    ]
    if missing_columns and not allow_ddl:
        names = ", ".join(column for column, _ in missing_columns)
        raise SchemaMigrationRequired(
            f"Database table '{table_name}' is missing columns: {names}. Run the "
            "deployment migrations before starting the web service."
        )
    for column, column_type in missing_columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {column_type}")


def ensure_agent_profile_table(conn, *, allow_ddl=False):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(agent_profiles)").fetchall()}
    if not columns and allow_ddl:
        conn.executescript(AGENT_PROFILE_SQL)
    ensure_table_columns(
        conn,
        "agent_profiles",
        PROFILE_COLUMN_TYPES,
        allow_ddl=allow_ddl,
    )


def ensure_social_system_tables(conn, *, allow_ddl=False):
    global SOCIAL_SCHEMA_READY
    if SOCIAL_SCHEMA_READY:
        return
    with SOCIAL_SCHEMA_LOCK:
        if SOCIAL_SCHEMA_READY:
            return
        _initialize_social_system_tables(conn, allow_ddl=allow_ddl)
        SOCIAL_SCHEMA_READY = True


def _initialize_social_system_tables(conn, *, allow_ddl=False):
    ensure_agent_profile_table(conn, allow_ddl=allow_ddl)
    if allow_ddl:
        conn.executescript(SOCIAL_SYSTEM_SQL)
        conn.executescript(BEHAVIOR_SYSTEM_SQL)
    ensure_table_columns(
        conn,
        "relationship_dynamics",
        RELATIONSHIP_DYNAMIC_COLUMNS,
        allow_ddl=allow_ddl,
    )
    ensure_table_columns(
        conn,
        "long_term_goals",
        LONG_TERM_GOAL_COLUMNS,
        allow_ddl=allow_ddl,
    )
    ensure_table_columns(
        conn,
        "simulation_action_logs",
        {
            "tick_id": "INTEGER",
            "state_before": "TEXT NOT NULL DEFAULT '{}'",
            "state_after": "TEXT NOT NULL DEFAULT '{}'",
        },
        allow_ddl=allow_ddl,
    )
    if allow_ddl:
        relationship_id_type = "SERIAL PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS relationship_change_events (
                id {relationship_id_type},
                day INTEGER NOT NULL DEFAULT 1,
                tick_id INTEGER,
                event_id INTEGER,
                from_resident_id INTEGER NOT NULL,
                to_resident_id INTEGER NOT NULL,
                interaction TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                affinity_before INTEGER NOT NULL DEFAULT 50,
                affinity_after INTEGER NOT NULL DEFAULT 50,
                trust_before INTEGER NOT NULL DEFAULT 50,
                trust_after INTEGER NOT NULL DEFAULT 50,
                cooperation_before INTEGER NOT NULL DEFAULT 50,
                cooperation_after INTEGER NOT NULL DEFAULT 50,
                competition_before INTEGER NOT NULL DEFAULT 0,
                competition_after INTEGER NOT NULL DEFAULT 0,
                conflict_before INTEGER NOT NULL DEFAULT 0,
                conflict_after INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        membership_id_type = "SERIAL PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS group_membership_events (
                id {membership_id_type}, day INTEGER NOT NULL DEFAULT 1, group_id INTEGER NOT NULL,
                resident_id INTEGER NOT NULL, action TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
                member_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
    ensure_table_columns(conn, "relationship_change_events", {})
    ensure_table_columns(conn, "group_membership_events", {})
    normalize_agent_hierarchy(conn)
    seed_long_term_goals(conn)
    seed_multiscale_goals(conn)
    seed_campus_organizations(conn)


def load_json_text(text, fallback):
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def infer_goal_category(goal_text):
    text = str(goal_text or "")
    if any(word in text for word in ["成绩", "课程", "考研", "论文", "学习", "实验", "奖学金"]):
        return "study"
    if any(word in text for word in ["销售", "创业", "消费", "订单", "收入", "商机"]):
        return "business"
    if any(word in text for word in ["活动", "社团", "朋友", "交流", "合作"]):
        return "social"
    if any(word in text for word in ["秩序", "设施", "服务", "管理", "安全"]):
        return "service"
    return "general"


def seed_long_term_goals(conn):
    day = get_current_day(conn)
    residents = conn.execute("SELECT id, goal FROM residents").fetchall()
    for resident in residents:
        exists = conn.execute(
            "SELECT 1 FROM long_term_goals WHERE resident_id = ? LIMIT 1",
            (resident["id"],),
        ).fetchone()
        if not exists:
            conn.execute(
                """
                INSERT INTO long_term_goals
                (resident_id, title, category, deadline_day, last_update_day)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    resident["id"],
                    resident["goal"],
                    infer_goal_category(resident["goal"]),
                    day + 14,
                    day,
                ),
            )


def seed_multiscale_goals(conn):
    """Expose legacy long-term goals through the unified multi-horizon goal model."""
    rows = conn.execute(
        """
        SELECT id, resident_id, title, category, progress, deadline_day, status,
               last_update_day, created_at, completed_at
        FROM long_term_goals
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT INTO agent_goals
            (resident_id, legacy_long_term_goal_id, horizon, title, category, source,
             priority, commitment, expected_utility, feasibility, uncertainty,
             deadline_at, status, progress, visibility, created_day,
             last_reviewed_day, created_at, completed_at)
            VALUES (?, ?, 'long', ?, ?, 'legacy_migration',
                    70, 65, 70, 55, 35, ?, ?, ?, 'private', 1, ?, ?, ?)
            ON CONFLICT(legacy_long_term_goal_id) DO NOTHING
            """,
            (
                row["resident_id"],
                row["id"],
                row["title"],
                row["category"],
                f"simulation-day:{row['deadline_day']}",
                row["status"],
                row["progress"],
                row["last_update_day"],
                row["created_at"],
                row["completed_at"],
            ),
        )


def record_goal_revision(conn, goal_id, resident_id, revision_type, before=None, after=None, reason="", trigger_type="runtime", tick_id=None):
    conn.execute(
        """
        INSERT INTO goal_revisions
        (goal_id, resident_id, day, tick_id, revision_type, before_json,
         after_json, reason, trigger_type, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            goal_id,
            resident_id,
            get_current_day(conn),
            tick_id,
            revision_type,
            json_dumps(before or {}, ensure_ascii=False),
            json_dumps(after or {}, ensure_ascii=False),
            reason[:240],
            trigger_type,
            json_dumps({"source": "multiscale-goal-runtime-v1"}, ensure_ascii=False),
        ),
    )


def create_agent_goal(conn, resident_id, horizon, title, category="general", parent_goal_id=None,
                      source="runtime", priority=50, commitment=50, expected_utility=50,
                      feasibility=50, uncertainty=30, deadline_at="", visibility="private"):
    day = get_current_day(conn)
    cursor = conn.execute(
        """
        INSERT INTO agent_goals
        (resident_id, parent_goal_id, horizon, title, category, source, priority,
         commitment, expected_utility, feasibility, uncertainty, deadline_at,
         status, progress, visibility, created_day, last_reviewed_day)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)
        """,
        (
            resident_id,
            parent_goal_id,
            horizon,
            title[:180],
            category,
            source,
            clamp(priority),
            clamp(commitment),
            clamp(expected_utility),
            clamp(feasibility),
            clamp(uncertainty),
            deadline_at,
            visibility,
            day,
            day,
        ),
    )
    goal = dict(conn.execute("SELECT * FROM agent_goals WHERE id = ?", (cursor.lastrowid,)).fetchone())
    record_goal_revision(
        conn,
        goal["id"],
        resident_id,
        "created",
        after=goal,
        reason=f"运行时生成{horizon}层目标",
        trigger_type="goal_generation",
    )
    return goal


def parse_goal_deadline(value):
    if not value or str(value).startswith("simulation-day:"):
        return None
    return parse_world_datetime(value)


def review_multiscale_goals(conn, resident_id, world_time, tick_id=None):
    day = get_current_day(conn)
    rows = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND status = 'active'
        ORDER BY id
        """,
        (resident_id,),
    ).fetchall()
    reviewed = 0
    revised = 0
    for raw in rows:
        goal = dict(raw)
        interval = 7 if goal["horizon"] == "long" else 1
        if day - int(goal.get("last_reviewed_day") or 0) < interval:
            continue
        before = dict(goal)
        status = goal["status"]
        deadline_at = goal["deadline_at"]
        progress = int(goal["progress"] or 0)
        revision_type = "reviewed"
        reason = "按时间尺度完成周期复盘"
        if progress >= 100:
            status = "completed"
            revision_type = "completed"
            reason = "目标进度达到完成阈值"
        else:
            deadline = parse_goal_deadline(deadline_at)
            if deadline and deadline <= world_time:
                commitment = int(goal["commitment"] or 0)
                feasibility = int(goal["feasibility"] or 0)
                if goal["horizon"] == "short":
                    status = "completed" if progress >= 75 else ("paused" if commitment >= 65 else "abandoned")
                    revision_type = status
                    reason = "短期目标到期，根据完成度和承诺强度结算"
                elif goal["horizon"] == "medium" and feasibility < 35 and progress < 40:
                    status = "paused"
                    revision_type = "paused"
                    reason = "中期项目到期且可行性持续偏低"
                else:
                    extension_days = 30 if goal["horizon"] == "long" else 7
                    deadline_at = (world_time + timedelta(days=extension_days)).isoformat()
                    revision_type = "extended"
                    reason = "目标仍有价值，调整期限继续推进"
        completed_at = world_time.isoformat() if status == "completed" else goal.get("completed_at")
        conn.execute(
            """
            UPDATE agent_goals
            SET status = ?, deadline_at = ?, last_reviewed_day = ?,
                completed_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, deadline_at, day, completed_at, goal["id"]),
        )
        after = dict(conn.execute("SELECT * FROM agent_goals WHERE id = ?", (goal["id"],)).fetchone())
        if revision_type != "reviewed":
            record_goal_revision(
                conn,
                goal["id"],
                resident_id,
                revision_type,
                before=before,
                after=after,
                reason=reason,
                trigger_type="periodic_review",
                tick_id=tick_id,
            )
        reviewed += 1
        revised += int(revision_type != "reviewed")
    return {"reviewed": reviewed, "revised": revised}


def multiscale_goal_templates(resident, long_goal):
    category = str(long_goal.get("category") or infer_goal_category(long_goal.get("title")))
    role = role_group(resident.get("role"))
    templates = {
        "study": ("形成可检查的阶段学习成果", "完成当前阶段最重要的一项学习任务"),
        "business": ("验证近期校园需求并改进服务", "完成一次具体服务并记录反馈"),
        "social": ("通过持续互动发展一段有意义的关系", "履行一次交流、帮助或协作约定"),
        "service": ("改善一个可观察的校园运行问题", "完成一次巡查、协调或服务响应"),
        "general": ("把长期方向转化为一个可验证的阶段项目", "完成当前阶段最可行的一步"),
    }
    medium, short = templates.get(category, templates["general"])
    if role == "teacher" and category == "general":
        medium, short = "推进教学、指导或研究中的一个阶段成果", "完成一次具体教学或指导任务"
    elif role == "business" and category == "general":
        medium, short = templates["business"]
        category = "business"
    elif role == "service" and category == "general":
        medium, short = templates["service"]
        category = "service"
    return category, f"{medium}：围绕《{long_goal['title']}》", short


def ensure_goal_trajectory_episode(conn, goal, world_time):
    lookup_params = (goal["resident_id"], goal["id"], goal["horizon"])
    row = conn.execute(
        """
        SELECT * FROM trajectory_episodes
        WHERE resident_id = ? AND goal_id = ? AND horizon = ?
        """,
        lookup_params,
    ).fetchone()
    if row:
        return dict(row)

    # Existing goal episodes are read far more often than they are created.
    # Avoid a needless PostgreSQL unique-index write on every world tick.
    cursor = conn.execute(
        """
        INSERT INTO trajectory_episodes
        (resident_id, goal_id, horizon, episode_type, title, start_at, status,
         planned_summary, evidence_json)
        VALUES (?, ?, ?, 'goal_pursuit', ?, ?, 'active', ?, '{}')
        ON CONFLICT(resident_id, goal_id, horizon) DO NOTHING
        """,
        (
            goal["resident_id"],
            goal["id"],
            goal["horizon"],
            goal["title"],
            world_time.isoformat(),
            f"围绕{goal['horizon']}层目标推进：{goal['title']}",
        ),
    )
    row = conn.execute(
        """
        SELECT * FROM trajectory_episodes
        WHERE resident_id = ? AND goal_id = ? AND horizon = ?
        """,
        lookup_params,
    ).fetchone()
    return dict(row) if row else {"id": cursor.lastrowid}


def ensure_daily_commitments(conn, resident, short_goal, world_time):
    day_key = world_time.strftime("%Y-%m-%d")
    conn.execute(
        """
        UPDATE agent_commitments
        SET status = 'released', updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ? AND status = 'active' AND goal_id IS NOT NULL
          AND goal_id IN (
              SELECT id FROM agent_goals
              WHERE resident_id = ? AND status != 'active'
          )
        """,
        (resident["id"], resident["id"]),
    )
    expired = conn.execute(
        """
        SELECT * FROM agent_commitments
        WHERE resident_id = ? AND status = 'active' AND due_at != '' AND due_at <= ?
        """,
        (resident["id"], world_time.isoformat()),
    ).fetchall()
    for row in expired:
        linked_goal = conn.execute(
            "SELECT progress FROM agent_goals WHERE id = ?",
            (row["goal_id"],),
        ).fetchone() if row["goal_id"] else None
        status = "fulfilled" if linked_goal and int(linked_goal["progress"] or 0) >= 60 else "missed"
        conn.execute(
            "UPDATE agent_commitments SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, row["id"]),
        )
    existing = conn.execute(
        """
        SELECT * FROM agent_commitments
        WHERE resident_id = ? AND goal_id = ? AND status = 'active' AND start_at LIKE ?
        ORDER BY importance DESC, id
        LIMIT 1
        """,
        (resident["id"], short_goal["id"], f"{day_key}%"),
    ).fetchone()
    if existing:
        return dict(existing)
    group = role_group(resident.get("role"))
    weekday = world_time.weekday() < 5
    if group == "teacher":
        title, commitment_type, importance = "履行当天教学与指导职责", "institutional", 82
    elif group == "business":
        title, commitment_type, importance = "维持当天校园服务并回应需求", "service", 76
    elif group == "service":
        title, commitment_type, importance = "完成当天校园运行巡查与协调", "institutional", 84
    elif weekday:
        title, commitment_type, importance = "完成当天课程与学习安排", "institutional", 78
    else:
        title, commitment_type, importance = "平衡休息、自主学习与社会联系", "personal", 62
    start_at = world_time.replace(hour=0, minute=0, second=0, microsecond=0)
    due_at = start_at + timedelta(days=1)
    cursor = conn.execute(
        """
        INSERT INTO agent_commitments
        (resident_id, goal_id, commitment_type, title, start_at, due_at,
         status, importance, flexibility, visibility)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, 'private')
        """,
        (
            resident["id"],
            short_goal["id"],
            commitment_type,
            title,
            start_at.isoformat(),
            due_at.isoformat(),
            importance,
            35 if commitment_type == "institutional" else 65,
        ),
    )
    return dict(conn.execute("SELECT * FROM agent_commitments WHERE id = ?", (cursor.lastrowid,)).fetchone())


def ensure_multiscale_goal_structure(conn, resident, world_time, tick_id=None):
    review = review_multiscale_goals(conn, resident["id"], world_time, tick_id=tick_id)
    active = [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM agent_goals
            WHERE resident_id = ? AND status = 'active'
            ORDER BY priority DESC, commitment DESC, id
            """,
            (resident["id"],),
        ).fetchall()
    ]
    long_goals = [goal for goal in active if goal["horizon"] == "long"]
    if not long_goals:
        long_goal = create_agent_goal(
            conn,
            resident["id"],
            "long",
            resident.get("goal") or "形成稳定而有意义的校园生活",
            category=infer_goal_category(resident.get("goal")),
            source="self",
            priority=70,
            commitment=65,
            expected_utility=70,
            feasibility=55,
            uncertainty=35,
            deadline_at=(world_time + timedelta(days=90)).isoformat(),
        )
    else:
        long_goal = max(
            long_goals,
            key=lambda goal: (
    int(goal.get("priority") or 0)
    + int(goal.get("commitment") or 0)
    + int(goal.get("expected_utility") or 0)
    + random.uniform(-8, 8)
),
        )
        for competing_goal in long_goals:
            if competing_goal["id"] == long_goal["id"]:
                continue
            conn.execute(
                """
                INSERT INTO goal_dependencies
                (goal_id, related_goal_id, relationship_type, strength, explanation)
                VALUES (?, ?, 'competes', 45, '多个长期方向竞争有限的时间、精力和资源')
                ON CONFLICT(goal_id, related_goal_id, relationship_type) DO NOTHING
                """,
                (long_goal["id"], competing_goal["id"]),
            )
    category, medium_title, short_title = multiscale_goal_templates(resident, long_goal)
    medium_row = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND parent_goal_id = ? AND horizon = 'medium' AND status = 'active'
        ORDER BY priority DESC, id LIMIT 1
        """,
        (resident["id"], long_goal["id"]),
    ).fetchone()
    medium_goal = dict(medium_row) if medium_row else create_agent_goal(
        conn,
        resident["id"],
        "medium",
        medium_title,
        category=category,
        parent_goal_id=long_goal["id"],
        source="goal_decomposition",
        priority=68,
        commitment=62,
        expected_utility=68,
        feasibility=62,
        uncertainty=28,
        deadline_at=(world_time + timedelta(days=21)).isoformat(),
    )
    short_row = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND parent_goal_id = ? AND horizon = 'short' AND status = 'active'
        ORDER BY priority DESC, id LIMIT 1
        """,
        (resident["id"], medium_goal["id"]),
    ).fetchone()
    short_goal = dict(short_row) if short_row else create_agent_goal(
        conn,
        resident["id"],
        "short",
        short_title,
        category=category,
        parent_goal_id=medium_goal["id"],
        source="goal_decomposition",
        priority=72,
        commitment=68,
        expected_utility=66,
        feasibility=72,
        uncertainty=20,
        deadline_at=(world_time + timedelta(days=3)).isoformat(),
    )
    for goal_id, related_goal_id in (
        (medium_goal["id"], long_goal["id"]),
        (short_goal["id"], medium_goal["id"]),
    ):
        conn.execute(
            """
            INSERT INTO goal_dependencies
            (goal_id, related_goal_id, relationship_type, strength, explanation)
            VALUES (?, ?, 'supports', 80, '下层目标为上层目标提供可验证进展')
            ON CONFLICT(goal_id, related_goal_id, relationship_type) DO NOTHING
            """,
            (goal_id, related_goal_id),
        )
    commitment = ensure_daily_commitments(conn, resident, short_goal, world_time)
    episodes = {
        goal["horizon"]: ensure_goal_trajectory_episode(conn, goal, world_time)
        for goal in (long_goal, medium_goal, short_goal)
    }
    conn.execute(
        "UPDATE trajectory_episodes SET parent_episode_id = ? WHERE id = ?",
        (episodes["long"]["id"], episodes["medium"]["id"]),
    )
    conn.execute(
        "UPDATE trajectory_episodes SET parent_episode_id = ? WHERE id = ?",
        (episodes["medium"]["id"], episodes["short"]["id"]),
    )
    return {
        "long": long_goal,
        "medium": medium_goal,
        "short": short_goal,
        "commitment": commitment,
        "episodes": episodes,
        "review": review,
    }


def attach_goal_context_to_plan(plan, goal_context):
    plan = dict(plan or {})
    chain = {
        "long_goal_id": goal_context["long"]["id"],
        "long_goal": goal_context["long"]["title"],
        "medium_goal_id": goal_context["medium"]["id"],
        "medium_goal": goal_context["medium"]["title"],
        "short_goal_id": goal_context["short"]["id"],
        "short_goal": goal_context["short"]["title"],
        "commitment_id": goal_context["commitment"]["id"] if goal_context.get("commitment") else None,
        "commitment": goal_context["commitment"]["title"] if goal_context.get("commitment") else "",
    }
    plan["goal_chain"] = chain
    steps = []
    for step in plan.get("steps") or []:
        enriched = dict(step)
        enriched.update({
            "long_goal_id": chain["long_goal_id"],
            "medium_goal_id": chain["medium_goal_id"],
            "short_goal_id": chain["short_goal_id"],
            "commitment_id": chain["commitment_id"],
        })
        steps.append(enriched)
    plan["steps"] = steps
    return plan


def seed_campus_organizations(conn):
    """Create a small set of persistent campus organizations without forcing membership."""
    defaults = [
        ("学生会", "school", "组织校园活动与学生服务", 2200, {"venue_slots": 3, "notice_channels": 2}, [{"time": "周三 18:00", "task": "例会", "location": "校务处"}]),
        ("创新社", "club", "推进技术项目与成员协作", 1600, {"workstations": 8, "project_slots": 4}, [{"time": "周二 19:00", "task": "项目讨论", "location": "教学楼"}]),
        ("校园商户联盟", "business", "保障服务供给并维持经营", 3000, {"stock_budget": 1200, "marketing_slots": 2}, [{"time": "每日 11:30", "task": "经营协调", "location": "商业街"}]),
        ("图书馆服务组", "service", "维护学习空间与资源秩序", 1200, {"maintenance_slots": 2, "study_seats": 220}, [{"time": "周一 09:00", "task": "设施巡检", "location": "图书馆"}]),
    ]
    for name, organization_type, goal, budget, resources, schedule in defaults:
        conn.execute(
            """
            INSERT OR IGNORE INTO campus_organizations
            (name, organization_type, goal, budget, resources, schedule)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, organization_type, goal, budget, json_dumps(resources, ensure_ascii=False), json_dumps(schedule, ensure_ascii=False)),
        )


def get_relationship_dynamics(conn, from_id, to_id):
    ensure_social_system_tables(conn)
    row = conn.execute(
        "SELECT * FROM relationship_dynamics WHERE from_resident_id = ? AND to_resident_id = ?",
        (from_id, to_id),
    ).fetchone()
    if not row:
        base_score = get_relationship_score(conn, from_id, to_id)
        conn.execute(
            """
            INSERT INTO relationship_dynamics
            (from_resident_id, to_resident_id, affinity, trust, cooperation, competition, conflict, tension, last_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                from_id,
                to_id,
                clamp(50 + base_score // 2),
                clamp(45 + base_score // 2),
                clamp(40 + base_score // 2),
                0,
                0,
                0,
                get_current_day(conn),
            ),
        )
        row = conn.execute(
            "SELECT * FROM relationship_dynamics WHERE from_resident_id = ? AND to_resident_id = ?",
            (from_id, to_id),
        ).fetchone()
    return dict(row)


def evolve_relationship(
    conn,
    from_id,
    to_id,
    interaction,
    note,
    trust_delta=0,
    cooperation_delta=0,
    tension_delta=0,
    affinity_delta=None,
    competition_delta=0,
    conflict_delta=None,
    tick_id=None,
    event_id=None,
):
    current = get_relationship_dynamics(conn, from_id, to_id)
    if affinity_delta is None:
        affinity_delta = round((trust_delta + cooperation_delta - tension_delta) / 3)
    if conflict_delta is None:
        conflict_delta = tension_delta
    affinity = clamp(int(current["affinity"]) + affinity_delta)
    trust = clamp(int(current["trust"]) + trust_delta)
    cooperation = clamp(int(current["cooperation"]) + cooperation_delta)
    competition = clamp(int(current["competition"]) + competition_delta)
    conflict = clamp(int(current["conflict"]) + conflict_delta)
    tension = clamp(int(current["tension"]) + tension_delta)
    relationship_delta = round((affinity_delta + trust_delta + cooperation_delta - conflict_delta) / 4)
    relationship_score = change_relationship(conn, from_id, to_id, relationship_delta, note)
    conn.execute(
        """
        UPDATE relationship_dynamics
        SET affinity = ?, trust = ?, cooperation = ?, competition = ?, conflict = ?, tension = ?,
            interaction_count = interaction_count + 1, last_day = ?
        WHERE from_resident_id = ? AND to_resident_id = ?
        """,
        (affinity, trust, cooperation, competition, conflict, tension, get_current_day(conn), from_id, to_id),
    )
    change_cursor = conn.execute(
        """
        INSERT INTO relationship_change_events
        (day, tick_id, event_id, from_resident_id, to_resident_id, interaction, reason,
         affinity_before, affinity_after, trust_before, trust_after,
         cooperation_before, cooperation_after, competition_before, competition_after,
         conflict_before, conflict_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            get_current_day(conn), tick_id, event_id, from_id, to_id, interaction, note or "",
            int(current["affinity"]), affinity, int(current["trust"]), trust,
            int(current["cooperation"]), cooperation, int(current["competition"]), competition,
            int(current["conflict"]), conflict,
        ),
    )
    relationship_change_event_id = getattr(change_cursor, "lastrowid", None)
    append_social_interaction_event(
        conn,
        actor_resident_id=from_id,
        target_resident_id=to_id,
        interaction_type=interaction,
        summary=note or "",
        tick_id=tick_id,
        world_event_id=event_id,
        relationship_change_event_id=relationship_change_event_id,
        intensity=max(abs(affinity_delta), abs(trust_delta), abs(cooperation_delta), abs(competition_delta), abs(conflict_delta), 1) * 10,
        valence=clamp(affinity_delta + trust_delta + cooperation_delta - conflict_delta, -100, 100),
        evidence={
            "relationship_delta": relationship_delta,
            "affinity_before": int(current["affinity"]),
            "affinity_after": affinity,
            "trust_before": int(current["trust"]),
            "trust_after": trust,
            "cooperation_before": int(current["cooperation"]),
            "cooperation_after": cooperation,
            "competition_before": int(current["competition"]),
            "competition_after": competition,
            "conflict_before": int(current["conflict"]),
            "conflict_after": conflict,
        },
    )
    record_social_relation_interpretation(conn, from_id, to_id, tick_id=tick_id)
    return {
        "interaction": interaction,
        "affinity": affinity,
        "trust": trust,
        "cooperation": cooperation,
        "competition": competition,
        "conflict": conflict,
        "tension": tension,
        "relationship_score": relationship_score,
    }


def append_social_interaction_event(
    conn,
    actor_resident_id,
    target_resident_id=None,
    interaction_type="interaction",
    summary="",
    tick_id=None,
    world_event_id=None,
    relationship_change_event_id=None,
    location="",
    channel="in_person",
    intensity=50,
    valence=0,
    visibility="local",
    disclosure_state="ordinary",
    resource_context="",
    institution_context="",
    evidence=None,
):
    ensure_social_system_tables(conn)
    participants = [actor_resident_id]
    if target_resident_id is not None:
        participants.append(target_resident_id)
    conn.execute(
        """
        INSERT INTO social_interaction_events
        (day, tick_id, world_event_id, relationship_change_event_id, actor_resident_id,
         target_resident_id, participants_json, location, interaction_type, interaction_channel,
         intensity, valence, visibility, disclosure_state, resource_context, institution_context,
         observer_summary, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            get_current_day(conn), tick_id, world_event_id, relationship_change_event_id,
            actor_resident_id, target_resident_id, json_dumps(participants, ensure_ascii=False),
            location or "", interaction_type or "interaction", channel or "in_person",
            clamp(intensity), max(-100, min(100, int(valence))), visibility or "local",
            disclosure_state or "ordinary", resource_context or "", institution_context or "",
            summary or "", json_dumps(evidence or {}, ensure_ascii=False),
        ),
    )


def infer_emergent_relationship(conn, from_id, to_id, dynamics=None, score=None, history_rows=None):
    """Interpret an edge from accumulated evidence without declaring a fixed relationship type."""
    dynamics = dynamics or get_relationship_dynamics(conn, from_id, to_id)
    affinity = int(dynamics.get("affinity") or 50)
    trust = int(dynamics.get("trust") or 50)
    cooperation = int(dynamics.get("cooperation") or 50)
    competition = int(dynamics.get("competition") or 0)
    conflict = int(dynamics.get("conflict") or 0)
    tension = int(dynamics.get("tension") or 0)
    interaction_count = int(dynamics.get("interaction_count") or 0)
    score = int(score if score is not None else get_relationship_score(conn, from_id, to_id))
    if history_rows is None:
        history_rows = conn.execute(
            """
            SELECT interaction, reason, affinity_before, affinity_after, trust_before, trust_after,
                   cooperation_before, cooperation_after, competition_before, competition_after,
                   conflict_before, conflict_after, day, created_at
            FROM relationship_change_events
            WHERE from_resident_id = ? AND to_resident_id = ?
            ORDER BY id DESC
            LIMIT 12
            """,
            (from_id, to_id),
        ).fetchall()
    interaction_counts = {}
    evidence = []
    for row in history_rows:
        interaction = row["interaction"] or "interaction"
        interaction_counts[interaction] = interaction_counts.get(interaction, 0) + 1
        if len(evidence) < 4:
            reason = row["reason"] or interaction
            evidence.append(f"第{row['day']}天：{reason}")

    candidates = []

    def add_candidate(label, weight, rationale):
        weight = max(0, min(100, int(round(weight))))
        if weight > 0:
            candidates.append({"label": label, "confidence": weight, "rationale": rationale})

    add_candidate("弱联系/待观察", 65 - min(interaction_count * 9, 45), "互动证据还少，关系解释应保持开放")
    add_candidate("熟人关系", 34 + interaction_count * 4 + max(0, score - 45) * 0.5, "多次接触形成基本熟悉度")
    add_candidate("可信关系", trust * 0.75 + interaction_count * 2 - conflict * 0.25, "信任值和稳定互动共同支撑")
    add_candidate("合作伙伴", cooperation * 0.8 + interaction_counts.get("collaborate", 0) * 8 + interaction_counts.get("collaboration", 0) * 8, "协作行为和合作维度较强")
    add_candidate("紧张关系", conflict * 0.9 + tension * 0.55 + interaction_counts.get("conflict", 0) * 10, "冲突、紧张或摩擦事件较多")
    add_candidate("竞争关系", competition * 0.85 + interaction_counts.get("competition", 0) * 9, "竞争维度或竞争事件突出")
    add_candidate("潜在亲近关系", affinity * 0.55 + trust * 0.35 + interaction_count * 2 - conflict * 0.45, "高好感、高信任与重复接触可能形成更亲近解释")
    add_candidate("疏远但可信", trust * 0.7 - affinity * 0.2 - interaction_count * 1.5, "信任存在，但亲近和互动证据不足")

    candidates.sort(key=lambda item: item["confidence"], reverse=True)
    top = candidates[0] if candidates else {"label": "未形成稳定解释", "confidence": 20, "rationale": "缺少关系证据"}
    if not evidence:
        evidence.append("暂无明确关系变化事件，主要依据当前关系指标推断")
    return {
        "label": top["label"],
        "confidence": top["confidence"],
        "candidates": candidates[:4],
        "evidence": evidence,
        "metrics": {
            "score": score,
            "affinity": affinity,
            "trust": trust,
            "cooperation": cooperation,
            "competition": competition,
            "conflict": conflict,
            "tension": tension,
            "interaction_count": interaction_count,
        },
        "perspective": "from_agent",
        "interpretation_boundary": "这是从互动证据和关系指标生成的当前解释，不是预设身份，也不是确定事实。",
    }


def relationship_histories_by_target(conn, from_id, target_ids, per_target=12):
    ids = sorted({int(target_id) for target_id in target_ids if target_id is not None})
    if not ids:
        return {}
    placeholders = ",".join(["?"] * len(ids))
    rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT to_resident_id, interaction, reason, affinity_before, affinity_after,
                   trust_before, trust_after, cooperation_before, cooperation_after,
                   competition_before, competition_after, conflict_before, conflict_after,
                   day, created_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY to_resident_id ORDER BY id DESC
                   ) AS history_rank
            FROM relationship_change_events
            WHERE from_resident_id = ? AND to_resident_id IN ({placeholders})
        ) ranked
        WHERE history_rank <= ?
        ORDER BY to_resident_id, history_rank
        """,
        (from_id, *ids, max(1, min(int(per_target), 20))),
    ).fetchall()
    grouped = {target_id: [] for target_id in ids}
    for row in rows:
        grouped.setdefault(int(row["to_resident_id"]), []).append(row)
    return grouped


def record_social_relation_interpretation(conn, from_id, to_id, tick_id=None, perspective="system_researcher"):
    ensure_social_system_tables(conn)
    interpretation = infer_emergent_relationship(conn, from_id, to_id)
    conn.execute(
        """
        INSERT INTO social_relation_interpretations
        (day, tick_id, from_resident_id, to_resident_id, perspective, current_label,
         label_confidence, candidate_labels_json, evidence_json, metrics_json, interpretation_boundary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            get_current_day(conn), tick_id, from_id, to_id, perspective,
            interpretation["label"], interpretation["confidence"],
            json_dumps(interpretation["candidates"], ensure_ascii=False),
            json_dumps(interpretation["evidence"], ensure_ascii=False),
            json_dumps(interpretation["metrics"], ensure_ascii=False),
            interpretation["interpretation_boundary"],
        ),
    )


def advance_personal_goal(conn, resident_id, action, success):
    ensure_social_system_tables(conn)
    goal = conn.execute(
        """
        SELECT * FROM long_term_goals
        WHERE resident_id = ? AND status = 'active'
        ORDER BY deadline_day, id LIMIT 1
        """,
        (resident_id,),
    ).fetchone()
    if not goal:
        return None
    action_points = {
        "study": {"observe": 3, "move": 2, "chat": 1},
        "business": {"buy_sell": 6, "chat": 2, "move": 2, "observe": 1},
        "social": {"chat": 5, "move": 2, "observe": 1},
        "service": {"submit_policy": 5, "observe": 2, "move": 2, "chat": 1},
        "general": {"move": 2, "chat": 2, "buy_sell": 2, "submit_policy": 3, "observe": 1},
    }
    points = action_points.get(goal["category"], action_points["general"]).get(action, 1)
    if not success:
        points = 0
    progress = clamp(int(goal["progress"] or 0) + points)
    status = "completed" if progress >= int(goal["target_progress"] or 100) else "active"
    conn.execute(
        """
        UPDATE long_term_goals
        SET progress = ?, status = ?, last_update_day = ?,
            completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END
        WHERE id = ?
        """,
        (progress, status, get_current_day(conn), status, datetime.now(timezone.utc).isoformat(), goal["id"]),
    )
    if status == "completed":
        add_event(conn, get_current_day(conn), "goal_completed", f"Agent {resident_id} 完成长期目标《{goal['title']}》。")
    return {"goal_id": goal["id"], "title": goal["title"], "progress": progress, "status": status, "points": points}


def advance_group_goals(conn, day, action_results):
    ensure_social_system_tables(conn)
    completed_actions = {
        item["resident_id"]: item["action"]
        for item in action_results
        if item.get("success")
    }
    updates = []
    groups = conn.execute("SELECT * FROM group_goals WHERE status = 'active'").fetchall()
    for group in groups:
        members = json.loads(group["member_ids"])
        participant_count = sum(1 for member_id in members if member_id in completed_actions)
        if participant_count == 0:
            continue
        increment = min(15, 2 + participant_count * 2)
        progress = clamp(int(group["progress"] or 0) + increment)
        status = "completed" if progress >= int(group["target_progress"] or 100) else "active"
        conn.execute("UPDATE group_goals SET progress = ?, status = ? WHERE id = ?", (progress, status, group["id"]))
        updates.append({"group_id": group["id"], "name": group["name"], "progress": progress, "status": status, "active_members": participant_count})
        if status == "completed":
            add_event(conn, day, "group_goal_completed", f"群体目标《{group['shared_goal']}》已完成。")

    conn.execute(
        """
        UPDATE relationship_dynamics
        SET tension = CASE WHEN tension > 0 THEN tension - 1 ELSE 0 END
        WHERE last_day < ?
        """,
        (day,),
    )
    return updates


def schedule_location(task):
    text = str(task or "")
    if any(word in text for word in ["早餐", "午餐", "晚餐", "吃饭", "备菜"]):
        return "食堂"
    if any(word in text for word in ["课程", "课", "实验", "面试", "小组讨论", "编程"]):
        return "教学楼"
    if any(word in text for word in ["图书馆", "自习", "阅读", "背单词", "论文", "查招聘", "投递简历"]):
        return "图书馆"
    if any(word in text for word in ["训练", "晨跑", "操场", "采访"]):
        return "操场"
    if any(word in text for word in ["开店", "促销", "订单", "调研", "奶茶", "商业"]):
        return "商业街"
    if any(word in text for word in ["通知", "校务", "审批", "巡查", "维护", "维修", "治理"]):
        return "校务处"
    if any(word in text for word in ["宿舍", "复盘", "休息", "睡"]):
        return "宿舍区"
    return None


def get_schedule_context(schedule, env):
    entries = schedule if isinstance(schedule, list) else []
    time_text = str(env.get("real_time") or "")
    try:
        hour, minute = [int(value) for value in time_text.split(":")[:2]]
        now_minutes = hour * 60 + minute
    except (TypeError, ValueError):
        now_minutes = {"上午": 9 * 60, "中午": 12 * 60, "下午": 15 * 60, "晚上": 20 * 60, "深夜": 2 * 60}.get(env.get("time_slot"), 9 * 60)

    parsed = []
    for entry in entries:
        match = re.match(r"\s*(\d{1,2}):(\d{2})\s+(.+)", str(entry))
        if not match:
            continue
        start = int(match.group(1)) * 60 + int(match.group(2))
        task = match.group(3).strip()
        parsed.append({"entry": str(entry), "start_minutes": start, "task": task, "location": schedule_location(task)})
    if not parsed:
        return {"current_task": "自由安排", "is_due": False, "location": None, "minutes_until": None}

    parsed.sort(key=lambda item: item["start_minutes"])
    current = min(parsed, key=lambda item: abs(item["start_minutes"] - now_minutes))
    minutes_until = current["start_minutes"] - now_minutes
    is_due = -30 <= minutes_until <= 45
    return {
        "current_task": current["task"],
        "entry": current["entry"],
        "location": current["location"],
        "minutes_until": minutes_until,
        "is_due": is_due,
        "next_tasks": parsed[:4],
    }


def attach_schedule_guidance(schedule_context, decision):
    """Expose the current commitment to the Agent without overriding its choice."""
    decision["schedule_guidance"] = schedule_context
    if schedule_context.get("is_due"):
        decision["schedule_note"] = f"当前安排「{schedule_context['current_task']}」已到点，Agent 可自主选择执行或暂缓。"
    return decision


def is_schedule_aligned(resident, action, tool_input, schedule_context):
    if not schedule_context or not schedule_context.get("is_due"):
        return None
    expected_location = schedule_context.get("location")
    if not expected_location:
        return None
    if action == "move":
        return tool_input.get("destination") == expected_location
    return action == "observe" and resident["location"] == expected_location


def get_agent_module_state(conn, resident_id):
    ensure_agent_profile_table(conn)
    ensure_memory_columns(conn)
    resident = conn.execute("SELECT * FROM residents WHERE id = ?", (resident_id,)).fetchone()
    if not resident:
        return None

    profile = conn.execute(
        "SELECT * FROM agent_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    inventory_rows = conn.execute(
        "SELECT item_name, quantity FROM inventory WHERE resident_id = ? ORDER BY item_name",
        (resident_id,),
    ).fetchall()
    relationship_rows = conn.execute(
        """
        SELECT relationships.to_resident_id, residents.name, residents.role,
               relationships.score, relationships.notes
        FROM relationships
        JOIN residents ON residents.id = relationships.to_resident_id
        WHERE relationships.from_resident_id = ?
        ORDER BY relationships.score DESC
        LIMIT 10
        """,
        (resident_id,),
    ).fetchall()
    current_day = get_current_day(conn)
    memory_rows = conn.execute(
        """
        SELECT day, content, importance, memory_type, tags, source, access_count, last_accessed_at, created_at
        FROM memories
        WHERE resident_id = ? AND day <= ?
        ORDER BY id DESC
        LIMIT 8
        """,
        (resident_id, current_day),
    ).fetchall()

    profile_data = dict(profile) if profile else {}
    schedule = load_json_text(profile_data.get("schedule"), [])
    perception = load_json_text(profile_data.get("perception"), {})
    skills = load_json_text(profile_data.get("skills"), {})
    strategy = load_json_text(profile_data.get("strategy"), {})
    hierarchy_level = profile_data.get("hierarchy_level", 1)
    hierarchy_title = get_hierarchy_title(hierarchy_level)
    env = get_campus_environment(conn)
    schedule_context = get_schedule_context(schedule, env)

    return {
        "id": resident["id"],
        "name": resident["name"],
        "gender": profile_data.get("gender", "未设置"),
        "avatar_style": profile_data.get("avatar_style", "简单卡通校园人物"),
        "avatar_image": profile_data.get("avatar_image", ""),
        "organization": profile_data.get("organization", "学生"),
        "hierarchy_level": hierarchy_level,
        "hierarchy_title": hierarchy_title,
        "modules": {
            "Physical": {
                "description": "我是谁、我在哪",
                "position": resident["location"],
                "role": resident["role"],
                "energy": profile_data.get("energy", 80),
                "time_budget": profile_data.get("time_budget", 100),
                "money": resident["money"],
                "mood": profile_data.get("mood", "平稳"),
                "inventory": rows_to_dicts(inventory_rows),
            },
            "Mental": {
                "description": "我想干什么",
                "goal": resident["goal"],
                "personality": resident["personality"],
                "personality_traits": strategy.get("personality_traits", {}),
                "personality_version": strategy.get("personality_version", ""),
                "task": profile_data.get("current_task", "适应校园生活"),
            },
            "Social": {
                "description": "我认识谁",
                "relationships": rows_to_dicts(relationship_rows),
            },
            "Memory": {
                "description": "我经历过什么",
                "memories": rows_to_dicts(memory_rows),
            },
            "Schedule": {
                "description": "我现在该干什么",
                "schedule": schedule,
                "current_schedule": schedule_context,
            },
            "Perception": {
                "description": "我现在看见什么",
                "perception": perception,
            },
        },
    }


def get_all_agent_module_states(conn):
    rows = conn.execute("SELECT id FROM residents ORDER BY id").fetchall()
    return [get_agent_module_state(conn, row["id"]) for row in rows]


def clamp(value, low=0, high=100):
    return max(low, min(high, int(value)))


def choose_mood(energy, action, success=True):
    if not success:
        return "受挫"
    if energy <= 25:
        return "疲惫"
    if action == "chat":
        return "放松"
    if action == "buy_sell":
        return "满足"
    if action == "submit_policy":
        return "认真"
    if action == "move":
        return "行动中"
    return "观察中"


def calculate_action_cost(conn, resident_id, action, tool_input=None, success=True):
    tool_input = tool_input or {}
    base_costs = {
        "move": {"energy": 8, "time": 12},
        "chat": {"energy": 3, "time": 10},
        "buy_sell": {"energy": 5, "time": 15},
        "submit_policy": {"energy": 6, "time": 25},
        "create_group": {"energy": 7, "time": 28},
        "join_group": {"energy": 3, "time": 12},
        "leave_group": {"energy": 2, "time": 8},
        "observe": {"energy": 2, "time": 8},
    }
    cost = dict(base_costs.get(action, base_costs["observe"]))
    env = get_campus_environment(conn)
    if action == "move":
        destination = tool_input.get("destination")
        space = next((item for item in get_space_snapshot(conn)["spaces"] if item["location"] == destination), None)
        if space and int(space["crowd_percent"]) >= 70:
            cost["time"] += 6
            cost["energy"] += 2
        if int(env.get("rainfall", 0)) >= 20:
            cost["time"] += 4
            cost["energy"] += 2
        if env.get("traffic_status") == "拥堵":
            cost["time"] += 3
    if action == "buy_sell" and int(env.get("commercial_crowd", 0)) >= 70:
        cost["time"] += 5
    if action == "observe" and int(env.get("study_atmosphere", 0)) >= 75:
        cost["time"] = max(5, cost["time"] - 2)
    if not success:
        cost["energy"] += 3
        cost["time"] += 5
    return cost


def ensure_action_affordable(conn, resident_id, cost, action):
    profile = conn.execute("SELECT energy, time_budget FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    if not profile:
        return
    if action != "observe" and int(profile["energy"]) < int(cost["energy"]):
        raise ValueError("精力不足，需要先休息或进行低成本观察")
    if int(profile["time_budget"]) < int(cost["time"]):
        raise ValueError("今日可用时间不足，需要等待下一模拟日")


def update_agent_profile_after_action(conn, resident_id, action, reason, success=True, cost=None, schedule_context=None, tool_input=None):
    ensure_agent_profile_table(conn)
    profile = conn.execute(
        "SELECT energy, time_budget FROM agent_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    if not profile:
        return

    cost = cost or calculate_action_cost(conn, resident_id, action, success=success)
    energy_delta = -int(cost["energy"])

    new_energy = clamp(int(profile["energy"]) + energy_delta)
    new_time_budget = clamp(int(profile["time_budget"]) - int(cost["time"]))
    new_mood = choose_mood(new_energy, action, success)
    task_label = {
        "move": "前往新地点并观察周围变化",
        "chat": "完成一次校园交流",
        "buy_sell": "完成一次校园消费或交易",
        "submit_policy": "提出校园治理建议",
        "create_group": "发起一项协作计划",
        "join_group": "加入一项协作计划",
        "leave_group": "调整自己的协作关系",
        "observe": "观察校园环境并记录线索",
    }.get(action, "根据当前状态继续行动")
    schedule_aligned = is_schedule_aligned(get_resident(conn, resident_id), action, tool_input or {}, schedule_context)
    if schedule_aligned is True:
        task_label = f"按日程执行：{schedule_context['current_task']}"
    elif schedule_aligned is False:
        task_label = f"自主选择暂缓日程：{schedule_context['current_task']}"
    perception = {
        "last_action": action,
        "last_reason": reason,
        "status": "成功" if success else "失败后转为观察",
        "action_cost": cost,
        "time_budget_remaining": new_time_budget,
        "schedule_adherence": schedule_aligned,
    }
    conn.execute(
        """
        UPDATE agent_profiles
        SET energy = ?, time_budget = ?, mood = ?, current_task = ?, perception = ?
        WHERE resident_id = ?
        """,
        (new_energy, new_time_budget, new_mood, task_label, json_dumps(perception, ensure_ascii=False), resident_id),
    )
    body_effects = apply_action_body_effects(
        conn,
        resident_id,
        action,
        success=success,
    )
    if body_effects:
        new_energy = body_effects["energy"]
    return {
        "energy_cost": int(cost["energy"]),
        "time_cost": int(cost["time"]),
        "energy_remaining": new_energy,
        "time_budget_remaining": new_time_budget,
        "body_effects": body_effects,
    }


def recover_agents_for_new_day(conn, day):
    ensure_agent_profile_table(conn)
    conn.execute(
        """
        UPDATE agent_profiles
        SET energy = CASE WHEN energy + 16 > 100 THEN 100 ELSE energy + 16 END,
            time_budget = 100,
            current_task = '开始新的一天，准备执行日程'
        """
    )
    add_event(conn, day, "daily_recovery", "新的一天开始：所有 Agent 恢复部分精力，并重置每日时间预算。")


def get_simulation_state_value(conn, key, default=""):
    row = conn.execute("SELECT value FROM simulation_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_simulation_state_value(conn, key, value):
    existing = conn.execute("SELECT 1 FROM simulation_state WHERE key = ?", (key,)).fetchone()
    if existing:
        conn.execute("UPDATE simulation_state SET value = ? WHERE key = ?", (str(value), key))
    else:
        conn.execute("INSERT INTO simulation_state (key, value) VALUES (?, ?)", (key, str(value)))


def infer_runtime_day_anchor_date(conn, current_day, current_real_date):
    rows = conn.execute(
        """
        SELECT DISTINCT substr(world_time, 1, 10) AS real_date
        FROM world_ticks
        WHERE day = ? AND world_time != ''
        ORDER BY real_date ASC
        """,
        (current_day,),
    ).fetchall()
    dates = [row["real_date"] for row in rows if row["real_date"]]
    return dates[0] if dates else current_real_date


def sync_current_day_with_world_date(conn, world_time):
    """Advance the simulation day when the real-world date crosses midnight."""
    current_real_date = world_time.date().isoformat()
    current_day = get_current_day(conn)
    last_real_date = get_simulation_state_value(conn, "world_runtime_current_day_date", "")
    if not last_real_date:
        last_real_date = infer_runtime_day_anchor_date(conn, current_day, current_real_date)
        set_simulation_state_value(conn, "world_runtime_current_day_date", last_real_date)
    try:
        last_date = datetime.fromisoformat(last_real_date).date()
    except ValueError:
        last_date = world_time.date()

    elapsed_days = (world_time.date() - last_date).days
    if elapsed_days <= 0:
        if last_real_date != current_real_date:
            set_simulation_state_value(conn, "world_runtime_current_day_date", current_real_date)
        return {"advanced": False, "day": current_day, "elapsed_days": 0}

    elapsed_days = min(elapsed_days, 7)
    new_day = current_day + elapsed_days
    set_simulation_state_value(conn, "current_day", new_day)
    set_simulation_state_value(conn, "world_runtime_current_day_date", current_real_date)
    for day in range(current_day + 1, new_day + 1):
        recover_agents_for_new_day(conn, day)
        values = dict(DEFAULT_ENV)
        values.update({"semester_stage": "平时周", "event_name": "真实时间推进"})
        values = derive_environment_from_real_time(values, world_time)
        save_environment_values(conn, day, values)

    append_world_event(
        conn,
        "world_day_rollover",
        "世界日期已自动推进",
        f"真实日期从 {last_date.isoformat()} 推进到 {current_real_date}，仿真日从第 {current_day} 天推进到第 {new_day} 天。",
        day=new_day,
        slot=world_slot_from_hour(world_time.hour),
        payload={
            "previous_day": current_day,
            "new_day": new_day,
            "elapsed_days": elapsed_days,
            "last_real_date": last_date.isoformat(),
            "current_real_date": current_real_date,
        },
        ensure_schema=False,
    )
    return {"advanced": True, "day": new_day, "previous_day": current_day, "elapsed_days": elapsed_days}




CHENGDU_LATITUDE = 30.5728
CHENGDU_LONGITUDE = 104.0668

WEATHER_CODE_MAP = {
    0: "晴",
    1: "多云",
    2: "多云",
    3: "阴",
    45: "雾",
    48: "雾",
    51: "小雨",
    53: "小雨",
    55: "小雨",
    56: "小雨",
    57: "小雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "小雨",
    67: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "小雪",
    80: "阵雨",
    81: "阵雨",
    82: "大雨",
    85: "小雪",
    86: "大雪",
    95: "雷雨",
    96: "雷雨",
    99: "雷雨",
}


def get_real_campus_time(now=None):
    tz = timezone(timedelta(hours=8))
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    hour = current.hour
    if 5 <= hour <= 10:
        time_slot = "上午"
    elif 11 <= hour <= 13:
        time_slot = "中午"
    elif 14 <= hour <= 17:
        time_slot = "下午"
    elif 18 <= hour <= 23:
        time_slot = "晚上"
    else:
        time_slot = "深夜"

    month = current.month
    day = current.day
    if month in {2, 8}:
        semester_stage = "假期"
    elif month in {1, 7}:
        semester_stage = "考试周"
    elif month in {4, 11} and 10 <= day <= 25:
        semester_stage = "期中周"
    elif month in {3, 9} and day <= 20:
        semester_stage = "开学适应期"
    elif month in {5, 10}:
        semester_stage = "活动周"
    else:
        semester_stage = "平时周"

    return {
        "real_date": current.strftime("%Y-%m-%d"),
        "real_time": current.strftime("%H:%M:%S"),
        "weekday": weekdays[current.weekday()],
        "time_slot": time_slot,
        "semester_stage": semester_stage,
        "time_source": "system_clock",
        "hour": hour,
        "is_weekend": current.weekday() >= 5,
    }


def derive_environment_from_real_time(values, now=None):
    real_time = get_real_campus_time(now)
    hour = real_time["hour"]
    is_weekend = real_time["is_weekend"]
    time_slot = real_time["time_slot"]
    semester_stage = real_time["semester_stage"]
    values.update({key: real_time[key] for key in ["real_date", "real_time", "weekday", "time_slot", "semester_stage", "time_source"]})

    class_peak = 0 if is_weekend else (75 if 8 <= hour <= 11 or 14 <= hour <= 17 else 30)
    canteen_peak = 90 if 11 <= hour <= 13 or 17 <= hour <= 19 else (45 if 7 <= hour <= 9 else 25)
    library_base = 75 if semester_stage in {"期中周", "考试周"} else 45
    library_peak = library_base + (20 if 18 <= hour <= 22 else 0) - (15 if is_weekend and hour < 12 else 0)
    dorm_peak = 85 if hour >= 22 or hour <= 7 else (55 if 12 <= hour <= 14 else 35)
    playground_peak = 70 if 16 <= hour <= 20 and int(values.get("rainfall", 0)) < 20 else 25
    commercial_peak = 80 if 12 <= hour <= 14 or 18 <= hour <= 21 else 40

    exam_pressure = 82 if semester_stage == "考试周" else (65 if semester_stage == "期中周" else int(values.get("exam_pressure", 35)))
    activity_heat = 75 if semester_stage == "活动周" else int(values.get("activity_heat", 50))
    if is_weekend:
        activity_heat = min(100, activity_heat + 10)

    values.update({
        "exam_pressure": clamp(exam_pressure, 0, 100),
        "assignment_pressure": clamp(70 if semester_stage in {"期中周", "考试周"} else int(values.get("assignment_pressure", 40)), 0, 100),
        "study_atmosphere": clamp(55 + exam_pressure // 3 + (10 if time_slot == "晚上" else 0), 0, 100),
        "activity_heat": clamp(activity_heat, 0, 100),
        "event_name": "真实时间驱动校园状态",
        "event_intensity": clamp(activity_heat + (10 if time_slot in {"中午", "晚上"} else 0), 0, 100),
        "classroom_crowd": clamp(class_peak, 0, 100),
        "canteen_crowd": clamp(canteen_peak, 0, 100),
        "library_crowd": clamp(library_peak, 0, 100),
        "dorm_crowd": clamp(dorm_peak, 0, 100),
        "playground_crowd": clamp(playground_peak, 0, 100),
        "commercial_crowd": clamp(commercial_peak, 0, 100),
    })
    campus_flow = (values["classroom_crowd"] + values["canteen_crowd"] + values["commercial_crowd"] + values["playground_crowd"]) // 4
    values["campus_flow"] = clamp(campus_flow + (10 if time_slot in {"中午", "下午"} else 0), 0, 100)
    values["traffic_status"] = "拥堵" if values["campus_flow"] >= 75 else "正常"
    values["network_status"] = "拥堵" if values["dorm_crowd"] >= 75 and time_slot in {"晚上", "深夜"} else "稳定"
    values["resource_pressure"] = clamp((values["canteen_crowd"] + values["library_crowd"] + values["classroom_crowd"]) // 3, 0, 100)
    values["campus_mood"] = "紧张" if values["exam_pressure"] >= 75 else ("活跃" if values["activity_heat"] >= 70 else "平稳")
    values["consumption_index"] = round(max(0.5, min(1.8, 0.75 + values["commercial_crowd"] / 180 + values["canteen_crowd"] / 260)), 2)
    return values


def fetch_met_no_weather(latitude=CHENGDU_LATITUDE, longitude=CHENGDU_LONGITUDE):
    response = requests.get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": latitude, "lon": longitude},
        headers={"User-Agent": "campus-agent-simulation/1.0 github.com/mai555555/campus-agent-simulation"},
        timeout=12,
    )
    response.raise_for_status()
    series = response.json()["properties"]["timeseries"][0]
    details = series["data"]["instant"]["details"]
    next_hour = series["data"].get("next_1_hours", {})
    symbol = str(next_hour.get("summary", {}).get("symbol_code", "clearsky"))
    rainfall = max(0, min(100, int(round(float(next_hour.get("details", {}).get("precipitation_amount", 0) or 0) * 20))))
    temperature = int(round(float(details.get("air_temperature", 24))))

    if "thunder" in symbol:
        weather = "雷雨"
    elif "snow" in symbol:
        weather = "小雪"
    elif "rain" in symbol or "sleet" in symbol:
        weather = "小雨"
    elif "fog" in symbol:
        weather = "雾"
    elif "cloudy" in symbol:
        weather = "多云"
    else:
        weather = "晴"
    if temperature >= 32 and weather in {"晴", "多云"}:
        weather = "闷热"

    return {
        "weather": weather,
        "temperature": temperature,
        "rainfall": rainfall,
        "weather_source": "met-no",
        "weather_observed_at": str(series.get("time", "")),
        "raw": {"symbol_code": symbol, "wind_speed_10m": details.get("wind_speed"), "precipitation": rainfall / 20},
    }


def fetch_real_weather(latitude=CHENGDU_LATITUDE, longitude=CHENGDU_LONGITUDE):
    record = OpenMeteoAdapter().fetch(
        {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": "Asia/Shanghai",
            "timeout_seconds": 12,
        }
    )[0]
    payload = record["payload"]
    return {
        "weather": payload["weather"],
        "temperature": payload["temperature"],
        "rainfall": payload["rainfall"],
        "weather_source": "open-meteo",
        "weather_observed_at": record["observed_at"],
        "raw": payload,
    }


def derive_environment_from_weather(base_values):
    values = dict(base_values)
    rainfall = int(values.get("rainfall", 0) or 0)
    temperature = int(values.get("temperature", 24) or 24)
    weather = values.get("weather", "晴")
    activity_heat = int(values.get("activity_heat", 50) or 50)
    exam_pressure = int(values.get("exam_pressure", 35) or 35)
    assignment_pressure = int(values.get("assignment_pressure", 40) or 40)

    outdoor_penalty = min(35, rainfall // 2)
    heat_penalty = 10 if temperature >= 32 else 0
    values["playground_crowd"] = clamp(int(values.get("playground_crowd", 40)) - outdoor_penalty - heat_penalty, 10, 100)
    values["library_crowd"] = clamp(35 + exam_pressure // 2 + rainfall // 4, 10, 100)
    values["canteen_crowd"] = clamp(int(values.get("canteen_crowd", 50)) + (10 if rainfall > 20 else 0), 10, 100)
    values["commercial_crowd"] = clamp(35 + activity_heat // 2 - rainfall // 5 + (8 if temperature >= 30 else 0), 10, 100)
    values["campus_flow"] = clamp(55 + activity_heat // 3 - rainfall // 4, 10, 100)
    values["classroom_crowd"] = clamp(40 + assignment_pressure // 2, 10, 100)
    values["dorm_crowd"] = clamp(int(values.get("dorm_crowd", 45)) + (12 if rainfall > 20 else 0), 10, 100)
    values["study_atmosphere"] = clamp(35 + exam_pressure // 2 + assignment_pressure // 3, 10, 100)
    values["traffic_status"] = "拥堵" if values["campus_flow"] > 75 or rainfall > 40 else "正常"
    values["resource_pressure"] = clamp((values["canteen_crowd"] + values["library_crowd"] + values["classroom_crowd"]) // 3, 10, 100)
    values["network_status"] = "拥堵" if values["dorm_crowd"] > 75 else "稳定"
    values["safety_level"] = clamp(92 - rainfall // 8 - values["campus_flow"] // 12, 50, 100)
    values["consumption_index"] = round(max(0.5, min(1.8, 0.7 + activity_heat / 120 + values["commercial_crowd"] / 240)), 2)
    if exam_pressure > 75:
        values["campus_mood"] = "紧张"
    elif weather in {"小雨", "中雨", "大雨", "雷雨"}:
        values["campus_mood"] = "低落"
    elif activity_heat > 70:
        values["campus_mood"] = "活跃"
    else:
        values["campus_mood"] = "平稳"
    return values


def save_environment_values(conn, day, values):
    full_values = {key: values.get(key, default) for key, default in DEFAULT_ENV.items()}
    columns = list(DEFAULT_ENV.keys())
    assignments = ", ".join([f"{column} = excluded.{column}" for column in columns])
    placeholders = ", ".join(["?"] * (len(columns) + 1))
    conn.execute(
        f"""
        INSERT INTO campus_state (day, {', '.join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(day) DO UPDATE SET {assignments}
        """,
        [day] + [full_values[column] for column in columns],
    )



def get_hierarchy_title(level):
    titles = {
        1: "普通成员",
        2: "小组/商家负责人",
        3: "管理与协调者",
        4: "学校/组织决策层",
    }
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 1
    return titles.get(level, "普通成员")


def infer_hierarchy(role):
    if any(word in role for word in ["学校", "后勤", "组织"]):
        return 4, "学校组织"
    if any(word in role for word in ["辅导员", "管理员", "老师"]):
        return 3, "学校管理"
    if any(word in role for word in ["商家", "创业", "学生会", "社团", "委员"]):
        return 2, "校园服务/学生组织"
    return 1, "学生"




def normalize_agent_hierarchy(conn):
    rows = conn.execute(
        """
        SELECT residents.id, residents.role, agent_profiles.hierarchy_level, agent_profiles.organization
        FROM residents
        JOIN agent_profiles ON agent_profiles.resident_id = residents.id
        """
    ).fetchall()
    for row in rows:
        level, organization = infer_hierarchy(row["role"])
        if int(row["hierarchy_level"]) != level or row["organization"] != organization:
            conn.execute(
                """
                UPDATE agent_profiles
                SET hierarchy_level = ?, organization = ?
                WHERE resident_id = ?
                """,
                (level, organization, row["id"]),
            )


def ensure_profile_meta(conn, resident_id):
    ensure_social_system_tables(conn)
    resident = conn.execute("SELECT role FROM residents WHERE id = ?", (resident_id,)).fetchone()
    if not resident:
        return None
    profile = conn.execute("SELECT * FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    if not profile:
        level, organization = infer_hierarchy(resident["role"])
        conn.execute(
            """
            INSERT INTO agent_profiles (
                resident_id, gender, avatar_style, hierarchy_level, organization, skills, strategy
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (resident_id, "未设置", "简单卡通校园人物", level, organization, "{}", "{}"),
        )
        profile = conn.execute("SELECT * FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    return profile


def action_score(action, success=True):
    base = {
        "chat": 2,
        "communicate": 2,
        "negotiate": 4,
        "collaborate": 5,
        "compete": 3,
        "buy_sell": 3,
        "submit_policy": 4,
        "create_group": 5,
        "join_group": 3,
        "leave_group": 1,
        "move": 1,
        "observe": 1,
    }.get(action, 1)
    return base if success else -1


def record_learning(conn, resident_id, action, outcome, score_delta, lesson):
    profile = ensure_profile_meta(conn, resident_id)
    if not profile:
        return None
    day = get_current_day(conn)
    skills = load_json_text(profile["skills"], {})
    strategy = load_json_text(profile["strategy"], {})
    action_key = str(action)
    lesson = format_learning_diary(action_key, outcome, lesson)
    skill = skills.get(action_key, {"uses": 0, "score": 0})
    if not isinstance(skill, dict):
        skill = {"uses": int(skill), "score": 0}
    skill["uses"] = int(skill.get("uses", 0)) + 1
    skill["score"] = int(skill.get("score", 0)) + int(score_delta)
    skills[action_key] = skill
    strategy[action_key] = {
        "last_outcome": outcome,
        "last_score_delta": int(score_delta),
        "lesson": lesson,
    }
    conn.execute(
        """
        UPDATE agent_profiles
        SET skills = ?, strategy = ?
        WHERE resident_id = ?
        """,
        (json_dumps(skills, ensure_ascii=False), json_dumps(strategy, ensure_ascii=False), resident_id),
    )
    conn.execute(
        """
        INSERT INTO agent_learning (resident_id, day, action, outcome, score_delta, lesson)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (resident_id, day, action_key, outcome, int(score_delta), lesson),
    )
    add_memory(
        conn,
        resident_id,
        day,
        lesson,
        importance=4,
        memory_type="semantic",
        tags=[action_key, "学习", "经验"],
        source="learning",
    )
    return {
        "resident_id": resident_id,
        "action": action_key,
        "outcome": outcome,
        "score_delta": int(score_delta),
        "lesson": lesson,
        "skills": skills,
        "strategy": strategy,
    }


def format_learning_diary(action, outcome, lesson):
    """Keep personal memories readable; never store raw tool output or JSON."""
    action_text = {
        "chat": "和校园里的其他人聊了聊",
        "move": "前往了新的校园空间",
        "buy_sell": "完成了一次交易",
        "observe": "观察了周围的校园环境",
        "submit_policy": "参与了校园事务讨论",
        "create_group": "发起了一项协作计划",
        "join_group": "加入了一项协作计划",
        "leave_group": "调整了自己的协作安排",
        "negotiate": "和他人协商了一件事情",
        "collaborate": "参与了一次合作",
        "compete": "参与了一次竞争",
    }.get(action, "完成了一次自主行动")
    default_insight = {
        "chat": "交流能帮助我更了解他人的想法，也值得继续保持联系。",
        "move": "不同空间的氛围和资源会影响我的下一步选择。",
        "buy_sell": "我需要继续留意价格、预算和实际需求。",
        "observe": "环境变化值得记下来，之后可以据此调整计划。",
    }.get(action, "这次经历会帮助我以后做出更合适的选择。")
    raw_lesson = str(lesson or "").strip()
    if "{" in raw_lesson or "[" in raw_lesson or "执行 " in raw_lesson:
        insight = default_insight
    else:
        insight = raw_lesson.replace("学习记录：", "").strip() or default_insight
    outcome_text = "顺利完成" if outcome in {"成功", "完成沟通", "加入协作", "回应沟通", "获胜"} else "留下了新的经验"
    return f"今天我{action_text}，这次行动{outcome_text}。{insight}"


def get_relationship_score(conn, from_id, to_id):
    row = conn.execute(
        "SELECT score FROM relationships WHERE from_resident_id = ? AND to_resident_id = ?",
        (from_id, to_id),
    ).fetchone()
    return int(row["score"]) if row else 0


def change_relationship(conn, from_id, to_id, delta, note):
    current = get_relationship_score(conn, from_id, to_id)
    next_score = clamp(current + delta)
    conn.execute(
        """
        INSERT INTO relationships (from_resident_id, to_resident_id, score, notes)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(from_resident_id, to_resident_id)
        DO UPDATE SET score = excluded.score, notes = excluded.notes
        """,
        (from_id, to_id, next_score, note),
    )
    return next_score


def negotiate_between(conn, initiator_id, target_id, topic, proposal):
    ensure_social_system_tables(conn)
    initiator = get_resident(conn, initiator_id)
    target = get_resident(conn, target_id)
    if not initiator or not target:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    initiator_profile = ensure_profile_meta(conn, initiator_id)
    target_profile = ensure_profile_meta(conn, target_id)
    relationship = get_relationship_score(conn, initiator_id, target_id)
    level_bonus = int(initiator_profile["hierarchy_level"]) - int(target_profile["hierarchy_level"])
    success = relationship + level_bonus * 8 >= 25
    delta = 6 if success else 2
    status = "达成初步共识" if success else "保留分歧，等待更多条件"
    description = f"{initiator['name']} 与 {target['name']} 围绕「{topic}」协商：{proposal}。结果：{status}。"
    evolve_relationship(conn, initiator_id, target_id, "negotiation", f"协商议题：{topic}", delta, delta, 0 if success else 2)
    evolve_relationship(conn, target_id, initiator_id, "negotiation", f"回应协商：{topic}", max(1, delta - 1), max(1, delta - 1), 0 if success else 2)
    add_event(conn, get_current_day(conn), "negotiation", description)
    record_learning(conn, initiator_id, "negotiate", status, action_score("negotiate", success), f"围绕「{topic}」协商，学会根据关系和层级调整提案。")
    record_learning(conn, target_id, "negotiate", status, action_score("negotiate", success), f"回应「{topic}」协商，形成对合作条件的判断。")
    conn.commit()
    return {
        "type": "negotiation",
        "success": success,
        "status": status,
        "relationship_after": get_relationship_score(conn, initiator_id, target_id),
        "description": description,
    }


def create_collaboration(conn, leader_id, member_ids, title, goal):
    ensure_social_system_tables(conn)
    ids = [leader_id] + [mid for mid in member_ids if mid != leader_id]
    residents = conn.execute(
        f"SELECT id, name FROM residents WHERE id IN ({','.join(['?'] * len(ids))})",
        ids,
    ).fetchall()
    if len(residents) != len(set(ids)):
        raise HTTPException(status_code=404, detail="有 Agent 不存在")
    score = 10 + len(ids) * 3
    conn.execute(
        """
        INSERT INTO collaborations (title, leader_id, member_ids, goal, status, score)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, leader_id, json_dumps(ids, ensure_ascii=False), goal, "active", score),
    )
    roles = {str(member_id): ("负责人" if member_id == leader_id else "成员") for member_id in ids}
    group_cursor = conn.execute(
        """
        INSERT INTO group_goals (name, group_type, leader_id, member_ids, roles, shared_goal, deadline_day, current_plan)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title, "协作小组", leader_id, json_dumps(ids, ensure_ascii=False), json_dumps(roles, ensure_ascii=False), goal, get_current_day(conn) + 10, "成员按各自任务推进，并在每日模拟后汇总进度。"),
    )
    for from_id in ids:
        for to_id in ids:
            if from_id != to_id:
                evolve_relationship(conn, from_id, to_id, "collaboration", f"参与协作：{title}", 4, 5, -1)
        record_learning(conn, from_id, "collaborate", "加入协作", action_score("collaborate", True), f"参与「{title}」，围绕「{goal}」分工合作。")
    add_event(conn, get_current_day(conn), "collaboration", f"协作项目「{title}」启动，目标：{goal}。")
    conn.commit()
    return {"title": title, "leader_id": leader_id, "member_ids": ids, "goal": goal, "status": "active"}


def record_group_membership_event(conn, group_id, resident_id, action, reason, member_ids):
    conn.execute(
        "INSERT INTO group_membership_events (day, group_id, resident_id, action, reason, member_ids) VALUES (?, ?, ?, ?, ?, ?)",
        (get_current_day(conn), group_id, resident_id, action, reason or "", json_dumps(member_ids, ensure_ascii=False)),
    )


def join_group_goal(conn, resident_id, group_id):
    ensure_social_system_tables(conn)
    group = conn.execute("SELECT * FROM group_goals WHERE id = ? AND status = 'active'", (group_id,)).fetchone()
    if not group:
        raise ValueError("没有可加入的活跃小组")
    members = load_json_text(group["member_ids"], [])
    if resident_id in members:
        return {"group_id": group_id, "message": "已经是该小组成员"}
    members.append(resident_id)
    roles = load_json_text(group["roles"], {})
    roles[str(resident_id)] = "成员"
    conn.execute("UPDATE group_goals SET member_ids = ?, roles = ? WHERE id = ?", (json_dumps(members, ensure_ascii=False), json_dumps(roles, ensure_ascii=False), group_id))
    record_group_membership_event(conn, group_id, resident_id, "join", f"加入群体：{group['name']}", members)
    for member_id in members:
        if member_id != resident_id:
            evolve_relationship(conn, resident_id, member_id, "group_join", f"加入小组：{group['name']}", 2, 3, -1)
            evolve_relationship(conn, member_id, resident_id, "group_join", f"新成员加入：{group['name']}", 1, 2, 0)
    add_event(conn, get_current_day(conn), "group_join", f"Agent {resident_id} 加入小组「{group['name']}」。")
    return {"group_id": group_id, "group_name": group["name"], "member_ids": members, "message": "加入小组成功"}


def leave_group_goal(conn, resident_id, group_id):
    ensure_social_system_tables(conn)
    group = conn.execute("SELECT * FROM group_goals WHERE id = ? AND status = 'active'", (group_id,)).fetchone()
    if not group:
        raise ValueError("没有可退出的活跃小组")
    members = load_json_text(group["member_ids"], [])
    if resident_id not in members:
        raise ValueError("当前不是该小组成员")
    if int(group["leader_id"]) == resident_id:
        raise ValueError("负责人不能直接退出，请先由小组重新选择负责人")
    members.remove(resident_id)
    roles = load_json_text(group["roles"], {})
    roles.pop(str(resident_id), None)
    conn.execute("UPDATE group_goals SET member_ids = ?, roles = ? WHERE id = ?", (json_dumps(members, ensure_ascii=False), json_dumps(roles, ensure_ascii=False), group_id))
    record_group_membership_event(conn, group_id, resident_id, "leave", f"离开群体：{group['name']}", members)
    add_event(conn, get_current_day(conn), "group_leave", f"Agent {resident_id} 退出小组「{group['name']}」。")
    return {"group_id": group_id, "group_name": group["name"], "member_ids": members, "message": "退出小组成功"}
    return {"type": "collaboration", "title": title, "leader_id": leader_id, "member_ids": ids, "goal": goal, "score": score, "status": "active", "group_goal_id": group_cursor.lastrowid}


def create_competition(conn, participant_ids, title, metric):
    ensure_social_system_tables(conn)
    if len(participant_ids) < 2:
        raise HTTPException(status_code=400, detail="竞争至少需要 2 个 Agent")
    rows = conn.execute(
        f"SELECT residents.id, residents.name, residents.money, agent_profiles.energy, agent_profiles.skills FROM residents JOIN agent_profiles ON agent_profiles.resident_id = residents.id WHERE residents.id IN ({','.join(['?'] * len(participant_ids))})",
        participant_ids,
    ).fetchall()
    if len(rows) != len(set(participant_ids)):
        raise HTTPException(status_code=404, detail="有 Agent 不存在")
    scores = []
    for row in rows:
        skills = load_json_text(row["skills"], {})
        compete_skill = skills.get("compete", {}) if isinstance(skills, dict) else {}
        skill_score = compete_skill.get("score", 0) if isinstance(compete_skill, dict) else 0
        score = int(row["energy"]) + int(row["money"]) // 10 + int(skill_score) + random.randint(0, 12)
        scores.append({"id": row["id"], "name": row["name"], "score": score})
    scores.sort(key=lambda item: item["score"], reverse=True)
    winner = scores[0]
    result = f"{winner['name']} 在「{title}」中以 {winner['score']} 分暂时领先，评价指标：{metric}。"
    conn.execute(
        """
        INSERT INTO competitions (title, participant_ids, metric, winner_id, result)
        VALUES (?, ?, ?, ?, ?)
        """,
        (title, json_dumps(participant_ids, ensure_ascii=False), metric, winner["id"], result),
    )
    for item in scores:
        won = item["id"] == winner["id"]
        record_learning(conn, item["id"], "compete", "获胜" if won else "参与竞争", action_score("compete", won), f"参与「{title}」竞争，理解自身在「{metric}」上的优势和差距。")
        for opponent in scores:
            if opponent["id"] != item["id"]:
                evolve_relationship(conn, item["id"], opponent["id"], "competition", f"参与竞争：{title}", 1 if won else 0, 0, 3)
    add_event(conn, get_current_day(conn), "competition", result)
    conn.commit()
    return {"type": "competition", "title": title, "metric": metric, "winner_id": winner["id"], "scores": scores, "result": result}


class MoveRequest(BaseModel):
    resident_id: int
    destination: str


class ChatRequest(BaseModel):
    speaker_id: int
    listener_id: int
    message: str


class NegotiateRequest(BaseModel):
    initiator_id: int
    target_id: int
    topic: str
    proposal: str


class CollaborateRequest(BaseModel):
    leader_id: int
    member_ids: list[int] = Field(default_factory=list)
    title: str
    goal: str


class CompeteRequest(BaseModel):
    participant_ids: list[int]
    title: str
    metric: str = "综合表现"


class LongTermGoalRequest(BaseModel):
    resident_id: int
    title: str
    category: str = "general"
    deadline_day: Optional[int] = None


class GroupGoalRequest(BaseModel):
    name: str
    group_type: str = "临时小组"
    leader_id: int
    member_ids: list[int] = Field(default_factory=list)
    shared_goal: str
    deadline_day: Optional[int] = None
    current_plan: str = "成员根据分工推进任务，并在每日模拟后汇总进度。"


class BuySellRequest(BaseModel):
    buyer_id: int
    seller_id: int
    item_name: str
    quantity: int = Field(gt=0)
    unit_price: int = Field(gt=0)


class PolicyRequest(BaseModel):
    proposer_id: int
    title: str
    description: str


class VotePolicyRequest(BaseModel):
    resident_id: int
    policy_id: int
    vote: str


class CampusEnvironmentRequest(BaseModel):
    weather: Optional[str] = None
    semester_stage: Optional[str] = None
    time_slot: Optional[str] = None
    weekday: Optional[str] = None
    real_date: Optional[str] = None
    real_time: Optional[str] = None
    time_source: Optional[str] = None
    temperature: Optional[int] = Field(default=None, ge=-20, le=45)
    rainfall: Optional[int] = Field(default=None, ge=0, le=100)
    exam_pressure: Optional[int] = Field(default=None, ge=0, le=100)
    assignment_pressure: Optional[int] = Field(default=None, ge=0, le=100)
    study_atmosphere: Optional[int] = Field(default=None, ge=0, le=100)
    activity_heat: Optional[int] = Field(default=None, ge=0, le=100)
    event_name: Optional[str] = None
    event_intensity: Optional[int] = Field(default=None, ge=0, le=100)
    campus_flow: Optional[int] = Field(default=None, ge=0, le=100)
    classroom_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    canteen_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    library_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    dorm_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    playground_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    commercial_crowd: Optional[int] = Field(default=None, ge=0, le=100)
    traffic_status: Optional[str] = None
    network_status: Optional[str] = None
    safety_level: Optional[int] = Field(default=None, ge=0, le=100)
    resource_pressure: Optional[int] = Field(default=None, ge=0, le=100)
    campus_mood: Optional[str] = None
    consumption_index: Optional[float] = Field(default=None, ge=0.1, le=3.0)


class CampusEventRequest(BaseModel):
    title: str
    event_type: str = "校园活动"
    intensity: int = Field(default=50, ge=1, le=100)
    target_spaces: list[str] = Field(default_factory=list)
    effects: dict = Field(default_factory=dict)


class SpaceStatusRequest(BaseModel):
    status: str


class ObserverSessionRequest(BaseModel):
    session_id: Optional[int] = None
    user_id: str = "anonymous"
    session_type: str = "observer"
    focused_resident_id: Optional[int] = None
    focused_location: str = ""


class AdminWorldEventRequest(BaseModel):
    title: str
    content: str = ""
    event_type: str = "admin_event"
    resident_id: Optional[int] = None
    location: str = ""
    target_spaces: list[str] = Field(default_factory=list)
    intensity: int = Field(default=50, ge=1, le=100)
    payload: dict = Field(default_factory=dict)


class CalibrationObservationRequest(BaseModel):
    source_name: str = "manual"
    observed_at: str = ""
    metric_name: str
    metric_value: float
    location: str = ""
    role_group: str = ""
    sample_size: int = Field(default=0, ge=0)
    metadata: dict = Field(default_factory=dict)


class EnvironmentConfigRequest(BaseModel):
    config_key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    config: dict
    parent_config_id: Optional[int] = None
    created_by: str = Field(default="admin", max_length=80)
    activate: bool = False


class WorldSnapshotRequest(BaseModel):
    reason: str = Field(default="manual checkpoint", max_length=240)
    snapshot_type: str = Field(default="manual_checkpoint", max_length=60)
    run_id: str = Field(default="", max_length=120)
    branch_key: str = Field(default="", max_length=80)
    parent_snapshot_id: Optional[int] = None
    external_data_version: str = Field(default="", max_length=120)
    metadata: dict = Field(default_factory=dict)


class WorldSnapshotRestoreRequest(BaseModel):
    reason: str = Field(default="restore checkpoint", max_length=240)
    create_backup: bool = True


class WorldBranchRequest(BaseModel):
    branch_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    name: str = Field(default="", max_length=120)
    source_snapshot_id: int
    metadata: dict = Field(default_factory=dict)


class WorldBranchSwitchRequest(BaseModel):
    reason: str = Field(default="switch branch", max_length=240)


def row_to_dict(row):
    return dict(row) if row else None


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def ensure_campus_state_table(conn, *, allow_ddl=False):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(campus_state)").fetchall()}
    if not columns and allow_ddl:
        conn.executescript(CAMPUS_STATE_SQL)
    ensure_table_columns(
        conn,
        "campus_state",
        ENV_COLUMN_TYPES,
        allow_ddl=allow_ddl,
    )


def ensure_space_system(conn, *, allow_ddl=False):
    space_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(campus_spaces)").fetchall()
    }
    event_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(campus_events)").fetchall()
    }
    if (not space_columns or not event_columns) and allow_ddl:
        conn.executescript(SPACE_SYSTEM_SQL)
    ensure_table_columns(conn, "campus_spaces", {})
    ensure_table_columns(conn, "campus_events", {})
    existing_codes = {
        row["code"]
        for row in conn.execute("SELECT code FROM campus_spaces").fetchall()
    }
    for space in DEFAULT_SPACES:
        if space[0] in existing_codes:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO campus_spaces
            (code, name, location, capacity, open_hour, close_hour, status, crowd_field, purpose)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            space,
        )


def ensure_agent_news_system(conn, *, allow_ddl=False):
    if allow_ddl:
        conn.executescript(AGENT_NEWS_SQL)
    ensure_table_columns(
        conn,
        "agent_news_posts",
        AGENT_NEWS_COLUMN_TYPES,
        allow_ddl=allow_ddl,
    )


def ensure_external_information_system(conn, *, allow_ddl=False):
    if allow_ddl:
        conn.executescript(EXTERNAL_INFORMATION_SQL)
    ensure_table_columns(
        conn,
        "agent_information",
        AGENT_INFORMATION_COLUMNS,
        allow_ddl=allow_ddl,
    )


def ensure_world_runtime_tables(conn, *, allow_ddl=False):
    global WORLD_SCHEMA_READY
    if WORLD_SCHEMA_READY:
        return
    with WORLD_SCHEMA_LOCK:
        if WORLD_SCHEMA_READY:
            return
        ensure_social_system_tables(conn, allow_ddl=allow_ddl)
        if allow_ddl:
            conn.executescript(WORLD_RUNTIME_SQL)
            conn.executescript(RESEARCH_SYSTEM_SQL)
        ensure_table_columns(
            conn, "world_runtime", WORLD_RUNTIME_COLUMNS, allow_ddl=allow_ddl
        )
        ensure_table_columns(
            conn,
            "world_event_stream",
            WORLD_EVENT_STREAM_COLUMNS,
            allow_ddl=allow_ddl,
        )
        ensure_table_columns(
            conn, "world_snapshots", WORLD_SNAPSHOT_COLUMNS, allow_ddl=allow_ddl
        )
        ensure_table_columns(
            conn, "experiment_runs", EXPERIMENT_RUN_COLUMNS, allow_ddl=allow_ddl
        )
        conn.execute(
            """
            UPDATE world_event_stream
            SET root_event_id = id,
                occurred_at = CASE WHEN occurred_at = '' THEN created_at ELSE occurred_at END
            WHERE root_event_id IS NULL
            """
        )
        if allow_ddl:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_parent ON world_event_stream(parent_event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_root ON world_event_stream(root_event_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_source ON world_event_stream(source_type, source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_event_stream_branch ON world_event_stream(branch_key, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_world_snapshots_parent ON world_snapshots(parent_snapshot_id)")
        seed_world_runtime_rules(conn)
        seed_world_action_rules(conn)
        seed_world_update_schedules(conn)
        conn.execute(
            """
            INSERT OR IGNORE INTO world_resource_accounts
            (account_key, owner_type, resource_type, balance)
            VALUES ('campus-services', 'system', 'money', 0)
            """
        )
        default_config = seed_default_environment_config(conn)
        now = datetime.now(WORLD_TZ).isoformat()
        budget_date = now[:10]
        conn.execute(
            """
            INSERT OR IGNORE INTO world_runtime
            (id, status, world_timezone, world_time, budget_date)
            VALUES (?, 'paused', ?, ?, ?)
            """,
            (WORLD_RUNTIME_ID, WORLD_TIMEZONE, now, budget_date),
        )
        conn.execute(
            """
            UPDATE world_runtime
            SET daily_auto_model_budget = 500
            WHERE id = ? AND daily_auto_model_budget < 500
            """,
            (WORLD_RUNTIME_ID,),
        )
        conn.execute(
            """
            UPDATE world_runtime
            SET environment_config_id = COALESCE(environment_config_id, ?),
                environment_version = CASE WHEN environment_version = '' THEN ? ELSE environment_version END,
                random_seed = CASE WHEN random_seed = '' THEN 'campus-default-seed-v1' ELSE random_seed END
            WHERE id = ?
            """,
            (default_config["id"], environment_version_label(default_config), WORLD_RUNTIME_ID),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO world_branches
            (branch_key, name, status, metadata_json)
            VALUES ('main', '主世界', 'active', '{}')
            """
        )
        conn.execute(
            "UPDATE world_event_stream SET branch_key = 'main' WHERE branch_key = ''"
        )
        seed_agent_personality_traits(conn)
        WORLD_SCHEMA_READY = True


def seed_world_runtime_rules(conn):
    for rule in DEFAULT_SCHEDULE_RULES:
        conn.execute(
            """
            INSERT OR IGNORE INTO campus_schedule_rules
            (rule_key, role_group, action_type, location, start_hour, end_hour,
             weekday_pattern, min_exam_pressure, max_exam_pressure, base_weight, noise, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rule,
        )
    for weight in DEFAULT_CAUSAL_WEIGHTS:
        conn.execute(
            """
            INSERT OR IGNORE INTO world_causal_weights
            (weight_key, source_metric, target_type, target_key, direction, strength, threshold, noise, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            weight,
        )


def seed_world_action_rules(conn):
    for action_type, rule in DEFAULT_WORLD_ACTION_RULES.items():
        existing = conn.execute(
            """
            SELECT id FROM world_action_rules
            WHERE action_type = ? AND rule_version = 'action-rule-v1'
            ORDER BY id DESC LIMIT 1
            """,
            (action_type,),
        ).fetchone()
        if existing:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO world_action_rules
            (rule_key, action_type, rule_version, preconditions_json,
             required_resources_json, duration_minutes, success_probability,
             direct_effects_json, delayed_effects_json, failure_policy_json)
            VALUES (?, ?, 'action-rule-v1', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{action_type}-default",
                action_type,
                canonical_json(rule.get("preconditions", {})),
                canonical_json(rule.get("resources", {})),
                int(rule.get("duration_minutes", 10)),
                float(rule.get("success_probability", 1.0)),
                canonical_json(rule.get("direct_effects", [])),
                canonical_json(rule.get("delayed_effects", [])),
                canonical_json({"probability_failure_cost_ratio": 0.5}),
            ),
        )


def seed_world_update_schedules(conn):
    for schedule in DEFAULT_WORLD_UPDATE_SCHEDULES:
        conn.execute(
            """
            INSERT OR IGNORE INTO world_update_schedules
            (update_key, scope, cadence, interval_seconds, rule_version, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                schedule["update_key"],
                schedule["scope"],
                schedule["cadence"],
                schedule["interval_seconds"],
                schedule["rule_version"],
                canonical_json(schedule["metadata"]),
            ),
        )


def canonical_json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_checksum(value):
    text = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_environment_config():
    spaces = [
        {
            "code": code,
            "name": name,
            "location": location,
            "capacity": capacity,
            "open_hour": open_hour,
            "close_hour": close_hour,
            "status": status,
            "crowd_field": crowd_field,
            "purpose": purpose,
        }
        for code, name, location, capacity, open_hour, close_hour, status, crowd_field, purpose in DEFAULT_SPACES
    ]
    return {
        "schema_version": "environment-config-v1",
        "campus": {
            "key": "campus-default",
            "name": "默认校园平行世界",
            "school_type": "综合校园",
            "timezone": WORLD_TIMEZONE,
            "semester_system": "term",
        },
        "spaces": spaces,
        "population": {
            "initial_size": 20,
            "role_mix": {"student": 0.70, "teacher": 0.10, "business": 0.10, "service": 0.10},
            "generation_mode": "seeded_profiles",
        },
        "institutions": {
            "access_policy": "campus-default-v1",
            "schedule_rule_version": "campus-schedule-v1",
            "organizations": ["教学系统", "校务系统", "学生社团", "校园商业"],
        },
        "economy": {
            "currency": "campus_credit",
            "price_baseline": 1.0,
            "resource_abundance": 0.65,
            "ledger_mode": "legacy-transactions",
        },
        "external_context": {
            "city": "成都",
            "culture": "campus-local",
            "policy_context": "baseline",
            "external_data_mode": "live",
        },
        "environment_baseline": {
            key: value
            for key, value in DEFAULT_ENV.items()
            if key not in {"real_date", "real_time", "weather_observed_at"}
        },
        "rules": {
            "world_rule_version": "world-runtime-v1",
            "causal_weight_version": "causal-weights-v1",
            "action_taxonomy": "world-runtime-v3",
        },
    }


def validate_environment_config(config):
    if not isinstance(config, dict):
        raise ValueError("环境配置必须是 JSON 对象")
    required = {"campus", "spaces", "population", "institutions", "economy", "external_context"}
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"环境配置缺少字段：{', '.join(missing)}")
    for section in required - {"spaces"}:
        if not isinstance(config[section], dict):
            raise ValueError(f"环境配置字段 {section} 必须是对象")
    if not isinstance(config["spaces"], list) or not config["spaces"]:
        raise ValueError("环境配置至少需要一个空间")
    codes = set()
    locations = set()
    for space in config["spaces"]:
        if not isinstance(space, dict):
            raise ValueError("空间配置必须是对象")
        code = str(space.get("code") or "").strip()
        location = str(space.get("location") or "").strip()
        if not code or not location:
            raise ValueError("每个空间必须包含 code 和 location")
        if code in codes or location in locations:
            raise ValueError("空间 code 和 location 必须唯一")
        codes.add(code)
        locations.add(location)
        try:
            capacity = int(space.get("capacity"))
            open_hour = int(space.get("open_hour"))
            close_hour = int(space.get("close_hour"))
        except (TypeError, ValueError) as exc:
            raise ValueError("空间容量和开放时间必须是整数") from exc
        if capacity <= 0 or not 0 <= open_hour <= 24 or not 0 <= close_hour <= 24:
            raise ValueError("空间容量必须大于 0，开放时间必须在 0-24 之间")
    expected_locations = set(VALID_LOCATIONS)
    if locations != expected_locations:
        missing_locations = sorted(expected_locations - locations)
        unsupported_locations = sorted(locations - expected_locations)
        details = []
        if missing_locations:
            details.append(f"缺少地点：{', '.join(missing_locations)}")
        if unsupported_locations:
            details.append(f"当前 runtime 尚不支持：{', '.join(unsupported_locations)}")
        raise ValueError("当前环境配置必须覆盖七个 runtime 地点；" + "；".join(details))
    baseline = config.get("environment_baseline", {})
    if baseline is not None and not isinstance(baseline, dict):
        raise ValueError("environment_baseline 必须是对象")
    unknown_baseline = sorted(set(baseline or {}) - set(ENV_COLUMN_TYPES))
    if unknown_baseline:
        raise ValueError(f"environment_baseline 包含未知字段：{', '.join(unknown_baseline)}")
    return config


def environment_version_label(config_row):
    return f"{config_row['config_key']}@{config_row['version']}:{config_row['checksum'][:12]}"


def decode_environment_config(row):
    item = dict(row)
    item["config"] = load_json_text(item.pop("config_json", "{}"), {})
    item["version_label"] = environment_version_label(item)
    return item


def seed_default_environment_config(conn):
    config = default_environment_config()
    checksum = content_checksum(config)
    conn.execute(
        """
        INSERT OR IGNORE INTO environment_configs
        (config_key, name, version, status, config_json, checksum, created_by)
        VALUES ('campus-default', '默认校园平行世界', 1, 'active', ?, ?, 'system')
        """,
        (canonical_json(config), checksum),
    )
    row = conn.execute(
        """
        SELECT * FROM environment_configs
        WHERE config_key = 'campus-default' AND version = 1
        """
    ).fetchone()
    return dict(row)


def get_active_environment_config(conn):
    row = conn.execute(
        """
        SELECT c.*
        FROM environment_configs c
        JOIN world_runtime w ON w.environment_config_id = c.id
        WHERE w.id = ?
        """,
        (WORLD_RUNTIME_ID,),
    ).fetchone()
    if not row:
        row = conn.execute(
            "SELECT * FROM environment_configs WHERE status = 'active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return decode_environment_config(row) if row else None


def create_environment_config_record(conn, config_key, name, config, parent_config_id=None, created_by="admin"):
    config_key = str(config_key or "").strip()
    name = str(name or "").strip()
    if not config_key or not name:
        raise ValueError("环境配置 key 和名称不能为空")
    validate_environment_config(config)
    if parent_config_id:
        parent = conn.execute("SELECT id FROM environment_configs WHERE id = ?", (parent_config_id,)).fetchone()
        if not parent:
            raise ValueError("父环境配置不存在")
    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS value FROM environment_configs WHERE config_key = ?",
        (config_key,),
    ).fetchone()
    version = int(row["value"] or 0) + 1
    checksum = content_checksum(config)
    cursor = conn.execute(
        """
        INSERT INTO environment_configs
        (config_key, name, version, parent_config_id, status, config_json, checksum, created_by)
        VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)
        """,
        (
            config_key,
            name,
            version,
            parent_config_id,
            canonical_json(config),
            checksum,
            created_by,
        ),
    )
    return decode_environment_config(
        conn.execute("SELECT * FROM environment_configs WHERE id = ?", (cursor.lastrowid,)).fetchone()
    )


def apply_environment_config(conn, config_row):
    config = load_json_text(config_row["config_json"], {})
    validate_environment_config(config)
    ensure_campus_state_table(conn)
    ensure_space_system(conn)
    for space in config["spaces"]:
        conn.execute(
            """
            INSERT INTO campus_spaces
            (code, name, location, capacity, open_hour, close_hour, status, crowd_field, purpose)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                location = excluded.location,
                capacity = excluded.capacity,
                open_hour = excluded.open_hour,
                close_hour = excluded.close_hour,
                status = excluded.status,
                crowd_field = excluded.crowd_field,
                purpose = excluded.purpose,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                space["code"],
                space.get("name") or space["location"],
                space["location"],
                int(space["capacity"]),
                int(space["open_hour"]),
                int(space["close_hour"]),
                space.get("status") or "开放",
                space.get("crowd_field") or "campus_flow",
                space.get("purpose") or "",
            ),
        )
    baseline = config.get("environment_baseline")
    applied_baseline = []
    if isinstance(baseline, dict):
        allowed = sorted(set(baseline) & set(ENV_COLUMN_TYPES) - {"real_date", "real_time", "time_source"})
        if allowed:
            day = get_current_day(conn)
            get_campus_environment(conn, day)
            set_clause = ", ".join(f"{key} = ?" for key in allowed)
            conn.execute(
                f"UPDATE campus_state SET {set_clause} WHERE day = ?",
                [baseline[key] for key in allowed] + [day],
            )
            applied_baseline = allowed
    conn.execute("UPDATE environment_configs SET status = 'archived' WHERE status = 'active'")
    conn.execute("UPDATE environment_configs SET status = 'active' WHERE id = ?", (config_row["id"],))
    version_label = environment_version_label(config_row)
    conn.execute(
        """
        UPDATE world_runtime
        SET environment_config_id = ?, environment_version = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (config_row["id"], version_label, WORLD_RUNTIME_ID),
    )
    return {"spaces": len(config["spaces"]), "baseline_fields": applied_baseline}


def seed_agent_personality_traits(conn):
    try:
        rows = conn.execute("SELECT resident_id, strategy FROM agent_profiles").fetchall()
    except Exception:
        return
    for row in rows:
        resident_id = int(row["resident_id"])
        traits = DEFAULT_AGENT_PERSONALITY_TRAITS.get(resident_id)
        if not traits:
            continue
        strategy = load_json_text(row["strategy"], {})
        if not isinstance(strategy, dict):
            strategy = {}
        existing = strategy.get("personality_traits")
        if isinstance(existing, dict) and all(key in existing for key in traits):
            continue
        strategy["personality_traits"] = {**traits, **(existing if isinstance(existing, dict) else {})}
        strategy["personality_version"] = "structured-v1"
        conn.execute(
            """
            UPDATE agent_profiles
            SET strategy = ?
            WHERE resident_id = ?
            """,
            (json_dumps(strategy, ensure_ascii=False), resident_id),
        )


def previous_completed_world_window(world_time):
    return get_previous_completed_world_window(
        world_time,
        window_seconds=WORLD_CAMPUS_NEWS_WINDOW_SECONDS,
    )


def get_world_runtime(conn):
    ensure_world_runtime_tables(conn)
    now = get_world_now()
    budget_date = now.strftime("%Y-%m-%d")
    row = conn.execute("SELECT * FROM world_runtime WHERE id = ?", (WORLD_RUNTIME_ID,)).fetchone()
    if row and row["budget_date"] != budget_date:
        conn.execute(
            """
            UPDATE world_runtime
            SET auto_model_calls_used = 0, budget_date = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (budget_date, WORLD_RUNTIME_ID),
        )
    conn.execute(
        "UPDATE world_runtime SET world_time = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (now.isoformat(), WORLD_RUNTIME_ID),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM world_runtime WHERE id = ?", (WORLD_RUNTIME_ID,)).fetchone())


def read_world_runtime(conn):
    """Read runtime state without refreshing timestamps or taking a row lock."""
    row = conn.execute(
        "SELECT * FROM world_runtime WHERE id = ?",
        (WORLD_RUNTIME_ID,),
    ).fetchone()
    if row is None:
        raise RuntimeError("world_runtime is not initialized")
    return dict(row)


def active_world_branch_key(conn):
    row = conn.execute(
        "SELECT active_branch_key FROM world_runtime WHERE id = ?",
        (WORLD_RUNTIME_ID,),
    ).fetchone()
    return (row["active_branch_key"] if row else "") or "main"


def update_world_runtime_status(conn, status):
    ensure_world_runtime_tables(conn)
    now = get_world_now().isoformat()
    conn.execute(
        """
        UPDATE world_runtime
        SET status = ?, world_time = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, now, WORLD_RUNTIME_ID),
    )
    conn.commit()
    return read_world_runtime(conn)


def _world_event_json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def append_world_event(
    conn,
    event_type,
    title,
    content,
    tick_id=None,
    resident_id=None,
    location="",
    payload=None,
    day=None,
    slot=None,
    ensure_schema=True,
    source_type="runtime",
    source_id="",
    parent_event_id=None,
    root_event_id=None,
    rule_version="world-runtime-v1",
    occurred_at=None,
    branch_key=None,
):
    if ensure_schema:
        ensure_world_runtime_tables(conn)
    now = get_world_now()
    day = day or get_current_day(conn)
    slot = slot or world_slot_from_hour(now.hour)
    if parent_event_id:
        parent = conn.execute(
            "SELECT id, root_event_id, branch_key FROM world_event_stream WHERE id = ?",
            (parent_event_id,),
        ).fetchone()
        if not parent:
            raise ValueError("parent_event_id 不存在")
        root_event_id = root_event_id or parent["root_event_id"] or parent["id"]
        branch_key = branch_key or parent["branch_key"]
    if not branch_key:
        runtime_row = conn.execute(
            "SELECT active_branch_key FROM world_runtime WHERE id = ?",
            (WORLD_RUNTIME_ID,),
        ).fetchone()
        branch_key = runtime_row["active_branch_key"] if runtime_row else "main"
    cursor = conn.execute(
        """
        INSERT INTO world_event_stream
        (tick_id, day, slot, event_type, resident_id, location, title, content, payload,
         source_type, source_id, parent_event_id, root_event_id, rule_version, occurred_at,
         branch_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tick_id,
            day,
            slot,
            event_type,
            resident_id,
            location or "",
            title,
            content,
            json_dumps(payload or {}, ensure_ascii=False, default=_world_event_json_default),
            source_type or "runtime",
            str(source_id or ""),
            parent_event_id,
            root_event_id,
            rule_version or "world-runtime-v1",
            occurred_at or now.isoformat(),
            branch_key or "main",
        ),
    )
    event_id = cursor.lastrowid
    if not root_event_id:
        root_event_id = event_id
        conn.execute(
            "UPDATE world_event_stream SET root_event_id = ? WHERE id = ?",
            (event_id, event_id),
        )
    return dict(conn.execute("SELECT * FROM world_event_stream WHERE id = ?", (event_id,)).fetchone())


def decode_world_action_rule(row):
    rule = dict(row)
    for source_key, target_key, fallback in (
        ("preconditions_json", "preconditions", {}),
        ("required_resources_json", "required_resources", {}),
        ("direct_effects_json", "direct_effects", []),
        ("delayed_effects_json", "delayed_effects", []),
        ("failure_policy_json", "failure_policy", {}),
    ):
        rule[target_key] = load_json_text(rule.pop(source_key, ""), fallback)
    return rule


def get_world_action_rule(conn, action_type):
    row = conn.execute(
        """
        SELECT * FROM world_action_rules
        WHERE action_type = ? AND status = 'active'
        ORDER BY id DESC LIMIT 1
        """,
        (action_type,),
    ).fetchone()
    return decode_world_action_rule(row) if row else None


def action_resource_state(conn, resident_id):
    row = conn.execute(
        """
        SELECT r.money, p.energy, p.time_budget, p.mood
        FROM residents r
        JOIN agent_profiles p ON p.resident_id = r.id
        WHERE r.id = ?
        """,
        (resident_id,),
    ).fetchone()
    if not row:
        raise ValueError("行动者不存在或缺少 Agent profile")
    return {
        "energy": int(row["energy"] if row["energy"] is not None else 80),
        "time_budget": int(row["time_budget"] if row["time_budget"] is not None else 100),
        "money": int(row["money"] if row["money"] is not None else 0),
        "mood": row["mood"] or "平稳",
    }


def deterministic_action_roll(conn, tick_id, resident_id, action_type, location):
    runtime = conn.execute(
        "SELECT random_seed FROM world_runtime WHERE id = ?",
        (WORLD_RUNTIME_ID,),
    ).fetchone()
    seed = runtime["random_seed"] if runtime else "campus-default-seed-v1"
    material = f"{seed}|{tick_id or 0}|{resident_id}|{action_type}|{location}"
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


def evaluate_world_action_preconditions(conn, resident_id, action_type, location, rule, world_time):
    preconditions = rule.get("preconditions", {})
    resources = rule.get("required_resources", {})
    state = action_resource_state(conn, resident_id)
    checks = []
    budget_choice = None
    market_choice = None
    body_state = get_body_state(conn, resident_id) or {}
    critical_recovery = (
        action_type == "consume"
        and float(body_state.get("hunger") or 0) >= 90
    ) or (
        action_type == "rest"
        and (
            float(body_state.get("fatigue") or 0) >= 88
            or float(body_state.get("sleep_debt") or 0) >= 85
            or float(body_state.get("health", 100) if body_state.get("health") is not None else 100) < 35
            or float(body_state.get("attention", 100) if body_state.get("attention") is not None else 100) < 15
        )
    )

    def add_check(key, passed, actual, required, failure_code, reason):
        checks.append(
            {
                "key": key,
                "passed": bool(passed),
                "actual": actual,
                "required": required,
                "failure_code": "" if passed else failure_code,
                "reason": "" if passed else reason,
            }
        )

    allowed_locations = preconditions.get("allowed_locations")
    if allowed_locations:
        add_check(
            "allowed_location",
            location in allowed_locations,
            location,
            allowed_locations,
            "location_mismatch",
            f"{action_type} 不能在{location}完成",
        )
    if preconditions.get("location_open"):
        add_check(
            "location_open",
            location in VALID_LOCATIONS and is_location_open_at_hour(location, world_time.hour),
            location,
            "open",
            "location_closed",
            f"{location}当前未开放",
        )
    if preconditions.get("capacity_available") and location in VALID_LOCATIONS:
        snapshot = get_space_snapshot(conn)
        space = next((item for item in snapshot["spaces"] if item["location"] == location), None)
        available = int(space.get("available_slots", 0)) if space else 0
        add_check(
            "capacity_available",
            bool(space) and available > 0 and space.get("effective_status") != "关闭",
            available,
            "> 0",
            "space_full",
            f"{location}当前没有可用容量",
        )
    if action_type != "move" and location in VALID_LOCATIONS:
        resource = check_action_resource(conn, location, action_type)
        if resource["required"]:
            add_check(
                "spatial_resource_available",
                resource["available"],
                resource,
                "> 0 available units",
                "resource_unavailable",
                (
                    f"{location}的{resource['resource_name']}当前不可用，"
                    f"预计等待 {resource['estimated_wait_minutes']} 分钟"
                ),
            )
    if action_type == "consume" and rule.get("rule_key") != "passive-runtime-poll":
        supply = consumption_availability(conn, location)
        if supply.get("managed"):
            add_check(
                "supply_available",
                supply["available"],
                supply,
                "> 0 saleable units",
                "goods_out_of_stock",
                f"{location}当前可消费商品缺货",
            )
            if (
                supply["available"]
                and market_runtime_available(conn)
            ):
                mechanism = find_market_mechanism(
                    conn,
                    item_name=supply["item_name"],
                    provider_actor_key=supply["provider_actor_key"],
                    location=location,
                )
                if mechanism:
                    market_choice = evaluate_market_choice(
                        conn,
                        resident_id=resident_id,
                        mechanism_id=int(mechanism["id"]),
                        action_type=action_type,
                        world_time=world_time,
                    )
                    market_allowed = market_choice["status"] == "accepted"
                    add_check(
                        "market_offer",
                        market_allowed,
                        market_choice,
                        "accepted market offer",
                        f"market_{market_choice['status']}",
                        (
                            market_choice["reason"]
                            + (
                                f"，可替代为{market_choice['substitute']['item_name']}"
                                if market_choice.get("substitute")
                                else ""
                            )
                        ),
                    )
                    resources["money"] = int(
                        math.ceil(
                            market_choice["total_unit_cost_minor"]
                            * market_choice["quantity"]
                            / 100
                        )
                    )
    if rule.get("rule_key") != "passive-runtime-poll":
        checks.extend(body_action_checks(conn, resident_id, action_type))
        checks.extend(capability_action_checks(conn, resident_id, action_type))
        if budget_runtime_available(conn):
            profile = conn.execute(
                "SELECT resident_id FROM household_budget_profiles WHERE resident_id = ?",
                (resident_id,),
            ).fetchone()
            if profile:
                budget_choice = evaluate_action_choice(
                    conn,
                    resident_id=resident_id,
                    action_type=action_type,
                    location=location,
                    required_money_minor=int(resources.get("money", 0) or 0) * 100,
                    required_time_minutes=int(rule.get("duration_minutes", 0) or 0),
                    world_time=world_time,
                )
                add_check(
                    "budget_disposable",
                    budget_choice["decision"] != "rejected",
                    budget_choice["disposable_minor"],
                    budget_choice["required_money_minor"],
                    "insufficient_disposable_budget",
                    budget_choice["rationale"],
                )
                add_check(
                    "budget_free_time",
                    budget_choice["decision"] != "deferred" or critical_recovery,
                    budget_choice["free_time_minutes"],
                    budget_choice["required_time_minutes"],
                    "insufficient_free_time",
                    (
                        "关键生理恢复行动允许越过当日自由时间门槛"
                        if budget_choice["decision"] == "deferred" and critical_recovery
                        else budget_choice["rationale"]
                    ),
                )
    for resource_key in ("energy", "time_budget", "money"):
        required = int(resources.get(resource_key, 0) or 0)
        available = state[resource_key] >= required
        if (
            resource_key == "money"
            and budget_choice
            and budget_choice["emergency_override"]
        ):
            available = True
        if resource_key == "time_budget" and critical_recovery:
            available = True
        add_check(
            f"resource_{resource_key}",
            available,
            state[resource_key],
            required,
            f"insufficient_{resource_key}",
            f"{resource_key}不足，需要 {required}，当前 {state[resource_key]}",
        )
    if budget_choice:
        state = {**state, "budget_choice": budget_choice}
    if market_choice:
        state = {**state, "market_choice": market_choice}
    return checks, state


def begin_world_action_execution(
    conn,
    resident_id,
    action_type,
    location,
    world_time,
    tick_id=None,
    parent_event_id=None,
    settlement_mode="active",
):
    rule = get_world_action_rule(conn, action_type)
    if not rule:
        raise ValueError(f"未找到行动规则：{action_type}")
    if settlement_mode == "passive":
        rule = {
            **rule,
            "rule_key": "passive-runtime-poll",
            "rule_version": "passive-tick-v1",
            "preconditions": {},
            "required_resources": {"energy": 0, "time_budget": 0, "money": 0},
            "duration_minutes": 0,
            "success_probability": 1.0,
            "direct_effects": [],
            "delayed_effects": [],
        }
    else:
        rule = individualize_action_rule(conn, resident_id, rule, action_type)
    checks, resources_before = evaluate_world_action_preconditions(
        conn, resident_id, action_type, location, rule, world_time
    )
    if rule.get("individualization"):
        resources_before = {
            **resources_before,
            "capability_adjustment": rule["individualization"],
        }
    failed_check = next((check for check in checks if not check["passed"]), None)
    roll = deterministic_action_roll(conn, tick_id, resident_id, action_type, location)
    probability = float(rule.get("success_probability", 1.0))
    status = "rejected" if failed_check else ("pending" if roll <= probability else "failed")
    failure_code = failed_check["failure_code"] if failed_check else ("probability_failure" if status == "failed" else "")
    failure_reason = failed_check["reason"] if failed_check else ("行动未通过成功概率结算" if status == "failed" else "")
    cursor = conn.execute(
        """
        INSERT INTO world_action_executions
        (tick_id, resident_id, action_type, target_type, target_id, location, status, settlement_mode,
         rule_key, rule_version, precondition_results_json, resources_before_json,
         resource_costs_json, duration_minutes, success_probability, random_roll,
         direct_effects_json, failure_code, failure_reason, parent_event_id, occurred_at)
        VALUES (?, ?, ?, 'location', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tick_id,
            resident_id,
            action_type,
            location,
            location,
            status,
            settlement_mode,
            rule["rule_key"],
            rule["rule_version"],
            canonical_json(checks),
            canonical_json(resources_before),
            canonical_json(rule.get("required_resources", {})),
            int(rule.get("duration_minutes", 0)),
            probability,
            roll,
            canonical_json(rule.get("direct_effects", [])),
            failure_code,
            failure_reason,
            parent_event_id,
            world_time.isoformat(),
        ),
    )
    if resources_before.get("budget_choice"):
        record_action_choice(
            conn,
            action_execution_id=cursor.lastrowid,
            resident_id=resident_id,
            action_type=action_type,
            location=location,
            evaluation=resources_before["budget_choice"],
            world_time=world_time,
        )
    if resources_before.get("market_choice"):
        record_market_demand(
            conn,
            action_execution_id=cursor.lastrowid,
            resident_id=resident_id,
            evaluation=resources_before["market_choice"],
            world_time=world_time,
        )
    return {
        "id": cursor.lastrowid,
        "status": status,
        "failure_code": failure_code,
        "failure_reason": failure_reason,
        "preconditions": checks,
        "resources_before": resources_before,
        "rule": rule,
        "random_roll": roll,
        "settlement_mode": settlement_mode,
    }


def apply_structured_world_effect(conn, resident_id, effect):
    target_type = effect.get("target_type")
    state_key = effect.get("state_key")
    operation = effect.get("operation", "add")
    value = effect.get("value")
    if target_type == "agent_profile":
        if state_key == "energy" and operation == "add":
            row = conn.execute(
                "SELECT energy FROM agent_profiles WHERE resident_id = ?",
                (resident_id,),
            ).fetchone()
            before = int(row["energy"])
            after = clamp(before + int(value))
            conn.execute(
                "UPDATE agent_profiles SET energy = ? WHERE resident_id = ?",
                (after, resident_id),
            )
        elif state_key == "mood" and operation == "set":
            row = conn.execute(
                "SELECT mood FROM agent_profiles WHERE resident_id = ?",
                (resident_id,),
            ).fetchone()
            before = row["mood"]
            after = str(value)[:40]
            conn.execute(
                "UPDATE agent_profiles SET mood = ? WHERE resident_id = ?",
                (after, resident_id),
            )
        else:
            raise ValueError(f"不支持的 Agent 效果：{state_key}/{operation}")
    elif target_type == "campus_state":
        allowed_numeric = set(ENV_COLUMN_TYPES) & {
            "exam_pressure", "assignment_pressure", "study_atmosphere", "activity_heat",
            "event_intensity", "campus_flow", "classroom_crowd", "canteen_crowd",
            "library_crowd", "dorm_crowd", "playground_crowd", "commercial_crowd",
            "safety_level", "resource_pressure", "consumption_index",
        }
        if state_key not in allowed_numeric or operation not in {"add", "set"}:
            raise ValueError(f"不支持的校园效果：{state_key}/{operation}")
        day = get_current_day(conn)
        state = conn.execute(
            f"SELECT {state_key} AS value FROM campus_state WHERE day = ?",
            (day,),
        ).fetchone()
        if not state:
            raise ValueError(f"第 {day} 天校园状态不存在")
        before = state["value"]
        raw_after = float(before) + float(value) if operation == "add" else float(value)
        if state_key == "consumption_index":
            after = round(max(0.1, min(3.0, raw_after)), 2)
        else:
            after = clamp(round(raw_after))
        conn.execute(
            f"UPDATE campus_state SET {state_key} = ? WHERE day = ?",
            (after, day),
        )
    else:
        raise ValueError(f"不支持的效果目标：{target_type}")
    return {
        "target_type": target_type,
        "state_key": state_key,
        "operation": operation,
        "before": before,
        "after": after,
    }


def settle_world_action_resources(conn, action_execution, success):
    resident_id = action_execution["resources_before"].get("resident_id")
    if not resident_id:
        row = conn.execute(
            "SELECT resident_id FROM world_action_executions WHERE id = ?",
            (action_execution["id"],),
        ).fetchone()
        resident_id = int(row["resident_id"])
    rule = action_execution["rule"]
    requested_costs = {
        key: int(rule.get("required_resources", {}).get(key, 0) or 0)
        for key in ("energy", "time_budget", "money")
    }
    ratio = 1.0 if success else float(rule.get("failure_policy", {}).get("probability_failure_cost_ratio", 0.5))
    costs = {
        key: min(value, max(0, round(value * ratio)))
        for key, value in requested_costs.items()
    }
    exact_money_minor = costs["money"] * 100
    if action_execution["resources_before"].get("market_choice"):
        market_choice = action_execution["resources_before"]["market_choice"]
        exact_money_minor = (
            int(market_choice["total_unit_cost_minor"])
            * int(market_choice["quantity"])
        )
    if costs["money"] and success and action_execution["resources_before"].get("budget_choice"):
        fund_emergency_action(
            conn,
            resident_id=resident_id,
            amount_minor=exact_money_minor,
            action_execution_id=action_execution["id"],
            evaluation=action_execution["resources_before"]["budget_choice"],
        )
    before = action_resource_state(conn, resident_id)
    conn.execute(
        """
        UPDATE agent_profiles
        SET energy = ?, time_budget = ?
        WHERE resident_id = ?
        """,
        (
            clamp(before["energy"] - costs["energy"]),
            clamp(before["time_budget"] - costs["time_budget"]),
            resident_id,
        ),
    )
    supply_settlement = None
    if (
        costs["money"]
        and success
        and rule["action_type"] == "consume"
        and supply_runtime_available(conn)
        and action_execution["resources_before"].get("market_choice")
    ):
        supply_settlement = fulfill_market_goods_trade(
            conn,
            resident_id=resident_id,
            evaluation=action_execution["resources_before"]["market_choice"],
            action_execution_id=action_execution["id"],
        )
        ledger_transaction = {"id": supply_settlement["ledger_transaction_id"]}
        transfer_target = supply_settlement["provider_actor_key"]
    elif (
        costs["money"]
        and success
        and rule["action_type"] == "consume"
        and supply_runtime_available(conn)
    ):
        execution_row = conn.execute(
            "SELECT location FROM world_action_executions WHERE id = ?",
            (action_execution["id"],),
        ).fetchone()
        supply_settlement = fulfill_runtime_consumption(
            conn,
            resident_id,
            execution_row["location"],
            costs["money"] * 100,
            action_execution["id"],
        )
        ledger_transaction = {"id": supply_settlement["ledger_transaction_id"]}
        transfer_target = supply_settlement["provider_actor_key"]
    elif costs["money"]:
        ledger_transaction = post_money_transfer(
            conn,
            transaction_key=f"action-cost:{action_execution['id']}:money",
            from_account_key=f"resident:{resident_id}:cash",
            to_account_key="system:campus-services:cash",
            amount_coins=costs["money"],
            transaction_type="action_resource_cost",
            source_type="world_action_execution",
            source_id=str(action_execution["id"]),
            action_execution_id=action_execution["id"],
            description=f"{rule['action_type']} 行动资源成本",
            metadata={"success": bool(success)},
        )
        transfer_target = "campus-services"
    else:
        ledger_transaction = None
        transfer_target = ""
    if costs["money"]:
        conn.execute(
            """
            INSERT INTO world_resource_transfers
            (action_execution_id, from_type, from_id, to_account_key,
             resource_type, amount, reason)
            VALUES (?, 'resident', ?, ?, 'money', ?, ?)
            """,
            (
                action_execution["id"],
                str(resident_id),
                transfer_target,
                costs["money"],
                f"{rule['action_type']} 行动资源成本",
            ),
        )
    applied_effects = []
    if success:
        for effect in rule.get("direct_effects", []):
            applied_effects.append(apply_structured_world_effect(conn, resident_id, effect))
    body_effects = None
    if action_execution.get("settlement_mode") != "passive":
        body_effects = apply_action_body_effects(
            conn,
            resident_id,
            rule["action_type"],
            success=success,
        )
        if body_effects:
            applied_effects.append(
                {
                    "target_type": "agent_body_state",
                    "state_key": "body",
                    "operation": "transition",
                    "before": body_effects["before"],
                    "after": body_effects["after"],
                }
            )
    after = action_resource_state(conn, resident_id)
    conn.execute(
        """
        UPDATE world_action_executions
        SET status = ?, resources_after_json = ?, resource_costs_json = ?,
            direct_effects_json = ?, completed_at = ?
        WHERE id = ?
        """,
        (
            "completed" if success else "failed",
            canonical_json(after),
            canonical_json(costs),
            canonical_json(applied_effects),
            get_world_now().isoformat(),
            action_execution["id"],
        ),
    )
    return {
        "before": before,
        "after": after,
        "costs": costs,
        "direct_effects": applied_effects,
        "body_effects": body_effects,
        "supply_settlement": supply_settlement,
        "ledger_transaction_id": (
            ledger_transaction["id"] if ledger_transaction else None
        ),
    }


def finalize_rejected_action_execution(conn, action_execution):
    conn.execute(
        """
        UPDATE world_action_executions
        SET resources_after_json = ?, resource_costs_json = '{}',
            direct_effects_json = '[]', completed_at = ?
        WHERE id = ?
        """,
        (
            canonical_json(action_execution["resources_before"]),
            get_world_now().isoformat(),
            action_execution["id"],
        ),
    )
    return {
        "before": action_execution["resources_before"],
        "after": action_execution["resources_before"],
        "costs": {"energy": 0, "time_budget": 0, "money": 0},
        "direct_effects": [],
    }


def enqueue_world_delayed_effects(conn, action_execution, source_event_id, world_time):
    effect_ids = []
    resident_id = conn.execute(
        "SELECT resident_id FROM world_action_executions WHERE id = ?",
        (action_execution["id"],),
    ).fetchone()["resident_id"]
    for effect in action_execution["rule"].get("delayed_effects", []):
        due_at = world_time + timedelta(minutes=max(0, int(effect.get("delay_minutes", 0))))
        target_type = effect.get("target_type") or "campus_state"
        target_id = effect.get("target_id")
        if target_id is None and target_type == "agent_profile":
            target_id = resident_id
        cursor = conn.execute(
            """
            INSERT INTO world_delayed_effects
            (source_action_execution_id, source_event_id, due_at, effect_type,
             target_type, target_id, state_key, operation, value_json, rule_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_execution["id"],
                source_event_id,
                due_at.isoformat(),
                f"{target_type}.{effect.get('state_key')}",
                target_type,
                str(target_id or ""),
                effect.get("state_key") or "",
                effect.get("operation") or "add",
                canonical_json(effect.get("value")),
                action_execution["rule"]["rule_version"],
            ),
        )
        effect_ids.append(cursor.lastrowid)
    conn.execute(
        "UPDATE world_action_executions SET delayed_effect_ids_json = ? WHERE id = ?",
        (canonical_json(effect_ids), action_execution["id"]),
    )
    return effect_ids


def link_action_execution_event(conn, action_execution_id, event_id):
    conn.execute(
        "UPDATE world_action_executions SET world_event_id = ? WHERE id = ?",
        (event_id, action_execution_id),
    )


def process_due_world_delayed_effects(conn, world_time, tick_id=None, day=None, slot=None, limit=100):
    rows = conn.execute(
        """
        SELECT d.*, a.resident_id
        FROM world_delayed_effects d
        LEFT JOIN world_action_executions a ON a.id = d.source_action_execution_id
        WHERE d.status = 'pending' AND d.due_at <= ?
        ORDER BY d.due_at, d.id
        LIMIT ?
        """,
        (world_time.isoformat(), limit),
    ).fetchall()
    if any(row["target_type"] == "campus_state" for row in rows):
        get_campus_environment(conn, day)
    applied = []
    failed = []
    for raw in rows:
        effect = dict(raw)
        conn.execute("SAVEPOINT delayed_effect_apply")
        try:
            value = load_json_text(effect["value_json"], None)
            result = apply_structured_world_effect(
                conn,
                int(effect.get("target_id") or effect.get("resident_id") or 0),
                {
                    "target_type": effect["target_type"],
                    "state_key": effect["state_key"],
                    "operation": effect["operation"],
                    "value": value,
                },
            )
            event = append_world_event(
                conn,
                "delayed_effect_applied",
                "延迟效果已结算",
                f"{effect['effect_type']} 已按计划生效。",
                tick_id=tick_id,
                resident_id=effect.get("resident_id"),
                payload={"delayed_effect_id": effect["id"], "result": result},
                day=day,
                slot=slot,
                source_type="delayed_effect",
                source_id=effect["id"],
                parent_event_id=effect.get("source_event_id"),
                rule_version=effect["rule_version"],
                occurred_at=world_time.isoformat(),
            )
            conn.execute(
                """
                UPDATE world_delayed_effects
                SET status = 'applied', attempts = attempts + 1,
                    applied_event_id = ?, applied_at = ?, last_error = ''
                WHERE id = ?
                """,
                (event["id"], world_time.isoformat(), effect["id"]),
            )
            conn.execute("RELEASE SAVEPOINT delayed_effect_apply")
            applied.append({"id": effect["id"], "event_id": event["id"], "result": result})
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT delayed_effect_apply")
            conn.execute("RELEASE SAVEPOINT delayed_effect_apply")
            attempts = int(effect.get("attempts") or 0) + 1
            conn.execute(
                """
                UPDATE world_delayed_effects
                SET status = ?, attempts = ?, last_error = ?
                WHERE id = ?
                """,
                ("failed" if attempts >= 3 else "pending", attempts, str(exc)[:240], effect["id"]),
            )
            failed.append({"id": effect["id"], "error": str(exc)})
    return {"due_count": len(rows), "applied": applied, "failed": failed}


def decode_world_update_schedule(row):
    item = dict(row)
    item["metadata"] = load_json_text(item.pop("metadata_json", "{}"), {})
    return item


def decode_world_update_run(row):
    item = dict(row)
    item["metrics"] = load_json_text(item.pop("metrics_json", "{}"), {})
    return item


def world_events_for_update(conn, after_id, through_id):
    branch_key = active_world_branch_key(conn)
    return [
        decode_world_event(row)
        for row in conn.execute(
            """
            SELECT * FROM world_event_stream
            WHERE id > ? AND id <= ? AND branch_key = ?
            ORDER BY id
            """,
            (after_id, through_id, branch_key),
        ).fetchall()
    ]


def aggregate_campus_space_activity(conn, events):
    residents_by_location = {
        row["location"]: int(row["count"])
        for row in conn.execute(
            "SELECT location, COUNT(*) AS count FROM residents GROUP BY location ORDER BY location"
        ).fetchall()
    }
    actions_by_type = {}
    actions_by_location = {}
    rejected_actions = 0
    for event in events:
        if event["event_type"] != "agent_tick":
            continue
        payload = event["payload"]
        action = str(payload.get("action") or "unknown")
        location = event.get("location") or "校园"
        actions_by_type[action] = actions_by_type.get(action, 0) + 1
        actions_by_location[location] = actions_by_location.get(location, 0) + 1
        if payload.get("action_success") is False or payload.get("failure_code"):
            rejected_actions += 1
    return {
        "resident_count": sum(residents_by_location.values()),
        "residents_by_location": residents_by_location,
        "action_count": sum(actions_by_type.values()),
        "actions_by_type": actions_by_type,
        "actions_by_location": actions_by_location,
        "rejected_action_count": rejected_actions,
    }


def aggregate_social_dynamics(conn, events):
    interactions = 0
    positive_effects = 0
    commitments_created = 0
    residents_involved = set()
    for event in events:
        payload = event["payload"]
        social_effect = payload.get("social_effect")
        if not isinstance(social_effect, dict):
            continue
        interactions += 1
        if social_effect.get("effect") == "positive":
            positive_effects += 1
        if social_effect.get("commitment"):
            commitments_created += 1
        if event.get("resident_id"):
            residents_involved.add(int(event["resident_id"]))
        if social_effect.get("target_id"):
            residents_involved.add(int(social_effect["target_id"]))
    relationship_summary = conn.execute(
        """
        SELECT COUNT(*) AS relationship_count,
               COALESCE(AVG(trust), 0) AS average_trust,
               COALESCE(AVG(cooperation), 0) AS average_cooperation,
               COALESCE(AVG(conflict), 0) AS average_conflict
        FROM relationship_dynamics
        """
    ).fetchone()
    return {
        "interaction_count": interactions,
        "positive_effect_count": positive_effects,
        "commitment_count": commitments_created,
        "residents_involved": sorted(residents_involved),
        "relationship_count": int(relationship_summary["relationship_count"] or 0),
        "average_trust": round(float(relationship_summary["average_trust"] or 0), 2),
        "average_cooperation": round(float(relationship_summary["average_cooperation"] or 0), 2),
        "average_conflict": round(float(relationship_summary["average_conflict"] or 0), 2),
    }


def aggregate_institutional_resources(conn, events):
    policy_counts = {
        row["status"]: int(row["count"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM policies GROUP BY status ORDER BY status"
        ).fetchall()
    }
    resource_accounts = {
        row["account_key"]: {
            "resource_type": row["resource_type"],
            "balance": float(row["balance"]),
        }
        for row in conn.execute(
            "SELECT account_key, resource_type, balance FROM world_resource_accounts ORDER BY account_key"
        ).fetchall()
    }
    pending_effects = conn.execute(
        "SELECT COUNT(*) AS count FROM world_delayed_effects WHERE status = 'pending'"
    ).fetchone()
    active_events = conn.execute(
        "SELECT COUNT(*) AS count FROM campus_events WHERE status = 'active'"
    ).fetchone()
    return {
        "policy_counts": policy_counts,
        "resource_accounts": resource_accounts,
        "pending_delayed_effects": int(pending_effects["count"] or 0),
        "active_campus_events": int(active_events["count"] or 0),
        "source_event_count": len(events),
    }


WORLD_UPDATE_HANDLERS = {
    "campus_space_activity": aggregate_campus_space_activity,
    "social_dynamics": aggregate_social_dynamics,
    "institutional_resource_review": aggregate_institutional_resources,
}


def run_due_world_updates(conn, world_time, tick_id, day, slot, parent_event_id=None):
    ensure_world_runtime_tables(conn)
    schedule_rows = conn.execute(
        """
        SELECT * FROM world_update_schedules
        WHERE status = 'active'
        ORDER BY interval_seconds, id
        """
    ).fetchall()
    schedules = []
    seen_update_keys = set()
    for schedule_row in schedule_rows:
        schedule = dict(schedule_row)
        update_key = schedule["update_key"]
        if update_key in seen_update_keys:
            continue
        seen_update_keys.add(update_key)
        schedules.append(schedule)
    event_cursor_row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS value FROM world_event_stream"
    ).fetchone()
    input_event_cursor = int(event_cursor_row["value"] or 0)
    completed = []
    failed = []
    for schedule in schedules:
        next_run_at = parse_world_datetime(schedule["next_run_at"])
        if next_run_at and next_run_at > world_time:
            continue
        handler = WORLD_UPDATE_HANDLERS.get(schedule["update_key"])
        if not handler:
            continue
        run_cursor = conn.execute(
            """
            INSERT INTO world_update_runs
            (schedule_id, tick_id, update_key, scheduled_for, input_event_cursor)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                schedule["id"],
                tick_id,
                schedule["update_key"],
                world_time.isoformat(),
                input_event_cursor,
            ),
        )
        run_id = run_cursor.lastrowid
        conn.execute("SAVEPOINT world_update_run")
        try:
            events = world_events_for_update(
                conn,
                int(schedule.get("last_event_cursor") or 0),
                input_event_cursor,
            )
            metrics = handler(conn, events)
            metrics["event_window"] = {
                "after_id": int(schedule.get("last_event_cursor") or 0),
                "through_id": input_event_cursor,
            }
            update_event = append_world_event(
                conn,
                "world_multiscale_update",
                f"{schedule['scope']} 多尺度更新完成",
                f"{schedule['cadence']} 更新《{schedule['update_key']}》已从底层状态与事件完成聚合。",
                tick_id=tick_id,
                payload={
                    "run_id": run_id,
                    "update_key": schedule["update_key"],
                    "scope": schedule["scope"],
                    "cadence": schedule["cadence"],
                    "metrics": metrics,
                },
                day=day,
                slot=slot,
                source_type="world_update_run",
                source_id=run_id,
                parent_event_id=parent_event_id,
                rule_version=schedule["rule_version"],
            )
            completed_at = world_time.isoformat()
            next_due_at = (
                world_time + timedelta(seconds=int(schedule["interval_seconds"]))
            ).isoformat()
            conn.execute(
                """
                UPDATE world_update_runs
                SET output_event_id = ?, status = 'completed', metrics_json = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (update_event["id"], canonical_json(metrics), completed_at, run_id),
            )
            conn.execute(
                """
                UPDATE world_update_schedules
                SET last_run_at = ?, next_run_at = ?, last_event_cursor = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (completed_at, next_due_at, input_event_cursor, schedule["id"]),
            )
            conn.execute("RELEASE SAVEPOINT world_update_run")
            completed.append(
                decode_world_update_run(
                    conn.execute(
                        "SELECT * FROM world_update_runs WHERE id = ?", (run_id,)
                    ).fetchone()
                )
            )
        except Exception as exc:
            conn.execute("ROLLBACK TO SAVEPOINT world_update_run")
            conn.execute("RELEASE SAVEPOINT world_update_run")
            retry_at = (
                world_time
                + timedelta(seconds=min(int(schedule["interval_seconds"]), 300))
            ).isoformat()
            conn.execute(
                """
                UPDATE world_update_runs
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (str(exc)[:500], world_time.isoformat(), run_id),
            )
            conn.execute(
                """
                UPDATE world_update_schedules
                SET next_run_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (retry_at, schedule["id"]),
            )
            failed.append({"run_id": run_id, "update_key": schedule["update_key"], "error": str(exc)})
    return {"due_count": len(completed) + len(failed), "completed": completed, "failed": failed}


def get_recent_observer_focus(conn, minutes=10):
    ensure_world_runtime_tables(conn)
    cutoff = (get_world_now() - timedelta(minutes=minutes)).isoformat()
    rows = conn.execute(
        """
        SELECT focused_resident_id, focused_location
        FROM observer_sessions
        WHERE last_seen_at >= ?
        ORDER BY last_seen_at DESC
        LIMIT 12
        """,
        (cutoff,),
    ).fetchall()
    focused_agents = [int(row["focused_resident_id"]) for row in rows if row["focused_resident_id"]]
    focused_locations = [row["focused_location"] for row in rows if row["focused_location"]]
    return focused_agents, focused_locations


def log_model_call(conn, trigger_type, status="logged", resident_id=None, related_event_id=None, model_name="", prompt_version="world-runtime-v1", input_tokens=0, output_tokens=0, estimated_cost=0):
    ensure_world_runtime_tables(conn)
    cursor = conn.execute(
        """
        INSERT INTO model_call_logs
        (trigger_type, resident_id, related_event_id, model_name, prompt_version, input_tokens, output_tokens, estimated_cost, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (trigger_type, resident_id, related_event_id, model_name, prompt_version, input_tokens, output_tokens, estimated_cost, status),
    )
    return cursor.lastrowid


def consume_auto_model_budget(conn, trigger_type, resident_id=None):
    if not is_llm_configured():
        log_model_call(conn, trigger_type, status="skipped:llm_unconfigured", resident_id=resident_id)
        return False
    now = get_world_now()
    budget_date = now.strftime("%Y-%m-%d")

    def reserve_budget(budget_conn):
        cursor = budget_conn.execute(
            """
            UPDATE world_runtime
            SET auto_model_calls_used = CASE
                    WHEN budget_date <> ? THEN 1
                    ELSE auto_model_calls_used + 1
                END,
                budget_date = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND (budget_date <> ? OR auto_model_calls_used < daily_auto_model_budget)
            RETURNING auto_model_calls_used
            """,
            (budget_date, budget_date, WORLD_RUNTIME_ID, budget_date),
        )
        return cursor.fetchone() is not None

    reserved = reserve_budget(conn)
    if not reserved:
        log_model_call(conn, trigger_type, status="budget_exhausted", resident_id=resident_id)
        return False
    return True


def role_group(role):
    text = str(role or "")
    if "老师" in text:
        return "teacher"
    if "商" in text or "老板" in text:
        return "business"
    if "后勤" in text or "管理" in text:
        return "service"
    return "student"


def space_hours_by_location():
    return {
        location: {"open_hour": int(open_hour), "close_hour": int(close_hour)}
        for _, _, location, _, open_hour, close_hour, _, _, _ in DEFAULT_SPACES
    }


def is_location_open_at_hour(location, hour):
    if location not in VALID_LOCATIONS:
        return False
    hours = space_hours_by_location().get(location)
    if not hours:
        return True
    open_hour = hours["open_hour"]
    close_hour = hours["close_hour"]
    if close_hour == 24:
        return hour >= open_hour
    if open_hour <= close_hour:
        return open_hour <= hour < close_hour
    return hour >= open_hour or hour < close_hour


def weighted_choice(options):
    weighted = [(item, max(0.0, float(weight))) for item, weight in options if item]
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return weighted[0][0] if weighted else "宿舍区"
    pick = random.uniform(0, total)
    cursor = 0.0
    for item, weight in weighted:
        cursor += weight
        if pick <= cursor:
            return item
    return weighted[-1][0]


def active_schedule_rules(conn, role, hour, env=None):
    if not conn:
        return []
    group = role_group(role)
    env = env or {}
    exam_pressure = int(env.get("exam_pressure") or 0)
    rows = conn.execute(
        """
        SELECT * FROM campus_schedule_rules
        WHERE status = 'active'
          AND role_group IN ('all', ?)
          AND min_exam_pressure <= ?
          AND max_exam_pressure >= ?
        """,
        (group, exam_pressure, exam_pressure),
    ).fetchall()
    rules = []
    for row in rows:
        start_hour = int(row["start_hour"] if row["start_hour"] is not None else 0)
        end_hour = int(row["end_hour"] if row["end_hour"] is not None else 24)
        if (start_hour <= end_hour and start_hour <= hour < end_hour) or (start_hour > end_hour and (hour >= start_hour or hour < end_hour)):
            rules.append(dict(row))
    return rules


def causal_multiplier_for_target(conn, env, target_type, target_key):
    if not conn:
        return 1.0
    rows = conn.execute(
        """
        SELECT * FROM world_causal_weights
        WHERE status = 'active' AND target_type = ? AND target_key = ?
        """,
        (target_type, target_key),
    ).fetchall()
    multiplier = 1.0
    for row in rows:
        metric = float(env.get(row["source_metric"]) or 0)
        threshold = float(row["threshold"] or 0)
        if metric < threshold:
            continue
        effect = ((metric - threshold) / 100.0) * float(row["strength"] or 0) * float(row["direction"] or 1)
        effect += random.uniform(-float(row["noise"] or 0), float(row["noise"] or 0))
        multiplier *= max(0.05, 1.0 + effect)
    return multiplier


def action_noise_for_agent(agent):
    strategy = load_json_text(agent.get("strategy"), {}) if isinstance(agent, dict) else {}
    traits = strategy.get("personality_traits") if isinstance(strategy, dict) else None
    if isinstance(traits, dict):
        def score(name, default=50):
            try:
                return float(traits.get(name, default))
            except (TypeError, ValueError):
                return float(default)
        return {
            "social": max(0.35, 0.5 + score("extraversion") / 100 * 0.65 + score("social_need") / 100 * 0.45 + score("empathy") / 100 * 0.20),
            "study": max(0.35, 0.55 + score("conscientiousness") / 100 * 0.75 + score("rule_orientation") / 100 * 0.25),
            "routine": max(0.35, 0.55 + score("rule_orientation") / 100 * 0.55 + score("conscientiousness") / 100 * 0.25),
            "risk": max(0.2, 0.45 + score("risk_tolerance") / 100 * 0.75 + score("competitiveness") / 100 * 0.25 - score("rule_orientation") / 100 * 0.20),
            "service": max(0.35, 0.55 + score("empathy") / 100 * 0.45 + score("conscientiousness") / 100 * 0.30),
            "stress": max(0.2, 0.4 + score("stress_sensitivity") / 100 * 0.9 - score("emotional_stability") / 100 * 0.35),
        }
    text = " ".join(str(agent.get(key, "")) for key in ("personality", "goal", "role"))
    bias = {"social": 1.0, "study": 1.0, "routine": 1.0, "risk": 1.0, "service": 1.0, "stress": 1.0}
    if any(token in text for token in ("外向", "社交", "朋友", "活动", "社团")):
        bias["social"] += 0.35
    if any(token in text for token in ("认真", "学习", "成绩", "科研", "论文", "自律")):
        bias["study"] += 0.35
    if any(token in text for token in ("谨慎", "内向", "稳定", "秩序", "规则")):
        bias["routine"] += 0.25
        bias["risk"] -= 0.20
    if any(token in text for token in ("叛逆", "冲动", "冒险", "竞争")):
        bias["risk"] += 0.30
    if role_group(agent.get("role")) in {"service", "teacher"}:
        bias["service"] += 0.30
    return bias


def location_options_for_context(role, hour, weather="", current_location="", conn=None, env=None, agent=None):
    group = role_group(role)
    weather_text = str(weather or "")
    rainy = any(token in weather_text for token in ("雨", "雷", "雪", "雾", "大风"))
    if 0 <= hour < 6:
        base = {
            "student": [("宿舍区", 82), ("图书馆", 8), ("教学楼", 5), ("操场", 2), (current_location, 3)],
            "teacher": [("宿舍区", 62), ("教学楼", 12), ("图书馆", 14), ("校务处", 5), (current_location, 7)],
            "business": [("商业街", 26), ("宿舍区", 42), ("食堂", 8), ("校务处", 6), (current_location, 18)],
            "service": [("宿舍区", 34), ("校务处", 26), ("食堂", 14), ("图书馆", 8), (current_location, 18)],
        }.get(group, [("宿舍区", 80), (current_location, 20)])
    elif 6 <= hour < 9:
        base = {
            "student": [("食堂", 34), ("教学楼", 24), ("宿舍区", 18), ("操场", 10), ("图书馆", 8), (current_location, 6)],
            "teacher": [("教学楼", 34), ("食堂", 18), ("校务处", 16), ("图书馆", 16), (current_location, 16)],
            "business": [("商业街", 30), ("食堂", 30), ("校务处", 8), (current_location, 32)],
            "service": [("食堂", 28), ("校务处", 28), ("宿舍区", 18), ("教学楼", 12), (current_location, 14)],
        }.get(group, [("食堂", 30), ("教学楼", 25), (current_location, 20)])
    elif 9 <= hour < 11:
        base = {
            "student": [("教学楼", 48), ("图书馆", 24), ("操场", 8), ("商业街", 6), (current_location, 14)],
            "teacher": [("教学楼", 50), ("图书馆", 18), ("校务处", 18), (current_location, 14)],
            "business": [("商业街", 55), ("食堂", 20), ("校务处", 8), (current_location, 17)],
            "service": [("校务处", 38), ("教学楼", 20), ("食堂", 18), ("图书馆", 10), (current_location, 14)],
        }.get(group, [("教学楼", 35), ("图书馆", 25), (current_location, 15)])
    elif 11 <= hour < 14:
        base = {
            "student": [("食堂", 42), ("教学楼", 18), ("图书馆", 16), ("商业街", 10), (current_location, 14)],
            "teacher": [("食堂", 32), ("教学楼", 28), ("校务处", 12), ("图书馆", 12), (current_location, 16)],
            "business": [("食堂", 36), ("商业街", 42), ("校务处", 6), (current_location, 16)],
            "service": [("食堂", 30), ("校务处", 30), ("教学楼", 14), (current_location, 26)],
        }.get(group, [("食堂", 38), ("教学楼", 20), (current_location, 15)])
    elif 14 <= hour < 17:
        base = {
            "student": [("教学楼", 38), ("图书馆", 26), ("商业街", 10), ("操场", 8), (current_location, 18)],
            "teacher": [("教学楼", 38), ("图书馆", 24), ("校务处", 20), (current_location, 18)],
            "business": [("商业街", 56), ("食堂", 16), ("校务处", 8), (current_location, 20)],
            "service": [("校务处", 34), ("图书馆", 18), ("教学楼", 18), ("食堂", 12), (current_location, 18)],
        }.get(group, [("教学楼", 32), ("图书馆", 24), (current_location, 18)])
    elif 17 <= hour < 21:
        base = {
            "student": [("食堂", 30), ("操场", 22), ("图书馆", 18), ("商业街", 14), ("宿舍区", 10), (current_location, 6)],
            "teacher": [("食堂", 24), ("图书馆", 22), ("教学楼", 18), ("操场", 10), ("宿舍区", 12), (current_location, 14)],
            "business": [("商业街", 46), ("食堂", 26), ("宿舍区", 8), (current_location, 20)],
            "service": [("宿舍区", 24), ("食堂", 24), ("校务处", 18), ("操场", 12), (current_location, 22)],
        }.get(group, [("食堂", 28), ("操场", 18), ("宿舍区", 16), (current_location, 12)])
    else:
        base = {
            "student": [("宿舍区", 56), ("图书馆", 18), ("操场", 8), ("教学楼", 8), (current_location, 10)],
            "teacher": [("宿舍区", 42), ("图书馆", 20), ("教学楼", 14), (current_location, 24)],
            "business": [("商业街", 28), ("宿舍区", 30), ("食堂", 8), (current_location, 34)],
            "service": [("宿舍区", 34), ("校务处", 20), ("食堂", 12), (current_location, 34)],
        }.get(group, [("宿舍区", 50), (current_location, 20)])
    if rainy:
        base = [(location, weight * 0.25 if location == "操场" else weight) for location, weight in base]
    for rule in active_schedule_rules(conn, role, hour, env):
        if rule.get("location") in VALID_LOCATIONS:
            noise = float(rule.get("noise") or 0)
            weight = float(rule.get("base_weight") or 1.0) * 8
            weight *= 1 + random.uniform(-noise, noise)
            base.append((rule["location"], weight))
    if agent:
        bias = action_noise_for_agent(agent)
        adjusted = []
        for location, weight in base:
            if location in {"教学楼", "图书馆"}:
                weight *= bias["study"]
            if location in {"食堂", "操场", "商业街"}:
                weight *= bias["social"]
            if location in {"校务处", "宿舍区"}:
                weight *= bias["routine"]
            adjusted.append((location, weight))
        base = adjusted
    if conn and env:
        base = [
            (location, weight * causal_multiplier_for_target(conn, env, "location", location))
            for location, weight in base
        ]
    if conn and agent and agent.get("id"):
        memory_factors = spatial_memory_location_factors(
            conn,
            agent["id"],
            branch_key=active_world_branch_key(conn),
        )
        base = [
            (location, weight * memory_factors.get(location, 1.0))
            for location, weight in base
        ]
    open_options = [(location, weight) for location, weight in base if is_location_open_at_hour(location, hour)]
    return open_options or [("宿舍区", 1)]


def realistic_location_for_context(role, hour, weather="", current_location="", preferred_location="", conn=None, env=None, agent=None):
    if preferred_location and is_location_open_at_hour(preferred_location, hour):
        if preferred_location == "操场" and any(token in str(weather or "") for token in ("雨", "雷", "雪", "大风")):
            return weighted_choice(location_options_for_context(role, hour, weather, current_location, conn=conn, env=env, agent=agent))
        return preferred_location
    return weighted_choice(location_options_for_context(role, hour, weather, current_location, conn=conn, env=env, agent=agent))


def action_for_context(role, location, hour, conn=None, env=None, agent=None):
    options = []
    if 0 <= hour < 6:
        options = [("rest", 7), ("reflect", 3)] if location == "宿舍区" else [("observe", 4), ("late", 1)]
    elif location == "宿舍区":
        options = [("rest", 4), ("reflect", 3), ("observe", 1)] if hour >= 21 or hour < 7 else [("reflect", 3), ("observe", 2)]
    elif location == "教学楼":
        options = [("attend_class", 5), ("collaborate", 2), ("late", 0.5), ("observe", 1)]
    elif location == "图书馆":
        options = [("observe", 4), ("collaborate", 1.2), ("reflect", 1)]
    elif location == "食堂":
        options = [("queue", 3), ("consume", 4), ("chat", 2), ("conflict", 0.2)]
    elif location == "商业街":
        options = [("consume", 4), ("queue", 1), ("chat", 2), ("conflict", 0.25)]
    elif location == "操场":
        options = [("club_activity", 4), ("chat", 2), ("collaborate", 1), ("observe", 1)]
    elif location == "校务处":
        options = [("request_leave", 1.5), ("collaborate", 3), ("observe", 2)]
    else:
        options = [("observe", 2), ("move", 1)]
    for rule in active_schedule_rules(conn, role, hour, env):
        if not rule.get("location") or rule.get("location") == location:
            noise = float(rule.get("noise") or 0)
            weight = float(rule.get("base_weight") or 1.0) * (1 + random.uniform(-noise, noise))
            options.append((rule["action_type"], weight))
    if conn and env:
        options = [
            (action, weight * causal_multiplier_for_target(conn, env, "action", action))
            for action, weight in options
        ]
    if agent:
        bias = action_noise_for_agent(agent)
        adjusted = []
        for action, weight in options:
            if action in {"chat", "club_activity", "collaborate"}:
                weight *= bias["social"]
            if action in {"attend_class", "observe", "reflect"}:
                weight *= bias["study"]
            if action in {"conflict", "late"}:
                weight *= bias["risk"]
            if action in {"request_leave", "collaborate"}:
                weight *= bias["service"]
            adjusted.append((action, weight))
        options = adjusted
    return weighted_choice(options)


def build_rule_based_plan(conn, resident, window_start, window_end, world_time=None, goal_context=None):
    role = str(resident["role"])
    env = dict(get_campus_environment(conn, get_current_day(conn))) if conn else {}
    agent = dict(resident)
    if role_group(role) == "teacher":
        intent = "平衡教学、指导学生和校园服务"
    elif role_group(role) == "business":
        intent = "维持校园服务供给并寻找需求变化"
    elif role_group(role) == "service":
        intent = "维护空间秩序和资源稳定"
    elif window_start.hour == 0:
        intent = "在夜间恢复精力，并为白天学习生活做准备"
    elif window_start.hour == 8:
        intent = "推进课程学习并保持必要社交"
    else:
        intent = "整理一天收获并进行轻量社交"
    steps = []
    offsets = [45, 255, 435]
    for index, offset in enumerate(offsets):
        step_time = window_start + timedelta(minutes=offset + random.randint(-20, 20))
        if step_time >= window_end:
            step_time = window_end - timedelta(minutes=30)
        hour = step_time.hour
        location = realistic_location_for_context(role, hour, env.get("weather", ""), current_location=resident["location"], conn=conn, env=env, agent=agent)
        action = action_for_context(role, location, hour, conn=conn, env=env, agent=agent)
        steps.append(
            {
                "time": step_time.strftime("%H:%M"),
                "action": action,
                "location": location,
                "goal": (
                    goal_context["short"]["title"]
                    if goal_context
                    else f"{resident['name']}围绕「{resident['goal']}」调整当前节奏"
                ),
            }
        )
    return {
        "resident_id": resident["id"],
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "intent": intent,
        "steps": steps,
        "flexibility": 0.35,
        "source": "rule-based-v1",
    }


def normalize_plan_step(step, window_start, index, fallback_location, fallback_goal):
    step = step if isinstance(step, dict) else {}
    action = str(step.get("action") or "observe").strip().lower()
    if action not in WORLD_AUTONOMOUS_ACTIONS:
        action = "observe"
    location = str(step.get("location") or fallback_location or "校园").strip()
    if location not in VALID_LOCATIONS:
        location = fallback_location if fallback_location in VALID_LOCATIONS else "校园"
    goal = str(step.get("goal") or fallback_goal or "观察校园环境").strip()[:160]
    time_text = str(step.get("time") or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", time_text):
        step_time = window_start + timedelta(minutes=45 + index * 135)
        time_text = step_time.strftime("%H:%M")
    try:
        hour = int(time_text.split(":", 1)[0])
    except (TypeError, ValueError):
        hour = window_start.hour
    if not is_location_open_at_hour(location, hour):
        location = realistic_location_for_context("", hour, current_location=fallback_location)
        action = "observe" if action in {"move", "attend_class", "consume", "queue", "club_activity"} else action
    return {"time": time_text, "action": action, "location": location, "goal": goal}


def build_llm_action_plan(conn, resident, window_start, window_end, world_time, goal_context=None):
    if not consume_auto_model_budget(conn, "planner", resident_id=resident["id"]):
        return None
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    prompt = f"""
你是一个校园平行世界的运行时 planner。请为 Agent 制定接下来 8 小时内的简短行动计划。

世界时间：{world_time.strftime('%Y-%m-%d %H:%M')}
计划窗口：{window_start.strftime('%H:%M')} 到 {window_end.strftime('%H:%M')}
可选地点：{", ".join(VALID_LOCATIONS)}
可选动作：move, observe, chat, reflect, attend_class, queue, consume, rest, club_activity, conflict, collaborate, late, request_leave
现实约束：
- 00:00-06:00 大多数学生应在宿舍区休息或反思，只有少量异常情况会在其他开放空间观察。
- 食堂开放 06:00-21:00，商业街 09:00-22:00，教学楼和图书馆夜间关闭，校务处 08:00-18:00。
- 下雨、雷雨、大风时应显著减少操场计划。
- 计划需要有少量随机性和个体差异，但不能让所有 Agent 同时去同一地点。

Agent:
- id: {resident['id']}
- name: {resident['name']}
- role: {resident['role']}
- current_location: {resident['location']}
- long_goal: {resident['goal']}
- active_long_goal: {goal_context['long']['title'] if goal_context else resident['goal']}
- medium_project: {goal_context['medium']['title'] if goal_context else '尚未建立'}
- short_goal: {goal_context['short']['title'] if goal_context else '尚未建立'}
- current_commitment: {goal_context['commitment']['title'] if goal_context and goal_context.get('commitment') else '无'}

只返回 JSON，不要解释。格式：
{{
  "intent": "一句话说明这个 8 小时窗口的意图",
  "steps": [
    {{"time": "HH:MM", "action": "attend_class", "location": "教学楼", "goal": "具体目标"}}
  ],
  "flexibility": 0.35
}}
steps 保持 3 条以内，时间必须落在计划窗口内。
"""
    try:
        raw = ask_llm(prompt)
        payload = extract_json(raw)
        steps = [
            normalize_plan_step(step, window_start, index, resident["location"], resident["goal"])
            for index, step in enumerate((payload.get("steps") or [])[:3])
        ]
        if not steps:
            raise ValueError("LLM plan has no steps")
        plan = {
            "resident_id": resident["id"],
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "intent": str(payload.get("intent") or f"{resident['name']}按个人目标推进校园生活")[:180],
            "steps": steps,
            "flexibility": float(payload.get("flexibility") or 0.35),
            "source": "llm-planner-v1",
        }
        log_model_call(
            conn,
            "planner",
            status="success",
            resident_id=resident["id"],
            model_name=model_name,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return plan
    except Exception as exc:
        logger.warning("LLM planner failed for resident %s", resident["id"], exc_info=True)
        log_model_call(conn, "planner", status=f"failed:{type(exc).__name__}", resident_id=resident["id"], model_name=model_name)
        return None


def ensure_current_action_plans(conn, world_time):
    ensure_world_runtime_tables(conn)
    window_start, window_end = get_world_plan_window(world_time)
    lifecycle_join = ""
    lifecycle_filter = ""
    if population_runtime_available(conn):
        lifecycle_join = (
            "LEFT JOIN population_profiles lifecycle "
            "ON lifecycle.resident_id = r.id"
        )
        lifecycle_filter = (
            "WHERE lifecycle.resident_id IS NULL "
            "OR lifecycle.lifecycle_status = 'active'"
        )
    residents = conn.execute(
        f"""
        SELECT r.id, r.name, r.role, r.personality, r.goal, r.money, r.location, p.strategy
        FROM residents r
        LEFT JOIN agent_profiles p ON p.resident_id = r.id
        {lifecycle_join}
        {lifecycle_filter}
        ORDER BY r.id
        """
    ).fetchall()
    created = 0
    llm_plans = 0
    rule_based_plans = 0
    backfilled_plans = 0
    goals_revised = 0
    for resident in residents:
        resident_dict = dict(resident)
        goal_context = ensure_multiscale_goal_structure(conn, resident_dict, world_time)
        goals_revised += int(goal_context["review"]["revised"])
        existing = conn.execute(
            """
            SELECT id, plan_json FROM agent_action_plans
            WHERE resident_id = ? AND window_start = ? AND status = 'active'
            """,
            (resident["id"], window_start.isoformat()),
        ).fetchone()
        if existing:
            existing_plan = load_json_text(existing["plan_json"], {})
            if not existing_plan.get("goal_chain"):
                existing_plan = attach_goal_context_to_plan(existing_plan, goal_context)
                conn.execute(
                    """
                    UPDATE agent_action_plans
                    SET plan_json = ?, prompt_version = 'world-runtime-v4',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (json_dumps(existing_plan, ensure_ascii=False), existing["id"]),
                )
                backfilled_plans += 1
            continue
        plan = build_llm_action_plan(conn, resident, window_start, window_end, world_time, goal_context=goal_context)
        if plan:
            model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
            llm_plans += 1
        else:
            plan = build_rule_based_plan(conn, resident, window_start, window_end, world_time, goal_context=goal_context)
            model_name = "rule-based-v1"
            rule_based_plans += 1
        plan = attach_goal_context_to_plan(plan, goal_context)
        conn.execute(
            """
            INSERT INTO agent_action_plans
            (resident_id, window_start, window_end, plan_json, model_name, prompt_version, status)
            VALUES (?, ?, ?, ?, 'rule-based-v1', 'world-runtime-v4', 'active')
            ON CONFLICT(resident_id, window_start)
            DO UPDATE SET plan_json = excluded.plan_json, window_end = excluded.window_end,
                          prompt_version = 'world-runtime-v4', status = 'active',
                          updated_at = CURRENT_TIMESTAMP
            """,
            (resident["id"], window_start.isoformat(), window_end.isoformat(), json_dumps(plan, ensure_ascii=False)),
        )
        conn.execute(
            """
            UPDATE agent_action_plans
            SET model_name = ?, updated_at = CURRENT_TIMESTAMP
            WHERE resident_id = ? AND window_start = ?
            """,
            (model_name, resident["id"], window_start.isoformat()),
        )
        created += 1
    return {
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "created": created,
        "llm_plans": llm_plans,
        "rule_based_plans": rule_based_plans,
        "backfilled_plans": backfilled_plans,
        "goals_revised": goals_revised,
    }


def get_environment_hour(env):
    real_time = str(env.get("real_time") or "")
    try:
        return int(real_time.split(":", 1)[0])
    except (TypeError, ValueError):
        return {"上午": 9, "中午": 12, "下午": 15, "晚上": 20, "深夜": 2}.get(env.get("time_slot"), 9)


def get_active_campus_events(conn, day=None):
    day = day or get_current_day(conn)
    return rows_to_dicts(
        conn.execute(
            "SELECT * FROM campus_events WHERE day = ? AND status = 'active' ORDER BY id DESC",
            (day,),
        ).fetchall()
    )


def get_space_snapshot(conn, day=None):
    ensure_space_system(conn)
    env = get_campus_environment(conn, day)
    hour = get_environment_hour(env)
    active_events = get_active_campus_events(conn, day)
    actual_counts = {
        row["location"]: row["count"]
        for row in conn.execute("SELECT location, COUNT(*) AS count FROM residents GROUP BY location").fetchall()
    }
    spaces = []
    for row in conn.execute("SELECT * FROM campus_spaces ORDER BY code").fetchall():
        space = dict(row)
        capacity = int(space["capacity"])
        crowd_percent = clamp(env.get(space["crowd_field"], env.get("campus_flow", 50)))
        estimated_occupancy = round(capacity * crowd_percent / 100)
        actual_agents = int(actual_counts.get(space["location"], 0))
        occupancy = max(actual_agents, estimated_occupancy)
        event_status = None
        relevant_events = []
        for event in active_events:
            targets = json.loads(event["target_spaces"])
            effects = json.loads(event["effects"])
            if space["location"] in targets:
                relevant_events.append(event["title"])
                event_status = effects.get("space_status", event_status)
        within_hours = space["open_hour"] <= hour < space["close_hour"] if space["close_hour"] != 24 else hour >= space["open_hour"]
        base_status = space["status"]
        if base_status != "开放":
            effective_status = base_status
        elif event_status:
            effective_status = event_status
        elif not within_hours:
            effective_status = "已关闭"
        elif occupancy >= capacity:
            effective_status = "满员"
        else:
            effective_status = "开放"
        space.update(
            {
                "crowd_percent": crowd_percent,
                "actual_agents": actual_agents,
                "estimated_occupancy": estimated_occupancy,
                "occupancy": occupancy,
                "available_slots": max(0, capacity - occupancy),
                "effective_status": effective_status,
                "active_events": relevant_events,
            }
        )
        spaces.append(space)
    return {"hour": hour, "spaces": spaces, "active_events": active_events}


def assert_destination_available(conn, destination):
    if destination not in VALID_LOCATIONS:
        raise ValueError("地点不存在")
    snapshot = get_space_snapshot(conn)
    space = next((item for item in snapshot["spaces"] if item["location"] == destination), None)
    if not space:
        return
    if space["effective_status"] != "开放":
        raise ValueError(f"{destination}当前{space['effective_status']}，Agent 需要调整计划")


def apply_campus_event_effects(conn, day, effects):
    updates = effects.get("environment_updates", {}) if isinstance(effects, dict) else {}
    allowed = set(DEFAULT_ENV.keys())
    updates = {key: value for key, value in updates.items() if key in allowed}
    if not updates:
        return {}
    get_campus_environment(conn, day)
    set_clause = ", ".join([f"{key} = ?" for key in updates])
    conn.execute(f"UPDATE campus_state SET {set_clause} WHERE day = ?", list(updates.values()) + [day])
    return updates


def default_event_configuration(env, event_type, intensity, target_spaces):
    intensity = clamp(intensity, 1, 100)
    targets = target_spaces or {
        "设施故障": ["图书馆"],
        "天气预警": ["操场"],
        "大型活动": ["操场", "教学楼"],
        "考试通知": ["图书馆", "教学楼"],
    }.get(event_type, [])
    updates = {
        "event_name": event_type,
        "event_intensity": intensity,
    }
    space_status = "开放"
    if event_type == "设施故障":
        space_status = "维护中"
        updates.update(
            {
                "resource_pressure": clamp(int(env["resource_pressure"]) + intensity // 2),
                "campus_mood": "关注中",
            }
        )
    elif event_type == "天气预警":
        space_status = "暂停开放"
        updates.update(
            {
                "playground_crowd": clamp(int(env["playground_crowd"]) - intensity // 2),
                "campus_flow": clamp(int(env["campus_flow"]) - intensity // 4),
                "campus_mood": "谨慎",
            }
        )
    elif event_type == "大型活动":
        updates.update(
            {
                "activity_heat": clamp(int(env["activity_heat"]) + intensity // 3),
                "campus_flow": clamp(int(env["campus_flow"]) + intensity // 4),
                "campus_mood": "活跃",
            }
        )
    elif event_type == "考试通知":
        updates.update(
            {
                "exam_pressure": clamp(int(env["exam_pressure"]) + intensity // 3),
                "study_atmosphere": clamp(int(env["study_atmosphere"]) + intensity // 4),
                "library_crowd": clamp(int(env["library_crowd"]) + intensity // 3),
                "campus_mood": "紧张",
            }
        )
    return targets, {"space_status": space_status, "environment_updates": updates}


def create_campus_event(conn, day, title, event_type, intensity, target_spaces=None, effects=None):
    ensure_space_system(conn)
    env = get_campus_environment(conn, day)
    targets, default_effects = default_event_configuration(env, event_type, intensity, target_spaces or [])
    final_effects = effects or default_effects
    cursor = conn.execute(
        """
        INSERT INTO campus_events (day, title, event_type, intensity, target_spaces, effects)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            day,
            title,
            event_type,
            intensity,
            json_dumps(targets, ensure_ascii=False),
            json_dumps(final_effects, ensure_ascii=False),
        ),
    )
    updates = apply_campus_event_effects(conn, day, final_effects)
    description = f"校园事件《{title}》已触发，类型：{event_type}，影响空间：{targets or '全校'}。"
    add_event(conn, day, "campus_event", description)
    conn.commit()
    return {"id": cursor.lastrowid, "title": title, "event_type": event_type, "target_spaces": targets, "effects": final_effects, "environment_updates": updates}


def maybe_generate_environment_event(conn, day):
    if get_active_campus_events(conn, day):
        return None
    env = get_campus_environment(conn, day)
    if int(env.get("rainfall", 0)) >= 60:
        return create_campus_event(conn, day, "降雨天气预警", "天气预警", 65, ["操场"])
    if int(env.get("resource_pressure", 0)) >= 80:
        return create_campus_event(conn, day, "设施资源紧张", "设施故障", 55, ["图书馆"])
    if int(env.get("activity_heat", 0)) >= 75 and random.random() < 0.45:
        return create_campus_event(conn, day, "校园主题活动", "大型活动", 60, ["操场", "教学楼"])
    return None


def build_environment_modules(env):
    return {
        "TimeWeather": {
            "description": "时间、天气和学期阶段",
            "weather": env["weather"],
            "temperature": env["temperature"],
            "rainfall": env["rainfall"],
            "weather_source": env["weather_source"],
            "weather_observed_at": env["weather_observed_at"],
            "real_date": env.get("real_date", ""),
            "real_time": env.get("real_time", ""),
            "time_source": env.get("time_source", "simulation"),
            "weekday": env["weekday"],
            "time_slot": env["time_slot"],
            "semester_stage": env["semester_stage"],
        },
        "Academic": {
            "description": "学习氛围、考试压力和作业压力",
            "exam_pressure": env["exam_pressure"],
            "assignment_pressure": env["assignment_pressure"],
            "study_atmosphere": env["study_atmosphere"],
        },
        "Activity": {
            "description": "校园活动与事件热度",
            "activity_heat": env["activity_heat"],
            "event_name": env["event_name"],
            "event_intensity": env["event_intensity"],
        },
        "Crowd": {
            "description": "校园各空间人流和拥挤度",
            "campus_flow": env["campus_flow"],
            "classroom_crowd": env["classroom_crowd"],
            "canteen_crowd": env["canteen_crowd"],
            "library_crowd": env["library_crowd"],
            "dorm_crowd": env["dorm_crowd"],
            "playground_crowd": env["playground_crowd"],
            "commercial_crowd": env["commercial_crowd"],
        },
        "Infrastructure": {
            "description": "交通、网络、资源和安全秩序",
            "traffic_status": env["traffic_status"],
            "network_status": env["network_status"],
            "safety_level": env["safety_level"],
            "resource_pressure": env["resource_pressure"],
        },
        "Business": {
            "description": "商业消费和校园整体情绪",
            "consumption_index": env["consumption_index"],
            "campus_mood": env["campus_mood"],
        },
    }


def get_campus_environment(conn, day=None):
    ensure_campus_state_table(conn)
    if day is None:
        day = get_current_day(conn)

    row = conn.execute("SELECT * FROM campus_state WHERE day = ?", (day,)).fetchone()
    if not row:
        previous = conn.execute(
            "SELECT * FROM campus_state WHERE day < ? ORDER BY day DESC LIMIT 1",
            (day,),
        ).fetchone()
        values = dict(previous) if previous else dict(DEFAULT_ENV)
        values.pop("day", None)
        values.pop("created_at", None)
        full_values = {key: values.get(key, default) for key, default in DEFAULT_ENV.items()}
        columns = ["day"] + list(DEFAULT_ENV.keys())
        placeholders = ", ".join(["?"] * len(columns))
        conn.execute(
            f"INSERT INTO campus_state ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT(day) DO NOTHING",
            [day] + [full_values[key] for key in DEFAULT_ENV.keys()],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM campus_state WHERE day = ?", (day,)).fetchone()

    env = dict(row)
    env["modules"] = build_environment_modules(env)
    return env


def retrieve_relevant_memories(conn, resident_id, query_terms=None, limit=6):
    """Rank personal memories by relevance, importance, recency, and prior reuse."""
    ensure_memory_columns(conn)
    current_day = get_current_day(conn)
    terms = [str(term).strip() for term in (query_terms or []) if str(term).strip()]
    rows = conn.execute(
        """
        SELECT id, day, content, importance, memory_type, tags, source,
               access_count, last_accessed_at, created_at
        FROM memories
        WHERE resident_id = ? AND day <= ?
        ORDER BY id DESC
        LIMIT 120
        """,
        (resident_id, current_day),
    ).fetchall()
    type_bonus = {"relationship": 18, "semantic": 15, "episodic": 9, "working": 5}
    ranked = []
    for row in rows:
        memory = dict(row)
        text = f"{memory.get('tags', '')} {memory['content']}"
        matches = sum(1 for term in terms if term in text)
        age = max(0, current_day - int(memory["day"]))
        score = (
            int(memory["importance"]) * 10
            + type_bonus.get(memory.get("memory_type"), 6)
            + min(int(memory.get("access_count") or 0), 5) * 2
            + matches * 18
            + max(0, 18 - age * 3)
        )
        memory["relevance_score"] = score
        ranked.append(memory)
    selected = sorted(ranked, key=lambda item: item["relevance_score"], reverse=True)[:limit]
    if selected:
        placeholders = ", ".join("?" for _ in selected)
        conn.execute(
            f"UPDATE memories SET access_count = access_count + 1, last_accessed_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
            [item["id"] for item in selected],
        )
        for memory in selected:
            memory["access_count"] = int(memory.get("access_count") or 0) + 1
            memory["last_accessed_at"] = "本次决策检索"
    return selected


def get_recent_context(conn, resident_id, limit=6, query_terms=None):
    memories = retrieve_relevant_memories(conn, resident_id, query_terms=query_terms, limit=limit)
    events = conn.execute(
        """
        SELECT day, event_type, description, created_at
        FROM city_events
        WHERE day <= ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (get_current_day(conn), limit),
    ).fetchall()
    return {
        "memories": rows_to_dicts(memories),
        "memory_retrieval_terms": query_terms or [],
        "events": rows_to_dicts(events),
    }


def extract_json(text):
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.S)
    if match:
        cleaned = match.group(0)
    return json.loads(cleaned)




def perceive_environment(conn, resident_id):
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    day = get_current_day(conn)
    env = get_campus_environment(conn, day)
    module_state = get_agent_module_state(conn, resident_id)
    schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
    location = resident["location"]
    crowd_by_location = {
        "教学楼": env.get("classroom_crowd", 50),
        "图书馆": env.get("library_crowd", 50),
        "食堂": env.get("canteen_crowd", 50),
        "宿舍区": env.get("dorm_crowd", 50),
        "操场": env.get("playground_crowd", 50),
        "商业街": env.get("commercial_crowd", 50),
        "校务处": env.get("campus_flow", 50),
    }
    local_crowd = crowd_by_location.get(location, env.get("campus_flow", 50))
    space_snapshot = get_space_snapshot(conn, day)
    current_space = next((space for space in space_snapshot["spaces"] if space["location"] == location), None)
    perception = {
        "day": day,
        "location": location,
        "weather": env.get("weather"),
        "temperature": env.get("temperature"),
        "rainfall": env.get("rainfall"),
        "local_crowd": local_crowd,
        "campus_mood": env.get("campus_mood"),
        "exam_pressure": env.get("exam_pressure"),
        "activity_heat": env.get("activity_heat"),
        "event_name": env.get("event_name"),
        "network_status": env.get("network_status"),
        "safety_level": env.get("safety_level"),
        "current_space": current_space,
        "active_events": space_snapshot["active_events"],
        "agent_energy": module_state["modules"]["Physical"]["energy"],
        "agent_mood": module_state["modules"]["Physical"]["mood"],
        "current_task": module_state["modules"]["Mental"]["task"],
    }
    conn.execute(
        "UPDATE agent_profiles SET perception = ? WHERE resident_id = ?",
        (json_dumps(perception, ensure_ascii=False), resident_id),
    )
    add_memory(
        conn,
        resident_id,
        day,
        f"感知环境：当前位置 {location}，天气 {perception['weather']}，局部拥挤度 {local_crowd}，校园情绪 {perception['campus_mood']}。",
        importance=1,
    )
    conn.commit()
    return perception


def apply_environment_feedback(conn, resident_id, action, result):
    day = get_current_day(conn)
    env = get_campus_environment(conn, day)
    updates = {}
    description = result.get("description", "") if isinstance(result, dict) else ""

    if action == "move":
        updates["campus_flow"] = clamp(int(env.get("campus_flow", 55)) + 1, 0, 100)
        if "图书馆" in description:
            updates["library_crowd"] = clamp(int(env.get("library_crowd", 45)) + 2, 0, 100)
        elif "食堂" in description:
            updates["canteen_crowd"] = clamp(int(env.get("canteen_crowd", 50)) + 2, 0, 100)
        elif "操场" in description:
            updates["playground_crowd"] = clamp(int(env.get("playground_crowd", 40)) + 2, 0, 100)
        elif "商业街" in description:
            updates["commercial_crowd"] = clamp(int(env.get("commercial_crowd", 50)) + 2, 0, 100)
    elif action == "chat":
        updates["campus_mood"] = "活跃"
        updates["activity_heat"] = clamp(int(env.get("activity_heat", 50)) + 1, 0, 100)
    elif action == "buy_sell":
        updates["consumption_index"] = round(min(1.8, float(env.get("consumption_index", 1.0)) + 0.03), 2)
        updates["commercial_crowd"] = clamp(int(env.get("commercial_crowd", 50)) + 1, 0, 100)
    elif action == "submit_policy":
        updates["resource_pressure"] = clamp(int(env.get("resource_pressure", 45)) - 1, 0, 100)
        updates["campus_mood"] = "有序"
    elif action in {"create_group", "join_group"}:
        updates["activity_heat"] = clamp(int(env.get("activity_heat", 50)) + 3, 0, 100)
        updates["campus_mood"] = "活跃"
    elif action == "leave_group":
        updates["activity_heat"] = clamp(int(env.get("activity_heat", 50)) - 1, 0, 100)
    elif action == "observe":
        updates["study_atmosphere"] = clamp(int(env.get("study_atmosphere", 60)) + 1, 0, 100)

    if updates:
        set_clause = ", ".join([f"{key} = ?" for key in updates])
        conn.execute(f"UPDATE campus_state SET {set_clause} WHERE day = ?", list(updates.values()) + [day])
        add_event(conn, day, "environment_feedback", f"Agent 行动 {action} 反馈到环境：{updates}")
        conn.commit()
    return updates


def record_simulation_log(conn, resident_id, perception, decision_data, execution, feedback,
                          state_before=None, state_after=None, tick_id=None):
    """Persist the exact inputs and outcome that explain one autonomous action."""
    ensure_social_system_tables(conn)
    memory_context = decision_data.get("memory_context", {})
    cursor = conn.execute(
        """
        INSERT INTO simulation_action_logs
        (day, resident_id, tick_id, perception, retrieved_memories, decision, execution,
         environment_feedback, state_before, state_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            get_current_day(conn),
            resident_id,
            tick_id,
            json_dumps(perception or {}, ensure_ascii=False),
            json_dumps(memory_context.get("memories", []), ensure_ascii=False),
            json_dumps(decision_data.get("decision", {}), ensure_ascii=False),
            json_dumps(execution or {}, ensure_ascii=False),
            json_dumps(feedback or {}, ensure_ascii=False),
            json_dumps(state_before or {}, ensure_ascii=False),
            json_dumps(state_after or {}, ensure_ascii=False),
        ),
    )
    return cursor.lastrowid


def run_lifecycle_step(conn, resident_id):
    before = get_agent_module_state(conn, resident_id)
    perception = perceive_environment(conn, resident_id)
    decision_data = decide_agent_action(conn, resident_id)
    execution = execute_decision(conn, resident_id, decision_data["decision"])
    feedback = apply_environment_feedback(conn, resident_id, execution["action"], execution["result"])
    after = get_agent_module_state(conn, resident_id)
    record_simulation_log(conn, resident_id, perception, decision_data, execution, feedback, before, after)
    conn.commit()
    env_after = get_campus_environment(conn)
    return {
        "loop": "perceive -> decide -> act -> feedback -> memory",
        "resident_id": resident_id,
        "before": before,
        "perception": perception,
        "decision": decision_data["decision"],
        "action_result": execution,
        "environment_feedback": feedback,
        "after": after,
        "environment_after": env_after,
    }


def decide_agent_action(conn, resident_id):
    ensure_social_system_tables(conn)
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    day = get_current_day(conn)
    env = get_campus_environment(conn, day)
    module_state = get_agent_module_state(conn, resident_id)
    schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
    memory_terms = [
        resident["location"],
        resident["goal"],
        env.get("weather", ""),
        env.get("event_name", ""),
        schedule_context.get("current_task", ""),
        schedule_context.get("location", ""),
    ]
    context = get_recent_context(conn, resident_id, query_terms=memory_terms)
    other_agents = conn.execute(
        "SELECT id, name, role, location FROM residents WHERE id != ? ORDER BY id",
        (resident_id,),
    ).fetchall()
    active_groups = conn.execute(
        "SELECT id, name, shared_goal, member_ids, deadline_day FROM group_goals WHERE status = 'active' ORDER BY id DESC LIMIT 8"
    ).fetchall()

    prompt = f"""
你正在驱动一个校园封闭世界中的 Agent。

当前日期：第 {day} 天
校园环境：{json_dumps(env, ensure_ascii=False)}
空间状态（容量、开放状态和事件）：{json_dumps(get_space_snapshot(conn, day), ensure_ascii=False)}
当前 Agent：{json_dumps(dict(resident), ensure_ascii=False)}
其他 Agent：{json_dumps(rows_to_dicts(other_agents), ensure_ascii=False)}
可加入或协作的活跃小组：{json_dumps(rows_to_dicts(active_groups), ensure_ascii=False)}
近期记忆和事件：{json_dumps(context, ensure_ascii=False)}
Agent 六模块状态：{json_dumps(module_state, ensure_ascii=False)}
当前日程提示：{json_dumps(schedule_context, ensure_ascii=False)}。日程、天气、关系和资源都是你需要权衡的信息，不是强制命令。你必须自主选择行动，也要在 reason 中说明是否愿意承担暂缓日程、绕开拥挤或消耗资源的后果。

请只返回严格 JSON，不要解释，不要 Markdown。
可选 action 只能是：move、chat、buy_sell、submit_policy、observe、create_group、join_group、leave_group。
地点只能从这些里面选：{list(VALID_LOCATIONS)}。

返回格式：
{{
  "action": "move/chat/buy_sell/submit_policy/observe",
  "reason": "为什么这样做",
  "tool_input": {{}}
}}

tool_input 规则：
move: {{"destination": "图书馆"}}
chat: {{"target_id": 2, "message": "一句校园对话"}}
buy_sell: {{"seller_id": 5, "item_name": "套餐饭", "quantity": 1, "unit_price": 12}}
submit_policy: {{"title": "政策标题", "description": "政策内容"}}
observe: {{"focus": "观察什么"}}
create_group: {{"title": "小组名称", "goal": "共同目标", "member_ids": [2, 3]}}
join_group: {{"group_id": 1}}
leave_group: {{"group_id": 1}}
"""

    try:
        raw = ask_llm(prompt)
        decision = extract_json(raw)
    except Exception as exc:
        decision = {
            "action": "observe",
            "reason": f"AI 决策解析失败，改为观察校园：{exc}",
            "tool_input": {"focus": "校园整体状态"},
        }

    decision = attach_schedule_guidance(schedule_context, decision)

    return {
        "resident": dict(resident),
        "decision": decision,
        "schedule_context": schedule_context,
        "memory_context": context,
    }


def execute_decision(conn, resident_id, decision):
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    action = str(decision.get("action", "observe")).strip()
    reason = str(decision.get("reason", "自主决策"))
    tool_input = decision.get("tool_input") or {}
    day = get_current_day(conn)
    module_state = get_agent_module_state(conn, resident_id)
    schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
    planned_cost = calculate_action_cost(conn, resident_id, action, tool_input, success=True)

    try:
        ensure_action_affordable(conn, resident_id, planned_cost, action)
        if action == "move":
            destination = tool_input.get("destination", resident["location"])
            assert_destination_available(conn, destination)
            result = move_resident(conn, resident_id, destination)
        elif action == "chat":
            target_id = int(tool_input.get("target_id"))
            message = tool_input.get("message") or "今天校园情况怎么样？"
            result = chat_between(conn, resident_id, target_id, message)
        elif action == "buy_sell":
            seller_id = int(tool_input.get("seller_id", 5))
            item_name = tool_input.get("item_name", "套餐饭")
            quantity = int(tool_input.get("quantity", 1))
            unit_price = int(tool_input.get("unit_price", 10))
            result = buy_sell(conn, resident_id, seller_id, item_name, quantity, unit_price)
        elif action == "submit_policy":
            title = tool_input.get("title", "校园微调建议")
            description = tool_input.get("description", reason)
            conn.execute(
                """
                INSERT INTO policies (title, description, proposer_id)
                VALUES (?, ?, ?)
                """,
                (title, description, resident_id),
            )
            text = f"{resident['name']} 提交校园政策《{title}》：{description}"
            add_event(conn, day, "policy_submit", text)
            add_memory(conn, resident_id, day, text, importance=3)
            conn.commit()
            result = {"message": "政策提交成功", "description": text}
        elif action == "create_group":
            title = str(tool_input.get("title") or f"{resident['name']}的协作小组")[:40]
            goal = str(tool_input.get("goal") or resident["goal"])[:180]
            member_ids = [int(member_id) for member_id in tool_input.get("member_ids", []) if str(member_id).isdigit()]
            member_ids = [member_id for member_id in member_ids if member_id != resident_id][:5]
            if not member_ids:
                raise ValueError("发起协作至少需要邀请一位其他 Agent")
            group = create_collaboration(conn, resident_id, member_ids, title, goal)
            result = {"message": "协作小组已发起", "description": f"{resident['name']} 发起小组「{title}」。", "group": group}
        elif action == "join_group":
            group_id = int(tool_input.get("group_id"))
            group = join_group_goal(conn, resident_id, group_id)
            result = {"message": group["message"], "description": f"{resident['name']} 加入小组「{group['group_name']}」。", "group": group}
        elif action == "leave_group":
            group_id = int(tool_input.get("group_id"))
            group = leave_group_goal(conn, resident_id, group_id)
            result = {"message": group["message"], "description": f"{resident['name']} 退出小组「{group['group_name']}」。", "group": group}
        elif action == "observe":
            focus = tool_input.get("focus", "校园状态")
            text = f"{resident['name']} 观察 {focus}。原因：{reason}"
            add_event(conn, day, "agent_observe", text)
            add_memory(conn, resident_id, day, text, importance=1)
            conn.commit()
            result = {"message": "观察完成", "description": text}
        else:
            raise ValueError(f"不支持的自主行动：{action}")
    except Exception as exc:
        # PostgreSQL marks the current transaction as unusable after a failed
        # statement. Roll it back before recording this Agent's failed action.
        conn.rollback()
        text = f"{resident['name']} 自主选择执行 {action}，但未能完成：{exc}。本轮不替 Agent 改选其他行为。"
        add_event(conn, day, "agent_action_failed", text)
        add_memory(conn, resident_id, day, text, importance=1)
        failed_cost = calculate_action_cost(conn, resident_id, action, tool_input, success=False)
        action_cost = update_agent_profile_after_action(conn, resident_id, action, reason, success=False, cost=failed_cost, schedule_context=schedule_context, tool_input=tool_input)
        conn.commit()
        result = {"message": "行动失败，保留自主选择结果", "description": text, "error": str(exc)}

    success = "error" not in result
    learned_action = action
    if success:
        action_cost = update_agent_profile_after_action(conn, resident_id, action, reason, success=True, cost=planned_cost, schedule_context=schedule_context, tool_input=tool_input)
    social_update = None
    if success and action == "chat":
        try:
            target_id = int(tool_input.get("target_id"))
            social_update = {
                "speaker": evolve_relationship(conn, resident_id, target_id, "chat", "日常交流", 3, 2, -1),
                "listener": evolve_relationship(conn, target_id, resident_id, "chat", "回应交流", 2, 2, -1),
            }
        except Exception as exc:
            conn.rollback()
            social_update = {"error": str(exc)}
    goal_update = advance_personal_goal(conn, resident_id, learned_action, success)
    learning = record_learning(
        conn,
        resident_id,
        learned_action,
        "成功" if success else "失败",
        action_score(learned_action, success),
        f"执行 {learned_action} 后得到反馈：{result}",
    )
    conn.commit()

    return {
        "resident_id": resident_id,
        "action": action,
        "reason": reason,
        "result": result,
        "success": success,
        "learning": learning,
        "social_update": social_update,
        "long_term_goal": goal_update,
        "action_cost": action_cost,
        "schedule_context": schedule_context,
    }


def get_current_agent_plan(conn, resident_id, world_time):
    window_start, _ = get_world_plan_window(world_time)
    row = conn.execute(
        """
        SELECT * FROM agent_action_plans
        WHERE resident_id = ? AND window_start = ? AND status = 'active'
        """,
        (resident_id, window_start.isoformat()),
    ).fetchone()
    if not row:
        return None
    plan = load_json_text(row["plan_json"], {})
    plan["_plan_row_id"] = row["id"]
    return plan


def plan_step_key(step):
    return "|".join(
        str(step.get(key, "")).strip()
        for key in ("time", "action", "location", "goal")
    )


def choose_plan_step(plan, world_time, current_location="校园"):
    steps = plan.get("steps") or []
    if not steps:
        return {"action": "observe", "location": current_location, "goal": plan.get("intent", "观察校园环境"), "plan_state": "unplanned"}
    current_hm = world_time.strftime("%H:%M")
    normalized = [step if isinstance(step, dict) else {} for step in steps]
    due_steps = [
        (index, step)
        for index, step in enumerate(normalized)
        if str(step.get("time", "00:00")) <= current_hm
    ]
    pending_due = [
        (index, step)
        for index, step in due_steps
        if not step.get("executed_at")
    ]
    if pending_due:
        index, step = pending_due[0]
        selected = dict(step)
        selected["step_index"] = index
        selected["step_key"] = plan_step_key(step)
        selected["plan_state"] = "due"
        return selected
    future_steps = [
        (index, step)
        for index, step in enumerate(normalized)
        if str(step.get("time", "00:00")) > current_hm and not step.get("executed_at")
    ]
    if future_steps:
        index, step = future_steps[0]
        return {
            "action": "observe",
            "location": current_location,
            "goal": f"等待 {step.get('time', '--:--')} 的计划：{step.get('goal') or plan.get('intent') or '继续观察校园环境'}",
            "step_index": index,
            "step_key": plan_step_key(step),
            "plan_state": "waiting",
            "next_step": step,
        }
    return {
        "action": "reflect",
        "location": current_location,
        "goal": plan.get("intent") or "本窗口计划已完成，整理状态等待下一窗口。",
        "plan_state": "completed",
    }


def mark_plan_step_executed(conn, plan, step, world_time, execution):
    plan_row_id = plan.get("_plan_row_id")
    step_index = step.get("step_index")
    if not plan_row_id or step_index is None:
        return
    steps = plan.get("steps") or []
    if not (0 <= int(step_index) < len(steps)):
        return
    steps[int(step_index)]["executed_at"] = world_time.isoformat()
    steps[int(step_index)]["last_execution"] = {
        "action": execution.get("action"),
        "location": execution.get("location"),
        "goal": execution.get("goal"),
        "mode": execution.get("mode"),
    }
    plan["steps"] = steps
    plan.pop("_plan_row_id", None)
    conn.execute(
        """
        UPDATE agent_action_plans
        SET plan_json = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (json_dumps(plan, ensure_ascii=False), plan_row_id),
    )


GOAL_RELEVANT_ACTIONS = {
    "study": {"attend_class", "observe", "reflect", "collaborate"},
    "business": {"consume", "queue", "chat", "collaborate"},
    "social": {"chat", "club_activity", "collaborate"},
    "service": {"observe", "request_leave", "collaborate", "queue"},
    "wellbeing": {"rest", "reflect", "club_activity"},
    "general": WORLD_AUTONOMOUS_ACTIONS - {"move", "late", "conflict"},
}


def goal_progress_delta(goal, action, adherence):
    relevant = action in GOAL_RELEVANT_ACTIONS.get(goal.get("category"), GOAL_RELEVANT_ACTIONS["general"])
    if not relevant:
        return 0
    base = {"short": 12, "medium": 5, "long": 2}.get(goal.get("horizon"), 1)
    if adherence == "followed":
        return base
    if adherence == "adjusted":
        return max(1, round(base * 0.7))
    return max(0, round(base * 0.35))


def advance_multiscale_goals_from_outcome(conn, resident_id, goal_ids, action, adherence, world_time, tick_id, outcome_id):
    updates = []
    for horizon, goal_id in goal_ids.items():
        if not goal_id:
            continue
        raw = conn.execute(
            "SELECT * FROM agent_goals WHERE id = ? AND resident_id = ?",
            (goal_id, resident_id),
        ).fetchone()
        if not raw or raw["status"] != "active":
            continue
        goal = dict(raw)
        delta = goal_progress_delta(goal, action, adherence)
        if delta <= 0:
            continue
        before = dict(goal)
        progress = clamp(int(goal["progress"] or 0) + delta)
        status = "completed" if progress >= 100 else "active"
        conn.execute(
            """
            UPDATE agent_goals
            SET progress = ?, status = ?,
                completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (progress, status, status, world_time.isoformat(), goal_id),
        )
        if goal.get("legacy_long_term_goal_id"):
            conn.execute(
                """
                UPDATE long_term_goals
                SET progress = ?, status = ?, last_update_day = ?,
                    completed_at = CASE WHEN ? = 'completed' THEN ? ELSE completed_at END
                WHERE id = ?
                """,
                (
                    progress,
                    status,
                    get_current_day(conn),
                    status,
                    world_time.isoformat(),
                    goal["legacy_long_term_goal_id"],
                ),
            )
        after = dict(conn.execute("SELECT * FROM agent_goals WHERE id = ?", (goal_id,)).fetchone())
        if status == "completed":
            record_goal_revision(
                conn,
                goal_id,
                resident_id,
                "completed",
                before=before,
                after=after,
                reason=f"{action} 行动使目标达到完成阈值",
                trigger_type="plan_outcome",
                tick_id=tick_id,
            )
            conn.execute(
                """
                UPDATE trajectory_episodes
                SET status = 'completed', end_at = ?, outcome_summary = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE resident_id = ? AND goal_id = ?
                """,
                (world_time.isoformat(), f"目标《{goal['title']}》完成", resident_id, goal_id),
            )
            if horizon == "short":
                conn.execute(
                    """
                    UPDATE agent_commitments
                    SET status = 'fulfilled', updated_at = CURRENT_TIMESTAMP
                    WHERE resident_id = ? AND goal_id = ? AND status = 'active'
                    """,
                    (resident_id, goal_id),
                )
        updates.append({"goal_id": goal_id, "horizon": horizon, "delta": delta, "progress": progress, "status": status})
    return updates


def update_trajectory_from_outcome(conn, resident_id, goal_ids, action, location, adherence, world_time, outcome_id):
    for horizon, goal_id in goal_ids.items():
        if not goal_id:
            continue
        row = conn.execute(
            """
            SELECT * FROM trajectory_episodes
            WHERE resident_id = ? AND goal_id = ? AND horizon = ?
            """,
            (resident_id, goal_id, horizon),
        ).fetchone()
        if not row:
            continue
        evidence = load_json_text(row["evidence_json"], {})
        if not isinstance(evidence, dict):
            evidence = {}
        evidence["outcome_count"] = int(evidence.get("outcome_count") or 0) + 1
        evidence["followed_count"] = int(evidence.get("followed_count") or 0) + int(adherence == "followed")
        evidence["last_outcome_id"] = outcome_id
        evidence["last_action"] = action
        evidence["last_location"] = location
        evidence["last_at"] = world_time.isoformat()
        conn.execute(
            """
            UPDATE trajectory_episodes
            SET actual_summary = ?, evidence_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                f"最近在{location}执行 {action}，计划关系：{adherence}",
                json_dumps(evidence, ensure_ascii=False),
                row["id"],
            ),
        )


def record_plan_outcome(conn, agent, plan, step, decision, action, destination, content, world_time, tick_id, day, event_id):
    plan_id = plan.get("_plan_row_id")
    step_key = step.get("step_key") or plan_step_key(step)
    if not plan_id or step.get("plan_state") != "due" or not step_key:
        return None
    planned_action = str(step.get("action") or "")
    planned_location = str(step.get("location") or "")
    relation = str(decision.get("plan_relation") or "continue")
    if action == planned_action and destination == planned_location and relation == "continue":
        adherence = "followed"
        deviation_type = ""
    elif relation in {"adjust", "respond", "rest"}:
        adherence = "adjusted"
        deviation_type = relation
    else:
        adherence = "deviated"
        deviation_type = "action_or_location_changed"
    deviation_reason = ""
    if adherence != "followed":
        notes = decision.get("constraint_notes") or []
        deviation_reason = "；".join(str(note) for note in notes) or str(decision.get("reason") or "")
    goal_ids = {
        "long": step.get("long_goal_id") or plan.get("goal_chain", {}).get("long_goal_id"),
        "medium": step.get("medium_goal_id") or plan.get("goal_chain", {}).get("medium_goal_id"),
        "short": step.get("short_goal_id") or plan.get("goal_chain", {}).get("short_goal_id"),
    }
    cursor = conn.execute(
        """
        INSERT INTO plan_outcomes
        (resident_id, plan_id, long_goal_id, medium_goal_id, short_goal_id,
         tick_id, day, step_key, planned_json, actual_json, adherence,
         deviation_type, deviation_reason, outcome_summary, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(plan_id, step_key) DO NOTHING
        """,
        (
            agent["id"],
            plan_id,
            goal_ids["long"],
            goal_ids["medium"],
            goal_ids["short"],
            tick_id,
            day,
            step_key,
            json_dumps(step, ensure_ascii=False),
            json_dumps({"action": action, "location": destination, "decision": decision}, ensure_ascii=False),
            adherence,
            deviation_type,
            deviation_reason[:240],
            content[:300],
            json_dumps({"world_event_id": event_id}, ensure_ascii=False),
        ),
    )
    outcome = conn.execute(
        "SELECT * FROM plan_outcomes WHERE plan_id = ? AND step_key = ?",
        (plan_id, step_key),
    ).fetchone()
    if not outcome:
        return None
    outcome_id = outcome["id"]
    progress_updates = advance_multiscale_goals_from_outcome(
        conn,
        agent["id"],
        goal_ids,
        action,
        adherence,
        world_time,
        tick_id,
        outcome_id,
    ) if cursor.rowcount else []
    if cursor.rowcount:
        conn.execute(
            """
            UPDATE plan_outcomes
            SET progress_delta = ?, evidence_json = ?
            WHERE id = ?
            """,
            (
                sum(int(item["delta"]) for item in progress_updates),
                json_dumps(
                    {"world_event_id": event_id, "goal_progress": progress_updates},
                    ensure_ascii=False,
                ),
                outcome_id,
            ),
        )
        update_trajectory_from_outcome(
            conn,
            agent["id"],
            goal_ids,
            action,
            destination,
            adherence,
            world_time,
            outcome_id,
        )
    return {
        "id": outcome_id,
        "adherence": adherence,
        "deviation_type": deviation_type,
        "goal_progress": progress_updates,
    }


def should_generate_observed_agent_detail(conn, resident_id, world_time):
    branch_key = active_world_branch_key(conn)
    row = conn.execute(
        """
        SELECT created_at FROM world_event_stream
        WHERE event_type = 'observer_model_detail' AND resident_id = ?
          AND branch_key = ?
        ORDER BY id DESC LIMIT 1
        """,
        (resident_id, branch_key),
    ).fetchone()
    latest_at = parse_world_datetime(row["created_at"]) if row else None
    if latest_at and (world_time - latest_at).total_seconds() < OBSERVER_MODEL_DETAIL_COOLDOWN_SECONDS:
        return False
    return True


def generate_observed_agent_detail(conn, agent, step, world_time, tick_id, base_event, day, slot):
    if not should_generate_observed_agent_detail(conn, agent["id"], world_time):
        return None
    if not consume_auto_model_budget(conn, "observer", resident_id=agent["id"]):
        return None
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    prompt = f"""
你是校园平行世界的局部观察镜头。用户正在观察这个 Agent，请生成一条短小、具体、可被记录的观察细节。

世界时间：{world_time.strftime('%Y-%m-%d %H:%M')}
Agent：{agent['name']}，{agent['role']}
当前位置：{agent['location']}
长期目标：{agent['goal']}
当前计划步骤：{json_dumps(step, ensure_ascii=False)}

要求：
- 只写 1 句中文，80 字以内。
- 用第三人称描述可观察行为或一瞬间的想法外显，不要写系统解释。
- 不要编造超自然或大规模事件。
"""
    try:
        raw = ask_llm(prompt)
        detail = re.sub(r"\s+", " ", raw).strip().strip('"“”')[:160]
        if not detail:
            raise ValueError("empty observer detail")
        detail_event = append_world_event(
            conn,
            "observer_model_detail",
            f"{agent['name']}的被观察细节",
            detail,
            tick_id=tick_id,
            resident_id=agent["id"],
            location=agent["location"],
            payload={"base_event_id": base_event["id"], "plan_step": step, "trigger": "observer_focus"},
            day=day,
            slot=slot,
        )
        log_model_call(
            conn,
            "observer",
            status="success",
            resident_id=agent["id"],
            related_event_id=detail_event["id"],
            model_name=model_name,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return detail_event
    except Exception as exc:
        logger.warning("Observer LLM detail failed for resident %s", agent["id"], exc_info=True)
        log_model_call(conn, "observer", status=f"failed:{type(exc).__name__}", resident_id=agent["id"], related_event_id=base_event["id"], model_name=model_name)
        return None


def build_runtime_perception(conn, agent, world_time, day, slot, plan, step, observed):
    env = dict(get_campus_environment(conn, day))
    branch_key = active_world_branch_key(conn)
    cognitive_context = get_agent_cognitive_context(
        conn,
        agent["id"],
        branch_key=branch_key,
        limit=8,
    )
    location_counts = {
        row["location"]: row["count"]
        for row in conn.execute("SELECT location, COUNT(*) AS count FROM residents GROUP BY location").fetchall()
    }
    hour = world_time.hour
    open_locations = [location for location in VALID_LOCATIONS if is_location_open_at_hour(location, hour)]
    realistic_options = [
        location
        for location, _ in location_options_for_context(
            agent["role"],
            hour,
            env.get("weather"),
            agent["location"],
            conn=None,
            env=None,
            agent=agent,
        )
    ]
    schedule_rules = active_schedule_rules(conn, agent["role"], hour, env)
    relationships = conn.execute(
        """
        SELECT r.to_resident_id, residents.name, r.affinity, r.trust, r.cooperation, r.conflict, r.tension
        FROM relationship_dynamics r
        JOIN residents ON residents.id = r.to_resident_id
        WHERE r.from_resident_id = ?
        ORDER BY r.interaction_count DESC, r.trust DESC
        LIMIT 5
        """,
        (agent["id"],),
    ).fetchall()
    profile = conn.execute("SELECT energy, time_budget, mood, skills, strategy FROM agent_profiles WHERE resident_id = ?", (agent["id"],)).fetchone()
    body_state = get_body_state(conn, agent["id"])
    return {
        "world_time": world_time.isoformat(),
        "slot": slot,
        "agent_location": agent["location"],
        "agent_profile": {
            "personality": agent.get("personality", ""),
            "money": agent.get("money", 0),
            "energy": profile["energy"] if profile else None,
            "time_budget": profile["time_budget"] if profile else None,
            "mood": profile["mood"] if profile else "",
            "trait_bias": action_noise_for_agent(agent),
        },
        "body_state": body_state or {},
        "local_crowd": int(location_counts.get(agent["location"], 0)),
        "open_locations": open_locations,
        "realistic_location_options": realistic_options,
        "available_actions": sorted(WORLD_AUTONOMOUS_ACTIONS),
        "active_schedule_rules": [
            {
                "action_type": row.get("action_type"),
                "location": row.get("location"),
                "base_weight": row.get("base_weight"),
                "description": row.get("description"),
            }
            for row in schedule_rules[:6]
        ],
        "relationship_context": rows_to_dicts(relationships),
        "realism_constraints": {
            "hour": hour,
            "deep_night": 0 <= hour < 6,
            "bad_weather": any(token in str(env.get("weather") or "") for token in ("雨", "雷", "雪", "大风")),
            "note": "行动可以随机偏离计划，但需符合时间、天气、空间开放、角色身份、个体差异和关系网络。",
        },
        "environment": {
            "weather": env.get("weather"),
            "temperature": env.get("temperature"),
            "time_slot": env.get("time_slot"),
            "rainfall": env.get("rainfall"),
        },
        "plan_intent": plan.get("intent", ""),
        "goal_chain": plan.get("goal_chain", {}),
        "plan_step": step,
        "local_observations": cognitive_context["observations"],
        "beliefs": cognitive_context["beliefs"],
        "spatial_memories": cognitive_context["spatial_memories"],
        "adaptive_memories": cognitive_context["adaptive_memories"],
        "learned_strategies": cognitive_context["strategy_states"],
        "norm_beliefs": cognitive_context["norm_beliefs"],
        "received_information": cognitive_context["received_information"],
        "information_boundary": (
            "仅包含亲历、自身状态、局部观察、已接收消息和由这些证据形成的信念；"
            "不包含校园全局事件或系统聚合真相。"
        ),
    }


def normalize_runtime_decision(payload, fallback_step, fallback_location, fallback_goal):
    payload = payload if isinstance(payload, dict) else {}
    action = str(payload.get("action") or fallback_step.get("action") or "observe").strip().lower()
    if action not in WORLD_AUTONOMOUS_ACTIONS:
        action = "observe"
    location = str(payload.get("location") or fallback_step.get("location") or fallback_location or "校园").strip()
    if location not in VALID_LOCATIONS:
        location = fallback_location if fallback_location in VALID_LOCATIONS else "校园"
    goal = str(payload.get("goal") or fallback_step.get("goal") or fallback_goal or "观察校园环境").strip()[:180]
    reason = str(payload.get("reason") or goal).strip()[:220]
    relation = str(payload.get("plan_relation") or "continue").strip().lower()
    if relation not in {"continue", "adjust", "respond", "rest"}:
        relation = "continue"
    return {
        "action": action,
        "location": location,
        "goal": goal,
        "reason": reason,
        "plan_relation": relation,
        "mode": "llm-autonomous-v1",
    }


def apply_realism_constraints_to_decision(conn, agent, decision, perception, world_time):
    decision = dict(decision or {})
    hour = world_time.hour
    env = perception.get("environment", {}) if isinstance(perception, dict) else {}
    weather = env.get("weather", "")
    action = str(decision.get("action") or "observe")
    destination = str(decision.get("location") or agent["location"])
    role = str(agent.get("role") or "")
    notes = []

    action_location_defaults = {
        "attend_class": "教学楼",
        "queue": "食堂",
        "consume": "食堂" if 6 <= hour < 14 or 17 <= hour < 21 else "商业街",
        "rest": "宿舍区",
        "club_activity": "操场",
        "request_leave": "校务处",
    }
    if action in action_location_defaults:
        preferred = action_location_defaults[action]
        if is_location_open_at_hour(preferred, hour):
            destination = preferred

    if destination not in VALID_LOCATIONS:
        notes.append("目的地不存在，改为当前位置观察")
        destination = agent["location"] if agent["location"] in VALID_LOCATIONS else "宿舍区"
        action = "observe"

    if not is_location_open_at_hour(destination, hour):
        adjusted = realistic_location_for_context(role, hour, weather, current_location=agent["location"])
        notes.append(f"{destination}当前不适合进入，调整到{adjusted}")
        destination = adjusted
        action = "reflect" if destination == "宿舍区" and (hour < 6 or hour >= 22) else "observe"

    if 0 <= hour < 6 and role_group(role) == "student" and destination != "宿舍区":
        if random.random() < 0.88:
            notes.append("深夜学生活动概率较低，回到宿舍区休息")
            destination = "宿舍区"
            action = "reflect"

    if destination == "操场" and any(token in str(weather or "") for token in ("雨", "雷", "雪", "大风")):
        if random.random() < 0.78:
            adjusted = realistic_location_for_context(role, hour, weather, current_location=agent["location"])
            notes.append(f"{weather}降低户外活动意愿，改到{adjusted}")
            destination = adjusted
            action = "observe" if action == "move" else action

    if action == "move" and destination == agent["location"]:
        action = "observe"
        notes.append("已在目标地点，改为现场观察")

    if action == "attend_class" and destination != "教学楼":
        action = "observe"
        notes.append("课程活动无法在当前空间完成，改为观察学习状态")
    if action in {"queue", "consume"} and destination not in {"食堂", "商业街"}:
        action = "observe"
        notes.append("消费/排队行为与当前空间不匹配，改为观察")
    if action == "club_activity" and destination != "操场":
        action = "chat"
        notes.append("社团活动转为室内轻量交流")
    if action == "request_leave" and not is_location_open_at_hour("校务处", hour):
        action = "reflect"
        destination = "宿舍区" if is_location_open_at_hour("宿舍区", hour) else agent["location"]
        notes.append("校务处未开放，请假改为整理申请理由")

    if random.random() < 0.04:
        alternate = realistic_location_for_context(role, hour, weather, current_location=agent["location"])
        if alternate != destination:
            notes.append(f"受到临时状态扰动，短暂偏离计划到{alternate}")
            destination = alternate
            action = "move" if alternate != agent["location"] else "observe"
            decision["plan_relation"] = "adjust"

    decision["action"] = action
    decision["location"] = destination
    if notes:
        decision["constraint_notes"] = notes
        reason = str(decision.get("reason") or "")
        decision["reason"] = f"{reason}（现实约束：{'；'.join(notes)}）"[:220]
    return decision


def apply_wellbeing_priority_to_decision(conn, agent, decision, world_time):
    body_state = get_body_state(conn, agent["id"])
    if not body_state:
        return decision
    decision = dict(decision or {})
    action = str(decision.get("action") or "observe")
    destination = str(decision.get("location") or agent["location"])
    hour = world_time.hour
    hunger = float(body_state.get("hunger") or 0)
    fatigue = float(body_state.get("fatigue") or 0)
    sleep_debt = float(body_state.get("sleep_debt") or 0)
    health = float(
        body_state.get("health", 100)
        if body_state.get("health") is not None
        else 100
    )
    attention = float(
        body_state.get("attention", 100)
        if body_state.get("attention") is not None
        else 100
    )

    def recovery_decision(next_action, next_location, goal, reason):
        return {
            **decision,
            "action": next_action,
            "location": next_location,
            "goal": goal,
            "reason": reason,
            "plan_relation": "rest",
            "mode": f"{decision.get('mode') or 'rule'}+wellbeing-priority-v1",
            "wellbeing_override": {
                "previous_action": action,
                "previous_location": destination,
                "hunger": hunger,
                "fatigue": fatigue,
                "sleep_debt": sleep_debt,
                "health": health,
                "attention": attention,
            },
        }

    hunger_instruction = hunger_recovery_instruction(
        action=action,
        destination=destination,
        current_location=agent["location"],
        hunger=hunger,
        hour=hour,
        is_location_open=is_location_open_at_hour,
    )
    if hunger_instruction:
        return recovery_decision(
            hunger_instruction["action"],
            hunger_instruction["location"],
            hunger_instruction["goal"],
            hunger_instruction["reason"],
        )

    if health < 35 and action != "rest":
        return recovery_decision(
            "rest",
            "宿舍区",
            "健康状态偏低，先回宿舍休息恢复",
            "健康状态不足以支撑普通行动，优先进入恢复节奏。",
        )

    if fatigue >= 88 and action != "rest":
        return recovery_decision(
            "rest",
            "宿舍区",
            "疲劳过高，先休息恢复体能",
            "疲劳已达到行动风险阈值，暂缓原计划并回宿舍休息。",
        )

    if sleep_debt >= 85 and action not in {"rest", "reflect"}:
        return recovery_decision(
            "rest",
            "宿舍区",
            "睡眠债过高，优先补觉恢复注意力",
            "睡眠债过高会持续拖累健康与注意力，先补充睡眠。",
        )

    if attention < 15 and action in {"attend_class", "collaborate", "observe"}:
        return recovery_decision(
            "rest",
            "宿舍区",
            "注意力不足，先恢复后再继续学习或协作",
            "注意力已低于可靠行动阈值，先进入恢复动作。",
        )

    return decision


def fallback_runtime_decision(agent, step, reason, mode):
    plan_state = str(step.get("plan_state") or "")
    plan_relation = "continue" if plan_state in {"due", "waiting", "unplanned"} else ("rest" if plan_state == "completed" else "continue")
    return {
        "action": str(step.get("action") or "observe"),
        "location": str(step.get("location") or agent["location"]),
        "goal": str(step.get("goal") or "观察校园环境"),
        "reason": reason,
        "plan_relation": plan_relation,
        "mode": mode,
    }


def build_autonomous_tick_decision(conn, agent, perception, step):
    if step.get("plan_state") != "due":
        return fallback_runtime_decision(agent, step, "计划步骤尚未到点或已完成，按当前位置进行轻量观察。", "rule-waiting-v1")
    if not consume_auto_model_budget(conn, "autonomous_decision", resident_id=agent["id"]):
        if not is_llm_configured():
            return fallback_runtime_decision(agent, step, "当前世界使用规则决策，按个人计划继续行动。", "rule-unconfigured-v1")
        return fallback_runtime_decision(agent, step, "自动模型预算不足，按原计划执行。", "rule-budget-fallback-v1")
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    prompt = f"""
你是校园平行世界中一个 Agent 的局部自主循环决策器。

请根据当前环境、近期事件、Agent 长期目标和 8 小时计划步骤，决定这个 tick 是否继续计划、轻微调整、响应事件或休息。

Agent:
- id: {agent['id']}
- name: {agent['name']}
- role: {agent['role']}
- current_location: {agent['location']}
- long_goal: {agent['goal']}

感知上下文：
{json_dumps(perception, ensure_ascii=False)}

现实约束：
- 必须尊重当前时间段、空间开放时间、天气和拥挤度。
- 深夜学生通常在宿舍区，食堂、商业街、校务处等关闭或低活跃空间不应成为普通目的地。
- 行动可以有随机性，可以轻微偏离计划，但偏离需要有可解释原因。
- 如果计划不合时宜，应选择 rest 或 adjust，而不是机械执行。

只返回 JSON，不要解释。格式：
{{
  "action": "move|observe|chat|reflect|attend_class|queue|consume|rest|club_activity|conflict|collaborate|late|request_leave",
  "location": "只能从 {list(VALID_LOCATIONS)} 中选择",
  "goal": "本 tick 的具体目标，80 字以内",
  "reason": "为什么这样做，100 字以内",
  "plan_relation": "continue|adjust|respond|rest"
}}
"""
    try:
        raw = ask_llm(prompt)
        payload = extract_json(raw)
        decision = normalize_runtime_decision(payload, step, agent["location"], step.get("goal"))
        log_model_call(
            conn,
            "autonomous_decision",
            status="success",
            resident_id=agent["id"],
            model_name=model_name,
            prompt_version="autonomous-loop-v2",
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return decision
    except Exception as exc:
        logger.warning("Autonomous tick decision failed for resident %s", agent["id"], exc_info=True)
        log_model_call(conn, "autonomous_decision", status=f"failed:{type(exc).__name__}", resident_id=agent["id"], model_name=model_name, prompt_version="autonomous-loop-v2")
        return fallback_runtime_decision(agent, step, "自主决策失败，按原计划执行。", "rule-error-fallback-v1")


def nearby_interaction_target(conn, agent_id, location):
    row = conn.execute(
        """
        SELECT id, name FROM residents
        WHERE id != ? AND location = ?
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (agent_id, location),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        """
        SELECT id, name FROM residents
        WHERE id != ?
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (agent_id,),
    ).fetchone()
    return dict(row) if row else None


def maybe_create_social_commitment(conn, agent_id, target, location):
    existing = conn.execute(
        """
        SELECT * FROM agent_commitments
        WHERE resident_id = ? AND counterparty_resident_id = ?
          AND commitment_type = 'social_collaboration' AND status = 'active'
        ORDER BY id DESC LIMIT 1
        """,
        (agent_id, target["id"]),
    ).fetchone()
    if existing:
        return dict(existing)
    short_goal = conn.execute(
        """
        SELECT * FROM agent_goals
        WHERE resident_id = ? AND horizon = 'short' AND status = 'active'
        ORDER BY priority DESC, id LIMIT 1
        """,
        (agent_id,),
    ).fetchone()
    if not short_goal:
        return None
    now = get_world_now()
    cursor = conn.execute(
        """
        INSERT INTO agent_commitments
        (resident_id, goal_id, counterparty_resident_id, commitment_type, title,
         start_at, due_at, status, importance, flexibility, visibility)
        VALUES (?, ?, ?, 'social_collaboration', ?, ?, ?, 'active', 68, 55, 'shared')
        """,
        (
            agent_id,
            short_goal["id"],
            target["id"],
            f"继续与{target['name']}推进在{location}形成的协作",
            now.isoformat(),
            (now + timedelta(days=3)).isoformat(),
        ),
    )
    return dict(conn.execute("SELECT * FROM agent_commitments WHERE id = ?", (cursor.lastrowid,)).fetchone())


def apply_runtime_social_effect(conn, agent, action, location, day):
    target = nearby_interaction_target(conn, agent["id"], location)
    if not target:
        return None
    if action in {"chat", "club_activity", "collaborate"}:
        change = evolve_relationship(conn, agent["id"], target["id"], action, f"{location}发生协作或交流", 3, 4, -1)
        commitment = maybe_create_social_commitment(conn, agent["id"], target, location) if action == "collaborate" else None
        return {
            "target_id": target["id"],
            "target_name": target["name"],
            "effect": "positive",
            "relationship": change,
            "commitment": commitment,
        }
    if action == "conflict":
        change = evolve_relationship(conn, agent["id"], target["id"], "conflict", f"{location}发生轻微摩擦", -3, -2, 4)
        add_event(conn, day, "world_agent_conflict", f"{agent['name']} 与 {target['name']} 在{location}出现轻微摩擦。")
        return {"target_id": target["id"], "target_name": target["name"], "effect": "conflict", "relationship": change}
    return None


def describe_runtime_action(conn, agent, action, destination, goal, day, observed=False):
    social_effect = None
    importance = 1
    if action == "attend_class":
        content = f"{agent['name']} 在{destination}参与课程活动，围绕「{goal}」记录课堂进展。"
        event_type = "world_agent_attend_class"
    elif action == "queue":
        content = f"{agent['name']} 在{destination}排队等待服务，资源压力让当前节奏变慢，目标：{goal}。"
        event_type = "world_agent_queue"
    elif action == "consume":
        content = f"{agent['name']} 在{destination}完成一次校园消费或服务使用，目标：{goal}。"
        event_type = "world_agent_consume"
    elif action == "rest":
        content = f"{agent['name']} 在{destination}休息恢复精力，暂时放慢行动节奏，目标：{goal}。"
        event_type = "world_agent_rest"
    elif action == "club_activity":
        content = f"{agent['name']} 在{destination}参加社团或课余活动，校园互动热度被轻微带动，目标：{goal}。"
        event_type = "world_agent_club_activity"
        social_effect = apply_runtime_social_effect(conn, agent, action, destination, day)
    elif action == "conflict":
        content = f"{agent['name']} 在{destination}因拥挤、资源或意见差异出现轻微冲突，目标：{goal}。"
        event_type = "world_agent_conflict"
        social_effect = apply_runtime_social_effect(conn, agent, action, destination, day)
        importance = max(importance, 3)
    elif action == "collaborate":
        content = f"{agent['name']} 在{destination}与他人协作推进任务，目标：{goal}。"
        event_type = "world_agent_collaborate"
        social_effect = apply_runtime_social_effect(conn, agent, action, destination, day)
        importance = max(importance, 2)
    elif action == "late":
        content = f"{agent['name']} 到达{destination}的节奏偏慢，可能错过部分安排，目标：{goal}。"
        event_type = "world_agent_late"
        importance = max(importance, 2)
    elif action == "request_leave":
        content = f"{agent['name']} 在{destination}整理或提交请假/事务申请，目标：{goal}。"
        event_type = "world_agent_request_leave"
    elif action == "chat":
        content = f"{agent['name']} 在{agent['location']}围绕{destination}附近的校园状态进行轻量交流，目标：{goal}。"
        event_type = "world_agent_chat"
        social_effect = apply_runtime_social_effect(conn, agent, action, agent["location"], day)
    elif action == "reflect":
        content = f"{agent['name']} 在{agent['location']}整理当前节奏和个人状态，目标：{goal}。"
        event_type = "world_agent_reflect"
    else:
        focus = destination if destination in VALID_LOCATIONS else "校园状态"
        content = f"{agent['name']} 在{agent['location']}观察{focus}，目标：{goal}。"
        event_type = "world_agent_observe"
    add_event(conn, day, event_type, content)
    add_memory_once(conn, agent["id"], day, content, importance=importance, source="world_tick")
    return content, event_type, social_effect


def process_world_agent_tick(conn, agent, world_time, tick_id, day, slot, observed=False, parent_event_id=None):
    state_before = get_agent_module_state(conn, agent["id"])
    plan = get_current_agent_plan(conn, agent["id"], world_time) or {}
    step = choose_plan_step(plan, world_time, agent["location"])
    perception = build_runtime_perception(conn, agent, world_time, day, slot, plan, step, observed)
    decision = build_autonomous_tick_decision(conn, agent, perception, step)
    decision = apply_realism_constraints_to_decision(conn, agent, decision, perception, world_time)
    decision = apply_wellbeing_priority_to_decision(conn, agent, decision, world_time)
    action = str(decision.get("action") or "observe")
    destination = str(decision.get("location") or agent["location"])
    goal = str(decision.get("goal") or plan.get("intent") or "观察校园环境")
    destination_actions = {
        "attend_class",
        "queue",
        "consume",
        "rest",
        "club_activity",
        "request_leave",
        "collaborate",
        "conflict",
        "late",
    }
    if (
        action in destination_actions
        and destination in VALID_LOCATIONS
        and destination != agent["location"]
        and spatial_runtime_available(conn)
    ):
        decision["deferred_action"] = action
        decision["reason"] = (
            f"先前往{destination}，到达后再执行 {action}。"
        )
        action = "move"
        decision["action"] = action
    title = f"{agent['name']}正在{destination}行动"

    try:
        action_execution = begin_world_action_execution(
            conn,
            agent["id"],
            action,
            destination,
            world_time,
            tick_id=tick_id,
            parent_event_id=parent_event_id,
            settlement_mode="active" if step.get("plan_state") == "due" else "passive",
        )
        if action_execution["status"] in {"rejected", "failed"}:
            if action_execution["status"] == "failed":
                settlement = settle_world_action_resources(conn, action_execution, success=False)
                event_type = "agent_action_failed"
                title = f"{agent['name']}的行动未成功"
            else:
                settlement = finalize_rejected_action_execution(conn, action_execution)
                event_type = "agent_action_rejected"
                title = f"{agent['name']}的行动条件不足"
            content = (
                f"{agent['name']}未能执行 {action}：{action_execution['failure_reason']}。"
                "本次结算保留了结构化失败原因，Agent 可在后续 tick 选择替代行动。"
            )
            execution = {
                "action": action,
                "result": {"description": content},
                "success": False,
                "failure_code": action_execution["failure_code"],
                "causal_settlement": settlement,
                "plan_step": step,
                "runtime_decision": decision,
            }
            state_after = get_agent_module_state(conn, agent["id"])
            event = append_world_event(
                conn,
                event_type,
                title,
                content,
                tick_id=tick_id,
                resident_id=agent["id"],
                location=destination if destination in VALID_LOCATIONS else agent["location"],
                payload={
                    "action": action,
                    "goal": goal,
                    "failure_code": action_execution["failure_code"],
                    "failure_reason": action_execution["failure_reason"],
                    "preconditions": action_execution["preconditions"],
                    "action_execution_id": action_execution["id"],
                    "causal_settlement": settlement,
                },
                day=day,
                slot=slot,
                source_type="world_action_execution",
                source_id=action_execution["id"],
                parent_event_id=parent_event_id,
                rule_version=action_execution["rule"]["rule_version"],
            )
            link_action_execution_event(conn, action_execution["id"], event["id"])
            record_simulation_log(
                conn,
                agent["id"],
                perception,
                {
                    "decision": {
                        "action": action,
                        "reason": decision.get("reason") or goal,
                        "tool_input": {"destination": destination},
                    },
                    "memory_context": {"memories": []},
                },
                execution,
                {},
                state_before,
                state_after,
                tick_id=tick_id,
            )
            add_memory_once(
                conn,
                agent["id"],
                day,
                content,
                importance=2,
                source="world_action_settlement",
            )
            conn.commit()
            return {
                "resident_id": agent["id"],
                "success": True,
                "action_success": False,
                "event": event,
                "action_execution_id": action_execution["id"],
                "plan_outcome": None,
            }

        if action == "move" and destination in VALID_LOCATIONS and destination != agent["location"]:
            result = move_resident(conn, agent["id"], destination, commit=False)
            content = result["description"]
        elif action in destination_actions and destination in VALID_LOCATIONS and destination != agent["location"]:
            move_resident(conn, agent["id"], destination, commit=False)
            agent = dict(agent)
            agent["location"] = destination
            content, _, social_effect = describe_runtime_action(conn, agent, action, destination, goal, day, observed=observed)
        else:
            content, _, social_effect = describe_runtime_action(conn, agent, action, destination, goal, day, observed=observed)
        execution = {"action": action, "result": {"description": content}, "success": True, "plan_step": step, "runtime_decision": decision}
        if "social_effect" in locals() and social_effect:
            execution["social_effect"] = social_effect
        settlement = settle_world_action_resources(conn, action_execution, success=True)
        execution["causal_settlement"] = settlement
        state_after = get_agent_module_state(conn, agent["id"])
        conn.execute(
            """
            UPDATE agent_profiles
            SET current_task = ?, perception = ?
            WHERE resident_id = ?
            """,
            (goal[:120], json_dumps(perception, ensure_ascii=False), agent["id"]),
        )
        event = append_world_event(
            conn,
            "agent_tick",
            title,
            content,
            tick_id=tick_id,
            resident_id=agent["id"],
            location=destination if destination in VALID_LOCATIONS else agent["location"],
            payload={
                "action": action,
                "goal": goal,
                "observed": observed,
                "plan_step": step,
                "goal_chain": plan.get("goal_chain", {}),
                "runtime_decision": decision,
                "social_effect": execution.get("social_effect"),
                "action_taxonomy": "world-runtime-v3",
                "action_execution_id": action_execution["id"],
                "preconditions": action_execution["preconditions"],
                "causal_settlement": settlement,
            },
            day=day,
            slot=slot,
            source_type="world_action_execution",
            source_id=action_execution["id"],
            parent_event_id=parent_event_id,
            rule_version=action_execution["rule"]["rule_version"],
        )
        link_action_execution_event(conn, action_execution["id"], event["id"])
        delayed_effect_ids = enqueue_world_delayed_effects(
            conn,
            action_execution,
            event["id"],
            world_time,
        )
        execution["delayed_effect_ids"] = delayed_effect_ids
        record_simulation_log(
            conn,
            agent["id"],
            perception,
            {
                "decision": {
                    "action": action,
                    "reason": decision.get("reason") or goal,
                    "tool_input": {"destination": destination},
                },
                "memory_context": {"memories": []},
            },
            execution,
            {},
            state_before,
            state_after,
            tick_id=tick_id,
        )
        plan_outcome = record_plan_outcome(
            conn,
            agent,
            plan,
            step,
            decision,
            action,
            destination,
            content,
            world_time,
            tick_id,
            day,
            event["id"],
        )
        if step.get("plan_state") == "due":
            mark_plan_step_executed(conn, plan, step, world_time, {"action": action, "location": destination, "goal": goal, "mode": decision.get("mode")})
        if observed:
            generate_observed_agent_detail(conn, agent, step, world_time, tick_id, event, day, slot)
        conn.commit()
        return {"resident_id": agent["id"], "success": True, "event": event, "plan_outcome": plan_outcome}
    except Exception as exc:
        conn.rollback()
        error_content = f"{agent['name']} 的 world tick 行动失败，已保留状态：{type(exc).__name__}。"
        event = append_world_event(
            conn,
            "agent_tick_failed",
            "Agent tick 失败",
            error_content,
            tick_id=tick_id,
            resident_id=agent["id"],
            location=agent["location"],
            payload={"error": str(exc), "action": action, "goal": goal},
            day=day,
            slot=slot,
            source_type="agent_action",
            source_id=agent["id"],
            parent_event_id=parent_event_id,
            rule_version="world-runtime-v3",
        )
        conn.commit()
        return {"resident_id": agent["id"], "success": False, "event": event, "error": str(exc)}


def maybe_publish_campus_news_from_world_window(conn, world_time, tick_id=None, day=None):
    """Publish campus news from the autonomous runtime, prioritizing unusual emergent material."""
    ensure_agent_news_system(conn)
    ensure_world_runtime_tables(conn)
    day = day or get_current_day(conn)
    branch_key = active_world_branch_key(conn)
    window_start, window_end, source_slot = previous_completed_world_window(world_time)
    source_window_key = f"{window_start.date().isoformat()} {source_slot}"
    existing = conn.execute(
        """
        SELECT id FROM world_event_stream
        WHERE event_type = 'campus_news_published'
          AND branch_key = ?
          AND payload LIKE ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (branch_key, f'%"source_window_key": "{source_window_key}"%'),
    ).fetchone()
    if existing:
        return {"skipped": True, "reason": "already_published", "source_window_key": source_window_key}

    candidates = []
    seen_residents = set()
    for candidate in collect_campus_news_candidates(conn, day, source_slot):
        resident_id = int(candidate["resident_id"])
        if resident_id in seen_residents:
            continue
        seen_residents.add(resident_id)
        candidates.append(candidate)
        if len(candidates) >= 3:
            break

    if not candidates:
        event = append_world_event(
            conn,
            "campus_news_skipped",
            "校园新闻本窗口未发布",
            f"{source_slot} 暂无新的可发布发现，校园日报继续等待 runtime 事件。",
            tick_id=tick_id,
            payload={
                "source_window_key": source_window_key,
                "source_window_start": window_start.isoformat(),
                "source_window_end": window_end.isoformat(),
                "source_slot": source_slot,
                "reason": "no_new_agent_material",
                "retryable": True,
            },
            day=day,
            slot=source_slot,
        )
        return {"skipped": True, "reason": "no_new_agent_material", "event_id": event["id"], "source_window_key": source_window_key}

    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    published = []
    failed = []
    for candidate in candidates:
        action = candidate.get("action") or "observe"
        payload = candidate.get("payload") if isinstance(candidate.get("payload"), dict) else {}
        goal = payload.get("goal") or payload.get("runtime_decision", {}).get("goal") or "推进校园生活"
        source_text = f"{candidate['title']}：{candidate['content']}"
        headline = campus_news_headline(candidate["category"], candidate["location"], candidate["name"])
        content = None
        prompt = f"""
你是《校园世界时报》的运行时观察记者。请根据校园平行世界刚出现的事实材料，写一则 90 到 150 字、具有现场感和可读性的中文校园快讯。

时间窗口：{source_slot}
新闻类型：{candidate['category']}
人物：{candidate['name']}（{candidate['role']}）
地点：{candidate['location'] or '校园'}
动作类型：{action}
行动目标：{goal}
事实材料：{source_text}

要求：
- 使用第三人称、客观新闻口吻。
- 第一两句直接写清人物在什么地点做了什么，不要用“系统捕捉到”“值得记录”“发布最新进展”等套话起笔。
- 随后写出行动造成的具体变化、反应或悬念；结尾说明为什么值得继续关注。
- 句式自然，有长短变化，避免连续重复人物全称、地点和“校园”。
- 优先呈现突发异常、关系风向、反常行为、群体现象、内心发现或校园环境变化。
- 只能基于事实材料写，不要编造材料中没有的人物关系或因果。
- 不要写标题、JSON、Markdown、口号或解释，只输出新闻正文。
"""
        model_configured = is_llm_configured()
        if consume_auto_model_budget(conn, "campus_news", resident_id=candidate["resident_id"]):
            try:
                raw = ask_llm(prompt)
                content = re.sub(r"\s+", " ", raw).strip().strip('"“”')
                if not content or content.startswith(("{", "[")):
                    raise ValueError("invalid campus news content")
                log_model_call(
                    conn,
                    "campus_news",
                    status="success",
                    resident_id=candidate["resident_id"],
                    related_event_id=candidate.get("source_event_id"),
                    model_name=model_name,
                    prompt_version="campus-news-runtime-v2",
                    input_tokens=max(1, len(prompt) // 4),
                    output_tokens=max(1, len(raw) // 4),
                )
            except Exception as exc:
                logger.warning("Campus news generation failed for resident %s", candidate["resident_id"], exc_info=True)
                failed.append({"resident_id": candidate["resident_id"], "reason": type(exc).__name__})
                log_model_call(
                    conn,
                    "campus_news",
                    status=f"failed:{type(exc).__name__}",
                    resident_id=candidate["resident_id"],
                    related_event_id=candidate.get("source_event_id"),
                    model_name=model_name,
                    prompt_version="campus-news-runtime-v2",
                )
        elif model_configured:
            failed.append({"resident_id": candidate["resident_id"], "reason": "budget_exhausted"})
        if not content:
            content = fallback_campus_news_content(candidate)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO agent_news_posts
            (day, resident_id, source_slot, source_event_id, news_value, headline, content)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day,
                candidate["resident_id"],
                source_slot,
                candidate.get("source_event_id"),
                candidate["score"],
                headline,
                content[:500],
            ),
        )
        if cursor.rowcount:
            published.append(
                {
                    "resident_id": candidate["resident_id"],
                    "headline": headline,
                    "category": candidate["category"],
                    "source_event_id": candidate.get("source_event_id"),
                    "score": candidate["score"],
                }
            )

    if published:
        title = "校园新闻已自动发布"
        labels = "、".join(sorted({item["category"] for item in published}))
        content = f"{source_slot} 窗口从 runtime 事件中发布 {len(published)} 条校园快讯，类型包括：{labels}。"
        event_type = "campus_news_published"
    else:
        title = "校园新闻生成未完成"
        content = f"{source_slot} 窗口没有成功生成校园快讯，世界运行继续。"
        event_type = "campus_news_skipped"
    event = append_world_event(
        conn,
        event_type,
        title,
        content,
        tick_id=tick_id,
        payload={
            "source_window_key": source_window_key,
            "source_window_start": window_start.isoformat(),
            "source_window_end": window_end.isoformat(),
            "source_slot": source_slot,
            "published": published,
            "failed": failed,
        },
        day=day,
        slot=source_slot,
    )
    return {
        "skipped": not bool(published),
        "published_count": len(published),
        "failed_count": len(failed),
        "event_id": event["id"],
        "source_window_key": source_window_key,
    }


def select_world_tick_agents(conn, runtime):
    movement_join = ""
    movement_column = "'idle' AS movement_status"
    if spatial_runtime_available(conn):
        movement_join = (
            "LEFT JOIN agent_spatial_states spatial "
            "ON spatial.resident_id = r.id"
        )
        movement_column = "COALESCE(spatial.movement_status, 'idle') AS movement_status"
    lifecycle_join = ""
    lifecycle_filter = ""
    if population_runtime_available(conn):
        lifecycle_join = (
            "LEFT JOIN population_profiles lifecycle "
            "ON lifecycle.resident_id = r.id"
        )
        lifecycle_filter = (
            "WHERE lifecycle.resident_id IS NULL "
            "OR lifecycle.lifecycle_status = 'active'"
        )
    agents = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT r.id, r.name, r.role, r.personality, r.goal, r.money,
                   r.location, p.strategy, {movement_column}
            FROM residents r
            LEFT JOIN agent_profiles p ON p.resident_id = r.id
            {movement_join}
            {lifecycle_join}
            {lifecycle_filter}
            ORDER BY r.id
            """
        ).fetchall()
    ]
    if not agents:
        return [], int(runtime.get("current_agent_cursor", 0) or 0), set()
    eligible_agents = [
        agent
        for agent in agents
        if agent["movement_status"] not in ACTIVE_MOVEMENT_STATUSES
    ]
    if not eligible_agents:
        return [], int(runtime.get("current_agent_cursor", 0) or 0), set()
    focused_agent_ids, _ = get_recent_observer_focus(conn)
    focused_set = set(focused_agent_ids)
    agent_by_id = {agent["id"]: agent for agent in eligible_agents}
    per_tick = bounded_agent_batch_size(
        runtime.get("agents_per_tick", 3),
        len(eligible_agents),
        seed=(
            runtime.get("last_tick_completed_at")
            or runtime.get("world_time")
            or runtime.get("current_agent_cursor", 0)
        ),
    )
    selected = [
        agent_by_id[agent_id]
        for agent_id in focused_agent_ids
        if agent_id in agent_by_id
    ][:per_tick]
    cursor = int(runtime.get("current_agent_cursor", 0) or 0) % len(eligible_agents)
    next_cursor = cursor
    while len(selected) < per_tick and len(selected) < len(eligible_agents):
        candidate = eligible_agents[next_cursor % len(eligible_agents)]
        if candidate["id"] not in {item["id"] for item in selected}:
            selected.append(candidate)
        next_cursor += 1
    return selected[:per_tick], next_cursor % len(eligible_agents), focused_set


def maybe_generate_group_behavior_event(conn, world_time, tick_id, day, slot):
    ensure_world_runtime_tables(conn)
    branch_key = active_world_branch_key(conn)
    latest = conn.execute(
        """
        SELECT created_at FROM world_event_stream
        WHERE event_type IN ('group_diffusion', 'crowd_transmission', 'organization_mobilization')
          AND branch_key = ?
        ORDER BY id DESC LIMIT 1
        """,
        (branch_key,),
    ).fetchone()
    latest_at = parse_world_datetime(latest["created_at"]) if latest else None
    if latest_at and (world_time - latest_at).total_seconds() < 1800:
        return {"skipped": True, "reason": "interval_not_elapsed"}

    env = dict(get_campus_environment(conn, day))
    counts = {
        row["location"]: int(row["count"])
        for row in conn.execute("SELECT location, COUNT(*) AS count FROM residents GROUP BY location").fetchall()
    }
    hot_location, hot_count = max(counts.items(), key=lambda item: item[1]) if counts else ("校园", 0)
    campus_flow = int(env.get("campus_flow") or 0)
    activity_heat = int(env.get("activity_heat") or 0)
    event = None
    if hot_count >= 4 and campus_flow >= 65:
        event = append_world_event(
            conn,
            "crowd_transmission",
            "空间拥堵正在传导",
            f"{hot_location} 聚集了 {hot_count} 位 Agent，拥挤感开始影响周边行动选择。",
            tick_id=tick_id,
            location=hot_location,
            payload={"location": hot_location, "agent_count": hot_count, "campus_flow": campus_flow},
            day=day,
            slot=slot,
        )
    elif activity_heat >= 70 and random.random() < 0.35:
        event = append_world_event(
            conn,
            "organization_mobilization",
            "组织活动正在动员",
            "校园活动热度较高，部分社团和组织开始吸引周边 Agent 关注。",
            tick_id=tick_id,
            payload={"activity_heat": activity_heat, "mechanism": "activity_heat_threshold"},
            day=day,
            slot=slot,
        )
    else:
        recent_info = conn.execute(
            """
            SELECT title, category FROM external_information
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if recent_info and random.random() < 0.25:
            event = append_world_event(
                conn,
                "group_diffusion",
                "外部信息沿关系扩散",
                f"关于「{recent_info['title']}」的讨论开始在少数相关 Agent 之间扩散。",
                tick_id=tick_id,
                payload={"information_title": recent_info["title"], "category": recent_info["category"], "mechanism": "relationship_and_place_diffusion"},
                day=day,
                slot=slot,
            )
    if not event:
        return {"skipped": True, "reason": "no_group_trigger"}
    return {"skipped": False, "event_id": event["id"], "event_type": event["event_type"]}


def sync_world_time_environment(conn, world_time):
    day = get_current_day(conn)
    values = dict(get_campus_environment(conn, day))
    values = derive_environment_from_real_time(values, world_time)
    save_environment_values(conn, day, values)
    return get_campus_environment(conn, day)


def sync_real_weather_into_world(conn, event_type="real_weather_manual_sync", tick_id=None, day=None, slot=None, world_time=None):
    day = day or get_current_day(conn)
    current_env = get_campus_environment(conn, day)
    weather_data = fetch_real_weather()
    values = dict(current_env)
    values.update({key: weather_data[key] for key in ["weather", "temperature", "rainfall", "weather_source", "weather_observed_at"]})
    values = derive_environment_from_weather(values)
    values = derive_environment_from_real_time(values, world_time)
    save_environment_values(conn, day, values)
    content = f"接入真实天气：{values['weather']}，{values['temperature']}℃，降雨指数 {values['rainfall']}。"
    add_event(conn, day, "real_weather_sync", content)
    event = append_world_event(
        conn,
        event_type,
        "真实天气自动同步" if event_type == "real_weather_auto_sync" else "真实天气同步",
        content,
        tick_id=tick_id,
        payload={
            "weather": values["weather"],
            "temperature": values["temperature"],
            "rainfall": values["rainfall"],
            "weather_source": values.get("weather_source", ""),
            "weather_observed_at": values.get("weather_observed_at", ""),
        },
        day=day,
        slot=slot,
    )
    return {"environment": get_campus_environment(conn, day), "raw": weather_data.get("raw", {}), "event": event}


def maybe_auto_sync_real_weather(conn, world_time, tick_id=None, day=None, slot=None):
    ensure_world_runtime_tables(conn)
    latest = conn.execute(
        """
        SELECT created_at FROM world_event_stream
        WHERE event_type IN ('real_weather_auto_sync', 'real_weather_auto_sync_failed')
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    latest_at = parse_world_datetime(latest["created_at"]) if latest else None
    if latest_at and (world_time - latest_at).total_seconds() < WORLD_WEATHER_SYNC_INTERVAL_SECONDS:
        return {"skipped": True, "reason": "interval_not_elapsed", "last_synced_at": latest_at.isoformat()}
    try:
        result = sync_real_weather_into_world(conn, event_type="real_weather_auto_sync", tick_id=tick_id, day=day, slot=slot, world_time=world_time)
        env = result["environment"]
        return {
            "skipped": False,
            "weather": env.get("weather"),
            "temperature": env.get("temperature"),
            "rainfall": env.get("rainfall"),
            "weather_source": env.get("weather_source"),
            "weather_observed_at": env.get("weather_observed_at"),
            "event_id": result["event"].get("id"),
        }
    except Exception as exc:
        logger.warning("Auto real weather sync failed", exc_info=True)
        event = append_world_event(
            conn,
            "real_weather_auto_sync_failed",
            "真实天气自动同步失败",
            f"真实天气源暂时不可用：{type(exc).__name__}",
            tick_id=tick_id,
            payload={"error": str(exc)[:240]},
            day=day,
            slot=slot,
        )
        return {"skipped": False, "failed": True, "error": str(exc), "event_id": event["id"]}


@contextmanager
def world_tick_database_lease():
    """Hold a cross-process tick lease.

    Session-level advisory locks are incompatible with PgBouncer transaction
    pooling (Supabase port 6543).  Fall back to the in-process WORLD_TICK_LOCK
    which is sufficient for single-instance deployments.
    """
    # Cross-process lease skipped: PgBouncer transaction pooling does not
    # preserve session state across transactions, so pg_advisory_lock cannot
    # work reliably.
    yield True


def stale_world_tick_seconds():
    try:
        configured = int(
            os.getenv(
                "WORLD_STALE_TICK_SECONDS",
                str(DEFAULT_WORLD_STALE_TICK_SECONDS),
            )
        )
    except ValueError:
        configured = DEFAULT_WORLD_STALE_TICK_SECONDS
    return max(300, configured)


def reconcile_stale_world_ticks(conn, now=None):
    """Mark abandoned running rows failed after the configured safety window."""
    now = now or get_world_now()
    stale_ids = []
    rows = conn.execute(
        """
        SELECT id, started_at FROM world_ticks
        WHERE status = 'running'
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        started_at = parse_world_datetime(row["started_at"])
        if started_at is None:
            continue
        if (now - started_at).total_seconds() >= stale_world_tick_seconds():
            stale_ids.append(int(row["id"]))
    if not stale_ids:
        return []
    completed_at = now.isoformat()
    message = "runner recovered a stale running tick"
    for tick_id in stale_ids:
        conn.execute(
            """
            UPDATE world_ticks
            SET status = 'failed', error_message = ?, completed_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (message, completed_at, tick_id),
        )
    remaining = conn.execute(
        "SELECT 1 FROM world_ticks WHERE status = 'running' LIMIT 1"
    ).fetchone()
    if remaining is None:
        conn.execute(
            """
            UPDATE world_runtime
            SET last_tick_started_at = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (WORLD_RUNTIME_ID,),
        )
    return stale_ids


def record_world_tick_failure(tick_id, reason, exc):
    failed_at = get_world_now().isoformat()
    with get_connection() as failure_conn:
        if tick_id is not None:
            failure_conn.execute(
                """
                UPDATE world_ticks
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (f"{type(exc).__name__}: {str(exc)[:500]}", failed_at, tick_id),
            )
            failure_conn.execute(
                """
                UPDATE world_runtime
                SET last_tick_started_at = '', last_tick_completed_at = ?,
                    world_time = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (failed_at, failed_at, WORLD_RUNTIME_ID),
            )
        append_world_event(
            failure_conn,
            "world_tick_failed",
            "世界 tick 失败",
            f"后台世界推进失败：{type(exc).__name__}: {str(exc)[:180]}",
            tick_id=tick_id,
            payload={"error": str(exc), "reason": reason},
        )
        failure_conn.commit()


def advance_world_tick(reason="background"):
    if not WORLD_TICK_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="世界 tick 正在执行中")
    try:
        with world_tick_database_lease() as acquired:
            if not acquired:
                raise HTTPException(status_code=409, detail="另一个服务实例正在执行世界 tick")
            return _advance_world_tick_locked(reason)
    finally:
        WORLD_TICK_LOCK.release()


def _advance_world_tick_locked(reason="background"):
    tick_id = None
    try:
        with get_connection() as conn:
            tick = start_world_tick(
                conn,
                reason,
                runtime_id=WORLD_RUNTIME_ID,
                read_runtime=read_world_runtime,
                get_world_now=get_world_now,
                sync_current_day=sync_current_day_with_world_date,
                world_slot_from_hour=world_slot_from_hour,
            )
            runtime = tick["runtime"]
            world_time = tick["world_time"]
            day_sync = tick["day_sync"]
            day = tick["day"]
            slot = tick["slot"]
            tick_id = tick["tick_id"]
            tick_index = tick["tick_index"]
            pre_agent = run_pre_agent_subsystems(
                conn,
                reason,
                world_time=world_time,
                tick_id=tick_id,
                tick_index=tick_index,
                day_sync=day_sync,
                day=day,
                slot=slot,
                active_branch_key=lambda: active_world_branch_key(conn),
                append_world_event=lambda *args, **kwargs: append_world_event(conn, *args, **kwargs),
                compact_external_sync_result=compact_external_sync_result,
                process_population_runtime=process_population_runtime,
                ensure_current_action_plans=ensure_current_action_plans,
                sync_world_time_environment=sync_world_time_environment,
                process_due_world_delayed_effects=process_due_world_delayed_effects,
                external_world_available=external_world_available,
                process_external_world_runtime=process_external_world_runtime,
                maybe_auto_sync_real_weather=maybe_auto_sync_real_weather,
                get_campus_environment=get_campus_environment,
                maybe_auto_sync_external_information=maybe_auto_sync_external_information,
                process_resilience_runtime=process_resilience_runtime,
                capture_tick_observations=capture_tick_observations,
                advance_body_states=advance_body_states,
                advance_active_movements=advance_active_movements,
                run_due_world_updates=run_due_world_updates,
                process_supply_runtime=process_supply_runtime,
                process_market_runtime=process_market_runtime,
                process_labor_runtime=process_labor_runtime,
                process_credit_runtime=process_credit_runtime,
                process_budget_runtime=process_budget_runtime,
                process_public_policy_runtime=process_public_policy_runtime,
                process_organization_runtime=process_organization_runtime,
                process_social_institution_runtime=process_social_institution_runtime,
                process_macro_runtime=process_macro_runtime,
            )
            start_event = pre_agent["start_event"]
            local_observations = pre_agent["local_observations"]
            body_states = pre_agent["body_states"]
            movement_results = pre_agent["movement_results"]
            movement_events = pre_agent["movement_events"]
            multiscale_updates = pre_agent["multiscale_updates"]
            organization_updates = pre_agent["organization_updates"]
            organization_events = pre_agent["organization_events"]
            supply_updates = pre_agent["supply_updates"]
            market_updates = pre_agent["market_updates"]
            labor_updates = pre_agent["labor_updates"]
            credit_updates = pre_agent["credit_updates"]
            budget_updates = pre_agent["budget_updates"]
            public_policy_updates = pre_agent["public_policy_updates"]
            social_institution_updates = pre_agent["social_institution_updates"]
            macro_updates = pre_agent["macro_updates"]
            resilience_updates = pre_agent["resilience_updates"]
            population_updates = pre_agent["population_updates"]
            external_world_updates = pre_agent["external_world_updates"]
            agent_stage = run_agent_and_learning_stage(
                conn,
                runtime,
                world_time=world_time,
                tick_id=tick_id,
                tick_index=tick_index,
                day=day,
                slot=slot,
                parent_event_id=start_event["id"],
                active_branch_key=lambda: active_world_branch_key(conn),
                select_world_tick_agents=select_world_tick_agents,
                process_world_agent_tick=process_world_agent_tick,
                process_adaptive_learning=process_adaptive_learning,
                process_norm_emergence=process_norm_emergence,
                process_institution_evolution=process_institution_evolution,
                process_longitudinal_runtime=process_longitudinal_runtime,
            )
            selected_agents = agent_stage["selected_agents"]
            next_cursor = agent_stage["next_cursor"]
            results = agent_stage["results"]
            failed = agent_stage["failed"]
            adaptive_learning = agent_stage["adaptive_learning"]
            norm_emergence = agent_stage["norm_emergence"]
            institution_evolution = agent_stage["institution_evolution"]
            longitudinal_updates = agent_stage["longitudinal_updates"]
            completed_at = get_world_now().isoformat()
            action_limited = settle_tick_completion(
                conn,
                runtime_id=WORLD_RUNTIME_ID,
                tick_id=tick_id,
                next_cursor=next_cursor,
                results=results,
                failed=failed,
                completed_at=completed_at,
            )
            finish_event = append_world_event(
                conn,
                "world_tick_complete",
                "世界 tick 完成",
                (
                    f"本次 tick 处理 {len(results)} 位 Agent，运行失败 {failed} 位"
                    f"，行动受限 {action_limited} 位。"
                ),
                tick_id=tick_id,
                payload={
                    "started_event_id": start_event["id"],
                    "processed_agents": len(results),
                    "failed_agents": failed,
                    "action_limited_agents": action_limited,
                    "multiscale_updates": {
                        "due_count": multiscale_updates["due_count"],
                        "completed_count": len(multiscale_updates["completed"]),
                        "failed_count": len(multiscale_updates["failed"]),
                    },
                    "organization_updates": organization_updates,
                    "supply_updates": supply_updates,
                    "market_updates": market_updates,
                    "labor_updates": labor_updates,
                    "credit_updates": credit_updates,
                    "budget_updates": budget_updates,
                    "public_policy_updates": public_policy_updates,
                    "social_institution_updates": social_institution_updates,
                    "macro_updates": macro_updates,
                    "adaptive_learning": adaptive_learning,
                    "norm_emergence": norm_emergence,
                    "institution_evolution": institution_evolution,
                    "resilience_updates": resilience_updates,
                    "population_updates": population_updates,
                    "external_world_updates": external_world_updates,
                    "longitudinal_updates": longitudinal_updates,
                    "organization_event_count": len(organization_events),
                    "spatial_movements": {
                        "advanced_count": len(movement_results),
                        "arrived_count": sum(
                            1
                            for item in movement_results
                            if item["movement_status"] == "arrived"
                        ),
                    },
                    "body_states": {
                        "advanced_count": len(body_states),
                        "sleeping_count": sum(
                            1 for item in body_states if item["sleeping"]
                        ),
                        "moving_count": sum(
                            1 for item in body_states if item["moving"]
                        ),
                    },
                    "local_perception": {
                        "observation_count": len(local_observations),
                        "observer_count": len(
                            {
                                item["observer_resident_id"]
                                for item in local_observations
                            }
                        ),
                    },
                },
                day=day,
                slot=slot,
                source_type="runtime_tick",
                source_id=tick_id,
                parent_event_id=start_event["id"],
            )
            group_behavior, campus_news = run_post_tick_handlers(
                conn,
                world_time=world_time,
                tick_id=tick_id,
                day=day,
                slot=slot,
                maybe_generate_group_behavior_event=maybe_generate_group_behavior_event,
                maybe_publish_campus_news_from_world_window=maybe_publish_campus_news_from_world_window,
            )
            return {
                "tick_id": tick_id,
                "tick_index": tick_index,
                "world_time": completed_at,
                "day": day,
                "slot": slot,
                "reason": reason,
                "processed_agents": len(results),
                "failed_agents": failed,
                "events": [start_event, finish_event],
                "multiscale_updates": multiscale_updates,
                "organization_updates": organization_updates,
                "supply_updates": supply_updates,
                "market_updates": market_updates,
                "labor_updates": labor_updates,
                "credit_updates": credit_updates,
                "budget_updates": budget_updates,
                "public_policy_updates": public_policy_updates,
                "social_institution_updates": social_institution_updates,
                "macro_updates": macro_updates,
                "adaptive_learning": adaptive_learning,
                "norm_emergence": norm_emergence,
                "institution_evolution": institution_evolution,
                "resilience_updates": resilience_updates,
                "population_updates": population_updates,
                "external_world_updates": external_world_updates,
                "longitudinal_updates": longitudinal_updates,
                "organization_events": organization_events,
                "spatial_movements": movement_results,
                "body_states": body_states,
                "local_observations": local_observations,
                "movement_events": movement_events,
                "group_behavior": group_behavior,
                "campus_news": campus_news,
                "results": results,
            }
    except HTTPException as exc:
        if tick_id is not None:
            record_world_tick_failure(tick_id, reason, exc)
        raise
    except Exception as exc:
        logger.exception("World tick failed")
        try:
            record_world_tick_failure(tick_id, reason, exc)
        except Exception:
            logger.exception("Failed to persist world tick failure state")
        raise


def world_runtime_auto_start_enabled():
    return environment_flag_enabled("WORLD_RUNTIME_AUTO_START")


def world_runner_enabled():
    """Allow read-only app instances to run without a background world writer."""
    return environment_flag_enabled("WORLD_RUNNER_ENABLED")


def ensure_world_runtime_running_unless_manually_paused(conn, runtime):
    if runtime.get("status") != "paused" or not world_runtime_auto_start_enabled():
        return runtime
    if get_simulation_state_value(conn, "world_runtime_manual_pause", "false") == "true":
        return runtime
    runtime = update_world_runtime_status(conn, "running")
    append_world_event(
        conn,
        "world_runtime_auto_start",
        "世界运行已自动恢复",
        "服务启动后自动恢复校园平行世界后台运行。",
        payload={"source": "world_runner_loop"},
    )
    conn.commit()
    return runtime


def world_runner_loop():
    run_world_runner_loop(
        get_connection=get_connection,
        reconcile_stale_ticks=reconcile_stale_world_ticks,
        read_runtime=read_world_runtime,
        ensure_runtime_running=ensure_world_runtime_running_unless_manually_paused,
        tick_due=world_tick_due,
        advance_tick=advance_world_tick,
        http_exception_type=HTTPException,
        logger=logger,
    )


@app.on_event("startup")
def start_world_runner_thread():
    global WORLD_RUNNER_THREAD
    if not world_runner_enabled():
        logger.info("World runner disabled by WORLD_RUNNER_ENABLED")
        return
    with WORLD_RUNNER_LOCK:
        if WORLD_RUNNER_THREAD and WORLD_RUNNER_THREAD.is_alive():
            return
        WORLD_RUNNER_THREAD = Thread(target=world_runner_loop, daemon=True)
        WORLD_RUNNER_THREAD.start()


def auto_update_environment(conn, day):
    previous = get_campus_environment(conn, day)
    weather = random.choice(["晴", "多云", "小雨", "闷热", "大风"])
    weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][(day - 1) % 7]
    time_slot = random.choice(["上午", "中午", "下午", "晚上"])
    temperature = random.randint(18, 32)
    rainfall = random.randint(20, 80) if weather == "小雨" else random.randint(0, 15)

    semester_stage = previous.get("semester_stage", "平时周")
    exam_pressure = int(previous.get("exam_pressure", 35))
    assignment_pressure = int(previous.get("assignment_pressure", 40))
    activity_heat = int(previous.get("activity_heat", 50))

    if day % 7 == 0:
        semester_stage = "考试周"
        exam_pressure = min(100, exam_pressure + 25)
        assignment_pressure = min(100, assignment_pressure + 15)
        activity_heat = max(20, activity_heat - 15)
        event_name = "期末复习"
    elif day % 5 == 0:
        semester_stage = "活动周"
        exam_pressure = max(10, exam_pressure - 10)
        assignment_pressure = max(10, assignment_pressure - 5)
        activity_heat = min(100, activity_heat + 25)
        event_name = "校园社团节"
    else:
        event_name = random.choice(["社团招新", "普通教学日", "讲座通知", "运动训练"])
        exam_pressure = max(10, min(100, exam_pressure + random.randint(-8, 8)))
        assignment_pressure = max(10, min(100, assignment_pressure + random.randint(-8, 8)))
        activity_heat = max(10, min(100, activity_heat + random.randint(-10, 10)))

    study_atmosphere = max(10, min(100, 35 + exam_pressure // 2 + assignment_pressure // 3))
    event_intensity = max(10, min(100, activity_heat + random.randint(-10, 15)))
    campus_flow = max(10, min(100, 45 + activity_heat // 2 + random.randint(-10, 10)))
    classroom_crowd = max(10, min(100, 40 + assignment_pressure // 2 + random.randint(-10, 10)))
    canteen_crowd = max(10, min(100, campus_flow + (20 if time_slot in {"中午", "晚上"} else 0) + random.randint(-10, 10)))
    library_crowd = max(10, min(100, 35 + exam_pressure // 2 + random.randint(-10, 15)))
    dorm_crowd = max(10, min(100, 35 + (20 if time_slot == "晚上" else 0) + random.randint(-10, 15)))
    playground_crowd = max(10, min(100, 30 + activity_heat // 2 - rainfall // 3 + random.randint(-10, 10)))
    commercial_crowd = max(10, min(100, 35 + activity_heat // 2 + random.randint(-5, 20)))

    traffic_status = "拥堵" if campus_flow > 75 else "正常"
    network_status = "拥堵" if dorm_crowd > 70 and time_slot == "晚上" else "稳定"
    safety_level = max(50, min(100, 95 - campus_flow // 8 - event_intensity // 10))
    resource_pressure = max(10, min(100, (canteen_crowd + library_crowd + classroom_crowd) // 3))
    campus_mood = "紧张" if exam_pressure > 75 else ("活跃" if activity_heat > 70 else "平稳")
    consumption_index = round(max(0.5, min(1.8, 0.7 + activity_heat / 110 + commercial_crowd / 220 + random.uniform(-0.1, 0.1))), 2)

    values = {
        "weather": weather,
        "semester_stage": semester_stage,
        "time_slot": time_slot,
        "weekday": weekday,
        "temperature": temperature,
        "rainfall": rainfall,
        "exam_pressure": exam_pressure,
        "assignment_pressure": assignment_pressure,
        "study_atmosphere": study_atmosphere,
        "activity_heat": activity_heat,
        "event_name": event_name,
        "event_intensity": event_intensity,
        "campus_flow": campus_flow,
        "classroom_crowd": classroom_crowd,
        "canteen_crowd": canteen_crowd,
        "library_crowd": library_crowd,
        "dorm_crowd": dorm_crowd,
        "playground_crowd": playground_crowd,
        "commercial_crowd": commercial_crowd,
        "traffic_status": traffic_status,
        "network_status": network_status,
        "safety_level": safety_level,
        "resource_pressure": resource_pressure,
        "campus_mood": campus_mood,
        "consumption_index": consumption_index,
    }

    try:
        real_weather = fetch_real_weather()
        values.update({key: real_weather[key] for key in ["weather", "temperature", "rainfall", "weather_source", "weather_observed_at"]})
    except Exception as exc:
        logger.warning("Falling back to simulated weather: %s", exc)
        values["weather_source"] = "simulation"
        values["weather_observed_at"] = ""
    values = derive_environment_from_weather(values)
    values = derive_environment_from_real_time(values)
    save_environment_values(conn, day, values)
    conn.commit()
    maybe_generate_environment_event(conn, day)
    return get_campus_environment(conn, day)


@app.get("/")
def home():
    return FileResponse(PROJECT_ROOT / "frontend" / "index.html")


@app.get("/favicon.svg")
def favicon_svg():
    return FileResponse(PROJECT_ROOT / "frontend" / "assets" / "site" / "favicon.svg")


@app.get("/favicon.ico")
def favicon_ico():
    return FileResponse(PROJECT_ROOT / "frontend" / "assets" / "site" / "favicon.ico")


@app.get("/apple-touch-icon.png")
def apple_touch_icon():
    return FileResponse(PROJECT_ROOT / "frontend" / "assets" / "site" / "apple-touch-icon.png")


@app.get("/share-image.png")
def share_image():
    return FileResponse(PROJECT_ROOT / "frontend" / "assets" / "site" / "share-image.png")


@app.get("/api/ai/test")
def ai_test():
    if not is_llm_configured():
        raise HTTPException(status_code=503, detail="当前环境未配置 LLM_API_KEY 或 LLM_API_URL，世界将使用规则模式运行。")
    prompt = "请用一句话说明你已接入校园封闭世界 AI-Agent 系统。"
    return {"message": "AI API 调用成功", "result": ask_llm(prompt)}


def require_admin_token(authorization: Optional[str]):
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected:
        logger.warning("ADMIN_TOKEN is not configured; admin world endpoint is open for local development.")
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=403, detail="Admin token 无效或缺失")


class WorldTickExclusion:
    def __enter__(self):
        if not WORLD_TICK_LOCK.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="世界 tick 或状态恢复正在执行中")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        WORLD_TICK_LOCK.release()


SNAPSHOT_STATE_TABLES = {
    "simulation_state": "key",
    "campus_state": "day",
    "campus_spaces": "code",
    "campus_events": "id",
    "residents": "id",
    "agent_profiles": "resident_id",
    "inventory": "id",
    "transactions": "id",
    "relationships": "from_resident_id, to_resident_id",
    "relationship_dynamics": "from_resident_id, to_resident_id",
    "long_term_goals": "id",
    "agent_goals": "id",
    "goal_dependencies": "id",
    "goal_revisions": "id",
    "campus_organizations": "id",
    "organization_members": "organization_id, resident_id",
    "agent_commitments": "id",
    "plan_outcomes": "id",
    "trajectory_episodes": "id",
    "group_goals": "id",
    "memories": "id",
    "agent_learning": "id",
    "collaborations": "id",
    "competitions": "id",
    "external_information": "id",
    "agent_information": "information_id, resident_id",
    "agent_action_plans": "id",
    "agent_news_posts": "id",
    "relationship_change_events": "id",
    "social_interaction_events": "id",
    "social_relation_interpretations": "id",
    "social_beliefs": "id",
    "policies": "id",
    "world_runtime": "id",
    "campus_schedule_rules": "id",
    "world_causal_weights": "id",
    "world_action_rules": "id",
    "world_delayed_effects": "due_at, id",
    "world_update_schedules": "id",
    "world_resource_accounts": "id",
}

SPATIAL_FOUNDATION_SNAPSHOT_TABLES = {
    "spatial_nodes": "id",
    "spatial_edges": "id",
    "agent_spatial_capabilities": "resident_id",
    "agent_spatial_states": "resident_id",
}

SPATIAL_SNAPSHOT_STATE_TABLES = {
    **SPATIAL_FOUNDATION_SNAPSHOT_TABLES,
    "spatial_resources": "id",
    "spatial_admission_queue": "node_id, queue_position",
}

BODY_SNAPSHOT_STATE_TABLES = {
    "agent_body_states": "resident_id",
}

PERCEPTION_SNAPSHOT_STATE_TABLES = {
    "agent_observations": "id",
    "agent_belief_states": "id",
    "agent_spatial_memories": "id",
}

CAPABILITY_SNAPSHOT_STATE_TABLES = {
    "agent_capability_profiles": "resident_id",
    "agent_opportunity_access": "id",
}

ECONOMY_SNAPSHOT_STATE_TABLES = {
    "economic_actors": "id",
    "ledger_accounts": "id",
    "ledger_transactions": "id",
    "ledger_entries": "id",
}

ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES = {
    "ledger_authorization_rules": "id",
    "ledger_authorized_operations": "transaction_id",
    "ledger_reversals": "original_transaction_id",
    "ledger_audit_events": "id",
}

ORGANIZATION_SNAPSHOT_STATE_TABLES = {
    "organization_runtime_profiles": "organization_id",
    "organization_roles": "id",
    "organization_role_assignments": "organization_id, resident_id",
    "organization_proposals": "id",
    "organization_votes": "proposal_id, resident_id",
    "organization_commitments": "id",
    "organization_relationships": "from_organization_id, to_organization_id",
    "organization_events": "id",
}

SUPPLY_SNAPSHOT_STATE_TABLES = {
    "catalog_items": "id",
    "inventory_accounts": "id",
    "production_recipes": "id",
    "production_recipe_inputs": "recipe_id, item_id",
    "production_batches": "id",
    "inventory_movements": "id",
    "service_offerings": "id",
    "service_deliveries": "id",
}

LABOR_SNAPSHOT_STATE_TABLES = {
    "labor_positions": "id",
    "employment_contracts": "id",
    "labor_shifts": "id",
    "income_programs": "id",
    "income_payments": "id",
    "expense_obligations": "id",
}

BUDGET_SNAPSHOT_STATE_TABLES = {
    "household_budget_profiles": "resident_id",
    "household_budget_snapshots": "id",
    "savings_transfers": "id",
    "choice_evaluations": "id",
}

MARKET_SNAPSHOT_STATE_TABLES = {
    "market_mechanisms": "id",
    "market_price_snapshots": "id",
    "market_demand_signals": "id",
    "market_friction_events": "id",
}

CREDIT_SNAPSHOT_STATE_TABLES = {
    "savings_goals": "id",
    "household_risk_profiles": "resident_id",
    "economic_shocks": "id",
    "risk_pool_claims": "id",
    "credit_products": "id",
    "credit_profiles": "resident_id",
    "credit_contracts": "id",
    "credit_installments": "id",
    "credit_payments": "id",
    "credit_events": "id",
}

PUBLIC_POLICY_SNAPSHOT_STATE_TABLES = {
    "public_services": "id",
    "public_service_operations": "id",
    "public_service_usages": "id",
    "externality_events": "id",
    "externality_exposures": "id",
    "policy_instruments": "id",
    "policy_benefits": "id",
    "policy_outcome_snapshots": "id",
}

SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES = {
    "communication_channels": "id",
    "information_claims": "id",
    "information_versions": "id",
    "information_transmissions": "id",
    "information_exposures": "id",
    "information_beliefs": "resident_id, claim_id",
    "institutional_rules": "id",
    "institutional_cases": "id",
    "institutional_decisions": "id",
    "resident_power_profiles": "resident_id",
    "institutional_trust_events": "id",
}
MACRO_SNAPSHOT_STATE_TABLES = {
    "macro_metric_definitions": "id",
    "macro_snapshots": "id",
    "macro_metric_values": "id",
    "macro_metric_components": "id",
    "macro_reconciliation_checks": "id",
}

ADAPTATION_SNAPSHOT_STATE_TABLES = {
    "constraint_rules": "id",
    "constraint_evaluations": "id",
    "boundary_attempts": "id",
    "constraint_consequences": "id",
    "experience_records": "id",
    "adaptive_memories": "id",
    "memory_revisions": "id",
    "strategy_states": "id",
    "learning_updates": "id",
    "norm_signals": "id",
    "norm_candidates": "id",
    "norm_evidence": "id",
    "agent_norm_beliefs": "resident_id, norm_id",
    "norm_state_transitions": "id",
    "norm_responses": "id",
    "rule_primitives": "id",
    "institutional_rule_proposals": "id",
    "rule_deliberations": "id",
    "evolved_rule_versions": "id",
    "rule_effect_reviews": "id",
}

RESILIENCE_SNAPSHOT_STATE_TABLES = {
    "shock_definitions": "id",
    "shock_instances": "id",
    "shock_impacts": "id",
    "resident_shock_exposures": "id",
    "recovery_actions": "id",
    "shock_state_transitions": "id",
}

POPULATION_SNAPSHOT_STATE_TABLES = {
    "population_profiles": "resident_id",
    "population_events": "id",
    "resident_role_assignments": "id",
    "resident_residency_periods": "id",
    "membership_transitions": "id",
    "population_effects": "id",
}

EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES = {
    "external_sources": "id",
    "external_sync_runs": "id",
    "external_raw_observations": "id",
    "external_source_locks": "source_id",
    "external_event_catalog": "event_type",
    "external_events": "id",
    "external_event_links": "id",
    "external_data_snapshots": "id",
    "external_snapshot_items": "snapshot_id, external_event_id",
    "external_runtime_modes": "branch_key",
    "external_exposures": "id",
    "external_replay_deliveries": "id",
    "external_impact_rules": "id",
    "external_event_impacts": "id",
    "external_state_reconciliations": "id",
    "external_governance_reviews": "id",
    "external_access_audit": "id",
    "external_runtime_health": "branch_key",
    "external_snapshot_exports": "id",
    "external_experiment_bindings": "id",
}

LONGITUDINAL_SNAPSHOT_STATE_TABLES = {
    "longitudinal_profiles": "resident_id",
    "life_course_stages": "id",
    "life_turning_points": "id",
    "path_dependency_links": "id",
    "longitudinal_aggregations": "id",
    "trajectory_reconciliations": "id",
}


def snapshot_table_exists(conn, table_name):
    return bool(conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def snapshot_state_tables(conn):
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
            **LONGITUDINAL_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
            **LONGITUDINAL_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in {
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
        }
    ):
        return {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
        }
    if all(
        snapshot_table_exists(conn, table_name)
        for table_name in SPATIAL_SNAPSHOT_STATE_TABLES
    ):
        return {**SNAPSHOT_STATE_TABLES, **SPATIAL_SNAPSHOT_STATE_TABLES}
    return SNAPSHOT_STATE_TABLES


def capture_objective_world_state(conn, ensure_schema=True, state_tables=None):
    if ensure_schema:
        ensure_campus_state_table(conn)
        ensure_space_system(conn)
        ensure_agent_news_system(conn)
        ensure_external_information_system(conn)
    state = {}
    for table_name, order_by in (
        state_tables or snapshot_state_tables(conn)
    ).items():
        where_clause = " WHERE status = 'pending'" if table_name == "world_delayed_effects" else ""
        rows = conn.execute(
            f"SELECT * FROM {table_name}{where_clause} ORDER BY {order_by}"
        ).fetchall()
        state[table_name] = [dict(row) for row in rows]
    return state


def decode_world_snapshot(row, include_state=False):
    item = dict(row)
    item["metadata"] = load_json_text(item.pop("metadata_json", "{}"), {})
    if include_state:
        item["state"] = load_json_text(item.pop("state_json", "{}"), {})
    else:
        item.pop("state_json", None)
    return item


def create_world_snapshot_record(
    conn,
    reason="manual checkpoint",
    snapshot_type="manual_checkpoint",
    run_id="",
    branch_key="main",
    parent_snapshot_id=None,
    external_data_version="",
    metadata=None,
):
    ensure_world_runtime_tables(conn)
    if parent_snapshot_id:
        parent = conn.execute(
            "SELECT id FROM world_snapshots WHERE id = ?",
            (parent_snapshot_id,),
        ).fetchone()
        if not parent:
            raise ValueError("父快照不存在")
    runtime = dict(
        conn.execute("SELECT * FROM world_runtime WHERE id = ?", (WORLD_RUNTIME_ID,)).fetchone()
    )
    active_config = get_active_environment_config(conn)
    event_row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) AS value FROM world_event_stream"
    ).fetchone()
    tick_row = conn.execute("SELECT id FROM world_ticks ORDER BY id DESC LIMIT 1").fetchone()
    state = capture_objective_world_state(conn)
    schema_version = (
        "world-snapshot-v16-household-credit"
        if all(table in state for table in CREDIT_SNAPSHOT_STATE_TABLES)
        else (
            "world-snapshot-v15-market-pricing"
            if all(table in state for table in MARKET_SNAPSHOT_STATE_TABLES)
            else (
                "world-snapshot-v14-budget-choice"
                if all(table in state for table in BUDGET_SNAPSHOT_STATE_TABLES)
                else (
                    "world-snapshot-v13-labor-runtime"
                    if all(table in state for table in LABOR_SNAPSHOT_STATE_TABLES)
                    else (
                        "world-snapshot-v12-supply-runtime"
                        if all(table in state for table in SUPPLY_SNAPSHOT_STATE_TABLES)
                        else (
                            "world-snapshot-v11-organization-runtime"
                            if all(table in state for table in ORGANIZATION_SNAPSHOT_STATE_TABLES)
                            else (
                                "world-snapshot-v10-ledger-controls"
                                if all(table in state for table in ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES)
                                else (
                                    "world-snapshot-v9-economy"
                                    if all(table in state for table in ECONOMY_SNAPSHOT_STATE_TABLES)
                                    else (
                                        "world-snapshot-v8-capability"
                                        if all(table in state for table in CAPABILITY_SNAPSHOT_STATE_TABLES)
                                        else (
                                            "world-snapshot-v7-perception"
                                            if all(table in state for table in PERCEPTION_SNAPSHOT_STATE_TABLES)
                                            else (
                                                "world-snapshot-v6-body"
                                                if all(table in state for table in BODY_SNAPSHOT_STATE_TABLES)
                                                else (
                                                    "world-snapshot-v5-admission"
                                                    if all(
                                                        table in state
                                                        for table in SPATIAL_SNAPSHOT_STATE_TABLES
                                                    )
                                                    else (
                                                        "world-snapshot-v4-spatial"
                                                        if all(
                                                            table in state
                                                            for table in SPATIAL_FOUNDATION_SNAPSHOT_TABLES
                                                        )
                                                        else "world-snapshot-v3"
                                                    )
                                                )
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            )
        )
    )
    if all(table in state for table in PUBLIC_POLICY_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v17-public-policy"
    if all(table in state for table in SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v18-social-institutions"
    if all(table in state for table in MACRO_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v19-macro-reconciliation"
    if all(table in state for table in ADAPTATION_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v23-institution-evolution"
    if all(table in state for table in RESILIENCE_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v24-shock-recovery"
    if all(table in state for table in POPULATION_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v25-population-mobility"
    if all(table in state for table in EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v30-external-governance"
    if all(table in state for table in LONGITUDINAL_SNAPSHOT_STATE_TABLES):
        schema_version = "world-snapshot-v31-longitudinal-paths"
    state_json = canonical_json(state)
    event_cursor = int(event_row["value"] or 0)
    effective_branch_key = branch_key or runtime.get("active_branch_key") or "main"
    snapshot_metadata = {
        "table_counts": {name: len(rows) for name, rows in state.items()},
        "environment_checksum": active_config["checksum"] if active_config else "",
        "state_table_count": len(state),
        "restorable": True,
        **(metadata or {}),
    }
    cursor = conn.execute(
        """
        INSERT INTO world_snapshots
        (run_id, snapshot_type, world_time, day, tick_id, reason, state_json,
         schema_version, environment_config_id, environment_version, random_seed,
         external_data_version, event_cursor, parent_snapshot_id, branch_key,
         checksum, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id or runtime.get("active_run_id") or "",
            snapshot_type,
            runtime.get("world_time") or get_world_now().isoformat(),
            get_current_day(conn),
            tick_row["id"] if tick_row else None,
            reason,
            state_json,
            schema_version,
            runtime.get("environment_config_id"),
            runtime.get("environment_version") or "",
            runtime.get("random_seed") or "",
            external_data_version,
            event_cursor,
            parent_snapshot_id,
            effective_branch_key,
            content_checksum(state_json),
            canonical_json(snapshot_metadata),
        ),
    )
    snapshot_id = cursor.lastrowid
    conn.execute(
        """
        UPDATE world_branches
        SET head_snapshot_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE branch_key = ?
        """,
        (snapshot_id, effective_branch_key),
    )
    return decode_world_snapshot(
        conn.execute("SELECT * FROM world_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    )


SNAPSHOT_UPSERT_KEYS = {
    "residents": ("id",),
    "world_runtime": ("id",),
    "world_update_schedules": ("id",),
    "spatial_nodes": ("id",),
    "spatial_edges": ("id",),
    "spatial_resources": ("id",),
    "agent_spatial_capabilities": ("resident_id",),
    "agent_spatial_states": ("resident_id",),
    "agent_body_states": ("resident_id",),
    "agent_observations": ("id",),
    "agent_belief_states": ("id",),
    "agent_spatial_memories": ("id",),
    "agent_capability_profiles": ("resident_id",),
    "agent_opportunity_access": ("id",),
}


def snapshot_row_or_error(conn, snapshot_id):
    row = conn.execute(
        "SELECT * FROM world_snapshots WHERE id = ?",
        (snapshot_id,),
    ).fetchone()
    if not row:
        raise ValueError("世界快照不存在")
    if row["checksum"] != content_checksum(row["state_json"]):
        raise ValueError("世界快照 checksum 校验失败")
    state = load_json_text(row["state_json"], {})
    if not isinstance(state, dict):
        raise ValueError("世界快照状态格式无效")
    if row["schema_version"] == "world-snapshot-v31-longitudinal-paths":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
            **LONGITUDINAL_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v30-external-governance":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
            **EXTERNAL_WORLD_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v25-population-mobility":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
            **POPULATION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v24-shock-recovery":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
            **RESILIENCE_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v23-institution-evolution":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
            **ADAPTATION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v19-macro-reconciliation":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
            **MACRO_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v18-social-institutions":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
            **SOCIAL_INSTITUTION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v17-public-policy":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
            **PUBLIC_POLICY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v16-household-credit":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
            **CREDIT_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v15-market-pricing":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
            **MARKET_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v14-budget-choice":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
            **BUDGET_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v13-labor-runtime":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
            **LABOR_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v12-supply-runtime":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
            **SUPPLY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v11-organization-runtime":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
            **ORGANIZATION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v10-ledger-controls":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_CONTROL_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v9-economy":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
            **ECONOMY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v8-capability":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
            **CAPABILITY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v7-perception":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
            **PERCEPTION_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v6-body":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_SNAPSHOT_STATE_TABLES,
            **BODY_SNAPSHOT_STATE_TABLES,
        }
    elif row["schema_version"] == "world-snapshot-v5-admission":
        expected_tables = {**SNAPSHOT_STATE_TABLES, **SPATIAL_SNAPSHOT_STATE_TABLES}
    elif row["schema_version"] == "world-snapshot-v4-spatial":
        expected_tables = {
            **SNAPSHOT_STATE_TABLES,
            **SPATIAL_FOUNDATION_SNAPSHOT_TABLES,
        }
    else:
        expected_tables = SNAPSHOT_STATE_TABLES
    missing = [table for table in expected_tables if table not in state]
    if missing:
        raise ValueError(f"快照版本不支持完整恢复，缺少状态表：{', '.join(missing[:6])}")
    return row, state, expected_tables


def insert_snapshot_rows(conn, table_name, rows):
    if not rows:
        return
    table_columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for row in rows:
        columns = [column for column in row if column in table_columns]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(row[column] for column in columns),
        )


def upsert_snapshot_rows(conn, table_name, rows, key_columns):
    table_columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    for row in rows:
        columns = [column for column in row if column in table_columns]
        mutable_columns = [column for column in columns if column not in key_columns]
        where_clause = " AND ".join(f"{column} = ?" for column in key_columns)
        values = [row[column] for column in mutable_columns]
        values.extend(row[column] for column in key_columns)
        cursor = conn.execute(
            f"UPDATE {table_name} SET "
            + ", ".join(f"{column} = ?" for column in mutable_columns)
            + f" WHERE {where_clause}",
            tuple(values),
        )
        if not cursor.rowcount:
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )


def restore_world_snapshot_state(conn, snapshot_id, active_branch_key=None, active_run_id=""):
    ensure_world_runtime_tables(conn)
    snapshot_row, state, state_tables = snapshot_row_or_error(conn, snapshot_id)
    current_resident_ids = {
        int(row["id"]) for row in conn.execute("SELECT id FROM residents").fetchall()
    }
    snapshot_resident_ids = {int(row["id"]) for row in state["residents"]}
    if current_resident_ids != snapshot_resident_ids:
        raise ValueError("当前版本仅支持居民拓扑一致的快照恢复")

    conn.execute("SAVEPOINT world_snapshot_restore")
    try:
        replace_tables = [
            table
            for table in state_tables
            if table not in SNAPSHOT_UPSERT_KEYS
        ]
        for table_name in reversed(replace_tables):
            conn.execute(f"DELETE FROM {table_name}")
        for table_name in state_tables:
            rows = state[table_name]
            key_columns = SNAPSHOT_UPSERT_KEYS.get(table_name)
            if key_columns:
                upsert_snapshot_rows(conn, table_name, rows, key_columns)
            else:
                insert_snapshot_rows(conn, table_name, rows)

        effective_branch = (
            active_branch_key
            or snapshot_row["branch_key"]
            or "main"
        )
        conn.execute(
            """
            UPDATE world_runtime
            SET status = 'paused', active_branch_key = ?, active_run_id = ?,
                world_time = ?, last_tick_started_at = '',
                last_tick_completed_at = '', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                effective_branch,
                active_run_id,
                snapshot_row["world_time"],
                WORLD_RUNTIME_ID,
            ),
        )
        restored_state = capture_objective_world_state(
            conn,
            ensure_schema=False,
            state_tables=state_tables,
        )
        restored_checksum = content_checksum(canonical_json(restored_state))
        expected_checksum = content_checksum(snapshot_row["state_json"])
        if restored_checksum != expected_checksum:
            runtime_rows = restored_state.get("world_runtime", [])
            snapshot_runtime_rows = state.get("world_runtime", [])
            for rows in (runtime_rows, snapshot_runtime_rows):
                if rows:
                    rows[0]["status"] = "paused"
                    rows[0]["active_branch_key"] = effective_branch
                    rows[0]["active_run_id"] = active_run_id
                    rows[0]["last_tick_started_at"] = ""
                    rows[0]["last_tick_completed_at"] = ""
                    rows[0].pop("updated_at", None)
                    rows[0]["world_time"] = snapshot_row["world_time"]
            restored_checksum = content_checksum(canonical_json(restored_state))
            expected_checksum = content_checksum(canonical_json(state))
            if restored_checksum != expected_checksum:
                raise ValueError("快照恢复后的状态校验失败")
        conn.execute("RELEASE SAVEPOINT world_snapshot_restore")
        return {
            "snapshot_id": snapshot_id,
            "branch_key": effective_branch,
            "schema_version": snapshot_row["schema_version"],
            "table_counts": {
                table: len(rows) for table, rows in state.items()
            },
        }
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT world_snapshot_restore")
        conn.execute("RELEASE SAVEPOINT world_snapshot_restore")
        raise


def decode_world_branch(row):
    item = dict(row)
    item["metadata"] = load_json_text(item.pop("metadata_json", "{}"), {})
    return item


def create_world_branch_record(conn, branch_key, name, source_snapshot_id, metadata=None):
    source_row, _, _ = snapshot_row_or_error(conn, source_snapshot_id)
    existing = conn.execute(
        "SELECT id FROM world_branches WHERE branch_key = ?",
        (branch_key,),
    ).fetchone()
    if existing:
        raise ValueError("世界分支标识已存在")
    runtime = conn.execute(
        "SELECT * FROM world_runtime WHERE id = ?",
        (WORLD_RUNTIME_ID,),
    ).fetchone()
    parent_branch_key = runtime["active_branch_key"] or "main"
    run_id = f"branch-{uuid4()}"
    clone_metadata = load_json_text(source_row["metadata_json"], {})
    clone_metadata.update(
        {
            "forked_from_snapshot_id": source_snapshot_id,
            "isolated_branch": True,
            **(metadata or {}),
        }
    )
    clone_cursor = conn.execute(
        """
        INSERT INTO world_snapshots
        (run_id, snapshot_type, world_time, day, tick_id, reason, state_json,
         schema_version, environment_config_id, environment_version, random_seed,
         external_data_version, event_cursor, parent_snapshot_id, branch_key,
         checksum, metadata_json)
        VALUES (?, 'branch_seed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source_row["world_time"],
            source_row["day"],
            source_row["tick_id"],
            f"从快照 #{source_snapshot_id} 创建隔离分支",
            source_row["state_json"],
            source_row["schema_version"],
            source_row["environment_config_id"],
            source_row["environment_version"],
            source_row["random_seed"],
            source_row["external_data_version"],
            source_row["event_cursor"],
            source_snapshot_id,
            branch_key,
            source_row["checksum"],
            canonical_json(clone_metadata),
        ),
    )
    head_snapshot_id = clone_cursor.lastrowid
    branch_cursor = conn.execute(
        """
        INSERT INTO world_branches
        (branch_key, name, parent_branch_key, base_snapshot_id, head_snapshot_id,
         run_id, status, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, 'ready', ?)
        """,
        (
            branch_key,
            name or branch_key,
            parent_branch_key,
            source_snapshot_id,
            head_snapshot_id,
            run_id,
            canonical_json(metadata or {}),
        ),
    )
    conn.execute(
        """
        INSERT INTO experiment_runs
        (run_id, experiment_name, control_or_treatment, random_seed,
         environment_version, world_rules_version, environment_config_id,
         source_snapshot_id, parent_run_id, branch_key, event_cursor_start,
         status, metadata_json)
        VALUES (?, ?, 'branch', ?, ?, 'world-runtime-v1', ?, ?, ?, ?, ?, 'paused', ?)
        """,
        (
            run_id,
            name or branch_key,
            source_row["random_seed"],
            source_row["environment_version"],
            source_row["environment_config_id"],
            source_snapshot_id,
            runtime["active_run_id"] or "",
            branch_key,
            source_row["event_cursor"],
            canonical_json(metadata or {}),
        ),
    )
    return decode_world_branch(
        conn.execute(
            "SELECT * FROM world_branches WHERE id = ?",
            (branch_cursor.lastrowid,),
        ).fetchone()
    )


def decode_world_event(row):
    event = dict(row)
    event["payload"] = load_json_text(event.get("payload"), {})
    return event


def runtime_response(conn):
    current_day = get_current_day(conn)
    runtime = read_world_runtime(conn)
    latest_tick = conn.execute("SELECT * FROM world_ticks ORDER BY id DESC LIMIT 1").fetchone()
    latest_event = conn.execute(
        "SELECT id FROM world_event_stream WHERE branch_key = ? ORDER BY id DESC LIMIT 1",
        (runtime.get("active_branch_key") or "main",),
    ).fetchone()
    runtime["latest_tick"] = dict(latest_tick) if latest_tick else None
    runtime["latest_event_id"] = latest_event["id"] if latest_event else 0
    runtime["budget"] = {
        "date": runtime["budget_date"],
        "auto_model_calls_used": runtime["auto_model_calls_used"],
        "daily_auto_model_budget": runtime["daily_auto_model_budget"],
        "remaining_auto_model_calls": max(0, int(runtime["daily_auto_model_budget"]) - int(runtime["auto_model_calls_used"])),
    }
    runtime["day_sync"] = {
        "advanced": False,
        "day": current_day,
        "elapsed_days": 0,
    }
    runtime["environment_config"] = get_active_environment_config(conn)
    active_branch = conn.execute(
        "SELECT * FROM world_branches WHERE branch_key = ?",
        (runtime.get("active_branch_key") or "main",),
    ).fetchone()
    runtime["active_branch"] = decode_world_branch(active_branch) if active_branch else None
    runtime["multiscale_updates"] = {
        "schedules": [
            decode_world_update_schedule(row)
            for row in conn.execute(
                "SELECT * FROM world_update_schedules WHERE status = 'active' ORDER BY interval_seconds, id"
            ).fetchall()
        ],
        "latest_runs": [
            decode_world_update_run(row)
            for row in conn.execute(
                "SELECT * FROM world_update_runs ORDER BY id DESC LIMIT 3"
            ).fetchall()
        ],
    }
    return runtime


@app.get("/api/world/runtime")
def get_world_runtime_api():
    with get_connection() as conn:
        return runtime_response(conn)


@app.get("/api/world/environment-config")
def get_current_environment_config_api():
    with get_connection() as conn:
        return {"environment_config": get_active_environment_config(conn)}


@app.get("/api/world/environment-configs")
def list_environment_configs(limit: int = 50):
    limit = max(1, min(limit, 200))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM environment_configs
            ORDER BY config_key, version DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"environment_configs": [decode_environment_config(row) for row in rows]}


@app.post("/api/admin/world/environment-configs")
def create_environment_config_api(
    payload: EnvironmentConfigRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_admin_token(authorization)
    with get_connection() as conn:
        ensure_world_runtime_tables(conn)
        try:
            config = create_environment_config_record(
                conn,
                payload.config_key.strip(),
                payload.name.strip(),
                payload.config,
                parent_config_id=payload.parent_config_id,
                created_by=payload.created_by.strip() or "admin",
            )
            applied = None
            event = None
            if payload.activate:
                row = conn.execute(
                    "SELECT * FROM environment_configs WHERE id = ?",
                    (config["id"],),
                ).fetchone()
                applied = apply_environment_config(conn, dict(row))
                event = append_world_event(
                    conn,
                    "environment_config_activated",
                    "环境配置已激活",
                    f"校园环境切换到《{config['name']}》版本 {config['version']}。",
                    payload={"environment_config_id": config["id"], "applied": applied},
                    source_type="admin_configuration",
                    source_id=config["id"],
                    rule_version=config["version_label"],
                )
                config = get_active_environment_config(conn)
            conn.commit()
            return {"environment_config": config, "applied": applied, "event": event}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/world/environment-configs/{config_id}/activate")
def activate_environment_config_api(
    config_id: int,
    authorization: Optional[str] = Header(default=None),
):
    require_admin_token(authorization)
    with get_connection() as conn:
        ensure_world_runtime_tables(conn)
        row = conn.execute(
            "SELECT * FROM environment_configs WHERE id = ?",
            (config_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="环境配置不存在")
        try:
            applied = apply_environment_config(conn, dict(row))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config = get_active_environment_config(conn)
        event = append_world_event(
            conn,
            "environment_config_activated",
            "环境配置已激活",
            f"校园环境切换到《{config['name']}》版本 {config['version']}。",
            payload={"environment_config_id": config_id, "applied": applied},
            source_type="admin_configuration",
            source_id=config_id,
            rule_version=config["version_label"],
        )
        conn.commit()
        return {"environment_config": config, "applied": applied, "event": event}


def list_world_snapshots(limit: int = 30):
    limit = max(1, min(limit, 200))
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM world_snapshots ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return {"snapshots": [decode_world_snapshot(row) for row in rows]}


def list_world_update_schedules():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM world_update_schedules ORDER BY interval_seconds, id"
        ).fetchall()
        return {"update_schedules": [decode_world_update_schedule(row) for row in rows]}


def list_world_update_runs(update_key: str = "", status: str = "", limit: int = 50):
    limit = max(1, min(limit, 200))
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM world_update_runs
            WHERE (? = '' OR update_key = ?)
              AND (? = '' OR status = ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (update_key, update_key, status, status, limit),
        ).fetchall()
        return {"update_runs": [decode_world_update_run(row) for row in rows]}


def get_world_snapshot_api(snapshot_id: int, include_state: bool = False):
    with get_connection() as conn:
        result = get_snapshot(conn, snapshot_id, include_state=include_state, decode_snapshot=decode_world_snapshot)
        if result is None:
            raise HTTPException(status_code=404, detail="世界快照不存在")
        return result


app.state.list_world_snapshots = list_world_snapshots
app.state.list_world_update_schedules = list_world_update_schedules
app.state.list_world_update_runs = list_world_update_runs
app.state.get_world_snapshot = get_world_snapshot_api


@app.post("/api/admin/world/snapshots")
def create_world_snapshot_api(
    payload: WorldSnapshotRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_admin_token(authorization)
    with get_connection() as conn:
        try:
            return create_snapshot(
                conn,
                payload=payload,
                create_record=create_world_snapshot_record,
                append_event=append_world_event,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/world/snapshots/{snapshot_id}/restore")
def restore_world_snapshot_api(
    snapshot_id: int,
    payload: WorldSnapshotRestoreRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_admin_token(authorization)
    with WorldTickExclusion(), get_connection() as conn:
        try:
            return restore_snapshot(
                conn,
                snapshot_id,
                payload,
                runtime_id=WORLD_RUNTIME_ID,
                ensure_tables=ensure_world_runtime_tables,
                create_record=create_world_snapshot_record,
                restore_state=restore_world_snapshot_state,
                append_event=append_world_event,
            )
        except ValueError as exc:
            conn.rollback()
            if "必须先暂停" in str(exc):
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ensure_world_runtime_tables(conn)
        runtime = conn.execute(
            "SELECT * FROM world_runtime WHERE id = ?",
            (WORLD_RUNTIME_ID,),
        ).fetchone()
        try:
            active_branch_key = require_paused_runtime(runtime)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        active_branch = conn.execute(
            "SELECT * FROM world_branches WHERE branch_key = ?",
            (active_branch_key,),
        ).fetchone()
        try:
            backup = None
            if payload.create_backup:
                backup = create_world_snapshot_record(
                    conn,
                    reason=f"恢复快照 #{snapshot_id} 前自动备份：{payload.reason}",
                    snapshot_type="pre_restore_backup",
                    branch_key=active_branch_key,
                    parent_snapshot_id=active_branch["head_snapshot_id"] if active_branch else None,
                    metadata={"restore_target_snapshot_id": snapshot_id},
                )
            restored = restore_world_snapshot_state(
                conn,
                snapshot_id,
                active_branch_key=active_branch_key,
                active_run_id=runtime["active_run_id"] or "",
            )
            checkpoint = create_world_snapshot_record(
                conn,
                reason=f"已恢复快照 #{snapshot_id}：{payload.reason}",
                snapshot_type="restored_checkpoint",
                branch_key=active_branch_key,
                parent_snapshot_id=snapshot_id,
                metadata={
                    "restored_from_snapshot_id": snapshot_id,
                    "backup_snapshot_id": backup["id"] if backup else None,
                },
            )
            event = append_world_event(
                conn,
                "world_snapshot_restored",
                "世界快照已恢复",
                f"分支 {active_branch_key} 已恢复到快照 #{snapshot_id}，runtime 保持暂停。",
                payload={
                    "snapshot_id": snapshot_id,
                    "backup_snapshot_id": backup["id"] if backup else None,
                    "checkpoint_snapshot_id": checkpoint["id"],
                    "branch_key": active_branch_key,
                },
                source_type="snapshot_restore",
                source_id=snapshot_id,
                rule_version="world-snapshot-restore-v1",
                branch_key=active_branch_key,
            )
            conn.commit()
            return {
                "restored": restored,
                "backup_snapshot": backup,
                "checkpoint_snapshot": checkpoint,
                "event": event,
            }
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/world/branches")
def list_world_branches():
    with get_connection() as conn:
        return list_branches(conn, decode_branch=decode_world_branch)


@app.post("/api/admin/world/branches")
def create_world_branch_api(
    payload: WorldBranchRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_admin_token(authorization)
    with get_connection() as conn:
        try:
            return create_branch(
                conn,
                payload=payload,
                ensure_tables=ensure_world_runtime_tables,
                create_record=create_world_branch_record,
                append_event=append_world_event,
            )
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/world/branches/{branch_key}/switch")
def switch_world_branch_api(
    branch_key: str,
    payload: WorldBranchSwitchRequest,
    authorization: Optional[str] = Header(default=None),
):
    require_admin_token(authorization)
    with WorldTickExclusion(), get_connection() as conn:
        try:
            return switch_branch(
                conn,
                branch_key,
                payload,
                runtime_id=WORLD_RUNTIME_ID,
                ensure_tables=ensure_world_runtime_tables,
                create_record=create_world_snapshot_record,
                restore_state=restore_world_snapshot_state,
                append_event=append_world_event,
            )
        except ValueError as exc:
            conn.rollback()
            if "必须先暂停" in str(exc) or "目标分支不存在" in str(exc):
                raise HTTPException(status_code=409 if "暂停" in str(exc) else 404, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ensure_world_runtime_tables(conn)
        runtime = conn.execute(
            "SELECT * FROM world_runtime WHERE id = ?",
            (WORLD_RUNTIME_ID,),
        ).fetchone()
        try:
            current_branch_key = require_paused_runtime(runtime)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if branch_key == current_branch_key:
            return {"switched": False, "reason": "already_active", "branch_key": branch_key}
        target_branch = conn.execute(
            "SELECT * FROM world_branches WHERE branch_key = ?",
            (branch_key,),
        ).fetchone()
        if not target_branch or not target_branch["head_snapshot_id"]:
            raise HTTPException(status_code=404, detail="目标分支不存在或没有可恢复的分支头")
        current_branch = conn.execute(
            "SELECT * FROM world_branches WHERE branch_key = ?",
            (current_branch_key,),
        ).fetchone()
        try:
            outgoing_snapshot = create_world_snapshot_record(
                conn,
                reason=f"切换到 {branch_key} 前封存 {current_branch_key}：{payload.reason}",
                snapshot_type="branch_checkpoint",
                branch_key=current_branch_key,
                parent_snapshot_id=current_branch["head_snapshot_id"] if current_branch else None,
                metadata={"switch_target_branch": branch_key},
            )
            restored = restore_world_snapshot_state(
                conn,
                target_branch["head_snapshot_id"],
                active_branch_key=branch_key,
                active_run_id=target_branch["run_id"] or "",
            )
            conn.execute(
                """
                UPDATE world_branches
                SET status = CASE WHEN branch_key = ? THEN 'active' ELSE 'ready' END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE branch_key IN (?, ?)
                """,
                (branch_key, current_branch_key, branch_key),
            )
            conn.execute(
                """
                UPDATE experiment_runs
                SET status = CASE WHEN branch_key = ? THEN 'running' ELSE 'paused' END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE branch_key IN (?, ?)
                """,
                (branch_key, current_branch_key, branch_key),
            )
            event = append_world_event(
                conn,
                "world_branch_switched",
                "活动世界分支已切换",
                f"活动世界从 {current_branch_key} 切换到 {branch_key}，runtime 保持暂停。",
                payload={
                    "from_branch": current_branch_key,
                    "to_branch": branch_key,
                    "outgoing_snapshot_id": outgoing_snapshot["id"],
                    "restored_snapshot_id": target_branch["head_snapshot_id"],
                },
                source_type="world_branch",
                source_id=target_branch["id"],
                rule_version="world-branch-v1",
                branch_key=branch_key,
            )
            conn.commit()
            return {
                "switched": True,
                "from_branch": current_branch_key,
                "to_branch": branch_key,
                "outgoing_snapshot": outgoing_snapshot,
                "restored": restored,
                "event": event,
            }
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/world/action-rules")
def list_world_action_rules():
    with get_connection() as conn:
        return read_action_rules(conn, decode_action_rule=decode_world_action_rule)


@app.get("/api/world/action-executions")
def list_world_action_executions(
    resident_id: Optional[int] = None,
    status: str = "",
    limit: int = 50,
):
    with get_connection() as conn:
        return read_action_executions(
            conn,
            resident_id=resident_id,
            status=status,
            limit=limit,
            load_json=load_json_text,
        )


@app.get("/api/world/delayed-effects")
def list_world_delayed_effects(status: str = "", limit: int = 50):
    with get_connection() as conn:
        return read_delayed_effects(
            conn,
            status=status,
            limit=limit,
            load_json=load_json_text,
        )


@app.get("/api/world/events")
def get_world_events(after_id: int = 0, limit: int = 50, branch_key: str = ""):
    with get_connection() as conn:
        return read_world_events(
            conn,
            after_id=after_id,
            limit=limit,
            branch_key=branch_key,
            active_branch_key=active_world_branch_key,
            decode_world_event=decode_world_event,
        )


def current_metric_value(conn, metric_name, location=""):
    day = get_current_day(conn)
    env = dict(get_campus_environment(conn, day))
    if metric_name in env:
        return float(env.get(metric_name) or 0)
    if metric_name == "agent_count" and location:
        row = conn.execute("SELECT COUNT(*) AS value FROM residents WHERE location = ?", (location,)).fetchone()
        return float(row["value"] if row else 0)
    if metric_name == "action_count":
        row = conn.execute("SELECT COUNT(*) AS value FROM simulation_action_logs WHERE day = ?", (day,)).fetchone()
        return float(row["value"] if row else 0)
    return None


@app.post("/api/research/calibration-observations")
def create_calibration_observation(payload: CalibrationObservationRequest):
    with get_connection() as conn:
        ensure_world_runtime_tables(conn)
        if payload.location and payload.location not in VALID_LOCATIONS:
            raise HTTPException(status_code=400, detail="校准观测地点不存在")
        cursor = conn.execute(
            """
            INSERT INTO calibration_observations
            (source_name, observed_at, metric_name, metric_value, location, role_group, sample_size, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.source_name,
                payload.observed_at or get_world_now().isoformat(),
                payload.metric_name,
                payload.metric_value,
                payload.location,
                payload.role_group,
                payload.sample_size,
                json_dumps(payload.metadata, ensure_ascii=False),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM calibration_observations WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return {"observation": dict(row)}


@app.get("/api/research/calibration-report")
def get_calibration_report():
    with get_connection() as conn:
        ensure_world_runtime_tables(conn)
        rows = conn.execute(
            """
            SELECT * FROM calibration_observations
            ORDER BY id DESC
            LIMIT 100
            """
        ).fetchall()
        comparisons = []
        for row in rows:
            simulated = current_metric_value(conn, row["metric_name"], row["location"])
            if simulated is None:
                continue
            observed = float(row["metric_value"])
            delta = simulated - observed
            comparisons.append(
                {
                    "metric_name": row["metric_name"],
                    "location": row["location"],
                    "observed": observed,
                    "simulated": simulated,
                    "delta": delta,
                    "relative_error": round(abs(delta) / max(1.0, abs(observed)), 3),
                }
            )
        mean_error = round(sum(item["relative_error"] for item in comparisons) / len(comparisons), 3) if comparisons else None
        summary = "暂无可比较校准观测。" if mean_error is None else f"最近 {len(comparisons)} 条可比较观测的平均相对误差为 {mean_error}。"
        report_key = f"calibration-{get_world_now().strftime('%Y%m%d%H%M%S')}"
        cursor = conn.execute(
            """
            INSERT INTO calibration_reports
            (report_key, summary, parameter_updates, quality_report_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                report_key,
                summary,
                json_dumps({}, ensure_ascii=False),
                json_dumps({"mean_relative_error": mean_error, "comparisons": comparisons[:40]}, ensure_ascii=False),
            ),
        )
        conn.commit()
        return {"report_id": cursor.lastrowid, "summary": summary, "mean_relative_error": mean_error, "comparisons": comparisons}


@app.post("/api/world/observer-sessions")
def upsert_observer_session(payload: ObserverSessionRequest):
    if payload.session_type not in {"observer", "participant", "admin"}:
        raise HTTPException(status_code=400, detail="session_type 只支持 observer/participant/admin")
    if payload.focused_location and payload.focused_location not in VALID_LOCATIONS:
        raise HTTPException(status_code=400, detail="关注地点不存在")
    with get_connection() as conn:
        if payload.focused_resident_id and not get_resident(conn, payload.focused_resident_id):
            raise HTTPException(status_code=404, detail="关注 Agent 不存在")
        now = get_world_now().isoformat()
        if payload.session_id:
            existing = conn.execute("SELECT * FROM observer_sessions WHERE id = ?", (payload.session_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE observer_sessions
                    SET user_id = ?, session_type = ?, focused_resident_id = ?,
                        focused_location = ?, last_seen_at = ?
                    WHERE id = ?
                    """,
                    (payload.user_id, payload.session_type, payload.focused_resident_id, payload.focused_location, now, payload.session_id),
                )
                session_id = payload.session_id
            else:
                session_id = None
        else:
            session_id = None
        if not session_id:
            cursor = conn.execute(
                """
                INSERT INTO observer_sessions
                (user_id, session_type, focused_resident_id, focused_location, started_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (payload.user_id, payload.session_type, payload.focused_resident_id, payload.focused_location, now, now),
            )
            session_id = cursor.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM observer_sessions WHERE id = ?", (session_id,)).fetchone()
        return {"session": dict(row), "event": None}


@app.post("/api/admin/world/start")
def start_world_runtime(authorization: Optional[str] = Header(default=None)):
    require_admin_token(authorization)
    with get_connection() as conn:
        set_simulation_state_value(conn, "world_runtime_manual_pause", "false")
        runtime = update_world_runtime_status(conn, "running")
        append_world_event(conn, "admin_world_start", "世界运行已启动", "admin 启动了校园平行世界后台运行。")
        conn.commit()
        return {"message": "世界运行已启动", "runtime": runtime}


@app.post("/api/admin/world/pause")
def pause_world_runtime(authorization: Optional[str] = Header(default=None)):
    require_admin_token(authorization)
    with get_connection() as conn:
        set_simulation_state_value(conn, "world_runtime_manual_pause", "true")
        runtime = update_world_runtime_status(conn, "paused")
        append_world_event(conn, "admin_world_pause", "世界运行已暂停", "admin 暂停了校园平行世界后台运行。")
        conn.commit()
        return {"message": "世界运行已暂停", "runtime": runtime}


@app.post("/api/admin/world/tick")
def run_world_tick_once(authorization: Optional[str] = Header(default=None)):
    require_admin_token(authorization)
    return {"message": "世界 tick 已完成", "tick": advance_world_tick(reason="admin")}


def generate_admin_event_impact(conn, payload, base_event, day):
    if not consume_auto_model_budget(conn, "admin", resident_id=payload.resident_id):
        return None
    model_name = os.getenv("LLM_MODEL") or os.getenv("LLM_API_MODEL") or "configured-llm"
    target_text = "、".join(payload.target_spaces) or payload.location or "校园全局"
    prompt = f"""
你是校园平行世界的事件导演。admin 刚刚向世界注入一个事件，请生成一条短的运行反馈。

事件标题：{payload.title}
事件内容：{payload.content or '无补充内容'}
事件类型：{payload.event_type}
目标空间：{target_text}
目标 Agent：{payload.resident_id or '无'}

要求：
- 只写 1 句中文，100 字以内。
- 说明这个事件会如何被校园空间或 Agent 感知到。
- 不要写技术字段，不要承诺尚未执行的长期结果。
"""
    try:
        raw = ask_llm(prompt)
        content = re.sub(r"\s+", " ", raw).strip().strip('"“”')[:180]
        if not content:
            raise ValueError("empty admin impact")
        event = append_world_event(
            conn,
            "admin_model_impact",
            "admin 事件影响已生成",
            content,
            resident_id=payload.resident_id,
            location=payload.location,
            payload={"base_event_id": base_event["id"], "target_spaces": payload.target_spaces},
            day=day,
        )
        log_model_call(
            conn,
            "admin",
            status="success",
            resident_id=payload.resident_id,
            related_event_id=event["id"],
            model_name=model_name,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(raw) // 4),
        )
        return event
    except Exception as exc:
        logger.warning("Admin event impact LLM failed", exc_info=True)
        log_model_call(conn, "admin", status=f"failed:{type(exc).__name__}", resident_id=payload.resident_id, related_event_id=base_event["id"], model_name=model_name)
        return None


@app.post("/api/admin/events/trigger")
def trigger_admin_world_event(payload: AdminWorldEventRequest, authorization: Optional[str] = Header(default=None)):
    require_admin_token(authorization)
    invalid_spaces = set(payload.target_spaces) - set(VALID_LOCATIONS)
    if invalid_spaces:
        raise HTTPException(status_code=400, detail=f"不存在的空间：{sorted(invalid_spaces)}")
    if payload.location and payload.location not in VALID_LOCATIONS:
        raise HTTPException(status_code=400, detail="事件地点不存在")
    with get_connection() as conn:
        day = get_current_day(conn)
        if payload.resident_id and not get_resident(conn, payload.resident_id):
            raise HTTPException(status_code=404, detail="目标 Agent 不存在")
        campus_event = None
        if payload.target_spaces:
            campus_event = create_campus_event(conn, day, payload.title, payload.event_type, payload.intensity, payload.target_spaces)
        content = payload.content or f"admin 触发事件：{payload.title}"
        event = append_world_event(
            conn,
            payload.event_type,
            payload.title,
            content,
            resident_id=payload.resident_id,
            location=payload.location,
            payload={**payload.payload, "campus_event": campus_event},
            day=day,
        )
        log_model_call(conn, "admin", status="event_recorded", resident_id=payload.resident_id, related_event_id=event["id"])
        impact_event = generate_admin_event_impact(conn, payload, event, day)
        add_event(conn, day, "admin_world_event", content)
        conn.commit()
        return {"message": "admin 事件已写入世界", "event": event, "impact_event": impact_event, "campus_event": campus_event}


@app.get("/api/state")
def get_state():
    with get_connection() as conn:
        day_sync = sync_current_day_with_world_date(conn, get_world_now())
        if day_sync.get("advanced"):
            conn.commit()
        day = day_sync["day"]
        residents = conn.execute("SELECT * FROM residents ORDER BY id").fetchall()
        events = conn.execute(
            "SELECT * FROM city_events ORDER BY id DESC LIMIT 80"
        ).fetchall()
        return {
            "world_type": "campus_closed_world",
            "current_day": day,
            "locations": sorted(VALID_LOCATIONS),
            "environment": get_campus_environment(conn, day),
            "spaces": get_space_snapshot(conn, day),
            "agents": rows_to_dicts(residents),
            "residents": rows_to_dicts(residents),
            "events": rows_to_dicts(events),
            "agent_modules": get_all_agent_module_states(conn),
        }


def get_world_observer_state():
    with get_connection() as conn:
        return build_world_observer_state(
            conn,
            get_current_day=get_current_day,
            read_world_runtime=read_world_runtime,
            get_campus_environment=get_campus_environment,
            get_space_snapshot=get_space_snapshot,
            rows_to_dicts=rows_to_dicts,
        )


app.state.get_world_observer_state = get_world_observer_state


def get_agents():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM residents ORDER BY id").fetchall()
        return rows_to_dicts(rows)


def get_residents():
    return get_agents()



def get_agents_modules():
    with get_connection() as conn:
        return get_all_agent_module_states(conn)


def get_agent_modules(resident_id: int):
    with get_connection() as conn:
        return get_agent_module_state(conn, resident_id)


app.state.list_agents = get_agents
app.state.list_agent_modules = get_agents_modules
app.state.get_agent_modules = get_agent_modules


def get_relevant_agent_memories(resident_id: int, query: str = ""):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        terms = [term.strip() for term in query.split(",") if term.strip()]
        return {
            "resident_id": resident_id,
            "query_terms": terms,
            "memories": retrieve_relevant_memories(conn, resident_id, query_terms=terms),
        }


def get_agent_memories(resident_id: int, limit: int = 20, offset: int = 0):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        ensure_memory_columns(conn)
        current_day = get_current_day(conn)
        page_limit = min(max(limit, 1), 100)
        page_offset = max(offset, 0)
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM memories WHERE resident_id = ? AND day <= ?",
            (resident_id, current_day),
        ).fetchone()["total"]
        rows = conn.execute(
            """
            SELECT id, day, content, importance, memory_type, tags, source,
                   access_count, last_accessed_at, created_at
            FROM memories
            WHERE resident_id = ? AND day <= ?
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (resident_id, current_day, page_limit, page_offset),
        ).fetchall()
        memories = rows_to_dicts(rows)
        return {
            "resident_id": resident_id,
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(memories) < total,
            "memories": memories,
        }


app.state.get_relevant_agent_memories = get_relevant_agent_memories
app.state.get_agent_memories = get_agent_memories

def get_inventory():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT inventory.*, residents.name AS owner_name
            FROM inventory
            JOIN residents ON residents.id = inventory.resident_id
            ORDER BY resident_id, item_name
            """
        ).fetchall()
        return rows_to_dicts(rows)


def get_today_environment():
    with get_connection() as conn:
        return get_campus_environment(conn)


def get_campus_spaces():
    with get_connection() as conn:
        return get_space_snapshot(conn)


app.state.get_inventory = get_inventory
app.state.get_today_environment = get_today_environment
app.state.get_campus_spaces = get_campus_spaces


@app.post("/api/campus/spaces/{location}/status")
def set_space_status(location: str, payload: SpaceStatusRequest):
    with get_connection() as conn:
        ensure_space_system(conn)
        updated = conn.execute(
            "UPDATE campus_spaces SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE location = ?",
            (payload.status, location),
        )
        if updated.rowcount == 0:
            raise HTTPException(status_code=404, detail="空间不存在")
        day = get_current_day(conn)
        add_event(conn, day, "space_status", f"空间「{location}」状态调整为：{payload.status}。")
        conn.commit()
        return get_space_snapshot(conn, day)


@app.post("/api/campus/events/trigger")
def trigger_campus_event(payload: CampusEventRequest):
    with get_connection() as conn:
        day = get_current_day(conn)
        invalid_spaces = set(payload.target_spaces) - set(VALID_LOCATIONS)
        if invalid_spaces:
            raise HTTPException(status_code=400, detail=f"不存在的空间：{sorted(invalid_spaces)}")
        event = create_campus_event(
            conn,
            day,
            payload.title,
            payload.event_type,
            payload.intensity,
            payload.target_spaces,
            payload.effects,
        )
        return {"message": "校园事件已触发", "event": event, "environment": get_campus_environment(conn, day), "spaces": get_space_snapshot(conn, day)}


@app.post("/api/campus/events/{event_id}/resolve")
def resolve_campus_event(event_id: int):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM campus_events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="事件不存在")
        conn.execute(
            "UPDATE campus_events SET status = 'resolved', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (event_id,),
        )
        day = get_current_day(conn)
        add_event(conn, day, "campus_event_resolved", f"校园事件《{row['title']}》已结束。")
        conn.commit()
        return {"message": "校园事件已结束", "event_id": event_id, "spaces": get_space_snapshot(conn, day)}


@app.post("/api/campus/environment/sync-real-time")
def sync_real_time():
    with get_connection() as conn:
        day = get_current_day(conn)
        values = dict(get_campus_environment(conn, day))
        values = derive_environment_from_real_time(values)
        save_environment_values(conn, day, values)
        add_event(conn, day, "real_time_sync", f"校园环境已同步真实时间：{values['real_date']} {values['real_time']}，{values['weekday']}，{values['time_slot']}。")
        conn.commit()
        return {"message": "真实时间同步成功", "environment": get_campus_environment(conn, day)}


@app.post("/api/campus/environment/sync-real-weather")
def sync_real_weather():
    with get_connection() as conn:
        result = sync_real_weather_into_world(conn, event_type="real_weather_manual_sync")
        conn.commit()
        env = result["environment"]
        env["real_weather_raw"] = result["raw"]
        return env


@app.post("/api/campus/environment/set")
def set_today_environment(payload: CampusEnvironmentRequest):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="至少填写一个环境参数")

    allowed = set(DEFAULT_ENV.keys())
    if not set(updates).issubset(allowed):
        raise HTTPException(status_code=400, detail="存在不支持的环境参数")

    with get_connection() as conn:
        day = get_current_day(conn)
        get_campus_environment(conn, day)
        set_clause = ", ".join([f"{key} = ?" for key in updates])
        values = list(updates.values()) + [day]
        conn.execute(f"UPDATE campus_state SET {set_clause} WHERE day = ?", values)
        add_event(conn, day, "environment_update", f"校园环境参数更新：{updates}")
        conn.commit()
        return get_campus_environment(conn, day)




@app.get("/api/social/hierarchy")
def get_social_hierarchy():
    with get_connection() as conn:
        return build_social_hierarchy(
            conn,
            ensure_tables=ensure_social_system_tables,
            get_hierarchy_title=get_hierarchy_title,
        )


@app.get("/api/agents/{resident_id}/learning")
def get_agent_learning(resident_id: int):
    with get_connection() as conn:
        return list_agent_learning(conn, resident_id, ensure_tables=ensure_social_system_tables, rows_to_dicts=rows_to_dicts)


@app.post("/api/social/communicate")
def social_communicate(payload: ChatRequest):
    with get_connection() as conn:
        ensure_social_system_tables(conn)
        result = chat_between(conn, payload.speaker_id, payload.listener_id, payload.message)
        record_learning(conn, payload.speaker_id, "communicate", "完成沟通", action_score("communicate", True), f"主动沟通：{payload.message}")
        record_learning(conn, payload.listener_id, "communicate", "回应沟通", action_score("communicate", True), f"收到沟通：{payload.message}")
        conn.commit()
        return {"type": "communication", "result": result}


@app.post("/api/social/negotiate")
def social_negotiate(payload: NegotiateRequest):
    with get_connection() as conn:
        return negotiate_between(conn, payload.initiator_id, payload.target_id, payload.topic, payload.proposal)


@app.post("/api/social/collaborate")
def social_collaborate(payload: CollaborateRequest):
    with get_connection() as conn:
        return create_collaboration(conn, payload.leader_id, payload.member_ids, payload.title, payload.goal)


@app.post("/api/social/compete")
def social_compete(payload: CompeteRequest):
    with get_connection() as conn:
        return create_competition(conn, payload.participant_ids, payload.title, payload.metric)


@app.get("/api/agents/{resident_id}/long-term-goals")
def get_long_term_goals(resident_id: int):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        return list_long_term_goals(conn, resident_id, ensure_tables=ensure_social_system_tables, rows_to_dicts=rows_to_dicts)


@app.get("/api/agents/{resident_id}/goal-system")
def get_agent_goal_system(resident_id: int):
    with get_connection() as conn:
        resident = get_resident(conn, resident_id)
        if not resident:
            raise HTTPException(status_code=404, detail="Agent 不存在")
        return build_goal_system(
            conn,
            resident_id,
            resident=resident,
            ensure_tables=ensure_social_system_tables,
            rows_to_dicts=rows_to_dicts,
            load_json=load_json_text,
        )
        ensure_social_system_tables(conn)
        profile = conn.execute(
            "SELECT strategy, energy, mood, current_task FROM agent_profiles WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()
        goals = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM agent_goals
                WHERE resident_id = ?
                ORDER BY CASE horizon WHEN 'long' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                         status, priority DESC, id
                """,
                (resident_id,),
            ).fetchall()
        )
        by_parent = {}
        for goal in goals:
            by_parent.setdefault(goal.get("parent_goal_id"), []).append(goal)

        def goal_node(goal):
            item = dict(goal)
            item["children"] = [goal_node(child) for child in by_parent.get(goal["id"], [])]
            return item

        dependencies = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM goal_dependencies
                WHERE goal_id IN (SELECT id FROM agent_goals WHERE resident_id = ?)
                ORDER BY id
                """,
                (resident_id,),
            ).fetchall()
        )
        commitments = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM agent_commitments
                WHERE resident_id = ?
                ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, due_at DESC, id DESC
                LIMIT 40
                """,
                (resident_id,),
            ).fetchall()
        )
        revisions = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM goal_revisions
                WHERE resident_id = ?
                ORDER BY id DESC LIMIT 60
                """,
                (resident_id,),
            ).fetchall()
        )
        outcomes = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM plan_outcomes
                WHERE resident_id = ?
                ORDER BY id DESC LIMIT 60
                """,
                (resident_id,),
            ).fetchall()
        )
        trajectories = rows_to_dicts(
            conn.execute(
                """
                SELECT * FROM trajectory_episodes
                WHERE resident_id = ?
                ORDER BY CASE horizon WHEN 'long' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                         id DESC
                """,
                (resident_id,),
            ).fetchall()
        )
        plan_row = conn.execute(
            """
            SELECT * FROM agent_action_plans
            WHERE resident_id = ? AND status = 'active'
            ORDER BY window_start DESC LIMIT 1
            """,
            (resident_id,),
        ).fetchone()
        current_plan = dict(plan_row) if plan_row else None
        if current_plan:
            current_plan["plan"] = load_json_text(current_plan.pop("plan_json"), {})
        strategy = load_json_text(profile["strategy"], {}) if profile else {}
        return {
            "version": "multiscale-goals-v1",
            "resident": dict(resident),
            "stable_layer": {
                "personality": resident["personality"],
                "role": resident["role"],
                "money": resident["money"],
                "energy": profile["energy"] if profile else None,
                "mood": profile["mood"] if profile else "",
                "current_task": profile["current_task"] if profile else "",
                "personality_traits": strategy.get("personality_traits", {}) if isinstance(strategy, dict) else {},
            },
            "goal_tree": [goal_node(goal) for goal in by_parent.get(None, [])],
            "goals": goals,
            "dependencies": dependencies,
            "commitments": commitments,
            "current_plan": current_plan,
            "recent_outcomes": outcomes,
            "goal_revisions": revisions,
            "trajectory_episodes": trajectories,
        }


@app.post("/api/goals")
def create_long_term_goal(payload: LongTermGoalRequest):
    with get_connection() as conn:
        if not get_resident(conn, payload.resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        ensure_social_system_tables(conn)
        day = get_current_day(conn)
        cursor = conn.execute(
            """
            INSERT INTO long_term_goals (resident_id, title, category, deadline_day, last_update_day)
            VALUES (?, ?, ?, ?, ?)
            """,
            (payload.resident_id, payload.title, payload.category, payload.deadline_day or day + 14, day),
        )
        seed_multiscale_goals(conn)
        unified = conn.execute(
            "SELECT id FROM agent_goals WHERE legacy_long_term_goal_id = ?",
            (cursor.lastrowid,),
        ).fetchone()
        add_event(conn, day, "long_term_goal", f"Agent {payload.resident_id} 新增长期目标《{payload.title}》。")
        conn.commit()
        return {
            "message": "长期目标已创建",
            "goal_id": cursor.lastrowid,
            "agent_goal_id": unified["id"] if unified else None,
        }


@app.get("/api/social/relationships/{resident_id}")
def get_social_relationships(resident_id: int):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        return list_relationships(conn, resident_id, ensure_tables=ensure_social_system_tables, get_relationship_dynamics=get_relationship_dynamics)


def _life_course_action_label(action):
    return {
        "move": "移动",
        "chat": "交流",
        "buy_sell": "交易",
        "submit_policy": "政策提案",
        "observe": "观察",
        "create_group": "创建群体",
        "join_group": "加入群体",
        "leave_group": "离开群体",
        "attend_class": "参加课程",
        "club_activity": "参加活动",
        "collaborate": "协作",
        "conflict": "冲突",
        "reflect": "反思",
        "rest": "休息",
    }.get(str(action or "").strip(), "行动")


def _life_course_evidence(source, row_id):
    return {"source": source, "id": row_id}


def _life_course_score_event(event):
    """Score turning points with transparent rules; never infer causality here."""
    score = int(event.get("importance") or 1)
    reasons = []
    event_type = str(event.get("event_type") or "")
    action = str(event.get("action") or "")
    content = str(event.get("content") or "")
    is_memory = event.get("source") == "memories"
    if action in {"chat", "conflict", "collaborate", "create_group", "join_group", "leave_group", "submit_policy"}:
        score += 2
        reasons.append("社会互动或群体行为")
    if action in {"conflict", "submit_policy", "create_group", "leave_group"} or "conflict" in event_type:
        score += 2
        reasons.append("可能改变关系或群体状态")
    if "failed" in event_type or event.get("success") is False:
        score += 2
        reasons.append("行动失败或运行异常")
    if event.get("goal_completed"):
        score += 3
        reasons.append("长期目标完成")
    if event.get("memory_importance", 0) >= 3:
        score += 1 if is_memory else 2
        reasons.append("留下重要记忆" if is_memory else "行动被记忆强化")
    if event.get("spread_count", 0) > 1:
        score += 1
        reasons.append("影响多个对象或地点")
    if any(word in content for word in ("关系", "小组", "冲突", "目标", "政策", "信息")):
        score += 1
    if not reasons:
        reasons.append("构成日常行动轨迹")
    level = "turning_point" if score >= 7 else ("important" if score >= 4 else "ordinary")
    event["turning_point_score"] = min(score, 12)
    event["significance"] = level
    event["significance_reasons"] = reasons
    return event


def _life_course_kind(item):
    if item.get("source") == "memories":
        return "memory"
    return "action"


def _life_course_display_title(item):
    source = item.get("source")
    action = item.get("action")
    if source == "memories":
        return "日记与记忆" if item.get("memory_source") == "diary" else "记忆沉淀"
    if source == "simulation_action_logs":
        return f"{_life_course_action_label(action)}路线"
    if source == "world_event_stream":
        return item.get("title") or f"{_life_course_action_label(action)}事件"
    return item.get("title") or "校园经历"


def _life_course_turning_summary(item):
    reasons = item.get("significance_reasons") or []
    if item.get("source") == "memories":
        prefix = "重要记忆" if item.get("memory_source") != "diary" else "重要日记"
    elif item.get("significance") == "turning_point":
        prefix = "关键转折"
    else:
        prefix = "重要行动"
    return f"{prefix} · {'、'.join(reasons[:2]) if reasons else '值得回看'}"


def _life_course_timeline(conn, resident_id, from_day=None, to_day=None, limit=240):
    clauses = ["resident_id = ?"]
    params = [resident_id]
    if from_day is not None:
        clauses.append("day >= ?")
        params.append(max(1, int(from_day)))
    if to_day is not None:
        clauses.append("day <= ?")
        params.append(max(1, int(to_day)))
    where = " AND ".join(clauses)
    branch_key = active_world_branch_key(conn)
    events = []
    seen_action_keys = set()
    world_event_items = {}
    memory_items = {}

    world_rows = conn.execute(
        f"""
        SELECT id, tick_id, day, slot, event_type, resident_id, location, title, content, payload, created_at
        FROM world_event_stream
        WHERE {where} AND branch_key = ?
          AND event_type NOT IN ('observer_session', 'observer_model_detail')
        ORDER BY day ASC, id ASC
        LIMIT ?
        """,
        params + [branch_key, min(max(int(limit), 20), 500)],
    ).fetchall()
    for row in world_rows:
        payload = load_json_text(row["payload"], {})
        action = payload.get("action") or payload.get("runtime_decision", {}).get("action")
        key = (row["day"], str(action or row["event_type"]), row["location"] or "", row["content"] or "")
        if key in world_event_items:
            existing = world_event_items[key]
            existing["repeat_count"] = int(existing.get("repeat_count") or 1) + 1
            existing["evidence"].append(_life_course_evidence("world_event_stream", row["id"]))
            continue
        seen_action_keys.add(key)
        item = {
            "id": row["id"],
            "day": row["day"],
            "slot": row["slot"],
            "event_type": row["event_type"],
            "action": action,
            "title": row["title"],
            "content": row["content"],
            "location": row["location"],
            "created_at": row["created_at"],
            "source": "world_event_stream",
            "evidence": [_life_course_evidence("world_event_stream", row["id"])],
            "payload": payload,
            "success": row["event_type"] not in {"agent_tick_failed", "world_tick_failed"},
            "memory_importance": 0,
            "spread_count": len(payload.get("recipients", [])) if isinstance(payload.get("recipients"), list) else 0,
            "repeat_count": 1,
        }
        world_event_items[key] = item
        events.append(_life_course_score_event(item))

    log_rows = conn.execute(
        f"""
        SELECT id, day, tick_id, perception, retrieved_memories, decision, execution,
               environment_feedback, state_before, state_after, created_at
        FROM simulation_action_logs
        WHERE {where}
        ORDER BY day ASC, id ASC
        LIMIT ?
        """,
        params + [min(max(int(limit), 20), 500)],
    ).fetchall()
    for row in log_rows:
        decision = load_json_text(row["decision"], {})
        execution = load_json_text(row["execution"], {})
        feedback = load_json_text(row["environment_feedback"], {})
        action = decision.get("action") or execution.get("action")
        result = execution.get("result") if isinstance(execution, dict) else {}
        result = result if isinstance(result, dict) else {}
        location = result.get("location") or decision.get("tool_input", {}).get("destination", "")
        key = (row["day"], str(action or "action"), str(location or ""), str(result.get("description") or result.get("message") or decision.get("reason") or ""))
        if key in seen_action_keys:
            continue
        seen_action_keys.add(key)
        goal_update = execution.get("long_term_goal") if isinstance(execution, dict) else {}
        item = {
            "id": row["id"],
            "day": row["day"],
            "slot": "",
            "event_type": "simulation_action",
            "action": action,
            "title": f"{_life_course_action_label(action)}行动",
            "content": result.get("description") or result.get("message") or str(decision.get("reason") or "完成一次行动"),
            "location": location,
            "created_at": row["created_at"],
            "tick_id": row["tick_id"],
            "source": "simulation_action_logs",
            "evidence": [_life_course_evidence("simulation_action_logs", row["id"])],
            "decision": decision,
            "execution": execution,
            "environment_feedback": feedback,
            "retrieved_memories": load_json_text(row["retrieved_memories"], []),
            "state_before": load_json_text(row["state_before"], {}),
            "state_after": load_json_text(row["state_after"], {}),
            "success": execution.get("success", "error" not in result),
            "goal_completed": isinstance(goal_update, dict) and goal_update.get("status") == "completed",
            "memory_importance": 0,
            "spread_count": 0,
            "repeat_count": 1,
        }
        events.append(_life_course_score_event(item))

    memory_rows = conn.execute(
        f"""
        SELECT id, day, content, importance, memory_type, source, created_at
        FROM memories
        WHERE {where}
        ORDER BY day ASC, id ASC
        LIMIT ?
        """,
        params + [min(max(int(limit), 20), 500)],
    ).fetchall()
    for row in memory_rows:
        importance = int(row["importance"] or 1)
        source = str(row["source"] or "action")
        if importance < 2 and source not in {"diary", "relationship", "fallback", "world_tick"}:
            continue
        item = {
            "id": row["id"],
            "day": row["day"],
            "slot": "",
            "event_type": "memory",
            "action": "memory",
            "title": "个人经历记录" if source != "diary" else "个人日记",
            "content": row["content"],
            "location": "",
            "created_at": row["created_at"],
            "source": "memories",
            "evidence": [_life_course_evidence("memories", row["id"])],
            "importance": importance,
            "memory_type": row["memory_type"],
            "memory_source": source,
            "memory_importance": importance,
            "success": True,
            "spread_count": 0,
            "repeat_count": 1,
        }
        memory_key = (row["day"], source, row["content"] or "")
        if memory_key in memory_items:
            existing = memory_items[memory_key]
            existing["repeat_count"] = int(existing.get("repeat_count") or 1) + 1
            existing["evidence"].append(_life_course_evidence("memories", row["id"]))
            continue
        memory_items[memory_key] = item
        events.append(_life_course_score_event(item))

    events.sort(key=lambda item: (int(item.get("day") or 0), str(item.get("created_at") or ""), int(item.get("id") or 0)))
    return events[-min(max(int(limit), 20), 500):]


def _life_course_relationships(conn, resident_id, timeline):
    rows = conn.execute(
        """
        SELECT relationships.to_resident_id, residents.name, residents.role,
               relationships.score, relationship_dynamics.affinity,
               relationship_dynamics.trust, relationship_dynamics.cooperation,
               relationship_dynamics.competition, relationship_dynamics.conflict,
               relationship_dynamics.interaction_count
        FROM relationships
        JOIN residents ON residents.id = relationships.to_resident_id
        LEFT JOIN relationship_dynamics
          ON relationship_dynamics.from_resident_id = relationships.from_resident_id
         AND relationship_dynamics.to_resident_id = relationships.to_resident_id
        WHERE relationships.from_resident_id = ?
        ORDER BY relationships.score DESC
        LIMIT 12
        """,
        (resident_id,),
    ).fetchall()
    result = []
    for row in rows:
        target_id = row["to_resident_id"]
        history_rows = conn.execute(
            """
            SELECT id, day, tick_id, event_id, interaction, reason,
                   affinity_before, affinity_after, trust_before, trust_after,
                   cooperation_before, cooperation_after, competition_before, competition_after,
                   conflict_before, conflict_after, created_at
            FROM relationship_change_events
            WHERE from_resident_id = ? AND to_resident_id = ?
            ORDER BY day ASC, id ASC
            LIMIT 30
            """,
            (resident_id, target_id),
        ).fetchall()
        related = []
        for event in timeline:
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            social = payload.get("social_effect") if isinstance(payload.get("social_effect"), dict) else {}
            if social.get("target_id") == target_id or payload.get("target_id") == target_id:
                related.append(event["id"])
        result.append({
            "resident_id": target_id,
            "name": row["name"],
            "role": row["role"],
            "score": row["score"],
            "affinity": row["affinity"] if row["affinity"] is not None else 50,
            "trust": row["trust"] if row["trust"] is not None else 50,
            "cooperation": row["cooperation"] if row["cooperation"] is not None else 50,
            "competition": row["competition"] if row["competition"] is not None else 0,
            "conflict": row["conflict"] if row["conflict"] is not None else 0,
            "interaction_count": row["interaction_count"] if row["interaction_count"] is not None else 0,
            "evidence_event_ids": related[:12],
            "history_available": bool(history_rows),
            "history": rows_to_dicts(history_rows),
            "emergent_interpretation": infer_emergent_relationship(conn, resident_id, target_id, dict(row), row["score"]),
        })
    return result


def _life_course_groups(conn, resident_id, timeline):
    groups = []
    rows = conn.execute("SELECT * FROM group_goals ORDER BY status, id DESC").fetchall()
    for row in rows:
        members = load_json_text(row["member_ids"], [])
        member_ids = [int(member) for member in members if str(member).isdigit()]
        if resident_id not in member_ids and int(row["leader_id"]) != resident_id:
            continue
        evidence = [
            event["id"]
            for event in timeline
            if any(word in str(event.get("event_type") or "") for word in ("group", "collabor"))
            or any(word in str(event.get("content") or "") for word in (str(row["name"]), str(row["shared_goal"])))
        ]
        groups.append({
            "id": row["id"],
            "name": row["name"],
            "group_type": row["group_type"],
            "leader_id": row["leader_id"],
            "member_ids": member_ids,
            "roles": load_json_text(row["roles"], {}),
            "shared_goal": row["shared_goal"],
            "progress": row["progress"],
            "target_progress": row["target_progress"],
            "status": row["status"],
            "evidence_event_ids": evidence[:12],
            "membership_history_available": False,
        })
        history = conn.execute("SELECT id, day, resident_id, action, reason, member_ids, created_at FROM group_membership_events WHERE group_id = ? ORDER BY day ASC, id ASC LIMIT 100", (row["id"],)).fetchall()
        groups[-1]["membership_history"] = rows_to_dicts(history)
        groups[-1]["membership_history_available"] = bool(history)
    return groups


def _life_course_episodes(timeline):
    """Aggregate tick-level evidence into daily experience episodes."""
    by_day = {}
    for event in timeline:
        day = int(event.get("day") or 0)
        if day <= 0:
            continue
        episode = by_day.setdefault(day, {
            "id": f"day-{day}", "day": day, "event_ids": [], "actions": [],
            "locations": [], "evidence": [], "event_count": 0, "repeat_count": 0,
            "planned_actions": [], "actual_actions": [], "deviations": [],
            "feedback": [], "memories": [], "state_before": None, "state_after": None,
            "reasons": [],
        })
        episode["event_ids"].append(event.get("id"))
        episode["event_count"] += 1
        episode["repeat_count"] += max(1, int(event.get("repeat_count") or 1))
        if event.get("action") and event["action"] != "memory" and event["action"] not in episode["actions"]:
            episode["actions"].append(event["action"])
        decision = event.get("decision") if isinstance(event.get("decision"), dict) else {}
        execution = event.get("execution") if isinstance(event.get("execution"), dict) else {}
        planned = decision.get("planned_action") or decision.get("action")
        actual = execution.get("action") or (event.get("action") if event.get("action") != "memory" else None)
        if planned and planned not in episode["planned_actions"]: episode["planned_actions"].append(planned)
        if actual and actual not in episode["actual_actions"]: episode["actual_actions"].append(actual)
        if planned and actual and planned != actual:
            episode["deviations"].append({"planned": planned, "actual": actual, "reason": decision.get("reason", "")})
        reason = str(decision.get("reason") or event.get("content") or "").strip()
        if reason and reason not in episode["reasons"] and event.get("source") != "memories":
            episode["reasons"].append(reason)
        if event.get("location") and event["location"] not in episode["locations"]: episode["locations"].append(event["location"])
        if event.get("environment_feedback"): episode["feedback"].append(event["environment_feedback"])
        if event.get("source") == "memories": episode["memories"].append(event.get("content", ""))
        before = event.get("state_before") if isinstance(event.get("state_before"), dict) else None
        after = event.get("state_after") if isinstance(event.get("state_after"), dict) else None
        if before and episode["state_before"] is None: episode["state_before"] = before
        if after: episode["state_after"] = after
        episode["evidence"].extend(event.get("evidence") or [])
    episodes = []
    for episode in sorted(by_day.values(), key=lambda item: item["day"], reverse=True):
        labels = [_life_course_action_label(action) for action in episode["actions"][:4]]
        episode["title"] = f"第{episode['day']}天经历片段"
        episode["summary"] = "、".join(labels) if labels else "校园日常观察"
        episode["evidence"] = episode["evidence"][:20]
        before, after = episode.get("state_before") or {}, episode.get("state_after") or {}
        changes = {key: {"before": before.get(key), "after": after.get(key)} for key in ("location", "energy", "time_budget", "mood", "current_task") if before.get(key) != after.get(key) and (before.get(key) is not None or after.get(key) is not None)}
        feedback_keys = [f"{key}={value}" for item in episode["feedback"] if isinstance(item, dict) for key, value in item.items()]
        impact_parts = []
        if changes: impact_parts.append("状态变化：" + "、".join(f"{key} {value['before']}→{value['after']}" for key, value in changes.items()))
        if episode["memories"]: impact_parts.append(f"形成 {len(episode['memories'])} 条后续记忆")
        if feedback_keys: impact_parts.append("环境反馈：" + "、".join(feedback_keys[:4]))
        episode["narrative"] = {"intention": "、".join(_life_course_action_label(item) for item in episode["planned_actions"][:4]) or "未记录计划", "actual": "、".join(_life_course_action_label(item) for item in episode["actual_actions"][:4]) or "未记录行动", "deviation_count": len(episode["deviations"]), "memory_count": len(episode["memories"]), "feedback_count": len(episode["feedback"])}
        episode["narrative"]["reasons"] = episode["reasons"][:3]
        episode["impact"] = {"state_changes": changes, "interpretation": "；".join(impact_parts) + "。这些是时序上观察到的结果，不代表已证明因果关系。" if impact_parts else "当前片段暂无可观测的后续状态变化。"}
        episodes.append(episode)
    return episodes


def _life_course_latest_recorded_day(conn, resident_id, timeline=None):
    """Return the newest evidence that the current life-course view can show."""
    if timeline is not None:
        days = [int(item["day"]) for item in timeline if item.get("day") is not None]
        return max(days) if days else None

    latest_days = []
    branch_key = active_world_branch_key(conn)
    table_filters = {
        "world_event_stream": ("resident_id = ? AND branch_key = ?", (resident_id, branch_key)),
        "simulation_action_logs": ("resident_id = ?", (resident_id,)),
        "memories": ("resident_id = ?", (resident_id,)),
    }
    for table, (where, params) in table_filters.items():
        row = conn.execute(
            f"SELECT MAX(day) AS latest_day FROM {table} WHERE {where}",
            params,
        ).fetchone()
        if row and row["latest_day"] is not None:
            latest_days.append(int(row["latest_day"]))
    return max(latest_days) if latest_days else None


def _life_course_temporal_coverage(current_day, latest_recorded_day, from_day=None, to_day=None):
    current = max(1, int(current_day or 1))
    latest = int(latest_recorded_day) if latest_recorded_day is not None else None
    requested_to = max(1, int(to_day)) if to_day is not None else current
    requested_from = max(1, int(from_day)) if from_day is not None else None
    return {
        "current_day": current,
        "latest_recorded_day": latest,
        "has_current_day_record": latest == current,
        "days_without_records_after_latest": max(0, current - latest) if latest is not None else None,
        "requested_from_day": requested_from,
        "requested_to_day": requested_to,
        "window_includes_current_day": requested_to >= current,
    }


def _build_life_course_overview(conn, resident_id, from_day=None, to_day=None, limit=240):
    resident = get_resident(conn, resident_id)
    if not resident:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    ensure_social_system_tables(conn)
    profile = conn.execute("SELECT * FROM agent_profiles WHERE resident_id = ?", (resident_id,)).fetchone()
    goals = conn.execute(
        "SELECT * FROM long_term_goals WHERE resident_id = ? ORDER BY status, deadline_day, id",
        (resident_id,),
    ).fetchall()
    timeline = _life_course_timeline(conn, resident_id, from_day=from_day, to_day=to_day, limit=limit)
    episodes = _life_course_episodes(timeline)
    for item in timeline:
        item["timeline_kind"] = _life_course_kind(item)
        item["display_title"] = _life_course_display_title(item)
        item["turning_summary"] = _life_course_turning_summary(item)
    action_timeline = [item for item in timeline if item.get("timeline_kind") == "action"]
    memory_timeline = [item for item in timeline if item.get("timeline_kind") == "memory"]
    relationships = _life_course_relationships(conn, resident_id, timeline)
    groups = _life_course_groups(conn, resident_id, timeline)
    action_counts = {}
    locations = set()
    for item in timeline:
        action = item.get("action")
        if action and action != "memory":
            action_counts[action] = action_counts.get(action, 0) + 1
        if item.get("location"):
            locations.add(item["location"])
    current_profile = dict(profile) if profile else {}
    current_state = {
        "location": resident["location"],
        "energy": current_profile.get("energy"),
        "time_budget": current_profile.get("time_budget"),
        "mood": current_profile.get("mood"),
        "current_task": current_profile.get("current_task"),
    }
    important = [
        item
        for item in timeline
        if item.get("significance") == "turning_point"
        or (
            item.get("significance") == "important"
            and item.get("timeline_kind") == "action"
        )
    ]
    temporal_coverage = _life_course_temporal_coverage(
        get_current_day(conn),
        _life_course_latest_recorded_day(conn, resident_id, timeline=timeline),
        from_day=from_day,
        to_day=to_day,
    )
    return {
        "analysis_version": "life-course-v2",
        "temporal_coverage": temporal_coverage,
        "resident": dict(resident),
        "current_state": current_state,
        "initial_goal": resident["goal"],
        "goals": [dict(goal) for goal in goals],
        "timeline": timeline,
        "episodes": episodes,
        "action_timeline": action_timeline,
        "memory_timeline": memory_timeline,
        "turning_points": sorted(important, key=lambda item: (-int(item.get("turning_point_score") or 0), int(item.get("day") or 0)))[:12],
        "relationships": relationships,
        "groups": groups,
        "behavior_summary": {
            "event_count": len(timeline),
            "action_event_count": len(action_timeline),
            "memory_event_count": len(memory_timeline),
            "action_counts": action_counts,
            "unique_spaces": sorted(locations),
            "relationship_count": len(relationships),
            "active_group_count": sum(1 for group in groups if group.get("status") == "active"),
        },
        "research_boundaries": {
            "state_history_available": any(item.get("state_before") or item.get("state_after") for item in timeline if item.get("source") == "simulation_action_logs"),
            "relationship_history_available": any(item.get("history_available") for item in relationships),
            "group_membership_history_available": any(group.get("membership_history_available") for group in groups),
            "causal_links_available": False,
            "message": "当前版本展示事件证据和时序关联，不将时序关联表述为因果关系。",
        },
        "evidence": [
            _life_course_evidence("residents", resident_id),
            *[_life_course_evidence("world_event_stream", item["id"]) for item in timeline if item.get("source") == "world_event_stream"],
        ][:40],
    }


@app.get("/api/agents/{resident_id}/life-course/overview")
def get_agent_life_course_overview(resident_id: int, from_day: Optional[int] = None, to_day: Optional[int] = None, limit: int = 240):
    with get_connection() as conn:
        return lifecycle_overview(conn, resident_id, build_overview=_build_life_course_overview, from_day=from_day, to_day=to_day, limit=limit)


@app.get("/api/agents/{resident_id}/life-course/events")
def get_agent_life_course_events(resident_id: int, from_day: Optional[int] = None, to_day: Optional[int] = None, limit: int = 240):
    with get_connection() as conn:
        return lifecycle_events(conn, resident_id, build_overview=_build_life_course_overview, from_day=from_day, to_day=to_day, limit=limit)


@app.get("/api/agents/{resident_id}/life-course/turning-points")
def get_agent_life_course_turning_points(resident_id: int, limit: int = 12):
    with get_connection() as conn:
        return lifecycle_turning_points(conn, resident_id, build_overview=_build_life_course_overview, limit=limit)


@app.get("/api/agents/{resident_id}/life-course/relationships")
def get_agent_life_course_relationships(resident_id: int):
    with get_connection() as conn:
        return lifecycle_relationships(conn, resident_id, build_overview=_build_life_course_overview)


@app.get("/api/agents/{resident_id}/life-course/groups")
def get_agent_life_course_groups(resident_id: int):
    with get_connection() as conn:
        return lifecycle_groups(conn, resident_id, build_overview=_build_life_course_overview)


def build_agent_social_graph(conn, resident_id, limit=10):
    rows = conn.execute(
        """
        SELECT relationships.to_resident_id, residents.name, residents.role,
               relationships.score, relationship_dynamics.affinity, relationship_dynamics.trust,
               relationship_dynamics.cooperation, relationship_dynamics.competition,
               relationship_dynamics.conflict, relationship_dynamics.tension,
               relationship_dynamics.interaction_count
        FROM relationships
        JOIN residents ON residents.id = relationships.to_resident_id
        LEFT JOIN relationship_dynamics
          ON relationship_dynamics.from_resident_id = relationships.from_resident_id
         AND relationship_dynamics.to_resident_id = relationships.to_resident_id
        WHERE relationships.from_resident_id = ?
        ORDER BY relationships.score DESC
        LIMIT ?
        """,
        (resident_id, min(max(limit, 1), 20)),
    ).fetchall()
    histories = relationship_histories_by_target(
        conn,
        resident_id,
        [row["to_resident_id"] for row in rows],
    )
    owner = get_resident(conn, resident_id)
    return {
        "nodes": [{"id": resident_id, "name": owner["name"], "role": owner["role"], "owner": True}]
        + [{"id": row["to_resident_id"], "name": row["name"], "role": row["role"], "owner": False} for row in rows],
        "links": [
            {
                "from": resident_id,
                "to": row["to_resident_id"],
                "score": row["score"],
                "affinity": row["affinity"] if row["affinity"] is not None else 50,
                "trust": row["trust"] if row["trust"] is not None else 50,
                "cooperation": row["cooperation"] if row["cooperation"] is not None else 50,
                "competition": row["competition"] if row["competition"] is not None else 0,
                "conflict": row["conflict"] if row["conflict"] is not None else 0,
                "tension": row["tension"] if row["tension"] is not None else 0,
                "interaction_count": row["interaction_count"] if row["interaction_count"] is not None else 0,
                "emergent_interpretation": infer_emergent_relationship(
                    conn,
                    resident_id,
                    row["to_resident_id"],
                    dict(row),
                    row["score"],
                    history_rows=histories.get(int(row["to_resident_id"]), []),
                ),
            }
            for row in rows
        ],
    }


@app.get("/api/agents/{resident_id}/social-graph")
def get_agent_social_graph(resident_id: int, limit: int = 10):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        ensure_social_system_tables(conn)
        return build_agent_social_graph(conn, resident_id, limit=limit)


def fetch_agent_timeline(conn, resident_id, limit=30, offset=0):
    rows = conn.execute(
        """
        SELECT day, decision, execution, created_at
        FROM simulation_action_logs
        WHERE resident_id = ?
        ORDER BY id DESC LIMIT ? OFFSET ?
        """,
        (resident_id, min(max(limit, 1), 50), max(offset, 0)),
    ).fetchall()
    timeline = []
    for row in rows:
        decision = load_json_text(row["decision"], {})
        execution = load_json_text(row["execution"], {})
        if not isinstance(decision, dict):
            decision = {}
        if not isinstance(execution, dict):
            execution = {}
        result = execution.get("result", {})
        runtime_decision = execution.get("runtime_decision", {})
        if not isinstance(runtime_decision, dict):
            runtime_decision = {}
        timeline.append({
            "day": row["day"],
            "decision": {
                "action": decision.get("action") or execution.get("action", ""),
                "reason": decision.get("reason") or runtime_decision.get("reason", ""),
            },
            "execution": {
                "result": {
                    "description": result.get("description", "") if isinstance(result, dict) else str(result or ""),
                },
            },
            "created_at": row["created_at"],
        })
    return timeline


@app.get("/api/agents/{resident_id}/timeline")
def get_agent_timeline(resident_id: int, limit: int = 30, offset: int = 0):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        ensure_social_system_tables(conn)
        return fetch_agent_timeline(conn, resident_id, limit=limit, offset=offset)


def fetch_agent_simulation_logs(conn, resident_id, limit=12):
    return fetch_simulation_logs(conn, resident_id, limit=limit, load_json=load_json_text)


@app.get("/api/agents/{resident_id}/simulation-logs")
def get_agent_simulation_logs(resident_id: int, limit: int = 12):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        ensure_social_system_tables(conn)
        return fetch_agent_simulation_logs(conn, resident_id, limit=limit)


@app.get("/api/agents/{resident_id}/profile-activity")
def get_agent_profile_activity(resident_id: int, timeline_limit: int = 20):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        return build_profile_activity(
            conn,
            resident_id,
            ensure_tables=ensure_social_system_tables,
            fetch_timeline=fetch_agent_timeline,
            fetch_logs=fetch_agent_simulation_logs,
            build_social_graph=build_agent_social_graph,
            timeline_limit=timeline_limit,
        )


@app.get("/api/organizations")
def get_campus_organizations():
    with get_connection() as conn:
        return list_organizations(conn, ensure_tables=ensure_social_system_tables, load_json=load_json_text)


@app.get("/api/groups")
def get_group_goals():
    with get_connection() as conn:
        return list_group_goals(conn, ensure_tables=ensure_social_system_tables, rows_to_dicts=rows_to_dicts)


@app.post("/api/groups")
def create_group_goal(payload: GroupGoalRequest):
    with get_connection() as conn:
        ensure_social_system_tables(conn)
        ids = [payload.leader_id] + [member_id for member_id in payload.member_ids if member_id != payload.leader_id]
        residents = conn.execute(
            f"SELECT id FROM residents WHERE id IN ({','.join(['?'] * len(ids))})",
            ids,
        ).fetchall()
        if len(residents) != len(set(ids)):
            raise HTTPException(status_code=404, detail="有 Agent 不存在")
        day = get_current_day(conn)
        roles = {str(member_id): ("负责人" if member_id == payload.leader_id else "成员") for member_id in ids}
        cursor = conn.execute(
            """
            INSERT INTO group_goals
            (name, group_type, leader_id, member_ids, roles, shared_goal, deadline_day, current_plan)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                payload.group_type,
                payload.leader_id,
                json_dumps(ids, ensure_ascii=False),
                json_dumps(roles, ensure_ascii=False),
                payload.shared_goal,
                payload.deadline_day or day + 10,
                payload.current_plan,
            ),
        )
        for from_id in ids:
            for to_id in ids:
                if from_id != to_id:
                    evolve_relationship(conn, from_id, to_id, "group_goal", f"共同目标：{payload.shared_goal}", 2, 3, 0)
        add_event(conn, day, "group_goal", f"群体「{payload.name}」成立，共同目标：{payload.shared_goal}。")
        conn.commit()
        return {"message": "群体目标已创建", "group_id": cursor.lastrowid, "member_ids": ids}

@app.post("/api/tools/move")
def tool_move(payload: MoveRequest):
    with get_connection() as conn:
        module_state = get_agent_module_state(conn, payload.resident_id)
        schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
        cost = calculate_action_cost(conn, payload.resident_id, "move", {"destination": payload.destination})
        ensure_action_affordable(conn, payload.resident_id, cost, "move")
        assert_destination_available(conn, payload.destination)
        result = move_resident(conn, payload.resident_id, payload.destination)
        result["long_term_goal"] = advance_personal_goal(conn, payload.resident_id, "move", True)
        result["action_cost"] = update_agent_profile_after_action(conn, payload.resident_id, "move", "手动移动", cost=cost, schedule_context=schedule_context, tool_input={"destination": payload.destination})
        conn.commit()
        return result


@app.post("/api/tools/chat")
def tool_chat(payload: ChatRequest):
    with get_connection() as conn:
        module_state = get_agent_module_state(conn, payload.speaker_id)
        schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
        cost = calculate_action_cost(conn, payload.speaker_id, "chat")
        ensure_action_affordable(conn, payload.speaker_id, cost, "chat")
        result = chat_between(conn, payload.speaker_id, payload.listener_id, payload.message)
        result["social_update"] = {
            "speaker": evolve_relationship(conn, payload.speaker_id, payload.listener_id, "chat", "日常交流", 3, 2, -1),
            "listener": evolve_relationship(conn, payload.listener_id, payload.speaker_id, "chat", "回应交流", 2, 2, -1),
        }
        result["long_term_goal"] = advance_personal_goal(conn, payload.speaker_id, "chat", True)
        result["action_cost"] = update_agent_profile_after_action(conn, payload.speaker_id, "chat", "手动交流", cost=cost, schedule_context=schedule_context, tool_input={"target_id": payload.listener_id})
        conn.commit()
        return result


@app.post("/api/tools/buy-sell")
def tool_buy_sell(payload: BuySellRequest):
    with get_connection() as conn:
        module_state = get_agent_module_state(conn, payload.buyer_id)
        schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
        cost = calculate_action_cost(conn, payload.buyer_id, "buy_sell")
        ensure_action_affordable(conn, payload.buyer_id, cost, "buy_sell")
        result = buy_sell(
            conn,
            payload.buyer_id,
            payload.seller_id,
            payload.item_name,
            payload.quantity,
            payload.unit_price,
        )
        result["long_term_goal"] = advance_personal_goal(conn, payload.buyer_id, "buy_sell", True)
        result["action_cost"] = update_agent_profile_after_action(conn, payload.buyer_id, "buy_sell", "手动交易", cost=cost, schedule_context=schedule_context, tool_input={"seller_id": payload.seller_id})
        conn.commit()
        return result


@app.get("/api/policies")
def get_policies():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT policies.*, residents.name AS proposer_name
            FROM policies
            LEFT JOIN residents ON residents.id = policies.proposer_id
            ORDER BY policies.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


@app.post("/api/tools/submit-policy")
def submit_policy(payload: PolicyRequest):
    with get_connection() as conn:
        proposer = get_resident(conn, payload.proposer_id)
        if not proposer:
            raise HTTPException(status_code=404, detail="提案人不存在")
        module_state = get_agent_module_state(conn, payload.proposer_id)
        schedule_context = module_state["modules"]["Schedule"]["current_schedule"]
        cost = calculate_action_cost(conn, payload.proposer_id, "submit_policy")
        ensure_action_affordable(conn, payload.proposer_id, cost, "submit_policy")
        day = get_current_day(conn)
        conn.execute(
            """
            INSERT INTO policies (title, description, proposer_id)
            VALUES (?, ?, ?)
            """,
            (payload.title, payload.description, payload.proposer_id),
        )
        description = f"{proposer['name']} 提交校园政策《{payload.title}》：{payload.description}"
        add_event(conn, day, "policy_submit", description)
        add_memory(conn, payload.proposer_id, day, description, importance=3)
        action_cost = update_agent_profile_after_action(conn, payload.proposer_id, "submit_policy", "手动提交政策", cost=cost, schedule_context=schedule_context, tool_input={"title": payload.title})
        conn.commit()
        return {"message": "政策提交成功", "description": description, "action_cost": action_cost}


@app.post("/api/tools/vote-policy")
def vote_policy(payload: VotePolicyRequest):
    if payload.vote not in {"yes", "no"}:
        raise HTTPException(status_code=400, detail="vote 只能是 yes 或 no")

    with get_connection() as conn:
        resident = get_resident(conn, payload.resident_id)
        policy = conn.execute("SELECT * FROM policies WHERE id = ?", (payload.policy_id,)).fetchone()
        if not resident or not policy:
            raise HTTPException(status_code=404, detail="投票人或政策不存在")
        cost = calculate_action_cost(conn, payload.resident_id, "observe")
        ensure_action_affordable(conn, payload.resident_id, cost, "observe")
        column = "yes_votes" if payload.vote == "yes" else "no_votes"
        conn.execute(f"UPDATE policies SET {column} = {column} + 1 WHERE id = ?", (payload.policy_id,))
        day = get_current_day(conn)
        description = f"{resident['name']} 对政策《{policy['title']}》投票：{payload.vote}"
        add_event(conn, day, "policy_vote", description)
        add_memory(conn, payload.resident_id, day, description, importance=1)
        action_cost = update_agent_profile_after_action(conn, payload.resident_id, "observe", "参与政策投票", cost=cost)
        conn.commit()
        return {"message": "投票成功", "description": description, "action_cost": action_cost}


@app.post("/api/tools/close-policy/{policy_id}")
def close_policy(policy_id: int):
    with get_connection() as conn:
        policy = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
        if not policy:
            raise HTTPException(status_code=404, detail="政策不存在")
        status = "passed" if int(policy["yes_votes"]) >= int(policy["no_votes"]) else "rejected"
        conn.execute("UPDATE policies SET status = ? WHERE id = ?", (status, policy_id))
        day = get_current_day(conn)
        description = f"政策《{policy['title']}》投票结束，赞成 {policy['yes_votes']}，反对 {policy['no_votes']}，结果：{status}。"
        add_event(conn, day, "policy_close", description)
        conn.commit()
        return {"message": "政策已结算", "status": status, "description": description}


@app.post("/api/tools/daily-reflect")
def daily_reflect():
    with get_connection() as conn:
        day = get_current_day(conn)
        agents = conn.execute("SELECT * FROM residents ORDER BY id").fetchall()
        events = conn.execute(
            "SELECT description FROM city_events WHERE day = ? ORDER BY id DESC LIMIT 20",
            (day,),
        ).fetchall()
        event_text = "；".join([row["description"] for row in events]) or "今天校园较为平静。"

        results = []
        for agent in agents:
            prompt = f"请以{agent['name']}的第一人称，用一句话总结今天的校园生活。今日事件：{event_text}"
            try:
                reflection = ask_llm(prompt)
            except Exception:
                reflection = f"{agent['name']} 记录了第 {day} 天的校园生活。"
            add_memory(conn, agent["id"], day, reflection, importance=2)
            results.append({"agent_id": agent["id"], "name": agent["name"], "reflection": reflection})

        add_event(conn, day, "daily_reflect", f"第 {day} 天校园日报总结完成，共生成 {len(results)} 条记忆。")
        conn.commit()
        return {"day": day, "results": results}


@app.get("/api/newspaper/today")
def newspaper_today():
    with get_connection() as conn:
        day = get_current_day(conn)
        env = get_campus_environment(conn, day)
        events = conn.execute(
            "SELECT event_type, description, created_at FROM city_events WHERE day = ? ORDER BY id DESC LIMIT 30",
            (day,),
        ).fetchall()
        return {
            "title": f"校园封闭世界日报 第 {day} 天",
            "environment": env,
            "events": rows_to_dicts(events),
            "agent_modules": get_all_agent_module_states(conn),
        }


def summarize_action_for_news(execution):
    result = execution.get("result") if isinstance(execution, dict) else None
    if isinstance(result, dict):
        return str(result.get("description") or result.get("message") or execution.get("action") or "完成了一次校园行动")
    return str(execution.get("action") or "完成了一次校园行动")


def classify_campus_news_candidate(event_type, action="", content="", payload=None):
    payload = payload if isinstance(payload, dict) else {}
    factual_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"runtime_decision", "preconditions", "causal_settlement"}
    }
    text = f"{event_type} {action} {content} {json_dumps(factual_payload, ensure_ascii=False)}"
    if action in {"conflict", "late", "request_leave"} or any(word in text for word in ("冲突", "紧张", "请假", "迟到")):
        return "反常行为", 90
    if (
        event_type in {"agent_tick_failed", "world_tick_failed", "real_weather_auto_sync_failed"}
        or payload.get("action_success") is False
        or payload.get("failure_code")
        or any(word in str(content or "") for word in ("异常", "故障", "中断", "失败"))
    ):
        return "突发异常", 100
    if (
        event_type in {"social_interaction", "relationship_change"}
        or payload.get("social_effect")
        or any(word in text for word in ("关系", "信任", "合作", "好感", "竞争"))
    ):
        return "关系风向", 86
    if action in {"collaborate", "create_group", "join_group", "club_activity"} or any(word in text for word in ("小组", "社团", "协作", "动员", "扩散")):
        return "群体现象", 82
    if event_type in {"crowd_transmission", "organization_mobilization", "group_diffusion"}:
        return "群体现象", 80
    if action in {"observe", "reflect"} or any(word in text for word in ("发现", "观察", "反思", "想法")):
        return "内心发现", 70
    if any(word in text for word in ("天气", "人流", "拥挤", "食堂", "图书馆", "空间", "资源", "服务")):
        return "校园环境", 60
    return "校园环境", 50


def campus_news_headline(category, location, name):
    location = location or "校园"
    return {
        "突发异常": f"{location}出现需要关注的异常信号",
        "反常行为": f"{name}的反常行动引发关注",
        "关系风向": "校园关系网络出现新动向",
        "群体现象": f"{location}涌现出新的集体动态",
        "内心发现": f"{name}记录到一条内心发现",
        "校园环境": f"{location}出现新的环境变化",
    }.get(category, f"{location}发布最新校园动态")


def fallback_campus_news_content(candidate):
    category = candidate["category"]
    name = candidate["name"]
    role = candidate["role"] or "校园居民"
    location = candidate["location"] or "校园"
    content = str(candidate["content"] or "校园出现一条新的运行记录。")
    if category == "关系风向":
        return f"{role}{name}在{location}经历了一次不同于往常的互动。{content[:110]} 这次接触是否会改变双方后续的信任与合作，成为编辑部继续追踪的线索。"
    if category == "突发异常":
        return f"{location}出现异常动向，{role}{name}正处在事件中心。{content[:110]} 当前影响仍在发展，相关行动与环境变化需要继续观察。"
    if category == "群体现象":
        return f"{role}{name}在{location}参与的行动开始吸引更多人响应。{content[:110]} 原本的个人选择正在形成集体趋势，可能改变接下来的空间热度和校园注意力。"
    if category == "反常行为":
        return f"{role}{name}今天在{location}偏离了惯常行动轨迹。{content[:110]} 这究竟是临时选择还是持续变化，仍需结合后续行为判断。"
    if category == "内心发现":
        return f"{role}{name}在{location}停下来重新审视自己的选择。{content[:110]} 这份想法尚未转化为行动，却可能影响其下一步决定。"
    if category == "校园环境":
        return f"{location}的运行状态发生变化。{content[:120]} 身处其中的{role}{name}首先受到影响，其他居民的行动和空间选择也可能随之调整。"
    return f"{role}{name}在{location}完成了一次不同寻常的行动。{content[:120]} 这件事为观察后续变化留下了新的线索。"


def collect_campus_news_candidates(conn, day, source_slot, limit=60):
    branch_key = active_world_branch_key(conn)
    existing_residents = {
        int(row["resident_id"])
        for row in conn.execute("SELECT resident_id FROM agent_news_posts WHERE day = ?", (day,)).fetchall()
    }
    candidates = []
    rows = conn.execute(
        """
        SELECT e.id, e.event_type, e.resident_id, e.location, e.title, e.content, e.payload,
               r.name, r.role
        FROM world_event_stream e
        LEFT JOIN residents r ON r.id = e.resident_id
        WHERE e.day = ?
          AND e.branch_key = ?
          AND e.resident_id IS NOT NULL
          AND e.event_type NOT IN (
              'world_tick_started', 'world_tick_complete',
              'campus_news_published', 'campus_news_skipped',
              'observer_session', 'observer_model_detail'
          )
        ORDER BY e.id DESC
        LIMIT ?
        """,
        (day, branch_key, limit),
    ).fetchall()
    for row in rows:
        resident_id = int(row["resident_id"])
        if resident_id in existing_residents:
            continue
        payload = load_json_text(row["payload"], {})
        action = payload.get("action") or payload.get("runtime_decision", {}).get("action") or ""
        category, score = classify_campus_news_candidate(row["event_type"], action, row["content"], payload)
        if row["event_type"] == "agent_tick" and source_slot and row["content"] and row["location"]:
            score += 5
        candidates.append({
            "resident_id": resident_id,
            "name": row["name"] or f"Agent {resident_id}",
            "role": row["role"] or "校园居民",
            "location": row["location"] or "校园",
            "event_type": row["event_type"],
            "title": row["title"],
            "content": row["content"],
            "payload": payload,
            "action": action,
            "category": category,
            "score": score,
            "source_event_id": row["id"],
        })

    relationship_rows = conn.execute(
        """
        SELECT c.id, c.from_resident_id, c.to_resident_id, c.interaction, c.reason,
               c.affinity_before, c.affinity_after, c.trust_before, c.trust_after,
               c.cooperation_before, c.cooperation_after, c.conflict_before, c.conflict_after,
               r.name, r.role, r.location, target.name AS target_name
        FROM relationship_change_events c
        JOIN residents r ON r.id = c.from_resident_id
        JOIN residents target ON target.id = c.to_resident_id
        WHERE c.day = ?
        ORDER BY c.id DESC
        LIMIT 40
        """,
        (day,),
    ).fetchall()
    for row in relationship_rows:
        resident_id = int(row["from_resident_id"])
        if resident_id in existing_residents:
            continue
        delta = abs(int(row["trust_after"] or 0) - int(row["trust_before"] or 0))
        delta += abs(int(row["cooperation_after"] or 0) - int(row["cooperation_before"] or 0))
        delta += abs(int(row["conflict_after"] or 0) - int(row["conflict_before"] or 0))
        content = f"{row['name']}与{row['target_name']}的关系发生变化：{row['reason'] or row['interaction']}。"
        candidates.append({
            "resident_id": resident_id,
            "name": row["name"],
            "role": row["role"],
            "location": row["location"] or "校园",
            "event_type": "relationship_change",
            "title": "关系变化被记录",
            "content": content,
            "payload": {"relationship_change_event_id": row["id"], "target_name": row["target_name"]},
            "action": row["interaction"],
            "category": "关系风向",
            "score": 86 + min(delta, 20),
            "source_event_id": None,
        })
    candidates.sort(key=lambda item: (-item["score"], item["resident_id"]))
    return candidates


def publish_agent_news(conn, day, results):
    """Create a small number of public-facing posts from autonomous actions."""
    ensure_agent_news_system(conn)
    published = []
    for item in random.sample(results, min(4, len(results))):
        resident_id = item.get("resident_id")
        resident = conn.execute(
            "SELECT id, name, role, location, goal FROM residents WHERE id = ?",
            (resident_id,),
        ).fetchone()
        if not resident:
            continue

        action_summary = summarize_action_for_news(item.get("execution", {}))
        prompt = f"""
你是《校园世界时报》的校园记者。根据以下事实写一则 90 到 150 字、具有现场感的中文校园快讯：
消息来源：{resident['name']}（{resident['role']}）
地点：{resident['location']}
事实：{action_summary}

使用第三人称和客观新闻口吻，先写具体行动，再写变化或影响。句式自然，避免“发布最新动态”“提供参考”“值得记录”等公文套话。
不要写个人感想、日记、号召口号、标题、JSON、Markdown 或解释，只输出新闻正文。
"""
        try:
            content = ask_llm(prompt).strip()
        except Exception:
            content = f"{resident['role']}{resident['name']}当天来到{resident['location']}，完成了与自身目标相关的一项行动。现场留下的变化已经进入后续观察，编辑部将继续追踪它是否影响其他居民的选择。"

        if not content or content.startswith(("{", "[")):
            content = f"{resident['role']}{resident['name']}当天来到{resident['location']}，完成了与自身目标相关的一项行动。现场留下的变化已经进入后续观察，编辑部将继续追踪它是否影响其他居民的选择。"
        if any(word in content for word in ("维修", "检修", "施工")):
            headline = f"{resident['location']}启动设施维护"
        elif any(word in content for word in ("食堂", "套餐", "供餐", "补货")):
            headline = "校园餐饮服务推出新安排"
        elif any(word in content for word in ("实验", "项目", "科研", "代码")):
            headline = "校园教学科研项目取得新进展"
        elif any(word in content for word in ("考试", "复习", "压力")):
            headline = "考试周校园保障措施持续推进"
        else:
            headline = f"{resident['location']}发布最新校园动态"
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO agent_news_posts
            (day, resident_id, source_slot, news_value, headline, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (day, resident["id"], "日终补充", 55, headline, content[:500]),
        )
        if cursor.rowcount:
            published.append({"resident_id": resident["id"], "headline": headline})
    return published


def write_agent_daily_diaries(conn, day, results=None, replace_existing=False):
    """Let every Agent write a first-person diary from its own lived context."""
    agents = conn.execute(
        "SELECT id, name, role, personality, location, goal FROM residents ORDER BY id"
    ).fetchall()
    by_agent = {item.get("resident_id"): item for item in (results or [])}
    created = []
    action_text = {
        "chat": "和校园里的其他人交流",
        "move": "前往新的校园空间",
        "buy_sell": "完成了一次交易",
        "observe": "观察校园环境",
        "submit_policy": "参与校园事务讨论",
    }
    for agent in agents:
        exists = conn.execute(
            "SELECT 1 FROM memories WHERE resident_id = ? AND day = ? AND content LIKE ?",
            (agent["id"], day, f"日记·第{day}天：%"),
        ).fetchone()
        if exists and not replace_existing:
            continue
        if exists and replace_existing:
            conn.execute(
                "DELETE FROM memories WHERE resident_id = ? AND day = ? AND content LIKE ?",
                (agent["id"], day, f"日记·第{day}天：%"),
            )
        item = by_agent.get(agent["id"], {})
        execution = item.get("execution", {}) if isinstance(item, dict) else {}
        decision = item.get("decision", {}).get("decision", {}) if isinstance(item, dict) else {}
        action = execution.get("action") or decision.get("action")
        activity = action_text.get(action, "完成自己的校园安排")
        reason = str(decision.get("reason") or "")[:90].strip()
        recent_memories = conn.execute(
            """
            SELECT content FROM memories
            WHERE resident_id = ? AND day = ? AND content NOT LIKE ?
            ORDER BY id DESC LIMIT 4
            """,
            (agent["id"], day, f"日记·第{day}天：%"),
        ).fetchall()
        memory_text = "；".join(row["content"][:160] for row in recent_memories)
        prompt = f"""
你是校园封闭世界中的 Agent“{agent['name']}”。
你的身份：{agent['role']}；性格：{agent['personality']}；长期目标：{agent['goal']}；当前位置：{agent['location']}。
今天你实际完成的行动：{activity}。你的行动理由：{reason or '根据自己的状态和环境自主判断'}。
今天留下的个人经历：{memory_text or '暂无额外记录'}。

请以第一人称写一段 70 到 130 字的中文个人日记，内容必须符合你的性格和目标，写真实感受、观察或下一步想法。
只输出日记正文，不要标题、JSON、Markdown、技术字段或解释。
"""
        fallback = f"今天我在{agent['location']}{activity}。这次经历让我更清楚地看到校园的变化，也提醒我继续朝“{agent['goal']}”努力。"
        try:
            diary_text = ask_llm(prompt).strip()
        except Exception:
            diary_text = fallback
        if not diary_text or diary_text.startswith(("{", "[")):
            diary_text = fallback
        diary = f"日记·第{day}天：{diary_text[:500]}"
        add_memory(
            conn,
            agent["id"],
            day,
            diary,
            importance=5,
            memory_type="episodic",
            tags=["日记", agent["location"], activity],
            source="diary",
        )
        created.append(agent["id"])
    return created


@app.post("/api/agents/daily-diaries/backfill")
def backfill_agent_daily_diaries(day: Optional[int] = None, rewrite: bool = False):
    with get_connection() as conn:
        target_day = day or get_current_day(conn)
        created = write_agent_daily_diaries(conn, target_day, replace_existing=rewrite)
        conn.commit()
        return {"day": target_day, "created": len(created), "agent_ids": created}


@app.get("/api/newspaper/agent-posts")
def agent_newspaper_posts(day: Optional[int] = None):
    """Return campus newspaper posts for the requested day, defaulting to today."""
    with get_connection() as conn:
        ensure_agent_news_system(conn)
        current_day = get_current_day(conn)
        target_day = max(1, int(day)) if day is not None else current_day
        posts = conn.execute(
            """
            SELECT p.day, p.resident_id, r.name, r.role, p.source_slot,
                   p.source_event_id, p.news_value, p.headline, p.content, p.created_at
            FROM agent_news_posts p
            JOIN residents r ON r.id = p.resident_id
            WHERE p.day = ?
            ORDER BY p.id DESC
            LIMIT 12
            """,
            (target_day,),
        ).fetchall()
        days = [
            int(row["day"])
            for row in conn.execute(
                "SELECT DISTINCT day FROM agent_news_posts ORDER BY day DESC LIMIT 60"
            ).fetchall()
        ]
        previous_day = next((item for item in days if item < target_day), None)
        next_day = next((item for item in sorted(days) if item > target_day), None)
        return {
            "day": target_day,
            "current_day": current_day,
            "edition": {
                "kind": "rolling" if target_day == current_day else "archive",
                "label": "今日滚动版" if target_day == current_day else f"第 {target_day} 天归档日报",
                "brief_count": len(posts),
                "issue_key": f"day-{target_day}",
            },
            "available_days": days,
            "previous_day": previous_day,
            "next_day": next_day,
            "posts": rows_to_dicts(posts),
        }


@app.get("/api/newspaper/ai-today")
def ai_newspaper_today():
    data = newspaper_today()
    prompt = f"请把下面校园封闭世界数据写成一份简短校园日报，分为标题、环境、主要事件、趋势判断：{json_dumps(data, ensure_ascii=False)}"
    return {"day": data["title"], "newspaper": ask_llm(prompt), "source": data}


EXTERNAL_RSS_SOURCES = [
    (
        "Google News RSS",
        "https://news.google.com/rss/search?q=(AI%20OR%20%E5%A4%A7%E5%AD%A6%20OR%20%E6%95%99%E8%82%B2%20OR%20%E5%B0%B1%E4%B8%9A)&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "Bing News RSS",
        "https://www.bing.com/news/search?q=(AI%20OR%20university%20OR%20education%20OR%20employment)&format=rss&setlang=zh-CN&cc=CN",
    ),
]


def classify_external_information(text):
    normalized = str(text or "").lower()
    if any(word in normalized for word in ("ai", "人工智能", "科技", "技术")):
        return "technology"
    if any(word in normalized for word in ("就业", "招聘", "创业", "商业", "经济")):
        return "career"
    if any(word in normalized for word in ("教育", "大学", "考试", "课程", "学生")):
        return "education"
    return "general"


def fetch_external_information(limit=5):
    """Read fixed public RSS sources; Agents never receive arbitrary URLs."""
    errors = []
    for source_name, source_url in EXTERNAL_RSS_SOURCES:
        try:
            records = FixedRSSAdapter().fetch(
                {
                    "feed_url": source_url,
                    "limit": limit,
                    "timeout_seconds": 5,
                }
            )
            items = [
                {
                    "title": record["payload"]["title"],
                    "summary": record["payload"]["summary"],
                    "source_name": source_name,
                    "source_url": record["payload"]["link"],
                    "published_at": record["payload"]["published_at_text"],
                    "category": record["payload"]["category"],
                }
                for record in records
            ]
            if items:
                return items
            errors.append(f"{source_name}: no RSS items")
        except Exception as exc:
            logger.warning("External information source failed: %s", source_name, exc_info=True)
            errors.append(f"{source_name}: {type(exc).__name__}")
    raise RuntimeError("; ".join(errors))


def deliver_external_information(
    conn,
    information,
    resident_id,
    channel,
    relevance=65,
    credibility=80,
    distortion_note="",
    source_resident_id=None,
):
    ensure_external_information_system(conn)
    inserted = conn.execute(
        """
        INSERT OR IGNORE INTO agent_information
        (information_id, resident_id, channel, relevance, credibility, distortion_note, source_resident_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (information["id"], resident_id, channel, relevance, credibility, distortion_note, source_resident_id),
    ).rowcount
    if not inserted:
        return False

    profile = ensure_profile_meta(conn, resident_id)
    perception = load_json_text(profile["perception"], {}) if profile else {}
    feed = perception.get("external_information", [])
    feed.insert(0, {
        "title": information["title"],
        "category": information["category"],
        "channel": channel,
        "credibility": credibility,
        "distortion_note": distortion_note,
    })
    perception["external_information"] = feed[:4]
    conn.execute(
        "UPDATE agent_profiles SET perception = ? WHERE resident_id = ?",
        (json_dumps(perception, ensure_ascii=False), resident_id),
    )
    day = get_current_day(conn)
    add_memory(
        conn,
        resident_id,
        day,
        f"我从{channel}得知外部消息：{information['title']}。可信度 {credibility}。{distortion_note}",
        importance=4,
        memory_type="working",
        tags=["外部资讯", information["category"], channel, f"可信度{credibility}"],
        source="external_information",
    )
    return True


def seed_external_information_recipients(conn, information):
    agents = conn.execute(
        """
        SELECT residents.id, residents.role, residents.goal, residents.personality,
               agent_profiles.skills, agent_profiles.organization
        FROM residents LEFT JOIN agent_profiles ON agent_profiles.resident_id = residents.id
        ORDER BY residents.id
        """
    ).fetchall()
    category_terms = {
        "technology": ("AI", "人工智能", "技术", "创业"),
        "career": ("创业", "商业", "投资", "就业"),
        "education": ("学生", "教师", "课程", "学习"),
    }
    terms = category_terms.get(information["category"], ())
    ranked = sorted(
        agents,
        key=lambda agent: sum(term in f"{agent['role']} {agent['goal']} {agent['personality']} {agent['skills'] or ''} {agent['organization'] or ''}" for term in terms),
        reverse=True,
    )
    recipients = ranked[:4]
    return [
        agent["id"]
        for agent in recipients
        if deliver_external_information(conn, information, agent["id"], "外部资讯订阅", relevance=80, credibility=88)
    ]


def spread_external_information(conn, limit=12):
    """Let information travel along existing social relationships over later simulation days."""
    ensure_external_information_system(conn)
    rows = conn.execute(
        """
        SELECT ai.information_id, ai.resident_id AS sender_id, ai.credibility,
               ei.title, ei.category
        FROM agent_information ai
        JOIN external_information ei ON ei.id = ai.information_id
        ORDER BY ai.received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    delivered = 0
    for row in rows:
        contacts = conn.execute(
            """
            SELECT relationships.to_resident_id, relationship_dynamics.trust, relationship_dynamics.affinity
            FROM relationships
            LEFT JOIN relationship_dynamics
              ON relationship_dynamics.from_resident_id = relationships.from_resident_id
             AND relationship_dynamics.to_resident_id = relationships.to_resident_id
            WHERE relationships.from_resident_id = ? AND relationships.score >= 55
            ORDER BY COALESCE(relationship_dynamics.trust, 50) + COALESCE(relationship_dynamics.affinity, 50) DESC LIMIT 2
            """,
            (row["sender_id"],),
        ).fetchall()
        info = {"id": row["information_id"], "title": row["title"], "category": row["category"]}
        for contact in contacts:
            distortion = random.choice(["", "转述时省略了部分背景。", "转述时更强调了与自己相关的部分。"])
            credibility = max(35, int(row["credibility"] or 80) - (8 if distortion else 3))
            relevance = min(85, 52 + int(contact["trust"] or 50) // 3)
            delivered += int(
                deliver_external_information(
                    conn,
                    info,
                    contact["to_resident_id"],
                    "熟人转述",
                    relevance=relevance,
                    credibility=credibility,
                    distortion_note=distortion,
                    source_resident_id=row["sender_id"],
                )
            )
    return delivered


def sync_external_information_into_world(conn, event_type="external_information_manual_sync", tick_id=None, day=None, slot=None):
    ensure_external_information_system(conn)
    ensure_world_runtime_tables(conn)
    fetched = fetch_external_information()
    created = []
    recipient_ids = set()
    for item in fetched:
        conn.execute(
            """
            INSERT OR IGNORE INTO external_information
            (title, summary, source_name, source_url, category, published_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item["title"], item["summary"], item["source_name"], item["source_url"], item["category"], item["published_at"]),
        )
        row = conn.execute("SELECT * FROM external_information WHERE title = ?", (item["title"],)).fetchone()
        if row:
            information = dict(row)
            newly_informed = seed_external_information_recipients(conn, information)
            if newly_informed:
                created.append(information)
                recipient_ids.update(newly_informed)
    if created:
        content = f"校园接入 {len(created)} 条外部资讯，已有 {len(recipient_ids)} 位 Agent 先行获知。"
        add_event(conn, day or get_current_day(conn), "external_information", content)
    else:
        content = f"外部资讯已检查，抓取 {len(fetched)} 条，暂无新的 Agent 接收记录。"
    event = append_world_event(
        conn,
        event_type,
        "外部世界自动同步" if event_type == "external_information_auto_sync" else "外部世界同步",
        content,
        tick_id=tick_id,
        payload={"fetched": len(fetched), "new_information_count": len(created), "initial_recipients": len(recipient_ids)},
        day=day,
        slot=slot,
    )
    return {"fetched": len(fetched), "new_information": created, "initial_recipients": len(recipient_ids), "event": event}


def maybe_auto_sync_external_information(conn, world_time, tick_id=None, day=None, slot=None):
    ensure_world_runtime_tables(conn)
    latest = conn.execute(
        """
        SELECT created_at FROM world_event_stream
        WHERE event_type IN ('external_information_auto_sync', 'external_information_auto_sync_failed')
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    latest_at = parse_world_datetime(latest["created_at"]) if latest else None
    if latest_at and (world_time - latest_at).total_seconds() < WORLD_EXTERNAL_SYNC_INTERVAL_SECONDS:
        return {"skipped": True, "reason": "interval_not_elapsed", "last_synced_at": latest_at.isoformat()}
    try:
        result = sync_external_information_into_world(
            conn,
            event_type="external_information_auto_sync",
            tick_id=tick_id,
            day=day,
            slot=slot,
        )
        result["skipped"] = False
        return result
    except Exception as exc:
        logger.warning("Auto external information sync failed", exc_info=True)
        event = append_world_event(
            conn,
            "external_information_auto_sync_failed",
            "外部世界自动同步失败",
            f"外部资讯源暂时不可用：{type(exc).__name__}",
            tick_id=tick_id,
            payload={"error": str(exc)},
            day=day,
            slot=slot,
        )
        return {"skipped": False, "failed": True, "error": str(exc), "event": event}


def compact_external_sync_result(result):
    compact = {
        "skipped": bool(result.get("skipped")),
        "failed": bool(result.get("failed")),
        "reason": result.get("reason", ""),
        "fetched": int(result.get("fetched") or 0),
        "new_information_count": len(result.get("new_information") or []),
        "initial_recipients": int(result.get("initial_recipients") or 0),
        "last_synced_at": result.get("last_synced_at", ""),
    }
    if result.get("event"):
        compact["event_id"] = result["event"].get("id")
        compact["event_type"] = result["event"].get("event_type")
    if result.get("error"):
        compact["error"] = str(result["error"])[:240]
    return compact


@app.post("/api/external-information/sync")
def sync_external_information():
    with get_connection() as conn:
        try:
            result = sync_external_information_into_world(conn, event_type="external_information_manual_sync")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"外部资讯同步失败：{exc}")
        conn.commit()
        return result


@app.get("/api/external-information")
def get_external_information():
    with get_connection() as conn:
        ensure_external_information_system(conn)
        rows = conn.execute(
            "SELECT * FROM external_information ORDER BY id DESC LIMIT 20"
        ).fetchall()
        return {"items": rows_to_dicts(rows)}


@app.post("/api/agent/decide/{resident_id}")
def decide_agent(resident_id: int):
    with get_connection() as conn:
        return decide_agent_action(conn, resident_id)


@app.post("/api/agent/act/{resident_id}")
def act_agent(resident_id: int):
    with get_connection() as conn:
        decision_data = decide_agent_action(conn, resident_id)
        result = execute_decision(conn, resident_id, decision_data["decision"])
        return {"decision": decision_data, "execution": result}


@app.post("/api/agent/act-all")
def act_all_agents():
    with get_connection() as conn:
        agents = conn.execute("SELECT id FROM residents ORDER BY id").fetchall()
        results = []
        for agent in agents:
            decision_data = decide_agent_action(conn, agent["id"])
            execution = execute_decision(conn, agent["id"], decision_data["decision"])
            feedback = apply_environment_feedback(conn, agent["id"], execution["action"], execution["result"])
            results.append({"decision": decision_data, "execution": execution, "environment_feedback": feedback})
        return {"message": f"{len(results)} 个校园 Agent 已轮流自主行动", "results": results}


@app.post("/api/simulate/lifecycle-step/{resident_id}")
def simulate_lifecycle_step(resident_id: int):
    with get_connection() as conn:
        return run_lifecycle_step(conn, resident_id)


@app.post("/api/simulate/lifecycle-round")
def simulate_lifecycle_round():
    with get_connection() as conn:
        agents = conn.execute("SELECT id FROM residents ORDER BY id").fetchall()
        results = []
        for agent in agents:
            results.append(run_lifecycle_step(conn, agent["id"]))
        day = get_current_day(conn)
        add_event(conn, day, "lifecycle_round", f"第 {day} 天完成一轮 Agent-环境交互循环，共 {len(results)} 个 Agent。")
        conn.commit()
        return {
            "message": f"{len(results)} 个 Agent 完成感知-决策-行动-反馈循环",
            "loop": "perceive -> decide -> act -> feedback -> memory",
            "results": results,
        }


def run_simulate_ai_day(progress=None):
    def report(event, message, **data):
        logger.info(
            "[simulate-day] %s | %s | %s",
            event,
            message,
            json_dumps(data, ensure_ascii=False, default=str),
        )
        if progress:
            progress({"event": event, "message": message, **data})

    with get_connection() as conn:
        old_day = get_current_day(conn)
        new_day = old_day + 1
        report("day_advance", f"模拟日从第 {old_day} 天推进到第 {new_day} 天。", old_day=old_day, day=new_day)
        conn.execute("UPDATE simulation_state SET value = ? WHERE key = 'current_day'", (str(new_day),))
        conn.commit()
        report("environment_start", "正在生成校园环境并同步真实时间/天气。", day=new_day)
        env = auto_update_environment(conn, new_day)
        report("environment_done", f"第 {new_day} 天环境已生成：{env.get('weather')}，校园情绪 {env.get('campus_mood')}。", day=new_day)
        report("agent_recovery", "正在恢复全部 Agent 精力并重置每日时间预算。", day=new_day)
        recover_agents_for_new_day(conn, new_day)
        report("information_spread", "正在沿关系网络传播外部资讯。", day=new_day)
        spread_count = spread_external_information(conn)
        conn.commit()
        agents = conn.execute("SELECT id, name, role FROM residents ORDER BY id").fetchall()
        total_agents = len(agents)
        report("agents_start", f"开始遍历 {total_agents} 个 Agent。", day=new_day, total_agents=total_agents)
        results = []
        fallback_agents = []
        for index, agent in enumerate(agents, start=1):
            state_before = get_agent_module_state(conn, agent["id"])
            resident_label = f"{agent['name']}（{agent['role']}）"
            try:
                report("agent_perceiving", f"{resident_label} 正在感知校园环境。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents)
                perception = perceive_environment(conn, agent["id"])
                report("agent_deciding", f"{resident_label} 正在检索记忆并生成自主决策。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents)
                decision_data = decide_agent_action(conn, agent["id"])
                action = decision_data.get("decision", {}).get("action", "observe")
                report("agent_acting", f"{resident_label} 决定执行 {action}。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents, action=action)
                execution = execute_decision(conn, agent["id"], decision_data["decision"])
                report("agent_feedback", f"{resident_label} 的行动已完成，正在反馈到校园环境。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents, action=execution["action"], success=execution.get("success", False))
                feedback = apply_environment_feedback(conn, agent["id"], execution["action"], execution["result"])
            except Exception as exc:
                logger.exception("Agent %s failed during day %s", agent["id"], new_day)
                fallback_agents.append(agent["id"])
                report("agent_fallback", f"{resident_label} 行动管线异常，降级为观察。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents, error=type(exc).__name__)
                # A failed PostgreSQL statement invalidates the transaction.
                # Start a clean transaction and record a minimal fallback instead
                # of running the full action pipeline a second time.
                conn.rollback()
                resident = get_resident(conn, agent["id"])
                try:
                    perception = perceive_environment(conn, agent["id"])
                except Exception:
                    conn.rollback()
                    perception = {}
                decision_data = {
                    "decision": {
                        "action": "observe",
                        "reason": f"当日行动异常，改为观察并保留状态：{type(exc).__name__}",
                        "tool_input": {"focus": "校园环境"},
                    }
                }
                name = resident["name"] if resident else f"Agent {agent['id']}"
                description = f"{name} 当日行动出现异常，改为观察校园环境。"
                execution = {
                    "resident_id": agent["id"],
                    "action": "observe",
                    "reason": decision_data["decision"]["reason"],
                    "result": {"message": "降级观察完成", "description": description, "error": str(exc)},
                    "success": False,
                }
                try:
                    add_event(conn, new_day, "agent_fallback_observe", description)
                    add_memory(conn, agent["id"], new_day, description, importance=1, source="fallback")
                    conn.commit()
                except Exception:
                    logger.exception("Fallback record failed for Agent %s", agent["id"])
                    conn.rollback()
                feedback = {}

            try:
                state_after = get_agent_module_state(conn, agent["id"])
                record_simulation_log(conn, agent["id"], perception, decision_data, execution, feedback, state_before, state_after)
                conn.commit()
                report("agent_logged", f"{resident_label} 的决策日志已写入。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents, action=execution["action"], success=execution.get("success", False))
            except Exception:
                logger.exception("Simulation log failed for Agent %s", agent["id"])
                conn.rollback()
                report("agent_log_failed", f"{resident_label} 的决策日志写入失败，已继续处理后续 Agent。", day=new_day, resident_id=agent["id"], agent_name=agent["name"], agent_index=index, total_agents=total_agents)
            results.append(
                {
                    "resident_id": agent["id"],
                    "perception": perception,
                    "decision": decision_data,
                    "execution": execution,
                    "environment_feedback": feedback,
                }
            )
        try:
            report("group_goals", "正在推进群体目标并调整关系紧张度。", day=new_day)
            group_updates = advance_group_goals(conn, new_day, [item["execution"] for item in results])
            conn.commit()
        except Exception:
            logger.exception("Group goal update failed for day %s", new_day)
            conn.rollback()
            group_updates = []
        try:
            report("daily_diaries", "正在为全部 Agent 生成第一人称日记。", day=new_day)
            daily_diaries = write_agent_daily_diaries(conn, new_day, results)
            report("campus_news", "正在从当天行动中抽取最多 4 条生成校园新闻。", day=new_day, daily_diaries=len(daily_diaries))
            published_news = publish_agent_news(conn, new_day, results)
            conn.commit()
        except Exception:
            logger.exception("Daily publishing failed for day %s", new_day)
            conn.rollback()
            daily_diaries = []
            published_news = []
        add_event(conn, new_day, "daily_reflect", f"第 {new_day} 天校园自动模拟完成，共产生 {len(results)} 个行动。")
        conn.commit()
        report("finished", f"第 {new_day} 天模拟完成，共处理 {len(results)} 个 Agent。", day=new_day, actions_count=len(results), fallback_agents=fallback_agents)
        return {
            "message": "校园一天模拟完成",
            "day": new_day,
            "environment": env,
            "external_information_spread": spread_count,
            "actions": results,
            "group_goal_updates": group_updates,
            "daily_diaries": len(daily_diaries),
            "published_news": published_news,
            "fallback_agents": fallback_agents,
        }


@app.post("/api/simulate/ai-day")
def simulate_ai_day():
    return run_simulate_ai_day()


def prune_simulation_jobs(max_age_seconds=3600):
    cutoff = time.time() - max_age_seconds
    with SIMULATION_JOBS_LOCK:
        stale_ids = [
            job_id
            for job_id, job in SIMULATION_JOBS.items()
            if job.get("created_at", 0) < cutoff and job.get("status") != "running"
        ]
        for job_id in stale_ids:
            SIMULATION_JOBS.pop(job_id, None)


@app.post("/api/simulate/ai-day/progress")
def start_simulate_ai_day_progress():
    prune_simulation_jobs()
    job_id = uuid4().hex
    logger.info("[simulate-day:%s] progress job created", job_id)
    job = {
        "id": job_id,
        "status": "running",
        "events": [
            {
                "event": "queued",
                "message": "模拟任务已启动，正在连接校园世界。",
            }
        ],
        "result": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with SIMULATION_JOBS_LOCK:
        SIMULATION_JOBS[job_id] = job

    def append_event(event):
        with SIMULATION_JOBS_LOCK:
            current = SIMULATION_JOBS.get(job_id)
            if not current:
                return
            current["events"].append(event)
            current["updated_at"] = time.time()
        logger.info(
            "[simulate-day:%s] event queued | %s | %s",
            job_id,
            event.get("event"),
            event.get("message"),
        )

    def worker():
        try:
            result = run_simulate_ai_day(append_event)
            with SIMULATION_JOBS_LOCK:
                current = SIMULATION_JOBS.get(job_id)
                if current:
                    current["status"] = "complete"
                    current["result"] = {
                        "message": result["message"],
                        "day": result["day"],
                        "actions_count": len(result["actions"]),
                        "daily_diaries": result["daily_diaries"],
                        "published_news_count": len(result["published_news"]),
                        "fallback_agents": result["fallback_agents"],
                    }
                    current["events"].append(
                        {
                            "event": "complete",
                            "message": result["message"],
                            **current["result"],
                        }
                    )
                    current["updated_at"] = time.time()
            logger.info("[simulate-day:%s] progress job complete", job_id)
        except Exception as exc:
            logger.exception("Progress simulation failed")
            with SIMULATION_JOBS_LOCK:
                current = SIMULATION_JOBS.get(job_id)
                if current:
                    current["status"] = "error"
                    current["error"] = {"message": str(exc), "type": type(exc).__name__}
                    current["events"].append(
                        {
                            "event": "error",
                            "message": str(exc),
                            "error": type(exc).__name__,
                        }
                    )
                    current["updated_at"] = time.time()
            logger.info("[simulate-day:%s] progress job failed | %s", job_id, type(exc).__name__)

    Thread(target=worker, daemon=True).start()
    return {"job_id": job_id, "status": "running", "events": job["events"]}


@app.get("/api/simulate/ai-day/progress/{job_id}")
def get_simulate_ai_day_progress(job_id: str, after: int = 0):
    with SIMULATION_JOBS_LOCK:
        job = SIMULATION_JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="模拟任务不存在或已过期")
        events = list(job["events"])
        logger.info(
            "[simulate-day:%s] progress polled | after=%s new=%s status=%s",
            job_id,
            after,
            len(events[after:]),
            job["status"],
        )
        return {
            "job_id": job_id,
            "status": job["status"],
            "events": events[after:],
            "next_index": len(events),
            "result": job["result"],
            "error": job["error"],
        }


@app.post("/api/simulate/ai-day/stream")
def simulate_ai_day_stream():
    events = Queue()

    def progress(event):
        events.put(event)

    def worker():
        try:
            result = run_simulate_ai_day(progress)
            events.put(
                {
                    "event": "complete",
                    "message": result["message"],
                    "day": result["day"],
                    "actions_count": len(result["actions"]),
                    "daily_diaries": result["daily_diaries"],
                    "published_news_count": len(result["published_news"]),
                    "fallback_agents": result["fallback_agents"],
                }
            )
        except Exception as exc:
            logger.exception("Streaming simulation failed")
            events.put({"event": "error", "message": str(exc), "error": type(exc).__name__})
        finally:
            events.put(None)

    Thread(target=worker, daemon=True).start()

    def stream_events():
        while True:
            event = events.get()
            if event is None:
                break
            yield json_dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream_events(), media_type="application/x-ndjson; charset=utf-8")
