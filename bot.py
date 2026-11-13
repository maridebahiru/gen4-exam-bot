"""Telegram exam bot: timed (30 minutes), one attempt per student.

Works two ways with the same file:
- On your computer (no RENDER_EXTERNAL_URL / WEBHOOK_BASE_URL set): polling mode.
- On Render (RENDER_EXTERNAL_URL is set automatically): webhook mode.

How it works
- Each student has ONE exam session. Pressing Start again only resumes it, so
  questions can no longer repeat or mix.
- The 30-minute timer starts when the student presses Start. When time is up the
  answered questions are submitted automatically and the student is told the exam
  has finished. The admin gets the result either way.
- Question messages are sent with protect_content (no forwarding / saving), and an
  answered question is replaced by a short line so the questions do not pile up in the chat.
- Progress is saved to state.json. It is also carried inside the buttons, so a
  student can continue even if the free server was asleep or restarted.

Environment variables:
    BOT_TOKEN       required, from @BotFather
    ADMIN_CHAT_ID   your Telegram numeric ID; every result is sent here
    STATE_FILE      optional, where progress is saved (default: state.json next to bot.py)
"""

import hashlib
import json
import logging
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Settings you can change ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_FILE = os.path.join(BASE_DIR, "questions.json")
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(BASE_DIR, "state.json"))
NUM_QUESTIONS = None     # questions per exam; None = ask all of them
SHUFFLE_QUESTIONS = True
PASS_PERCENT = 50        # pass mark in percent
TIME_LIMIT_MINUTES = 30  # whole exam must be finished within this time
PER_QUESTION_SECONDS = 0  # no answer within this time = student left; exam closes, answered ones are kept (0 = off)
MAX_SKIPS_IN_ROW = 3     # this many unanswered questions in a row = student left, exam closes
ALLOW_RETAKE = False     # False = each student can take the exam only once
PROTECT_CONTENT = True   # True = questions cannot be forwarded or saved
SHOW_ANSWERS = False     # False = students never see right/wrong or the correct answer
ID_PREFIX = "EJAT-"  # every student ID must start with this
ADDIS_TZ = timezone(timedelta(hours=3))  # Ethiopia time, used in result messages

# ---------- Texts (edit freely) ----------
TXT_START_BTN = "🚀 ፈተና ጀምር"
TXT_QUESTION = "ጥያቄ"
TXT_TIME_LEFT = "⏱ {m} ደቂቃ ቀርቷል"
TXT_CORRECT = "✅ ትክክል!"
TXT_WRONG = "❌ ስህተት። ትክክለኛው መልስ"
TXT_YOUR_ANSWER = "የእርስዎ መልስ"
TXT_RECORDED = "✔️ መልስዎ ተመዝግቧል"
TXT_RESULT = "📊 ውጤት"
TXT_PASSED = "🎉 አልፈዋል!"
TXT_FAILED = "አልተሳካም።"
TXT_ANSWERED = "የተመለሱ ጥያቄዎች"
TXT_FINISHED = "✅ ፈተናውን ጨርሰዋል።"
TXT_TIME_UP = "⏰ ሰዓቱ አልቋል። የመለሷቸው ጥያቄዎች ገብተዋል፤ ፈተናው ተጠናቀቀ።"
TXT_TIME_UP_SHORT = "⏰ ሰዓቱ አልቋል"
TXT_ALREADY_DONE = "ፈተናውን አስቀድመው ጨርሰዋል፤ ደግመው መውሰድ አይችሉም።"
TXT_STALE = "ይህ ጥያቄ አስቀድሞ ተመልሷል።"
TXT_RESUME = "▶️ ፈተናዎ ቀጥሏል። ያልተመለሰው ጥያቄ ከታች ነው።"
TXT_ADMIN_TITLE = "📝 አዲስ የፈተና ውጤት"
TXT_ASK_ID = "🎓 እባክዎ የተማሪ መለያ ቁጥርዎን (ID) ይጻፉና ይላኩ። መለያ ቁጥሩ በ EJAT- ይጀምራል (ለምሳሌ፡ EJAT-001/2/...)። ከላኩ በኋላ ፈተናው ይጀምራል።"
TXT_ID_BAD = "⚠️ መለያ ቁጥሩ ትክክል አይደለም። በ EJAT- ይጀምራል (ለምሳሌ፡ EJAT-001/2/...)። እባክዎ እንደገና ይጻፉ።"
TXT_ID_TAKEN = "⚠️ ይህ መለያ ቁጥር ቀድሞ ጥቅም ላይ ውሏል። ትክክለኛውን ቁጥርዎን ይጻፉ።"
TXT_NEED_START = "ፈተና ለመጀመር /start ይጫኑ።"
TXT_LEFT = "⛔ ለረጅም ጊዜ ምላሽ አልሰጡም። የመለሷቸው ጥያቄዎች ገብተዋል፤ ፈተናው ተጠናቀቀ።"
TXT_CLOSED_LEFT = "⛔ ጥያቄው ተዘግቷል"
TXT_REASON_LEFT = "⛔ ተማሪው ከቦቱ ወጥቷል / መልስ አልሰጠም (የተመለሱት ገብተዋል)"
TXT_REASON_DONE = "✅ ፈተናውን ጨርሷል"
TXT_REASON_TIME = "⏰ ሰዓት አልቋል (የተመለሱት በራስ-ሰር ገብተዋል)"

