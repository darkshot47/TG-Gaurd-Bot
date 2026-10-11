"""User-facing private-chat handlers: /start registration, menus, plans, about..."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CommandHandler, filters

from . import screens as S
from .common import CTX, get_db, get_state, is_owner, show_screen

log = logging.getLogger("tgguard.pm_user")


def _bot_username(context: CTX) -> str:
    return context.bot.username or get_state(context).data.get("bot_username", "")


async def cmd_start(update: Update, context: CTX) -> None:
    user = update.effective_user
    state = get_state(context)
    db = get_db(context)

    # /start <payload> inside a GROUP = the bot was just added via the
    # "Add to group" deep-link button (startgroup=tgguard).
    if update.effective_chat.type != "private":
        gid = update.effective_chat.id
        g, created = state.ensure_group(gid, update.effective_chat.title, user.id if user else None)
        if created:
            await db.audit_group(g, by=(user.full_name if user else ""))
        db.request_save()
        await update.effective_message.reply_text(
            "🛡 TG-Guard is ready!\n\n"
            "Group admins: press /panel here (or open my PM) to set the welcome message, "
            "filters, bad words, link guard and scheduled posts — all with coloured buttons. ✨"
        )
        return

    rec, created = state.touch_user(user.id, user.first_name, user.last_name or "", user.username or "")
    if created:
        number = len(state.users)
        await db.audit_new_user(rec, number)
    if rec.get("blocked"):
        state.mark_blocked(user.id, False)
        await db.audit_unblocked(rec)
    state.changed()
    db.request_save()

    owner = is_owner(user.id)
    screen = S.scr_home(state, user, _bot_username(context), owner)
    await show_screen(context, user.id, screen)

    # deep link  /start panel_<gid>  -> open that group's admin panel
    payload = (context.args or [""])[0]
    if payload.startswith("panel_"):
        from .common import is_group_admin
        from .render import render_cb

        try:
            gid = int(payload[6:])
        except ValueError:
            gid = None
        if gid and await is_group_admin(context, gid, user.id):
            gscreen = await render_cb(f"gp:{gid}", context, user=user)
            if gscreen:
                await show_screen(context, user.id, gscreen)
    if owner and created:
        await show_screen(
            context,
            user.id,
            (
                "👑 You are the OWNER of this bot.\n"
                "Press 🛡 Owner Panel above — users, broadcast, stats, editable texts, "
                "plans/prices and every group setting is button-driven.\n\n"
                "💾 Database = your private channel. Every change is saved there automatically.",
                None,
                None,
            ),
        )


async def cmd_about(update: Update, context: CTX) -> None:
    await show_screen(context, update.effective_chat.id, S.scr_simple(get_state(context), "about", "🎙️ ABOUT"))


async def cmd_courses(update: Update, context: CTX) -> None:
    await show_screen(context, update.effective_chat.id, S.scr_simple(get_state(context), "courses", "📚 COURSES"))


async def cmd_contact(update: Update, context: CTX) -> None:
    await show_screen(context, update.effective_chat.id, S.scr_simple(get_state(context), "contact", "📞 CONTACT"))


async def cmd_plan(update: Update, context: CTX) -> None:
    state = get_state(context)
    chat_id = update.effective_chat.id
    owner = is_owner(update.effective_user.id)
    await show_screen(context, chat_id, S.scr_plans(state, owner=owner, back="h" if chat_id == update.effective_user.id else "noop"))


async def cmd_help(update: Update, context: CTX) -> None:
    state = get_state(context)
    await show_screen(context, update.effective_chat.id, S.scr_help(state, _bot_username(context)))


def register(app) -> None:
    pm = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start))  # also handles in-group /start payload
    app.add_handler(CommandHandler(["about", "artist"], cmd_about, filters=pm))
    app.add_handler(CommandHandler(["courses", "classes"], cmd_courses, filters=pm))
    app.add_handler(CommandHandler(["contact", "support"], cmd_contact, filters=pm))
    app.add_handler(CommandHandler(["plan", "plans", "price", "prices", "fees"], cmd_plan))
    app.add_handler(CommandHandler("help", cmd_help, filters=pm))
