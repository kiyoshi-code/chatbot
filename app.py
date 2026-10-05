"""A multi-chat Streamlit chatbot powered by OpenRouter's free models.

Features: streaming replies, persistent chat history (JSON file), rename/delete chats,
and request-usage tracking (per-minute and per-day) so you stay inside the free limits.
"""

import json
import os
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI

# ---------- Configuration ----------
DATA_FILE = Path(__file__).parent / "chats.json"
USAGE_FILE = Path(__file__).parent / "usage.json"
MINUTE_LIMIT = 20  # OpenRouter's free models allow about 20 requests per minute
DEFAULT_DAILY_LIMIT = 50  # about 50/day on free models; more if you've added credits
DEFAULT_MODEL = "openrouter/free"  # OpenRouter's free-models router
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."
TITLE_MAX_LEN = 40
INTERRUPTED_NOTE = (
    "⚠️ This answer was stopped because you pressed Stop or clicked something in the app "
    "while it was being written. Please ask your question again. (Note: This query will not be saved in chat history.)"
)

# Small copy icon shown at the bottom-right of each message (runs inside a tiny iframe).
# __TEXT__ is replaced with the message as a JSON string.
COPY_BUTTON_HTML = """
<style>
  html, body { margin: 0; padding: 0; background: transparent; }
  #copy { float: right; display: flex; align-items: center; background: none; border: none;
          cursor: pointer; padding: 3px 5px; border-radius: 6px; color: #8b93a1; opacity: .8; }
  #copy:hover { opacity: 1; background: rgba(128,128,128,.2); }
</style>
<button id="copy" title="Copy" aria-label="Copy message"></button>
<script>
  const text = __TEXT__;
  const btn = document.getElementById("copy");
  const copyIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
  const doneIcon = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  btn.innerHTML = copyIcon;
  function fallbackCopy() {
    const t = document.createElement("textarea");
    t.value = text; t.style.position = "fixed"; t.style.opacity = "0";
    document.body.appendChild(t); t.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(t);
  }
  btn.onclick = async () => {
    try { await navigator.clipboard.writeText(text); } catch (e) { fallbackCopy(); }
    btn.innerHTML = doneIcon;
    setTimeout(() => { btn.innerHTML = copyIcon; }, 1500);
  };
</script>
"""

st.set_page_config(page_title="My Chatbot", page_icon="💬", layout="centered")

# Hide the little toolbar (fullscreen / table view buttons) that appears on charts
st.markdown(
    "<style>[data-testid='stElementToolbar'] {display: none;}"
    "[data-testid='stChatMessage'] iframe {display: block; margin-top: -0.6rem;}</style>",
    unsafe_allow_html=True,
)


# ---------- Storage (JSON files) ----------
def read_json(path: Path) -> dict:
    """Read a JSON file, returning {} if it's missing or corrupted."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def write_json(path: Path, data: dict) -> None:
    """Write to a temp file first, then swap it in, so a crash can't corrupt the file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def drop_unanswered(messages: list) -> list:
    """Remove user messages that never got a reply (e.g. the run was stopped mid-answer)."""
    last = len(messages) - 1
    return [
        m for i, m in enumerate(messages)
        if not (m["role"] == "user" and (i == last or messages[i + 1]["role"] == "user"))
    ]


def save_chats() -> None:
    write_json(DATA_FILE, st.session_state.chats)


def new_chat() -> None:
    chat_id = uuid.uuid4().hex[:8]
    st.session_state.chats[chat_id] = {
        "title": "New chat",
        "created": datetime.now().isoformat(timespec="seconds"),
        "messages": [],
    }
    st.session_state.current_id = chat_id
    save_chats()


# ---------- Usage tracking ----------
def today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def requests_today() -> int:
    return st.session_state.usage.get(today_key(), 0)


def record_request() -> None:
    """Call every time we send a request to the API (OpenRouter counts failed ones too)."""
    st.session_state.usage[today_key()] = requests_today() + 1
    write_json(USAGE_FILE, st.session_state.usage)
    st.session_state.recent_requests.append(time.time())


