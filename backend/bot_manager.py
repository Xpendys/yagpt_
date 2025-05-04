import threading
import time
import logging
import requests
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from backend.database import SessionLocal
from backend.models import User
from backend.config import YANDEX_API_KEY, YANDEX_FOLDER_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKEND_URL = "http://localhost:8000"

def get_yandex_gpt_response(prompt, system_prompt):
    try:
        response = requests.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers={
                "Authorization": f"Api-Key {YANDEX_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt",
                "completionOptions": {
                    "temperature": 0.7,
                    "maxTokens": 2000
                },
                "messages": [
                    {
                        "role": "system",
                        "text": system_prompt
                    },
                    {
                        "role": "user",
                        "text": prompt
                    }
                ]
            }
        )
        response.raise_for_status()
        return response.json()["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        logger.error(f"Ошибка при обращении к Yandex GPT: {e}")
        return "Извините, произошла ошибка при генерации ответа."

class BotThread(threading.Thread):
    def __init__(self, token, user_id):
        super().__init__()
        self.token = token
        self.user_id = user_id
        self.application = None
        self.running = True
        self.loop = None

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
            
        # Получаем актуальный системный промпт из базы данных
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == self.user_id).first()
            if user is not None and user.system_prompt is not None:
                user_message = update.message.text
                response = get_yandex_gpt_response(user_message, user.system_prompt)
                await update.message.reply_text(response)
            else:
                await update.message.reply_text("Извините, системный промпт не настроен.")
        finally:
            db.close()

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        try:
            self.application = Application.builder().token(self.token).build()
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
            self.loop.run_until_complete(self.application.initialize())
            self.loop.run_until_complete(self.application.start())
            self.loop.run_until_complete(self.application.run_polling())
        except Exception as e:
            logger.error(f"Ошибка в боте {self.user_id}: {e}")
        finally:
            self.loop.close()

    def stop(self):
        self.running = False
        if self.application is not None and self.loop is not None:
            try:
                async def stop_application():
                    if self.application is not None:
                        await self.application.stop()
                        await self.application.shutdown()
                if self.loop.is_running():
                    self.loop.run_until_complete(stop_application())
            except Exception as e:
                logger.error(f"Ошибка при остановке бота {self.user_id}: {e}")

class BotManager:
    def __init__(self):
        self.bots = {}  # user_id: BotThread

    def update_bots(self):
        db = SessionLocal()
        try:
            users = db.query(User).filter(User.tg_bot_token != None, User.tg_bot_token != '').all()
            user_tokens = {user.id: user.tg_bot_token for user in users}

            # Остановить ботов, которых больше нет или токен изменился
            for user_id in list(self.bots.keys()):
                if user_id not in user_tokens or self.bots[user_id].token != user_tokens[user_id]:
                    logger.info(f"Останавливаю бота для user_id={user_id}")
                    self.bots[user_id].stop()
                    self.bots[user_id].join()
                    del self.bots[user_id]

            # Запустить новых ботов
            for user_id, token in user_tokens.items():
                if user_id not in self.bots:
                    logger.info(f"Запускаю бота для user_id={user_id}")
                    bot_thread = BotThread(token, user_id)
                    bot_thread.start()
                    self.bots[user_id] = bot_thread
        finally:
            db.close()

    def run(self):
        while True:
            self.update_bots()
            time.sleep(10)  # Проверять каждые 10 секунд 