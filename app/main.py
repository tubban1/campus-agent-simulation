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
from contextlib import asynccontextmanager, contextmanager
from threading import Event, Lock, Thread
from uuid import uuid4
from xml.etree import ElementTree
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db import get_connection, using_postgres
from app.db.migration_runtime import (
    READINESS_REQUIRED_TABLES,
    create_migration_engine,
    get_alembic_config,
    get_current_revision,
    get_head_revision,
    list_business_tables,
    migrate_pending_to_head,
)
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
from app.api.external_router import router as external_information_router
from app.api.news_router import router as news_router
from app.api.system_router import router as system_router
from app.api.research_router import router as research_router
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
from app.memory_read_service import paginated_memories, recent_context
from app.agent_service import calculate_action_cost as calculate_agent_action_cost, ensure_action_affordable as ensure_agent_action_affordable, choose_mood as choose_agent_mood, recover_agents_for_new_day as recover_agents
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
    life_course_action_label,
    life_course_evidence,
    score_life_course_event,
    life_course_kind,
    life_course_display_title,
    life_course_turning_summary,
    life_course_temporal_coverage,
    life_course_episodes,
    life_course_groups,
    life_course_relationships,
    life_course_latest_recorded_day,
    life_course_timeline,
    build_life_course_overview,
    lifecycle_events,
    lifecycle_groups,
    lifecycle_overview,
    lifecycle_relationships,
    lifecycle_turning_points,
)

_life_course_temporal_coverage = life_course_temporal_coverage


def classify_campus_news_candidate(event_type, action="", content="", payload=None):
    return classify_candidate(event_type, action, content, payload, json_dumps=json_dumps)


def _life_course_timeline(conn, resident_id, from_day=None, to_day=None, limit=240):
    return life_course_timeline(
        conn,
        resident_id,
        from_day,
        to_day,
        limit,
        active_branch=active_world_branch_key,
        load_json=load_json_text,
    )
from app.simulation_read_service import fetch_simulation_logs
from app.world_state.read_service import get_snapshot, list_branches, list_snapshots
from app.world_state.snapshot_catalog import *
from app.world_state.write_service import (
    create_branch,
    create_snapshot,
    require_paused_runtime,
    restore_snapshot,
    switch_branch,
)
from app.world_state.models import (
    EnvironmentConfigRequest,
    WorldBranchRequest,
    WorldBranchSwitchRequest,
    WorldSnapshotRequest,
    WorldSnapshotRestoreRequest,
)
from app.campus_models import CampusEnvironmentRequest, CampusEventRequest, SpaceStatusRequest
from app.social_models import ChatRequest, NegotiateRequest, CollaborateRequest, CompeteRequest, LongTermGoalRequest, GroupGoalRequest
from app.tools_models import MoveRequest, BuySellRequest
from app.admin_models import AdminWorldEventRequest
from app.research_models import CalibrationObservationRequest
from services.newspaper import classify_candidate, headline as campus_news_headline, fallback_content as fallback_campus_news_content, publish_agent_news as publish_news_service, write_daily_diaries, collect_candidates
from services.external_information import classify_information as classify_external_information, compact_sync_result as compact_external_sync_result, fetch_information, deliver_information, seed_recipients, spread_information, sync_into_world, maybe_auto_sync
from services import social_actions
from services import policy_actions
from services import observability
from app.policy_models import PolicyRequest, VotePolicyRequest
from app.api.world_router import router as world_api_router
from app.api.agent_router import router as agent_api_router
from app.api.campus_router import router as campus_api_router
from app.api.lifecycle_router import router as lifecycle_api_router
from app.api.social_router import router as social_api_router
from app.world_runtime.orchestrator import (
    run_post_tick_handlers,
    run_agent_and_learning_stage,
    run_pre_agent_subsystems,
    settle_tick_completion,
    start_world_tick,
)
from app.world_runtime.dream_runtime import process_night_dreams as _process_night_dreams
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

@asynccontextmanager
async def app_lifespan(_app):
    """Own the optional world writer for this ASGI process lifetime.

    On PostgreSQL, bring the schema up to the current migration head before
    serving requests so a deployment that lags its migrations heals on restart
    instead of failing at runtime with missing tables.
    """
    if using_postgres():
        try:
            result = migrate_pending_to_head()
            if result.get("applied"):
                logger.info(
                    "Applied pending schema migrations %s -> %s",
                    result.get("from_revision"),
                    result.get("to_revision"),
                )
            elif result.get("reason") == "unversioned_schema":
                logger.warning(
                    "Database schema is unversioned; run scripts/deploy_database.py "
                    "to bootstrap before serving."
                )
        except Exception:
            logger.exception("Startup schema migration failed; serving with current schema")
    start_world_runner_thread()
    try:
        yield
    finally:
        WORLD_RUNNER_STOP_EVENT.set()


app = FastAPI(
    title="World2 · Agent 平行世界",
    version="1.0.0",
    lifespan=app_lifespan,
)
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
app.include_router(external_information_router)
app.include_router(news_router)
app.include_router(system_router)
app.include_router(research_router)
app.include_router(longitudinal_router)
app.include_router(world_api_router)
app.include_router(agent_api_router)
app.include_router(campus_api_router)
app.include_router(lifecycle_api_router)
app.include_router(social_api_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/avatars", StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "assets" / "avatars")), name="avatars")
app.mount("/css", StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "js")), name="js")
THREE_MODULE_DIR = PROJECT_ROOT / "frontend" / "vendor" / "three"
app.mount("/three", StaticFiles(directory=str(THREE_MODULE_DIR)), name="three")
app.mount("/vendor", StaticFiles(directory=str(PROJECT_ROOT / "frontend" / "vendor")), name="vendor")
SOCIAL_SCHEMA_LOCK = Lock()
SOCIAL_SCHEMA_READY = False
WORLD_RUNNER_LOCK = Lock()
WORLD_RUNNER_THREAD = None
WORLD_RUNNER_STOP_EVENT = Event()
WORLD_TICK_LOCK = Lock()
WRITE_RATE_LIMIT_LOCK = Lock()
WRITE_REQUESTS_BY_CLIENT = {}
WORLD_SCHEMA_LOCK = Lock()
WORLD_SCHEMA_READY = False
WORLD_RUNTIME_ID = 1
WORLD_TICK_ADVISORY_LOCK_ID = 7_436_177_031
DEFAULT_WORLD_STALE_TICK_SECONDS = 90
WORLD_EXTERNAL_SYNC_INTERVAL_SECONDS = 900
WORLD_WEATHER_SYNC_INTERVAL_SECONDS = 1800
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
        "metadata": {"description": "从居民位置与行动事件聚合 World2 空间活动"},
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
    DEFAULT_ENV, DEFAULT_SPACES, ENV_COLUMN_TYPES,
