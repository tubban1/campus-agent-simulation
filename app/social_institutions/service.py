from __future__ import annotations

from typing import Optional

from datetime import datetime, timedelta, timezone
import hashlib
import json

from app.economy.service import post_money_transfer_minor


RULE_VERSION = "social-institution-v1"


def _json(value) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)


def _load(value, default=None):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return {} if default is None else default


from app.world_runtime.clock import parse_world_datetime, WORLD_TZ


def _now(value=None) -> datetime:
    if value is None:
        return datetime.now(WORLD_TZ)
    if isinstance(value, datetime):
        return value.astimezone(WORLD_TZ) if value.tzinfo else value.replace(tzinfo=WORLD_TZ)
    return parse_world_datetime(value) or datetime.now(WORLD_TZ)


def _clamp(value, low=0, high=100) -> int:
    return max(low, min(high, int(round(value))))


def _role_matches(role: str, allowed_roles: list[str]) -> bool:
    normalized = str(role or "").strip().casefold()
    return any(
        allowed
        and (
            normalized == allowed
            or allowed in normalized
            or normalized in allowed
        )
        for allowed in (str(item).strip().casefold() for item in allowed_roles)
    )


def social_institution_runtime_available(conn) -> bool:
    return bool(conn.execute("PRAGMA table_info(information_claims)").fetchall())


