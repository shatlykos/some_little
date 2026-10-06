from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.slots import (
    Busy,
    available_durations,
    available_starts,
    fit_booking,
    free_windows,
    merge_busy,
)

TZ = ZoneInfo("Asia/Tbilisi")
GAP = timedelta(minutes=5)
STEP = timedelta(minutes=30)
HOUR = timedelta(hours=1)
HALF = timedelta(minutes=30)


def t(h, m=0):
    return datetime(2026, 10, 6, h, m, tzinfo=TZ)


OPEN, CLOSE = t(9), t(22)


def fit(start, dur, busy):
    return fit_booking(start, dur, busy, OPEN, CLOSE, GAP)


def test_example_from_owner_both_neighbours():
    # Мастерская: заняты 16–17 и 18–19, клиент хочет 17:00 на час → 17:05–17:55
    busy = [Busy(t(16), t(17)), Busy(t(18), t(19))]
    s = fit(t(17), HOUR, busy)
    assert (s.start, s.end) == (t(17, 5), t(17, 55))


def test_no_neighbours_keeps_time():
    s = fit(t(17), HOUR, [])
    assert (s.start, s.end) == (t(17), t(18))


def test_only_previous_neighbour():
    s = fit(t(17), HOUR, [Busy(t(16), t(17))])
    assert (s.start, s.end) == (t(17, 5), t(18))


def test_only_next_neighbour():
    s = fit(t(17), HOUR, [Busy(t(18), t(19))])
    assert (s.start, s.end) == (t(17), t(17, 55))


def test_neighbour_with_existing_gap_is_not_touched():
    # Сосед закончил в 16:55 — перерыв уже есть, сдвигать не нужно.
    s = fit(t(17), HOUR, [Busy(t(16), t(16, 55))])
    assert (s.start, s.end) == (t(17), t(18))


def test_neighbour_too_close_shifts_to_full_gap():
    # Сосед закончил в 16:58 → начинаем в 17:03.
    s = fit(t(17), HOUR, [Busy(t(16), t(16, 58))])
    assert s.start == t(17, 3)


def test_overlap_rejected():
    assert fit(t(17), HOUR, [Busy(t(17, 30), t(18, 30))]) is None
    assert fit(t(17), HOUR, [Busy(t(16, 30), t(17, 2))]) is None


def test_outside_working_hours():
    assert fit(t(8, 30), HOUR, []) is None
    assert fit(t(21, 30), HOUR, []) is None
    assert fit(t(21), HOUR, []) is not None


def test_piano_half_hour_between_bookings():
    # Фортепиано: 13:30–14:30 и 15:00–16:00, клиент берёт 14:30 на 30 мин → 14:35–14:55
    busy = [Busy(t(13, 30), t(14, 30)), Busy(t(15), t(16))]
    s = fit(t(14, 30), HALF, busy)
    assert (s.start, s.end) == (t(14, 35), t(14, 55))


def test_all_day_event_blocks_everything():
    busy = [Busy(t(0), t(0) + timedelta(days=1), "Фестиваль")]
    assert available_starts(busy, OPEN, CLOSE, STEP, HOUR, GAP) == []


def test_available_starts_hall():
    # Как на скриншоте: 13:30–14:30 и 14:30–15:30 заняты
    busy = [Busy(t(13, 30), t(14, 30)), Busy(t(14, 30), t(15, 30))]
    starts = available_starts(busy, OPEN, CLOSE, STEP, HOUR, GAP)
    assert t(12, 30) in starts  # 12:30–13:25
    assert t(13) not in starts  # не помещается
    assert t(14) not in starts and t(14, 30) not in starts and t(15) not in starts
    assert t(15, 30) in starts  # 15:35–16:30
    assert starts[0] == t(9) and starts[-1] == t(21)


def test_available_starts_not_before():
    starts = available_starts([], OPEN, CLOSE, STEP, HOUR, GAP, not_before=t(17, 10))
    assert starts[0] == t(17, 30)


def test_durations_stop_at_next_booking():
    busy = [Busy(t(19), t(20))]
    opts = available_durations(t(17), busy, OPEN, CLOSE, STEP, HOUR, GAP)
    assert [d for d, _ in opts] == [HOUR, timedelta(hours=1, minutes=30), timedelta(hours=2)]
    last = opts[-1][1]
    assert (last.start, last.end) == (t(17), t(18, 55))


def test_durations_until_close():
    opts = available_durations(t(20), [], OPEN, CLOSE, STEP, HOUR, GAP)
    assert [d for d, _ in opts] == [HOUR, timedelta(hours=1, minutes=30), timedelta(hours=2)]


def test_free_windows():
    busy = [Busy(t(13, 30), t(14, 30)), Busy(t(14, 30), t(15, 30))]
    assert free_windows(busy, OPEN, CLOSE) == [(t(9), t(13, 30)), (t(15, 30), t(22))]


def test_merge_keeps_named_events():
    busy = [Busy(t(10), t(11)), Busy(t(10, 30), t(12)), Busy(t(12), t(13), "Концерт")]
    merged = merge_busy(busy)
    assert merged == [Busy(t(10), t(12)), Busy(t(12), t(13), "Концерт")]
