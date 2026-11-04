"""I/O adapters and development CLI for JSON dictionary loading and JSONL streaming."""

import argparse
import json
from pathlib import Path
import sys

from address_segmentation.matcher import AddressMatcher
from address_segmentation.model import MatcherConfig

EXPECTED_DICTIONARY_KEYS = frozenset({"streets", "wards", "provinces"})


def load_dictionary(path_str: str) -> dict[str, list[str]]:
    """Load and validate JSON dictionary object with exactly streets, wards, provinces keys.

    Explicit relative paths resolve against caller working directory.
    """
    dict_path = Path(path_str)
    if not dict_path.is_absolute():
        dict_path = Path.cwd() / dict_path

    if not dict_path.exists():
        raise FileNotFoundError(f"Dictionary file not found: {dict_path}")
    if not dict_path.is_file():
        raise ValueError(f"Dictionary path is not a file: {dict_path}")

    with open(dict_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Dictionary root must be a JSON object, got {type(data).__name__}")

    actual_keys = frozenset(data.keys())
    if actual_keys != EXPECTED_DICTIONARY_KEYS:
        raise ValueError(
            f"Dictionary object must contain exactly keys {sorted(EXPECTED_DICTIONARY_KEYS)}, got {sorted(actual_keys)}"
        )

    for key in ("streets", "wards", "provinces"):
        if not isinstance(data[key], list):
            raise TypeError(
                f"Dictionary key {key!r} must be a list, got {type(data[key]).__name__}"
            )

    return data


def load_config(path: str | None) -> MatcherConfig:
    if path is None:
        return MatcherConfig()
    with open(path, encoding="utf-8") as stream:
        values = json.load(stream)
    if not isinstance(values, dict):
        raise ValueError("Matcher configuration must be a JSON object")
    return MatcherConfig(**values)


def run_cli(argv: list[str] | None = None) -> int:
    """Run CLI streaming stdin JSON strings to stdout UTF-8 JSON objects.

    One JSON string per line. Blank lines are rejected as malformed JSON.
    JSON empty strings produce three nulls. Immediate EOF produces no output.
    """
    parser = argparse.ArgumentParser(
        description="Address segmentation CLI: stream JSON strings to segmented JSON objects."
    )
    parser.add_argument(
        "--dictionary",
        required=True,
        help="Path to JSON dictionary file containing streets, wards, provinces lists.",
    )
    parser.add_argument("--config", help="Optional MatcherConfig JSON file")
    args = parser.parse_args(argv)

    try:
        dict_data = load_dictionary(args.dictionary)
        matcher = AddressMatcher(
            streets=dict_data["streets"],
            wards=dict_data["wards"],
            provinces=dict_data["provinces"],
            config=load_config(args.config),
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError) as exc:
        sys.stderr.write(f"Dictionary error: {exc}\n")
        return 1

    for line_idx, line in enumerate(sys.stdin, start=1):
        clean_line = line.rstrip("\r\n")

        # Blank records violate the one-JSON-string-per-line protocol
        if not clean_line.strip():
            sys.stderr.write(
                f"Line {line_idx}: Malformed JSON input: blank line is not a valid JSON string\n"
            )
            return 1

        try:
            raw_val = json.loads(clean_line)
        except json.JSONDecodeError as exc:
            sys.stderr.write(f"Line {line_idx}: Malformed JSON input: {exc}\n")
            return 1

        if not isinstance(raw_val, str):
            sys.stderr.write(
                f"Line {line_idx}: Expected JSON string, got {type(raw_val).__name__}\n"
            )
            return 1

        result = matcher.identify(raw_val)
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    sys.exit(run_cli())
