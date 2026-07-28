import logging
import re
import os
import asyncio
import io
import random
import json
import datetime
import urllib.parse
from zoneinfo import ZoneInfo
import aiohttp

from groq import AsyncGroq
from supabase import create_client, Client
from telegram import Update, constants, ReactionTypeEmoji
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

MODEL = "llama-3.3-70b-versatile"
TIMEZONE = ZoneInfo("Asia/Tashkent")

client = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

TARGET_CHAT_ID = None

# --- МОДУЛЬ 1: РАБОТА С СОСТОЯНИЕМ В БД ---

def get_state():
    try:
        res = supabase.table("mila_state_v2").select("*").eq("id", 1).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"[State Get Error]: {e}")
    return {
        "trust": 80, "love": 85, "jealousy": 10, "mood": "playful",
        "last_user_msg_time": datetime.datetime.now(TIMEZONE).isoformat(),
        "last_sleep_date": None, "last_wake_date": None
    }

def update_state(data: dict):
    try:
        supabase.table("mila_state_v2").update(data).eq("id", 1).execute()
    except Exception as e:
        print(f"[State Update Error]: {e}")

def get_active_offenses():
    try:
        res = supabase.table("offense_history").select("*").eq("forgiven", False).execute()
        return res.data or []
    except Exception as e:
        return []

# --- МОДУЛЬ 2: ДЕТЕРМИНИРОВАННЫЙ ДВИЖОК НАСТРОЕНИЙ (PYTHON LOGIC) ---

def calculate_python_mood(state: dict, active_offenses: list, user_text_intent: str = None) -> str:
    """Вычисляет жесткое настроение в Python, а не отдаёт его LLM"""
    now = datetime.datetime.now(TIMEZONE)
    
    # 1. Сонное время
    if 0 <= now.hour < 7:
        return "sleepy"
        
    # 2. Обиды
    if len(active_offenses) > 0:
        if state["trust"] < 40:
            return "angry"
        return "sad"
        
    # 3. Низкое доверие
    if state["trust"] < 30:
        return "angry"
        
    # 4. Ревность
    if state["jealousy"] > 60:
        return "jealous"
        
    # 5. Интенты пользователя
    if user_text_intent == "compliment":
        return "happy"
    if user_text_intent == "flirt" and state["love"] > 70:
        return "horny" if random.random() < 0.4 else "romantic"
        
    # 6. Временные задержки (Скучание)
    if state.get("last_user_msg_time"):
        last_msg = datetime.datetime.fromisoformat(state["last_user_msg_time"])
        hours_passed = (now - last_msg).total_seconds() / 3600
        if hours_passed > 24:
            return "sad"
        elif hours_passed > 6:
            return "pouting"

    # 7. Зависимость от уровня любви
    if state["love"] > 90 and random.random() < 0.3:
        return "romantic"
        
    return "playful"

# --- МОДУЛЬ 3: ИСТОРИЯ СООБЩЕНИЙ В SUPABASE ---

def push_chat_history(role: str, content: str):
    try:
        supabase.table("chat_history_db").insert({"role": role, "content": content}).execute()
    except Exception as e:
        print(f"[DB History Insert Error]: {e}")

def get_db_chat_history(limit=15):
    try:
        res = supabase.table("chat_history_db").select("role, content").order("id", desc=True).limit(limit).execute()
        if res.data:
            history = res.data[::-1] # Разворачиваем в хронологическом порядке
            return [{"role": r["role"], "content": r["content"]} for r in history]
    except Exception as e:
        print(f"[DB History Fetch Error]: {e}")
    return []

# --- МОДУЛЬ 4: АНАЛИЗАТОР ИНТЕНТА (LLM только оценивает текст) ---

