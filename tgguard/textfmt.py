"""Text formatting with {placeholders} -> Telegram message entities.

Why entities instead of parse_mode? Admins write their own texts. If we ran
those texts through Markdown/HTML parsing, a stray '_' or '<' would break the
message. Here the admin text is sent RAW and only our placeholders become
entities (e.g. {mention} becomes a real clickable/pinging user mention).

IMPORTANT: Telegram entity offsets are UTF-16 code units, not Python chars.
"""

from __future__ import annotations

import datetime as dtm
import re
from zoneinfo import ZoneInfo

from telegram import MessageEntity, User

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

PLACEHOLDER_HELP = (
    "{mention} {name} {firstname} {username} {id} "
    "{group} {title} {count} {date} {time} {botname}"
)


def u16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _tz(tzname: str) -> ZoneInfo:
    try:
        return ZoneInfo(tzname or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def user_full_name(user) -> str:
    if user is None:
        return ""
    full = (user.first_name or "").strip()
    if getattr(user, "last_name", None):
        full = f"{full} {user.last_name}".strip()
    return full or getattr(user, "username", None) or "User"


def format_placeholders(
    template: str,
    *,
    user=None,
    chat_title: str | None = None,
    count: int | None = None,
    bot_name: str | None = None,
    tz: str = "Asia/Kolkata",
    duration: str | None = None,
) -> tuple[str, list[MessageEntity]]:
    """Replace {placeholders}; returns (text, entities) ready for send_message()."""
    if not template:
        return "", []
    now = dtm.datetime.now(_tz(tz))
    out: list[str] = []
    entities: list[MessageEntity] = []
    pos = 0
    for m in PLACEHOLDER_RE.finditer(template):
        out.append(template[pos:m.start()])
        key = m.group(1).lower()
        rep = None
        if key == "mention" and user is not None:
            rep = user_full_name(user) or "User"
            entities.append(
                MessageEntity(
                    MessageEntity.TEXT_MENTION,
                    u16len("".join(out)),
                    u16len(rep),
                    user=User(user.id, user.first_name or rep, bool(user.is_bot)),
                )
            )
        elif key == "mention":
            rep = "there"
        elif key == "name":
            rep = user_full_name(user) if user else None
        elif key == "firstname":
            rep = (user.first_name if user else None) or None
        elif key == "lastname":
            rep = (getattr(user, "last_name", "") if user else "") or None
        elif key == "username":
            if user is not None:
                rep = f"@{user.username}" if user.username else user_full_name(user)
        elif key == "id":
            rep = str(user.id) if user else None
        elif key in ("group", "title"):
            rep = chat_title
        elif key == "count":
            rep = str(count) if count is not None else None
        elif key == "botname" or key == "bot":
            rep = bot_name
        elif key == "date":
            rep = now.strftime("%d %b %Y")
        elif key == "time":
            rep = now.strftime("%I:%M %p")
        elif key == "duration":
            rep = duration
        if rep is None:
            # Unknown / not-available placeholder: keep the literal {word}
            rep = m.group(0)
        out.append(rep)
        pos = m.end()
    out.append(template[pos:])
    return "".join(out), entities


def split_long(text: str, entities: list[MessageEntity] | None, limit: int = 4000):
    """Split (text, entities) into chunks <= limit UTF-16 units, shifting entities.

    Entities that straddle a split point are dropped for safety.
    """
    entities = entities or []
    if u16len(text) <= limit:
        return [(text, entities)]

    # map python char index -> utf16 offset
    u16_offsets = [0] * (len(text) + 1)
    acc = 0
    for i, ch in enumerate(text):
        u16_offsets[i] = acc
        acc += u16len(ch)
    u16_offsets[len(text)] = acc

    chunks = []
    start = 0  # python index
    while start < len(text):
        # find end python index so that u16 span <= limit
        end = start
        while end < len(text) and u16_offsets[end + 1] - u16_offsets[start] <= limit:
            end += 1
        if end == start:  # single char > limit? (impossible) guard
            end = start + 1
        # prefer newline boundary
        if end < len(text):
            nl = text.rfind("\n", start, end)
            if nl > start + (end - start) // 2:
                end = nl + 1
        seg = text[start:end]
        base = u16_offsets[start]
        seg_end = u16_offsets[min(end, len(text))]
        seg_entities = []
        for e in entities:
            if e.offset >= base and e.offset + e.length <= seg_end:
                seg_entities.append(
                    MessageEntity(e.type, e.offset - base, e.length, user=e.user, url=e.url, language=e.language)
                )
        chunks.append((seg, seg_entities))
        start = end
    return chunks


def truncate(text: str, limit: int = 3900) -> str:
    if u16len(text) <= limit:
        return text
    out = []
    total = 0
    for ch in text:
        w = u16len(ch)
        if total + w > limit - 3:
            break
        out.append(ch)
        total += w
    return "".join(out) + "..."


URL_RE = re.compile(r"(?:https?://|www\.|t\.me/|telegram\.me/|telegra\.ph/)", re.IGNORECASE)


def has_link(text: str, entities=None) -> bool:
    """True when a message carries a hyperlink (entity or raw pattern)."""
    for e in entities or []:
        if e.type in (MessageEntity.URL, MessageEntity.TEXT_LINK):
            return True
    return bool(text and URL_RE.search(text))
