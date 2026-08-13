from __future__ import annotations

import json
from app.json_utils import json_dumps
from datetime import datetime, timezone
from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _json(value):
    return json_dumps(value, ensure_ascii=False, sort_keys=True)


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


def _table_exists(conn, name):
    return bool(conn.execute(f"PRAGMA table_info({name})").fetchall())


def longitudinal_runtime_available(conn):
    return _table_exists(conn, "longitudinal_profiles")


def _stage_for(conn, resident):
    population = None
    if _table_exists(conn, "population_profiles"):
        population = conn.execute(
            "SELECT * FROM population_profiles WHERE resident_id = ?",
            (resident["id"],),
        ).fetchone()
    lifecycle = population["lifecycle_status"] if population else "active"
    lifecycle_stage = population["lifecycle_stage"] if population else ""
    role = str(resident["role"] or "")
    if lifecycle == "departed":
        if lifecycle_stage == "graduation":
            return "graduated", "已毕业"
        return "departed", "已离开校园"
    if lifecycle == "leave":
        return "leave", "休学或暂离"
    if lifecycle_stage == "exchange":
        return "exchange", "交换阶段"
    if any(word in role for word in ("教师", "老师", "职员", "商家", "后勤")):
        return "employment", role
    if "学生" in role or "研究生" in role:
        return "academic", role
    return "campus_member", role or "校园成员"


def _sync_stage(conn, resident, now, trigger_type="runtime", trigger_id=""):
    stage_type, label = _stage_for(conn, resident)
    active = conn.execute(
        """
        SELECT * FROM life_course_stages
        WHERE resident_id = ? AND status = 'active'
        ORDER BY id DESC LIMIT 1
        """,
        (resident["id"],),
    ).fetchone()
    if active and active["stage_type"] == stage_type and active["stage_label"] == label:
        return dict(active), False
    if active:
        conn.execute(
            """
            UPDATE life_course_stages
            SET status = 'completed', ends_at = ? WHERE id = ?
            """,
            (now.isoformat(), active["id"]),
        )
    ordinal = conn.execute(
        "SELECT COUNT(*) AS value FROM life_course_stages WHERE resident_id = ?",
        (resident["id"],),
    ).fetchone()["value"]
    stage_key = f"life-stage:{resident['id']}:{int(ordinal) + 1}"
    cursor = conn.execute(
        """
        INSERT INTO life_course_stages
        (stage_key, resident_id, stage_type, stage_label, starts_at,
         trigger_type, trigger_id, evidence_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stage_key,
            resident["id"],
            stage_type,
            label,
            now.isoformat(),
            trigger_type,
            str(trigger_id or ""),
            _json(
                {
                    "role": resident["role"],
                    "lifecycle_evidence": bool(
                        _table_exists(conn, "population_profiles")
                    ),
                }
            ),
        ),
    )
    stage = dict(
        conn.execute(
            "SELECT * FROM life_course_stages WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    )
    conn.execute(
        """
        UPDATE longitudinal_profiles
        SET current_stage_key = ?, last_observed_at = ?,
            version = version + 1, updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (stage_key, now.isoformat(), resident["id"]),
    )
    return stage, True


def seed_longitudinal_runtime(conn, now=None):
    now = _time(now)
    residents = conn.execute(
        "SELECT id, name, role, goal, money, created_at FROM residents ORDER BY id"
    ).fetchall()
    created = stages = 0
    for resident in residents:
        before = conn.execute(
            "SELECT resident_id FROM longitudinal_profiles WHERE resident_id = ?",
            (resident["id"],),
        ).fetchone()
        if not before:
            conn.execute(
                """
                INSERT INTO longitudinal_profiles
                (resident_id, current_stage_key, goal_state_json,
                 economic_position_json, first_observed_at, last_observed_at)
                VALUES (?, '', ?, ?, ?, ?)
                """,
                (
                    resident["id"],
                    _json({"legacy_goal": resident["goal"]}),
                    _json({"legacy_money": resident["money"]}),
                    resident["created_at"] or now.isoformat(),
                    now.isoformat(),
                ),
            )
            created += 1
        _, stage_created = _sync_stage(
            conn, resident, now, "population_seed", resident["id"]
        )
        stages += int(stage_created)
    return {"profiles": len(residents), "created": created, "stages_created": stages}


