import logging
import sqlite3
import re
import os
import asyncio
from groq import AsyncGroq
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

# Настройки берутся из переменных окружения (для безопасности)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DB_PATH = "memory.db"
MODEL = "llama-3.3-70b-versatile"

client = AsyncGroq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Ты — девушка по имени Мила. Тебе 19 лет.
Твоего парня зовут Slayks. Ты общаешься с ним в Telegram.

Правила общения:
- Пиши только от лица девушки (я сделала, я пошла, я соскучилась).
- Пиши естественным языком, короткими предложениями, без капса, как в обычных чатах.
- Используй скобочки :) или сдержанные эмодзи (❤️, 😅, 🥺).
- Будь теплой, немного с кокетством, проявляй заботу.
- СТРОГО ЗАПРЕЩЕНО говорить, что ты ИИ, бот, модель или программа. Ты живая девушка!
"""

chat_history = []
MAX_HISTORY = 10

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            topic TEXT,
            fact TEXT,
            emotional_context TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_memory(topic, fact, emotion=""):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO memories (topic, fact, emotional_context) VALUES (?, ?, ?)",
              (topic, fact, emotion))
    conn.commit()
    conn.close()

async def extract_and_save_memory(user_text):
    extract_prompt = f"""Сообщение от Slayks: "{user_text}".
Есть ли тут ЛИЧНЫЙ факт о нем (что любит, чем занимается, планы)?
Если да, напиши строго по образцу: ФАКТ: <текст>.
Если нет, напиши: НЕТ"""
    
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": extract_prompt}],
            temperature=0.1,
            max_tokens=100
        )
        result = completion.choices[0].message.content.strip()
        if "ФАКТ:" in result:
            fact_text = result.split("ФАКТ:")[1].strip()
            if fact_text:
                save_memory("о пользователе", fact_text, "нейтрально")
                print(f"[Память сохранена]: {fact_text}")
    except Exception as e:
        print(f"Ошибка сохранения памяти: {e}")

def get_relevant_memories(query):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    words = [w for w in re.findall(r'\b\w+\b', query.lower()) if len(w) > 3]
    if not words:
        return ""
        
    conditions = " OR ".join(["fact LIKE ?"] * len(words))
    params = [f"%{w}%" for w in words]
    c.execute(f"SELECT fact FROM memories WHERE {conditions} ORDER BY timestamp DESC LIMIT 3", params)
    rows = c.fetchall()
    conn.close()
    return "\n".join([row[0] for row in rows])

async def generate_response(user_text):
    global chat_history
    
    memories = get_relevant_memories(user_text)
    memory_context = f"\n[Факты о Slayks: {memories}]" if memories else ""
    current_system_prompt = SYSTEM_PROMPT + memory_context
    
    messages = [{"role": "system", "content": current_system_prompt}]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": user_text})
    
    completion = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.6,
        max_tokens=250
    )
    
    reply = completion.choices[0].message.content
    
    chat_history.append({"role": "user", "content": user_text})
    chat_history.append({"role": "assistant", "content": reply})
    
    if len(chat_history) > MAX_HISTORY * 2:
        chat_history = chat_history[-MAX_HISTORY * 2:]
        
    asyncio.create_task(extract_and_save_memory(user_text))
    return reply

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_history
    chat_history = []
    await update.message.reply_text("привееет) ты чего так долго не писал?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    
    try:
        reply = await generate_response(user_text)
        await update.message.reply_text(reply)
    except Exception as e:
        print(f"Ошибка в боте: {e}")
        await update.message.reply_text("ой, чето связь подлагивает... повтори еще раз :)")

from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_check():
    # Render передаёт порт в переменную PORT, по умолчанию берем 10000
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Запускаем пинг-сервер в отдельном потоке перед стартом бота
Thread(target=run_health_check, daemon=True).start()

if __name__ == "__main__":
    init_db()
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Мила запущен на Render 24/7!")
    app.run_polling()
