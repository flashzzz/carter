"""
ONE-TIME manual login (run locally, NOT in the container).

    HEADLESS=false PLATFORM=blinkit python seed_session.py

Opens a real browser window at the store. You log in with your phone + OTP by
hand (the bot never touches your credentials or the OTP). When the store shows
you logged in, come back to the terminal and press Enter — the script dumps the
authenticated session to SESSION_PATH (default ./storage_state.json locally).

Then upload that file to the Northflank volume at /data/storage_state.json.
Repeat whenever session_check.py tells you the session died (~monthly).
"""
import asyncio
import os

import config
from store_selectors import get_site


async def main():
    from playwright.async_api import async_playwright

    site = get_site()
    # Default to a local path if the container default isn't writable.
    out = config.SESSION_PATH
    if out.startswith("/data") and not os.path.isdir("/data"):
        out = os.path.abspath("storage_state.json")

    print(f"Platform: {site.name}")
    print(f"Will write session to: {out}")
    print("A browser window will open. Log in with phone + OTP by hand.\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(site.base_url, wait_until="domcontentloaded")

        input("\n>>> Finish logging in, then press Enter here to capture the session... ")

        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        await context.storage_state(path=out)
        await browser.close()

    print(f"\n✅ Session saved to {out}")
    print("Upload it to the Northflank volume at", config.SESSION_PATH)


if __name__ == "__main__":
    asyncio.run(main())
