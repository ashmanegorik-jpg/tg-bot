import os
import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==== ТОКЕН ТОЛЬКО ЗДЕСЬ: API_TOKEN ====
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is not set")

bot = Bot(token=API_TOKEN)  # без parse_mode
dp = Dispatcher(bot)


# ====== HELP-текст и меню команд ======
HELP_TEXT = (
    "Привет! Я бот для учёта и подготовки листингов.\n\n"
    "Работаю с CSV-хранилищем.\n\n"
    "Присылай пересланные уведомления от автобая (или используй /add_buy).\n\n"
    "Команды:\n"
    "/add_buy Игра|Цена|Примечание — добавить вручную\n"
    "/list — показать текущие не проданные лоты\n"
    "/generate_listing <id> <target_net> — получить мин. цену и шаблон\n"
    "/mark_published <id> — пометить как опубликованный\n"
    "/sold <id>|<price> — отметить как проданный (пример: /sold 3|10)\n"
    "/stats — общая статистика\n"
    "/monthly YYYY-MM — статистика за месяц\n"
    "/export — экспорт CSV\n"
)

async def set_bot_commands():
    commands = [
        BotCommand("start", "помощь и список команд"),
        BotCommand("add_buy", "Игра|Цена|Примечание"),
        BotCommand("list", "лоты в наличии"),
        BotCommand("generate_listing", "<id> <target_net>"),
        BotCommand("mark_published", "<id>"),
        BotCommand("sold", "<id>|<price>"),
        BotCommand("stats", "общая статистика"),
        BotCommand("monthly", "YYYY-MM"),
        BotCommand("export", "выгрузка CSV"),
    ]
    await bot.set_my_commands(commands)
# ======================================


# ========== НАСТРОЙКИ ==========
API_TOKEN = os.getenv("BOT_TOKEN")  # ВАЖНО: читаем BOT_TOKEN (как в Render)
DATA_CSV = os.path.join(os.path.dirname(__file__), "inventory.csv")
COMMISSION = 0.06  # 6% (3% продажа + 3% вывод)
# ==============================

if not API_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is not set")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Создаём CSV если его нет
FIELDNAMES = [
    "id", "source_text", "game", "account_desc", "buy_price",
    "buy_date", "status", "min_sale_for_target", "notes",
    "sell_price", "sell_date", "net_profit"
]

if not os.path.exists(DATA_CSV):
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()

