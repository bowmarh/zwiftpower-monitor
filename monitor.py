import os
import base64
import hashlib
import sys
import time
import re
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- Required env ---
TARGET_URL = os.environ.get("TARGET_URL", "").strip()
STORAGE_STATE_B64 = os.environ.get("STORAGE_STATE_B64", "").strip()

# --- Optional env (any combination works) ---
GENERIC_WEBHOOK = os.environ.get("WEBHOOK_URL", "").strip()           # treated like Discord payload
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()

# --- Local cache files (persisted via Actions cache step) ---
CACHE_FILE = "last_hash.txt"
SNAPSHOT_HTML = "last_table.html"
STORAGE_STATE_FILE = "storage_state.json"

# --- Basic guards ---
if not TARGET_URL:
    print("ERROR: TARGET_URL env var is required")
    sys.exit(1)
if not STORAGE_STATE_B64:
    print("ERROR: STORAGE_STATE_B64 secret missing.")
    sys.exit(1)
if not (GENERIC_WEBHOOK or DISCORD_WEBHOOK or SLACK_WEBHOOK):
    print("WARNING: No webhook set (WEBHOOK_URL or DISCORD_WEBHOOK_URL or SLACK_WEBHOOK_URL). You won't receive notifications.")

def write_storage_state():
    """Decode base64 cookies into storage_state.json."""
    with open(STORAGE_STATE_FILE, "wb") as f:
        f.write(base64.b64decode(STORAGE_STATE_B64))

def html_to_text(html: str) -> str:
    """Very simple tag stripper for Slack-friendly text."""
    # Replace <br> / <p> with newlines first
    html = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    html = re.sub(r"(?i)</\s*p\s*>", "\n", html)
    # Remove other tags
    text = re.sub(r"<[^>]+>", "", html)
    # Collapse excessive whitespace
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text

def notify(html_msg: str, text_msg: str):
    """
    Sends to any/all configured webhooks.
    - Discord & generic expect {"content": "..."}; we send the HTML snippet in a code block for readability.
    - Slack expects {"text": "..."}; we send the plain-text version for better legibility.
    """
    sent_any = False

    # Discord-like (Discord + generic)
    discord_payload = {"content": html_msg}
    for name, url in (("Generic", GENERIC_WEBHOOK), ("Discord", DISCORD_WEBHOOK)):
        if not url:
            continue
        try:
            r = requests.post(url, json=discord_payload, timeout=15)
            r.raise_for_status()
            print(f"✅ Sent to {name}")
            sent_any = True
        except Exception as e:
            print(f"❌ Failed to send to {name}: {e}")

    # Slack
    if SLACK_WEBHOOK:
        try:
            r = requests.post(SLACK_WEBHOOK, json={"text": text_msg}, timeout=15)
            r.raise_for_status()
            print("✅ Sent to Slack")
            sent_any = True
        except Exception as e:
            print(f"❌ Failed to send to Slack: {e}")

    if not sent_any:
        print("(No webhooks delivered)")

def get_first_present_html(page, selectors):
    """Return innerHTML of the first visible selector; fallback to <body>."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            el.wait_for(state="visible", timeout=7000)
            return el.inner_html()
        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue
    return page.locator("body").inner_html()

def main():
    write_storage_state()

    # Most specific first to reduce false positives
    watched_selectors = [
        "table#results",
        "table.dataTable",
        "#events_results_table",
        "div#content table",
        "table",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STORAGE_STATE_FILE)
        page = context.new_page()

        # Load target and wait for network quiet
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        # Small extra wait for DataTables/AJAX
        time.sleep(2.0)

        html = get_first_present_html(page, watched_selectors)

        # Hash to detect change
        h = hashlib.sha256(html.encode("utf-8")).hexdigest()
        last = ""
        if os.path.exists(CACHE_FILE):
            last = open(CACHE_FILE, "r", encoding="utf-8").read().strip()

        if h != last:
            # Save full snapshot
            with open(SNAPSHOT_HTML, "w", encoding="utf-8") as f:
                f.write(html)

            # Prepare readable messages (respect ~2000 chars for Discord)
            html_snippet = html
            if len(html_snippet) > 1800:
                html_snippet = html_snippet[:1800] + "\n...(truncated)..."
            html_msg = f"ZwiftPower change detected:\n{TARGET_URL}\n\n```html\n{html_snippet}\n```"

            text_snippet = html_to_text(html)
            if len(text_snippet) > 3500:
                text_snippet = text_snippet[:3500] + "\n...(truncated)..."
            text_msg = f"ZwiftPower change detected:\n{TARGET_URL}\n\n{text_snippet}"

            notify(html_msg=html_msg, text_msg=text_msg)

            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                f.write(h)
            print("Change detected. Hash & snapshot updated.")
        else:
            print("No change.")

        browser.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
