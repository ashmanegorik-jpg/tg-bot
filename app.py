# app.py
import os
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify

from aiogram import Bot, Dispatcher
from aiogram.types import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton

from lzt_scraper import poll_new_texts
from bot import (
    dp, bot, read_rows, write_rows, next_id, generate_unique_alias,
    parse_notification, FILE_LOCK, create_lot_and_prompt
)

app = Flask(__name__)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

STARTUP_DONE = False

async def ensure_startup():
    global STARTUP_DONE
    if STARTUP_DONE:
        return
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

    data = request.get_json(force=True, silent=True) or {}
    if "message" not in data and "callback_query" not in data:
        return "IGNORED", 200

    update = Update.to_object(data)
    Bot.set_current(bot)
    Dispatcher.set_current(dp)

    async def _handle():
        await ensure_startup()
        await dp.process_update(update)

    asyncio.run(_handle())
    return "OK", 200


@app.route("/llz_hook", methods=["POST"])
def llz_hook():
    if request.args.get("secret", "") != os.getenv("CRON_SECRET"):
        return "Forbidden", 403

    data = request.get_json(force=True, silent=True) or {}
    game = (data.get("game") or "").strip()
    account_desc = (data.get("account_desc") or "").strip()
    price_raw = data.get("price")

    try:
        price = float(str(price_raw).replace(",", "."))
    except Exception:
        price = None

    if not game or price is None:
        return "Bad payload", 400

    async def _work():
        await ensure_startup()
        async with FILE_LOCK:
            rows = read_rows()
            alias = generate_unique_alias({(r.get("alias") or "").lower() for r in rows if r.get("alias")})
            nid = next_id(rows)
            rows.append({
                "id": str(nid),
                "alias": alias,
                "source_text": f"llz_hook:{game}|{price:.2f}|{account_desc}",
                "game": game,
                "account_desc": account_desc,
                "buy_price": f"{price:.2f}",
                "buy_date": datetime.utcnow().isoformat(),
                "status": "in_stock",
                "min_sale_for_target": "",
                "notes": "",
                "sell_price": "",
                "sell_date": "",
                "net_profit": ""
            })
            write_rows(rows)

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

        text = (
            f"🆕 Новый лот (ID {nid})\n"
            f"Игра: {game}\n"
            f"Описание: {account_desc}\n"
            f"Куплено за: {price:.2f}$\n\n"
            "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
        )
        if ADMIN_CHAT_ID:
            await bot.send_message(ADMIN_CHAT_ID, text, reply_markup=kb)

    asyncio.run(_work())
    return "OK", 200


@app.route("/poll", methods=["GET"])
def poll():
    if request.args.get("secret") != os.getenv("CRON_SECRET"):
        return "forbidden", 403

    try:
        new_texts = poll_new_texts()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    async def _send():
        await ensure_startup()
        for t in new_texts:
            parsed = parse_notification(t)
            if parsed.get("buy_price"):
                await create_lot_and_prompt(parsed, ADMIN_CHAT_ID)

    asyncio.run(_send())
    return jsonify({"ok": True, "delivered": len(new_texts)})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
