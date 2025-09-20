
import os, base64, hashlib, json, sys, time
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

TARGET_URL = os.environ.get("TARGET_URL", "").strip()
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").strip()  # Discord/Slack webhook
STORAGE_STATE_B64 = os.environ.get("STORAGE_STATE_B64", "").strip()  # base64 of storage_state.json
CACHE_FILE = "last_hash.txt"

if not TARGET_URL:
    print("ERROR: TARGET_URL env var is required")
    sys.exit(1)

def write_storage_state():
    if not STORAGE_STATE_B64:
        print("ERROR: STORAGE_STATE_B64 secret missing.")
        sys.exit(1)
    with open("storage_state.json", "wb") as f:
        f.write(base64.b64decode(STORAGE_STATE_B64))

def notify(msg: str):
    if not WEBHOOK_URL:
        print(f"(No webhook set) {msg}")
        return
    try:
        # Discord-compatible JSON payload; for Slack, use {"text": msg}
        payload = {"content": msg}
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Failed to send webhook: {e}")

def get_first_present_html(page, selectors):
    for sel in selectors:
        try:
            el = page.locator(sel).first
            el.wait_for(state="visible", timeout=5000)
            return el.inner_html()
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    # Fallback: whole body (may cause more “changes” noise)
    return page.locator("body").inner_html()

def main():
    write_storage_state()

    watched_selectors = [
        "table#results",
        "table.dataTable",
        "#events_results_table",
        "div#content table",
        "table",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state="storage_state.json")
        page = context.new_page()
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)

        # Give the page time for dynamic tables (DataTables/AJAX)
        time.sleep(2.0)

        html = get_first_present_html(page, watched_selectors)
        h = hashlib.sha256(html.encode("utf-8")).hexdigest()

        last = ""
        if os.path.exists(CACHE_FILE):
            last = open(CACHE_FILE, "r", encoding="utf-8").read().strip()

        if h != last:
            notify(f"ZwiftPower change detected:\n{TARGET_URL}")
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                f.write(h)
            print("Change detected. Hash updated.")
        else:
            print("No change.")

        browser.close()

if __name__ == "__main__":
    main()
