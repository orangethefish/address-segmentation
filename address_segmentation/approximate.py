"""Approximate typo recovery using Optimal String Alignment and trigram indexing.

# Approximate Recovery Architecture (Stage D)

Stage D adds bounded, deterministic typo recovery for address components:
1. Indexing: Prepares per-entry FULL and BODY folded forms once per matcher.
   Constructs char-trigram inverted postings lists and length buckets.
   Maintains a tiny bounded bucket for short names (length 5..7).
2. Spans: Extracts component windows around administrative markers and
   comma/newline-delimited segments, stripping leading house numbers.
3. Non-Greedy Extension: Trailing unexplained name tokens after an exact prefix
   trigger extended recovery windows (e.g., 'An Phú' + 'dnog' -> 'An Phú Đông').
4. Candidate Retrieval: Sorts query trigrams rare-first, looks up postings
   bounded by `max_postings`, and generates shortlists up to `max_candidates_per_span`.
   Retrieved candidates appear before distance rejection for retrieval recall diagnostics.
5. Verification: Computes thresholded OSA distance bounded by `max_verifications`
   and `max_edits`. Conservative acceptance requires length >= 5, max 1 edit
   for < 9 letters, max 2 edits for longer, and enough unchanged text.
   Numeric names and digits are strictly exact only.
6. Budgets: Tracks exact counts for fuzzy_spans, postings, verifications, and
   candidates_per_span. If any budget is exhausted, affected fuzzy fields and
   unresolved suspicious exact prefixes conservatively abstain. Independent exact
   fields are preserved.
"""

from bisect import bisect_left
import re
from typing import Any
from address_segmentation.retrieve import (
    JOINED_MARKER_PATTERNS, MARKER_FIELD_BY_ENDING, accepts_marker, find_preceding_marker, joined_markers,
)
from dataclasses import dataclass

from address_segmentation.catalog import Catalog, CatalogEntry
from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.normalize import Token
from address_segmentation.verify import thresholded_osa_distance

COMPONENT_SEPARATOR = re.compile(r"[,;\n]")


class ExhaustedDict(dict):
    """Dictionary of budget exhaustion flags that evaluates truthy if any flag is True."""

    def __bool__(self) -> bool:
        return any(self.values())


@dataclass(frozen=True, slots=True)
class TargetForm:
    """An indexed catalog target form (full canonical or body only)."""

    form_id: int
    entry: CatalogEntry
    form_type: str  # 'full' or 'body'
    folded_text: str
    length: int
    trigrams: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateSpan:
    """A candidate text window in the source address for approximate matching."""

    start: int
    end: int
    words: tuple[Token, ...]
    folded_text: str
    has_marker: bool
    field_hint: str | None
    is_extension_of: Candidate | None
    marker_word: str | None = None
    joined: bool = False
    literal_alternatives: tuple[str, ...] = ()


def extract_trigrams(text: str) -> tuple[str, ...]:
    """Extract character trigrams from normalized string."""
    if len(text) < 3:
        return ()
    return tuple(text[i : i + 3] for i in range(len(text) - 2))


