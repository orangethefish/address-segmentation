"""Dictionary-constrained typing hypotheses; never rewrite the original address.

Decode only supported Telex/VNI shapes and terminal tones. Tone and shape evidence
must agree with the candidate; absent accents remain unspecified. Compact matching
removes whitespace only, never punctuation or numeric separators. These paths use
hash lookups and share the recovery span, postings, and verification budgets.
"""
from dataclasses import dataclass
import unicodedata
from typing import Any

from address_segmentation.catalog import Catalog

from address_segmentation.approximate import extract_spans
from address_segmentation.model import Candidate, MatcherConfig
from address_segmentation.normalize import Token, fold_accents
from address_segmentation.retrieve import accepts_marker

TONES = dict(zip('sfrxj', '\u0301\u0300\u0309\u0303\u0323'))
VNI_TONES = dict(zip('12345', '\u0301\u0300\u0309\u0303\u0323'))
SHAPES = {'telex': {'dd': 'đ', 'aa': 'â', 'aw': 'ă', 'ee': 'ê',
                    'oo': 'ô', 'ow': 'ơ', 'uw': 'ư'},
          'vni': {'d9': 'đ', 'a6': 'â', 'a8': 'ă', 'e6': 'ê',
                  'o6': 'ô', 'o7': 'ơ', 'u7': 'ư'}}


@dataclass(frozen=True)
class Hypothesis:
    text: str
    tone: str | None
    changed: bool


def decode_token(text: str, mode: str) -> Hypothesis:
    text = unicodedata.normalize('NFC', text).casefold()
    changed = False
    tone = None
    table = TONES if mode == 'telex' else VNI_TONES
    if len(text) > 2 and text[-1] in table and any(c in fold_accents(text[:-1]) for c in 'aeiouy'):
        tone = table[text[-1]]
        text = text[:-1]
        changed = True
    for raw, composed in SHAPES[mode].items():
        if raw in text:
            text = text.replace(raw, composed)
            changed = True
    return Hypothesis(text, tone, changed)


def compatible(hypothesis: Hypothesis, target: str) -> bool:
    observed = unicodedata.normalize('NFD', hypothesis.text)
    target_nfd = unicodedata.normalize('NFD', target)
    # Both an already composed tone and a residual tone must agree with the label.
    tones = set(TONES.values())
    required = {c for c in observed if c in tones}
    if hypothesis.tone:
        required.add(hypothesis.tone)
    if required and required != {c for c in target_nfd if c in tones}:
        return False
    # Explicit shape/stroke evidence must exist in the canonical syllable.
    for shape in ('\u0302', '\u0306', '\u031b', 'đ'):
        if observed.count(shape) > target_nfd.count(shape):
            return False
    return fold_accents(hypothesis.text) == fold_accents(target)


def preserves_compact_accents(observed: str, target: str) -> bool:
    """Keep explicit accents at their character positions when spaces change."""
    def clusters(text: str) -> list[str]:
        result = []
        for char in unicodedata.normalize('NFD', text):
            if unicodedata.combining(char) and result:
                result[-1] += char
            else:
                result.append(char)
        return result

    left, right = clusters(observed), clusters(target)
    return len(left) == len(right) and all(
        fold_accents(a) == fold_accents(b) and set(a[1:]) <= set(b[1:])
        and (a[0] != 'đ' or b[0] == 'đ')
        for a, b in zip(left, right)
    )


class TypingIndex:
    def __init__(self, catalog: Catalog) -> None:
        self.compact = {}
        self.words = {}
        self.max_length = 0
        for entry in catalog.entries:
            forms = {entry.nfc_tokens: entry.folded_tokens,
                     entry.body_nfc_tokens: entry.body_folded_tokens}
            for words, folded_words in forms.items():
                if not words:
                    continue
                key = ''.join(folded_words)
                if len(key) < 4 or not key.isalpha():
                    continue
                self.max_length = max(self.max_length, len(key))
                record = (entry, words)
                self.compact.setdefault(key, []).append(record)
                self.words.setdefault(folded_words, []).append(record)


