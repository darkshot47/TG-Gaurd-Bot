# 🛡 TG-Guard Bot

**Voice & Dubbing Academy ka all-in-one Telegram bot** — guard + group manager + course/plans showcase + broadcast system, **bina kisi database ke**. Aapka **private Telegram channel hi database hai**: sara data (users, groups, settings, plans, filters, schedules) ek JSON snapshot me channel me **pin** hota hai, aur bot restart/redeploy hote hi sabse pehle channel se database **read** kar leta hai.

Banaya gaya hai ek voice artist ke liye jo Telegram group me online classes (voice improvement + dubbing) chalate hain — lekin ye kisi bhi community/course group ke liye kaam karta hai.

---

## ✨ Features

| # | Feature | Kaise |
|---|---------|-------|
| 1 | 💰 **Customisable plans & prices** — owner khud name/price/detail set karta hai | `/plans`, `/addplan`, buttons |
| 2 | 👋 **Customisable welcome messages** (PM + group) with **`{mention}`** — jahan `{mention}` likhoge wahan user real me ping/mention hoga | `/setwelcome`, `/gw` |
| 3 | 🗄 **Channel = Database** — no MongoDB, no files. Private channel me pinned JSON snapshot + live event log (har naya user channel me post hota hai) | automatic |
| 4 | ✏️ **/edit by reply** — bot ke kisi bhi settings-message ko reply karke `/edit <new text>` → turant update + channel me save | `/edit` |
| 5 | 👥 `/users` — sabhi users jinhone `/start` kiya (paginated buttons, profile, message) | owner |
| 6 | ✉️ `/msg <id> <text>` — kisi user ko P2P message (ya kisi forwarded message ko reply karke `/msg`) | owner |
| 7 | 📊 `/stats` — bot + groups ke stats (users, blocked, deletes, mutes, uptime, snapshot info) | owner |
| 8 | 🚫 `/busers` — jinhone bot ko block kiya (auto-detect: broadcast/send fail = blocked mark) | owner |
| 9 | 📢 `/broadcast` — sab users ko text/media broadcast, live progress, blocked auto-detect | owner |
| 10 | 🛡 **1-tap Add-to-Group button** — deep link se bot group me **admin rights ke saath** add hota hai (delete + restrict + invite + pin) | green button |
| 11 | 📅 **Scheduled posts** — "8:00 PM pe ye message group me bhejna" → daily ya one-time, timezone-aware | `/schedule 8:00 PM <text>` |
| 12 | 🚨 **/report system** — group me kisi message ko reply + `/report` → owner PM me forward + **Ban / Mute / Delete / Dismiss** coloured buttons. **Admin report hua to report NAHI jata** — uske badle `/rmsg` wala reply milta hai | `/report`, `/rmsg` |
| 13 | 🎯 **Keyword filters** — "Hello" likha to bot text/GIF/sticker/photo bheje (media bot ko bhejo, wo DB channel me backup + group me trigger) | `/filter hello <reply>` |
| 14 | 🔗 **Link guard** — non-admin ka link message DELETE + mention karke notice (custom text) | `/linkguard on` |
| 15 | 🤬 **Bad words** — word aate hi message delete + member ko X time ke liye restrict (mute) | `/badword <word> 2h` |
| 16 | 🎨 **Coloured buttons** — Telegram ke Feb-2026 update wale native button styles: 🔵 primary / 🟢 success / 🔴 danger — har command button se bhi chalta hai | sab jagah |
| 17 | 🧪 **Join verification (captcha)** — naya member ✅ button dabaye tabhi bol sake, warna timeout pe kick (optional, default OFF) | `/verify on` |
| 18 | 🌊 **Anti-flood** — X messages in Y seconds → auto-mute (optional, default OFF) | `/flood on` |
| 19 | ⚠️ **Warn system** — `/warn` reply, limit cross pe mute/ban (counts DB me save) | `/warn`, `/warnlimit` |
| 20 | 🔒 **Lockdown** — group lock/unlock (sirf admins bol saken) | `/lock`, `/unlock` |
| 21 | 💾 **Backup / Restore** — poora database JSON download/upload | `/backup`, `/restore` |
| 22 | 🕘 **Timezone** — schedules aur timestamps ke liye (default Asia/Kolkata) | `/tz` |

Sab kuch **English default text** ke saath aata hai aur **har text owner customize kar sakta hai** — aur har change **database channel me save** hota hai.

---

## 🗄 Database kaise kaam karta hai (Channel-as-DB)

