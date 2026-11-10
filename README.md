# Telegram Exam Bot (timed, one attempt)

Multiple-choice exam bot for Render or your own computer.

- Each student has **one exam**. Pressing Start again only continues it, so questions never repeat or mix.
- **30-minute limit**, starting when the student presses Start. When time is up, the answered questions are submitted automatically and the student is told the exam has finished.
- **One attempt** per student (see `ALLOW_RETAKE`).
- Students see only their final score, never the correct answers.
- Questions are sent with Telegram's content protection (no forwarding or saving), and answered questions are replaced by a short line so they do not stay in the chat.
- **Every finished exam is sent to you in Telegram**: name, username, ID, score, how many were answered, minutes used, and whether the student finished or ran out of time.

## Before you start
- Use a fresh bot token from @BotFather (`/revoke` if the old one was shared anywhere).
- Never put the token inside any file or on GitHub. It goes only into Render's environment variables.
- Your Telegram ID for results: message **@userinfobot**, copy the number after **Id**.

## Put the files on GitHub
Upload these files at the top level of the repository (not inside a folder): `bot.py`, `questions.json`, `requirements.txt`, `README.md`, `.gitignore`.
When updating, upload again with the same names and click **Commit changes**. Render redeploys by itself.

## Deploy on Render
1. render.com, sign up with GitHub, **New +**, **Web Service**, pick your repository.
2. **Build Command:** `pip install -r requirements.txt`
3. **Start Command:** `python bot.py`
4. **Instance Type:** Free
5. Environment Variables: `BOT_TOKEN` = your token, `ADMIN_CHAT_ID` = your numeric ID.
6. **Create Web Service**, then open your bot in Telegram and send `/start`.

## Important: limits of the free Render server
The free server goes to sleep after 15 minutes without messages, and it forgets its saved progress (`state.json`) when it sleeps, restarts or is redeployed.

What still works: a student who comes back and taps an answer continues correctly, with the right score and the right deadline, because progress is also kept inside the buttons. Answers sent after the 30 minutes are refused.

What does not work: if a student leaves and never comes back, and the server fell asleep or restarted in the meantime, nobody triggers the automatic submission for that student.

For an exam where every result must be recorded:
- Run the bot on a computer or phone that stays on (`python bot.py`), or use an always-on paid instance.
- Do not redeploy (do not change files on GitHub) while students are taking the exam.

## Settings (top of bot.py)
| Setting | Meaning |
|---|---|
| `TIME_LIMIT_MINUTES` | Time for the whole exam (30) |
| `NUM_QUESTIONS` | Questions per exam, `None` = all |
| `SHUFFLE_QUESTIONS` | Random order for each student |
| `PASS_PERCENT` | Pass mark |
| `ALLOW_RETAKE` | `False` = one attempt only |
| `PROTECT_CONTENT` | `True` = questions cannot be forwarded or saved |
| `SHOW_ANSWERS` | `False` = hide right/wrong from students |

All Amharic texts are below the settings.

## About screenshots
Content protection blocks forwarding, copying and saving of the question messages. Telegram says it also blocks screenshots in protected chats, but this was not confirmed for bot chats on every phone, and nothing can stop someone photographing the screen with another phone. Treat it as a strong deterrent, not a guarantee.

## Add or change questions
Edit `questions.json` on GitHub (pencil icon). The bot checks the file when it starts and stops with a clear message if a question is wrong.

```json
{
  "question": "Your question here?",
  "options": ["Option A", "Option B", "Option C", "Option D"],
  "answer": 1
}
```

- `answer` counts from 0 (0 = A, 1 = B, 2 = C, 3 = D).
- 2 to 6 options per question.
- Keep valid JSON: commas between questions, none after the last one.

## Run on your own computer
```
pip install -r requirements.txt
set BOT_TOKEN=your_token         (Windows; use export on Linux/Mac/Termux)
set ADMIN_CHAT_ID=your_id
python bot.py
```
Progress is saved in `state.json` next to `bot.py`. It contains student names and IDs, so do not upload it to GitHub (`.gitignore` already skips it).