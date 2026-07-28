import logging
import re
import os
import asyncio
from groq import AsyncGroq
from supabase import create_client, Client
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

MODEL = "llama-3.3-70b-versatile"

# Инициализация клиентов
client = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

# --- РАБОТА С ПАМЯТЬЮ (SUPABASE) ---
def save_memory(topic, fact, emotion=""):
    try:
        supabase.table("memories").insert({
            "topic": topic,
            "fact": fact,
            "emotional_context": emotion
        }).execute()
        print(f"[Память сохранена в Supabase]: {fact}")
    except Exception as e:
        print(f"Ошибка сохранения в Supabase: {e}")

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
    except Exception as e:
        print(f"Ошибка извлечения памяти: {e}")

def get_relevant_memories(query):
    try:
        words = [w for w in re.findall(r'\b\w+\b', query.lower()) if len(w) > 3]
        if not words:
            return ""
            
        # Формируем ilike поиск для Supabase
        filters_list = [f"fact.ilike.%{w}%" for w in words]
        filter_str = ",".join(filters_list)
        
        response = supabase.table("memories").select("fact").or_(filter_str).order("timestamp", desc=True).limit(3).execute()
        
        if response.data:
            return "\n".join([row["fact"] for row in response.data])
    except Exception as e:
        print(f"Ошибка получения памяти из Supabase: {e}")
    return ""

# --- ГЕНЕРАЦИЯ ОТВЕТА ---
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

# --- ХЭНДЛЕРЫ TELEGRAM ---
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

# --- ВЕБ-СЕРВЕР ДЛЯ RENDER HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_check():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

Thread(target=run_health_check, daemon=True).start()

if __name__ == "__main__":
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("🚀 Мила запущен с Supabase 24/7!")
    app.run_polling()
