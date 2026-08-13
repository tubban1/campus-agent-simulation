from __future__ import annotations

import json
from app.json_utils import json_dumps
from datetime import datetime, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ

from app.organizations.service import submit_organization_proposal


PRIMITIVE_SEEDS = (
    (
        "set-space-hours",
        "调整空间开放时间",
        "institutional",
        "set_space_hours",
        {
            "open_hour": {"type": "integer", "min": 0, "max": 23},
            "close_hour": {"type": "integer", "min": 1, "max": 24},
        },
        ["space"],
    ),
    (
        "set-space-capacity",
        "调整空间名义容量",
        "capacity",
        "set_space_capacity",
        {"capacity": {"type": "integer", "min": 0, "max": 5000}},
        ["space"],
    ),
    (
        "set-boundary-monitoring",
        "调整边界监测强度",
        "enforcement",
        "set_boundary_monitoring",
        {"monitoring_strength": {"type": "number", "min": 0.0, "max": 1.0}},
        ["campus", "space"],
    ),
    (
        "set-boundary-sanction",
        "调整边界处罚尺度",
        "enforcement",
        "set_boundary_sanction",
        {"sanction_minor": {"type": "integer", "min": 0, "max": 100000}},
        ["campus", "space"],
    ),
    (
        "set-space-service-status",
        "调整空间正式服务状态",
        "service",
        "set_space_service_status",
        {"status": {"type": "enum", "values": ["开放", "维护中", "临时关闭"]}},
        ["space"],
    ),
)


def _json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value, fallback):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _now(value=None):
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    parsed = parse_world_datetime(value)
    if parsed:
        return parsed
    raise ValueError(f"无法解析的时间格式: {value}")


def _table_exists(conn, table_name):
    return bool(conn.execute(f"PRAGMA table_info({table_name})").fetchall())


def institution_evolution_available(conn):
    return _table_exists(conn, "rule_primitives")