LETTERS = ["ሀ", "ለ", "ሐ", "መ", "ሠ", "ረ"]  # same letters as the paper exam

# user id (as text) -> exam session. Saved to STATE_FILE after every change.
STATE = {"sessions": {}}
_QUESTIONS = None
JOBQ = None  # set at start-up; used for the per-question inactivity timer


def now():
    return time.time()


# ---------- Questions ----------
def get_questions():
    global _QUESTIONS
    if _QUESTIONS is None:
        with open(QUESTIONS_FILE, encoding="utf-8") as f:
            _QUESTIONS = json.load(f)
    return _QUESTIONS


def validate_questions():
    """Stop with a clear message if questions.json has a mistake."""
    try:
        questions = get_questions()
    except (OSError, ValueError) as e:
        raise SystemExit(f"Cannot read questions.json: {e}")
    if not isinstance(questions, list) or not questions:
        raise SystemExit("questions.json must be a non-empty list of questions.")
    if len(questions) > 40:
        logger.warning("More than 40 questions: button data may exceed Telegram's 64-byte limit.")
    seen = set()
    for n, q in enumerate(questions, 1):
        ok = (
            isinstance(q, dict)
            and isinstance(q.get("question"), str)
            and q["question"].strip()
            and isinstance(q.get("options"), list)
            and 2 <= len(q["options"]) <= len(LETTERS)
            and all(isinstance(o, str) and o.strip() for o in q["options"])
            and isinstance(q.get("answer"), int)
            and 0 <= q["answer"] < len(q["options"])
        )
        if not ok:
            raise SystemExit(
                f"questions.json: question number {n} is not valid. It needs a question, "
                f"2 to {len(LETTERS)} options and an answer number that starts from 0."
            )
        if q["question"] in seen:
            logger.warning("questions.json: question number %s appears twice: %s", n, q["question"][:40])
        seen.add(q["question"])


def make_order(seed):
    """Same seed always gives the same question order, so it never needs to be stored."""
    order = list(range(len(get_questions())))
    if SHUFFLE_QUESTIONS:
        random.Random(seed).shuffle(order)
    if NUM_QUESTIONS:
        order = order[:NUM_QUESTIONS]
    return order


# ---------- Saved progress ----------
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            sessions = json.load(f).get("sessions")
        if isinstance(sessions, dict):
            return sessions
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Could not read %s, starting with an empty record", STATE_FILE)
    return {}


def save_state():
    tmp = STATE_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sessions": STATE["sessions"]}, f, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception:
        logger.exception("Could not save %s", STATE_FILE)


