"""Tiny shared helpers used by both the bot and the worker."""
import re

# Quantity can be a prefix ("2x milk", "2 milk"), a multiplier suffix
# ("milk x2", "milk *2"), or a plain suffix ("milk 2x").
_PREFIX_MULT = re.compile(r"^(\d+)\s*[x×*]\s*(.+)$", re.IGNORECASE)          # 2x milk, 2 * milk
_SUFFIX_MULT = re.compile(r"^(.+?)\s*(?:[x×*]\s*(\d+)|(\d+)\s*[x×])\s*$", re.IGNORECASE)  # milk x2 / milk 2x
_PREFIX_BARE = re.compile(r"^(\d+)\s+(.+)$")                                 # 2 milk, 2 amul milk
_SUFFIX_BARE = re.compile(r"^(.+?)\s+(\d+)$")                                # milk 2, amul toned milk 2


def parse_qty(text):
    """Extract a quantity from natural phrasings.

        '2x amul cheese' -> (2, 'amul cheese')
        '2 amul milk'    -> (2, 'amul milk')
        'milk x2'        -> (2, 'milk')
        'milk 2x'        -> (2, 'milk')
        'milk 2'         -> (2, 'milk')
        'amul toned milk 2' -> (2, 'amul toned milk')
        'milk'           -> (1, 'milk')

    Caveat: a bare leading/trailing number is treated as quantity, so names that
    genuinely start or end with a number ("5 star", "pack of 2") get misread —
    write those with an explicit 'x' (e.g. '2x pack of 2') or no count.
    """
    t = text.strip()
    m = _PREFIX_MULT.match(t)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = _SUFFIX_MULT.match(t)
    if m and (m.group(2) or m.group(3)):
        return int(m.group(2) or m.group(3)), m.group(1).strip()
    m = _PREFIX_BARE.match(t)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m = _SUFFIX_BARE.match(t)
    if m:
        return int(m.group(2)), m.group(1).strip()
    return 1, t


def normalize_phrase(text):
    """Canonical key for alias lookups: lowercased, collapsed whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())