SOCIAL_SYSTEM_SQL, BEHAVIOR_SYSTEM_SQL, RELATIONSHIP_DYNAMIC_COLUMNS,
    LONG_TERM_GOAL_COLUMNS, AGENT_INFORMATION_COLUMNS, WORLD_RUNTIME_SQL, RESEARCH_SYSTEM_SQL,
    WORLD_RUNTIME_COLUMNS, WORLD_EVENT_STREAM_COLUMNS, WORLD_SNAPSHOT_COLUMNS,
    EXPERIMENT_RUN_COLUMNS,
)
from app.db.bootstrap_schema import (
    SchemaMigrationRequired,
    ensure_agent_news_system,
    ensure_agent_profile_table,
    ensure_campus_state_table,
    ensure_external_information_system,
    ensure_space_system,
    ensure_table_columns,
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




def load_json_text(text, fallback):
    if not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


from app.goals import service as goal_service
from app.social import service as social_service


infer_goal_category = goal_service.infer_goal_category


def seed_long_term_goals(conn):
    return goal_service.seed_long_term_goals(conn, current_day=get_current_day)


def seed_multiscale_goals(conn):
    return goal_service.seed_multiscale_goals(conn)


def record_goal_revision(conn, goal_id, resident_id, revision_type, before=None, after=None, reason="", trigger_type="runtime", tick_id=None):
    return goal_service.record_goal_revision(
        conn, goal_id, resident_id, revision_type, current_day=get_current_day,
        json_dumps=json_dumps, before=before, after=after, reason=reason,
        trigger_type=trigger_type, tick_id=tick_id,
    )


def parse_goal_deadline(value):
    return goal_service.parse_goal_deadline(value, parse_world_datetime=parse_world_datetime)


def multiscale_goal_templates(resident, long_goal):
    return goal_service.multiscale_goal_templates(resident, long_goal, role_group=role_group)


ensure_goal_trajectory_episode = goal_service.ensure_goal_trajectory_episode
attach_goal_context_to_plan = goal_service.attach_goal_context_to_plan


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
    return social_service.get_relationship_dynamics(conn, from_id, to_id, ensure_tables=ensure_social_system_tables, relationship_score=get_relationship_score, clamp=clamp, current_day=get_current_day)








def relationship_histories_by_target(conn, from_id, target_ids, per_target=12):
    return social_service.relationship_histories_by_target(conn, from_id, target_ids, per_target)


def record_social_relation_interpretation(conn, from_id, to_id, tick_id=None, perspective="system_researcher"):
    return social_service.record_social_relation_interpretation(conn, from_id, to_id, tick_id, perspective, ensure_tables=ensure_social_system_tables, infer_relationship=infer_emergent_relationship, current_day=get_current_day, json_dumps=json_dumps)


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
        (progress, status, get_current_day(conn), status, get_world_now().isoformat(), goal["id"]),
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


def schedule_location(task, conn=None, resident_id=None):
    text = str(task or "")
    action = None
    if any(word in text for word in ["早餐", "午餐", "晚餐", "吃饭", "备菜"]):
        action, fallback = "consume", "食堂"
    elif any(word in text for word in ["课程", "课", "实验", "面试", "小组讨论", "编程"]):
        action, fallback = "attend_class", "教学楼"
    elif any(word in text for word in ["图书馆", "自习", "阅读", "背单词", "论文", "查招聘", "投递简历"]):
        action, fallback = "attend_class", "图书馆"
    elif any(word in text for word in ["训练", "晨跑", "操场", "采访"]):
        action, fallback = "club_activity", "操场"
    elif any(word in text for word in ["开店", "促销", "订单", "调研", "奶茶", "商业"]):
        action, fallback = "consume", "商业街"
    elif any(word in text for word in ["通知", "校务", "审批", "巡查", "维护", "维修", "治理"]):
        action, fallback = "request_leave", "校务处"
    elif any(word in text for word in ["宿舍", "复盘", "休息", "睡"]):
        action, fallback = "rest", "宿舍区"
    else:
        return None

    if conn and resident_id and action:
        from app.spatial.location_catalog import choose_weighted_real_location
        concrete = choose_weighted_real_location(conn, resident_id, action)
        if concrete:
            return concrete
    return fallback


def get_schedule_context(schedule, env, conn=None, resident_id=None):
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
        parsed.append({"entry": str(entry), "start_minutes": start, "task": task, "location": schedule_location(task, conn=conn, resident_id=resident_id)})
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




def get_all_agent_module_states(conn):
    rows = conn.execute("SELECT id FROM residents ORDER BY id").fetchall()
    return [get_agent_module_state(conn, row["id"]) for row in rows]


def clamp(value, low=0, high=100):
    return max(low, min(high, int(value)))


def choose_mood(energy, action, success=True):
    return choose_agent_mood(energy, action, success)


def calculate_action_cost(conn, resident_id, action, tool_input=None, success=True):
    return calculate_agent_action_cost(conn, resident_id, action, tool_input, success, campus_environment=get_campus_environment, space_snapshot=get_space_snapshot)


def ensure_action_affordable(conn, resident_id, cost, action):
    return ensure_agent_action_affordable(conn, resident_id, cost, action)




def recover_agents_for_new_day(conn, day):
    return recover_agents(conn, day, ensure_profile_table=ensure_agent_profile_table, add_event=add_event)


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






# Default weather anchor for the Tsinghua campus world.  Callers can still
# provide a different location explicitly when another geographic world is
# active.
BEIJING_LATITUDE = 40.0062
BEIJING_LONGITUDE = 116.3269

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






def fetch_met_no_weather(latitude=BEIJING_LATITUDE, longitude=BEIJING_LONGITUDE):
    response = requests.get(
        "https://api.met.no/weatherapi/locationforecast/2.0/compact",
        params={"lat": latitude, "lon": longitude},
        headers={"User-Agent": "world2/1.0 github.com/tubban1/world2"},
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


def fetch_real_weather(latitude=BEIJING_LATITUDE, longitude=BEIJING_LONGITUDE):
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


from app.environment import service as environment_service


def derive_environment_from_weather(base_values):
    return environment_service.derive_environment_from_weather(base_values, clamp=clamp)


def save_environment_values(conn, day, values):
    return environment_service.save_environment_values(
        conn,
        day,
        values,
        default_environment=DEFAULT_ENV,
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
    return social_service.get_relationship_score(conn, from_id, to_id)


def change_relationship(conn, from_id, to_id, delta, note):
    return social_service.change_relationship(conn, from_id, to_id, delta, note, clamp=clamp)


def negotiate_between(conn, initiator_id, target_id, topic, proposal):
    return social_service.negotiate_between(conn, initiator_id, target_id, topic, proposal, ensure_tables=ensure_social_system_tables, get_resident=get_resident, profile_meta=ensure_profile_meta, relationship_score=get_relationship_score, evolve_relationship=evolve_relationship, add_event=add_event, current_day=get_current_day, record_learning=record_learning, action_score=action_score, not_found=lambda detail: HTTPException(status_code=404, detail=detail))


def create_collaboration(conn, leader_id, member_ids, title, goal):
    return social_service.create_collaboration(conn, leader_id, member_ids, title, goal, ensure_tables=ensure_social_system_tables, json_dumps=json_dumps, current_day=get_current_day, evolve_relationship=evolve_relationship, record_learning=record_learning, action_score=action_score, add_event=add_event, not_found=lambda detail: HTTPException(status_code=404, detail=detail))


def record_group_membership_event(conn, group_id, resident_id, action, reason, member_ids):
    return social_service.record_group_membership_event(conn, group_id, resident_id, action, reason, member_ids, current_day=get_current_day, json_dumps=json_dumps)


def join_group_goal(conn, resident_id, group_id):
    return social_service.change_group_membership(conn, resident_id, group_id, "join", ensure_tables=ensure_social_system_tables, load_json=load_json_text, json_dumps=json_dumps, record_membership=record_group_membership_event, evolve_relationship=evolve_relationship, add_event=add_event, current_day=get_current_day)


def leave_group_goal(conn, resident_id, group_id):
    return social_service.change_group_membership(conn, resident_id, group_id, "leave", ensure_tables=ensure_social_system_tables, load_json=load_json_text, json_dumps=json_dumps, record_membership=record_group_membership_event, evolve_relationship=evolve_relationship, add_event=add_event, current_day=get_current_day)


def create_competition(conn, participant_ids, title, metric):
    return social_service.create_competition(conn, participant_ids, title, metric, ensure_tables=ensure_social_system_tables, load_json=load_json_text, json_dumps=json_dumps, random_int=random.randint, record_learning=record_learning, action_score=action_score, evolve_relationship=evolve_relationship, add_event=add_event, current_day=get_current_day, bad_request=lambda detail: HTTPException(status_code=400, detail=detail), not_found=lambda detail: HTTPException(status_code=404, detail=detail))


class ObserverSessionRequest(BaseModel):
    session_id: Optional[int] = None
    user_id: str = "anonymous"
    session_type: str = "observer"
    focused_resident_id: Optional[int] = None
    focused_location: str = ""


def row_to_dict(row):
    return dict(row) if row else None


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


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






def environment_version_label(config_row):
    return environment_service.environment_version_label(config_row)


def decode_environment_config(row):
    return environment_service.decode_environment_config(row, load_json=load_json_text)


def seed_default_environment_config(conn):
    return environment_service.seed_default_environment_config(
        conn,
        default_config=default_environment_config,
        content_checksum=content_checksum,
        canonical_json=canonical_json,
    )


def get_active_environment_config(conn):
    return environment_service.get_active_environment_config(
        conn,
        runtime_id=WORLD_RUNTIME_ID,
        load_json=load_json_text,
    )


def create_environment_config_record(conn, config_key, name, config, parent_config_id=None, created_by="admin"):
    return environment_service.create_environment_config_record(
        conn,
        config_key,
        name,
        config,
        parent_config_id,
        created_by,
        validate_config=validate_environment_config,
        content_checksum=content_checksum,
        canonical_json=canonical_json,
        load_json=load_json_text,
    )




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


from app.world_runtime import causal_actions
def _refresh_causal_actions_runtime():
    bindings = dict(globals())
    for name in ("evaluate_world_action_preconditions", "append_world_event", "begin_world_action_execution", "finalize_rejected_action_execution", "link_action_execution_event", "apply_structured_world_effect", "settle_world_action_resources", "enqueue_world_delayed_effects", "process_due_world_delayed_effects"):
        bindings.pop(name, None)
    causal_actions.configure(**bindings)

causal_actions.configure(**globals())
def evaluate_world_action_preconditions(*args, **kwargs):
    _refresh_causal_actions_runtime()
    return causal_actions.evaluate_world_action_preconditions(*args, **kwargs)
decode_world_update_schedule = causal_actions.decode_world_update_schedule
decode_world_update_run = causal_actions.decode_world_update_run
def begin_world_action_execution(*args, **kwargs):
    _refresh_causal_actions_runtime()
    return causal_actions.begin_world_action_execution(*args, **kwargs)
finalize_rejected_action_execution = causal_actions.finalize_rejected_action_execution
link_action_execution_event = causal_actions.link_action_execution_event
apply_structured_world_effect = causal_actions.apply_structured_world_effect
settle_world_action_resources = causal_actions.settle_world_action_resources
enqueue_world_delayed_effects = causal_actions.enqueue_world_delayed_effects
def process_due_world_delayed_effects(conn, world_time, tick_id=None, day=None, slot=None, limit=100):
    bindings = dict(globals())
    for name in ("evaluate_world_action_preconditions", "begin_world_action_execution", "finalize_rejected_action_execution", "link_action_execution_event", "apply_structured_world_effect", "settle_world_action_resources", "enqueue_world_delayed_effects", "process_due_world_delayed_effects"):
        bindings.pop(name, None)
    causal_actions.configure(**bindings)
    return causal_actions.process_due_world_delayed_effects(conn, world_time, tick_id, day, slot, limit)


from app.world_runtime import remaining_runtime
remaining_runtime.configure(**globals())


def _runtime_dependencies_for(module):
    names = set()
    module_function_names = set()
    for value in module.__dict__.values():
        if hasattr(value, "__code__") and getattr(value, "__module__", None) == module.__name__:
            module_function_names.add(value.__name__)
            names.update(value.__code__.co_names)
    return {
        name: globals()[name]
        for name in names
        if name in globals() and name not in module_function_names
    }

action_for_context = remaining_runtime.action_for_context
agent_newspaper_posts = remaining_runtime.agent_newspaper_posts
append_social_interaction_event = remaining_runtime.append_social_interaction_event
build_agent_social_graph = remaining_runtime.build_agent_social_graph
def _refresh_remaining_runtime():
    remaining_runtime.RemainingRuntimeDependencies(
        _runtime_dependencies_for(remaining_runtime)
    ).apply()


def build_autonomous_tick_decision(conn, agent, perception, step):
    _refresh_remaining_runtime()
    return remaining_runtime.build_autonomous_tick_decision(conn, agent, perception, step)


def build_rule_based_plan(conn, resident, window_start, window_end, world_time=None, goal_context=None):
    _refresh_remaining_runtime()
    return remaining_runtime.build_rule_based_plan(conn, resident, window_start, window_end, world_time, goal_context)
choose_plan_step = remaining_runtime.choose_plan_step
create_agent_goal = remaining_runtime.create_agent_goal
default_event_configuration = remaining_runtime.default_event_configuration
generate_admin_event_impact = remaining_runtime.generate_admin_event_impact
generate_observed_agent_detail = remaining_runtime.generate_observed_agent_detail
def get_space_snapshot(conn, day=None):
    _refresh_remaining_runtime()
    return remaining_runtime.get_space_snapshot(conn, day)
maybe_create_social_commitment = remaining_runtime.maybe_create_social_commitment
perceive_environment = remaining_runtime.perceive_environment
record_learning = remaining_runtime.record_learning
retrieve_relevant_memories = remaining_runtime.retrieve_relevant_memories
def runtime_response(conn):
    _refresh_remaining_runtime()
    return remaining_runtime.runtime_response(conn)
def sync_current_day_with_world_date(conn, world_time):
    _refresh_remaining_runtime()
    return remaining_runtime.sync_current_day_with_world_date(conn, world_time)

from app.world_runtime import environment_config
environment_config.configure(**globals())
def apply_environment_config(conn, config_row):
    bindings = dict(globals())
    bindings.pop("apply_environment_config", None)
    environment_config.configure(**bindings)
    return environment_config.apply_environment_config(conn, config_row)
build_environment_modules = environment_config.build_environment_modules
default_environment_config = environment_config.default_environment_config
derive_environment_from_real_time = environment_config.derive_environment_from_real_time
get_real_campus_time = environment_config.get_real_campus_time
validate_environment_config = environment_config.validate_environment_config

from app.world_state import runtime_schema


def _runtime_schema_dependencies():
    names = (
        "ENV_COLUMN_TYPES", "EXPERIMENT_RUN_COLUMNS", "RESEARCH_SYSTEM_SQL",
        "WORLD_EVENT_STREAM_COLUMNS", "WORLD_RUNTIME_COLUMNS", "WORLD_RUNTIME_ID",
        "WORLD_RUNTIME_SQL", "WORLD_SCHEMA_LOCK", "WORLD_SCHEMA_READY", "WORLD_SNAPSHOT_COLUMNS",
        "WORLD_TIMEZONE", "WORLD_TZ", "_world_event_json_default", "ensure_social_system_tables",
        "ensure_table_columns", "environment_version_label", "get_current_day", "get_world_now",
        "json_dumps", "seed_agent_personality_traits", "seed_default_environment_config",
        "seed_world_action_rules", "seed_world_runtime_rules", "seed_world_update_schedules",
        "using_postgres", "world_slot_from_hour",
    )
    return runtime_schema.RuntimeSchemaDependencies({name: globals()[name] for name in names})


_runtime_schema_dependencies().apply()
def ensure_world_runtime_tables(conn, *, allow_ddl=False):
    _runtime_schema_dependencies().apply()
    return runtime_schema.ensure_world_runtime_tables(conn, allow_ddl=allow_ddl)
append_world_event = runtime_schema.append_world_event

from app.world_runtime import social_runtime
social_runtime.configure(**globals())
_initialize_social_system_tables = social_runtime._initialize_social_system_tables
def advance_multiscale_goals_from_outcome(conn, resident_id, goal_ids, action, adherence, world_time, tick_id, outcome_id):
    social_runtime.configure(**_runtime_dependencies_for(social_runtime))
    return social_runtime.advance_multiscale_goals_from_outcome(
        conn, resident_id, goal_ids, action, adherence, world_time, tick_id, outcome_id
    )
evolve_relationship = social_runtime.evolve_relationship
infer_emergent_relationship = social_runtime.infer_emergent_relationship
update_agent_profile_after_action = social_runtime.update_agent_profile_after_action
remaining_runtime.infer_emergent_relationship = infer_emergent_relationship

from app.world_runtime import state_environment


def _state_environment_dependencies():
    names = (
        "action_noise_for_agent", "active_schedule_rules", "active_world_branch_key",
        "causal_multiplier_for_target", "derive_environment_from_real_time",
        "derive_environment_from_weather", "ensure_agent_profile_table", "ensure_memory_columns",
        "fetch_real_weather", "get_campus_environment", "get_current_day", "get_hierarchy_title",
        "get_schedule_context", "is_location_open_at_hour", "load_json_text", "logger",
        "maybe_generate_environment_event", "random", "realistic_location_for_context", "role_group",
        "rows_to_dicts", "save_environment_values", "spatial_memory_location_factors",
    )
    return state_environment.StateEnvironmentDependencies({name: globals()[name] for name in names})


state_environment.configure(**globals())
apply_realism_constraints_to_decision = state_environment.apply_realism_constraints_to_decision
auto_update_environment = state_environment.auto_update_environment


def _refresh_state_environment_runtime():
    _state_environment_dependencies().apply()


def get_agent_module_state(conn, resident_id):
    _refresh_state_environment_runtime()
    return state_environment.get_agent_module_state(conn, resident_id)


def location_options_for_context(role, hour, weather="", current_location="", conn=None, env=None, agent=None):
    _refresh_state_environment_runtime()
    return state_environment.location_options_for_context(role, hour, weather, current_location, conn, env, agent)

from app.world_runtime import planning_decision
planning_decision.configure(**globals())
apply_wellbeing_priority_to_decision = planning_decision.apply_wellbeing_priority_to_decision


def _refresh_planning_decision_runtime():
    bindings = dict(globals())
    for name in (
        "review_multiscale_goals",
        "ensure_daily_commitments",
        "ensure_multiscale_goal_structure",
        "build_llm_action_plan",
        "ensure_current_action_plans",
        "decide_agent_action",
        "execute_decision",
        "record_plan_outcome",
        "build_runtime_perception",
        "apply_wellbeing_priority_to_decision",
    ):
        bindings.pop(name, None)
    planning_decision.configure(**bindings)


def ensure_current_action_plans(conn, world_time):
    _refresh_planning_decision_runtime()
    return planning_decision.ensure_current_action_plans(conn, world_time)


def build_runtime_perception(conn, agent, world_time, day, slot, plan, step, observed):
    _refresh_planning_decision_runtime()
    return planning_decision.build_runtime_perception(conn, agent, world_time, day, slot, plan, step, observed)
build_llm_action_plan = planning_decision.build_llm_action_plan
decide_agent_action = planning_decision.decide_agent_action
ensure_daily_commitments = planning_decision.ensure_daily_commitments
ensure_multiscale_goal_structure = planning_decision.ensure_multiscale_goal_structure
execute_decision = planning_decision.execute_decision
record_plan_outcome = planning_decision.record_plan_outcome
review_multiscale_goals = planning_decision.review_multiscale_goals

from app.world_runtime import update_scheduler
update_scheduler.configure(**globals())
def run_due_world_updates(conn, world_time, tick_id, day, slot, parent_event_id=None):
    bindings = dict(globals())
    bindings.pop("run_due_world_updates", None)
    update_scheduler.configure(**bindings)
    return update_scheduler.run_due_world_updates(
        conn, world_time, tick_id, day, slot, parent_event_id
    )


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


def process_night_dreams(conn, world_time, *, day):
    """Bind private dream generation to the application's model/audit budget."""
    return _process_night_dreams(
        conn,
        world_time,
        day=day,
        add_memory=add_memory,
        consume_auto_model_budget=consume_auto_model_budget,
        ask_llm=ask_llm,
        is_llm_configured=is_llm_configured,
        log_model_call=log_model_call,
    )


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




def realistic_location_for_context(role, hour, weather="", current_location="", preferred_location="", conn=None, env=None, agent=None):
    if preferred_location and is_location_open_at_hour(preferred_location, hour):
        if preferred_location == "操场" and any(token in str(weather or "") for token in ("雨", "雷", "雪", "大风")):
            return weighted_choice(location_options_for_context(role, hour, weather, current_location, conn=conn, env=env, agent=agent))
        return preferred_location
    return weighted_choice(location_options_for_context(role, hour, weather, current_location, conn=conn, env=env, agent=agent))






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






def get_environment_hour(env):
    return environment_service.get_environment_hour(env)


def get_active_campus_events(conn, day=None):
    return environment_service.get_active_campus_events(
        conn, day, current_day=get_current_day, rows_to_dicts=rows_to_dicts
    )




def assert_destination_available(conn, destination):
    return environment_service.assert_destination_available(
        conn, destination, valid_locations=VALID_LOCATIONS, space_snapshot=get_space_snapshot
    )


def get_campus_environment(conn, day=None):
    return environment_service.get_campus_environment(
        conn,
        day,
        ensure_state_table=ensure_campus_state_table,
        current_day=get_current_day,
        default_environment=DEFAULT_ENV,
        build_modules=build_environment_modules,
    )


def apply_campus_event_effects(conn, day, effects):
    return environment_service.apply_campus_event_effects(
        conn,
        day,
        effects,
        default_environment=DEFAULT_ENV,
        campus_environment=get_campus_environment,
    )


def create_campus_event(conn, day, title, event_type, intensity, target_spaces=None, effects=None):
    return environment_service.create_campus_event(
        conn,
        day,
        title,
        event_type,
        intensity,
        target_spaces,
        effects,
        ensure_space_system=ensure_space_system,
        campus_environment=get_campus_environment,
        default_event_configuration=default_event_configuration,
        json_dumps=json_dumps,
        apply_effects=apply_campus_event_effects,
        add_event=add_event,
    )


def maybe_generate_environment_event(conn, day):
    return environment_service.maybe_generate_environment_event(
        conn,
        day,
        active_events=get_active_campus_events,
        campus_environment=get_campus_environment,
        create_event=create_campus_event,
        random_value=random.random,
    )




def get_recent_context(conn, resident_id, limit=6, query_terms=None):
    return recent_context(conn, resident_id, limit, query_terms, retrieve_memories=retrieve_relevant_memories, current_day=get_current_day, rows_to_dicts=rows_to_dicts)


def extract_json(text):
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.S)
    if match:
        cleaned = match.group(0)
    return json.loads(cleaned)






def apply_environment_feedback(conn, resident_id, action, result):
    return environment_service.apply_environment_feedback(
        conn,
        resident_id,
        action,
        result,
        current_day=get_current_day,
        campus_environment=get_campus_environment,
        clamp=clamp,
        add_event=add_event,
    )


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
    relevant = (
        action in GOAL_RELEVANT_ACTIONS.get(goal.get("category"), GOAL_RELEVANT_ACTIONS["general"])
        or (action == "move" and adherence in ("followed", "adjusted"))
    )
    if not relevant:
        return 0
    base = {"short": 12, "medium": 5, "long": 2}.get(goal.get("horizon"), 1)
    if adherence == "followed":
        return base
    if adherence == "adjusted":
        return max(1, round(base * 0.7))
    return max(0, round(base * 0.35))




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






def normalize_runtime_decision(payload, fallback_step, fallback_location, fallback_goal):
    payload = payload if isinstance(payload, dict) else {}
    action = str(payload.get("action") or fallback_step.get("action") or "observe").strip().lower()
    action_aliases = {
        "hydrate": "consume",
        "drink": "consume",
        "eat": "consume",
        "buy": "consume",
        "shopping": "consume",
        "study": "attend_class",
        "work": "reflect",
        "sleep": "rest",
        "nap": "rest",
        "walk": "move",
        "goto": "move",
        "talk": "chat",
        "speak": "chat",
    }
    if action in action_aliases:
        action = action_aliases[action]
    elif action not in WORLD_AUTONOMOUS_ACTIONS:
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




def nearby_interaction_target(conn, agent_id, location):
    row = conn.execute(
        """
        SELECT other.id, other.name, node.id AS node_id, node.name AS location
        FROM agent_spatial_states actor_state
        JOIN agent_spatial_states other_state
          ON other_state.current_node_id = actor_state.current_node_id
         AND other_state.resident_id != actor_state.resident_id
         AND other_state.movement_status IN ('idle', 'arrived')
        JOIN residents other ON other.id = other_state.resident_id
        JOIN spatial_nodes node ON node.id = actor_state.current_node_id
        WHERE actor_state.resident_id = ?
          AND actor_state.movement_status IN ('idle', 'arrived')
          AND node.name = ?
        ORDER BY other.id
        LIMIT 1
        """,
        (agent_id, location),
    ).fetchone()
    return dict(row) if row else None




from app.world_runtime import action_execution
action_execution.configure(
    nearby_interaction_target=nearby_interaction_target,
    evolve_relationship=evolve_relationship,
    maybe_create_social_commitment=maybe_create_social_commitment,
    add_event=add_event,
    add_memory_once=add_memory_once,
    get_agent_module_state=get_agent_module_state,
    get_current_agent_plan=get_current_agent_plan,
    choose_plan_step=choose_plan_step,
    build_runtime_perception=build_runtime_perception,
    build_autonomous_tick_decision=build_autonomous_tick_decision,
    apply_realism_constraints_to_decision=apply_realism_constraints_to_decision,
    apply_wellbeing_priority_to_decision=apply_wellbeing_priority_to_decision,
    spatial_runtime_available=spatial_runtime_available,
    begin_world_action_execution=begin_world_action_execution,
    settle_world_action_resources=settle_world_action_resources,
    finalize_rejected_action_execution=finalize_rejected_action_execution,
    append_world_event=append_world_event,
    link_action_execution_event=link_action_execution_event,
    record_simulation_log=record_simulation_log,
    move_resident=move_resident,
    enqueue_world_delayed_effects=enqueue_world_delayed_effects,
    record_plan_outcome=record_plan_outcome,
    mark_plan_step_executed=mark_plan_step_executed,
    generate_observed_agent_detail=generate_observed_agent_detail,
    VALID_LOCATIONS=VALID_LOCATIONS,
    json_dumps=json_dumps,
)
apply_runtime_social_effect = action_execution.apply_runtime_social_effect
describe_runtime_action = action_execution.describe_runtime_action
def process_world_agent_tick(conn, agent, world_time, tick_id, day, slot, observed=False, parent_event_id=None):
    _refresh_planning_decision_runtime()
    action_execution.configure(
        get_agent_module_state=get_agent_module_state,
        get_current_agent_plan=get_current_agent_plan,
        build_autonomous_tick_decision=build_autonomous_tick_decision,
        apply_realism_constraints_to_decision=apply_realism_constraints_to_decision,
        append_world_event=append_world_event,
    )
    return action_execution.process_world_agent_tick(
        conn, agent, world_time, tick_id, day, slot, observed, parent_event_id
    )


from services import newspaper as newspaper_runtime
newspaper_runtime.configure_runtime(**globals())
def maybe_publish_campus_news_from_world_window(conn, world_time, tick_id=None, day=None):
    newspaper_runtime.configure_runtime(**globals())
    return newspaper_runtime.maybe_publish_campus_news_from_world_window(
        conn, world_time, tick_id, day
    )
select_world_tick_agents = newspaper_runtime.select_world_tick_agents
maybe_generate_group_behavior_event = newspaper_runtime.maybe_generate_group_behavior_event
sync_world_time_environment = newspaper_runtime.sync_world_time_environment
sync_real_weather_into_world = newspaper_runtime.sync_real_weather_into_world
maybe_auto_sync_real_weather = newspaper_runtime.maybe_auto_sync_real_weather
world_tick_database_lease = newspaper_runtime.world_tick_database_lease


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
    return max(60, configured)


from app.world_runtime import tick_runtime
tick_runtime.configure(**globals())
def _refresh_tick_runtime():
    tick_runtime.TickRuntimeDependencies(
        _runtime_dependencies_for(tick_runtime)
    ).apply()


def reconcile_stale_world_ticks(conn, now=None):
    _refresh_tick_runtime()
    return tick_runtime.reconcile_stale_world_ticks(conn, now)


def record_world_tick_failure(tick_id, reason, exc):
    _refresh_tick_runtime()
    return tick_runtime.record_world_tick_failure(tick_id, reason, exc)


def advance_world_tick(reason="background"):
    _refresh_tick_runtime()
    return tick_runtime.advance_world_tick(reason)
_advance_world_tick_locked = tick_runtime._advance_world_tick_locked


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
        stop_event=WORLD_RUNNER_STOP_EVENT,
    )


def start_world_runner_thread():
    environment = os.getenv("APP_ENV", "local").strip().lower()
    if environment not in {"", "local", "development", "test"} and not os.getenv(
        "ADMIN_TOKEN", ""
    ).strip():
        raise RuntimeError("ADMIN_TOKEN must be configured outside local development")
    global WORLD_RUNNER_THREAD
    if not world_runner_enabled():
        logger.warning("World runner disabled by WORLD_RUNNER_ENABLED")
        return
    with WORLD_RUNNER_LOCK:
        if WORLD_RUNNER_THREAD and WORLD_RUNNER_THREAD.is_alive():
            return
        WORLD_RUNNER_STOP_EVENT.clear()
        WORLD_RUNNER_THREAD = Thread(target=world_runner_loop, daemon=True)
        WORLD_RUNNER_THREAD.start()
        logger.warning("World runner started")




@app.get("/")
def home():
    return FileResponse(
        PROJECT_ROOT / "frontend" / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health/live")
def health_live():
    return {
        "status": "live",
        "environment": os.getenv("APP_ENV", "local").strip().lower() or "local",
        "world_runner": {
            "enabled": world_runner_enabled(),
            "alive": bool(WORLD_RUNNER_THREAD and WORLD_RUNNER_THREAD.is_alive()),
        },
    }


@app.get("/health/ready")
def health_ready():
    engine = None
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        engine = create_migration_engine()
        current_revision = get_current_revision(engine)
        head_revision = get_head_revision(get_alembic_config())
        missing_tables = sorted(READINESS_REQUIRED_TABLES - set(list_business_tables(engine)))
    except Exception:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="database_or_schema_unavailable")
    finally:
        if engine is not None:
            engine.dispose()
    if current_revision != head_revision or missing_tables:
        raise HTTPException(status_code=503, detail="database_schema_not_ready")
    return {"status": "ready", "revision": current_revision}


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


def ai_test():
    if not is_llm_configured():
        raise HTTPException(status_code=503, detail="当前环境未配置 LLM_API_KEY 或 LLM_API_URL，世界将使用规则模式运行。")
    prompt = "请用一句话说明你已接入真实地理校园 Agent 世界。"
    return {"message": "AI API 调用成功", "result": ask_llm(prompt)}


app.state.ai_test = ai_test


def require_admin_token(authorization: Optional[str]):
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected:
        logger.warning("ADMIN_TOKEN is not configured; admin world endpoint is open for local development.")
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=403, detail="Admin token 无效或缺失")


