# Telegram Exam Bot (Render version)

Multiple-choice exam bot. It asks questions one by one and gives the student only the final score. Correct answers are hidden from students (see `SHOW_ANSWERS` in `bot.py`).
**Every finished exam is sent to you in Telegram** (name, username, ID, score, time).

## Before you start
- Use a fresh bot token from @BotFather (`/revoke` if the old one was shared anywhere).
- Never put the token inside any file or on GitHub. It goes only into Render's environment variables.

## 1. Find your Telegram ID (for results)
Open **@userinfobot** in Telegram and press Start. It replies with your numeric ID, for example `123456789`.
(After the bot is running you can also send `/myid` to your own bot.)

## 2. Put the files on GitHub
1. Create a free account at github.com and click **New repository** (private is fine).
2. Click **uploading an existing file** and upload: `bot.py`, `questions.json`, `requirements.txt`, `README.md`, `.gitignore`.
3. Click **Commit changes**.

## 3. Deploy on Render
1. Go to render.com and sign up with GitHub.
2. **New +** then **Web Service**, and pick your repository.
3. Fill in:
   - **Language:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Instance Type:** Free
4. Under **Environment Variables** add:
   - `BOT_TOKEN` = your token from @BotFather
   - `ADMIN_CHAT_ID` = your ID from step 1
5. Click **Create Web Service**. Wait until the log says it is live.

Then open your bot in Telegram and send `/start`.

## Good to know
- The free Render server sleeps after 15 minutes without messages. The first message after a quiet period can take about a minute. Telegram keeps retrying, so the message is not lost.
- Exam progress is stored inside the buttons, so a student can pause for hours and continue.
- Results are saved as messages in your admin chat. They also appear in Render's logs, but those are kept only for a short time.

## Add your own questions
Edit `questions.json` on GitHub (pencil icon). Render redeploys automatically.

```json
{
  "question": "Your question here?",
  "options": ["Option A", "Option B", "Option C", "Option D"],
  "answer": 1,
  "explanation": "Optional note shown after answering."
}
```

- `answer` counts from 0 (0 = A, 1 = B, 2 = C, 3 = D).
- `explanation` is only shown to students if you set `SHOW_ANSWERS = True`.
- Keep valid JSON: commas between questions, none after the last one.

## Settings (top of bot.py)
`NUM_QUESTIONS`, `SHUFFLE_QUESTIONS`, `PASS_PERCENT`, `SHOW_ANSWERS` (False = hide right/wrong and correct answers, True = show them after each question), and all the Amharic texts.

## Run on your own computer (optional)
```
pip install -r requirements.txt
set BOT_TOKEN=your_token        (Windows; use export on Linux/Mac/Termux)
python bot.py
```