# ---- Утилиты для CSV ----
def read_rows():
    with open(DATA_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def write_rows(rows):
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

def next_id(rows):
    if not rows:
        return 1
    return max(int(r["id"]) for r in rows) + 1

def to_decimal(s):
    """Преобразует строку с ',' или '.' в Decimal, убирает пробелы и NBSP."""
    if s is None:
        raise InvalidOperation
    s = s.replace("\xa0", "").replace(" ", "").strip()
    s = s.replace(",", ".")
    return Decimal(s)

def calc_min_sale(buy_price, target_net=1.0):
    # sale = (target_net + buy) / (1 - COMMISSION)
    buy = Decimal(str(buy_price))
    target = Decimal(str(target_net))
    denom = Decimal("1") - Decimal(str(COMMISSION))
    sale = (target + buy) / denom
    return float(sale.quantize(Decimal("0.01")))

def calc_net_from_sale(sale_price, buy_price):
    sale = Decimal(str(sale_price))
    buy = Decimal(str(buy_price))
    received = sale * (Decimal("1") - Decimal(str(COMMISSION)))
    net = received - buy
    return float(net.quantize(Decimal("0.01")))

# ---- Парсер уведомления ----
RE_LINK = re.compile(r'По вашей ссылке\s*["“](?P<link>[^"”]+)["”]', re.IGNORECASE)
RE_BUYPRICE = re.compile(r'за\s*([\d\.,]+)\s*\$')
RE_KUP = re.compile(r'куплен аккаунт\s*(?P<acc>.*?)\s*(?:В сети:|в сети:|за\s|$)', re.IGNORECASE)
RE_GAME_QUOTE_FALLBACK = re.compile(r'["“](?P<g>[^"”]+)["”]')

def parse_notification(text: str):
    text = text.strip()
    game = None
    account_desc = ""
    buy_price = None

    m = RE_LINK.search(text)
    if m:
        game = m.group("link").strip()

    m2 = RE_KUP.search(text)
    if m2:
        account_desc = m2.group("acc").strip()

    m3 = RE_BUYPRICE.search(text)
    if m3:
        price_str = m3.group(1)
        try:
            buy_price = float(str(to_decimal(price_str)))
        except Exception:
            buy_price = None

    if not game:
        m4 = RE_GAME_QUOTE_FALLBACK.search(text)
        if m4:
            game = m4.group("g").strip()

    if not game:
        m5 = re.search(r'(.+?)куплен аккаунт', text, re.IGNORECASE)
        if m5:
            game = m5.group(1).strip().split()[-1]

    return {
        "game": game or "",
        "account_desc": account_desc or "",
        "buy_price": buy_price,
        "source_text": text
    }

# ---- Команды/Handlers ----
@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    await message.answer(HELP_TEXT)


@dp.message_handler(commands=["add_buy"])
async def cmd_add_buy(message: types.Message):
    args = message.get_args()
    if not args or "|" not in args:
        await message.answer("Использование: /add_buy Игра|Цена|Примечание")
        return
    try:
        game, price, notes = [p.strip() for p in args.split("|", 2)]
        price_f = float(to_decimal(price))
    except Exception:
        await message.answer("Неверный формат цены.")
        return

    rows = read_rows()
    nid = next_id(rows)
    min_sale = calc_min_sale(price_f, target_net=1.0)
    new = {
        "id": str(nid),
        "source_text": f"manual:{game}|{price_f}|{notes}",
        "game": game,
        "account_desc": "",
        "buy_price": f"{price_f:.2f}",
        "buy_date": datetime.utcnow().isoformat(),
        "status": "in_stock",
        "min_sale_for_target": f"{min_sale:.2f}",
        "notes": notes,
        "sell_price": "",
        "sell_date": "",
        "net_profit": ""
    }
    rows.append(new)
    write_rows(rows)
    await message.answer(f"Добавлен лот ID {nid} — {game} за {price_f}$\nМин. цена для $1: {min_sale}$")

@dp.message_handler(commands=["list"])
async def cmd_list(message: types.Message):
    rows = read_rows()
    in_stock = [r for r in rows if r["status"] in ("in_stock", "listed")]
    if not in_stock:
        await message.answer("Нет лотов в наличии.")
        return
    text = "Лоты в наличии:\n"
    for r in in_stock:
        text += f"ID {r['id']}: {r['game']} — buy {r['buy_price']}$ — status: {r['status']} — notes: {r['notes']}\n"
    await message.answer(text)

@dp.message_handler(content_types=types.ContentType.TEXT)
async def handle_text(message: types.Message):
    text = (message.text or "").strip()
    parsed = parse_notification(text)
    if not parsed["buy_price"]:
        return

    rows = read_rows()
    nid = next_id(rows)
    min_sale = calc_min_sale(parsed["buy_price"], target_net=1.0)
    new = {
        "id": str(nid),
        "source_text": parsed["source_text"],
        "game": parsed["game"],
        "account_desc": parsed["account_desc"],
        "buy_price": f"{float(parsed['buy_price']):.2f}",
        "buy_date": datetime.utcnow().isoformat(),
        "status": "in_stock",
        "min_sale_for_target": f"{min_sale:.2f}",
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
        InlineKeyboardButton("Профит $1", callback_data=f"profit:{nid}:1"),
        InlineKeyboardButton("Профит $2", callback_data=f"profit:{nid}:2"),
    )
    kb.add(InlineKeyboardButton("Custom", callback_data=f"profit:{nid}:custom"))
    kb.add(InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
           InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}"))

    draft_text = (
        f"🆕 Новый лот (ID {nid})\n"
        f"Игра: {parsed['game']}\n"
        f"Описание: {parsed['account_desc']}\n"
        f"Куплено за: {float(parsed['buy_price']):.2f}$\n"
        f"Мин. цена для $1: {min_sale}$\n\n"
        "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
    )
    await message.answer(draft_text, reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("profit:"))
async def cb_profit(call: types.CallbackQuery):
    _, nid, profit = call.data.split(":", 2)
    rows = read_rows()
    row = next((r for r in rows if r["id"] == nid), None)
    if not row:
        await call.answer("Лот не найден.", show_alert=True)
        return

    if profit == "custom":
        await call.message.answer(f"Введите /generate_listing {nid} <целевой_профит>\nПример: /generate_listing {nid} 1.5")
        await call.answer()
        return

    try:
        target = float(profit)
    except:
        target = 1.0

    min_sale = calc_min_sale(float(row["buy_price"]), target_net=target)
    listing_text = (
        f"ID {nid} — {row['game']}\n"
        f"Куплено: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        "Шаблон объявления:\n"
        f"Заголовок: Аккаунт {row['game']} | Цена {min_sale}$\n"
        f"Описание:\n- Куплен за: {row['buy_price']}$\n- Особенности: {row['account_desc'] or row['notes']}\n"
    )
    await call.message.answer(listing_text)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("posted:"))
async def cb_posted(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)
    rows = read_rows()
    row = next((r for r in rows if r["id"] == nid), None)
    if not row:
        await call.answer("Лот не найден.", show_alert=True)
        return
    row["status"] = "listed"
    write_rows(rows)
    await call.message.answer(f"Лот {nid} помечен как опубликованный.")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("sold_direct:"))