async def analyze_input_text(user_text: str):
    """LLM здесь только классифицирует текст, но НЕ меняет состояние напрямую"""
    prompt = f"""Проанализируй текст парня: "{user_text}"
Ответь СТРОГО в JSON:
{{
  "intent": "compliment / insult / apology / flirt / casual / question",
  "insult_reason": "если оскорбление, укажи причину, иначе null",
  "is_sincere_apology": true/false,
  "extracted_event": "если они договорились о чем-то или произошел важный факт (например 'посмотрели фильм'), укажи иначе null",
  "reaction_emoji": "один из [🥰, ❤️, 😏, 🤣, 🙄, 😒, 🫶, 😭, 💀] или null"
}}"""

    try:
        comp = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            response_format={"type": "json_object"}
        )
        return json.loads(comp.choices[0].message.content)
    except Exception as e:
        print(f"[Analysis Error]: {e}")
        return {"intent": "casual", "insult_reason": None, "is_sincere_apology": False, "extracted_event": None, "reaction_emoji": None}

# --- МОДУЛЬ 5: ГЕНЕРАЦИЯ ЖИВОГО ОТВЕТА ---

async def send_living_messages(context: ContextTypes.DEFAULT_TYPE, chat_id: int, full_text: str):
    parts = [p.strip() for p in full_text.split("===") if p.strip()]
    if not parts:
        parts = [p.strip() for p in full_text.split("\n") if p.strip()]

    for idx, part in enumerate(parts):
        await context.bot.send_chat_action(chat_id=chat_id, action=constants.ChatAction.TYPING)
        typing_time = min(max(len(part) * 0.07, 1.5), 5.0)
        await asyncio.sleep(typing_time)
        
        await context.bot.send_message(chat_id=chat_id, text=part)
        if idx < len(parts) - 1:
            await asyncio.sleep(random.uniform(2.0, 4.5))

