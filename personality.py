import random
import re
import time
from typing import List, Tuple
from memory import Memory
from bot_config import REACTION_PROBABILITY, FOLLOWUP_PROBABILITY, APOLOGY_THRESHOLD


class Personality:
    """Логика личности: настроение, реакция на оскорбления и извинения, генерация сообщений."""

    INSULT_KEYWORDS = [
        'дурак', 'идиот', 'тупой', 'глупый', 'жалкий', 'ничтожество', 'хрен', 'ублюдок', 'отстой', 'мерзавец'
    ]
    APOLOGY_KEYWORDS = ['извини', 'прости', 'прошу прощения', 'извиняюсь', 'sorry']
    STRONG_WORDS = ['очень', 'искренне', 'сердечно', 'действительно', 'клянусь']

    MOODS = ['happy', 'neutral', 'sad', 'angry']

    def __init__(self, memory: Memory):
        self.memory = memory
        # базовое настроение
        mood = self.memory.get_state('mood')
        self.mood = mood if mood else 'neutral'
        # offended per user stored in state as 'offended:<user>' = '1'/'0'

    # --- Detection ---
    def detect_insult(self, text: str) -> bool:
        t = text.lower()
        for k in self.INSULT_KEYWORDS:
            if k in t:
                return True
        return False

    def detect_apology(self, text: str) -> Tuple[bool, int]:
        t = text.lower()
        count = 0
        for k in self.APOLOGY_KEYWORDS:
            if k in t:
                count += 1
        # усилители
        for s in self.STRONG_WORDS:
            if s in t:
                count += 1
        return (count > 0, count)

    # --- State helpers ---
    def set_offended(self, user: str, value: bool):
        key = f'offended:{user}'
        self.memory.set_state(key, '1' if value else '0')

    def is_offended(self, user: str) -> bool:
        key = f'offended:{user}'
        val = self.memory.get_state(key)
        return val == '1'

    # --- Mood ---
    def update_mood(self):
        # Простейшая случайная динамика: небольшое смещение настроения
        idx = self.MOODS.index(self.mood) if self.mood in self.MOODS else 1
        shift = random.choice([-1, 0, 1])
        idx = max(0, min(len(self.MOODS) - 1, idx + shift))
        self.mood = self.MOODS[idx]
        self.memory.set_state('mood', self.mood)

    # --- Generation ---
    def generate_replies(self, user: str, incoming: str = None) -> Tuple[List[str], bool]:
        """
        Возвращает список сообщений (1 или 2) и флаг реакций.
        Если бот обижен на пользователя, он игнорирует сообщения (пустой список).
        """
        if self.is_offended(user):
            # игнорировать
            return ([], False)

        # Основной короткий текст в зависимости от настроения
        base = {
            'happy': ['О, привет! Как настроение?', 'Рада тебя видеть 😊'],
            'neutral': ['Привет.', 'Да?'],
            'sad': ['Ммм...', 'Не очень сейчас, но могу послушать.'],
            'angry': ['Хм.', 'Если честно, сейчас не в духе.']
        }[self.mood]

        first = random.choice(base)

        messages = [first]
        # иногда добавляет короткое доп.сообщение
        if random.random() < FOLLOWUP_PROBABILITY:
            followups = [
                'И ещё...',
                'Кстати, да.',
                'Если коротко: спасибо.',
                'Маленькое дополнение.'
            ]
            messages.append(random.choice(followups))

        # Решение ставить реакцию
        reaction = random.random() < REACTION_PROBABILITY
        return (messages, reaction)

    # Обработка входящего сообщения от пользователя
    def handle_incoming(self, user: str, text: str) -> Tuple[List[str], bool]:
        # Проверка на оскорбление
        if self.detect_insult(text):
            # сохранить в БД
            self.memory.add_insult(user, text)
            self.set_offended(user, True)
            # моментальный ответ (короткий и обиженный)
            return (["Так не годится. Я запомнила это."], False)

        # проверка на извинение
        is_apol, strength = self.detect_apology(text)
        if is_apol and self.is_offended(user):
            # если сила достаточна — прощаем и удаляем оскорбления
            if strength >= APOLOGY_THRESHOLD:
                self.memory.clear_insults(user)
                self.set_offended(user, False)
                return (["Ладно. Я прощаю. Удаляю то, что помнила."], True)
            else:
                return (["Слышно " + ('немного ' if strength==1 else '') + "извинение, но мне нужно чуть больше."], False)

        # Иначе обычный генератор
        return self.generate_replies(user, text)
