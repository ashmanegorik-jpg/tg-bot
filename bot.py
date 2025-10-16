import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor

logging.basicConfig(level=logging.INFO)

# === Настройки из переменных окружения ===
TOKEN = os.getenv("BOT_TOKEN")              # токен бота (задашь в Railway)
APP_URL = os.getenv("APP_URL", "").rstrip("/")  # публичный URL Railway, например https://your-app.up.railway.app
PORT = int(os.getenv("PORT", 8080))         # Railway сам проставит PORT

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан (Environment variable)")

WEBHOOK_PATH = f"/{TOKEN}"
WEBHOOK_URL = f"{APP_URL}{WEBHOOK_PATH}" if APP_URL else None

# === Aiogram ===
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

@dp.message_handler(commands=["start", "help"])
async def cmd_start(m: types.Message):
    await m.answer("Привет! Бот на вебхуках запущен ✅")

# --- хуки старта/остановки ---
async def on_startup(dp: Dispatcher):
    if not WEBHOOK_URL:
        logging.warning("APP_URL не задан — вебхук не будет установлен.")
        return
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook set to: {WEBHOOK_URL}")

async def on_shutdown(dp: Dispatcher):
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Webhook deleted")

if __name__ == "__main__":
    # запускаем встроенный web-сервер aiogram
    executor.start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host="0.0.0.0",
        port=PORT,
    )


