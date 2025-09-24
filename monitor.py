import os, base64, hashlib, sys, time, re, json
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# === Required env ===
TARGET_URL        = os.environ.get("TARGET_URL", "").strip()
STORAGE_STATE_B64 = os.environ.get("STORAGE_STATE_B64", "").strip()

# === Optional env ===
# If you know the exact table on your page, set secret TARGET_SELECTOR, e.g. "table#team_riders"
TARGET_SELECTOR   = os.environ.get("TARGET_SELECTOR", "").strip()

# Any of these can be set; message will be sent to all that exist
GENERIC_WEBHOOK   = os.environ.get("WEBHOOK_URL", "").strip()         # Discord-style
DISCORD_WEBHOOK   = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
SLACK_WEBHOOK     = os.environ.get("SLACK_WEBHOOK_URL", "").strip()

# === Local files (persist via Actions cache for last_hash.txt) ===
CACHE_FILE          = "last_hash.txt"
SNAPSHOT_HTML       = "last_table.html"
SNAPSHOT_JSON       = "last_table.json"
STORAGE_STATE_FILE  = "storage_state.json"

# === Guards ===
if not TARGET_URL:
    print("ERROR: TARGET_URL env var is required"); sys.exit(1)
if not STORAGE_STATE_B64:
    print("ERROR: STORAGE_STATE_B64 secret missing."); sys.exit(1)
if not (GENERIC_WEBHOOK or DISCORD_WEBHOOK or SLACK_WEBHOOK):
    print("WARNING: No webhook set (WEBHOOK_URL / DISCORD_WEBHOOK_URL / SLACK_WEBHOOK_URL).")

def write_storage_state():
    with open(STORAGE_STATE_FILE, "wb") as f:
        f.write(base64.b64decode(STORAGE_STATE_B64))

def html_to_text(html: str) -> str:
    html = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", html)
    html = re.sub(r"(?i)</\s*p\s*>", "\n", html)
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

def notify(markdown_msg: str, plain_msg: str):
    sent = False
    payload_discord = {"content": markdown_msg}
    for name, url in (("Generic", GENERIC_WEBHOOK), ("Discord", DISCORD_WEBHOOK)):
        if not url: continue
        try:
            r = requests.post(url, json=payload_discord, timeout=15)
            r.raise_for_status()
            print(f"✅ Sent to {name}"); sent = True
        except Exception as e:
            print(f"❌ Failed to send to {name}: {e}")
    if SLACK_WEBHOOK:
        try:
            r = requests.post(SLACK_WEBHOOK, json={"text": plain_msg}, timeout=15)
            r.raise_for_status()
            print("✅ Sent to Slack"); sent = True
        except Exception as e:
            print(f"❌ Failed to send to Slack: {e}")
    if not sent:
        print("(No webhooks delivered)")

def get_table_html_with_rows(page, selectors, extra_wait_s=1.5):
    """
    Try each selector. For the first visible table, wait until it has at least one <tbody><tr>,
    (and if DataTables is present, wait for the processing overlay to finish), then return innerHTML.
    Falls back to the first visible table's HTML if no rows appear.
    """
    first_visible_html = None
    for sel in selectors:
        try:
            page.locator(sel).first.wait_for(state="visible", timeout=30000)
            if first_visible_html is None:
                first_visible_html = page.locator(sel).first.inner_html()

            # If it's a DataTable, wait for possible "processing" overlay to hide
            if sel.startswith("table#"):
                table_id = sel.split("#", 1)[1]
                proc = page.locator(f"#{table_id}_processing")
                try:
                    proc.wait_for(state="visible", timeout=3000)
                    proc.wait_for(state="hidden", timeout=30000)
                except:
                    pass  # overlay might never show; that's fine

            # Wait for rows to actually exist
            page.wait_for_function(
                """(sel) => {
                    const t = document.querySelector(sel);
                    if (!t) return false;
                    const rows = t.querySelectorAll('tbody tr');
                    return rows && rows.length > 0;
                }""",
                arg=sel,
                timeout=30000
            )

            time.sleep(extra_wait_s)  # give AJAX a moment to finish
            return page.locator(sel).first.inner_html()
        except Exception:
            continue
    return first_visible_html or page.locator("body").inner_html()

