from app.world_runtime.decision import hunger_recovery_instruction
from app.world_runtime import planning_decision


def test_hunger_policy_restores_when_food_is_closed():
    instruction = hunger_recovery_instruction(
        action="observe",
        destination="宿舍区",
        current_location="宿舍区",
        hunger=100,
        hour=1,
        is_location_open=lambda location, hour: False,
    )

    assert instruction["action"] == "rest"
    assert instruction["location"] == "宿舍区"


def test_hunger_policy_uses_open_food_earlier():
    instruction = hunger_recovery_instruction(
        action="observe",
        destination="宿舍区",
        current_location="宿舍区",
        hunger=75,
        hour=8,
        is_location_open=lambda location, hour: location == "食堂",
    )

    assert instruction["action"] == "move"
    assert instruction["location"] == "食堂"


def test_low_energy_hungry_agent_rests_before_travelling_to_food(monkeypatch):
    monkeypatch.setattr(
        planning_decision,
        "get_body_state",
        lambda conn, resident_id: {"hunger": 90, "fatigue": 30, "sleep_debt": 0, "health": 0, "attention": 80},
    )
    monkeypatch.setattr(
        planning_decision,
        "is_location_open_at_hour",
        lambda location, hour: location == "食堂",
        raising=False,
    )

    decision = planning_decision.apply_wellbeing_priority_to_decision(
        None,
        {"id": 1, "location": "宿舍区", "energy": 4},
        {"action": "observe", "location": "宿舍区"},
        type("WorldTime", (), {"hour": 12})(),
    )

    assert decision["action"] == "rest"
    assert decision["location"] == "宿舍区"
