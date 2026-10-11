"""Offline handler simulations with a fake bot — the guard pipeline, reports,
warns, welcome, callback toggles, report punishments and scheduled posts."""

import asyncio
import os
from types import SimpleNamespace


os.environ.update(
    BOT_TOKEN="123456:TEST",
    DB_CHANNEL="-100999",
    OWNER_ID="42",
)

from tgguard import config  # noqa: E402
from tgguard.db import ChannelDB  # noqa: E402
from tgguard.handlers import callbacks as CB  # noqa: E402
from tgguard.handlers import engine as ENG  # noqa: E402
from tgguard.handlers import group_cmds as GC  # noqa: E402
from tgguard.state import State  # noqa: E402

run = asyncio.run


class FakeBot:
    username = "testbot"

    def __init__(self, admins=()):
        self.sent = []          # (chat_id, text, kwargs)
        self.deleted = []       # (chat_id, message_id)
        self.restricted = []    # (chat_id, uid, kwargs)
        self.banned = []
        self.edited = []
        self.copied = []
        self._admins = set(admins)
        self._mid = 100

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text, kw))
        self._mid += 1
        return SimpleNamespace(message_id=self._mid, chat_id=chat_id, text=text)

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kw):
        self.edited.append((chat_id, message_id, text))
        return True

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    async def restrict_chat_member(self, chat_id, user_id, **kw):
        self.restricted.append((chat_id, user_id, kw))
        return True

    async def ban_chat_member(self, chat_id, user_id, **kw):
        self.banned.append((chat_id, user_id, kw))
        return True

    async def unban_chat_member(self, chat_id, user_id, **kw):
        return True

    async def copy_message(self, chat_id, from_chat_id, message_id, **kw):
        self.copied.append((chat_id, from_chat_id, message_id))
        return SimpleNamespace(message_id=1)

    async def get_chat_administrators(self, chat_id):
        return [SimpleNamespace(user=SimpleNamespace(id=a), status="creator") for a in self._admins]

    async def get_chat_member(self, chat_id, user_id):
        status = "administrator" if user_id in self._admins else "member"
        return SimpleNamespace(status=status, can_delete_messages=True, can_restrict_members=True, can_pin_messages=True)

    async def get_me(self):
        return SimpleNamespace(id=1, username=self.username, is_bot=True)

    async def forward_message(self, chat_id, **kw):
        return SimpleNamespace(message_id=self._mid + 1, document=None, delete=lambda: None)


def make_context(bot=None, state=None):
    config.load()
    cfg = config.CFG
    state = state or State(tz="Asia/Kolkata")
    bot = bot or FakeBot()
    db = ChannelDB(bot, cfg, state)
    bot_data = {"state": state, "db": db, "cfg": cfg, "started": 0}
    return SimpleNamespace(
        bot=bot,
        bot_data=bot_data,
        user_data={},
        chat_data={},
        job_queue=None,
        application=SimpleNamespace(create_task=lambda c: None),
    ), state, db, bot


def fake_user(uid=501, first="Member", username="mem", is_bot=False):
    return SimpleNamespace(id=uid, first_name=first, last_name="", username=username, is_bot=is_bot,
                           full_name=first)


def fake_group_chat(gid=-100555, title="Voice Class"):
    return SimpleNamespace(id=gid, title=title, type="supergroup")


def fake_msg(chat, user, text="", mid=7, reply_to=None, entities=(), deleted=None):
    async def _delete():
        if deleted is not None:
            deleted.append((chat.id, mid))

    async def _forward(chat_id, **kw):
        return None

    m = SimpleNamespace(
        text=text, caption=None, entities=list(entities), caption_entities=[],
        sender_chat=None, message_id=mid, chat=chat, from_user=user,
        reply_to_message=reply_to, photo=None, sticker=None, animation=None,
        video=None, document=None, audio=None, voice=None, video_note=None,
        new_chat_members=None, delete=_delete, forward=_forward,
    )

    async def _reply(t, **kw):
        return None

    m.reply_text = _reply
    return m


def fake_update(chat, user, msg):
    return SimpleNamespace(effective_chat=chat, effective_user=user, effective_message=msg, callback_query=None)


