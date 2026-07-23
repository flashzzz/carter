"""
Playwright worker: the single, serialized consumer of the job queue.

Only `link` and `reset` enqueue jobs. Exactly one job runs at a time (queue
depth is effectively 1 because there is one consumer and one shared session),
so the logged-in session is never used concurrently.
"""
import logging
import os

import config
import db
from store_selectors import get_site

log = logging.getLogger("worker")

# Realistic desktop fingerprint for the persisted session.
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_VIEWPORT = {"width": 1280, "height": 800}


async def worker_loop(app, queue):
    """Runs forever; pulls one job at a time off the asyncio.Queue."""
    while True:
        job = await queue.get()
        try:
            if job["type"] == "build":
                await _handle_build(app, job)
            elif job["type"] == "reset":
                await _handle_reset(app, job)
        except Exception:
            log.exception("job failed: %s", job)
            await _safe_send(app, job["chat_id"], "⚠️ Something broke while driving the browser. Try again.")
        finally:
            queue.task_done()


async def _new_context(p):
    browser = await p.chromium.launch(
        headless=config.HEADLESS,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    storage = config.SESSION_PATH if os.path.exists(config.SESSION_PATH) else None
    context = await browser.new_context(
        storage_state=storage, user_agent=_UA, viewport=_VIEWPORT
    )
    return browser, context


async def _handle_build(app, job):
    from playwright.async_api import async_playwright

    group_id = job["group_id"]
    chat_id = job["chat_id"]
    site = get_site()

    async with async_playwright() as p:
        browser, context = await _new_context(p)
        page = await context.new_page()
        try:
            if not await site.ensure_logged_in(page):
                await db.set_cart_status(group_id, "open")
                await _alert_session_dead(app, chat_id)
                return

            items = await db.get_pending_items(group_id)
            added, not_found, ambiguous, errored = [], [], [], []

            for item in items:
                from util import normalize_phrase

                phrase = normalize_phrase(item["raw_text"])
                alias = await db.get_alias(group_id, phrase)

                if alias:
                    res = await site.add_by_product_id(
                        page, alias["product_id"], alias["product_name"]
                    )
                else:
                    res = await site.search_and_add(page, phrase)

                await _apply_result(group_id, item, res, phrase)

                if res.status == "added":
                    added.append(res.product_name or item["raw_text"])
                elif res.status == "ambiguous":
                    ambiguous.append(item)
                elif res.status == "not_found":
                    not_found.append(item["raw_text"])
                else:
                    errored.append(item["raw_text"])

            cart_url = await site.open_cart(page)
            await context.storage_state(path=config.SESSION_PATH)  # refresh cookies
            await db.set_cart_status(group_id, "open")

            summary = _format_summary(cart_url, added, not_found, ambiguous, errored)
            await _safe_send(app, chat_id, summary)
        finally:
            await browser.close()


async def _handle_reset(app, job):
    """Clear the DB list; live-cart emptying is best-effort."""
    await db.clear_cart(job["group_id"])
    await _safe_send(app, job["chat_id"], "🧹 Fresh list started. Drop items whenever.")


async def _apply_result(group_id, item, res, phrase):
    if res.status == "added":
        await db.set_item_status(item["id"], "added", res.product_id, res.product_name)
        if res.product_id:
            await db.upsert_alias(group_id, phrase, res.product_id, res.product_name)
    elif res.status == "ambiguous":
        await db.set_item_status(item["id"], "ambiguous", candidates=res.candidates)
    elif res.status == "not_found":
        await db.set_item_status(item["id"], "not_found")
    else:
        await db.set_item_status(item["id"], "error")
        log.warning("resolve error for %r: %s", item["raw_text"], res.note)


def _format_summary(cart_url, added, not_found, ambiguous, errored):
    lines = [f"🛒 Cart ready: {cart_url}", ""]
    if added:
        lines.append(f"✅ Added ({len(added)}): " + ", ".join(added))
    if not_found:
        lines.append(f"❌ Not found ({len(not_found)}): " + ", ".join(not_found))
    if errored:
        lines.append(f"⚠️ Errored ({len(errored)}): " + ", ".join(errored))
    if ambiguous:
        first = ambiguous[0]
        cands = first.get("candidates") or []
        opts = "\n".join(f"  {i + 1}. {c['name']}" for i, c in enumerate(cands))
        lines.append(
            f"\n❓ Which one for “{first['raw_text']}”? Reply with a number:\n{opts}"
        )
        if len(ambiguous) > 1:
            lines.append(f"({len(ambiguous) - 1} more to disambiguate after this.)")
    return "\n".join(lines)


async def _safe_send(app, chat_id, text):
    try:
        await app.bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
    except Exception:
        log.exception("failed to send message to %s", chat_id)


async def _alert_session_dead(app, chat_id):
    msg = "🔐 Session expired — I can't reach the logged-in store. Re-seed it with seed_session.py and re-upload storage_state.json."
    await _safe_send(app, chat_id, msg)
    if config.ADMIN_CHAT_ID and config.ADMIN_CHAT_ID != chat_id:
        await _safe_send(app, config.ADMIN_CHAT_ID, msg)
