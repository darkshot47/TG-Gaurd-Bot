"""Screen builders shared by commands and callback buttons.

Every screen is a tuple (text, entities, InlineKeyboardMarkup) so the exact
same screen can be sent by a /command or rendered by a coloured button.
"""

from __future__ import annotations

import re

from ..keyboards import BLUE, GREEN, RED, add_to_group_url, back_row, cbtn, kboard, nav_rows
from ..state import DEFAULT_SETTINGS, State
from ..textfmt import PLACEHOLDER_HELP, format_placeholders, truncate
from ..timeutil import fmt_ts

# ---------------------------------------------------------------------- #
# editable setting registry (labels shown in menus / views)
# ---------------------------------------------------------------------- #
SETTING_LABELS = {
    "settings.welcome_pm": "👋 PM Welcome Message",
    "settings.welcome_group": "🎉 Default Group Welcome",
    "settings.about": "🎙️ About (the artist)",
    "settings.courses": "📚 Courses Text",
    "settings.contact": "📞 Contact Text",
    "settings.rules": "📜 Default Rules",
    "settings.rmsg": "🚨 Admin-Report Reply (/rmsg)",
    "settings.link_msg": "🔗 Link-Guard Notice",
    "settings.badword_msg": "🤬 Bad-Word Mute Notice",
    "settings.verify_msg": "🧪 Verify/Captcha Message",
    "settings.flood_msg": "🌊 Anti-Flood Notice",
}

SETTING_ORDER = [
    "settings.welcome_pm",
    "settings.welcome_group",
    "settings.about",
    "settings.courses",
    "settings.contact",
    "settings.rules",
    "settings.rmsg",
    "settings.link_msg",
    "settings.badword_msg",
    "settings.verify_msg",
    "settings.flood_msg",
]

GROUP_SUB_LABELS = [
    ("welcome.text", "👋 Group Welcome"),
    ("rules", "📜 Group Rules"),
    ("linkguard.msg", "🔗 Link-Guard Notice"),
    ("verify.msg", "🧪 Verify Message"),
    ("warns.mute_min", "⚠️ Warn-Mute Minutes"),
    ("warns.limit", "⚠️ Warn Limit"),
    ("flood.limit", "🌊 Flood Message Limit"),
    ("flood.window", "🌊 Flood Window (sec)"),
    ("flood.mute_min", "🌊 Flood Mute (min)"),
    ("verify.timeout_min", "🧪 Verify Timeout (min)"),
]

INT_LEAVES = {
    "mute_min",
    "timeout_min",
    "limit",
    "window",
}


def label_for(state: State, path: str) -> str:
    if path in SETTING_LABELS:
        return SETTING_LABELS[path]
    m = re.match(r"^groups\.(-?\d+)\.(.+)$", path)
    if m:
        gid, sub = int(m.group(1)), m.group(2)
        g = state.get_group(gid)
        title = (g or {}).get("title") or str(gid)
        for pref, lab in GROUP_SUB_LABELS:
            if sub.startswith(pref):
                return f"{lab} — {truncate(title, 24)}"
        fm = re.match(r"(filters|badwords|schedules)\[([^\]]+)\]\.(.+)", sub)
        if fm:
            return f"✏️ {fm.group(1)} item ({fm.group(3)}) — {truncate(title, 20)}"
    return path


def parent_of(path: str) -> str:
    """Callback data of the screen a path belongs to (used after edits/toggles)."""
    if path.startswith("settings."):
        return "sm"
    m = re.match(r"^groups\.(-?\d+)\.(.+)$", path)
    if not m:
        return "om"
    gid, sub = m.group(1), m.group(2)
    if sub.startswith("welcome"):
        return f"gw:{gid}"
    if sub.startswith("rules"):
        return f"gru:{gid}"
    if sub.startswith("linkguard"):
        return f"gl:{gid}"
    if sub.startswith("verify"):
        return f"gv:{gid}"
    if sub.startswith("warns"):
        return f"gwa:{gid}"
    if sub.startswith("flood"):
        return f"gfl:{gid}"
    if sub.startswith("filters"):
        return f"gf:{gid}"
    if sub.startswith("badwords"):
        return f"gb:{gid}"
    if sub.startswith("schedules"):
        return f"gs:{gid}"
    return f"gp:{gid}"


def is_int_path(path: str) -> bool:
    return path.rsplit(".", 1)[-1].split("[")[0] in INT_LEAVES


def default_for(path: str):
    """Default value for reset (None => 'use global default' for group paths)."""
    if path.startswith("settings."):
        key = path.split(".", 1)[1]
        return DEFAULT_SETTINGS.get(key, "")
    return None


