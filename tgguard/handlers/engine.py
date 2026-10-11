"""The group runtime engine:

* guard pipeline  — link guard, bad words, anti-flood, keyword filters
* welcome + join verification (captcha)
* /report  -> owner PM with Ban/Mute/Delete/Dismiss coloured buttons
* warn system
* scheduled posts (job queue, timezone aware, self re-arming)
* my_chat_member — bot added/removed from groups, users blocking the bot
"""

from __future__ import annotations

import datetime as dtm
import logging
import time
from collections import deque

from telegram import ChatMemberUpdated, Update
from telegram.error import TelegramError
from telegram.ext import ChatMemberHandler, CommandHandler, MessageHandler, filters

from .. import config
from ..keyboards import GREEN, add_to_group_url, cbtn, kboard
from ..textfmt import format_placeholders, has_link, truncate, user_full_name
from ..timeutil import humanize_seconds, next_fire
from .common import (
    CTX,
    ALL_OFF,
    dm_user,
    get_db,
    get_state,
    group_admin_ids,
    is_owner,
    media_info,
    mute_user,
    send_media,
    send_text,
)
from .render import bot_username

log = logging.getLogger("tgguard.engine")


# ---------------------------------------------------------------------- #
# small utils
# ---------------------------------------------------------------------- #
def _thr(context: CTX, key, seconds: int) -> bool:
    """Simple per-key throttle. True when allowed (and records the hit)."""
    store = context.bot_data.setdefault("throttle", {})
    now = time.time()
    last = store.get(key, 0)
    if now - last < seconds:
        return False
    store[key] = now
    return True


async def bot_rights(context: CTX, gid: int) -> dict:
    try:
        mem = await context.bot.get_chat_member(gid, (await context.bot.get_me()).id)
    except TelegramError:
        return {"admin": False}
    return {
        "admin": mem.status in ("administrator", "creator"),
        "delete": bool(getattr(mem, "can_delete_messages", False)),
        "restrict": bool(getattr(mem, "can_restrict_members", False)),
        "pin": bool(getattr(mem, "can_pin_messages", False)),
    }


# ---------------------------------------------------------------------- #
# WELCOME + VERIFY
# ---------------------------------------------------------------------- #
async def do_welcome(context: CTX, chat, user) -> None:
    state = get_state(context)
    db = get_db(context)
    gid = chat.id
    key = (gid, user.id)
    dedup = context.bot_data.setdefault("wdedup", {})
    if time.time() - dedup.get(key, 0) < 60:
        return
    dedup[key] = time.time()

    g, _ = state.ensure_group(gid, getattr(chat, "title", None))
    state.bump("welcomes")
    state.bump_group(gid, "welcomes")
    state.bump_group(gid, "joins")
    state.changed()
    await db.audit(
        f"👋 JOINED GROUP: {user_full_name(user)}"
        + (f" (@{user.username})" if user.username else "")
        + f" — ID {user.id}\n    → {g.get('title') or gid}"
    )

    verify_on = bool(g["verify"]["on"])
    welcome_on = bool(g["welcome"]["on"])
    if not welcome_on and not verify_on:
        return

    members = int(g.get("members") or 0) or None
    tpl = g["welcome"].get("text") or state.setting("welcome_group")
    text, ents = format_placeholders(
        tpl, user=user, chat_title=chat.title, count=members, bot_name=bot_username(context), tz=state.tz
    )
    markup = None
    if verify_on:
        vtpl = g["verify"].get("msg") or state.setting("verify_msg")
        vtext, vents = format_placeholders(vtpl, user=user, chat_title=chat.title, tz=state.tz)
        if welcome_on:
            from ..textfmt import u16len

            prefix = text + "\n\n"
            text = prefix + vtext
            ents = ents + shift_entities(vents, u16len(prefix))
        else:
            text, ents = vtext, vents
        markup = kboard([[cbtn("✅ Verify — I'm human", f"ver:{gid}:{user.id}", color=GREEN)]])
        # hold the member until they verify
        timeout_min = int(g["verify"].get("timeout_min", 10) or 10)
        until = dtm.datetime.now(dtm.timezone.utc) + dtm.timedelta(minutes=timeout_min)
        try:
            await context.bot.restrict_chat_member(gid, user.id, permissions=ALL_OFF, until_date=until)
        except TelegramError as e:
            log.warning("verify restrict failed: %s", e)
        if context.job_queue:
            context.job_queue.run_once(
                kick_unverified,
                when=until + dtm.timedelta(seconds=5),
                data={"gid": gid, "uid": user.id, "name": user_full_name(user)},
                name=f"vk:{gid}:{user.id}",
            )

    sent = await send_text(context, gid, text, ents, markup)
    if sent and g["welcome"].get("pin") and welcome_on:
        old = g["welcome"].get("pinned_mid")
        try:
            if old:
                await context.bot.unpin_chat_message(gid, message_id=old)
            await context.bot.pin_chat_message(gid, sent.message_id, disable_notification=True)
            g["welcome"]["pinned_mid"] = sent.message_id
            state.changed()
        except TelegramError as e:
            log.warning("pin welcome failed: %s", e)


