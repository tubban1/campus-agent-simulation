from __future__ import annotations

import json
import hashlib
from app.json_utils import json_dumps
import re
from datetime import datetime, timedelta, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ
from html import unescape
from urllib.parse import urlparse

from app.resilience.service import (
    create_shock,
    process_resilience_runtime,
)
from app.external_world.adapters import get_adapter


CATALOG_VERSION = "external-event-catalog-v1"
TRANSFORM_VERSION = "external-transform-v1"
IMPACT_RULE_VERSION = "external-impact-rules-v1"

EVENT_CATALOG = (
    ("weather.condition_changed", "weather", 1, 0),
    ("weather.warning_issued", "weather", 1, 1),
    ("campus.notice_published", "campus", 0, 0),
    ("campus.facility_closed", "campus", 1, 1),
    ("transport.service_changed", "transport", 1, 0),
    ("economy.price_changed", "economy", 1, 0),
    ("economy.supply_disrupted", "economy", 1, 1),
    ("labor.opportunity_changed", "labor", 1, 0),
    ("policy.rule_changed", "policy", 1, 1),
    ("health.risk_changed", "health", 1, 1),
    ("news.public_event_reported", "news", 0, 0),
)

SOURCE_SEEDS = (
    (
        "manual-campus",
        "校园人工核验来源",
        "manual",
        "",
        "manual-v1",
        0.9,
        ["campus.notice_published", "campus.facility_closed"],
        86400,
        604800,
        "internal",
        {},
    ),
    (
        "synthetic-research",
        "合成实验来源",
        "synthetic",
        "",
        "synthetic-v1",
        1.0,
        [item[0] for item in EVENT_CATALOG],
        86400,
        31536000,
        "research",
        {},
    ),
    (
        "open-meteo-beijing",
        "Open-Meteo 北京天气",
        "weather",
        "https://api.open-meteo.com",
        "open-meteo-v1",
        0.85,
        ["weather.condition_changed", "weather.warning_issued"],
        1800,
        7200,
        "simulation",
        {
            "latitude": 40.0062,
            "longitude": 116.3269,
            "timezone": "Asia/Shanghai",
            "location_label": "北京（清华大学）",
        },
    ),
    (
        "google-news-public",
        "Google News 公共资讯",
        "rss",
        "https://news.google.com",
        "fixed-rss-v1",
        0.6,
        ["news.public_event_reported"],
        7200,
        21600,
        "simulation",
        {
            "feed_url": (
                "https://news.google.com/rss/search?"
                "q=(AI%20OR%20%E5%A4%A7%E5%AD%A6%20OR%20%E6%95%99%E8%82%B2"
                "%20OR%20%E5%B0%B1%E4%B8%9A)&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
            ),
            "limit": 5,
        },
    ),
)

IMPACT_RULES = (
    ("weather-condition", "weather.condition_changed", "weather", "campus", "travel_cost", "trigger_shock", "", 0.45, 0, 0, {"shock_key": "heavy-rain"}),
    ("weather-warning", "weather.warning_issued", "weather", "campus", "travel_cost", "trigger_shock", "", 0.65, 1, 1, {"shock_key": "heavy-rain"}),
    ("facility-closure", "campus.facility_closed", "facility", "space", "official_access", "trigger_shock", "status", 0.65, 1, 1, {"shock_key": "facility-closure"}),
    ("transport-change", "transport.service_changed", "transport", "campus", "travel_cost", "trigger_shock", "", 0.55, 0, 0, {"shock_key": "heavy-rain"}),
    ("price-change", "economy.price_changed", "price", "market", "price", "trigger_shock", "multiplier", 0.55, 0, 0, {"shock_key": "price-shock"}),
    ("supply-disruption", "economy.supply_disrupted", "supply", "sector", "supply", "trigger_shock", "multiplier", 0.65, 1, 1, {"shock_key": "supply-shortage"}),
    ("labor-change", "labor.opportunity_changed", "employment", "role", "employment", "trigger_shock", "multiplier", 0.55, 0, 0, {"shock_key": "employment-shock"}),
    ("policy-change", "policy.rule_changed", "policy", "campus", "policy", "trigger_shock", "event", 0.75, 1, 1, {"shock_key": "policy-shock"}),
    ("health-risk", "health.risk_changed", "health", "campus", "health_risk", "trigger_shock", "probability", 0.75, 1, 1, {"shock_key": "public-health-event"}),
)


def _json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _time(value=None):
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    parsed = parse_world_datetime(value)
    if parsed:
        return parsed
    raise ValueError(f"无法解析的时间格式: {value}")


def _hash(value):
    return hashlib.sha256(
        value.encode("utf-8") if isinstance(value, str) else value
    ).hexdigest()


def _table_exists(conn, name):
    return bool(conn.execute(f"PRAGMA table_info({name})").fetchall())


def external_world_available(conn):
    return _table_exists(conn, "external_sources")


def _clean_text(value, limit=4000):
    text = unescape(str(value or ""))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(
        r"(?i)(ignore|disregard)\s+(all\s+)?(previous|prior)\s+instructions?",
        "[removed untrusted instruction]",
        text,
    )
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _validate_base_url(base_url):
    if not base_url:
        return
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("外部来源 base_url 必须是公开 HTTPS 地址")
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        raise ValueError("外部来源不允许访问本机或内网地址")
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host):
        raise ValueError("外部来源不允许访问私有网络地址")


