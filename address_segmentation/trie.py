"""Experimental trie lookups and bounded OSA traversal.

Canonical entries, span extraction, typing explanations and acceptance rules are
shared with the indexed backend. No trigram postings are built by this backend.
"""

from address_segmentation.approximate import (
    TargetForm, extract_spans, fuzzy_edit_limit, make_fuzzy_candidate,
)
from address_segmentation.catalog import Catalog, build_entries
from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.retrieve import accepts_marker

TRIE_FIELD_BITS = {"street": 1, "ward": 2, "province": 4}


class TrieMultiMap:
    """A trie whose terminals retain every value, including normalized collisions."""

    def __init__(self):
        self._children = [{}]
        self._values = [[]]
        self._frozen = False

    def add(self, key, value):
        if self._frozen:
            raise TypeError("Cannot add to a frozen trie")
        node = 0
        for part in key:
            child = self._children[node].get(part)
            if child is None:
                child = len(self._children)
                self._children[node][part] = child
                self._children.append({})
                self._values.append([])
            node = child
        self._values[node].append(value)

    def freeze(self, unique=False):
        self._children = tuple(dict(sorted(children.items())) for children in self._children)
        self._values = tuple(tuple(sorted(set(values))) if unique else tuple(values)
                             for values in self._values)
        self._frozen = True

    def get(self, key, default=None):
        node = 0
        for part in key:
            node = self._children[node].get(part)
            if node is None:
                return default
        return self._values[node] or default


class TrieCatalog(Catalog):
    """The ordinary catalog contract backed by token and character tries."""

    def __init__(self, streets=(), wards=(), provinces=()):
        self._entries = build_entries(streets, wards, provinces)
        self._exact_index = TrieMultiMap()
        self._folded_index = TrieMultiMap()
        self._body_exact_index = TrieMultiMap()
        self._body_folded_index = TrieMultiMap()
        self._province_initials = TrieMultiMap()
        self._window_lengths = TrieMultiMap()
        lengths = set()
        for entry in self._entries:
            for index, words in ((self._exact_index, entry.nfc_tokens),
                                 (self._folded_index, entry.folded_tokens),
                                 (self._body_exact_index, entry.body_nfc_tokens),
                                 (self._body_folded_index, entry.body_folded_tokens)):
                index.add(words, entry)
            for words in (entry.folded_tokens, entry.body_folded_tokens):
                lengths.add(len(words))
                self._window_lengths.add(words[0], len(words))
            if (entry.field == "province" and len(entry.body_folded_tokens) >= 2
                    and all(word.isalpha() for word in entry.body_folded_tokens)):
                initials = "".join(word[0] for word in entry.body_folded_tokens)
                self._province_initials.add(initials, entry)
                self._window_lengths.add(initials, 1)
                lengths.add(1)
        for index in (self._exact_index, self._folded_index, self._body_exact_index,
                      self._body_folded_index, self._province_initials):
            index.freeze()
        self._window_lengths.freeze(unique=True)
        self._token_lengths = frozenset(lengths)
        self._max_tokens = max(lengths, default=0)


class TrieTypingIndex:
    """Reuse typing verification while replacing its lookup maps with tries."""

    def __init__(self, catalog):
        self.compact = TrieMultiMap()
        self.words = TrieMultiMap()
        self.max_length = 0
        for entry in catalog.entries:
            forms = {entry.nfc_tokens: entry.folded_tokens,
                     entry.body_nfc_tokens: entry.body_folded_tokens}
            for words, folded in forms.items():
                key = "".join(folded)
                if len(key) < 4 or not key.isalpha():
                    continue
                self.max_length = max(self.max_length, len(key))
                self.compact.add(key, (entry, words))
                self.words.add(folded, (entry, words))
        self.compact.freeze()
        self.words.freeze()


class TrieApproximateIndex:
    """Full/body character trie with descendant length and field bounds."""

    def __init__(self, catalog):
        self._trie = TrieMultiMap()
        forms = []
        for entry in catalog.entries:
            variants = [("full", entry.folded_tokens)]
            if entry.has_prefix and entry.body_folded_tokens != entry.folded_tokens:
                variants.append(("body", entry.body_folded_tokens))
            for kind, words in variants:
                text = " ".join(words)
                if len(text) < 5 or any(char.isdigit() for char in text):
                    continue
                form = TargetForm(len(forms), entry, kind, text, len(text), ())
                forms.append(form)
                self._trie.add(text, form)
        self._trie.freeze()
        self._forms = tuple(forms)
        bounds = [(10**30, 0, 0)] * len(self._trie._children)
        for node in range(len(bounds) - 1, -1, -1):
            low, high, fields = 10**30, 0, 0
            for form in self._trie._values[node]:
                low, high = min(low, form.length), max(high, form.length)
                fields |= TRIE_FIELD_BITS[form.entry.field]
            for child in self._trie._children[node].values():
                child_low, child_high, child_fields = bounds[child]
                low, high = min(low, child_low), max(high, child_high)
                fields |= child_fields
            bounds[node] = (low, high, fields)
        self._bounds = tuple(bounds)

    def node(self, node_id):
        return (self._trie._children[node_id], self._trie._values[node_id],
                *self._bounds[node_id])