def seed_rule_primitives(conn):
    created = 0
    for key, name, layer, executor, schema, scopes in PRIMITIVE_SEEDS:
        before = conn.execute(
            "SELECT id FROM rule_primitives WHERE primitive_key = ?", (key,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO rule_primitives
            (primitive_key, name, rule_layer, executor_key,
             parameter_schema_json, allowed_scope_types_json,
             immutable_invariants_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                name,
                layer,
                executor,
                _json(schema),
                _json(scopes),
                _json(
                    [
                        "causal_order",
                        "history_immutability",
                        "inventory_conservation",
                        "double_entry_ledger",
                    ]
                ),
            ),
        )
        created += int(before is None)
    return {"primitives": len(PRIMITIVE_SEEDS), "created": created}


def _validate_parameters(primitive, parameters, scope_type):
    scopes = _load(primitive["allowed_scope_types_json"], [])
    if scope_type not in scopes:
        raise ValueError("规则原语不支持该适用范围")
    schema = _load(primitive["parameter_schema_json"], {})
    unknown = set(parameters) - set(schema)
    if unknown:
        raise ValueError(f"规则参数不受支持: {', '.join(sorted(unknown))}")
    normalized = {}
    for key, spec in schema.items():
        if key not in parameters:
            raise ValueError(f"规则参数缺失: {key}")
        value = parameters[key]
        if spec["type"] == "integer":
            if isinstance(value, bool):
                raise ValueError(f"规则参数类型错误: {key}")
            value = int(value)
        elif spec["type"] == "number":
            value = float(value)
        elif spec["type"] == "enum":
            if value not in spec["values"]:
                raise ValueError(f"规则参数值不受支持: {key}")
        if spec["type"] in {"integer", "number"}:
            if value < spec["min"] or value > spec["max"]:
                raise ValueError(f"规则参数超出允许范围: {key}")
        normalized[key] = value
    if primitive["executor_key"] == "set_space_hours":
        if normalized["open_hour"] >= normalized["close_hour"]:
            raise ValueError("开放时间必须早于关闭时间")
    return normalized


def submit_rule_proposal(
    conn,
    *,
    proposal_key,
    organization_id,
    proposer_resident_id,
    primitive_key,
    title,
    rationale,
    scope_type,
    scope_key,
    parameters,
    world_time=None,
    source_norm_id=None,
    requested_budget_minor=0,
    monitoring_plan=None,
    review_after_days=30,
    repeal_conditions=None,
):
    existing = conn.execute(
        "SELECT * FROM institutional_rule_proposals WHERE proposal_key = ?",
        (proposal_key,),
    ).fetchone()
    if existing:
        return dict(existing)
    primitive = conn.execute(
        """
        SELECT * FROM rule_primitives
        WHERE primitive_key = ? AND status = 'active'
        """,
        (primitive_key,),
    ).fetchone()
    if not primitive:
        raise ValueError("规则原语不存在或不可用，提案只能记录为未支持诉求")
    normalized = _validate_parameters(primitive, parameters, scope_type)
    now = _now(world_time)
    organization_proposal = submit_organization_proposal(
        conn,
        proposal_key=f"institution-rule:{proposal_key}",
        organization_id=organization_id,
        proposer_resident_id=proposer_resident_id,
        proposal_type="institutional_rule",
        title=title,
        description=rationale,
        requested_budget_minor=requested_budget_minor,
        world_time=now,
        source_type="norm_candidate" if source_norm_id else "resident_proposal",
        source_id=str(source_norm_id or proposer_resident_id),
    )
    cursor = conn.execute(
        """
        INSERT INTO institutional_rule_proposals
        (proposal_key, source_norm_id, organization_id, proposer_resident_id,
         organization_proposal_id, primitive_id, title, rationale, scope_type,
         scope_key, parameters_json, requested_budget_minor,
         monitoring_plan_json, review_after_days, repeal_conditions_json,
         status, submitted_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'deliberation', ?, ?)
        """,
        (
            proposal_key,
            source_norm_id,
            organization_id,
            proposer_resident_id,
            organization_proposal["id"],
            primitive["id"],
            title,
            rationale,
            scope_type,
            scope_key,
            _json(normalized),
            requested_budget_minor,
            _json(monitoring_plan or {}),
            int(review_after_days),
            _json(repeal_conditions or {}),
            now.isoformat(),
            _json({"primitive_key": primitive_key}),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM institutional_rule_proposals WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    )


def record_rule_deliberation(
    conn,
    *,
    proposal_id,
    participant_type,
    participant_id,
    stance,
    argument,
    influence_weight=1,
    evidence=None,
    world_time=None,
):
    now = _now(world_time)
    cursor = conn.execute(
        """
        INSERT INTO rule_deliberations
        (proposal_id, participant_type, participant_id, stance,
         influence_weight, argument, evidence_json, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal_id,
            participant_type,
            str(participant_id),
            stance,
            float(influence_weight),
            argument,
            _json(evidence or {}),
            now.isoformat(),
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM rule_deliberations WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )


def _apply_rule(conn, primitive, scope_key, parameters):
    executor = primitive["executor_key"]
    constraint_rule_id = None
    if executor == "set_space_hours":
        cursor = conn.execute(
            """
            UPDATE campus_spaces SET open_hour = ?, close_hour = ?
            WHERE location = ?
            """,
            (parameters["open_hour"], parameters["close_hour"], scope_key),
        )
    elif executor == "set_space_capacity":
        cursor = conn.execute(
            "UPDATE campus_spaces SET capacity = ? WHERE location = ?",
            (parameters["capacity"], scope_key),
        )
    elif executor == "set_space_service_status":
        cursor = conn.execute(
            "UPDATE campus_spaces SET status = ? WHERE location = ?",
            (parameters["status"], scope_key),
        )
    elif executor in {"set_boundary_monitoring", "set_boundary_sanction"}:
        rule_key = "space-boundary-enforcement"
        row = conn.execute(
            "SELECT * FROM constraint_rules WHERE rule_key = ?", (rule_key,)
        ).fetchone()
        if not row:
            raise ValueError("边界约束规则尚未初始化")
        config = _load(
            row["enforcement_json"]
            if executor == "set_boundary_monitoring"
            else row["parameters_json"],
            {},
        )
        config.update(parameters)
        column = (
            "enforcement_json"
            if executor == "set_boundary_monitoring"
            else "parameters_json"
        )
        conn.execute(
            f"""
            UPDATE constraint_rules
            SET {column} = ?, version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (_json(config), row["id"]),
        )
        constraint_rule_id = int(row["id"])
        cursor = type("Result", (), {"rowcount": 1})()
    else:
        raise ValueError("规则原语没有受控执行器")
    if not cursor.rowcount:
        raise ValueError("规则适用对象不存在")
    return constraint_rule_id


def enact_approved_rule(conn, proposal_id, world_time=None):
    now = _now(world_time)
    proposal = conn.execute(
        """
        SELECT proposal.*, primitive.primitive_key, primitive.executor_key,
               primitive.parameter_schema_json,
               primitive.allowed_scope_types_json
        FROM institutional_rule_proposals proposal
        JOIN rule_primitives primitive ON primitive.id = proposal.primitive_id
        WHERE proposal.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if not proposal:
        raise ValueError("制度规则提案不存在")
    organization_proposal = conn.execute(
        "SELECT * FROM organization_proposals WHERE id = ?",
        (proposal["organization_proposal_id"],),
    ).fetchone()
    if not organization_proposal or organization_proposal["status"] != "executed":
        raise ValueError("组织提案尚未依法批准并执行")
    existing = conn.execute(
        "SELECT * FROM evolved_rule_versions WHERE proposal_id = ?",
        (proposal_id,),
    ).fetchone()
    if existing:
        return dict(existing)
    parameters = _validate_parameters(
        proposal, _load(proposal["parameters_json"], {}), proposal["scope_type"]
    )
    lineage_key = f"{proposal['primitive_key']}:{proposal['scope_type']}:{proposal['scope_key']}"
    previous = conn.execute(
        """
        SELECT * FROM evolved_rule_versions
        WHERE lineage_key = ? AND status IN ('active', 'trial')
        ORDER BY version DESC LIMIT 1
        """,
        (lineage_key,),
    ).fetchone()
    version = int(previous["version"]) + 1 if previous else 1
    constraint_rule_id = _apply_rule(
        conn, proposal, proposal["scope_key"], parameters
    )
    if previous:
        conn.execute(
            """
            UPDATE evolved_rule_versions
            SET status = 'superseded', effective_to = ?
            WHERE id = ?
            """,
            (now.isoformat(), previous["id"]),
        )
    cursor = conn.execute(
        """
        INSERT INTO evolved_rule_versions
        (lineage_key, version, proposal_id, primitive_id, scope_type,
         scope_key, parameters_json, status, effective_from,
         replaces_rule_version_id, constraint_rule_id, enacted_by_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            lineage_key,
            version,
            proposal_id,
            proposal["primitive_id"],
            proposal["scope_type"],
            proposal["scope_key"],
            _json(parameters),
            now.isoformat(),
            previous["id"] if previous else None,
            constraint_rule_id,
            str(proposal["organization_id"]),
        ),
    )
    conn.execute(
        """
        UPDATE institutional_rule_proposals
        SET status = 'enacted', decided_at = ?, enacted_at = ?
        WHERE id = ?
        """,
        (organization_proposal["decided_at"], now.isoformat(), proposal_id),
    )
    return dict(
        conn.execute(
            "SELECT * FROM evolved_rule_versions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )


def process_institution_evolution(conn, world_time=None):
    if not institution_evolution_available(conn):
        return {"available": False}
    rows = conn.execute(
        """
        SELECT proposal.id, organization.status AS organization_status
        FROM institutional_rule_proposals proposal
        JOIN organization_proposals organization
          ON organization.id = proposal.organization_proposal_id
        WHERE proposal.status IN ('deliberation', 'approved')
        ORDER BY proposal.id
        """
    ).fetchall()
    enacted = []
    rejected = []
    for row in rows:
        if row["organization_status"] == "executed":
            enacted.append(int(enact_approved_rule(conn, row["id"], world_time)["id"]))
        elif row["organization_status"] in {"rejected", "expired", "cancelled"}:
            conn.execute(
                """
                UPDATE institutional_rule_proposals
                SET status = 'rejected', decided_at = ?
                WHERE id = ?
                """,
                (_now(world_time).isoformat(), row["id"]),
            )
            rejected.append(int(row["id"]))
        elif row["organization_status"] == "approved":
            conn.execute(
                "UPDATE institutional_rule_proposals SET status = 'approved' WHERE id = ?",
                (row["id"],),
            )
    return {"available": True, "enacted": enacted, "rejected": rejected}
