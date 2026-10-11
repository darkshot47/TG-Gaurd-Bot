"""ChannelDB — the private Telegram channel IS the database.

How it works (no MongoDB, no files, nothing external):

* SAVE   — the whole state is serialized to `tgguard_db.json` and uploaded to
           the DB channel as a document, then PINNED. The channel description
           stores a fallback pointer (`snap=<message_id>`). Old snapshots are
           deleted after we keep KEEP_SNAPSHOTS of them.
* LOAD   — on boot: get_chat(DB channel) -> pinned_message -> download the
           document -> parse JSON. If the pin is missing, the description
           pointer is used (the message is fetched via forward_message by id,
           which bots are allowed to do in channels they administer).
* LOG    — human-readable events (new users, new groups, setting changes,
           bans...) are posted to the channel in real time, so the channel is
           both a live audit log and a visible member list.
"""

from __future__ import annotations

import logging
import re
from io import BytesIO

from telegram import Bot, Message
from telegram.error import BadRequest, Forbidden, TelegramError

from .config import Config
from .state import State
from .timeutil import fmt_ts, now_ts

log = logging.getLogger("tgguard.db")

SNAP_FILENAME = "tgguard_db.json"
SNAP_MARK = "TGGB#SNAP"
KEEP_SNAPSHOTS = 5
POINTER_RE = re.compile(r"snap=(\d+)")