def is_local_environment() -> bool:
    return os.getenv("APP_ENV", "local").strip().lower() in {"", "local", "development", "test"}


@app.middleware("http")
async def protect_nonlocal_write_requests(request: Request, call_next):
    if (
        request.method not in {"POST", "PUT", "PATCH", "DELETE"}
        or not request.url.path.startswith("/api/")
        or is_local_environment()
    ):
        return await call_next(request)

    expected = os.getenv("ADMIN_TOKEN", "").strip()
    if not expected or request.headers.get("authorization") != f"Bearer {expected}":
        return JSONResponse(status_code=403, content={"detail": "Admin token 无效或缺失"})

    try:
        limit = max(1, int(os.getenv("WRITE_RATE_LIMIT_PER_MINUTE", "60")))
    except ValueError:
        limit = 60
    client_key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with WRITE_RATE_LIMIT_LOCK:
        recent = [stamp for stamp in WRITE_REQUESTS_BY_CLIENT.get(client_key, []) if now - stamp < 60]
        if len(recent) >= limit:
            WRITE_REQUESTS_BY_CLIENT[client_key] = recent
            return JSONResponse(status_code=429, content={"detail": "写请求过于频繁"})
        recent.append(now)
        WRITE_REQUESTS_BY_CLIENT[client_key] = recent
    return await call_next(request)


