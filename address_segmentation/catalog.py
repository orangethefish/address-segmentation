"""Dictionary validation, canonical entry representations, and stable collision-safe indexes.

# Catalog and Prefix-Body Representation (Stage C)

This module builds immutable catalogs from field label sequences.
Labels are validated, deduplicated, and assigned stable IDs sorted lexicographically
by (field, canonical_label) so IDs are independent of dictionary input order.

## Prefix-Body Decomposition

Stage C decomposes canonical labels into administrative prefix and name body:
- Ward prefixes: 'phường', 'phuong', 'p.', 'xã', 'xa', 'x.', 'thị trấn', 'thi tran', 'tt.'
- Province prefixes: 'thành phố', 'thanh pho', 'tp.', 'tỉnh', 'tinh', 't.'
- Street prefixes: 'đường', 'duong', 'đ.', 'd.'

When an entry canonically includes a prefix (e.g., 'Phường Bến Thành'), its body ('Bến Thành')
is indexed separately so addresses with abbreviated markers (e.g., 'P. Bến Thành') or bare names
('Bến Thành') can be retrieved and properly scored against full matches.
"""

from collections.abc import Sequence, Set
from dataclasses import dataclass
from typing import Any

from address_segmentation.normalize import tokenize_label

# Lexicographically sorted field names for stable ID assignment: 'province' < 'street' < 'ward'
LEXICOGRAPHICAL_FIELDS: tuple[str, ...] = ("province", "street", "ward")

# Field-specific recognized administrative prefixes (longest sequences first)
WARD_PREFIX_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("thị", "trấn"),
    ("thi", "tran"),
    ("phường",),
    ("phuong",),
    ("p",),
    ("xã",),
    ("xa",),
    ("x",),
    ("tt",),
)

PROVINCE_PREFIX_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("thành", "phố"),
    ("thanh", "pho"),
    ("tp",),
    ("tỉnh",),
    ("tinh",),
    ("t",),
)

STREET_PREFIX_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("đường",),
    ("duong",),
    ("đ",),
    ("d",),
)

FIELD_PREFIX_PATTERNS: dict[str, tuple[tuple[str, ...], ...]] = {
    "ward": WARD_PREFIX_PATTERNS,
    "province": PROVINCE_PREFIX_PATTERNS,
    "street": STREET_PREFIX_PATTERNS,
}


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """An immutable dictionary entry with stable ID, field, canonical label, and tokens.

    Attributes:
        entry_id: Stable deterministic integer ID.
        field: Field name ('province', 'street', 'ward').
        canonical_label: Original canonical string label from dictionary.
        nfc_tokens: Casefolded Unicode NFC token sequence of the full label.
        folded_tokens: Accent-folded token sequence of the full label.
        prefix_tokens: Tokens corresponding to an administrative prefix, if present.
        body_nfc_tokens: Casefolded Unicode NFC token sequence of the body.
        body_folded_tokens: Accent-folded token sequence of the body.
        has_prefix: True if canonical label begins with a recognized administrative prefix.
    """

    entry_id: int
    field: str
    canonical_label: str
    nfc_tokens: tuple[str, ...]
    folded_tokens: tuple[str, ...]
    prefix_tokens: tuple[str, ...] = ()
    body_nfc_tokens: tuple[str, ...] = ()
    body_folded_tokens: tuple[str, ...] = ()
    has_prefix: bool = False


