# lolz_publisher.py
import os
from playwright.async_api import async_playwright

LOLZ_BASE = "https://lolz.team"

class LolzError(Exception):
    pass

async def publish_on_lolz(title: str, price: float, description: str) -> str:
    """
    Логинится на Lolz, создаёт лот и возвращает ссылку на опубликованный лот.
    Важно: подправь селекторы под реальную форму размещения.
    """
    username = os.getenv("LOLZ_USERNAME")
    password = os.getenv("LOLZ_PASSWORD")
    if not username or not password:
        raise LolzError("Не заданы LOLZ_USERNAME/LOLZ_PASSWORD в переменных окружения.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context()  # можно хранить storage_state в файле для повторного входа
        page = await ctx.new_page()

        # 1) Логин
        await page.goto(f"{LOLZ_BASE}/login/")  # проверь точный URL логина
        # TODO: проверь селекторы полей логина и пароля
        await page.fill('input[name="login"]', username)
        await page.fill('input[name="password"]', password)
        await page.click('button[type="submit"]')
        # дождёмся, что залогинились (например, появится аватар/ник)
        await page.wait_for_load_state("networkidle")
        # Можно убедиться, что на странице нет формы логина:
        if await page.query_selector('input[name="password"]'):
            await browser.close()
            raise LolzError("Не удалось войти на Lolz (проверь логин/пароль или captcha/2FA).")

        # 2) Переход к форме создания лота (URL/раздел зависит от категории)
        # TODO: поменяй URL на страницу создания продажи нужной игры/категории
        await page.goto(f"{LOLZ_BASE}/market/create")  # примерный путь — подправь
        await page.wait_for_load_state("networkidle")

        # 3) Заполнение формы
        # Название/заголовок лота:
        # TODO: селектор заголовка
        await page.fill('input[name="title"]', title)

        # Цена:
        # TODO: селектор цены
        await page.fill('input[name="price"]', f"{price:.2f}")

        # Описание:
        # В некоторых формах это textarea, в некоторых — редактор. Подправь.
        # Простой вариант:
        if await page.query_selector('textarea[name="description"]'):
            await page.fill('textarea[name="description"]', description)
        else:
            # пример для contenteditable:
            desc_el = await page.query_selector('[contenteditable="true"]')
            if desc_el:
                await desc_el.fill(description)

        # 4) Отправка формы
        # TODO: селектор кнопки публикации
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # 5) Забираем URL опубликованного лота
        url = page.url
        await browser.close()

        # Небольшая валидация: на успешной публикации URL обычно содержит id/slug лота
        if "market" not in url:
            raise LolzError(f"Похоже, публикация не удалась. Текущий URL: {url}")

        return url
