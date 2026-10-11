"""End-to-end test of the channel-as-database logic with a fake bot.

Simulates: fresh boot -> save snapshot (pin + description pointer) ->
"restart" -> restore from pin -> restart with pin removed -> restore via
description pointer -> snapshot rotation keeps only the newest 5.
"""

import asyncio
from types import SimpleNamespace

import pytest

from tgguard.db import KEEP_SNAPSHOTS, SNAP_FILENAME, SNAP_MARK, ChannelDB
from tgguard.state import State


class FakeDoc:
    def __init__(self, file_name, file_id):
        self.file_name = file_name
        self.file_id = file_id


class FakeMsg:
    def __init__(self, bot, message_id, document=None, caption=None, text=None):
        self._bot = bot
        self.message_id = message_id
        self.document = document
        self.caption = caption
        self.text = text

    async def delete(self):
        self._bot.messages.pop(self.message_id, None)
        if getattr(self._bot.chat, "pinned_message", None) and self._bot.chat.pinned_message.message_id == self.message_id:
            self._bot.chat.pinned_message = None


class FakeFile:
    def __init__(self, blob):
        self._blob = blob

    async def download_as_bytearray(self):
        return bytearray(self._blob)


class FakeBot:
    username = "testbot"

    def __init__(self):
        self.next_id = 1
        self.messages = {}          # id -> FakeMsg
        self.files = {}             # file_id -> bytes
        self.chat = SimpleNamespace(pinned_message=None, description="", id=-100123, title="DB")

    def _mid(self):
        m = self.next_id
        self.next_id += 1
        return m

    async def get_me(self):
        return SimpleNamespace(id=1, username=self.username)

    async def get_chat(self, channel):
        return self.chat

    async def get_chat_member(self, channel, uid):
        return SimpleNamespace(
            status="administrator",
            can_post_messages=True,
            can_pin_messages=True,
            can_delete_messages=True,
            can_change_info=True,
        )

    async def send_document(self, channel, document=None, filename=None, caption=None, disable_notification=False):
        blob = document.read()
        fid = f"file{self._mid()}"
        self.files[fid] = blob
        msg = FakeMsg(self, self._mid(), document=FakeDoc(filename or SNAP_FILENAME, fid), caption=caption)
        self.messages[msg.message_id] = msg
        return msg

    async def send_message(self, chat_id, text, disable_notification=False, **kw):
        msg = FakeMsg(self, self._mid(), text=text)
        self.messages[msg.message_id] = msg
        return msg

    async def pin_chat_message(self, channel, message_id, disable_notification=False):
        self.chat.pinned_message = self.messages[message_id]

    async def unpin_chat_message(self, channel, message_id=None):
        if self.chat.pinned_message and self.chat.pinned_message.message_id == message_id:
            self.chat.pinned_message = None

    async def set_chat_description(self, channel, description=None):
        self.chat.description = description or ""

    async def delete_message(self, channel, message_id):
        self.messages.pop(message_id, None)

    async def forward_message(self, chat_id, from_chat_id=None, message_id=None, disable_notification=False):
        src = self.messages[message_id]
        msg = FakeMsg(self, self._mid(), document=src.document, caption=src.caption, text=src.text)
        self.messages[msg.message_id] = msg
        return msg

    async def get_file(self, file_id):
        return FakeFile(self.files[file_id])


def make_cfg():
    return SimpleNamespace(db_channel=-100123, owner_id=777, snapshot_every=240, log_users=True)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def bot():
    return FakeBot()


def test_startup_no_snapshot(bot):
    db = ChannelDB(bot, make_cfg(), State())
    blob, source = run(db.startup())
    assert blob is None and source is None


def test_save_then_restore_from_pin(bot):
    cfg = make_cfg()
    s1 = State()
    s1.touch_user(11, "Asha", "Devi", "asha")
    s1.touch_user(12, "Bob")
    s1.ensure_group(-100555, "Voice Class")
    s1.add_plan("Pro", "₹2000", "8 weeks")
    db1 = ChannelDB(bot, cfg, s1)
    run(db1.startup())
    assert run(db1.save_snapshot(force=True)) is True
    assert bot.chat.pinned_message is not None
    assert bot.chat.pinned_message.caption.startswith(SNAP_MARK)
    assert f"snap={bot.chat.pinned_message.message_id}" in bot.chat.description

    # "restart"
    db2 = ChannelDB(bot, cfg, State())
    blob, source = run(db2.startup())
    assert source == "pinned"
    s2 = State.from_json(blob)
    assert s2.get_user(11)["first"] == "Asha"
    assert len(s2.users) == 2
    assert s2.get_group(-100555)["title"] == "Voice Class"
    assert any(p["price"] == "₹2000" for p in s2.plans())
    assert s2.data["seq"] == 1


def test_restore_via_description_pointer(bot):
    cfg = make_cfg()
    s1 = State()
    s1.touch_user(21, "Pointer")
    db1 = ChannelDB(bot, cfg, s1)
    run(db1.startup())
    run(db1.save_snapshot(force=True))
    snap_mid = bot.chat.pinned_message.message_id

    # someone unpinned the message in the channel
    bot.chat.pinned_message = None
    db2 = ChannelDB(bot, cfg, State())
    blob, source = run(db2.startup())
    assert source == "pointer"
    s2 = State.from_json(blob)
    assert s2.get_user(21)["first"] == "Pointer"
    # the temporary forward used for fetching got cleaned up
    assert snap_mid in bot.messages


def test_snapshot_rotation_keeps_last_n(bot):
    cfg = make_cfg()
    s = State()
    db = ChannelDB(bot, cfg, s)
    run(db.startup())
    for i in range(KEEP_SNAPSHOTS + 3):
        s.touch_user(100 + i, f"U{i}")
        run(db.save_snapshot(force=True))
    docs = [m for m in bot.messages.values() if m.document]
    assert len(docs) == KEEP_SNAPSHOTS
    assert len(s.data["snap_history"]) == KEEP_SNAPSHOTS
    # the pinned one is the newest snapshot
    assert bot.chat.pinned_message.message_id == s.data["snap_history"][-1]
    # and it still restores
    db2 = ChannelDB(bot, cfg, State())
    blob, source = run(db2.startup())
    assert source == "pinned"
    assert len(State.from_json(blob).users) == KEEP_SNAPSHOTS + 3


def test_dirty_flag_skips_save(bot):
    cfg = make_cfg()
    s = State()
    db = ChannelDB(bot, cfg, s)
    run(db.startup())
    run(db.save_snapshot(force=True))
    s.dirty = False
    assert run(db.save_snapshot()) is False  # nothing to do


def test_audit_posts_to_channel(bot):
    cfg = make_cfg()
    s = State()
    db = ChannelDB(bot, cfg, s)
    run(db.startup())
    run(db.audit("👋 hello channel"))
    texts = [m.text for m in bot.messages.values() if m.text]
    assert any("hello channel" in t for t in texts)


def test_store_media_forwards_into_channel(bot):
    cfg = make_cfg()
    s = State()
    db = ChannelDB(bot, cfg, s)
    run(db.startup())
    src = FakeMsg(bot, 999, text="sticker-ish")
    bot.messages[999] = src
    src.chat_id = 55

    async def go():
        return await db.store_media(src)

    mid = run(go())
    assert mid in bot.messages
