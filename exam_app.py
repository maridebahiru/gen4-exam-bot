"""Telegram Mini App exam: the exam page runs inside Telegram, so leaving/minimizing it is detected.
Env: BOT_TOKEN, ADMIN_CHAT_ID, (Render sets RENDER_EXTERNAL_URL; or set WEBAPP_URL yourself, must be https)."""
import asyncio, hashlib, hmac, json, logging, os, random, time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CommandHandler

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("exam")
BASE = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_FILE = os.path.join(BASE, "questions.json")
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(BASE, "state.json"))
TIME_LIMIT_MINUTES = 30
LEFT_SECONDS = 20          # no sign of life from the page this long = student left
ALLOW_RETAKE = False
ID_PREFIX = "EJAT-"
LETTERS = ["ሀ", "ለ", "ሐ", "መ", "ሠ", "ረ"]
TZ = timezone(timedelta(hours=3))
TOKEN = os.environ.get("BOT_TOKEN", "")
SECRET = hashlib.sha256(TOKEN.encode()).hexdigest()
QS = json.load(open(QUESTIONS_FILE, encoding="utf-8"))
SESS = {}
BOT = None
now = time.time

def save():
    try:
        json.dump(SESS, open(STATE_FILE + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(STATE_FILE + ".tmp", STATE_FILE)
    except Exception:
        log.exception("save failed")

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

async def finish(s, reason):
    if s["status"] != "active": return
    s.update(status="finished", reason=reason, finished_at=int(now())); save()
    score, lines = report(s)
    why = {"done": "✅ ፈተናውን ጨርሷል", "time": "⏰ ሰዓት አልቋል", "left": "⛔ ተማሪው ከፈተናው ወጥቷል (የተመለሱት ገብተዋል)"}[reason]
    when = datetime.fromtimestamp(s["finished_at"], TZ).strftime("%Y-%m-%d %H:%M")
    used = (s["finished_at"] - s["start"]) // 60
    n = len(s["answers"]); total = len(QS)
    text = (f"📝 አዲስ የፈተና ውጤት\n\n👤 {s['name']}\n🔗 {'@'+s['username'] if s['username'] else '-'}\n🆔 {s['user_id']}\n"
            f"🎓 ID: {s['student_id']}\n📊 ውጤት: {score}/{total} ({round(score*100/total)}%)\nየተመለሱ ጥያቄዎች: {n}/{total}\n"
            f"⏱ {used} ደቂቃ\n{why}\n🕒 {when}\n\n{lines}")
    log.info("RESULT %s %s %s/%s %s", s["student_id"], s["name"], score, total, reason)
    admin = os.environ.get("ADMIN_CHAT_ID", "").strip()
    try:
        if admin: await BOT.send_message(int(admin), text)
        await BOT.send_message(s["user_id"], "⏰ ሰዓቱ አልቋል፤ ፈተናው ተጠናቀቀ።" if reason == "time" else
                               "⛔ ፈተናው ተዘግቷል፤ የመለሷቸው ጥያቄዎች ገብተዋል።" if reason == "left" else "✅ ፈተናውን ጨርሰዋል።")
    except Exception:
        log.exception("notify failed")

async def watcher():
    while True:
        await asyncio.sleep(5)
        for s in list(SESS.values()):
            if s["status"] == "active":
                if now() >= s["deadline"]: await finish(s, "time")
                elif now() - s["last_seen"] > LEFT_SECONDS: await finish(s, "left")

async def api(req):
    d = await req.json(); u = verify(d.get("initData"))
    if not u: return web.json_response({"state": "auth"})
    act, uid = req.match_info["act"], str(u["id"]); s = SESS.get(uid)
    if act == "start":
        if s: 
            await finish(s, "left")
            if not ALLOW_RETAKE or s["status"] == "active": return web.json_response({"state": "closed"})
        sid_txt = " ".join(str(d.get("student_id", "")).split()).upper()
        if not (sid_txt.startswith(ID_PREFIX) and 8 <= len(sid_txt) <= 30): return web.json_response({"state": "badid"})
        if any(x["student_id"] == sid_txt and x["user_id"] != u["id"] for x in SESS.values()): return web.json_response({"state": "takenid"})
        t = int(now()); sid = random.randint(10000, 99999)
        s = SESS[uid] = dict(user_id=u["id"], name=(u.get("first_name", "") + " " + u.get("last_name", "")).strip() or "?",
                             username=u.get("username", ""), student_id=sid_txt, sid=sid, start=t, deadline=t + TIME_LIMIT_MINUTES * 60,
                             answers={}, status="active", reason=None, last_seen=t, finished_at=None); save()
        qs = [{"i": i, "q": QS[i]["question"], "o": QS[i]["options"]} for i in order(sid)]
        return web.json_response({"state": "run", "left": s["deadline"] - t, "sid": sid_txt, "questions": qs, "letters": LETTERS})
    if not s or s["status"] != "active": return web.json_response({"state": "closed"})
    if now() >= s["deadline"]:
        await finish(s, "time"); return web.json_response({"state": "closed"})
    if act == "finish":
        await finish(s, "left" if d.get("reason") == "left" else "done"); return web.json_response({"state": "closed"})
    s["last_seen"] = now()
    i, c = d.get("i"), d.get("c")
    if isinstance(i, int) and isinstance(c, int) and 0 <= i < len(QS) and 0 <= c < len(QS[i]["options"]) and str(i) not in s["answers"]:
        s["answers"][str(i)] = c; save()
    return web.json_response({"state": "run", "left": int(s["deadline"] - now())})

async def hook(req):
    if req.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET: return web.Response(status=403)
    await APP.process_update(Update.de_json(await req.json(), BOT)); return web.Response(text="ok")

async def cmd_start(update: Update, ctx):
    s = SESS.get(str(update.effective_user.id))
    if s and s["status"] != "active" and not ALLOW_RETAKE:
        await update.message.reply_text("ፈተናውን አስቀድመው ጨርሰዋል፤ ደግመው መውሰድ አይችሉም።"); return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🚀 ፈተና ጀምር", web_app=WebAppInfo(url=URL))]])
    await update.message.reply_text(
        "እንኳን ደህና መጡ! 👋\n\nይህ የ4ኛ ትውልድ ሱባኤ-ጉባኤ የፈተና ቦት ነው።\n\n📌 ደንቦች\n"
        f"• የጥያቄዎች ብዛት፡ {len(QS)}\n• የተሰጠ ሰዓት፡ {TIME_LIMIT_MINUTES} ደቂቃ\n• ፈተናውን አንድ ጊዜ ብቻ መውሰድ ይችላሉ\n"
        "• ፈተናው ላይ ሳሉ ወደ ሌላ መተግበሪያ ከወጡ ወይም ቴሌግራምን ካሳነሱ ፈተናው ይዘጋል፤ የመለሷቸው ብቻ ይቆጠራሉ\n"
        "• ጥያቄዎችን ፎቶ ማንሳት አይፈቀድም\n\nለመጀመር ከታች ያለውን ቁልፍ ይጫኑ።", reply_markup=kb)

async def main():
    global APP, BOT, URL
    if not TOKEN: raise SystemExit("BOT_TOKEN is not set.")
    base = (os.environ.get("WEBAPP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if not base.startswith("https://"): raise SystemExit("Set WEBAPP_URL (https) or run on Render.")
    URL = base + "/"
    try: SESS.update(json.load(open(STATE_FILE, encoding="utf-8")))
    except Exception: pass
    APP = Application.builder().token(TOKEN).build(); BOT = APP.bot
    APP.add_handler(CommandHandler(["start", "exam"], cmd_start))
    await APP.initialize(); await APP.start()
    await BOT.set_webhook(f"{base}/{SECRET}", secret_token=SECRET)
    w = web.Application()
    w.router.add_get("/", lambda r: web.FileResponse(os.path.join(BASE, "index.html")))
    w.router.add_post("/api/{act}", api); w.router.add_post(f"/{SECRET}", hook)
    r = web.AppRunner(w); await r.setup()
    await web.TCPSite(r, "0.0.0.0", int(os.environ.get("PORT", "10000"))).start()
    asyncio.create_task(watcher()); log.info("running at %s", URL)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
