from __future__ import annotations

import json
from typing import Optional

from app.json_utils import json_dumps
from datetime import datetime, timedelta, timezone

from app.economy.service import MINOR_PER_COIN, post_money_transfer_minor


DEFAULT_PERMISSIONS = {
    "chair": ["propose", "vote", "execute", "assign_roles", "commit"],
    "member": ["propose", "vote"],
    "auditor": ["vote", "audit"],
}

SEEDED_MEMBERS = {
    "学生会": [("苏晴", "chair"), ("白露", "member")],
    "创新社": [("秦越", "chair"), ("乔安然", "member"), ("韩墨", "member")],
    "校园商户联盟": [("周老板", "chair"), ("李姐", "member"), ("秦越", "member")],
    "图书馆服务组": [("何管理员", "chair"), ("校园后勤", "member"), ("王老师", "member")],
}


def _json(value) -> str:
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    parsed = parse_world_datetime(value)
    if parsed:
        return parsed
    raise ValueError(f"无法解析的时间格式: {value}")


def _now(value=None) -> datetime:
    return _parse_time(value) if value is not None else datetime.now(WORLD_TZ)


def _organization_account(conn, organization_id: int):
    return conn.execute(
        """
        SELECT ea.actor_key, la.account_key, la.balance_minor
        FROM economic_actors ea
        JOIN ledger_accounts la
          ON la.actor_id = ea.id AND la.account_code = 'cash'
        WHERE ea.organization_id = ? AND ea.status = 'active'
        """,
        (organization_id,),
    ).fetchone()


def organization_budget_state(conn, organization_id: int, exclude_proposal_id=None) -> dict:
    account = _organization_account(conn, organization_id)
    if not account:
        raise ValueError("组织尚未接入统一账本")
    params: list = [organization_id]
    exclusion = ""
    if exclude_proposal_id is not None:
        exclusion = "AND id <> ?"
        params.append(int(exclude_proposal_id))
    reserved = conn.execute(
        f"""
        SELECT COALESCE(SUM(requested_budget_minor), 0) AS value
        FROM organization_proposals
        WHERE organization_id = ?
          AND status IN ('pending', 'approved')
          {exclusion}
        """,
        tuple(params),
    ).fetchone()
    cash_minor = int(account["balance_minor"])
    reserved_minor = int(reserved["value"])
    commitments = conn.execute(
        """
        SELECT COALESCE(SUM(amount_minor), 0) AS value
        FROM organization_commitments
        WHERE organization_id = ? AND status = 'active'
        """,
        (organization_id,),
    ).fetchone()
    commitment_minor = int(commitments["value"])
    return {
        "actor_key": account["actor_key"],
        "account_key": account["account_key"],
        "cash_minor": cash_minor,
        "reserved_minor": reserved_minor + commitment_minor,
        "proposal_reserved_minor": reserved_minor,
        "commitment_reserved_minor": commitment_minor,
        "available_minor": max(0, cash_minor - reserved_minor - commitment_minor),
    }


def _member_authority(conn, organization_id: int, resident_id: int) -> dict:
    row = conn.execute(
        """
        SELECT ora.role_id, roles.role_key, roles.permissions_json,
               roles.spending_limit_minor, roles.vote_weight
        FROM organization_role_assignments ora
        JOIN organization_roles roles ON roles.id = ora.role_id
        JOIN organization_members members
          ON members.organization_id = ora.organization_id
         AND members.resident_id = ora.resident_id
        WHERE ora.organization_id = ? AND ora.resident_id = ?
          AND ora.status = 'active' AND roles.status = 'active'
          AND members.status = 'active'
        """,
        (organization_id, resident_id),
    ).fetchone()
    if not row:
        raise ValueError("居民不是该组织的有效成员")
    result = dict(row)
    result["permissions"] = json.loads(result.pop("permissions_json") or "[]")
    return result