class ChannelDB:
    def __init__(self, bot: Bot, cfg: Config, state: State):
        self.bot = bot
        self.cfg = cfg
        self.state = state
        self.channel = cfg.db_channel
        self.last_save = 0.0
        self.last_audit_err = 0.0
        self.chat = None  # ChatFullInfo after startup
        self.force_next = False

    # ------------------------------------------------------------------ #
    # BOOT
    # ------------------------------------------------------------------ #
    async def startup(self) -> tuple[bytes | None, str | None]:
        """Returns (json_blob or None, source) — source in {pinned, pointer}."""
        self.chat = await self.bot.get_chat(self.channel)  # ChatFullInfo

        me = await self.bot.get_me()
        mem = await self.bot.get_chat_member(self.channel, me.id)
        if mem.status != "administrator":
            raise RuntimeError(
                f"The bot is not an ADMIN of the DB channel ({self.channel}). "
                "Add it as admin with ALL rights (post, pin, delete, change info)."
            )
        missing = [
            r
            for r, ok in (
                ("post messages", mem.can_post_messages),
                ("pin messages", mem.can_pin_messages),
                ("delete messages", mem.can_delete_messages),
                ("edit info/description", mem.can_change_info),
            )
            if not ok
        ]
        if missing:
            log.warning("DB channel admin rights missing: %s", ", ".join(missing))

        blob, source = await self._load_from_pin()
        if blob is None:
            blob, source = await self._load_from_pointer()
        return blob, source

    async def _download_doc(self, msg: Message) -> bytes | None:
        if not msg or not msg.document:
            return None
        try:
            f = await self.bot.get_file(msg.document.file_id)
            return bytes(await f.download_as_bytearray())
        except TelegramError as e:
            log.error("DB file download failed: %s", e)
            return None

    async def _load_from_pin(self) -> tuple[bytes | None, str | None]:
        pm = getattr(self.chat, "pinned_message", None)
        if pm and pm.document and (
            pm.document.file_name == SNAP_FILENAME or (pm.caption or "").startswith(SNAP_MARK)
        ):
            blob = await self._download_doc(pm)
            if blob:
                return blob, "pinned"
        return None, None

    async def _load_from_pointer(self) -> tuple[bytes | None, str | None]:
        desc = getattr(self.chat, "description", None) or ""
        m = POINTER_RE.search(desc)
        if not m:
            return None, None
        try:
            fwd = await self.bot.forward_message(self.channel, from_chat_id=self.channel, message_id=int(m.group(1)))
        except TelegramError as e:
            log.warning("DB pointer message %s not fetchable: %s", m.group(1), e)
            return None, None
        blob = await self._download_doc(fwd)
        try:
            await fwd.delete()
        except TelegramError:
            pass
        return (blob, "pointer") if blob else (None, None)

    # ------------------------------------------------------------------ #
    # SAVE
    # ------------------------------------------------------------------ #
    def request_save(self) -> None:
        """Ask for a near-immediate save on the next tick."""
        self.force_next = True
        self.state.changed()

    async def save_snapshot(self, force: bool = False) -> bool:
        if not force and not self.state.dirty:
            return False
        data = self.state.data
        data["seq"] = int(data.get("seq", 0)) + 1
        data["saved_at"] = now_ts()
        if self.bot.username:
            data["bot_username"] = self.bot.username
        blob = self.state.to_json()
        caption = (
            f"{SNAP_MARK} v{data['seq']} | users {len(data.get('users', {}))} | "
            f"groups {len(data.get('groups', {}))} | {fmt_ts(data['saved_at'], self.state.tz)}"
        )
        try:
            sent = await self.bot.send_document(
                self.channel,
                document=BytesIO(blob),
                filename=SNAP_FILENAME,
                caption=caption,
                disable_notification=True,
            )
        except TelegramError as e:
            log.error("Snapshot upload FAILED: %s", e)
            self.state.changed()  # stay dirty, retry next tick
            await self._audit_error(f"❌ DB snapshot upload failed: {e}")
            return False

        # unpin previous snapshot, pin the new one
        prev = data.get("pinned") or 0
        try:
            await self.bot.pin_chat_message(self.channel, sent.message_id, disable_notification=True)
            if prev and prev != sent.message_id:
                await self.bot.unpin_chat_message(self.channel, message_id=prev)
            data["pinned"] = sent.message_id
        except TelegramError as e:
            log.error("Pin failed: %s", e)

        # fallback pointer in the channel description
        try:
            await self.bot.set_chat_description(
                self.channel, f"{SNAP_MARK} snap={sent.message_id} ts={now_ts()} v{data['seq']}"
            )
        except TelegramError as e:
            log.warning("Could not set description pointer: %s", e)

        # keep only the last KEEP_SNAPSHOTS documents
        hist = data.setdefault("snap_history", [])
        hist.append(sent.message_id)
        while len(hist) > KEEP_SNAPSHOTS:
            old = hist.pop(0)
            try:
                await self.bot.delete_message(self.channel, old)
            except TelegramError:
                pass

        self.state.dirty = False
        self.force_next = False
        self.last_save = now_ts()
        log.info("Snapshot v%s saved (%d bytes, %d users, %d groups)",
                 data["seq"], len(blob), len(data.get("users", {})), len(data.get("groups", {})))
        return True

    async def tick(self) -> None:
        """Called every minute by the job queue: throttled auto-save."""
        try:
            if self.force_next or (self.state.dirty and now_ts() - self.last_save >= self.cfg.snapshot_every):
                await self.save_snapshot()
        except Exception:  # pragma: no cover - defensive
            log.exception("db tick error")

    # ------------------------------------------------------------------ #
    # LIVE AUDIT LOG (visible in the channel)
    # ------------------------------------------------------------------ #
    async def audit(self, text: str) -> None:
        try:
            await self.bot.send_message(self.channel, text, disable_notification=True)
        except Forbidden:
            log.error("Lost access to DB channel (Forbidden). Check bot admin rights!")
        except TelegramError as e:
            # avoid error spam if channel is broken
            if now_ts() - self.last_audit_err > 300:
                log.error("Audit post failed: %s", e)
                self.last_audit_err = now_ts()

    async def _audit_error(self, text: str) -> None:
        # max one error DM per 30 minutes (no spam if the channel is broken)
        now = now_ts()
        if now - getattr(self, "_last_err_dm", 0) < 1800:
            return
        self._last_err_dm = now
        try:
            await self.bot.send_message(self.cfg.owner_id, text)
        except TelegramError:
            pass

    async def audit_new_user(self, rec: dict, number: int) -> None:
        uname = f" (@{rec['user']})" if rec.get("user") else ""
        await self.audit(
            f"👤 NEW USER #{number}\n"
            f"Name : {rec.get('first', '')} {rec.get('last', '')}\n"
            f"User : {uname or '—'}\n"
            f"ID   : {rec['id']}\n"
            f"When : {fmt_ts(rec.get('joined'), self.state.tz)}"
        )

    async def audit_unblocked(self, rec: dict) -> None:
        await self.audit(f"♻️ User unblocked the bot again: {rec.get('first', '')} — ID {rec['id']}")

    async def audit_blocked(self, rec: dict) -> None:
        await self.audit(f"🚫 User blocked the bot: {rec.get('first', '')} — ID {rec['id']}")

    async def audit_group(self, g: dict, removed: bool = False, by: str = "") -> None:
        if removed:
            await self.audit(f"❌ Bot REMOVED from group: {g.get('title')} ({g['id']})")
        else:
            await self.audit(f"🛡 Bot ADDED to group: {g.get('title')} ({g['id']}){(' by ' + by) if by else ''}")

    async def audit_setting(self, path: str, by_name: str, value_preview: str = "") -> None:
        preview = (value_preview or "").replace("\n", " ")[:120]
        await self.audit(f"⚙️ SETTING CHANGED by {by_name}\nPath : {path}\nValue: {preview or '(empty)'}")

    async def audit_action(self, text: str) -> None:
        await self.audit(f"🛡 {text}")

    async def store_media(self, message: Message) -> int | None:
        """Forward a media message into the DB channel (backup copy); returns its id."""
        try:
            fwd = await self.bot.forward_message(
                self.channel, from_chat_id=message.chat_id, message_id=message.message_id, disable_notification=True
            )
            return fwd.message_id
        except (BadRequest, TelegramError) as e:
            log.warning("store_media failed: %s", e)
            return None
