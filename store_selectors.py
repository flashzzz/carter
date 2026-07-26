"""
ALL site-specific DOM knowledge lives here. When Blinkit/Zepto change their
UI, this is the ONLY file you touch.

Each Site subclass implements async methods against a Playwright `page`:

    ensure_location(page, location) -> bool   # set guest delivery location
    search_and_add(page, query, qty, preferred_id) -> ResolveResult
    open_cart(page) -> str                    # returns the shareable cart link

The Blinkit implementation below is filled in and verified against the live DOM.
The Zepto subclass is a placeholder skeleton (selectors need filling from the
live DOM). No login/OTP anywhere — a guest delivery location is all that's
required to browse, cart, and share on Blinkit.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

import config

log = logging.getLogger("store")


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

    async def ensure_location(self, page, location) -> bool:
        """Set the delivery location. Base no-op; override per site."""
        return True

    async def ensure_logged_in(self, page) -> bool:
        """Deprecated (guest flow needs no login). Kept for the generic base."""
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

    async def search_and_add(self, page, query, qty=1, preferred_id=None) -> ResolveResult:
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
                await self._click_add(page, first, qty)
                return ResolveResult(
                    "added",
                    product_id=candidates[0]["product_id"],
                    product_name=candidates[0]["name"],
                )
            return ResolveResult("ambiguous", candidates=candidates)
        except Exception as exc:  # never let one item kill the batch
            return ResolveResult("error", note=str(exc)[:200])

    async def add_by_product_id(self, page, product_id, product_name, qty=1) -> ResolveResult:
        """Fast path when an alias already knows the exact product."""
        url = self.product_url(product_id)
        if not url:
            return ResolveResult("error", note="product_url() not implemented")
        try:
            await self._goto(page, url)
            await self._click_add(page, page, qty)
            return ResolveResult("added", product_id=product_id, product_name=product_name)
        except Exception as exc:
            return ResolveResult("error", note=str(exc)[:200])

    async def open_cart(self, page) -> str:
        await self._goto(page, self.cart_url)
        return page.url

    # --- helpers subclasses may override ---

    async def empty_cart(self, page):
        """Override per-site. Base no-op (e.g. Zepto not implemented yet)."""
        return

    def product_url(self, product_id):
        return None

    def _is_confident(self, query, candidates):
        """Add the top hit outright when it's an obvious match."""
        if not candidates:
            return False
        top = candidates[0]["name"].lower()
        q = query.lower()
        return q in top or all(w in top for w in q.split())

    async def _click_add(self, page, scope, qty=1):
        add = scope.locator(self.PRODUCT_ADD_BTN).first
        await add.click()
        await _human_delay()
        # Clicking ADD sets qty to 1; press "+" to reach the requested count.
        for _ in range(max(0, qty - 1)):
            inc = scope.locator(self.PRODUCT_INCREMENT).first
            await inc.click()
            await _human_delay()


