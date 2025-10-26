# list_open_feature.py
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def setup_list_open_feature(
    dp, bot,
    read_rows, write_rows,
    get_description_for_game, auto_desc_for_game,
    compose_listing, calc_net_from_sale
):
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

    @dp.callback_query_handler(lambda c: c.data and c.data.startswith("open:"))
    async def cb_open(call: types.CallbackQuery):
        _, nid = call.data.split(":", 1)
        rows = read_rows()
        row = next((r for r in rows if r.get("id") == str(nid)), None)
        if not row:
            await call.answer("Лот не найден.", show_alert=True)
            return

        try:
            min_sale = float(row.get("min_sale_for_target") or 0)
        except:
            min_sale = 0.0
        try:
            buy_price = float(row.get("buy_price") or 0)
        except:
            buy_price = 0.0

        target = calc_net_from_sale(min_sale, buy_price) if (min_sale and buy_price) else 1.0
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