"""Extract serial-number candidates from reader output.

The interface mirrors the committed meter-closeup code (BaseScanning c813182), where the
candidate and matching rules live in `experiments/meter-closeup/src/meter_eval/`:

- `locate.candidates(result: dict) -> list[dict]`: one candidate per digit-bearing token of
  every recognized line, with the features the ranking uses (core, box, barcode_confirmed,
  keyword, alone, spec, vertical, zeros, length_ok, height).
- `locate.tokens(text: str) -> list[str]`: digit-bearing tokens of one line, with a leading
  label word (No., Nr., #, S/N, Serial) removed and runs of numeric groups merged.
- `locate.payload_tokens(barcodes: list[dict]) -> set[str]`: cores of the digit-bearing
  parts of every decoded barcode payload.
- `match.normalize(text: str) -> str`: uppercased, only A-Z and 0-9.
- `match.core(text: str) -> str`: the normalized text without a leading run of letters
  before a digit.

meter_eval.match digests real meter numbers with a keyed HMAC because CONTRIBUTING.md
forbids committing them and an unkeyed hash of a short number can be reversed. This
experiment's serials are synthetic, so cores stay plaintext: no HMAC key is read or needed
here, and meter_eval itself is neither imported nor modified.

As in `locate.candidates`, a text token contained in exactly one longer barcode payload
(at least 6 characters) is treated as a partial read of it and takes the payload's core; a
token contained in two payloads of the same length stays unpromoted (ambiguous).
"""

import re

# A label word in front of the number (locate.py's KEYWORD): No., Nr., No, #:, S/N, Serial.
KEYWORD = re.compile(
    r"^\s*(?:N[O0R]\.?|N\u00b0|\u2116|#\s*:|S/?N|SERIAL\s*#?)\s*[.:#]?\s*", re.IGNORECASE
)
# Lines that describe the meter rather than identify it (locate.py's SPEC, narrowed to the
# constructs the synthetic plates can print).
SPEC = re.compile(
    r"\d\s*\(\s*\d+\s*\)|\d\s*V\b|HZ\b|\bKH\b|\d\s*CL\b|FORM|TYPE|KWH|U/|AMP|VOLT|\d+/\d+",
    re.IGNORECASE,
)
DIGIT_GROUP = re.compile(r"^[\d.,\-]+$")


def digits(text: str) -> int:
    return sum(ch.isdigit() for ch in text)


def normalize(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def core(text: str) -> str:
    return re.sub(r"^[A-Z]+(?=\d)", "", normalize(text))


def tokens(text: str) -> list[str]:
    """Digit-bearing tokens of one line, with a leading label word removed."""
    rest = KEYWORD.sub("", text).strip()
    groups = rest.split()
    found: list[str] = []
    run: list[str] = []
    for group in groups:
        if DIGIT_GROUP.match(group):
            run.append(group)
            continue
        if run:
            found.append(" ".join(run))
            run = []
        found.append(group)
    if run:
        found.append(" ".join(run))
    if 1 < len(groups) <= 4:
        found.append(rest)
    unique = list(dict.fromkeys(found))
    return [t for t in unique if digits(t) >= 4]


def payload_cores(barcodes: list[dict]) -> set[str]:
    """Cores of the digit-bearing parts of every decoded payload."""
    parts: set[str] = set()
    for barcode in barcodes:
        for part in re.split(r"[\s;*,{}]+", barcode.get("payload") or ""):
            if digits(part) >= 4:
                parts.add(core(part))
    return parts


def candidates(result: dict) -> list[dict]:
    """Every candidate with the features the ranking uses."""
    payloads = payload_cores(result.get("barcodes") or [])
    found = []
    for line in result.get("lines") or []:
        text = line["text"]
        _, _, w, h = line["box"]
        for token in tokens(text):
            token_core = core(token)
            confirmed = token_core in payloads
            if not confirmed and len(token_core) >= 6:
                longer = [p for p in payloads if token_core in p]
                shortest = min((len(p) for p in longer), default=0)
                closest = [p for p in longer if len(p) == shortest]
                if len(closest) == 1:
                    token_core, confirmed = closest[0], True
            found.append(
                {
                    "core": token_core,
                    "box": line["box"],
                    "barcode_confirmed": confirmed,
                    "keyword": bool(KEYWORD.match(text)),
                    "alone": core(KEYWORD.sub("", text)) == token_core,
                    "spec": bool(SPEC.search(text)),
                    "vertical": h > w,
                    "zeros": set(token_core) <= {"0"},
                    "length_ok": 6 <= len(token_core) <= 14 and digits(token_core) >= 5,
                    "height": h,
                }
            )
    return found