class WorldTickExclusion:
    def __enter__(self):
        if not WORLD_TICK_LOCK.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="世界 tick 或状态恢复正在执行中")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        WORLD_TICK_LOCK.release()


from app.world_state import snapshot_service
snapshot_service.configure(
    ensure_campus_state_table=ensure_campus_state_table,
    ensure_space_system=ensure_space_system,
    ensure_agent_news_system=ensure_agent_news_system,
    ensure_external_information_system=ensure_external_information_system,
    ensure_world_runtime_tables=ensure_world_runtime_tables,
    get_active_environment_config=get_active_environment_config,
    get_current_day=get_current_day,
    get_world_now=get_world_now,
    active_world_branch_key=active_world_branch_key,
    canonical_json=canonical_json,
    content_checksum=content_checksum,
    load_json_text=load_json_text,
    WORLD_RUNTIME_ID=WORLD_RUNTIME_ID,
    uuid4=uuid4,
)
snapshot_table_exists = snapshot_service.snapshot_table_exists
snapshot_state_tables = snapshot_service.snapshot_state_tables
capture_objective_world_state = snapshot_service.capture_objective_world_state
decode_world_snapshot = snapshot_service.decode_world_snapshot
create_world_snapshot_record = snapshot_service.create_world_snapshot_record


