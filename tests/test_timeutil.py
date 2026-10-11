import datetime as dtm

from tgguard.timeutil import fmt_ts, humanize_seconds, next_fire, parse_clock, parse_duration


def test_parse_clock():
    assert parse_clock("8:00 PM") == (20, 0)
    assert parse_clock("20:00") == (20, 0)
    assert parse_clock("8pm") == (20, 0)
    assert parse_clock("8 pm") == (20, 0)
    assert parse_clock("08:30am") == (8, 30)
    assert parse_clock("12am") == (0, 0)
    assert parse_clock("12pm") == (12, 0)
    assert parse_clock("9:5") == (9, 5)
    assert parse_clock("25:00") is None
    assert parse_clock("12:61") is None
    assert parse_clock("hello") is None
    assert parse_clock("") is None


def test_parse_duration():
    assert parse_duration("2h") == 7200
    assert parse_duration("90") == 5400          # bare number = minutes
    assert parse_duration("1h30m") == 5400
    assert parse_duration("45 min") == 2700
    assert parse_duration("1d") == 86400
    assert parse_duration("30s") == 30
    assert parse_duration("5m") == 300
    assert parse_duration("blah") is None


def test_next_fire():
    tz = "Asia/Kolkata"
    # 19:00 UTC = 00:30 IST (next day) -> 20:00 IST today(IST) = 14:30 UTC same day as IST date
    now = dtm.datetime(2026, 10, 10, 19, 0, tzinfo=dtm.timezone.utc)
    fire = next_fire("20:00", tz, now=now)
    assert fire is not None
    assert (fire.hour, fire.minute) == (20, 0)
    assert fire.astimezone(dtm.timezone.utc) > now
    # earlier in the IST day -> fires the same IST day
    now2 = dtm.datetime(2026, 10, 10, 6, 0, tzinfo=dtm.timezone.utc)  # 11:30 IST
    fire2 = next_fire("20:00", tz, now=now2)
    ist = fire2.astimezone(dtm.timezone.utc)
    assert ist > now2
    assert (ist - now2) < dtm.timedelta(hours=12)


def test_humanize():
    assert humanize_seconds(3725) == "1h 2m 5s"
    assert humanize_seconds(60) == "1m"
    assert humanize_seconds(0) == "0s"
    assert humanize_seconds(86400) == "1d"


def test_fmt_ts():
    assert fmt_ts(0) == "—"
    assert fmt_ts(None) == "—"
    out = fmt_ts(1760000000, "Asia/Kolkata")
    assert "2025" in out
