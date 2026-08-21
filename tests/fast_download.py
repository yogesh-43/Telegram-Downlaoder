from fast_download import PART_SIZE, connections_for_size, part_plan


def test_connections_scale_with_file_size():
    assert connections_for_size(500_000) == 1
    assert connections_for_size(5 * 1024 * 1024) == 6
    assert connections_for_size(20 * 1024 * 1024) == 8
    assert connections_for_size(200 * 1024 * 1024) == 12


def test_part_plan_covers_every_byte_once():
    file_size = 10 * PART_SIZE + 100
    plan = part_plan(file_size, PART_SIZE, 8)
    assert [count for _, count in plan] == [2, 2, 2, 1, 1, 1, 1, 1]
    assert [offset for offset, _ in plan] == [i * PART_SIZE for i in range(8)]


def test_part_plan_caps_connections_to_part_count():
    plan = part_plan(PART_SIZE * 2, PART_SIZE, 12)
    assert len(plan) == 2
    assert [count for _, count in plan] == [1, 1]
