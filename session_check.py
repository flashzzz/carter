"""
Cron entrypoint. Loads the persisted session, checks it's still logged in, and
DMs the admin (and exits non-zero) if it's dead. No queue, no bot loop.

Run by a Northflank cron job every few hours:  python session_check.py
"""
import asyncio
import logging
import os
import sys
import urllib.parse
import urllib.request

import config
from store_selectors import get_site

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("session_check")

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _notify(text):
    if not config.ADMIN_CHAT_ID:
        log.warning("no ADMIN_CHAT_ID set; cannot DM: %s", text)
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": config.ADMIN_CHAT_ID, "text": text}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            r.read()
    except Exception:
        log.exception("failed to send Telegram alert")


async def _check():
    from playwright.async_api import async_playwright

    site = get_site()
    if not os.path.exists(config.SESSION_PATH):
        _notify(f"🔐 [{site.name}] No session file at {config.SESSION_PATH}. Seed it.")
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            storage_state=config.SESSION_PATH, user_agent=_UA,
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        try:
            ok = await site.ensure_logged_in(page)
        finally:
            await browser.close()

    if ok:
        log.info("[%s] session healthy", site.name)
    else:
        _notify(f"🔐 [{site.name}] Session expired — re-seed storage_state.json.")
    return ok


def main():
    ok = asyncio.run(_check())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
