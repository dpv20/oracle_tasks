# Oracle Tasks Chile

Desktop app (Windows) to automate Oracle DBA tasks for the team — currently
account spool extraction (PROD → QA/DEV) for Chile, Peru, Colombia and Mexico.

---

## 1. Install

1. Download `install.bat` from this repo and run it (double-click).
2. The installer will:
   - Install **Python 3.12** if missing (per-user, no admin).
   - Install **Git** if missing (per-user, no admin).
   - Clone the app to `%LOCALAPPDATA%\OracleTasksChile\app`.
   - Detect **SQLcl** (PATH → common locations → menu prompt if not found).
   - Install Python dependencies.
   - Create desktop and Start Menu shortcuts **"Oracle Tasks Chile"**.
3. Launch the app from that shortcut.
4. To pin it, pin **Oracle Tasks Chile** from the Start Menu shortcut.

> The app updates itself: when a new version lands on `main`, a banner appears
> at the top of the home screen — click it and the app installs the update.

---

## 2. Configure SQLcl

Settings → **General** → field **"SQLcl path (sql.exe)"**:

- Click **Auto-detect** — it searches `PATH` and common locations.
- If it doesn't find it, click **Browse...** and pick `sql.exe` manually
  (typical path: `C:\Users\<you>\Desktop\sqlcl\sqlcl\bin\sql.exe`).
- Click **Apply** to save.

Verify with the **Test connection** button below: pick any credential and
hit Test — should report **"Connected — query returned 1"**.

---

## 3. Add credentials

Settings → **Credentials**.

Two ways:

- **Paste** (fastest): paste your `user[schema]/pass@DB` lines (e.g. the
  block from `tnsnames.ora`), click **Parse & save**. The app auto-detects
  country and environment from the TNS name.
- **Form**: pick country + environment, type user, password and TNS name.

Saved credentials appear as 4 country tiles (Chile · Peru · Colombia ·
Mexico). Click a tile to see the credentials grouped by environment
(PROD / QA / DEV / BUP) and **Edit** or **Delete** any of them.

Passwords are encrypted with Windows DPAPI on disk — readable only by your
Windows user on this machine.

---

## 4. Use it — extract account spools

Home → **Spools / CL Accounts** (Chile Loans) or **Spools / Savings
Accounts** (Savings).

1. Pick **Country**.
2. Pick **Source DB** — dropdown lists every environment of that country
   (PROD / BUP PROD / QA / BUP QA / DEV), each tagged so it's clear.
3. Type an account number, click **+ Add** (or press Enter). Repeat for
   every account you want. Each row has a red **[×]** to remove it.
4. Click **Extract spools**.
5. Watch the status rows: `⟳` running (blue) → `✓` OK (green) or `✗`
   error (red) with the last error line.
6. Click **Open spools folder** to open the destination folder in Explorer.

The resulting `.SQL` files land in:
`%LOCALAPPDATA%\OracleTasksChile\spools_CL_out\<Country>\CL_Acc_Spool_<account>.SQL`
(or `spools_savings_out\` for the savings flow).

---

## Uninstall

Run `uninstall.bat` from `%LOCALAPPDATA%\OracleTasksChile\app\`.

---

## For developers

```bat
git clone https://github.com/dpv20/oracle_tasks.git
cd oracle_tasks
pip install -r requirements.txt
python src\main.py
```
