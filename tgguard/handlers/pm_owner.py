"""Owner commands & the interactive flows behind the buttons.

Owner commands:  /panel /users /msg /broadcast /busers /stats /edit
                 /setwelcome /gwelcome /setabout /setcourses /setcontact
                 /setrules /rmsg /plans /setprice /addplan /tz /backup /restore /id
Every one of them also has a coloured button equivalent (see screens.py).
"""

from __future__ import annotations

import asyncio
import logging
from functools import wraps

from telegram import Update
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import CommandHandler, filters

from ..state import MISSING, State
from ..timeutil import now_ts
from . import screens as S
from .common import (
    CTX,
    clear_flow,
    dm_user,
    edit_screen,
    flow,
    get_db,
    get_state,
    is_group_admin,
    is_owner,
    media_info,
    send_text,
    show_screen,
    start_flow,
)
from .render import render_cb

log = logging.getLogger("tgguard.owner")


# ---------------------------------------------------------------------- #
# guards
# ---------------------------------------------------------------------- #
def owner_cmd(fn):
    @wraps(fn)
    async def wrap(update: Update, context: CTX):
        user = update.effective_user
        if not user or not is_owner(user.id):
            if update.effective_message:
                await update.effective_message.reply_text("🔒 This command is for the bot owner only.")
            return None
        return await fn(update, context)

    return wrap


async def can_edit_path(context: CTX, uid: int, path: str) -> bool:
    """settings.* -> owner only; groups.<gid>.* -> owner or that group's admin."""
    if path.startswith("settings."):
        return is_owner(uid)
    if path.startswith("groups."):
        try:
            gid = int(path.split(".", 2)[1])
        except (ValueError, IndexError):
            return False
        return await is_group_admin(context, gid, uid)
    return is_owner(uid)


# ---------------------------------------------------------------------- #
# the universal setter (used by /edit, ✏️ buttons and /set* commands)
# ---------------------------------------------------------------------- #
async def apply_set(
    context: CTX,
    path: str,
    value,
    *,
    actor_name: str = "owner",
    src: tuple[int, int] | None = None,
    reply: Update | None = None,
    silent: bool = False,
) -> bool:
    state = get_state(context)
    db = get_db(context)
    if state.get_path(path, MISSING) is MISSING:
        if reply and reply.effective_message:
            await reply.effective_message.reply_text(f"⚠️ Unknown setting path: {path}")
        return False
    # int-typed leaves
    if S.is_int_path(path):
        try:
            value = int(str(value).strip())
            if value < 0 or value > 1_000_000:
                raise ValueError
        except ValueError:
            if reply and reply.effective_message:
                await reply.effective_message.reply_text("⚠️ Please send a plain number (e.g. 10).")
            return False
    try:
        state.set_path(path, value)
    except KeyError:
        if reply and reply.effective_message:
            await reply.effective_message.reply_text(f"⚠️ Unknown setting path: {path}")
        return False
    label = S.label_for(state, path)
    preview = value if isinstance(value, str) else str(value)
    await db.audit_setting(path, actor_name, preview)
    db.request_save()
    if not silent:
        if reply and reply.effective_message:
            await reply.effective_message.reply_text(f"✅ Saved — {label}\n💾 Written to the database channel.")
    # re-render the source message (the one whose ✏️ was pressed / that was replied to)
    if src:
        screen = await render_cb(S.parent_of(path), context, user=None) or S.scr_setting_view(state, path)
        await edit_screen(context, src[0], src[1], screen)
    return True