def _sv(path: str, label: str, color: str = BLUE):
    return cbtn(label, f"sv:{path}", color=color)


# ---------------------------------------------------------------------- #
# user-facing screens
# ---------------------------------------------------------------------- #
def scr_home(state: State, user, bot_username: str, owner: bool):
    text, ents = format_placeholders(
        state.setting("welcome_pm", "Welcome {mention}!"),
        user=user,
        bot_name=f"@{bot_username}" if bot_username else "the bot",
        tz=state.tz,
    )
    rows = []
    if owner:
        rows.append([cbtn("🛡 Owner Panel", "om", color=GREEN)])
        rows.append(
            [
                cbtn("👥 Users", "us:0", color=BLUE),
                cbtn("📊 Stats", "st", color=BLUE),
                cbtn("⚙️ Settings", "sm", color=BLUE),
            ]
        )
    rows += [
        [cbtn("🎙️ About", "ab", color=BLUE), cbtn("📚 Courses", "cs", color=BLUE), cbtn("📞 Contact", "co", color=BLUE)],
        [cbtn("💰 Plans & Fees", "pl", color=GREEN), cbtn("❓ Help", "he", color=BLUE)],
        [cbtn("🛡 Add me to your Group", url=add_to_group_url(bot_username), color=GREEN)],
    ]
    return text, ents, kboard(rows)


def scr_simple(state: State, key: str, heading: str, back: str = "h"):
    text = state.setting(key, "") or "(not set yet)"
    rows = []
    if key == "courses":
        rows.append([cbtn("💰 Plans & Fees", "pl", color=GREEN)])
    rows.append(back_row(back, "🔙 Home"))
    return f"{heading}\n{'─' * 26}\n{text}", None, kboard(rows)


def scr_plans(state: State, owner: bool = False, back: str = "h"):
    text = state.format_plans()
    rows = []
    if owner:
        rows.append([cbtn("🛠 Manage Plans", "plm", color=BLUE)])
    rows.append(back_row(back))
    return text, None, kboard(rows)


def scr_help(state: State, bot_username: str):
    text = (
        "❓ HELP — what can I do?\n"
        "─────────────────────────\n"
        "👤 In this PM:\n"
        "  /start — main menu\n"
        "  /plan — course plans & fees\n"
        "  /about • /courses • /contact\n"
        "  /help — this message\n\n"
        "🛡 In a group (admins):\n"
        "  /gw — group welcome message\n"
        "  /filter <word> — auto-reply triggers\n"
        "  /badword <word> [mins] — mute on bad words\n"
        "  /linkguard on|off — delete links\n"
        "  /schedule 8:00 PM <text> — timed posts\n"
        "  /warn • /unwarn • /warns (reply to a user)\n"
        "  /lock • /unlock • /verify • /flood\n"
        "  /rules • /setrules • /id • /panel\n\n"
        "🙋 Everyone in a group:\n"
        "  /report — reply to a message to report it to the admin\n\n"
        "➕ Add me to your group with the green button below — "
        "I'll ask for the admin rights I need automatically."
    )
    rows = [
        [cbtn("🛡 Add me to your Group", url=add_to_group_url(bot_username), color=GREEN)],
        back_row("h", "🔙 Home"),
    ]
    return text, None, kboard(rows)


# ---------------------------------------------------------------------- #
# owner screens
# ---------------------------------------------------------------------- #
def scr_owner_menu(state: State):
    users = len(state.users)
    blocked = len(state.users_sorted(blocked=True))
    groups = len(state.active_groups())
    text = (
        "🛡 OWNER PANEL\n"
        "─────────────────────────\n"
        f"👥 Users: {users}   🚫 Blocked: {blocked}\n"
        f"🛡 Groups: {groups}   💰 Plans: {len(state.plans())}\n"
        f"💾 DB snapshot: v{state.data.get('seq', 0)} • {fmt_ts(state.data.get('saved_at'), state.tz)}\n\n"
        "Everything below works with buttons — and every change is saved "
        "into your database channel automatically."
    )
    rows = [
        [cbtn("👥 Users", "us:0", color=BLUE), cbtn(f"🚫 Blocked ({blocked})", "bu:0", color=BLUE)],
        [cbtn("📊 Stats", "st", color=BLUE), cbtn("✉️ Send Message", "ms", color=BLUE)],
        [cbtn("📢 Broadcast", "bc", color=GREEN), cbtn("⚙️ Editable Texts", "sm", color=BLUE)],
        [cbtn("💰 Plans & Prices", "plm", color=BLUE), cbtn(f"🛡 Groups ({groups})", "ge", color=BLUE)],
        [cbtn("💾 Backup", "bk", color=BLUE), cbtn("🔄 Restore", "rstr", color=RED)],
        [cbtn("🕘 Timezone", "tz", color=BLUE), cbtn("🏠 Home", "h", color=BLUE)],
        close_row_(),
    ]
    return text, None, kboard(rows)


