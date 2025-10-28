import os
import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
import asyncio
import random, string
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
FILE_LOCK = asyncio.Lock()
# Память простых состояний диалога и последних описаний по игре
USER_STATE = {}          # user_id -> {...}
GAME_DEFAULT_DESC = {}   # game_title -> last_description

# ==== ТОКЕН ТОЛЬКО ЗДЕСЬ: API_TOKEN ====
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is not set")



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
    "/reset_stats — очистить базу (нужно подтверждение)\n"
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
        BotCommand("reset_stats", "очистить базу (подтверждение)"),
    ]
    await bot.set_my_commands(commands)
# ======================================


# ========== НАСТРОЙКИ ==========
DATA_CSV = os.path.join(os.path.dirname(__file__), "inventory.csv")
DESC_CSV = os.path.join(os.path.dirname(__file__), "descriptions.csv")
COMMISSION = 0.06           # 6%
PRICE_ENDING = "tenth_9"    # психологическое окончание
# ==============================

if not API_TOKEN:
    raise RuntimeError("Environment variable BOT_TOKEN is not set")

bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# Создаём CSV если его нет
FIELDNAMES = [
    "id", "alias", "source_text", "game", "account_desc", "buy_price",
    "buy_date", "status", "min_sale_for_target", "notes",
    "sell_price", "sell_date", "net_profit"
]
# ====== Память описаний по игре (descriptions.csv) ======
DESC_CSV = os.path.join(os.path.dirname(__file__), "descriptions.csv")
DESC_FIELDS = ["game_key", "description", "updated_at"]

if not os.path.exists(DESC_CSV):
    with open(DESC_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DESC_FIELDS)
        writer.writeheader()

# Кто сейчас «вводит описание»: key = chat_id -> контекст ввода
WAITING_DESC = {}  # { chat_id: {"nid": str, "target": float, "min_sale": float, "game": str} }

def _game_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())