# ---------------------------------------------------------------------- #
def test_linkguard_deletes_and_notifies():
    ctx, state, db, bot = make_context()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    g["linkguard"]["on"] = True
    chat, user = fake_group_chat(gid), fake_user()
    msg = fake_msg(chat, user, "join now https://t.me/scamchannel", deleted=bot.deleted)
    run(ENG.guard_pipeline(fake_update(chat, user, msg), ctx))
    assert bot.deleted and bot.deleted[0][0] == gid
    notices = [(t, kw) for c, t, kw in bot.sent if c == gid]
    assert notices and "Links are NOT allowed" in notices[0][0]
    # mention entity present in the notice
    assert notices[0][1].get("entities"), "notice must ping the user with a real mention entity"
    assert state.data["counters"]["deletes"] == 1
    assert g["stats"]["deletes"] == 1


def test_linkguard_ignores_admins():
    ctx, state, db, bot = make_context()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    g["linkguard"]["on"] = True
    bot._admins.add(501)
    chat, user = fake_group_chat(gid), fake_user(501)
    msg = fake_msg(chat, user, "https://t.me/ok")
    run(ENG.guard_pipeline(fake_update(chat, user, msg), ctx))
    assert not bot.deleted


def test_badword_mutes_and_deletes():
    ctx, state, db, bot = make_context()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    g["badwords"].append({"id": "b1", "word": "idiot", "mute_min": 30})
    chat, user = fake_group_chat(gid), fake_user()
    msg = fake_msg(chat, user, "you are an IDIOT bro", deleted=bot.deleted)
    run(ENG.guard_pipeline(fake_update(chat, user, msg), ctx))
    assert bot.deleted
    assert bot.restricted and bot.restricted[0][1] == user.id
    until = bot.restricted[0][2]["until_date"]
    assert until is not None
    assert any("muted" in t.lower() or "Muted" in t for c, t, kw in bot.sent if c == gid)
    assert g["stats"]["mutes"] == 1
    # audit reached the DB channel
    assert any(c == config.CFG.db_channel and "AUTO-MUTE" in t for c, t, _ in bot.sent)


def test_filter_text_reply_for_everyone():
    ctx, state, db, bot = make_context()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    g["filters"].append({"id": "f1", "word": "hello", "kind": "text", "content": "Hi {mention}! Welcome 👋"})
    bot._admins.add(501)
    chat, user = fake_group_chat(gid), fake_user(501, "Adminji")
    msg = fake_msg(chat, user, "Hello everyone")
    run(ENG.guard_pipeline(fake_update(chat, user, msg), ctx))
    replies = [(t, kw) for c, t, kw in bot.sent if c == gid]
    assert replies and "Hi Adminji! Welcome" in replies[0][0]
    assert replies[0][1].get("reply_to_message_id") == 7
    assert state.data["counters"]["filters_hit"] == 1


def test_flood_control():
    ctx, state, db, bot = make_context()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    g["flood"].update({"on": True, "limit": 3, "window": 10, "mute_min": 5})
    chat, user = fake_group_chat(gid), fake_user()
    for i in range(4):
        msg = fake_msg(chat, user, f"msg {i}", mid=10 + i)
        run(ENG.guard_pipeline(fake_update(chat, user, msg), ctx))
    assert bot.restricted, "flooder should be muted"
    assert g["stats"]["mutes"] == 1


# ---------------------------------------------------------------------- #
def test_report_admin_gets_rmsg_not_forwarded():
    ctx, state, db, bot = make_context()
    gid = -100555
    state.ensure_group(gid, "Voice Class")
    state.settings["rmsg"] = "🚫 {mention}, admins cannot be reported. Behave!"
    bot._admins.add(600)  # target is an admin
    chat = fake_group_chat(gid)
    reporter = fake_user(501, "Chotu")
    target = fake_user(600, "AdminSir")
    replied = fake_msg(chat, target, "class at 8", mid=55)
    msg = fake_msg(chat, reporter, "/report he is rude", mid=56, reply_to=replied)
    run(ENG.cmd_report(fake_update(chat, reporter, msg), ctx))
    group_msgs = [t for c, t, _ in bot.sent if c == gid]
    assert any("admins cannot be reported" in t and "Chotu" in t for t in group_msgs)
    owner_msgs = [t for c, t, _ in bot.sent if c == config.CFG.owner_id]
    assert not owner_msgs, "report against an admin must NOT reach the owner"


