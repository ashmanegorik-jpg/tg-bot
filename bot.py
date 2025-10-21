import os
import csv
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
import asyncio

FILE_LOCK = asyncio.Lock()
# Память простых состояний диалога и последних описаний по игре
USER_STATE = {}          # user_id -> {...}
GAME_DEFAULT_DESC = {}   # game_title -> last_description

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
API_TOKEN = os.getenv("BOT_TOKEN")  # ВАЖНО: читаем BOT_TOKEN (как в Render)
DATA_CSV = os.path.join(os.path.dirname(__file__), "inventory.csv")
COMMISSION = 0.06  # 6% (3% продажа + 3% вывод)
PRICE_ENDING = "tenth_9"  # округление к следующей десятичной, затем -0.01
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

def compose_listing(row, nid, target, min_sale, description: str) -> str:
    return (
        f"ID {nid} — {row['game']}\n"
        f"Куплено: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        "Описание для лота:\n"
        f"{description.strip()}"
    )
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
from decimal import Decimal

def apply_psychological_ending(amount, ending=PRICE_ENDING):
    """
    Режимы:
      ending == "tenth_9": всегда округлить ВВЕРХ к следующей 0.1, затем -0.01
        3.66 -> 3.69, 6.30 -> 6.39, 2.00 -> 2.09, 3.69 -> 3.69
      ending in (".99", ".49"): как раньше — к ближайшему следующему X.99 или X.49
    """
    try:
        d = Decimal(str(amount)).quantize(Decimal("0.01"))
    except Exception:
        return float(amount)

    if ending == "tenth_9":
        # работаем в центах, чтобы не ловить двоичную арифметику
        cents = int(d * 100)                              # 3.66 -> 366
        next_tenth_cents = ((cents + 9) // 10) * 10       # ceil до множителя 10 центов: 366 -> 370
        candidate_cents = next_tenth_cents - 1            # 370 - 1 = 369 -> 3.69
        return float(Decimal(candidate_cents) / Decimal(100))

    if ending in (".99", ".49"):
        end = Decimal(ending)
        int_part = int(d)                                 # целая часть вниз
        candidate = Decimal(int_part) + end               # 6.38 -> 6.99 / 6.49
        if candidate < d:
            candidate = Decimal(int_part + 1) + end
        return float(candidate.quantize(Decimal("0.01")))

    # по умолчанию — ничего не делаем
    return float(d)

    end = Decimal(ending)
    int_part = int(d)  # целая часть вниз
    candidate = Decimal(int_part) + end
    if candidate < d:
        candidate = Decimal(int_part + 1) + end
    return float(candidate.quantize(Decimal("0.01")))
def get_description_for_game(game: str) -> str:
    return GAME_DEFAULT_DESC.get(game)

def set_description_for_game(game: str, desc: str) -> None:
    if desc:
        GAME_DEFAULT_DESC[game] = desc.strip()

def compose_listing(row, nid, target, min_sale, desc: str) -> str:
    return (
        f"ID {nid} — {row['game']}\n"
        f"Куплено: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        "Описание для лота:\n"
        f"{desc}"
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
        min_sale = apply_psychological_ending(calc_min_sale(price_f, target_net=1.0))
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

    lines = []
    for r in in_stock:
        # описание берём из базы шаблонов / памяти; если нет — fallback
        desc = get_description_for_game(r["game"]) or r["account_desc"] or f'Stirka | "{r["game"]}"'
        # цена продажи — это сохранённая мин. цена; если пусто, не ломаемся
        sale = r["min_sale_for_target"].strip() if r.get("min_sale_for_target") else ""
        if sale:
            lines.append(f'ID {r["id"]} — {desc} — {sale}$')
        else:
            # если цена ещё не выбрана, просто покажем без неё
            lines.append(f'ID {r["id"]} — {desc}')

    await message.answer("Лоты в наличии:\n" + "\n".join(lines))

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
        InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
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
    listing_text = (
        f"ID {nid} — {row['game']}\n"
        f"Куплено: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        "Описание для лота:\n"
        f"{desc}"
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
    kb.add(
        InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
        InlineKeyboardButton("Отметить проданным",      callback_data=f"sold_direct:{nid}")
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
        InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
        InlineKeyboardButton("Отметить проданным",      callback_data=f"sold_direct:{nid}")
    )
    kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))
    await message.answer(listing_text, reply_markup=kb)
    USER_STATE.pop(message.from_user.id, None)
@dp.message_handler(
    lambda m: (
        m.text
        and not m.text.startswith('/')
        and USER_STATE.get(m.from_user.id, {}).get("mode") in ("custom_desc", "edit_desc", "await_profit_value")
    ),
    content_types=types.ContentType.TEXT,
)
async def handle_desc_or_profit(message: types.Message):
    ...
    st = USER_STATE.get(message.from_user.id)
    if not st:
        return  # не наша ветка — дальше сработает твой общий парсер уведомлений

    text = message.text.strip()
    nid = st.get("nid")

    # 1) Пользователь только что ввёл ОПИСАНИЕ
    if st.get("mode") in ("custom_desc", "edit_desc"):
        rows = read_rows()
        row = next((r for r in rows if r["id"] == str(nid)), None)
        if not row:
            USER_STATE.pop(message.from_user.id, None)
            await message.answer("ID не найден. Начните заново.")
            return

        # Сохраняем описание как шаблон для этой игры
        set_description_for_game(row["game"], text)

        # Просим выбрать профит (или ввести своё число)
        kb = InlineKeyboardMarkup(row_width=3)
        kb.add(
            InlineKeyboardButton("Профит $0.5", callback_data=f"profit:{nid}:0.5"),
            InlineKeyboardButton("Профит $1",   callback_data=f"profit:{nid}:1"),
            InlineKeyboardButton("Профит $2",   callback_data=f"profit:{nid}:2"),
        )
        kb.add(InlineKeyboardButton("Другая сумма", callback_data=f"enterprofit:{nid}"))

        USER_STATE[message.from_user.id] = {"mode": "wait_profit", "nid": nid}
        await message.answer("Теперь выберите размер профита:", reply_markup=kb)
        return

    # 2) Пользователь вводит ЧИСЛО профита (после кнопки «Другая сумма»)
    if st.get("mode") == "await_profit_value":
        try:
            target = float(text.replace(",", "."))
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



        desc = get_description_for_game(row["game"]) or f'Stirka | "{row["game"]}"'

        listing_text = compose_listing(row, nid, target, min_sale, desc)

        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"editdesc:{nid}"))
        kb.add(
            InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
            InlineKeyboardButton("Отметить проданным",      callback_data=f"sold_direct:{nid}")
        )
        kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))
        USER_STATE.pop(message.from_user.id, None)  # цепочка завершена
        await message.answer(listing_text, reply_markup=kb)
        return