def get_description_for_game(game: str):
    key = _game_key(game)
    try:
        with open(DESC_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["game_key"] == key:
                    return row["description"]
    except FileNotFoundError:
        pass
    return None

def save_description_for_game(game: str, description: str):
    key = _game_key(game)
    rows = []
    if os.path.exists(DESC_CSV):
        with open(DESC_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

    updated = False
    for r in rows:
        if r["game_key"] == key:
            r["description"] = description
            r["updated_at"] = datetime.utcnow().isoformat()
            updated = True
            break
    if not updated:
        rows.append({
            "game_key": key,
            "description": description,
            "updated_at": datetime.utcnow().isoformat()
        })

    with open(DESC_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DESC_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

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


def reset_csv():
    """Перезаписывает inventory.csv, оставляя только заголовок."""
    with open(DATA_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()


def next_id(rows):
    if not rows:
        return 1
    return max(int(r["id"]) for r in rows) + 1

def generate_unique_alias(existing: set, length: int = 3) -> str:
    """Возвращает уникальные 3 буквы [a-z], которых нет в existing."""
    while True:
        alias = ''.join(random.choice(string.ascii_lowercase) for _ in range(length))
        if alias not in existing:
            return alias

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

def apply_psychological_ending(amount, ending=PRICE_ENDING):
    """
    Режимы:
      ending == "tenth_9": округлить вверх к следующей 0.1, затем -0.01
        3.66 -> 3.69, 6.30 -> 6.39, 2.00 -> 2.09, 3.69 -> 3.69
      ending in (".99", ".49"): округлить вверх к ближайшему X.99 или X.49
    """
    try:
        d = Decimal(str(amount)).quantize(Decimal("0.01"))
    except Exception:
        return float(amount)

    if ending == "tenth_9":
        cents = int(d * 100)                       # 3.66 -> 366
        next_tenth_cents = ((cents + 9) // 10) * 10
        candidate_cents = next_tenth_cents - 1     # 370 -> 369 => 3.69
        return float(Decimal(candidate_cents) / Decimal(100))

    if ending in (".99", ".49"):
        end = Decimal(ending)
        int_part = int(d)  # целая часть вниз
        candidate = Decimal(int_part) + end
        if candidate < d:
            candidate = Decimal(int_part + 1) + end
        return float(candidate.quantize(Decimal("0.01")))

    return float(d)

    end = Decimal(ending)
    int_part = int(d)  # целая часть вниз
    candidate = Decimal(int_part) + end
    if candidate < d:
        candidate = Decimal(int_part + 1) + end
    return float(candidate.quantize(Decimal("0.01")))

def set_description_for_game(game: str, desc: str) -> None:
    if desc:
        GAME_DEFAULT_DESC[game] = desc.strip()

def compose_listing(row, nid, target, min_sale, desc: str) -> str:
    alias = (row.get("alias") or "").lower()
    prefix = f"{alias} | " if alias else ""
    return (
        f"ID {nid} — {row['game']}\n"
        f"Куплено: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        "Описание для лота:\n"
        f"{prefix}{desc.strip()}"
    )

# ---- Парсер уведомления ----
RE_LINK = re.compile(r'По вашей ссылке\s*["“](?P<link>[^"”]+)["”]', re.IGNORECASE)
RE_BUYPRICE = re.compile(r'за\s*(?:\$\s*)?([\d\.,]+)(?:\s*\$)?', re.IGNORECASE)
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
# --- Авто-описание по игре ---
def auto_desc_for_game(game: str, account_desc: str = "") -> str:
    t = f"{game} {account_desc}".lower()

    # CS2
    if ("cs2" in t) or ("counter-strike 2" in t) or ("counter strike 2" in t):
        return "CS2 PRIME | ПОЛНЫЙ ДОСТУП | МОЖНО СМЕНИТЬ ПОЧТУ"

    # DBD
    if ("dead by daylight" in t) or ("dbd" in t):
        return "DEAD BY DAYLIGHT | HOURS"

    # GTA: сохраняем оригинальное написание из уведомления
    if ("gta 5" in t) or ("gta v" in t) or ("grand theft auto v" in t):
        name = (game or "GTA 5").strip()
        return f"{name} | 0 HOURS | СОШИАЛ МОЖНО ПРИВЯЗАТЬ САМОМУ"

    # По умолчанию — капсом
    return (game or "").upper()
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

    async with FILE_LOCK:
        rows = read_rows()
        nid = next_id(rows)
        existing_aliases = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
        alias = generate_unique_alias(existing_aliases)
        min_sale = apply_psychological_ending(calc_min_sale(price_f, target_net=1.0))
        new = {
            "id": str(nid),
            "alias": alias,
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
    listed = [r for r in rows if (r.get("status") or "") == "listed"]

    if not listed:
        await message.answer("Пока нет опубликованных лотов.")
        return

    kb = InlineKeyboardMarkup(row_width=1)
    for r in listed:
        alias = (r.get("alias") or "").lower()
        title = f'ID {r["id"]} — {(alias + " | ") if alias else ""}{r["game"]}'
        kb.add(InlineKeyboardButton(title, callback_data=f"open:{r['id']}"))

    await message.answer("Выберите лот:", reply_markup=kb)
@dp.message_handler(lambda m: (m.text is not None) and m.chat.id in WAITING_DESC, content_types=types.ContentType.TEXT)
async def receive_description(message: types.Message):
    ctx = WAITING_DESC.pop(message.chat.id, None)
    if not ctx:
        return

    desc = message.text.strip()
    nid = ctx["nid"]
    target = ctx["target"]
    min_sale = ctx["min_sale"]
    game = ctx["game"]

    # Сохраняем «память» описания для этой игры
    async with FILE_LOCK:
        save_description_for_game(game, desc)

    # Достаём ряд для сборки ответа
    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        await message.answer("Лот не найден, попробуйте ещё раз.")
        return

    listing_text = compose_listing(row, nid, target, min_sale, desc)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"editdesc:{nid}"))
    kb.add(
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{(row.get('alias') or '').lower()}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
)
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))
    await message.answer(listing_text, reply_markup=kb)
# 3.1. Пользователь вводит описание для кастомного профита
@dp.message_handler(lambda m: USER_STATE.get(m.from_user.id, {}).get("mode") == "custom_desc")
async def wait_custom_description(message: types.Message):
    st = USER_STATE.get(message.from_user.id)
    if not st:
        return
    desc = (message.text or "").strip()
    if not desc:
        await message.answer("Опишите лот текстом, пожалуйста.")
        return

    # сохраним описание временно и попросим ввести профит
    USER_STATE[message.from_user.id] = {"mode": "custom_profit", "nid": st["nid"], "desc": desc}
    await message.answer("Ок! Теперь введите желаемый профит числом (например: 1.2).")


# 3.2. Пользователь вводит сам профит (число)
@dp.message_handler(lambda m: USER_STATE.get(m.from_user.id, {}).get("mode") == "custom_profit")
async def wait_custom_profit(message: types.Message):
    st = USER_STATE.get(message.from_user.id)
    if not st:
        return

    text = (message.text or "").strip()
    try:
        target = float(to_decimal(text))
    except Exception:
        await message.answer("Не понял. Введите профит числом, например 1.5")
        return

    nid = st["nid"]
    desc = st["desc"]

    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        USER_STATE.pop(message.from_user.id, None)
        await message.answer("Лот не найден.")
        return

    # посчитаем минимальную цену и сразу сохраним её в CSV
    min_sale = apply_psychological_ending(
        calc_min_sale(float(row["buy_price"]), target_net=target)
    )
    row["min_sale_for_target"] = f"{min_sale:.2f}"
    write_rows(rows)


    # запомним описание для этой игры на будущее
    GAME_DEFAULT_DESC[row["game"]] = desc

    # соберём ответ и кнопки
    listing_text = compose_listing(row, nid, target, min_sale, desc)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
    kb.add(
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{(row.get('alias') or '').lower()}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
)
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))
    await message.answer(listing_text, reply_markup=kb)
    USER_STATE.pop(message.from_user.id, None)
