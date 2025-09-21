import os, base64, hashlib, sys, time, re, json
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# --- Required env ---
TARGET_URL = os.environ.get("TARGET_URL", "").strip()
STORAGE_STATE_B64 = os.environ.get("STORAGE_STATE_B64", "").strip()

# --- Optional env (any combination works) ---
GENERIC_WEBHOOK = os.environ.get("WEBHOOK_URL", "").strip()           # treated like Discord payload
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "").strip()

# --- Local cache files (persisted via Actions cache) ---
CACHE_FILE = "last_hash.txt"
SNAPSHOT_HTML = "last_table.html"
SNAPSHOT_JSON = "last_table.json"
STORAGE_STATE_FILE = "storage_state.json"

if not TARGET_URL:
    print("ERROR: TARGET_URL env var is required"); sys.exit(1)
if not STORAGE_STATE_B64:
    print("ERROR: STORAGE_STATE_B64 secret missing."); sys.exit(1)
if not (GENERIC_WEBHOOK or DISCORD_WEBHOOK or SLACK_WEBHOOK):
    print("WARNING: No webhook set (WEBHOOK_URL or DISCORD_WEBHOOK_URL or SLACK_WEBHOOK_URL).")

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
            r = requests.post(url, json=payload_discord, timeout=15); r.raise_for_status()
            print(f"✅ Sent to {name}"); sent = True
        except Exception as e:
            print(f"❌ Failed to send to {name}: {e}")
    if SLACK_WEBHOOK:
        try:
            r = requests.post(SLACK_WEBHOOK, json={"text": plain_msg}, timeout=15); r.raise_for_status()
            print("✅ Sent to Slack"); sent = True
        except Exception as e:
            print(f"❌ Failed to send to Slack: {e}")
    if not sent: print("(No webhooks delivered)")

def get_first_present_html(page, selectors):
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

