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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.normalize import Token, fold_accents, is_combining_mark, normalize_nfc

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

MARKER_ENDINGS = {
    "ward": frozenset(("phuong", "p", "xa", "x", "tt", "tran")),
    "province": frozenset(("pho", "tp", "tinh", "t")),
    "street": frozenset(("duong", "d")),
}
MARKER_FIELD_BY_ENDING = {
    ending: field for field, endings in MARKER_ENDINGS.items() for ending in endings
}

# Indexed by the folded initial of the marker's final syllable.
JOINED_MARKER_PATTERNS = {
    "p": (("ward", "", "phường", "phuong"), ("province", "thành", "phố", "pho"),
          ("ward", "", "p", "p")),
    "x": (("ward", "", "xã", "xa"), ("ward", "", "x", "x")),
    "t": (("ward", "thị", "trấn", "tran"), ("province", "", "tỉnh", "tinh"),
          ("province", "", "tp", "tp"), ("ward", "", "tt", "tt"),
          ("province", "", "t", "t")),
    "d": (("street", "", "đường", "duong"), ("street", "", "đ", "d")),
}


@dataclass(frozen=True, slots=True)
class JoinedMarker:
    field: str
    word: str
    start: int
    body: Token
    exact: bool
    nfc: tuple[str, ...]
    folded: tuple[str, ...]


def joined_markers(tokens: list[Token], index: int, address: str, *, catalog: "Catalog | None" = None):
    """Yield alternative splits of a token, keeping its literal view available."""
    token = tokens[index]
    if not token.folded:
        return
    recovery_markers = {}
    for field, head, tail, folded_tail in JOINED_MARKER_PATTERNS.get(token.folded[0], ()):
        if len(token.folded) <= len(folded_tail) or not token.folded.startswith(folded_tail):
            continue
        if catalog is not None and not catalog.window_lengths(token.folded[len(folded_tail):]):
            continue
        if catalog is None and len(folded_tail) == 1 and not token.text[0].isupper():
            continue
        start = token.start
        nfc_head, folded_head = (), ()
        if head:
            if index == 0:
                continue
            previous = tokens[index - 1]
            delimiter = address[previous.end:token.start]
            if (previous.folded != fold_accents(head)
                    or "\n" in delimiter or not is_valid_marker_delimiter(delimiter)):
                continue
            start = previous.start
            nfc_head, folded_head = (previous.nfc,), (previous.folded,)

        # The folded Vietnamese marker has one base character per letter.
        # Count in the original token so decomposed accents retain their offsets.
        cut = 0
        for _ in folded_tail:
            cut += 1
            while cut < len(token.text) and is_combining_mark(token.text[cut]):
                cut += 1
            if cut == len(token.text):
                break
        prefix, suffix = token.text[:cut], token.text[cut:]
        # For a one-letter abbreviation, require a visible title-case boundary
        # before admitting edits. Exact lookup can support case-insensitive splits.
        if not suffix or not suffix[0].isalnum():
            continue
        allows_recovery = len(folded_tail) > 1 or (
            prefix.isupper() and suffix[0].isupper() and any(c.islower() for c in suffix[1:])
        )
        if (catalog is None and not allows_recovery) or fold_accents(prefix) != folded_tail:
            continue
        nfc = normalize_nfc(suffix).casefold()
        body = Token(suffix, nfc, fold_accents(nfc), token.start + cut, token.end)
        prefix_nfc = normalize_nfc(prefix).casefold()
        marker_nfc = (*nfc_head, prefix_nfc)
        marker_folded = (*folded_head, folded_tail)
        exact = (not head or nfc_head == (head,)) and (
            prefix_nfc == tail or (tail == "đ" and prefix_nfc == "d")
        )
        marker = JoinedMarker(field, marker_folded[0], start, body, exact, marker_nfc, marker_folded)
        if catalog is not None:
            yield marker
        else:
            # For recovery, prefer exact marker spelling and then greater
            # coverage. For example, X + An has stronger spelling evidence
            # than the accent-folded Xa + n split.
            key = (start, field)
            previous = recovery_markers.get(key)
            if previous is None or (marker.exact, body.start) > (previous.exact, previous.body.start):
                recovery_markers[key] = marker
    yield from recovery_markers.values()


def _retrieve_joined(tokens, catalog, address, literal_candidates):
    literal_scores = {}
    for candidate in literal_candidates:
        key = (candidate.start, candidate.end, candidate.path)
        literal_scores[key] = max(candidate.score, literal_scores.get(key, 0.0))

    for index in range(len(tokens)):
        if tokens[index].folded[:1] not in JOINED_MARKER_PATTERNS:
            continue
        for marker in joined_markers(tokens, index, address, catalog=catalog):
            for length in catalog.window_lengths(marker.body.folded):
                end_index = index + length
                if end_index > len(tokens):
                    continue
                window = [marker.body, *tokens[index + 1:end_index]]
                if any(any(c in address[left.end:right.start] for c in ",;\n")
                       for left, right in zip(window, window[1:])):
                    continue
                end = window[-1].end
                if (window[-1].folded.isdigit() and end_index < len(tokens)
                        and tokens[end_index].folded.isdigit()
                        and address[end:tokens[end_index].start].isspace()):
                    continue
                nfc = tuple(t.nfc for t in window)
                folded = tuple(t.folded for t in window)
                exact_ids = {entry.entry_id for entry in catalog.lookup_body_exact(nfc)}
                for entry in catalog.lookup_body_folded(folded):
                    if entry.field != marker.field or not accepts_marker(entry, marker.word):
                        continue
                    exact_body = entry.entry_id in exact_ids
                    full = entry.has_prefix and marker.folded == entry.folded_tokens[:len(entry.prefix_tokens)]
                    if full:
                        exact = exact_body and marker.nfc == entry.prefix_tokens
                    else:
                        exact = exact_body and (marker.exact or not entry.has_prefix)
                    path = "exact" if exact else "folded"
                    if full:
                        score = (FULL_EXACT_SCORE if exact else FULL_FOLDED_SCORE) + 5.0
                    elif exact:
                        score = BODY_EXACT_WITH_MARKER_SCORE if entry.has_prefix else FULL_EXACT_SCORE
                    else:
                        score = BODY_FOLDED_WITH_MARKER_SCORE if entry.has_prefix else FULL_FOLDED_SCORE
                    # Equal-quality literal evidence remains viable at the same
                    # occurrence. A joined interpretation gets no free advantage.
                    literal_score = literal_scores.get((marker.start, end, path))
                    if literal_score is not None:
                        score = min(score, literal_score)
                    if literal_candidates:
                        start_index = index if marker.start == tokens[index].start else index - 1
                        for field in MARKER_ENDINGS:
                            preceding = find_preceding_marker(tokens, start_index, field, address)
                            if preceding and (preceding[0], end, path) in literal_scores:
                                score = min(score, BARE_EXACT_SCORE if exact else BARE_FOLDED_SCORE)
                                break
                    yield Candidate(entry, marker.start, end, path, score=score,
                                    context_marker=True, reasons=("joined_marker", "marker"))


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
    if MARKER_FIELD_BY_ENDING.get(prev_tok.folded) != field:
        return None
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

    for candidate in _retrieve_joined(tokens, catalog, address, raw_candidates):
        raw_candidates.append(candidate)
        if candidate.path == "exact":
            marked_spans_by_field[candidate.field].append(candidate.span)

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
