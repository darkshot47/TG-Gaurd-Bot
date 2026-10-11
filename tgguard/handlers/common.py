"""Shared helpers: permissions, safe send/edit, the micro flow engine, media utils."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from telegram import (
    ChatFullInfo,
    ChatMember,
    ChatPermissions,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    Update,
    User,
)
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from .. import config
from ..db import ChannelDB
from ..state import State

log = logging.getLogger("tgguard.common")

CTX = ContextTypes.DEFAULT_TYPE

# ---------------------------------------------------------------------- #
# accessors
# ---------------------------------------------------------------------- #
def get_state(context: CTX) -> State:
    return context.bot_data["state"]


def get_db(context: CTX) -> ChannelDB:
    return context.bot_data["db"]


def is_owner(user_id: int | None) -> bool:
    return config.CFG.is_owner(user_id)


# ---------------------------------------------------------------------- #
# group-admin cache (live check via getChatMember, cached 3 minutes)
# ---------------------------------------------------------------------- #
ADMIN_TTL = 180


async def group_admin_ids(context: CTX, chat_id: int, refresh: bool = False) -> set[int]:
    cache = context.bot_data.setdefault("admcache", {})
    entry = cache.get(chat_id)
    import time

    now = time.time()
    if not refresh and entry and now - entry[0] < ADMIN_TTL:
        return entry[1]
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        ids = {m.user.id for m in admins if m.status in ("administrator", "creator")}
        cache[chat_id] = (now, ids)
        return ids
    except TelegramError as e:
        log.warning("get_chat_administrators(%s) failed: %s", chat_id, e)
        return entry[1] if entry else set()


async def is_group_admin(context: CTX, chat_id: int, user_id: int) -> bool:
    if is_owner(user_id):
        return True
    return user_id in await group_admin_ids(context, chat_id)


# ---------------------------------------------------------------------- #
# safe send / edit
# ---------------------------------------------------------------------- #
async def send_text(
    context: CTX,
    chat_id: int,
    text: str,
    entities: list[MessageEntity] | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    reply_to: int | None = None,
) -> Message | None:
    try:
        return await context.bot.send_message(
            chat_id,
            text[:4096],
            entities=entities or None,
            reply_markup=reply_markup,
            reply_to_message_id=reply_to,
            allow_sending_without_reply=True,
        )
    except TelegramError as e:
        log.warning("send_text(%s) failed: %s", chat_id, e)
        return None


async def edit_text(
    context: CTX,
    chat_id: int,
    message_id: int,
    text: str,
    entities: list[MessageEntity] | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> bool:
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text[:4096],
            entities=entities or None,
            reply_markup=reply_markup,
        )
        return True
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return True
        if "message to edit not found" in str(e).lower() or "can't edit" in str(e).lower():
            return False
        log.warning("edit_text(%s/%s): %s", chat_id, message_id, e)
        return False
    except TelegramError as e:
        log.warning("edit_text(%s/%s): %s", chat_id, message_id, e)
        return False


async def show_screen(context: CTX, chat_id: int, screen) -> Message | None:
    """screen = (text, entities, reply_markup)."""
    text, entities, markup = screen
    return await send_text(context, chat_id, text, entities, markup)


async def edit_screen(context: CTX, chat_id: int, message_id: int, screen) -> bool:
    text, entities, markup = screen
    ok = await edit_text(context, chat_id, message_id, text, entities, markup)
    if not ok:  # message vanished -> send fresh
        await show_screen(context, chat_id, screen)
    return ok


async def answer_cb(update: Update, text: str | None = None) -> None:
    cb = update.callback_query
    if cb:
        try:
            await cb.answer(text=text or "")
        except TelegramError:
            pass


# ---------------------------------------------------------------------- #
# DM delivery with automatic block detection
# ---------------------------------------------------------------------- #
async def dm_user(context: CTX, uid: int, text: str, **kw) -> bool:
    try:
        await context.bot.send_message(uid, text[:4096], **kw)
        return True
    except Forbidden:
        await _mark_blocked(context, uid, True)
        return False
    except BadRequest as e:
        if "blocked" in str(e).lower() or "user is deactivated" in str(e).lower():
            await _mark_blocked(context, uid, True)
        else:
            log.warning("dm_user(%s): %s", uid, e)
        return False
    except TelegramError as e:
        log.warning("dm_user(%s): %s", uid, e)
        return False


async def _mark_blocked(context: CTX, uid: int, blocked: bool) -> None:
    state = get_state(context)
    rec = state.get_user(uid)
    was = bool(rec.get("blocked")) if rec else False
    state.mark_blocked(uid, blocked)
    if blocked != was:
        db = get_db(context)
        rec = state.get_user(uid) or {"id": uid, "first": "?"}
        if blocked:
            await db.audit_blocked(rec)
        else:
            await db.audit_unblocked(rec)
        db.request_save()


# ---------------------------------------------------------------------- #
# micro flow engine (button-driven multi-step inputs)
# ---------------------------------------------------------------------- #
FLOW: dict[str, Callable[[dict, Update, CTX], Awaitable[None]]] = {}


def flow(name: str):
    def deco(fn):
        FLOW[name] = fn
        return fn

    return deco


def start_flow(context: CTX, act: str, prompt: str, **kw) -> None:
    context.user_data["flow"] = {"act": act, "prompt": prompt, **kw}


def cur_flow(context: CTX) -> dict | None:
    return context.user_data.get("flow")


def clear_flow(context: CTX) -> None:
    context.user_data.pop("flow", None)


async def flow_entry(update: Update, context: CTX) -> None:
    """High-priority private-chat handler. Consumes messages while a flow is active."""
    fl = cur_flow(context)
    if not fl or not update.effective_message:
        return
    msg = update.effective_message
    text = msg.text or ""
    if text.startswith("/"):
        cmd = text.split()[0][1:].split("@")[0].lower()
        if cmd == "cancel":
            clear_flow(context)
            await msg.reply_text("❌ Action cancelled.")
            raise ApplicationHandlerStop
        if cmd == "start":
            # cancel the flow but let /start itself run normally
            clear_flow(context)
            return
        # any other command: abandon flow silently and let it through
        clear_flow(context)
        return
    handler = FLOW.get(fl["act"])
    if not handler:
        clear_flow(context)
        return
    try:
        await handler(fl, update, context)
    except ApplicationHandlerStop:
        raise
    except Exception:
        log.exception("flow %s crashed", fl.get("act"))
        clear_flow(context)
        await msg.reply_text("⚠️ Something went wrong — action cancelled.")
    raise ApplicationHandlerStop


# ---------------------------------------------------------------------- #
# media helpers
# ---------------------------------------------------------------------- #
MEDIA_KINDS = ("photo", "sticker", "animation", "video", "document", "audio", "voice", "video_note")


def media_info(msg: Message) -> dict | None:
    """Extract a storable representation of a media message."""
    if msg.photo:
        p = msg.photo[-1]
        return {"kind": "photo", "file_id": p.file_id, "cap": msg.caption or ""}
    for kind in MEDIA_KINDS[1:]:
        obj = getattr(msg, kind, None)
        if obj:
            return {"kind": kind, "file_id": obj.file_id, "cap": msg.caption or ""}
    return None


async def send_media(
    context: CTX,
    chat_id: int,
    m: dict,
    reply_to: int | None = None,
    caption: str | None = None,
) -> bool:
    """Send stored media; falls back to copying the DB-channel backup message."""
    kind = m.get("kind")
    file_id = m.get("file_id")
    cap = caption if caption is not None else m.get("cap") or None
    sender = {
        "photo": context.bot.send_photo,
        "sticker": context.bot.send_sticker,
        "animation": context.bot.send_animation,
        "video": context.bot.send_video,
        "document": context.bot.send_document,
        "audio": context.bot.send_audio,
        "voice": context.bot.send_voice,
        "video_note": context.bot.send_video_note,
    }.get(kind)
    if sender and file_id:
        try:
            kw = {"reply_to_message_id": reply_to, "allow_sending_without_reply": True}
            if kind == "sticker":
                await sender(chat_id, file_id, **kw)
            elif kind == "video_note":
                await sender(chat_id, file_id, **kw)
            else:
                await sender(chat_id, file_id, caption=(cap[:1024] if cap else None), **kw)
            return True
        except BadRequest as e:
            log.warning("send_media %s failed (%s), trying db copy", kind, e)
    db_mid = m.get("db_mid")
    if db_mid:
        try:
            await context.bot.copy_message(
                chat_id, from_chat_id=config.CFG.db_channel, message_id=db_mid, reply_to_message_id=reply_to
            )
            return True
        except TelegramError as e:
            log.warning("copy_message fallback failed: %s", e)
    return False


# ---------------------------------------------------------------------- #
# misc
# ---------------------------------------------------------------------- #
def paginate(items: list, page: int, per: int) -> tuple[list, int, int]:
    npages = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, npages - 1))
    return items[page * per : (page + 1) * per], page, npages


def user_display(rec_or_user, with_id: bool = True) -> str:
    if isinstance(rec_or_user, User):
        u = rec_or_user
        name = u.full_name
        uname = f" @{u.username}" if u.username else ""
        return f"{name}{uname}" + (f" • `{u.id}`" if with_id else "")
    rec = rec_or_user
    name = ((rec.get("first") or "") + " " + (rec.get("last") or "")).strip() or "Unknown"
    uname = f" @{rec['user']}" if rec.get("user") else ""
    return f"{name}{uname}" + (f" • {rec.get('id')}" if with_id else "")


async def fetch_members(context: CTX, gid: int) -> int:
    """Best-effort member count, cached 10 minutes in state."""
    state = get_state(context)
    g = state.get_group(gid)
    import time

    if g and g.get("members") and time.time() - int(g.get("members_ts") or 0) < 600:
        return int(g["members"])
    try:
        chat: ChatFullInfo = await context.bot.get_chat(gid)
        n = int(chat.member_count or 0)
        if g:
            g["members"] = n
            g["members_ts"] = int(time.time())
            if chat.title and g.get("title") != chat.title:
                g["title"] = chat.title
            state.changed()
        return n
    except TelegramError:
        return int(g.get("members") or 0) if g else 0


PERM_FIELDS = (
    "can_send_messages",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
)

ALL_OFF = ChatPermissions(
    can_send_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
    can_manage_topics=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
)

ALL_ON = ChatPermissions(
    can_send_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_change_info=None,
    can_invite_users=True,
    can_pin_messages=None,
    can_manage_topics=None,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
)


async def mute_user(context: CTX, chat_id: int, uid: int, seconds: int) -> bool:
    import datetime as dtm

    until = dtm.datetime.now(dtm.timezone.utc) + dtm.timedelta(seconds=seconds)
    try:
        await context.bot.restrict_chat_member(chat_id, uid, permissions=ALL_OFF, until_date=until)
        return True
    except TelegramError as e:
        log.warning("mute_user(%s,%s): %s", chat_id, uid, e)
        return False


async def unmute_user(context: CTX, chat_id: int, uid: int) -> bool:
    try:
        await context.bot.restrict_chat_member(chat_id, uid, permissions=ALL_ON)
        return True
    except TelegramError as e:
        log.warning("unmute_user(%s,%s): %s", chat_id, uid, e)
        return False


def member_status_ok(m: ChatMember | None) -> bool:
    return bool(m) and m.status in ("member", "administrator", "creator", "restricted")
