"""Stages A–E address matcher coordinating retrieval, verification, and joint selection.

# Matcher Orchestration and Trace API (Stage D)

AddressMatcher coordinates end-to-end identification:
1. Validates and tokenizes input address strings, preserving original source character offsets.
2. Retrieves exact and folded candidates using immutable catalog indexes and prefix-body logic.
3. Fast path: full exact addresses with no suspicious tails bypass approximate search.
4. Approximate recovery: for unmatched spans and trailing unexplained name tokens, executes
   bounded trigram candidate retrieval and thresholded OSA verification.
5. Budget monitoring: strictly respects limits on spans, postings, verifications, and candidates.
   Exhausted fuzzy searches conservatively abstain affected fields while preserving independent
   exact fields.
6. Selection evaluates compatible triples (no cross-field span overlaps) within work budgets.
7. Emits canonical dictionary labels for unambiguous fields and returns fresh result dictionaries.

## API Contracts

- `AddressMatcher(streets, wards, provinces, *, config=None)`
- `matcher.identify(address) -> dict[str, str | None]`
  Fast public path returning a fresh dictionary with keys 'street', 'ward', 'province'.
- `matcher.identify_with_trace(address) -> tuple[dict, dict]`
  Diagnostic path returning `(result, trace_dict)` for evaluation and error analysis.
"""

from collections.abc import Sequence, Set
from dataclasses import replace
from typing import Any

from address_segmentation.approximate import (
    ApproximateIndex,
    recover_fuzzy_candidates,
    extract_spans,
)
from address_segmentation.catalog import Catalog
from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.normalize import Token, tokenize
from address_segmentation.retrieve import retrieve_candidates
from address_segmentation.select import select_candidates, select_candidates_dp
from address_segmentation.typing import TypingIndex, recover_typing, contradicts_tone
from address_segmentation.trie import (
    TrieCatalog, TrieApproximateIndex, TrieTypingIndex, recover_trie_candidates,
)


