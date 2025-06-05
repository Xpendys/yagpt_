import logging
import requests
from telegram import Update
from telegram.ext import (ApplicationBuilder, MessageHandler,
                          ContextTypes, filters)
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_message = update.message.text
    response = ask_backend(user_message)
    await update.message.reply_text(response)

async def run_bot():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    await application.run_polling()

def main():
    import asyncio
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
