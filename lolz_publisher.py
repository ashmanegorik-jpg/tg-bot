# lolz_publisher.py
import os
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

LOLZ_BASE = "https://lolz.team"
STATE_FILE = Path("state.json")  # тут будут храниться куки/сессия

class LolzError(Exception):
    pass

async def _ensure_login(page):
    """Проверяет, что мы залогинены. Если нет — логинится формой."""
    username = os.getenv("LOLZ_USERNAME")
    password = os.getenv("LOLZ_PASSWORD")
    if not username or not password:
        raise LolzError("Не заданы LOLZ_USERNAME/LOLZ_PASSWORD в переменных окружения.")

    # Проверим, вдруг уже залогинены (есть аватар/ник/меню)
    await page.goto(f"{LOLZ_BASE}/", wait_until="networkidle")
    if not await page.query_selector('a[href*="/logout"]'):
        # Идём на /login
        await page.goto(f"{LOLZ_BASE}/login/", wait_until="domcontentloaded")
        # TODO: подправь селекторы полей логина/пароля под реальную форму
        await page.fill('input[name="login"]', username)
        await page.fill('input[name="password"]', password)
        await page.click('button[type="submit"]')
        try:
            await page.wait_for_selector('a[href*="/logout"]', timeout=10000)
        except PWTimeout:
            # снимок страницы для дебага
            await page.screenshot(path="login_failed.png", full_page=True)
            raise LolzError("Не удалось войти (проверь логин/пароль, капчу или 2FA).")

async def publish_on_lolz(title: str, price: float, description: str) -> str:
    """
    Открывает сайт, гарантирует логин, идёт на форму создания, публикует,
    возвращает URL лота. Селекторы формы нужно подогнать под реальную разметку.
    """
    from playwright.async_api import Browser

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=True)
        # Если есть сохранённая сессия — используем
        if STATE_FILE.exists():
            ctx = await browser.new_context(storage_state=str(STATE_FILE))
        else:
            ctx = await browser.new_context()

        page = await ctx.new_page()

        # Логин/проверка авторизации
        await _ensure_login(page)
        # после успешного логина — сохраним state.json, чтобы не логиниться в следующий раз
        await ctx.storage_state(path=str(STATE_FILE))

        # Переход к форме (URL нужно подправить под реальную форму)
        # Примерно так, но поменяй под твою категорию:
        await page.goto(f"{LOLZ_BASE}/market/create", wait_until="domcontentloaded")

        # === Заполнение формы (ПОДГОНИ СЕЛЕКТОРЫ) ===
        # Заголовок:
        # пример: input[name="title"]
        await page.fill('input[name="title"]', title)

        # Цена:
        await page.fill('input[name="price"]', f"{price:.2f}")

        # Описание:
        # либо textarea[name="description"], либо contenteditable редактор:
        if await page.query_selector('textarea[name="description"]'):
            await page.fill('textarea[name="description"]', description)
        else:
            desc_el = await page.query_selector('[contenteditable="true"]')
            if desc_el:
                # Некоторые редакторы не принимают .fill — используем eval:
                await desc_el.evaluate("(el, text) => { el.innerText = text; }", description)

        # Кнопка публикации:
        # пример: button[type="submit"] или кнопка с текстом
        # постарайся указать максимально конкретный селектор (id/датароль/название)
        await page.click('button[type="submit"]')

        # Ожидаем переход на страничку лота…
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            await page.screenshot(path="publish_timeout.png", full_page=True)
            raise LolzError("Публикация не подтвердилась (таймаут ожидания).")

        url = page.url
        await browser.close()

        # Простейшая валидация:
        if "market" not in url:
            raise LolzError(f"Похоже, публикация не удалась. Текущий URL: {url}")

        return url