def shift_entities(entities, text_offset: int):
    from telegram import MessageEntity

    out = []
    for e in entities or []:
        out.append(MessageEntity(e.type, e.offset + text_offset, e.length, user=e.user, url=e.url))
    return out


async def kick_unverified(context: CTX) -> None:
    data = context.job.data
    gid, uid = data["gid"], data["uid"]
    verok = context.bot_data.setdefault("verok", {})
    if verok.pop(f"{gid}:{uid}", None):
        return
    state = get_state(context)
    try:
        await context.bot.ban_chat_member(gid, uid)
        await context.bot.unban_chat_member(gid, uid)
        state.bump_group(gid, "bans")
        state.changed()
        await get_db(context).audit(f"🧪 Kicked UNVERIFIED member: {data.get('name')} ({uid}) from group {gid}")
    except TelegramError as e:
        log.info("kick_unverified(%s) skipped: %s", uid, e)


async def on_chat_member(update: Update, context: CTX) -> None:
    """User joins/leaves (chat_member updates — requires bot to be admin)."""
    cm: ChatMemberUpdated = update.chat_member
    chat = cm.chat
    if chat.type not in ("group", "supergroup"):
        return
    user = cm.new_chat_member.user
    if user.is_bot:
        return
    old, new = cm.old_chat_member.status, cm.new_chat_member.status
    if old in ("left", "kicked") and new in ("member", "restricted", "administrator"):
        await do_welcome(context, chat, user)


async def on_service_join(update: Update, context: CTX) -> None:
    """Fallback welcome via service messages (basic groups / old clients)."""
    msg = update.effective_message
    for m in msg.new_chat_members or []:
        if not m.is_bot:
            await do_welcome(context, msg.chat, m)


