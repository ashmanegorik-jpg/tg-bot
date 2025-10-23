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

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    # Cookie-Editor обычно отдаёт список объектов {name,value,domain,path,...}
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and "name" in c and "value" in c:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    elif isinstance(cookies, dict):
        # на случай словаря name->value
        for k, v in cookies.items():
            s.cookies.set(k, v)
    return s

# Ищем текст вида: По вашей ссылке "GTA 5" куплен аккаунт ... за $5.53
PATTERN = re.compile(
    r'По вашей ссылке\s*["“]([^"”]+)["”]\s*куплен аккаунт\s*(.+?)\s*за\s*(?:\$\s*)?([\d\.,]+)',
    re.I | re.S
)

def _extract_texts_from_html(html: str):
    # грубо уберём теги, чтобы остался сплошной текст
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