```
┌────────────────────────── PRIVATE DB CHANNEL ──────────────────────────┐
│ 📌 PINNED:  tgguard_db.json   ← poora state (users/groups/settings/…)  │
│      description: "TGGB#SNAP snap=<msg_id> ts=… v42"  (fallback pointer)│
│                                                                        │
│ 👤 NEW USER #37  Name: Asha (@asha)  ID: 123…      ← live event log    │
│ 🛡 Bot ADDED to group: Voice Class (-100…)                             │
│ ⚙️ SETTING CHANGED by Owner  Path: settings.welcome_pm  Value: …       │
│ 🔇 AUTO-MUTE Ramesh (555) for 60m — bad word "…"                       │
│ 😄 [filter sticker backup]   🎞 [filter GIF backup]                    │
└────────────────────────────────────────────────────────────────────────┘
```

* **Save:** har change ke baad state dirty hota hai; ~4 minute me ek (env: `SNAPSHOT_EVERY`) aur **shutdown/redeploy (SIGTERM) pe turant** naya `tgguard_db.json` upload + **pin** hota hai. Purane 5 snapshots rotate hote rehte hain.
* **Load (restart pe pehla kaam):** `getChat → pinned_message → download → parse`. Pin gayab ho to channel-description ka `snap=<id>` pointer → message forward karke nikaal laate hain.
* **Log:** naye users, group joins, setting changes, mutes/bans, reports — sab real-time channel me post hota hai (visible member-list + audit trail). Filter media bhi channel me backup hota hai.

> Bot **private channel ka admin** hona chahiye with rights: post messages, pin, delete, change info. Channel ka history padhne ki zaroorat NAHI padti (isliye ye pure Bot API se chalta hai — koi userbot/session string nahi).

---

## 🚀 Setup (10 minute)

### 1) Bot banao — @BotFather
1. `/newbot` → naam + username do → **BOT_TOKEN** mil jayega.
2. `/setprivacy` → apna bot choose karo → **Disable**.
   (Bot group me admin hoga to sab messages waise bhi dikhenge, par Disable safe hai.)

### 2) Database channel banao
1. Telegram me ek **PRIVATE channel** banao (jaise `My Bot DB`).
2. Channel → Administrators → **Add Admin** → apna bot → **saare rights ON** (Post, Pin, Delete, Edit Info).
3. Channel ID nikalo — 2 aasan tarike:
   * Channel me koi ek message post karo, phir browser me kholo:
     `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates`
     → `"channel_post": { "chat": { "id": -100XXXXXXXXXX …` — yahi **DB_CHANNEL** hai.
   * Ya agar channel ka username hai to simply `DB_CHANNEL=@yourchannel` bhi chalega.

### 3) Owner ID nikalo
Apne dost (owner) ke account se **@userinfobot** ko `/start` bhejo → jo number aaye wahi **OWNER_ID** hai.

### 4) Render pe deploy

**Option A — Background Worker (recommended, $7/mo Starter):**
Repo connect karo — `render.yaml` blueprint automatically worker bana dega. Bas env vars bharo.