SNAPSHOT_UPSERT_KEYS = snapshot_service.SNAPSHOT_UPSERT_KEYS
snapshot_row_or_error = snapshot_service.snapshot_row_or_error
insert_snapshot_rows = snapshot_service.insert_snapshot_rows
upsert_snapshot_rows = snapshot_service.upsert_snapshot_rows
restore_world_snapshot_state = snapshot_service.restore_world_snapshot_state
decode_world_branch = snapshot_service.decode_world_branch
create_world_branch_record = snapshot_service.create_world_branch_record


def decode_world_event(row):
    event = dict(row)
    event["payload"] = load_json_text(event.get("payload"), {})
    event_time = parse_world_datetime(event.get("occurred_at") or event.get("created_at"))
    event["display_time"] = event_time.strftime("%m月%d日 %H:%M") if event_time else ""
    return event




def get_world_runtime_api():
    with get_connection() as conn:
        return runtime_response(conn)


def get_current_environment_config_api():
    with get_connection() as conn:
        return {"environment_config": get_active_environment_config(conn)}


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


app.state.create_environment_config = create_environment_config_api
app.state.activate_environment_config = activate_environment_config_api


def list_world_snapshots(limit: int = 30):
    limit = max(1, min(limit, 200))
    with get_connection() as conn:
        return list_snapshots(conn, limit, decode_snapshot=decode_world_snapshot)


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



