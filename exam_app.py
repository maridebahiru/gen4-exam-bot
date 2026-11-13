"""Telegram Mini App exam, Render free + Firestore (Spark) storage.
Env: BOT_TOKEN, ADMIN_CHAT_ID, ADMIN_KEY (for CSV export), FIREBASE_CREDENTIALS (service-account JSON, one line),
     WEBAPP_URL or RENDER_EXTERNAL_URL (https)."""
import asyncio, csv, hashlib, hmac, io, json, logging, os, random, time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.error import RetryAfter
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx INFO logs print the bot token
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("exam")
BASE = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_FILE = os.path.join(BASE, "questions.json")
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(BASE, "state.json"))
TIME_LIMIT_MINUTES = 30
LEFT_SECONDS = 40          # client pings every 10s; no ping this long = student left
FLUSH_SECONDS = 3          # batch Firestore writes (keeps within Spark 20k writes/day)
ID_PREFIX = "EJAT-"
LETTERS = ["ሀ", "ለ", "ሐ", "መ", "ሠ", "ረ"]
TZ = timezone(timedelta(hours=3))
TOKEN = os.environ.get("BOT_TOKEN", "")
SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()
ADMIN = os.environ.get("ADMIN_CHAT_ID", "").strip()
ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()
QS = json.load(open(QUESTIONS_FILE, encoding="utf-8"))
SESS = {}
DIRTY = set()
DB = None
BOT = APP = URL = None
ADMINQ = STUQ = FLAGQ = None
now = time.time

# ---------- storage ----------
def init_db():
    global DB
    info = os.environ.get("FIREBASE_CREDENTIALS", "").strip()
    if not info:
        log.warning("FIREBASE_CREDENTIALS not set: using local state.json only"); return
    from google.cloud import firestore
    DB = firestore.Client.from_service_account_info(json.loads(info))

def load():
    if DB:
        for d in DB.collection("sessions").stream(): SESS[d.id] = d.to_dict()
    else:
        try: SESS.update(json.load(open(STATE_FILE, encoding="utf-8")))
        except Exception: pass
    t = int(now())
    for s in SESS.values():
        if s["status"] == "active": s["last_seen"] = t   # grace after restart
    log.info("loaded %d sessions", len(SESS))

def save(s): DIRTY.add(str(s["user_id"]))

