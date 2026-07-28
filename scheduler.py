import threading
import random
import time
from datetime import datetime, timedelta
from bot_config import (
    SPONTANEOUS_MIN,
    SPONTANEOUS_MAX,
    TEST_SPONTANEOUS_MIN,
    TEST_SPONTANEOUS_MAX,
    SLEEP_START_HOUR,
    WAKE_UP_HOUR_MIN,
    WAKE_UP_HOUR_MAX,
    MOOD_UPDATE_INTERVAL,
)


class Scheduler(threading.Thread):
    """Запускает цикл отправки спонтанных сообщений и обновления настроения."""

    def __init__(self, personality, messenger, user='User', test_mode=False):
        super().__init__(daemon=True)
        self.personality = personality
        self.messenger = messenger
        self.user = user
        self.test_mode = test_mode
        self._stop = threading.Event()

    def in_sleep_window(self, now: datetime) -> bool:
        if now.hour == SLEEP_START_HOUR:
            return True
        # ночь — между 00:00 и случайным пробуждением в 7-8
        # Для простоты проверяем только часы
        if 0 <= now.hour < WAKE_UP_HOUR_MIN:
            return True
        return False

    def next_wake_time(self) -> datetime:
        # выбрать случайное утро между 7:00 и 8:00 ближайшего дня
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wake_hour = random.randint(WAKE_UP_HOUR_MIN, WAKE_UP_HOUR_MAX)
        return tomorrow.replace(hour=wake_hour)

    def run(self):
        # основной цикл
        while not self._stop.is_set():
            now = datetime.now()
            # обновление настроения
            try:
                self.personality.update_mood()
            except Exception:
                pass

            # сон — отправить прощание в 00:00 и заснуть до утра
            if self.in_sleep_window(now):
                # если только что стало полночь — попрощаться
                if now.hour == SLEEP_START_HOUR and now.minute == 0:
                    self.messenger.send_to_user(self.user, 'Всё, я спать. Пока.')
                # ждать до утра
                wake = self.next_wake_time()
                wait_seconds = max(60, (wake - now).total_seconds())
                # Фоновый сон — ждем, пока не проснемся
                self._stop.wait(wait_seconds)
                continue

            # вычислить интервал
            if self.test_mode:
                min_s, max_s = TEST_SPONTANEOUS_MIN, TEST_SPONTANEOUS_MAX
            else:
                min_s, max_s = SPONTANEOUS_MIN, SPONTANEOUS_MAX
            interval = random.randint(min_s, max_s)

            # ждем случайный интервал, но прерываем при stop
            waited = 0
            while waited < interval and not self._stop.is_set():
                time.sleep(1)
                waited += 1

            if self._stop.is_set():
                break

            # сгенерировать и отправить спонтанное сообщение
            msgs, reaction = self.personality.generate_replies(self.user)
            if msgs:
                self.messenger.send_spontaneous(self.user, msgs, reaction)

    def stop(self):
        self._stop.set()
