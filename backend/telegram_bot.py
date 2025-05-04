import logging
import requests
from telegram.ext import Updater, MessageHandler, Filters
from backend.config import TELEGRAM_BOT_TOKEN

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKEND_URL = "http://localhost:8000"

def ask_backend(prompt):
    try:
        response = requests.post(
            f"{BACKEND_URL}/ask/",
            json={"prompt": prompt},
            timeout=60
        )
        response.raise_for_status()
        return response.json().get("answer", "Нет ответа от модели.")
    except Exception as e:
        logger.error(f"Ошибка при обращении к backend: {e}")
        return "Произошла ошибка при обращении к серверу."

def handle_message(update, context):
    user_message = update.message.text
    response = ask_backend(user_message)
    update.message.reply_text(response)

def run_bot():
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_message))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    run_bot()