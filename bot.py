#!/usr/bin/env python3
"""TG-Guard Bot — entrypoint.

Boot sequence:
  1. read env (BOT_TOKEN, DB_CHANNEL, OWNER_ID, ...)
  2. DOWNLOAD the database snapshot from the pinned document in the private
     DB channel (the channel IS the database)
  3. re-arm scheduled posts, start the auto-save job
  4. serve updates — every change is written back to the channel
  5. on shutdown (Render redeploy = SIGTERM): force-save the snapshot first
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

from telegram import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.error import TelegramError
from telegram.ext import Application, MessageHandler, filters

from tgguard import __version__, config
from tgguard.db import ChannelDB
from tgguard.handlers import callbacks, common, engine, group_cmds, pm_owner, pm_user
from tgguard.state import State
from tgguard.timeutil import fmt_ts, now_ts

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("tgguard.main")

USER_CMDS = [
    BotCommand("start", "Main menu"),
    BotCommand("plan", "Course plans & fees"),
    BotCommand("about", "About the artist"),
    BotCommand("courses", "Courses offered"),
    BotCommand("contact", "Contact admin"),
    BotCommand("rules", "Group rules"),
    BotCommand("help", "Help"),
]
OWNER_CMDS = [
    BotCommand("panel", "Owner panel (buttons)"),
    BotCommand("users", "Everyone who started the bot"),
    BotCommand("msg", "P2P message a user: /msg <id> <text>"),
    BotCommand("broadcast", "Broadcast to all users"),
    BotCommand("busers", "Users who blocked the bot"),
    BotCommand("stats", "Bot & group statistics"),
    BotCommand("edit", "Reply to a bot message + /edit <new text>"),
    BotCommand("setwelcome", "PM welcome text ({mention} works)"),
    BotCommand("gwelcome", "Default group welcome text"),
    BotCommand("setabout", "About text"),
    BotCommand("setcourses", "Courses text"),
    BotCommand("setcontact", "Contact text"),
    BotCommand("setrules", "Default rules text"),
    BotCommand("rmsg", "Reply shown when someone reports an ADMIN"),
    BotCommand("plans", "Manage plans & prices"),
    BotCommand("setprice", "Manage plans & prices"),
    BotCommand("addplan", "Add a plan: /addplan Name | ₹499 | details"),
    BotCommand("tz", "Timezone for schedules"),
    BotCommand("backup", "Download the database JSON"),
    BotCommand("restore", "Restore the database from a JSON file"),
    BotCommand("id", "Show your Telegram id"),
]
GROUP_CMDS = [
    BotCommand("panel", "Open the coloured-button control panel"),
    BotCommand("gw", "Group welcome: /gw <text> | /gw on|off"),
    BotCommand("filter", "Keyword auto-reply: /filter hello <text> (or reply to media)"),
    BotCommand("filters", "List keyword filters"),
    BotCommand("stopfilter", "Remove a filter: /stopfilter hello"),
    BotCommand("badword", "Auto-mute word: /badword <word> [2h]"),
    BotCommand("badwords", "List bad words"),
    BotCommand("delbadword", "Remove a bad word"),
    BotCommand("linkguard", "Link guard: /linkguard on|off"),
    BotCommand("linkmsg", "Custom link-deleted notice"),
    BotCommand("schedule", "Timed post: /schedule 8:00 PM <text>"),
    BotCommand("schedules", "List scheduled posts"),
    BotCommand("delschedule", "Remove a scheduled post"),
    BotCommand("warn", "Warn a user (reply)"),
    BotCommand("unwarn", "Remove a warn (reply)"),
    BotCommand("warns", "Show warns (reply)"),
    BotCommand("warnlimit", "Warns before action: /warnlimit 3"),
    BotCommand("warnaction", "warnaction mute|ban"),
    BotCommand("lock", "Lock the group (only admins can talk)"),
    BotCommand("unlock", "Unlock the group"),
    BotCommand("verify", "Captcha on join: /verify on|off"),
    BotCommand("verifytime", "Verify timeout minutes"),
    BotCommand("flood", "Anti-flood: /flood on|off"),
    BotCommand("rules", "Show rules"),
    BotCommand("setrules", "Set group rules"),
    BotCommand("report", "Reply to a message to report it to the admin"),
    BotCommand("gstats", "This group's stats"),
    BotCommand("gid", "Show chat/user ids"),
]


async def _set_commands(app: Application) -> None:
    cfg = config.CFG
    try:
        await app.bot.set_my_commands(USER_CMDS, scope=BotCommandScopeDefault())
        await app.bot.set_my_commands(GROUP_CMDS, scope=BotCommandScopeAllGroupChats())
        for oid in [cfg.owner_id, *cfg.extra_admins]:
            await app.bot.set_my_commands(OWNER_CMDS + USER_CMDS, scope=BotCommandScopeChat(oid))
    except TelegramError as e:
        log.warning("set_my_commands failed: %s", e)


async def post_init(app: Application) -> None:
    cfg = config.CFG
    log.info("TG-Guard v%s booting — loading database from channel %s ...", __version__, cfg.db_channel)

    db = ChannelDB(app.bot, cfg, State(tz=cfg.timezone))  # temp state, replaced below
    blob, source = None, None
    try:
        blob, source = await db.startup()
    except Exception as e:
        log.critical("Could not access the DB channel: %s", e)
        try:
            await app.bot.send_message(
                cfg.owner_id,
                f"❌ TG-Guard could not access the database channel!\n\n{e}\n\n"
                "Fix: create a PRIVATE channel → add the bot as ADMIN with all rights → "
                "set DB_CHANNEL to the channel id (-100…) → redeploy.",
            )
        except TelegramError:
            pass

    if blob:
        try:
            state = State.from_json(blob, tz=cfg.timezone)
            log.info("Database restored from %s snapshot v%s — %d users, %d groups.",
                     source, state.data.get("seq"), len(state.users), len(state.active_groups()))
        except Exception as e:
            log.error("Snapshot corrupt (%s) — starting fresh.", e)
            state = State(tz=cfg.timezone)
            source = None
    else:
        state = State(tz=cfg.timezone)

    db.state = state
    app.bot_data.update({"state": state, "db": db, "cfg": cfg, "started": time.time()})

    me = await app.bot.get_me()
    state.data["bot_username"] = me.username or ""
    log.info("Logged in as @%s (id %s)", me.username, me.id)

    if source:
        await db.audit(
            f"🔄 BOT RESTARTED — database restored from channel ({source} snapshot v{state.data.get('seq', 0)}).\n"
            f"    👥 {len(state.users)} users • 🛡 {len(state.active_groups())} groups • {fmt_ts(now_ts(), state.tz)}"
        )
    else:
        await db.audit("🆕 BOT STARTED with a FRESH database (no snapshot found in this channel yet).")

    # re-arm scheduled posts from the restored database
    try:
        await engine.rebuild_all_schedules(app)
    except Exception:
        log.exception("schedule rebuild failed")

    # periodic jobs: autosave + housekeeping
    if app.job_queue:
        app.job_queue.run_repeating(db_tick, 60, first=45, name="dbtick")
        app.job_queue.run_repeating(housekeeping, 900, first=300, name="house")

    await _set_commands(app)

    # first snapshot if we booted fresh or anything changed during boot
    if state.dirty:
        await db.save_snapshot(force=True)

    try:
        await app.bot.send_message(
            cfg.owner_id,
            f"🟢 TG-Guard v{__version__} is ONLINE.\n"
            f"💾 Database: {'restored from channel (' + str(source) + ')' if source else 'fresh (will be saved to your channel)'}\n"
            f"👥 {len(state.users)} users • 🛡 {len(state.active_groups())} groups • 💰 {len(state.plans())} plans\n"
            f"🕘 Timezone: {state.tz}\n\nSend /panel to open the owner panel.",
        )
    except TelegramError as e:
        log.warning("Could not DM the owner (OWNER_ID=%s): %s — press /start on the bot once!", cfg.owner_id, e)


async def db_tick(context) -> None:
    db: ChannelDB = context.bot_data.get("db")
    if db:
        await db.tick()


async def housekeeping(context) -> None:
    """Prune caches, expire reports, refresh group member counts (for {count})."""
    state: State = context.bot_data.get("state")
    if not state:
        return
    state.prune_reports()
    for key in ("throttle", "wdedup", "verok"):
        store = context.bot_data.get(key)
        if isinstance(store, dict) and len(store) > 5000:
            store.clear()
    flood = context.bot_data.get("flood")
    if isinstance(flood, dict) and len(flood) > 5000:
        flood.clear()
    # refresh member counts of up to 5 active groups (rotating)
    rot = context.bot_data.setdefault("mc_rot", 0)
    groups = state.active_groups()
    for g in groups[rot % max(1, len(groups)) :][:5] if groups else []:
        try:
            await common.fetch_members(context, int(g["id"]))
        except Exception:
            pass
    context.bot_data["mc_rot"] = (rot + 5) % max(1, len(groups) or 1)


async def post_shutdown(app: Application) -> None:
    db: ChannelDB = app.bot_data.get("db")
    if not db:
        return
    log.info("Shutting down — saving database snapshot to the channel...")
    try:
        await db.save_snapshot(force=True)
        await db.audit(f"🔴 Bot stopped (snapshot v{db.state.data.get('seq', 0)} saved) — {fmt_ts(now_ts(), db.state.tz)}")
    except Exception:
        log.exception("shutdown save failed")


async def on_error(update: object, context) -> None:
    log.error("Unhandled exception while handling update %s", update, exc_info=context.error)
    last = context.bot_data.get("last_err_notified", 0)
    if time.time() - last > 300:
        context.bot_data["last_err_notified"] = time.time()
        try:
            await context.bot.send_message(
                config.CFG.owner_id,
                f"⚠️ TG-Guard error (details in Render logs):\n{str(context.error)[:400]}",
            )
        except TelegramError:
            pass


def _start_health_server() -> None:
    """If deployed as a WEB service (Render injects PORT), serve a tiny health
    endpoint so free plans can be kept awake with an uptime pinger
    (UptimeRobot / cron-job.org, ping every 5-10 minutes).
    Background workers simply have no PORT and this does nothing."""
    port_s = os.getenv("PORT", "").strip()
    if not port_s.isdigit():
        return
    port = int(port_s)

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _H(BaseHTTPRequestHandler):
        def _ok(self, body: bool):
            data = "🛡 TG-Guard bot is running ✅\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            self._ok(True)

        def do_HEAD(self):  # noqa: N802
            self._ok(False)

        def log_message(self, *args):
            pass

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), _H)
    except OSError as e:
        log.warning("Health server could not bind port %s: %s", port, e)
        return
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("Health server on 0.0.0.0:%s (ping this URL every 5-10 min to stay awake on free plans)", port)


def main() -> None:
    cfg = config.load()
    _start_health_server()
    app = (
        Application.builder()
        .token(cfg.token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_error_handler(on_error)

    # 1) flow engine first (private chat, consumes messages while a flow is active)
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, common.flow_entry), group=-2)

    # 2) everything else
    pm_user.register(app)
    pm_owner.register(app)
    group_cmds.register(app)
    engine.register(app)
    callbacks.register(app)

    log.info("Starting polling...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        stop_signals=(signal.SIGINT, signal.SIGTERM),
        close_loop=False,
    )


if __name__ == "__main__":
    main()