# ---------------------------------------------------------------------- #
# GUARD PIPELINE (link guard, bad words, flood, filters)
# ---------------------------------------------------------------------- #
async def guard_pipeline(update: Update, context: CTX) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or chat.type not in ("group", "supergroup"):
        return
    if msg.sender_chat:  # anonymous admins / linked channel posts
        return
    state = get_state(context)
    g, _ = state.ensure_group(chat.id, chat.title)
    state.bump("msgs_seen")
    state.bump_group(chat.id, "msgs")
    state.changed()
    if not user or user.is_bot:
        return

    text = msg.text or msg.caption or ""
    admins = await group_admin_ids(context, chat.id)
    is_admin = user.id in admins

    # 1) LINK GUARD --------------------------------------------------- #
    if (
        not is_admin
        and g["linkguard"]["on"]
        and has_link(text, list(msg.entities or []) + list(msg.caption_entities or []))
    ):
        await _delete(context, chat.id, msg)
        if _thr(context, ("lg", chat.id, user.id), 15):
            tpl = g["linkguard"].get("msg") or state.setting("link_msg")
            t, e = format_placeholders(tpl, user=user, chat_title=chat.title, tz=state.tz)
            await send_text(context, chat.id, t, e)
        return

    # 2) BAD WORDS ----------------------------------------------------- #
    if not is_admin and g["badwords"] and text:
        low = text.lower()
        hit = next((b for b in g["badwords"] if b.get("word") and b["word"] in low), None)
        if hit:
            await _delete(context, chat.id, msg)
            minutes = int(hit.get("mute_min", 60) or 60)
            if await mute_user(context, chat.id, user.id, minutes * 60):
                state.bump("mutes")
                state.bump_group(chat.id, "mutes")
                state.changed()
                await get_db(context).audit(
                    f"🔇 AUTO-MUTE {user_full_name(user)} ({user.id}) for {minutes}m in "
                    f"{chat.title} ({chat.id}) — bad word “{hit['word']}”"
                )
            tpl = state.setting("badword_msg")
            t, e = format_placeholders(tpl, user=user, chat_title=chat.title, duration=humanize_seconds(minutes * 60), tz=state.tz)
            await send_text(context, chat.id, t, e)
            return

    # 3) ANTI-FLOOD ---------------------------------------------------- #
    if not is_admin and g["flood"]["on"]:
        fl = context.bot_data.setdefault("flood", {})
        dq = fl.setdefault((chat.id, user.id), deque())
        now = time.time()
        dq.append(now)
        window = int(g["flood"].get("window", 10) or 10)
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) > int(g["flood"].get("limit", 7) or 7):
            dq.clear()
            await _delete(context, chat.id, msg)
            minutes = int(g["flood"].get("mute_min", 5) or 5)
            if await mute_user(context, chat.id, user.id, minutes * 60):
                state.bump("mutes")
                state.bump_group(chat.id, "mutes")
                state.changed()
                await get_db(context).audit(f"🌊 AUTO-MUTE (flood) {user_full_name(user)} ({user.id}) for {minutes}m in {chat.title}")
            tpl = state.setting("flood_msg")
            t, e = format_placeholders(tpl, user=user, duration=humanize_seconds(minutes * 60), tz=state.tz)
            await send_text(context, chat.id, t, e)
            return

    # 4) KEYWORD FILTERS (for everyone, admins included) --------------- #
    if g["filters"] and text:
        low = text.lower()
        for f in g["filters"]:
            word = f.get("word")
            if not word or word not in low:
                continue
            if not _thr(context, ("flt", chat.id, user.id, word), 20):
                break
            state.bump("filters_hit")
            state.changed()
            if f.get("kind") == "text":
                t, e = format_placeholders(f.get("content", ""), user=user, chat_title=chat.title, tz=state.tz)
                await send_text(context, chat.id, t, e, reply_to=msg.message_id)
            else:
                await send_media(
                    context,
                    chat.id,
                    {"kind": f.get("mtype"), "file_id": f.get("file_id"), "cap": f.get("cap", ""), "db_mid": f.get("db_mid")},
                    reply_to=msg.message_id,
                )
            break


async def _delete(context: CTX, gid: int, msg) -> None:
    try:
        await msg.delete()
        state = get_state(context)
        state.bump("deletes")
        state.bump_group(gid, "deletes")
        state.changed()
    except TelegramError as e:
        if _thr(context, ("delerr", gid), 300):
            log.warning("delete failed in %s: %s (need can_delete_messages)", gid, e)