def list_world_branches():
    with get_connection() as conn:
        return list_branches(conn, decode_branch=decode_world_branch)


app.state.list_world_branches = list_world_branches


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



app.state.create_world_snapshot = create_world_snapshot_api
app.state.restore_world_snapshot = restore_world_snapshot_api
app.state.create_world_branch = create_world_branch_api
app.state.switch_world_branch = switch_world_branch_api


def list_world_action_rules():
    with get_connection() as conn:
        return read_action_rules(conn, decode_action_rule=decode_world_action_rule)


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


def list_world_delayed_effects(status: str = "", limit: int = 50):
    with get_connection() as conn:
        return read_delayed_effects(
            conn,
            status=status,
            limit=limit,
            load_json=load_json_text,
        )


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


app.state.get_world_runtime = get_world_runtime_api
app.state.get_environment_config = get_current_environment_config_api
app.state.list_environment_configs = list_environment_configs
app.state.list_action_rules = list_world_action_rules
app.state.list_action_executions = list_world_action_executions
app.state.list_delayed_effects = list_world_delayed_effects
app.state.get_world_events = get_world_events


def current_metric_value(conn, metric_name, location=""):
    return observability.current_metric_value(conn, metric_name, location, current_day=get_current_day, campus_environment=get_campus_environment)


def create_calibration_observation(payload: CalibrationObservationRequest):
    with get_connection() as conn:
        ensure_world_runtime_tables(conn)
        if payload.location and payload.location not in VALID_LOCATIONS:
            raise HTTPException(status_code=400, detail="校准观测地点不存在")
        result = observability.create_calibration_observation(conn, payload, world_now=get_world_now, json_dumps=json_dumps)
        conn.commit()
        return result


app.state.create_calibration_observation = create_calibration_observation


def get_calibration_report():
    with get_connection() as conn:
        ensure_world_runtime_tables(conn)
        result = observability.calibration_report(conn, metric_value=current_metric_value, world_now=get_world_now, json_dumps=json_dumps)
        conn.commit()
        return result


app.state.get_calibration_report = get_calibration_report


def upsert_observer_session(payload: ObserverSessionRequest):
    if payload.session_type not in {"observer", "participant", "admin"}:
        raise HTTPException(status_code=400, detail="session_type 只支持 observer/participant/admin")
    if payload.focused_location and payload.focused_location not in VALID_LOCATIONS:
        raise HTTPException(status_code=400, detail="关注地点不存在")
    for attempt in range(3):
        try:
            with get_connection() as conn:
                if payload.focused_resident_id and not get_resident(conn, payload.focused_resident_id):
                    raise HTTPException(status_code=404, detail="关注 Agent 不存在")
                result = observability.upsert_observer_session(conn, payload, world_now=get_world_now)
                conn.commit()
                return result
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 2:
                raise
            time.sleep(0.05 * (attempt + 1))


app.state.upsert_observer_session = upsert_observer_session


def start_world_runtime(authorization: Optional[str] = Header(default=None)):
    require_admin_token(authorization)
    with get_connection() as conn:
        set_simulation_state_value(conn, "world_runtime_manual_pause", "false")
        runtime = update_world_runtime_status(conn, "running")
        append_world_event(conn, "admin_world_start", "世界运行已启动", "admin 启动了校园平行世界后台运行。")
        conn.commit()
        return {"message": "世界运行已启动", "runtime": runtime}


def pause_world_runtime(authorization: Optional[str] = Header(default=None)):
    require_admin_token(authorization)
    with get_connection() as conn:
        set_simulation_state_value(conn, "world_runtime_manual_pause", "true")
        runtime = update_world_runtime_status(conn, "paused")
        append_world_event(conn, "admin_world_pause", "世界运行已暂停", "admin 暂停了校园平行世界后台运行。")
        conn.commit()
        return {"message": "世界运行已暂停", "runtime": runtime}


def run_world_tick_once(authorization: Optional[str] = Header(default=None)):
    require_admin_token(authorization)
    return {"message": "世界 tick 已完成", "tick": advance_world_tick(reason="admin")}


app.state.start_world_runtime = start_world_runtime
app.state.pause_world_runtime = pause_world_runtime
app.state.run_world_tick_once = run_world_tick_once




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


app.state.trigger_admin_world_event = trigger_admin_world_event


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
            "world_type": "multiverse_sector",
            "current_day": day,
            "locations": sorted(VALID_LOCATIONS),
            "environment": get_campus_environment(conn, day),
            "spaces": get_space_snapshot(conn, day),
            "agents": rows_to_dicts(residents),
            "residents": rows_to_dicts(residents),
            "events": rows_to_dicts(events),
            "agent_modules": get_all_agent_module_states(conn),
        }


app.state.get_state = get_state