async def cb_sold_direct(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)
    await call.message.answer(f"Чтобы отметить лот {nid} как проданный, отправь: /sold {nid}|<цена_продажи>\nПример: /sold {nid}|10")
    await call.answer()

@dp.message_handler(commands=["generate_listing"])
async def cmd_generate_listing(message: types.Message):
    args = message.get_args().split()
    if len(args) < 2:
        await message.answer("Использование: /generate_listing <id> <target_net>")
        return
    nid, target = args[0], args[1]
    try:
        target_f = float(target)
    except:
        await message.answer("Неверный целевой профит.")
        return
    rows = read_rows()
    row = next((r for r in rows if r["id"] == nid), None)
    if not row:
        await message.answer("ID не найден.")
        return
    min_sale = calc_min_sale(float(row["buy_price"]), target_net=target_f)
    txt = (
        f"ID {nid} — {row['game']}\n"
        f"Куплено за: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target_f}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        f"Шаблон объявления:\nЗаголовок: Аккаунт {row['game']} | Цена {min_sale}$\n"
        f"Описание: - Куплен за {row['buy_price']}$; {row['account_desc'] or row['notes']}\n"
    )
    await message.answer(txt)

@dp.message_handler(commands=["mark_published"])
async def cmd_mark_published(message: types.Message):
    nid = message.get_args().strip()
    if not nid:
        await message.answer("Использование: /mark_published <id>")
        return
    rows = read_rows()
    row = next((r for r in rows if r["id"] == nid), None)
    if not row:
        await message.answer("ID не найден.")
        return
    row["status"] = "listed"
    write_rows(rows)
    await message.answer(f"ID {nid} помечен как опубликованный.")

@dp.message_handler(commands=["sold"])
async def cmd_sold(message: types.Message):
    args = message.get_args()
    if not args or "|" not in args:
        await message.answer("Использование: /sold <id>|<price>\nПример: /sold 3|10")
        return
    nid, price = [p.strip() for p in args.split("|", 1)]
    try:
        price_f = float(to_decimal(price))
    except:
        await message.answer("Неверная цена.")
        return
    rows = read_rows()
    row = next((r for r in rows if r["id"] == nid), None)
    if not row:
        await message.answer("ID не найден.")
        return
    net = calc_net_from_sale(price_f, float(row["buy_price"]))
    row["status"] = "sold"
    row["sell_price"] = f"{price_f:.2f}"
    row["sell_date"] = datetime.utcnow().isoformat()
    row["net_profit"] = f"{net:.2f}"
    write_rows(rows)
    await message.answer(f"ID {nid} отмечен как проданный. Чистая прибыль: {net:.2f}$")

@dp.message_handler(commands=["stats"])
async def cmd_stats(message: types.Message):
    rows = read_rows()
    bought_count = len(rows)
    sold = [r for r in rows if r["status"] == "sold" and r["net_profit"]]
    total_net = sum(float(r["net_profit"]) for r in sold) if sold else 0.0
    total_spent = sum(float(r["buy_price"]) for r in rows)
    sold_count = len(sold)
    text = (
        f"Статистика:\n"
        f"Всего позиций: {bought_count}\n"
        f"Продано: {sold_count}\n"
        f"Потрачено всего: {total_spent:.2f}$\n"
        f"Суммарная чистая прибыль: {total_net:.2f}$\n"
    )
    await message.answer(text)

@dp.message_handler(commands=["monthly"])
async def cmd_monthly(message: types.Message):
    arg = message.get_args().strip()
    if not arg:
        await message.answer("Использование: /monthly YYYY-MM")
        return
    try:
        year, month = arg.split("-", 1)
        year = int(year); month = int(month)
    except:
        await message.answer("Неверный формат. Пример: /monthly 2025-10")
        return
    rows = read_rows()
    bought = [r for r in rows if r["buy_date"] and r["buy_date"].startswith(f"{year:04d}-{month:02d}")]
    sold = [r for r in rows if r["sell_date"] and r["sell_date"].startswith(f"{year:04d}-{month:02d}")]
    total_spent = sum(float(r["buy_price"]) for r in bought)
    total_net = sum(float(r["net_profit"]) for r in sold if r["net_profit"])
    res = (
        f"Месяц {year}-{month:02d}:\n"
        f"Куплено: {len(bought)} шт., потрачено: {total_spent:.2f}$\n"
        f"Продано: {len(sold)} шт., чистая прибыль: {total_net:.2f}$\n"
    )
    await message.answer(res)

@dp.message_handler(commands=["export"])
async def cmd_export(message: types.Message):
    if not os.path.exists(DATA_CSV):
        await message.answer("Файл не найден.")
        return
    await message.answer_document(open(DATA_CSV, "rb"))

# ВАЖНО: никаких executor.start_polling здесь нет!
# dp и bot импортирует app.py (Flask) и гоняет webhook.




