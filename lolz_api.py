# lolz_api.py
import os
import json
import urllib.request
import urllib.error
import asyncio

class LolzError(Exception):
    pass

class LolzClient:
    def __init__(self):
        self.api_key = os.getenv("LOLZ_API_KEY")  # задай в Render → Environment
        if not self.api_key:
            raise LolzError("Переменная окружения LOLZ_API_KEY не задана.")
        self.base = "https://api.zelenka.guru"    # пример базового домена API

    async def publish_listing(self, title: str, description: str, price: float, extra: dict | None = None) -> int:
        """
        Пример-обёртка. Здесь нужно поставить точный endpoint и поля под твою категорию.
        Сейчас показан шаблон, чтобы связка в боте работала.
        """
        payload = {
            "title": title,
            "description": description,
            "price": price,
        }
        if extra:
            payload.update(extra)

        # ВНИМАНИЕ: замени URL на реальный endpoint из документации Lolz (Market API).
        url = f"{self.base}/market/your-endpoint-here"

        def _do_request():
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8")
                    if resp.status >= 400:
                        raise LolzError(f"HTTP {resp.status}: {body}")
                    j = json.loads(body or "{}")
                    # ВЫТАЩИ ИЗ j реальный ID объявления по схеме их ответа:
                    listing_id = j.get("id") or j.get("data", {}).get("id")
                    if not listing_id:
                        raise LolzError(f"Не удалось получить ID объявления из ответа: {j}")
                    return int(listing_id)
            except urllib.error.HTTPError as e:
                raise LolzError(f"HTTPError {e.code}: {e.read().decode('utf-8', 'ignore')}")
            except urllib.error.URLError as e:
                raise LolzError(f"URLError: {e.reason}")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_request)