def close_row_():
    return [cbtn("✖️ Close", "cl", color=RED)]


def scr_users(state: State, page: int = 0, blocked_only: bool = False):
    items = state.users_sorted(blocked=True if blocked_only else None)
    from ..timeutil import now_ts

    day = now_ts() - 86400
    week = now_ts() - 7 * 86400
    new24 = sum(1 for u in state.users.values() if int(u.get("joined") or 0) >= day)
    new7 = sum(1 for u in state.users.values() if int(u.get("joined") or 0) >= week)
    head = (
        "🚫 BLOCKED USERS (they blocked the bot)"
        if blocked_only
        else "👥 USERS — everyone who pressed /start"
    )
    text = f"{head}\n─────────────────────────\n"
    if not blocked_only:
        text += f"Total: {len(state.users)} • new 24h: {new24} • new 7d: {new7}\n"
    text += f"Showing page {min(page + 1, max(1, (len(items) + 7) // 8))} — tap a user for details.\n"
    rows = []
    prefix = "bu" if blocked_only else "us"
    if not items:
        text += "\n(empty)"
    else:
        from .common import paginate

        chunk, page, npages = paginate(items, page, 8)
        for u in chunk:
            mark = "🚫 " if u.get("blocked") else "👤 "
            name = truncate((u.get("first") or "Unknown") + (f" @{u['user']}" if u.get("user") else ""), 26)
            rows.append([cbtn(f"{mark}{name}", f"up:{u['id']}:{prefix}:{page}", color=RED if u.get("blocked") else BLUE)])
        text = text.rstrip() + f"\n\n{len(items)} total"
        rows += nav_rows(prefix, page, npages)
    rows.append(back_row("om", "🔙 Owner Panel"))
    return text, None, kboard(rows)


def scr_user_profile(state: State, uid: int, back: str = "us", page: int = 0):
    u = state.get_user(uid)
    if not u:
        return "👤 User not in database (they may have been removed by a restore).", None, kboard([back_row(f"{back}:{page}")])
    uname = f"@{u['user']}" if u.get("user") else "—"
    text = (
        "👤 USER PROFILE\n"
        "─────────────────────────\n"
        f"Name    : {(u.get('first') or '')} {(u.get('last') or '')}\n"
        f"Username: {uname}\n"
        f"ID      : {u['id']}\n"
        f"Joined  : {fmt_ts(u.get('joined'), state.tz)}\n"
        f"Last seen: {fmt_ts(u.get('seen'), state.tz)}\n"
        f"Msgs to bot: {u.get('msgs', 0)}\n"
        f"Status  : {'🚫 BLOCKED the bot' if u.get('blocked') else '✅ Active'}"
    )
    rows = [
        [cbtn("✉️ Send Message", f"um:{uid}", color=GREEN), cbtn("🔗 Open Profile", url=f"tg://user?id={uid}", color=BLUE)],
    ]
    if u.get("blocked"):
        rows.append([cbtn("♻️ Mark Active", f"ub:{uid}:{back}:{page}", color=GREEN)])
    else:
        rows.append([cbtn("🚫 Mark Blocked", f"ub:{uid}:{back}:{page}", color=RED)])
    rows.append(back_row(f"{back}:{page}"))
    return text, None, kboard(rows)


def scr_settings_menu(state: State):
    text = (
        "⚙️ EDITABLE TEXTS\n"
        "─────────────────────────\n"
        "Tap any item to view it. Then either:\n"
        "• REPLY to the shown message with  /edit <new text>\n"
        "• or press its ✏️ Edit button.\n\n"
        "Saved automatically to your DB channel. 💾"
    )
    rows = [[_sv(p, SETTING_LABELS[p])] for p in SETTING_ORDER]
    rows.append([cbtn("🕘 Timezone", "tz", color=BLUE)])
    rows.append(back_row("om", "🔙 Owner Panel"))
    return text, None, kboard(rows)


