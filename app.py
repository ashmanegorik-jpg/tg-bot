# app.py
import os
from flask import Flask, request
import asyncio
from aiogram.types import Update
from bot_logic import dp


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
        # логируем входящее сырьё — очень полезно
        print(">>> incoming update:", data)

        # ВАЖНО: пропускаем всё, что не message/callback_query,
        # чтобы не падать внутри aiogram на NoneType
        if "message" not in data and "callback_query" not in data:
            return "IGNORED", 200

        update = Update(**data)
        # запускаем обработку строго для одного апдейта
        asyncio.run(dp.process_updates([update]))

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
