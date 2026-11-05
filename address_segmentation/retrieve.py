"""Exact and folded token-window candidate retrieval with prefix-body recognition.

# Retrieval Architecture (Stage C)

This module scans normalized token sequences across bounded token windows:
1. Complete exact and folded matching against full canonical catalog entries.
2. Field-specific prefix-body matching (e.g., 'Phường Bến Thành' matched via 'Bến Thành').
3. Administrative marker detection ('Đường', 'Phường', 'P.', 'Thành phố', 'TP.', 'Tỉnh', etc.)
   immediately preceding candidate bodies.
4. Deduplication by (entry ID, start, end) character offsets, strictly preferring exact accented
   matches over accent-folded alternatives.
5. Strict containment: candidates cannot claim administrative prefixes from unrelated spans
   or across major punctuation boundaries.
"""

from typing import TYPE_CHECKING

from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.normalize import Token

if TYPE_CHECKING:
    from address_segmentation.catalog import Catalog, CatalogEntry

FULL_EXACT_SCORE: float = 120.0
BODY_EXACT_WITH_MARKER_SCORE: float = 115.0
BARE_EXACT_SCORE: float = 100.0

FULL_FOLDED_SCORE: float = 70.0
BODY_FOLDED_WITH_MARKER_SCORE: float = 65.0
BARE_FOLDED_SCORE: float = 50.0

MARKER_KINDS = {
    "phuong": "phuong", "p": "phuong", "xa": "xa", "x": "xa",
    "thi": "thi_tran", "tt": "thi_tran",
    "thanh": "thanh_pho", "tp": "thanh_pho", "tinh": "tinh", "t": "tinh",
    "duong": "duong", "d": "duong",
}


def accepts_marker(entry: "CatalogEntry", marker_word: str | None) -> bool:
    """A typed marker cannot support a different canonical administrative type.

    Bare canonical labels have no type to contradict. Both arguments use folded
    tokens, so accented, accentless and abbreviated markers share the same rule.
    """
    if marker_word is None or not entry.has_prefix:
        return True
    return MARKER_KINDS.get(marker_word) == MARKER_KINDS.get(entry.folded_tokens[0])


def is_valid_marker_delimiter(sep: str) -> bool:
    """Return True if separation between marker and body is strictly whitespace/dots."""
    return all(c.isspace() or c == "." for c in sep)


def find_preceding_marker(
    tokens: list[Token],
    body_start_idx: int,
    field: str,
    address: str,
) -> tuple[int, bool] | None:
    """Check if token(s) immediately preceding body_start_idx form an administrative marker.

    Returns (marker_start_char_offset, is_exact) or None.
    """
    if body_start_idx < 1:
        return None

    prev_tok = tokens[body_start_idx - 1]
    sep = address[prev_tok.end : tokens[body_start_idx].start]
    if not is_valid_marker_delimiter(sep):
        return None

    # Check 2-token administrative markers first
    if body_start_idx >= 2:
        tok2 = tokens[body_start_idx - 2]
        sep2 = address[tok2.end : prev_tok.start]
        if is_valid_marker_delimiter(sep2):
            if field == "ward":
                pair_nfc = (tok2.nfc, prev_tok.nfc)
                if pair_nfc == ("thị", "trấn"):
                    return (tok2.start, True)
                pair_folded = (tok2.folded, prev_tok.folded)
                if pair_folded == ("thi", "tran"):
                    return (tok2.start, False)
            elif field == "province":
                pair_nfc = (tok2.nfc, prev_tok.nfc)
                if pair_nfc == ("thành", "phố"):
                    return (tok2.start, True)
                pair_folded = (tok2.folded, prev_tok.folded)
                if pair_folded == ("thanh", "pho"):
                    return (tok2.start, False)

    # Check 1-token administrative markers
    if field == "ward":
        if prev_tok.nfc in ("phường", "xã", "p", "x", "tt"):
            return (prev_tok.start, True)
        if prev_tok.folded in ("phuong", "xa"):
            return (prev_tok.start, False)
    elif field == "province":
        if prev_tok.nfc in ("tỉnh", "tp", "t"):
            return (prev_tok.start, True)
        if prev_tok.folded in ("tinh",):
            return (prev_tok.start, False)
    elif field == "street":
        if prev_tok.nfc in ("đường", "đ", "d"):
            return (prev_tok.start, True)
        if prev_tok.folded in ("duong",):
            return (prev_tok.start, False)

    return None