# ---------------------------------------------------------------------- #
# /edit — reply-to-message editing (the core "customise anything" command)
# ---------------------------------------------------------------------- #
@owner_cmd
async def cmd_edit(update: Update, context: CTX) -> None:
    msg = update.effective_message
    state = get_state(context)
    replied = msg.reply_to_message
    args = context.args or []

    if replied is not None and replied.from_user and replied.from_user.id == context.bot.id:
        ent = state.editable_key(msg.chat_id, replied.message_id)
        if ent:
            new_text = msg.text.split(None, 1)[1] if " " in (msg.text or "") else ""
            if new_text.strip():
                await apply_set(context, ent["key"], new_text, src=(msg.chat_id, replied.message_id), reply=update)
            else:
                from ..textfmt import PLACEHOLDER_HELP

                start_flow(
                    context,
                    "setpath",
                    f"✏️ Editing: {ent.get('label') or ent['key']}",
                    path=ent["key"],
                    src=(msg.chat_id, replied.message_id),
                )
                await msg.reply_text(
                    f"✏️ Editing: {ent.get('label') or ent['key']}\n\n"
                    f"Send the NEW text now (/cancel to abort).\n💡 placeholders: {PLACEHOLDER_HELP}"
                )
            return
        await msg.reply_text(
            "🤔 That message isn't a registered editable text.\n"
            "Open ⚙️ Settings (or /panel) and use the ✏️ Edit buttons — or reply to a message shown from there."
        )
        return

    if len(args) >= 2 and "." in args[0]:
        await apply_set(context, args[0], " ".join(args[1:]), reply=update)
        return

    await show_screen(context, msg.chat_id, S.scr_editables(state))


# ---------------------------------------------------------------------- #
# setpath flow (✏️ Edit buttons & /edit without text)
# ---------------------------------------------------------------------- #
@flow("setpath")
async def flow_setpath(fl: dict, update: Update, context: CTX) -> None:
    msg = update.effective_message
    path = fl["path"]
    uid = update.effective_user.id
    if not await can_edit_path(context, uid, path):
        await msg.reply_text("🔒 You may not edit this setting.")
        clear_flow(context)
        return
    if S.is_int_path(path):
        value = msg.text
    else:
        value = msg.text or msg.caption or ""
        if len(value) > 3500:
            await msg.reply_text("⚠️ Too long (max ~3500 characters). Send a shorter text.")
            return  # keep flow alive for another try
    ok = await apply_set(context, path, value, actor_name=update.effective_user.full_name, src=fl.get("src"), reply=update)
    if ok:
        clear_flow(context)
        if not fl.get("src"):
            screen = await render_cb(S.parent_of(path), context, user=update.effective_user)
            if screen:
                await show_screen(context, msg.chat_id, screen)
    # on failure the flow stays open so the user can retry


# ---------------------------------------------------------------------- #
# simple screen commands
# ---------------------------------------------------------------------- #
@owner_cmd
async def cmd_panel(update: Update, context: CTX) -> None:
    screen = await render_cb("om", context, user=update.effective_user)
    await show_screen(context, update.effective_chat.id, screen)


@owner_cmd
async def cmd_users(update: Update, context: CTX) -> None:
    page = 0
    if context.args and context.args[0].isdigit():
        page = int(context.args[0])
    await show_screen(context, update.effective_chat.id, S.scr_users(get_state(context), page))


@owner_cmd
async def cmd_busers(update: Update, context: CTX) -> None:
    await show_screen(context, update.effective_chat.id, S.scr_users(get_state(context), 0, blocked_only=True))


@owner_cmd
async def cmd_stats(update: Update, context: CTX) -> None:
    import time

    up = int(time.time() - float(context.bot_data.get("started", time.time())))
    await show_screen(context, update.effective_chat.id, S.scr_stats(get_state(context), up))


@owner_cmd
async def cmd_id(update: Update, context: CTX) -> None:
    await update.effective_message.reply_text(
        f"🆔 Your user id: {update.effective_user.id}\n💬 This chat id: {update.effective_chat.id}"
    )


# ---------------------------------------------------------------------- #
# /msg — P2P message to any user
# ---------------------------------------------------------------------- #
def _forward_sender(msg) -> int | None:
    fo = getattr(msg, "forward_origin", None)
    if fo is not None:
        su = getattr(fo, "sender_user", None)
        if su:
            return su.id
    fu = getattr(msg, "forward_from", None)
    return fu.id if fu else None