class ApproximateIndex:
    """Immutable inverted trigram index and length buckets for catalog forms."""

    def __init__(self, catalog: Catalog) -> None:
        forms: list[TargetForm] = []
        postings: dict[str, list[TargetForm]] = {}
        length_buckets: dict[int, list[TargetForm]] = {}
        short_name_forms: list[TargetForm] = []
        form_id = 0

        # Forms are prepared once per matcher
        for entry in catalog.entries:
            # 1. Full folded form
            full_folded = " ".join(entry.folded_tokens)
            # Digits and short names (< 5) cannot be repaired fuzzily
            if not any(c.isdigit() for c in full_folded) and len(full_folded) >= 5:
                full_grams = extract_trigrams(full_folded)
                tf_full = TargetForm(
                    form_id=form_id,
                    entry=entry,
                    form_type="full",
                    folded_text=full_folded,
                    length=len(full_folded),
                    trigrams=full_grams,
                )
                forms.append(tf_full)
                form_id += 1
                length_buckets.setdefault(tf_full.length, []).append(tf_full)
                if 5 <= tf_full.length <= 7:
                    short_name_forms.append(tf_full)
                for g in set(full_grams):
                    postings.setdefault(g, []).append(tf_full)

            # 2. Body folded form if distinct from full folded form
            if entry.has_prefix:
                body_folded = " ".join(entry.body_folded_tokens)
                if not any(c.isdigit() for c in body_folded) and len(body_folded) >= 5:
                    if body_folded != full_folded:
                        body_grams = extract_trigrams(body_folded)
                        tf_body = TargetForm(
                            form_id=form_id,
                            entry=entry,
                            form_type="body",
                            folded_text=body_folded,
                            length=len(body_folded),
                            trigrams=body_grams,
                        )
                        forms.append(tf_body)
                        form_id += 1
                        length_buckets.setdefault(tf_body.length, []).append(tf_body)
                        if 5 <= tf_body.length <= 7:
                            short_name_forms.append(tf_body)
                        for g in set(body_grams):
                            postings.setdefault(g, []).append(tf_body)

        self._forms: tuple[TargetForm, ...] = tuple(forms)
        self._postings: dict[str, tuple[TargetForm, ...]] = {
            k: tuple(v) for k, v in postings.items()
        }
        self._length_buckets: dict[int, tuple[TargetForm, ...]] = {
            k: tuple(v) for k, v in length_buckets.items()
        }
        self._short_name_forms: tuple[TargetForm, ...] = tuple(short_name_forms)
        self.max_length = max(length_buckets, default=0)
        self._gram_frequency: dict[str, int] = {
            g: len(p) for g, p in self._postings.items()
        }

    @property
    def gram_frequency(self) -> dict[str, int]:
        return self._gram_frequency

    @property
    def postings(self) -> dict[str, tuple[TargetForm, ...]]:
        return self._postings

    @property
    def short_name_forms(self) -> tuple[TargetForm, ...]:
        return self._short_name_forms

    @property
    def length_buckets(self) -> dict[int, tuple[TargetForm, ...]]:
        return self._length_buckets


def is_house_number_token(tok: Token) -> bool:
    """Check if token represents house number components (digits or slash numbers)."""
    return any(c.isdigit() for c in tok.text)