def flush(docs):
    if not DB:
        json.dump(docs, open(STATE_FILE + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(STATE_FILE + ".tmp", STATE_FILE); return
    items = list(docs.items())
    for i in range(0, len(items), 400):
        b = DB.batch()
        for u, d in items[i:i + 400]: b.set(DB.collection("sessions").document(u), d)
        b.commit()

async def flusher():
    while True:
        await asyncio.sleep(FLUSH_SECONDS)
        if not DIRTY: continue
        ids = list(DIRTY); DIRTY.clear()
        # file fallback needs everything; Firestore only the changed docs
        docs = {u: json.loads(json.dumps(SESS[u])) for u in (SESS if not DB else ids) if u in SESS}
        try: await asyncio.to_thread(flush, docs)
        except Exception:
            log.exception("flush failed"); DIRTY.update(ids)

# ---------- logic ----------
def order(sid):
    o = list(range(len(QS))); random.Random(sid).shuffle(o); return o

def verify(init):
    p = dict(parse_qsl(init or "", keep_blank_values=True)); h = p.pop("hash", None)
    chk = "\n".join(f"{k}={v}" for k, v in sorted(p.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    if not h or not hmac.compare_digest(hmac.new(key, chk.encode(), hashlib.sha256).hexdigest(), h): return None
    if now() - int(p.get("auth_date", 0)) > 86400: return None
    return json.loads(p["user"])

def report(s):
    score, lines = 0, []
    for i in range(len(QS)):
        c = s["answers"].get(str(i))
        if c is None: lines.append(f"{i+1},-")
        else:
            ok = c == QS[i]["answer"]; score += ok
            lines.append(f"{i+1},{LETTERS[c]} {'✓' if ok else '✗'}")
    return score, "\n".join(lines)

WHY = {"done": "✅ ፈተናውን ጨርሷል", "time": "⏰ ሰዓት አልቋል", "left": "⛔ ተማሪው ከፈተናው ወጥቷል (የተመለሱት ገብተዋል)"}

def admin_text(s):
    score, lines = report(s)
    when = datetime.fromtimestamp(s["finished_at"], TZ).strftime("%Y-%m-%d %H:%M")
    used = (s["finished_at"] - s["start"]) // 60
    n = len(s["answers"]); total = len(QS)
    return (f"📝 አዲስ የፈተና ውጤት\n\n👤 {s['name']}\n🔗 {'@'+s['username'] if s['username'] else '-'}\n🆔 {s['user_id']}\n"
            f"🎓 ID: {s['student_id']}\n📊 ውጤት: {score}/{total} ({round(score*100/total)}%)\nየተመለሱ ጥያቄዎች: {n}/{total}\n"
            f"⏱ {used} ደቂቃ\n{WHY[s['reason']]}\n🕒 {when}\n\n{lines}")

def finish(s, reason):
    if s["status"] != "active": return
    s.update(status="finished", reason=reason, finished_at=int(now()), notified=False); save(s)
    score, _ = report(s)
    log.info("RESULT %s %s %s/%s %s", s["student_id"], s["name"], score, len(QS), reason)
    ADMINQ.put_nowait(str(s["user_id"]))
    STUQ.put_nowait((s["user_id"], "⏰ ሰዓቱ አልቋል፤ ፈተናው ተጠናቀቀ።" if reason == "time" else
                     "⛔ ፈተናው ተዘግቷል፤ የመለሷቸው ጥያቄዎች ገብተዋል።\nበስርዓት ወይም በኔትወርክ ችግር ከሆነ አስተዳዳሪውን ያነጋግሩ።" if reason == "left" else "✅ ፈተናውን ጨርሰዋል።"))

async def send(chat, text, markup=None):
    for _ in range(6):
        try: await BOT.send_message(chat, text, reply_markup=markup); return True
        except RetryAfter as e: await asyncio.sleep(e.retry_after + 1)
        except Exception: log.exception("send failed %s", chat); return False
    return False

async def admin_loop():
    while True:
        u = await ADMINQ.get(); s = SESS.get(u)
        if s and not s.get("notified") and ADMIN:
            if await send(int(ADMIN), admin_text(s)): s["notified"] = True; save(s)
        await asyncio.sleep(1.1)       # ~1 msg/s per chat

async def flag_loop():
    while True:
        text = await FLAGQ.get()
        await send(int(ADMIN), text); await asyncio.sleep(1.1)

async def student_loop():
    while True:
        chat, text = await STUQ.get()
        await send(chat, text); await asyncio.sleep(0.05)   # ~20/s global

async def watcher():
    while True:
        await asyncio.sleep(5)
        for s in list(SESS.values()):
            if s["status"] == "active":
                if now() >= s["deadline"]: finish(s, "time")
                elif now() - s["last_seen"] > LEFT_SECONDS: finish(s, "left")

# ---------- http ----------
def questions_for(s):
    return [{"i": i, "q": QS[i]["question"], "o": QS[i]["options"]} for i in order(s["sid"])]

async def api(req):
    d = await req.json(); u = verify(d.get("initData"))
    if not u: return web.json_response({"state": "auth"})
    act, uid = req.match_info["act"], str(u["id"]); s = SESS.get(uid)
    if act == "start":
        sid_txt = " ".join(str(d.get("student_id", "")).split()).upper()
        if s and s["status"] == "active" and s.get("resume"):      # admin re-opened this exam (/reopen)
            if sid_txt != s["student_id"]: return web.json_response({"state": "badid"})
            t = now(); s.update(resume=False, last_seen=t, deadline=t + s.get("resume_secs", 300)); save(s)
            return web.json_response({"state": "run", "left": int(s["deadline"] - t), "sid": s["student_id"],
                                      "questions": questions_for(s), "letters": LETTERS, "answers": s["answers"]})
        if s:
            finish(s, "left"); return web.json_response({"state": "closed"})
        if not (sid_txt.startswith(ID_PREFIX) and 8 <= len(sid_txt) <= 30): return web.json_response({"state": "badid"})
        if any(x["student_id"] == sid_txt and x["user_id"] != u["id"] for x in SESS.values()): return web.json_response({"state": "takenid"})
        t = int(now()); sid = random.randint(10000, 99999)
        s = SESS[uid] = dict(user_id=u["id"], name=(u.get("first_name", "") + " " + u.get("last_name", "")).strip() or "?",
                             username=u.get("username", ""), student_id=sid_txt, sid=sid, start=t, deadline=t + TIME_LIMIT_MINUTES * 60,
                             answers={}, status="active", reason=None, last_seen=t, finished_at=None, notified=False); save(s)
        return web.json_response({"state": "run", "left": s["deadline"] - t, "sid": sid_txt, "questions": questions_for(s), "letters": LETTERS})
    if not s or s["status"] != "active": return web.json_response({"state": "closed"})
    if now() >= s["deadline"]:
        finish(s, "time"); return web.json_response({"state": "closed"})
    if act == "finish":
        for k_, c_ in (d.get("answers") or {}).items():
            try:
                i_, c_ = int(k_), int(c_)
                if 0 <= i_ < len(QS) and 0 <= c_ < len(QS[i_]["options"]): s["answers"][str(i_)] = c_
            except (ValueError, TypeError): pass
        finish(s, "left" if d.get("reason") == "left" else "done"); return web.json_response({"state": "closed"})
    if act == "flag":        # browser saw a screenshot/print key: count it and warn the admin (first 3 per student)
        s["flags"] = s.get("flags", 0) + 1; s["last_seen"] = now(); save(s)
        if s["flags"] <= 3 and ADMIN and FLAGQ is not None:
            FLAGQ.put_nowait(f"📸 ማስጠንቀቂያ: የስክሪን ቀረጻ ሙከራ\n👤 {s['name']}\n🎓 {s['student_id']}\nጥያቄ {int(d.get('q') or 0)} · ሙከራ #{s['flags']} · {str(d.get('kind', ''))[:20]}")
        return web.json_response({"state": "run", "left": int(s["deadline"] - now())})
    s["last_seen"] = now()
    i, c = d.get("i"), d.get("c")
    if isinstance(i, int) and isinstance(c, int) and 0 <= i < len(QS) and 0 <= c < len(QS[i]["options"]):
        s["answers"][str(i)] = c; save(s)
    return web.json_response({"state": "run", "left": int(s["deadline"] - now())})

async def hook(req):
    if req.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET: return web.Response(status=403)
    await APP.process_update(Update.de_json(await req.json(), BOT)); return web.Response(text="ok")

async def export(req):
    if not ADMIN_KEY or not hmac.compare_digest(req.match_info["key"], ADMIN_KEY): return web.Response(status=403)
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["student_id", "name", "username", "telegram_id", "score", "total", "answered", "status", "reason", "minutes", "finished_at", "screenshot_flags"])
    for s in sorted(SESS.values(), key=lambda x: x["student_id"]):
        score, _ = report(s); fin = s.get("finished_at")
        w.writerow([s["student_id"], s["name"], s["username"], s["user_id"], score, len(QS), len(s["answers"]), s["status"], s["reason"] or "",
                    (fin - s["start"]) // 60 if fin else "", datetime.fromtimestamp(fin, TZ).strftime("%Y-%m-%d %H:%M") if fin else "", s.get("flags", 0)])
    return web.Response(body=("\ufeff" + out.getvalue()).encode("utf-8"), content_type="text/csv", charset="utf-8",
                        headers={"Content-Disposition": 'attachment; filename="results.csv"'})

def delete_docs(ids):
    for i in range(0, len(ids), 400):
        b = DB.batch()
        for u in ids[i:i + 400]: b.delete(DB.collection("sessions").document(u))
        b.commit()

async def do_reset(ids):
    for u in ids: SESS.pop(u, None); DIRTY.discard(u)
    if DB and ids: await asyncio.to_thread(delete_docs, ids)
    elif not DB: DIRTY.add("_")
    log.warning("RESET %d sessions", len(ids))

async def reset(req):
    """/reset/<ADMIN_KEY>?all=yes  or  ?uid=<telegram_id>"""
    if not ADMIN_KEY or not hmac.compare_digest(req.match_info["key"], ADMIN_KEY): return web.Response(status=403)
    uid = req.query.get("uid", "").strip()
    ids = list(SESS) if req.query.get("all") == "yes" else ([uid] if uid in SESS else [])
    await do_reset(ids); return web.Response(text=f"deleted {len(ids)} sessions")

async def dbtest(req):
    """/dbtest/<ADMIN_KEY> : writes and reads one test document, shows the exact Firestore error."""
    if not ADMIN_KEY or not hmac.compare_digest(req.match_info["key"], ADMIN_KEY): return web.Response(status=403)
    if not DB: return web.Response(text="NOT CONNECTED: FIREBASE_CREDENTIALS missing or not loaded (server is using state.json only)")
    def t():
        r = DB.collection("diag").document("test"); r.set({"t": int(now())}); return r.get().to_dict()
    try: return web.Response(text=f"OK project={DB.project} wrote+read: {await asyncio.to_thread(t)}\nsessions in memory: {len(SESS)}")
    except Exception as e: return web.Response(text=f"FAILED: {type(e).__name__}: {str(e)[:600]}")

def is_admin(update): return bool(ADMIN) and str(update.effective_user.id) == ADMIN

async def cmd_reset(update: Update, ctx):
    if not is_admin(update): return
    u = str(update.effective_user.id)
    await do_reset([u] if u in SESS else [])
    await update.message.reply_text("✅ የእርስዎ የሙከራ ፈተና ተሰርዟል። /start ይጫኑ።")

async def cmd_resetall(update: Update, ctx):
    if not is_admin(update): return
    if ctx.args != ["yes"]:
        await update.message.reply_text(f"⚠️ {len(SESS)} ተማሪዎችን ሁሉ ይሰርዛል።\nለማረጋገጥ ይላኩ፡ /resetall yes"); return
    n = len(SESS); await do_reset(list(SESS))
    await update.message.reply_text(f"✅ {n} sessions deleted.")

async def cmd_stats(update: Update, ctx):
    if not is_admin(update): return
    a = sum(x["status"] == "active" for x in SESS.values())
    await update.message.reply_text(f"👥 Total: {len(SESS)}\n▶️ Active: {a}\n✅ Finished: {len(SESS) - a}")

def find_sid(txt):
    t = " ".join(str(txt).split()).upper()
    return next((x for x in SESS.values() if x["student_id"] == t), None)

def do_reopen(s, mins=None):
    rem = max(int(s["deadline"] - (s.get("finished_at") or now())), 0)
    secs = mins * 60 if mins else max(rem, 300)
    t = now()
    s.update(status="active", reason=None, finished_at=None, notified=False, resume=True, resume_secs=secs,
             last_seen=t + 900, deadline=t + secs + 900); save(s)
    STUQ.put_nowait((s["user_id"], "✅ ፈተናዎ እንደገና ተከፍቷል፤ የመለሷቸው ተቀምጠዋል። /start ይጫኑና መለያ ቁጥርዎን ያስገቡ።"))
    return f"✅ {s['name']} ({s['student_id']}) reopened: {secs // 60} min, {len(s['answers'])} answers kept."

async def cmd_reopen(update: Update, ctx):
    """/reopen EJAT-001 [minutes] : admin does it directly."""
    if not is_admin(update): return
    a = list(ctx.args); mins = None
    if len(a) > 1 and a[-1].isdigit(): mins = int(a.pop())
    s = find_sid(" ".join(a))
    if not s: await update.message.reply_text("ተማሪው አልተገኘም።\n/reopen EJAT-001 10"); return
    await update.message.reply_text(do_reopen(s, mins))

async def cmd_start(update: Update, ctx):
    s = SESS.get(str(update.effective_user.id))
    if s and s["status"] != "active":
        await update.message.reply_text("ፈተናውን አስቀድመው ጨርሰዋል፤ ደግመው መውሰድ አይችሉም።\n\nበስርዓት ወይም በኔትወርክ ችግር ከተቋረጠ አስተዳዳሪውን ያነጋግሩ።"); return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 ፈተና ጀምር", web_app=WebAppInfo(url=URL))]])
    await update.message.reply_text(
        "እንኳን ደህና መጡ! 👋\n\nይህ የ4ኛ ትውልድ ሱባኤ-ጉባኤ የፈተና ቦት ነው።\n\n📌 ደንቦች\n"
        f"• የጥያቄዎች ብዛት፡ {len(QS)}\n• የተሰጠ ሰዓት፡ {TIME_LIMIT_MINUTES} ደቂቃ\n• ፈተናውን አንድ ጊዜ ብቻ መውሰድ ይችላሉ\n"
        "• ከማስረከብዎ በፊት ወደ ማንኛውም ጥያቄ ተመልሰው መልስዎን መቀየር ይችላሉ\n"
        "• ፈተናው ላይ ሳሉ ወደ ሌላ መተግበሪያ ከወጡ ወይም ቴሌግራምን ካሳነሱ ፈተናው ይዘጋል፤ የመለሷቸው ብቻ ይቆጠራሉ\n"
        "• ጥያቄዎችን ፎቶ ማንሳት አይፈቀድም\n\nለመጀመር ከታች ያለውን ቁልፍ ይጫኑ።", reply_markup=kb)

async def main():
    global APP, BOT, URL, ADMINQ, STUQ, FLAGQ
    if not TOKEN: raise SystemExit("BOT_TOKEN is not set.")
    base = (os.environ.get("WEBAPP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if not base.startswith("https://"): raise SystemExit("Set WEBAPP_URL (https) or run on Render.")
    URL = base + "/"
    ADMINQ, STUQ, FLAGQ = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
    init_db(); load()
    APP = Application.builder().token(TOKEN).build(); BOT = APP.bot
    APP.add_handler(CommandHandler(["start", "exam"], cmd_start))
    APP.add_handler(CommandHandler("reset", cmd_reset))
    APP.add_handler(CommandHandler("resetall", cmd_resetall))
    APP.add_handler(CommandHandler("stats", cmd_stats))
    APP.add_handler(CommandHandler("reopen", cmd_reopen))
    await APP.initialize(); await APP.start()
    await BOT.set_webhook(f"{base}/{SECRET}", secret_token=SECRET)
    w = web.Application()
    w.router.add_get("/", lambda r: web.FileResponse(os.path.join(BASE, "index.html")))
    w.router.add_get("/health", lambda r: web.Response(text="ok"))
    w.router.add_get("/export/{key}", export)
    w.router.add_get("/reset/{key}", reset)
    w.router.add_get("/dbtest/{key}", dbtest)
    w.router.add_post("/api/{act}", api); w.router.add_post(f"/{SECRET}", hook)
    r = web.AppRunner(w); await r.setup()
    await web.TCPSite(r, "0.0.0.0", int(os.environ.get("PORT", "10000"))).start()
    for s in SESS.values():                      # resend results not yet delivered before a restart
        if s["status"] == "finished" and not s.get("notified"): ADMINQ.put_nowait(str(s["user_id"]))
    for f in (watcher, flusher, admin_loop, student_loop, flag_loop): asyncio.create_task(f())
    log.info("running at %s", URL)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