def requests_last_minute() -> int:
    """Count requests in the last 60 seconds, dropping older timestamps."""
    cutoff = time.time() - 60
    st.session_state.recent_requests = [t for t in st.session_state.recent_requests if t > cutoff]
    return len(st.session_state.recent_requests)


def last_7_days() -> dict:
    """Requests per day for the last 7 days, labelled MM-DD, oldest first."""
    result = {}
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        result[day[5:]] = st.session_state.usage.get(day, 0)
    return result


# ---------- State ----------
def init_state() -> None:
    """Load saved data into st.session_state (Streamlit reruns the script on every interaction)."""
    if "chats" not in st.session_state:
        st.session_state.chats = read_json(DATA_FILE)
        for chat in st.session_state.chats.values():  # repair chats left half-finished by a stopped run
            chat["messages"] = drop_unanswered(chat["messages"])
    if "usage" not in st.session_state:
        st.session_state.usage = read_json(USAGE_FILE)  # {"2026-10-02": 12, ...}
    if "recent_requests" not in st.session_state:
        st.session_state.recent_requests = []  # timestamps, only needed for the last 60 s

    chats = st.session_state.chats
    if st.session_state.get("current_id") not in chats:
        if chats:
            # open the most recent chat
            st.session_state.current_id = max(chats, key=lambda k: chats[k]["created"])
        else:
            new_chat()


# ---------- Sidebar ----------
def render_usage(daily_limit: int) -> None:
    used_today = requests_today()
    st.progress(min(used_today / daily_limit, 1.0), text=f"Today: {used_today} / {daily_limit} requests")
    st.caption(f"Last 60 seconds: {requests_last_minute()} / {MINUTE_LIMIT}")

    if used_today >= daily_limit:
        st.error("Daily limit reached.")
    elif used_today >= 0.8 * daily_limit:
        st.warning(f"Only {daily_limit - used_today} requests left today.")

    st.bar_chart(last_7_days(), height=120)


def render_chat_manager() -> None:
    """New chat button, rename box, and the clickable history list."""
    chats = st.session_state.chats
    current_id = st.session_state.current_id

    if st.button("➕ New chat", use_container_width=True):
        new_chat()
        st.rerun()

    with st.expander("✏️ Rename current chat"):
        title = chats[current_id]["title"]
        # the title is part of the key so the box refreshes whenever the title changes
        new_title = st.text_input("Chat name (press Enter to save)", value=title, key=f"rename_{current_id}_{title}")
        if not new_title.strip():
            st.warning("The name can't be empty.")
        elif new_title.strip() != title:  # pressing Enter changes the value and reruns the script
            chats[current_id]["title"] = new_title.strip()
            save_chats()
            st.rerun()

    st.subheader("History")
    for cid, chat in sorted(chats.items(), key=lambda kv: kv[1]["created"], reverse=True):
        col_open, col_delete = st.columns([5, 1])
        label = ("● " if cid == current_id else "") + chat["title"]
        if col_open.button(label, key=f"open_{cid}", use_container_width=True):
            st.session_state.current_id = cid
            st.rerun()
        if col_delete.button("🗑", key=f"del_{cid}"):
            del chats[cid]
            save_chats()
            st.rerun()  # init_state() picks another chat (or creates one) on the rerun


def render_sidebar() -> tuple:
    """Draw the sidebar and return the settings the chat needs."""
    with st.sidebar:
        st.header("Settings")
        api_key = os.getenv("OPENROUTER_API_KEY") or st.text_input(
            "OpenRouter API key", type="password", help="Or set the OPENROUTER_API_KEY env variable"
        )
        model = st.text_input("Model", value=DEFAULT_MODEL, help="Use a model ID ending in :free")
        system_prompt = st.text_area("System prompt", value=DEFAULT_SYSTEM_PROMPT, height=100)

        st.divider()
        st.subheader("Usage")
        daily_limit = st.number_input(
            "Daily request limit",
            min_value=1,
            value=DEFAULT_DAILY_LIMIT,
            step=10,
            help="OpenRouter free models are usually limited to about 50 requests/day "
            "(more if you've added credits to your account). Check your account and adjust.",
        )
        render_usage(daily_limit)

        st.divider()
        render_chat_manager()
    return api_key, model, system_prompt, daily_limit


