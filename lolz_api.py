# lolz_api.py
import os
import json
import urllib.parse
import urllib.request
import urllib.error

class LolzError(Exception):
    pass

class LolzClient:
    def __init__(self):
        self.api_key = os.getenv("LOLZ_API_KEY")  # ключ с аккаунта, где крутится автобай
        if not self.api_key:
            raise LolzError("Переменная окружения LOLZ_API_KEY не задана.")
        self.base = os.getenv("LZT_API_BASE", "https://api.zelenka.guru")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "notify-bot/1.0"
        }

    def get_recent_purchases(self, limit=50):
        """
        Возвращает последние покупки. Точный endpoint укажем через переменную
        окружения LZT_PURCHASES_URL, чтобы не упираться в формат.
        По умолчанию попробуем /market/purchases.
        """
        url = os.getenv("LZT_PURCHASES_URL", f"{self.base}/market/purchases")
        qs = urllib.parse.urlencode({"limit": limit})
        if "?" in url:
            full = f"{url}&{qs}"
        else:
            full = f"{url}?{qs}"

        req = urllib.request.Request(full, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", "ignore")
                if resp.status >= 400:
                    raise LolzError(f"HTTP {resp.status}: {body}")
                return json.loads(body or "{}")
        except urllib.error.HTTPError as e:
            raise LolzError(f"HTTPError {e.code}: {e.read().decode('utf-8','ignore')}")
        except urllib.error.URLError as e:
            raise LolzError(f"URLError: {e.reason}")
