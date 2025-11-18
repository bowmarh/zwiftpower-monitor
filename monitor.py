import os
import base64
import json
import tempfile
import textwrap

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

TARGET_URL = os.environ["TARGET_URL"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
STORAGE_STATE_B64 = os.environ["STORAGE_STATE_B64"]


def write_storage_state_file() -> str:
    """Decode STORAGE_STATE_B64 into a temporary JSON file and return its path."""
    data = base64.b64decode(STORAGE_STATE_B64)
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def scrape_rows(page):
    """
    Scrape ZwiftPower team table rows.

    Adjust selectors here if ZwiftPower changes layout.
    """
    # Make sure we are on the right page and JS has run
    page.goto(TARGET_URL, wait_until="networkidle")

    # Try to wait for any ZwiftPower DataTable
    try:
        page.wait_for_selector("table.dataTable tbody tr", timeout=15000)
    except PlaywrightTimeoutError:
        # Return empty but with some debug context
        return [], {
            "title": page.title(),
            "url": page.url,
            "note": "Timed out waiting for table.dataTable tbody tr"
        }

    rows = []
    for tr in page.query_selector_all("table.dataTable tbody tr"):
        tds = tr.query_selector_all("td")
        if not tds:
            continue
        cols = [td.inner_text().strip() for td in tds]
        rows.append(cols)

    debug_info = {
        "title": page.title(),
        "url": page.url,
        "rows_found": len(rows),
    }
    return rows, debug_info


def rows_to_markdown(rows, max_rows=20):
    """
    Convert scraped rows into a simple markdown table.

    You may want to customise headers / which columns to show
    based on the actual ZwiftPower columns.
    """
    if not rows:
        return "(no rows found)"

    # Assume columns: Pos, Name, Category, Points,... etc.
    # Adapt headers to what ZwiftPower actually shows for your team.
    header = ["#", "Name", "Category", "Points"]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]

    for idx, row in enumerate(rows[:max_rows], start=1):
        # Safely extract some columns with fallback
        name = row[1] if len(row) > 1 else ""
        category = row[2] if len(row) > 2 else ""
        points = row[3] if len(row) > 3 else ""

        lines.append(f"| {idx} | {name} | {category} | {points} |")

    if len(rows) > max_rows:
        lines.append(f"\n_…and {len(rows) - max_rows} more rows_")

    return "\n".join(lines)


def send_to_webhook(content: str):
    if not WEBHOOK_URL:
        print("WEBHOOK_URL not set; printing message instead:\n")
        print(content)
        return

    # Discord-compatible JSON payload; adapt if you’re using Slack/etc.
    payload = {"content": content}
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=20)
    resp.raise_for_status()


def main():
    storage_state_path = write_storage_state_file()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()

        rows, debug = scrape_rows(page)

        md_table = rows_to_markdown(rows)

        message = textwrap.dedent(
            f"""
            ZwiftPower snapshot:
            {TARGET_URL}

            ```md
            {md_table}
            ```

            Debug:
            - Title: {debug.get('title')}
            - URL: {debug.get('url')}
            - Rows found: {debug.get('rows_found', 'n/a')}
            """
        ).strip()

        send_to_webhook(message)

        context.close()
        browser.close()


if __name__ == "__main__":
    main()