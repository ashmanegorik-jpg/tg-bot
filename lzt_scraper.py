# lzt_scraper.py
import os, json, re, hashlib
import requests

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
    cookies_json = os.getenv("LZT_COOKIES_JSON") or os.getenv("LOLZ_COOKIES_JSON") or ""
    if not cookies_json:
        raise RuntimeError("Set LZT_COOKIES_JSON (or LOLZ_COOKIES_JSON) in env")
    try:
        cookies = json.loads(cookies_json)
    except Exception:
        raise RuntimeError("LZT_COOKIES_JSON must be valid JSON exported by Cookie-Editor")

    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept-Language": "ru,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://lolz.live/",
    })
    # Cookie-Editor обычно отдаёт список объектов {name,value,domain,path,...}
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and "name" in c and "value" in c:
                s.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
    elif isinstance(cookies, dict):
        for k, v in cookies.items():
            s.cookies.set(k, v)
    return s

# Ищем текст вида: По вашей ссылке "GTA 5" куплен аккаунт ... за $5.53
PATTERN = re.compile(
    r'По вашей\s+ссылке\s*[«"“]?([^"”»]+)["”»]?\s*куплен[а-я\s]*аккаунт\s*(.+?)\s*за\s*(?:\$?\s*)([\d\.,]+)\s*\$?',
    re.I | re.S
)

def _clean_html(html: str) -> str:
    html = re.sub(r'<script.*?</script>|<style.*?</style>', '', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()

def _extract_texts_from_html(html: str):
    text = _clean_html(html)
    out = []
    for m in PATTERN.finditer(text):
        game, acc, price = m.groups()
        out.append(f'По вашей ссылке "{game}" куплен аккаунт {acc} за ${price}')
    return out

def poll_new_texts():
    s = _session_with_cookies()

    # Сначала пытаемся получить JSON (AJAX-представление XenForo)
    r = s.get(ALERTS_URL, params={"_xfResponseType": "json"},
              headers={"X-Requested-With": "XMLHttpRequest"}, timeout=25)

    html = ""
    ct = r.headers.get("Content-Type", "")
    if "application/json" in ct:
        try:
            j = r.json()
            # У разных сборок XenForo html может лежать по-разному
            html = (
                (j.get("html") or {}).get("content") or
                j.get("html") or
                j.get("page") or
                ""
            )
        except Exception:
            html = ""
    if not html:
        # Фоллбек — обычная страница
        r = s.get(ALERTS_URL, timeout=25)
        r.raise_for_status()
        html = r.text

    texts = _extract_texts_from_html(html)

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