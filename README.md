# 💬 Streamlit Chatbot

A multi-chat AI chatbot built with **Streamlit** and **OpenRouter's free models**. It streams replies as they're generated, saves your conversations between sessions, and tracks API usage so you stay within the free-tier limits.

## Features

- **Streaming responses**: text appears word by word
- **Multiple chats**: create, switch, rename and delete conversations
- **Persistent history**: chats are saved to a local JSON file and survive restarts
- **Copy button on every message**: so you can copy a question and re-send it easily
- **Interruption handling**: if a reply is stopped (Stop button or clicking in the app mid-answer), the chat shows a notice asking you to re-ask instead of leaving a broken chat
- **Usage tracking**: daily and per-minute request counters, a warning at 80% of the daily limit, and a 7-day usage chart
- **Configurable**: change the model and system prompt from the sidebar
- **Safe API key handling**: the key comes from an environment variable or a password field and is never written to disk

## Tech stack

| Part | Choice |
|------|--------|
| UI | Streamlit (chat elements, `session_state`, `write_stream`) |
| LLM API | OpenRouter, via the official `openai` Python SDK (OpenRouter is OpenAI-compatible) |
| Storage | JSON files with atomic writes |

## Run it locally

```bash
git clone <your-repo-url>
cd <repo-folder>
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export OPENROUTER_API_KEY="sk-or-..."   # Windows PowerShell: $env:OPENROUTER_API_KEY="sk-or-..."
streamlit run app.py
```

If you don't set the environment variable, paste the key into the sidebar instead. Get a free key at [openrouter.ai](https://openrouter.ai).

## How it works

1. **Streamlit reruns the whole script on every interaction.** Anything that must survive a rerun (chats, usage, the current chat) lives in `st.session_state`, loaded from disk once at startup by `init_state()`.
2. **Sending a message** (`handle_prompt`): check limits, save the question with an "interrupted" placeholder answer, call OpenRouter with the system prompt plus the chat history, stream the reply into the UI, then swap the placeholder for the real reply and rerun so the sidebar updates. If Streamlit kills the run mid-answer (Stop button or a click), the placeholder is what remains, so the chat explains what happened. Interrupted exchanges are not sent to the model as context.
3. **Persistence**: `chats.json` stores `{chat_id: {title, created, messages[]}}`. `usage.json` stores `{date: request_count}`.
4. **Atomic writes**: data is written to a `.tmp` file and then renamed over the real file, so a crash mid-write can't corrupt the history.
5. **Rate limiting**: the per-minute limit is a list of recent request timestamps (older than 60 s are dropped). The per-day limit is a counter per date. Failed requests are counted too, because OpenRouter counts them.

## Project structure

```
app.py            # the whole app, split into small functions by section
requirements.txt
README.md
.gitignore        # keeps chats.json, usage.json and secrets out of git
```

## Limitations and possible improvements

- **Single user**: chats are stored in one shared JSON file, so there are no accounts. A next step would be user login plus a real database (SQLite for simplicity, or PostgreSQL/MySQL for hosting) with a `users`, `chats` and `messages` table.
- **Full history is sent on every request**: very long chats will eventually hit the model's context limit. A fix is to send only the last N messages, or to summarize older ones.
- **Usage counters are local**: they only count requests made through this app, not your whole OpenRouter account.
- **Free models can be slow or rate-limited**, so the app shows API errors instead of failing silently.