# Пользователь ввёл описание после выбора фиксированного профита (0.5/1/2)
@dp.message_handler(lambda m: USER_STATE.get(m.from_user.id, {}).get("mode") == "fixed_desc")
async def wait_fixed_desc(message: types.Message):
    st = USER_STATE.get(message.from_user.id)
    if not st:
        return

    nid = st["nid"]
    target = float(st["target"])
    desc = (message.text or "").strip()
    if not desc:
        await message.answer("Опишите лот текстом, пожалуйста.")
        return

    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        USER_STATE.pop(message.from_user.id, None)
        await message.answer("Лот не найден.")
        return

    min_sale = apply_psychological_ending(
         calc_min_sale(float(row["buy_price"]), target_net=target)
    )
    row["min_sale_for_target"] = f"{min_sale:.2f}"
    write_rows(rows)


    # Запоминаем описание и в памяти, и в CSV
    GAME_DEFAULT_DESC[row["game"]] = desc
    save_description_for_game(row["game"], desc)

    listing_text = compose_listing(row, nid, target, min_sale, desc)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
    kb.add(
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{(row.get('alias') or '').lower()}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
)
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))
    await message.answer(listing_text, reply_markup=kb)
    USER_STATE.pop(message.from_user.id, None)
@dp.message_handler(
    lambda m: USER_STATE.get(m.from_user.id, {}).get("mode") == "await_profit_value",
    content_types=types.ContentType.TEXT,
)
async def handle_custom_profit_value(message: types.Message):
    st = USER_STATE.get(message.from_user.id)
    if not st:
        return

    nid = st["nid"]
    text = (message.text or "").strip()
    try:
        target = float(to_decimal(text))
    except Exception:
        await message.answer("Не понял число. Введите, например: 1.5")
        return

    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        USER_STATE.pop(message.from_user.id, None)
        await message.answer("ID не найден. Начните заново.")
        return

    min_sale = apply_psychological_ending(
        calc_min_sale(float(row["buy_price"]), target_net=target)
    )
    row["min_sale_for_target"] = f"{min_sale:.2f}"
    write_rows(rows)

    # Авто-описание по игре
    desc = auto_desc_for_game(row["game"], row.get("account_desc", ""))
    save_description_for_game(row["game"], desc)

    listing_text = compose_listing(row, nid, target, min_sale, desc)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
    kb.add(
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{(row.get('alias') or '').lower()}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
)
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))

    USER_STATE.pop(message.from_user.id, None)
    await message.answer(listing_text, reply_markup=kb)
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("open:"))
async def cb_open(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)
    rows = read_rows()
    row = next((r for r in rows if r.get("id") == str(nid)), None)
    if not row:
        await call.answer("Лот не найден.", show_alert=True)
        return

    # безошибочно читаем числа
    try:
        min_sale = float(row.get("min_sale_for_target") or 0)
    except:
        min_sale = 0.0
    try:
        buy_price = float(row.get("buy_price") or 0)
    except:
        buy_price = 0.0

    # оценим target для карточки (если min_sale/price есть)
    target = calc_net_from_sale(min_sale, buy_price) if (min_sale and buy_price) else 1.0

    # подтянем шаблон описания (или автоген)
    desc = get_description_for_game(row["game"]) or auto_desc_for_game(
        row["game"], row.get("account_desc", "")
    )

    text = compose_listing(row, row["id"], target, min_sale, desc)
    alias = (row.get("alias") or "").lower()

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{row['id']}:{target}"))
    kb.add(
        InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{row['id']}:{alias}"),
        InlineKeyboardButton("Отметить проданным",      callback_data=f"sold_direct:{row['id']}")
    )
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{row['id']}"))

    await call.message.answer(text, reply_markup=kb)
    await call.answer()