class Blinkit(Site):
    """Concrete Blinkit implementation, verified against the live DOM (2026-07).

    Notes on Blinkit's reality:
    - No standalone cart/checkout route: /cart and /checkout both redirect to the
      home page. The cart is server-side on the guest session and shown in a
      slide-over. But the cart's Share control (POST /v1/assist/cart/share) mints
      a shareable deep link (https://link.blinkit.com/bln/<code>) that opens the
      cart in the Blinkit app on any phone — `open_cart` returns THAT.
    - Product cards are `[role='button'][id]` where `id` IS the product id
      (excluding the results wrapper `#product_container`).
    - Search is driven by URL (/s/?q=...) — far more robust than the search box.
    - Add flow is qty-aware/idempotent: it reads the current cart qty on the card
      and adjusts with the +/- stepper, so repeated `link`s don't over-add.
    """
    name = "blinkit"
    base_url = "https://blinkit.com"
    account_url = "https://blinkit.com/"
    cart_url = "https://blinkit.com/"          # cart is account-side; no deep link

    CARD = "[role='button'][id]:not(#product_container)"
    NAME = ".tw-line-clamp-2"
    ADD_BTN = "[role='button']:has-text('ADD')"
    INC = ".icon-plus"
    DEC = ".icon-minus"

    def product_url(self, product_id):
        # /prn/<slug>/prid/<id> — slug is cosmetic; a placeholder works.
        return f"https://blinkit.com/prn/x/prid/{product_id}"

    def _search_url(self, query):
        from urllib.parse import quote
        return f"https://blinkit.com/s/?q={quote(query)}"

    LOCATION_INPUT = "input[name='select-locality']"
    LOCATION_SUGGESTION = (
        "[class*='LocationSearchList__LocationListContainer'] [class*='LocationSearchList']"
    )

    async def ensure_location(self, page, location) -> bool:
        """Set the guest delivery location by pincode/locality. No login needed —
        a delivery location is all Blinkit requires to browse, cart, and share."""
        await page.goto(self.base_url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1500)
        box = page.locator(self.LOCATION_INPUT)
        try:
            await box.wait_for(timeout=8000)
        except Exception:
            return True  # no location prompt -> already set (persisted context)
        try:
            await box.first.click()
            await box.first.fill(str(location))
            await page.wait_for_timeout(2500)
            await page.locator(self.LOCATION_SUGGESTION).first.click(timeout=8000)
            await page.wait_for_timeout(3000)
            # Success when the location prompt is gone (a store was selected).
            return await page.locator(self.LOCATION_INPUT).count() == 0
        except Exception:
            return False

    async def search_and_add(self, page, query, qty=1, preferred_id=None) -> ResolveResult:
        """Everything runs on the search-results page, where the +/- stepper
        reliably reflects cart state. `preferred_id` (from a learned alias) pins
        the exact product the user picked before, if it's in the results."""
        try:
            await self._goto(page, self._search_url(query))
            await page.wait_for_timeout(1500)
            cards = page.locator(self.CARD)
            n = await cards.count()
            if n == 0:
                try:
                    head = (await page.inner_text("body"))[:300].replace("\n", " ")
                except Exception:
                    head = "?"
                log.warning("no cards for %r | url=%s | body=%s", query, page.url, head)
                return ResolveResult("not_found")

            # If we know the exact product from a prior disambiguation, use it.
            if preferred_id:
                pref = page.locator(f"[role='button'][id='{preferred_id}']")
                if await pref.count():
                    name = (await pref.first.locator(self.NAME).first.inner_text()).strip()
                    await self._set_qty(pref.first, qty)
                    return ResolveResult("added", preferred_id, name)

            top = min(n, 3)
            cand = []
            for i in range(top):
                c = cards.nth(i)
                name = (await c.locator(self.NAME).first.inner_text()).strip()
                cand.append({"name": name, "product_id": await c.get_attribute("id")})
            if top == 1 or self._is_confident(query, cand):
                await self._set_qty(cards.first, qty)
                return ResolveResult("added", cand[0]["product_id"], cand[0]["name"])
            return ResolveResult("ambiguous", candidates=cand)
        except Exception as exc:
            return ResolveResult("error", note=str(exc)[:200])

    async def add_by_product_id(self, page, product_id, product_name, qty=1) -> ResolveResult:
        """Unused by the worker (kept for completeness). The product page does not
        expose the cart stepper reliably, so the worker routes aliases through
        search_and_add(preferred_id=...) instead."""
        return await self.search_and_add(page, product_name or "", qty, preferred_id=product_id)

    # Cart slide-over + share control.
    CART_BUTTON = "div[class*='CartButton__Button']"
    SHARE_API = "/assist/cart/share"
    # Cart-line decrement inside the modal. Minus is AddToCart___StyledDiv;
    # plus is AddToCart___StyledDiv2 — the "-sc" anchors to the minus only.
    CART_LINE_MINUS = "div[class*='CartWrapper'] [class*='AddToCart___StyledDiv-sc']"

    async def _open_cart_modal(self, page) -> bool:
        await self._goto(page, self.base_url)
        btn = page.locator(self.CART_BUTTON).first
        if not await btn.count():
            return False
        await btn.click()
        await page.wait_for_timeout(1500)
        return True

    CART_LINE = "div[class*='CartProduct__Container']"

    async def empty_cart(self, page):
        """Remove every line from the live cart by clicking each line's minus.
        Bounded: per-click timeout + a no-progress guard so it can never hang."""
        if not await self._open_cart_modal(page):
            return
        last, stale = -1, 0
        for _ in range(40):
            lines = await page.locator(self.CART_LINE).count()
            if lines == 0:
                break
            if lines == last:
                stale += 1
                if stale >= 3:  # clicks aren't reducing the cart — stop, don't hang
                    break
            else:
                stale = 0
            last = lines
            minus = page.locator(self.CART_LINE_MINUS)
            if not await minus.count():
                break
            try:
                await minus.first.click(timeout=3000)
                await page.wait_for_timeout(700)
            except Exception:
                break

    async def open_cart(self, page) -> str:
        """Open the cart, hit Share, and return Blinkit's shareable cart link
        (https://link.blinkit.com/bln/<code>) — openable on any phone without
        being logged in. Falls back to the home URL."""
        await self._goto(page, self.base_url)
        try:
            btn = page.locator(self.CART_BUTTON).first
            if await btn.count():
                await btn.click(timeout=8000)
                await page.wait_for_timeout(1500)
            # Blinkit's JS builds the correct POST payload; we just read the reply.
            async with page.expect_response(
                lambda r: self.SHARE_API in r.url and r.request.method == "POST",
                timeout=15_000,
            ) as resp_info:
                await page.get_by_text("Share", exact=True).first.click(timeout=8000)
            data = (await (await resp_info.value).json()).get("data", {})
            link = data.get("deferred_deeplink")
            if link:
                return link
        except Exception:
            pass
        return self.cart_url

    async def _current_qty(self, scope) -> int:
        """The in-cart stepper's control has purely-numeric text; 0 if not added."""
        btns = scope.locator("[role='button']")
        for i in range(await btns.count()):
            t = (await btns.nth(i).inner_text()).strip()
            if t.isdigit():
                return int(t)
        return 0

    async def _set_qty(self, scope, target):
        """Drive the card/product to exactly `target` units (idempotent)."""
        cur = 0
        add = scope.locator(self.ADD_BTN)
        if await add.count():
            await add.first.click()
            await _human_delay()
            cur = 1
        else:
            cur = await self._current_qty(scope)
        guard = 0
        while cur < target and guard < 50:
            await scope.locator(self.INC).first.click()
            await _human_delay()
            cur += 1
            guard += 1
        while cur > target and guard < 50:
            await scope.locator(self.DEC).first.click()
            await _human_delay()
            cur -= 1
            guard += 1


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
