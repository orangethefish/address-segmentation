"""Joint candidate enumeration, combination scoring, top-K pruning, and ambiguity resolution.

# Selection and Ambiguity Model (Stage C)

This module implements joint candidate selection across fields (street, ward, province).
Unlike single-field independent matching, joint selection evaluates complete triples:

1. **Compatibility**:
   Combinations cannot reuse overlapping character spans across different fields.
   Candidates at distinct non-overlapping spans can both be selected even if they share
   canonical labels.
2. **Deterministic Top-K Pruning**:
   Each field retains at most K candidates (default 8). If pruning is required for a field,
   it is conservatively nulled BEFORE scoring and recorded in `pruned_fields` to prevent
   arbitrarily omitted candidates from creating false uniqueness or biasing other fields.
3. **Contained Name Supersession**:
   At one occurrence, complete exact/folded names supersede contained fragments,
   preserving explicit accents and independent markers. Verified recovery can
   extend a same-field prefix. Separate occurrences remain competitors.
4. **Ambiguity and Rejection Policy**:
   A field is emitted only if:
   - Exactly one canonical label appears across all combinations within `ambiguity_margin`
     of the global best score.
   - The null interpretation does NOT appear within `ambiguity_margin` of the global best score.
   - The winning candidate's score meets or exceeds `min_score` (default 10.0).
   - The field was not pruned.
   Deterministic ordering is strictly used for trace/display reproducibility, NEVER for tie resolution.
"""

from bisect import bisect_left
from typing import TYPE_CHECKING, Any

from address_segmentation.model import Candidate, MatcherConfig

if TYPE_CHECKING:
    pass

FIELDS: tuple[str, ...] = ("street", "ward", "province")


def supersede_contained_prefixes(candidates: list[Candidate]) -> list[Candidate]:
    """Prefer complete names over their contained fragments at one occurrence.

    Full exact/folded names explain unmarked fragments across fields; recovered
    names can extend a prefix within the same field. Separate occurrences and
    explicitly marked competing fields remain independent.
    """
    by_start: dict[int, list[Candidate]] = {}
    for candidate in candidates:
        by_start.setdefault(candidate.start, []).append(candidate)
    starts = sorted(by_start)
    removed = set()
    for longer in candidates:
        # Compare only candidates inside this occurrence, rather than scanning all
        # repeated names elsewhere in a long input.
        for pos in range(bisect_left(starts, longer.start), bisect_left(starts, longer.end)):
            for short in by_start[starts[pos]]:
                if (short is longer or short.end > longer.end
                        or (short.start == longer.start and short.end == longer.end)
                        or (short.field == longer.field and short.label == longer.label)):
                    continue
                if short.field != longer.field and short.context_marker:
                    continue
                if longer.path in ("exact", "folded") and "province_initials" not in longer.reasons:
                    # A fully observed name supersedes its contained name fragments,
                    # including suffixes. Preserve explicit accent evidence: an exact
                    # fragment must also occur literally in the longer canonical name.
                    attr = "body_nfc_tokens" if short.path == "exact" else "body_folded_tokens"
                    small, large = getattr(short.entry, attr), getattr(longer.entry, attr)
                    if (small and len(small) < len(large)
                            and any(large[i:i + len(small)] == small
                                    for i in range(len(large) - len(small) + 1))):
                        removed.add(id(short))
                elif (short.field == longer.field and short.start == longer.start
                      and longer.path in ("fuzzy", "compact", "telex", "vni")
                      and longer.cost <= 2 and longer.score >= 10.0):
                    # Verified recovery may extend an exact prefix at this occurrence.
                    for attr in ("nfc_tokens", "body_nfc_tokens", "folded_tokens", "body_folded_tokens"):
                        small, large = getattr(short.entry, attr), getattr(longer.entry, attr)
                        if small and len(small) < len(large) and large[:len(small)] == small:
                            removed.add(id(short))
                            break
    return [c for c in candidates if id(c) not in removed]


