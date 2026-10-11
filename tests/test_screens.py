from types import SimpleNamespace

from tgguard.handlers import screens as S
from tgguard.state import State


def make_user(uid=42, first="Asha"):
    return SimpleNamespace(id=uid, first_name=first, last_name="", username="asha", is_bot=False)


def filled_state():
    s = State()
    for i in range(20):
        s.touch_user(1000 + i, f"User{i}", "", f"u{i}")
    s.mark_blocked(1003, True)
    g, _ = s.ensure_group(-100555, "Voice & Dubbing Class", by=42)
    g["filters"].append({"id": "f1", "word": "hello", "kind": "text", "content": "Hi {mention}!"})
    g["badwords"].append({"id": "b1", "word": "spam", "mute_min": 30})
    g["schedules"].append({"id": "s1", "time": "20:00", "repeat": "daily", "on": True, "text": "Class at 8!"})
    return s


def test_home_owner_vs_user():
    s = filled_state()
    t, e, kb = S.scr_home(s, make_user(), "mybot", owner=True)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == "om" for b in flat)
    add = next(b for b in flat if b.url)
    assert "startgroup=tgguard" in add.url and add.style == "success"
    t2, e2, kb2 = S.scr_home(s, make_user(), "mybot", owner=False)
    flat2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
    assert "om" not in flat2
    assert e2  # welcome contains {mention} -> entity


def test_users_pagination():
    s = filled_state()
    t, e, kb = S.scr_users(s, 0)
    flat = [b for row in kb.inline_keyboard for b in row]
    profiles = [b for b in flat if b.callback_data and b.callback_data.startswith("up:")]
    assert len(profiles) == 8
    assert "20" in t
    _, _, kb3 = S.scr_users(s, 2)
    p3 = [b for row in kb3.inline_keyboard for b in row if b.callback_data and b.callback_data.startswith("up:")]
    assert len(p3) == 4  # 20 users -> 8+8+4


def test_blocked_list_marks():
    s = filled_state()
    t, e, kb = S.scr_users(s, 0, blocked_only=True)
    assert "BLOCKED" in t
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(d and d.startswith("up:1003:bu:0") for d in flat)


def test_profile_and_back():
    s = filled_state()
    t, e, kb = S.scr_user_profile(s, 1005, "us", 1)
    flat = [b for row in kb.inline_keyboard for b in row]
    assert any(b.callback_data == "um:1005" for b in flat)
    assert any(b.url == "tg://user?id=1005" for b in flat)
    assert any(b.callback_data == "us:1" for b in flat)


def test_group_panel_and_subscreens():
    s = filled_state()
    t, e, kb = S.scr_group_panel(s, -100555, members=123)
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    for tag in ("gw:-100555", "gf:-100555", "gb:-100555", "gl:-100555", "gs:-100555", "gv:-100555", "gfl:-100555", "gwa:-100555", "glk:-100555"):
        assert tag in flat
    assert "123" in t

    tf, _, kbf = S.scr_filters(s, -100555)
    assert "hello" in tf
    flatf = [b.callback_data for row in kbf.inline_keyboard for b in row]
    assert "gfd:-100555:0" in flatf and "gfa:-100555" in flatf

    tb, _, _ = S.scr_badwords(s, -100555)
    assert "spam" in tb and "30" in tb

    ts, _, kbs = S.scr_schedules(s, -100555)
    assert "20:00" in ts
    flats = [b.callback_data for row in kbs.inline_keyboard for b in row]
    assert "gst:-100555:s1" in flats and "gsd:-100555:s1" in flats


def test_setting_view_and_registry_paths():
    s = filled_state()
    t, e, kb = S.scr_setting_view(s, "settings.welcome_pm")
    assert "welcome_pm" in t
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "ed:settings.welcome_pm" in flat
    assert "rs:settings.welcome_pm" in flat
    assert S.parent_of("settings.welcome_pm") == "sm"
    assert S.parent_of("groups.-100555.welcome.text") == "gw:-100555"
    assert S.parent_of("groups.-100555.linkguard.on") == "gl:-100555"
    assert S.parent_of("groups.-100555.schedules[s1].text") == "gs:-100555"
    assert S.is_int_path("groups.-100555.flood.limit")
    assert not S.is_int_path("settings.about")
    assert S.default_for("settings.rmsg") == ""
    assert S.default_for("groups.-100555.rules") is None


def test_group_welcome_shows_source():
    s = filled_state()
    t, e, kb = S.scr_group_welcome(s, -100555)
    assert "GLOBAL default" in t
    s.set_path("groups.-100555.welcome.text", "Custom {mention}")
    t2, _, _ = S.scr_group_welcome(s, -100555)
    assert "Custom" in t2


def test_plans_screens():
    s = filled_state()
    t, e, kb = S.scr_plans(s, owner=False)
    assert "Voice Basics" in t and "₹499" in t
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert not any(d and d.startswith("pld") for d in flat)
    t2, _, kb2 = S.scr_plans_manage(s)
    flat2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
    assert "pla" in flat2
    pid = s.plans()[0]["id"]
    assert f"ple:{pid}" in flat2 and f"pld:{pid}" in flat2


def test_report_screens():
    s = filled_state()
    rid = s.add_report({"gid": -100555, "gtitle": "Voice Class", "reporter": "A", "reporter_id": 1,
                        "target": "B", "target_id": 2, "mid": 77, "reason": "spam"})
    t, e, kb = S.scr_report(s, rid)
    flat = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"rp:{rid}:ban" in flat and f"rp:{rid}:mute" in flat
    ban = next(b for row in kb.inline_keyboard for b in row if b.callback_data == f"rp:{rid}:ban")
    assert ban.style == "danger"
    t2, _, kb2 = S.scr_report_durations(s, rid, "mute")
    flat2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
    assert f"rp:{rid}:mute:600" in flat2


def test_stats_screen():
    s = filled_state()
    s.bump("deletes", 5)
    t, e, kb = S.scr_stats(s, uptime_s=3661)
    assert "20" in t and "1h 1m" in t and "5" in t


def test_owner_menu_counts():
    s = filled_state()
    t, e, kb = S.scr_owner_menu(s)
    assert "Users: 20" in t and "Blocked: 1" in t and "Groups: 1" in t


def test_unknown_group_screens_graceful():
    s = State()
    for fn in (S.scr_group_panel, S.scr_group_welcome, S.scr_filters, S.scr_badwords,
               S.scr_linkguard, S.scr_schedules, S.scr_verify, S.scr_flood, S.scr_warns):
        t, e, kb = fn(s, -1)
        assert "not found" in t.lower()