# ---------------------------------------------------------------------- #
# REPORTS
# ---------------------------------------------------------------------- #
async def cmd_report(update: Update, context: CTX) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    state = get_state(context)
    db = get_db(context)

    # PM /report = direct line to the owner (feedback)
    if chat.type == "private":
        text = msg.text.split(None, 1)[1] if " " in (msg.text or "") else ""
        replied = msg.reply_to_message
        if not text and not replied:
            await msg.reply_text("Send your message like:  /report <your message>\n(In a group, reply to a message with /report to report it.)")
            return
        await dm_user(
            context,
            config.CFG.owner_id,
            f"📩 PM REPORT/FEEDBACK from {user_full_name(user)} (@{user.username or '—'} • {user.id}):\n{text}",
        )
        if replied:
            try:
                await replied.forward(config.CFG.owner_id)
            except TelegramError:
                pass
        await msg.reply_text("✅ Your message was sent to the admin.")
        return

    if chat.type not in ("group", "supergroup"):
        return

    replied = msg.reply_to_message
    reason = msg.text.split(None, 1)[1] if " " in (msg.text or "") else ""
    target = replied.from_user if replied and replied.from_user else None

    if target and target.is_bot:
        await msg.reply_text("🤖 Bots cannot be reported.")
        return

    # Reporting an ADMIN? -> never forwarded; reply with /rmsg instead.
    if target and (is_owner(target.id) or target.id in await group_admin_ids(context, chat.id)):
        rmsg = state.setting("rmsg") or ""
        if rmsg.strip():
            t, e = format_placeholders(rmsg, user=user, chat_title=chat.title, tz=state.tz)
            await send_text(context, chat.id, t, e, reply_to=msg.message_id)
        else:
            await msg.reply_text("⚠️ Admins cannot be reported. Your report was NOT sent.")
        return

    if not target and not reason.strip():
        await msg.reply_text(
            "🚨 How to report:\n"
            "• REPLY to the offending message with /report [reason]\n"
            "• or /report <reason> without a reply"
        )
        return

    g, _ = state.ensure_group(chat.id, chat.title)
    rid = state.add_report(
        {
            "gid": chat.id,
            "gtitle": chat.title or str(chat.id),
            "reporter": user_full_name(user),
            "reporter_id": user.id,
            "target": user_full_name(target) if target else "",
            "target_id": target.id if target else 0,
            "mid": replied.message_id if replied else 0,
            "reason": reason.strip()[:500],
            "snippet": truncate(replied.text or replied.caption or ("[media]" if media_info(replied) else "") or "", 300) if replied else "",
        }
    )
    state.bump("reports")
    state.bump_group(chat.id, "reports")
    db.request_save()

    # forward the offending message to the owner (best effort)
    fwd_note = ""
    if replied:
        try:
            await replied.forward(config.CFG.owner_id)
        except TelegramError:
            fwd_note = "\n⚠️ (original message could not be forwarded — protected content)"

    from . import screens as S

    screen = S.scr_report(state, rid)
    header = screen[0] + fwd_note
    ok = await dm_user(context, config.CFG.owner_id, header, reply_markup=screen[2])
    if not ok:
        await msg.reply_text("⚠️ Could not reach the admin right now.")
        state.del_report(rid)
        return
    t, e = format_placeholders(
        "✅ {mention}, your report was sent to the admin for review. Thank you for keeping the group clean! 🛡",
        user=user,
        chat_title=chat.title,
        tz=state.tz,
    )
    await send_text(context, chat.id, t, e, reply_to=msg.message_id)
    await db.audit(
        f"🚨 REPORT #{rid}: {user_full_name(user)} ({user.id}) reported "
        f"{(user_full_name(target) + f' ({target.id})') if target else 'the group'} in {chat.title} — {reason or '(no reason)'}"
    )


async def exec_report_action(context: CTX, rid: str, act: str, secs: int) -> str:
    """Executes Ban/Mute/Delete/Dismiss; returns a human summary."""
    state = get_state(context)
    db = get_db(context)
    r = state.get_report(rid)
    if not r:
        return "🤷 Report expired or already handled."
    gid, target_id, mid = r["gid"], r.get("target_id"), r.get("mid")
    result = ""
    if act == "ban" and target_id:
        until = dtm.datetime.now(dtm.timezone.utc) + dtm.timedelta(seconds=secs) if secs else None
        try:
            await context.bot.ban_chat_member(gid, target_id, until_date=until, revoke_messages=True)
            state.bump("bans")
            state.bump_group(gid, "bans")
            result = f"🚫 BANNED {r.get('target') or target_id} " + (f"for {humanize_seconds(secs)}." if secs else "FOREVER.")
        except TelegramError as e:
            result = f"⚠️ Ban failed: {e}"
    elif act == "mute" and target_id:
        until = dtm.datetime.now(dtm.timezone.utc) + dtm.timedelta(seconds=secs or 3600)
        try:
            await context.bot.restrict_chat_member(gid, target_id, permissions=ALL_OFF, until_date=until)
            state.bump("mutes")
            state.bump_group(gid, "mutes")
            result = f"🔇 MUTED {r.get('target') or target_id} for {humanize_seconds(secs or 3600)}."
        except TelegramError as e:
            result = f"⚠️ Mute failed: {e}"
    elif act == "del" and mid:
        try:
            await context.bot.delete_message(gid, mid)
            state.bump("deletes")
            state.bump_group(gid, "deletes")
            result = "🗑 Reported message deleted."
        except TelegramError as e:
            result = f"⚠️ Delete failed: {e}"
    elif act == "no":
        result = "❌ Report dismissed."
    else:
        result = "🤷 Nothing to do (missing target/message)."
    state.del_report(rid)
    state.changed()
    db.request_save()
    if not result.startswith("⚠️"):
        await db.audit(f"🚨 Report #{rid} ({r.get('gtitle')}): {result}")
    return result


