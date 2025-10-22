# app.py
import os
import asyncio
from flask import Flask, request, jsonify
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.types import Update, BotCommand
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot import dp, bot, read_rows, write_rows, next_id, generate_unique_alias

app = Flask(__name__)

# Токен читаем из переменных окружения Render
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

# --- Ставит меню команд один раз при первом апдейте ---
STARTUP_DONE = False

async def ensure_startup():
    """
    Выполняется один раз: регистрируем меню команд в Telegram.
    """
    global STARTUP_DONE
    if STARTUP_DONE:
        return

    # Кладём bot/dp в текущий контекст aiogram
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

        # Пропускаем апдейты без message/callback_query
        if "message" not in data and "callback_query" not in data:
            return "IGNORED", 200

        # Словарь -> объект Update
        update = Update.to_object(data)

        # Важно: положить bot/dp в текущий контекст aiogram
        Bot.set_current(bot)
        Dispatcher.set_current(dp)

        async def _handle():
            # Меню команд ставим один раз
            await ensure_startup()
            # Обрабатываем апдейт
            await dp.process_update(update)

        asyncio.run(_handle())
        return "OK", 200

    except Exception as e:
        import traceback
        print(">>> ERROR in webhook:", e)
        traceback.print_exc()
        # Возвращаем 200, чтобы Telegram не ретраил тот же апдейт
        return "OK", 200

@app.post("/lolz/notify")
def lolz_notify():
    """
    Ждём JSON от Lolz (или твоего посредника) вида:
    {
      "game": "кс",
      "description": "CS2 Prime | Garrys Mod",
      "price": 7.29
    }
    Ключи можно подстроить: title/amount/buy_price и т.п.
    """
    data = request.get_json(silent=True) or {}
    game = (data.get("game") or data.get("title") or "").strip()
    account_desc = (data.get("description") or data.get("desc") or "").strip()
    price_val = data.get("price") or data.get("amount") or data.get("buy_price")

    if not game or price_val is None:
        return jsonify({"ok": False, "error": "missing game or price"}), 400

    try:
        price_f = float(str(price_val).replace(",", "."))
    except Exception:
        return jsonify({"ok": False, "error": "bad price"}), 400

    # Создаём новый лот в CSV (как будто пришло обычное уведомление)
    rows = read_rows()
    existing_aliases = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
    alias = generate_unique_alias(existing_aliases)
    nid = next_id(rows)

    new = {
        "id": str(nid),
        "alias": alias,
        "source_text": "lolz:webhook",
        "game": game,
        "account_desc": account_desc,
        "buy_price": f"{price_f:.2f}",
        "buy_date": datetime.utcnow().isoformat(),
        "status": "in_stock",
        "min_sale_for_target": "",
        "notes": "",
        "sell_price": "",
        "sell_date": "",
        "net_profit": ""
    }
    rows.append(new)
    write_rows(rows)

    # Клавиатура точно как в боте
    kb = InlineKeyboardMarkup(row_width=4)
    kb.add(
        InlineKeyboardButton("Профит $0.5", callback_data=f"profit:{nid}:0.5"),
        InlineKeyboardButton("Профит $1",   callback_data=f"profit:{nid}:1"),
        InlineKeyboardButton("Профит $2",   callback_data=f"profit:{nid}:2"),
    )
    kb.add(InlineKeyboardButton("Custom", callback_data=f"profit:{nid}:custom"))
    kb.add(
        InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
        InlineKeyboardButton("Отметить проданным",      callback_data=f"sold_direct:{nid}")
    )

    draft_text = (
        f"🆕 Новый лот (ID {nid})\n"
        f"Игра: {game}\n"
        f"Описание: {account_desc}\n"
        f"Куплено за: {price_f:.2f}$\n\n"
        "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
    )

    # Шлём сообщение админу
    if ADMIN_CHAT_ID:
        async def _send():
            await bot.send_message(ADMIN_CHAT_ID, draft_text, reply_markup=kb)
        try:
            asyncio.run(_send())
        except RuntimeError:
            # на случай, если цикл уже запущен
            loop = asyncio.get_event_loop()
            loop.create_task(_send())

    return jsonify({"ok": True, "id": nid})
if __name__ == "__main__":
    # Локальный запуск (на Render всё равно стартует через gunicorn)
    app.run(host="0.0.0.0", port=10000)