@owner_cmd
async def cmd_msg(update: Update, context: CTX) -> None:
    msg = update.effective_message
    args = context.args or []
    replied = msg.reply_to_message
    if replied is not None:
        uid = _forward_sender(replied)
        if uid:
            text = msg.text.split(None, 1)[1] if " " in (msg.text or "") else ""
            if text.strip():
                ok = await dm_user(context, uid, text)
                await msg.reply_text("✅ Message delivered." if ok else "🚫 Delivery failed — user probably blocked the bot (added to /busers).")
            else:
                start_flow(context, "msg_user", f"✉️ Sending to user {uid}.\nSend the message now (text, photo, sticker, anything) — /cancel to abort.", uid=uid)
                await msg.reply_text(f"✉️ OK — now send me the message for user {uid}.")
            return
    if args and re_isdigit(args[0]):
        uid = int(args[0])
        text = " ".join(args[1:])
        if text:
            ok = await dm_user(context, uid, text)
            await msg.reply_text("✅ Message delivered." if ok else "🚫 Delivery failed — user probably blocked the bot (added to /busers).")
        else:
            start_flow(context, "msg_user", f"✉️ Sending to user {uid}.\nSend the message now — /cancel to abort.", uid=uid)
            await msg.reply_text(f"✉️ OK — now send me the message for user {uid}.")
        return
    start_flow(context, "msg_ask_uid", "✉️ Send the USER ID you want to message (digits only).\n\nTip: forward me any message from that user and reply /msg to it.", )
    await msg.reply_text("✉️ Send the user ID to message.\n(Tip: /users → tap a user → ✉️ Send Message is faster.)")


def re_isdigit(s: str) -> bool:
    s = s.strip()
    return s.lstrip("-").isdigit()


@flow("msg_ask_uid")
async def flow_msg_ask_uid(fl: dict, update: Update, context: CTX) -> None:
    txt = (update.effective_message.text or "").strip()
    if not re_isdigit(txt):
        await update.effective_message.reply_text("⚠️ That doesn't look like a user id. Send digits only, or /cancel.")
        return
    uid = int(txt)
    clear_flow(context)
    start_flow(context, "msg_user", f"✉️ Sending to user {uid}.\nSend the message now — /cancel to abort.", uid=uid)
    await update.effective_message.reply_text(f"✉️ Now send me the message for {uid}.")


@flow("msg_user")
async def flow_msg_user(fl: dict, update: Update, context: CTX) -> None:
    msg = update.effective_message
    uid = int(fl["uid"])
    state = get_state(context)
    m = media_info(msg)
    ok = False
    if m:
        try:
            await context.bot.copy_message(uid, from_chat_id=msg.chat_id, message_id=msg.message_id)
            ok = True
        except Forbidden:
            await _blocked(context, uid)
        except TelegramError as e:
            log.warning("msg copy failed: %s", e)
    else:
        ok = await dm_user(context, uid, msg.text or msg.caption or "")
    if ok:
        rec = state.get_user(uid)
        state.changed()
        await msg.reply_text(f"✅ Delivered to {rec.get('first') if rec else uid}.")
    clear_flow(context)


async def _blocked(context: CTX, uid: int) -> None:
    state = get_state(context)
    state.mark_blocked(uid, True)
    await get_db(context).audit_blocked(state.get_user(uid) or {"id": uid, "first": "?"})


# ---------------------------------------------------------------------- #
# /broadcast
# ---------------------------------------------------------------------- #
@owner_cmd
async def cmd_broadcast(update: Update, context: CTX) -> None:
    msg = update.effective_message
    state = get_state(context)
    targets = state.users_sorted(blocked=False)
    replied = msg.reply_to_message
    content = None
    if replied is not None and replied.message_id != msg.message_id:
        content = {"kind": "copy", "chat": msg.chat_id, "mid": replied.message_id}
    else:
        text = msg.text.split(None, 1)[1] if " " in (msg.text or "") else ""
        if text.strip():
            content = {"kind": "text", "text": text}
    if content:
        await _bc_preview(context, msg, content, len(targets))
    else:
        start_flow(context, "bc_content", "📢 BROADCAST\nSend (or forward) the content to broadcast to all users — text, photo, video, sticker…\n/cancel to abort.")
        await msg.reply_text(f"📢 Send the content to broadcast to your {len(targets)} users.\n(forward anything, or type text)")


@flow("bc_content")
async def flow_bc_content(fl: dict, update: Update, context: CTX) -> None:
    msg = update.effective_message
    state = get_state(context)
    targets = state.users_sorted(blocked=False)
    if not (msg.text or media_info(msg)):
        await msg.reply_text("⚠️ Send some content (text or media), or /cancel.")
        return
    content = {"kind": "copy", "chat": msg.chat_id, "mid": msg.message_id}
    clear_flow(context)
    await _bc_preview(context, msg, content, len(targets))