def test_report_member_reaches_owner_with_buttons():
    ctx, state, db, bot = make_context()
    gid = -100555
    state.ensure_group(gid, "Voice Class")
    chat = fake_group_chat(gid)
    reporter = fake_user(501, "Chotu")
    target = fake_user(600, "Spammer")
    replied = fake_msg(chat, target, "buy my course cheap", mid=55)
    msg = fake_msg(chat, reporter, "/report advertising", mid=56, reply_to=replied)
    run(ENG.cmd_report(fake_update(chat, reporter, msg), ctx))
    owner_msgs = [(t, kw) for c, t, kw in bot.sent if c == config.CFG.owner_id]
    assert owner_msgs, "owner should receive the report"
    header, kw = owner_msgs[-1]
    assert "NEW REPORT" in header and "Spammer" in header and "advertising" in header
    markup = kw.get("reply_markup")
    flat = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert any(d.startswith("rp:") and d.endswith(":ban") for d in flat)
    # confirmation in the group with mention
    assert any("report was sent to the admin" in t for c, t, _ in bot.sent if c == gid)


def test_exec_report_action_ban_and_mute():
    ctx, state, db, bot = make_context()
    rid = state.add_report({"gid": -100555, "gtitle": "Voice Class", "target": "X", "target_id": 600, "mid": 55, "reason": "r"})
    out = run(ENG.exec_report_action(ctx, rid, "ban", 604800))
    assert "BANNED" in out and bot.banned and bot.banned[0][1] == 600
    rid2 = state.add_report({"gid": -100555, "gtitle": "Voice Class", "target": "Y", "target_id": 601, "mid": 56, "reason": "r"})
    out2 = run(ENG.exec_report_action(ctx, rid2, "mute", 3600))
    assert "MUTED" in out2 and any(r[1] == 601 for r in bot.restricted)
    rid3 = state.add_report({"gid": -100555, "gtitle": "VC", "target": "", "target_id": 0, "mid": 57, "reason": "r"})
    out3 = run(ENG.exec_report_action(ctx, rid3, "del", 0))
    assert "deleted" in out3 and (-100555, 57) in bot.deleted


def test_warn_limit_triggers_mute():
    ctx, state, db, bot = make_context()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    g["warns"].update({"limit": 2, "action": "mute", "mute_min": 15})
    chat = fake_group_chat(gid)
    target = fake_user(700, "Bad")
    run(ENG.apply_warn(ctx, chat, target, "r1", by=fake_user(42)))
    assert g["warns"]["counts"]["700"] == 1
    assert not bot.restricted
    run(ENG.apply_warn(ctx, chat, target, "r2", by=fake_user(42)))
    assert bot.restricted and bot.restricted[0][1] == 700
    assert g["warns"]["counts"]["700"] == 0
    assert any("WARN-LIMIT" in t for c, t, _ in bot.sent if c == config.CFG.db_channel)


def test_welcome_with_mention():
    ctx, state, db, bot = make_context()
    gid = -100555
    state.ensure_group(gid, "Voice Class")
    chat = fake_group_chat(gid)
    user = fake_user(800, "Newbie")
    run(ENG.do_welcome(ctx, chat, user))
    welc = [(t, kw) for c, t, kw in bot.sent if c == gid]
    assert welc and "Welcome Newbie to Voice Class!" in welc[0][0]
    assert welc[0][1].get("entities"), "welcome must contain a real mention entity"
    # join logged into the DB channel
    assert any(c == config.CFG.db_channel and "JOINED GROUP" in t and "Newbie" in t for c, t, _ in bot.sent)
    assert state.data["counters"]["welcomes"] == 1


