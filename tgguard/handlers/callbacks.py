"""The callback-query router: every coloured button lands here.

Pure screens are rendered via render.render_cb(); this module implements the
ACTION buttons (toggles, deletes, report punishments, verify, flows...).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler

from ..keyboards import BLUE, GREEN, RED, cbtn, kboard
from ..state import new_id
from ..textfmt import PLACEHOLDER_HELP, truncate, user_full_name
from ..timeutil import humanize_seconds, parse_clock, parse_duration
from . import screens as S
from .common import (
    CTX,
    ALL_OFF,
    clear_flow,
    edit_screen,
    flow,
    get_db,
    get_state,
    is_group_admin,
    is_owner,
    media_info,
    show_screen,
    start_flow,
    unmute_user,
)
from .engine import (
    drop_schedule_job,
    ensure_schedule_job,
    exec_report_action,
)
from .pm_owner import apply_set, can_edit_path, run_broadcast
from .render import render_cb

log = logging.getLogger("tgguard.callbacks")

OWNER_TAGS = {
    "om", "us", "bu", "up", "um", "ub", "ms", "st", "sm", "plm", "ple", "pla",
    "pld", "pld2", "bk", "rstr", "bc", "bcgo", "bccancel", "rp", "rpb", "tze",
}
GROUP_TAGS = {
    "gp", "gw", "gf", "gb", "gl", "gs", "gv", "gfl", "gwa", "gru", "gsx",
    "gfa", "gfd", "gba", "gbd", "gsa", "gsr", "gst", "gsd", "gwn", "gwt", "glk",
}


async def _gid_of(parts) -> int | None:
    try:
        return int(parts[1])
    except (ValueError, IndexError):
        return None


async def on_callback(update: Update, context: CTX) -> None:
    cb = update.callback_query
    if not cb or not cb.data:
        return
    data = cb.data
    parts = data.split(":")
    tag = parts[0]
    user = cb.from_user
    state = get_state(context)
    db = get_db(context)
    chat_id = cb.message.chat_id if cb.message else user.id
    mid = cb.message.message_id if cb.message else 0

    # ---------------- tiny actions ---------------- #
    if tag == "cl":
        await cb.answer()
        try:
            await cb.message.delete()
        except TelegramError:
            pass
        return
    if tag == "noop":
        await cb.answer()
        return

    # ---------------- verify button (any member, in group) ---------------- #
    if tag == "ver":
        gid = await _gid_of(parts)
        try:
            target_uid = int(parts[2])
        except (ValueError, IndexError):
            target_uid = -1
        if user.id != target_uid:
            await cb.answer("🧪 This verification button belongs to another user.", show_alert=True)
            return
        await cb.answer("✅ Verified! Welcome.")
        context.bot_data.setdefault("verok", {})[f"{gid}:{user.id}"] = 1
        await unmute_user(context, gid, user.id)
        g = state.get_group(gid)
        if g:
            state.bump_group(gid, "verified")
            state.changed()
        try:
            await cb.edit_message_text(
                (cb.message.text or "") + f"\n\n✅ {user_full_name(user)} verified — welcome to the group! 🎉",
                reply_markup=None,
            )
        except TelegramError:
            pass
        if context.job_queue:
            context.job_queue.run_once(_del_msg_job, 120, data={"chat": gid, "mid": mid})
        return

    # ---------------- authorization ---------------- #
    allowed = True
    if tag in OWNER_TAGS:
        allowed = is_owner(user.id)
    elif tag in GROUP_TAGS:
        gid = await _gid_of(parts)
        allowed = gid is not None and await is_group_admin(context, gid, user.id)
    elif tag in ("ed", "rs", "gt", "sv"):
        allowed = await can_edit_path(context, user.id, ":".join(parts[1:]))
    if not allowed:
        await cb.answer("🔒 You are not allowed to use this button.", show_alert=True)
        return

    # ---------------- edit / reset / toggle ---------------- #
    if tag == "ed":
        path = ":".join(parts[1:])
        label = S.label_for(state, path)
        await cb.answer("📝 Now send the new text here…")
        start_flow(
            context,
            "setpath",
            f"✏️ Editing {label}",
            path=path,
            src=(chat_id, mid),
        )
        await context.bot.send_message(
            chat_id,
            f"✏️ Editing: {label}\n\nSend the NEW text now (/cancel to abort).\n💡 placeholders: {PLACEHOLDER_HELP}",
        )
        return

    if tag == "rs":
        path = ":".join(parts[1:])
        await apply_set(context, path, S.default_for(path), actor_name=user.full_name, src=(chat_id, mid), silent=True)
        await cb.answer("↩️ Reset to default.")
        return

    if tag == "gt":
        path = ":".join(parts[1:])
        cur = state.get_path(path, False)
        await apply_set(context, path, not bool(cur), actor_name=user.full_name, src=(chat_id, mid), silent=True)
        await cb.answer("✅ ON" if not cur else "⛔ OFF")
        return

    # ---------------- owner: users ---------------- #
    if tag == "um":
        uid = int(parts[1])
        await cb.answer("✉️ Now send the message…")
        start_flow(context, "msg_user", f"✉️ Messaging user {uid}", uid=uid)
        await context.bot.send_message(chat_id, f"✉️ Send the message for user {uid} (text or media). /cancel to abort.")
        return

    if tag == "ub":
        uid = int(parts[1])
        rec = state.get_user(uid)
        now_blocked = bool(rec.get("blocked")) if rec else False
        state.mark_blocked(uid, not now_blocked)
        db.request_save()
        await cb.answer("♻️ Marked active" if now_blocked else "🚫 Marked blocked")
        await _rerender(context, cb, data, f"up:{uid}:{parts[2]}:{parts[3]}")
        return

    if tag == "ms":
        await cb.answer()
        start_flow(context, "msg_ask_uid", "✉️ Send the user ID")
        await context.bot.send_message(chat_id, "✉️ Send the USER ID you want to message.\n(Tip: 👥 Users → tap a user → ✉️ Send Message.)")
        return

    # ---------------- owner: broadcast ---------------- #
    if tag == "bc":
        await cb.answer()
        start_flow(context, "bc_content", "📢 Send the content to broadcast")
        await context.bot.send_message(chat_id, "📢 Send (or forward) the content to broadcast to all users — text, photo, video, sticker…\n/cancel to abort.")
        return

    if tag == "bcgo":
        content = context.user_data.pop("bc", None)
        if not content:
            await cb.answer("⚠️ Broadcast expired — start again with /broadcast.", show_alert=True)
            return
        n = len(state.users_sorted(blocked=False))
        await cb.answer("📢 Starting broadcast…")
        try:
            await cb.edit_message_text(f"📢 Broadcasting… 0/{n}")
        except TelegramError:
            pass
        context.application.create_task(run_broadcast(context, chat_id, mid, content))
        return

    if tag == "bccancel":
        context.user_data.pop("bc", None)
        clear_flow(context)
        await cb.answer("❌ Cancelled")
        try:
            await cb.edit_message_text("❌ Broadcast cancelled.")
        except TelegramError:
            pass
        return

    # ---------------- owner: backup / restore ---------------- #
    if tag == "bk":
        await cb.answer("💾 Preparing backup…")
        from io import BytesIO

        from ..timeutil import now_ts

        blob = state.to_json()
        await context.bot.send_document(
            chat_id,
            document=BytesIO(blob),
            filename=f"tgguard_backup_{now_ts()}.json",
            caption=f"💾 Backup — {len(state.users)} users, {len(state.active_groups())} groups, snapshot v{state.data.get('seq', 0)}.",
        )
        return

    if tag == "rstr":
        await cb.answer()
        start_flow(context, "restore_wait", "Send the backup .json file")
        await context.bot.send_message(chat_id, "🔄 Send me the backup .json file to restore.\n⚠️ This OVERWRITES the current database.")
        return

    # ---------------- owner: plans ---------------- #
    if tag == "pla":
        await cb.answer()
        start_flow(context, "plan_name", "➕ NEW PLAN (1/3) — send the NAME")
        await context.bot.send_message(chat_id, "➕ NEW PLAN (1/3)\nSend the plan/course NAME.\nExample: 🎙️ Voice Basics")
        return

    if tag == "pld":
        pid = parts[1]
        p = state.get_plan(pid)
        await cb.answer()
        await edit_screen(
            context,
            chat_id,
            mid,
            (
                f"🗑 Delete plan “{truncate((p or {}).get('name', pid), 30)}”?\nThis cannot be undone.",
                None,
                kboard([[cbtn("🗑 YES — Delete", f"pld2:{pid}", color=RED), cbtn("🔙 No, keep it", "plm", color=GREEN)]]),
            ),
        )
        return

    if tag == "pld2":
        pid = parts[1]
        p = state.get_plan(pid)
        if state.del_plan(pid):
            await db.audit_setting(f"settings.plans[{pid}]", user.full_name, f"DELETED plan {(p or {}).get('name', pid)}")
            db.request_save()
            await cb.answer("🗑 Plan deleted")
        else:
            await cb.answer("🤷 Plan not found")
        await _rerender(context, cb, data, "plm")
        return

    # ---------------- owner: timezone ---------------- #
    if tag == "tze":
        await cb.answer()
        start_flow(context, "tz_set", "Send a timezone name, e.g. Asia/Kolkata")
        await context.bot.send_message(chat_id, "🕘 Send a timezone name:\nAsia/Kolkata • Asia/Dubai • Europe/London • UTC …")
        return

    # ---------------- reports ---------------- #
    if tag == "rp":
        rid, act = parts[1], parts[2]
        if act in ("ban", "mute") and len(parts) < 4:
            await cb.answer()
            await edit_screen(context, chat_id, mid, S.scr_report_durations(state, rid, act))
            return
        secs = int(parts[3]) if len(parts) > 3 else 0
        await cb.answer("⏳ Working…")
        result = await exec_report_action(context, rid, act, secs)
        try:
            await cb.edit_message_text(result)
        except TelegramError:
            await context.bot.send_message(chat_id, result)
        return

    # ---------------- group: filters ---------------- #
    if tag == "gfa":
        gid = await _gid_of(parts)
        await cb.answer()
        start_flow(context, "f_word", "🎯 Send the keyword", gid=gid)
        await context.bot.send_message(chat_id, "🎯 NEW FILTER (1/2)\nSend the KEYWORD that triggers the bot.\nExample: hello")
        return

    if tag == "gfd":
        gid, idx = await _gid_of(parts), int(parts[2])
        g = state.get_group(gid)
        if g and 0 <= idx < len(g["filters"]):
            f = g["filters"].pop(idx)
            state.changed()
            await db.audit_setting(f"groups.{gid}.filters", user.full_name, f"REMOVED filter “{f.get('word')}”")
            db.request_save()
            await cb.answer("🗑 Filter removed")
        else:
            await cb.answer("🤷 Not found")
        await _rerender(context, cb, data, f"gf:{gid}")
        return

    # ---------------- group: bad words ---------------- #
    if tag == "gba":
        gid = await _gid_of(parts)
        await cb.answer()
        start_flow(context, "b_word", "🤬 Send: word [duration]", gid=gid)
        await context.bot.send_message(
            chat_id,
            "🤬 NEW BAD WORD\nSend the word, optionally with a mute duration:\n• fuck\n• spam 2h\n• idiot 30m\n(default mute: 60 min)",
        )
        return

    if tag == "gbd":
        gid, idx = await _gid_of(parts), int(parts[2])
        g = state.get_group(gid)
        if g and 0 <= idx < len(g["badwords"]):
            b = g["badwords"].pop(idx)
            state.changed()
            await db.audit_setting(f"groups.{gid}.badwords", user.full_name, f"REMOVED bad word “{b.get('word')}”")
            db.request_save()
            await cb.answer("🗑 Removed")
        else:
            await cb.answer("🤷 Not found")
        await _rerender(context, cb, data, f"gb:{gid}")
        return

    # ---------------- group: schedules ---------------- #
    if tag == "gsa":
        gid = await _gid_of(parts)
        await cb.answer()
        start_flow(context, "s_time", "⏰ Send the time", gid=gid)
        await context.bot.send_message(chat_id, f"📅 NEW SCHEDULE (1/2)\nSend the TIME (tz: {state.tz}).\nExamples: 8:00 PM • 20:00 • 9am")
        return

    if tag == "gsr":
        gid = await _gid_of(parts)
        hhmm = f"{parts[2]}:{parts[3]}"
        repeat = parts[4] if len(parts) > 4 else "daily"
        await cb.answer()
        start_flow(context, "s_content", "📅 Send the content", gid=gid, hhmm=hhmm, repeat=repeat)
        await context.bot.send_message(
            chat_id,
            f"📅 NEW SCHEDULE (2/2) — ⏰ {hhmm} • {repeat}\n"
            "Send the TEXT to post (or forward media: photo/sticker/GIF).\n"
            f"💡 placeholders: {{group}} {{count}} {{date}} {{time}}",
        )
        return

    if tag == "gst":
        gid, sid = await _gid_of(parts), parts[2]
        g = state.get_group(gid)
        s = next((x for x in (g or {}).get("schedules", []) if x.get("id") == sid), None)
        if s:
            s["on"] = not s.get("on", True)
            state.changed()
            db.request_save()
            if s["on"]:
                await ensure_schedule_job(context, gid, sid)
            else:
                drop_schedule_job(context, gid, sid)
            await cb.answer("▶️ Armed" if s["on"] else "⏸ Paused")
        else:
            await cb.answer("🤷 Not found")
        await _rerender(context, cb, data, f"gs:{gid}")
        return

    if tag == "gsd":
        gid, sid = await _gid_of(parts), parts[2]
        g = state.get_group(gid)
        if g:
            before = len(g["schedules"])
            g["schedules"] = [x for x in g["schedules"] if x.get("id") != sid]
            if len(g["schedules"]) != before:
                state.changed()
                db.request_save()
                drop_schedule_job(context, gid, sid)
                await cb.answer("🗑 Schedule removed")
            else:
                await cb.answer("🤷 Not found")
        await _rerender(context, cb, data, f"gs:{gid}")
        return

    # ---------------- group: warns config ---------------- #
    if tag == "gwn":
        gid, delta = await _gid_of(parts), int(parts[2])
        g = state.get_group(gid)
        if g:
            g["warns"]["limit"] = max(1, min(20, int(g["warns"].get("limit", 3)) + delta))
            state.changed()
            db.request_save()
        await cb.answer()
        await _rerender(context, cb, data, f"gwa:{gid}")
        return

    if tag == "gwt":
        gid = await _gid_of(parts)
        g = state.get_group(gid)
        if g:
            g["warns"]["action"] = "ban" if g["warns"].get("action") == "mute" else "mute"
            state.changed()
            db.request_save()
            await db.audit(f"⚠️ Warn action = {g['warns']['action'].upper()} in {g.get('title')} ({gid})")
        await cb.answer()
        await _rerender(context, cb, data, f"gwa:{gid}")
        return

    # ---------------- group: lock ---------------- #
    if tag == "glk":
        gid = await _gid_of(parts)
        g, _ = state.ensure_group(gid)
        if not g.get("locked"):
            try:
                chat_full = await context.bot.get_chat(gid)
                if chat_full.permissions:
                    g["lock_saved"] = chat_full.permissions.to_dict()
                await context.bot.set_chat_permissions(gid, ALL_OFF)
                g["locked"] = True
                state.changed()
                db.request_save()
                await db.audit(f"🔒 Group LOCKED via panel: {g.get('title')} ({gid})")
                await cb.answer("🔒 Locked")
            except TelegramError as e:
                await cb.answer(f"⚠️ {e}", show_alert=True)
        else:
            from telegram import ChatPermissions

            from .common import ALL_ON, PERM_FIELDS

            saved = g.get("lock_saved") or {}
            perms = ChatPermissions(**{k: v for k, v in saved.items() if k in PERM_FIELDS}) if saved else ALL_ON
            try:
                await context.bot.set_chat_permissions(gid, perms)
                g["locked"] = False
                g["lock_saved"] = None
                state.changed()
                db.request_save()
                await db.audit(f"🔓 Group UNLOCKED via panel: {g.get('title')} ({gid})")
                await cb.answer("🔓 Unlocked")
            except TelegramError as e:
                await cb.answer(f"⚠️ {e}", show_alert=True)
        await _rerender(context, cb, data, f"gp:{gid}")
        return

    # ---------------- default: render the screen ---------------- #
    await cb.answer()
    screen = await render_cb(data, context, user=user)
    if screen:
        await edit_screen(context, chat_id, mid, screen)
        if tag == "sv":
            path = ":".join(parts[1:])
            state.register_editable(chat_id, mid, path, S.label_for(state, path))
            db.request_save()
    else:
        try:
            await cb.edit_message_text("⚠️ This menu is outdated — please reopen it (/panel or /start).")
        except TelegramError:
            pass


async def _rerender(context: CTX, cb, orig_data: str, new_data: str) -> None:
    user = cb.from_user
    screen = await render_cb(new_data, context, user=user)
    if screen and cb.message:
        await edit_screen(context, cb.message.chat_id, cb.message.message_id, screen)


async def _del_msg_job(context: CTX) -> None:
    d = context.job.data
    try:
        await context.bot.delete_message(d["chat"], d["mid"])
    except TelegramError:
        pass


# ---------------------------------------------------------------------- #
# flows started from buttons
# ---------------------------------------------------------------------- #
@flow("f_word")
async def flow_f_word(fl: dict, update: Update, context: CTX) -> None:
    word = (update.effective_message.text or "").strip().lower()
    if len(word) < 2:
        await update.effective_message.reply_text("⚠️ Keyword must be at least 2 characters.")
        return
    fl["act"] = "f_reply"
    fl["word"] = word[:64]
    await update.effective_message.reply_text(
        f"🎯 Filter for “{word}” (2/2)\nNow send the REPLY the bot should send:\n"
        "• a TEXT message, or\n• a STICKER / PHOTO / GIF / VIDEO (forward or send it here)"
    )


@flow("f_reply")
async def flow_f_reply(fl: dict, update: Update, context: CTX) -> None:
    msg = update.effective_message
    state = get_state(context)
    db = get_db(context)
    gid = fl["gid"]
    g, _ = state.ensure_group(gid)
    entry = {"id": new_id("f"), "word": fl["word"]}
    m = media_info(msg)
    if m:
        db_mid = await db.store_media(msg)
        entry.update({"kind": "media", "mtype": m["kind"], "file_id": m["file_id"], "cap": m.get("cap", ""), "db_mid": db_mid})
    elif msg.text:
        entry.update({"kind": "text", "content": msg.text[:2000]})
    else:
        await msg.reply_text("⚠️ Send a text or a media file, or /cancel.")
        return
    g["filters"] = [f for f in g["filters"] if f.get("word") != entry["word"]]
    g["filters"].append(entry)
    state.changed()
    await db.audit_setting(f"groups.{gid}.filters[{entry['id']}]", update.effective_user.full_name, f"FILTER “{entry['word']}” → {entry['kind']}")
    db.request_save()
    clear_flow(context)
    await msg.reply_text(f"✅ Filter “{entry['word']}” is live!")
    await show_screen(context, msg.chat_id, S.scr_filters(state, gid))


@flow("b_word")
async def flow_b_word(fl: dict, update: Update, context: CTX) -> None:
    txt = (update.effective_message.text or "").strip()
    if not txt:
        await update.effective_message.reply_text("⚠️ Send: word [duration]. Example: spam 2h")
        return
    bits = txt.split(None, 1)
    word = bits[0].lower()
    if len(word) < 2:
        await update.effective_message.reply_text("⚠️ Word must be at least 2 characters.")
        return
    secs = parse_duration(bits[1]) if len(bits) > 1 else None
    minutes = max(1, (secs or 3600) // 60)
    state = get_state(context)
    db = get_db(context)
    gid = fl["gid"]
    g, _ = state.ensure_group(gid)
    g["badwords"] = [b for b in g["badwords"] if b.get("word") != word]
    entry = {"id": new_id("b"), "word": word, "mute_min": minutes}
    g["badwords"].append(entry)
    state.changed()
    await db.audit_setting(f"groups.{gid}.badwords[{entry['id']}]", update.effective_user.full_name, f"BADWORD “{word}” → mute {minutes}m")
    db.request_save()
    clear_flow(context)
    await update.effective_message.reply_text(f"✅ Bad word “{word}” → 🔇 {humanize_seconds(minutes * 60)} mute + delete.")
    await show_screen(context, update.effective_message.chat_id, S.scr_badwords(state, gid))


@flow("s_time")
async def flow_s_time(fl: dict, update: Update, context: CTX) -> None:
    txt = (update.effective_message.text or "").strip()
    parsed = parse_clock(txt)
    if not parsed:
        await update.effective_message.reply_text("⚠️ Could not read that time. Examples: 8:00 PM • 20:00 • 9am")
        return
    hh, mm = parsed
    gid = fl["gid"]
    clear_flow(context)
    await update.effective_message.reply_text(
        f"⏰ Time: {hh:02d}:{mm:02d} ({get_state(context).tz})\nHow often?",
        reply_markup=kboard(
            [
                [cbtn("🔁 Daily", f"gsr:{gid}:{hh:02d}:{mm:02d}", color=GREEN), cbtn("1️⃣ Only once", f"gsr:{gid}:{hh:02d}:{mm:02d}:once", color=BLUE)],
                [cbtn("❌ Cancel", "cl", color=RED)],
            ]
        ),
    )


@flow("s_content")
async def flow_s_content(fl: dict, update: Update, context: CTX) -> None:
    msg = update.effective_message
    state = get_state(context)
    db = get_db(context)
    gid, hhmm, repeat = fl["gid"], fl["hhmm"], fl["repeat"]
    entry = {"id": new_id("s"), "time": hhmm, "repeat": repeat, "on": True}
    m = media_info(msg)
    if m:
        from ..timeutil import now_ts

        db_mid = await db.store_media(msg)
        entry["m"] = {"kind": m["kind"], "file_id": m["file_id"], "cap": m.get("cap", ""), "db_mid": db_mid}
        entry["text"] = ""
        entry["created"] = now_ts()
    elif msg.text:
        from ..timeutil import now_ts

        entry["text"] = msg.text[:3500]
        entry["created"] = now_ts()
    else:
        await msg.reply_text("⚠️ Send text or media, or /cancel.")
        return
    g, _ = state.ensure_group(gid)
    g["schedules"].append(entry)
    state.changed()
    db.request_save()
    await ensure_schedule_job(context, gid, entry["id"])
    clear_flow(context)
    await msg.reply_text(f"✅ Scheduled — ⏰ {hhmm} ({state.tz}) • {repeat}.")
    await show_screen(context, msg.chat_id, S.scr_schedules(state, gid))


# ---------------------------------------------------------------------- #
def register(app) -> None:
    app.add_handler(CallbackQueryHandler(on_callback))
