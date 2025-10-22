# lolz_publisher.py
import os
import requests

BASE = "https://prod-api.lzt.market"
LOLZ_TOKEN = os.getenv("LOLZ_API_TOKEN")

class LolzError(Exception):
    pass

def _headers():
    if not LOLZ_TOKEN:
        raise LolzError("Переменная окружения LOLZ_API_TOKEN не установлена.")
    return {
        "Authorization": f"Bearer {LOLZ_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def fast_sell(category_id: int, title: str, description: str, price: float, account_data: dict):
    """
    Пытается опубликовать лот через быстрый метод.
    ВНИМАНИЕ: точный состав поля `account_data` зависит от категории.
    Для простых категорий обычно достаточно {"login": "...", "password": "..."}.
    Для Steam почти всегда нужен мафайл (см. Steam endpoints в доках).
    Возвращает dict ответа API.
    """
    url = f"{BASE}/market/item/fast-sell"
    payload = {
        "category_id": category_id,     # ID категории из /categories (см. доку)
        "title": title,                  # заголовок (можно просто игра/алиас)
        "description": description,      # описание
        "price": price,                  # цена в USD по умолчанию
        "currency": "USD",
        "auto_buy": True,                # чтобы работал автобай
        "data": account_data,            # ДАННЫЕ АККАУНТА (логины/пароли и т.п.)
    }
    r = requests.post(url, headers=_headers(), json=payload, timeout=60)
    # успешный ответ отдает JSON с информацией об объявлении (id, url, etc.)
    try:
        data = r.json()
    except Exception:
        data = {"raw": r.text}
    if r.status_code >= 400:
        # API обычно возвращает {"error": "..."} — поднимем исключение с текстом
        raise LolzError(str(data))
    return data