def _require_permission(authority: dict, permission: str) -> None:
    if permission not in authority["permissions"]:
        raise ValueError(f"组织角色缺少 {permission} 权限")


def _execution_authority(conn, organization_id: int, preferred_resident_id: int) -> dict:
    try:
        preferred = _member_authority(conn, organization_id, preferred_resident_id)
    except ValueError:
        preferred = None
    if preferred and "execute" in preferred["permissions"]:
        return {**preferred, "resident_id": preferred_resident_id}
    rows = conn.execute(
        """
        SELECT assignment.resident_id, role.id AS role_id, role.role_key,
               role.permissions_json, role.spending_limit_minor, role.vote_weight
        FROM organization_role_assignments assignment
        JOIN organization_roles role ON role.id = assignment.role_id
        JOIN organization_members member
          ON member.organization_id = assignment.organization_id
         AND member.resident_id = assignment.resident_id
        WHERE assignment.organization_id = ?
          AND assignment.status = 'active' AND role.status = 'active'
          AND member.status = 'active'
        ORDER BY role.vote_weight DESC, assignment.resident_id
        """,
        (organization_id,),
    ).fetchall()
    for row in rows:
        authority = dict(row)
        authority["permissions"] = json.loads(
            authority.pop("permissions_json") or "[]"
        )
        if "execute" in authority["permissions"]:
            return authority
    raise ValueError("组织没有可承担执行责任的有效角色")


def _record_event(
    conn,
    *,
    event_key: str,
    organization_id: int,
    event_type: str,
    details: dict,
    proposal_id: Optional[int] = None,
    severity: str = "info",
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO organization_events
        (event_key, organization_id, proposal_id, event_type, severity, details_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            organization_id,
            proposal_id,
            event_type,
            severity,
            _json(details),
        ),
    )


def _proposal_details(conn, proposal_id: int) -> dict:
    row = conn.execute(
        """
        SELECT proposal.*, organization.name AS organization_name
        FROM organization_proposals proposal
        JOIN campus_organizations organization
          ON organization.id = proposal.organization_id
        WHERE proposal.id = ?
        """,
        (proposal_id,),
    ).fetchone()
    if not row:
        raise ValueError("组织提案不存在")
    result = dict(row)
    result["votes"] = [
        dict(vote)
        for vote in conn.execute(
            """
            SELECT vote.*, resident.name AS resident_name
            FROM organization_votes vote
            JOIN residents resident ON resident.id = vote.resident_id
            WHERE vote.proposal_id = ?
            ORDER BY vote.created_at, vote.resident_id
            """,
            (proposal_id,),
        ).fetchall()
    ]
    return result