def test_callback_toggle_welcome():
    ctx, state, db, bot = make_context()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    assert g["welcome"]["on"] is True
    user = fake_user(42, "Owner")

    async def _answer(text=None, **kw):
        return None

    cbq = SimpleNamespace(
        data=f"gt:groups.{gid}.welcome.on",
        from_user=user,
        message=SimpleNamespace(chat_id=42, message_id=900, text="x"),
        answer=_answer,
        edit_message_text=lambda *a, **k: None,
    )
    upd = SimpleNamespace(callback_query=cbq, effective_user=user, effective_chat=SimpleNamespace(id=42, type="private"), effective_message=None)
    run(CB.on_callback(upd, ctx))
    assert g["welcome"]["on"] is False
    # screen was re-rendered through edit_message_text
    assert any(e[1] == 900 for e in bot.edited)


def test_my_chat_member_pm_block_marks_user():
    ctx, state, db, bot = make_context()
    state.touch_user(55, "Blocker")
    cmu = SimpleNamespace(
        chat=SimpleNamespace(type="private", id=55),
        new_chat_member=SimpleNamespace(status="kicked"),
        old_chat_member=SimpleNamespace(status="member"),
        from_user=fake_user(55, "Blocker"),
    )
    upd = SimpleNamespace(chat_member=cmu, effective_chat=cmu.chat, effective_user=cmu.from_user)
    run(ENG.on_my_chat_member(upd, ctx))
    assert state.get_user(55)["blocked"] is True
    assert any("blocked the bot" in t.lower() for c, t, _ in bot.sent if c == config.CFG.db_channel)


def test_schedule_fires_and_rearms():
    fired = []

    class FakeJQ:
        def get_jobs_by_name(self, name):
            return []

        def run_once(self, cb, when=None, data=None, name=None):
            fired.append((name, when, data))

        def jobs(self):
            return []

    ctx, state, db, bot = make_context()
    ctx.job_queue = FakeJQ()
    gid = -100555
    g, _ = state.ensure_group(gid, "Voice Class")
    g["schedules"].append({"id": "s1", "time": "20:00", "repeat": "daily", "on": True, "text": "🎙 Class starts at 8 PM {group}!"})
    run(ENG.ensure_schedule_job(ctx, gid, "s1"))
    assert fired and fired[0][0] == f"sc:{gid}:s1"
    ctx.job = SimpleNamespace(data={"gid": gid, "sid": "s1"})
    run(ENG.sched_fire(ctx))
    posts = [t for c, t, _ in bot.sent if c == gid]
    assert posts and "Class starts at 8 PM Voice Class!" in posts[0]
    assert len(fired) == 2, "daily schedule must re-arm itself"
    # once-schedule removes itself after firing
    g["schedules"].append({"id": "s2", "time": "09:00", "repeat": "once", "on": True, "text": "Exam tomorrow!"})
    run(ENG.ensure_schedule_job(ctx, gid, "s2"))
    ctx.job = SimpleNamespace(data={"gid": gid, "sid": "s2"})
    run(ENG.sched_fire(ctx))
    assert all(s["id"] != "s2" for s in g["schedules"])


def test_group_cmd_gw_sets_and_saves():
    ctx, state, db, bot = make_context()
    gid = -100555
    state.ensure_group(gid, "Voice Class")
    bot._admins.add(42)
    chat = fake_group_chat(gid)
    owner = fake_user(42, "Owner")
    replies = []
    msg = fake_msg(chat, owner, "/gw Hello {mention}, welcome to our class! 🎙", mid=31)
    async def _reply(t, **kw):
        replies.append(t)
    msg.reply_text = _reply
    run(GC.cmd_gw(fake_update(chat, owner, msg), ctx))
    g = state.get_group(gid)
    assert g["welcome"]["text"] == "Hello {mention}, welcome to our class! 🎙"
    assert replies and "updated" in replies[0].lower()
    assert state.dirty  # will be saved to the DB channel on next tick


def test_group_cmd_denied_for_non_admin():
    ctx, state, db, bot = make_context()
    gid = -100555
    state.ensure_group(gid, "Voice Class")
    chat = fake_group_chat(gid)
    user = fake_user(999, "Normal")
    replies = []
    msg = fake_msg(chat, user, "/lock", mid=33)
    async def _reply(t, **kw):
        replies.append(t)
    msg.reply_text = _reply
    run(GC.cmd_lock(fake_update(chat, user, msg), ctx))
    assert replies and "Only group admins" in replies[0]
