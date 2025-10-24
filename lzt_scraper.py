# lzt_scraper.py
import os, json, re, hashlib
import requests

# Можно переопределить конкретным урлом через LZT_ALERTS_URL.
DEFAULT_URLS = [
    os.getenv("LZT_ALERTS_URL", "").strip() or "https://zelenka.guru/account/alerts",
    "https://lolz.guru/account/alerts",
    "https://lolz.live/account/alerts",
]

STATE_PATH = os.path.join(os.path.dirname(__file__), "alerts_state.json")

# --------- seen-state ----------
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

# --------- cookies + session ----------
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
    # Похожий на браузер набор заголовков
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
    })

    # Cookie-Editor обычно отдаёт список объектов {name,value,domain,path,...}
    if isinstance(cookies, list):
        for c in cookies:
            if isinstance(c, dict) and "name" in c and "value" in c:
                s.cookies.set(
                    c["name"], c["value"],
                    domain=c.get("domain"),
                    path=c.get("path", "/"),
                    secure=c.get("secure", True),
                )
    elif isinstance(cookies, dict):
        # на случай, если экспорт в виде словаря name->value
        for k, v in cookies.items():
            s.cookies.set(k, v)
    return s

# --------- парсер текста ----------
PATTERN = re.compile(
    r'По вашей ссылке\s*["“]([^"”]+)["”]\s*куплен аккаунт\s*(.+?)\s*за\s*(?:\$\s*)?([\d\.,]+)',
    re.I | re.S
)

def _extract_texts_from_html(html: str):
    # грубо уберём теги/скрипты, получим сплошной текст
    html = re.sub(r'<script.*?</script>|<style.*?</style>', '', html, flags=re.S)
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)

    out = []
    for m in PATTERN.finditer(text):
        game, acc, price = m.groups()
        out.append(f'По вашей ссылке "{game}" куплен аккаунт {acc} за ${price}')
    return out

# --------- сетевой слой ----------
def _looks_like_js_challenge(html: str) -> bool:
    """Простейшая эвристика, когда возвращают страницу с dfjs/main(...)"""
    if not html:
        return False
    h = html.lower()
    return (
        '/_dfjs/' in h
        or 'please enable javascript and cookies' in h
        or 'document.addEventListener(\'DOMContentLoaded\'' in h
    )

def fetch_alerts_html():
    """
    Пытаемся открыть один из доменов.
    Возвращает dict:
      {
        "ok": True/False,
        "url": "...",
        "status": <int>,
        "js_challenge": True/False,
        "logged_in_guess": True/False,
        "snippet": "<первые ~300 символов>"
        "html": "<полный html или ''>"
      }
    """
    s = _session_with_cookies()

    last_info = None
    for url in DEFAULT_URLS:
        try:
            r = s.get(url, timeout=25, allow_redirects=True)
            status = r.status_code
            html = r.text or ""
            js_ch = _looks_like_js_challenge(html)
            # “угадать”, что мы залогинены (по наличию xf_user в куки и статуса 200)
            logged_guess = ("xf_user" in s.cookies.get_dict()) and (status == 200)
            info = {
                "ok": (status == 200) and not js_ch,
                "url": url,
                "status": status,
                "js_challenge": js_ch,
                "logged_in_guess": bool(logged_guess),
                "snippet": (html[:300] if html else ""),
                "html": html,
            }
            if info["ok"]:
                return info
            last_info = info
        except Exception as e:
            last_info = {
                "ok": False,
                "url": url,
                "status": 0,
                "js_challenge": False,
                "logged_in_guess": False,
                "snippet": str(e),
                "html": "",
            }
    return last_info or {"ok": False, "url": "", "status": 0, "js_challenge": False, "logged_in_guess": False, "snippet": "", "html": ""}

# --------- публичные функции ----------
def poll_new_texts():
    """
    Основная функция: тянем HTML, выдёргиваем тексты, делаем дедуп по alerts_state.json.
    Если встречаем JS-челлендж — кидаем понятную ошибку.
    """
    info = fetch_alerts_html()
    if not info["ok"]:
        # Пусть вызывающая сторона покажет это как есть в /probe
        if info.get("js_challenge"):
            raise RuntimeError("JS challenge page (anti-bot). Проверь cookies и домен (zelenka.guru/lolz.guru).")
        raise RuntimeError(f"Fetch failed: status={info.get('status')} url={info.get('url')}")

    texts = _extract_texts_from_html(info["html"])
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
    Возвращает краткий отчёт для эндпойнта /probe: можно ли читать страницу, есть ли матчи.
    """
    info = fetch_alerts_html()
    res = {
        "ok": bool(info and info.get("status") == 200),
        "url": info.get("url"),
        "status": info.get("status"),
        "js_challenge": info.get("js_challenge"),
        "logged_in_guess": info.get("logged_in_guess"),
        "len": len(info.get("html") or ""),
        "snippet": info.get("snippet"),
        "found_matches": 0,
        "examples": [],
    }
    if res["ok"] and not res["js_challenge"]:
        texts = _extract_texts_from_html(info.get("html") or "")
        res["found_matches"] = len(texts)
        res["examples"] = texts[:3]
    return res