def register_source(
    conn,
    *,
    source_key,
    name,
    source_type,
    adapter_key,
    base_url="",
    trust_prior=0.5,
    allowed_event_types=None,
    poll_interval_seconds=3600,
    stale_after_seconds=7200,
    license_note="",
    allowed_use="simulation",
    retention_days=30,
    sensitivity="public",
    config=None,
):
    _validate_base_url(base_url)
    existing = conn.execute(
        "SELECT * FROM external_sources WHERE source_key = ?", (source_key,)
    ).fetchone()
    if existing:
        return dict(existing)
    cursor = conn.execute(
        """
        INSERT INTO external_sources
        (source_key, name, source_type, base_url, adapter_key, trust_prior,
         allowed_event_types_json, poll_interval_seconds, stale_after_seconds,
         license_note, allowed_use, retention_days, sensitivity, config_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_key,
            name,
            source_type,
            base_url,
            adapter_key,
            float(trust_prior),
            _json(allowed_event_types or []),
            int(poll_interval_seconds),
            int(stale_after_seconds),
            license_note,
            allowed_use,
            int(retention_days),
            sensitivity,
            _json(config or {}),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM external_sources WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )


def begin_sync_run(conn, source_id, run_key, started_at=None, cursor_before="", leader_key=""):
    existing = conn.execute(
        "SELECT * FROM external_sync_runs WHERE run_key = ?", (run_key,)
    ).fetchone()
    if existing:
        return dict(existing)
    cursor = conn.execute(
        """
        INSERT INTO external_sync_runs
        (run_key, source_id, started_at, cursor_before, leader_key)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_key, source_id, _time(started_at).isoformat(), cursor_before, leader_key),
    )
    return dict(
        conn.execute(
            "SELECT * FROM external_sync_runs WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )


def finish_sync_run(conn, run_id, *, status, finished_at=None, cursor_after="", error_summary=""):
    run = conn.execute(
        "SELECT * FROM external_sync_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if not run:
        raise ValueError("同步运行不存在")
    if run["status"] != "running":
        return dict(run)
    committed_cursor = cursor_after if status in {"success", "partial"} else run["cursor_before"]
    conn.execute(
        """
        UPDATE external_sync_runs
        SET status = ?, finished_at = ?, cursor_after = ?, error_summary = ?
        WHERE id = ?
        """,
        (
            status,
            _time(finished_at).isoformat(),
            committed_cursor,
            _clean_text(error_summary, 500),
            run_id,
        ),
    )
    conn.execute(
        """
        UPDATE external_sources
        SET last_attempt_at = ?,
            last_success_at = CASE WHEN ? IN ('success', 'partial')
                                   THEN ? ELSE last_success_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            _time(finished_at).isoformat(),
            status,
            _time(finished_at).isoformat(),
            run["source_id"],
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM external_sync_runs WHERE id = ?", (run_id,)
        ).fetchone()
    )


def _acquire_source_lock(conn, source_id, owner_key, now, ttl_seconds):
    expires = now + timedelta(seconds=max(30, int(ttl_seconds)))
    conn.execute(
        """
        INSERT OR IGNORE INTO external_source_locks
        (source_id, owner_key, acquired_at, expires_at)
        VALUES (?, ?, ?, ?)
        """,
        (source_id, owner_key, now.isoformat(), expires.isoformat()),
    )
    lock = conn.execute(
        "SELECT * FROM external_source_locks WHERE source_id = ?", (source_id,)
    ).fetchone()
    if lock and lock["owner_key"] != owner_key:
        if _time(lock["expires_at"]) > now:
            raise RuntimeError("该外部来源已有同步任务持有 leader lock")
        conn.execute(
            """
            UPDATE external_source_locks
            SET owner_key = ?, acquired_at = ?, expires_at = ?
            WHERE source_id = ? AND expires_at <= ?
            """,
            (
                owner_key,
                now.isoformat(),
                expires.isoformat(),
                source_id,
                now.isoformat(),
            ),
        )
    lock = conn.execute(
        "SELECT owner_key FROM external_source_locks WHERE source_id = ?",
        (source_id,),
    ).fetchone()
    if not lock or lock["owner_key"] != owner_key:
        raise RuntimeError("无法取得外部来源 leader lock")


def _release_source_lock(conn, source_id, owner_key):
    conn.execute(
        """
        DELETE FROM external_source_locks
        WHERE source_id = ? AND owner_key = ?
        """,
        (source_id, owner_key),
    )


def ingest_raw_observation(
    conn,
    *,
    source_id,
    source_record_id,
    payload,
    observed_at,
    parser_version,
    sync_run_id=None,
    request_fingerprint="",
    http_status=200,
    content_type="application/json",
    ingested_at=None,
):
    source = conn.execute(
        "SELECT * FROM external_sources WHERE id = ? AND enabled = 1", (source_id,)
    ).fetchone()
    if not source:
        raise ValueError("来源不存在或未启用")
    payload_text = _json(payload)
    if len(payload_text.encode("utf-8")) > 1_000_000:
        raise ValueError("原始响应超过 1 MB 限制")
    content_hash = _hash(payload_text)
    existing = conn.execute(
        """
        SELECT * FROM external_raw_observations
        WHERE source_id = ? AND source_record_id = ? AND content_hash = ?
        """,
        (source_id, source_record_id, content_hash),
    ).fetchone()
    if existing:
        if sync_run_id:
            conn.execute(
                """
                UPDATE external_sync_runs
                SET duplicate_count = duplicate_count + 1,
                    request_count = request_count + 1
                WHERE id = ?
                """,
                (sync_run_id,),
            )
        return {**dict(existing), "duplicate": True}
    validation_errors = []
    if http_status < 200 or http_status >= 300:
        validation_errors.append(f"http_status:{http_status}")
    allowed_mime = ("application/json", "application/xml", "text/xml", "text/plain", "application/rss+xml")
    if not any(content_type.startswith(item) for item in allowed_mime):
        validation_errors.append(f"content_type:{content_type}")
    status = "valid" if not validation_errors else "quarantined"
    cursor = conn.execute(
        """
        INSERT INTO external_raw_observations
        (source_id, source_record_id, request_fingerprint, content_hash,
         http_status, content_type, payload_json, observed_at, ingested_at,
         parser_version, sync_run_id, validation_status,
         validation_errors_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            source_record_id,
            request_fingerprint or _hash(f"{source_id}:{source_record_id}"),
            content_hash,
            int(http_status),
            content_type,
            payload_text,
            _time(observed_at).isoformat(),
            _time(ingested_at).isoformat(),
            parser_version,
            sync_run_id,
            status,
            _json(validation_errors),
        ),
    )
    if sync_run_id:
        conn.execute(
            """
            UPDATE external_sync_runs
            SET raw_count = raw_count + 1, request_count = request_count + 1,
                error_count = error_count + ?
            WHERE id = ?
            """,
            (int(bool(validation_errors)), sync_run_id),
        )
    row = dict(
        conn.execute(
            "SELECT * FROM external_raw_observations WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    )
    row["duplicate"] = False
    return row


def seed_external_world(conn):
    catalog_created = source_created = rule_created = 0
    # Older deployments created this project-owned source under a Chengdu key.
    # Preserve its ID and audit history while moving its configuration to the
    # actual campus location.  If both keys somehow exist, retire the legacy
    # source so the runtime cannot ingest two conflicting city forecasts.
    legacy_weather = conn.execute(
        "SELECT id FROM external_sources WHERE source_key = ?",
        ("open-meteo-chengdu",),
    ).fetchone()
    beijing_weather = conn.execute(
        "SELECT id FROM external_sources WHERE source_key = ?",
        ("open-meteo-beijing",),
    ).fetchone()
    beijing_config = _json({
        "latitude": 40.0062,
        "longitude": 116.3269,
        "timezone": "Asia/Shanghai",
        "location_label": "北京（清华大学）",
    })
    if legacy_weather and not beijing_weather:
        conn.execute(
            """
            UPDATE external_sources
            SET source_key = ?, name = ?, config_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("open-meteo-beijing", "Open-Meteo 北京天气", beijing_config, legacy_weather["id"]),
        )
    elif beijing_weather:
        conn.execute(
            "UPDATE external_sources SET name = ?, config_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            ("Open-Meteo 北京天气", beijing_config, beijing_weather["id"]),
        )
        if legacy_weather:
            conn.execute(
                "UPDATE external_sources SET enabled = 0, status = 'retired', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (legacy_weather["id"],),
            )
    # `external_information` is the live audience projection, not the audit
    # ledger (raw observations/events retain their history).  Remove obsolete
    # Chengdu forecast cards so a location migration cannot leave misleading
    # weather in the campus dashboard or agent work memories.
    if _table_exists(conn, "external_information"):
        conn.execute(
            "DELETE FROM external_information WHERE title LIKE ? OR source_name = ?",
            ("成都天气更新：%", "Open-Meteo 成都天气"),
        )
    for event_type, category, objective, high_impact in EVENT_CATALOG:
        before = conn.execute(
            "SELECT event_type FROM external_event_catalog WHERE event_type = ?",
            (event_type,),
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO external_event_catalog
            (event_type, category, objective_impact_allowed, high_impact,
             schema_version)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_type, category, objective, high_impact, CATALOG_VERSION),
        )
        catalog_created += int(before is None)
    for key, name, source_type, base_url, adapter, trust, allowed, poll, stale, use, config in SOURCE_SEEDS:
        before = conn.execute(
            "SELECT id FROM external_sources WHERE source_key = ?", (key,)
        ).fetchone()
        source = register_source(
            conn,
            source_key=key,
            name=name,
            source_type=source_type,
            adapter_key=adapter,
            base_url=base_url,
            trust_prior=trust,
            allowed_event_types=allowed,
            poll_interval_seconds=poll,
            stale_after_seconds=stale,
            license_note="project-owned",
            allowed_use=use,
            retention_days=365,
            config=config,
        )
        source_created += int(before is None)
        review = conn.execute(
            "SELECT id FROM external_governance_reviews WHERE source_id = ?",
            (source["id"],),
        ).fetchone()
        if not review and source_type in {"manual", "synthetic"}:
            conn.execute(
                """
                INSERT INTO external_governance_reviews
                (review_key, source_id, license_approved, purpose_approved,
                 retention_approved, privacy_approved, reviewer, decision,
                 notes, reviewed_at)
                VALUES (?, ?, 1, 1, 1, 1, 'system-seed', 'approved',
                        'Project-owned source', ?)
                """,
                (f"seed-review:{source['id']}", source["id"], _time().isoformat()),
            )
    for rule in IMPACT_RULES:
        key, event_type, impact_type, target_type, state_key, operation, unit, minimum, high, verify, parameters = rule
        before = conn.execute(
            "SELECT id FROM external_impact_rules WHERE rule_key = ?", (key,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO external_impact_rules
            (rule_key, event_type, impact_type, target_type, state_key,
             operation, unit, min_confidence, high_impact,
             requires_verification, parameters_json, rule_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                event_type,
                impact_type,
                target_type,
                state_key,
                operation,
                unit,
                minimum,
                high,
                verify,
                _json(parameters),
                IMPACT_RULE_VERSION,
            ),
        )
        rule_created += int(before is None)
    conn.execute(
        """
        INSERT OR IGNORE INTO external_runtime_modes
        (branch_key, mode, simulation_seed) VALUES ('main', 'live', 0)
        """
    )
    return {
        "catalog": len(EVENT_CATALOG),
        "catalog_created": catalog_created,
        "sources": len(SOURCE_SEEDS),
        "sources_created": source_created,
        "impact_rules": len(IMPACT_RULES),
        "impact_rules_created": rule_created,
    }


def sync_registered_source(conn, source_id, now=None):
    now = _time(now)
    source = conn.execute(
        """
        SELECT * FROM external_sources
        WHERE id = ? AND enabled = 1 AND status = 'active'
        """,
        (source_id,),
    ).fetchone()
    if not source:
        raise ValueError("外部来源不存在或未启用")
    config = _load(source["config_json"], {})
    config["timeout_seconds"] = source["timeout_seconds"]
    owner_key = f"sync:{source_id}:{now.isoformat()}"
    _acquire_source_lock(
        conn,
        source_id,
        owner_key,
        now,
        max(30, int(source["timeout_seconds"]) * 4),
    )
    run = None
    try:
        run = begin_sync_run(
            conn,
            source_id,
            owner_key,
            now,
            leader_key=owner_key,
        )
        records = get_adapter(source["adapter_key"]).fetch(config)
        event_ids = []
        for record in records:
            raw = ingest_raw_observation(
                conn,
                source_id=source_id,
                source_record_id=record["source_record_id"],
                payload=record["payload"],
                observed_at=record.get("observed_at") or now,
                ingested_at=now,
                parser_version=source["adapter_key"],
                sync_run_id=run["id"],
                request_fingerprint=_hash(
                    f"{source['source_key']}:{record['source_record_id']}"
                ),
            )
            if raw["duplicate"] or raw["validation_status"] != "valid":
                continue
            payload = record["payload"]
            if source["source_type"] == "weather":
                rainfall = float(payload.get("rainfall", 0) or 0)
                event = normalize_external_event(
                    conn,
                    raw_observation_id=raw["id"],
                    event_key=f"weather:{raw['content_hash']}",
                    event_type="weather.condition_changed",
                    title=f"{config.get('location_label', '校园')}天气更新：{payload.get('weather', '未知')}",
                    summary=(
                        f"气温 {payload.get('temperature')}℃，"
                        f"降雨指数 {int(rainfall)}。"
                    ),
                    occurred_at=record.get("observed_at") or now,
                    effective_from=now,
                    expires_at=now
                    + timedelta(seconds=int(source["stale_after_seconds"])),
                    campus_scope={"campus": "main"},
                    affected_spaces=["操场"] if rainfall > 0 else [],
                    magnitude=min(1.0, rainfall / 100),
                    direction="decrease" if rainfall > 0 else "neutral",
                    unit="rainfall_index",
                    severity=min(1.0, rainfall / 100),
                    confidence=source["trust_prior"],
                    payload=payload,
                )
            else:
                event = normalize_external_event(
                    conn,
                    raw_observation_id=raw["id"],
                    event_key=f"public-report:{raw['content_hash']}",
                    event_type="news.public_event_reported",
                    title=payload.get("title") or "公共资讯",
                    summary=payload.get("summary") or payload.get("title") or "",
                    occurred_at=now,
                    published_at=now,
                    effective_from=now,
                    expires_at=now
                    + timedelta(seconds=int(source["stale_after_seconds"])),
                    confidence=source["trust_prior"],
                    payload=payload,
                )
                if not event.get("duplicate"):
                    _project_news_event_to_information(conn, event, payload, source["name"])
            event_ids.append(event["id"])
        conn.execute(
            """
            UPDATE external_sync_runs
            SET new_event_count = ?, error_count = error_count
            WHERE id = ?
            """,
            (len(event_ids), run["id"]),
        )
        completed = finish_sync_run(
            conn,
            run["id"],
            status="success",
            finished_at=now,
            cursor_after=records[-1]["source_record_id"] if records else "",
        )
        return {
            "sync_run": completed,
            "record_count": len(records),
            "event_ids": event_ids,
        }
    except Exception as exc:
        if run:
            finish_sync_run(
                conn,
                run["id"],
                status="failed",
                finished_at=now,
                error_summary=f"{type(exc).__name__}: {exc}",
            )
        raise
    finally:
        _release_source_lock(conn, source_id, owner_key)


def normalize_external_event(
    conn,
    *,
    raw_observation_id,
    event_key,
    event_type,
    title,
    summary,
    occurred_at,
    published_at=None,
    effective_from=None,
    effective_to=None,
    expires_at=None,
    geo_scope=None,
    campus_scope=None,
    affected_spaces=None,
    affected_roles=None,
    affected_organizations=None,
    affected_economic_sectors=None,
    magnitude=None,
    direction="neutral",
    unit="",
    severity=0,
    novelty=0,
    confidence=None,
    verification_state="unverified",
    payload=None,
    semantic_key="",
    correction_of=None,
    replaces_event_id=None,
):
    existing = conn.execute(
        "SELECT * FROM external_events WHERE event_key = ?", (event_key,)
    ).fetchone()
    if existing:
        return {**dict(existing), "duplicate": True}
    raw = conn.execute(
        """
        SELECT raw.*, source.trust_prior, source.allowed_event_types_json,
               source.name AS source_name, source.base_url AS source_base_url
        FROM external_raw_observations raw
        JOIN external_sources source ON source.id = raw.source_id
        WHERE raw.id = ?
        """,
        (raw_observation_id,),
    ).fetchone()
    if not raw or raw["validation_status"] != "valid":
        raise ValueError("只有校验通过的原始观测可以标准化")
    catalog = conn.execute(
        "SELECT * FROM external_event_catalog WHERE event_type = ? AND status = 'active'",
        (event_type,),
    ).fetchone()
    if not catalog:
        raise ValueError("事件类型不在版本化目录中")
    allowed = _load(raw["allowed_event_types_json"], [])
    if allowed and event_type not in allowed:
        raise ValueError("来源未获准生成该事件类型")
    occurred = _time(occurred_at)
    published = _time(published_at or occurred)
    effective = _time(effective_from or occurred)
    expiry = _time(expires_at or (effective + timedelta(hours=6)))
    if expiry <= effective:
        raise ValueError("expires_at 必须晚于 effective_from")
    clean_title = _clean_text(title, 240)
    clean_summary = _clean_text(summary, 2000)
    scope = campus_scope or {}
    fingerprint = _hash(
        semantic_key
        or _json(
            {
                "event_type": event_type,
                "title": clean_title.lower(),
                "scope": scope,
                "hour": occurred.replace(minute=0, second=0, microsecond=0).isoformat(),
            }
        )
    )
    base_confidence = float(confidence if confidence is not None else raw["trust_prior"])
    age_hours = max(
        0.0, (_time(raw["ingested_at"]) - _time(raw["observed_at"])).total_seconds() / 3600
    )
    freshness = max(0.5, 1 - age_hours / 168)
    computed_confidence = max(0.0, min(1.0, base_confidence * freshness))
    cursor = conn.execute(
        """
        INSERT INTO external_events
        (event_key, event_type, title, summary, source_id,
         raw_observation_id, source_record_id, semantic_fingerprint,
         occurred_at, published_at, observed_at, ingested_at,
         effective_from, effective_to, expires_at, geo_scope_json,
         campus_scope_json, affected_spaces_json, affected_roles_json,
         affected_organizations_json, affected_economic_sectors_json,
         magnitude, direction, unit, severity, novelty, confidence,
         verification_state, payload_json, transform_version,
         correction_of, replaces_event_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            event_type,
            clean_title,
            clean_summary,
            raw["source_id"],
            raw_observation_id,
            raw["source_record_id"],
            fingerprint,
            occurred.isoformat(),
            published.isoformat(),
            raw["observed_at"],
            raw["ingested_at"],
            effective.isoformat(),
            _time(effective_to).isoformat() if effective_to else "",
            expiry.isoformat(),
            _json(geo_scope or {}),
            _json(scope),
            _json(affected_spaces or []),
            _json(affected_roles or []),
            _json(affected_organizations or []),
            _json(affected_economic_sectors or []),
            magnitude,
            direction,
            unit,
            float(severity),
            float(novelty),
            computed_confidence,
            verification_state,
            _json(payload or {}),
            TRANSFORM_VERSION,
            correction_of,
            replaces_event_id,
        ),
    )
    event_id = cursor.lastrowid
    peers = conn.execute(
        """
        SELECT * FROM external_events
        WHERE semantic_fingerprint = ? AND id != ? AND status = 'active'
        ORDER BY id
        """,
        (fingerprint, event_id),
    ).fetchall()
    for peer in peers:
        conflict = (
            direction != "neutral"
            and peer["direction"] != "neutral"
            and direction != peer["direction"]
        )
        link_type = "conflicts" if conflict else "corroborates"
        conn.execute(
            """
            INSERT OR IGNORE INTO external_event_links
            (from_event_id, to_event_id, link_type, confidence, evidence_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                peer["id"],
                link_type,
                min(computed_confidence, float(peer["confidence"])),
                _json({"semantic_fingerprint": fingerprint}),
            ),
        )
        if conflict:
            conn.execute(
                """
                UPDATE external_events SET verification_state = 'conflicted',
                    confidence = confidence * 0.65
                WHERE id IN (?, ?)
                """,
                (event_id, peer["id"]),
            )
        else:
            conn.execute(
                """
                UPDATE external_events
                SET verification_state = 'corroborated',
                    confidence = CASE
                        WHEN confidence * 1.1 > 1.0 THEN 1.0
                        ELSE confidence * 1.1
                    END
                WHERE id IN (?, ?)
                """,
                (event_id, peer["id"]),
            )
    if correction_of or replaces_event_id:
        prior_id = correction_of or replaces_event_id
        link_type = "corrects" if correction_of else "replaces"
        conn.execute(
            """
            INSERT OR IGNORE INTO external_event_links
            (from_event_id, to_event_id, link_type, confidence, evidence_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, prior_id, link_type, computed_confidence, _json({"preserves_history": True})),
        )
        conn.execute(
            "UPDATE external_events SET status = 'superseded' WHERE id = ?",
            (prior_id,),
        )
    if _table_exists(conn, "external_information"):
        conn.execute(
            """
            INSERT OR IGNORE INTO external_information
            (title, summary, source_name, source_url, category, relevance,
             published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_title,
                clean_summary,
                raw["source_name"],
                raw["source_base_url"],
                catalog["category"],
                int(round(computed_confidence * 100)),
                published.isoformat(),
            ),
        )
    row = dict(
        conn.execute("SELECT * FROM external_events WHERE id = ?", (event_id,)).fetchone()
    )
    row["duplicate"] = False
    return row


def create_external_snapshot(
    conn,
    *,
    snapshot_key,
    window_start,
    window_end,
    mode="snapshot",
    metadata=None,
    seal=True,
):
    existing = conn.execute(
        "SELECT * FROM external_data_snapshots WHERE snapshot_key = ?",
        (snapshot_key,),
    ).fetchone()
    if existing:
        return dict(existing)
    start, end = _time(window_start), _time(window_end)
    if end < start:
        raise ValueError("快照结束时间不能早于开始时间")
    cursor = conn.execute(
        """
        INSERT INTO external_data_snapshots
        (snapshot_key, mode, window_start, window_end,
         event_catalog_version, transform_version, impact_rule_version,
         metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_key,
            mode,
            start.isoformat(),
            end.isoformat(),
            CATALOG_VERSION,
            TRANSFORM_VERSION,
            IMPACT_RULE_VERSION,
            _json(metadata or {}),
        ),
    )
    snapshot_id = cursor.lastrowid
    events = conn.execute(
        """
        SELECT id, raw_observation_id, occurred_at
        FROM external_events
        WHERE occurred_at >= ? AND occurred_at <= ?
        ORDER BY occurred_at, id
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    for ordinal, event in enumerate(events):
        conn.execute(
            """
            INSERT INTO external_snapshot_items
            (snapshot_id, raw_observation_id, external_event_id, ordinal, event_time)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                event["raw_observation_id"],
                event["id"],
                ordinal,
                event["occurred_at"],
            ),
        )
    checksum = _hash(
        _json(
            {
                "snapshot_key": snapshot_key,
                "events": [int(row["id"]) for row in events],
                "versions": [CATALOG_VERSION, TRANSFORM_VERSION, IMPACT_RULE_VERSION],
            }
        )
    )
    if seal:
        conn.execute(
            """
            UPDATE external_data_snapshots
            SET status = 'sealed', checksum = ?, sealed_at = ?
            WHERE id = ?
            """,
            (checksum, _time().isoformat(), snapshot_id),
        )
    return dict(
        conn.execute(
            "SELECT * FROM external_data_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
    )


def configure_external_mode(
    conn,
    *,
    branch_key,
    mode,
    snapshot_id=None,
    replay_start_world_time=None,
    replay_speed=1.0,
    simulation_seed=0,
):
    if mode in {"snapshot", "replay"}:
        snapshot = conn.execute(
            "SELECT * FROM external_data_snapshots WHERE id = ? AND status = 'sealed'",
            (snapshot_id,),
        ).fetchone()
        if not snapshot:
            raise ValueError("快照或回放模式必须绑定已封存快照")
    conn.execute(
        """
        INSERT INTO external_runtime_modes
        (branch_key, mode, snapshot_id, replay_start_world_time,
         replay_speed, simulation_seed)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (branch_key) DO UPDATE SET
            mode = excluded.mode,
            snapshot_id = excluded.snapshot_id,
            replay_start_world_time = excluded.replay_start_world_time,
            replay_speed = excluded.replay_speed,
            simulation_seed = excluded.simulation_seed,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            branch_key,
            mode,
            snapshot_id,
            _time(replay_start_world_time).isoformat() if replay_start_world_time else "",
            float(replay_speed),
            int(simulation_seed),
        ),
    )
    if mode == "replay":
        snapshot = conn.execute(
            "SELECT * FROM external_data_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        replay_start = _time(replay_start_world_time)
        snapshot_start = _time(snapshot["window_start"])
        items = conn.execute(
            """
            SELECT * FROM external_snapshot_items
            WHERE snapshot_id = ? ORDER BY ordinal
            """,
            (snapshot_id,),
        ).fetchall()
        for item in items:
            delta = (_time(item["event_time"]) - snapshot_start) / float(replay_speed)
            conn.execute(
                """
                INSERT OR IGNORE INTO external_replay_deliveries
                (delivery_key, snapshot_id, external_event_id, branch_key,
                 scheduled_world_time)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    f"replay:{snapshot_id}:{branch_key}:{item['external_event_id']}",
                    snapshot_id,
                    item["external_event_id"],
                    branch_key,
                    (replay_start + delta).isoformat(),
                ),
            )
    return dict(
        conn.execute(
            "SELECT * FROM external_runtime_modes WHERE branch_key = ?",
            (branch_key,),
        ).fetchone()
    )


def schedule_exposure(
    conn,
    *,
    exposure_key,
    external_event_id,
    resident_id,
    channel,
    scheduled_at,
    credibility_at_delivery,
    sender_resident_id=None,
    parent_exposure_id=None,
    distortion=None,
    attention_cost=0,
    correction_of_exposure_id=None,
):
    existing = conn.execute(
        "SELECT * FROM external_exposures WHERE exposure_key = ?", (exposure_key,)
    ).fetchone()
    if existing:
        return dict(existing)
    cursor = conn.execute(
        """
        INSERT INTO external_exposures
        (exposure_key, external_event_id, resident_id, channel,
         sender_resident_id, parent_exposure_id, scheduled_at,
         credibility_at_delivery, distortion_json, attention_cost,
         correction_of_exposure_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exposure_key,
            external_event_id,
            resident_id,
            channel,
            sender_resident_id,
            parent_exposure_id,
            _time(scheduled_at).isoformat(),
            float(credibility_at_delivery),
            _json(distortion or {}),
            float(attention_cost),
            correction_of_exposure_id,
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM external_exposures WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )


def deliver_due_exposures(conn, world_time=None):
    now = _time(world_time)
    due = conn.execute(
        """
        SELECT exposure.*, event.title, event.summary
        FROM external_exposures exposure
        JOIN external_events event ON event.id = exposure.external_event_id
        WHERE exposure.response = 'pending' AND exposure.scheduled_at <= ?
        ORDER BY exposure.scheduled_at, exposure.id
        """,
        (now.isoformat(),),
    ).fetchall()
    delivered = []
    for exposure in due:
        response = (
            "believed"
            if float(exposure["credibility_at_delivery"]) >= 0.7
            else "doubted"
            if float(exposure["credibility_at_delivery"]) >= 0.4
            else "ignored"
        )
        noticed_at = now.isoformat() if response != "ignored" else ""
        conn.execute(
            """
            UPDATE external_exposures
            SET delivered_at = ?, noticed_at = ?, response = ?
            WHERE id = ?
            """,
            (now.isoformat(), noticed_at, response, exposure["id"]),
        )
        if _table_exists(conn, "agent_information"):
            information = conn.execute(
                "SELECT id FROM external_information WHERE title = ?",
                (exposure["title"],),
            ).fetchone()
            if information:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO agent_information
                    (information_id, resident_id, channel, relevance,
                     credibility, distortion_note, source_resident_id,
                     received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        information["id"],
                        exposure["resident_id"],
                        exposure["channel"],
                        70,
                        int(round(float(exposure["credibility_at_delivery"]) * 100)),
                        _json(_load(exposure["distortion_json"], {})),
                        exposure["sender_resident_id"],
                        now.isoformat(),
                    ),
                )
        delivered.append(exposure["id"])
    return {"delivered": delivered}


def _source_governed(conn, source_id):
    if not _table_exists(conn, "external_governance_reviews"):
        return True
    row = conn.execute(
        """
        SELECT decision, license_approved, purpose_approved,
               retention_approved, privacy_approved
        FROM external_governance_reviews
        WHERE source_id = ? ORDER BY id DESC LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    return bool(
        row
        and row["decision"] == "approved"
        and all(
            int(row[key])
            for key in (
                "license_approved",
                "purpose_approved",
                "retention_approved",
                "privacy_approved",
            )
        )
    )


def propose_event_impacts(conn, external_event_id):
    event = conn.execute(
        "SELECT * FROM external_events WHERE id = ?", (external_event_id,)
    ).fetchone()
    if not event:
        raise ValueError("外部事件不存在")
    rules = conn.execute(
        """
        SELECT * FROM external_impact_rules
        WHERE event_type = ? AND status = 'active' ORDER BY id
        """,
        (event["event_type"],),
    ).fetchall()
    results = []
    for rule in rules:
        target_values = (
            _load(event["affected_spaces_json"], [])
            if rule["target_type"] == "space"
            else _load(event["affected_roles_json"], [])
            if rule["target_type"] == "role"
            else _load(event["affected_economic_sectors_json"], [])
            if rule["target_type"] in {"market", "sector"}
            else ["campus"]
        )
        if not target_values:
            target_values = ["campus"]
        for index, target in enumerate(target_values):
            key = f"external:{external_event_id}:rule:{rule['id']}:target:{index}"
            existing = conn.execute(
                "SELECT * FROM external_event_impacts WHERE impact_key = ?", (key,)
            ).fetchone()
            if not existing:
                value = abs(
                    float(
                        event["magnitude"]
                        if event["magnitude"] is not None
                        else event["severity"]
                    )
                )
                conn.execute(
                    """
                    INSERT INTO external_event_impacts
                    (impact_key, external_event_id, impact_rule_id, impact_type,
                     target_type, target_key, state_key, operation, value, unit,
                     starts_at, ends_at, confidence, rule_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        external_event_id,
                        rule["id"],
                        rule["impact_type"],
                        rule["target_type"],
                        str(target),
                        rule["state_key"],
                        rule["operation"],
                        value,
                        rule["unit"],
                        event["effective_from"],
                        event["effective_to"],
                        event["confidence"],
                        rule["rule_version"],
                    ),
                )
            results.append(
                dict(
                    conn.execute(
                        "SELECT * FROM external_event_impacts WHERE impact_key = ?",
                        (key,),
                    ).fetchone()
                )
            )
    return results


def _validate_impact(event, rule, impact, now, governed):
    if event["status"] != "active":
        return False, "event_not_active"
    if _time(event["expires_at"]) < now:
        return False, "event_stale"
    if not governed:
        return False, "source_governance_not_approved"
    if event["verification_state"] == "conflicted":
        return False, "source_conflict"
    if float(impact["confidence"]) < float(rule["min_confidence"]):
        return False, "confidence_below_threshold"
    if int(rule["requires_verification"]) and event["verification_state"] not in {
        "verified",
        "corroborated",
    }:
        return False, "high_impact_requires_verification"
    return True, "validated"


def apply_external_impacts(conn, world_time=None, branch_key="main"):
    now = _time(world_time)
    events = conn.execute(
        """
        SELECT DISTINCT event.*
        FROM external_events event
        JOIN external_event_impacts impact ON impact.external_event_id = event.id
        WHERE impact.status IN ('proposed', 'validated')
          AND impact.starts_at <= ?
        ORDER BY event.effective_from, event.id
        """,
        (now.isoformat(),),
    ).fetchall()
    applied, rejected = [], []
    for event in events:
        impacts = conn.execute(
            """
            SELECT impact.*, rule.parameters_json, rule.min_confidence,
                   rule.requires_verification, rule.high_impact
            FROM external_event_impacts impact
            JOIN external_impact_rules rule ON rule.id = impact.impact_rule_id
            WHERE impact.external_event_id = ?
              AND impact.status IN ('proposed', 'validated')
            ORDER BY impact.id
            """,
            (event["id"],),
        ).fetchall()
        for impact in impacts:
            valid, reason = _validate_impact(
                event, impact, impact, now, _source_governed(conn, event["source_id"])
            )
            if not valid:
                conn.execute(
                    "UPDATE external_event_impacts SET status = 'rejected', reason = ? WHERE id = ?",
                    (reason, impact["id"]),
                )
                rejected.append({"id": impact["id"], "reason": reason})
                continue
            parameters = _load(impact["parameters_json"], {})
            shock_key = parameters.get("shock_key")
            if impact["operation"] != "trigger_shock" or not shock_key:
                conn.execute(
                    "UPDATE external_event_impacts SET status = 'rejected', reason = 'unsupported_operation' WHERE id = ?",
                    (impact["id"],),
                )
                rejected.append({"id": impact["id"], "reason": "unsupported_operation"})
                continue
            scope_key = (
                "space"
                if impact["target_type"] == "space"
                else "role"
                if impact["target_type"] == "role"
                else "sector"
                if impact["target_type"] in {"market", "sector"}
                else "target"
            )
            shock = create_shock(
                conn,
                instance_key=f"external-impact:{impact['id']}",
                shock_key=shock_key,
                scheduled_at=impact["starts_at"],
                severity=min(1.0, max(0.01, abs(float(impact["value"])))),
                scope={scope_key: impact["target_key"]},
                parameters={
                    "external_event_id": event["id"],
                    "impact_rule_version": impact["rule_version"],
                },
                source_type="external_mapped",
                source_id=str(event["id"]),
                branch_key=branch_key,
                random_seed=int(event["id"]),
                duration_minutes=max(
                    1,
                    int(
                        (
                            _time(event["effective_to"]) - _time(event["effective_from"])
                        ).total_seconds()
                        / 60
                    )
                    if event["effective_to"]
                    else 180,
                ),
            )
            resilience = process_resilience_runtime(conn, now)
            from app.main import append_world_event

            world_event = append_world_event(
                conn,
                "external_event_applied",
                event["title"],
                event["summary"],
                payload={
                    "external_event_id": event["id"],
                    "raw_observation_id": event["raw_observation_id"],
                    "impact_id": impact["id"],
                    "impact_rule_id": impact["impact_rule_id"],
                    "shock_instance_id": shock["id"],
                    "resilience": resilience,
                },
                source_type="external_event",
                source_id=event["id"],
                rule_version=impact["rule_version"],
                occurred_at=event["occurred_at"],
                branch_key=branch_key,
            )
            conn.execute(
                """
                UPDATE external_event_impacts
                SET status = 'applied', world_event_id = ?,
                    shock_instance_id = ?, reason = 'applied',
                    applied_state_json = ?, applied_at = ?
                WHERE id = ?
                """,
                (
                    world_event["id"],
                    shock["id"],
                    _json({"shock_status": shock["status"], "resilience": resilience}),
                    now.isoformat(),
                    impact["id"],
                ),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO external_state_reconciliations
                (reconciliation_key, external_event_id, impact_id,
                 expected_state_json, actual_state_json, status, checked_at,
                 details_json)
                VALUES (?, ?, ?, ?, ?, 'matched', ?, ?)
                """,
                (
                    f"external-reconcile:{impact['id']}",
                    event["id"],
                    impact["id"],
                    _json({"shock_instance_id": shock["id"]}),
                    _json({"shock_instance_id": shock["id"]}),
                    now.isoformat(),
                    _json({"world_event_id": world_event["id"]}),
                ),
            )
            applied.append(impact["id"])
    return {"applied": applied, "rejected": rejected}


def evaluate_external_health(conn, world_time=None, branch_key="main"):
    now = _time(world_time)
    sources = conn.execute(
        "SELECT * FROM external_sources WHERE enabled = 1 AND status = 'active'"
    ).fetchall()
    stale = failed = 0
    details = []
    for source in sources:
        last_success = _time(source["last_success_at"]) if source["last_success_at"] else None
        is_stale = not last_success or (
            now - last_success
        ).total_seconds() > int(source["stale_after_seconds"])
        latest_run = conn.execute(
            """
            SELECT status FROM external_sync_runs
            WHERE source_id = ? ORDER BY id DESC LIMIT 1
            """,
            (source["id"],),
        ).fetchone()
        is_failed = bool(latest_run and latest_run["status"] in {"failed", "dead_letter"})
        stale += int(is_stale)
        failed += int(is_failed)
        details.append(
            {
                "source_id": source["id"],
                "stale": is_stale,
                "failed": is_failed,
                "last_success_at": source["last_success_at"],
            }
        )
    dead = conn.execute(
        "SELECT COUNT(*) AS value FROM external_sync_runs WHERE status = 'dead_letter'"
    ).fetchone()["value"]
    status = (
        "external_data_degraded"
        if failed or (sources and stale == len(sources))
        else "stale"
        if stale
        else "healthy"
    )
    conn.execute(
        """
        INSERT INTO external_runtime_health
        (branch_key, status, stale_source_count, failed_source_count,
         dead_letter_count, last_evaluated_at, details_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (branch_key) DO UPDATE SET
            status = excluded.status,
            stale_source_count = excluded.stale_source_count,
            failed_source_count = excluded.failed_source_count,
            dead_letter_count = excluded.dead_letter_count,
            last_evaluated_at = excluded.last_evaluated_at,
            details_json = excluded.details_json
        """,
        (branch_key, status, stale, failed, int(dead), now.isoformat(), _json(details)),
    )
    return {
        "status": status,
        "stale_source_count": stale,
        "failed_source_count": failed,
        "dead_letter_count": int(dead),
    }


def review_external_source(
    conn,
    *,
    source_id,
    reviewer,
    decision,
    reviewed_at=None,
    license_approved=False,
    purpose_approved=False,
    retention_approved=False,
    privacy_approved=False,
    notes="",
):
    if not conn.execute(
        "SELECT id FROM external_sources WHERE id = ?", (source_id,)
    ).fetchone():
        raise ValueError("外部来源不存在")
    reviewed = _time(reviewed_at)
    cursor = conn.execute(
        """
        INSERT INTO external_governance_reviews
        (review_key, source_id, license_approved, purpose_approved,
         retention_approved, privacy_approved, reviewer, decision,
         notes, reviewed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"review:{source_id}:{reviewed.isoformat()}",
            source_id,
            int(license_approved),
            int(purpose_approved),
            int(retention_approved),
            int(privacy_approved),
            reviewer,
            decision,
            _clean_text(notes, 1000),
            reviewed.isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO external_access_audit
        (actor_key, action, resource_type, resource_id, decision, reason,
         occurred_at, metadata_json)
        VALUES (?, 'review_source', 'external_source', ?, 'allowed', ?, ?, ?)
        """,
        (
            reviewer,
            str(source_id),
            decision,
            reviewed.isoformat(),
            _json({"review_id": cursor.lastrowid}),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM external_governance_reviews WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    )


def bind_external_experiment(
    conn,
    *,
    experiment_key,
    branch_key,
    external_mode,
    simulation_seed,
    snapshot_id=None,
    metadata=None,
):
    if external_mode in {"snapshot", "replay"} and not conn.execute(
        """
        SELECT id FROM external_data_snapshots
        WHERE id = ? AND status = 'sealed'
        """,
        (snapshot_id,),
    ).fetchone():
        raise ValueError("可复现实验必须绑定已封存外部数据快照")
    conn.execute(
        """
        INSERT INTO external_experiment_bindings
        (experiment_key, branch_key, external_mode, snapshot_id,
         event_catalog_version, transform_version, impact_rule_version,
         simulation_seed, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            experiment_key,
            branch_key,
            external_mode,
            snapshot_id,
            CATALOG_VERSION,
            TRANSFORM_VERSION,
            IMPACT_RULE_VERSION,
            int(simulation_seed),
            _json(metadata or {}),
        ),
    )
    return dict(
        conn.execute(
            """
            SELECT * FROM external_experiment_bindings
            WHERE experiment_key = ?
            """,
            (experiment_key,),
        ).fetchone()
    )


def export_external_snapshot(conn, *, export_key, snapshot_id, requested_by):
    snapshot = conn.execute(
        """
        SELECT * FROM external_data_snapshots
        WHERE id = ? AND status = 'sealed'
        """,
        (snapshot_id,),
    ).fetchone()
    if not snapshot:
        raise ValueError("只能导出已封存外部数据快照")
    items = conn.execute(
        """
        SELECT external_event_id, raw_observation_id, ordinal, event_time
        FROM external_snapshot_items WHERE snapshot_id = ? ORDER BY ordinal
        """,
        (snapshot_id,),
    ).fetchall()
    manifest = {
        "snapshot_key": snapshot["snapshot_key"],
        "snapshot_checksum": snapshot["checksum"],
        "versions": {
            "catalog": snapshot["event_catalog_version"],
            "transform": snapshot["transform_version"],
            "impact_rules": snapshot["impact_rule_version"],
        },
        "items": [dict(row) for row in items],
    }
    checksum = _hash(_json(manifest))
    conn.execute(
        """
        INSERT INTO external_snapshot_exports
        (export_key, snapshot_id, requested_by, status, manifest_json,
         checksum, completed_at)
        VALUES (?, ?, ?, 'complete', ?, ?, ?)
        """,
        (
            export_key,
            snapshot_id,
            requested_by,
            _json(manifest),
            checksum,
            _time().isoformat(),
        ),
    )
    return dict(
        conn.execute(
            """
            SELECT * FROM external_snapshot_exports WHERE export_key = ?
            """,
            (export_key,),
        ).fetchone()
    )


def _project_news_event_to_information(conn, event, payload, source_name):
    title = event.get("title") or payload.get("title") or "公共资讯"
    summary = event.get("summary") or payload.get("summary") or title
    source_url = payload.get("link") or payload.get("source_url") or ""
    published_at = payload.get("published_at_text") or str(event.get("published_at") or "")
    category = payload.get("category")
    if not category:
        try:
            from services.external_information import classify_information
            category = classify_information(f"{title} {summary}")
        except Exception:
            category = "general"
    conn.execute(
        """
        INSERT OR IGNORE INTO external_information
        (title, summary, source_name, source_url, category, published_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, summary, source_name, source_url, category, published_at),
    )
    row = conn.execute(
        "SELECT * FROM external_information WHERE title = ?", (title,)
    ).fetchone()
    if row:
        info = dict(row)
        from services.external_information import seed_recipients, deliver_information
        from app.main import (
            ensure_external_information_system,
            ensure_profile_meta,
            load_json_text,
            json_dumps,
            get_current_day,
            add_memory,
        )
        def deliver_cb(c, information, resident_id, channel="外部资讯订阅", relevance=80, credibility=88):
            return deliver_information(
                c,
                information,
                resident_id,
                channel,
                relevance,
                credibility,
                "",
                None,
                ensure_system=ensure_external_information_system,
                ensure_profile=ensure_profile_meta,
                load_json=load_json_text,
                json_dumps=json_dumps,
                current_day=get_current_day,
                add_memory=add_memory,
            )
        seed_recipients(conn, info, deliver=deliver_cb)


def maybe_sync_due_sources(conn, now=None):
    now = _time(now)
    sources = conn.execute(
        """
        SELECT * FROM external_sources
        WHERE enabled = 1 AND status = 'active'
        """
    ).fetchall()
    sync_results = []
    for raw_source in sources:
        source = dict(raw_source)
        source_id = source["id"]
        source_key = source["source_key"]
        if source["source_type"] not in {"rss", "weather"}:
            continue
        last_attempt = source.get("last_attempt_at")
        poll_interval = int(source.get("poll_interval_seconds") or 3600)
        if last_attempt:
            try:
                last_attempt_time = _time(last_attempt)
                if (now - last_attempt_time).total_seconds() < poll_interval:
                    sync_results.append({
                        "source_id": source_id,
                        "source_key": source_key,
                        "status": "skipped",
                        "reason": "interval_not_elapsed",
                    })
                    continue
            except Exception:
                pass
        try:
            res = sync_registered_source(conn, source_id, now=now)
            sync_results.append({
                "source_id": source_id,
                "source_key": source_key,
                "status": "success",
                "record_count": res.get("record_count", 0),
                "event_count": len(res.get("event_ids", [])),
                "sync_run_id": res.get("sync_run", {}).get("id"),
            })
        except Exception as exc:
            sync_results.append({
                "source_id": source_id,
                "source_key": source_key,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
    return sync_results


def process_external_world_runtime(conn, world_time=None, branch_key="main"):
    if not external_world_available(conn) or not _table_exists(conn, "external_runtime_modes"):
        return {"available": False}
    now = _time(world_time)
    mode = conn.execute(
        "SELECT * FROM external_runtime_modes WHERE branch_key = ?", (branch_key,)
    ).fetchone()
    replayed = []
    sync_results = []
    if mode and mode["mode"] == "replay":
        due = conn.execute(
            """
            SELECT * FROM external_replay_deliveries
            WHERE branch_key = ? AND status = 'scheduled'
              AND scheduled_world_time <= ?
            ORDER BY scheduled_world_time, id
            """,
            (branch_key, now.isoformat()),
        ).fetchall()
        for item in due:
            propose_event_impacts(conn, item["external_event_id"])
            conn.execute(
                """
                UPDATE external_replay_deliveries
                SET status = 'delivered', delivered_at = ? WHERE id = ?
                """,
                (now.isoformat(), item["id"]),
            )
            replayed.append(item["external_event_id"])
    elif not mode or mode["mode"] == "live":
        sync_results = maybe_sync_due_sources(conn, now)
        ready = conn.execute(
            """
            SELECT id FROM external_events
            WHERE status = 'active' AND effective_from <= ? AND expires_at >= ?
            ORDER BY effective_from, id
            """,
            (now.isoformat(), now.isoformat()),
        ).fetchall()
        for event in ready:
            propose_event_impacts(conn, event["id"])
    impact_result = apply_external_impacts(conn, now, branch_key)
    exposures = deliver_due_exposures(conn, now)
    health = evaluate_external_health(conn, now, branch_key)
    return {
        "available": True,
        "mode": mode["mode"] if mode else "live",
        "sync_results": sync_results,
        "replayed_event_ids": replayed,
        "impacts": impact_result,
        "exposures": exposures,
        "health": health,
    }