def scr_setting_view(state: State, path: str):
    label = label_for(state, path)
    try:
        value = state.get_path(path)
    except KeyError:
        return f"⚠️ Unknown path: {path}", None, kboard([back_row(parent_of(path))])
    if isinstance(value, list):
        value = ", ".join(str(v.get("name", v)) if isinstance(v, dict) else str(v) for v in value)
    shown = value if value not in (None, "") else "(empty — not set)"
    if value is None and path.startswith("groups."):
        shown = "(not set — using the GLOBAL default; press ✏️ to override)"
    text = (
        f"{label}\n"
        "─────────────────────────\n"
        f"{shown}\n"
        "─────────────────────────\n"
        f"🔖 path: {path}\n"
        f"💡 placeholders: {PLACEHOLDER_HELP}\n\n"
        "✏️ REPLY to this message with:\n"
        "/edit <your new text>\n"
        "…or press the Edit button below."
    )
    rows = [[cbtn("✏️ Edit", f"ed:{path}", color=GREEN)]]
    if path.startswith("settings.") and path.split(".", 1)[1] in DEFAULT_SETTINGS:
        rows[0].append(cbtn("↩️ Reset Default", f"rs:{path}", color=RED))
    elif path.startswith("groups."):
        rows[0].append(cbtn("↩️ Use Global Default", f"rs:{path}", color=RED))
    rows.append(back_row(parent_of(path)))
    return text, None, kboard(rows)


def scr_plans_manage(state: State):
    plans = state.plans()
    text = "💰 PLANS & PRICES — manage\n─────────────────────────\n"
    rows = []
    if not plans:
        text += "(no plans yet — add your first one!)"
    for i, p in enumerate(plans, 1):
        text += f"{i}. {p.get('name', '?')} — {p.get('price', '?')}\n"
        rows.append(
            [
                cbtn(f"✏️ {truncate(p.get('name', 'plan'), 18)}", f"ple:{p['id']}", color=BLUE),
                cbtn("🗑", f"pld:{p['id']}", color=RED),
            ]
        )
    text += "\nThis is what users see in 💰 Plans. Set any price you like — ₹, $, 'Free', 'DM for price'…"
    rows.append([cbtn("➕ Add Plan", "pla", color=GREEN), cbtn("👁 User View", "pl", color=BLUE)])
    rows.append(back_row("om", "🔙 Owner Panel"))
    return text, None, kboard(rows)


def scr_plan_edit(state: State, pid: str):
    p = state.get_plan(pid)
    if not p:
        return "Plan not found.", None, kboard([back_row("plm")])
    text = (
        "✏️ EDIT PLAN\n─────────────────────────\n"
        f"Name  : {p.get('name')}\nPrice : {p.get('price')}\nDetail: {p.get('detail') or '—'}\n"
        f"id    : {p['id']}"
    )
    rows = [
        [cbtn("✏️ Name", f"ed:settings.plans[{pid}].name", color=BLUE), cbtn("💰 Price", f"ed:settings.plans[{pid}].price", color=GREEN)],
        [cbtn("📝 Detail", f"ed:settings.plans[{pid}].detail", color=BLUE), cbtn("🗑 Delete Plan", f"pld:{pid}", color=RED)],
        back_row("plm"),
    ]
    return text, None, kboard(rows)


def scr_groups_list(state: State, bot_username: str):
    groups = state.active_groups()
    text = "🛡 GROUPS\n─────────────────────────\n"
    rows = []
    if not groups:
        text += "No groups yet. Add the bot to a group with the button below — it will ask for admin rights automatically."
        rows.append([cbtn("🛡 Add to a Group", url=add_to_group_url(bot_username), color=GREEN)])
    for g in groups:
        title = truncate(g.get("title") or str(g["id"]), 26)
        rows.append([cbtn(f"🛡 {title}", f"gp:{g['id']}", color=BLUE)])
    rows.append(back_row("om", "🔙 Owner Panel"))
    return text, None, kboard(rows)


def _onoff(flag) -> str:
    return "✅ ON" if flag else "⛔ OFF"


def scr_group_panel(state: State, gid: int, members: int = 0):
    g = state.get_group(gid)
    if not g:
        return "Group not found in database.", None, kboard([back_row("ge")])
    text = (
        f"🛡 GROUP PANEL — {truncate(g.get('title') or str(gid), 30)}\n"
        "─────────────────────────\n"
        f"👥 Members: {members or g.get('members') or '?'}\n"
        f"👋 Welcome: {_onoff(g['welcome']['on'])}   📌 Pin: {_onoff(g['welcome']['pin'])}\n"
        f"🎯 Filters: {len(g['filters'])}   🤬 Bad words: {len(g['badwords'])}\n"
        f"🔗 Link guard: {_onoff(g['linkguard']['on'])}   🧪 Verify: {_onoff(g['verify']['on'])}\n"
        f"🌊 Anti-flood: {_onoff(g['flood']['on'])}   🔒 Locked: {'YES' if g.get('locked') else 'no'}\n"
        f"📅 Schedules: {len(g['schedules'])}   ⚠️ Warn limit: {g['warns']['limit']} ({g['warns']['action']})"
    )
    rows = [
        [cbtn("👋 Welcome", f"gw:{gid}", color=BLUE), cbtn("🎯 Filters", f"gf:{gid}", color=BLUE)],
        [cbtn("🤬 Bad Words", f"gb:{gid}", color=BLUE), cbtn("🔗 Link Guard", f"gl:{gid}", color=BLUE)],
        [cbtn("📅 Schedules", f"gs:{gid}", color=BLUE), cbtn("🧪 Verify Join", f"gv:{gid}", color=BLUE)],
        [cbtn("🌊 Anti-Flood", f"gfl:{gid}", color=BLUE), cbtn("⚠️ Warns", f"gwa:{gid}", color=BLUE)],
        [cbtn("📜 Rules", f"gru:{gid}", color=BLUE), cbtn("📊 Group Stats", f"gsx:{gid}", color=BLUE)],
        [
            cbtn("🔒 Lock Group", f"glk:{gid}", color=RED)
            if not g.get("locked")
            else cbtn("🔓 Unlock Group", f"glk:{gid}", color=GREEN)
        ],
        back_row("ge", "🔙 Groups"),
    ]
    return text, None, kboard(rows)


