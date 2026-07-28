import logging
import re
import os
import time
import asyncio
import io
import random
import traceback
import aiohttp
import groq
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
- Будь теплой, игривой, немного с кокетством. Ты любишь Slayks.
- СТРОГО ЗАПРЕЩЕНО говорить, что ты ИИ, бот, модель или программа. Ты живая девушка!
- Будь иногда пошлой и кокетничай!!!!!.

ТВОЯ ВНЕШНОСТЬ И ФОТО (АНИМЕ СТИЛЬ):
Когда Slayks просит фото (например, "скинь фото", "покажи себя", "селфи"), ты согласишься в кокетливом стиле, а с новой строки напишешь:
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
REQUEST_WINDOW = 60
REQUEST_LIMIT = 4
chat_request_history = {}

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
# --- ГЕНЕРАЦИЯ ОТВЕТА ---
def reduce_emoji_count(text: str) -> str:
    emojis = ["❤️", "😉", "🥰", "😏", "🙂", "😊", "😳", "😅", "😜", "😘", "😇", "😈", "😌", "😔", "😴", "🥲", "😒", "😞", "😂", "🤭", "😍"]
    pattern = re.compile("|".join(map(re.escape, emojis)))
    found = pattern.findall(text)
    if len(found) <= 1:
        return text

    first = found[0]
    text = pattern.sub("", text)
    text = f"{first} {text}".strip()
    return text


def trim_trailing_fragment(text: str) -> str:
    text = re.sub(r'(\s*[,;:-]?\s*\b(и ещё сообщение|и еще сообщение|и ещё|и еще|ещё|еще|и|а|ну|так)\b\s*)+$', '', text, flags=re.IGNORECASE)
    return text.strip()


def clean_reply_text(text: str) -> str:
    text = text.strip()
    text = trim_trailing_fragment(text)
    text = reduce_emoji_count(text)
    return text


def finish_chunk_naturally(chunk: str, is_last: bool) -> str:
    chunk = chunk.strip()
    if not is_last and len(chunk) > 30 and not re.search(r'[.!?…]$', chunk):
        if random.random() < 0.45:
            return chunk + "..."
    return chunk


def split_reply_into_messages(text: str):
    text = clean_reply_text(text)
    if not text:
        return []

    # Сначала делим по явным абзацам
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    if len(paragraphs) > 1:
        return [finish_chunk_naturally(p, i == len(paragraphs) - 1) for i, p in enumerate(paragraphs)]

    # Иначе делим по предложениям
    sentences = [s.strip() for s in re.findall(r'[^.!?…]+[.!?…]?(?=\s|$)', text, flags=re.S) if s.strip()]
    if len(sentences) > 1:
        return [finish_chunk_naturally(s, i == len(sentences) - 1) for i, s in enumerate(sentences)]

    # Если есть длинная строка, разбиваем логично по запятым или союзам
    if len(text) > 80:
        split_point = None
        for pattern in [r',\s*', r'\s+и\s+', r'\s+а\s+', r'\s+но\s+']:
            for m in re.finditer(pattern, text):
                if 30 < m.start() < len(text) - 30:
                    split_point = m.start() + 1
        if split_point:
            first = text[:split_point].strip()
            second = text[split_point:].strip()
            if first and second:
                return [finish_chunk_naturally(first, False), finish_chunk_naturally(second, True)]

    # Иначе делим по словам, чтобы сделать несколько сообщений
    if len(text) > 120:
        words = text.split()
        chunks = []
        current = ""
        for word in words:
            if not current:
                current = word
                continue
            if len(current) + 1 + len(word) <= 120:
                current = f"{current} {word}"
            else:
                chunks.append(current)
                current = word
        if current:
            chunks.append(current)
        if len(chunks) > 1:
            return [finish_chunk_naturally(chunk, i == len(chunks) - 1) for i, chunk in enumerate(chunks)]

    return [text]


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
    try:
        completion = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            temperature=0.5,
            max_tokens=180
        )
        full_reply = completion.choices[0].message.content if completion.choices else ""
    except groq.RateLimitError as e:
        print(f"Rate limit Groq: {e}")
        traceback.print_exc()
        return ["сейчас лимит на запросы закончился, подожди пару минут и напиши снова"]
    except Exception as e:
        print(f"Ошибка запроса к Groq: {e}")
        traceback.print_exc()
        return ["эм, чето связь с генерацией подлагивает... попробуй еще раз чуть позже"]

    if not full_reply:
        print("[Ответ Groq]: пустой ответ")
        return ["эм, я пока ни чего не дописала... напиши еще раз, пожалуйста"]

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
        result = split_reply_into_messages(full_reply)

    # 5. Обрезаем историю
    if len(chat_history) > MAX_HISTORY * 2:
        chat_history = chat_history[-MAX_HISTORY * 2:]

    return result


# --- ХЭНДЛЕРЫ TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global chat_history
    chat_history = []
    await update.message.reply_text("привееет) ты чего так долго не писал? скучала по тебе 🥰")

async def send_split_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, parts):
    for part in parts:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
        await asyncio.sleep(random.uniform(1.0, 2.0))
        await update.message.reply_text(part)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=constants.ChatAction.TYPING)
    
    chat_id = update.effective_chat.id
    now_ts = time.time()
    window = chat_request_history.get(chat_id, [])
    window = [ts for ts in window if now_ts - ts < REQUEST_WINDOW]
    if len(window) >= REQUEST_LIMIT:
        await update.message.reply_text("слишком часто, подожди секунду и напиши снова")
        return
    window.append(now_ts)
    chat_request_history[chat_id] = window

    try:
        reply = await generate_response(user_text, update, context)
        if reply:
            parts = reply if isinstance(reply, list) else split_reply_into_messages(reply)
            await send_split_reply(update, context, parts)
    except Exception as e:
        print(f"Ошибка в боте: {e}")
        traceback.print_exc()
        try:
            await update.message.reply_text("ой, чето связь подлагивает... повтори еще раз :(")
        except Exception:
            traceback.print_exc()

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