async def _bc_preview(context: CTX, msg, content: dict, n: int) -> None:
    from ..keyboards import GREEN, RED, cbtn, kboard

    context.user_data["bc"] = content
    if content["kind"] == "copy":
        try:
            await context.bot.copy_message(msg.chat_id, from_chat_id=content["chat"], message_id=content["mid"])
        except TelegramError:
            pass
    await context.bot.send_message(
        msg.chat_id,
        f"📢 Broadcast this to {n} users?",
        reply_markup=kboard(
            [
                [cbtn(f"📢 SEND to {n}", "bcgo", color=GREEN), cbtn("❌ Cancel", "bccancel", color=RED)],
            ]
        ),
    )


async def run_broadcast(context: CTX, chat_id: int, progress_mid: int, content: dict) -> None:
    state = get_state(context)
    db = get_db(context)
    targets = state.users_sorted(blocked=False)
    total = len(targets)
    ok = blocked = failed = 0
    for i, u in enumerate(targets, 1):
        uid = int(u["id"])
        try:
            if content["kind"] == "text":
                await context.bot.send_message(uid, content["text"][:4096])
            else:
                await context.bot.copy_message(uid, from_chat_id=content["chat"], message_id=content["mid"])
            ok += 1
        except Forbidden:
            state.mark_blocked(uid, True)
            blocked += 1
        except BadRequest as e:
            if "blocked" in str(e).lower():
                state.mark_blocked(uid, True)
                blocked += 1
            else:
                failed += 1
        except TelegramError:
            failed += 1
        if i % 25 == 0:
            await asyncio.sleep(1)
            try:
                await context.bot.edit_message_text(
                    chat_id, progress_mid, f"📢 Broadcasting… {i}/{total}\n✅ {ok} • 🚫 {blocked} • ⚠️ {failed}"
                )
            except TelegramError:
                pass
    state.changed()
    db.request_save()
    summary = f"📢 Broadcast finished.\n\n✅ Delivered: {ok}\n🚫 Blocked (auto-added to /busers): {blocked}\n⚠️ Failed: {failed}\n👥 Total: {total}"
    try:
        await context.bot.edit_message_text(chat_id, progress_mid, summary)
    except TelegramError:
        await send_text(context, chat_id, summary)
    await db.audit(f"📢 Broadcast by owner — delivered {ok}/{total}, {blocked} blocked detected")


# ---------------------------------------------------------------------- #
# plans & prices
# ---------------------------------------------------------------------- #
@owner_cmd
async def cmd_plans(update: Update, context: CTX) -> None:
    await show_screen(context, update.effective_chat.id, S.scr_plans_manage(get_state(context)))


@owner_cmd
async def cmd_addplan(update: Update, context: CTX) -> None:
    args = context.args or []
    state = get_state(context)
    if len(args) >= 2:
        # /addplan Name | ₹499 | details...
        parts = [x.strip() for x in " ".join(args).split("|")]
        name = parts[0]
        price = parts[1] if len(parts) > 1 else "—"
        detail = parts[2] if len(parts) > 2 else ""
        p = state.add_plan(name, price, detail)
        await get_db(context).audit_setting(f"settings.plans[{p['id']}]", update.effective_user.full_name, f"ADDED {name} {price}")
        get_db(context).request_save()
        await show_screen(context, update.effective_chat.id, S.scr_plans_manage(state))
        return
    start_flow(context, "plan_name", "➕ NEW PLAN (1/3)\nSend the plan/course NAME.\nExample: 🎙️ Voice Basics")
    await update.effective_message.reply_text("➕ Let's add a plan.\nSend the NAME (1/3):")


@flow("plan_name")
async def flow_plan_name(fl: dict, update: Update, context: CTX) -> None:
    name = (update.effective_message.text or "").strip()
    if not name:
        await update.effective_message.reply_text("⚠️ Send a name (text).")
        return
    fl["act"] = "plan_price"
    fl["name"] = name[:120]
    await update.effective_message.reply_text("💰 (2/3) Now send the PRICE — anything you like: ₹499, $10, Free, “DM for price”…")


@flow("plan_price")
async def flow_plan_price(fl: dict, update: Update, context: CTX) -> None:
    price = (update.effective_message.text or "").strip()
    if not price:
        await update.effective_message.reply_text("⚠️ Send a price (text).")
        return
    fl["act"] = "plan_detail"
    fl["price"] = price[:60]
    await update.effective_message.reply_text("📝 (3/3) Send a short DETAIL line (duration, what's included…) — or send “-” to skip.")