def recover_typing(
    address: str, tokens: list[Token], exact: list[Candidate],
    index: TypingIndex, config: MatcherConfig,
) -> tuple[list[Candidate], list[Candidate], dict[str, Any], set[str], list[Candidate]]:
    """Return retrieved, verified, budgets, exhausted fields, suspicious prefixes."""
    spans, exhausted, suspicious = extract_spans(address, tokens, exact, config.max_fuzzy_spans)
    counts = dict(fuzzy_spans=len(spans), postings=0, verifications=0, candidates_per_span=0)
    limits = dict(fuzzy_spans=config.max_fuzzy_spans, postings=config.max_postings,
                  verifications=config.max_verifications, candidates_per_span=config.max_candidates_per_span)
    flags = {key: False for key in limits}
    flags['fuzzy_spans'] = exhausted
    fields = set(('street', 'ward', 'province')) if exhausted else set()
    retrieved, verified = [], []
    for span in spans:
        # extract_spans may supply a marker-stripped body as folded_text; preserve
        # original composed syllables by taking the corresponding trailing tokens.
        words = [t.nfc for t in tokens if span.start <= t.start and t.end <= span.end]
        size = len(span.folded_text.split())
        words = words[-size:]
        modes = []
        compact = ''.join(fold_accents(w) for w in words)
        if config.enable_compact and 5 <= len(compact) <= index.max_length:
            modes.append(('compact', index.compact.get(compact, ()), None))
        if config.enable_typing:
            for mode in ('telex', 'vni'):
                hypotheses = [decode_token(w, mode) for w in words]
                if any(h.changed for h in hypotheses):
                    key = tuple(fold_accents(h.text) for h in hypotheses)
                    modes.append((mode, index.words.get(key, ()), hypotheses))
        span_visited = 0
        for mode, records, hypotheses in modes:
            for entry, target in records:
                if counts['postings'] >= limits['postings'] or span_visited >= limits['candidates_per_span']:
                    flags['postings' if counts['postings'] >= limits['postings'] else 'candidates_per_span'] = True
                    fields.update((span.field_hint,) if span.field_hint else ('street', 'ward', 'province'))
                    break
                counts['postings'] += 1
                span_visited += 1
                counts['candidates_per_span'] = max(counts['candidates_per_span'], span_visited)
                if span.field_hint and entry.field != span.field_hint:
                    continue
                if not accepts_marker(entry, span.marker_word):
                    continue
                candidate = Candidate(entry, span.start, span.end, mode, score=0)
                retrieved.append(candidate)
                if counts['verifications'] >= limits['verifications']:
                    flags['verifications'] = True
                    fields.add(entry.field)
                    continue
                counts['verifications'] += 1
                if mode == 'compact':
                    # Must actually explain a changed word boundary, with no punctuation.
                    body_start = next(t.start for t in tokens if span.start <= t.start and t.nfc == words[0])
                    source = address[body_start:span.end]
                    if any(not (c.isalpha() or c.isspace() or unicodedata.category(c).startswith('M')) for c in source):
                        continue
                    if tuple(fold_accents(w) for w in words) == tuple(fold_accents(w) for w in target):
                        continue
                    if not preserves_compact_accents(''.join(words), ''.join(target)):
                        continue
                elif len(target) != len(hypotheses) or not all(compatible(h, w) for h, w in zip(hypotheses, target)):
                    continue
                score = 60.0 if span.has_marker else 45.0
                if mode == 'compact' and ''.join(words) == ''.join(target):
                    score = 95.0 if span.has_marker else 85.0
                verified.append(Candidate(entry, span.start, span.end, mode, cost=1,
                    score=score, context_marker=span.has_marker,
                    reasons=(mode + '_verified',) + (('extension_recovery',) if span.is_extension_of else ())))
    return retrieved, verified, {'counts': counts, 'limits': limits, 'exhausted': any(flags.values()),
                                'exhaustion_flags': flags}, fields, [c for c in suspicious if c.field in fields]


def contradicts_tone(candidate: Candidate, tokens: list[Token]) -> bool:
    """Prevent ordinary edit deletion from overriding an explicit residual tone."""
    words = [t.nfc for t in tokens if candidate.start <= t.start and t.end <= candidate.end]
    for target in (candidate.entry.nfc_tokens, candidate.entry.body_nfc_tokens):
        if len(target) > len(words):
            continue
        observed = words[-len(target):]
        for mode in ('telex', 'vni'):
            for word, canonical in zip(observed, target):
                h = decode_token(word, mode)
                if h.tone and fold_accents(h.text) == fold_accents(canonical):
                    tones = {c for c in unicodedata.normalize('NFD', canonical) if c in TONES.values()}
                    if tones != {h.tone}:
                        return True
    return False
