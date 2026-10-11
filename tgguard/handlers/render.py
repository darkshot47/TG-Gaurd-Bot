"""Maps callback-data strings to screens. Used by the callback router AND by
flows/commands that need to re-render a menu after a change (single source of
truth, so buttons and commands always show the same screen)."""

from __future__ import annotations

import logging

from . import screens as S
from .common import CTX, fetch_members, get_state, group_admin_ids, is_owner

log = logging.getLogger("tgguard.render")


def bot_username(context: CTX) -> str:
    return context.bot.username or get_state(context).data.get("bot_username", "")


async def groups_visible_to(context: CTX, user_id: int):
    """Owner sees all groups; a group admin sees only groups they admin."""
    state = get_state(context)
    groups = state.active_groups()
    if is_owner(user_id):
        return groups
    out = []
    for g in groups:
        if user_id in await group_admin_ids(context, int(g["id"])):
            out.append(g)
    return out


async def render_cb(data: str, context: CTX, user=None):
    """Return (text, entities, markup) for a callback-data string, or None."""
    state = get_state(context)
    p = data.split(":")
    tag = p[0]
    uid = user.id if user else 0

    try:
        if tag == "noop":
            return None
        if tag == "h":
            return S.scr_home(state, user, bot_username(context), is_owner(uid)) if user else None
        if tag == "om":
            return S.scr_owner_menu(state) if is_owner(uid) else S.scr_home(state, user, bot_username(context), False)
        if tag == "us":
            return S.scr_users(state, int(p[1])) if is_owner(uid) else None
        if tag == "bu":
            return S.scr_users(state, int(p[1]), blocked_only=True) if is_owner(uid) else None
        if tag == "up":
            return S.scr_user_profile(state, int(p[1]), p[2] if len(p) > 2 else "us", int(p[3]) if len(p) > 3 else 0) if is_owner(uid) else None
        if tag == "st":
            import time

            up = int(time.time() - float(context.bot_data.get("started", time.time())))
            return S.scr_stats(state, up) if is_owner(uid) else None
        if tag == "sm":
            return S.scr_settings_menu(state) if is_owner(uid) else None
        if tag == "sv":
            path = ":".join(p[1:])
            return S.scr_setting_view(state, path)
        if tag == "ed":  # rendered same as view (the flow starts separately)
            path = ":".join(p[1:])
            return S.scr_setting_view(state, path)
        if tag == "pl":
            return S.scr_plans(state, owner=is_owner(uid))
        if tag == "plm":
            return S.scr_plans_manage(state) if is_owner(uid) else None
        if tag == "ple":
            return S.scr_plan_edit(state, p[1]) if is_owner(uid) else None
        if tag == "ge":
            groups = await groups_visible_to(context, uid)
            if is_owner(uid):
                return S.scr_groups_list(state, bot_username(context))
            rows = [[S.cbtn(f"🛡 {S.truncate(g.get('title') or str(g['id']), 26)}", f"gp:{g['id']}", color=S.BLUE)] for g in groups]
            rows.append(S.back_row("h", "🔙 Home"))
            text = "🛡 YOUR GROUPS\n─────────────────────────\nPick a group to manage it." if groups else "You don't administrate any group with me yet."
            return text, None, S.kboard(rows)
        if tag == "gp":
            gid = int(p[1])
            members = await fetch_members(context, gid)
            return S.scr_group_panel(state, gid, members)
        if tag == "gw":
            return S.scr_group_welcome(state, int(p[1]))
        if tag == "gru":
            return S.scr_group_rules(state, int(p[1]))
        if tag == "gf":
            return S.scr_filters(state, int(p[1]))
        if tag == "gb":
            return S.scr_badwords(state, int(p[1]))
        if tag == "gl":
            return S.scr_linkguard(state, int(p[1]))
        if tag == "gs":
            return S.scr_schedules(state, int(p[1]))
        if tag == "gv":
            return S.scr_verify(state, int(p[1]))
        if tag == "gfl":
            return S.scr_flood(state, int(p[1]))
        if tag == "gwa":
            return S.scr_warns(state, int(p[1]))
        if tag == "gsx":
            gid = int(p[1])
            members = await fetch_members(context, gid)
            return S.scr_group_stats(state, gid, members)
        if tag == "rp":
            return S.scr_report(state, p[1]) if is_owner(uid) else None
        if tag == "rpb":
            return S.scr_report(state, p[1]) if is_owner(uid) else None
        if tag == "ab":
            return S.scr_simple(state, "about", "🎙️ ABOUT")
        if tag == "cs":
            return S.scr_simple(state, "courses", "📚 COURSES")
        if tag == "co":
            return S.scr_simple(state, "contact", "📞 CONTACT")
        if tag == "he":
            return S.scr_help(state, bot_username(context))
        if tag == "tz":
            return S.scr_timezone(state) if is_owner(uid) else None
    except (ValueError, IndexError, KeyError) as e:
        log.warning("render_cb(%s): %s", data, e)
        return None
    return None
