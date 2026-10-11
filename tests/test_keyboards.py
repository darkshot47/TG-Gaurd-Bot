from tgguard.keyboards import BLUE, GREEN, RED, add_to_group_url, cbtn, kboard, nav_rows


def test_button_styles_serialize():
    b = cbtn("Save", cb="x", color=GREEN)
    d = b.to_dict()
    assert d["style"] == "success"
    assert cbtn("Del", cb="y", color=RED).to_dict()["style"] == "danger"
    assert cbtn("Info", cb="z", color=BLUE).to_dict()["style"] == "primary"
    # no colour -> no style key (plain button, maximum compatibility)
    assert "style" not in cbtn("Plain", cb="p").to_dict()


def test_url_button():
    b = cbtn("Add", url="https://t.me/x", color=GREEN)
    d = b.to_dict()
    assert d["url"] == "https://t.me/x" and d["style"] == "success"


def test_kboard_drops_empty_rows():
    kb = kboard([[cbtn("a", "a")], [], [cbtn("b", "b")]])
    assert len(kb.inline_keyboard) == 2


def test_nav_rows():
    assert nav_rows("us", 0, 1) == []
    rows = nav_rows("us", 0, 3)
    assert len(rows) == 1
    data = [b.callback_data for b in rows[0]]
    assert "noop" in data and "us:1" in data


def test_add_to_group_deep_link():
    url = add_to_group_url("mybot")
    assert url.startswith("https://t.me/mybot?startgroup=tgguard&admin=")
    for right in ("delete_messages", "restrict_members", "invite_users", "pin_messages"):
        assert right in url
