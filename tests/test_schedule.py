from datetime import datetime, timezone

from dealscout.schedule import should_run_now


def _utc(y, m, d, h):
    return datetime(y, m, d, h, 0, tzinfo=timezone.utc)


def test_true_at_local_run_hour():
    # 01:00 UTC == 09:00 in Asia/Kuala_Lumpur (UTC+8, no DST)
    assert should_run_now("Asia/Kuala_Lumpur", 9, _utc(2026, 7, 20, 1)) is True


def test_false_off_hour():
    assert should_run_now("Asia/Kuala_Lumpur", 10, _utc(2026, 7, 20, 1)) is False


def test_dst_aware_new_york():
    # Same 13:00 UTC is 09:00 EDT in summer but 08:00 EST in winter.
    assert should_run_now("America/New_York", 9, _utc(2026, 7, 1, 13)) is True
    assert should_run_now("America/New_York", 9, _utc(2026, 1, 1, 13)) is False
