"""Datasets and schemas for address segmentation evaluation and benchmarking.

Enforces strict JSONL schemas, dictionary membership validation, canonical
SHA-256 calculation, and platform metadata inspection.
"""

from collections.abc import Sequence, Set
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

REQUIRED_CASE_KEYS: frozenset[str] = frozenset(
    {"id", "address", "expected", "tags", "group_id", "expectation_kind"}
)
REQUIRED_EXPECTED_KEYS: frozenset[str] = frozenset({"street", "ward", "province"})
VALID_EXPECTATION_KINDS: frozenset[str] = frozenset(
    {"recoverable", "unsupported", "ambiguous"}
)


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """An immutable evaluation case record adhering strictly to the JSONL schema."""

    id: str
    address: str
    expected: dict[str, str | None]
    tags: tuple[str, ...]
    group_id: str
    expectation_kind: str

    def to_dict(self) -> dict[str, Any]:
        """Convert case to a plain dictionary representation."""
        return {
            "id": self.id,
            "address": self.address,
            "expected": dict(self.expected),
            "tags": list(self.tags),
            "group_id": self.group_id,
            "expectation_kind": self.expectation_kind,
        }

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def sha256_bytes(data: bytes) -> str:
    """Compute canonical lowercase hexadecimal SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Compute canonical lowercase hexadecimal SHA-256 digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Compute canonical lowercase hexadecimal SHA-256 digest of file contents."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    if not p.is_file():
        raise ValueError(f"Path is not a regular file: {p}")

    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def canonical_json_hash(obj: Any) -> str:
    """Compute canonical SHA-256 digest of object serialized with deterministic JSON settings."""
    canonical_bytes = json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256_bytes(canonical_bytes)


def _resolve_repo_root(start_dir: str | Path | None = None) -> Path:
    """Locate the repository root directory containing address_segmentation package."""
    current = Path(start_dir).resolve() if start_dir else Path(__file__).resolve().parent.parent
    if (current / "address_segmentation").is_dir():
        return current
    for parent in current.parents:
        if (parent / "address_segmentation").is_dir():
            return parent
    return current


def get_source_hash(repo_root: str | Path | None = None) -> str:
    """Compute deterministic SHA-256 digest across all package source files."""
    root = _resolve_repo_root(repo_root)
    pkg_dir = root / "address_segmentation"
    if not pkg_dir.is_dir():
        return hashlib.sha256(b"").hexdigest()

    py_files = sorted(pkg_dir.rglob("*.py"))
    hasher = hashlib.sha256()
    for py_file in py_files:
        rel_path = py_file.relative_to(root).as_posix()
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(py_file.read_bytes())
    return hasher.hexdigest()


def get_platform_metadata() -> dict[str, Any]:
    """Collect system and Python runtime platform metadata."""
    return {
        "python_version": sys.version,
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }


def _normalize_dictionaries(dictionaries: Any) -> dict[str, set[str]]:
    """Normalize dictionary inputs into field-keyed sets for fast membership checks."""
    if not isinstance(dictionaries, dict):
        raise TypeError(
            f"dictionaries must be a dict object, got {type(dictionaries).__name__}"
        )

    dict_lookup: dict[str, set[str]] = {
        "street": set(),
        "ward": set(),
        "province": set(),
    }

    # Support plural and singular keys
    for singular, plural in (
        ("street", "streets"),
        ("ward", "wards"),
        ("province", "provinces"),
    ):
        raw_items = dictionaries.get(plural, dictionaries.get(singular, ()))
        if isinstance(raw_items, (str, bytes)) or not isinstance(
            raw_items, (Sequence, Set)
        ):
            raise TypeError(
                f"Dictionary entries for {plural!r} must be a sequence or set, got {type(raw_items).__name__}"
            )
        dict_lookup[singular] = set(raw_items)

    return dict_lookup


def load_cases(path: str | Path, dictionaries: Any) -> list[EvaluationCase]:
    """Load and strictly validate evaluation cases from a JSONL file.

    Requires:
    - Unique nonblank IDs across the entire file
    - Nonblank group_ids
    - Valid schema keys exactly matching REQUIRED_CASE_KEYS
    - 'expected' object with exactly 'street', 'ward', 'province' keys
    - Expected non-null labels must exist in the supplied dictionary
    - 'tags' as a list of strings
    - 'expectation_kind' in ('recoverable', 'unsupported', 'ambiguous')
    - Rejects blank lines, whitespace-only lines, and malformed JSON records.
    """
    dict_lookup = _normalize_dictionaries(dictionaries)

    case_path = Path(path)
    if not case_path.is_absolute():
        case_path = Path.cwd() / case_path

    if not case_path.exists():
        raise FileNotFoundError(f"Cases file not found: {case_path}")
    if not case_path.is_file():
        raise ValueError(f"Cases path is not a file: {case_path}")

    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()

    with open(case_path, "r", encoding="utf-8") as f:
        for line_idx, line in enumerate(f, start=1):
            raw_line = line.rstrip("\r\n")

            # Blank or whitespace-only lines violate JSONL strict format
            if not raw_line.strip():
                raise ValueError(
                    f"Line {line_idx}: blank records are rejected"
                )

            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Line {line_idx}: malformed JSON: {exc}"
                ) from exc

            if not isinstance(record, dict):
                raise ValueError(
                    f"Line {line_idx}: case record must be a JSON object, got {type(record).__name__}"
                )

            # Strict schema validation: exactly required keys
            actual_keys = frozenset(record.keys())
            if actual_keys != REQUIRED_CASE_KEYS:
                raise ValueError(
                    f"Line {line_idx}: case keys must be exactly {sorted(REQUIRED_CASE_KEYS)}, got {sorted(actual_keys)}"
                )

            # ID validation: unique and nonblank
            cid = record["id"]
            if not isinstance(cid, str) or not cid.strip():
                raise ValueError(
                    f"Line {line_idx}: 'id' must be a nonblank string"
                )
            if cid in seen_ids:
                raise ValueError(
                    f"Line {line_idx}: duplicate case id '{cid}'"
                )
            seen_ids.add(cid)

            # Address validation: string
            address = record["address"]
            if not isinstance(address, str):
                raise TypeError(
                    f"Line {line_idx}: 'address' must be a string, got {type(address).__name__}"
                )

            # Group ID validation: nonblank string
            group_id = record["group_id"]
            if not isinstance(group_id, str) or not group_id.strip():
                raise ValueError(
                    f"Line {line_idx}: 'group_id' must be a nonblank string"
                )

            # Expectation kind validation: known enum
            kind = record["expectation_kind"]
            if kind not in VALID_EXPECTATION_KINDS:
                raise ValueError(
                    f"Line {line_idx}: 'expectation_kind' must be one of {sorted(VALID_EXPECTATION_KINDS)}, got {kind!r}"
                )

            # Tags validation: list of strings
            tags = record["tags"]
            if not isinstance(tags, list):
                raise TypeError(
                    f"Line {line_idx}: 'tags' must be a list of strings, got {type(tags).__name__}"
                )
            for tag_idx, tag in enumerate(tags):
                if not isinstance(tag, str):
                    raise TypeError(
                        f"Line {line_idx}: tag item at index {tag_idx} must be a string, got {type(tag).__name__}"
                    )

            # Expected fields validation
            expected = record["expected"]
            if not isinstance(expected, dict):
                raise TypeError(
                    f"Line {line_idx}: 'expected' must be an object, got {type(expected).__name__}"
                )
            actual_exp_keys = frozenset(expected.keys())
            if actual_exp_keys != REQUIRED_EXPECTED_KEYS:
                raise ValueError(
                    f"Line {line_idx}: 'expected' keys must be exactly {sorted(REQUIRED_EXPECTED_KEYS)}, got {sorted(actual_exp_keys)}"
                )

            # Validate each expected field value and dictionary membership
            validated_expected: dict[str, str | None] = {}
            for field in ("street", "ward", "province"):
                val = expected[field]
                if val is not None and not isinstance(val, str):
                    raise TypeError(
                        f"Line {line_idx}: expected field {field!r} must be str or null, got {type(val).__name__}"
                    )
                if val is not None:
                    allowed_labels = dict_lookup[field]
                    if val not in allowed_labels:
                        raise ValueError(
                            f"Line {line_idx}: expected {field} {val!r} does not exist in supplied dictionary"
                        )
                validated_expected[field] = val

            cases.append(
                EvaluationCase(
                    id=cid,
                    address=address,
                    expected=validated_expected,
                    tags=tuple(tags),
                    group_id=group_id,
                    expectation_kind=kind,
                )
            )

    return cases
