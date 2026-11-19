"""Read-only, on-demand access to an offline-compiled dictionary.

The file contains an open-addressed table of key offsets and primitive records.
CRC32 selects a slot; complete key comparison resolves all hash collisions.
No Python object graph or dictionary-wide index is reconstructed at startup.
"""

from _json import encode_basestring, scanstring
import marshal
import mmap
import struct
import sys
import unicodedata
import zlib

from address_segmentation.catalog import CatalogEntry
from address_segmentation.matcher import AddressMatcher
from address_segmentation.model import MatcherConfig

INDEX_MAGIC = b"ASIDX002"
INDEX_SLOT = struct.Struct("<QII")
HIERARCHY_TIEBREAK_POLICY = "text-ambiguity-tiebreak"
# The bundle compiler replaces this value with its source identity.
RUNTIME_ID = None


def index_key(namespace, key):
    if isinstance(key, tuple):
        key = "\x1f".join(key)
    return (namespace + "\x00" + str(key)).encode("utf-8")


class MappedDictionary:
    def __init__(self, path):
        self._file = open(path, "rb")
        self._map = None
        try:
            self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            data = self._map
            if len(data) < 12 or data[:8] != INDEX_MAGIC:
                raise ValueError("Invalid prepared dictionary header")
            header_size = struct.unpack_from("<I", data, 8)[0]
            self._table_start = 12 + header_size
            if self._table_start > len(data):
                raise ValueError("Truncated prepared dictionary header")
            self.metadata = marshal.loads(data[12:self._table_start], allow_code=False)
            if self.metadata["python_minor"] != tuple(sys.version_info[:2]):
                raise ValueError("Prepared dictionary Python version mismatch; rebuild it")
            slots = self.metadata["slots"]
            if (not isinstance(slots, int) or slots < 2 or slots & (slots - 1)):
                raise ValueError("Invalid prepared dictionary table size")
            self._mask = slots - 1
            self._payload_start = self._table_start + slots * INDEX_SLOT.size
            if self._payload_start > len(data):
                raise ValueError("Truncated prepared dictionary table")
            if self.metadata["unicode_version"] != unicodedata.unidata_version:
                raise ValueError("Prepared dictionary Unicode version mismatch; rebuild it")
            if RUNTIME_ID is not None and self.metadata["runtime_id"] != RUNTIME_ID:
                raise ValueError("Prepared dictionary/runtime mismatch; rebuild the bundle")
            self._entries = {}
            self._forms = {}
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._map is not None:
            self._map.close()
            self._map = None
        self._file.close()

    def get(self, namespace, key, default=None):
        encoded = index_key(namespace, key)
        slot = zlib.crc32(encoded) & self._mask
        data = self._map
        for _ in range(self._mask + 1):
            offset, key_size, value_size = INDEX_SLOT.unpack_from(
                data, self._table_start + slot * INDEX_SLOT.size)
            if offset == 0:
                return default
            if (offset < self._payload_start or key_size == 0
                    or offset + key_size + value_size > len(data)):
                raise ValueError("Corrupt prepared dictionary record")
            if key_size == len(encoded) and data[offset:offset + key_size] == encoded:
                return marshal.loads(data[offset + key_size:offset + key_size + value_size], allow_code=False)
            slot = (slot + 1) & self._mask
        raise ValueError("Prepared dictionary table has no empty slot")

    def entry(self, entry_id):
        if entry_id not in self._entries:
            values = self.get("entry", entry_id)
            if values is None:
                raise ValueError("Missing prepared dictionary entry")
            self._entries[entry_id] = CatalogEntry(
                values[0], values[1], values[2], *(tuple(v) for v in values[3:8]), values[8])
        return self._entries[entry_id]

    def form(self, form_id):
        if form_id not in self._forms:
            values = self.get("form", form_id)
            if values is None:
                raise ValueError("Missing prepared dictionary target form")
            self._forms[form_id] = PreparedForm(self, values)
        return self._forms[form_id]


class PreparedForm:
    """Expose cheap form metadata before fetching the canonical entry.

    Each form ID is interned by its store, so identity hashing merges the same
    form across postings exactly once. Length-rejected forms never load entries.
    """

    __slots__ = ("_store", "_values")

    def __init__(self, store, values):
        self._store, self._values = store, values

    @property
    def form_id(self):
        return self._values[0]

    @property
    def entry(self):
        return self._store.entry(self._values[1])

    @property
    def form_type(self):
        return self._values[2]

    @property
    def folded_text(self):
        return self._values[3]

    @property
    def length(self):
        return self._values[4]

    @property
    def trigrams(self):
        return tuple(self._values[5])