@dp.message_handler(
    lambda m: (m.text is not None) and not m.text.startswith('/') and m.chat.id not in WAITING_DESC,
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
        nid = next_id(rows)
        new = {
            "id": str(nid),
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
    txt = (
        f"ID {nid} — {row['game']}\n"
        f"Куплено: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target_f}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        "Описание для лота:\n"
        f'Stirka | "{row["game"]}"'
    )
    await message.answer(txt)
   

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("posted:"))
async def cb_posted(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)
    async with FILE_LOCK:
        rows = read_rows()
        row = next((r for r in rows if r["id"] == nid), None)
        if not row:
            await call.answer("Лот не найден.", show_alert=True)
            return
        row["status"] = "listed"
        write_rows(rows)

    await call.message.answer(f"Лот {nid} помечен как опубликованный.")
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

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("enterprofit:"))
async def cb_enterprofit(call: types.CallbackQuery):
    _, nid = call.data.split(":", 1)
    USER_STATE[call.from_user.id] = {"mode": "await_profit_value", "nid": nid}
    await call.message.answer("Введите желаемый профит числом, например: 1.5")
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
    

@dp.callback_query_handler(lambda c: c.data and c.data.startswith("profit:"))
async def cb_profit(call: types.CallbackQuery):
    _, nid, profit = call.data.split(":", 2)

    rows = read_rows()
    row = next((r for r in rows if r["id"] == str(nid)), None)
    if not row:
        await call.answer("Лот не найден.", show_alert=True)
        return

    if profit == "custom":
        # сначала попросим описание, потом спросим кастомный профит
        # попробуем подсказать сохранённый шаблон, если он уже есть
        saved = GAME_DEFAULT_DESC.get(row["game"])
        hint = f'\n(для "{row["game"]}" у меня уже есть шаблон: {saved})' if saved else ""
        USER_STATE[call.from_user.id] = {"mode": "custom_desc", "nid": nid}
        await call.message.answer(
            "Введите желаемый текст для описания лота." + hint + "\n(после этого я спрошу размер профита)"
        )
        await call.answer()
        return

    try:
        target = float(profit)
    except Exception:
        target = 1.0

    min_sale = apply_psychological_ending(
    calc_min_sale(float(row["buy_price"]), target_net=target)
    )

    # Пробуем найти сохранённое описание для этой игры
    saved_desc = GAME_DEFAULT_DESC.get(row["game"])
    saved_desc = GAME_DEFAULT_DESC.get(row["game"])
    if saved_desc:
        row["min_sale_for_target"] = f"{min_sale:.2f}"
        write_rows(rows)

        listing_text = (
            f"ID {nid} — {row['game']}\n"
            f"Куплено: {row['buy_price']}$\n"
            f"Целевой чистый профит: {target}$\n"
            f"Мин. цена продажи: {min_sale}$\n\n"
            "Описание для лота:\n"
            f"{saved_desc}"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
        kb.add(
            InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
            InlineKeyboardButton("Отметить проданным",      callback_data=f"sold_direct:{nid}")
        )
        kb.add(InlineKeyboardButton("Восстановлен", callback_data=f"restored:{nid}"))
        await call.message.answer(listing_text, reply_markup=kb)
        await call.answer()
        return


    # Если сохранённого описания нет — просим ввести
    USER_STATE[call.from_user.id] = {"mode": "fixed_desc", "nid": nid, "target": target}
    await call.message.answer(
        f"Введите желаемый текст для описания лота для игры «{row['game']}». "
        f"После отправки я подставлю его в шаблон и запомню для этой игры."
    )
    await call.answer()
# Нажали "Изменить текст" — просим новый текст и помним выбранный профит
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

    listing_text = (
        f"ID {nid} — {row['game']}\n"
        f"Куплено: {row['buy_price']}$\n"
        f"Целевой чистый профит: {target}$\n"
        f"Мин. цена продажи: {min_sale}$\n\n"
        "Описание для лота:\n"
        f"{desc}"
    )

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("Изменить текст", callback_data=f"edit_desc:{nid}:{target}"))
    kb.add(
        InlineKeyboardButton("Отметить опубликованным", callback_data=f"posted:{nid}"),
        InlineKeyboardButton("Отметить проданным",      callback_data=f"sold_direct:{nid}")
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

    sold = [r for r in rows if r["status"] == "sold" and r.get("net_profit")]
    sold_count = len(sold)
    total_net_sales = sum(float(r["net_profit"]) for r in sold) if sold else 0.0  # чистая прибыль с продаж

    restored = [r for r in rows if r["status"] == "restored"]
    restored_count = len(restored)
    total_losses = sum(float(r["buy_price"]) for r in restored)  # потери = цена покупки

    # Итоговый финансовый результат = прибыль с продаж - потери от восстановленных
    real_result = total_net_sales - total_losses

    text = (
        "Статистика:\n"
        f"Всего позиций: {bought_count}\n"
        f"Продано: {sold_count}\n"
        f"Восстановлено: {restored_count}\n"
        f"Потрачено всего: {total_spent:.2f}$\n"
        f"Чистая прибыль с продаж: {total_net_sales:.2f}$\n"
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

























