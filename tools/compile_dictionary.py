"""Build a standalone, dictionary-derived matcher bundle before submission.

No addresses or expected results are accepted by this compiler. Matching logic
is bundled from the maintained source; only model construction and annotations
are specialized to avoid runtime dataclasses/typing/build-tool imports.
"""

import argparse
import ast
from dataclasses import asdict
import hashlib
import json
import marshal
from pathlib import Path
import py_compile
import struct
import sys
import tempfile
import time
import unicodedata
import zlib

from address_segmentation import AddressMatcher
from address_segmentation.io import load_config, load_dictionary
from address_segmentation.prepared import INDEX_MAGIC, INDEX_SLOT, index_key
from tools.datasets import canonical_json_hash, get_source_hash, sha256_file

PACKAGE = Path(__file__).resolve().parent.parent / "address_segmentation"
MODULES = ("normalize", "model", "catalog", "retrieve", "verify", "select",
           "approximate", "typing", "matcher", "prepared")


class BundleSource(ast.NodeTransformer):
    """Specialize our closed set of runtime modules; never transform user code."""

    def visit_ImportFrom(self, node):
        if node.module.startswith(("address_segmentation", "typing", "dataclasses", "collections.abc")):
            return None
        return node

    def visit_If(self, node):
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            return None
        return self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is None:
            return None
        return ast.copy_location(ast.Assign(targets=[node.target], value=self.visit(node.value)), node)

    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_FunctionDef(self, node):
        node.returns = None
        node.body = without_docstring(node.body)
        return self.generic_visit(node)

    def visit_ClassDef(self, node):
        node.body = without_docstring(node.body)
        if not node.decorator_list:
            return self.generic_visit(node)
        # All decorated source models are ordinary frozen dataclasses with
        # positional/default fields. Fail closed if this source contract changes.
        if len(node.decorator_list) != 1:
            raise ValueError(f"Unsupported decorators on {node.name}")
        decorator = node.decorator_list[0]
        if (not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Name)
                or decorator.func.id != "dataclass"
                or any(k.arg not in ("frozen", "slots") for k in decorator.keywords)
                or not any(k.arg == "frozen" and ast.literal_eval(k.value) is True
                           for k in decorator.keywords)):
            raise ValueError(f"Unsupported model declaration: {node.name}")
        fields = [item for item in node.body if isinstance(item, ast.AnnAssign)]
        if any(not isinstance(f.target, ast.Name) for f in fields):
            raise ValueError(f"Unsupported model fields: {node.name}")
        names = [f.target.id for f in fields]
        args = [f.target.id + ("=" + ast.unparse(f.value) if f.value is not None else "")
                for f in fields]
        values = ", ".join("self." + name for name in names)
        other_values = ", ".join("other." + name for name in names)
        methods = [item for item in node.body if not isinstance(item, ast.AnnAssign)]
        initialization = "\n".join(
            f"    object.__setattr__(self, {name!r}, {name})" for name in names)
        if any(isinstance(item, ast.FunctionDef) and item.name == "__post_init__" for item in methods):
            initialization += "\n    self.__post_init__()"
        generated = ast.parse(f'''
__slots__ = {tuple(names)!r}
__fields__ = {tuple(names)!r}
def __init__(self, {", ".join(args)}):
{initialization}
def __setattr__(self, name, value):
    raise AttributeError("immutable model")
def __delattr__(self, name):
    raise AttributeError("immutable model")
def __hash__(self):
    return hash(({values},))
def __eq__(self, other):
    if type(self) is not type(other):
        return NotImplemented
    return ({values},) == ({other_values},)
''').body
        node.decorator_list = []
        node.body = generated + methods
        return self.generic_visit(node)


def without_docstring(body):
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        return body[1:]
    return body


def bundle_source():
    chunks = ['"""Generated from maintained matching source; rebuild with tools.compile_dictionary."""']
    for module in MODULES:
        tree = ast.parse((PACKAGE / (module + ".py")).read_text(encoding="utf-8"))
        tree.body = without_docstring(tree.body)
        if module == "catalog":
            tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CatalogEntry"]
        if module in ("approximate", "typing"):
            tree.body = [node for node in tree.body
                         if not (isinstance(node, ast.ClassDef) and node.name in ("ApproximateIndex", "TypingIndex"))]
        if module == "matcher":
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == "AddressMatcher":
                    node.body = [item for item in node.body
                                 if not (isinstance(item, ast.FunctionDef) and item.name == "__init__")]
        tree = BundleSource().visit(tree)
        ast.fix_missing_locations(tree)
        chunks.append(ast.unparse(tree))
    chunks.append('''
def replace(instance, **changes):
    values = {name: getattr(instance, name) for name in instance.__fields__}
    values.update(changes)
    return type(instance)(**values)
''')
    source = "\n\n".join(chunks) + "\n"
    runtime_id = hashlib.sha256(source.encode("utf-8")).hexdigest()
    source += f"\nRUNTIME_ID = {runtime_id!r}\n"
    compile(source, "matcher_runtime.py", "exec")
    return source, runtime_id