@flow("plan_detail")
async def flow_plan_detail(fl: dict, update: Update, context: CTX) -> None:
    detail = (update.effective_message.text or "").strip()
    if detail in ("-", "—", "skip", "."):
        detail = ""
    state = get_state(context)
    p = state.add_plan(fl["name"], fl["price"], detail[:300])
    db = get_db(context)
    await db.audit_setting(f"settings.plans[{p['id']}]", update.effective_user.full_name, f"ADDED {p['name']} — {p['price']}")
    db.request_save()
    clear_flow(context)
    await update.effective_message.reply_text(f"✅ Plan added: {p['name']} — {p['price']}\n💾 Saved to the database channel.")
    await show_screen(context, update.effective_chat.id, S.scr_plans_manage(state))


@owner_cmd
async def cmd_delplan(update: Update, context: CTX) -> None:
    state = get_state(context)
    args = context.args or []
    if args and state.del_plan(args[0]):
        get_db(context).request_save()
        await update.effective_message.reply_text("🗑 Plan deleted.")
    else:
        await update.effective_message.reply_text("Usage: /delplan <plan-id> (see ids in 💰 Plans & Prices).")
    await show_screen(context, update.effective_chat.id, S.scr_plans_manage(state))


# ---------------------------------------------------------------------- #
# /set* quick setters
# ---------------------------------------------------------------------- #
def make_setter(key: str):
    path = f"settings.{key}"

    @owner_cmd
    async def handler(update: Update, context: CTX) -> None:
        msg = update.effective_message
        state = get_state(context)
        text = msg.text.split(None, 1)[1] if " " in (msg.text or "") else ""
        if text.strip():
            await apply_set(context, path, text, actor_name=update.effective_user.full_name, reply=update)
        else:
            sent = await show_screen(context, msg.chat_id, S.scr_setting_view(state, path))
            if sent:
                state.register_editable(msg.chat_id, sent.message_id, path, S.label_for(state, path))

    return handler


# ---------------------------------------------------------------------- #
# timezone
# ---------------------------------------------------------------------- #
@owner_cmd
async def cmd_tz(update: Update, context: CTX) -> None:
    state = get_state(context)
    args = context.args or []
    if not args:
        await show_screen(context, update.effective_chat.id, S.scr_timezone(state))
        start_flow(context, "tz_set", "Send a timezone name, e.g. Asia/Kolkata")
        return
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = args[0]
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        await update.effective_message.reply_text(f"⚠️ Unknown timezone “{name}”. Example: Asia/Kolkata, Asia/Dubai, UTC")
        return
    state.settings["tz"] = name
    state.changed()
    get_db(context).request_save()
    from .engine import rebuild_all_schedules

    await rebuild_all_schedules(context)
    await update.effective_message.reply_text(f"🕘 Timezone set to {name}. All schedules re-armed.")


@flow("tz_set")
async def flow_tz_set(fl: dict, update: Update, context: CTX) -> None:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = (update.effective_message.text or "").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        await update.effective_message.reply_text(f"⚠️ Unknown timezone “{name}”. Try Asia/Kolkata.")
        return
    state = get_state(context)
    state.settings["tz"] = name
    state.changed()
    get_db(context).request_save()
    from .engine import rebuild_all_schedules

    await rebuild_all_schedules(context)
    clear_flow(context)
    await update.effective_message.reply_text(f"🕘 Timezone set to {name}.")


# ---------------------------------------------------------------------- #
# backup / restore
# ---------------------------------------------------------------------- #
@owner_cmd
async def cmd_backup(update: Update, context: CTX) -> None:
    from io import BytesIO

    state = get_state(context)
    blob = state.to_json()
    fname = f"tgguard_backup_{now_ts()}.json"
    await context.bot.send_document(
        update.effective_chat.id,
        document=BytesIO(blob),
        filename=fname,
        caption=f"💾 TG-Guard backup — {len(state.users)} users, {len(state.active_groups())} groups, snapshot v{state.data.get('seq', 0)}.",
    )