def parse_table(html: str):
    """
    Returns: headers (list), rows (list of dict), and a key for each row (prefer ZID from link, else Name).
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        # try to salvage
        text = html_to_text(html)
        lines = [l for l in text.splitlines() if l.strip()]
        headers = lines[0].split() if lines else []
        rows = []
        return headers, rows

    # Headers
    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    if not headers:
        first_row = table.select_one("tr")
        if first_row:
            headers = [c.get_text(strip=True) for c in first_row.find_all(["th","td"])]

    # Body rows
    data_rows = []
    body_rows = table.select("tbody tr") or table.select("tr")[1:]
    for tr in body_rows:
        # get name cell link (ZID) if present
        name_link = tr.find("a", href=True)
        zid = ""
        if name_link and "profile.php?z=" in name_link["href"]:
            # typical pattern: ...profile.php?z=123456
            zid = name_link["href"].split("z=")[-1].split("&")[0]
        # build cells list
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td","th"])]
        # map to dict
        row = {}
        for i, val in enumerate(cells):
            key = headers[i] if i < len(headers) and headers[i] else f"col_{i+1}"
            row[key] = val
        # keep also raw name and zid if useful
        if zid and "ZID" not in row: row["ZID"] = zid
        if name_link and "Name" in row: row["Name"] = row["Name"]
        data_rows.append(row)

    return headers, data_rows

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
    return md.strip() or "(no rows)";

def rank_arrow(old_rank: str, new_rank: str) -> str:
    # normalize ranks like "12", "#12", "12." etc.
    def to_int(s):
        s = re.sub(r"[^\d]", "", s or "")
        return int(s) if s.isdigit() else None
    o, n = to_int(old_rank), to_int(new_rank)
    if o is None or n is None: return "→"
    if n < o: return "↑"
    if n > o: return "↓"
    return "→"

def diff_rows(prev_rows, curr_rows, key_fields=("ZID","Name"), compare_fields=("Rank","Status","20m w/kg","20m power","15s w/kg","15s power")):
    """
    Returns dict with added, removed, changed lists.
    Key is ZID if present, else Name.
    """
    def key_for(r):
        for k in key_fields:
            v = r.get(k, "").strip()
            if v: return (k, v)
        # fallback: whole name-less row string
        return ("_row", json.dumps(r, sort_keys=True))

    prev_map = {key_for(r): r for r in prev_rows}
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
                    if f == "Rank":
                        arrow = rank_arrow(ov, nv)
                        changes[f] = f"{ov} {arrow} {nv}"
                    else:
                        changes[f] = f"{ov} → {nv}"
            if changes:
                label = r.get("Name") or r.get("ZID") or "(unknown)"
                changed.append((label, changes))

    for k, r in prev_map.items():
        if k not in curr_map:
            removed.append(r)

    return {"added": added, "removed": removed, "changed": changed}

def build_diff_markdown(url, headers, rows, prev_rows):
    if prev_rows is None:
        # first run: show snapshot table
        md_table = table_to_markdown(headers, rows, max_cols=6, max_rows=15)
        md = f"ZwiftPower initial snapshot:\n{url}\n\n```md\n{md_table}\n```"
        text = f"ZwiftPower initial snapshot:\n{url}\n\n{md_table}"
        return md, text

    d = diff_rows(prev_rows, rows)
    parts_md = [f"ZwiftPower change detected:\n{url}"]
    parts_text = [f"ZwiftPower change detected:\n{url}"]

    if d["added"]:
        lines = []
        for r in d["added"][:15]:
            nm = r.get("Name") or r.get("ZID") or "(unknown)"
            rk = r.get("Rank","")
            lines.append(f"➕ {nm}  {rk}".rstrip())
        parts_md.append("\n**Added**\n```\n" + "\n".join(lines) + "\n```")
        parts_text.append("\nAdded\n" + "\n".join(lines))

    if d["removed"]:
        lines = []
        for r in d["removed"][:15]:
            nm = r.get("Name") or r.get("ZID") or "(unknown)"
            rk = r.get("Rank","")
            lines.append(f"➖ {nm}  {rk}".rstrip())
        parts_md.append("\n**Removed**\n```\n" + "\n".join(lines) + "\n```")
        parts_text.append("\nRemoved\n" + "\n".join(lines))

    if d["changed"]:
        lines = []
        for label, changes in d["changed"][:20]:
            # show Rank first if present
            rank_change = changes.pop("Rank", None)
            if rank_change:
                lines.append(f"✳️ {label}: Rank {rank_change}")
            for k, v in changes.items():
                lines.append(f"   {k}: {v}")
        parts_md.append("\n**Changed**\n```\n" + "\n".join(lines) + "\n```")
        parts_text.append("\nChanged\n" + "\n".join(lines))

    # If nothing classified (hash changed but parser couldn't diff), send compact table
    if len(parts_md) == 1:
        md_table = table_to_markdown(headers, rows, max_cols=6, max_rows=15)
        parts_md.append("\n```md\n" + md_table + "\n```")
        parts_text.append("\n" + md_table)

    md_msg = "\n".join(parts_md)
    text_msg = "\n".join(parts_text)
    # keep under platform limits
    if len(md_msg) > 1900: md_msg = md_msg[:1900] + "\n...(truncated)..."
    if len(text_msg) > 3500: text_msg = text_msg[:3500] + "\n...(truncated)..."
    return md_msg, text_msg

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
        context = browser.new_context(storage_state=STORAGE_STATE_FILE)
        page = context.new_page()
        page.goto(TARGET_URL, wait_until="networkidle", timeout=60000)
        time.sleep(2.0)

        html = get_first_present_html(page, watched_selectors)
        h = hashlib.sha256(html.encode("utf-8")).hexdigest()
        last = open(CACHE_FILE, "r", encoding="utf-8").read().strip() if os.path.exists(CACHE_FILE) else ""

        headers, rows = parse_table(html)
        prev_rows = None
        if os.path.exists(SNAPSHOT_JSON):
            try:
                prev_rows = json.load(open(SNAPSHOT_JSON, "r", encoding="utf-8"))
            except Exception:
                prev_rows = None

        if h != last:
            # Save full HTML + parsed rows
            open(SNAPSHOT_HTML, "w", encoding="utf-8").write(html)
            json.dump(rows, open(SNAPSHOT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

            md_msg, text_msg = build_diff_markdown(TARGET_URL, headers, rows, prev_rows)
            notify(markdown_msg=f"{md_msg}", plain_msg=f"{text_msg}")

            open(CACHE_FILE, "w", encoding="utf-8").write(h)
            print("Change detected. Hash & snapshots updated.")
        else:
            print("No change.")

        browser.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FATAL: {e}")
        sys.exit(1)
