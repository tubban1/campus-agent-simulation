from __future__ import annotations

from copy import deepcopy
import json

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection

from app.capability_models import (
    CAPABILITY_FIELDS,
    agent_capability_profiles,
    agent_opportunity_access,
)
from app.spatial.models import agent_spatial_capabilities


from datetime import datetime, timezone

DEFAULTS_VERSION = "capability-defaults-v1"
MISSING_VALUE_POLICY = "structured trait, then role baseline, then deterministic neutral default"

OPPORTUNITY_DEFINITIONS = {
    "academic_support": ("information_literacy", 0),
    "institutional_services": ("institutional_access", 0),
    "social_referral": ("social_capital", 0),
    "commercial_services": ("economic_access", 5),
    "public_information": ("language_access", 0),
}

ACTION_CAPABILITY_WEIGHTS = {
    "move": {"physical_endurance": 1.0},
    "attend_class": {"information_literacy": 0.65, "time_management": 0.35},
    "observe": {"information_literacy": 1.0},
    "collaborate": {"social_capital": 0.65, "language_access": 0.35},
    "chat": {"social_capital": 0.65, "language_access": 0.35},
    "request_leave": {
        "institutional_access": 0.5,
        "language_access": 0.3,
        "rule_adherence": 0.2,
    },
    "conflict": {"risk_tolerance": 0.5, "stress_resilience": 0.5},
    "queue": {"time_management": 0.55, "stress_resilience": 0.45},
    "consume": {"economic_access": 0.4, "time_management": 0.6},
}

ACTION_OPPORTUNITIES = {
    "attend_class": "academic_support",
    "observe": "public_information",
    "collaborate": "social_referral",
    "request_leave": "institutional_services",
    "consume": "commercial_services",
}


def _clamp(value, lower=0, upper=100):
    return max(lower, min(upper, int(round(value))))


