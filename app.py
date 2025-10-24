# app.py
import os
import asyncio
from datetime import datetime

from flask import Flask, request, jsonify

from aiogram import Bot, Dispatcher
from aiogram.types import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton

# наши модули
from bot import (
    dp, bot, read_rows, write_rows, next_id,
    generate_unique_alias, parse_notification, FILE_LOCK,
    create_lot_and_prompt
)
from lzt_scraper import poll_new_texts, debug_probe

app = Flask(__name__)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)

STARTUP_DONE = False

async def ensure_startup():
    """Ставим команды в ТГ один раз."""
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


@app.get("/")
def root():
    return "OK", 200


@app.route(f"/{TOKEN}", methods=["GET", "POST"])
def telegram_webhook():
    if request.method == "GET":
        return "Webhook is working", 200

    try:
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

    except Exception as e:
        import traceback
        print(">>> ERROR in webhook:", e)
        traceback.print_exc()
        # отдаём 200, чтобы Telegram не ретраил
        return "OK", 200


# ---------- Ручной мост (POST /llz_hook?secret=...) ----------
@app.post("/llz_hook")
def llz_hook():
    secret = request.args.get("secret", "")
    if secret != os.getenv("CRON_SECRET"):
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
            existing_aliases = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
            alias = generate_unique_alias(existing_aliases)
            nid = next_id(rows)
            new = {
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
            }
            rows.append(new)
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


# ---------- Вебхук от стороннего сервиса (если появится) ----------
@app.post("/lolz/notify")
def lolz_notify():
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

    async def _send():
        await ensure_startup()
        if ADMIN_CHAT_ID:
            await bot.send_message(ADMIN_CHAT_ID, draft_text, reply_markup=kb)

    asyncio.run(_send())
    return jsonify({"ok": True, "id": nid})


# ---------- Приём «сырого текста» (например, с почты) ----------
@app.post("/lolz/email")
def lolz_email():
    sec = request.headers.get("X-Secret")
    cron_secret = os.getenv("CRON_SECRET", "")
    if not cron_secret or sec != cron_secret:
        return "forbidden", 403

    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return "bad request", 400

    parsed = parse_notification(text)
    if not parsed.get("buy_price"):
        return "IGNORED", 200

    rows = read_rows()
    existing_aliases = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
    alias = generate_unique_alias(existing_aliases)
    nid = next_id(rows)

    new = {
        "id": str(nid),
        "alias": alias,
        "source_text": parsed["source_text"],
        "game": parsed["game"],
        "account_desc": parsed["account_desc"],
        "buy_price": f"{float(parsed['buy_price']):.2f}",
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

    msg = (
        f"🆕 Новый лот (ID {nid})\n"
        f"Игра: {parsed['game']}\n"
        f"Описание: {parsed['account_desc']}\n"
        f"Куплено за: {float(parsed['buy_price']):.2f}$\n\n"
        "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
    )

    async def _send():
        await ensure_startup()
        if ADMIN_CHAT_ID:
            await bot.send_message(ADMIN_CHAT_ID, msg, reply_markup=kb)

    asyncio.run(_send())
    return "OK", 200


# ---------- GET-версия теста ----------
@app.get("/debug/push_buy_get")
def debug_push_buy_get():
    secret = request.args.get("secret", "")
    if secret != os.getenv("CRON_SECRET"):
        return "Forbidden", 403

    game = (request.args.get("game") or "").strip()
    account_desc = (request.args.get("account_desc") or request.args.get("desc") or "").strip()
    price_raw = request.args.get("price") or request.args.get("buy_price")

    try:
        price_f = float(str(price_raw).replace(",", "."))
    except Exception:
        return "Bad Request", 400
    if not game:
        return "Bad Request", 400

    rows = read_rows()
    existing_aliases = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
    alias = generate_unique_alias(existing_aliases)
    nid = next_id(rows)
    new = {
        "id": str(nid),
        "alias": alias,
        "source_text": f"debug_get:{game}|{price_f}|{account_desc}",
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
        f"Куплено за: {price_f:.2f}$\n\n"
        "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
    )

    async def _send():
        await ensure_startup()
        admin_id = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
        if admin_id:
            await bot.send_message(admin_id, text, reply_markup=kb)

    asyncio.run(_send())
    return "OK", 200


# ---------- Кнопка «потянуть вручную» (скрапер) ----------
@app.get("/poll")
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


# ---------- Диагностика скрапера ----------
@app.get("/scraper_debug_open")
def scraper_debug_open():
    try:
        info = debug_probe()
        return jsonify(info)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