def extract_spans(
    address: str,
    tokens: list[Token],
    exact_candidates: list[Candidate],
    max_spans: int,
) -> tuple[list[CandidateSpan], bool, list[Candidate]]:
    """Extract bounded component windows and non-greedy extension spans.

    Returns:
        (spans, spans_exhausted, suspicious_exact_candidates)
    """
    spans = []
    seen = set()
    suspicious = []
    exhausted = False
    coverage = sorted((c.start, c.end) for c in exact_candidates)
    coverage_position, covered_end = 0, -1
    candidates_by_start = {}
    for candidate in exact_candidates:
        candidates_by_start.setdefault(candidate.start, []).append(candidate)

    def add_span(start, words, field, prefix, marker_word, *, joined=False, literal_alternatives=()):
        nonlocal exhausted
        end = words[-1].end
        folded = " ".join(word.folded for word in words)
        key = (start, end, folded, field)
        if key in seen:
            return
        if len(spans) >= max_spans:
            exhausted = True
            return
        seen.add(key)
        spans.append(CandidateSpan(start, end, words, folded, field is not None, field,
                                   prefix, marker_word, joined, literal_alternatives))

    # Partition tokens at separators and explicit field markers, including no-comma input.
    boundaries = [0]
    for index in range(1, len(tokens)):
        if COMPONENT_SEPARATOR.search(address, tokens[index - 1].end, tokens[index].start):
            boundaries.append(index)
    boundaries.append(len(tokens))
    candidate_starts = sorted(candidates_by_start)
    for group_start, group_end in zip(boundaries, boundaries[1:]):
        if exhausted:
            # Further recovery is already inconclusive. Only groups with an
            # exact candidate can still affect the suspicious-prefix policy.
            exact_pos = bisect_left(candidate_starts, tokens[group_start].start)
            if exact_pos == len(candidate_starts) or candidate_starts[exact_pos] >= tokens[group_end - 1].end:
                continue
        markers = {}
        for index in range(group_start + 1, group_end):
            previous_token = tokens[index - 1]
            field = MARKER_FIELD_BY_ENDING.get(previous_token.folded)
            if field is not None:
                marker = find_preceding_marker(tokens, index, field, address)
                if marker is not None:
                    marker_index = index - 1 if previous_token.start == marker[0] else index - 2
                    if marker_index >= group_start:
                        markers[marker_index] = (index, field)
        cuts = sorted({group_start, group_end, *markers})
        for left, right in zip(cuts, cuts[1:]):
            hint = markers.get(left)
            body = hint[0] if hint else left
            if hint is None:
                while body < right and (tokens[body].folded.replace("/", "").isdigit()
                        or (tokens[body].folded == "so" and body+1 < right
                            and tokens[body+1].folded.replace("/", "").isdigit())):
                    body += 1
                left = body
            if body >= right:
                continue
            start, end = tokens[left].start, tokens[right - 1].end
            while coverage_position < len(coverage) and coverage[coverage_position][0] <= start:
                covered_end = max(covered_end, coverage[coverage_position][1])
                coverage_position += 1
            if covered_end >= end:
                continue
            prefix = next((c for c in candidates_by_start.get(start, ()) if c.end < end
                           and (hint is None or c.field == hint[1])), None)
            if prefix is not None:
                suspicious.append(prefix)
            # Each joined marker offers an alternative body view within the
            # component. Literal spans and their ordinary recovery remain viable.
            alternatives = {}
            for pos in range(body, right):
                if tokens[pos].folded[:1] not in JOINED_MARKER_PATTERNS:
                    continue
                for marker in joined_markers(tokens, pos, address):
                    marker_pos = pos if marker.start == tokens[pos].start else pos - 1
                    if marker_pos >= body:
                        alternatives.setdefault(marker_pos, []).append((pos, marker))
            for offset in dict.fromkeys((left, body)):
                words = tuple(tokens[offset:right])
                split_forms = tuple(
                    " ".join((marker.body.folded, *(token.folded for token in tokens[pos + 1:right])))
                    for pos, marker in alternatives.get(offset, ())
                )
                add_span(start, words, hint[1] if hint else None, prefix,
                         tokens[left].folded if hint else None,
                         literal_alternatives=split_forms)
            joined_cuts = [*sorted(alternatives), right]
            for marker_pos, stop in zip(joined_cuts, joined_cuts[1:]):
                for pos, marker in alternatives[marker_pos]:
                    if pos >= stop:
                        continue
                    joined_end = tokens[stop - 1].end
                    matching = [c for c in candidates_by_start.get(marker.start, ())
                                if c.field == marker.field]
                    if any(c.end >= joined_end for c in matching):
                        continue
                    joined_prefix = next(iter(matching), None)
                    if joined_prefix is not None:
                        suspicious.append(joined_prefix)
                    words = (marker.body, *tokens[pos + 1:stop])
                    add_span(marker.start, words, marker.field, joined_prefix, marker.word, joined=True)
    spans.sort(key=lambda span: (span.is_extension_of is None, span.start, -(span.end-span.start), span.folded_text))
    return spans, exhausted, suspicious


def fuzzy_edit_limit(span_length: int, target_length: int, config: MatcherConfig) -> int:
    """Keep acceptance thresholds independent of the retrieval data structure."""
    return 1 if target_length < 9 or span_length < 9 else min(2, config.max_edits)


