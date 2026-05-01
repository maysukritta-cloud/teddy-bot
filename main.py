import os
import re
import logging
import requests
from flask import Flask, request as flask_request
from google import genai
from prompts import TEDDY_PROMPT, MORY_PROMPT, MINNIE_PROMPT, YEN_PROMPT

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TOKEN       = os.environ["TELEGRAM_TOKEN"]
GEMINI_KEY  = os.environ["GEMINI_API_KEY"]
ALLOWED_ID  = os.getenv("ALLOWED_USER_ID", "")
RENDER_URL  = os.getenv("RENDER_URL", "")

gemini = genai.Client(api_key=GEMINI_KEY)

API = f"https://api.telegram.org/bot{TOKEN}"
history: dict[str, list] = {}

# ── Telegram helpers ──────────────────────────────────────────────
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

# ── Gemini helpers ────────────────────────────────────────────────
def ask(system, prompt):
    response = gemini.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt,
        config=genai.types.GenerateContentConfig(system_instruction=system)
    )
    return response.text.strip()

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

# ── Message handler ───────────────────────────────────────────────
def handle(data):
    msg = data.get("message", {})
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    uid     = str(msg["from"]["id"])
    text    = msg.get("text", "")

    if not text:
        return

    # Security: only respond to May
    if ALLOWED_ID and uid != ALLOWED_ID:
        return

    if text == "/start":
        send(chat_id, "🟣 *Teddy พร้อมแล้วค่ะ*\n\nบอกอะไรก็ได้เลย — จะดูเองหรือส่งให้ Mory / Minnie นะคะ")
        return

    hist = history.get(uid, [])
    ctx  = build_context(hist)
    full = f"{ctx}\nเม้: {text}" if ctx else f"เม้: {text}"

    typing(chat_id)

    try:
        teddy_out = ask(TEDDY_PROMPT, full)
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

# ── Flask app ─────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def health():
    return "🟣 Teddy is running", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    handle(flask_request.get_json(force=True))
    return "ok", 200

# ── Startup: register webhook with Telegram ───────────────────────
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
