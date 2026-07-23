"""
Cron entrypoint — health canary. There's no login/session to expire anymore
(guest flow), so this verifies the guest path still works end to end: set the
delivery location and confirm products render + a search returns results. DMs
the admin (and exits non-zero) if the flow is broken (e.g. Blinkit UI changed).

Run by a Northflank cron job every few hours:  python session_check.py
"""
import asyncio
import logging
import sys
import urllib.parse
import urllib.request

import config
from store_selectors import get_site

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("health_check")

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
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = await browser.new_context(
            user_agent=_UA, viewport={"width": 1280, "height": 900}
        )
        page = await context.new_page()
        try:
            if not await site.ensure_location(page, config.BLINKIT_LOCATION):
                _notify(f"📍 [{site.name}] Can't set location “{config.BLINKIT_LOCATION}”.")
                return False
            res = await site.search_and_add(page, "milk", 1)
            ok = res.status in ("added", "ambiguous")
        finally:
            await browser.close()

    if ok:
        log.info("[%s] guest flow healthy", site.name)
    else:
        _notify(f"⚠️ [{site.name}] Guest flow broken — search returned '{res.status}'. UI may have changed.")
    return ok


def main():
    ok = asyncio.run(_check())
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
