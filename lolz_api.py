# lolz_api.py
import os, json, urllib.request, urllib.error, asyncio

class LolzError(Exception):
    pass

class LolzClient:
    def __init__(self):
        self.api_key = os.getenv("LOLZ_API_KEY")      # тот Market API токен
        if not self.api_key:
            raise LolzError("Переменная окружения LOLZ_API_KEY не задана.")
        self.base = "https://prod-api.lzt.market"     # <— ВАЖНО

    async def get_profile(self) -> dict:
        def _do():
            req = urllib.request.Request(
                f"{self.base}/me",
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do)

    async def publish_listing(self, title: str, description: str, price: float,
                              extra: dict | None = None) -> int:
        # по умолчанию шлём на /item/add. Если хочешь fast-sell — поменяй на /item/fast-sell
        url = f"{self.base}/item/add"

        payload = {
            "title": title,
            "description": description,
            "price": price,
        }
        if extra:
            payload.update(extra)

        def _do():
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8", "ignore")
                    j = json.loads(body or "{}")
                    # вытащи id из реального поля ответа:
                    lid = j.get("id") or j.get("data", {}).get("id")
                    if not lid:
                        raise LolzError(f"Не удалось получить ID объявления из ответа: {j}")
                    return int(lid)
            except urllib.error.HTTPError as e:
                err = e.read().decode("utf-8", "ignore")
                raise LolzError(f"HTTPError {e.code}: {err}")
            except urllib.error.URLError as e:
                raise LolzError(f"URLError: {e.reason}")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do)