def spans_overlap(c1: Candidate, c2: Candidate) -> bool:
    """Return True if character spans of two candidates overlap."""
    return max(c1.start, c2.start) < min(c1.end, c2.end)


def select_candidates(
    verified_candidates: list[Candidate],
    config: MatcherConfig,
) -> tuple[dict[str, str | None], list[Candidate], dict[str, Any]]:
    """Jointly evaluate candidate combinations and return (result, retained_candidates, selection_trace).

    - result: Fresh dictionary with exact keys 'street', 'ward', 'province'.
    - retained_candidates: List of post-pruning Candidate objects evaluated in joint selection.
    - selection_trace: JSON-serializable dictionary with best_score, ambiguous_fields,
      pruned_fields, and rejection_reasons.
    """
    # Step 1: Supersede contained name fragments at the same occurrence
    post_supersede = supersede_contained_prefixes([c for c in verified_candidates if c.score >= config.min_score])

    # Step 2: Top-K Pruning per field
    by_field: dict[str, list[Candidate]] = {f: [] for f in FIELDS}
    for c in post_supersede:
        by_field[c.field].append(c)

    pruned_fields: list[str] = []
    retained_by_field: dict[str, list[Candidate]] = {}

    for f in FIELDS:
        cands_f = by_field[f]
        # Sort deterministically
        sorted_f = sorted(
            cands_f,
            key=lambda c: (-c.score, c.cost, c.start, c.end, c.entry.entry_id),
        )
        if len(sorted_f) > config.top_k:
            # Conservative pruning: null field before scoring to avoid retained
            # arbitrary candidates forcing decisions in other fields.
            pruned_fields.append(f)
            retained_by_field[f] = []
        else:
            retained_by_field[f] = sorted_f

    # Step 3: Enumerate compatible triples bounded by (K+1)^3
    choices: dict[str, list[Candidate | None]] = {
        f: list(retained_by_field[f]) + [None] for f in FIELDS
    }

    best_score: float = 0.0
    label_max_score: dict[str, dict[str | None, float]] = {f: {} for f in FIELDS}

    for cs in choices["street"]:
        for cw in choices["ward"]:
            if cs is not None and cw is not None and spans_overlap(cs, cw):
                continue
            for cp in choices["province"]:
                if cp is not None:
                    if cs is not None and spans_overlap(cs, cp):
                        continue
                    if cw is not None and spans_overlap(cw, cp):
                        continue

                # Compatible combination
                combo_score = (
                    (cs.score if cs else 0.0)
                    + (cw.score if cw else 0.0)
                    + (cp.score if cp else 0.0)
                )

                if combo_score > best_score:
                    best_score = combo_score

                # Aggregate max combo score achieved by each field label
                for f, cand in (("street", cs), ("ward", cw), ("province", cp)):
                    lbl = cand.label if cand is not None else None
                    prev_max = label_max_score[f].get(lbl, -1.0)
                    if combo_score > prev_max:
                        label_max_score[f][lbl] = combo_score

    return resolve_selection(retained_by_field, label_max_score, best_score, config, pruned_fields)


def resolve_selection(retained_by_field, label_max_score, best_score, config, pruned_fields):
    """Apply the same label/null margins to either joint-selection algorithm."""
    threshold = best_score - config.ambiguity_margin
    ambiguous_fields: list[str] = []
    rejection_reasons: dict[str, str] = {}
    result: dict[str, str | None] = {"street": None, "ward": None, "province": None}

    for f in FIELDS:
        if f in pruned_fields:
            result[f] = None
            rejection_reasons[f] = "pruned"
            continue

        if not retained_by_field[f]:
            result[f] = None
            rejection_reasons[f] = "no_candidates"
            continue

        # Collect all labels achieving score within margin of best
        labels_in_margin = [
            lbl
            for lbl, max_s in label_max_score[f].items()
            if max_s >= threshold - 1e-9
        ]
        non_null_labels = [lbl for lbl in labels_in_margin if lbl is not None]
        has_null = None in labels_in_margin

        # Field emitted only if exactly one canonical label and no null in margin
        if not has_null and len(non_null_labels) == 1:
            winning_label = non_null_labels[0]
            # Verify candidate score meets min_score
            best_cand_score = max(
                (c.score for c in retained_by_field[f] if c.label == winning_label),
                default=0.0,
            )
            if best_cand_score >= config.min_score:
                result[f] = winning_label
            else:
                result[f] = None
                rejection_reasons[f] = "below_min_score"
        else:
            result[f] = None
            ambiguous_fields.append(f)
            rejection_reasons[f] = "ambiguous"

    selection_trace: dict[str, Any] = {
        "best_score": round(float(best_score), 4),
        "ambiguous_fields": sorted(ambiguous_fields),
        "pruned_fields": sorted(pruned_fields),
        "rejection_reasons": rejection_reasons,
    }

    retained_candidates = [candidate for field in FIELDS for candidate in retained_by_field[field]]
    return result, retained_candidates, selection_trace


