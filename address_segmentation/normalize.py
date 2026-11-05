"""Text normalization, accent folding, and tokenization with source offsets."""

from collections.abc import Iterator
from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True, slots=True)
class Token:
    """A token with raw text, normalized forms, and character offsets in the source string."""

    text: str
    nfc: str
    folded: str
    start: int
    end: int


def is_base_alnum(c: str) -> bool:
    """Check if character is an alphanumeric base letter or digit, excluding underscore."""
    return c.isalnum() and c != "_"


def is_combining_mark(c: str) -> bool:
    """Check if character is a Unicode combining diacritical mark (category Mn, Mc, Me)."""
    return unicodedata.category(c).startswith("M")


def fold_accents(text: str) -> str:
    """Casefold and fold all Vietnamese and Latin diacritics, including đ/Đ -> d."""
    # Step 1: Normalize to NFC and casefold
    cf = unicodedata.normalize("NFC", text).casefold()
    # Step 2: Replace Vietnamese d-stroke (đ -> d)
    cf = cf.replace("đ", "d")
    # Step 3: Decompose with NFD so combining diacritical marks separate
    nfd = unicodedata.normalize("NFD", cf)
    # Step 4: Filter out all combining diacritical marks (Unicode general category Mn)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    # Step 5: Return precomposed NFC form of the accent-stripped string
    return unicodedata.normalize("NFC", stripped)


def normalize_nfc(text: str) -> str:
    """Normalize string to Unicode NFC form."""
    return unicodedata.normalize("NFC", text)


def _token_spans(text: str) -> Iterator[tuple[int, int]]:
    """Scan shared address/label boundaries without allocating normalized tokens."""
    n = len(text)
    i = 0

    while i < n:
        # Advance until we find a base alphanumeric character
        if not is_base_alnum(text[i]):
            i += 1
            continue

        token_start = i

        while i < n:
            # Consume segment: base alphanumeric characters and combining marks
            while i < n and (is_base_alnum(text[i]) or is_combining_mark(text[i])):
                i += 1

            # Check if followed by '/' and another base alphanumeric character
            if i < n and text[i] == "/" and (i + 1 < n) and is_base_alnum(text[i + 1]):
                i += 1  # consume slash and continue consuming next segment
            else:
                break

        yield token_start, i


def tokenize(text: str) -> list[Token]:
    """Tokenize original text with accurate source offsets, including NFD marks.

    Slash-connected forms remain whole (3/2 cannot match inside 53/2).
    """
    tokens: list[Token] = []
    for token_start, token_end in _token_spans(text):
        raw = text[token_start:token_end]
        nfc = unicodedata.normalize("NFC", raw).casefold()
        tokens.append(Token(raw, nfc, fold_accents(nfc), token_start, token_end))
    return tokens


def tokenize_label(
    label: str, *, token_cache: dict[str, tuple[str, str]] | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Normalize labels without allocating source-offset Token objects.

    A catalog-local cache reuses repeated words while building one dictionary;
    it is discarded after construction and never caches request results.
    """
    nfc_words, folded_words = [], []
    for start, end in _token_spans(label):
        raw = label[start:end]
        forms = token_cache.get(raw) if token_cache is not None else None
        if forms is None:
            nfc = unicodedata.normalize("NFC", raw).casefold()
            forms = (nfc, fold_accents(nfc))
            if token_cache is not None:
                token_cache[raw] = forms
        nfc_words.append(forms[0])
        folded_words.append(forms[1])
    return tuple(nfc_words), tuple(folded_words)