def _json_value(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _trait(traits, key, fallback):
    value = traits.get(key)
    try:
        return _clamp(value if value is not None else fallback)
    except (TypeError, ValueError):
        return _clamp(fallback)


def derive_capability_profile(resident_id, role, money, strategy):
    strategy = _json_value(strategy, {})
    traits = strategy.get("personality_traits", {})
    role = str(role or "student")
    role_institutional = {
        "teacher": 78,
        "service": 82,
        "business": 58,
        "student": 46,
    }.get(role, 50)
    variance = (int(resident_id) * 7) % 13 - 6
    emotional_stability = _trait(traits, "emotional_stability", 55 + variance)
    conscientiousness = _trait(traits, "conscientiousness", 55 - variance)
    autonomy = _trait(traits, "autonomy", 55 + variance / 2)
    stress_sensitivity = _trait(traits, "stress_sensitivity", 50 - variance)
    extraversion = _trait(traits, "extraversion", 50 + variance)
    social_need = _trait(traits, "social_need", 50 + variance / 2)
    empathy = _trait(traits, "empathy", 55 - variance / 2)
    profile = {
        "physical_endurance": _clamp(42 + emotional_stability * 0.38 + variance),
        "time_management": conscientiousness,
        "risk_tolerance": _trait(traits, "risk_tolerance", 50 + variance),
        "rule_adherence": _trait(traits, "rule_orientation", 55 - variance),
        "information_literacy": _clamp(
            conscientiousness * 0.58 + autonomy * 0.42
        ),
        "economic_access": _clamp(
            30 + max(0, int(money or 0)) / 4 + (8 if role == "business" else 0)
        ),
        "social_capital": _clamp(
            extraversion * 0.4 + social_need * 0.25 + empathy * 0.35
        ),
        "institutional_access": _clamp(role_institutional + variance),
        "language_access": _clamp(74 + variance),
        "stress_resilience": _clamp(
            emotional_stability * 0.58 + (100 - stress_sensitivity) * 0.42
        ),
    }
    return {
        **profile,
        "source": "derived-structured-profile",
        "source_detail": {
            "inputs": [
                "agent_profiles.strategy.personality_traits",
                "residents.role",
                "residents.money",
                "resident_id deterministic fallback",
            ],
            "excluded_inference": ["name", "gender", "narrative biography"],
        },
        "defaults_version": DEFAULTS_VERSION,
        "missing_value_policy": MISSING_VALUE_POLICY,
        "version": 1,
        "updated_at": datetime.now(timezone.utc),
    }


def derive_opportunities(profile):
    opportunities = []
    now = datetime.now(timezone.utc)
    for key, (capability_key, monetary_barrier) in OPPORTUNITY_DEFINITIONS.items():
        access_level = int(profile[capability_key])
        opportunities.append(
            {
                "opportunity_key": key,
                "access_level": access_level,
                "time_cost_multiplier": round(
                    max(0.72, min(1.35, 1.18 - access_level * 0.0045)), 3
                ),
                "monetary_barrier": monetary_barrier,
                "eligibility": "limited" if access_level < 20 else "eligible",
                "source": "derived-capability-profile",
                "source_detail": {
                    "capability": capability_key,
                    "defaults_version": DEFAULTS_VERSION,
                },
                "version": 1,
                "updated_at": now,
            }
        )
    return opportunities


def seed_capability_foundation(connection: Connection) -> dict:
    residents = list(
        connection.exec_driver_sql(
            """
            SELECT r.id, r.role, r.money, p.strategy
            FROM residents r
            LEFT JOIN agent_profiles p ON p.resident_id = r.id
            ORDER BY r.id
            """
        ).mappings()
    )
    existing_profiles = {
        int(row.resident_id): dict(row)
        for row in connection.execute(select(agent_capability_profiles)).mappings()
    }
    existing_opportunities = {
        (int(row.resident_id), row.opportunity_key)
        for row in connection.execute(
            select(
                agent_opportunity_access.c.resident_id,
                agent_opportunity_access.c.opportunity_key,
            )
        )
    }
    profiles_created = 0
    opportunities_created = 0
    capabilities_updated = 0
    profiles = {}
    for resident in residents:
        resident_id = int(resident["id"])
        profile = existing_profiles.get(resident_id)
        if not profile:
            profile = derive_capability_profile(
                resident_id,
                resident.get("role"),
                resident.get("money"),
                resident.get("strategy"),
            )
            connection.execute(
                insert(agent_capability_profiles).values(
                    resident_id=resident_id,
                    **profile,
                )
            )
            profiles_created += 1
        profiles[resident_id] = profile
        for opportunity in derive_opportunities(profile):
            key = (resident_id, opportunity["opportunity_key"])
            if key in existing_opportunities:
                continue
            connection.execute(
                insert(agent_opportunity_access).values(
                    resident_id=resident_id,
                    **opportunity,
                )
            )
            existing_opportunities.add(key)
            opportunities_created += 1

        spatial_row = connection.execute(
            select(agent_spatial_capabilities).where(
                agent_spatial_capabilities.c.resident_id == resident_id
            )
        ).mappings().first()
        if spatial_row and spatial_row["source"] in {
            "seeded",
            "derived-capability-v1",
        }:
            values = spatial_capability_values(profile)
            if any(
                abs(float(spatial_row[key]) - float(value)) > 0.001
                for key, value in values.items()
            ) or spatial_row["source"] != "derived-capability-v1":
                connection.execute(
                    update(agent_spatial_capabilities)
                    .where(agent_spatial_capabilities.c.resident_id == resident_id)
                    .values(
                        **values,
                        source="derived-capability-v1",
                        version=int(spatial_row["version"]) + 1,
                    )
                )
                capabilities_updated += 1
    return {
        "profiles": profiles,
        "profiles_created": profiles_created,
        "opportunities_created": opportunities_created,
        "capabilities_updated": capabilities_updated,
    }


def spatial_capability_values(profile):
    return {
        "base_speed_m_per_min": round(
            68 + int(profile["physical_endurance"]) * 0.2, 2
        ),
        "perception_radius_m": round(
            25 + int(profile["information_literacy"]) * 0.18, 2
        ),
        "hearing_radius_m": round(
            16 + int(profile["language_access"]) * 0.08, 2
        ),
    }


def capability_runtime_available(conn):
    return bool(conn.execute("PRAGMA table_info(agent_capability_profiles)").fetchall())


def get_capability_profile(conn, resident_id):
    if not capability_runtime_available(conn):
        return None
    row = conn.execute(
        "SELECT * FROM agent_capability_profiles WHERE resident_id = ?",
        (resident_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["source_detail"] = _json_value(item.get("source_detail"), {})
    return item


def get_opportunity_access(conn, resident_id):
    if not capability_runtime_available(conn):
        return []
    rows = conn.execute(
        """
        SELECT * FROM agent_opportunity_access
        WHERE resident_id = ?
        ORDER BY opportunity_key
        """,
        (resident_id,),
    ).fetchall()
    result = []
    organization_roles = []
    if conn.execute("PRAGMA table_info(organization_role_assignments)").fetchall():
        organization_roles = [
            dict(row)
            for row in conn.execute(
                """
                SELECT assignment.organization_id, role.role_key,
                       organization.name AS organization_name
                FROM organization_role_assignments assignment
                JOIN organization_roles role ON role.id = assignment.role_id
                JOIN campus_organizations organization
                  ON organization.id = assignment.organization_id
                WHERE assignment.resident_id = ?
                  AND assignment.status = 'active' AND role.status = 'active'
                ORDER BY assignment.organization_id
                """,
                (resident_id,),
            ).fetchall()
        ]
    role_bonus = sum(
        12 if role["role_key"] == "chair" else 6
        for role in organization_roles
    )
    power_profile = None
    if conn.execute("PRAGMA table_info(resident_power_profiles)").fetchall():
        power_profile = conn.execute(
            """
            SELECT formal_authority, institutional_trust, procedural_access
            FROM resident_power_profiles WHERE resident_id = ?
            """,
            (resident_id,),
        ).fetchone()
    for row in rows:
        item = dict(row)
        item["source_detail"] = _json_value(item.get("source_detail"), {})
        if organization_roles and item["opportunity_key"] in {
            "institutional_services",
            "social_referral",
        }:
            bonus = min(20, role_bonus)
            item["access_level"] = _clamp(int(item["access_level"]) + bonus)
            item["eligibility"] = (
                "limited" if item["access_level"] < 20 else "eligible"
            )
            item["time_cost_multiplier"] = round(
                max(0.72, float(item["time_cost_multiplier"]) - bonus * 0.004),
                3,
            )
            item["source_detail"] = {
                **item["source_detail"],
                "organization_memberships": organization_roles,
                "organization_access_bonus": bonus,
            }
        if power_profile and item["opportunity_key"] == "institutional_services":
            power_bonus = _clamp(
                (
                    int(power_profile["formal_authority"])
                    + int(power_profile["institutional_trust"])
                    - 100
                )
                / 8,
                -12,
                18,
            )
            item["access_level"] = _clamp(int(item["access_level"]) + power_bonus)
            item["eligibility"] = (
                "limited" if item["access_level"] < 20 else "eligible"
            )
            item["time_cost_multiplier"] = round(
                max(0.72, min(1.35, float(item["time_cost_multiplier"]) - power_bonus * 0.004)),
                3,
            )
            item["source_detail"] = {
                **item["source_detail"],
                "institutional_power_bonus": power_bonus,
                "formal_authority": int(power_profile["formal_authority"]),
                "institutional_trust": int(power_profile["institutional_trust"]),
                "procedural_access": int(power_profile["procedural_access"]),
            }
        result.append(item)
    return result


def individualize_action_rule(conn, resident_id, rule, action_type):
    profile = get_capability_profile(conn, resident_id)
    if not profile:
        return rule
    adjusted = deepcopy(rule)
    original_resources = dict(adjusted.get("required_resources", {}))
    resources = dict(original_resources)
    endurance = int(profile["physical_endurance"])
    time_management = int(profile["time_management"])
    energy_multiplier = max(0.78, min(1.22, 1.12 - endurance * 0.0024))
    time_multiplier = max(0.78, min(1.25, 1.14 - time_management * 0.0028))
    opportunity_key = ACTION_OPPORTUNITIES.get(action_type)
    opportunity = next(
        (
            item
            for item in get_opportunity_access(conn, resident_id)
            if item["opportunity_key"] == opportunity_key
        ),
        None,
    )
    if opportunity:
        time_multiplier *= float(opportunity["time_cost_multiplier"])
    resources["energy"] = max(
        0, round(float(resources.get("energy", 0)) * energy_multiplier)
    )
    resources["time_budget"] = max(
        0, round(float(resources.get("time_budget", 0)) * time_multiplier)
    )
    adjusted["required_resources"] = resources
    adjusted["duration_minutes"] = max(
        0, round(float(adjusted.get("duration_minutes", 0)) * time_multiplier)
    )

    weights = ACTION_CAPABILITY_WEIGHTS.get(action_type, {})
    capability_score = (
        sum(float(profile[key]) * weight for key, weight in weights.items())
        if weights
        else 50.0
    )
    original_probability = float(adjusted.get("success_probability", 1.0))
    probability_modifier = (capability_score - 50.0) / 500.0 if weights else 0.0
    adjusted["success_probability"] = round(
        max(0.35, min(1.0, original_probability + probability_modifier)), 4
    )
    adjusted["individualization"] = {
        "version": DEFAULTS_VERSION,
        "source": profile["source"],
        "capability_score": round(capability_score, 2),
        "capabilities": {key: profile[key] for key in weights},
        "opportunity_key": opportunity_key,
        "opportunity_access_level": (
            opportunity["access_level"] if opportunity else None
        ),
        "resources_before": original_resources,
        "resources_after": resources,
        "success_probability_before": original_probability,
        "success_probability_after": adjusted["success_probability"],
        "duration_minutes_before": rule.get("duration_minutes", 0),
        "duration_minutes_after": adjusted["duration_minutes"],
    }
    return adjusted


def capability_action_checks(conn, resident_id, action_type):
    opportunity_key = ACTION_OPPORTUNITIES.get(action_type)
    if not opportunity_key:
        return []
    opportunity = next(
        (
            item
            for item in get_opportunity_access(conn, resident_id)
            if item["opportunity_key"] == opportunity_key
        ),
        None,
    )
    if not opportunity:
        return []
    passed = (
        opportunity["eligibility"] == "eligible"
        and int(opportunity["access_level"]) >= 15
    )
    return [
        {
            "key": f"opportunity_{opportunity_key}",
            "passed": passed,
            "actual": {
                "access_level": opportunity["access_level"],
                "eligibility": opportunity["eligibility"],
            },
            "required": {"access_level": ">= 15", "eligibility": "eligible"},
            "failure_code": "" if passed else "opportunity_access_limited",
            "reason": (
                ""
                if passed
                else f"{opportunity_key}机会当前不可达，需要其他信息、关系或制度支持"
            ),
        }
    ]