def retrieve_candidates(
    tokens: list[Token],
    catalog: "Catalog",
    config: MatcherConfig,
    address: str,
) -> list[Candidate]:
    """Retrieve all exact and folded window candidates from input tokens.

    Deduplicates candidates on (entry ID, start, end) preferring exact path.
    """
    num_tokens = len(tokens)
    if num_tokens == 0 or catalog.max_tokens == 0:
        return []

    raw_candidates: list[Candidate] = []
    tokens_by_start = {t.start: t for t in tokens}
    token_indices = {t.start: index for index, t in enumerate(tokens)}

    # Map to track preceding markers for conjunction propagation
    # e.g., in "Góc đường Lê Lợi và Trần Hưng Đạo", both get marker credit
    marked_spans_by_field: dict[str, list[tuple[int, int]]] = {
        "street": [],
        "ward": [],
        "province": [],
    }

    # Bounded token-window scanning
    for i, token in enumerate(tokens):
        for length in catalog.window_lengths(token.folded):
            if i + length > num_tokens:
                continue
            window = tokens[i : i + length]
            if any(any(c in address[left.end:right.start] for c in ",;\n")
                   for left, right in zip(window, window[1:])):
                continue  # A component cannot straddle explicit address separators.
            body_start_char = window[0].start
            body_end_char = window[-1].end
            window_nfc = tuple(t.nfc for t in window)
            window_folded = tuple(t.folded for t in window)
            if length == 1:
                marker_info = find_preceding_marker(tokens, i, "province", address)
                for entry in catalog.lookup_province_initials(window_folded[0]):
                    marker_word = tokens_by_start[marker_info[0]].folded if marker_info else None
                    if not accepts_marker(entry, marker_word):
                        continue
                    raw_candidates.append(Candidate(
                        entry=entry, start=marker_info[0] if marker_info else body_start_char,
                        end=body_end_char, path="folded",
                        score=60.0 if marker_info else 45.0,
                        context_marker=marker_info is not None, reasons=("province_initials",),
                    ))
            if (window[-1].folded.isdigit() and i + length < num_tokens
                    and tokens[i + length].folded.isdigit()
                    and address[window[-1].end:tokens[i + length].start].isspace()):
                continue  # An unfinished numeric sequence is not a complete name.

            # --- 1. Full matches ---
            exact_full = catalog.lookup_exact(window_nfc)
            exact_full_ids = set()
            for entry in exact_full:
                exact_full_ids.add(entry.entry_id)
                # Check preceding marker if entry does not have prefix in canonical label
                has_marker = entry.has_prefix
                cand_start = body_start_char
                if not has_marker:
                    marker_info = find_preceding_marker(tokens, i, entry.field, address)
                    if marker_info is not None:
                        has_marker = True
                        cand_start = marker_info[0]

                if entry.field != "street" and not has_marker and all(t.replace("/", "").isdigit() for t in window_folded):
                    continue
                score = (FULL_EXACT_SCORE if has_marker else BARE_EXACT_SCORE) + (5.0 if entry.has_prefix else 0.0)
                reasons = ("exact_full",) + (("marker",) if has_marker else ())
                raw_candidates.append(
                    Candidate(
                        entry=entry,
                        start=cand_start,
                        end=body_end_char,
                        path="exact",
                        cost=0,
                        score=score,
                        context_marker=has_marker,
                        reasons=reasons,
                    )
                )
                if has_marker:
                    marked_spans_by_field[entry.field].append((cand_start, body_end_char))

            folded_full = catalog.lookup_folded(window_folded)
            for entry in folded_full:
                if entry.entry_id in exact_full_ids:
                    continue
                has_marker = entry.has_prefix
                cand_start = body_start_char
                if not has_marker:
                    marker_info = find_preceding_marker(tokens, i, entry.field, address)
                    if marker_info is not None:
                        has_marker = True
                        cand_start = marker_info[0]

                if entry.field != "street" and not has_marker and all(t.replace("/", "").isdigit() for t in window_folded):
                    continue
                score = (FULL_FOLDED_SCORE if has_marker else BARE_FOLDED_SCORE) + (5.0 if entry.has_prefix else 0.0)
                reasons = ("folded_full",) + (("marker",) if has_marker else ())
                raw_candidates.append(
                    Candidate(
                        entry=entry,
                        start=cand_start,
                        end=body_end_char,
                        path="folded",
                        cost=0,
                        score=score,
                        context_marker=has_marker,
                        reasons=reasons,
                    )
                )

            # --- 2. Body matches for entries with canonical prefixes ---
            exact_body = catalog.lookup_body_exact(window_nfc)
            exact_body_ids = set()
            for entry in exact_body:
                if not entry.has_prefix:
                    # Entries without prefix were already handled as full matches
                    continue
                exact_body_ids.add(entry.entry_id)
                marker_info = find_preceding_marker(tokens, i, entry.field, address)
                if marker_info is not None and not accepts_marker(entry, tokens_by_start[marker_info[0]].folded):
                    continue
                has_marker = marker_info is not None
                if not has_marker and all(t == "so" or t.replace("/", "").isdigit() for t in window_folded):
                    continue
                cand_start = marker_info[0] if marker_info is not None else body_start_char
                marker_exact = marker_info[1] if marker_info is not None else True
                path = "exact" if marker_exact else "folded"
                if path == "exact":
                    score = BODY_EXACT_WITH_MARKER_SCORE if has_marker else BARE_EXACT_SCORE
                else:
                    score = BODY_FOLDED_WITH_MARKER_SCORE if has_marker else BARE_FOLDED_SCORE
                reasons = ("exact_body",) + (("marker",) if has_marker else ())
                raw_candidates.append(
                    Candidate(
                        entry=entry,
                        start=cand_start,
                        end=body_end_char,
                        path=path,
                        cost=0,
                        score=score,
                        context_marker=has_marker,
                        reasons=reasons,
                    )
                )
                if has_marker:
                    marked_spans_by_field[entry.field].append((cand_start, body_end_char))

            folded_body = catalog.lookup_body_folded(window_folded)
            for entry in folded_body:
                if not entry.has_prefix or entry.entry_id in exact_body_ids:
                    continue
                marker_info = find_preceding_marker(tokens, i, entry.field, address)
                if marker_info is not None and not accepts_marker(entry, tokens_by_start[marker_info[0]].folded):
                    continue
                has_marker = marker_info is not None
                if not has_marker and all(t == "so" or t.replace("/", "").isdigit() for t in window_folded):
                    continue
                cand_start = marker_info[0] if marker_info is not None else body_start_char
                score = BODY_FOLDED_WITH_MARKER_SCORE if has_marker else BARE_FOLDED_SCORE
                reasons = ("folded_body",) + (("marker",) if has_marker else ())
                raw_candidates.append(
                    Candidate(
                        entry=entry,
                        start=cand_start,
                        end=body_end_char,
                        path="folded",
                        cost=0,
                        score=score,
                        context_marker=has_marker,
                        reasons=reasons,
                    )
                )

    # Conjunction-marker propagation:
    # In coordinate structures like "đường X và Y", propagate marker evidence to Y
    # so unrelated competing streets remain equal and abstain deterministically.
    enhanced_candidates: list[Candidate] = []
    marked_street_ends = {end for _, end in marked_spans_by_field["street"]}
    for cand in raw_candidates:
        if not cand.context_marker and cand.entry.field == "street":
            # Check if immediately preceded by conjunction 'và' / 'va'
            # and a prior marked street candidate
            start_idx = token_indices.get(cand.start)
            preceding_tok_idx = start_idx - 1 if start_idx is not None else None
            if preceding_tok_idx is not None and preceding_tok_idx >= 1:
                conj_tok = tokens[preceding_tok_idx]
                if conj_tok.nfc in ("và", "va"):
                    # Check if any prior marked street span ends right before the conjunction
                    m_end = tokens[preceding_tok_idx - 1].end
                    if m_end in marked_street_ends and address[m_end:conj_tok.start].isspace():
                        score = (
                            FULL_EXACT_SCORE
                            if cand.path == "exact"
                            else FULL_FOLDED_SCORE
                        )
                        cand = Candidate(
                            entry=cand.entry,
                            start=cand.start,
                            end=cand.end,
                            path=cand.path,
                            cost=cand.cost,
                            score=score,
                            context_marker=True,
                            reasons=cand.reasons + ("conjunction_marker",),
                        )
        enhanced_candidates.append(cand)

    # Deduplicate by (entry ID, start, end) character offsets, strictly preferring exact
    deduped: dict[tuple[int, int, int], Candidate] = {}
    for cand in enhanced_candidates:
        key = (cand.entry.entry_id, cand.start, cand.end)
        if key not in deduped:
            deduped[key] = cand
        else:
            existing = deduped[key]
            # Exact path beats folded path
            if existing.path == "folded" and cand.path == "exact":
                deduped[key] = cand
            elif existing.path == cand.path:
                # Prefer higher score / marker presence
                if cand.score > existing.score:
                    deduped[key] = cand

    # Sort deterministically
    return sorted(
        deduped.values(),
        key=lambda c: (-c.score, c.cost, c.start, c.end, c.entry.entry_id),
    )