def get_world_observer_state():
    with get_connection() as conn:
        return build_world_observer_state(
            conn,
            get_current_day=get_current_day,
            read_world_runtime=read_world_runtime,
            get_campus_environment=get_campus_environment,
            get_space_snapshot=get_space_snapshot,
            rows_to_dicts=rows_to_dicts,
            decode_world_event=decode_world_event,
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
        return paginated_memories(conn, resident_id, limit, offset, ensure_columns=ensure_memory_columns, current_day=get_current_day, rows_to_dicts=rows_to_dicts)


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


def get_space_activity_log(location: str, limit: int = 30):
    with get_connection() as conn:
        ensure_space_system(conn)
        ensure_world_runtime_tables(conn)

        snapshot = get_space_snapshot(conn)
        space_info = next((s for s in snapshot.get("spaces", []) if s.get("location") == location or s.get("name") == location), None)
        if not space_info:
            space_info = {"location": location, "name": location, "status": "开放", "crowd_percent": 50, "actual_agents": 0}

        agent_rows = conn.execute(
            """
            SELECT id, name, role, location, goal
            FROM residents
            WHERE location = ? OR location LIKE ?
            """,
            (location, f"%{location}%"),
        ).fetchall()
        current_agents = rows_to_dicts(agent_rows)

        log_rows = conn.execute(
            """
            SELECT e.id, e.day, e.slot, e.event_type, e.resident_id, e.location,
                   e.title, e.content, e.payload, e.created_at, e.occurred_at,
                   r.name AS resident_name, r.role AS resident_role
            FROM world_event_stream e
            LEFT JOIN residents r ON r.id = e.resident_id
            WHERE e.location = ? OR e.location LIKE ? OR ? LIKE ('%' || e.location || '%')
            ORDER BY e.id DESC
            LIMIT ?
            """,
            (location, f"%{location}%", location, limit),
        ).fetchall()

        return {
            "location": location,
            "space_info": space_info,
            "current_agents": current_agents,
            "activity_timeline": rows_to_dicts(log_rows),
        }


app.state.get_inventory = get_inventory
app.state.get_today_environment = get_today_environment
app.state.get_campus_spaces = get_campus_spaces
app.state.get_space_activity_log = get_space_activity_log


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


app.state.set_space_status = set_space_status
app.state.trigger_campus_event = trigger_campus_event
app.state.resolve_campus_event = resolve_campus_event


def sync_real_time():
    with get_connection() as conn:
        day = get_current_day(conn)
        values = dict(get_campus_environment(conn, day))
        values = derive_environment_from_real_time(values)
        save_environment_values(conn, day, values)
        add_event(conn, day, "real_time_sync", f"校园环境已同步真实时间：{values['real_date']} {values['real_time']}，{values['weekday']}，{values['time_slot']}。")
        conn.commit()
        return {"message": "真实时间同步成功", "environment": get_campus_environment(conn, day)}


def sync_real_weather():
    with get_connection() as conn:
        result = sync_real_weather_into_world(conn, event_type="real_weather_manual_sync")
        conn.commit()
        env = result["environment"]
        env["real_weather_raw"] = result["raw"]
        return env


app.state.sync_real_time = sync_real_time
app.state.sync_real_weather = sync_real_weather


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


app.state.set_today_environment = set_today_environment




def get_social_hierarchy():
    with get_connection() as conn:
        return build_social_hierarchy(
            conn,
            ensure_tables=ensure_social_system_tables,
            get_hierarchy_title=get_hierarchy_title,
        )


def get_agent_learning(resident_id: int):
    with get_connection() as conn:
        return list_agent_learning(conn, resident_id, ensure_tables=ensure_social_system_tables, rows_to_dicts=rows_to_dicts)


def social_communicate(payload: ChatRequest):
    with get_connection() as conn:
        ensure_social_system_tables(conn)
        result = chat_between(conn, payload.speaker_id, payload.listener_id, payload.message)
        record_learning(conn, payload.speaker_id, "communicate", "完成沟通", action_score("communicate", True), f"主动沟通：{payload.message}")
        record_learning(conn, payload.listener_id, "communicate", "回应沟通", action_score("communicate", True), f"收到沟通：{payload.message}")
        conn.commit()
        return {"type": "communication", "result": result}


def social_negotiate(payload: NegotiateRequest):
    with get_connection() as conn:
        return negotiate_between(conn, payload.initiator_id, payload.target_id, payload.topic, payload.proposal)


def social_collaborate(payload: CollaborateRequest):
    with get_connection() as conn:
        return create_collaboration(conn, payload.leader_id, payload.member_ids, payload.title, payload.goal)


def social_compete(payload: CompeteRequest):
    with get_connection() as conn:
        return create_competition(conn, payload.participant_ids, payload.title, payload.metric)


app.state.social_communicate = social_communicate
app.state.social_negotiate = social_negotiate
app.state.social_collaborate = social_collaborate
app.state.social_compete = social_compete


def get_long_term_goals(resident_id: int):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        return list_long_term_goals(conn, resident_id, ensure_tables=ensure_social_system_tables, rows_to_dicts=rows_to_dicts)


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



def create_long_term_goal(payload: LongTermGoalRequest):
    with get_connection() as conn:
        try:
            result = social_actions.create_goal(conn, payload, get_resident=get_resident, ensure_tables=ensure_social_system_tables, current_day=get_current_day, seed_goals=seed_multiscale_goals, add_event=add_event)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        conn.commit()
        return result


def get_social_relationships(resident_id: int):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        return list_relationships(conn, resident_id, ensure_tables=ensure_social_system_tables, get_relationship_dynamics=get_relationship_dynamics)



def _build_life_course_overview(conn, resident_id, from_day=None, to_day=None, limit=240):
    return build_life_course_overview(conn, resident_id, from_day, to_day, limit, get_resident=get_resident, ensure_tables=ensure_social_system_tables, current_day=get_current_day, load_json=load_json_text, rows_to_dicts=rows_to_dicts, active_branch=active_world_branch_key, infer_relationship=infer_emergent_relationship)

def get_agent_life_course_overview(resident_id: int, from_day: Optional[int] = None, to_day: Optional[int] = None, limit: int = 240):
    with get_connection() as conn:
        return lifecycle_overview(conn, resident_id, build_overview=_build_life_course_overview, from_day=from_day, to_day=to_day, limit=limit)


def get_agent_life_course_events(resident_id: int, from_day: Optional[int] = None, to_day: Optional[int] = None, limit: int = 240):
    with get_connection() as conn:
        return lifecycle_events(conn, resident_id, build_overview=_build_life_course_overview, from_day=from_day, to_day=to_day, limit=limit)


def get_agent_life_course_turning_points(resident_id: int, limit: int = 12):
    with get_connection() as conn:
        return lifecycle_turning_points(conn, resident_id, build_overview=_build_life_course_overview, limit=limit)


def get_agent_life_course_relationships(resident_id: int):
    with get_connection() as conn:
        return lifecycle_relationships(conn, resident_id, build_overview=_build_life_course_overview)


def get_agent_life_course_groups(resident_id: int):
    with get_connection() as conn:
        return lifecycle_groups(conn, resident_id, build_overview=_build_life_course_overview)


app.state.life_course_overview = get_agent_life_course_overview
app.state.life_course_events = get_agent_life_course_events
app.state.life_course_turning_points = get_agent_life_course_turning_points
app.state.life_course_relationships = get_agent_life_course_relationships
app.state.life_course_groups = get_agent_life_course_groups




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


def get_agent_timeline(resident_id: int, limit: int = 30, offset: int = 0):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        ensure_social_system_tables(conn)
        return fetch_agent_timeline(conn, resident_id, limit=limit, offset=offset)


def fetch_agent_simulation_logs(conn, resident_id, limit=12):
    return fetch_simulation_logs(conn, resident_id, limit=limit, load_json=load_json_text)


def get_agent_simulation_logs(resident_id: int, limit: int = 12):
    with get_connection() as conn:
        if not get_resident(conn, resident_id):
            raise HTTPException(status_code=404, detail="Agent 不存在")
        ensure_social_system_tables(conn)
        return fetch_agent_simulation_logs(conn, resident_id, limit=limit)


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


app.state.get_social_graph = get_agent_social_graph
app.state.get_timeline = get_agent_timeline
app.state.get_simulation_logs = get_agent_simulation_logs
app.state.get_profile_activity = get_agent_profile_activity


def get_campus_organizations():
    with get_connection() as conn:
        return list_organizations(conn, ensure_tables=ensure_social_system_tables, load_json=load_json_text)


def get_world_v2_organizations(world_key: str = "default"):
    with get_connection() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM world_organizations WHERE world_key = ? AND status = 'active' ORDER BY id",
                (world_key,),
            ).fetchall()
            if rows:
                return rows_to_dicts(rows)
        except Exception:
            pass
        return list_organizations(conn, ensure_tables=ensure_social_system_tables, load_json=load_json_text)


def get_world_v2_spaces(world_key: str = "default"):
    with get_connection() as conn:
        try:
            nodes = conn.execute(
                "SELECT node_id, name, node_type, category, crowd_ratio FROM spatial_nodes WHERE world_key = ? ORDER BY node_id LIMIT 100",
                (world_key,),
            ).fetchall()
            if nodes:
                return {"world_key": world_key, "spaces": rows_to_dicts(nodes)}
        except Exception:
            pass
        return get_space_snapshot(conn)


def get_world_v2_environment(world_key: str = "default"):
    with get_connection() as conn:
        try:
            row = conn.execute(
                "SELECT * FROM world_environment_states WHERE world_key = ? ORDER BY day DESC, id DESC LIMIT 1",
                (world_key,),
            ).fetchone()
            if row:
                return dict(row)
        except Exception:
            pass
        return get_campus_environment(conn)


def get_group_goals():
    with get_connection() as conn:
        return list_group_goals(conn, ensure_tables=ensure_social_system_tables, rows_to_dicts=rows_to_dicts)


app.state.get_social_hierarchy = get_social_hierarchy
app.state.get_social_relationships = get_social_relationships
app.state.get_organizations = get_campus_organizations
app.state.get_world_v2_organizations = get_world_v2_organizations
app.state.get_world_v2_spaces = get_world_v2_spaces
app.state.get_world_v2_environment = get_world_v2_environment
app.state.get_groups = get_group_goals



def create_group_goal(payload: GroupGoalRequest):
    with get_connection() as conn:
        try:
            result = social_actions.create_group(conn, payload, ensure_tables=ensure_social_system_tables, current_day=get_current_day, json_dumps=json_dumps, evolve_relationship=evolve_relationship, add_event=add_event)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        conn.commit()
        return result


app.state.create_long_term_goal = create_long_term_goal
app.state.create_group_goal = create_group_goal


def tool_move(payload: MoveRequest):
    with get_connection() as conn:
        result = social_actions.move(conn, payload, module_state=get_agent_module_state, action_cost=calculate_action_cost, ensure_affordable=ensure_action_affordable, ensure_destination=assert_destination_available, move_resident=move_resident, advance_goal=advance_personal_goal, update_profile=update_agent_profile_after_action)
        conn.commit()
        return result