@dp.message_handler(
    lambda m: (
        m.text is not None
        and not m.text.startswith('/')
        and m.chat.id not in WAITING_DESC
        and USER_STATE.get(m.from_user.id, {}).get("mode") is None  # <— важно
    ),
    content_types=types.ContentType.TEXT,
)
async def handle_text(message: types.Message):
    text = message.text.strip()
    if text.startswith("/"):
        return

    parsed = parse_notification(text)
    if not parsed["buy_price"]:
        return

    async with FILE_LOCK:
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
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{alias}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
    )

    draft_text = (
        f"🆕 Новый лот (ID {nid})\n"
        f"Игра: {parsed['game']}\n"
        f"Описание: {parsed['account_desc']}\n"
        f"Куплено за: {float(parsed['buy_price']):.2f}$\n\n"
        "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
    )
    await message.answer(draft_text, reply_markup=kb)


@dp.message_handler(commands=["generate_listing"])
async def cmd_generate_listing(message: types.Message):
    args = message.get_args().split()
    if len(args) < 2:
        await message.answer("Использование: /generate_listing <id> <target_net>")
        return

    nid, target = args[0], args[1]
    try:
        target_f = float(target)
    except Exception:
        await message.answer("Неверный целевой профит.")
        return

    rows = read_rows()
    row = next((r for r in rows if r["id"] == nid), None)
    if not row:
        await message.answer("ID не найден.")
        return

    min_sale = apply_psychological_ending(
        calc_min_sale(float(row["buy_price"]), target_net=target_f)
    )
    desc_default = auto_desc_for_game(row["game"], row.get("account_desc", ""))
    txt = compose_listing(row, nid, target_f, min_sale, desc_default)
    await message.answer(txt)
   

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("profit:"))
async def cb_profit(call: types.CallbackQuery):
    _, nid, profit = call.data.split(":", 2)

    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        await call.answer("Лот не найден.", show_alert=True)
        return

    # Если пользователь выбрал Custom — просто просим ввести число профита
    if profit == "custom":
        USER_STATE[call.from_user.id] = {"mode": "await_profit_value", "nid": nid}
        await call.message.answer("Введите желаемый профит числом, например: 1.5")
        await call.answer()
        return

    # Фиксированные варианты (0.5 / 1 / 2)
    try:
        target = float(profit)
    except Exception:
        target = 1.0

    min_sale = apply_psychological_ending(
        calc_min_sale(float(row["buy_price"]), target_net=target)
    )
    row["min_sale_for_target"] = f"{min_sale:.2f}"
    write_rows(rows)

    # Текст описания теперь всегда = название игры (alias подставится в compose_listing)
    desc = auto_desc_for_game(row["game"], row.get("account_desc", ""))
    save_description_for_game(row["game"], desc)
    listing_text = compose_listing(row, nid, target, min_sale, desc)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
    kb.add(
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{(row.get('alias') or '').lower()}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
)
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))

    await call.message.answer(listing_text, reply_markup=kb)
    await call.answer()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("posted:"))
