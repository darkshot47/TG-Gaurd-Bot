from types import SimpleNamespace

from telegram import MessageEntity

from tgguard.textfmt import format_placeholders, has_link, split_long, u16len


def make_user(uid=42, first="Asha", last="Devi", username="asha", is_bot=False):
    return SimpleNamespace(id=uid, first_name=first, last_name=last, username=username, is_bot=is_bot)


def test_basic_placeholders():
    text, ents = format_placeholders("Hi {name}! Group: {group}", user=make_user(), chat_title="Voice Class")
    assert text == "Hi Asha Devi! Group: Voice Class"
    assert ents == []


def test_mention_entity_offsets_are_utf16():
    user = make_user()
    text, ents = format_placeholders("🎉🎉 Welcome {mention} to {group}!", user=user, chat_title="VG")
    assert "Asha Devi" in text
    assert len(ents) == 1
    e = ents[0]
    assert e.type == MessageEntity.TEXT_MENTION
    assert e.user.id == 42
    # verify offsets against UTF-16 encoding of the produced text
    u16 = text.encode("utf-16-le")
    mentioned = u16[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le")
    assert mentioned == "Asha Devi"


def test_mention_after_emoji_name():
    # names contain an emoji (non-BMP, 2 UTF-16 units) — offsets must still be right
    user = make_user(first="🎙Asha")
    text, ents = format_placeholders("{mention} hi {mention}", user=user)
    u16 = text.encode("utf-16-le")
    for e in ents:
        seg = u16[e.offset * 2 : (e.offset + e.length) * 2].decode("utf-16-le")
        assert seg == "🎙Asha Devi"


def test_unknown_placeholder_kept():
    text, ents = format_placeholders("Hello {notathing} {name}", user=make_user())
    assert "{notathing}" in text
    assert "Asha" in text


def test_no_user_mention_fallback():
    text, ents = format_placeholders("Hi {mention}!")
    assert text == "Hi there!"
    assert ents == []


def test_split_long_keeps_entities():
    user = make_user()
    tpl = "{mention}\n" + ("line\n" * 2000)
    text, ents = format_placeholders(tpl, user=user)
    chunks = split_long(text, ents, limit=500)
    assert len(chunks) > 1
    # first chunk keeps the mention entity, offsets still valid per chunk
    c0, e0 = chunks[0]
    assert u16len(c0) <= 500
    assert len(e0) == 1
    u16 = c0.encode("utf-16-le")
    seg = u16[e0[0].offset * 2 : (e0[0].offset + e0[0].length) * 2].decode("utf-16-le")
    assert seg == "Asha Devi"
    # reassembled text equals original
    assert "".join(c for c, _ in chunks) == text


def test_has_link():
    assert has_link("check https://x.com now")
    assert has_link("t.me/somechannel")
    assert has_link("visit www.example.com")
    assert has_link("hello everyone") is False
    ent = SimpleNamespace(type=MessageEntity.URL)
    assert has_link("", [ent]) is True
