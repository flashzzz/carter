"""
Telegram entrypoint: parses messages, routes commands, and produces jobs onto
the in-process asyncio.Queue that the Playwright worker drains.

Run: python bot.py   (long-polling; no public ingress required)
"""
import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import db
import worker
from util import normalize_phrase, parse_qty

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

RESET_WORDS = {"reset", "ordered", "done"}


# ---------- access control ----------

def _allowed(chat_id):
    return not config.ALLOWED_CHAT_IDS or chat_id in config.ALLOWED_CHAT_IDS


# ---------- reactions ----------

async def _react_ok(bot, chat_id, message_id):
    # 👍 is in Telegram's default allowed-reaction set; ✅ is not (returns 400).
    for emoji in ("👍", "✅"):
        try:
            await bot.set_message_reaction(chat_id=chat_id, message_id=message_id, reaction=emoji)
            return
        except Exception:
            continue


# ---------- message handling ----------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg is None or not msg.text:
        return
    chat_id = update.effective_chat.id
    text = msg.text.strip()
    lower = text.lstrip("/").strip().lower()  # accept `link` and `/link` alike

    # Always answer chatid, even for unlisted chats — needed for first-time setup.
    if lower in ("chatid", "whoami"):
        await msg.reply_text(f"This chat's id is `{chat_id}`", parse_mode="Markdown")
        return

    if not _allowed(chat_id):
        log.info("ignoring message from unlisted chat %s", chat_id)
        return

    # Commands
    if lower == "link":
        await cmd_link(update, context)
        return
    if lower in RESET_WORDS:
        await cmd_reset(update, context)
        return
    if lower == "list":
        await cmd_list(update, context)
        return
    if lower.startswith("remove "):
        await cmd_remove(update, context, text[len("remove "):].strip())
        return
    if lower.startswith("/remove "):
        await cmd_remove(update, context, text[len("/remove "):].strip())
        return

    # A bare number answers the live disambiguation prompt.
    if lower.isdigit():
        await answer_disambiguation(update, context, int(lower))
        return

    # Anything else is an item to add.
    await add_item(update, context)


async def add_item(update: Update, context):
    msg = update.effective_message
    chat_id = update.effective_chat.id
    qty, phrase = parse_qty(msg.text.strip())
    who = update.effective_user.first_name if update.effective_user else None
    await db.add_item(chat_id, phrase, qty=qty, added_by=who)
    await _react_ok(context.bot, chat_id, msg.message_id)


async def cmd_list(update: Update, context):
    chat_id = update.effective_chat.id
    items = await db.list_items(chat_id, statuses=["pending", "ambiguous", "not_found", "error"])
    if not items:
        await update.effective_message.reply_text("List is empty. Drop some items.")
        return
    lines = ["📝 Current list:"]
    for it in items:
        tag = "" if it["status"] == "pending" else f" ({it['status']})"
        qty = f"{it['qty']}× " if it["qty"] > 1 else ""
        lines.append(f"• {qty}{it['raw_text']}{tag}")
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_remove(update: Update, context, text):
    if not text:
        await update.effective_message.reply_text("Usage: remove <text>")
        return
    chat_id = update.effective_chat.id
    n = await db.remove_items(chat_id, text)
    await update.effective_message.reply_text(
        f"Removed {n} item(s) matching “{text}”." if n else f"No items matched “{text}”."
    )


async def cmd_link(update: Update, context):
    chat_id = update.effective_chat.id
    active = await db.get_active_items(chat_id)
    if not active:
        await update.effective_message.reply_text("List is empty. Drop items first.")
        return
    await db.set_cart_status(chat_id, "building")
    queue = context.application.bot_data["queue"]
    await queue.put({"type": "build", "group_id": chat_id, "chat_id": chat_id})
    depth = queue.qsize()
    suffix = f" (position {depth} in queue)" if depth > 1 else ""
    await update.effective_message.reply_text(f"🛒 Building your cart…{suffix}")


async def cmd_reset(update: Update, context):
    chat_id = update.effective_chat.id
    queue = context.application.bot_data["queue"]
    await queue.put({"type": "reset", "group_id": chat_id, "chat_id": chat_id})


async def on_pick(update: Update, context):
    """Inline-keyboard callback: user tapped a product for an ambiguous item."""
    q = update.callback_query
    await q.answer()
    chat_id = update.effective_chat.id
    if not _allowed(chat_id):
        return
    try:
        _, sid, sidx = q.data.split(":")
        item_id, idx = int(sid), int(sidx)
    except (ValueError, AttributeError):
        return
    item = await db.get_item(item_id)
    if not item or item["deleted"]:
        await q.edit_message_text("This choice expired — send `link` again.")
        return
    cands = item["candidates"] or []
    if idx < 0 or idx >= len(cands):
        return
    chosen = cands[idx]
    await db.set_item_status(item_id, "pending", chosen.get("product_id"), chosen.get("name"))
    if chosen.get("product_id"):
        await db.upsert_alias(
            chat_id, normalize_phrase(item["raw_text"]), chosen["product_id"], chosen.get("name")
        )
    await q.edit_message_text(f"✅ “{item['raw_text']}” → {chosen.get('name')}\nSend `link` to update the cart.")


async def answer_disambiguation(update: Update, context, choice):
    chat_id = update.effective_chat.id
    item = await db.get_next_ambiguous(chat_id)
    if not item:
        await update.effective_message.reply_text("Nothing to disambiguate right now.")
        return
    cands = item.get("candidates") or []
    if choice < 1 or choice > len(cands):
        await update.effective_message.reply_text(f"Pick a number between 1 and {len(cands)}.")
        return
    chosen = cands[choice - 1]
    # Resolve to that product and remember it as an alias for next time.
    await db.set_item_status(item["id"], "resolved", chosen.get("product_id"), chosen.get("name"))
    if chosen.get("product_id"):
        await db.upsert_alias(
            chat_id, normalize_phrase(item["raw_text"]), chosen["product_id"], chosen.get("name")
        )
    # Put it back to pending so the next `link` actually adds it to the cart.
    await db.set_item_status(item["id"], "pending", chosen.get("product_id"), chosen.get("name"))
    await update.effective_message.reply_text(
        f"Got it — “{chosen.get('name')}” for “{item['raw_text']}”. Send `link` to rebuild the cart."
    )


# ---------- lifecycle ----------

async def _health_server(port):
    async def handle(reader, writer):
        try:
            await reader.read(1024)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain\r\n\r\nok")
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    log.info("health server listening on :%s", port)
    return server


async def post_init(app: Application):
    await db.init(config.DATABASE_URL)
    queue: asyncio.Queue = asyncio.Queue()
    app.bot_data["queue"] = queue
    app.bot_data["worker_task"] = asyncio.create_task(worker.worker_loop(app, queue))
    try:
        app.bot_data["health_server"] = await _health_server(config.HEALTH_PORT)
    except OSError as exc:
        # A busy port must never take the bot down; the health probe is optional.
        log.warning("health server not started (%s)", exc)
    log.info("bot ready — platform=%s allowed=%s", config.PLATFORM, config.ALLOWED_CHAT_IDS or "ALL")


async def post_shutdown(app: Application):
    await db.close()


def main():
    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CallbackQueryHandler(on_pick, pattern="^pick:"))
    app.add_handler(MessageHandler(filters.TEXT, handle_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