class PreparedMap:
    def __init__(self, store, namespace, decode=None):
        self.store, self.namespace, self.decode = store, namespace, decode

    def get(self, key, default=None):
        value = self.store.get(self.namespace, key)
        if value is None:
            return default
        return self.decode(value) if self.decode else value

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __contains__(self, key):
        return self.get(key) is not None


class PreparedCatalog:
    def __init__(self, store):
        self.store = store
        self.max_tokens = store.metadata["max_tokens"]
        self.token_lengths = frozenset(store.metadata["token_lengths"])

    @property
    def entries(self):
        return tuple(self.store.entry(i) for i in range(self.store.metadata["entry_count"]))

    def _lookup(self, namespace, key):
        return tuple(self.store.entry(i) for i in self.store.get(namespace, key, ()))

    def lookup_exact(self, key):
        return self._lookup("exact", key)

    def lookup_folded(self, key):
        return self._lookup("folded", key)

    def lookup_body_exact(self, key):
        return self._lookup("body_exact", key)

    def lookup_body_folded(self, key):
        return self._lookup("body_folded", key)

    def lookup_province_initials(self, key):
        return self._lookup("initials", key)

    def window_lengths(self, key):
        return self.store.get("windows", key, ())


class PreparedApproximateIndex:
    def __init__(self, store):
        self.store = store
        self.gram_frequency = PreparedMap(store, "frequency")
        self.postings = PreparedMap(store, "postings", self._forms)
        self.length_buckets = PreparedMap(store, "lengths", self._forms)
        self.max_length = store.metadata.get("fuzzy_max_length", float("inf"))

    def _forms(self, ids):
        return (self.store.form(i) for i in ids)

    @property
    def short_name_forms(self):
        return self._forms(self.store.get("short", "", ()))


class PreparedTypingIndex:
    def __init__(self, store):
        self.store = store
        self.max_length = store.metadata["typing_max_length"]
        self.words = PreparedMap(store, "words", self._records)
        self.compact = PreparedMap(store, "compact", self._records)

    def _records(self, records):
        return ((self.store.entry(i), tuple(words)) for i, words in records)


class PreparedMatcher(AddressMatcher):
    """Same matcher pipeline over an immutable offline-prepared dictionary."""

    def __init__(self, path):
        self._store = MappedDictionary(path)
        try:
            self._config = MatcherConfig(**self._store.metadata["config"])
            self._catalog = PreparedCatalog(self._store)
            self._location_masks = (PreparedMap(self._store, "location")
                                    if self._store.metadata.get("hierarchy_policy") == HIERARCHY_TIEBREAK_POLICY
                                    else None)
            self._approx_index = PreparedApproximateIndex(self._store) if self._config.enable_fuzzy else None
            self._typing_index = (PreparedTypingIndex(self._store)
                                  if self._config.enable_typing or self._config.enable_compact else None)
        except BaseException:
            self.close()
            raise

    def close(self):
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def run_prepared_cli(argv=None, default_path=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--help"]:
        print("Usage: submission.py [--index PATH]  (JSON strings on stdin; JSON results on stdout)")
        return 0
    if not argv and default_path:
        path = default_path
    elif len(argv) == 2 and argv[0] == "--index":
        path = argv[1]
    else:
        sys.stderr.write("Usage: submission.py [--index PATH]\n")
        return 1
    try:
        with PreparedMatcher(path) as matcher:
            for line_idx, line in enumerate(sys.stdin, 1):
                if not line.strip():
                    raise ValueError(f"Line {line_idx}: blank line is not a valid JSON string")
                address = decode_address_line(line)
                print(encode_result(matcher.identify(address)), flush=True)
    except (OSError, ValueError, TypeError, KeyError, EOFError, struct.error) as exc:
        sys.stderr.write(f"Prepared dictionary/input error: {exc}\n")
        return 1
    return 0


def decode_address_line(line):
    """Use CPython's JSON string codec without importing the general decoder.

    The public protocol accepts only a string. Trailing documents and non-JSON
    whitespace are rejected. Invalid escapes/control characters are rejected by
    the same scanner used in json.loads; error paths may import json lazily.
    """
    text = line.strip(" \t\r\n")
    if not text.startswith('"'):
        raise TypeError("Expected JSON string")
    address, end = scanstring(text, 1, True)
    if end != len(text):
        raise ValueError("Extra data after JSON string")
    return address


def encode_result(result):
    return "{" + ", ".join(
        '"' + field + '": ' + ("null" if result[field] is None else encode_basestring(result[field]))
        for field in ("street", "ward", "province")) + "}"
