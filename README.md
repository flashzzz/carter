# Grocery Cart Telegram Bot

Drop grocery items into a Telegram chat; the bot accumulates a shared list and,
on `link`, drives Blinkit's web store (Playwright) to build a cart and hand back
a **shareable cart link** you open on your phone. Payment is always manual.

> ⚠️ **Read this.** Blinkit has no public API. Automating it likely violates its
> Terms of Service. Keep this private, low volume, human-paced. Never automate
> payment — the bot stops at "here's the cart link."

## No login required 🎉

Blinkit only needs a **delivery location** (a pincode) to browse, add to cart,
and share — **no phone/OTP/account**. The bot uses a fresh guest session per
build, sets the configured pincode, adds your items, and calls Blinkit's own
cart **Share** control to mint a link (`https://link.blinkit.com/bln/<code>`).
Opening that link on a phone launches the Blinkit app showing the cart, so
anyone can review and pay — no shared account needed.

This removes all the usual friction: no OTP seeding, no session expiry, no
stored credentials, no persistent volume.

## Commands

| Message                      | Action                                                    |
|------------------------------|-----------------------------------------------------------|
| any normal message           | add item (`2x milk`, `milk 2`, `2 milk` → qty 2), reacts 👍|
| `link`                       | build the cart, reply with the shareable link + summary   |
| `list`                       | show the current list (DB only)                           |
| `remove <text>`              | soft-delete matching items                                |
| `reset` / `ordered` / `done` | clear the list                                            |
| tap a button                 | pick a product when an item is ambiguous                  |
| `chatid`                     | reply the chat id (works before allowlisting)             |

## Files

| File                 | Role |
|----------------------|------|
| `bot.py`             | Telegram long-poll, parser, router, queue producer, button callbacks, health server |
| `worker.py`          | Single serialized Playwright consumer (fresh guest per build) |
| `db.py`              | Postgres schema + all queries (async, psycopg 3) |
| `store_selectors.py` | **All** Blinkit URLs, selectors, and flow (the one file UI changes touch) |
| `session_check.py`   | Cron health canary: sets location + searches, DMs admin if the guest flow breaks |
| `config.py` / `util.py` | env config / shared helpers |

> The module is `store_selectors.py`, **not** `selectors.py` — the latter shadows
> a Python stdlib module and crashes anything using `asyncio`.

## Config (env / secrets)

| Var                 | Meaning |
|---------------------|---------|
| `TELEGRAM_TOKEN`    | from @BotFather (and disable privacy mode so the bot sees group messages) |
| `DATABASE_URL`      | Postgres connection string |
| `PLATFORM`          | `blinkit` (Zepto is a stubbed skeleton) |
| `BLINKIT_LOCATION`  | delivery **pincode** or locality, e.g. `411057` |
| `ALLOWED_CHAT_IDS`  | comma-separated chat ids allowed to drive the bot (empty = all; dev only) |
| `ADMIN_CHAT_ID`     | where health-check alerts are DM'd (optional) |
| `HEADLESS`          | `false` locally to watch the browser; `true` in the container |

## Local run

Requires Python 3.10+ (the container image is 3.10).

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # only needed locally; the image ships it
cp .env.example .env                 # fill TELEGRAM_TOKEN, DATABASE_URL, BLINKIT_LOCATION
python bot.py
```

Send `chatid` to the bot to learn your chat id, then set `ALLOWED_CHAT_IDS`.

## Northflank deploy

1. **Project** — create one (free sandbox tier).
2. **Postgres addon** — add the free DB, copy its connection string → `DATABASE_URL`.
3. **Service** — create a *combined/deployment* service from this Git repo
   (Dockerfile build). No public ingress needed. **No persistent volume needed**
   (there's no session to store).
4. **Env / secrets**: `TELEGRAM_TOKEN`, `DATABASE_URL`, `PLATFORM=blinkit`,
   `BLINKIT_LOCATION`, `ALLOWED_CHAT_IDS`, `ADMIN_CHAT_ID`.
5. **Cron job** (optional) — `python session_check.py` every few hours; DMs you
   if Blinkit's UI changes and the guest flow breaks.
6. **Deploy.** The bot long-polls; watch logs for `bot ready`.

> Confirm current free-tier egress terms at signup — tiny for this workload.

## Design notes

- **One shared browser flow ⇒ serialized work.** A single `asyncio.Queue`
  consumer runs one job at a time; concurrent "add" messages are just DB writes.
- **Fresh guest context per `link`** ⇒ the cart always starts empty and is
  rebuilt from the whole list, so the cart == the list (idempotent, no drift).
- **Ambiguous items** become inline-keyboard buttons labelled with the real
  product names; tapping one resolves it and is remembered as an alias.
- **The returned link** is Blinkit's own shared-cart deep link, from the cart's
  Share control (`POST /v1/assist/cart/share`).
- Every browser step is time-bounded and each build has a hard `BUILD_TIMEOUT`,
  so a wedged browser can never hang the worker.

## Tested

`db.py` is verified end-to-end against a real Postgres; parser, summary, and the
full Blinkit guest flow (set location → add with quantities → ambiguous buttons →
share link) are verified against the live site. The Telegram round-trip runs
locally against a real bot token.
