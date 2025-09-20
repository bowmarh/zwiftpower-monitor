
from playwright.sync_api import sync_playwright

OUTPUT = "storage_state.json"
START = "https://zwiftpower.com/"

print("A browser window will open. Log in to ZwiftPower fully, then come back here.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto(START)
    input("➡️ After you log in and can see your ZwiftPower dashboard, come back here and press ENTER...")
    context.storage_state(path=OUTPUT)
    context.close()
    browser.close()

print(f"✅ Saved login cookies into {OUTPUT}")