async def cb_posted(call: types.CallbackQuery):
    parts = call.data.split(":")
    # варианты: ["posted", nid] (старые кнопки) или ["posted", nid, alias] (новые)
    if len(parts) == 3:
        _, nid, alias = parts
        alias = (alias or "").lower().strip()
    else:
        _, nid = parts
        alias = None

    nid = str(nid).strip()

    async with FILE_LOCK:
        rows = read_rows()

        row = None
        if alias:
            # Жёстко по паре id+alias
            for r in rows:
                if r.get("id") == nid and (r.get("alias") or "").lower() == alias:
                    row = r
                    break
        else:
            # Фолбэк для старых сообщений без alias
            row = next((r for r in rows if r.get("id") == nid), None)

        if not row:
            await call.answer("Лот не найден.", show_alert=True)
            return

        row["status"] = "listed"
        write_rows(rows)

    await call.message.answer(f"✅ Лот {nid} помечен как опубликованный (alias: {(row.get('alias') or '').lower()}).")
    await call.answer()

    
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("editdesc:"))
async def cb_editdesc(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)
    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        await call.answer("Лот не найден.", show_alert=True)
        return

    saved = get_description_for_game(row["game"])
    hint = f'\n(текущий шаблон: {saved})' if saved else ""
    USER_STATE[call.from_user.id] = {"mode": "edit_desc", "nid": nid}
    await call.message.answer(f'Введите новый текст для описания лота для «{row["game"]}».{hint}')
    await call.answer()

        
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("wipe:"))
async def cb_wipe(call: types.CallbackQuery):
    if call.data == "wipe:yes":
        async with FILE_LOCK:
            reset_csv()
        await call.message.answer("✅ Готово. База очищена (inventory.csv перезаписан заголовком).")
    else:
        await call.message.answer("Отменено.")
    await call.answer()

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("sold_direct:"))
async def cb_sold_direct(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)
    await call.message.answer(f"Чтобы отметить лот {nid} как проданный, отправь: /sold {nid}|<цена_продажи>\nПример: /sold {nid}|10")
    await call.answer()
    


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("restored:"))
async def cb_restored(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)

    async with FILE_LOCK:
        rows = read_rows()
        row = next((r for r in rows if r["id"] == str(nid)), None)
        if not row:
            await call.answer("Лот не найден.", show_alert=True)
            return

        if row["status"] == "sold":
            await call.answer("Этот лот уже отмечен как проданный.", show_alert=True)
            return
        if row["status"] == "restored":
            await call.answer("Этот лот уже отмечен как восстановленный.", show_alert=True)
            return

        loss = float(row["buy_price"])
        row["status"] = "restored"
        row["sell_price"] = ""
        row["sell_date"] = datetime.utcnow().isoformat()
        # фиксируем убыток отрицательным нетто, чтобы можно было считать итоги
        row["net_profit"] = f"{-loss:.2f}"
        write_rows(rows)

    await call.message.answer(f"Лот {nid} помечен как восстановленный.\nПотеря: {loss:.2f}$")
    await call.answer()
@dp.callback_query_handler(lambda c: c.data and c.data.startswith("edit_desc:"))
async def cb_edit_desc(call: types.CallbackQuery):
    _, nid, target = call.data.split(":", 2)
    USER_STATE[call.from_user.id] = {"mode": "edit_desc", "nid": nid, "target": float(target)}
    await call.message.answer("Введите новый текст описания лота.")
    await call.answer()


# Пришёл новый текст — пересобираем листинг с тем же профитом
@dp.message_handler(lambda m: USER_STATE.get(m.from_user.id, {}).get("mode") == "edit_desc")
async def handle_edit_desc(message: types.Message):
    st = USER_STATE.get(message.from_user.id)
    if not st:
        return
    nid = st["nid"]
    target = st["target"]
    desc = (message.text or "").strip()
    if not desc:
        await message.answer("Опишите лот текстом, пожалуйста.")
        return

    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        USER_STATE.pop(message.from_user.id, None)
        await message.answer("Лот не найден.")
        return

    min_sale = apply_psychological_ending(
    calc_min_sale(float(row["buy_price"]), target_net=target)
    )
    GAME_DEFAULT_DESC[row["game"]] = desc

    listing_text = compose_listing(row, nid, target, min_sale, desc)

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
    kb.add(
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{(row.get('alias') or '').lower()}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
)
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))
    
    await message.answer(listing_text, reply_markup=kb)
    USER_STATE.pop(message.from_user.id, None)