def _turning_point(
    conn,
    *,
    point_key,
    resident_id,
    occurred_at,
    category,
    evidence_layer,
    title,
    summary,
    salience,
    source_type,
    source_id,
    cause_refs=None,
    consequence_refs=None,
    objective_evidence_count=1,
    subjective_evidence_count=0,
):
    conn.execute(
        """
        INSERT OR IGNORE INTO life_turning_points
        (point_key, resident_id, occurred_at, category, evidence_layer,
         title, summary, salience, objective_evidence_count,
         subjective_evidence_count, source_type, source_id,
         cause_refs_json, consequence_refs_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            point_key,
            resident_id,
            _time(occurred_at).isoformat(),
            category,
            evidence_layer,
            title,
            summary,
            float(salience),
            int(objective_evidence_count),
            int(subjective_evidence_count),
            source_type,
            str(source_id),
            _json(cause_refs or []),
            _json(consequence_refs or []),
        ),
    )


def _collect_population_points(conn):
    if not _table_exists(conn, "population_events"):
        return 0
    rows = conn.execute(
        """
        SELECT event.*, resident.name
        FROM population_events event
        JOIN residents resident ON resident.id = event.resident_id
        WHERE event.status = 'applied' ORDER BY event.id
        """
    ).fetchall()
    before = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    labels = {
        "new_student": "进入校园",
        "exchange_arrival": "开始交换阶段",
        "graduation": "完成学业并毕业",
        "transfer_program": "转换专业方向",
        "leave_of_absence": "进入休学阶段",
        "resume_study": "恢复在校学习",
        "withdrawal": "离开学业项目",
        "teacher_transfer": "教师岗位调动",
        "job_change": "岗位发生变化",
        "organization_join": "加入组织",
        "organization_leave": "离开组织",
        "leadership_change": "组织角色发生变化",
        "dorm_move": "居住地点调整",
        "city_migration": "迁离校园所在城市",
    }
    for event in rows:
        event_type = event["event_type"]
        if event_type not in labels:
            continue
        layer = (
            "formal_institution"
            if event_type in {"graduation", "withdrawal", "teacher_transfer"}
            else "individual"
        )
        _turning_point(
            conn,
            point_key=f"population-event:{event['id']}",
            resident_id=event["resident_id"],
            occurred_at=event["applied_at"] or event["effective_at"],
            category="transition",
            evidence_layer=layer,
            title=labels[event_type],
            summary=f"{event['name']}的生命周期状态因 {event_type} 发生变化。",
            salience=90 if event_type in {"graduation", "withdrawal", "city_migration"} else 70,
            source_type="population_event",
            source_id=event["id"],
            cause_refs=[{"type": event["source_type"], "id": event["source_id"]}],
            consequence_refs=_load(event["result_json"], {}),
        )
    after = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    return int(after) - int(before)


def _collect_learning_paths(conn):
    if not _table_exists(conn, "learning_updates"):
        return {"points": 0, "links": 0}
    updates = conn.execute(
        """
        SELECT learning.*, experience.objective_summary,
               experience.outcome, experience.occurred_at AS experience_at
        FROM learning_updates learning
        JOIN experience_records experience ON experience.id = learning.experience_id
        ORDER BY learning.id
        """
    ).fetchall()
    points_before = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    links_before = conn.execute(
        "SELECT COUNT(*) AS value FROM path_dependency_links"
    ).fetchone()["value"]
    for update in updates:
        before_state = _load(update["before_json"], {})
        after_state = _load(update["after_json"], {})
        _turning_point(
            conn,
            point_key=f"learning-update:{update['id']}",
            resident_id=update["resident_id"],
            occurred_at=update["occurred_at"],
            category="adaptation",
            evidence_layer="individual",
            title=f"策略发生可验证变化：{update['target_key']}",
            summary=update["update_reason"],
            salience=60,
            source_type="learning_update",
            source_id=update["id"],
            cause_refs=[{"type": "experience_record", "id": update["experience_id"]}],
            consequence_refs=[
                {
                    "target_type": update["target_type"],
                    "target_key": update["target_key"],
                    "before": before_state,
                    "after": after_state,
                }
            ],
            subjective_evidence_count=int(update["memory_id"] is not None),
        )
        direction = (
            "reinforces"
            if update["target_type"] in {"strategy", "habit", "skill"}
            else "redirects"
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO path_dependency_links
            (link_key, resident_id, from_type, from_id, to_type, to_id,
             mechanism, direction, strength, evidence_json, occurred_at)
            VALUES (?, ?, 'experience_record', ?, 'learning_update', ?,
                    ?, ?, ?, ?, ?)
            """,
            (
                f"experience:{update['experience_id']}:learning:{update['id']}",
                update["resident_id"],
                str(update["experience_id"]),
                str(update["id"]),
                update["update_reason"],
                direction,
                min(1.0, 0.5 + 0.1 * int(update["memory_id"] is not None)),
                _json(
                    {
                        "objective_summary": update["objective_summary"],
                        "outcome": update["outcome"],
                        "before": before_state,
                        "after": after_state,
                    }
                ),
                update["occurred_at"],
            ),
        )
    points_after = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    links_after = conn.execute(
        "SELECT COUNT(*) AS value FROM path_dependency_links"
    ).fetchone()["value"]
    return {
        "points": int(points_after) - int(points_before),
        "links": int(links_after) - int(links_before),
    }


