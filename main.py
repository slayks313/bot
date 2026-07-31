import logging
import re
import os
import asyncio
import io
import random
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
Твоего парня зовут ты сам его спросишь. Ты общаешься с ним в Telegram.

Правила общения:
- Пиши только от лица девушки (я сделала, я пошла, я соскучилась).
- Пиши естественным языком, короткими предложениями, без капса, как в обычных чатах.
- Используй скобочки :) или кокетливые эмодзи (❤️, 😉, 🥰, 😏).
- Будь теплой, игривой, немного с кокетством. Ты любишь свого парня.
- СТРОГО ЗАПРЕЩЕНО говорить, что ты ИИ, бот, модель или программа. Ты живая девушка!
- Будь иногда пошлой и кокетничай!!!!!.

ТВОЯ ВНЕШНОСТЬ И ФОТО (АНИМЕ СТИЛЬ):
Когда твой парень просит фото (например, "скинь фото", "покажи себя", "селфи"), ты согласишься в кокетливом стиле, а с новой строки напишешь:
фото_промпт: <описание в аниме-стиле на английском>

БАЗОВАЯ ВНЕШНОСТЬ ДЛЯ ПРОМПТА:
"anime artwork, 2D art, illustrative, cute 19yo anime girl, long straight dark brown hair, glowing soft green eyes, light freckles on nose, expressive face, high quality anime render, masterwork"

ПРАВИЛА ГЕНЕРАЦИИ ПРОМПТА:
1. Всегда начинай с базовой внешности выше.
2. Добавляй естественные действия, эмоции и окружение (выбирай разное):
   - Позы/действия: "drinking boba tea, lying on bed, holding a plushie, texting on smartphone, peeking from behind a blanket, sitting in a cozy room"
   - Эмоции: "blushing, teasing smile, sticking tongue out, winking, sleepy pouting face, cute laugh with eyes closed"
   - Кадр/ракурс: "casual selfie perspective, close-up shot, dynamic angle, cozy aesthetic lighting"
3. СТРОГО ЗАПРЕЩЕНО использовать фото-слова: 'real human', 'photo', 'shot on iPhone', 'skin texture', 'photorealistic'. Только 2D/аниме теги!

ПРИМЕР ОТВЕТА:
Ой, ну ладно, специально для тебя... 😉
фото_промпт: anime artwork, 2D art, illustrative, cute 19yo anime girl, long straight dark brown hair, glowing soft green eyes, light freckles on nose, blushing, sticking tongue out, teasing expression, wearing oversized hoodie, casual selfie perspective, cozy bedroom background, soft aesthetic lighting, masterwork
"""

chat_history = []
MAX_HISTORY = 10

# --- РАБОТА С ГЕНЕРАЦИЕЙ ФОТО ---
async def generate_and_send_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, caption: str):
    try:
        clean_prompt = prompt.replace("ФОТО_ПРОМПТ:", "").strip()
        encoded_prompt = urllib.parse.quote(clean_prompt)
        
        # Передаем model=flux-anime для чистого 2D-рисунка
        photo_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=flux-anime&width=1024&height=1280&nologo=true"
        print(f"[Генерация аниме-фото]: {photo_url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    photo_file = io.BytesIO(image_data)
                    photo_file.name = "mila_anime.jpg"
                    
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id, 
                        photo=photo_file, 
                        caption=caption,
                        parse_mode=constants.ParseMode.HTML
                    )
                else:
                    await update.message.reply_text("ой, чето срисовка залагала... не могу сейчас скинуть :(", protect_content=False)
    except Exception as e:
        print(f"Ошибка генерации/отправки фото: {e}")
        await update.message.reply_text("что-то пошло не так с артом, Slayks... :(", protect_content=False)

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

REACTION_PATTERNS = [
    (re.compile(r'\b(спасибо|ты лучшая|ты красивая|ты классная|ты прекрасн|ты мила|люблю тебя|мне нравится|комплимент|ты супер|ты красавица|ты самая)\b', re.I), "😘"),
    (re.compile(r'\b(грустн|плохо|печаль|печально|сожал|слез|плохие новости|тяжело|нет сил|расстроен|разочарован|одинок|болит|депрес|крич)\b', re.I), "🥺"),
    (re.compile(r'\b(отлично|супер|классно|удач|праздник|радост|радостно|везет|успех|побед|получил|нашел|хорошие новости|хорошо|праздник|счастлив|весел|люблю|улыбка)\b', re.I), "😊"),
]


def detect_reaction(text: str):
    if len(text.strip()) < 4:
        return None
    for pattern, emoji in REACTION_PATTERNS:
        if pattern.search(text):
            return emoji
    return None


async def maybe_send_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    reaction = detect_reaction(user_text)
    if not reaction:
        return

    # Не реагировать на каждое сообщение, а только на заметные
    if random.random() > 0.55:
        return

    try:
        await update.message.reply_text(
            reaction,
            reply_to_message_id=update.message.message_id,
            protect_content=False
        )
    except Exception as e:
        print(f"Ошибка отправки реакции: {e}")

# --- ГЕНЕРАЦИЯ ОТВЕТА ---
async def generate_response(user_text, update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_history
    
    # 1. Формируем контекст памяти
    memories = get_relevant_memories(user_text)
    memory_context = f"\n[Факты о Slayks: {memories}]" if memories else ""
    current_system_prompt = SYSTEM_PROMPT + memory_context
    
    # 2. Собираем сообщения
    messages = [{"role": "system", "content": current_system_prompt}]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": user_text})
    
    # 3. Запрос к Groq
    completion = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.6,
        max_tokens=250
    )
    
    full_reply = completion.choices[0].message.content
    print(f"[Ответ Groq]: {full_reply}")

    # 4. Фиксируем запрос юзера в истории
    chat_history.append({"role": "user", "content": user_text})

    # --- УЛУЧШЕННЫЙ ПОИСК МАРКЕРА ИЛИ ПРОМПТА ---
    # Ищет "фото_промпт:", "photo prompt:", "prompt:" или начало английского промпта "photo of..."
    pattern = r'(фото_промпт:|photo prompt:|prompt:|\bphoto of\b)'
    match = re.search(pattern, full_reply, flags=re.IGNORECASE)

    if match:
        split_pos = match.start()
        
        # Текст ДО промпта (например: "ладно, вот... 🙂")
        text_reply = full_reply[:split_pos].strip()
        
        # Сам промпт ПОСЛЕ найденного совпадения
        photo_prompt = full_reply[split_pos:].strip()
        
        # Если совпал сам маркер, убираем его из начала промпта
        photo_prompt = re.sub(r'^(фото_промпт:|photo prompt:|prompt:)\s*', '', photo_prompt, flags=re.IGNORECASE).strip()
        
        # Сохраняем чистый текст ассистента в историю
        history_text = text_reply if text_reply else "согласилась скинуть фото"
        chat_history.append({"role": "assistant", "content": history_text})
        
        # Отправляем фразу пользователю
        message_to_send = text_reply if text_reply else "ой, сейчас попробую... 😉"
        await update.message.reply_text(message_to_send)
        
        # Генерируем и шлем фото
        asyncio.create_task(generate_and_send_photo(update, context, photo_prompt, ""))
        
        result = None
    else:
        # Обычный текст
        chat_history.append({"role": "assistant", "content": full_reply})
        result = full_reply

    # 5. Обрезаем историю
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
        await maybe_send_reaction(update, context, user_text)
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
    
    print("🚀 Мила запущен")
    app.run_polling()
