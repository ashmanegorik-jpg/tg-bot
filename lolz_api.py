# lolz_api.py
import os
import aiohttp
from typing import Any, Dict, Optional

LZT_TOKEN = os.getenv("LZT_TOKEN")
# === ПОДТВЕРДИ по докам базовый URL ===
BASE = "https://api.zelenka.guru"  # TODO: если другой, поменяй

class LolzError(RuntimeError):
    pass

class LolzClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or LZT_TOKEN
        if not self.token:
            raise LolzError("LZT_TOKEN не задан в переменных окружения")
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(self, method: str, url: str, **kw) -> Dict[str, Any]:
        async with aiohttp.ClientSession(headers=self._headers) as s:
            async with s.request(method, url, **kw) as r:
                # некоторые API шлют text при ошибке
                ct = r.headers.get("content-type", "")
                if "application/json" in ct:
                    data = await r.json()
                else:
                    data = {"raw": await r.text()}
                if r.status >= 300:
                    raise LolzError(f"{r.status} {data}")
                return data

    # ========= ПУБЛИКАЦИЯ ЛОТА =========
    async def publish_listing(self, *, title: str, description: str, price: float,
                              extra: Optional[Dict[str, Any]] = None) -> str:
        """
        Возвращает ID/ссылку объявления.
        TODO: замени путь и поля payload на реальные из доки.
        """
        payload = {
            "title": title,
            "description": description,
            "price": price,
            # возможно потребуются:
            # "category_id": 123,
            # "currency": "USD",
            # "game_id": ...,
            # "account": {...}
        }
        if extra:
            payload.update(extra)

        url = f"{BASE}/market/items"   # TODO: поправь путь из доки
        data = await self._request("POST", url, json=payload)

        # верни реальный ключ из ответа (посмотри в доке/примере)
        return str(
            data.get("id") or data.get("item_id") or data.get("data", {}).get("id") or data
        )

    # ========= ПОЛУЧЕНИЕ ПОКУПОК (для polling) =========
    async def get_recent_purchases(self, *, limit: int = 50) -> Dict[str, Any]:
        """
        Вернёт сырой ответ. Ты из него возьмёшь нужные поля.
        TODO: замени путь/параметры на реальные.
        """
        url = f"{BASE}/market/purchases?limit={limit}"  # TODO: поправь
        return await self._request("GET", url)
