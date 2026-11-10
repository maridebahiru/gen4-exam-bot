"""Telegram exam bot, ready for Render (free web service).

The same file works two ways:
- On your computer (no RENDER_EXTERNAL_URL / WEBHOOK_BASE_URL set): polling mode.
- On Render (RENDER_EXTERNAL_URL is set automatically): webhook mode.

Exam progress is stored inside the buttons (seed, question number, score),
so nothing is lost when the free server goes to sleep.
Every finished exam is sent to the admin's Telegram chat (ADMIN_CHAT_ID).

Environment variables:
    BOT_TOKEN       required, from @BotFather
    ADMIN_CHAT_ID   optional, your Telegram numeric ID (get it from @userinfobot or /myid)
"""

import hashlib
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- Settings you can change ----------
QUESTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "questions.json")
NUM_QUESTIONS = None    # questions per exam; use None to ask all of them
SHUFFLE_QUESTIONS = True
PASS_PERCENT = 50       # pass mark in percent
SHOW_ANSWERS = False    # False = students never see right/wrong or the correct answer; True = show after each question
ADDIS_TZ = timezone(timedelta(hours=3))  # Ethiopia time, used in result messages

# ---------- Texts (edit freely) ----------
TXT_WELCOME = (
    "እንኳን ደህና መጡ! 👋\n\n"
    "ይህ የ4ኛ ትውልድ ሱባኤ-ጉባኤ የፈተና ቦት ነው።\n"
    "ለመጀመር ከታች ያለውን ቁልፍ ይጫኑ።"
)
TXT_START_BTN = "🚀 ፈተና ጀምር"
TXT_RETRY_BTN = "🔁 እንደገና ሞክር"
TXT_QUESTION = "ጥያቄ"
TXT_CORRECT = "✅ ትክክል!"
TXT_WRONG = "❌ ስህተት። ትክክለኛው መልስ"
TXT_YOUR_ANSWER = "የእርስዎ መልስ"
TXT_RESULT = "📊 ውጤት"
TXT_PASSED = "🎉 አልፈዋል!"
TXT_FAILED = "አልተሳካም።"
TXT_RECORDED = "✔️ መልስዎ ተመዝግቧል"
TXT_ADMIN_TITLE = "📝 አዲስ የፈተና ውጤት"

LETTERS = ["A", "B", "C", "D", "E", "F"]


def load_questions():
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        return json.load(f)


def pick_order(seed, total):
    """Same seed always gives the same question order, so no server memory is needed."""
    order = list(range(total))
    if SHUFFLE_QUESTIONS:
        random.Random(seed).shuffle(order)
    if NUM_QUESTIONS:
        order = order[:NUM_QUESTIONS]
    return order


def admin_id():
    raw = os.environ.get("ADMIN_CHAT_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("ADMIN_CHAT_ID must be a number, got: %r", raw)
        return None


def question_markup(seed, i, score, q):
    # callback_data format: a:<seed>:<question number>:<choice>:<score so far>
    rows = [
        [InlineKeyboardButton(f"{LETTERS[c]}. {opt}", callback_data=f"a:{seed}:{i}:{c}:{score}")]
        for c, opt in enumerate(q["options"])
    ]
    return InlineKeyboardMarkup(rows)


async def send_question(chat_id, context, seed, i, score):
    questions = load_questions()
    order = pick_order(seed, len(questions))
    q = questions[order[i]]
    await context.bot.send_message(
        chat_id,
        f"{TXT_QUESTION} {i + 1}/{len(order)}\n\n{q['question']}",
        reply_markup=question_markup(seed, i, score, q),
    )


async def start_exam(chat_id, context):
    seed = random.randint(0, 99999)
    await send_question(chat_id, context, seed, 0, 0)


async def notify_admin(context, user, score, total, percent):
    now = datetime.now(ADDIS_TZ).strftime("%Y-%m-%d %H:%M")
    name = user.full_name if user else "?"
    username = f"@{user.username}" if user and user.username else "-"
    uid = user.id if user else "-"
    logger.info("RESULT | %s | %s | %s | %s | %s/%s (%s%%)", now, name, username, uid, score, total, percent)

    admin = admin_id()
    if admin is None:
        return
    text = (
        f"{TXT_ADMIN_TITLE}\n\n"
        f"👤 {name}\n"
        f"🔗 {username}\n"
        f"🆔 {uid}\n"
        f"{TXT_RESULT}: {score}/{total} ({percent}%)\n"
        f"🕒 {now}"
    )
    try:
        await context.bot.send_message(admin, text)
    except Exception:
        logger.exception("Could not send the result to the admin")


async def finish_exam(chat_id, user, context, score, total):
    percent = round(score * 100 / total)
    verdict = TXT_PASSED if percent >= PASS_PERCENT else TXT_FAILED
    await context.bot.send_message(
        chat_id,
        f"{TXT_RESULT}: {score}/{total} ({percent}%)\n\n{verdict}",
    )
    await notify_admin(context, user, score, total, percent)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup([[InlineKeyboardButton(TXT_START_BTN, callback_data="begin")]])
    await update.message.reply_text(TXT_WELCOME, reply_markup=markup)


async def cmd_exam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_exam(update.effective_chat.id, context)


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your chat ID: {update.effective_chat.id}")


async def on_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start_exam(query.message.chat_id, context)


async def on_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, seed, i, choice, score = query.data.split(":")
        seed, i, choice, score = int(seed), int(i), int(choice), int(score)
    except ValueError:
        return

    questions = load_questions()
    order = pick_order(seed, len(questions))
    total = len(order)
    if not (0 <= i < total):
        return
    q = questions[order[i]]
    if not (0 <= choice < len(q["options"])):
        return

    correct = q["answer"]
    if choice == correct:
        score += 1

    text = (
        f"{TXT_QUESTION} {i + 1}/{total}\n\n{q['question']}\n\n"
        f"{TXT_YOUR_ANSWER}: {LETTERS[choice]}. {q['options'][choice]}"
    )
    if SHOW_ANSWERS:
        if choice == correct:
            text += f"\n{TXT_CORRECT}"
        else:
            text += f"\n{TXT_WRONG}: {LETTERS[correct]}. {q['options'][correct]}"
        if q.get("explanation"):
            text += f"\n\n💡 {q['explanation']}"
    else:
        text += f"\n\n{TXT_RECORDED}"

    # Replace the question with the student's answer (this removes the buttons).
    # If the message was already edited (double tap), stop so nothing is sent twice.
    try:
        await query.edit_message_text(text)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return
        raise

    chat_id = query.message.chat_id
    if i + 1 < total:
        await send_question(chat_id, context, seed, i + 1, score)
    else:
        await finish_exam(chat_id, update.effective_user, context, score, total)


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN is not set. Put the token from @BotFather in the BOT_TOKEN variable.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("exam", cmd_exam))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CallbackQueryHandler(on_begin, pattern=r"^begin$"))
    app.add_handler(CallbackQueryHandler(on_answer, pattern=r"^a:"))

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
