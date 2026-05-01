import os
import re
import logging
import requests
import base64
from datetime import datetime, timezone, timedelta
from flask import Flask, request as flask_request
from groq import Groq
from prompts import TEDDY_PROMPT, MORY_PROMPT, MINNIE_PROMPT, YEN_PROMPT

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN        = os.environ["TELEGRAM_TOKEN"]
GROQ_KEY     = os.environ["GROQ_API_KEY"]
ALLOWED_ID   = os.getenv("ALLOWED_USER_ID", "")
RENDER_URL   = os.getenv("RENDER_URL", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "")

groq_client = Groq(api_key=GROQ_KEY)
GROQ_MODEL  = "llama-3.3-70b-versatile"

API = f"https://api.telegram.org/bot{TOKEN}"
history: dict[str, list] = {}

def get_github_file(path: str) -> str:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return ""
    url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            content = r.json().get("content", "")
            return base64.b64decode(content).decode("utf-8")
    except Exception as e:
        logging.warning(f"GitHub fetch failed ({path}): {e}")
    return ""

def load_context() -> str:
    tz_thai = timezone(timedelta(hours=7))
    today   = datetime.now(tz_thai)
    month   = today.strftime("%Y-%m")
    date    = today.strftime("%Y-%m-%d")
    index_md  = get_github_file("KNOWLEDGE_BASE/INDEX.md")
    daily_log = get_github_file(f"Teddy_Secretary/Daily Log/{month}/{date}.md")
    parts = []
    if index_md:
        parts.append(f"=== INDEX.md ===\n{index_md[:2000]}")
    if daily_log:
        parts.append(f"=== Daily Log {date} ===\n{daily_log[:1500]}")
    return "\n\n".join(parts) if parts else ""

def send(chat_id, text):
    requests.post(f"{API}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    })

def typing(chat_id):
    requests.post(f"{API}/sendChatAction", json={
        "chat_id": chat_id,
        "action": "typing"
    })

def ask(system: str, prompt: str) -> str:
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt}
            ],
            temperature=0.4,
            max_tokens=1024,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"Groq error: {e}")
        return "⚠️ เกิดข้อผิดพลาด ลองใหม่ค่ะ"

def detect_route(text):
    t = text.lower()
    if "[route:mory]"   in t: return "mory"
    if "[route:minnie]" in t: return "minnie"
    return "teddy"

def strip_tags(text):
    return re.sub(r"\[route:\w+\]", "", text, flags=re.IGNORECASE).strip()

def build_context(hist):
    return "\n".join(
        f"{'เม้' if h['role'] == 'user' else 'ผู้ช่วย'}: {h['text']}"
        for h in hist[-6:]
    )

def handle(data):
    msg = data.get("message", {})
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    uid     = str(msg["from"]["id"])
    text    = msg.get("text", "")
    if not text:
        return
    if ALLOWED_ID and uid != ALLOWED_ID:
        return
    if text == "/start":
        send(chat_id, "🟣 *Teddy พร้อมแล้วค่ะ*\n\nบอกอะไรก็ได้เลย")
        return

    gh_context = load_context()
    if gh_context:
        system_with_context = TEDDY_PROMPT + f"\n\n--- context ล่าสุด ---\n{gh_context}"
    else:
        system_with_context = TEDDY_PROMPT

    hist = history.get(uid, [])
    ctx  = build_context(hist)
    full = f"{ctx}\nเม้: {text}" if ctx else f"เม้: {text}"

    typing(chat_id)

    try:
        teddy_out = ask(system_with_context, full)
        route     = detect_route(teddy_out)

        if route == "mory":
            typing(chat_id)
            mory_out = ask(MORY_PROMPT, f"Teddy ส่งมา: {strip_tags(teddy_out)}\nคำขอ: {text}")
            reply = f"🟠 *Mory:*\n{mory_out}"
        elif route == "minnie":
            typing(chat_id)
            minnie_out = ask(MINNIE_PROMPT, f"Teddy ส่งมา: {strip_tags(teddy_out)}\nคำขอ: {text}")
            if any(c.isdigit() for c in minnie_out):
                typing(chat_id)
                yen_out = ask(YEN_PROMPT, f"ตรวจจาก Minnie:\n{minnie_out}")
                reply = f"🩷 *Minnie:*\n{minnie_out}\n\n🔵 *Yen:*\n{yen_out}"
            else:
                reply = f"🩷 *Minnie:*\n{minnie_out}"
        else:
            reply = f"🟣 *Teddy:*\n{strip_tags(teddy_out)}"

        hist.extend([
            {"role": "user",      "text": text},
            {"role": "assistant", "text": reply}
        ])
        history[uid] = hist[-20:]
        send(chat_id, reply)

    except Exception as e:
        logging.error(f"Error: {e}")
        send(chat_id, "⚠️ เกิดข้อผิดพลาด ลองใหม่นะคะ")

app = Flask(__name__)

@app.route("/")
def health():
    return "🟣 Teddy is running", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    handle(flask_request.get_json(force=True))
    return "ok", 200

def set_webhook():
    if not RENDER_URL:
        logging.warning("RENDER_URL not set — webhook not registered")
        return
    resp = requests.post(f"{API}/setWebhook", json={"url": f"{RENDER_URL}/webhook"})
    logging.info(f"Webhook: {resp.json()}")

if __name__ == "__main__":
    set_webhook()
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