def _collect_relationship_points(conn):
    if not _table_exists(conn, "relationship_change_events"):
        return 0
    rows = conn.execute(
        "SELECT * FROM relationship_change_events ORDER BY id"
    ).fetchall()
    before = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    for row in rows:
        change = max(
            abs(int(row["trust_after"]) - int(row["trust_before"])),
            abs(int(row["affinity_after"]) - int(row["affinity_before"])),
            abs(int(row["cooperation_after"]) - int(row["cooperation_before"])),
            abs(int(row["conflict_after"]) - int(row["conflict_before"])),
        )
        if change < 20:
            continue
        for resident_id, counterpart in (
            (row["from_resident_id"], row["to_resident_id"]),
            (row["to_resident_id"], row["from_resident_id"]),
        ):
            _turning_point(
                conn,
                point_key=f"relationship-change:{row['id']}:{resident_id}",
                resident_id=resident_id,
                occurred_at=row["created_at"],
                category="relationship",
                evidence_layer="individual",
                title="重要关系发生转折",
                summary=row["reason"] or row["interaction"],
                salience=min(100, 50 + change),
                source_type="relationship_change_event",
                source_id=row["id"],
                consequence_refs=[
                    {
                        "counterpart_resident_id": counterpart,
                        "trust_before": row["trust_before"],
                        "trust_after": row["trust_after"],
                    }
                ],
            )
    after = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    return int(after) - int(before)


def _collect_institution_points(conn):
    if not _table_exists(conn, "institutional_rule_proposals"):
        return 0
    rows = conn.execute(
        """
        SELECT * FROM institutional_rule_proposals
        WHERE status IN ('enacted', 'rejected') ORDER BY id
        """
    ).fetchall()
    before = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    for row in rows:
        _turning_point(
            conn,
            point_key=f"institution-proposal:{row['id']}",
            resident_id=row["proposer_resident_id"],
            occurred_at=row["enacted_at"] or row["decided_at"] or row["submitted_at"],
            category="institution",
            evidence_layer="formal_institution",
            title=(
                f"推动规则生效：{row['title']}"
                if row["status"] == "enacted"
                else f"规则提案未获通过：{row['title']}"
            ),
            summary=row["rationale"],
            salience=85 if row["status"] == "enacted" else 65,
            source_type="institutional_rule_proposal",
            source_id=row["id"],
            cause_refs=(
                [{"type": "norm_candidate", "id": row["source_norm_id"]}]
                if row["source_norm_id"]
                else []
            ),
            consequence_refs=[{"status": row["status"], "scope": row["scope_key"]}],
        )
    after = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    return int(after) - int(before)