def scr_group_welcome(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("gp:" + str(gid))])
    eff = g["welcome"].get("text") or state.setting("welcome_group")
    src = "this group's own text" if g["welcome"].get("text") else "GLOBAL default (group has no override)"
    text = (
        f"👋 GROUP WELCOME — {truncate(g.get('title') or str(gid), 26)}\n"
        "─────────────────────────\n"
        f"Status: {_onoff(g['welcome']['on'])} • pinned: {_onoff(g['welcome']['pin'])}\n"
        f"Source: {src}\n\n"
        f"{truncate(str(eff), 900)}\n\n"
        f"💡 placeholders: {PLACEHOLDER_HELP}"
    )
    on = g["welcome"]["on"]
    rows = [
        [
            cbtn("⛔ Turn OFF", f"gt:groups.{gid}.welcome.on", color=RED)
            if on
            else cbtn("✅ Turn ON", f"gt:groups.{gid}.welcome.on", color=GREEN),
            cbtn("📌 Pin: OFF", f"gt:groups.{gid}.welcome.pin", color=BLUE)
            if not g["welcome"]["pin"]
            else cbtn("📌 Pin: ON", f"gt:groups.{gid}.welcome.pin", color=GREEN),
        ],
        [cbtn("✏️ Edit Text", f"ed:groups.{gid}.welcome.text", color=GREEN), cbtn("↩️ Use Default", f"rs:groups.{gid}.welcome.text", color=RED)],
        back_row(f"gp:{gid}", "🔙 Group Panel"),
    ]
    return text, None, kboard(rows)


