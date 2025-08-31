from playwright.sync_api import sync_playwright
import json

with open("cookies.json", "r") as f:
    cookies = json.load(f)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    context.add_cookies(cookies)  # carrega cookies salvos
    page = context.new_page()
    page.goto("https://youtube.com")
    print(page.title())
    input("pressione enter para fechar")
    context.cookies()
    with open("cookies.json", "w") as f:
        json.dump(context.cookies(), f, indent=2)