def _collect_norm_points(conn):
    if not _table_exists(conn, "norm_responses"):
        return 0
    rows = conn.execute(
        """
        SELECT response.*, norm.name AS norm_name, norm.state AS norm_state
        FROM norm_responses response
        JOIN norm_candidates norm ON norm.id = response.norm_id
        WHERE response.response_type IN ('hidden_violate', 'open_challenge')
           OR response.detected = 1
        ORDER BY response.id
        """
    ).fetchall()
    before = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    for row in rows:
        _turning_point(
            conn,
            point_key=f"norm-response:{row['id']}",
            resident_id=row["resident_id"],
            occurred_at=row["occurred_at"],
            category="norm",
            evidence_layer="group_norm",
            title=f"对群体规范作出关键回应：{row['norm_name']}",
            summary=(
                f"公开行为为 {row['public_behavior']}，"
                f"私人立场为 {row['private_stance']}。"
            ),
            salience=75 if row["response_type"] == "open_challenge" else 60,
            source_type="norm_response",
            source_id=row["id"],
            cause_refs=[
                {
                    "type": "norm_candidate",
                    "id": row["norm_id"],
                    "state": row["norm_state"],
                }
            ],
            consequence_refs=_load(row["consequence_json"], {}),
        )
    after = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points"
    ).fetchone()["value"]
    return int(after) - int(before)


def _update_profile(conn, resident, now):
    strategies = (
        conn.execute(
            """
            SELECT strategy_key, context_key, expected_utility, confidence
            FROM strategy_states
            WHERE resident_id = ? AND status = 'active'
            ORDER BY confidence DESC, id LIMIT 20
            """,
            (resident["id"],),
        ).fetchall()
        if _table_exists(conn, "strategy_states")
        else []
    )
    learned_updates = (
        conn.execute(
            """
            SELECT id, target_type, target_key, before_json, after_json,
                   update_reason, occurred_at
            FROM learning_updates
            WHERE resident_id = ?
              AND target_type IN ('strategy', 'habit', 'skill')
            ORDER BY occurred_at DESC, id DESC LIMIT 50
            """,
            (resident["id"],),
        ).fetchall()
        if _table_exists(conn, "learning_updates")
        else []
    )
    relationships = conn.execute(
        """
        SELECT AVG(score) AS average_score, COUNT(*) AS relationship_count
        FROM relationships WHERE from_resident_id = ?
        """,
        (resident["id"],),
    ).fetchone()
    memberships = (
        conn.execute(
            """
            SELECT organization_id, member_role FROM organization_members
            WHERE resident_id = ? AND status = 'active'
            ORDER BY organization_id
            """,
            (resident["id"],),
        ).fetchall()
        if _table_exists(conn, "organization_members")
        else []
    )
    goals = (
        conn.execute(
            """
            SELECT id, horizon, title, priority, status, progress
            FROM agent_goals WHERE resident_id = ?
            ORDER BY horizon, priority DESC, id
            """,
            (resident["id"],),
        ).fetchall()
        if _table_exists(conn, "agent_goals")
        else []
    )
    conn.execute(
        """
        UPDATE longitudinal_profiles
        SET habit_state_json = ?, reputation_state_json = ?,
            social_position_json = ?, economic_position_json = ?,
            goal_state_json = ?, last_observed_at = ?,
            version = version + 1, updated_at = CURRENT_TIMESTAMP
        WHERE resident_id = ?
        """,
        (
            _json(
                {
                    "current_strategy_states": [dict(row) for row in strategies],
                    "evidence_linked_learning": [
                        dict(row) for row in learned_updates
                    ],
                }
            ),
            _json(
                {
                    "average_relationship_score": float(
                        relationships["average_score"] or 0
                    ),
                    "relationship_count": int(
                        relationships["relationship_count"] or 0
                    ),
                }
            ),
            _json({"active_memberships": [dict(row) for row in memberships]}),
            _json({"legacy_money": resident["money"]}),
            _json(
                {
                    "legacy_goal": resident["goal"],
                    "structured_goals": [dict(row) for row in goals],
                }
            ),
            now.isoformat(),
            resident["id"],
        ),
    )