async def generate_response(user_text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = get_state()
    active_offenses = get_active_offenses()
    now = datetime.datetime.now(TIMEZONE)

    # 1. Анализируем интент входящего сообщения
    analysis = await analyze_input_text(user_text)
    
    # 2. Корректируем метрики в Python на основе интента
    delta_trust, delta_love, delta_jealousy = 0, 0, 0
    intent = analysis.get("intent")
    
    if intent == "compliment":
        delta_love += 2
        delta_trust += 1
    elif intent == "insult":
        delta_love -= 6
        delta_trust -= 8
        # Фиксируем новую обиду в БД
        supabase.table("offense_history").insert({
            "reason": analysis.get("insult_reason", "Грубость"),
            "severity": 6
        }).execute()
    elif intent == "apology" and analysis.get("is_sincere_apology"):
        delta_trust += 4
        # Прощаем самую старую активную обиду
        if active_offenses:
            supabase.table("offense_history").update({"forgiven": True}).eq("id", active_offenses[0]["id"]).execute()

    # Сохраняем событие, если произошло что-то важное
    if analysis.get("extracted_event"):
        supabase.table("relationship_events").insert({
            "event_type": "user_event",
            "description": analysis["extracted_event"]
        }).execute()

    # 3. Высчитываем НОВОЕ настроение через Python State Machine
    new_trust = max(0, min(100, state["trust"] + delta_trust))
    new_love = max(0, min(100, state["love"] + delta_love))
    new_jealousy = max(0, min(100, state["jealousy"] + delta_jealousy))
    
    active_offenses = get_active_offenses() # Перезапрашиваем обиды
    calculated_mood = calculate_python_mood(state, active_offenses, intent)

    # Обновляем состояние в БД
    update_state({
        "trust": new_trust,
        "love": new_love,
        "jealousy": new_jealousy,
        "mood": calculated_mood,
        "last_user_msg_time": now.isoformat()
    })

    # Ставим случайную реакцию (~20% шанс)
    if analysis.get("reaction_emoji") and random.random() < 0.2:
        try:
            await context.bot.set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                reaction=[ReactionTypeEmoji(analysis["reaction_emoji"])]
            )
        except Exception:
            pass

    # 4. Проверка на Игнор при обиде (с паузой)
    if active_offenses and random.random() < 0.3:
        await asyncio.sleep(random.uniform(5, 12))
        try:
            await context.bot.set_message_reaction(
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                reaction=[ReactionTypeEmoji("😒")]
            )
        except Exception:
            pass
        return

    # 5. Сборка Системного Промпта на основе жестких ограничений характера
    emoji_rule = "НЕ используй сердечки, эмодзи и будь холодной." if new_trust < 40 else "Используй эмодзи ❤️, 😉, 🥰, 😏."
    
    # Загружаем последние события из БД
    events_res = supabase.table("relationship_events").select("description").order("id", desc=True).limit(5).execute()
    events_list = "\n".join([f"- {e['description']}" for e in events_res.data]) if events_res.data else "Нет важных событий."

    offenses_text = "\n".join([f"- {o['reason']}" for o in active_offenses]) if active_offenses else "Обид нет."

    system_prompt = f"""Ты — девушка по имени Мила, 19 лет. Твоего парня зовут Slayks.

ЖЕСТКИЕ ПРАВИЛА ТВОЕГО ТЕКУЩЕГО СОСТОЯНИЯ:
- Твое настроение (рассчитано системой): {calculated_mood}
- Уровень доверия: {new_trust}/100
- Уровень любви: {new_love}/100
- Правило эмодзи: {emoji_rule}
- Активные обиды:
{offenses_text}

ПОСЛЕДНИЕ СОБЫТИЯ В ВАШИХ ОТНОШЕНИЯХ:
{events_list}

ОФОРМЛЕНИЕ:
- Пиши короткими репликами. Разделяй отдельные мысли строкой `===`.
- Никогда не упоминай ИИ, алгоритмы или программы.

ФОТО: Если просит фото, ответь согласием, а с новой строки напиши:
фото_промпт: anime artwork, 2D art, illustrative, cute 19yo anime girl, long straight dark brown hair, glowing soft green eyes, light freckles on nose, [описание]
"""

    # Достаем историю диалога из Supabase
    db_history = get_db_chat_history(limit=12)
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(db_history)
    messages.append({"role": "user", "content": user_text})

    completion = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        max_tokens=350
    )

    full_reply = completion.choices[0].message.content

    # Сохраняем в Supabase
    push_chat_history("user", user_text)

    if "фото_промпт:" in full_reply.lower():
        parts = re.split(r'фото_промпт:', full_reply, flags=re.IGNORECASE)
        text_reply = parts[0].strip()
        photo_prompt = parts[1].strip()

        if text_reply:
            await send_living_messages(context, update.effective_chat.id, text_reply)
            push_chat_history("assistant", text_reply)
        asyncio.create_task(generate_and_send_photo(update, context, photo_prompt))
    else:
        push_chat_history("assistant", full_reply)
        await send_living_messages(context, update.effective_chat.id, full_reply)

# --- МОДУЛЬ 6: ГЕНЕРАЦИЯ ФОТО ---

async def generate_and_send_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    try:
        clean_prompt = prompt.replace("фото_промпт:", "").strip()
        encoded_prompt = urllib.parse.quote(clean_prompt)
        photo_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=flux-anime&width=1024&height=1280&nologo=true"

        async with aiohttp.ClientSession() as session:
            async with session.get(photo_url) as resp:
                if resp.status == 200:
                    image_data = await resp.read()
                    photo_file = io.BytesIO(image_data)
                    photo_file.name = "mila.jpg"
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_file)
    except Exception as e:
        print(f"[Photo Error]: {e}")

# --- МОДУЛЬ 7: УМНЫЙ ФОНОВЫЙ КРОН (СПАЧ, УТРО, АВТО-СООБЩЕНИЯ) ---

