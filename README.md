# Oracle Tasks Chile

Desktop app (Windows) to automate Oracle DBA tasks for the team. First task: extract account spools from PROD databases and apply them to QA/DEV.

> Internal tool. See `implementation_plan.md` for design and `progress.md` for current status.

---

## For end users — install

1. Download `install.bat` from this repo and run it (double-click).
2. The installer will:
   - Install Python 3.12 if missing (per-user, no admin)
   - Install Git if missing (per-user, no admin)
   - Clone the app to `%LOCALAPPDATA%\OracleTasksChile\app`
   - Detect SQLcl (PATH → common locations → menu prompt to enter path or download)
   - Install Python dependencies
   - Create a desktop shortcut
3. Launch from the desktop shortcut "Oracle Tasks Chile".
4. First time: open **Settings → Credentials**, paste your `user/pass@DB` lines (one per line, supports proxy auth `user[schema]/pass@DB`), click "Parse & save".

## Update

The app checks for updates on launch. If a new version is available, a banner appears at the top — click it and the app updates itself via `update.bat`.

## Uninstall

Run `uninstall.bat` from `%LOCALAPPDATA%\OracleTasksChile\app\`.

---

## For developers

Repo: https://github.com/dpv20/oracle_tasks

```
git clone https://github.com/dpv20/oracle_tasks.git
cd oracle_tasks
pip install -r requirements.txt
python src/main.py
```

Read `implementation_plan.md` for architecture. Always log work in `progress.md` — see §0 of the plan for the rule.