def select_candidates_dp(
    verified_candidates: list[Candidate], config: MatcherConfig,
) -> tuple[dict[str, str | None], list[Candidate], dict[str, Any]]:
    """Select non-overlapping intervals with eight field-mask states per position.

    Forward/backward maximum scores give each label's best complete explanation,
    including the null alternative. No traceback tie-break or top-K truncation
    is needed. Sorting/compressing endpoints costs O(C log C); DP costs O(8*C).
    """
    candidates = supersede_contained_prefixes(
        [candidate for candidate in verified_candidates if candidate.score >= config.min_score])
    by_field = {field: [] for field in FIELDS}
    for candidate in candidates:
        by_field[candidate.field].append(candidate)
    for field in FIELDS:
        by_field[field].sort(key=lambda c: (-c.score, c.cost, c.start, c.end, c.entry.entry_id))
    bits = {field: 1 << index for index, field in enumerate(FIELDS)}
    # Zero-length candidates cannot overlap any interval under spans_overlap.
    # Place their DP edges at the final position, where increasing masks keep
    # those edges acyclic. Normal retrieved source spans are always nonempty.
    last = max((candidate.end for candidate in candidates), default=0)
    spans = [(last, last) if c.start == c.end else c.span for c in candidates]
    positions = sorted({0, *(offset for span in spans for offset in span)})
    offsets = {position: index for index, position in enumerate(positions)}
    edges = [[] for _ in positions]
    for candidate, (start, end) in zip(candidates, spans):
        edges[offsets[start]].append((offsets[end], bits[candidate.field], candidate))
    negative_infinity = float("-inf")
    forward = [[negative_infinity] * 8 for _ in positions]
    forward[0][0] = 0.0
    for position, outgoing in enumerate(edges):
        for mask in range(8):
            score = forward[position][mask]
            if score == negative_infinity:
                continue
            if position + 1 < len(positions):
                forward[position + 1][mask] = max(forward[position + 1][mask], score)
            for end, bit, candidate in outgoing:
                if not mask & bit:
                    forward[end][mask | bit] = max(forward[end][mask | bit], score + candidate.score)
    backward = [[0.0] * 8 for _ in positions]
    for position in range(len(positions) - 1, -1, -1):
        for mask in range(7, -1, -1):
            score = backward[position + 1][mask] if position + 1 < len(positions) else 0.0
            for end, bit, candidate in edges[position]:
                if not mask & bit:
                    score = max(score, candidate.score + backward[end][mask | bit])
            backward[position][mask] = score
    best_score = max(forward[-1])
    label_max_score = {
        field: {None: max(score for mask, score in enumerate(forward[-1]) if not mask & bits[field])}
        for field in FIELDS
    }
    for position, outgoing in enumerate(edges):
        for end, bit, candidate in outgoing:
            score = max(forward[position][mask] + candidate.score + backward[end][mask | bit]
                        for mask in range(8) if not mask & bit)
            labels = label_max_score[candidate.field]
            labels[candidate.label] = max(labels.get(candidate.label, negative_infinity), score)
    return resolve_selection(by_field, label_max_score, best_score, config, [])
