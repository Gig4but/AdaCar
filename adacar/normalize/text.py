"""Plain text from portal pages, and matching helpers (car facts: normalize/cars.py)."""

import re
import unicodedata
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from html import unescape

from selectolax.lexbor import LexborHTMLParser, LexborNode

_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table",
    "section", "article", "header", "footer", "blockquote", "pre", "dd", "dt", "hr",
}  # fmt: skip
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "button", "form"}
_LEADING_SYMBOLS = re.compile(r"^[^\w(]+")
_DATE = re.compile(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})")


def fold(text: str) -> str:
    """Lowercase without diacritics, for matching ("Škoda Fábie" -> "skoda fabie")."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def accented_words(user_text: str) -> list[str]:
    """Search words typed with diacritics ("škoda"), lowercased."""
    return [w for w in re.findall(r"\w+", user_text.casefold()) if fold(w) != w]


@lru_cache(maxsize=256)
def _word_starts(words: str) -> list[re.Pattern]:
    return [re.compile(rf"(?<!\w){re.escape(w)}", re.IGNORECASE) for w in words.split()]


def has_word_starts(text: str | None, words: str) -> int:
    """1 if every word in `words` (space-separated) starts a word in `text`, diacritics
    included: "fábie" matches "Fábie", not "fabie". Registered as an SQLite function."""
    if not words:
        return 1
    text = text or ""
    return int(all(p.search(text) for p in _word_starts(words)))


def clean_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def clean_title(text: str | None) -> str:
    """ "⭐Škoda Octavia" -> "Škoda Octavia": without the emoji or symbols titles start with."""
    return _LEADING_SYMBOLS.sub("", clean_spaces(text or ""))


def czech_date(text: str | None) -> date | None:
    """The first date written "7. 10. 2026" in a text."""
    m = _DATE.search(text or "")
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def card_date(text: str | None) -> datetime | None:
    """ "Dnes", "Včera" or "7. 10. 2026", as listing cards show the day an ad was posted."""
    folded = fold(text or "")
    if folded == "dnes":
        day = date.today()
    elif folded == "vcera":
        day = date.today() - timedelta(days=1)
    else:
        day = czech_date(folded)
    return datetime.combine(day, time()) if day else None


def html_to_text(html: str | None) -> str:
    """Readable plain text: one line per paragraph or list item, bullets for list items."""
    if not html:
        return ""
    if "&lt;" in html and "<" not in html:  # escaped markup
        html = unescape(html)
    if "<" not in html:
        return clean_spaces(html)
    root = LexborHTMLParser(f"<div>{html}</div>").body
    return node_text(root) if root else ""


def node_text(node: LexborNode) -> str:
    parts: list[str] = []

    def walk(n: LexborNode) -> None:
        for child in n.iter(include_text=True):
            tag = child.tag
            if tag == "-text":
                parts.append(child.text_content or "")
            elif tag in _SKIP_TAGS or tag == "-comment":
                continue
            elif tag in _BLOCK_TAGS:
                parts.append("\n• " if tag == "li" else "\n")
                walk(child)
                parts.append("\n")
            else:
                walk(child)

    walk(node)
    lines = (clean_spaces(line) for line in "".join(parts).split("\n"))
    text = "\n".join(line for line in lines if line and line != "•")
    return text.strip()


def split_places(text: str | None) -> list[str]:
    """ "Praha-Libeň, Středočeský" -> ["Praha-Libeň", "Středočeský"]."""
    if not text:
        return []
    return [p.strip() for p in re.split(r"[,;]| \+ ", text) if p.strip()]


def slugify(text: str) -> str:
    """ "Kutná Hora" -> "kutna-hora", as portals write places in URLs."""
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")


def valid_ico(text: str | None) -> bool:
    """Eight digits with the right check digit (mod 11): a typo is not a company."""
    if not text or not re.fullmatch(r"\d{8}", text):
        return False
    total = sum(int(d) * w for d, w in zip(text[:7], range(8, 1, -1), strict=True))
    return (11 - total % 11) % 10 == int(text[7])
