"""Text normalization, accent folding, and tokenization with source offsets."""

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


def tokenize(text: str) -> list[Token]:
    """Tokenize original text into tokens with accurate source character offsets.

    Attaches combining marks to base letters in decomposed Unicode (NFD).
    Preserves slash-connected tokens (e.g. 3/2, 56/33) while preventing 3/2 from
    matching within house numbers like 53/2.
    """
    tokens: list[Token] = []
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

        token_end = i
        raw = text[token_start:token_end]
        nfc = unicodedata.normalize("NFC", raw).casefold()
        folded = fold_accents(nfc)
        tokens.append(
            Token(
                text=raw,
                nfc=nfc,
                folded=folded,
                start=token_start,
                end=token_end,
            )
        )

    return tokens


def tokenize_label(label: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Tokenize a canonical label into tuples of NFC casefold tokens and accent-folded tokens."""
    tokens = tokenize(label)
    nfc_tokens = tuple(t.nfc for t in tokens)
    folded_tokens = tuple(t.folded for t in tokens)
    return nfc_tokens, folded_tokens