@owner_cmd
async def cmd_restore(update: Update, context: CTX) -> None:
    msg = update.effective_message
    if msg.reply_to_message and msg.reply_to_message.document:
        await _do_restore(context, msg.reply_to_message)
        return
    start_flow(context, "restore_wait", "Send me the backup .json file.")
    await msg.reply_text("🔄 Send me the backup .json file (or reply /restore to it).")


@flow("restore_wait")
async def flow_restore_wait(fl: dict, update: Update, context: CTX) -> None:
    msg = update.effective_message
    if not msg.document:
        await msg.reply_text("⚠️ Please send the .json backup FILE, or /cancel.")
        return
    clear_flow(context)
    await _do_restore(context, msg)


async def _do_restore(context: CTX, msg) -> None:
    state = get_state(context)
    db = get_db(context)
    try:
        if (msg.document.file_size or 0) > 8 * 1024 * 1024:
            await msg.reply_text("⚠️ File too big (>8MB) — not a TG-Guard backup?")
            return
        f = await context.bot.get_file(msg.document.file_id)
        blob = bytes(await f.download_as_bytearray())
        new_state = State.from_json(blob, tz=state.tz)
    except Exception as e:
        await msg.reply_text(f"⚠️ Restore failed: {e}")
        return
    old_u, old_g = len(state.users), len(state.active_groups())
    state.data = new_state.data
    state.changed()
    await db.audit(f"🔄 DATABASE RESTORED from backup file by owner — users {old_u}→{len(state.users)}, groups {old_g}→{len(state.active_groups())}")
    await db.save_snapshot(force=True)
    from .engine import rebuild_all_schedules

    await rebuild_all_schedules(context)
    await msg.reply_text(
        f"✅ Restored!\n👥 Users: {len(state.users)}\n🛡 Groups: {len(state.active_groups())}\n💰 Plans: {len(state.plans())}\n💾 New snapshot uploaded to the DB channel."
    )


# ---------------------------------------------------------------------- #
# registration
# ---------------------------------------------------------------------- #
def register(app) -> None:
    pm = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler(["panel", "admin", "menu"], cmd_panel, filters=pm))
    app.add_handler(CommandHandler(["users", "userlist"], cmd_users, filters=pm))
    app.add_handler(CommandHandler(["busers", "blocked"], cmd_busers, filters=pm))
    app.add_handler(CommandHandler("stats", cmd_stats, filters=pm))
    app.add_handler(CommandHandler("edit", cmd_edit, filters=pm))
    app.add_handler(CommandHandler("msg", cmd_msg, filters=pm))
    app.add_handler(CommandHandler(["broadcast", "bc"], cmd_broadcast, filters=pm))
    app.add_handler(CommandHandler(["plans", "setprice", "prices"], cmd_plans, filters=pm))
    app.add_handler(CommandHandler("addplan", cmd_addplan, filters=pm))
    app.add_handler(CommandHandler("delplan", cmd_delplan, filters=pm))
    app.add_handler(CommandHandler(["tz", "timezone"], cmd_tz, filters=pm))
    app.add_handler(CommandHandler("backup", cmd_backup, filters=pm))
    app.add_handler(CommandHandler("restore", cmd_restore, filters=pm))
    app.add_handler(CommandHandler("id", cmd_id, filters=pm))

    app.add_handler(CommandHandler(["setwelcome", "setwel", "setwelcome"], make_setter("welcome_pm"), filters=pm))
    app.add_handler(CommandHandler(["gwelcome", "setgwelcome"], make_setter("welcome_group"), filters=pm))
    app.add_handler(CommandHandler("setabout", make_setter("about"), filters=pm))
    app.add_handler(CommandHandler("setcourses", make_setter("courses"), filters=pm))
    app.add_handler(CommandHandler("setcontact", make_setter("contact"), filters=pm))
    app.add_handler(CommandHandler("setrules", make_setter("rules"), filters=pm))
    app.add_handler(CommandHandler(["rmsg", "setrmsg"], make_setter("rmsg"), filters=pm))
    app.add_handler(CommandHandler("setlinkmsg", make_setter("link_msg"), filters=pm))
    app.add_handler(CommandHandler("setbadmsg", make_setter("badword_msg"), filters=pm))
    app.add_handler(CommandHandler("setverifymsg", make_setter("verify_msg"), filters=pm))
    app.add_handler(CommandHandler("setfloodmsg", make_setter("flood_msg"), filters=pm))
