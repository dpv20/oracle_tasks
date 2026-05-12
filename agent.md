# AGENT.md — Read this first

You are a agent working on **Oracle Tasks Chile**, a Windows desktop app in Python that automates Oracle DBA tasks for the user's team (Diego Pavez Verdi). The first automated task is generating account spools from production databases and applying them to QA/DEV.

## Before doing anything, read these files in order:

1. **`implementation_plan.md`** — the full plan: stack, architecture, repo structure, install flow, implementation phases. This is the source of truth on **what** we are building and **why**. Read it end-to-end, especially §0 (mandatory tracking rule) and §14 (phases).

2. **`progress.md`** — chronological log (most recent entries on top) of what has been done, what problems came up, and what decisions were made along the way. It tells you **where things stand right now**. Start working from the first task in §14 of the plan that does **not** appear as ✅ in `progress.md`.

3. (Optional, only if you lack context on the original manual flow) `cuenta_prod_a_QA.txt` and the `spools/CL_ACCOUNT_SPOOL_*.sql` files — reference material describing the manual workflow we are automating.

## Non-negotiable rules

- **Everything you do must be logged in `progress.md`.** Each completed plan step, each subtask, each problem, each non-trivial technical decision. Format and icon convention live in §0 of the plan. This is the only thing that gives continuity across sessions.

- **Do not make large architectural decisions without asking the user first.** If you find that something in the plan does not match reality, first document the discovery in `progress.md` with `⚠️`, then ask the user before changing the plan.

- **Languages:** UI is bilingual EN/ES (default EN, switch in Settings). SQLcl/Oracle error messages are left raw in English. The user writes to you in Spanish; reply in Spanish.

- **Do not rewrite the user's original `.sql` scripts.** They are versioned as `.sql.tmpl` with a `{{SPOOL_OUT_DIR}}` placeholder that the app fills in on the fly to a temp copy. Reason: respect code that already works, and let the output dir be configurable.

- **Install pattern = exact copy of the user's `vpn` app**, located at `c:\Users\Diego Pavez\Desktop\Oracle\varios\vpn\`. If you have any doubt about `install.bat`, `update.bat`, single-instance guard, AppUserModelID, DPAPI, etc., **read those files first before inventing**. The `vpn` app is the proven reference.

- **Credentials:** prod credentials are shared across the team, QA credentials are per-user. Mandatory support for proxy auth `user[schema]/pass@DB` (SQLcl syntax). Passwords are encrypted with DPAPI before touching disk.

- **SQLcl:** detection is a cascade (PATH → common locations → interactive menu). Do NOT auto-download without asking — the current user already has SQLcl on PATH.

## User context

- Diego Pavez Verdi, DBA at BICE/Falabella (Chile), works with Oracle DBs for Chile, Peru, Colombia and Mexico.
- He already built the `vpn` app on the same pattern we're copying — always reference it before inventing.
- The project lives at `c:\Users\Diego Pavez\Desktop\Oracle\varios\Spool_maker\` (working directory).
- GitHub repo: `https://github.com/dpv20/oracle_tasks`.

## When you finish something

1. Update `progress.md` with today's entry.
2. If you finished a whole phase of the plan, mark its verification milestone as run (or note that it could not be tested and why).
3. Suggest the next logical step to the user according to §14 of the plan.