async def precise_scheduler_job(context: ContextTypes.DEFAULT_TYPE):
    """Запускается каждые 60 секунд для точной проверки событий"""
    global TARGET_CHAT_ID
    if not TARGET_CHAT_ID:
        return

    now = datetime.datetime.now(TIMEZONE)
    today_str = now.strftime("%Y-%m-%d")
    state = get_state()
    active_offenses = get_active_offenses()

    # 1. ПРОВЕРКА "СПОКОЙНОЙ НОЧИ" (После 00:00, если сегодня еще не прощалась)
    if now.hour == 0 and state.get("last_sleep_date") != today_str:
        update_state({"last_sleep_date": today_str})
        goodnight_msg = "все, я укуталась в одеялко и отключаюсь... 🥱===спокойной ночи, любимый ❤️"
        await send_living_messages(context, TARGET_CHAT_ID, goodnight_msg)
        push_chat_history("assistant", goodnight_msg)
        return

    # 2. ПРОВЕРКА "ДОБРОЕ УТРО" (Гарантированно 1 раз в день между 07:15 и 08:45)
    if (now.hour == 7 and now.minute >= 15) or (now.hour == 8 and now.minute <= 45):
        if state.get("last_wake_date") != today_str:
            update_state({"last_wake_date": today_str})
            morning_msg = random.choice([
                "доброе утро) ты уже проснулся?☀️",
                "утречка 🥰===я только встала, пью чаек",
                "привееет! как спалось? ❤️"
            ])
            await send_living_messages(context, TARGET_CHAT_ID, morning_msg)
            push_chat_history("assistant", morning_msg)
            return

    # 3. АВТО-СООБЩЕНИЯ (ПО NEXT_AUTO_MSG_TIME ИЗ БД)
    # Если наступила ночь или есть обиды — не писать
    if 0 <= now.hour < 7 or len(active_offenses) > 0:
        return

    next_auto = state.get("next_auto_msg_time")
    if next_auto:
        next_auto_dt = datetime.datetime.fromisoformat(next_auto)
        if now >= next_auto_dt:
            # Наступило время написать!
            # Генерируем новое время следующего автосообщения: NOW + random(2..5 часов)
            hours_delay = random.uniform(2, 5)
            next_time = now + datetime.timedelta(hours=hours_delay)
            update_state({"next_auto_msg_time": next_time.isoformat()})

            # Проверяем, сколько времени парень молчит
            last_user_time = datetime.datetime.fromisoformat(state["last_user_msg_time"])
            hours_silent = (now - last_user_time).total_seconds() / 3600

            if hours_silent > 24:
                prompt_text = "Ты не писал мне больше суток. Спроси с грустью и обидой, где он пропал."
            elif hours_silent > 6:
                prompt_text = "Парень молчит больше 6 часов. Спроси кокетливо, чем он занят."
            else:
                prompt_text = "Напиши случайную мысль, поделись делом (например, пьешь чай или читаешь) или спроси что-то."

            comp = await client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": f"Ты Мила. Настроение: {state['mood']}. Разделяй фразы через '==='."},
                    {"role": "user", "content": prompt_text}
                ],
                temperature=0.85
            )

            reply = comp.choices[0].message.content
            await send_living_messages(context, TARGET_CHAT_ID, reply)
            push_chat_history("assistant", reply)
    else:
        # Если время еще не задано, ставим первично на 2 часа вперед
        next_time = now + datetime.timedelta(hours=2)
        update_state({"next_auto_msg_time": next_time.isoformat()})

# --- ХЭНДЛЕРЫ TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TARGET_CHAT_ID
    TARGET_CHAT_ID = update.effective_chat.id
    await update.message.reply_text("привееет) наконец-то ты тут 🥰")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global TARGET_CHAT_ID
    TARGET_CHAT_ID = update.effective_chat.id
    await generate_response(update.message.text, update, context)

# --- ВЕБ-СЕРВЕР HEALTH CHECK ---

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

    # Крон проверяет условия каждую минуту
    job_queue = app.job_queue
    job_queue.run_repeating(precise_scheduler_job, interval=60, first=10)

    print("🚀 Personality Engine 2.0 запущен!")
    app.run_polling()
