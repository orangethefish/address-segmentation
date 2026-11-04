"""Stage C address matcher coordinating retrieval, verification, and joint selection.

# Matcher Orchestration and Trace API (Stage C)

AddressMatcher coordinates end-to-end identification:
1. Validates and tokenizes input address strings, preserving original source character offsets.
2. Retrieves exact and folded candidates using immutable catalog indexes and prefix-body logic.
3. For exact-only Stage C, verification passes retrieved candidates directly to joint selection.
4. Selection evaluates compatible triples (no cross-field span overlaps) within work budgets.
5. Emits canonical dictionary labels for unambiguous fields and returns fresh result dictionaries.

## API Contracts

- `AddressMatcher(streets, wards, provinces, *, config=None)`
- `matcher.identify(address) -> dict[str, str | None]`
  Fast public path returning a fresh dictionary with keys 'street', 'ward', 'province'.
- `matcher.identify_with_trace(address) -> tuple[dict, dict]`
  Diagnostic path returning `(result, trace_dict)` for evaluation and error analysis.
"""

from collections.abc import Sequence, Set
from typing import Any

from address_segmentation.catalog import Catalog
from address_segmentation.model import MatcherConfig
from address_segmentation.normalize import tokenize
from address_segmentation.retrieve import retrieve_candidates
from address_segmentation.select import select_candidates


class AddressMatcher:
    """Synchronous, standard-library address matcher implementing Stage C joint selection."""

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

        self._catalog = Catalog(streets=streets, wards=wards, provinces=provinces)
        self._config = config if config is not None else MatcherConfig()

    @property
    def catalog(self) -> Catalog:
        """Return the immutable catalog instance."""
        return self._catalog

    @property
    def config(self) -> MatcherConfig:
        """Return the matcher configuration."""
        return self._config

    def identify(self, address: str) -> dict[str, str | None]:
        """Identify canonical street, ward, and province from free-form address string.

        Returns a fresh dictionary with exact keys 'street', 'ward', 'province'.
        Does not incur tracing overhead.
        """
        if not isinstance(address, str):
            raise TypeError(f"address must be a str, got {type(address).__name__}")

        # Empty, whitespace-only, and punctuation-only inputs yield three nulls
        tokens = tokenize(address)
        if not tokens or self._catalog.max_tokens == 0:
            return {"street": None, "ward": None, "province": None}

        retrieved = retrieve_candidates(tokens, self._catalog, self._config, address)
        result, _, _ = select_candidates(retrieved, self._config)
        return {
            "street": result["street"],
            "ward": result["ward"],
            "province": result["province"],
        }

    def identify_with_trace(
        self, address: str
    ) -> tuple[dict[str, str | None], dict[str, Any]]:
        """Identify canonical fields with detailed diagnostic trace for Stage B evaluator.

        Returns:
            (result_dict, trace_dict) where trace_dict is guaranteed JSON-serializable.
        """
        if not isinstance(address, str):
            raise TypeError(f"address must be a str, got {type(address).__name__}")

        tokens = tokenize(address)
        if not tokens or self._catalog.max_tokens == 0:
            empty_result: dict[str, str | None] = {
                "street": None,
                "ward": None,
                "province": None,
            }
            empty_trace: dict[str, Any] = {
                "retrieved": [],
                "verified": [],
                "retained": [],
                "selection": {
                    "best_score": 0.0,
                    "ambiguous_fields": [],
                    "pruned_fields": [],
                    "rejection_reasons": {
                        "street": "no_candidates",
                        "ward": "no_candidates",
                        "province": "no_candidates",
                    },
                },
            }
            return empty_result, empty_trace

        retrieved = retrieve_candidates(tokens, self._catalog, self._config, address)
        # In Stage C exact baseline, verified candidates mirror retrieved candidates
        verified = retrieved
        result, retained, selection_trace = select_candidates(verified, self._config)

        fresh_result = {
            "street": result["street"],
            "ward": result["ward"],
            "province": result["province"],
        }
        trace = {
            "retrieved": [c.to_dict() for c in retrieved],
            "verified": [c.to_dict() for c in verified],
            "retained": [c.to_dict() for c in retained],
            "selection": selection_trace,
        }
        return fresh_result, trace
