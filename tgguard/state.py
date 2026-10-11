"""In-memory state model + JSON (de)serialization.

The state is ONE json document. It is saved into the private DB channel as a
pinned snapshot document (see db.py) and restored from there on every boot.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .timeutil import now_ts

SCHEMA_VERSION = 4

DEFAULT_SETTINGS: dict[str, Any] = {
    "welcome_pm": (
        "🎙️ Welcome to {botname}, {mention}!\n\n"
        "You have reached the official bot of our Voice & Dubbing Academy — "
        "learn voice improvement, dubbing and mic techniques from a professional voice artist.\n\n"
        "✨ Use the menu below:\n"
        "📚 Courses  •  💰 Plans & Fees  •  📞 Contact\n\n"
        "Ready to find your voice? Let's go! 🎧"
    ),
    "welcome_group": (
        "🎉 Welcome {mention} to {group}!\n\n"
        "🎙️ You just joined our Voice Improvement & Dubbing family.\n"
        "📜 Please read the rules: /rules\n"
        "💰 Want to join the course? Open the bot PM and press 💰 Plans.\n\n"
        "Enjoy the classes! ✨"
    ),
    "about": (
        "🎙️ About the Artist\n\n"
        "A professional voice artist teaching online:\n"
        "• Voice improvement & modulation\n"
        "• Dubbing for ads, films and animation\n"
        "• Recording & mic techniques\n\n"
        "Students from all over India learn here through live classes and daily practice tasks.\n\n"
        "(Owner: customize me — reply /edit or press ✏️ Edit.)"
    ),
    "courses": (
        "📚 Courses\n\n"
        "1️⃣ Voice Improvement — beginner to pro\n"
        "2️⃣ Dubbing Masterclass — projects + feedback\n"
        "3️⃣ Character Voice Acting — advanced\n\n"
        "💰 Press the Plans button to see fees, or ask in the group."
    ),
    "contact": (
        "📞 Contact\n\n"
        "For admission, fees or any doubt, DM the admin directly.\n"
        "(Owner: set your contact link/username with /setcontact)"
    ),
    "rules": (
        "📜 Group Rules\n\n"
        "1️⃣ Respect everyone — this is a learning space.\n"
        "2️⃣ No spam, links or ads without admin permission.\n"
        "3️⃣ Abusive language = automatic mute.\n"
        "4️⃣ Class timings & materials are posted here — stay tuned.\n"
        "5️⃣ Payment / plan queries → DM the admin.\n\n"
        "Report anything suspicious with /report (reply to a message)."
    ),
    "plans": [
        {
            "id": "p_demo1",
            "name": "🎙️ Voice Basics",
            "price": "₹499",
            "detail": "4 weeks • live classes • daily practice tasks",
        },
        {
            "id": "p_demo2",
            "name": "🎧 Dubbing Masterclass",
            "price": "₹999",
            "detail": "6 weeks • real dubbing projects • personal feedback",
        },
        {
            "id": "p_demo3",
            "name": "👑 Pro Bundle (Voice + Dubbing)",
            "price": "₹1499",
            "detail": "10 weeks • certificate • 1-on-1 doubt sessions",
        },
    ],
    # reply shown when someone tries to /report an admin (set via /rmsg)
    "rmsg": "",
    "tz": "Asia/Kolkata",
    "link_msg": (
        "🚫 Links are NOT allowed in this group, {mention}.\n"
        "Your message was deleted. Ask an admin for permission first."
    ),
    "badword_msg": "🔇 {mention} — bad words are not allowed. Muted for {duration}.",
    "verify_msg": "👋 {mention}, welcome to {group}!\nPress ✅ below to verify you are human.",
    "flood_msg": "🌊 Slow down {mention}! Flooding is not allowed — muted for {duration}.",
}

DEFAULT_COUNTERS = {
    "msgs_seen": 0,
    "welcomes": 0,
    "deletes": 0,
    "mutes": 0,
    "bans": 0,
    "warns": 0,
    "reports": 0,
    "filters_hit": 0,
    "blocked_marked": 0,
}


def default_state(tz: str = "Asia/Kolkata") -> dict:
    settings = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULT_SETTINGS.items()}
    settings["tz"] = tz
    return {
        "v": SCHEMA_VERSION,
        "seq": 0,
        "saved_at": 0,
        "snap_history": [],
        "pinned": 0,
        "bot_username": "",
        "settings": settings,
        "users": {},
        "groups": {},
        "editable": {},
        "reports": {},
        "counters": dict(DEFAULT_COUNTERS),
    }


def default_group(gid: int, title: str | None = None, by: int | None = None) -> dict:
    return {
        "id": gid,
        "title": title or "",
        "joined": now_ts(),
        "by": by,
        "left": False,
        "members": 0,
        "members_ts": 0,
        "welcome": {"on": True, "text": None, "pin": False},
        "rules": None,
        "filters": [],
        "linkguard": {"on": False, "msg": None},
        "badwords": [],
        "flood": {"on": False, "limit": 7, "window": 10, "mute_min": 5},
        "verify": {"on": False, "timeout_min": 10, "msg": None},
        "warns": {"limit": 3, "action": "mute", "mute_min": 60, "counts": {}},
        "schedules": [],
        "locked": False,
        "lock_saved": None,
        "stats": {
            "msgs": 0,
            "joins": 0,
            "deletes": 0,
            "mutes": 0,
            "bans": 0,
            "warns": 0,
            "reports": 0,
            "welcomes": 0,
            "verified": 0,
        },
    }


_SEG_RE = re.compile(r"^([A-Za-z0-9_\-]+)(?:\[([^\]]+)\])?$")

MISSING = object()   # returned by get_path when a default of MISSING is passed
_RAISE = object()    # internal sentinel: no default -> raise KeyError


def _parse_seg(seg: str):
    m = _SEG_RE.match(seg)
    if not m:
        raise KeyError(f"bad path segment {seg!r}")
    return m.group(1), m.group(2)


def _resolve_item(lst: list, key: str):
    """Find a list item by its 'id' field, else by integer index."""
    for it in lst:
        if isinstance(it, dict) and str(it.get("id")) == key:
            return it
    try:
        return lst[int(key)]
    except (ValueError, IndexError):
        raise KeyError(f"[{key}] not found")


def get_path(data: dict, path: str, default=_RAISE):
    cur: Any = data
    try:
        for seg in path.split("."):
            name, key = _parse_seg(seg)
            cur = cur[name]
            if key is not None:
                cur = _resolve_item(cur, key)
    except (KeyError, TypeError, IndexError):
        if default is _RAISE:
            raise KeyError(path)
        return default
    return cur


def set_path(data: dict, path: str, value) -> Any:
    """Set value at dotted path; supports list items via [id]/[index]. Returns old value."""
    segs = path.split(".")
    cur: Any = data
    for seg in segs[:-1]:
        name, key = _parse_seg(seg)
        cur = cur[name]
        if key is not None:
            cur = _resolve_item(cur, key)
    name, key = _parse_seg(segs[-1])
    if key is not None:
        # final segment addresses a whole list item: settings.plans[p1]
        lst = cur[name]
        for i, it in enumerate(lst):
            if isinstance(it, dict) and str(it.get("id")) == key:
                old = it
                lst[i] = value
                return old
        idx = int(key)
        old = lst[idx]
        lst[idx] = value
        return old
    old = cur.get(name, MISSING)
    cur[name] = value
    return old


def _deep_merge_defaults(data: dict, defaults: dict) -> dict:
    for k, dv in defaults.items():
        if k not in data:
            data[k] = dv
        elif isinstance(dv, dict) and isinstance(data[k], dict):
            _deep_merge_defaults(data[k], dv)
    return data


def fill_group(rec: dict) -> dict:
    """Make sure a (possibly old/partial) group record has every field."""
    tmpl = default_group(int(rec.get("id") or 0))
    for k, v in tmpl.items():
        if k not in rec:
            rec[k] = v
        elif isinstance(v, dict) and isinstance(rec[k], dict):
            for k2, v2 in v.items():
                rec[k].setdefault(k2, v2)
    return rec


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


class State:
    """Wrapper around the raw dict with mutation helpers that mark it dirty."""

    def __init__(self, data: dict | None = None, tz: str = "Asia/Kolkata"):
        self.data: dict = data if data is not None else default_state(tz)
        _deep_merge_defaults(self.data, default_state(self.data.get("settings", {}).get("tz", tz)))
        for rec in self.data.get("groups", {}).values():
            if isinstance(rec, dict):
                fill_group(rec)
        self.dirty: bool = data is None
        self.users_at_boot: int = len(self.data.get("users", {}))
        self.groups_at_boot: int = len(self.data.get("groups", {}))

    def changed(self) -> None:
        self.dirty = True

    # ---------------- serialization ---------------- #
    def to_json(self) -> bytes:
        return json.dumps(self.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_json(cls, blob: bytes, tz: str = "Asia/Kolkata") -> "State":
        data = json.loads(blob.decode("utf-8"))
        if not isinstance(data, dict) or "settings" not in data or "users" not in data:
            raise ValueError("not a TG-Guard database file")
        return cls(data, tz=tz)

    # ---------------- settings ---------------- #
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    def setting(self, key: str, default=None):
        return self.settings.get(key, default)

    @property
    def tz(self) -> str:
        return self.settings.get("tz") or "Asia/Kolkata"

    # ---------------- users ---------------- #
    @property
    def users(self) -> dict:
        return self.data["users"]

    def get_user(self, uid: int) -> dict | None:
        return self.users.get(str(uid))

    def touch_user(self, uid: int, first: str = "", last: str = "", username: str = "") -> tuple[dict, bool]:
        rec = self.users.get(str(uid))
        created = rec is None
        if created:
            rec = {
                "id": uid,
                "first": first,
                "last": last,
                "user": username,
                "joined": now_ts(),
                "seen": now_ts(),
                "blocked": False,
                "blocked_ts": 0,
                "msgs": 0,
            }
            self.users[str(uid)] = rec
            self.changed()
        else:
            rec["seen"] = now_ts()
            rec["msgs"] = int(rec.get("msgs", 0)) + 1
            if first and rec.get("first") != first:
                rec["first"] = first
            if last is not None and rec.get("last") != last:
                rec["last"] = last
            if username is not None and rec.get("user") != (username or ""):
                rec["user"] = username or ""
            self.changed()
        return rec, created

    def mark_blocked(self, uid: int, blocked: bool) -> None:
        rec = self.users.get(str(uid))
        if rec is None:
            rec, _ = self.touch_user(uid)
        if bool(rec.get("blocked")) != blocked:
            rec["blocked"] = blocked
            rec["blocked_ts"] = now_ts() if blocked else 0
            if blocked:
                self.data["counters"]["blocked_marked"] = self.data["counters"].get("blocked_marked", 0) + 1
            self.changed()

    def users_sorted(self, blocked: bool | None = None) -> list[dict]:
        items = list(self.users.values())
        if blocked is True:
            items = [u for u in items if u.get("blocked")]
        elif blocked is False:
            items = [u for u in items if not u.get("blocked")]
        items.sort(key=lambda u: int(u.get("joined") or 0), reverse=True)
        return items

    # ---------------- groups ---------------- #
    @property
    def groups(self) -> dict:
        return self.data["groups"]

    def get_group(self, gid: int) -> dict | None:
        return self.groups.get(str(gid))

    def ensure_group(self, gid: int, title: str | None = None, by: int | None = None) -> tuple[dict, bool]:
        rec = self.groups.get(str(gid))
        created = rec is None
        if created:
            rec = default_group(gid, title, by)
            self.groups[str(gid)] = rec
            self.changed()
        else:
            fill_group(rec)
            if title and rec.get("title") != title:
                rec["title"] = title
                self.changed()
            if rec.get("left"):
                rec["left"] = False
                self.changed()
        return rec, created

    def active_groups(self) -> list[dict]:
        return [g for g in self.groups.values() if not g.get("left")]

    # ---------------- plans ---------------- #
    def plans(self) -> list:
        return self.settings.setdefault("plans", [])

    def get_plan(self, pid: str) -> dict | None:
        for p in self.plans():
            if p.get("id") == pid:
                return p
        return None

    def add_plan(self, name: str, price: str, detail: str = "") -> dict:
        p = {"id": new_id("p"), "name": name, "price": price, "detail": detail}
        self.plans().append(p)
        self.changed()
        return p

    def del_plan(self, pid: str) -> bool:
        before = len(self.plans())
        self.settings["plans"] = [p for p in self.plans() if p.get("id") != pid]
        if len(self.settings["plans"]) != before:
            self.changed()
            return True
        return False

    def format_plans(self) -> str:
        plans = self.plans()
        if not plans:
            return "💰 No plans listed yet. Ask the admin!"
        lines = ["💰 COURSE PLANS & FEES", ""]
        for i, p in enumerate(plans, 1):
            lines.append(f"{i}. {p.get('name', 'Plan')} — {p.get('price', '—')}")
            if p.get("detail"):
                lines.append(f"    {p['detail']}")
            lines.append("")
        lines.append("📩 Interested? Press 📞 Contact and message the admin.")
        return "\n".join(lines).rstrip()

    # ---------------- editable-message registry (for /edit by reply) ---------------- #
    def register_editable(self, chat_id: int, message_id: int, key: str, label: str = "") -> None:
        reg = self.data.setdefault("editable", {})
        reg[f"{chat_id}:{message_id}"] = {"key": key, "label": label, "ts": now_ts()}
        if len(reg) > 250:  # prune oldest
            for k in sorted(reg, key=lambda k: reg[k].get("ts", 0))[: len(reg) - 250]:
                reg.pop(k, None)
        self.changed()

    def editable_key(self, chat_id: int, message_id: int) -> dict | None:
        return self.data.get("editable", {}).get(f"{chat_id}:{message_id}")

    def editable_recent(self, limit: int = 12) -> list[dict]:
        reg = self.data.get("editable", {})
        items = sorted(reg.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
        out = []
        for k, v in items[:limit]:
            chat_id, mid = k.split(":")
            out.append({"chat_id": int(chat_id), "msg_id": int(mid), **v})
        return out

    # ---------------- reports ---------------- #
    def add_report(self, info: dict) -> str:
        rid = uuid.uuid4().hex[:8]
        info["rid"] = rid
        info["ts"] = now_ts()
        self.data.setdefault("reports", {})[rid] = info
        self.prune_reports()
        self.changed()
        return rid

    def get_report(self, rid: str) -> dict | None:
        return self.data.get("reports", {}).get(rid)

    def del_report(self, rid: str) -> None:
        self.data.get("reports", {}).pop(rid, None)
        self.changed()

    def prune_reports(self, max_age: int = 7 * 86400) -> None:
        reports = self.data.get("reports", {})
        cutoff = now_ts() - max_age
        for rid in [r for r, v in reports.items() if int(v.get("ts", 0)) < cutoff]:
            reports.pop(rid, None)

    # ---------------- counters ---------------- #
    def bump(self, name: str, n: int = 1) -> None:
        c = self.data.setdefault("counters", {})
        c[name] = int(c.get(name, 0)) + n

    def bump_group(self, gid: int, name: str, n: int = 1) -> None:
        g = self.get_group(gid)
        if g:
            s = g.setdefault("stats", {})
            s[name] = int(s.get(name, 0)) + n

    # ---------------- path helpers bound to this state ---------------- #
    def get_path(self, path: str, default=_RAISE):
        return get_path(self.data, path, default)

    def set_path(self, path: str, value) -> Any:
        old = set_path(self.data, path, value)
        self.changed()
        return old
