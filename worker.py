"""
Playwright worker: the single, serialized consumer of the job queue.

Only `link` and `reset` enqueue jobs. Exactly one job runs at a time (queue
depth is effectively 1 because there is one consumer and one shared session),
so the shared browser flow is never used concurrently.
"""
import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import config
import db
from store_selectors import get_site
from util import normalize_phrase

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
        log.info("JOB START type=%s group=%s", job.get("type"), job.get("group_id"))
        try:
            if job["type"] == "build":
                await _handle_build(app, job)
            elif job["type"] == "reset":
                await _handle_reset(app, job)
            log.info("JOB DONE type=%s", job.get("type"))
        except Exception:
            log.exception("job failed: %s", job)
            await _safe_send(app, job["chat_id"], "⚠️ Something broke while driving the browser. Try again.")
        finally:
            queue.task_done()


BUILD_TIMEOUT = 180  # seconds — hard cap so a wedged browser can never hang the worker


async def _new_context(p):
    browser = await p.chromium.launch(
        headless=config.HEADLESS,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
        timeout=45_000,
    )
    # Fresh guest context every build: no login, empty cart to start, no drift.
    ctx_kwargs = {"user_agent": _UA, "viewport": _VIEWPORT}
    if config.PROXY:
        ctx_kwargs["proxy"] = config.PROXY
    context = await browser.new_context(**ctx_kwargs)
    return browser, context


async def _handle_build(app, job):
    try:
        await asyncio.wait_for(_do_build(app, job), timeout=BUILD_TIMEOUT)
    except asyncio.TimeoutError:
        log.error("build timed out after %ss for group %s", BUILD_TIMEOUT, job.get("group_id"))
        await db.set_cart_status(job["group_id"], "open")
        await _safe_send(app, job["chat_id"], "⏱️ Building took too long — please try `link` again.")


async def _do_build(app, job):
    from playwright.async_api import async_playwright

    group_id = job["group_id"]
    chat_id = job["chat_id"]
    site = get_site()

    async with async_playwright() as p:
        log.info("build: browser launching")
        browser, context = await _new_context(p)
        page = await context.new_page()
        try:
            # One-off diagnostic: what IP/country is the browser egressing from?
            try:
                await page.goto("https://api.country.is/", timeout=15_000)
                log.info("egress: %s", (await page.inner_text("body"))[:200])
            except Exception as exc:
                log.info("egress check failed: %s", exc)

            log.info("build: setting location")
            if not await site.ensure_location(page, config.BLINKIT_LOCATION):
                await db.set_cart_status(group_id, "open")
                await _alert_location_failed(app, chat_id)
                return

            # Fresh guest context => cart already empty. Add the whole list so the
            # cart always equals the list (idempotent, no drift).
            log.info("build: fetching list")
            items = await db.get_active_items(group_id)
            log.info("build: %d items to add", len(items))
            added, not_found, ambiguous, errored = [], [], [], []

            for item in items:
                phrase = normalize_phrase(item["raw_text"])
                alias = await db.get_alias(group_id, phrase)
                qty = item["qty"] or 1
                preferred_id = alias["product_id"] if alias else None
                res = await site.search_and_add(page, phrase, qty, preferred_id=preferred_id)
                log.info("build %s x%s -> %s (%s)", phrase, qty, res.status, res.product_name)

                await _apply_result(group_id, item, res, phrase)

                if res.status == "added":
                    label = res.product_name or item["raw_text"]
                    added.append(f"{qty}× {label}" if qty > 1 else label)
                elif res.status == "ambiguous":
                    item = dict(item)
                    item["candidates"] = res.candidates
                    ambiguous.append(item)
                elif res.status == "not_found":
                    not_found.append(item["raw_text"])
                else:
                    errored.append(item["raw_text"])

            cart_url = await site.open_cart(page)
            await db.set_cart_status(group_id, "open")

            summary = _format_summary(cart_url, added, not_found, ambiguous, errored)
            await _safe_send(app, chat_id, summary)
            await _send_ambiguous_prompts(app, chat_id, ambiguous)
        finally:
            await browser.close()


async def _handle_reset(app, job):
    """Clear the DB list. The live cart is per-build (fresh guest each time),
    so there's nothing to empty — the next `link` starts clean."""
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
    lines = [f"🛒 Shared cart — open on your phone (Blinkit app): {cart_url}", ""]
    if added:
        lines.append(f"✅ Added ({len(added)}): " + ", ".join(added))
    if not_found:
        lines.append(f"❌ Not found ({len(not_found)}): " + ", ".join(not_found))
    if errored:
        lines.append(f"⚠️ Errored ({len(errored)}): " + ", ".join(errored))
    if ambiguous:
        names = ", ".join(f"“{it['raw_text']}”" for it in ambiguous)
        lines.append(f"\n❓ Tap a choice below for: {names}")
    return "\n".join(lines)


async def _send_ambiguous_prompts(app, chat_id, ambiguous):
    """One message per ambiguous item, with the actual product names as buttons."""
    for it in ambiguous:
        cands = it.get("candidates") or []
        if not cands:
            continue
        keyboard = [
            [InlineKeyboardButton(c["name"][:60], callback_data=f"pick:{it['id']}:{i}")]
            for i, c in enumerate(cands)
        ]
        await _safe_send_markup(
            app, chat_id, f"❓ Which “{it['raw_text']}”?", InlineKeyboardMarkup(keyboard)
        )


async def _safe_send_markup(app, chat_id, text, markup):
    try:
        await app.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
    except Exception:
        log.exception("failed to send prompt to %s", chat_id)


async def _safe_send(app, chat_id, text):
    try:
        await app.bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
    except Exception:
        log.exception("failed to send message to %s", chat_id)


async def _alert_location_failed(app, chat_id):
    msg = (f"📍 Couldn't set the delivery location “{config.BLINKIT_LOCATION}” on Blinkit — "
           "it may be unserviceable, or the site's location UI changed.")
    await _safe_send(app, chat_id, msg)
    if config.ADMIN_CHAT_ID and config.ADMIN_CHAT_ID != chat_id:
        await _safe_send(app, config.ADMIN_CHAT_ID, msg)
