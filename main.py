import os
import re
import logging
import threading
import asyncio
from flask import Flask
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from prompts import TEDDY_PROMPT, MORY_PROMPT, MINNIE_PROMPT, YEN_PROMPT

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID", "")  # May's Telegram user ID

genai.configure(api_key=GEMINI_API_KEY)

# In-memory chat history (resets on restart)
history: dict[str, list] = {}

# ── Gemini helper ─────────────────────────────────────────────────
def ask(system: str, prompt: str) -> str:
    model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=system)
    return model.generate_content(prompt).text.strip()

def detect_route(text: str) -> str:
    t = text.lower()
    if "[route:mory]"   in t: return "mory"
    if "[route:minnie]" in t: return "minnie"
    return "teddy"

def strip_tags(text: str) -> str:
    return re.sub(r"\[route:\w+\]", "", text, flags=re.IGNORECASE).strip()

def build_context(hist: list) -> str:
    return "\n".join(
        f"{'เม้' if h['role'] == 'user' else 'ผู้ช่วย'}: {h['text']}"
        for h in hist[-6:]
    )

# ── Telegram handlers ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟣 *Teddy พร้อมแล้วค่ะ*\n\nบอกอะไรก็ได้เลย — จะดูเองหรือส่งให้ Mory / Minnie ตามความเหมาะสมนะคะ",
        parse_mode="Markdown"
    )

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)

    # Security: only respond to May
    if ALLOWED_USER_ID and uid != ALLOWED_USER_ID:
        return

    msg  = update.message.text
    hist = history.get(uid, [])
    ctx  = build_context(hist)
    full = f"{ctx}\nเม้: {msg}" if ctx else f"เม้: {msg}"

    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    try:
        teddy_out = ask(TEDDY_PROMPT, full)
        route     = detect_route(teddy_out)

        if route == "mory":
            await context.bot.send_chat_action(update.effective_chat.id, "typing")
            brief    = strip_tags(teddy_out)
            mory_out = ask(MORY_PROMPT, f"Teddy ส่งมา: {brief}\nคำขอ: {msg}")
            reply    = f"🟠 *Mory:*\n{mory_out}"

        elif route == "minnie":
            await context.bot.send_chat_action(update.effective_chat.id, "typing")
            brief      = strip_tags(teddy_out)
            minnie_out = ask(MINNIE_PROMPT, f"Teddy ส่งมา: {brief}\nคำขอ: {msg}")

            if any(c.isdigit() for c in minnie_out):
                await context.bot.send_chat_action(update.effective_chat.id, "typing")
                yen_out = ask(YEN_PROMPT, f"ตรวจสอบจาก Minnie:\n{minnie_out}")
                reply   = f"🩷 *Minnie:*\n{minnie_out}\n\n🔵 *Yen ตรวจแล้ว:*\n{yen_out}"
            else:
                reply = f"🩷 *Minnie:*\n{minnie_out}"

        else:
            reply = f"🟣 *Teddy:*\n{strip_tags(teddy_out)}"

        # Save history
        hist.extend([
            {"role": "user",      "text": msg},
            {"role": "assistant", "text": reply}
        ])
        history[uid] = hist[-20:]

        await update.message.reply_text(reply, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("⚠️ เกิดข้อผิดพลาด ลองใหม่อีกครั้งนะคะ")

# ── Telegram bot runner (background thread) ───────────────────────
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.run_polling(drop_pending_updates=True)

# ── Flask health check (main thread — keeps Render alive) ─────────
web = Flask(__name__)

@web.route("/")
def health():
    return "🟣 Teddy is running", 200

if __name__ == "__main__":
    # Telegram bot runs in background thread
    threading.Thread(target=run_bot, daemon=True).start()

    # Flask web server runs in main thread (required by Render)
    port = int(os.getenv("PORT", 5000))
    web.run(host="0.0.0.0", port=port)