def _band_value(row, column, infinity):
    if row is None:
        return infinity
    offset, cells = row
    index = column - offset
    return cells[index] if 0 <= index < len(cells) else infinity


def search_trie(index, text: str, limit: int, max_nodes: int = 12000,
                max_matches: int | None = None, field_hint: str | None = None,
                marker_word: str | None = None, max_terminals: int | None = None):
    """Return all OSA neighbors within limits, with explicit truncation status.

    Each DP row stores at most 2*limit+1 cells. Two ancestor rows and the previous
    trie character preserve adjacent-transposition transitions across branches.
    max_nodes counts inspected edges, including length/field rejections.
    max_terminals bounds candidate checks, including rejected marker types.
    """
    if limit < 0:
        raise ValueError("limit must be non-negative")
    size, infinity = len(text), limit + 1
    field_mask = TRIE_FIELD_BITS[field_hint] if field_hint else 7
    _, _, low, high, fields = index.node(0)
    if low > size + limit or high < size - limit or not fields & field_mask:
        return [], 0, 0, None
    root_row = (0, list(range(min(size, limit) + 1)))
    stack = [(0, 0, root_row, None, None)]
    matches, visited, checked = [], 0, 0
    while stack:
        node, depth, previous, previous2, last_char = stack.pop()
        children = index.node(node)[0]
        for char, child in children.items():
            if visited >= max_nodes:
                return matches, visited, checked, "trie_nodes"
            visited += 1
            child_edges, terminal, low, high, fields = index.node(child)
            if low > size + limit or high < size - limit or not fields & field_mask:
                continue
            next_depth = depth + 1
            start = max(0, next_depth - limit)
            end = min(size, next_depth + limit)
            cells = []
            for column in range(start, end + 1):
                if column == 0:
                    value = next_depth
                else:
                    value = min(
                        _band_value(previous, column, infinity) + 1,
                        (cells[-1] if cells else infinity) + 1,
                        _band_value(previous, column - 1, infinity) + (char != text[column - 1]),
                    )
                    if (previous2 is not None and column > 1
                            and char == text[column - 2] and last_char == text[column - 1]):
                        value = min(value, _band_value(previous2, column - 2, infinity) + 1)
                cells.append(min(value, infinity))
            if not cells or min(cells) > limit:
                continue
            current = (start, cells)
            distance = _band_value(current, size, infinity)
            if distance <= limit:
                for form in terminal:
                    if max_terminals is not None and checked >= max_terminals:
                        return matches, visited, checked, "verifications"
                    checked += 1
                    if ((field_hint is not None and form.entry.field != field_hint)
                            or not accepts_marker(form.entry, marker_word)):
                        continue
                    if max_matches is not None and len(matches) >= max_matches:
                        return matches, visited, checked, "candidates_per_span"
                    matches.append((form, distance))
            if child_edges:
                stack.append((child, next_depth, current, previous, char))
    return matches, visited, checked, None


def recover_trie_candidates(address: str, tokens, catalog, config: MatcherConfig, index, exact_candidates):
    """Use shared spans and acceptance rules with deterministic trie work budgets."""
    spans, spans_exhausted, suspicious = extract_spans(
        address, tokens, exact_candidates, config.max_fuzzy_spans)
    limits = dict(fuzzy_spans=config.max_fuzzy_spans, postings=config.max_postings,
                  verifications=config.max_verifications,
                  candidates_per_span=config.max_candidates_per_span,
                  trie_nodes=config.max_trie_nodes)
    counts = dict(fuzzy_spans=len(spans), postings=0, verifications=0,
                  candidates_per_span=0, trie_nodes=0)
    flags = {key: False for key in limits}
    flags["fuzzy_spans"] = spans_exhausted
    fields = set(TRIE_FIELD_BITS) if spans_exhausted else set()
    retrieved, verified = [], []
    for span in spans:
        text = span.folded_text
        if len(text) < 5 or any(char.isdigit() for char in text):
            continue
        remaining = limits["verifications"] - counts["verifications"]
        limit = min(config.max_edits, 1 if len(text) < 9 else 2)
        matches, visited, checked, exhausted = search_trie(
            index, text, limit, limits["trie_nodes"] - counts["trie_nodes"],
            limits["candidates_per_span"], span.field_hint, span.marker_word, remaining)
        counts["trie_nodes"] += visited
        counts["verifications"] += checked
        counts["candidates_per_span"] = max(counts["candidates_per_span"], len(matches))
        for form, distance in sorted(matches, key=lambda pair: (pair[0].form_id, pair[1])):
            retrieved.append(Candidate(form.entry, span.start, span.end, "fuzzy",
                                       context_marker=span.has_marker,
                                       reasons=("retrieved_fuzzy", "trie_osa")))
            if distance <= fuzzy_edit_limit(len(text), form.length, config):
                candidate = make_fuzzy_candidate(span, form, distance)
                if candidate is not None:
                    verified.append(candidate)
        if exhausted:
            fields.update((span.field_hint,) if span.field_hint else TRIE_FIELD_BITS)
            flags[exhausted] = True
    trace = dict(counts=counts, limits=limits, exhausted=any(flags.values()),
                 exhaustion_flags=flags)
    return retrieved, verified, trace, fields, [c for c in suspicious if c.field in fields]