def extract_prefix_body(
    field: str,
    nfc_tokens: tuple[str, ...],
    folded_tokens: tuple[str, ...],
) -> tuple[bool, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Extract administrative prefix and body tokens for an entry.

    Returns (has_prefix, prefix_tokens, body_nfc_tokens, body_folded_tokens).
    """
    patterns = FIELD_PREFIX_PATTERNS.get(field, ())
    for pattern in patterns:
        plen = len(pattern)
        if len(nfc_tokens) > plen:
            # Check NFC match or folded match against prefix pattern
            if nfc_tokens[:plen] == pattern or folded_tokens[:plen] == pattern:
                return (
                    True,
                    nfc_tokens[:plen],
                    nfc_tokens[plen:],
                    folded_tokens[plen:],
                )
    return (False, (), nfc_tokens, folded_tokens)


def validate_field_sequence(labels: Any, field_name: str) -> list[str]:
    """Validate that labels is a finite sequence or set of non-empty strings.

    Rejects scalar strings, non-sequences, non-string items, and blank strings.
    """
    if isinstance(labels, (str, bytes)):
        raise TypeError(
            f"{field_name} must be a sequence of strings, not a scalar {type(labels).__name__}"
        )
    if not isinstance(labels, (Sequence, Set)):
        raise TypeError(
            f"{field_name} must be a finite sequence or set, got {type(labels).__name__}"
        )

    validated: list[str] = []
    for idx, item in enumerate(labels):
        if not isinstance(item, str):
            raise TypeError(
                f"Item at index {idx} in {field_name} must be str, got {type(item).__name__}"
            )
        if not item.strip():
            raise ValueError(
                f"Item at index {idx} in {field_name} cannot be blank or whitespace-only"
            )
        nfc_tokens, _ = tokenize_label(item)
        if not nfc_tokens:
            raise ValueError(
                f"Item at index {idx} in {field_name} contains no recognizable word or numeric tokens: {item!r}"
            )
        validated.append(item)

    return validated


class Catalog:
    """Immutable catalog storing entries and collision-safe token lookup indexes."""

    def __init__(
        self,
        streets: Sequence[str] | Set[str] = (),
        wards: Sequence[str] | Set[str] = (),
        provinces: Sequence[str] | Set[str] = (),
    ) -> None:
        validated_streets = validate_field_sequence(streets, "streets")
        validated_wards = validate_field_sequence(wards, "wards")
        validated_provinces = validate_field_sequence(provinces, "provinces")

        # Deduplicate exact labels within each field and sort deterministically
        unique_by_field: dict[str, list[str]] = {
            "street": sorted(set(validated_streets)),
            "ward": sorted(set(validated_wards)),
            "province": sorted(set(validated_provinces)),
        }

        # Build entries sorted lexicographically by (field, canonical_label)
        # to guarantee stable documented IDs ('province' < 'street' < 'ward')
        entries: list[CatalogEntry] = []
        entry_id = 0
        for field in LEXICOGRAPHICAL_FIELDS:
            for label in unique_by_field[field]:
                nfc_tokens, folded_tokens = tokenize_label(label)
                has_prefix, prefix_tokens, body_nfc, body_folded = extract_prefix_body(
                    field, nfc_tokens, folded_tokens
                )
                entry = CatalogEntry(
                    entry_id=entry_id,
                    field=field,
                    canonical_label=label,
                    nfc_tokens=nfc_tokens,
                    folded_tokens=folded_tokens,
                    prefix_tokens=prefix_tokens,
                    body_nfc_tokens=body_nfc,
                    body_folded_tokens=body_folded,
                    has_prefix=has_prefix,
                )
                entries.append(entry)
                entry_id += 1

        self._entries: tuple[CatalogEntry, ...] = tuple(entries)

        exact_map: dict[tuple[str, ...], list[CatalogEntry]] = {}
        folded_map: dict[tuple[str, ...], list[CatalogEntry]] = {}
        body_exact_map: dict[tuple[str, ...], list[CatalogEntry]] = {}
        body_folded_map: dict[tuple[str, ...], list[CatalogEntry]] = {}
        lengths: set[int] = set()

        for entry in self._entries:
            exact_map.setdefault(entry.nfc_tokens, []).append(entry)
            folded_map.setdefault(entry.folded_tokens, []).append(entry)
            lengths.add(len(entry.nfc_tokens))

            # Body forms index
            body_exact_map.setdefault(entry.body_nfc_tokens, []).append(entry)
            body_folded_map.setdefault(entry.body_folded_tokens, []).append(entry)
            lengths.add(len(entry.body_nfc_tokens))

        # Freeze indexes to immutable tuples and frozenset
        self._exact_index: dict[tuple[str, ...], tuple[CatalogEntry, ...]] = {
            k: tuple(v) for k, v in exact_map.items()
        }
        self._folded_index: dict[tuple[str, ...], tuple[CatalogEntry, ...]] = {
            k: tuple(v) for k, v in folded_map.items()
        }
        self._body_exact_index: dict[tuple[str, ...], tuple[CatalogEntry, ...]] = {
            k: tuple(v) for k, v in body_exact_map.items()
        }
        self._body_folded_index: dict[tuple[str, ...], tuple[CatalogEntry, ...]] = {
            k: tuple(v) for k, v in body_folded_map.items()
        }
        self._token_lengths: frozenset[int] = frozenset(lengths)
        self._max_tokens: int = max(self._token_lengths) if self._token_lengths else 0

    @property
    def entries(self) -> tuple[CatalogEntry, ...]:
        return self._entries

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def token_lengths(self) -> frozenset[int]:
        return self._token_lengths

    def lookup_exact(self, nfc_tokens: tuple[str, ...]) -> tuple[CatalogEntry, ...]:
        """Look up all entries matching exact full NFC casefolded tokens (immutable tuple)."""
        return self._exact_index.get(nfc_tokens, ())

    def lookup_folded(self, folded_tokens: tuple[str, ...]) -> tuple[CatalogEntry, ...]:
        """Look up all entries matching full accent-folded tokens (immutable tuple)."""
        return self._folded_index.get(folded_tokens, ())

    def lookup_body_exact(self, nfc_tokens: tuple[str, ...]) -> tuple[CatalogEntry, ...]:
        """Look up entries whose body matches exact NFC casefolded tokens (immutable tuple)."""
        return self._body_exact_index.get(nfc_tokens, ())

    def lookup_body_folded(self, folded_tokens: tuple[str, ...]) -> tuple[CatalogEntry, ...]:
        """Look up entries whose body matches accent-folded tokens (immutable tuple)."""
        return self._body_folded_index.get(folded_tokens, ())
