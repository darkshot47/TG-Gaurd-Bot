"""Inline keyboard helpers with Telegram's native coloured button styles.

Bot API update (9 Feb 2026) added `style` to InlineKeyboardButton /
KeyboardButton:
    primary -> blue   (navigation / info)
    success -> green  (positive actions: save, enable, add, verify)
    danger  -> red    (destructive: ban, delete, disable, cancel)

Old Telegram clients simply ignore the style — buttons still work.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

BLUE = "primary"
GREEN = "success"
RED = "danger"


def cbtn(text: str, cb: str | None = None, url: str | None = None, color: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb, url=url, style=color)


def kboard(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([r for r in rows if r])


def back_row(cb: str = "om", label: str = "🔙 Back", color: str = BLUE) -> list[InlineKeyboardButton]:
    return [cbtn(label, cb, color=color)]


def close_row(cb: str = "cl") -> list[InlineKeyboardButton]:
    return [cbtn("✖️ Close", cb, color=RED)]


def nav_rows(prefix: str, page: int, npages: int, back: str | None = None) -> list[list[InlineKeyboardButton]]:
    """Prev/Next pagination row (only when needed)."""
    rows = []
    if npages > 1:
        row = []
        if page > 0:
            row.append(cbtn("⬅️ Prev", f"{prefix}:{page - 1}", color=BLUE))
        row.append(cbtn(f"📄 {page + 1}/{npages}", "noop"))
        if page < npages - 1:
            row.append(cbtn("Next ➡️", f"{prefix}:{page + 1}", color=BLUE))
        rows.append(row)
    if back:
        rows.append(back_row(back))
    return rows


def add_to_group_url(bot_username: str) -> str:
    """Deep link that adds the bot to a group WITH the admin rights it needs.

    https://core.telegram.org/api/links  ->  ?startgroup=<param>&admin=<rights>
    """
    rights = "+".join(
        [
            "delete_messages",
            "restrict_members",
            "invite_users",
            "pin_messages",
            "change_info",
        ]
    )
    return f"https://t.me/{bot_username}?startgroup=tgguard&admin={rights}"
