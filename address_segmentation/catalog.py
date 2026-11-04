"""Dictionary validation, canonical entry representations, and stable collision-safe indexes."""

from collections.abc import Sequence, Set
from dataclasses import dataclass
from typing import Any

from address_segmentation.normalize import tokenize_label

# Lexicographically sorted field names for stable ID assignment: 'province' < 'street' < 'ward'
LEXICOGRAPHICAL_FIELDS: tuple[str, ...] = ("province", "street", "ward")


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """An immutable dictionary entry with stable ID, field, canonical label, and tokens."""

    entry_id: int
    field: str
    canonical_label: str
    nfc_tokens: tuple[str, ...]
    folded_tokens: tuple[str, ...]


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
                entry = CatalogEntry(
                    entry_id=entry_id,
                    field=field,
                    canonical_label=label,
                    nfc_tokens=nfc_tokens,
                    folded_tokens=folded_tokens,
                )
                entries.append(entry)
                entry_id += 1

        self._entries: tuple[CatalogEntry, ...] = tuple(entries)

        exact_map: dict[tuple[str, ...], list[CatalogEntry]] = {}
        folded_map: dict[tuple[str, ...], list[CatalogEntry]] = {}
        lengths: set[int] = set()

        for entry in self._entries:
            exact_map.setdefault(entry.nfc_tokens, []).append(entry)
            folded_map.setdefault(entry.folded_tokens, []).append(entry)
            lengths.add(len(entry.nfc_tokens))

        # Freeze indexes to immutable tuples and frozenset
        self._exact_index: dict[tuple[str, ...], tuple[CatalogEntry, ...]] = {
            k: tuple(v) for k, v in exact_map.items()
        }
        self._folded_index: dict[tuple[str, ...], tuple[CatalogEntry, ...]] = {
            k: tuple(v) for k, v in folded_map.items()
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
        """Look up all entries matching exact NFC casefolded tokens (immutable tuple)."""
        return self._exact_index.get(nfc_tokens, ())

    def lookup_folded(self, folded_tokens: tuple[str, ...]) -> tuple[CatalogEntry, ...]:
        """Look up all entries matching accent-folded tokens (immutable tuple)."""
        return self._folded_index.get(folded_tokens, ())
