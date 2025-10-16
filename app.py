# app.py
import os
import asyncio
from flask import Flask, request
from aiogram import types, Bot, Dispatcher

# Импортируем из твоего bot.py
from bot import bot, dp

app = Flask(__name__)

# Один event loop на всё приложение
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

@app.route("/", methods=["GET"])
def health():
    return "OK", 200

# Вебхук на /<TOKEN>
TOKEN = os.getenv("BOT_TOKEN")
@app.route(f"/{TOKEN}", methods=["GET", "POST"])
def telegram_webhook():
    if request.method == "GET":
        return "Webhook OK", 200

    data = request.get_json(force=True, silent=False)
    update = types.Update(**data)

    # Привязываем текущие объекты к контексту aiogram
    Bot.set_current(bot)
    Dispatcher.set_current(dp)

    # Обрабатываем апдейт
    loop.run_until_complete(dp.process_updates([update]))
    return "OK", 200

if __name__ == "__main__":
    # Локальный запуск (Render всё равно стартует через gunicorn)
    app.run(host="0.0.0.0", port=10000)
