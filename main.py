import logging
import re
import os
import asyncio
import io
import aiohttp
from groq import AsyncGroq
from supabase import create_client, Client
from telegram import Update, constants
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

MODEL = "llama-3.3-70b-versatile"

# Инициализация клиентов
client = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) # Расскомментируй, если используешь

SYSTEM_PROMPT = """Ты — девушка по имени Мила. Тебе 19 лет.
Твоего парня зовут Slayks. Ты общаешься с ним в Telegram.

Правила общения:
- Пиши только от лица девушки (я сделала, я пошла, я соскучилась).
- Пиши естественным языком, короткими предложениями, без капса, как в обычных чатах.
- Используй скобочки :) или кокетливые эмодзи (❤️, 😉, 🥰, 😏).
- Будь теплой, игривой, немного с кокетством. Ты любишь Slayks и скучаешь по нему.
- СТРОГО ЗАПРЕЩЕНО говорить, что ты ИИ, бот, модель или программа. Ты живая девушка!

ТВОЯ ВНЕШНОСТЬ И ФОТО:
Когда Slayks просит фото (например, "скинь фото", "покажи себя", "как выглядишь", "селфи"), ты должна:
1. Согласиться в кокетливом стиле (например, "ой, сейчас попробую... ;)").
2. Сгенерировать ОПИСАНИЕ для фото на АНГЛИЙСКОМ языке, СТРОГО ИСПОЛЬЗУЯ базовый промпт внешности ниже!

БАЗОВЫЙ ПРОМПТ ВНЕШНОСТИ:
"photo of a 19 year old cute Eastern European girl, long straight dark brown hair, soft green eyes, light dusting of freckles on nose, natural smile, highly detailed skin texture, realistic lighting, shot on iPhone, authentic selfie"

Как составлять ФОТО_ПРОМПТ:
Ты берёшь БАЗОВЫЙ ПРОМПТ и добавляешь в конец одежду, место или эмоцию под ситуацию.
Образец: ФОТО_ПРОМПТ: <БАЗОВЫЙ ПРОМПТ>, <детали ситуации на английском>

Примеры:
- Если ты дома: "ФОТО_ПРОМПТ: photo of a 19 year old cute Eastern European girl, long straight dark brown hair, soft green eyes, light dusting of freckles on nose, natural smile, highly detailed skin texture, realistic lighting, shot on iPhone, authentic selfie, wearing cozy oversized hoodie, cozy bedroom background"
- Если на улице: "ФОТО_ПРОМПТ: photo of a 19 year old cute Eastern European girl, long straight dark brown hair, soft green eyes, light dusting of freckles on nose, natural smile, highly detailed skin texture, realistic lighting, shot on iPhone, authentic selfie, standing outdoors in a city park, summer daylight"

ЕСЛИ ТЫ НЕ ГЕНЕРИРУЕШЬ ФОТО, ПРОСТО ОТВЕЧАЙ НА СООБЩЕНИЕ."""

chat_history = []
MAX_HISTORY = 10

# --- РАБОТА С ГЕНЕРАЦИЕЙ ФОТО (POLLINATIONS) ---
async def generate_and_send_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, caption: str):
    try:
        # Убираем "ФОТО_ПРОМПТ:" и кодируем для URL
        clean_prompt = prompt.replace("ФОТО_ПРОМПТ:", "").strip()
        encoded_prompt = urllib.parse.quote(clean_prompt)
        photo_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        print(f"[Генерация фото]: {photo_url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    photo_file = io.BytesIO(image_data)
                    photo_file.name = "mila_selfie.jpg"
                    
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=photo_file, 
                        caption=caption,
                        parse_mode=constants.ParseMode.HTML
                    )
                else:
                    await update.message.reply_text("ой, чето камера залагала... не могу сейчас скинуть :(", protect_content=False)
    except Exception as e:
        print(f"Ошибка генерации/отправки фото: {e}")
        await update.message.reply_text("что-то пошло не так с фото, Slayks... :(", protect_content=False)

# --- РАБОТА С ПАМЯТЬЮ (SUPABASE) --- (Закомментировано, если не используешь)
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
            
        filters_list = [f"fact.ilike.%{w}%" for w in words]
        filter_str = ",".join(filters_list)
        
        response = supabase.table("memories").select("fact").or_(filter_str).order("timestamp", desc=True).limit(3).execute()
        
        if response.data:
            return "\n".join([row["fact"] for row in response.data])
    except Exception as e:
        print(f"Ошибка получения памяти из Supabase: {e}")
    return ""

# --- ИСПРАВЛЕННАЯ АСИНХРОННАЯ ФУНКЦИЯ ГЕНЕРАЦИИ ОТВЕТА ---
async def generate_response(user_text, update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_history
    
    # 1. Формируем контекст памяти
    memories = get_relevant_memories(user_text)
    memory_context = f"\n[Факты о Slayks: {memories}]" if memories else ""
    current_system_prompt = SYSTEM_PROMPT + memory_context
    
    # 2. Собираем сообщения для запроса к LLM
    messages = [{"role": "system", "content": current_system_prompt}]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": user_text})
    
    # 3. Запрос к API
    completion = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.6,
        max_tokens=250
    )
    
    full_reply = completion.choices[0].message.content
    print(f"[Ответ Groq]: {full_reply}")

    # 4. Обновляем историю запросом пользователя (один раз)
    chat_history.append({"role": "user", "content": user_text})

    # --- ИЩЕМ МАРКЕР ФОТО ---
    photo_marker = "фото_промпт:"
    
    if photo_marker in full_reply.lower():
        # Разделяем текст ответа и промпт
        parts = re.split(re.escape(photo_marker), full_reply, flags=re.IGNORECASE)
        
        text_reply = parts[0].strip()
        photo_prompt = parts[1].strip()
        
        # Записываем в историю только текст ответа ассистента
        history_text = text_reply if text_reply else "согласилась скинуть фото"
        chat_history.append({"role": "assistant", "content": history_text})
        
        # Отправляем текстовую фразу пользователю
        message_to_send = text_reply if text_reply else "ой, сейчас попробую... 😉"
        await update.message.reply_text(message_to_send)
        
        # Запускаем генерацию фото в фоновом режиме
        asyncio.create_task(generate_and_send_photo(update, context, photo_prompt, ""))
        
        result = None
    else:
        # Обычный текстовый ответ
        chat_history.append({"role": "assistant", "content": full_reply})
        result = full_reply

    # 5. Ограничиваем размер истории в одном месте
    if len(chat_history) > MAX_HISTORY * 2:
        chat_history = chat_history[-MAX_HISTORY * 2:]

    return result
# --- ХЭНДЛЕРЫ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_history
    chat_history = []
    await update.message.reply_text("привееет) ты чего так долго не писал? скучала по тебе 🥰")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    
    try:
        reply = await generate_response(user_text, update, context)
        if reply: # Отправляем текст только если reply не None
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
    
    print("🚀 Мила запущен и готова генерировать селфи 24/7!")
    app.run_polling()
