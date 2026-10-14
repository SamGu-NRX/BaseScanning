"""Meter makers the brand reader looks for, and how their names are normalised.

The list names electricity-meter makers in general, not the ones in the photo set: the US
residential market (Itron, Landis+Gyr, Aclara and GE, Honeywell and Elster, Sensus, and the
older Sangamo, Westinghouse, Duncan and Schlumberger) plus the large European and Asian makers.
It was committed before any brand was scored. A photo whose maker is missing here counts as a
miss for the dictionary rule, so the rule's recall is what an app shipping this list would get.
"""

import re
import unicodedata

# Canonical name -> spellings as they appear on meters. Each printed name is its own brand, even
# where one company later bought another (Elster and Honeywell, Actaris and Itron), because the
# test is whether the phone reads what is printed. A spelling of 4 characters or fewer
# must match a whole OCR token exactly; one of 6 letters or more may be one edit away (see match_brand).
BRANDS: dict[str, tuple[str, ...]] = {
    "ITRON": ("ITRON",),
    "ACTARIS": ("ACTARIS",),
    "SCHLUMBERGER": ("SCHLUMBERGER",),
    "LANDIS+GYR": ("LANDIS+GYR", "LANDIS GYR", "LANDIS & GYR", "LANDIS&GYR", "L+G", "LANDIS"),
    "GE": ("GE", "GENERAL ELECTRIC"),
    "ACLARA": ("ACLARA",),
    "ELSTER": ("ELSTER",),
    "HONEYWELL": ("HONEYWELL",),
    "ABB": ("ABB",),
    "SENSUS": ("SENSUS",),
    "SANGAMO": ("SANGAMO",),
    "WESTINGHOUSE": ("WESTINGHOUSE",),
    "DUNCAN": ("DUNCAN",),
    "SIEMENS": ("SIEMENS",),
    "AEG": ("AEG",),
    "ISKRA": ("ISKRA", "ISKRAEMECO"),
    "EMH": ("EMH",),
    "DZG": ("DZG",),
    "KAMSTRUP": ("KAMSTRUP",),
    "EDMI": ("EDMI",),
    "ZIV": ("ZIV",),
    "SAGEMCOM": ("SAGEMCOM", "SAGEM"),
    "HEXING": ("HEXING",),
    "HOLLEY": ("HOLLEY",),
    "GENUS": ("GENUS",),
    "SECURE": ("SECURE METERS",),
    "TOSHIBA": ("TOSHIBA",),
    "MITSUBISHI": ("MITSUBISHI",),
    "OSAKI": ("OSAKI",),
    "TATUNG": ("TATUNG",),
    "CHUNGHSIN": ("CHUNGHSIN", "CHUNG HSIN", "CHUNG-HSIN"),
    "CGE": ("CGE",),
    "ENERMET": ("ENERMET",),
    "AMPY": ("AMPY",),
    "LOGAREX": ("LOGAREX",),
    "ECHELON": ("ECHELON",),
}

ALIASES: list[tuple[str, str]] = sorted(
    ((alias, canon) for canon, spellings in BRANDS.items() for alias in spellings),
    key=lambda pair: -len(pair[0]),
)

# Lines that describe the meter rather than name its maker, for the rule without a list.
SPEC = re.compile(
    r"KWH|VOLT|\bCL\s*\d|\bFORM|TYPE|\bHZ\b|\bKH\b|\bWH\b|AMP|PHASE|WIRE|CLASS|\bTA\b|\d",
)


def normalise(text: str) -> str:
    """Upper case, accents folded (`Itrón` reads as ITRON), `&` and `+` kept, other punctuation to spaces."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.upper().replace("＋", "+")
    text = re.sub(r"[^A-Z0-9+& ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def within_one_edit(a: str, b: str) -> bool:
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = 0
    edited = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        if edited:
            return False
        edited = True
        if len(a) == len(b):
            i += 1
        j += 1
    return True


def match_brand(line: str) -> str | None:
    """The canonical maker named in one recognised line, or None.

    Long spellings match whole tokens or one edit from a token or a pair of
    adjacent tokens (OCR drops or swaps a letter, or splits `LANDIS+GYR`). Short spellings such
    as `GE` must be a whole token, because they occur inside unrelated words.
    """
    norm = normalise(line)
    if not norm:
        return None
    tokens = norm.split(" ")
    windows = tokens + [a + b for a, b in zip(tokens, tokens[1:])] + [" ".join(p) for p in zip(tokens, tokens[1:])]
    for alias, canon in ALIASES:
        if len(alias) <= 4:
            if alias in tokens:
                return canon
            continue
        if re.search(rf"(^| ){re.escape(alias)}($| )", norm) or alias.replace(" ", "") in windows:
            return canon
        # One edit is allowed from 6 letters up: at 5, GENUS is one edit from "GE US".
        if len(alias.replace(" ", "")) >= 6 and any(within_one_edit(alias.replace(" ", ""), w.replace(" ", "")) for w in windows):
            return canon
    return None


def canonical(name: str) -> str | None:
    """A label's maker name mapped onto the list, or its normalised text when not listed."""
    name = name.strip()
    if not name:
        return None
    return match_brand(name) or normalise(name)