class AddressMatcher:
    """Synchronous, standard-library address matcher implementing general and typing recovery."""

    def __init__(
        self,
        streets: Sequence[str] | Set[str] = (),
        wards: Sequence[str] | Set[str] = (),
        provinces: Sequence[str] | Set[str] = (),
        *,
        config: MatcherConfig | None = None,
    ) -> None:
        if config is not None and not isinstance(config, MatcherConfig):
            raise TypeError(f"config must be a MatcherConfig, got {type(config).__name__}")

        self._config = config if config is not None else MatcherConfig()
        use_trie = self._config.backend == "trie_dp"
        catalog_type = TrieCatalog if use_trie else Catalog
        self._catalog = catalog_type(streets=streets, wards=wards, provinces=provinces)
        # Prepare per-entry full and body folded forms once per matcher
        approximate_type = TrieApproximateIndex if use_trie else ApproximateIndex
        typing_type = TrieTypingIndex if use_trie else TypingIndex
        self._approx_index = approximate_type(self._catalog) if self._config.enable_fuzzy else None
        self._typing_index = typing_type(self._catalog) if (self._config.enable_typing or self._config.enable_compact) else None

    @property
    def catalog(self) -> Catalog:
        """Return the immutable catalog instance."""
        return self._catalog

    @property
    def config(self) -> MatcherConfig:
        """Return the matcher configuration."""
        return self._config

    def _is_full_exact_fast_path(
        self,
        address: str,
        tokens: list[Token],
        exact_candidates: list[Candidate],
    ) -> bool:
        """Check if address is completely explained by exact matches with no suspicious tails."""
        if not exact_candidates:
            return False
        intervals = sorted((c.start, c.end) for c in exact_candidates)
        position, covered_end = 0, -1
        for token in tokens:
            while position < len(intervals) and intervals[position][0] <= token.start:
                covered_end = max(covered_end, intervals[position][1])
                position += 1
            if (covered_end < token.end and token.folded != "so"
                    and not token.folded.replace("/", "").isdigit()):
                return False
        return True

    def _execute_pipeline(
        self, address: str
    ) -> tuple[dict[str, str | None], list[Candidate], list[Candidate], list[Candidate], dict[str, Any], dict[str, Any]]:
        """Internal unified execution pipeline for identify and identify_with_trace."""
        selector = select_candidates_dp if self._config.backend == "trie_dp" else select_candidates
        tokens = tokenize(address)
        if not tokens or self._catalog.max_tokens == 0:
            empty_result: dict[str, str | None] = {
                "street": None,
                "ward": None,
                "province": None,
            }
            empty_selection: dict[str, Any] = {
                "best_score": 0.0,
                "ambiguous_fields": [],
                "pruned_fields": [],
                "rejection_reasons": {
                    "street": "no_candidates",
                    "ward": "no_candidates",
                    "province": "no_candidates",
                },
            }
            empty_budgets: dict[str, Any] = {
                "counts": {
                    "fuzzy_spans": 0,
                    "postings": 0,
                    "verifications": 0,
                    "candidates_per_span": 0,
                },
                "limits": {
                    "fuzzy_spans": self._config.max_fuzzy_spans,
                    "postings": self._config.max_postings,
                    "verifications": self._config.max_verifications,
                    "candidates_per_span": self._config.max_candidates_per_span,
                },
                "exhausted": False,
            }
            return empty_result, [], [], [], empty_selection, empty_budgets

        # 1. Exact and folded retrieval (Stage C)
        exact_retrieved = retrieve_candidates(
            tokens, self._catalog, self._config, address
        )

        # 2. Fast path for full exact address with no suspicious tail
        if not (self._config.enable_fuzzy or self._typing_index) or self._is_full_exact_fast_path(
            address, tokens, exact_retrieved
        ):
            all_retrieved = exact_retrieved
            all_verified = exact_retrieved
            budget_trace: dict[str, Any] = {
                "counts": {
                    "fuzzy_spans": 0,
                    "postings": 0,
                    "verifications": 0,
                    "candidates_per_span": 0,
                },
                "limits": {
                    "fuzzy_spans": self._config.max_fuzzy_spans,
                    "postings": self._config.max_postings,
                    "verifications": self._config.max_verifications,
                    "candidates_per_span": self._config.max_candidates_per_span,
                },
                "exhausted": False,
            }
            result, retained, selection_trace = selector(
                all_verified, self._config
            )
            return (
                result,
                all_retrieved,
                all_verified,
                retained,
                selection_trace,
                budget_trace,
            )

        # 3. Approximate fuzzy recovery (Stage D)
        span_data = extract_spans(address, tokens, exact_retrieved, self._config.max_fuzzy_spans)
        fuzzy_retrieved, fuzzy_verified = [], []
        exhausted_fields, suspicious_to_abstain = set(), []
        budget_trace = {
            "counts": {"fuzzy_spans": 0, "postings": 0, "verifications": 0, "candidates_per_span": 0},
            "limits": {"fuzzy_spans": self._config.max_fuzzy_spans, "postings": self._config.max_postings,
                       "verifications": self._config.max_verifications, "candidates_per_span": self._config.max_candidates_per_span},
            "exhausted": False, "exhaustion_flags": {},
        }
        if self._config.enable_fuzzy:
            fuzzy_config = self._config
            if self._typing_index is not None:
                fuzzy_config = replace(self._config, max_candidates_per_span=max(1, self._config.max_candidates_per_span // 2))
            recovery = recover_trie_candidates if self._config.backend == "trie_dp" else recover_fuzzy_candidates
            (fuzzy_retrieved, fuzzy_verified, budget_trace, exhausted_fields,
             suspicious_to_abstain) = recovery(span_data, fuzzy_config, self._approx_index)
            budget_trace["limits"]["candidates_per_span"] = self._config.max_candidates_per_span
        if self._typing_index is not None:
            remaining_postings = self._config.max_postings - budget_trace["counts"]["postings"]
            remaining_checks = self._config.max_verifications - budget_trace["counts"]["verifications"]
            remaining_candidates = self._config.max_candidates_per_span - budget_trace["counts"]["candidates_per_span"]
            if remaining_postings > 0 and remaining_checks > 0 and remaining_candidates > 0:
                typing_config = replace(self._config, max_postings=remaining_postings,
                                        max_verifications=remaining_checks, max_candidates_per_span=remaining_candidates)
                retrieved, verified, typing_budget, fields, suspicious = recover_typing(
                    address, span_data, self._typing_index, typing_config)
                fuzzy_retrieved.extend(retrieved)
                fuzzy_verified.extend(verified)
                exhausted_fields.update(fields)
                suspicious_to_abstain.extend(suspicious)
                for key, count in typing_budget["counts"].items():
                    if key in ("postings", "verifications", "candidates_per_span"):
                        budget_trace["counts"][key] += count
                    else:
                        budget_trace["counts"][key] = max(budget_trace["counts"][key], count)
                budget_trace["exhausted"] |= typing_budget["exhausted"]
                for key, value in typing_budget["exhaustion_flags"].items():
                    budget_trace["exhaustion_flags"][key] = budget_trace["exhaustion_flags"].get(key, False) or value

            else:
                # The remaining typing hypotheses cannot be checked. Do not let
                # a recovered label acquire false uniqueness at the budget boundary.
                _, _, suspicious = span_data
                suspicious_to_abstain.extend(suspicious)
                exhausted_fields.update(c.field for c in fuzzy_verified)
                exhausted_fields.update(c.field for c in suspicious)
                budget_trace["exhausted"] = True
                for name, remaining in (("postings", remaining_postings), ("verifications", remaining_checks),
                                        ("candidates_per_span", remaining_candidates)):
                    if remaining == 0:
                        budget_trace["exhaustion_flags"][name] = True
        if self._config.enable_typing:
            fuzzy_verified = [c for c in fuzzy_verified
                              if c.path != "fuzzy" or not contradicts_tone(c, tokens)]
        all_retrieved = exact_retrieved + fuzzy_retrieved

        # Filter verified candidates under budget exhaustion policy:
        # - Conservatively abstain affected fuzzy fields
        # - Abstain suspicious exact prefixes where longer competitors were not resolved
        # - Preserve independent exact fields
        abstain_exact_ids = {id(c) for c in suspicious_to_abstain}
        survived_exact = [
            c for c in exact_retrieved if id(c) not in abstain_exact_ids
        ]
        survived_fuzzy = [
            c for c in fuzzy_verified if c.field not in exhausted_fields
        ]
        deduped = {}
        for candidate in survived_exact + survived_fuzzy:
            key = (candidate.entry.entry_id, candidate.start, candidate.end)
            if key not in deduped or candidate.score > deduped[key].score:
                deduped[key] = candidate
        all_verified = list(deduped.values())

        # 4. Joint selection
        result, retained, selection_trace = selector(
            all_verified, self._config
        )

        # Record budget exhaustion in selection rejection reasons if field is null
        for f in exhausted_fields:
            if result[f] is None:
                selection_trace["rejection_reasons"][f] = "budget_exhausted"

        return (
            result,
            all_retrieved,
            all_verified,
            retained,
            selection_trace,
            budget_trace,
        )

    def identify(self, address: str) -> dict[str, str | None]:
        """Identify canonical street, ward, and province from free-form address string.

        Returns a fresh dictionary with exact keys 'street', 'ward', 'province'.
        Does not serialize detailed candidate traces. Parity with identify_with_trace.
        """
        if not isinstance(address, str):
            raise TypeError(f"address must be a str, got {type(address).__name__}")

        result, _, _, _, _, _ = self._execute_pipeline(address)
        return {
            "street": result["street"],
            "ward": result["ward"],
            "province": result["province"],
        }

    def identify_with_trace(
        self, address: str
    ) -> tuple[dict[str, str | None], dict[str, Any]]:
        """Identify canonical fields with detailed diagnostic trace for evaluation.

        Returns:
            (result_dict, trace_dict) where trace_dict is guaranteed JSON-serializable.
        """
        if not isinstance(address, str):
            raise TypeError(f"address must be a str, got {type(address).__name__}")

        (
            result,
            all_retrieved,
            all_verified,
            retained,
            selection_trace,
            budget_trace,
        ) = self._execute_pipeline(address)

        if self._config.backend == "trie_dp":
            budget_trace["counts"].setdefault("trie_nodes", 0)
            budget_trace["limits"]["trie_nodes"] = self._config.max_trie_nodes
            budget_trace.setdefault("exhaustion_flags", {}).setdefault("trie_nodes", False)

        fresh_result = {
            "street": result["street"],
            "ward": result["ward"],
            "province": result["province"],
        }
        trace = {
            "retrieved": [c.to_dict() for c in all_retrieved],
            "verified": [c.to_dict() for c in all_verified],
            "retained": [c.to_dict() for c in retained],
            "selection": selection_trace,
            "budgets": budget_trace,
        }
        return fresh_result, trace
