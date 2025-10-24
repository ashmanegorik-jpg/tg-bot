# lzt_scraper.py
import os, json, re, hashlib
import requests

# страница с уведомлениями Лолза (НЕ market)
ALERTS_URL = os.getenv("LZT_ALERTS_URL", "https://lolz.live/account/alerts")
STATE_PATH = os.path.join(os.path.dirname(__file__), "alerts_state.json")

def _load_seen():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_seen(seen: set):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen))[-500:], f, ensure_ascii=False)
    except Exception:
        pass

def _session_with_cookies():
    """
    Создаёт requests.Session с куками из LZT_COOKIES_JSON (или LOLZ_COOKIES_JSON).
    Подходит JSON из расширения Cookie-Editor на домене https://lolz.live/account/alerts
    """
    cookies_json = (
        os.getenv("LZT_COOKIES_JSON")
        or os.getenv("LOLZ_COOKIES_JSON")
        or ""
    )
    if not cookies_json:
        raise RuntimeError("Set LZT_COOKIES_JSON (or LOLZ_COOKIES_JSON) in env")

    try:
        cookies = json.loads(cookies_json)
    except Exception:
        raise RuntimeError("LZT_COOKIES_JSON must be valid JSON from Cookie-Editor")

    s = requests.Session()
    # чуть более “реальный” заголовок браузера
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive",
    })

    # Cookie-Editor обычно отдаёт список объектов {name,value,domain,path,...}
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and "name" in c and "value" in c:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    elif isinstance(cookies, dict):
        # На случай словаря name->value
        for k, v in cookies.items():
            s.cookies.set(k, v)

    return s

# Ищем текст вида: По вашей ссылке "GTA 5" куплен аккаунт ... за $5.53
PATTERN = re.compile(
    r'По вашей ссылке\s*["“]([^"”]+)["”]\s*куплен аккаунт\s*(.+?)\s*за\s*(?:\$\s*)?([\d\.,]+)',
    re.I | re.S
)

def _extract_texts_from_html(html: str):
    # убираем скрипты/стили и теги, чтобы искать по «плоскому» тексту
    html = re.sub(r'<script.*?</script>|<style.*?</style>', '', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)

    out = []
    for m in PATTERN.finditer(text):
        game, acc, price = m.groups()
        out.append(f'По вашей ссылке "{game}" куплен аккаунт {acc} за ${price}')
    return out

def poll_new_texts():
    """
    Возвращает список НОВЫХ (ещё не отправленных ранее) найденных уведомлений-строк.
    """
    s = _session_with_cookies()
    r = s.get(ALERTS_URL, timeout=25)
    r.raise_for_status()

    texts = _extract_texts_from_html(r.text)

    seen = _load_seen()
    new = []
    for t in texts:
        h = hashlib.sha1(t.encode("utf-8")).hexdigest()
        if h not in seen:
            seen.add(h)
            new.append(t)
    if new:
        _save_seen(seen)
    return new

def debug_probe():
    """
    Вспомогательная диагностика для /probe — не влияет на основную работу.
    Помогает понять: страница открывается, нет ли JS-челленджа, видим ли шаблон.
    """
    result = {
        "ok": False,
        "status": None,
        "url": ALERTS_URL,
        "len": 0,
        "snippet": "",
        "found_matches": 0,
        "examples": [],
        "logged_in_guess": False,
        "js_challenge": False,
    }
    try:
        s = _session_with_cookies()
        r = s.get(ALERTS_URL, timeout=25, allow_redirects=True)
        result["status"] = r.status_code
        result["url"] = r.url
        result["len"] = len(r.text)
        result["snippet"] = r.text[:370]

        # very rough heuristics
        txt_low = r.text.lower()
        result["js_challenge"] = ('/_dfjs/' in r.text) or ('please enable javascript' in txt_low and 'cookies' in txt_low)
        result["logged_in_guess"] = ('logout' in txt_low) or ('alerts' in r.url.lower())

        found = _extract_texts_from_html(r.text)
        result["found_matches"] = len(found)
        result["examples"] = found[:3]
        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)
    return result