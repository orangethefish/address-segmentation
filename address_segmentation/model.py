"""Domain models, candidate representations, and configuration for address segmentation.

# Scoring and Evidence Model (Stage C)

Stage C replaces single-field heuristics with joint candidate selection.
Candidates represent evidence found in the input address string for a dictionary entry.

## Scoring Features

Candidates receive an additive evidence score composed of:
1. **Base Retrieval Score**:
   - Exact NFC casefolded token sequence match: 100.0
   - Accent-folded token sequence match: 50.0
   Exact accented matches strictly outrank accent-folded matches at the same evidence.
2. **Context and Field Marker Bonus**:
   - Explicit preceding administrative marker (e.g., 'Đường', 'Phường', 'Thành phố'): +20.0
   Markers strengthen matching and disambiguate shared spans across fields.
3. **Prefix-Body Completeness**:
   - When a canonical dictionary label contains an administrative prefix (e.g., 'Phường Bến Thành'),
     matching the entire canonical form or matching the body with an explicit marker yields full evidence.
4. **Absolute Evidence Threshold**:
   - Candidates must achieve at least `min_score` (default 10.0) to be eligible for acceptance.

## Joint Selection and Ambiguity

Candidates are jointly evaluated across fields (street, ward, province) as triples:
- Non-overlapping spans: combinations cannot reuse overlapping source spans across different fields.
- Deduplication: multiple occurrences of the same label in the input text do not create ambiguity.
- Competing labels: if distinct labels in the same field achieve best scores within `ambiguity_margin`
  (default 0.0), that field is considered ambiguous and evaluates to `None`.
- Contained prefix supersession: a longer complete exact name at the same occurrence supersedes a
  strictly contained same-field prefix, without globally biasing toward longer names elsewhere.
"""

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from address_segmentation.catalog import CatalogEntry


@dataclass(frozen=True, slots=True)
class MatcherConfig:
    """Configuration parameters for address matching and candidate selection.

    Attributes:
        top_k: Maximum candidate pool size per field for joint selection (default 8).
        ambiguity_margin: Score delta within which alternative labels trigger abstention (default 0.0).
        min_score: Minimum evidence score required for candidate acceptance (default 10.0).
        enable_fuzzy: Flag for future approximate matching stages (default False).
        enable_compact: Flag for future compact whitespace matching stages (default False).
        enable_typing: Flag for future typing recovery stages (default False).
    """

    top_k: int = 8
    ambiguity_margin: float = 0.0
    min_score: float = 10.0
    enable_fuzzy: bool = False
    enable_compact: bool = False
    enable_typing: bool = False

    def __post_init__(self) -> None:
        # Validate top_k
        if isinstance(self.top_k, bool) or not isinstance(self.top_k, int):
            raise TypeError(f"top_k must be an int, got {type(self.top_k).__name__}")
        if self.top_k <= 0:
            raise ValueError(f"top_k must be a positive integer, got {self.top_k}")

        # Validate ambiguity_margin
        if isinstance(self.ambiguity_margin, bool) or not isinstance(
            self.ambiguity_margin, (int, float)
        ):
            raise TypeError(
                f"ambiguity_margin must be a float or int, got {type(self.ambiguity_margin).__name__}"
            )
        if not math.isfinite(self.ambiguity_margin):
            raise ValueError(f"ambiguity_margin must be finite, got {self.ambiguity_margin}")
        if self.ambiguity_margin < 0.0:
            raise ValueError(
                f"ambiguity_margin must be non-negative, got {self.ambiguity_margin}"
            )

        # Validate min_score
        if isinstance(self.min_score, bool) or not isinstance(self.min_score, (int, float)):
            raise TypeError(f"min_score must be a float or int, got {type(self.min_score).__name__}")
        if not math.isfinite(self.min_score):
            raise ValueError(f"min_score must be finite, got {self.min_score}")

        # Validate feature flags
        for flag_name in ("enable_fuzzy", "enable_compact", "enable_typing"):
            flag_val = getattr(self, flag_name)
            if not isinstance(flag_val, bool):
                raise TypeError(f"{flag_name} must be a bool, got {type(flag_val).__name__}")


@dataclass(frozen=True, slots=True)
class Candidate:
    """An evidence candidate representing a dictionary entry matched against a source span.

    Attributes:
        entry: Associated immutable CatalogEntry.
        start: Start character offset in the original raw address string.
        end: End character offset (half-open) in the original raw address string.
        path: Retrieval match path ('exact' or 'folded').
        cost: Verification edit distance or penalty cost (default 0).
        score: Additive evidence score computed from match quality and context.
        context_marker: Whether an explicit preceding field marker supported this match.
        reasons: Diagnostic tags and features explaining candidate scoring.
    """

    entry: "CatalogEntry"
    start: int
    end: int
    path: str
    cost: int = 0
    score: float = 0.0
    context_marker: bool = False
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.path not in ("exact", "folded"):
            raise ValueError(f"Invalid candidate path: {self.path!r}")
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"Invalid candidate character span: [{self.start}, {self.end}]")

    @property
    def span(self) -> tuple[int, int]:
        """Return the character offset tuple (start, end)."""
        return (self.start, self.end)

    @property
    def field(self) -> str:
        """Return entry field name ('street', 'ward', 'province')."""
        return self.entry.field

    @property
    def label(self) -> str:
        """Return canonical label text."""
        return self.entry.canonical_label

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable dictionary representation for trace diagnostics."""
        return {
            "field": self.field,
            "label": self.label,
            "span": [self.start, self.end],
            "path": self.path,
            "cost": self.cost,
            "score": round(float(self.score), 4),
        }
