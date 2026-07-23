"""Tiny shared helpers used by both the bot and the worker."""
import re

# Matches "2x milk", "2 x milk", "2*milk", "2× milk"
_QTY_RE = re.compile(r"^\s*(\d+)\s*(?:x|×|\*)\s*(.+)$", re.IGNORECASE)


def parse_qty(text):
    """'2x amul cheese' -> (2, 'amul cheese'); 'milk' -> (1, 'milk')."""
    m = _QTY_RE.match(text)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 1, text.strip()


def normalize_phrase(text):
    """Canonical key for alias lookups: lowercased, collapsed whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())