def verify_fuzzy_candidate(
    span: CandidateSpan, form: TargetForm, distance: int, remaining_checks: int,
) -> tuple[Candidate | None, int, bool]:
    """Return the candidate, additional distance checks, and exhaustion status."""
    if distance <= 0 or max(form.length, len(span.folded_text)) - distance < 4:
        return None, 0, False

    # A closer split belongs to the marker-aware path. Otherwise the literal
    # path could delete an incompatible marker and bypass its type constraint.
    checks = 0
    for alternative in span.literal_alternatives:
        if abs(len(alternative) - form.length) >= distance:
            continue
        if alternative == form.folded_text:
            return None, checks, False
        if checks >= remaining_checks:
            return None, checks, True
        checks += 1
        if thresholded_osa_distance(alternative, form.folded_text, distance - 1) < distance:
            return None, checks, False

    has_marker = span.has_marker or (form.form_type == "full" and form.entry.has_prefix)
    if form.form_type == "full":
        base_score = (70.0 if has_marker else 50.0) + (5.0 if form.entry.has_prefix else 0.0)
    else:
        base_score = 65.0 if has_marker else 50.0
    reasons = ("fuzzy_verified", f"dist_{distance}") + (("marker",) if has_marker else ())
    if span.is_extension_of is not None:
        reasons += ("extension_recovery",)
    candidate = Candidate(
        form.entry, span.start, span.end, "fuzzy", cost=distance,
        score=base_score - distance * 10.0, context_marker=has_marker,
        reasons=reasons, body_start=span.words[0].start if span.joined else None,
    )
    return candidate, checks, False


