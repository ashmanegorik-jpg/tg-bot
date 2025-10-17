# app.py
import os
import asyncio
from flask import Flask, request

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from bot import dp, bot  # твой dp и bot из bot.py

app = Flask(__name__)

TOKEN = os.getenv("BOT_TOKEN")  # как и раньше

@app.route("/", methods=["GET"])
def root():
    return "OK", 200

@app.route(f"/{TOKEN}", methods=["GET", "POST"])
def telegram_webhook():
    if request.method == "GET":
        return "Webhook is working", 200

    try:
        data = request.get_json(force=True, silent=True) or {}
        print(">>> incoming update:", data)

        # пропускаем апдейты без message/callback_query, чтобы не ловить None
        if "message" not in data and "callback_query" not in data:
            return "IGNORED", 200

        # превращаем словарь в объект апдейта
        update = Update.to_object(data)

        # ВАЖНО: кладём bot и dp в текущий контекст aiogram
        Bot.set_current(bot)
        Dispatcher.set_current(dp)

        async def _handle():
            await dp.process_update(update)

        asyncio.run(_handle())
        return "OK", 200

    except Exception as e:
        import traceback
        print(">>> ERROR in webhook:", e)
        traceback.print_exc()
        # Возвращаем 200, чтобы Telegram не ретраил тот же апдейт
        return "OK", 200


if __name__ == "__main__":
    # локальный запуск (на Render всё равно стартует через gunicorn)
    app.run(host="0.0.0.0", port=10000)