def submit_organization_proposal(
    conn,
    *,
    proposal_key: str,
    organization_id: int,
    proposer_resident_id: int,
    proposal_type: str,
    title: str,
    description: str = "",
    requested_budget_minor: int = 0,
    target_actor_key: str = "",
    world_time=None,
    expires_at: str = "",
    source_type: str = "organization_runtime",
    source_id: str = "",
) -> dict:
    existing = conn.execute(
        "SELECT id FROM organization_proposals WHERE proposal_key = ?",
        (proposal_key,),
    ).fetchone()
    if existing:
        return _proposal_details(conn, int(existing["id"]))
    authority = _member_authority(conn, organization_id, proposer_resident_id)
    _require_permission(authority, "propose")
    requested_budget_minor = int(requested_budget_minor)
    if requested_budget_minor < 0:
        raise ValueError("提案预算不能为负数")
    if requested_budget_minor and not target_actor_key:
        raise ValueError("支出提案必须指定收款经济主体")
    if requested_budget_minor > int(authority["spending_limit_minor"]):
        raise ValueError("提案金额超过角色权限上限")
    budget = organization_budget_state(conn, organization_id)
    if requested_budget_minor > budget["available_minor"]:
        raise ValueError("组织可用预算不足")
    profile = conn.execute(
        "SELECT * FROM organization_runtime_profiles WHERE organization_id = ?",
        (organization_id,),
    ).fetchone()
    if not profile:
        raise ValueError("组织运行档案不存在")
    now = _now(world_time)
    earliest = now + timedelta(minutes=int(profile["decision_delay_minutes"]))
    cursor = conn.execute(
        """
        INSERT INTO organization_proposals
        (proposal_key, organization_id, proposer_resident_id, proposal_type,
         title, description, requested_budget_minor, target_actor_key,
         approvals_required, earliest_decision_at, expires_at, source_type,
         source_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal_key,
            organization_id,
            proposer_resident_id,
            proposal_type,
            title,
            description,
            requested_budget_minor,
            target_actor_key,
            int(profile["quorum_weight"]),
            earliest.isoformat(),
            expires_at,
            source_type,
            source_id,
        ),
    )
    proposal_id = int(cursor.lastrowid)
    _record_event(
        conn,
        event_key=f"proposal:{proposal_key}:submitted",
        organization_id=organization_id,
        proposal_id=proposal_id,
        event_type="proposal_submitted",
        details={
            "proposer_resident_id": proposer_resident_id,
            "requested_budget_minor": requested_budget_minor,
            "earliest_decision_at": earliest.isoformat(),
        },
    )
    return _proposal_details(conn, proposal_id)


def cast_organization_vote(
    conn,
    *,
    proposal_id: int,
    resident_id: int,
    decision: str,
    rationale: str = "",
) -> dict:
    proposal = _proposal_details(conn, proposal_id)
    if proposal["status"] != "pending":
        raise ValueError("只有待决提案可以表决")
    if decision not in {"approve", "reject"}:
        raise ValueError("表决只能是 approve 或 reject")
    authority = _member_authority(conn, int(proposal["organization_id"]), resident_id)
    _require_permission(authority, "vote")
    existing = conn.execute(
        "SELECT 1 FROM organization_votes WHERE proposal_id = ? AND resident_id = ?",
        (proposal_id, resident_id),
    ).fetchone()
    if existing:
        raise ValueError("成员已经对该提案表决")
    conn.execute(
        """
        INSERT INTO organization_votes
        (proposal_id, resident_id, decision, vote_weight, rationale)
        VALUES (?, ?, ?, ?, ?)
        """,
        (proposal_id, resident_id, decision, int(authority["vote_weight"]), rationale),
    )
    weights = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN decision = 'approve' THEN vote_weight ELSE 0 END), 0) approvals,
          COALESCE(SUM(CASE WHEN decision = 'reject' THEN vote_weight ELSE 0 END), 0) rejections
        FROM organization_votes WHERE proposal_id = ?
        """,
        (proposal_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE organization_proposals
        SET approvals_weight = ?, rejections_weight = ?
        WHERE id = ?
        """,
        (int(weights["approvals"]), int(weights["rejections"]), proposal_id),
    )
    _record_event(
        conn,
        event_key=f"proposal:{proposal['proposal_key']}:vote:{resident_id}",
        organization_id=int(proposal["organization_id"]),
        proposal_id=proposal_id,
        event_type="proposal_vote_recorded",
        details={"resident_id": resident_id, "decision": decision, "weight": int(authority["vote_weight"])},
        severity="warning" if decision == "reject" else "info",
    )
    if decision == "reject":
        _record_event(
            conn,
            event_key=f"proposal:{proposal['proposal_key']}:dissent:{resident_id}",
            organization_id=int(proposal["organization_id"]),
            proposal_id=proposal_id,
            event_type="organization_internal_dissent",
            severity="warning",
            details={"resident_id": resident_id, "rationale": rationale},
        )
    return _proposal_details(conn, proposal_id)


def finalize_organization_proposal(conn, proposal_id: int, *, world_time=None) -> dict:
    proposal = _proposal_details(conn, proposal_id)
    if proposal["status"] != "pending":
        return proposal
    now = _now(world_time)
    if now < _parse_time(proposal["earliest_decision_at"]):
        raise ValueError("组织提案仍在强制决策等待期")
    expired = bool(proposal["expires_at"]) and now >= _parse_time(proposal["expires_at"])
    approved = int(proposal["approvals_weight"]) >= int(proposal["approvals_required"])
    rejected = int(proposal["rejections_weight"]) >= int(proposal["approvals_required"])
    if expired:
        status, reason = "expired", "提案超过有效期"
    elif approved:
        budget = organization_budget_state(
            conn,
            int(proposal["organization_id"]),
            exclude_proposal_id=proposal_id,
        )
        if int(proposal["requested_budget_minor"]) > budget["available_minor"]:
            status, reason = "rejected", "决策时组织可用预算不足"
        else:
            status, reason = "approved", "达到组织法定赞成权重"
    elif rejected:
        status, reason = "rejected", "达到组织法定反对权重"
    else:
        return proposal
    conn.execute(
        """
        UPDATE organization_proposals
        SET status = ?, decision_reason = ?, decided_at = ?
        WHERE id = ?
        """,
        (status, reason, now.isoformat(), proposal_id),
    )
    _record_event(
        conn,
        event_key=f"proposal:{proposal['proposal_key']}:decision:{status}",
        organization_id=int(proposal["organization_id"]),
        proposal_id=proposal_id,
        event_type=f"proposal_{status}",
        severity="warning" if status in {"rejected", "expired"} else "info",
        details={"reason": reason, "decided_at": now.isoformat()},
    )
    return _proposal_details(conn, proposal_id)


def execute_organization_proposal(conn, proposal_id: int, *, world_time=None) -> dict:
    proposal = _proposal_details(conn, proposal_id)
    if proposal["status"] == "executed":
        return proposal
    if proposal["status"] != "approved":
        raise ValueError("只有已批准提案可以执行")
    authority = _execution_authority(
        conn,
        int(proposal["organization_id"]),
        int(proposal["proposer_resident_id"]),
    )
    _require_permission(authority, "execute")
    executor_resident_id = int(authority["resident_id"])
    now = _now(world_time)
    transaction_id = None
    amount_minor = int(proposal["requested_budget_minor"])
    if amount_minor:
        budget = organization_budget_state(
            conn,
            int(proposal["organization_id"]),
            exclude_proposal_id=proposal_id,
        )
        target = conn.execute(
            """
            SELECT la.account_key, ea.organization_id
            FROM economic_actors ea
            JOIN ledger_accounts la
              ON la.actor_id = ea.id AND la.account_code = 'cash'
            WHERE ea.actor_key = ? AND ea.status = 'active'
            """,
            (proposal["target_actor_key"],),
        ).fetchone()
        if not target:
            raise ValueError("提案收款经济主体不存在或没有现金账户")
        transfer = post_money_transfer_minor(
            conn,
            transaction_key=f"organization-proposal:{proposal['proposal_key']}",
            from_account_key=budget["account_key"],
            to_account_key=target["account_key"],
            amount_minor=amount_minor,
            transaction_type="organization_collective_action",
            source_type="organization_proposal",
            source_id=str(proposal_id),
            description=proposal["title"],
            metadata={
                "organization_id": int(proposal["organization_id"]),
                "proposal_key": proposal["proposal_key"],
            },
        )
        transaction_id = int(transfer["id"])
        if (
            target["organization_id"] is not None
            and int(target["organization_id"]) != int(proposal["organization_id"])
        ):
            record_organization_relationship_evidence(
                conn,
                from_organization_id=int(proposal["organization_id"]),
                to_organization_id=int(target["organization_id"]),
                relation_type="service",
                trust_delta=1,
                influence_delta=1,
                evidence={
                    "proposal_id": proposal_id,
                    "ledger_transaction_id": transaction_id,
                },
            )
    conn.execute(
        """
        UPDATE organization_proposals
        SET status = 'executed', ledger_transaction_id = ?, executed_at = ?
        WHERE id = ?
        """,
        (transaction_id, now.isoformat(), proposal_id),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO organization_commitments
        (commitment_key, organization_id, proposal_id, commitment_type,
         counterparty_actor_key, amount_minor, status,
         responsibility_resident_id, metadata_json, resolved_at)
        VALUES (?, ?, ?, ?, ?, ?, 'fulfilled', ?, ?, ?)
        """,
        (
            f"proposal:{proposal['proposal_key']}:execution",
            int(proposal["organization_id"]),
            proposal_id,
            proposal["proposal_type"],
            proposal["target_actor_key"],
            amount_minor,
            executor_resident_id,
            _json({"ledger_transaction_id": transaction_id}),
            now.isoformat(),
        ),
    )
    _record_event(
        conn,
        event_key=f"proposal:{proposal['proposal_key']}:executed",
        organization_id=int(proposal["organization_id"]),
        proposal_id=proposal_id,
        event_type="collective_action_executed",
        details={
            "ledger_transaction_id": transaction_id,
            "amount_minor": amount_minor,
            "executor_resident_id": executor_resident_id,
        },
    )
    return _proposal_details(conn, proposal_id)


def create_organization_commitment(
    conn,
    *,
    commitment_key: str,
    organization_id: int,
    responsible_resident_id: int,
    commitment_type: str,
    due_at: str,
    counterparty_actor_key: str = "",
    amount_minor: int = 0,
    proposal_id: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> dict:
    existing = conn.execute(
        "SELECT * FROM organization_commitments WHERE commitment_key = ?",
        (commitment_key,),
    ).fetchone()
    if existing:
        return dict(existing)
    authority = _member_authority(conn, organization_id, responsible_resident_id)
    _require_permission(authority, "commit")
    amount_minor = int(amount_minor)
    if amount_minor < 0:
        raise ValueError("组织承诺金额不能为负数")
    if amount_minor and not counterparty_actor_key:
        raise ValueError("资金承诺必须指定交易对手")
    if amount_minor > int(authority["spending_limit_minor"]):
        raise ValueError("组织承诺金额超过角色权限上限")
    budget = organization_budget_state(conn, organization_id)
    if amount_minor > budget["available_minor"]:
        raise ValueError("组织可用预算不足")
    cursor = conn.execute(
        """
        INSERT INTO organization_commitments
        (commitment_key, organization_id, proposal_id, commitment_type,
         counterparty_actor_key, amount_minor, due_at,
         responsibility_resident_id, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            commitment_key,
            organization_id,
            proposal_id,
            commitment_type,
            counterparty_actor_key,
            amount_minor,
            _parse_time(due_at).isoformat(),
            responsible_resident_id,
            _json(metadata or {}),
        ),
    )
    commitment_id = int(cursor.lastrowid)
    _record_event(
        conn,
        event_key=f"commitment:{commitment_key}:created",
        organization_id=organization_id,
        proposal_id=proposal_id,
        event_type="organization_commitment_created",
        details={
            "commitment_id": commitment_id,
            "responsibility_resident_id": responsible_resident_id,
            "due_at": _parse_time(due_at).isoformat(),
            "amount_minor": amount_minor,
        },
    )
    return dict(
        conn.execute(
            "SELECT * FROM organization_commitments WHERE id = ?",
            (commitment_id,),
        ).fetchone()
    )


def record_organization_relationship_evidence(
    conn,
    *,
    from_organization_id: int,
    to_organization_id: int,
    relation_type: str,
    trust_delta: int = 0,
    influence_delta: int = 0,
    evidence: Optional[dict] = None,
) -> dict:
    if from_organization_id == to_organization_id:
        raise ValueError("组织不能与自身建立外部关系")
    if relation_type not in {"neutral", "alliance", "service", "competition", "conflict"}:
        raise ValueError("不支持的组织关系类型")
    row = conn.execute(
        """
        SELECT * FROM organization_relationships
        WHERE from_organization_id = ? AND to_organization_id = ?
        """,
        (from_organization_id, to_organization_id),
    ).fetchone()
    evidence_items = json.loads(row["evidence_json"] or "[]") if row else []
    evidence_items.append(evidence or {})
    evidence_items = evidence_items[-50:]
    trust = max(0, min(100, int(row["trust"] if row else 50) + int(trust_delta)))
    influence = max(
        -100,
        min(100, int(row["influence"] if row else 0) + int(influence_delta)),
    )
    conn.execute(
        """
        INSERT INTO organization_relationships
        (from_organization_id, to_organization_id, relation_type, trust,
         influence, status, evidence_json)
        VALUES (?, ?, ?, ?, ?, 'active', ?)
        ON CONFLICT (from_organization_id, to_organization_id)
        DO UPDATE SET relation_type = excluded.relation_type,
                      trust = excluded.trust,
                      influence = excluded.influence,
                      status = 'active',
                      evidence_json = excluded.evidence_json,
                      updated_at = CURRENT_TIMESTAMP
        """,
        (
            from_organization_id,
            to_organization_id,
            relation_type,
            trust,
            influence,
            _json(evidence_items),
        ),
    )
    return dict(
        conn.execute(
            """
            SELECT * FROM organization_relationships
            WHERE from_organization_id = ? AND to_organization_id = ?
            """,
            (from_organization_id, to_organization_id),
        ).fetchone()
    )


def process_organization_runtime(conn, world_time=None) -> dict:
    if not conn.execute("PRAGMA table_info(organization_proposals)").fetchall():
        return {
            "available": False,
            "due_count": 0,
            "approved": [],
            "rejected": [],
            "executed": [],
            "blocked": [],
            "breached_commitments": [],
        }
    now = _now(world_time)
    due = conn.execute(
        """
        SELECT id FROM organization_proposals
        WHERE status = 'pending' AND earliest_decision_at <= ?
        ORDER BY earliest_decision_at, id
        """,
        (now.isoformat(),),
    ).fetchall()
    approved = []
    rejected = []
    executed = []
    for row in due:
        result = finalize_organization_proposal(conn, int(row["id"]), world_time=now)
        if result["status"] == "approved":
            approved.append(result["id"])
        elif result["status"] in {"rejected", "expired"}:
            rejected.append(result["id"])
    approved_rows = conn.execute(
        "SELECT id, proposal_key, organization_id FROM organization_proposals WHERE status = 'approved' ORDER BY id"
    ).fetchall()
    blocked = []
    for row in approved_rows:
        try:
            executed_result = execute_organization_proposal(
                conn,
                int(row["id"]),
                world_time=now,
            )
            executed.append(executed_result["id"])
        except ValueError as exc:
            blocked.append({"proposal_id": int(row["id"]), "reason": str(exc)})
            _record_event(
                conn,
                event_key=f"proposal:{row['proposal_key']}:execution-blocked:{now.isoformat()}",
                organization_id=int(row["organization_id"]),
                proposal_id=int(row["id"]),
                event_type="collective_action_blocked",
                severity="warning",
                details={"reason": str(exc)},
            )
    breached = []
    overdue = conn.execute(
        """
        SELECT * FROM organization_commitments
        WHERE status = 'active' AND due_at <> '' AND due_at <= ?
        ORDER BY due_at, id
        """,
        (now.isoformat(),),
    ).fetchall()
    for commitment in overdue:
        conn.execute(
            """
            UPDATE organization_commitments
            SET status = 'breached', resolved_at = ?
            WHERE id = ?
            """,
            (now.isoformat(), commitment["id"]),
        )
        conn.execute(
            """
            UPDATE organization_runtime_profiles
            SET reputation = CASE WHEN reputation >= 3 THEN reputation - 3 ELSE 0 END,
                updated_at = CURRENT_TIMESTAMP
            WHERE organization_id = ?
            """,
            (commitment["organization_id"],),
        )
        breached.append(int(commitment["id"]))
    return {
        "available": True,
        "due_count": len(due),
        "approved": approved,
        "rejected": rejected,
        "executed": executed,
        "blocked": blocked,
        "breached_commitments": breached,
    }


def seed_organization_runtime(conn) -> dict:
    organizations = conn.execute(
        "SELECT id, name, organization_type, goal, budget FROM campus_organizations ORDER BY id"
    ).fetchall()
    assignments = 0
    for organization in organizations:
        organization_id = int(organization["id"])
        governance_mode = (
            "executive"
            if organization["organization_type"] in {"business", "service"}
            else "council"
        )
        delay = 30 if governance_mode == "executive" else 60
        conn.execute(
            """
            INSERT OR IGNORE INTO organization_runtime_profiles
            (organization_id, governance_mode, mission, reputation,
             decision_delay_minutes, quorum_weight, metadata_json)
            VALUES (?, ?, ?, 50, ?, 2, ?)
            """,
            (
                organization_id,
                governance_mode,
                organization["goal"],
                delay,
                _json({"seed_version": "organization-runtime-v1"}),
            ),
        )
        budget_minor = max(0, int(organization["budget"])) * MINOR_PER_COIN
        role_specs = (
            ("chair", "负责人", DEFAULT_PERMISSIONS["chair"], budget_minor, 2),
            ("member", "成员", DEFAULT_PERMISSIONS["member"], budget_minor // 4, 1),
            ("auditor", "监督员", DEFAULT_PERMISSIONS["auditor"], 0, 1),
        )
        for role_key, display_name, permissions, spending_limit, vote_weight in role_specs:
            conn.execute(
                """
                INSERT OR IGNORE INTO organization_roles
                (organization_id, role_key, display_name, permissions_json,
                 spending_limit_minor, vote_weight)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    organization_id,
                    role_key,
                    display_name,
                    _json(permissions),
                    spending_limit,
                    vote_weight,
                ),
            )
        for resident_name, role_key in SEEDED_MEMBERS.get(organization["name"], []):
            resident = conn.execute(
                "SELECT id FROM residents WHERE name = ?",
                (resident_name,),
            ).fetchone()
            role = conn.execute(
                """
                SELECT id FROM organization_roles
                WHERE organization_id = ? AND role_key = ?
                """,
                (organization_id, role_key),
            ).fetchone()
            if not resident or not role:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO organization_members
                (organization_id, resident_id, member_role, joined_day, status)
                VALUES (?, ?, ?, 1, 'active')
                """,
                (organization_id, resident["id"], role_key),
            )
            before = conn.execute(
                """
                SELECT 1 FROM organization_role_assignments
                WHERE organization_id = ? AND resident_id = ?
                """,
                (organization_id, resident["id"]),
            ).fetchone()
            conn.execute(
                """
                INSERT OR IGNORE INTO organization_role_assignments
                (organization_id, resident_id, role_id)
                VALUES (?, ?, ?)
                """,
                (organization_id, resident["id"], role["id"]),
            )
            assignments += int(before is None)
    organization_ids = [int(row["id"]) for row in organizations]
    for from_id in organization_ids:
        for to_id in organization_ids:
            if from_id == to_id:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO organization_relationships
                (from_organization_id, to_organization_id)
                VALUES (?, ?)
                """,
                (from_id, to_id),
            )
    counts = {}
    for table in (
        "organization_runtime_profiles",
        "organization_roles",
        "organization_role_assignments",
        "organization_relationships",
    ):
        counts[table] = int(
            conn.execute(f"SELECT COUNT(*) AS value FROM {table}").fetchone()["value"]
        )
    return {**counts, "assignments_created": assignments}