def dictionary_records(matcher):
    catalog = matcher.catalog
    for entry in catalog.entries:
        yield "entry", entry.entry_id, [entry.entry_id, entry.field, entry.canonical_label,
            entry.nfc_tokens, entry.folded_tokens, entry.prefix_tokens,
            entry.body_nfc_tokens, entry.body_folded_tokens, entry.has_prefix]
    for namespace, values in (("exact", catalog._exact_index), ("folded", catalog._folded_index),
                              ("body_exact", catalog._body_exact_index),
                              ("body_folded", catalog._body_folded_index),
                              ("initials", catalog._province_initials)):
        for key, entries in values.items():
            yield namespace, key, [entry.entry_id for entry in entries]
    for key, lengths in catalog._window_lengths.items():
        yield "windows", key, lengths
    approx = matcher._approx_index
    if approx is not None:
        for form in approx._forms:
            yield "form", form.form_id, [form.form_id, form.entry.entry_id, form.form_type,
                                         form.folded_text, form.length, form.trigrams]
        for gram, forms in approx.postings.items():
            yield "postings", gram, [form.form_id for form in forms]
            yield "frequency", gram, len(forms)
        for length, forms in approx.length_buckets.items():
            yield "lengths", length, [form.form_id for form in forms]
        yield "short", "", [form.form_id for form in approx.short_name_forms]
    typing = matcher._typing_index
    if typing is not None:
        for namespace, values in (("compact", typing.compact), ("words", typing.words)):
            for key, records in values.items():
                yield namespace, key, [(entry.entry_id, words) for entry, words in records]


def write_index(path, metadata, records):
    encoded = sorted((index_key(ns, key), marshal.dumps(value, 4, allow_code=False))
                     for ns, key, value in records)
    if len({key for key, _ in encoded}) != len(encoded):
        raise ValueError("Duplicate prepared dictionary keys")
    slots = 2
    while slots < len(encoded) * 2:
        slots *= 2
    metadata = dict(metadata, slots=slots, record_count=len(encoded))
    header = marshal.dumps(metadata, 4, allow_code=False)
    table = bytearray(slots * INDEX_SLOT.size)
    with open(path, "wb") as stream:
        stream.write(INDEX_MAGIC + struct.pack("<I", len(header)) + header)
        table_start = stream.tell()
        stream.write(table)
        for key, value in encoded:
            slot = zlib.crc32(key) & (slots - 1)
            while INDEX_SLOT.unpack_from(table, slot * INDEX_SLOT.size)[0]:
                slot = (slot + 1) & (slots - 1)
            INDEX_SLOT.pack_into(table, slot * INDEX_SLOT.size, stream.tell(), len(key), len(value))
            stream.write(key)
            stream.write(value)
        stream.seek(table_start)
        stream.write(table)
    return metadata


def compile_dictionary(dictionary_path, output_dir, config_path=None):
    start = time.perf_counter_ns()
    data = load_dictionary(str(dictionary_path))
    config = load_config(config_path)
    matcher = AddressMatcher(**data, config=config)
    source, runtime_id = bundle_source()
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    # Build all files in a temporary directory first. An interrupted compiler
    # cannot publish a partially written index file.
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=destination) as temporary:
        stage = Path(temporary)
        runtime = stage / "matcher_runtime.py"
        runtime.write_text(source, encoding="utf-8")
        runner = stage / "submission.py"
        runner.write_text('''"""Standalone address matcher; all dictionary preparation is offline."""
import sys
from matcher_runtime import run_prepared_cli

if __name__ == "__main__":
    directory = __file__.replace(chr(92), "/").rpartition("/")[0] or "."
    sys.exit(run_prepared_cli(default_path=directory + "/dictionary.idx"))
''', encoding="utf-8")
        metadata = write_index(stage / "dictionary.idx", {
            "runtime_id": runtime_id,
            "python_minor": tuple(sys.version_info[:2]),
            "dictionary_hash": canonical_json_hash(data),
            "unicode_version": unicodedata.unidata_version,
            "config": asdict(config),
            "max_tokens": matcher.catalog.max_tokens,
            "token_lengths": sorted(matcher.catalog.token_lengths),
            "entry_count": len(matcher.catalog.entries),
            "typing_max_length": matcher._typing_index.max_length if matcher._typing_index else 0,
        }, dictionary_records(matcher))
        for filename in ("matcher_runtime.py", "submission.py", "dictionary.idx"):
            (stage / filename).replace(destination / filename)
    bytecode_path = Path(py_compile.compile(
        str(destination / "matcher_runtime.py"), dfile="matcher_runtime.py", doraise=True,
        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH))
    manifest = {
        "schema_version": 1,
        "metadata": metadata,
        "source_hash": get_source_hash(),
        "dictionary_file_hash": sha256_file(dictionary_path),
        "dictionary_entries": {key: len(values) for key, values in data.items()},
        "build_python": sys.version,
        "build_elapsed_ns": time.perf_counter_ns() - start,
        "files": {name: {"bytes": (destination / name).stat().st_size,
                         "sha256": sha256_file(destination / name)}
                  for name in ("matcher_runtime.py", "submission.py", "dictionary.idx",
                               bytecode_path.relative_to(destination).as_posix())},
        "commands": {"default": ["python3.14", "submission.py"],
                     "without_site": ["python3.14", "-S", "submission.py"]},
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dictionary", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--config")
    args = parser.parse_args(argv)
    try:
        manifest = compile_dictionary(args.dictionary, args.output_dir, args.config)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f"Compilation error: {exc}\n")
    print(f"Compiled {manifest['metadata']['entry_count']} labels into {args.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