def admin_id():
    raw = os.environ.get("ADMIN_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("ADMIN_CHAT_ID must be a number, got: %r", raw)
        return None


# ---------- Sessions ----------
def new_session(user, chat_id, student_id="?"):
    start = int(now())
    return {
        "student_id": student_id,
        "q_sent": start,
        "user_id": user.id,
        "chat_id": chat_id,
        "name": user.full_name or "?",
        "username": user.username or "",
        "sid": random.randint(10000, 99999),
        "start": start,
        "deadline": start + TIME_LIMIT_MINUTES * 60,
        "answered": 0,
        "score": 0,
        "answers": [],
        "msg_id": None,
        "status": "active",
        "reason": None,
        "finished_at": None,
    }


def rebuild_session(user, message, sid, start, answered, answers):
    """The server forgot this exam (restart / sleep). Rebuild it from the button's data."""
    order = make_order(sid)
    total = len(order)
    if not (0 <= answered < total and len(answers) == answered and 0 < start <= now()):
        return None
    questions = get_questions()
    score = 0
    for k, c in enumerate(answers):
        q = questions[order[k]]
        if not (-1 <= c < len(q["options"])):
            return None
        score += c == q["answer"]
    return {
        "student_id": "?",
        "q_sent": int(now()),
        "user_id": user.id,
        "chat_id": message.chat_id,
        "name": user.full_name or "?",
        "username": user.username or "",
        "sid": sid,
        "start": start,
        "deadline": start + TIME_LIMIT_MINUTES * 60,
        "answered": answered,
        "score": score,
        "answers": list(answers),
        "msg_id": message.message_id,
        "status": "active",
        "reason": None,
        "finished_at": None,
    }


def total_of(s):
    return len(make_order(s["sid"]))


def minutes_left(s):
    return max(1, math.ceil((s["deadline"] - now()) / 60))


def finish_session(s, reason):
    """Close the exam. Returns True only for the call that really closed it, so the
    result is announced exactly once even if a tap and the timer arrive together."""
    if s["status"] != "active":
        return False
    s["status"] = "finished"
    s["reason"] = reason
    s["finished_at"] = int(now())
    save_state()
    return True


def result_line(s):
    total = total_of(s)
    percent = round(s["score"] * 100 / total)
    return f"{TXT_RESULT}: {s['score']}/{total} ({percent}%)", percent


def choices_report(s):
    """One line per question, in questions.json order: '<question number>,<letter> ✓/✗'.
    The student's shuffled order is mapped back to the original numbers."""
    order = make_order(s["sid"])
    questions = get_questions()
    given = {}
    for k, c in enumerate(s.get("answers", [])):
        if k < len(order):
            given[order[k]] = c
    lines = []
    for idx in sorted(order):
        c = given.get(idx)
        if c is None or c < 0:
            lines.append(f"{idx + 1},-")
        else:
            mark = "✓" if c == questions[idx]["answer"] else "✗"
            lines.append(f"{idx + 1},{LETTERS[c]} {mark}")
    return "\n".join(lines)


# ---------- Messages ----------
def welcome_text():
    total = len(make_order(0))
    once = "• ፈተናውን አንድ ጊዜ ብቻ መውሰድ ይችላሉ\n" if not ALLOW_RETAKE else ""
    return (
        "እንኳን ደህና መጡ! 👋\n\n"
        "ይህ የ4ኛ ትውልድ ሱባኤ-ጉባኤ የፈተና ቦት ነው።\n\n"
        "📌 ደንቦች\n"
        f"• የጥያቄዎች ብዛት፡ {total}\n"
        f"• የተሰጠ ሰዓት፡ {TIME_LIMIT_MINUTES} ደቂቃ\n"
        f"{once}"
        "• ከፈተናው ወጥተው ተመልሰው /start ከጫኑ ፈተናው ይዘጋል\n"
        "• ጥያቄዎችን ፎቶ ማንሳት ወይም ማስተላለፍ አይፈቀድም\n\n"
        "ለመጀመር ከታች ያለውን ቁልፍ ይጫኑ። ከተጫኑ በኋላ ሰዓቱ ይጀምራል።"
    )


def question_markup(s, i, q):
    # callback_data: a:<exam id>:<start time>:<question number>:<choice>:<choices so far>
    so_far = "".join(str(c) if c >= 0 else "x" for c in s.get("answers", [])) or "-"
    rows = [
        [
            InlineKeyboardButton(
                f"{LETTERS[c]}. {opt}",
                callback_data=f"a:{s['sid']}:{s['start']}:{i}:{c}:{so_far}",
            )
        ]
        for c, opt in enumerate(q["options"])
    ]
    return InlineKeyboardMarkup(rows)


async def safe_answer(query, text=None, alert=False):
    """Telegram refuses answers to old taps (e.g. the server was asleep). That must not stop the exam."""
    try:
        await query.answer(text, show_alert=alert)
    except Exception:
        pass


async def send_question(bot, s):
    i = s["answered"]
    order = make_order(s["sid"])
    q = get_questions()[order[i]]
    text = (
        f"{TXT_QUESTION} {i + 1}/{len(order)}   {TXT_TIME_LEFT.format(m=minutes_left(s))}\n"
        f"🎓 {s.get('student_id', '?')} | {s['name']}\n\n"
        f"{q['question']}"
    )
    msg = await bot.send_message(
        s["chat_id"],
        text,
        reply_markup=question_markup(s, i, q),
        protect_content=PROTECT_CONTENT,
    )
    s["msg_id"] = msg.message_id
    s["q_sent"] = int(now())
    save_state()
    schedule_idle(s)


async def delete_open_question(bot, s):
    if s.get("msg_id"):
        try:
            await bot.delete_message(s["chat_id"], s["msg_id"])
        except Exception:
            pass


async def notify_admin(bot, s, result, percent):  # result/percent kept for the log only
    total = total_of(s)
    when = datetime.fromtimestamp(s["finished_at"], ADDIS_TZ).strftime("%Y-%m-%d %H:%M")
    used = max(0, (s["finished_at"] - s["start"]) // 60)
    username = f"@{s['username']}" if s.get("username") else "-"
    reason = {"time": TXT_REASON_TIME, "left": TXT_REASON_LEFT}.get(s["reason"], TXT_REASON_DONE)
    logger.info(
        "RESULT | %s | %s (ID %s) | %s | %s | %s/%s (%s%%) | answered %s | %s min | %s",
        when, s["name"], s.get("student_id", "?"), username, s["user_id"], s["score"], total, percent, s["answered"], used, s["reason"],
    )
    admin = admin_id()
    if admin is None:
        return
    text = (
        f"{TXT_ADMIN_TITLE}\n\n"
        f"👤 {s['name']}\n"
        f"🔗 {username}\n"
        f"🆔 {s['user_id']}\n"
        f"🎓 ID: {s.get('student_id', '?')}\n"
        f"{result}\n"
        f"{TXT_ANSWERED}: {s['answered']}/{total}\n"
        f"⏱ {used} ደቂቃ\n"
        f"{reason}\n"
        f"🕒 {when}\n\n"
        f"{choices_report(s)}"
    )
    try:
        await bot.send_message(admin, text)
    except Exception:
        logger.exception("Could not send the result to the admin")


async def announce_finish(bot, s):
    """Tell the student and the admin. Call it only when finish_session() returned True."""
    total = total_of(s)
    result, percent = result_line(s)
    if s["reason"] in ("time", "left") and s.get("msg_id") and s["answered"] < total:
        # Replace the unanswered question so it cannot be copied after time is up.
        try:
            await bot.edit_message_text(
                f"{TXT_QUESTION} {s['answered'] + 1}/{total}\n\n"
                f"{TXT_CLOSED_LEFT if s['reason'] == 'left' else TXT_TIME_UP_SHORT}",
                chat_id=s["chat_id"],
                message_id=s["msg_id"],
            )
        except Exception:
            logger.info("Could not close the open question of %s", s["user_id"])
    headline = {"time": TXT_TIME_UP, "left": TXT_LEFT}.get(s["reason"], TXT_FINISHED)
    try:
        await bot.send_message(
            s["chat_id"],
            f"{headline}\n\n{TXT_ANSWERED}: {s['answered']}/{total}",
        )
    except Exception:
        logger.exception("Could not send the result to student %s", s["user_id"])
    await notify_admin(bot, s, result, percent)


# ---------- Timer ----------
def schedule_timeout(job_queue, s):
    if job_queue is None:
        logger.warning("No job queue: the 30-minute auto-submit is off. Install python-telegram-bot[job-queue].")
        return
    delay = max(1, s["deadline"] - now())
    job_queue.run_once(
        job_timeout,
        delay,
        data={"uid": s["user_id"], "sid": s["sid"]},
        name=f"exam-{s['user_id']}",
    )


def schedule_idle(s, delay=None):
    """(Re)start the 'student left' timer for the question that is open now."""
    if JOBQ is None or not PER_QUESTION_SECONDS:
        return
    name = f"idle-{s['user_id']}"
    for j in JOBQ.get_jobs_by_name(name):
        j.schedule_removal()
    JOBQ.run_once(
        job_idle,
        max(1, delay if delay is not None else PER_QUESTION_SECONDS + 1),
        data={"uid": s["user_id"], "sid": s["sid"], "i": s["answered"]},
        name=name,
    )


def is_idle(s):
    return bool(PER_QUESTION_SECONDS) and now() - s.get("q_sent", now()) > PER_QUESTION_SECONDS


async def skip_question(bot, s):
    """The question's time is over: count it as unanswered and move on (or close if the student is gone)."""
    total = total_of(s)
    i = s["answered"]
    s.setdefault("answers", []).append(-1)
    s["answered"] += 1
    save_state()
    try:
        if s.get("msg_id"):
            await bot.edit_message_text(
                f"{TXT_QUESTION} {i + 1}/{total}\n\n{TXT_TIME_UP_SHORT}", chat_id=s["chat_id"], message_id=s["msg_id"]
            )
    except Exception:
        pass
    row = 0
    for c in reversed(s["answers"]):
        if c != -1:
            break
        row += 1
    if s["answered"] >= total:
        if finish_session(s, "done"):
            await announce_finish(bot, s)
    elif row >= MAX_SKIPS_IN_ROW:
        if finish_session(s, "left"):
            await announce_finish(bot, s)
    else:
        await send_question(bot, s)


async def job_idle(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    s = STATE["sessions"].get(str(data["uid"]))
    if s and s["sid"] == data["sid"] and s["status"] == "active" and s["answered"] == data["i"]:
        await skip_question(context.bot, s)


async def job_timeout(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    s = STATE["sessions"].get(str(data["uid"]))
    if s and s["sid"] == data["sid"] and finish_session(s, "time"):
        await announce_finish(context.bot, s)


async def post_init(app: Application):
    """Runs once at start-up: close exams whose time ran out while the bot was off, re-arm the others."""
    global JOBQ
    JOBQ = app.job_queue
    for s in list(STATE["sessions"].values()):
        if s["status"] != "active":
            continue
        if now() >= s["deadline"]:
            if finish_session(s, "time"):
                await announce_finish(app.bot, s)
        elif is_idle(s):
            await skip_question(app.bot, s)
        else:
            schedule_timeout(app.job_queue, s)
            schedule_idle(s, s["q_sent"] + PER_QUESTION_SECONDS - now() + 1)


# ---------- Handlers ----------
async def enter(context, user, chat_id, create):
    """Start pressed. create=False shows the rules first; create=True starts the exam."""
    bot = context.bot
    s = STATE["sessions"].get(str(user.id))

    if s and s["status"] == "active":
        if now() >= s["deadline"]:
            if finish_session(s, "time"):
                await announce_finish(bot, s)
            return
        # Leaving the exam and coming back with /start ends it: no resuming.
        if finish_session(s, "left"):
            await announce_finish(bot, s)
        return

    if s and s["status"] == "finished" and not ALLOW_RETAKE:
        await bot.send_message(chat_id, TXT_ALREADY_DONE)
        return

    if not create:
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(TXT_START_BTN, callback_data="begin")]])
        await bot.send_message(chat_id, welcome_text(), reply_markup=markup)
        return

    # Ask for the student ID first; the exam (and its timer) starts when the ID arrives.
    context.user_data["awaiting_id"] = True
    await bot.send_message(chat_id, TXT_ASK_ID)


async def start_exam(context, user, chat_id, student_id):
    s = new_session(user, chat_id, student_id)
    STATE["sessions"][str(user.id)] = s
    save_state()
    schedule_timeout(context.job_queue, s)
    await send_question(context.bot, s)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat is None or chat.type != "private" or update.message is None:
        return
    s = STATE["sessions"].get(str(user.id))
    if s and (s["status"] == "active" or not ALLOW_RETAKE):
        return  # exam in progress or already done: ignore chatter
    if not context.user_data.get("awaiting_id"):
        await update.message.reply_text(TXT_NEED_START)
        return
    student_id = " ".join((update.message.text or "").split()).upper()
    if not (student_id.startswith(ID_PREFIX) and 8 <= len(student_id) <= 30):
        await update.message.reply_text(TXT_ID_BAD)
        return
    for other in STATE["sessions"].values():
        if other.get("student_id") == student_id and other["user_id"] != user.id:
            await update.message.reply_text(TXT_ID_TAKEN)
            return
    context.user_data.pop("awaiting_id", None)
    await start_exam(context, user, chat.id, student_id)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await enter(context, update.effective_user, update.effective_chat.id, create=False)


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your chat ID: {update.effective_chat.id}")


async def on_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    try:
        await query.edit_message_reply_markup(reply_markup=None)  # the Start button works only once
    except Exception:
        pass
    await enter(context, update.effective_user, query.message.chat_id, create=True)


async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    try:
        _, sid, start, i, choice, so_far = query.data.split(":")
        sid, start, i, choice = int(sid), int(start), int(i), int(choice)
        answers = [] if so_far == "-" else [-1 if ch == "x" else int(ch) for ch in so_far]
    except ValueError:
        await safe_answer(query)
        return

    s = STATE["sessions"].get(str(user.id))
    if s is None:
        s = rebuild_session(user, query.message, sid, start, i, answers)
        if s is None:
            await safe_answer(query)
            return
        STATE["sessions"][str(user.id)] = s
        save_state()
        schedule_timeout(context.job_queue, s)
    elif s["sid"] != sid:
        await safe_answer(query, TXT_STALE, alert=True)
        return

    if s["status"] != "active":
        await safe_answer(query, TXT_ALREADY_DONE, alert=True)
        return

    # Time is up: this answer is too late and is not counted.
    if now() >= s["deadline"]:
        await safe_answer(query)
        if finish_session(s, "time"):
            await announce_finish(context.bot, s)
        return

    # An old or repeated tap (a question that was already answered).
    if i != s["answered"]:
        await safe_answer(query, TXT_STALE)
        return

    # This question's time was already over (late tap): it is skipped and not counted.
    if is_idle(s):
        await safe_answer(query)
        await skip_question(context.bot, s)
        return

    order = make_order(s["sid"])
    total = len(order)
    q = get_questions()[order[i]]
    if not (0 <= choice < len(q["options"])):
        await safe_answer(query)
        return

    # ---- record the answer (no awaits between here and the save, so it cannot be applied twice) ----
    correct = choice == q["answer"]
    if correct:
        s["score"] += 1
    s.setdefault("answers", []).append(choice)
    s["answered"] += 1
    last = s["answered"] >= total
    if last:
        finish_session(s, "done")
    else:
        save_state()

    await safe_answer(query)

    # Replace the question by a short line, so the questions do not stay in the chat.
    text = (
        f"{TXT_QUESTION} {i + 1}/{total}\n"
        f"{TXT_YOUR_ANSWER}: {LETTERS[choice]}. {q['options'][choice]}"
    )
    if SHOW_ANSWERS:
        text += f"\n{TXT_CORRECT}" if correct else f"\n{TXT_WRONG}: {LETTERS[q['answer']]}. {q['options'][q['answer']]}"
    else:
        text += f"\n{TXT_RECORDED}"
    try:
        await query.edit_message_text(text)
    except Exception:
        logger.info("Could not edit the answered question of %s", user.id)

    if last:
        await announce_finish(context.bot, s)
    else:
        await send_question(context.bot, s)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Error while handling an update", exc_info=context.error)


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set. Put the token from @BotFather in the BOT_TOKEN variable.")

    validate_questions()
    STATE["sessions"] = load_state()

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler(["start", "exam"], cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CallbackQueryHandler(on_begin, pattern=r"^begin$"))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^a:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)

    base_url = os.environ.get("WEBHOOK_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if base_url:
        # Webhook mode (Render). The secret path is derived from the token, so no extra setting is needed.
        secret = hashlib.sha256(token.encode()).hexdigest()
        port = int(os.environ.get("PORT", "10000"))
        logger.info("Starting in webhook mode on port %s", port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=secret,
            webhook_url=f"{base_url.rstrip('/')}/{secret}",
            secret_token=secret,
        )
    else:
        logger.info("Starting in polling mode")
        app.run_polling()


if __name__ == "__main__":
    main()