def _aggregate(conn, resident_id, now):
    point_count = conn.execute(
        "SELECT COUNT(*) AS value FROM life_turning_points WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()["value"]
    path_count = conn.execute(
        "SELECT COUNT(*) AS value FROM path_dependency_links WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()["value"]
    stage_count = conn.execute(
        "SELECT COUNT(*) AS value FROM life_course_stages WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()["value"]
    cursor_values = {
        "turning_point_id": conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS value FROM life_turning_points WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()["value"],
        "path_link_id": conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS value FROM path_dependency_links WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()["value"],
    }
    key = f"longitudinal:{resident_id}:{now.date().isoformat()}"
    conn.execute(
        """
        INSERT INTO longitudinal_aggregations
        (aggregation_key, resident_id, window_type, window_start, window_end,
         metrics_json, source_cursors_json, evidence_completeness)
        VALUES (?, ?, 'life_to_date', '', ?, ?, ?, ?)
        ON CONFLICT (aggregation_key) DO UPDATE SET
            window_end = excluded.window_end,
            metrics_json = excluded.metrics_json,
            source_cursors_json = excluded.source_cursors_json,
            evidence_completeness = excluded.evidence_completeness
        """,
        (
            key,
            resident_id,
            now.isoformat(),
            _json(
                {
                    "stage_count": int(stage_count),
                    "turning_point_count": int(point_count),
                    "path_dependency_count": int(path_count),
                }
            ),
            _json(cursor_values),
            1.0 if _table_exists(conn, "experience_records") else 0.5,
        ),
    )


def process_longitudinal_runtime(conn, world_time=None):
    if not longitudinal_runtime_available(conn):
        return {"available": False}
    now = _time(world_time)
    seed = seed_longitudinal_runtime(conn, now)
    population_points = _collect_population_points(conn)
    learning = _collect_learning_paths(conn)
    relationship_points = _collect_relationship_points(conn)
    norm_points = _collect_norm_points(conn)
    institution_points = _collect_institution_points(conn)
    stage_changes = 0
    residents = conn.execute(
        "SELECT id, name, role, goal, money, created_at FROM residents ORDER BY id"
    ).fetchall()
    for resident in residents:
        population_event = (
            conn.execute(
                """
                SELECT id FROM population_events
                WHERE resident_id = ? AND status = 'applied'
                ORDER BY applied_at DESC, id DESC LIMIT 1
                """,
                (resident["id"],),
            ).fetchone()
            if _table_exists(conn, "population_events")
            else None
        )
        _, changed = _sync_stage(
            conn,
            resident,
            now,
            "population_event" if population_event else "runtime",
            population_event["id"] if population_event else "",
        )
        stage_changes += int(changed)
        _update_profile(conn, resident, now)
        _aggregate(conn, resident["id"], now)
        conn.execute(
            """
            INSERT OR IGNORE INTO trajectory_reconciliations
            (reconciliation_key, resident_id, check_type, status,
             expected_json, actual_json, checked_at, details_json)
            VALUES (?, ?, 'turning_point_evidence', 'passed', ?, ?, ?, ?)
            """,
            (
                f"trajectory-evidence:{resident['id']}:{now.date().isoformat()}",
                resident["id"],
                _json({"confirmed_requires_objective_evidence": True}),
                _json(
                    {
                        "invalid_confirmed_points": conn.execute(
                            """
                            SELECT COUNT(*) AS value FROM life_turning_points
                            WHERE resident_id = ? AND status = 'confirmed'
                              AND objective_evidence_count = 0
                            """,
                            (resident["id"],),
                        ).fetchone()["value"]
                    }
                ),
                now.isoformat(),
                _json({"memory_only_turning_points_allowed": False}),
            ),
        )
    return {
        "available": True,
        "profiles": seed["profiles"],
        "stage_changes": stage_changes,
        "new_turning_points": (
            population_points
            + learning["points"]
            + relationship_points
            + norm_points
            + institution_points
        ),
        "new_path_links": learning["links"],
    }


def get_life_course(conn, resident_id):
    profile = conn.execute(
        """
        SELECT profile.*, resident.name, resident.role
        FROM longitudinal_profiles profile
        JOIN residents resident ON resident.id = profile.resident_id
        WHERE profile.resident_id = ?
        """,
        (resident_id,),
    ).fetchone()
    if not profile:
        return None
    return {
        "profile": dict(profile),
        "stages": [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM life_course_stages
                WHERE resident_id = ? ORDER BY starts_at, id
                """,
                (resident_id,),
            ).fetchall()
        ],
        "turning_points": [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM life_turning_points
                WHERE resident_id = ? ORDER BY occurred_at, id
                """,
                (resident_id,),
            ).fetchall()
        ],
        "path_dependencies": [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM path_dependency_links
                WHERE resident_id = ? ORDER BY occurred_at, id
                """,
                (resident_id,),
            ).fetchall()
        ],
        "evidence_layers": {
            "individual": "个人经历、记忆、策略与关系事实",
            "group_norm": "群体规范及其证据",
            "formal_institution": "正式提案、决定与规则版本",
        },
    }