# ---------------------------------------------------------------------- #
# WARNS
# ---------------------------------------------------------------------- #
async def apply_warn(context: CTX, chat, target, reason: str, by=None) -> None:
    state = get_state(context)
    db = get_db(context)
    gid = chat.id
    if is_owner(target.id) or target.id in await group_admin_ids(context, gid):
        await send_text(context, gid, "⚠️ Admins cannot be warned.")
        return
    g, _ = state.ensure_group(gid, chat.title)
    counts = g["warns"].setdefault("counts", {})
    n = int(counts.get(str(target.id), 0)) + 1
    limit = int(g["warns"].get("limit", 3) or 3)
    state.bump("warns")
    state.bump_group(gid, "warns")
    state.changed()
    db.request_save()

    if n >= limit:
        action = g["warns"].get("action", "mute")
        counts[str(target.id)] = 0
        if action == "ban":
            try:
                await context.bot.ban_chat_member(gid, target.id, revoke_messages=True)
                state.bump("bans")
                state.bump_group(gid, "bans")
                text = f"🚫 {target.full_name} reached {limit}/{limit} warns → BANNED."
            except TelegramError as e:
                text = f"⚠️ Ban failed: {e}"
        else:
            minutes = int(g["warns"].get("mute_min", 60) or 60)
            if await mute_user(context, gid, target.id, minutes * 60):
                state.bump("mutes")
                state.bump_group(gid, "mutes")
                text = f"🔇 {target.full_name} reached {limit}/{limit} warns → muted {humanize_seconds(minutes * 60)}."
            else:
                text = f"⚠️ {target.full_name} reached {limit}/{limit} warns but mute failed (need restrict rights)."
        state.changed()
        await db.audit(f"⚠️ WARN-LIMIT: {text} Group: {chat.title} ({gid})")
    else:
        counts[str(target.id)] = n
        text = f"⚠️ {target.full_name} warned ({n}/{limit})." + (f" Reason: {reason}" if reason else "")
    await send_text(context, gid, text)


# ---------------------------------------------------------------------- #
# SCHEDULED POSTS
# ---------------------------------------------------------------------- #
def sched_job_name(gid: int, sid: str) -> str:
    return f"sc:{gid}:{sid}"


async def ensure_schedule_job(context: CTX, gid: int, sid: str) -> None:
    jq = context.job_queue
    if jq is None:
        return
    state = get_state(context)
    g = state.get_group(gid)
    if not g or g.get("left"):
        return
    s = next((x for x in g["schedules"] if x.get("id") == sid), None)
    if not s or not s.get("on", True):
        return
    name = sched_job_name(gid, sid)
    if jq.get_jobs_by_name(name):
        return
    fire = next_fire(s["time"], state.tz)
    if not fire:
        return
    jq.run_once(sched_fire, when=fire, data={"gid": gid, "sid": sid}, name=name)


def drop_schedule_job(context: CTX, gid: int, sid: str) -> None:
    jq = context.job_queue
    if jq is None:
        return
    for j in jq.get_jobs_by_name(sched_job_name(gid, sid)):
        j.schedule_removal()


async def rebuild_all_schedules(context: CTX) -> None:
    jq = context.job_queue
    if jq is None:
        return
    for j in list(jq.jobs()):
        if j.name and j.name.startswith("sc:"):
            j.schedule_removal()
    state = get_state(context)
    for g in state.groups.values():
        if g.get("left"):
            continue
        for s in g.get("schedules", []):
            await ensure_schedule_job(context, int(g["id"]), s["id"])