@dp.message_handler(commands=["mark_published"])
async def cmd_mark_published(message: types.Message):
    nid = message.get_args().strip()
    if not nid:
        await message.answer("Использование: /mark_published <id>")
        return

    async with FILE_LOCK:
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

    async with FILE_LOCK:
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
    total_spent = sum(float(r["buy_price"]) for r in rows if r.get("buy_price"))

    sold = [r for r in rows if r.get("status") == "sold" and r.get("net_profit")]
    sold_count = len(sold)
    total_profit = sum(float(r["net_profit"]) for r in sold) if sold else 0.0  # ← Прибыль с проданных

    restored = [r for r in rows if r.get("status") == "restored"]
    restored_count = len(restored)
    total_losses = sum(float(r["buy_price"]) for r in restored)  # Потери = цена покупки восстановленных

    # Итоговый результат = прибыль с продаж - потери от восстановленных
    real_result = total_profit - total_losses

    text = (
        "Статистика:\n"
        f"Всего позиций: {bought_count}\n"
        f"Продано: {sold_count}\n"
        f"Восстановлено: {restored_count}\n"
        f"Потрачено всего: {total_spent:.2f}$\n"
        f"Прибыль (проданное): {total_profit:.2f}$\n"          # ← ВОТ ЭТА СТРОКА
        f"Потери (восстановленные): {total_losses:.2f}$\n"
        f"ИТОГ (прибыль - потери): {real_result:.2f}$\n"
    )
    await message.answer(text)
@dp.message_handler(commands=["reset_stats"])
async def cmd_reset_stats(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("Да, удалить ВСЁ", callback_data="wipe:yes"),
        InlineKeyboardButton("Отмена", callback_data="wipe:no"),
    )
    await message.answer(
        "⚠️ Подтвердите очистку базы.\n"
        "Будут удалены ВСЕ записи (инвентарь и статистика) без возможности восстановления.",
        reply_markup=kb
    )

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

    bought = [r for r in rows if r.get("buy_date") and r["buy_date"].startswith(f"{year:04d}-{month:02d}")]
    sold = [r for r in rows if r.get("sell_date") and r["sell_date"].startswith(f"{year:04d}-{month:02d}") and r["status"] == "sold"]
    restored = [r for r in rows if r.get("sell_date") and r["sell_date"].startswith(f"{year:04d}-{month:02d}") and r["status"] == "restored"]

    total_spent = sum(float(r["buy_price"]) for r in bought if r.get("buy_price"))
    total_net_sales = sum(float(r["net_profit"]) for r in sold if r.get("net_profit"))
    total_losses = sum(float(r["buy_price"]) for r in restored if r.get("buy_price"))

    real_result = total_net_sales - total_losses

    res = (
        f"Месяц {year}-{month:02d}:\n"
        f"Куплено: {len(bought)} шт., потрачено: {total_spent:.2f}$\n"
        f"Продано: {len(sold)} шт., чистая прибыль: {total_net_sales:.2f}$\n"
        f"Восстановлено: {len(restored)} шт., потери: {total_losses:.2f}$\n"
        f"ИТОГ (прибыль - потери): {real_result:.2f}$\n"
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
# === helper: создать лот и отправить сообщение "Новый лот" с кнопками ===
async def create_lot_and_prompt(parsed: dict, chat_id: int):
    """
    parsed = {"game": str, "account_desc": str, "buy_price": float, "source_text": str}
    chat_id = куда отправлять сообщение
    """
    async with FILE_LOCK:
        rows = read_rows()
        existing_aliases = {(r.get("alias") or "").lower() for r in rows if r.get("alias")}
        alias = generate_unique_alias(existing_aliases)
        nid = next_id(rows)
        new = {
            "id": str(nid),
            "alias": alias,
            "source_text": parsed.get("source_text", ""),
            "game": parsed.get("game", ""),
            "account_desc": parsed.get("account_desc", ""),
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
    InlineKeyboardButton("Отметить опубликованным",
                         callback_data=f"posted:{nid}:{alias}"),
    InlineKeyboardButton("Отметить проданным", callback_data=f"sold_direct:{nid}")
    )

    draft_text = (
        f"🆕 Новый лот (ID {nid})\n"
        f"Игра: {parsed.get('game','')}\n"
        f"Описание: {parsed.get('account_desc','')}\n"
        f"Куплено за: {float(parsed['buy_price']):.2f}$\n\n"
        "Выбери целевой профит, чтобы получить мин. цену продажи и шаблон."
    )
    await bot.send_message(chat_id, draft_text, reply_markup=kb)




