def seed_social_institution_runtime(conn, world_time=None) -> dict:
    now = _now(world_time)
    channels = (
        ("in-person", "线下交谈", "in_person", "direct", 90, 0, 0),
        ("group-chat", "群聊", "group_chat", "group", 82, 3, 5),
        ("social-feed", "校园社交信息流", "social_feed", "network", 68, 10, 0),
        ("authority-notice", "权威公告", "authority_notice", "broadcast", 98, 2, 90),
    )
    channel_created = 0
    for row in channels:
        before = conn.execute(
            "SELECT id FROM communication_channels WHERE channel_key = ?", (row[0],)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO communication_channels
            (channel_key, name, channel_type, reach_mode, base_fidelity,
             delay_minutes, authority_weight)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        channel_created += int(before is None)

    rules = (
        (
            "student-leave",
            "学生请假审批",
            "leave_request",
            "system:campus-services",
            "approve",
            ["学生", "student"],
            ["reason", "time_window"],
            {"approval_threshold": 45, "opportunity_delta": 2},
            60,
            1,
        ),
        (
            "restricted-access",
            "受限资源访问申请",
            "access_request",
            "system:campus-services",
            "manage",
            [],
            ["purpose", "resource"],
            {"approval_threshold": 62, "opportunity_delta": 5},
            120,
            1,
        ),
        (
            "conduct-review",
            "行为违规审查",
            "conduct_violation",
            "system:campus-services",
            "audit",
            [],
            ["source_event_id"],
            {"sanction_minor": 300, "approval_threshold": 55, "opportunity_delta": -8},
            90,
            1,
        ),
        (
            "contribution-reward",
            "公共贡献奖励",
            "reward_nomination",
            "system:campus-services",
            "approve",
            [],
            ["source_event_id"],
            {"reward_minor": 500, "approval_threshold": 65, "opportunity_delta": 5},
            90,
            1,
        ),
        (
            "institutional-appeal",
            "制度决定申诉",
            "appeal",
            "system:campus-services",
            "audit",
            [],
            ["parent_case_id", "appeal_reason"],
            {"approval_threshold": 60, "opportunity_delta": 3},
            180,
            0,
        ),
    )
    rule_created = 0
    for rule in rules:
        before = conn.execute(
            "SELECT id FROM institutional_rules WHERE rule_key = ?", (rule[0],)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO institutional_rules
            (rule_key, name, case_type, authority_actor_key,
             required_permission, applies_to_roles_json,
             evidence_requirements_json, parameters_json,
             decision_delay_minutes, appeal_allowed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule[0], rule[1], rule[2], rule[3], rule[4],
                _json(rule[5]), _json(rule[6]), _json(rule[7]), rule[8], rule[9],
            ),
        )
        rule_created += int(before is None)
    power = calculate_power_profiles(conn, now)
    return {
        "channels": int(conn.execute("SELECT COUNT(*) value FROM communication_channels").fetchone()["value"]),
        "channels_created": channel_created,
        "rules": int(conn.execute("SELECT COUNT(*) value FROM institutional_rules").fetchone()["value"]),
        "rules_created": rule_created,
        "power_profiles": len(power),
    }


def calculate_power_profiles(conn, world_time=None) -> list[int]:
    if not social_institution_runtime_available(conn):
        return []
    now = _now(world_time)
    updated = []
    residents = conn.execute("SELECT id FROM residents ORDER BY id").fetchall()
    for resident in residents:
        resident_id = int(resident["id"])
        roles = []
        if conn.execute("PRAGMA table_info(organization_role_assignments)").fetchall():
            roles = conn.execute(
                """
                SELECT role.role_key, role.vote_weight, role.spending_limit_minor,
                       role.permissions_json
                FROM organization_role_assignments assignment
                JOIN organization_roles role ON role.id = assignment.role_id
                WHERE assignment.resident_id = ? AND assignment.status = 'active'
                  AND role.status = 'active'
                """,
                (resident_id,),
            ).fetchall()
        formal = min(
            100,
            sum(
                18
                + int(role["vote_weight"]) * 8
                + min(20, int(role["spending_limit_minor"]) // 5000)
                for role in roles
            ),
        )
        incoming = conn.execute(
            """
            SELECT COUNT(*) reach, COALESCE(AVG(score), 0) average_score
            FROM relationships WHERE to_resident_id = ? AND score > 0
            """,
            (resident_id,),
        ).fetchone()
        reach_count = int(incoming["reach"])
        average_score = float(incoming["average_score"])
        network_reach = _clamp(reach_count * 7)
        informal = _clamp(average_score * 0.65 + network_reach * 0.35)
        shared = conn.execute(
            """
            SELECT COUNT(*) value FROM information_transmissions
            WHERE sender_resident_id = ? AND status = 'received'
            """,
            (resident_id,),
        ).fetchone()
        accepted = conn.execute(
            """
            SELECT COUNT(*) value
            FROM information_transmissions transmission
            JOIN information_exposures exposure
              ON exposure.transmission_id = transmission.id
            WHERE transmission.sender_resident_id = ?
              AND exposure.reaction IN ('accept', 'share')
            """,
            (resident_id,),
        ).fetchone()
        share_count = int(shared["value"])
        information_influence = _clamp(
            int(accepted["value"]) * 8 + max(0, share_count - int(accepted["value"])) * 2
        )
        existing = conn.execute(
            "SELECT institutional_trust FROM resident_power_profiles WHERE resident_id = ?",
            (resident_id,),
        ).fetchone()
        trust = int(existing["institutional_trust"]) if existing else 50
        capability = conn.execute(
            """
            SELECT institutional_access FROM agent_capability_profiles
            WHERE resident_id = ?
            """,
            (resident_id,),
        ).fetchone()
        base_access = int(capability["institutional_access"]) if capability else 50
        procedural = _clamp(base_access * 0.7 + formal * 0.2 + trust * 0.1)
        evidence = {
            "roles": [dict(role) for role in roles],
            "incoming_relationship_count": reach_count,
            "incoming_relationship_average": round(average_score, 2),
            "transmissions": share_count,
            "accepted_transmissions": int(accepted["value"]),
        }
        conn.execute(
            """
            INSERT INTO resident_power_profiles
            (resident_id, formal_authority, informal_influence, network_reach,
             information_influence, institutional_trust, procedural_access,
             evidence_json, calculated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resident_id) DO UPDATE SET
                formal_authority = excluded.formal_authority,
                informal_influence = excluded.informal_influence,
                network_reach = excluded.network_reach,
                information_influence = excluded.information_influence,
                institutional_trust = excluded.institutional_trust,
                procedural_access = excluded.procedural_access,
                evidence_json = excluded.evidence_json,
                calculated_at = excluded.calculated_at,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                resident_id, formal, informal, network_reach,
                information_influence, trust, procedural,
                _json(evidence), now.isoformat(),
            ),
        )
        updated.append(resident_id)
    return updated


def _claim_classification(event) -> tuple[str, str, int]:
    event_type = str(event["event_type"] or "")
    if any(token in event_type for token in ("organization", "policy", "admin")):
        return "announcement", "verified", 92
    if any(token in event_type for token in ("chat", "relationship", "social")):
        return "gossip", "unverified", 58
    if any(token in event_type for token in ("failed", "conflict", "abandoned")):
        return "rumor", "disputed", 48
    return "fact", "verified", 82


def ingest_world_information(conn, world_time=None, limit=20) -> list[int]:
    if not social_institution_runtime_available(conn):
        return []
    now = _now(world_time)
    events = conn.execute(
        """
        SELECT event.id, event.event_type, event.resident_id, event.location,
               event.title, event.content, event.occurred_at, event.source_type,
               event.source_id
        FROM world_event_stream event
        LEFT JOIN information_claims claim
          ON claim.source_type = 'world_event' AND claim.source_id = CAST(event.id AS TEXT)
        WHERE claim.id IS NULL
          AND event.event_type NOT IN ('world_tick_started', 'world_tick_complete',
                                      'observer_session', 'observer_model_detail')
        ORDER BY event.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    created = []
    for event in reversed(events):
        claim_type, truth, reliability = _claim_classification(event)
        claim_key = f"world-event:{event['id']}"
        subject_type = "resident" if event["resident_id"] else "world_event"
        subject_key = str(event["resident_id"] or event["id"])
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO information_claims
            (claim_key, claim_type, title, canonical_content, subject_type,
             subject_key, source_type, source_id, origin_resident_id,
             truth_status, source_reliability, occurred_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, 'world_event', ?, ?, ?, ?, ?, ?)
            """,
            (
                claim_key, claim_type, event["title"], event["content"],
                subject_type, subject_key, str(event["id"]), event["resident_id"],
                truth, reliability, event["occurred_at"] or now.isoformat(),
                _json({"event_type": event["event_type"], "location": event["location"]}),
            ),
        )
        claim = conn.execute(
            "SELECT * FROM information_claims WHERE claim_key = ?", (claim_key,)
        ).fetchone()
        if not claim:
            continue
        version_key = f"{claim_key}:original"
        conn.execute(
            """
            INSERT OR IGNORE INTO information_versions
            (version_key, claim_id, content, fidelity, distortion_score,
             transformation_type, created_by_resident_id, created_at_world,
             metadata_json)
            VALUES (?, ?, ?, 100, 0, 'original', ?, ?, ?)
            """,
            (
                version_key, claim["id"], event["content"], event["resident_id"],
                event["occurred_at"] or now.isoformat(),
                _json({"source_event_id": event["id"]}),
            ),
        )
        if cursor.lastrowid:
            created.append(int(claim["id"]))
    if conn.execute("PRAGMA table_info(market_friction_events)").fetchall():
        market_rows = conn.execute(
            """
            SELECT friction.id, friction.friction_type, friction.occurred_at,
                   item.id AS item_id, item.item_key, item.name
            FROM market_friction_events friction
            JOIN market_mechanisms mechanism ON mechanism.id = friction.mechanism_id
            JOIN catalog_items item ON item.id = mechanism.item_id
            LEFT JOIN information_claims claim
              ON claim.source_type = 'market_friction'
             AND claim.source_id = CAST(friction.id AS TEXT)
            WHERE claim.id IS NULL
            ORDER BY friction.id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        for market in reversed(market_rows):
            claim_key = f"market-friction:{market['id']}"
            content = (
                f"{market['name']}出现"
                f"{'供应不足' if market['friction_type'] == 'stockout' else '配给或交易摩擦'}。"
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO information_claims
                (claim_key, claim_type, title, canonical_content, subject_type,
                 subject_key, source_type, source_id, truth_status,
                 source_reliability, occurred_at, metadata_json)
                VALUES (?, 'fact', ?, ?, 'catalog_item', ?, 'market_friction',
                        ?, 'verified', 88, ?, ?)
                """,
                (
                    claim_key, f"{market['name']}市场状态", content,
                    market["item_key"], str(market["id"]), market["occurred_at"],
                    _json({"item_id": market["item_id"], "friction_type": market["friction_type"]}),
                ),
            )
            claim = conn.execute(
                "SELECT * FROM information_claims WHERE claim_key = ?", (claim_key,)
            ).fetchone()
            conn.execute(
                """
                INSERT OR IGNORE INTO information_versions
                (version_key, claim_id, content, fidelity, distortion_score,
                 transformation_type, created_at_world, metadata_json)
                VALUES (?, ?, ?, 100, 0, 'original', ?, ?)
                """,
                (
                    f"{claim_key}:original", claim["id"], content,
                    market["occurred_at"], _json({"market_friction_id": market["id"]}),
                ),
            )
            created.append(int(claim["id"]))
    return created


def _channel(conn, channel_key):
    return conn.execute(
        "SELECT * FROM communication_channels WHERE channel_key = ? AND status = 'active'",
        (channel_key,),
    ).fetchone()


def _profile(conn, resident_id):
    return conn.execute(
        """
        SELECT profile.*, capability.information_literacy,
               capability.language_access
        FROM resident_power_profiles profile
        LEFT JOIN agent_capability_profiles capability
          ON capability.resident_id = profile.resident_id
        WHERE profile.resident_id = ?
        """,
        (resident_id,),
    ).fetchone()


def _record_exposure(conn, transmission, claim, version, channel, now) -> int:
    profile = _profile(conn, int(transmission["recipient_resident_id"]))
    literacy = int(profile["information_literacy"] or 50) if profile else 50
    language = int(profile["language_access"] or 50) if profile else 50
    trust = int(profile["institutional_trust"] or 50) if profile else 50
    comprehension = _clamp(literacy * 0.65 + language * 0.35)
    credibility = _clamp(
        int(claim["source_reliability"]) * 0.45
        + int(version["fidelity"]) * 0.30
        + int(channel["authority_weight"]) * 0.15
        + trust * 0.10
    )
    attention = _clamp(45 + int(channel["authority_weight"]) * 0.35 + (100 - int(version["fidelity"])) * 0.1)
    if credibility >= 68:
        reaction, stance = "accept", "believes"
    elif credibility >= 42:
        reaction, stance = "doubt", "uncertain"
    else:
        reaction, stance = "reject", "disbelieves"
    exposure_key = f"exposure:{transmission['id']}"
    conn.execute(
        """
        INSERT OR IGNORE INTO information_exposures
        (exposure_key, transmission_id, resident_id, claim_id, version_id,
         attention_score, comprehension_score, credibility_score, reaction,
         occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            exposure_key, transmission["id"], transmission["recipient_resident_id"],
            claim["id"], version["id"], attention, comprehension, credibility,
            reaction, now.isoformat(),
        ),
    )
    existing = conn.execute(
        """
        SELECT * FROM information_beliefs
        WHERE resident_id = ? AND claim_id = ?
        """,
        (transmission["recipient_resident_id"], claim["id"]),
    ).fetchone()
    if existing:
        confidence = _clamp(int(existing["confidence"]) * 0.6 + credibility * 0.4)
        if version["transformation_type"] == "clarification":
            stance = "corrected"
        conn.execute(
            """
            UPDATE information_beliefs
            SET believed_version_id = ?, confidence = ?, stance = ?,
                exposure_count = exposure_count + 1, last_updated_at = ?,
                metadata_json = ?
            WHERE resident_id = ? AND claim_id = ?
            """,
            (
                version["id"], confidence, stance, now.isoformat(),
                _json({"latest_transmission_id": transmission["id"]}),
                transmission["recipient_resident_id"], claim["id"],
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO information_beliefs
            (resident_id, claim_id, believed_version_id, confidence, stance,
             exposure_count, first_formed_at, last_updated_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                transmission["recipient_resident_id"], claim["id"], version["id"],
                credibility, stance, now.isoformat(), now.isoformat(),
                _json({"first_transmission_id": transmission["id"]}),
            ),
        )
    sender_id = transmission["sender_resident_id"]
    if sender_id and int(sender_id) != int(transmission["recipient_resident_id"]):
        relation_delta = 0
        note = ""
        if (
            claim["truth_status"] in {"disputed", "false", "corrected"}
            and int(version["distortion_score"]) >= 30
        ):
            relation_delta, note = -2, "传播高失真或已被澄清的信息"
        elif claim["truth_status"] == "verified" and credibility >= 70:
            relation_delta, note = 1, "分享了可核实且有用的信息"
        if relation_delta:
            conn.execute(
                """
                INSERT INTO relationships
                (from_resident_id, to_resident_id, score, notes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(from_resident_id, to_resident_id) DO UPDATE SET
                    score = CASE
                        WHEN relationships.score + excluded.score > 100 THEN 100
                        WHEN relationships.score + excluded.score < -100 THEN -100
                        ELSE relationships.score + excluded.score
                    END,
                    notes = CASE
                        WHEN relationships.notes = '' THEN excluded.notes
                        ELSE relationships.notes || '; ' || excluded.notes
                    END
                """,
                (
                    transmission["recipient_resident_id"], sender_id,
                    relation_delta, note,
                ),
            )
            if conn.execute("PRAGMA table_info(relationship_dynamics)").fetchall():
                conn.execute(
                    """
                    UPDATE relationship_dynamics
                    SET trust = CASE
                        WHEN trust + ? > 100 THEN 100
                        WHEN trust + ? < 0 THEN 0
                        ELSE trust + ?
                    END
                    WHERE from_resident_id = ? AND to_resident_id = ?
                    """,
                    (
                        relation_delta, relation_delta, relation_delta,
                        transmission["recipient_resident_id"], sender_id,
                    ),
                )
    exposure = conn.execute(
        "SELECT id FROM information_exposures WHERE exposure_key = ?", (exposure_key,)
    ).fetchone()
    return int(exposure["id"])


def transmit_information(
    conn,
    *,
    claim_id: int,
    version_id: int,
    recipient_resident_id: int,
    channel_key: str,
    world_time=None,
    sender_resident_id: Optional[int] = None,
    sender_actor_key: str = "",
    parent_transmission_id: Optional[int] = None,
    evidence_type: str = "broadcast",
    evidence_id: str = "",
    location: str = "",
) -> dict:
    now = _now(world_time)
    channel = _channel(conn, channel_key)
    claim = conn.execute(
        "SELECT * FROM information_claims WHERE id = ? AND status = 'active'",
        (claim_id,),
    ).fetchone()
    parent_version = conn.execute(
        "SELECT * FROM information_versions WHERE id = ? AND claim_id = ?",
        (version_id, claim_id),
    ).fetchone()
    if not channel or not claim or not parent_version:
        raise ValueError("传播所需的渠道、主张或版本不存在")
    salt = f"{claim_id}|{version_id}|{sender_resident_id}|{recipient_resident_id}|{channel_key}"
    noise = int.from_bytes(hashlib.sha256(salt.encode()).digest()[:2], "big") % 21
    sender = _profile(conn, sender_resident_id) if sender_resident_id else None
    literacy = int(sender["information_literacy"] or 50) if sender else 90
    distortion = _clamp(
        int(parent_version["distortion_score"])
        + max(0, 100 - int(channel["base_fidelity"])) // 3
        + max(0, 60 - literacy) // 5
        + noise // 4
    )
    transform = "verbatim"
    content = parent_version["content"]
    if distortion >= 45:
        transform = "misreading"
        content = f"有人声称：{claim['title']}，而且情况可能比公开内容更严重。"
    elif distortion >= 25:
        transform = "emphasis"
        content = f"转述重点：{claim['title']}。{str(parent_version['content'])[:100]}"
    elif distortion >= 12:
        transform = "summary"
        content = f"{claim['title']}：{str(parent_version['content'])[:120]}"
    if channel_key == "authority-notice":
        distortion = 0
        transform = (
            "clarification"
            if parent_version["transformation_type"] == "clarification"
            else "verbatim"
        )
        content = (
            parent_version["content"]
            if transform == "clarification"
            else claim["canonical_content"]
        )
    version_key = f"version:{hashlib.sha256(salt.encode()).hexdigest()[:24]}"
    conn.execute(
        """
        INSERT OR IGNORE INTO information_versions
        (version_key, claim_id, parent_version_id, content, fidelity,
         distortion_score, transformation_type, created_by_resident_id,
         created_at_world, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_key, claim_id, version_id, content, 100 - distortion,
            distortion, transform, sender_resident_id, now.isoformat(),
            _json({"channel_key": channel_key, "noise": noise}),
        ),
    )
    version = conn.execute(
        "SELECT * FROM information_versions WHERE version_key = ?", (version_key,)
    ).fetchone()
    transmission_key = (
        f"transmission:{claim_id}:{version['id']}:{recipient_resident_id}:"
        f"{parent_transmission_id or 0}:{channel_key}"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO information_transmissions
        (transmission_key, claim_id, version_id, parent_transmission_id,
         channel_id, sender_resident_id, sender_actor_key,
         recipient_resident_id, evidence_type, evidence_id, location,
         sent_at, received_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            transmission_key, claim_id, version["id"], parent_transmission_id,
            channel["id"], sender_resident_id, sender_actor_key,
            recipient_resident_id, evidence_type, evidence_id, location,
            now.isoformat(),
            (now + timedelta(minutes=int(channel["delay_minutes"]))).isoformat(),
        ),
    )
    transmission = conn.execute(
        "SELECT * FROM information_transmissions WHERE transmission_key = ?",
        (transmission_key,),
    ).fetchone()
    exposure_id = _record_exposure(conn, transmission, claim, version, channel, now)
    return {
        **dict(transmission),
        "version": dict(version),
        "exposure_id": exposure_id,
    }


def _initial_recipients(conn, claim):
    metadata = _load(claim["metadata_json"])
    location = metadata.get("location") or ""
    if claim["claim_type"] == "announcement":
        return [
            (int(row["id"]), "authority-notice", "broadcast", "")
            for row in conn.execute("SELECT id FROM residents ORDER BY id").fetchall()
        ]
    if claim["origin_resident_id"]:
        rows = conn.execute(
            """
            SELECT id FROM residents
            WHERE id <> ? AND (? = '' OR location = ?)
            ORDER BY id LIMIT 4
            """,
            (claim["origin_resident_id"], location, location),
        ).fetchall()
        return [
            (int(row["id"]), "in-person", "co_location", location) for row in rows
        ]
    return [
        (int(row["id"]), "social-feed", "broadcast", "")
        for row in conn.execute("SELECT id FROM residents ORDER BY id LIMIT 4").fetchall()
    ]


def propagate_information(conn, world_time=None, limit=30) -> dict:
    if not social_institution_runtime_available(conn):
        return {"available": False, "transmissions": [], "clarifications": []}
    now = _now(world_time)
    transmitted = []
    claims = conn.execute(
        "SELECT * FROM information_claims WHERE status = 'active' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    for claim in claims:
        original = conn.execute(
            "SELECT * FROM information_versions WHERE claim_id = ? ORDER BY id LIMIT 1",
            (claim["id"],),
        ).fetchone()
        existing_count = int(conn.execute(
            "SELECT COUNT(*) value FROM information_transmissions WHERE claim_id = ?",
            (claim["id"],),
        ).fetchone()["value"])
        if existing_count == 0:
            for recipient_id, channel_key, evidence_type, location in _initial_recipients(conn, claim):
                item = transmit_information(
                    conn,
                    claim_id=int(claim["id"]),
                    version_id=int(original["id"]),
                    recipient_resident_id=recipient_id,
                    channel_key=channel_key,
                    world_time=now,
                    sender_resident_id=claim["origin_resident_id"],
                    sender_actor_key=claim["origin_actor_key"],
                    evidence_type=evidence_type,
                    evidence_id=claim["source_id"],
                    location=location,
                )
                transmitted.append(int(item["id"]))
            continue
        frontier = conn.execute(
            """
            SELECT transmission.*, exposure.reaction
            FROM information_transmissions transmission
            JOIN information_exposures exposure
              ON exposure.transmission_id = transmission.id
            WHERE transmission.claim_id = ?
              AND exposure.reaction IN ('accept', 'share')
            ORDER BY transmission.id DESC LIMIT 8
            """,
            (claim["id"],),
        ).fetchall()
        already = {
            int(row["recipient_resident_id"])
            for row in conn.execute(
                "SELECT recipient_resident_id FROM information_transmissions WHERE claim_id = ?",
                (claim["id"],),
            ).fetchall()
        }
        for parent in frontier:
            contacts = conn.execute(
                """
                SELECT relation.to_resident_id, relation.score
                FROM relationships relation
                WHERE relation.from_resident_id = ? AND relation.score >= 20
                ORDER BY relation.score DESC, relation.to_resident_id LIMIT 2
                """,
                (parent["recipient_resident_id"],),
            ).fetchall()
            for contact in contacts:
                recipient_id = int(contact["to_resident_id"])
                if recipient_id in already:
                    continue
                item = transmit_information(
                    conn,
                    claim_id=int(claim["id"]),
                    version_id=int(parent["version_id"]),
                    recipient_resident_id=recipient_id,
                    channel_key="group-chat" if int(contact["score"]) >= 55 else "social-feed",
                    world_time=now,
                    sender_resident_id=int(parent["recipient_resident_id"]),
                    parent_transmission_id=int(parent["id"]),
                    evidence_type="relationship",
                    evidence_id=f"{parent['recipient_resident_id']}:{recipient_id}",
                )
                transmitted.append(int(item["id"]))
                already.add(recipient_id)
    clarifications = generate_clarifications(conn, now)
    return {
        "available": True,
        "transmissions": transmitted,
        "clarifications": clarifications,
    }


def generate_clarifications(conn, world_time=None) -> list[int]:
    now = _now(world_time)
    rows = conn.execute(
        """
        SELECT c.*, stats.exposed, stats.max_distortion
        FROM information_claims c
        JOIN (
            SELECT ver.claim_id,
                   COUNT(DISTINCT exposure.resident_id) AS exposed,
                   MAX(ver.distortion_score) AS max_distortion
            FROM information_versions ver
            JOIN information_exposures exposure ON exposure.claim_id = ver.claim_id
            GROUP BY ver.claim_id
            HAVING COUNT(DISTINCT exposure.resident_id) >= 3
               AND MAX(ver.distortion_score) >= 25
        ) stats ON stats.claim_id = c.id
        WHERE c.claim_type IN ('gossip', 'rumor')
          AND c.truth_status IN ('unverified', 'disputed')
        """
    ).fetchall()
    created = []
    authority_channel = _channel(conn, "authority-notice")
    for claim in rows:
        version_key = f"claim:{claim['id']}:clarification:v1"
        before = conn.execute(
            "SELECT id FROM information_versions WHERE version_key = ?", (version_key,)
        ).fetchone()
        conn.execute(
            """
            INSERT OR IGNORE INTO information_versions
            (version_key, claim_id, parent_version_id, content, fidelity,
             distortion_score, transformation_type, created_at_world,
             metadata_json)
            VALUES (?, ?, NULL, ?, 100, 0, 'clarification', ?, ?)
            """,
            (
                version_key, claim["id"],
                f"澄清：目前可核实内容为“{claim['canonical_content']}”，其他扩展说法缺少证据。",
                now.isoformat(), _json({"authority": "system:campus-services"}),
            ),
        )
        version = conn.execute(
            "SELECT * FROM information_versions WHERE version_key = ?", (version_key,)
        ).fetchone()
        conn.execute(
            "UPDATE information_claims SET truth_status = 'corrected' WHERE id = ?",
            (claim["id"],),
        )
        if before is None:
            created.append(int(version["id"]))
            recipients = conn.execute(
                "SELECT DISTINCT resident_id FROM information_exposures WHERE claim_id = ?",
                (claim["id"],),
            ).fetchall()
            for recipient in recipients:
                transmit_information(
                    conn,
                    claim_id=int(claim["id"]),
                    version_id=int(version["id"]),
                    recipient_resident_id=int(recipient["resident_id"]),
                    channel_key=authority_channel["channel_key"],
                    world_time=now,
                    sender_actor_key="system:campus-services",
                    evidence_type="broadcast",
                    evidence_id=version_key,
                )
    return created


def submit_institutional_case(
    conn,
    *,
    case_key: str,
    rule_key: str,
    subject_resident_id: int,
    world_time=None,
    submitted_by_resident_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    evidence: Optional[dict] = None,
    requested_outcome: str = "",
    parent_case_id: Optional[int] = None,
    bypass_attempted: bool = False,
) -> dict:
    now = _now(world_time)
    existing = conn.execute(
        "SELECT * FROM institutional_cases WHERE case_key = ?", (case_key,)
    ).fetchone()
    if existing:
        return dict(existing)
    rule = conn.execute(
        "SELECT * FROM institutional_rules WHERE rule_key = ? AND status = 'active'",
        (rule_key,),
    ).fetchone()
    resident = conn.execute(
        "SELECT role FROM residents WHERE id = ?", (subject_resident_id,)
    ).fetchone()
    if not rule or not resident:
        raise ValueError("制度规则或案件主体不存在")
    roles = _load(rule["applies_to_roles_json"], [])
    if roles and not _role_matches(resident["role"], roles):
        raise ValueError("该制度规则不适用于案件主体角色")
    evidence = evidence or {}
    required = _load(rule["evidence_requirements_json"], [])
    missing = [key for key in required if not evidence.get(key)]
    profile = _profile(conn, subject_resident_id)
    procedural_access = int(profile["procedural_access"]) if profile else 50
    priority = _clamp(40 + procedural_access * 0.25 + (10 if not missing else -10))
    delay = max(
        15,
        int(rule["decision_delay_minutes"]) - min(45, procedural_access // 2),
    )
    cursor = conn.execute(
        """
        INSERT INTO institutional_cases
        (case_key, rule_id, case_type, subject_resident_id,
         submitted_by_resident_id, parent_case_id, organization_id,
         priority, formal_path, bypass_attempted, evidence_json,
         requested_outcome, submitted_at, due_at, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_key, rule["id"], rule["case_type"], subject_resident_id,
            submitted_by_resident_id or subject_resident_id, parent_case_id,
            organization_id, priority, 0 if bypass_attempted else 1,
            int(bypass_attempted), _json(evidence), requested_outcome,
            now.isoformat(), (now + timedelta(minutes=delay)).isoformat(),
            _json({"missing_evidence": missing, "procedural_access": procedural_access}),
        ),
    )
    return dict(conn.execute(
        "SELECT * FROM institutional_cases WHERE id = ?", (cursor.lastrowid,)
    ).fetchone())


def submit_appeal(
    conn,
    *,
    parent_case_id: int,
    resident_id: int,
    reason: str,
    world_time=None,
) -> dict:
    parent = conn.execute(
        """
        SELECT case_row.*, rule.appeal_allowed
        FROM institutional_cases case_row
        JOIN institutional_rules rule ON rule.id = case_row.rule_id
        WHERE case_row.id = ? AND case_row.subject_resident_id = ?
        """,
        (parent_case_id, resident_id),
    ).fetchone()
    if not parent or not int(parent["appeal_allowed"]):
        raise ValueError("该案件不存在、主体不匹配或不允许申诉")
    conn.execute(
        "UPDATE institutional_cases SET status = 'appealed' WHERE id = ?",
        (parent_case_id,),
    )
    return submit_institutional_case(
        conn,
        case_key=f"appeal:{parent_case_id}:resident:{resident_id}",
        rule_key="institutional-appeal",
        subject_resident_id=resident_id,
        world_time=world_time,
        parent_case_id=parent_case_id,
        evidence={"parent_case_id": parent_case_id, "appeal_reason": reason},
        requested_outcome="reverse_parent_decision",
    )


def _decision_maker(conn, case_row, permission):
    if not case_row["organization_id"]:
        return None
    rows = conn.execute(
        """
        SELECT assignment.resident_id, role.permissions_json, role.vote_weight
        FROM organization_role_assignments assignment
        JOIN organization_roles role ON role.id = assignment.role_id
        WHERE assignment.organization_id = ?
          AND assignment.status = 'active' AND role.status = 'active'
        ORDER BY role.vote_weight DESC, assignment.resident_id
        """,
        (case_row["organization_id"],),
    ).fetchall()
    for row in rows:
        if permission in _load(row["permissions_json"], []):
            return int(row["resident_id"])
    return None


def _update_trust(conn, resident_id: int, case_id: int, fairness: int, outcome: str, now: datetime):
    profile = conn.execute(
        "SELECT institutional_trust FROM resident_power_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    before = int(profile["institutional_trust"]) if profile else 50
    delta = 4 if fairness >= 70 else (-6 if fairness < 45 else 0)
    event_type = "fair_process" if delta > 0 else ("unfair_process" if delta < 0 else "authority_notice")
    if outcome == "appeal_upheld":
        delta, event_type = 8, "appeal_success"
    elif outcome == "appeal_denied":
        delta, event_type = -2, "appeal_failure"
    elif outcome == "bypass_detected":
        delta, event_type = -10, "bypass_detected"
    after = _clamp(before + delta)
    conn.execute(
        "UPDATE resident_power_profiles SET institutional_trust = ? WHERE resident_id = ?",
        (after, resident_id),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO institutional_trust_events
        (event_key, resident_id, case_id, event_type, trust_before,
         trust_after, reason, occurred_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"trust:case:{case_id}:resident:{resident_id}", resident_id, case_id,
            event_type, before, after, f"制度案件结果：{outcome}",
            now.isoformat(),
        ),
    )


def process_institutional_cases(conn, world_time=None) -> dict:
    if not social_institution_runtime_available(conn):
        return {"available": False, "decisions": []}
    now = _now(world_time)
    decisions = []
    rows = conn.execute(
        """
        SELECT case_row.*, rule.rule_key, rule.required_permission,
               rule.parameters_json, rule.authority_actor_key
        FROM institutional_cases case_row
        JOIN institutional_rules rule ON rule.id = case_row.rule_id
        WHERE case_row.status IN ('submitted', 'under_review')
          AND case_row.due_at <= ?
        ORDER BY case_row.priority DESC, case_row.id
        """,
        (now.isoformat(),),
    ).fetchall()
    for case_row in rows:
        parameters = _load(case_row["parameters_json"])
        evidence = _load(case_row["evidence_json"])
        metadata = _load(case_row["metadata_json"])
        profile = _profile(conn, int(case_row["subject_resident_id"]))
        procedural = int(profile["procedural_access"]) if profile else 50
        missing = len(metadata.get("missing_evidence", []))
        compliance = _clamp(55 + len(evidence) * 10 - missing * 20)
        fairness = _clamp(65 + (10 if int(case_row["formal_path"]) else -30) - missing * 5)
        threshold = int(parameters.get("approval_threshold", 55))
        maker = _decision_maker(conn, case_row, case_row["required_permission"])
        consequence = 0
        ledger_id = None
        opportunity_delta = int(parameters.get("opportunity_delta", 0))
        if int(case_row["bypass_attempted"]):
            outcome, status, reason = "bypass_detected", "bypassed", "检测到绕过正式程序的尝试"
            fairness = min(fairness, 35)
            opportunity_delta = min(-5, opportunity_delta)
        elif case_row["case_type"] == "conduct_violation":
            if compliance >= threshold:
                outcome, status, reason = "sanctioned", "sanctioned", "证据达到违规认定阈值"
                consequence = int(parameters.get("sanction_minor", 0))
            else:
                outcome, status, reason = "rejected", "rejected", "违规证据不足"
                opportunity_delta = 0
        elif case_row["case_type"] == "reward_nomination":
            if compliance >= threshold:
                outcome, status, reason = "rewarded", "rewarded", "贡献证据达到奖励阈值"
                consequence = int(parameters.get("reward_minor", 0))
            else:
                outcome, status, reason = "rejected", "rejected", "贡献证据不足"
                opportunity_delta = 0
        elif case_row["case_type"] == "appeal":
            parent = conn.execute(
                """
                SELECT decision.procedural_fairness_score
                FROM institutional_decisions decision
                WHERE decision.case_id = ?
                """,
                (case_row["parent_case_id"],),
            ).fetchone()
            upheld = bool(parent and int(parent["procedural_fairness_score"]) < 60)
            outcome = "appeal_upheld" if upheld else "appeal_denied"
            status = "approved" if upheld else "rejected"
            reason = "原程序公平性不足，申诉成立" if upheld else "原决定程序完整，申诉不成立"
        else:
            score = compliance + procedural // 5
            approved = score >= threshold
            outcome = "approved" if approved else "rejected"
            status = outcome
            reason = "证据与程序条件达到规则阈值" if approved else "证据或程序条件未达到规则阈值"
            if not approved:
                opportunity_delta = min(0, opportunity_delta)
        if consequence and outcome == "sanctioned":
            cash = conn.execute(
                "SELECT balance_minor FROM ledger_accounts WHERE account_key = ?",
                (f"resident:{case_row['subject_resident_id']}:cash",),
            ).fetchone()
            consequence = min(consequence, int(cash["balance_minor"]) if cash else 0)
            if consequence:
                ledger = post_money_transfer_minor(
                    conn,
                    transaction_key=f"institution-case:{case_row['id']}:sanction",
                    from_account_key=f"resident:{case_row['subject_resident_id']}:cash",
                    to_account_key="system:campus-services:cash",
                    amount_minor=consequence,
                    transaction_type="institutional_sanction",
                    source_type="institutional_case",
                    source_id=str(case_row["id"]),
                    description=reason,
                )
                ledger_id = int(ledger["id"])
        elif consequence and outcome == "rewarded":
            available = conn.execute(
                "SELECT balance_minor FROM ledger_accounts WHERE account_key = 'system:campus-services:cash'"
            ).fetchone()
            consequence = min(consequence, int(available["balance_minor"]) if available else 0)
            if consequence:
                ledger = post_money_transfer_minor(
                    conn,
                    transaction_key=f"institution-case:{case_row['id']}:reward",
                    from_account_key="system:campus-services:cash",
                    to_account_key=f"resident:{case_row['subject_resident_id']}:cash",
                    amount_minor=consequence,
                    transaction_type="institutional_reward",
                    source_type="institutional_case",
                    source_id=str(case_row["id"]),
                    description=reason,
                )
                ledger_id = int(ledger["id"])
        cursor = conn.execute(
            """
            INSERT INTO institutional_decisions
            (decision_key, case_id, decision_maker_resident_id,
             decision_maker_actor_key, outcome, reason,
             rule_compliance_score, procedural_fairness_score,
             consequence_minor, ledger_transaction_id, opportunity_delta,
             relationship_delta, decided_at, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                f"decision:case:{case_row['id']}", case_row["id"], maker,
                case_row["authority_actor_key"],
                outcome, reason, compliance, fairness, consequence, ledger_id,
                opportunity_delta, now.isoformat(),
                _json({"procedural_access": procedural, "threshold": threshold}),
            ),
        )
        conn.execute(
            """
            UPDATE institutional_cases
            SET status = ?, resolved_at = ? WHERE id = ?
            """,
            (status, now.isoformat(), case_row["id"]),
        )
        _update_trust(
            conn, int(case_row["subject_resident_id"]), int(case_row["id"]),
            fairness, outcome, now,
        )
        decisions.append(int(cursor.lastrowid))
    calculate_power_profiles(conn, now)
    return {"available": True, "decisions": decisions}


def process_social_institution_runtime(conn, world_time=None) -> dict:
    if not social_institution_runtime_available(conn):
        return {
            "available": False,
            "claims": [],
            "propagation": {},
            "institutions": {},
            "power_profiles": [],
        }
    now = _now(world_time)
    power = calculate_power_profiles(conn, now)
    claims = ingest_world_information(conn, now)
    propagation = propagate_information(conn, now)
    institutions = process_institutional_cases(conn, now)
    return {
        "available": True,
        "claims": claims,
        "propagation": propagation,
        "institutions": institutions,
        "power_profiles": power,
        "rule_version": RULE_VERSION,
    }