# ---------- Chat ----------
def render_message(msg: dict) -> None:
    """Show one message. Normal messages get a copy icon; interrupted answers show a warning."""
    with st.chat_message(msg["role"]):
        if msg.get("interrupted"):
            st.warning(msg["content"])
        else:
            st.markdown(msg["content"])
            payload = json.dumps(msg["content"]).replace("</", "<\\/")  # safe to embed in <script>
            components.html(COPY_BUTTON_HTML.replace("__TEXT__", payload), height=30)


def usable_history(messages: list) -> list:
    """Messages to send to the model: skips interrupted exchanges (the question + its warning)."""
    history = []
    for m in messages:
        if m.get("interrupted"):
            if history and history[-1]["role"] == "user":
                history.pop()
            continue
        history.append({"role": m["role"], "content": m["content"]})
    return history


def stream_reply(client: OpenAI, model: str, messages: list):
    """Yield the model's reply piece by piece so the UI can show it as it's written."""
    stream = client.chat.completions.create(model=model, messages=messages, stream=True)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


def check_limits(api_key: str, daily_limit: int) -> None:
    """Stop the script with a message if we shouldn't send a request right now."""
    if not api_key:
        st.error("Please enter your OpenRouter API key in the sidebar.")
        st.stop()
    if requests_today() >= daily_limit:
        st.error("You've reached your daily request limit. Raise it in the sidebar if your account allows more.")
        st.stop()
    if requests_last_minute() >= MINUTE_LIMIT:
        st.warning("Too many requests in the last minute. Wait a few seconds and try again.")
        st.stop()


def handle_prompt(prompt: str, current: dict, settings: tuple) -> None:
    api_key, model, system_prompt, daily_limit = settings
    check_limits(api_key, daily_limit)

    user_msg = {"role": "user", "content": prompt}
    render_message(user_msg)

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    api_messages = [{"role": "system", "content": system_prompt}] + usable_history(current["messages"]) + [user_msg]

    # Save the question right away, followed by a placeholder answer that says the run was interrupted.
    # If the user presses Stop or clicks something mid-answer, Streamlit kills this script run and
    # the placeholder stays, so the chat shows the notice. When the real reply arrives, it replaces it.
    current["messages"] += [user_msg, {"role": "assistant", "content": INTERRUPTED_NOTE, "interrupted": True}]
    if current["title"] == "New chat":  # name the chat after its first message
        current["title"] = prompt[:TITLE_MAX_LEN] + ("..." if len(prompt) > TITLE_MAX_LEN else "")
    save_chats()

    record_request()  # count it even if it fails, since OpenRouter counts failed attempts too

    with st.chat_message("assistant"):
        try:
            reply = st.write_stream(stream_reply(client, model, api_messages))
            if not reply or not reply.strip():
                raise ValueError("The model returned an empty reply. Try a different model.")
        except Exception as e:
            current["messages"][-1]["content"] = (
                f"⚠️ The request failed: {e}\n\nPlease ask your question again "

            )
            save_chats()
            st.rerun()  # redraw so the warning shows in the chat

    current["messages"][-1] = {"role": "assistant", "content": reply}  # replace the placeholder
    save_chats()
    st.rerun()  # refresh the sidebar (new title, usage numbers)


def main() -> None:
    init_state()
    settings = render_sidebar()

    st.title("💬 Chatbot")
    current = st.session_state.chats[st.session_state.current_id]
    for msg in current["messages"]:
        render_message(msg)

    if prompt := st.chat_input("Type your message..."):
        handle_prompt(prompt, current, settings)


main()