async def sched_fire(context: CTX) -> None:
    job = context.job
    gid, sid = job.data["gid"], job.data["sid"]
    state = get_state(context)
    g = state.get_group(gid)
    if not g or g.get("left"):
        return
    s = next((x for x in g["schedules"] if x.get("id") == sid), None)
    if not s:
        return
    if s.get("on", True):
        if s.get("m"):
            await send_media(context, gid, s["m"])
        else:
            t, e = format_placeholders(
                s.get("text", ""), chat_title=g.get("title"), count=g.get("members") or None,
                bot_name=bot_username(context), tz=state.tz,
            )
            await send_text(context, gid, t, e)
    if s.get("repeat", "daily") == "once":
        g["schedules"] = [x for x in g["schedules"] if x.get("id") != sid]
        state.changed()
        get_db(context).request_save()
    else:
        drop_schedule_job(context, gid, sid)
        await ensure_schedule_job(context, gid, sid)


# ---------------------------------------------------------------------- #
# BOT MEMBERSHIP (added to / removed from groups, blocked in PM)
# ---------------------------------------------------------------------- #
async def on_my_chat_member(update: Update, context: CTX) -> None:
    cm: ChatMemberUpdated = update.chat_member
    chat = cm.chat
    new, old = cm.new_chat_member.status, cm.old_chat_member.status
    state = get_state(context)
    db = get_db(context)
    by = cm.from_user

    # ---- PM: user blocked / unblocked the bot ------------------------ #
    if chat.type == "private":
        if new == "kicked" and old != "kicked":
            if by:
                rec, _ = state.touch_user(by.id, by.first_name, by.last_name or "", by.username or "")
            else:
                rec = state.get_user(chat.id) or {"id": chat.id, "first": "?"}
            state.mark_blocked(chat.id, True)
            await db.audit_blocked(rec)
            db.request_save()
        elif new in ("member", "administrator") and old == "kicked":
            state.mark_blocked(chat.id, False)
            rec = state.get_user(chat.id)
            if rec:
                await db.audit_unblocked(rec)
            db.request_save()
        return

    if chat.type not in ("group", "supergroup"):
        return

    # ---- bot added ---------------------------------------------------- #
    if old in ("left", "kicked") and new in ("member", "administrator"):
        g, created = state.ensure_group(chat.id, chat.title, by.id if by else None)
        db.request_save()
        await db.audit_group(g, by=(user_full_name(by) if by else ""))
        rights = await bot_rights(context, chat.id)
        if rights.get("admin") and rights.get("delete") and rights.get("restrict"):
            await send_text(
                context,
                chat.id,
                "🛡 TG-Guard is ACTIVE!\n\n"
                "Admins can now:\n"
                "• /gw — set the group welcome (with {mention})\n"
                "• /filter hello <reply> — keyword auto-replies (text/GIF/sticker)\n"
                "• /badword <word> [2h] — auto-mute on bad words\n"
                "• /linkguard on — delete links automatically\n"
                "• /schedule 8:00 PM <text> — timed posts\n"
                "• /panel — full coloured-button control panel in PM\n\n"
                "Everyone: /report (reply to a message) to report it to the admin. /rules",
            )
            await rebuild_all_schedules(context)
        else:
            missing = [k for k in ("delete", "restrict") if not rights.get(k)]
            await send_text(
                context,
                chat.id,
                "🛡 TG-Guard added — but I'm missing admin rights"
                + (f" ({', '.join(missing)})" if missing else "")
                + ".\n\nWithout them I cannot delete links/bad words or mute anyone.\n"
                "Please promote me, or use the button below to re-add me with the correct rights:",
                None,
                kboard([[cbtn("🛡 Add me as ADMIN (1 tap)", url=add_to_group_url(bot_username(context)), color=GREEN)]]),
            )
        return

    # ---- bot removed --------------------------------------------------- #
    if new in ("left", "kicked") and old in ("member", "administrator"):
        g = state.get_group(chat.id)
        if g:
            g["left"] = True
            state.changed()
            for s in g.get("schedules", []):
                drop_schedule_job(context, chat.id, s["id"])
        db.request_save()
        await db.audit_group(g or {"id": chat.id, "title": chat.title}, removed=True)


# ---------------------------------------------------------------------- #
def register(app) -> None:
    grp = filters.ChatType.GROUPS
    app.add_handler(CommandHandler("report", cmd_report))
    # chat type is checked inside the handlers (ChatMemberHandler takes no filters)
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS & grp, on_service_join))
    app.add_handler(MessageHandler(grp & ~filters.COMMAND & ~filters.StatusUpdate.ALL, guard_pipeline))
