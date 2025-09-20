
# ZwiftPower Monitor (Beginner-Friendly Kit)

This kit gives you an **always-on** monitor for a **logged-in ZwiftPower page** using Playwright on **GitHub Actions**.
It will detect **changes** on the page (like table updates) and **notify** you via a webhook (Discord or Slack).

Designed for **Windows + Command Prompt (cmd)** users.

---

## 1) Unzip and open this folder

Unzip this kit and open a Command Prompt in the unzipped folder (e.g. `Documents\ZwiftPowerMonitor`).

**Open Command Prompt in this folder:**
- In File Explorer, click into the address bar, type `cmd` and press **Enter**.

You should see your prompt like:
```
C:\Users\<YourName>\Documents\ZwiftPowerMonitor>
```

---

## 2) Install Playwright locally (one-time)
```
pip install playwright
playwright install
```

If `pip` is not found, install Python from https://www.python.org/downloads/ (tick "Add to PATH" during install).

---

## 3) Capture your ZwiftPower login cookies

Run:
```
python save_storage_state.py
```

- A real browser opens.
- Log into **ZwiftPower** with your Zwift account.
- When you can see your logged-in dashboard, return to the Command Prompt and press **Enter**.
- You should now see `storage_state.json` created in this folder.

---

## 4) Convert cookies file to base64 (cmd-friendly)

Run:
```
python make_b64.py
```

This creates `storage_state.b64`. Open it in Notepad and **copy everything** (Ctrl+A, Ctrl+C).

---

## 5) Create a new GitHub repo and add Secrets

1. Create a **new GitHub repository** (e.g., `zwiftpower-monitor`). Push these files to it later.
2. In the repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

- **Name:** `TARGET_URL`  
  **Value:** The exact ZwiftPower page you want to watch (example: `https://zwiftpower.com/your/target/page`)

- **Name:** `WEBHOOK_URL`  
  **Value:** Your Discord (or Slack) webhook URL  
  - **Discord:** Server Settings → Integrations → Webhooks → New Webhook → Copy URL  
  - **Slack:** Use an Incoming Webhook App and copy its URL

- **Name:** `STORAGE_STATE_B64`  
  **Value:** Paste the entire contents of `storage_state.b64`

Click **Add secret** for each one.

> You can add more target URLs later by duplicating the workflow and using additional secrets like `TARGET_URL_2`.

---

## 6) Put these files in your repo

- Copy all files from this kit into your repo (keep the `.github/workflows/check.yml` path).
- Commit and push.

---

## 7) Run the monitor

- Go to **Actions** tab in your GitHub repo.
- Select **ZwiftPower Monitor** workflow.
- Click **Run workflow** to test immediately.
- It will also run **every 15 minutes** by default.

If the watched content changes, you’ll receive a webhook message.

---

## 8) Adjust what counts as a “change”

Open `monitor.py` and adjust this list of CSS selectors to best match your target table first:

```python
watched_selectors = [
    "table#results",        # try this first if present
    "table.dataTable",      # common on ZwiftPower
    "#events_results_table",
    "div#content table",
    "table",                # fallback (noisy)
]
```

The more specific the selector (an exact table id/class), the fewer false positives.

---

## 9) When login expires

Eventually, your Zwift/SSO session will expire. Just repeat steps **3** and **4**, then update the `STORAGE_STATE_B64` secret with the new value.

---

## Troubleshooting

- **`pip` not found**: Install Python from python.org and re-open Command Prompt.
- **Playwright browser missing**: Run `playwright install`.
- **No webhook sent**: Check that your `WEBHOOK_URL` is correct and the channel allows messages from the webhook.
- **No changes detected**: Verify `TARGET_URL` is correct and selector list in `monitor.py` matches the page’s table.

Happy monitoring! 🚴‍♂️
