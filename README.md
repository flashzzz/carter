# Grocery Cart Telegram Bot

Drop grocery items into a Telegram group; the bot accumulates a shared list and,
on `link`, drives a **logged-in** Blinkit/Zepto web session (Playwright) to build
the cart and hand back a checkout URL. Payment is always manual.

> ⚠️ **Read this.** Blinkit/Zepto have no public API and no add-to-cart deep link.
> Automating a logged-in session almost certainly violates their Terms of Service.
> Keep this private, one dedicated account, low volume, human-paced. Never automate
> payment. This is for personal use only.

## Commands

| Message                     | Action                                            |
|-----------------------------|---------------------------------------------------|
| any normal message          | add item (`2x amul cheese` → qty 2), reacts ✅      |
| `link`                      | build the cart, reply with URL + summary          |
| `list`                      | show the current list (DB only)                   |
| `remove <text>`             | soft-delete matching items                        |
| `reset` / `ordered` / `done`| clear the list, start fresh                       |
| `1` / `2` / `3`             | answer the live "which one?" prompt               |
| `chatid`                    | reply the chat id (works before allowlisting)     |

## Files

| File                 | Role |
|----------------------|------|
| `bot.py`             | Telegram long-poll, parser, command router, job producer, health server |
| `worker.py`          | Single serialized Playwright consumer of the job queue |
| `db.py`              | Postgres schema + all queries (async, psycopg 3) |
| `store_selectors.py` | **All** site-specific URLs + CSS selectors + flow (the one file UI changes touch) |
| `session_check.py`   | Cron entrypoint: auth health check → DM admin if dead |
| `seed_session.py`    | Local one-time headed login → `storage_state.json` |
| `config.py` / `util.py` | env config / shared helpers |

> Note: the module is `store_selectors.py`, **not** `selectors.py` — the latter
> shadows a Python stdlib module and crashes anything using `asyncio`.

## What still needs YOU (cannot be automated)

1. **Telegram token** — create the bot via [@BotFather], get `TELEGRAM_TOKEN`.
   Then **disable privacy mode** (`/setprivacy` → Disable) so the bot sees all
   group messages, and add the bot to your group.
2. **Fill in the CSS selectors** in `store_selectors.py`. The search/add/cart
   flow is written; the selector constants are placeholders (`# TODO`). Get the
   real ones by running the headed seed below and inspecting the DOM in DevTools.
3. **Seed the login session** — phone + OTP, by hand (see below).

## Local setup (for seeding + selector work)

Requires Python 3.10+ (the deps do; the container image is 3.10).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # only needed locally; the image ships it
cp .env.example .env                 # fill TELEGRAM_TOKEN, DATABASE_URL, PLATFORM
```

### Seed the session (one-time, and ~monthly when it expires)

```bash
HEADLESS=false PLATFORM=blinkit python seed_session.py
# a browser opens → log in with phone + OTP → press Enter → storage_state.json written
```

## Northflank deploy

1. **Project** — create one (free sandbox tier).
2. **Postgres addon** — add the free DB, copy its connection string → `DATABASE_URL`.
3. **Service** — create a *combined/deployment* service from this Git repo
   (Dockerfile build). No public ingress needed.
4. **Volume** — attach a persistent volume mounted at `/data`. The session lives
   at `/data/storage_state.json`.
5. **Env / secrets** on the service:
   - `TELEGRAM_TOKEN`
   - `DATABASE_URL`
   - `PLATFORM` = `blinkit` or `zepto`
   - `ALLOWED_CHAT_IDS` = your group's id (send `chatid` to the bot to learn it)
   - `ADMIN_CHAT_ID` = your personal chat id (health alerts)
   - `SESSION_PATH` = `/data/storage_state.json`
6. **Cron job** — one of the free crons, command `python session_check.py`,
   every few hours. DMs you when the session dies.
7. **Seed onto the volume** — run `seed_session.py` locally, then upload the
   resulting `storage_state.json` to the volume at `/data/storage_state.json`.
8. **Deploy.** The bot long-polls; watch logs to confirm `bot ready`.

> Confirm current free-tier terms for persistent disk + egress at signup — tiny
> for this workload, but Northflank bills those AWS-style.

## Design notes

- **One shared session ⇒ serialized browser work.** A single `asyncio.Queue`
  consumer runs one job at a time; concurrent "add" messages are just DB writes.
- **`link` rebuilds the cart from scratch** every time — idempotent, no drift.
- **Aliases** are learned from disambiguation answers, so repeat items resolve
  instantly via the product URL next time.
- Each item is wrapped in try/except so one failure never kills the batch.

## Tested

`db.py` is verified end-to-end against a real Postgres, and all modules +
parsing/summary logic are import- and unit-tested. The Playwright flow and the
Telegram round-trip are **not** testable without your seeded session, live
selectors, and a real token — validate those after step 2/3 above.

[@BotFather]: https://t.me/BotFather