**Option B — FREE Web Service (0₹):**
Render free plan me background workers nahi milte, isliye:
1. New → **Web Service** → repo connect → Build: `pip install -r requirements.txt` → Start: `python bot.py`
2. Env vars bharo (niche wali table). `PORT` Render khud inject karta hai — bot automatically ek health endpoint chala deta hai (`GET /` → 200 OK).
3. Free web service 15 min bina HTTP traffic ke so jati hai → **[UptimeRobot](https://uptimerobot.com) / [cron-job.org](https://cron-job.org)** pe free account banao aur apne Render URL (`https://<service>.onrender.com`) ko **har 5-10 minute ping** karo. Bot hamesha jaagta rahega. ✅

**Environment variables:**

| Var | Required | Example | Matlab |
|-----|----------|---------|--------|
| `BOT_TOKEN` | ✅ | `123456:ABC-DEF…` | @BotFather token |
| `DB_CHANNEL` | ✅ | `-1001234567890` ya `@mydbchannel` | private database channel |
| `OWNER_ID` | ✅ | `987654321` | dost (owner) ka user id |
| `EXTRA_ADMINS` | ❌ | `111,222` | extra bot-admins |
| `TIMEZONE` | ❌ | `Asia/Kolkata` | schedules/timestamps ka tz |
| `SNAPSHOT_EVERY` | ❌ | `240` | DB channel me save ka interval (sec, min 60) |
| `LOG_USERS` | ❌ | `1` | `0` = naye users channel me post na karo |

### 5) Local pe chalana ho to
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN="..." DB_CHANNEL="-100..." OWNER_ID="..."
python bot.py
```

---

## 🎮 Commands

### 👤 Sab users (PM)
`/start` `/plan` `/about` `/courses` `/contact` `/help` — sab coloured buttons ke saath.

### 👑 Owner (PM)
| Command | Kaam |
|---|---|
| `/panel` | Owner panel (sab buttons se) |
| `/users` | Jinhone `/start` kiya — list + profile + ✉️ message |
| `/msg <id> <text>` | P2P message (ya forwarded msg ko reply + `/msg`) |
| `/broadcast` | Sabko broadcast (text/media forward karke) |
| `/busers` | Blocked users |
| `/stats` | Bot + group stats |
| `/edit` | **Kisi bhi bot settings-message ko REPLY karke** `/edit <new text>` |
| `/setwelcome` `/gwelcome` | PM welcome / default group welcome |
| `/setabout` `/setcourses` `/setcontact` `/setrules` | Texts customize |
| `/rmsg` | Admin-report pe jo reply jaye (report forward NAHI hoti) |
| `/setlinkmsg` `/setbadmsg` `/setverifymsg` `/setfloodmsg` | Guard notices customize |
| `/plans` `/setprice` `/addplan Name \| ₹499 \| detail` | Plans & prices manage |
| `/tz Asia/Kolkata` | Timezone |
| `/backup` `/restore` | Database JSON download / upload |
| `/id` | Apna Telegram id |

### 🛡 Group me (group admins)
| Command | Kaam |
|---|---|
| `/gw <text>` | Group welcome set (`/gw on\|off`) |
| `/pinwel on\|off` | Welcome message pin karo |
| `/filter hello <text>` | Keyword → auto reply (ya **sticker/GIF/photo ko reply karke** `/filter hello`) |
| `/filters` `/stopfilter <word>` | List / remove |
| `/badword <word> [2h\|30m\|90]` | Bad word → delete + mute (default 60m) |
| `/badwords` `/delbadword <word>` | List / remove |
| `/linkguard on\|off` + `/linkmsg <text>` | Link delete + mention notice |
| `/schedule 8:00 PM <text>` | Daily timed post (`/schedule once 9:30 AM <text>` = ek baar) |
| `/schedules` `/delschedule <n>` | List / remove |
| `/warn` `/unwarn` `/warns` (reply) | Warn system |
| `/warnlimit 3` `/warnaction mute\|ban` | Warn policy |
| `/lock` `/unlock` | Group lockdown |
| `/verify on\|off` `/verifytime 10` | Join captcha |
| `/flood on\|off` | Anti-flood |
| `/rules` `/setrules <text>` | Rules |
| `/panel` | Poora control panel PM me (coloured buttons) |
| `/gstats` `/gid` | Group stats / ids |

### 🙋 Sab members (group)
`/report` — kisi message ko reply karke report → owner ke PM me **Ban (1d/7d/30d/Forever) • Mute (10m/1h/12h/1d/1w) • Delete • Dismiss** buttons.
**Admin ko report kiya to** report owner tak NAHI jati — bot `/rmsg` wala set kiya hua message reply kar deta hai.

---

## 💡 Placeholders (welcome/notices me use karo)

`{mention}` (real ping!) • `{name}` • `{firstname}` • `{username}` • `{id}` • `{group}`/`{title}` • `{count}` (members) • `{date}` • `{time}` • `{botname}` • `{duration}` (mute notices me)

Example group welcome:
```
/gw 🎉 Welcome {mention} to {group}!
🎙 Voice & Dubbing family me aapka swagat hai.
📜 Rules: /rules  •  Total members: {count}
```

---

## 🧪 Tests
```bash
pip install pytest
pytest tests -q          # 62 offline tests — state, db-channel roundtrip, guard pipeline, reports, schedules
```

## 🩺 Troubleshooting
* **"not an ADMIN of the DB channel"** → bot ko channel me admin banao, saare rights do.
* **Owner ko commands kaam nahi kar rahe** → `OWNER_ID` sahi hai? Owner ne bot ko `/start` kiya?
* **Group me guard kaam nahi kar raha** → bot group me **admin** hai with *Delete messages* + *Restrict members*? (Add-to-group green button yahi rights maangta hai.)
* **Free Render service so rahi hai** → uptime pinger lagao (upar Option B).
* **Coloured buttons plain dikh rahe** → Telegram app purana hai (Feb 2026 se pehle ka) — buttons kaam karte hain, bas rang nahi dikhte.
* **Data ud gaya?** → `/backup` regularly lo; channel me purane snapshots bhi version-history ki tarah pade rehte hain; `/restore` se wapas aa jata hai.

---

### 📁 Structure
```
bot.py                  entrypoint (boot → channel se DB load → polling → shutdown pe save)
tgguard/config.py       env vars
tgguard/state.py        poora data model (JSON) + path-based editing
tgguard/db.py           ChannelDB — pinned snapshot + pointer + live audit log
tgguard/textfmt.py      {mention} placeholders → UTF-16-safe entities
tgguard/keyboards.py    coloured button helpers (primary/success/danger)
tgguard/timeutil.py     "8:00 PM" parsing, timezone, schedules
tgguard/handlers/       pm_user • pm_owner • group_cmds • engine (guard) • callbacks • screens • render
tests/                  62 offline tests (fake Telegram bot ke saath)
```

Made with 🎙 for voice artists. License: MIT.
