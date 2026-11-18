"""Dealer names as ads write them -> a comparable key and ARES search variants (ported from
AdaWork's employer names).

"AUTO PLUS spol.s r.o." and "Auto Plus spol. s r.o." are one dealer: the key folds case and
diacritics, drops punctuation and the legal form. Search variants for ARES expand "ČR"/"CZ"
and drop a town after the name ("AAA AUTO - Plzeň") or generic words ("Autobazar").
"""

import re

from adacar.normalize.text import fold

# Legal forms as folded word sequences, longest first; stripped from the end of a name.
_FORMS = (
    "spol s r o", "spolecnost s rucenim omezenym", "s r o", "sro", "a s", "as", "k s",
    "akciova spolecnost", "verejna obchodni spolecnost", "komanditni spolecnost",
    "v o s", "s p", "z s", "z u", "o p s", "se", "druzstvo", "organizacni slozka",
    "gmbh", "ltd", "inc", "bv", "b v", "kft", "sp z o o", "s a", "sa", "a g", "ag", "n v",
    "nv", "plc", "llc", "kg", "spa", "s p a", "srl", "s r l", "o z", "odstepny zavod",
)  # fmt: skip
FORM_WORDS = tuple(sorted((tuple(f.split()) for f in _FORMS), key=len, reverse=True))
GENERIC = frozenset({
    "autobazar", "bazar", "autosalon", "prodejna", "pobocka", "provozovna", "firma",
    "spolecnost", "showroom",
})  # fmt: skip
_EXPAND = {"cr": "Česká republika", "cz": "Česká republika", "čr": "Česká republika"}
COUNTRY = frozenset({"cz", "cr", "czech", "republic", "ceska", "republika", "czechia"})


def words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", fold(name).replace("&", " a "))


def strip_form(tokens: list[str]) -> list[str]:
    """The name's words without trailing legal forms ("Auto Plus, a. s., organizační
    složka")."""
    changed = True
    while changed and tokens:
        changed = False
        for form in FORM_WORDS:
            n = len(form)
            if len(tokens) > n and tuple(tokens[-n:]) == form:
                tokens, changed = tokens[:-n], True
                break
    return tokens


def name_key(name: str | None) -> str:
    """ "Auto Plus, s. r. o." -> "auto plus"; "" for no name."""
    return " ".join(strip_form(words(name or "")))[:200]


def legal_form_in(name: str) -> bool:
    return len(strip_form(words(name))) < len(words(name))


def variants(name: str, places: frozenset[str] = frozenset()) -> list[str]:
    """ARES name queries, best first: as written, "ČR" expanded, without a town or the part
    after a dash ("AAA AUTO - Plzeň"), without generic words, the part in or before
    brackets, without the country."""
    name = re.sub(r"[\"„“”»«]", " ", name).strip(" ,.-")
    name = re.sub(r"\s+", " ", name)
    out = [name]
    out.append(" ".join(_EXPAND.get(w.lower().strip(",."), w) for w in name.split()))

    def without(drop, keep_first: bool = True) -> str:
        kept = [
            w
            for i, w in enumerate(name.split())
            if (keep_first and i == 0) or not drop(fold(w).strip(",.-"))
        ]
        return " ".join(kept)

    out.append(without(lambda w: w in places))
    dash = re.split(r"\s+[-–]\s+", name, maxsplit=1)
    if len(dash) == 2:
        out.append(dash[0])
    elif len(name.split()) >= 3:  # a branch after the name: "Autosalon Klokočka Centrum Řepy"
        out.append(name.rsplit(" ", 1)[0])
    out.append(without(lambda w: w in GENERIC, keep_first=False))
    inner = re.search(r"\(([^)]{3,})\)", name)
    if inner:
        out += [inner.group(1), name[: inner.start()]]
    out.append(without(lambda w: w in COUNTRY))
    return [v.strip(" ,.-–") for v in dict.fromkeys(out) if len(name_key(v)) >= 2]


def core_words(name: str) -> list[str]:
    """The name's words without legal forms and country words: what is compared. Inside a
    name only unmistakable forms go ("s r o", "GmbH")."""
    tokens = strip_form(words(name))
    out: list[str] = []
    i = 0
    while i < len(tokens):
        form = next((f for f in FORM_WORDS if tuple(tokens[i : i + len(f)]) == f), None)
        if form and i > 0 and (len(form[0]) >= 3 or len(form) >= 3):
            i += len(form)
            continue
        if tokens[i] not in COUNTRY or i == 0:
            out.append(tokens[i])
        i += 1
    return out
