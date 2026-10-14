import datetime as dt

import pytest

from newsgrabber.timeutil import (DateFormatError, briefing_window, format_long_date, parse_clock, parse_date,
                                  parse_datetime)
from conftest import BERLIN


def test_parse_date_variants():
    today = dt.date(2026, 10, 14)
    assert parse_date("heute", today) == today
    assert parse_date("morgen", today) == dt.date(2026, 10, 15)
    assert parse_date("gestern", today) == dt.date(2026, 10, 13)
    assert parse_date("14.10.2026") == today
    assert parse_date("2026-10-14") == today
    with pytest.raises(DateFormatError):
        parse_date("14/10/2026")


def test_parse_datetime_formats():
    expected = dt.datetime(2026, 10, 14, 3, 12, 45, 120000, tzinfo=dt.timezone.utc)
    assert parse_datetime("2026-10-14T05:12:45.120+02:00") == expected
    assert parse_datetime("2026-10-14T03:12:45.12Z") == expected
    assert parse_datetime("Wed, 14 Oct 2026 05:12:45 +0200") == expected.replace(microsecond=0)
    assert parse_datetime(1791946800) == dt.datetime(2026, 10, 14, 3, 0, tzinfo=dt.timezone.utc)
    assert parse_datetime("2026-10-14 05:00", default_tz=BERLIN) == dt.datetime(2026, 10, 14, 3, 0,
                                                                                 tzinfo=dt.timezone.utc)
    assert parse_datetime("kein Datum") is None


def test_briefing_window_for_today_past_and_cutoff():
    now = dt.datetime(2026, 10, 14, 6, 30, tzinfo=BERLIN)
    start, end = briefing_window(dt.date(2026, 10, 14), BERLIN, 24, None, now)
    assert end == now and start == now - dt.timedelta(hours=24)
    start, end = briefing_window(dt.date(2026, 10, 10), BERLIN, 24, None, now)
    assert end == dt.datetime(2026, 10, 11, 0, 0, tzinfo=BERLIN)
    start, end = briefing_window(dt.date(2026, 10, 14), BERLIN, 24, parse_clock("06:00"), now)
    assert end == dt.datetime(2026, 10, 14, 6, 0, tzinfo=BERLIN)
    # Sommerzeit-Ende (25.10.2026): Fenster bleibt 24 echte Stunden lang
    later = dt.datetime(2026, 10, 25, 12, 0, tzinfo=BERLIN)
    start, end = briefing_window(dt.date(2026, 10, 25), BERLIN, 24, None, later)
    assert (end - start) == dt.timedelta(hours=24)


def test_format_long_date():
    assert format_long_date(dt.date(2026, 10, 14)) == "Mittwoch, 14. Oktober 2026"
    assert format_long_date(dt.date(2026, 3, 2)) == "Montag, 2. März 2026"