def tool_chat(payload: ChatRequest):
    with get_connection() as conn:
        result = social_actions.chat(conn, payload, module_state=get_agent_module_state, action_cost=calculate_action_cost, ensure_affordable=ensure_action_affordable, chat_between=chat_between, evolve_relationship=evolve_relationship, advance_goal=advance_personal_goal, update_profile=update_agent_profile_after_action)
        conn.commit()
        return result


def tool_buy_sell(payload: BuySellRequest):
    with get_connection() as conn:
        result = social_actions.buy_sell(conn, payload, module_state=get_agent_module_state, action_cost=calculate_action_cost, ensure_affordable=ensure_action_affordable, transact=buy_sell, advance_goal=advance_personal_goal, update_profile=update_agent_profile_after_action)
        conn.commit()
        return result


app.state.tool_move = tool_move
app.state.tool_chat = tool_chat
app.state.tool_buy_sell = tool_buy_sell


def get_policies():
    with get_connection() as conn:
        return policy_actions.list_policies(conn, rows_to_dicts=rows_to_dicts)


app.state.get_policies = get_policies


def submit_policy(payload: PolicyRequest):
    with get_connection() as conn:
        try:
            result = policy_actions.submit(conn, payload, get_resident=get_resident, module_state=get_agent_module_state, action_cost=calculate_action_cost, ensure_affordable=ensure_action_affordable, current_day=get_current_day, add_event=add_event, add_memory=add_memory, update_profile=update_agent_profile_after_action)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        conn.commit()
        return result


def vote_policy(payload: VotePolicyRequest):
    with get_connection() as conn:
        try:
            result = policy_actions.vote(conn, payload, get_resident=get_resident, action_cost=calculate_action_cost, ensure_affordable=ensure_action_affordable, current_day=get_current_day, add_event=add_event, add_memory=add_memory, update_profile=update_agent_profile_after_action)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        conn.commit()
        return result


def close_policy(policy_id: int):
    with get_connection() as conn:
        try:
            result = policy_actions.close(conn, policy_id, current_day=get_current_day, add_event=add_event)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        conn.commit()
        return result


def daily_reflect():
    with get_connection() as conn:
        result = policy_actions.reflect(conn, current_day=get_current_day, ask_llm=ask_llm, add_memory=add_memory, add_event=add_event)
        conn.commit()
        return result

app.state.submit_policy = submit_policy
app.state.vote_policy = vote_policy
app.state.close_policy = close_policy
app.state.daily_reflect = daily_reflect


def newspaper_today():
    with get_connection() as conn:
        day = get_current_day(conn)
        env = get_campus_environment(conn, day)
        events = conn.execute(
            "SELECT event_type, description, created_at FROM city_events WHERE day = ? ORDER BY id DESC LIMIT 30",
            (day,),
        ).fetchall()
        return {
            "title": f"World2 世界日报 第 {day} 天",
            "environment": env,
            "events": rows_to_dicts(events),
            "agent_modules": get_all_agent_module_states(conn),
        }


def summarize_action_for_news(execution):
    result = execution.get("result") if isinstance(execution, dict) else None
    if isinstance(result, dict):
        return str(result.get("description") or result.get("message") or execution.get("action") or "完成了一次校园行动")
    return str(execution.get("action") or "完成了一次校园行动")



def collect_campus_news_candidates(conn, day, source_slot, limit=60):
    return collect_candidates(conn, day, source_slot, limit, active_branch=active_world_branch_key, load_json=load_json_text, classify=classify_campus_news_candidate)


def publish_agent_news(conn, day, results):
    return publish_news_service(conn, day, results, ensure_system=ensure_agent_news_system, choice=random.sample, summarize_action=summarize_action_for_news, ask_llm=ask_llm)


def write_agent_daily_diaries(conn, day, results=None, replace_existing=False):
    return write_daily_diaries(conn, day, results, replace_existing, ask_llm=ask_llm, add_memory=add_memory)


def backfill_agent_daily_diaries(day: Optional[int] = None, rewrite: bool = False):
    with get_connection() as conn:
        target_day = day or get_current_day(conn)
        created = write_agent_daily_diaries(conn, target_day, replace_existing=rewrite)
        conn.commit()
        return {"day": target_day, "created": len(created), "agent_ids": created}


app.state.backfill_agent_daily_diaries = backfill_agent_daily_diaries




def ai_newspaper_today():
    data = newspaper_today()
    prompt = f"请把下面平行世界数据写成一份简短维度日报，分为标题、环境、主要事件、趋势判断：{json_dumps(data, ensure_ascii=False)}"
    return {"day": data["title"], "newspaper": ask_llm(prompt), "source": data}


EXTERNAL_RSS_SOURCES = [
    (
        "36Kr RSS",
        "https://36kr.com/feed",
    ),
    (
        "Bing News RSS",
        "https://www.bing.com/news/search?q=(AI%20OR%20university%20OR%20education%20OR%20employment)&format=rss&setlang=zh-CN&cc=CN",
    ),
    (
        "ITHome RSS",
        "https://www.ithome.com/rss/",
    ),
    (
        "Google News RSS",
        "https://news.google.com/rss/search?q=(AI%20OR%20%E5%A4%A7%E5%AD%A6%20OR%20%E6%95%99%E8%82%B2%20OR%20%E5%B0%B1%E4%B8%9A)&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
]



def fetch_external_information(limit=5):
    return fetch_information(EXTERNAL_RSS_SOURCES, adapter_factory=FixedRSSAdapter, logger=logger, limit=limit)


def deliver_external_information(conn, information, resident_id, channel, relevance=65, credibility=80, distortion_note="", source_resident_id=None):
    return deliver_information(conn, information, resident_id, channel, relevance, credibility, distortion_note, source_resident_id, ensure_system=ensure_external_information_system, ensure_profile=ensure_profile_meta, load_json=load_json_text, json_dumps=json_dumps, current_day=get_current_day, add_memory=add_memory)


def seed_external_information_recipients(conn, information):
    return seed_recipients(conn, information, deliver=deliver_external_information)


def spread_external_information(conn, limit=12):
    return spread_information(conn, limit, ensure_system=ensure_external_information_system, deliver=deliver_external_information, choice=random.choice)


def sync_external_information_into_world(conn, event_type="external_information_manual_sync", tick_id=None, day=None, slot=None):
    return sync_into_world(conn, event_type, tick_id, day, slot, ensure_system=ensure_external_information_system, ensure_runtime=ensure_world_runtime_tables, fetch=fetch_external_information, seed=seed_external_information_recipients, add_event=add_event, current_day=get_current_day, append_event=append_world_event)


def maybe_auto_sync_external_information(conn, world_time, tick_id=None, day=None, slot=None):
    return maybe_auto_sync(conn, world_time, tick_id, day, slot, ensure_runtime=ensure_world_runtime_tables, parse_time=parse_world_datetime, interval_seconds=WORLD_EXTERNAL_SYNC_INTERVAL_SECONDS, sync=sync_external_information_into_world, append_event=append_world_event, logger=logger)


def sync_external_information():
    with get_connection() as conn:
        try:
            result = sync_external_information_into_world(conn, event_type="external_information_manual_sync")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"外部资讯同步失败：{exc}")
        conn.commit()
        return result


app.state.sync_external_information = sync_external_information


def get_external_information():
    with get_connection() as conn:
        ensure_external_information_system(conn)
        rows = conn.execute(
            "SELECT * FROM external_information ORDER BY id DESC LIMIT 20"
        ).fetchall()
        return {"items": rows_to_dicts(rows)}


app.state.get_newspaper_today = newspaper_today
app.state.get_agent_newspaper_posts = agent_newspaper_posts
app.state.get_ai_newspaper_today = ai_newspaper_today
app.state.get_external_information = get_external_information


def decide_agent(resident_id: int):
    with get_connection() as conn:
        return decide_agent_action(conn, resident_id)


def act_agent(resident_id: int):
    with get_connection() as conn:
        decision_data = decide_agent_action(conn, resident_id)
        result = execute_decision(conn, resident_id, decision_data["decision"])
        return {"decision": decision_data, "execution": result}


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


app.state.decide_agent = decide_agent
app.state.act_agent = act_agent
app.state.act_all_agents = act_all_agents
