#!/usr/bin/env python3
"""
Signal Scout — Premium Telegram Group Management Bot
Welcome/goodbye, ban/kick/mute/warn, anti-spam, blacklist,
keyword filters, notes, locks, analytics, multi-group support.
Cron-triggered via GitHub Actions every 5 min.
"""

import os, json, subprocess, datetime, re, html, time

GROUP_STATE_FILE = "group_state.json"
TOKEN = os.getenv("GROUP_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")

# ─────────────────────────────── HTTP ───────────────────────────────────────

def _post(url, data):
    r = subprocess.run(
        ["curl", "-s", "--max-time", "15", "-X", "POST",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(data, ensure_ascii=False), url],
        capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return {}

def _get(url):
    r = subprocess.run(["curl", "-s", "--max-time", "15", url], capture_output=True)
    try:
        return json.loads(r.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return {}

def api(method, **kw):
    return _post(f"https://api.telegram.org/bot{TOKEN}/{method}", kw)

def api_get(method, **kw):
    qs = "&".join(f"{k}={v}" for k, v in kw.items())
    return _get(f"https://api.telegram.org/bot{TOKEN}/{method}?{qs}")

# ─────────────────────────────── STATE ──────────────────────────────────────

DEFAULT_SETTINGS = {
    "welcome": "👋 Welcome {first_name} to <b>{group}</b>!\nYou are member #<b>{count}</b>.\nPlease read the /rules.",
    "goodbye": "👋 <b>{first_name}</b> has left the group.",
    "rules": "No rules set yet. Ask an admin to use /setrules.",
    "warn_limit": 3,
    "flood_limit": 5,
    "flood_window": 10,
    "blacklist": [],
    "filters": {},
    "locks": {},
    "log_channel": None,
}

def load_state():
    try:
        with open(GROUP_STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_update_id": 0, "groups": {}, "analytics": {}, "users": {}}

def save_state(state):
    with open(GROUP_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    subprocess.run(["git", "config", "user.email", "groupbot@signalscout"], capture_output=True)
    subprocess.run(["git", "config", "user.name",  "Group Bot"], capture_output=True)
    subprocess.run(["git", "add", GROUP_STATE_FILE], capture_output=True)
    r = subprocess.run(["git", "commit", "-m", "chore: group state [skip ci]"], capture_output=True)
    if b"nothing to commit" not in r.stdout + r.stderr:
        subprocess.run(["git", "pull", "--rebase", "--autostash"], capture_output=True)
        subprocess.run(["git", "push"], capture_output=True)

def init_group(state, cid, name=""):
    cid = str(cid)
    if cid not in state.setdefault("groups", {}):
        state["groups"][cid] = {
            "name": name,
            "settings": dict(DEFAULT_SETTINGS),
            "warnings": {},
            "notes": {},
            "flood_tracker": {},
            "member_count": 0,
        }
    elif name:
        state["groups"][cid]["name"] = name
    if cid not in state.setdefault("analytics", {}):
        state["analytics"][cid] = {
            "user_messages": {},
            "daily": {},
            "hourly": {str(h): 0 for h in range(24)},
            "total_messages": 0,
            "joins": 0,
            "leaves": 0,
            "bans": 0,
            "warns": 0,
            "spam_deleted": 0,
            "action_log": [],
        }
    return state["groups"][cid], state["analytics"][cid]

# ─────────────────────────────── HELPERS ────────────────────────────────────

def esc(text):
    return html.escape(str(text)) if text else ""

def now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def user_link(user):
    uid = user.get("id", 0)
    name = esc(user.get("first_name", "User"))
    return f'<a href="tg://user?id={uid}">{name}</a>'

def user_name(user):
    first = user.get("first_name", "")
    last  = user.get("last_name", "")
    return (first + " " + last).strip() if last else first

def send(chat_id, text, reply_to=None, **kw):
    kwargs = {"chat_id": chat_id, "text": text[:4096], "parse_mode": "HTML",
              "disable_web_page_preview": True}
    if reply_to:
        kwargs["reply_to_message_id"] = reply_to
    kwargs.update(kw)
    return api("sendMessage", **kwargs)

def delete_msg(chat_id, message_id):
    api("deleteMessage", chat_id=chat_id, message_id=message_id)

def is_admin(chat_id, user_id):
    r = api_get("getChatMember", chat_id=chat_id, user_id=user_id)
    return r.get("result", {}).get("status", "") in ("administrator", "creator")

def get_admins_list(chat_id):
    r = api_get("getChatAdministrators", chat_id=chat_id)
    return r.get("result", [])

def ban_user(chat_id, user_id, until=None):
    kw = {"chat_id": chat_id, "user_id": user_id}
    if until:
        kw["until_date"] = until
    return api("banChatMember", **kw)

def unban_user(chat_id, user_id):
    return api("unbanChatMember", chat_id=chat_id, user_id=user_id, only_if_banned=True)

def kick_user(chat_id, user_id):
    ban_user(chat_id, user_id)
    unban_user(chat_id, user_id)

def mute_user(chat_id, user_id, until=None):
    perms = {
        "can_send_messages": False,
        "can_send_media_messages": False,
        "can_send_polls": False,
        "can_send_other_messages": False,
        "can_add_web_page_previews": False,
    }
    kw = {"chat_id": chat_id, "user_id": user_id, "permissions": perms}
    if until:
        kw["until_date"] = until
    return api("restrictChatMember", **kw)

def unmute_user(chat_id, user_id):
    perms = {
        "can_send_messages": True,
        "can_send_media_messages": True,
        "can_send_polls": True,
        "can_send_other_messages": True,
        "can_add_web_page_previews": True,
    }
    return api("restrictChatMember", chat_id=chat_id, user_id=user_id, permissions=perms)

def pin_msg(chat_id, message_id, notify=False):
    return api("pinChatMessage", chat_id=chat_id, message_id=message_id,
               disable_notification=not notify)

def unpin_msg(chat_id):
    return api("unpinChatMessage", chat_id=chat_id)

def parse_duration(text):
    m = re.match(r"^(\d+)\s*([smhd])$", text.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]

def track_user(state, user):
    uid = str(user.get("id", ""))
    if uid:
        state.setdefault("users", {})[uid] = {
            "first_name": user.get("first_name", ""),
            "last_name":  user.get("last_name", ""),
            "username":   user.get("username", ""),
        }

def resolve_user(state, arg, reply_user=None):
    """Return (user_id, display_name) from reply or @username/id arg."""
    if reply_user:
        return reply_user.get("id"), user_name(reply_user)
    if not arg:
        return None, None
    arg = arg.lstrip("@")
    if arg.isdigit():
        uid = int(arg)
        u = state.get("users", {}).get(str(uid), {})
        return uid, u.get("first_name") or str(uid)
    for uid, u in state.get("users", {}).items():
        if u.get("username", "").lower() == arg.lower():
            return int(uid), u.get("first_name") or arg
    return None, arg

def log_action(state, cid, action, actor_name, target_name, reason=""):
    entry = {
        "ts": now_iso(), "action": action,
        "actor": actor_name, "target": target_name, "reason": reason,
    }
    log = state["analytics"][str(cid)].setdefault("action_log", [])
    log.append(entry)
    if len(log) > 200:
        state["analytics"][str(cid)]["action_log"] = log[-200:]

    log_ch = state["groups"][str(cid)]["settings"].get("log_channel")
    if log_ch:
        send(log_ch,
             f"📋 <b>{esc(action)}</b>\n"
             f"👮 {esc(actor_name)} → 👤 {esc(target_name)}"
             + (f"\n📝 {esc(reason)}" if reason else ""))

# ─────────────────────────────── ANALYTICS ──────────────────────────────────

def track_message(ana, user_id, username):
    uid   = str(user_id)
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    hour  = str(datetime.datetime.utcnow().hour)
    ana["total_messages"] = ana.get("total_messages", 0) + 1
    ana.setdefault("daily", {})[today] = ana["daily"].get(today, 0) + 1
    ana.setdefault("hourly", {str(h): 0 for h in range(24)})[hour] = \
        ana["hourly"].get(hour, 0) + 1
    um = ana.setdefault("user_messages", {})
    if uid not in um:
        um[uid] = {"count": 0, "username": username, "last_seen": ""}
    um[uid]["count"]     = um[uid].get("count", 0) + 1
    um[uid]["username"]  = username
    um[uid]["last_seen"] = now_iso()

def _week_bar(daily):
    today = datetime.datetime.utcnow()
    days  = [(today - datetime.timedelta(days=i)) for i in range(6, -1, -1)]
    maxv  = max((daily.get(d.strftime("%Y-%m-%d"), 0) for d in days), default=1) or 1
    bar   = ""
    for d in days:
        v = daily.get(d.strftime("%Y-%m-%d"), 0)
        filled = round(v / maxv * 8)
        bar += "█" * filled + "░" * (8 - filled) + f" {d.strftime('%a')} ({v})\n"
    return bar

def format_stats(g, ana, cid):
    name   = g.get("name", "Group")
    total  = ana.get("total_messages", 0)
    joins  = ana.get("joins", 0)
    leaves = ana.get("leaves", 0)
    bans   = ana.get("bans", 0)
    warns  = ana.get("warns", 0)
    spam   = ana.get("spam_deleted", 0)
    hourly = ana.get("hourly", {})
    daily  = ana.get("daily", {})
    peak_h = max(hourly, key=lambda h: hourly.get(h, 0), default="0")
    today  = datetime.datetime.utcnow()
    week_total = sum(daily.get(
        (today - datetime.timedelta(days=i)).strftime("%Y-%m-%d"), 0)
        for i in range(7))
    um = ana.get("user_messages", {})
    top5 = sorted(um.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:5]
    top_lines = ""
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, (uid, info) in enumerate(top5):
        uname = esc(info.get("username") or f"user{uid}")
        top_lines += f"  {medals[i]} {uname} — {info.get('count', 0)} msgs\n"

    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  📊 <b>{esc(name)} — Analytics</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💬 Total messages: <b>{total}</b>\n"
        f"📅 Last 7 days:    <b>{week_total}</b>\n"
        f"⏰ Peak hour:      <b>{peak_h}:00 UTC</b>\n\n"
        f"👥 Joins:  {joins}  ·  Leaves: {leaves}\n"
        f"🔨 Bans:   {bans}  ·  ⚠️ Warns: {warns}  ·  🗑 Spam: {spam}\n\n"
        f"🏆 <b>Top Contributors</b>\n{top_lines or '  No messages yet.'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

def format_top_users(g, ana, n=10):
    name = g.get("name", "Group")
    um   = ana.get("user_messages", {})
    top  = sorted(um.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:n]
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 17
    lines  = [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  🏆 <b>Top {min(n, len(top))} in {esc(name)}</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    for i, (uid, info) in enumerate(top):
        m     = medals[i] if i < len(medals) else f"{i+1}."
        uname = esc(info.get("username") or f"user{uid}")
        cnt   = info.get("count", 0)
        lines.append(f"{m} {uname} — <b>{cnt}</b> messages")
    if not top:
        lines.append("No messages tracked yet.")
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def format_activity(g, ana):
    name   = g.get("name", "Group")
    hourly = ana.get("hourly", {str(h): 0 for h in range(24)})
    daily  = ana.get("daily", {})
    peak_h = max(hourly, key=lambda h: hourly.get(h, 0), default="0")
    maxv   = max(hourly.values(), default=1) or 1
    bar    = ""
    for h in range(0, 24, 3):
        v      = hourly.get(str(h), 0)
        filled = round(v / maxv * 10)
        bar   += f"  {h:02d}h {'█' * filled}{'░' * (10 - filled)} {v}\n"
    week_bar = _week_bar(daily)
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  📈 <b>{esc(name)} — Activity</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Hourly (UTC, 3h slots)</b>\n<code>{bar}</code>\n"
        f"Peak: <b>{peak_h}:00 UTC</b>\n\n"
        f"<b>Last 7 Days</b>\n<code>{week_bar}</code>"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

# ─────────────────────────────── MODERATION ─────────────────────────────────

def add_warn(state, cid, user_id, reason=""):
    g   = state["groups"][str(cid)]
    uid = str(user_id)
    w   = g.setdefault("warnings", {}).setdefault(uid, {"count": 0, "reasons": []})
    w["count"]   = w.get("count", 0) + 1
    if reason:
        w["reasons"].append(reason)
    state["analytics"][str(cid)]["warns"] = \
        state["analytics"][str(cid)].get("warns", 0) + 1
    return w["count"], g["settings"].get("warn_limit", 3)

def reset_warns(state, cid, user_id):
    state["groups"][str(cid)].get("warnings", {}).pop(str(user_id), None)

def check_flood(state, cid, user_id, timestamp):
    g      = state["groups"][str(cid)]
    limit  = g["settings"].get("flood_limit", 5)
    window = g["settings"].get("flood_window", 10)
    uid    = str(user_id)
    ft     = g.setdefault("flood_tracker", {})
    times  = [t for t in ft.get(uid, []) if t > timestamp - window]
    times.append(timestamp)
    ft[uid] = times[-50:]
    return len(times) >= limit

def has_blacklisted_word(text, blacklist):
    if not text or not blacklist:
        return False
    low = text.lower()
    return any(w.lower() in low for w in blacklist)

def match_filter(text, filters):
    if not text or not filters:
        return None
    low = text.lower()
    for kw, response in filters.items():
        if kw.lower() in low:
            return response
    return None

def is_locked_msg(msg, locks):
    if not locks:
        return False
    checks = [
        ("sticker",  msg.get("sticker")),
        ("gif",      msg.get("animation")),
        ("document", msg.get("document")),
        ("video",    msg.get("video")),
        ("photo",    msg.get("photo")),
        ("audio",    msg.get("audio")),
        ("voice",    msg.get("voice")),
        ("contact",  msg.get("contact")),
        ("location", msg.get("location")),
    ]
    for key, val in checks:
        if locks.get(key) and val:
            return True
    if locks.get("link"):
        for e in msg.get("entities", []):
            if e.get("type") in ("url", "text_link"):
                return True
    return False

# ─────────────────────────────── COMMANDS ───────────────────────────────────

def _need_target(state, msg, args):
    reply_u = (msg.get("reply_to_message") or {}).get("from")
    return resolve_user(state, args[0] if args else None, reply_u)

def cmd_ban(state, msg, args, cid, actor):
    uid, uname = _need_target(state, msg, args)
    reason = " ".join(args[1:]) if len(args) > 1 else ""
    if not uid:
        return send(cid, "⚠️ Reply to a message or: /ban @user [reason]", reply_to=msg["message_id"])
    if is_admin(cid, uid):
        return send(cid, "❌ Cannot ban an admin.", reply_to=msg["message_id"])
    ban_user(cid, uid)
    reply_msg = (msg.get("reply_to_message") or {}).get("message_id")
    if reply_msg:
        delete_msg(cid, reply_msg)
    delete_msg(cid, msg["message_id"])
    state["analytics"][str(cid)]["bans"] = state["analytics"][str(cid)].get("bans", 0) + 1
    send(cid, f"🔨 <b>{esc(uname)}</b> banned."
              + (f"\n📝 {esc(reason)}" if reason else "")
              + f"\n👮 By: {esc(user_name(actor))}")
    log_action(state, cid, "BAN", user_name(actor), uname, reason)

def cmd_unban(state, msg, args, cid, actor):
    uid, uname = _need_target(state, msg, args)
    if not uid:
        return send(cid, "⚠️ Usage: /unban @user", reply_to=msg["message_id"])
    unban_user(cid, uid)
    send(cid, f"✅ <b>{esc(uname)}</b> unbanned.")
    log_action(state, cid, "UNBAN", user_name(actor), uname)

def cmd_kick(state, msg, args, cid, actor):
    uid, uname = _need_target(state, msg, args)
    reason = " ".join(args[1:]) if len(args) > 1 else ""
    if not uid:
        return send(cid, "⚠️ Reply to a message or: /kick @user [reason]", reply_to=msg["message_id"])
    if is_admin(cid, uid):
        return send(cid, "❌ Cannot kick an admin.", reply_to=msg["message_id"])
    kick_user(cid, uid)
    delete_msg(cid, msg["message_id"])
    send(cid, f"👢 <b>{esc(uname)}</b> kicked."
              + (f"\n📝 {esc(reason)}" if reason else "")
              + f"\n👮 By: {esc(user_name(actor))}")
    log_action(state, cid, "KICK", user_name(actor), uname, reason)

def cmd_mute(state, msg, args, cid, actor):
    uid, uname = _need_target(state, msg, args)
    if not uid:
        return send(cid, "⚠️ Reply to a message or: /mute @user [1h/30m/2d] [reason]",
                    reply_to=msg["message_id"])
    if is_admin(cid, uid):
        return send(cid, "❌ Cannot mute an admin.", reply_to=msg["message_id"])
    rest   = args[1:] if len(args) > 1 else []
    until  = None
    reason = ""
    dur_str = ""
    if rest:
        secs = parse_duration(rest[0])
        if secs:
            until   = int(time.time()) + secs
            dur_str = f" for {rest[0]}"
            reason  = " ".join(rest[1:])
        else:
            reason = " ".join(rest)
    mute_user(cid, uid, until)
    delete_msg(cid, msg["message_id"])
    send(cid, f"🔇 <b>{esc(uname)}</b> muted{dur_str}."
              + (f"\n📝 {esc(reason)}" if reason else "")
              + f"\n👮 By: {esc(user_name(actor))}")
    log_action(state, cid, f"MUTE{dur_str}", user_name(actor), uname, reason)

def cmd_unmute(state, msg, args, cid, actor):
    uid, uname = _need_target(state, msg, args)
    if not uid:
        return send(cid, "⚠️ Reply to a message or: /unmute @user", reply_to=msg["message_id"])
    unmute_user(cid, uid)
    send(cid, f"🔊 <b>{esc(uname)}</b> unmuted.")
    log_action(state, cid, "UNMUTE", user_name(actor), uname)

def cmd_warn(state, msg, args, cid, actor):
    uid, uname = _need_target(state, msg, args)
    reason = " ".join(args[1:]) if len(args) > 1 else ""
    if not uid:
        return send(cid, "⚠️ Reply to a message or: /warn @user [reason]", reply_to=msg["message_id"])
    if is_admin(cid, uid):
        return send(cid, "❌ Cannot warn an admin.", reply_to=msg["message_id"])
    count, limit = add_warn(state, cid, uid, reason)
    if count >= limit:
        ban_user(cid, uid)
        state["analytics"][str(cid)]["bans"] = state["analytics"][str(cid)].get("bans", 0) + 1
        send(cid, f"🔨 <b>{esc(uname)}</b> auto-banned after {count} warnings!")
        reset_warns(state, cid, uid)
    else:
        send(cid, f"⚠️ <b>{esc(uname)}</b> warned ({count}/{limit})."
                  + (f"\n📝 {esc(reason)}" if reason else "")
                  + f"\n<i>Auto-ban at {limit} warnings.</i>")
    log_action(state, cid, f"WARN {count}/{limit}", user_name(actor), uname, reason)

def cmd_resetwarns(state, msg, args, cid, actor):
    uid, uname = _need_target(state, msg, args)
    if not uid:
        return send(cid, "⚠️ Reply to a message or: /resetwarns @user", reply_to=msg["message_id"])
    reset_warns(state, cid, uid)
    send(cid, f"✅ Warnings cleared for <b>{esc(uname)}</b>.")
    log_action(state, cid, "RESETWARNS", user_name(actor), uname)

def cmd_warns(state, msg, args, cid):
    uid, uname = _need_target(state, msg, args)
    if not uid:
        uid   = msg.get("from", {}).get("id")
        uname = user_name(msg.get("from", {}))
    g     = state["groups"][str(cid)]
    w     = g.get("warnings", {}).get(str(uid), {})
    count = w.get("count", 0)
    limit = g["settings"].get("warn_limit", 3)
    reasons = w.get("reasons", [])
    text  = f"⚠️ <b>{esc(uname)}</b>: {count}/{limit} warnings"
    if reasons:
        text += "\n" + "\n".join(f"  • {esc(r)}" for r in reasons[-5:])
    send(cid, text, reply_to=msg["message_id"])

def cmd_pin(state, msg, args, cid):
    reply = msg.get("reply_to_message")
    if not reply:
        return send(cid, "⚠️ Reply to a message to pin it.", reply_to=msg["message_id"])
    notify = bool(args) and "notify" in args[0].lower()
    pin_msg(cid, reply["message_id"], notify=notify)
    delete_msg(cid, msg["message_id"])

def cmd_unpin(msg, cid):
    unpin_msg(cid)
    delete_msg(cid, msg["message_id"])

def cmd_del(msg, cid):
    reply = msg.get("reply_to_message")
    if reply:
        delete_msg(cid, reply["message_id"])
    delete_msg(cid, msg["message_id"])

def cmd_setwelcome(state, msg, args, cid):
    text = " ".join(args)
    if not text:
        reply = msg.get("reply_to_message", {})
        text  = reply.get("text", "")
    if not text:
        cur = state["groups"][str(cid)]["settings"]["welcome"]
        return send(cid,
            f"⚠️ /setwelcome [message]\n"
            f"Variables: {{first_name}} {{username}} {{group}} {{count}}\n\n"
            f"<b>Current:</b>\n{esc(cur)}", reply_to=msg["message_id"])
    state["groups"][str(cid)]["settings"]["welcome"] = text
    send(cid, f"✅ Welcome message updated!\n\n<b>Preview:</b>\n{text[:300]}")

def cmd_setgoodbye(state, msg, args, cid):
    text = " ".join(args)
    if not text:
        return send(cid, "⚠️ /setgoodbye [message]\nVariables: {first_name} {username}",
                    reply_to=msg["message_id"])
    state["groups"][str(cid)]["settings"]["goodbye"] = text
    send(cid, "✅ Goodbye message updated!")

def cmd_setrules(state, msg, args, cid):
    text = " ".join(args)
    if not text:
        reply = msg.get("reply_to_message", {})
        text  = reply.get("text", "")
    if not text:
        return send(cid, "⚠️ /setrules [rules text]", reply_to=msg["message_id"])
    state["groups"][str(cid)]["settings"]["rules"] = text
    send(cid, "✅ Rules updated! Use /rules to view them.")

def cmd_rules(state, msg, cid):
    rules = state["groups"][str(cid)]["settings"].get("rules", "No rules set.")
    send(cid, f"📜 <b>Group Rules</b>\n\n{esc(rules)}", reply_to=msg["message_id"])

def cmd_addfilter(state, msg, args, cid):
    if len(args) < 2:
        return send(cid, "⚠️ /filter [keyword] [response]", reply_to=msg["message_id"])
    kw       = args[0].lower()
    response = " ".join(args[1:])
    state["groups"][str(cid)]["settings"]["filters"][kw] = response
    send(cid, f"✅ Filter added: <code>{esc(kw)}</code> → {esc(response[:100])}")

def cmd_stopfilter(state, msg, args, cid):
    if not args:
        return send(cid, "⚠️ /stop [keyword]", reply_to=msg["message_id"])
    kw      = args[0].lower()
    removed = state["groups"][str(cid)]["settings"]["filters"].pop(kw, None)
    if removed:
        send(cid, f"✅ Filter removed: <code>{esc(kw)}</code>")
    else:
        send(cid, f"❌ Filter not found: <code>{esc(kw)}</code>")

def cmd_filters(state, msg, cid):
    filters = state["groups"][str(cid)]["settings"].get("filters", {})
    if not filters:
        return send(cid, "No filters active.", reply_to=msg["message_id"])
    lines = "\n".join(f"  • <code>{esc(k)}</code> → {esc(v[:60])}"
                      for k, v in list(filters.items())[:25])
    send(cid, f"🔎 <b>Active Filters ({len(filters)})</b>\n{lines}")

def cmd_blacklist(state, msg, args, cid, add=True):
    bl = state["groups"][str(cid)]["settings"].setdefault("blacklist", [])
    if not args:
        if not bl:
            return send(cid, "Blacklist is empty.", reply_to=msg["message_id"])
        return send(cid, "🚫 <b>Blacklist</b>\n" + "\n".join(f"  • {esc(w)}" for w in bl[:30]))
    word = " ".join(args).lower().strip()
    if add:
        if word not in bl:
            bl.append(word)
        send(cid, f"✅ Added to blacklist: <code>{esc(word)}</code>")
    else:
        if word in bl:
            bl.remove(word)
            send(cid, f"✅ Removed from blacklist: <code>{esc(word)}</code>")
        else:
            send(cid, f"❌ Not in blacklist: <code>{esc(word)}</code>")

LOCKABLE = ["sticker", "gif", "link", "document", "video", "photo", "audio", "voice", "all"]

def cmd_lock(state, msg, args, cid, lock=True):
    locks = state["groups"][str(cid)]["settings"].setdefault("locks", {})
    if not args or args[0].lower() not in LOCKABLE:
        active = [k for k, v in locks.items() if v]
        return send(cid,
            f"{'🔒' if lock else '🔓'} <b>Content Locks</b>\n"
            f"Active: {', '.join(active) or 'none'}\n"
            f"Types: {', '.join(LOCKABLE[:-1])}\n\n"
            f"/lock [type]   /unlock [type]",
            reply_to=msg["message_id"])
    ltype = args[0].lower()
    if ltype == "all":
        for t in LOCKABLE[:-1]:
            locks[t] = lock
    else:
        locks[ltype] = lock
    icon = "🔒" if lock else "🔓"
    send(cid, f"{icon} <b>{ltype.title()}</b> {'locked' if lock else 'unlocked'}.")

def cmd_note(state, msg, args, cid):
    reply = msg.get("reply_to_message", {})
    if len(args) == 1 and reply:
        name    = args[0].lower()
        content = reply.get("text") or reply.get("caption") or "[media]"
        state["groups"][str(cid)].setdefault("notes", {})[name] = content
        return send(cid, f"✅ Note <code>{esc(name)}</code> saved!", reply_to=msg["message_id"])
    if len(args) >= 2:
        name    = args[0].lower()
        content = " ".join(args[1:])
        state["groups"][str(cid)].setdefault("notes", {})[name] = content
        return send(cid, f"✅ Note <code>{esc(name)}</code> saved!")
    send(cid, "⚠️ /note [name] [content]  or reply to a message with /note [name]",
         reply_to=msg["message_id"])

def cmd_get_note(state, msg, cid, name):
    note = state["groups"][str(cid)].get("notes", {}).get(name.lower())
    if note:
        send(cid, note)
    else:
        send(cid, f"❌ Note <code>{esc(name)}</code> not found.", reply_to=msg["message_id"])

def cmd_notes(state, msg, cid):
    notes = state["groups"][str(cid)].get("notes", {})
    if not notes:
        return send(cid, "No notes saved yet.", reply_to=msg["message_id"])
    keys  = sorted(notes)[:30]
    lines = "\n".join(f"  • <code>{esc(k)}</code>" for k in keys)
    send(cid, f"📝 <b>Saved Notes ({len(notes)})</b>\n{lines}\n\n"
              f"Use /get [name] or #name to retrieve.")

def cmd_clear_note(state, msg, args, cid):
    if not args:
        return send(cid, "⚠️ /clear [note_name]", reply_to=msg["message_id"])
    name    = args[0].lower()
    removed = state["groups"][str(cid)].get("notes", {}).pop(name, None)
    send(cid, f"✅ Deleted <code>{esc(name)}</code>." if removed
              else f"❌ Note not found: <code>{esc(name)}</code>")

def cmd_setflood(state, msg, args, cid):
    g  = state["groups"][str(cid)]
    fl = g["settings"].get("flood_limit", 5)
    fw = g["settings"].get("flood_window", 10)
    if not args or not args[0].isdigit():
        return send(cid, f"🌊 Flood limit: {fl} msgs / {fw} sec.\n/setflood [n]",
                    reply_to=msg["message_id"])
    g["settings"]["flood_limit"] = max(3, min(int(args[0]), 50))
    send(cid, f"✅ Flood limit: {g['settings']['flood_limit']} messages per {fw}s.")

def cmd_setwarnlimit(state, msg, args, cid):
    if not args or not args[0].isdigit():
        wl = state["groups"][str(cid)]["settings"].get("warn_limit", 3)
        return send(cid, f"⚠️ Warn limit: {wl}\n/setwarnlimit [number]",
                    reply_to=msg["message_id"])
    n = max(1, min(int(args[0]), 10))
    state["groups"][str(cid)]["settings"]["warn_limit"] = n
    send(cid, f"✅ Warn limit set to {n}.")

def cmd_setlog(state, msg, args, cid):
    if not args:
        ch = state["groups"][str(cid)]["settings"].get("log_channel")
        return send(cid, f"📋 Log channel: {ch or 'not set'}\n/setlog [channel_id]",
                    reply_to=msg["message_id"])
    state["groups"][str(cid)]["settings"]["log_channel"] = args[0]
    send(cid, f"✅ Log channel set to <code>{esc(args[0])}</code>.")

def cmd_admins(cid):
    admins = get_admins_list(cid)
    if not admins:
        return send(cid, "⚠️ Could not fetch admin list. Is the bot an admin?")
    lines = []
    for a in admins[:20]:
        u      = a.get("user", {})
        status = a.get("status", "")
        title  = a.get("custom_title", "")
        icon   = "👑" if status == "creator" else "👮"
        link   = user_link(u)
        lines.append(f"  {icon} {link}" + (f" <i>— {esc(title)}</i>" if title else ""))
    send(cid, f"👮 <b>Admins ({len(admins)})</b>\n" + "\n".join(lines))

def cmd_modlog(state, msg, cid, n=15):
    log = state["analytics"][str(cid)].get("action_log", [])
    if not log:
        return send(cid, "No moderation actions logged yet.", reply_to=msg["message_id"])
    recent = log[-n:]
    lines  = [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  📋 <b>Mod Log (last {len(recent)})</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    for e in reversed(recent):
        ts     = e.get("ts", "")[:16].replace("T", " ")
        action = esc(e.get("action", ""))
        actor  = esc(e.get("actor", ""))
        target = esc(e.get("target", ""))
        reason = esc(e.get("reason", ""))
        lines.append(f"[{ts}] <b>{action}</b>\n  {actor} → {target}"
                     + (f"\n  📝 {reason}" if reason else ""))
    send(cid, "\n".join(lines))

def cmd_help(cid, admin=False):
    admin_section = (
        "\n<b>👮 Moderation</b>\n"
        "/ban @user [reason]    — ban user\n"
        "/unban @user           — unban user\n"
        "/kick @user [reason]   — kick (no ban)\n"
        "/mute @user [1h] [r]   — mute user\n"
        "/unmute @user          — unmute user\n"
        "/warn @user [reason]   — warn (+auto-ban)\n"
        "/resetwarns @user      — clear warnings\n"
        "/del                   — delete replied msg\n"
        "/pin  [notify]         — pin replied msg\n"
        "/unpin                 — unpin\n\n"
        "<b>⚙️ Settings</b>\n"
        "/setwelcome [text]     — welcome message\n"
        "/setgoodbye [text]     — goodbye message\n"
        "/setrules [text]       — set group rules\n"
        "/setwarnlimit [n]      — warn threshold\n"
        "/setflood [n]          — flood msg limit\n"
        "/setlog [channel_id]   — log channel\n\n"
        "<b>🔒 Locks</b>\n"
        "/lock [type]           — lock content type\n"
        "/unlock [type]         — unlock\n"
        "  Types: sticker gif link document video photo\n\n"
        "<b>🔎 Filters & Blacklist</b>\n"
        "/filter [word] [resp]  — keyword auto-reply\n"
        "/stop [word]           — remove filter\n"
        "/filters               — list all filters\n"
        "/blacklist [word]      — add to blacklist\n"
        "/unblacklist [word]    — remove from blacklist\n\n"
        "<b>📝 Notes</b>\n"
        "/note [name] [text]    — save a note\n"
        "/clear [name]          — delete note\n"
        "/modlog                — recent mod actions\n"
    ) if admin else ""

    send(cid,
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  🤖 <b>Signal Scout Group Bot</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>📊 Analytics & Info</b>\n"
        f"/stats                 — group analytics\n"
        f"/activity              — hourly/weekly chart\n"
        f"/top [n]               — top active users\n"
        f"/rules                 — show group rules\n"
        f"/warns [@user]         — view warnings\n"
        f"/notes                 — list saved notes\n"
        f"/get [name] or #name   — retrieve a note\n"
        f"/admins                — list admins\n"
        + admin_section +
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Add me as admin to unlock moderation.</i>"
    )

# ─────────────────────────────── WELCOME / GOODBYE ──────────────────────────

def handle_new_members(state, msg, cid):
    g, ana = state["groups"][str(cid)], state["analytics"][str(cid)]
    for member in msg.get("new_chat_members", []):
        if member.get("is_bot"):
            continue
        track_user(state, member)
        ana["joins"] = ana.get("joins", 0) + 1
        g["member_count"] = g.get("member_count", 0) + 1
        tpl = g["settings"].get("welcome", "")
        if tpl:
            text = tpl
            text = text.replace("{first_name}", esc(member.get("first_name", "User")))
            text = text.replace("{username}",   esc(member.get("username", "user")))
            text = text.replace("{group}",      esc(g.get("name", "this group")))
            text = text.replace("{count}",      str(g.get("member_count", "?")))
            send(cid, text)

def handle_left_member(state, msg, cid):
    g, ana = state["groups"][str(cid)], state["analytics"][str(cid)]
    member = msg.get("left_chat_member", {})
    if member.get("is_bot"):
        return
    ana["leaves"] = ana.get("leaves", 0) + 1
    g["member_count"] = max(0, g.get("member_count", 1) - 1)
    tpl = g["settings"].get("goodbye", "")
    if tpl:
        text = tpl.replace("{first_name}", esc(member.get("first_name", "User")))
        text = text.replace("{username}",  esc(member.get("username", "user")))
        send(cid, text)

# ─────────────────────────────── MAIN PROCESSOR ─────────────────────────────

def process_message(state, msg):
    chat      = msg.get("chat", {})
    chat_type = chat.get("type", "")
    cid       = chat.get("id")

    if not cid or chat_type not in ("group", "supergroup"):
        return

    g, ana = init_group(state, cid, chat.get("title", ""))
    sender  = msg.get("from", {})
    user_id = sender.get("id")
    if not user_id or sender.get("is_bot"):
        return

    track_user(state, sender)

    # Service messages
    if msg.get("new_chat_members"):
        handle_new_members(state, msg, cid)
        return
    if msg.get("left_chat_member"):
        handle_left_member(state, msg, cid)
        return

    username = sender.get("username") or user_name(sender)
    track_message(ana, user_id, username)

    text      = (msg.get("text") or msg.get("caption") or "").strip()
    timestamp = msg.get("date", 0)
    admin     = is_admin(cid, user_id)

    # ── Non-admin moderation ────────────────────────────────────────────────
    if not admin:
        if check_flood(state, cid, user_id, timestamp):
            delete_msg(cid, msg["message_id"])
            ana["spam_deleted"] = ana.get("spam_deleted", 0) + 1
            count, limit = add_warn(state, cid, user_id, "flooding")
            if count >= limit:
                ban_user(cid, user_id)
                ana["bans"] = ana.get("bans", 0) + 1
                send(cid, f"🔨 {user_link(sender)} banned for flooding!")
                reset_warns(state, cid, user_id)
            else:
                send(cid, f"⚠️ {user_link(sender)}, slow down! ({count}/{limit} warnings)")
            return

        if has_blacklisted_word(text, g["settings"].get("blacklist", [])):
            delete_msg(cid, msg["message_id"])
            ana["spam_deleted"] = ana.get("spam_deleted", 0) + 1
            count, limit = add_warn(state, cid, user_id, "blacklisted word")
            send(cid, f"🚫 {user_link(sender)}, that word is not allowed! ({count}/{limit})")
            return

        if is_locked_msg(msg, g["settings"].get("locks", {})):
            delete_msg(cid, msg["message_id"])
            ana["spam_deleted"] = ana.get("spam_deleted", 0) + 1
            return

    # ── Commands ────────────────────────────────────────────────────────────
    if text.startswith("/"):
        parts = text.split()
        cmd   = parts[0].lstrip("/").split("@")[0].lower()
        args  = parts[1:]

        # Admin-only commands
        if admin:
            if   cmd == "ban":           cmd_ban(state, msg, args, cid, sender)
            elif cmd == "unban":         cmd_unban(state, msg, args, cid, sender)
            elif cmd == "kick":          cmd_kick(state, msg, args, cid, sender)
            elif cmd == "mute":          cmd_mute(state, msg, args, cid, sender)
            elif cmd == "unmute":        cmd_unmute(state, msg, args, cid, sender)
            elif cmd == "warn":          cmd_warn(state, msg, args, cid, sender)
            elif cmd == "resetwarns":    cmd_resetwarns(state, msg, args, cid, sender)
            elif cmd == "pin":           cmd_pin(state, msg, args, cid)
            elif cmd == "unpin":         cmd_unpin(msg, cid)
            elif cmd == "del":           cmd_del(msg, cid)
            elif cmd == "setwelcome":    cmd_setwelcome(state, msg, args, cid)
            elif cmd == "setgoodbye":    cmd_setgoodbye(state, msg, args, cid)
            elif cmd == "setrules":      cmd_setrules(state, msg, args, cid)
            elif cmd == "setwarnlimit":  cmd_setwarnlimit(state, msg, args, cid)
            elif cmd == "setflood":      cmd_setflood(state, msg, args, cid)
            elif cmd == "setlog":        cmd_setlog(state, msg, args, cid)
            elif cmd == "filter":        cmd_addfilter(state, msg, args, cid)
            elif cmd == "stop":          cmd_stopfilter(state, msg, args, cid)
            elif cmd == "filters":       cmd_filters(state, msg, cid)
            elif cmd == "blacklist":     cmd_blacklist(state, msg, args, cid, add=True)
            elif cmd == "unblacklist":   cmd_blacklist(state, msg, args, cid, add=False)
            elif cmd == "lock":          cmd_lock(state, msg, args, cid, lock=True)
            elif cmd == "unlock":        cmd_lock(state, msg, args, cid, lock=False)
            elif cmd in ("note", "save"):cmd_note(state, msg, args, cid)
            elif cmd == "clear":         cmd_clear_note(state, msg, args, cid)
            elif cmd == "modlog":        cmd_modlog(state, msg, cid)

        # Public commands (any member)
        if   cmd in ("start", "help"):   cmd_help(cid, admin=admin)
        elif cmd == "rules":             cmd_rules(state, msg, cid)
        elif cmd == "warns":             cmd_warns(state, msg, args, cid)
        elif cmd == "stats":
            send(cid, format_stats(g, ana, cid))
        elif cmd == "activity":
            send(cid, format_activity(g, ana))
        elif cmd == "top":
            n = int(args[0]) if args and args[0].isdigit() else 10
            send(cid, format_top_users(g, ana, min(n, 20)))
        elif cmd in ("get", "getnote"):
            if args:
                cmd_get_note(state, msg, cid, args[0])
        elif cmd == "notes":
            cmd_notes(state, msg, cid)
        elif cmd == "admins":
            cmd_admins(cid)
        return

    # ── Hashtag note shortcut: #notename ────────────────────────────────────
    if text.startswith("#") and len(text) > 1:
        note_name = text[1:].split()[0].lower()
        note = g.get("notes", {}).get(note_name)
        if note:
            send(cid, note)
            return

    # ── Keyword filters (non-command messages) ───────────────────────────────
    if text:
        response = match_filter(text, g["settings"].get("filters", {}))
        if response:
            send(cid, response, reply_to=msg["message_id"])

# ─────────────────────────────── ENTRY POINT ────────────────────────────────

def main():
    if not TOKEN:
        print("No GROUP_BOT_TOKEN or TELEGRAM_BOT_TOKEN set.")
        return

    state  = load_state()
    offset = state.get("last_update_id", 0) + 1
    resp   = _get(
        f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        f"?offset={offset}&limit=100&timeout=0&allowed_updates=[\"message\"]"
    )
    updates = resp.get("result", [])

    if not updates:
        print("No new updates.")
        return

    changed = False
    for upd in updates:
        uid = upd.get("update_id")
        if uid is not None:
            state["last_update_id"] = uid
            changed = True
        msg = upd.get("message") or upd.get("edited_message")
        if msg:
            try:
                process_message(state, msg)
            except Exception as e:
                print(f"Error on update {uid}: {e}")

    if changed:
        save_state(state)

    print(f"Processed {len(updates)} update(s).")


if __name__ == "__main__":
    main()
