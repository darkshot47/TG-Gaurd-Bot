"""TG-Guard Bot — a fully customizable guard + course-management bot for Telegram.

Database: a private Telegram channel (no MongoDB / no external DB).
The whole state lives in a JSON snapshot document that is uploaded + pinned
inside that channel, plus a live human-readable event log (new users, groups,
setting changes...). On every restart the bot reads the snapshot back from
the channel before serving any update.
"""

__version__ = "1.0.0"
BOT_NAME = "TG-Guard"
