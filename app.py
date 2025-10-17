# app.py
import os
import asyncio
from flask import Flask, request

from aiogram import Bot, Dispatcher
from aiogram.types import Update, BotCommand

from bot import dp, bot, set_bot_commands  # + set_bot_commands
  # твой dp и bot из bot.py

app = Flask(__name__)

try:
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(set_bot_commands())
    else:
        loop.run_until_complete(set_bot_commands())
    print(">>> Bot commands set")
except Exception as e:
    print(">>> Failed to set bot commands:", e)
    
TOKEN = os.getenv("BOT_TOKEN")  # как и раньше

# --- ставим команды один раз при первом апдейте ---
STARTUP_DONE = False

async def ensure_startup():
    """
    Выполняется один раз: регистрируем меню команд в Telegram.
    """
    global STARTUP_DONE
    if STARTUP_DONE:
        return
    # на всякий случай кладём bot/dp в текущий контекст aiogram
    Bot.set_current(bot)
    Dispatcher.set_current(dp)

    await bot.set_my_commands([
        BotCommand("start", "Показать список команд"),
        BotCommand("add_buy", "Игра|Цена|Примечание — добавить вручную"),
        BotCommand("list", "Показать лоты в наличии"),
        BotCommand("generate_listing", "<id> <target_net> — расчёт цены"),
        BotCommand("mark_published", "<id> — отметить опубликованным"),
        BotCommand("sold", "<id>|<price> — отметить продажу"),
        BotCommand("stats", "Общая статистика"),
        BotCommand("monthly", "YYYY-MM — статистика за месяц"),
        BotCommand("export", "Экспорт CSV"),
    ])
    STARTUP_DONE = True


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
    await ensure_startup()           # <<< ВАЖНО: команды установятся один раз
    await dp.process_update(update)

        
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