def stabilise_html(html: str) -> str:
    """Strip volatile attributes so hash only changes on real content changes."""
    s = html
    s = re.sub(r'id="[^"]+"', '', s)
    s = re.sub(r'data-[a-zA-Z0-9\-]+="[^"]+"', '', s)
    s = re.sub(r'datetime="[^"]+"', '', s)
    s = re.sub(r'time="[^"]+"', '', s)
    s = re.sub(r'\s{2,}', ' ', s)
    return s.strip()

def parse_table(html: str):
    """Return (headers, rows[list of dict]). Includes ZID if present in a profile link."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return [], []

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    if not headers:
        first_row = table.select_one("tr")
        if first_row:
            headers = [c.get_text(strip=True) for c in first_row.find_all(["th","td"])]

    rows = []
    body_rows = table.select("tbody tr") or table.select("tr")[1:]
    for tr in body_rows:
        # ZID from profile link if present
        name_link = tr.find("a", href=True)
        zid = ""
        if name_link and "profile.php?z=" in name_link["href"]:
            zid = name_link["href"].split("z=")[-1].split("&")[0]
html_msg = f"ZwiftPower change detected:\n{TARGET_URL}\n```html\n{html_snippet}\n```"
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td","th"])]
        row = {}
        for i, val in enumerate(cells):
            key = headers[i] if i < len(headers) and headers[i] else f"col_{i+1}"
            row[key] = val
        if zid: row["ZID"] = zid
        rows.append(row)

    return headers, rows

def table_to_markdown(headers, rows, max_cols=6, max_rows=15):
    headers = headers[:max_cols]
    short_rows = []
    for r in rows[:max_rows]:
        short_rows.append([str(r.get(h, "")) for h in headers])
    md = ""
    if headers:
        md += " | ".join(headers) + "\n" + " | ".join(["---"]*len(headers)) + "\n"
    for r in short_rows:
        md += " | ".join(r) + "\n"
    return md.strip() or "(no rows)"

def rank_arrow(old_rank: str, new_rank: str) -> str:
    def to_int(s):
        s = re.sub(r"[^\d]", "", s or "")
        return int(s) if s.isdigit() else None
    o, n = to_int(old_rank), to_int(new_rank)
    if o is None or n is None: return "→"
    if n < o: return "↑"
    if n > o: return "↓"
    return "→"

def diff_rows(prev_rows, curr_rows,
              key_fields=("ZID","Name"),
              compare_fields=("Rank","Status","20m w/kg","20m power","15s w/kg","15s power")):
    def key_for(r):
        for k in key_fields:
            v = r.get(k, "").strip()
            if v: return (k, v)
        return ("_row", json.dumps(r, sort_keys=True))

    prev_map = {key_for(r): r for r in prev_rows} if prev_rows else {}
    curr_map = {key_for(r): r for r in curr_rows}

    added, removed, changed = [], [], []

    for k, r in curr_map.items():
        if k not in prev_map:
            added.append(r)
        else:
            old = prev_map[k]
            changes = {}
            for f in compare_fields:
                ov, nv = old.get(f, ""), r.get(f, "")
                if ov != nv:
                    changes[f] = (ov, nv)
            if changes:
                label = r.get("Name") or r.get("ZID") or "(unknown)"
                if "Rank" in changes:
                    ov, nv = changes.pop("Rank")
                    changes = {"Rank": f"{ov} {rank_arrow(ov,nv)} {nv}",
                               **{k: f"{ov} → {nv}" for k,(ov,nv) in changes.items()}}
                else:
                    changes = {k: f"{ov} → {nv}" for k,(ov,nv) in changes.items()}
                changed.append((label, changes))

    for k, r in prev_map.items():
        if k not in curr_map:
            removed.append(r)

    return {"added": added, "removed": removed, "changed": changed}

def build_messages(url, headers, rows, prev_rows):
    if prev_rows is None:
        md_table = table_to_markdown(headers, rows, max_cols=6, max_rows=15)
        md = f"ZwiftPower initial snapshot:\n{url}\n\n```md\n{md_table}\n```"
        txt = f"ZwiftPower initial snapshot:\n{url}\n\n{md_table}"
        return md, txt

    d = diff_rows(prev_rows, rows)
    parts_md = [f"ZwiftPower change detected:\n{url}"]
    parts_txt = [f"ZwiftPower change detected:\n{url}"]

    if d["added"]:
        lines = []
        for r in d["added"][:15]:
            nm = r.get("Name") or r.get("ZID") or "(unknown)"
            rk = r.get("Rank","")
            lines.append(f"➕ {nm}  {rk}".rstrip())
        parts_md.append("\n**Added**\n```\n" + "\n".join(lines) + "\n```")
        parts_txt.append("\nAdded\n" + "\n".join(lines))

    if d["removed"]:
        lines = []
        for r in d["removed"][:15]:
            nm = r.get("Name") or r.get("ZID") or "(unknown)"
            rk = r.get("Rank","")
            lines.append(f"➖ {nm}  {rk}".rstrip())
        parts_md.append("\n**Removed**\n```\n" + "\n".join(lines) + "\n```")
        parts_txt.append("\nRemoved\n" + "\n".join(lines))

    if d["changed"]:
        lines = []
        for label, changes in d["changed"][:20]:
            rank_change = changes.pop("Rank", None)
            if rank_change:
                lines.append(f"✳️ {label}: Rank {rank_change}")
            for k, v in changes.items():
                lines.append(f"   {k}: {v}")
        parts_md.append("\n**Changed**\n```\n" + "\n".join(lines) + "\n```")
        parts_txt.append("\nChanged\n" + "\n".join(lines))

    if len(parts_md) == 1:
        md_table = table_to_markdown(headers, rows, max_cols=6, max_rows=15)
        parts_md.append("\n```md\n" + md_table + "\n```")
        parts_txt.append("\n" + md_table)

    md_msg = "\n".join(parts_md)
    txt_msg = "\n".join(parts_txt)
    if len(md_msg) > 1900: md_msg = md_msg[:1900] + "\n...(truncated)..."
    if len(txt_msg) > 3500: txt_msg = txt_msg[:3500] + "\n...(truncated)..."
    return md_msg, txt_msg

def main():
    write_storage_state()

    # Build selector priority (prefer your explicit selector if provided)
    watched_selectors = []
    if TARGET_SELECTOR:
        watched_selectors.append(TARGET_SELECTOR)
    watched_selectors += [
        "table#team_riders",        # common on team pages
        "table#events_results_table",
        "table#results",
        "table.dataTable",
        "div#content table",
        "table",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STORAGE_STATE_FILE)
        page = context.new_page()
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        time.sleep(2.0)

        html = get_table_html_with_rows(page, watched_selectors)

        # DEBUG: count rows found
        try:
            _rows = len(BeautifulSoup(html, "html.parser").select("tbody tr"))
            print(f"DEBUG: extracted table rows = {_rows}")
        except Exception:
            pass

        # Stabilise before hashing so runs don't spam
        stable = stabilise_html(html)
        current_hash = hashlib.sha256(stable.encode("utf-8")).hexdigest()
        last_hash = open(CACHE_FILE, "r", encoding="utf-8").read().strip() if os.path.exists(CACHE_FILE) else ""

        # Parse for diffing/messages
        headers, rows = parse_table(html)
        prev_rows = None
        if os.path.exists(SNAPSHOT_JSON):
            try:
                prev_rows = json.load(open(SNAPSHOT_JSON, "r", encoding="utf-8"))
            except Exception:
                prev_rows = None

        if current_hash != last_hash:
            # Save snapshots
            open(SNAPSHOT_HTML, "w", encoding="utf-8").write(html)
            json.dump(rows, open(SNAPSHOT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            open(CACHE_FILE, "w", encoding="utf-8").write(current_hash)

            md_msg, txt_msg = build_messages(TARGET_URL, headers, rows, prev_rows)
            notify(markdown_msg=md_msg, plain_msg=txt_msg)
            print("Change detected. Stable hash & snapshots updated.")
        else:
            print("No change.")

        browser.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
