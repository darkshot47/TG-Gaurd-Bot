import json

import pytest

from tgguard.state import MISSING, State, default_group, get_path, set_path


def test_defaults_and_merge():
    s = State()
    assert s.settings["tz"] == "Asia/Kolkata"
    assert len(s.plans()) == 3
    # simulate an old/partial snapshot: deep merge must refill missing keys
    raw = json.loads(json.dumps(s.data))
    del raw["settings"]["rmsg"]
    del raw["counters"]
    raw["groups"]["-1001"] = {"id": -1001, "title": "old"}  # partial group record
    s2 = State(raw)
    assert s2.setting("rmsg") == ""
    assert "msgs_seen" in s2.data["counters"]


def test_touch_user():
    s = State()
    rec, created = s.touch_user(101, "Ash", "K", "ashk")
    assert created and rec["id"] == 101 and rec["msgs"] == 0
    rec2, created2 = s.touch_user(101, "Ash", "K", "ashk")
    assert not created2 and rec2["msgs"] == 1
    assert s.get_user(101) is rec2
    assert len(s.users_sorted()) == 1


def test_blocked():
    s = State()
    s.touch_user(1, "A")
    s.touch_user(2, "B")
    s.mark_blocked(1, True)
    assert s.get_user(1)["blocked"] is True
    assert len(s.users_sorted(blocked=True)) == 1
    assert len(s.users_sorted(blocked=False)) == 1
    s.mark_blocked(1, False)
    assert s.get_user(1)["blocked"] is False


def test_groups():
    s = State()
    g, created = s.ensure_group(-100555, "Voice Class", by=7)
    assert created and g["title"] == "Voice Class"
    g2, created2 = s.ensure_group(-100555, "Voice Class 2")
    assert not created2 and g2["title"] == "Voice Class 2"
    assert g2["welcome"]["on"] is True
    assert len(s.active_groups()) == 1
    g2["left"] = True
    assert len(s.active_groups()) == 0


def test_paths():
    s = State()
    s.ensure_group(-100555, "T")
    set_path(s.data, "settings.welcome_pm", "hello")
    assert s.get_path("settings.welcome_pm") == "hello"
    assert get_path(s.data, "groups.-100555.welcome.on") is True
    set_path(s.data, "groups.-100555.welcome.text", "yo {mention}")
    assert s.get_path("groups.-100555.welcome.text") == "yo {mention}"
    # list by id
    pid = s.plans()[0]["id"]
    set_path(s.data, f"settings.plans[{pid}].price", "₹123")
    assert s.get_plan(pid)["price"] == "₹123"
    # missing path
    assert get_path(s.data, "settings.nope", MISSING) is MISSING
    with pytest.raises(KeyError):
        get_path(s.data, "settings.definitely_missing")


def test_plans_crud():
    s = State()
    n0 = len(s.plans())
    p = s.add_plan("New", "₹10", "detail")
    assert len(s.plans()) == n0 + 1
    assert s.get_plan(p["id"])["price"] == "₹10"
    assert s.del_plan(p["id"]) is True
    assert len(s.plans()) == n0
    assert s.del_plan(p["id"]) is False
    assert "New" not in s.format_plans()


def test_editable_registry():
    s = State()
    s.register_editable(111, 22, "settings.about", "🎙️ About")
    ent = s.editable_key(111, 22)
    assert ent and ent["key"] == "settings.about"
    assert s.editable_recent(5)[0]["msg_id"] == 22
    for i in range(300):
        s.register_editable(1, 1000 + i, "settings.about")
    assert len(s.data["editable"]) <= 250


def test_reports():
    s = State()
    rid = s.add_report({"gid": -1, "target_id": 5})
    assert s.get_report(rid)["gid"] == -1
    s.del_report(rid)
    assert s.get_report(rid) is None


def test_json_roundtrip():
    s = State()
    s.touch_user(9, "Nine")
    s.ensure_group(-1009, "G")
    blob = s.to_json()
    s2 = State.from_json(blob)
    assert s2.get_user(9)["first"] == "Nine"
    assert s2.get_group(-1009)["title"] == "G"
    # invalid blob
    with pytest.raises(ValueError):
        State.from_json(b'{"hello": 1}')


def test_counters():
    s = State()
    s.bump("deletes", 2)
    s.bump("deletes")
    assert s.data["counters"]["deletes"] == 3
    s.ensure_group(-1)
    s.bump_group(-1, "mutes")
    assert s.get_group(-1)["stats"]["mutes"] == 1


def test_default_group_shape():
    g = default_group(-5, "t")
    for key in ("welcome", "filters", "linkguard", "badwords", "flood", "verify", "warns", "schedules", "stats"):
        assert key in g