def scr_group_rules(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    eff = g.get("rules") or state.setting("rules")
    text = (
        f"📜 RULES — {truncate(g.get('title') or str(gid), 26)}\n"
        "─────────────────────────\n"
        f"{truncate(str(eff), 1200)}\n\n"
        "Members see this with /rules in the group."
    )
    rows = [
        [cbtn("✏️ Edit", f"ed:groups.{gid}.rules", color=GREEN), cbtn("↩️ Default", f"rs:groups.{gid}.rules", color=RED)],
        back_row(f"gp:{gid}", "🔙 Group Panel"),
    ]
    return text, None, kboard(rows)


def _filter_desc(f: dict) -> str:
    if f.get("kind") == "text":
        return f"💬 {truncate(f.get('content', ''), 40)}"
    return {"photo": "📷 photo", "sticker": "😄 sticker", "animation": "🎞 GIF", "video": "📹 video",
            "document": "📄 file", "audio": "🎵 audio", "voice": "🎤 voice", "video_note": "⭕ video note"}.get(
        f.get("kind"), "📎 media"
    )


def scr_filters(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    fl = g["filters"]
    text = (
        f"🎯 KEYWORD FILTERS — {truncate(g.get('title') or str(gid), 24)}\n"
        "─────────────────────────\n"
        "When someone types a keyword, the bot reacts (text / sticker / GIF / photo…).\n\n"
    )
    rows = []
    if not fl:
        text += "(no filters yet)"
    for i, f in enumerate(fl):
        text += f"{i + 1}. “{f.get('word')}” → {_filter_desc(f)}\n"
        rows.append([cbtn(f"🗑 {truncate(f.get('word', '?'), 20)}", f"gfd:{gid}:{i}", color=RED)])
    text += "\nTip: matching is case-insensitive and works on partial words."
    rows.append([cbtn("➕ Add Filter", f"gfa:{gid}", color=GREEN)])
    rows.append(back_row(f"gp:{gid}", "🔙 Group Panel"))
    return text, None, kboard(rows)


def scr_badwords(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    bw = g["badwords"]
    text = (
        f"🤬 BAD WORDS — {truncate(g.get('title') or str(gid), 24)}\n"
        "─────────────────────────\n"
        "Message deleted + member muted automatically. Admins are exempt.\n\n"
    )
    rows = []
    if not bw:
        text += "(no bad words set)"
    for i, b in enumerate(bw):
        text += f"{i + 1}. “{b.get('word')}” → 🔇 {b.get('mute_min', 60)} min mute\n"
        rows.append([cbtn(f"🗑 {truncate(b.get('word', '?'), 20)}", f"gbd:{gid}:{i}", color=RED)])
    rows.append([cbtn("➕ Add Bad Word", f"gba:{gid}", color=GREEN)])
    rows.append(back_row(f"gp:{gid}", "🔙 Group Panel"))
    return text, None, kboard(rows)


def scr_linkguard(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    msg = g["linkguard"].get("msg") or state.setting("link_msg")
    on = g["linkguard"]["on"]
    text = (
        f"🔗 LINK GUARD — {truncate(g.get('title') or str(gid), 24)}\n"
        "─────────────────────────\n"
        f"Status: {_onoff(on)}\n\n"
        "When ON: any link from a non-admin is DELETED and the user is mentioned "
        "with the notice below.\n\n"
        f"Notice:\n{truncate(str(msg), 500)}\n\n"
        f"💡 placeholders: {PLACEHOLDER_HELP}"
    )
    rows = [
        [
            cbtn("⛔ Turn OFF", f"gt:groups.{gid}.linkguard.on", color=RED)
            if on
            else cbtn("✅ Turn ON", f"gt:groups.{gid}.linkguard.on", color=GREEN)
        ],
        [cbtn("✏️ Edit Notice", f"ed:groups.{gid}.linkguard.msg", color=BLUE), cbtn("↩️ Default", f"rs:groups.{gid}.linkguard.msg", color=RED)],
        back_row(f"gp:{gid}", "🔙 Group Panel"),
    ]
    return text, None, kboard(rows)


def scr_schedules(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    sch = g["schedules"]
    text = (
        f"📅 SCHEDULED POSTS — {truncate(g.get('title') or str(gid), 22)}\n"
        "─────────────────────────\n"
        f"Timezone: {state.tz}\n\n"
    )
    rows = []
    if not sch:
        text += "(nothing scheduled)"
    for s in sch:
        what = truncate(s.get("text") or f"[{s.get('mtype', 'media')}]", 34)
        text += f"⏰ {s.get('time')} • {s.get('repeat', 'daily')} • {_onoff(s.get('on', True))}\n    “{what}”\n"
        rows.append(
            [
                cbtn("⏸" if s.get("on", True) else "▶️", f"gst:{gid}:{s['id']}", color=BLUE),
                cbtn(f"🗑 {s.get('time')}", f"gsd:{gid}:{s['id']}", color=RED),
            ]
        )
    text += "\nExample: daily class reminder at 8:00 PM."
    rows.append([cbtn("➕ Add Schedule", f"gsa:{gid}", color=GREEN)])
    rows.append(back_row(f"gp:{gid}", "🔙 Group Panel"))
    return text, None, kboard(rows)


def scr_verify(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    v = g["verify"]
    msg = v.get("msg") or state.setting("verify_msg")
    text = (
        f"🧪 JOIN VERIFICATION (captcha) — {truncate(g.get('title') or str(gid), 20)}\n"
        "─────────────────────────\n"
        f"Status: {_onoff(v['on'])} • timeout: {v.get('timeout_min', 10)} min\n\n"
        "When ON: new members are muted until they press the ✅ button in the "
        "welcome message. If they don't verify in time, they are kicked.\n\n"
        f"Message:\n{truncate(str(msg), 400)}"
    )
    rows = [
        [
            cbtn("⛔ Turn OFF", f"gt:groups.{gid}.verify.on", color=RED)
            if v["on"]
            else cbtn("✅ Turn ON", f"gt:groups.{gid}.verify.on", color=GREEN),
            cbtn("⏱ Timeout", f"ed:groups.{gid}.verify.timeout_min", color=BLUE),
        ],
        [cbtn("✏️ Edit Message", f"ed:groups.{gid}.verify.msg", color=BLUE), cbtn("↩️ Default", f"rs:groups.{gid}.verify.msg", color=RED)],
        back_row(f"gp:{gid}", "🔙 Group Panel"),
    ]
    return text, None, kboard(rows)


def scr_flood(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    fl = g["flood"]
    text = (
        f"🌊 ANTI-FLOOD — {truncate(g.get('title') or str(gid), 24)}\n"
        "─────────────────────────\n"
        f"Status: {_onoff(fl['on'])}\n"
        f"Limit: {fl['limit']} msgs / {fl['window']}s → 🔇 {fl['mute_min']} min mute\n\n"
        "Catches people spamming many messages quickly. Admins are exempt."
    )
    rows = [
        [
            cbtn("⛔ Turn OFF", f"gt:groups.{gid}.flood.on", color=RED)
            if fl["on"]
            else cbtn("✅ Turn ON", f"gt:groups.{gid}.flood.on", color=GREEN)
        ],
        [
            cbtn("✏️ Limit", f"ed:groups.{gid}.flood.limit", color=BLUE),
            cbtn("✏️ Window", f"ed:groups.{gid}.flood.window", color=BLUE),
            cbtn("✏️ Mute min", f"ed:groups.{gid}.flood.mute_min", color=BLUE),
        ],
        back_row(f"gp:{gid}", "🔙 Group Panel"),
    ]
    return text, None, kboard(rows)


def scr_warns(state: State, gid: int):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    w = g["warns"]
    text = (
        f"⚠️ WARN SYSTEM — {truncate(g.get('title') or str(gid), 24)}\n"
        "─────────────────────────\n"
        f"Limit: {w['limit']} warns → action: {'🔇 mute' if w['action'] == 'mute' else '🚫 ban'}"
        f" ({w.get('mute_min', 60)} min)\n"
        f"Users with warns: {len([1 for c in w.get('counts', {}).values() if c])}\n\n"
        "In the group, admins use:\n/warn (reply) • /unwarn (reply) • /warns (reply)"
    )
    act = "ban" if w["action"] == "mute" else "mute"
    rows = [
        [cbtn("➖", f"gwn:{gid}:-1", color=BLUE), cbtn(f"Limit: {w['limit']}", "noop"), cbtn("➕", f"gwn:{gid}:+1", color=BLUE)],
        [cbtn(f"Action → {act.upper()}", f"gwt:{gid}", color=RED if act == "ban" else GREEN)],
        [cbtn("✏️ Mute minutes", f"ed:groups.{gid}.warns.mute_min", color=BLUE)],
        back_row(f"gp:{gid}", "🔙 Group Panel"),
    ]
    return text, None, kboard(rows)


def scr_group_stats(state: State, gid: int, members: int = 0):
    g = state.get_group(gid)
    if not g:
        return "Group not found.", None, kboard([back_row("ge")])
    s = g.get("stats", {})
    text = (
        f"📊 GROUP STATS — {truncate(g.get('title') or str(gid), 24)}\n"
        "─────────────────────────\n"
        f"ID: {gid}\n"
        f"👥 Members: {members or g.get('members') or '?'}\n"
        f"🛡 Guarding since: {fmt_ts(g.get('joined'), state.tz)}\n\n"
        f"💬 Messages seen : {s.get('msgs', 0)}\n"
        f"👋 Joins/welcomes: {s.get('joins', 0)} / {s.get('welcomes', 0)}\n"
        f"🗑 Deleted       : {s.get('deletes', 0)}\n"
        f"🔇 Mutes         : {s.get('mutes', 0)}\n"
        f"🚫 Bans          : {s.get('bans', 0)}\n"
        f"⚠️ Warns         : {s.get('warns', 0)}\n"
        f"🚨 Reports       : {s.get('reports', 0)}\n"
        f"🧪 Verified      : {s.get('verified', 0)}\n"
        f"🎯 Filter hits   : {len(g['filters'])} filters configured"
    )
    rows = [back_row(f"gp:{gid}", "🔙 Group Panel")]
    return text, None, kboard(rows)


def scr_stats(state: State, uptime_s: int, extra: str = ""):
    from ..timeutil import humanize_seconds, now_ts

    users = state.users
    day = now_ts() - 86400
    week = now_ts() - 7 * 86400
    total = len(users)
    blocked = len([u for u in users.values() if u.get("blocked")])
    new24 = len([u for u in users.values() if int(u.get("joined") or 0) >= day])
    new7 = len([u for u in users.values() if int(u.get("joined") or 0) >= week])
    active7 = len([u for u in users.values() if int(u.get("seen") or 0) >= week])
    groups = state.active_groups()
    c = state.data.get("counters", {})
    text = (
        "📊 BOT STATS\n"
        "─────────────────────────\n"
        f"👥 Users total   : {total}\n"
        f"🆕 New 24h / 7d  : {new24} / {new7}\n"
        f"💬 Active 7d     : {active7}\n"
        f"🚫 Blocked       : {blocked}\n"
        f"🛡 Groups        : {len(groups)}\n"
        f"👥 Group members : {sum(int(g.get('members') or 0) for g in groups)}\n"
        "─────────────────────────\n"
        f"💬 Msgs seen     : {c.get('msgs_seen', 0)}\n"
        f"👋 Welcomes sent : {c.get('welcomes', 0)}\n"
        f"🗑 Deleted       : {c.get('deletes', 0)}\n"
        f"🔇 Mutes         : {c.get('mutes', 0)}\n"
        f"🚫 Bans          : {c.get('bans', 0)}\n"
        f"⚠️ Warns         : {c.get('warns', 0)}\n"
        f"🚨 Reports       : {c.get('reports', 0)}\n"
        f"🎯 Filter hits   : {c.get('filters_hit', 0)}\n"
        "─────────────────────────\n"
        f"💾 DB snapshot   : v{state.data.get('seq', 0)} • {fmt_ts(state.data.get('saved_at'), state.tz)}\n"
        f"⏱ Uptime        : {humanize_seconds(uptime_s)}\n"
        f"{extra}"
    )
    rows = [
        [cbtn("🔄 Refresh", "st", color=BLUE), cbtn("👥 Users", "us:0", color=BLUE)],
        back_row("om", "🔙 Owner Panel"),
    ]
    return text, None, kboard(rows)


def scr_report(state: State, rid: str):
    r = state.get_report(rid)
    if not r:
        return "🚨 Report expired or already handled.", None, kboard([back_row("om")])
    text = (
        f"🚨 NEW REPORT #{rid}\n"
        "─────────────────────────\n"
        f"Group   : {r.get('gtitle')} ({r.get('gid')})\n"
        f"Reporter: {r.get('reporter')} ({r.get('reporter_id')})\n"
        f"Target  : {r.get('target') or '—'} ({r.get('target_id') or '—'})\n"
        f"Reason  : {r.get('reason') or '—'}\n"
        f"When    : {fmt_ts(r.get('ts'), state.tz)}\n\n"
        "⤴️ The reported message was forwarded above (when possible)."
    )
    rows = [
        [cbtn("🚫 Ban", f"rp:{rid}:ban", color=RED), cbtn("🔇 Mute", f"rp:{rid}:mute", color=BLUE)],
        [cbtn("🗑 Delete Message", f"rp:{rid}:del", color=RED), cbtn("❌ Dismiss", f"rp:{rid}:no", color=BLUE)],
    ]
    return text, None, kboard(rows)


def scr_report_durations(state: State, rid: str, act: str):
    r = state.get_report(rid)
    if not r:
        return "🚨 Report expired.", None, kboard([back_row("om")])
    title = "🚫 BAN" if act == "ban" else "🔇 MUTE"
    text = f"{title} — {r.get('target') or r.get('target_id')}\nGroup: {r.get('gtitle')}\n\nFor how long?"
    if act == "ban":
        opts = [("1 day", 86400), ("7 days", 604800), ("30 days", 2592000), ("♾️ Forever", 0)]
    else:
        opts = [("10 min", 600), ("1 hour", 3600), ("12 hours", 43200), ("1 day", 86400), ("1 week", 604800)]
    rows = [[cbtn(lab, f"rp:{rid}:{act}:{secs}", color=RED if act == "ban" else BLUE) for lab, secs in opts[:3]],
            [cbtn(lab, f"rp:{rid}:{act}:{secs}", color=RED if act == "ban" else BLUE) for lab, secs in opts[3:]]]
    rows.append(back_row(f"rpb:{rid}", "🔙 Back to report"))
    return text, None, kboard(rows)


def scr_editables(state: State):
    items = state.editable_recent(12)
    text = (
        "✏️ RECENTLY SHOWN EDITABLE MESSAGES\n"
        "─────────────────────────\n"
        "Tip: you can also REPLY to any settings message the bot sent you "
        "and write  /edit <new text>."
    )
    rows = []
    seen = set()
    for it in items:
        key = it["key"]
        if key in seen:
            continue
        seen.add(key)
        rows.append([cbtn(truncate(it.get("label") or label_for(state, key), 30), f"sv:{key}", color=BLUE)])
    if not rows:
        text += "\n\n(nothing yet — open ⚙️ Settings first)"
    rows.append(back_row("sm"))
    return text, None, kboard(rows)


def scr_timezone(state: State):
    text = (
        "🕘 TIMEZONE\n─────────────────────────\n"
        f"Current: {state.tz}\n\n"
        "Used for /schedule posts and all timestamps.\n"
        "Press the button and send a timezone name like:\n"
        "Asia/Kolkata • Asia/Dubai • UTC • America/New_York"
    )
    rows = [[cbtn("✏️ Change Timezone", "tze", color=GREEN)], back_row("sm")]
    return text, None, kboard(rows)
