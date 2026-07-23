"""
ALL site-specific DOM knowledge lives here. When Blinkit/Zepto change their
UI, this is the ONLY file you touch.

Each Site subclass implements four async methods against a Playwright `page`:

    ensure_logged_in(page) -> bool
    search_and_add(page, query) -> ResolveResult
    add_by_product_id(page, product_id, product_name) -> ResolveResult
    open_cart(page) -> str   # returns the cart/checkout URL

⚠️  THE CSS SELECTORS BELOW ARE PLACEHOLDERS.  Neither Blinkit nor Zepto
publishes stable selectors, and they cannot be inferred without the live DOM.
Fill them in ONCE by running `python seed_session.py` (headed), opening
DevTools, and copying the real selectors. Everything else — the flow, retries,
pacing, result plumbing — is already wired up.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

import config


@dataclass
class ResolveResult:
    status: str  # "added" | "ambiguous" | "not_found" | "error"
    product_id: str | None = None
    product_name: str | None = None
    candidates: list = field(default_factory=list)  # [{name, product_id}]
    note: str | None = None


async def _human_delay():
    await asyncio.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY))


class Site:
    """Base class. Subclasses fill in URLs + selectors + any flow quirks."""

    name = "base"
    base_url = ""
    account_url = ""       # a page that only renders when logged in
    cart_url = ""

    # --- selectors (FILL THESE IN) ---
    SEARCH_INPUT = ""          # e.g. "input[type='search']"
    LOGGED_OUT_MARKER = ""     # element present ONLY when logged out (e.g. a "Login" button)
    PRODUCT_CARD = ""          # each search-result product card
    PRODUCT_NAME = ""          # product title within a card
    PRODUCT_ADD_BTN = ""       # "ADD" button within a card
    PRODUCT_INCREMENT = ""     # "+" stepper (to raise qty after adding)
    PRODUCT_ID_ATTR = ""       # attribute on a card holding the product id (e.g. "data-product-id")

    async def _goto(self, page, url):
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        await _human_delay()

    async def ensure_logged_in(self, page) -> bool:
        """True if the persisted session is still authenticated."""
        await self._goto(page, self.account_url)
        if self.LOGGED_OUT_MARKER:
            # If the logged-out marker is visible, the session is dead.
            try:
                marker = page.locator(self.LOGGED_OUT_MARKER)
                if await marker.count() and await marker.first.is_visible():
                    return False
            except Exception:
                pass
        # Heuristic fallback: a login URL redirect means we're logged out.
        return "login" not in page.url.lower()

    async def search_and_add(self, page, query) -> ResolveResult:
        if not self.SEARCH_INPUT or not self.PRODUCT_CARD:
            return ResolveResult("error", note="store_selectors.py not filled in yet")
        try:
            await self._goto(page, self.base_url)
            box = page.locator(self.SEARCH_INPUT).first
            await box.click()
            await box.fill(query)
            await box.press("Enter")
            await _human_delay()

            cards = page.locator(self.PRODUCT_CARD)
            n = await cards.count()
            if n == 0:
                return ResolveResult("not_found")

            # Collect the top few candidates for a possible disambiguation.
            top = min(n, 3)
            candidates = []
            for i in range(top):
                card = cards.nth(i)
                name = (await card.locator(self.PRODUCT_NAME).first.inner_text()).strip()
                pid = await card.get_attribute(self.PRODUCT_ID_ATTR) if self.PRODUCT_ID_ATTR else None
                candidates.append({"name": name, "product_id": pid})

            # Confident top match -> add it. Otherwise hand back candidates.
            if top == 1 or self._is_confident(query, candidates):
                first = cards.first
                await self._click_add(page, first)
                return ResolveResult(
                    "added",
                    product_id=candidates[0]["product_id"],
                    product_name=candidates[0]["name"],
                )
            return ResolveResult("ambiguous", candidates=candidates)
        except Exception as exc:  # never let one item kill the batch
            return ResolveResult("error", note=str(exc)[:200])

    async def add_by_product_id(self, page, product_id, product_name) -> ResolveResult:
        """Fast path when an alias already knows the exact product."""
        url = self.product_url(product_id)
        if not url:
            return ResolveResult("error", note="product_url() not implemented")
        try:
            await self._goto(page, url)
            await self._click_add(page, page)
            return ResolveResult("added", product_id=product_id, product_name=product_name)
        except Exception as exc:
            return ResolveResult("error", note=str(exc)[:200])

    async def open_cart(self, page) -> str:
        await self._goto(page, self.cart_url)
        return page.url

    # --- helpers subclasses may override ---

    def product_url(self, product_id):
        return None

    def _is_confident(self, query, candidates):
        """Add the top hit outright when it's an obvious match."""
        if not candidates:
            return False
        top = candidates[0]["name"].lower()
        q = query.lower()
        return q in top or all(w in top for w in q.split())

    async def _click_add(self, page, scope):
        add = scope.locator(self.PRODUCT_ADD_BTN).first
        await add.click()
        await _human_delay()


class Blinkit(Site):
    name = "blinkit"
    base_url = "https://blinkit.com"
    account_url = "https://blinkit.com/account"
    cart_url = "https://blinkit.com/checkout"

    # TODO: verify all of these against the live DOM (DevTools -> Copy selector).
    SEARCH_INPUT = "input[type='text']"
    LOGGED_OUT_MARKER = "text=Login"
    PRODUCT_CARD = "[role='button'][id]"
    PRODUCT_NAME = "div.Product__UpdatedTitle-sc"
    PRODUCT_ADD_BTN = "div:has-text('ADD')"
    PRODUCT_INCREMENT = "[data-test-id='plus']"
    PRODUCT_ID_ATTR = "id"

    def product_url(self, product_id):
        # Blinkit product pages look like /prn/<slug>/prid/<id> — slug is optional.
        return f"https://blinkit.com/prn/x/prid/{product_id}"


class Zepto(Site):
    name = "zepto"
    base_url = "https://www.zeptonow.com"
    account_url = "https://www.zeptonow.com/account"
    cart_url = "https://www.zeptonow.com/cart"

    # TODO: verify all of these against the live DOM.
    SEARCH_INPUT = "input[placeholder*='Search']"
    LOGGED_OUT_MARKER = "text=Login"
    PRODUCT_CARD = "a[data-testid='product-card']"
    PRODUCT_NAME = "[data-testid='product-card-name']"
    PRODUCT_ADD_BTN = "button:has-text('Add')"
    PRODUCT_INCREMENT = "button[aria-label='increment']"
    PRODUCT_ID_ATTR = "href"

    def product_url(self, product_id):
        return f"https://www.zeptonow.com/pn/x/pvid/{product_id}"


def get_site() -> Site:
    return {"blinkit": Blinkit, "zepto": Zepto}.get(config.PLATFORM, Blinkit)()