def recover_fuzzy_candidates(
    span_data: tuple[list[CandidateSpan], bool, list[Candidate]],
    config: MatcherConfig,
    approx_index: ApproximateIndex,
) -> tuple[
    list[Candidate],
    list[Candidate],
    dict[str, Any],
    set[str],
    list[Candidate],
]:
    """Execute bounded approximate recovery using trigrams and thresholded OSA.

    Returns:
        (retrieved_fuzzy, verified_fuzzy, budget_trace, exhausted_fields, suspicious_exact_to_abstain)
    """
    postings_count = 0
    verifications_count = 0
    max_cands_in_span = 0

    postings_exhausted = False
    verifications_exhausted = False
    candidates_per_span_exhausted = False
    exhausted_fields: set[str] = set()

    spans, spans_exhausted, suspicious_exact = span_data
    if spans_exhausted:
        # If spans budget is hit, mark all fields evaluated as potentially truncated
        exhausted_fields.update({"street", "ward", "province"})

    retrieved_fuzzy: list[Candidate] = []
    verified_fuzzy: list[Candidate] = []
    verify_cache: dict[tuple[str, str, int], int] = {}

    for span in spans:
        span_folded = span.folded_text
        span_len = len(span_folded)

        # Acceptance conservative rules:
        # Length >= 5 for fuzzy, no digits/numeric strings
        if (span_len < 5 or span_len > approx_index.max_length + config.max_edits
                or any(c.isdigit() for c in span_folded)):
            continue

        # Extract trigrams and sort rare-first
        span_grams = extract_trigrams(span_folded)
        unique_grams = sorted(
            (g for g in set(span_grams) if g in approx_index.gram_frequency),
            key=lambda g: (approx_index.gram_frequency[g], g),
        )[:3]  # Fixed rare-gram retrieval heuristic; recall is measured separately.

        form_gram_hits: dict[TargetForm, int] = {}

        # 1. Look up inverted postings
        for g in unique_grams:
            postings_list = approx_index.postings.get(g, ())
            for target_form in postings_list:
                if postings_count >= config.max_postings:
                    postings_exhausted = True
                    exhausted_fields.update((span.field_hint,) if span.field_hint else ("street", "ward", "province"))
                    break
                postings_count += 1
                # Filter by length bucket delta <= max_edits
                if (abs(target_form.length - span_len) <= config.max_edits
                        and (span.field_hint is None or target_form.entry.field == span.field_hint)
                        and (not span.joined or not target_form.entry.has_prefix or target_form.form_type == "body")
                        and accepts_marker(target_form.entry, span.marker_word)):
                    form_gram_hits[target_form] = form_gram_hits.get(target_form, 0) + 1
            if postings_exhausted:
                break

        # 2. Short-name bucket: direct compare bounded short names (length 5..7)
        if span_len <= 7 and not postings_exhausted:
            for tf in approx_index.short_name_forms:
                if postings_count >= config.max_postings:
                    postings_exhausted = True
                    exhausted_fields.update((span.field_hint,) if span.field_hint else ("street", "ward", "province"))
                    break
                postings_count += 1
                if (abs(tf.length - span_len) <= config.max_edits
                        and (span.field_hint is None or tf.entry.field == span.field_hint)
                        and (not span.joined or not tf.entry.has_prefix or tf.form_type == "body")
                        and accepts_marker(tf.entry, span.marker_word)):
                    form_gram_hits.setdefault(tf, 0)

        if not form_gram_hits:
            continue

        # 3. Shortlist candidates deterministically
        sorted_forms = sorted(
            form_gram_hits.keys(),
            key=lambda f: (
                -form_gram_hits[f],
                abs(f.length - span_len),
                f.entry.entry_id,
                f.form_type,
            ),
        )

        cand_count = len(sorted_forms)
        if cand_count > config.max_candidates_per_span:
            candidates_per_span_exhausted = True
            exhausted_fields.update(form.entry.field for form in sorted_forms)
            sorted_forms = sorted_forms[: config.max_candidates_per_span]

        retained_count = len(sorted_forms)
        if retained_count > max_cands_in_span:
            max_cands_in_span = retained_count

        # 4. Record retrieved candidates BEFORE distance rejection for retrieval recall
        span_retrieved: list[Candidate] = []
        for form in sorted_forms:
            cand = Candidate(
                entry=form.entry,
                start=span.start,
                end=span.end,
                path="fuzzy",
                cost=0,
                score=0.0,
                context_marker=span.has_marker,
                reasons=("retrieved_fuzzy",),
            )
            span_retrieved.append(cand)
            retrieved_fuzzy.append(cand)

        # 5. Verification with thresholded OSA
        for form in sorted_forms:
            if verifications_count >= config.max_verifications:
                verifications_exhausted = True
                exhausted_fields.update((span.field_hint,) if span.field_hint else ("street", "ward", "province"))
                break

            target_len = form.length
            # Acceptance rules: max1 edit for <9 letters, max2 for longer
            allowed_edits = fuzzy_edit_limit(span_len, target_len, config)

            cache_key = (span_folded, form.folded_text, allowed_edits)
            if cache_key in verify_cache:
                dist = verify_cache[cache_key]
            else:
                verifications_count += 1
                dist = thresholded_osa_distance(
                    span_folded, form.folded_text, limit=allowed_edits
                )
                verify_cache[cache_key] = dist

            # Acceptance checks
            if dist <= allowed_edits:
                candidate, checks, verifications_exhausted = verify_fuzzy_candidate(
                    span, form, dist, config.max_verifications - verifications_count,
                )
                verifications_count += checks
                if verifications_exhausted:
                    exhausted_fields.add(form.entry.field)
                    break
                if candidate is not None:
                    verified_fuzzy.append(candidate)

        if verifications_exhausted:
            break

    # Build budget trace
    any_exhausted = bool(
        spans_exhausted
        or postings_exhausted
        or verifications_exhausted
        or candidates_per_span_exhausted
    )

    exhausted_map = ExhaustedDict(
        {
            "fuzzy_spans": spans_exhausted,
            "postings": postings_exhausted,
            "verifications": verifications_exhausted,
            "candidates_per_span": candidates_per_span_exhausted,
        }
    )

    budget_trace: dict[str, Any] = {
        "counts": {
            "fuzzy_spans": len(spans),
            "postings": postings_count,
            "verifications": verifications_count,
            "candidates_per_span": max_cands_in_span,
        },
        "limits": {
            "fuzzy_spans": config.max_fuzzy_spans,
            "postings": config.max_postings,
            "verifications": config.max_verifications,
            "candidates_per_span": config.max_candidates_per_span,
        },
        "exhausted": any_exhausted,
        "exhaustion_flags": dict(exhausted_map),
    }

    # If search was truncated, determine suspicious exact prefixes to abstain
    suspicious_to_abstain: list[Candidate] = []
    if any_exhausted:
        # If budget was exhausted, any exact candidate with an unexplained tail
        # could not be resolved against longer competitors and must be abstained
        suspicious_to_abstain = [c for c in suspicious_exact if c.field in exhausted_fields]

    return (
        retrieved_fuzzy,
        verified_fuzzy,
        budget_trace,
        exhausted_fields,
        suspicious_to_abstain,
    )
