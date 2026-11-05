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
3. **Contained Prefix Supersession**:
   At the same occurrence, a longer complete exact candidate supersedes a strictly contained
   same-field prefix (e.g., 'An Phú Đông' supersedes 'An Phú'). Shorter names are not globally
   penalized.
4. **Ambiguity and Rejection Policy**:
   A field is emitted only if:
   - Exactly one canonical label appears across all combinations within `ambiguity_margin`
     of the global best score.
   - The null interpretation does NOT appear within `ambiguity_margin` of the global best score.
   - The winning candidate's score meets or exceeds `min_score` (default 10.0).
   - The field was not pruned.
   Deterministic ordering is strictly used for trace/display reproducibility, NEVER for tie resolution.
"""

from typing import TYPE_CHECKING, Any

from address_segmentation.model import Candidate, MatcherConfig

if TYPE_CHECKING:
    pass

FIELDS: tuple[str, ...] = ("street", "ward", "province")


def supersede_contained_prefixes(candidates: list[Candidate]) -> list[Candidate]:
    """Supersede strict contained same-field prefixes at the same occurrence.

    If candidate C1 is a strict prefix of longer complete exact or verified
    fuzzy candidate C2 at the same occurrence/context, C2 supersedes C1.
    """
    by_field: dict[tuple[str, int], list[Candidate]] = {}
    for candidate in candidates:
        by_field.setdefault((candidate.field, candidate.start), []).append(candidate)

    survived: list[Candidate] = []
    for field_name, group in by_field.items():
        for short in group:
            superseded = False
            for longer in group:
                if longer is short or longer.label == short.label:
                    continue

                # Must be at the same occurrence/context
                same_start = longer.start == short.start
                if not same_start or longer.end <= short.end:
                    continue

                # Strict prefix check on token sequences
                is_strict_prefix = False
                for attr in (
                    "nfc_tokens",
                    "body_nfc_tokens",
                    "folded_tokens",
                    "body_folded_tokens",
                ):
                    left = getattr(short.entry, attr)
                    right = getattr(longer.entry, attr)
                    if left and len(left) < len(right) and right[: len(left)] == left:
                        is_strict_prefix = True
                        break

                if not is_strict_prefix:
                    continue

                # Exact or folded longer candidate supersedes if score >= short.score
                if longer.path in ("exact", "folded") and longer.score >= short.score:
                    superseded = True
                    break

                # Complete-name fuzzy evidence can supersede a contained shorter exact match
                # only with strong shared prefix + verified extension, same occurrence/context
                if longer.path in ("fuzzy", "compact", "telex", "vni") and longer.cost <= 2 and longer.score >= 10.0:
                    superseded = True
                    break

            if not superseded:
                survived.append(short)

    return survived


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
    # Step 1: Supersede contained same-field prefixes at the same occurrence
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

    retained_candidates: list[Candidate] = []
    for f in FIELDS:
        retained_candidates.extend(retained_by_field[f])

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

    # Step 4: Evaluate per-field ambiguity and acceptance
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

    return result, retained_candidates, selection_trace
