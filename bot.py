# bot.py
import os, csv, re
from datetime import datetime
from decimal import Decimal, getcontext

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ====== общие настройки ======
getcontext().prec = 28

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN env var is not set")

bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

BASE_DIR = os.path.dirname(__file__)
DATA_CSV = os.path.join(BASE_DIR, "inventory.csv")

COMMISSION = Decimal("0.06")    # комиссия площадки 6%
BRAND = "Stirka"

FIELDNAMES = [
    "id", "source_text", "game", "account_desc",
    "buy_price", "status", "min_sale_for_1", "notes",
    "sell_price", "sell_date", "net_profit"
]

def _ensure_csv():
    if not os.path.exists(DATA_CSV):
        with open(DATA_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()

def read_rows():
    _ensure_csv()
    with open(DATA_CSV, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def write_rows(rows):
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(rows)

def next_id(rows):
    return 1 if not rows else max(int(r["id"]) for r in rows) + 1

def calc_min_sale_for_profit(buy_price: Decimal, target_profit: Decimal) -> Decimal:
    if buy_price is None:
        buy_price = Decimal("0")
    price = (buy_price + target_profit) / (Decimal("1") - COMMISSION)
    return price.quantize(Decimal("0.01"))

# ====== парсинг исходного текста из автобая ======
RE_BUY = re.compile(r"на\s?сумму\s?(\d+[.,]?\d*)\$|куплен[оа]\s?за:\s?(\d+[.,]?\d*)\$", re.I)

def parse_game_title(text: str) -> str:
    if not text:
        return "Неизвестно"
    m = re.search(r"«([^»]+)»|\"([^\"]+)\"", text)
    if m:
        return (m.group(1) or m.group(2)).strip()
    m = re.search(r"Игра:?\s*([^\n\r]+)", text, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"^([^\d\$\n\r]+)", text)
    if m:
        return m.group(1).strip()
    return "Неизвестно"

def parse_source(text: str):
    game = parse_game_title(text)
    buy_price = None
    m = RE_BUY.search(text)
    if m:
        raw = (m.group(1) or m.group(2) or "").replace(",", ".")
        try:
            buy_price = Decimal(raw)
        except Exception:
            buy_price = None
    return {
        "game": game,
        "account_desc": "",
        "buy_price": buy_price,
        "source_text": text
    }

def build_lot_text(lot_id: int, game_title: str,
                   buy_price: Decimal, min_price_1usd: Decimal) -> str:
    game_title = " ".join(str(game_title).split())
    return (
        f"<b>Лот ID {lot_id}</b>\n"
        f"Игра: {game_title}\n"
        f"Куплено за: {buy_price:.2f}$\n"
        f"Мин. цена для $1: {min_price_1usd:.2f}$\n"
        f"Описание для лота: {BRAND} | \"{game_title}\" |\n"
        f"Выбери целевой профит:"
    )

def make_profit_kb(lot_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(row_width=3)
    kb.row(
        InlineKeyboardButton("Профит $0.5", callback_data=f"profit:{lot_id}:0.5"),
        InlineKeyboardButton("Профит $1",   callback_data=f"profit:{lot_id}:1"),
        InlineKeyboardButton("Профит $2",   callback_data=f"profit:{lot_id}:2"),
    )
    return kb

HELP_TEXT = (
    "Привет! Я бот для подготовки листингов.\n\n"
    "Просто перешли мне уведомление от автобая — я спаршу цену и покажу мин. цену для заданного профита.\n"
    "Кнопки под сообщением помогают быстро посчитать цену под $0.5 / $1 / $2 профита."
)

@dp.message_handler(commands=["start", "help"])
async def cmd_start(m: types.Message):
    await m.answer(HELP_TEXT)

@dp.message_handler(content_types=["text"])
async def handle_forwarded(m: types.Message):
    # игнорируем команды, чтобы не мешать
    if m.text.startswith("/"):
        return

    src_text = m.caption or m.text or ""
    if not src_text.strip():
        await m.answer("Текст пустой — ничего парсить.")
        return

    data = parse_source(src_text)
    buy_price = data["buy_price"] or Decimal("0")
    min_1 = calc_min_sale_for_profit(buy_price, Decimal("1.0"))

    rows = read_rows()
    lot_id = next_id(rows)
    rows.append({
        "id": str(lot_id),
        "source_text": data["source_text"],
        "game": data["game"],
        "account_desc": data["account_desc"],
        "buy_price": str(buy_price),
        "status": "new",
        "min_sale_for_1": str(min_1),
        "notes": "",
        "sell_price": "",
        "sell_date": "",
        "net_profit": ""
    })
    write_rows(rows)

    reply = build_lot_text(lot_id, data["game"], buy_price, min_1)
    await m.answer(reply, reply_markup=make_profit_kb(lot_id))

@dp.callback_query_handler(lambda c: c.data.startswith("profit:"))
async def on_profit_click(c: types.CallbackQuery):
    # формат: profit:<lot_id>:<target_profit>
    try:
        _, lot_id_s, target_s = c.data.split(":")
        lot_id = int(lot_id_s)
        target = Decimal(target_s)
    except Exception:
        await c.answer("Ошибка данных", show_alert=True)
        return

    rows = read_rows()
    row = next((r for r in rows if int(r["id"]) == lot_id), None)
    if not row:
        await c.answer("Лот не найден", show_alert=True)
        return

    buy_price = Decimal(row.get("buy_price") or "0")
    sale_price = calc_min_sale_for_profit(buy_price, target)

    txt = (
        f"Лот ID {lot_id}\n"
        f"Куплено за: {buy_price:.2f}$\n"
        f"Целевой профит: {target:.2f}$\n"
        f"<b>Выставляй по: {sale_price:.2f}$</b>"
    )
    await c.message.answer(txt)
    await c.answer()
