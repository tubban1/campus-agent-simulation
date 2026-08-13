from app.world_runtime.scheduler import bounded_agent_batch_size


def test_bounded_agent_batch_size_is_reproducible_and_bounded():
    sizes = [
        bounded_agent_batch_size(3, 20, f"tick-{index}")
        for index in range(32)
    ]

    assert sizes == [
        bounded_agent_batch_size(3, 20, f"tick-{index}")
        for index in range(32)
    ]
    assert set(sizes).issubset({2, 3, 4})
    assert bounded_agent_batch_size(3, 2, "limited") <= 2
    assert bounded_agent_batch_size(3, 0, "empty") == 0
