# lzt_scraper.py
import os, json, re, hashlib
import requests

ALERTS_URL = os.getenv("LZT_ALERTS_URL", "https://zelenka.guru/account/alerts")
STATE_PATH = os.path.join(os.path.dirname(__file__), "alerts_state.json")
UA = os.getenv("LZT_UA", "Mozilla/5.0")

# Ищем текст: По вашей ссылке "GTA 5" куплен аккаунт ... за $5.53
PATTERN = re.compile(
    r'По вашей ссылке\s*["“]([^"”]+)["”]\s*куплен аккаунт\s*(.+?)\s*за\s*(?:\$\s*)?([\d\.,]+)',
    re.I | re.S
)

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
        raise RuntimeError("LZT_COOKIES_JSON must be valid JSON from Cookie-Editor")

    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    # Cookie-Editor обычно отдаёт список объектов {name,value,domain,path,...}
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and "name" in c and "value" in c:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    elif isinstance(cookies, dict):
        for k, v in cookies.items():
            s.cookies.set(k, v)

    return s

def _extract_texts_from_html(html: str):
    html_noscript = re.sub(r'<script.*?</script>|<style.*?</style>', '', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', html_noscript)
    text = re.sub(r'\s+', ' ', text)
    out = []
    for m in PATTERN.finditer(text):
        game, acc, price = m.groups()
        out.append(f'По вашей ссылке "{game}" куплен аккаунт {acc} за ${price}')
    return out

def poll_new_texts():
    s = _session_with_cookies()
    r = s.get(ALERTS_URL, timeout=25, allow_redirects=True)
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

# Диагностика: посмотреть, что реально приходит со страницы
def debug_probe():
    s = _session_with_cookies()
    r = s.get(ALERTS_URL, timeout=25, allow_redirects=True)
    body = r.text or ""
    plain = re.sub(r'<script.*?</script>|<style.*?</style>', '', body, flags=re.S)
    plain = re.sub(r'<[^>]+>', ' ', plain)
    plain = re.sub(r'\s+', ' ', plain)

    matches = PATTERN.findall(plain)
    logged_in_guess = ("Выйти" in body) or ("logout" in body.lower()) or ("account/alerts" in r.url)
    js_challenge = ("/_dfjs/" in body) or ("Please enable JavaScript" in body)

    return {
        "url": r.url,
        "status": r.status_code,
        "len": len(body),
        "logged_in_guess": logged_in_guess,
        "js_challenge": js_challenge,
        "found_matches": len(matches),
        "examples": [f'По вашей ссылке "{g}" куплен аккаунт {a} за ${p}' for (g,a,p) in matches[:3]],
        "snippet": body[:400]
    }
