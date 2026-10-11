"""In-group commands. Group admins (and the owner) manage the group from
inside the group itself — everything also exists as coloured buttons in the
PM panel (/panel)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, filters

from ..state import new_id
from ..textfmt import format_placeholders, truncate
from ..timeutil import fmt_ts, humanize_seconds, now_ts, parse_clock, parse_duration
from .common import (
    CTX,
    ALL_OFF,
    ALL_ON,
    PERM_FIELDS,
    dm_user,
    get_db,
    get_state,
    is_group_admin,
    media_info,
    send_text,
    show_screen,
)

log = logging.getLogger("tgguard.group_cmds")


async def admin_gate(update: Update, context: CTX) -> bool:
    """True when the sender may run admin commands in this chat."""
    chat = update.effective_chat
    user = update.effective_user
    if not chat or chat.type not in ("group", "supergroup") or not user:
        return False
    if await is_group_admin(context, chat.id, user.id):
        return True
    await update.effective_message.reply_text("🔒 Only group admins can use this command.")
    return False


def _confirm(update: Update, text: str):
    return update.effective_message.reply_text(text)


# ---------------------------------------------------------------------- #
# welcome
# ---------------------------------------------------------------------- #
async def cmd_gw(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    args_raw = update.effective_message.text.split(None, 1)[1] if " " in (update.effective_message.text or "") else ""
    arg0 = args_raw.split(" ", 1)[0].lower() if args_raw else ""
    if arg0 in ("on", "off"):
        g["welcome"]["on"] = arg0 == "on"
        state.changed()
        get_db(context).request_save()
        await _confirm(update, f"👋 Group welcome turned {arg0.upper()}.")
        return
    if args_raw.strip():
        g["welcome"]["text"] = args_raw.strip()[:3000]
        state.changed()
        await get_db(context).audit_setting(f"groups.{gid}.welcome.text", update.effective_user.full_name, args_raw)
        get_db(context).request_save()
        await _confirm(update, "✅ Group welcome updated & saved to the database channel.\n💡 Use {mention} {name} {group} {count} placeholders.\nTest it: add a member or see it in /panel → 👋 Welcome.")
        return
    eff = g["welcome"].get("text") or state.setting("welcome_group")
    await _confirm(
        update,
        f"👋 GROUP WELCOME — {'✅ ON' if g['welcome']['on'] else '⛔ OFF'}\n"
        f"─────────────\n{truncate(str(eff), 1200)}\n─────────────\n"
        "Set it:  /gw <your text>\n"
        "Toggle:  /gw on | /gw off\n"
        "Pin it:  /pinwel on | off\n"
        "Buttons: /panel → 👋 Welcome",
    )


async def cmd_pinwel(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    arg = (context.args or ["toggle"])[0].lower()
    if arg == "on":
        g["welcome"]["pin"] = True
    elif arg == "off":
        g["welcome"]["pin"] = False
    else:
        g["welcome"]["pin"] = not g["welcome"]["pin"]
    state.changed()
    get_db(context).request_save()
    await _confirm(update, f"📌 Pinning of the welcome message: {'ON' if g['welcome']['pin'] else 'OFF'}")


# ---------------------------------------------------------------------- #
# keyword filters
# ---------------------------------------------------------------------- #
async def cmd_filter(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    msg = update.effective_message
    args = context.args or []
    if not args:
        await msg.reply_text(
            "🎯 Usage:\n"
            "• /filter hello Welcome! How are you?   → text reply\n"
            "• reply to a STICKER/PHOTO/GIF with /filter hello   → media reply\n"
            "• /filters — list   • /stopfilter hello — remove"
        )
        return
    word = args[0].lower().strip()
    if len(word) < 2:
        await msg.reply_text("⚠️ Keyword must be at least 2 characters.")
        return
    entry = {"id": new_id("f"), "word": word}
    replied = msg.reply_to_message
    m = media_info(replied) if replied else None
    rest = msg.text.split(None, 2)[2] if len(msg.text.split()) > 2 else ""
    if m:
        db_mid = await get_db(context).store_media(replied)
        entry.update({"kind": "media", "mtype": m["kind"], "file_id": m["file_id"], "cap": m.get("cap", ""), "db_mid": db_mid})
    elif rest.strip():
        entry.update({"kind": "text", "content": rest.strip()[:2000]})
    else:
        await msg.reply_text("⚠️ Add a reply text after the word, or reply to a media message with /filter <word>.")
        return
    g["filters"] = [f for f in g["filters"] if f.get("word") != word]
    g["filters"].append(entry)
    state.changed()
    await get_db(context).audit_setting(f"groups.{gid}.filters[{entry['id']}]", update.effective_user.full_name, f"FILTER “{word}” → {entry['kind']}")
    get_db(context).request_save()
    await msg.reply_text(f"✅ Filter set: “{word}” → {entry['kind']}. Saved to the database channel.")


async def cmd_filters(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    g = get_state(context).get_group(gid)
    if not g or not g["filters"]:
        await update.effective_message.reply_text("🎯 No filters set. Add one: /filter <word> <reply>")
        return
    lines = ["🎯 FILTERS:"]
    for i, f in enumerate(g["filters"], 1):
        desc = truncate(f.get("content", f"[{f.get('mtype', 'media')}]"), 40) if f.get("kind") == "text" else f"[{f.get('mtype', 'media')}]"
        lines.append(f"{i}. “{f['word']}” → {desc}")
    lines.append("\nRemove: /stopfilter <word>")
    await update.effective_message.reply_text("\n".join(lines)[:4000])


async def cmd_stopfilter(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g = state.get_group(gid)
    args = context.args or []
    if not g or not args:
        await update.effective_message.reply_text("Usage: /stopfilter <word>")
        return
    word = " ".join(args).lower()
    before = len(g["filters"])
    g["filters"] = [f for f in g["filters"] if f.get("word") != word]
    if len(g["filters"]) != before:
        state.changed()
        get_db(context).request_save()
        await update.effective_message.reply_text(f"🗑 Filter “{word}” removed.")
    else:
        await update.effective_message.reply_text(f"🤷 No filter named “{word}”.")


# ---------------------------------------------------------------------- #
# bad words
# ---------------------------------------------------------------------- #
async def cmd_badword(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "🤬 Usage: /badword <word> [mute duration]\n"
            "Examples:  /badword fuck  •  /badword spam 2h  •  /badword idiot 30m\n"
            "Default mute: 60 min. /badwords — list, /delbadword <word> — remove."
        )
        return
    word = args[0].lower().strip()
    if len(word) < 2:
        await update.effective_message.reply_text("⚠️ Word must be at least 2 characters.")
        return
    secs = parse_duration(args[1]) if len(args) > 1 else None
    minutes = max(1, (secs or 3600) // 60)
    g["badwords"] = [b for b in g["badwords"] if b.get("word") != word]
    entry = {"id": new_id("b"), "word": word, "mute_min": minutes}
    g["badwords"].append(entry)
    state.changed()
    await get_db(context).audit_setting(f"groups.{gid}.badwords[{entry['id']}]", update.effective_user.full_name, f"BADWORD “{word}” → mute {minutes}m")
    get_db(context).request_save()
    await update.effective_message.reply_text(f"✅ Bad word “{word}” added → 🔇 {humanize_seconds(minutes * 60)} mute + message delete.")


async def cmd_badwords(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    g = get_state(context).get_group(update.effective_chat.id)
    if not g or not g["badwords"]:
        await update.effective_message.reply_text("🤬 No bad words set. Add: /badword <word> [mins]")
        return
    lines = ["🤬 BAD WORDS:"] + [f"{i}. “{b['word']}” → 🔇 {b.get('mute_min', 60)} min" for i, b in enumerate(g["badwords"], 1)]
    lines.append("\nRemove: /delbadword <word>")
    await update.effective_message.reply_text("\n".join(lines)[:4000])


async def cmd_delbadword(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g = state.get_group(gid)
    args = context.args or []
    if not g or not args:
        await update.effective_message.reply_text("Usage: /delbadword <word>")
        return
    word = " ".join(args).lower()
    before = len(g["badwords"])
    g["badwords"] = [b for b in g["badwords"] if b.get("word") != word]
    if len(g["badwords"]) != before:
        state.changed()
        get_db(context).request_save()
        await update.effective_message.reply_text(f"🗑 Bad word “{word}” removed.")
    else:
        await update.effective_message.reply_text(f"🤷 No bad word “{word}”.")


# ---------------------------------------------------------------------- #
# link guard
# ---------------------------------------------------------------------- #
async def cmd_linkguard(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    arg = (context.args or ["status"])[0].lower()
    if arg in ("on", "off"):
        g["linkguard"]["on"] = arg == "on"
        state.changed()
        await get_db(context).audit(f"🔗 Link guard {arg.upper()} in group {g.get('title')} ({gid}) by {update.effective_user.full_name}")
        get_db(context).request_save()
        await update.effective_message.reply_text(f"🔗 Link guard: {arg.upper()}.\nNon-admin links will now be {'DELETED + user notified' if arg == 'on' else 'ignored'}.\nCustom notice: /linkmsg <text>")
    else:
        await update.effective_message.reply_text(f"🔗 Link guard is {'✅ ON' if g['linkguard']['on'] else '⛔ OFF'}.\nTurn on/off: /linkguard on | /linkguard off")


async def cmd_linkmsg(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    text = update.effective_message.text.split(None, 1)[1] if " " in (update.effective_message.text or "") else ""
    if not text.strip():
        cur = g["linkguard"].get("msg") or state.setting("link_msg")
        await update.effective_message.reply_text(f"🔗 Current notice:\n{cur}\n\nSet new: /linkmsg <text>  ({'{mention}'} pings the user)")
        return
    g["linkguard"]["msg"] = text.strip()[:1000]
    state.changed()
    await get_db(context).audit_setting(f"groups.{gid}.linkguard.msg", update.effective_user.full_name, text)
    get_db(context).request_save()
    await update.effective_message.reply_text("✅ Link-guard notice updated & saved.")


# ---------------------------------------------------------------------- #
# schedules
# ---------------------------------------------------------------------- #
async def cmd_schedule(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    db = get_db(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    msg = update.effective_message
    parts = (msg.text or "").split(None, 1)
    rest = parts[1] if len(parts) > 1 else ""
    tokens = rest.split(None, 1)
    if not tokens:
        await msg.reply_text(
            "📅 Usage:\n"
            "• /schedule 8:00 PM Good evening class! 🎙   → daily post\n"
            "• /schedule once 9:30 AM Exam tomorrow!      → one time\n"
            "• reply to a PHOTO/STICKER with /schedule 8:00 PM  → media post\n"
            f"Timezone: {state.tz} • /schedules — list"
        )
        return
    repeat = "daily"
    if tokens[0].lower() in ("once", "onetime", "1"):
        repeat = "once"
        tokens = tokens[1].split(None, 1) if len(tokens) > 1 else []
        if not tokens:
            await msg.reply_text("⚠️ /schedule once <time> <text>")
            return
    parsed = parse_clock(tokens[0])
    if not parsed:
        await msg.reply_text(f"⚠️ Could not understand time “{tokens[0]}”. Examples: 8:00 PM, 20:00, 9am")
        return
    hhmm = f"{parsed[0]:02d}:{parsed[1]:02d}"
    content = tokens[1].strip() if len(tokens) > 1 else ""
    entry = {"id": new_id("s"), "time": hhmm, "repeat": repeat, "on": True, "created": now_ts()}
    replied = msg.reply_to_message
    m = media_info(replied) if replied else None
    if m:
        db_mid = await db.store_media(replied)
        entry["m"] = {"kind": m["kind"], "file_id": m["file_id"], "cap": content or m.get("cap", ""), "db_mid": db_mid}
        entry["text"] = ""
    elif content:
        entry["text"] = content[:3500]
    else:
        await msg.reply_text("⚠️ Add the message text after the time (or reply to media).")
        return
    g["schedules"].append(entry)
    state.changed()
    db.request_save()
    from .engine import ensure_schedule_job

    await ensure_schedule_job(context, gid, entry["id"])
    await msg.reply_text(
        f"✅ Scheduled!\n⏰ {hhmm} ({state.tz}) • {repeat}\n"
        f"Content: {truncate(content or '[' + (entry.get('m', {}).get('kind', 'media')) + ']', 60)}\n"
        "Manage: /schedules or /panel → 📅 Schedules"
    )


async def cmd_schedules(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g = state.get_group(gid)
    if not g or not g["schedules"]:
        await update.effective_message.reply_text("📅 Nothing scheduled. Add: /schedule 8:00 PM <text>")
        return
    lines = [f"📅 SCHEDULES (tz: {state.tz}):"]
    for i, s in enumerate(g["schedules"], 1):
        what = truncate(s.get("text") or f"[{s.get('m', {}).get('kind', 'media')}]", 36)
        lines.append(f"{i}. ⏰ {s['time']} • {s.get('repeat', 'daily')} • {'✅' if s.get('on', True) else '⏸'} — “{what}”  (id {s['id']})")
    lines.append("\nRemove: /delschedule <number or id>")
    await update.effective_message.reply_text("\n".join(lines)[:4000])


async def cmd_delschedule(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g = state.get_group(gid)
    args = context.args or []
    if not g or not args:
        await update.effective_message.reply_text("Usage: /delschedule <number or id>")
        return
    key = args[0]
    target = None
    if key.isdigit() and 1 <= int(key) <= len(g["schedules"]):
        target = g["schedules"][int(key) - 1]
    else:
        target = next((s for s in g["schedules"] if s.get("id") == key), None)
    if not target:
        await update.effective_message.reply_text("🤷 Schedule not found.")
        return
    g["schedules"] = [s for s in g["schedules"] if s is not target]
    state.changed()
    get_db(context).request_save()
    from .engine import drop_schedule_job

    drop_schedule_job(context, gid, target["id"])
    await update.effective_message.reply_text(f"🗑 Schedule {target['time']} removed.")


# ---------------------------------------------------------------------- #
# warns
# ---------------------------------------------------------------------- #
async def cmd_warn(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    msg = update.effective_message
    replied = msg.reply_to_message
    if not replied or not replied.from_user or replied.from_user.is_bot:
        await msg.reply_text("⚠️ Reply to the user's message with /warn [reason].")
        return
    from .engine import apply_warn

    reason = msg.text.split(None, 1)[1] if " " in (msg.text or "") else ""
    await apply_warn(context, update.effective_chat, replied.from_user, reason, by=update.effective_user)


async def cmd_unwarn(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    msg = update.effective_message
    replied = msg.reply_to_message
    if not replied or not replied.from_user:
        await msg.reply_text("⚠️ Reply to the user with /unwarn.")
        return
    state = get_state(context)
    g = state.get_group(update.effective_chat.id)
    if g:
        counts = g["warns"].setdefault("counts", {})
        counts[str(replied.from_user.id)] = max(0, int(counts.get(str(replied.from_user.id), 0)) - 1)
        state.changed()
        get_db(context).request_save()
    await msg.reply_text(f"➖ Removed one warn from {replied.from_user.full_name}.")


async def cmd_warns(update: Update, context: CTX) -> None:
    msg = update.effective_message
    replied = msg.reply_to_message
    target = replied.from_user if replied else msg.from_user
    if not target:
        return
    state = get_state(context)
    g = state.get_group(update.effective_chat.id)
    n = int((g or {}).get("warns", {}).get("counts", {}).get(str(target.id), 0)) if g else 0
    limit = g["warns"]["limit"] if g else 3
    await msg.reply_text(f"⚠️ {target.full_name} has {n}/{limit} warns.")


async def cmd_warnlimit(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    state = get_state(context)
    g, _ = state.ensure_group(update.effective_chat.id, update.effective_chat.title)
    args = context.args or []
    if args and args[0].isdigit() and 1 <= int(args[0]) <= 20:
        g["warns"]["limit"] = int(args[0])
        state.changed()
        get_db(context).request_save()
        await update.effective_message.reply_text(f"⚠️ Warn limit set to {args[0]}.")
    else:
        await update.effective_message.reply_text(f"Current warn limit: {g['warns']['limit']}. Change: /warnlimit <1-20>")


async def cmd_warnaction(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    state = get_state(context)
    g, _ = state.ensure_group(update.effective_chat.id, update.effective_chat.title)
    args = [(a or "").lower() for a in (context.args or [])]
    if args and args[0] in ("mute", "ban"):
        g["warns"]["action"] = args[0]
        state.changed()
        get_db(context).request_save()
        await update.effective_message.reply_text(f"⚠️ Action on warn limit reached: {args[0].upper()}")
    else:
        await update.effective_message.reply_text(f"Current action: {g['warns']['action']}. Change: /warnaction mute|ban")


# ---------------------------------------------------------------------- #
# lock / verify / flood
# ---------------------------------------------------------------------- #
async def cmd_lock(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    try:
        chat_full = await context.bot.get_chat(gid)
        if chat_full.permissions:
            g["lock_saved"] = chat_full.permissions.to_dict()
        await context.bot.set_chat_permissions(gid, ALL_OFF)
        g["locked"] = True
        state.changed()
        await get_db(context).audit(f"🔒 Group LOCKED: {g.get('title')} ({gid}) by {update.effective_user.full_name}")
        get_db(context).request_save()
        await update.effective_message.reply_text("🔒 Group LOCKED — only admins can send messages now. /unlock to reopen.")
    except TelegramError as e:
        await update.effective_message.reply_text(f"⚠️ Could not lock (am I admin with restrict rights?): {e}")


async def cmd_unlock(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    from telegram import ChatPermissions

    saved = g.get("lock_saved") or {}
    perms = ChatPermissions(**{k: v for k, v in saved.items() if k in PERM_FIELDS}) if saved else ALL_ON
    try:
        await context.bot.set_chat_permissions(gid, perms)
        g["locked"] = False
        g["lock_saved"] = None
        state.changed()
        await get_db(context).audit(f"🔓 Group UNLOCKED: {g.get('title')} ({gid}) by {update.effective_user.full_name}")
        get_db(context).request_save()
        await update.effective_message.reply_text("🔓 Group UNLOCKED.")
    except TelegramError as e:
        await update.effective_message.reply_text(f"⚠️ Could not unlock: {e}")


async def cmd_verify(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    arg = (context.args or ["status"])[0].lower()
    if arg in ("on", "off"):
        g["verify"]["on"] = arg == "on"
        state.changed()
        await get_db(context).audit(f"🧪 Join verification {arg.upper()} in {g.get('title')} ({gid})")
        get_db(context).request_save()
        await update.effective_message.reply_text(
            f"🧪 Join verification: {arg.upper()}.\n"
            + ("New members must press ✅ in the welcome message or they get kicked after "
               f"{g['verify'].get('timeout_min', 10)} min (/verifytime <min>)." if arg == "on" else "")
        )
    else:
        await update.effective_message.reply_text(
            f"🧪 Verify-on-join is {'✅ ON' if g['verify']['on'] else '⛔ OFF'} (timeout {g['verify'].get('timeout_min', 10)} min).\n"
            "Turn on/off: /verify on | /verify off • /verifytime <minutes>"
        )


async def cmd_verifytime(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    state = get_state(context)
    g, _ = state.ensure_group(update.effective_chat.id, update.effective_chat.title)
    args = context.args or []
    if args and args[0].isdigit() and 1 <= int(args[0]) <= 1440:
        g["verify"]["timeout_min"] = int(args[0])
        state.changed()
        get_db(context).request_save()
        await update.effective_message.reply_text(f"🧪 Verify timeout set to {args[0]} minutes.")
    else:
        await update.effective_message.reply_text(f"Current timeout: {g['verify'].get('timeout_min', 10)} min. Change: /verifytime <1-1440>")


async def cmd_flood(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    arg = (context.args or ["status"])[0].lower()
    if arg in ("on", "off"):
        g["flood"]["on"] = arg == "on"
        state.changed()
        await get_db(context).audit(f"🌊 Anti-flood {arg.upper()} in {g.get('title')} ({gid})")
        get_db(context).request_save()
        await update.effective_message.reply_text(
            f"🌊 Anti-flood: {arg.upper()}"
            + (f" — more than {g['flood']['limit']} msgs in {g['flood']['window']}s → 🔇 {g['flood']['mute_min']} min mute." if arg == "on" else "")
        )
    else:
        f = g["flood"]
        await update.effective_message.reply_text(
            f"🌊 Anti-flood is {'✅ ON' if f['on'] else '⛔ OFF'} ({f['limit']} msgs/{f['window']}s → {f['mute_min']}m mute).\n"
            "Turn on/off: /flood on | /flood off • tune limits in /panel → 🌊"
        )


# ---------------------------------------------------------------------- #
# rules / panel / ids / group stats
# ---------------------------------------------------------------------- #
async def cmd_rules(update: Update, context: CTX) -> None:
    state = get_state(context)
    g = state.get_group(update.effective_chat.id)
    text = (g or {}).get("rules") or state.setting("rules")
    text, ents = format_placeholders(text, chat_title=update.effective_chat.title, tz=state.tz)
    await send_text(context, update.effective_chat.id, text, ents)


async def cmd_setrules(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    g, _ = state.ensure_group(gid, update.effective_chat.title)
    text = update.effective_message.text.split(None, 1)[1] if " " in (update.effective_message.text or "") else ""
    if not text.strip():
        await update.effective_message.reply_text("Usage: /setrules <text> (empty = use global default)")
        return
    g["rules"] = text.strip()[:3000]
    state.changed()
    await get_db(context).audit_setting(f"groups.{gid}.rules", update.effective_user.full_name, text)
    get_db(context).request_save()
    await update.effective_message.reply_text("✅ Group rules updated & saved.")


async def cmd_panel(update: Update, context: CTX) -> None:
    chat = update.effective_chat
    user = update.effective_user
    state = get_state(context)
    if chat.type not in ("group", "supergroup"):
        return
    if not await is_group_admin(context, chat.id, user.id):
        await update.effective_message.reply_text("🔒 Only group admins can open the panel.")
        return
    from .render import render_cb

    state.ensure_group(chat.id, chat.title)
    screen = await render_cb(f"gp:{chat.id}", context, user=user)
    ok = await dm_user(context, user.id, f"🛡 Control panel for “{truncate(chat.title or str(chat.id), 30)}”:")
    if ok and screen:
        await show_screen(context, user.id, screen)
    elif not ok:
        from ..keyboards import GREEN, cbtn, kboard

        await update.effective_message.reply_text(
            "⚠️ I can't DM you — press the button below, then /panel again.",
            reply_markup=kboard([[cbtn("🛡 Open the bot", url=f"https://t.me/{context.bot.username}?start=panel_{chat.id}", color=GREEN)]]),
        )


async def cmd_gid(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    await update.effective_message.reply_text(
        f"🆔 Your user id: {update.effective_user.id}\n💬 This chat id: {update.effective_chat.id}"
    )


async def cmd_gstats(update: Update, context: CTX) -> None:
    if not await admin_gate(update, context):
        return
    gid = update.effective_chat.id
    state = get_state(context)
    from .common import fetch_members

    members = await fetch_members(context, gid)
    g = state.get_group(gid) or {}
    s = g.get("stats", {})
    await update.effective_message.reply_text(
        f"📊 GROUP STATS — {truncate(g.get('title') or str(gid), 26)}\n"
        "─────────────\n"
        f"👥 Members: {members or '?'}\n"
        f"🛡 Guarding since: {fmt_ts(g.get('joined'), state.tz)}\n"
        f"💬 Msgs seen: {s.get('msgs', 0)} • 👋 Welcomes: {s.get('welcomes', 0)}\n"
        f"🗑 Deleted: {s.get('deletes', 0)} • 🔇 Mutes: {s.get('mutes', 0)} • 🚫 Bans: {s.get('bans', 0)}\n"
        f"⚠️ Warns: {s.get('warns', 0)} • 🚨 Reports: {s.get('reports', 0)}\n"
        f"🎯 Filters: {len(g.get('filters', []))} • 🤬 Bad words: {len(g.get('badwords', []))} • 📅 Schedules: {len(g.get('schedules', []))}"
    )


# ---------------------------------------------------------------------- #
def register(app) -> None:
    grp = filters.ChatType.GROUPS
    app.add_handler(CommandHandler("gw", cmd_gw, filters=grp))
    app.add_handler(CommandHandler(["pinwel", "pinwelcome"], cmd_pinwel, filters=grp))
    app.add_handler(CommandHandler("filter", cmd_filter, filters=grp))
    app.add_handler(CommandHandler("filters", cmd_filters, filters=grp))
    app.add_handler(CommandHandler(["stopfilter", "rmfilter", "delfilter"], cmd_stopfilter, filters=grp))
    app.add_handler(CommandHandler("badword", cmd_badword, filters=grp))
    app.add_handler(CommandHandler("badwords", cmd_badwords, filters=grp))
    app.add_handler(CommandHandler(["delbadword", "rmbadword"], cmd_delbadword, filters=grp))
    app.add_handler(CommandHandler("linkguard", cmd_linkguard, filters=grp))
    app.add_handler(CommandHandler("linkmsg", cmd_linkmsg, filters=grp))
    app.add_handler(CommandHandler(["schedule", "sched"], cmd_schedule, filters=grp))
    app.add_handler(CommandHandler(["schedules", "scheds"], cmd_schedules, filters=grp))
    app.add_handler(CommandHandler(["delschedule", "rmschedule"], cmd_delschedule, filters=grp))
    app.add_handler(CommandHandler("warn", cmd_warn, filters=grp))
    app.add_handler(CommandHandler("unwarn", cmd_unwarn, filters=grp))
    app.add_handler(CommandHandler("warns", cmd_warns, filters=grp))
    app.add_handler(CommandHandler("warnlimit", cmd_warnlimit, filters=grp))
    app.add_handler(CommandHandler("warnaction", cmd_warnaction, filters=grp))
    app.add_handler(CommandHandler("lock", cmd_lock, filters=grp))
    app.add_handler(CommandHandler("unlock", cmd_unlock, filters=grp))
    app.add_handler(CommandHandler("verify", cmd_verify, filters=grp))
    app.add_handler(CommandHandler("verifytime", cmd_verifytime, filters=grp))
    app.add_handler(CommandHandler("flood", cmd_flood, filters=grp))
    app.add_handler(CommandHandler("rules", cmd_rules, filters=grp))
    app.add_handler(CommandHandler("setrules", cmd_setrules, filters=grp))
    app.add_handler(CommandHandler("panel", cmd_panel, filters=grp))
    app.add_handler(CommandHandler("gid", cmd_gid, filters=grp))
    app.add_handler(CommandHandler(["gstats", "groupstats"], cmd_gstats, filters=grp))
