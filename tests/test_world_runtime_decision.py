from app.world_runtime.decision import hunger_recovery_instruction


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

    assert instruction["action"] == "consume"
    assert instruction["location"] == "食堂"
