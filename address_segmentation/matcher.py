"""Stage A exact-only address matcher with accent-fold support and conflict rejection."""

from collections.abc import Sequence, Set
from dataclasses import dataclass
from enum import IntEnum

from address_segmentation.catalog import Catalog, CatalogEntry
from address_segmentation.normalize import tokenize


class MatchTier(IntEnum):
    """Evidence tier: exact accented matching outranks accent-folded matching."""
    FOLDED = 1
    EXACT = 2


@dataclass(frozen=True, slots=True)
class CandidateMatch:
    """A match candidate with its catalog entry, source character span, and evidence tier."""
    entry: CatalogEntry
    span: tuple[int, int]
    tier: MatchTier


class AddressMatcher:
    """Synchronous, standard-library address matcher for stage A foundation."""

    def __init__(
        self,
        streets: Sequence[str] | Set[str] = (),
        wards: Sequence[str] | Set[str] = (),
        provinces: Sequence[str] | Set[str] = (),
    ) -> None:
        # Immutable catalog validates inputs, deduplicates exact labels, and builds stable indexes
        self._catalog = Catalog(streets=streets, wards=wards, provinces=provinces)

    def identify(self, address: str) -> dict[str, str | None]:
        """Identify canonical street, ward, and province from free-form address string.

        Returns a fresh dictionary with keys 'street', 'ward', 'province'.
        """
        if not isinstance(address, str):
            raise TypeError(f"address must be a str, got {type(address).__name__}")

        # Empty, whitespace-only, and punctuation-only inputs yield three nulls
        tokens = tokenize(address)
        if not tokens or self._catalog.max_tokens == 0:
            return {"street": None, "ward": None, "province": None}

        num_tokens = len(tokens)
        candidates: list[CandidateMatch] = []

        # Complete token-window matching bounded by available dictionary token lengths
        for length in self._catalog.token_lengths:
            if length > num_tokens:
                continue
            for i in range(num_tokens - length + 1):
                window = tokens[i : i + length]
                span = (window[0].start, window[-1].end)
                nfc_tuple = tuple(t.nfc for t in window)
                folded_tuple = tuple(t.folded for t in window)

                # Tier 1: Exact NFC casefold matches
                exact_entries = self._catalog.lookup_exact(nfc_tuple)
                exact_entry_ids = set()
                for entry in exact_entries:
                    candidates.append(
                        CandidateMatch(entry=entry, span=span, tier=MatchTier.EXACT)
                    )
                    exact_entry_ids.add(entry.entry_id)

                # Tier 2: Accent-folded matches (excluding those already matched as exact)
                folded_entries = self._catalog.lookup_folded(folded_tuple)
                for entry in folded_entries:
                    if entry.entry_id not in exact_entry_ids:
                        candidates.append(
                            CandidateMatch(entry=entry, span=span, tier=MatchTier.FOLDED)
                        )

        # Evaluate candidate evidence per field
        # "Exact accented evidence outranks folded evidence."
        # "Unresolved same-field competing labels return null."
        field_candidates: dict[str, list[CandidateMatch]] = {
            "street": [],
            "ward": [],
            "province": [],
        }
        for cand in candidates:
            field_candidates[cand.entry.field].append(cand)

        field_winners: dict[str, tuple[str, list[tuple[int, int]]] | None] = {
            "street": None,
            "ward": None,
            "province": None,
        }

        for field, cands in field_candidates.items():
            if not cands:
                continue

            # Determine best evidence tier available in this field
            best_tier = max(c.tier for c in cands)
            top_tier_cands = [c for c in cands if c.tier == best_tier]

            # Collect unique canonical labels at top tier
            unique_labels = {c.entry.canonical_label for c in top_tier_cands}

            # If multiple competing canonical labels exist, abstain (return null for this field)
            if len(unique_labels) == 1:
                winning_label = next(iter(unique_labels))
                # Spans supporting this winning label
                spans = [
                    c.span
                    for c in top_tier_cands
                    if c.entry.canonical_label == winning_label
                ]
                field_winners[field] = (winning_label, spans)

        # Cross-field collision check on evidence spans:
        # "Cross-field use of the same evidence span must not emit both fields;
        #  conservatively null affected fields, preserving non-conflicting fields."
        conflicting_fields: set[str] = set()
        fields_with_winners = [
            f for f in ("street", "ward", "province") if field_winners[f] is not None
        ]

        for i, f1 in enumerate(fields_with_winners):
            winner1 = field_winners[f1]
            assert winner1 is not None
            spans1 = winner1[1]
            for f2 in fields_with_winners[i + 1 :]:
                winner2 = field_winners[f2]
                assert winner2 is not None
                spans2 = winner2[1]

                # Check if any evidence span of f1 overlaps with any evidence span of f2
                overlaps = any(
                    max(s1[0], s2[0]) < min(s1[1], s2[1])
                    for s1 in spans1
                    for s2 in spans2
                )
                if overlaps:
                    conflicting_fields.add(f1)
                    conflicting_fields.add(f2)

        # Assemble a fresh result dictionary
        result: dict[str, str | None] = {}
        for field in ("street", "ward", "province"):
            winner = field_winners[field]
            if winner is not None and field not in conflicting_fields:
                result[field] = winner[0]
            else:
                result[field] = None

        return result
