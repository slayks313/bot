import threading
import time
from datetime import datetime
from typing import List
from personality import Personality
from memory import Memory


class Messenger:
    """Простейший симулятор мессенджера. В реале сюда интегрируют API (Telegram, WhatsApp и т.д.)
    Для проверки работает в консоли: user вводит сообщения, бот отвечает в stdout.
    """

    def __init__(self, memory: Memory, personality: Personality):
        self.memory = memory
        self.personality = personality
        self._lock = threading.Lock()

    def receive_user_message(self, user: str, text: str):
        # Симулирует получение сообщения от пользователя и передачу его личностям
        replies, reaction = self.personality.handle_incoming(user, text)
        # Если бот игнорирует — просто ничего не выводим
        if not replies:
            return
        for r in replies:
            self.send_to_user(user, r)
            time.sleep(0.25)  # небольшая пауза между короткими сообщениями
        if reaction:
            self.send_reaction(user, '🔥')

    def send_spontaneous(self, user: str, messages: List[str], reaction: bool):
        for m in messages:
            self.send_to_user(user, m)
            time.sleep(0.25)
        if reaction:
            self.send_reaction(user, '✨')

    def send_to_user(self, user: str, text: str):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts}] Бот -> {user}: {text}")

    def send_reaction(self, user: str, emoji: str):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{ts}] Бот reacts to {user}'s last message: {emoji}")

