"""Environment configuration.

Everything the bot needs comes from Render environment variables:

    BOT_TOKEN        - from @BotFather                      (required)
    DB_CHANNEL       - private channel id (-100...) or @name (required)
    OWNER_ID         - your friend's Telegram user id        (required)
    EXTRA_ADMINS     - optional, comma separated user ids
    TIMEZONE         - optional, default Asia/Kolkata
    SNAPSHOT_EVERY   - optional, seconds between DB saves, default 240
    LOG_USERS        - optional, "1"/"0" post every new user to DB channel, default 1
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("tgguard.config")


class ConfigError(RuntimeError):
    pass


def _int_env(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:  # pragma: no cover
        raise ConfigError(f"Env var {name} must be an integer, got {raw!r}") from e


class Config:
    def __init__(self) -> None:
        self.token = os.getenv("BOT_TOKEN", "").strip()
        if not self.token:
            raise ConfigError("BOT_TOKEN env var is missing (get it from @BotFather).")

        raw_channel = os.getenv("DB_CHANNEL", "").strip()
        if not raw_channel:
            raise ConfigError(
                "DB_CHANNEL env var is missing. Create a PRIVATE channel, add the bot "
                "as admin (all rights) and put the channel id (-100xxxxxxxxxx) here."
            )
        self.db_channel: int | str
        if raw_channel.startswith("@"):
            self.db_channel = raw_channel
        else:
            try:
                self.db_channel = int(raw_channel)
            except ValueError as e:
                raise ConfigError(
                    f"DB_CHANNEL must be a numeric id like -1001234567890 or @username, got {raw_channel!r}"
                ) from e

        self.owner_id = _int_env("OWNER_ID")
        if not self.owner_id:
            raise ConfigError("OWNER_ID env var is missing (the bot owner's Telegram user id).")

        self.extra_admins: set[int] = set()
        for chunk in os.getenv("EXTRA_ADMINS", "").replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk:
                try:
                    self.extra_admins.add(int(chunk))
                except ValueError:
                    log.warning("Ignoring bad EXTRA_ADMINS entry: %r", chunk)

        self.timezone = os.getenv("TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"
        self.snapshot_every = max(60, _int_env("SNAPSHOT_EVERY", 240) or 240)
        self.log_users = os.getenv("LOG_USERS", "1").strip().lower() not in ("0", "false", "no")

    # ------------------------------------------------------------------ #
    def is_owner(self, user_id: int | None) -> bool:
        return user_id is not None and (user_id == self.owner_id or user_id in self.extra_admins)


CFG = None  # set in bot.py after validation


def load() -> Config:
    global CFG
    CFG = Config()
    return CFG
