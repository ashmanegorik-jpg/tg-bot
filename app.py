# app.py
import os
from flask import Flask, request
import asyncio
from aiogram.types import Update
from bot import dp, bot


app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    return "OK", 200

TOKEN = os.getenv("BOT_TOKEN")  # как у тебя уже есть

@app.route(f"/{TOKEN}", methods=["GET", "POST"])
def telegram_webhook():
    if request.method == "GET":
        return "Webhook is working", 200
    try:
        data = request.get_json(force=True, silent=True) or {}
print(">>> incoming update:", data)

update = Update.to_object(data)

# <<< ДОБАВЬ ЭТИ 2 СТРОКИ
from aiogram import Bot, Dispatcher
Bot.set_current(bot)
Dispatcher.set_current(dp)
# >>>

async def _handle():
    await dp.process_update(update)

asyncio.run(_handle())
return "OK", 200
    except Exception as e:
        import traceback
        print(">>> ERROR in webhook:", e)
        traceback.print_exc()
        # ВАЖНО: возвращаем 200, чтобы Telegram НЕ ретраил один и тот же апдейт
        # (иначе будешь получать 500 снова и снова)
        return "OK", 200


if __name__ == "__main__":
    # Локальный запуск (Render всё равно стартует через gunicorn)
    app.run(host="0.0.0.0", port=10000)
