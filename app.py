# app.py
import os
import asyncio
from flask import Flask, request, jsonify
from datetime import datetime
from aiogram import Bot, Dispatcher
from aiogram.types import Update, BotCommand
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot import dp, bot, read_rows, write_rows, next_id, generate_unique_alias
from bot import parse_notification  # ДОБАВИТЬ
from bot import FILE_LOCK  # для безопасной записи CSV

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
    if not POLL_STARTED:
        asyncio.create_task(poll_lolz())
        POLL_STARTED = True
    STARTUP_DONE = True

# app.py (после ensure_startup определения)
from lolz_api import LolzClient, LolzError
from bot import dp, bot, read_rows, write_rows, next_id, generate_unique_alias, parse_notification  # если нужно

POLL_STARTED = False

async def poll_lolz():
    client = LolzClient()
    seen = set()
    while True:
        try:
            data = await client.get_recent_purchases(limit=50)
            # TODO: разберите структуру data по доке:
            items = data.get("items") or data.get("data") or []
            for it in items:
                pid = str(it.get("id") or it.get("purchase_id"))
                if not pid or pid in seen:
                    continue
                seen.add(pid)

                # Соберите нужные поля: game/title, description, price и т.п.
                game = (it.get("title") or it.get("game") or "Неизвестная игра").strip()
                account_desc = it.get("description") or ""
                buy_price = float(it.get("price") or 0)

                # Создаём черновик лота (ровно как сейчас при парсинге текста)
                rows = read_rows()
                alias_set = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
                alias = generate_unique_alias(alias_set)
                nid = next_id(rows)
                new = {
                    "id": str(nid),
                    "alias": alias,
                    "source_text": f"lolz_purchase:{pid}",
                    "game": game,
                    "account_desc": account_desc,
                    "buy_price": f"{buy_price:.2f}",
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
                    f"Куплено за: {buy_price:.2f}$\n\n"
                    "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
                )
                # отправим тебе в ЛС — поставь свой user_id или chat_id
                # await bot.send_message(<YOUR_CHAT_ID>, text, reply_markup=kb)

        except Exception as e:
            print("poll error:", e)

        await asyncio.sleep(25)

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
@app.route("/llz_hook", methods=["POST"])
def llz_hook():
    # 1) проверяем секрет
    secret = request.args.get("secret", "")
    if secret != os.getenv("CRON_SECRET"):
        return "Forbidden", 403

    # 2) читаем JSON
    data = request.get_json(force=True, silent=True) or {}
    game = (data.get("game") or "").strip()
    account_desc = (data.get("account_desc") or "").strip()

    # price может прийти строкой — приводим аккуратно
    price_raw = data.get("price")
    try:
        price = float(str(price_raw).replace(",", "."))
    except Exception:
        price = None

    if not game or price is None:
        return "Bad payload", 400

    async def _work():
        # обязательно один раз инициализируем бота/команды
        await ensure_startup()

        # 3) пишем в CSV новый лот
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

        # 4) готовим кнопки и текст — как в боте
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
            f"Куплено за: {price:.2f}$\n\n"
            "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
        )

        # 5) отправляем тебе в личку (или в чат) — возьми ID из переменной окружения
        chat_id = int(os.getenv("ADMIN_CHAT_ID"))
        await bot.send_message(chat_id, draft_text, reply_markup=kb)

    # запускаем асинхронную часть
    asyncio.run(_work())
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
# ===== Роут для приёма уведомлений от «моста» (email/webhook/скрипт) =====
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))  # твой телеграм ID
CRON_SECRET = os.getenv("CRON_SECRET", "")            # секрет для защиты

@app.route("/lolz/email", methods=["POST"])
def lolz_email():
    # 1) простая защита по секрету в заголовке
    sec = request.headers.get("X-Secret")
    if not CRON_SECRET or sec != CRON_SECRET:
        return "forbidden", 403

    # 2) берём текст письма/уведомления из JSON {"text": "..."}
    body = request.get_json(force=True, silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return "bad request", 400

    # 3) парсим текст тем же парсером, что и пересланные уведомления
    parsed = parse_notification(text)
    if not parsed.get("buy_price"):
        return "IGNORED", 200  # пришло не то письмо

    # 4) пишем в CSV и собираем клавиатуру как обычно
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
        Bot.set_current(bot)
        Dispatcher.set_current(dp)
        await bot.send_message(ADMIN_CHAT_ID, msg, reply_markup=kb)

    asyncio.run(_send())
    return "OK", 200
if __name__ == "__main__":
    # Локальный запуск (на Render всё равно стартует через gunicorn)
    app.run(host="0.0.0.0", port=10000)
@app.route("/debug/push_buy/<secret>", methods=["POST"])
def debug_push_buy(secret):
    import json
    from flask import request
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # 1) проверяем секрет
    if secret != os.getenv("CRON_SECRET"):
        return "Forbidden", 403

    data = request.get_json(silent=True) or {}
    game         = data.get("game", "").strip()
    account_desc = data.get("account_desc", "").strip()
    buy_price    = data.get("buy_price", None)

    if not game or buy_price is None:
        return "Bad Request", 400

    # 2) добавляем запись в CSV (функции импортированы из bot.py)
    rows = read_rows()
    existing_aliases = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
    alias = generate_unique_alias(existing_aliases)
    nid = next_id(rows)
    new = {
        "id": str(nid),
        "alias": alias,
        "source_text": f"debug:{game}|{buy_price}|{account_desc}",
        "game": game,
        "account_desc": account_desc,
        "buy_price": f"{float(buy_price):.2f}",
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

    # 3) отправляем такое же сообщение, как при реальном уведомлении
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

    # куда слать уведомление (твой админский чат)
    admin_chat_id = os.getenv("ADMIN_CHAT_ID")  # должен быть задан в Render
    text = (
        f"🆕 Новый лот (ID {nid})\n"
        f"Игра: {game}\n"
        f"Описание: {account_desc}\n"
        f"Куплено за: {float(buy_price):.2f}$\n\n"
        "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
    )

    async def _send():
        from aiogram import Bot, Dispatcher
        Bot.set_current(bot)
        Dispatcher.set_current(dp)
        await bot.send_message(admin_chat_id, text, reply_markup=kb)

    asyncio.run(_send())
    return "OK", 200
