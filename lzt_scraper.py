# lzt_scraper.py
import os, json, re, hashlib
import requests

ALERTS_URL = os.getenv("LZT_ALERTS_URL", "https://zelenka.guru/account/alerts")
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
        raise RuntimeError("LZT_COOKIES_JSON must be valid JSON exported from Cookie-Editor")

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "ru,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
        "Referer": "https://zelenka.guru/"
    })
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and "name" in c and "value" in c:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    elif isinstance(cookies, dict):
        for k, v in cookies.items():
            s.cookies.set(k, v)
    return s

# Чуть шире регекс — поддержим «ёлочки», варианты формулировок и цену с/без $.
PATTERN = re.compile(
    r'По вашей\s*(?:реферальной|рекламной|партн[её]рской)?\s*ссылке\s*[«"“]([^"»”]+)[»"”]\s*'
    r'(?:был[аио]? )?куплен[ао]?\s*(?:аккаунт|уч[её]тн(?:ая|ую) запись)\s*(.+?)\s*за\s*\$?\s*([\d\.,]+)\s*\$?',
    re.I | re.S
)

def _extract_texts_from_html(html: str):
    html = re.sub(r'<script.*?</script>|<style.*?</style>', '', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)

    out = []
    for m in PATTERN.finditer(text):
        game, acc, price = m.groups()
        out.append(f'По вашей ссылке "{game}" куплен аккаунт {acc} за ${price}')
    return out

def poll_new_texts():
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

# --- ОТЛАДКА ---
def scraper_debug():
    s = _session_with_cookies()
    r = s.get(ALERTS_URL, timeout=25)
    html = r.text
    texts = _extract_texts_from_html(html)
    flat = re.sub(r'\s+', ' ', html)  # без переводов строк
    return {
        "status": r.status_code,
        "url": r.url,
        "len": len(html),
        "logged_in_guess": ("Войти" not in flat and "login" not in r.url.lower()),
        "found_matches": len(texts),
        "examples": texts[:5],
        "snippet": flat[:700]
    }
