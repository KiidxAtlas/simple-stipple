"""Static correctness checks across the whole src/ tree.

These catch a class of bug that import/boundary tests structurally cannot see:
semantic anti-patterns *inside* function bodies. They exist because a manual
code read (prompted by a user spotting suspicious line numbers) found three
real issues that no existing test noticed:

  1. An entity's list-index captured once and stored in instance state
     (e.g. `self._bezier_handle_drag = (entity_index, ...)`), then reused
     across later event callbacks after the entity list may have changed —
     the exact "index that outlives its list position" failure LP-1 fixed
     for Document.selection, just relocated to view/tool transient state.
     (This specific instance in tools.py's bezier-handle-drag code has since
     been fixed independently — it now stores and re-resolves by ID. The
     test stays, as a guard against the same shape of bug recurring.)
  2. A variable named like an index (`entity_index`) actually holding a
     string ID returned by a `-> str | None` accessor — invites exactly the
     bug in (1), and misleads anyone reading or extending the code. (Still
     live at tools.py's dimension-circle-click handler as of this writing.)
  3. Dead stores: a variable assigned, then reassigned before ever being
     read, discarding the first computation silently. (Same location as #2
     — a carefully matched circle lookup was computed, then immediately
     discarded and replaced with a naive fallback on the next line.)

A fourth check (Check 5, added later) exists because a *fourth* real bug
slipped past all of the above, plus every other test in this repo, plus
`test_imports.py`'s "every module imports cleanly" check: `CanvasView`'s
`_selected_indices()`/`_mutable_selected_indices()` were deleted (superseded
by ID-based `_selected_ids()`/`_mutable_selected_ids()`), but ~40 call sites
across 7 service/interaction files still called the old names on `self._host`.
Nothing caught it — Python doesn't check attribute existence until a call
actually executes — until a user ran the app and hit
`AttributeError: 'DxfCanvas' object has no attribute '_selected_indices'`
the moment the properties panel tried to read the current selection.
Check 5 resolves the `self._host = host` delegation graph and verifies every
`self._host.<name>` call actually exists on the class that instantiates the
service, using real `dir()` (correct for inherited PySide6/Qt methods)
unioned with an AST scan of the *entire* ancestor chain's `self.<x> = ...`
assignments and class-body declarations (dir() alone misses both dynamic
instance attributes and bare `x: T` type-only declarations with no value).

Because these scan actual ASTs across all of src/, not a hardcoded list of
files, they keep catching this bug class after future refactors — a new file
introducing the same anti-pattern is covered automatically. Run this file's
own suite after making a change to see current line numbers and status;
don't trust the line numbers in these docstrings to stay current — the
codebase this was written against was under active, concurrent edits.

These are heuristics, not a full type checker. Where a heuristic could
plausibly misfire on legitimate code, prefer narrowing the pattern over
suppressing it with an allowlist — an allowlist just hides the next real
instance from view. Check 3 (persisted list-derived indices) is the one
exception: it's scoped to collections that are actually mutated outside
__init__ anywhere in src/, because an index into a write-once-at-construction
list cannot go stale — flagging it would be structurally guaranteed noise,
not a suppressed real finding.
"""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"


def _iter_py_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return None


SRC_FILES = [p for p in _iter_py_files(SRC) if _parse(p) is not None]


# =============================================================================
# Shared AST helpers
# =============================================================================


def _iter_functions(tree: ast.Module):
    """Every function/method def in the module, including nested ones."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _comprehension_bound_names(node: ast.expr) -> set[str]:
    """Names bound by a comprehension/generator-expression's own generators
    (its `for <target> in ...` clauses) — these are the comprehension's own
    scope in Python 3, distinct from any same-named variable outside it."""
    bound: set[str] = set()
    for gen in node.generators:
        for n in ast.walk(gen.target):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                bound.add(n.id)
    return bound


def _names_loaded(node: ast.AST) -> set[str]:
    """Every name read (Load context) anywhere within node, excluding names
    that are actually bound by an enclosing comprehension's own generators.

    Comprehensions have their own scope in Python 3 — `[x for x in y]`
    binds a fresh `x`, unrelated to any outer `x`. Without this exclusion,
    `x = 1; vals = [x for x in range(3)]; x = 2` reads as if the first `x`
    were used (the comprehension's `Name(id='x', ctx=Load)` for its `elt`),
    hiding a genuine dead store of the outer `x`. Simplification: treats a
    comprehension's first generator's `iter` as already inside the
    comprehension's scope, when Python actually evaluates it in the
    enclosing scope — only wrong for the vanishingly rare case of an
    iterable expression that itself references a name matching the
    comprehension's own loop variable, which doesn't occur in this codebase.
    """
    loaded: set[str] = set()

    def walk(n: ast.AST, shadowed: frozenset[str]) -> None:
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            inner_shadowed = shadowed | _comprehension_bound_names(n)
            for child in ast.iter_child_nodes(n):
                walk(child, inner_shadowed)
            return
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id not in shadowed:
                loaded.add(n.id)
        for child in ast.iter_child_nodes(n):
            walk(child, shadowed)

    walk(node, frozenset())
    return loaded


def _nested_blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    """Child statement lists of a compound statement, for block-scoped scans.

    Nested function/class defs are deliberately excluded — ast.walk() in
    _iter_functions already visits them as independent scopes, so descending
    into them here would double-count findings.
    """
    blocks: list[list[ast.stmt]] = []
    if isinstance(stmt, (ast.If, ast.For, ast.AsyncFor, ast.While)):
        blocks.append(stmt.body)
        if stmt.orelse:
            blocks.append(stmt.orelse)
    elif isinstance(stmt, ast.Try):
        blocks.append(stmt.body)
        for handler in stmt.handlers:
            blocks.append(handler.body)
        if stmt.orelse:
            blocks.append(stmt.orelse)
        if stmt.finalbody:
            blocks.append(stmt.finalbody)
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        blocks.append(stmt.body)
    return blocks


def _call_name(call: ast.expr) -> str | None:
    """The trailing attribute/name of a call target: `v._find_poly_at(...)` -> '_find_poly_at'."""
    if not isinstance(call, ast.Call):
        return None
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


# =============================================================================
# CHECK 1: Dead stores — a variable assigned, then reassigned unread
# =============================================================================
#
# Scoped conservatively to avoid false positives: only flags two assignments
# to the same simple Name target *as direct statements in the same block*
# (not across if/else branches — those are legitimately independent), with
# zero read of that name anywhere in between, including on the second
# assignment's own right-hand side (so `x = x + 1` is correctly not flagged).


@dataclass
class DeadStore:
    file: Path
    first_lineno: int
    second_lineno: int
    name: str


def _find_dead_stores(tree: ast.Module) -> list[DeadStore]:
    findings: list[DeadStore] = []

    def scan_block(stmts: list[ast.stmt]) -> None:
        pending: dict[str, int] = {}
        for stmt in stmts:
            for child_block in _nested_blocks(stmt):
                scan_block(child_block)

            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                target = stmt.targets[0].id
                for name in _names_loaded(stmt.value):
                    pending.pop(name, None)
                if target != "_" and target in pending:
                    findings.append(DeadStore(Path(""), pending[target], stmt.lineno, target))
                pending[target] = stmt.lineno
            else:
                for name in _names_loaded(stmt):
                    pending.pop(name, None)
                for n in ast.walk(stmt):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                        if n is not getattr(stmt, "_skip_self", None):
                            pending.pop(n.id, None)

    for func in _iter_functions(tree):
        scan_block(func.body)
    return findings


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_dead_stores(path: Path):
    """A variable must not be assigned twice with no read in between.

    This means the first computed value was thrown away — either dead code
    left over from a refactor, or a bug where the intended use of the first
    value was accidentally dropped. Found live at tools.py:756-775 before
    this test existed: an `entity_index` computed via a 15-line generator
    expression, immediately overwritten by a plain function call, with the
    first value never read.
    """
    tree = _parse(path)
    findings = _find_dead_stores(tree)
    assert not findings, (
        f"{path.relative_to(ROOT)}: variable(s) assigned then reassigned "
        f"without being read in between (dead store):\n"
        + "\n".join(
            f"  - '{f.name}' assigned at line {f.first_lineno}, "
            f"overwritten unread at line {f.second_lineno}"
            for f in findings
        )
    )


# =============================================================================
# CHECK 2: Entity index/ID naming must match actual provenance
# =============================================================================
#
# Builds a registry of which function/method *names* return a string ID vs
# an int index, by reading return annotations directly from source (works
# even under `from __future__ import annotations`, where runtime
# introspection sees stringified annotations — reading the AST directly
# sidesteps that entirely). Matched by simple name only (not fully resolved
# receiver types), which is a deliberate scope tradeoff: the codebase does
# not reuse names like `_find_poly_at` or `entity_for_id` for unrelated
# purposes, so name-based matching is reliable in practice here without a
# full type checker.

# Generic across any identity noun the codebase uses — entity_id, segment_index,
# group_id, shape_id, vertex_index, anchor_index, dimension_index, and any
# future one — not hardcoded to "entity". A variable named `foo_index` should
# hold an int; `foo_id` should hold a string ID, regardless of what `foo` is.
_INDEX_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*_(?:index|idx)$")
_ID_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*_id$")


def _annotation_text(node: ast.expr | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _build_return_type_registry() -> tuple[set[str], set[str]]:
    """Function names whose return annotation is str-ish vs int-ish."""
    id_returning: set[str] = set()
    index_returning: set[str] = set()

    for path in SRC_FILES:
        tree = _parse(path)
        for func in _iter_functions(tree):
            ann = _annotation_text(func.returns)
            if not ann:
                continue
            # Normalize e.g. "str | None", "Optional[str]", "'EntityId | None'"
            normalized = ann.replace("'", "").replace('"', "")
            is_str_ish = bool(re.search(r"\b(str|EntityId)\b", normalized))
            is_int_ish = bool(re.search(r"\bint\b", normalized))
            if is_str_ish and not is_int_ish:
                id_returning.add(func.name)
            elif is_int_ish and not is_str_ish:
                index_returning.add(func.name)

    return id_returning, index_returning


_ID_RETURNING, _INDEX_RETURNING = _build_return_type_registry()


@dataclass
class NamingMismatch:
    lineno: int
    var_name: str
    call_name: str
    detail: str


def _find_index_id_naming_mismatches(tree: ast.Module) -> list[NamingMismatch]:
    findings: list[NamingMismatch] = []

    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            continue

        var_name = node.targets[0].id
        call_name = _call_name(node.value)
        if call_name is None:
            continue

        if _INDEX_NAME.match(var_name) and call_name in _ID_RETURNING:
            findings.append(NamingMismatch(
                node.lineno, var_name, call_name,
                f"'{var_name}' is named like an index but '{call_name}()' returns a string ID",
            ))
        elif _ID_NAME.match(var_name) and call_name in _INDEX_RETURNING:
            findings.append(NamingMismatch(
                node.lineno, var_name, call_name,
                f"'{var_name}' is named like an ID but '{call_name}()' returns an int index",
            ))

        # `list.index(...)` builtin call always produces an int, regardless
        # of registry membership — catch this directly rather than relying
        # on `.index` being registered (it's a builtin method, not a def).
        if (
            isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "index"
            and _ID_NAME.match(var_name)
        ):
            findings.append(NamingMismatch(
                node.lineno, var_name, "index",
                f"'{var_name}' is named like an ID but '.index()' returns an int index",
            ))

    return findings


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_entity_index_id_naming_mismatch(path: Path):
    """A variable named `entity_index`/`entity_idx` must hold an int; a
    variable named `entity_id` must hold a string ID. Mixing these up is
    exactly how `Document.selection` ended up as `set[int]` before LP-1 —
    and the same confusion still existed in tools.py after LP-1 shipped,
    just in transient view state instead of the document.
    """
    tree = _parse(path)
    findings = _find_index_id_naming_mismatches(tree)
    assert not findings, (
        f"{path.relative_to(ROOT)}: entity index/ID naming mismatch:\n"
        + "\n".join(f"  - line {f.lineno}: {f.detail}" for f in findings)
    )


# =============================================================================
# CHECK 3: List-derived entity indices must not be persisted onto self/attrs
# =============================================================================
#
# `self.<attr> = value` (or `<param>.<attr> = value`, e.g. `v._x = ...` in a
# free function taking a view/model as its first argument) creates state
# that outlives the current call. If that state is — or contains — a value
# most recently derived from `list.index(...)` or `enumerate(...)`, it is
# exactly the LP-1 failure mode: an index captured once, replayed later
# against a list that may have since changed shape. The fix is always the
# same: persist the ID and re-resolve the index at the point of use, the way
# `_segment_from_ref` in tools.py already does correctly.


@dataclass
class PersistedIndex:
    lineno: int
    attr_path: str
    var_name: str
    origin_lineno: int
    origin_kind: str  # ".index()" or "enumerate()"
    collection_attr: str | None


_MUTATING_LIST_METHODS = {"append", "remove", "insert", "pop", "clear", "extend", "sort", "reverse"}


def _build_mutable_attr_registry() -> set[str]:
    """Attribute names (e.g. 'entities', '_states') that are ever reassigned
    or mutated in place *outside* __init__/__post_init__, anywhere in src/.

    Coarse by design: keyed on attribute name alone, not resolved to a
    specific owning class, because resolving `v: CanvasView` -> its class
    body across files (as tools.py/select.py do) needs real type resolution
    that plain AST doesn't give us. In this codebase attribute names like
    `_entities` are specific enough that name-based matching doesn't collide
    with unrelated classes — the cost of being coarse is a slightly wider
    "mutable" set, which only makes Check 3 more conservative (more likely
    to flag), never less.
    """
    mutable: set[str] = set()

    for path in SRC_FILES:
        tree = _parse(path)
        for func in _iter_functions(tree):
            if func.name in ("__init__", "__post_init__"):
                continue
            for node in ast.walk(func):
                # self.x = ... / self.x.append(...) etc.
                if (
                    isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Attribute) for t in node.targets)
                ):
                    for t in node.targets:
                        if isinstance(t, ast.Attribute):
                            mutable.add(t.attr)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _MUTATING_LIST_METHODS
                    and isinstance(node.func.value, ast.Attribute)
                ):
                    mutable.add(node.func.value.attr)

    return mutable


_MUTABLE_ATTRS = _build_mutable_attr_registry()


def _flatten_stmts(stmts: list[ast.stmt]) -> list[ast.stmt]:
    """Statements in roughly-execution order, descending into if/for/try/with
    bodies inline (but not into nested function/class defs — those are
    scanned separately as their own scope by the caller).

    Deliberately does NOT reset tracking state at block boundaries: a name
    assigned inside a `try`/`if` body is treated as still index-derived in
    the statements that follow the block. This over-approximates (a value
    assigned only on one branch is treated as if always assigned), which is
    the intentional side to be wrong on — the bezier-drag bug this check
    exists to catch is exactly a `try: entity_index = ...` followed by a
    sibling statement outside the try that reads it.
    """
    flat: list[ast.stmt] = []
    for stmt in stmts:
        flat.append(stmt)
        for child_block in _nested_blocks(stmt):
            flat.extend(_flatten_stmts(child_block))
    return flat


def _index_derived_from_expr(value: ast.expr) -> tuple[bool, str | None]:
    """Does this expression produce a list-position index? Covers both
    `coll.index(x)` and the `next((idx for idx, x in enumerate(coll) if
    ...), default)` shape — a pattern the codebase uses repeatedly (find-
    with-fallback over `enumerate()`), and which the earlier version of
    this check missed entirely because it only looked at `ast.For` loops,
    not generator expressions feeding `next()`.
    """
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "index"
    ):
        collection_attr = (
            value.func.value.attr if isinstance(value.func.value, ast.Attribute) else None
        )
        return True, collection_attr

    target = value
    if isinstance(value, ast.Call) and _call_name(value) == "next" and value.args:
        target = value.args[0]
    if isinstance(target, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        for gen in target.generators:
            if not (
                isinstance(gen.iter, ast.Call)
                and _call_name(gen.iter) == "enumerate"
                and isinstance(gen.target, ast.Tuple)
                and gen.target.elts
                and isinstance(gen.target.elts[0], ast.Name)
            ):
                continue
            index_name = gen.target.elts[0].id
            elt = getattr(target, "elt", None)
            if isinstance(elt, ast.Name) and elt.id == index_name:
                iter_arg = gen.iter.args[0] if gen.iter.args else None
                collection_attr = iter_arg.attr if isinstance(iter_arg, ast.Attribute) else None
                return True, collection_attr

    return False, None


def _find_persisted_list_indices(tree: ast.Module) -> list[PersistedIndex]:
    findings: list[PersistedIndex] = []

    def scan_function(body: list[ast.stmt]) -> None:
        # name -> (lineno, kind, collection_attr) where it was derived from
        # .index(...) or enumerate(...)
        index_derived: dict[str, tuple[int, str, str | None]] = {}

        for stmt in _flatten_stmts(body):
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
            ):
                target = stmt.targets[0].id
                value = stmt.value
                is_derived, collection_attr = _index_derived_from_expr(value)
                if is_derived:
                    kind = ".index()" if (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Attribute)
                        and value.func.attr == "index"
                    ) else "enumerate()"
                    index_derived[target] = (stmt.lineno, kind, collection_attr)
                else:
                    # Reassigned to something else: no longer index-derived.
                    index_derived.pop(target, None)
                continue

            if isinstance(stmt, ast.For) and isinstance(stmt.iter, ast.Call):
                if _call_name(stmt.iter) == "enumerate" and isinstance(stmt.target, ast.Tuple):
                    elts = stmt.target.elts
                    iterated = stmt.iter.args[0] if stmt.iter.args else None
                    collection_attr = (
                        iterated.attr if isinstance(iterated, ast.Attribute) else None
                    )
                    if elts and isinstance(elts[0], ast.Name):
                        index_derived[elts[0].id] = (stmt.lineno, "enumerate()", collection_attr)

            # Attribute assignment: self.x = ... / v.x = ... / view.x = ...
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Attribute)
            ):
                target_attr = stmt.targets[0]
                receiver = target_attr.value
                receiver_name = receiver.id if isinstance(receiver, ast.Name) else None
                if receiver_name not in ("self", "v", "view"):
                    continue
                attr_path = f"{receiver_name}.{target_attr.attr}"
                used_names = _names_loaded(stmt.value)
                for name in used_names:
                    if name in index_derived:
                        origin_lineno, origin_kind, collection_attr = index_derived[name]
                        # Only flag if the source collection is actually
                        # mutated somewhere outside __init__ — an index into
                        # a write-once-at-construction list can't go stale.
                        if collection_attr is not None and collection_attr not in _MUTABLE_ATTRS:
                            continue
                        findings.append(PersistedIndex(
                            stmt.lineno, attr_path, name, origin_lineno,
                            origin_kind, collection_attr,
                        ))

    for func in _iter_functions(tree):
        scan_function(func.body)
    return findings


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_persisted_list_derived_indices(path: Path):
    """An index derived from `.index()`/`enumerate()` over a *mutable*
    collection must not be written into `self.*`/`v.*`/`view.*` state that
    survives past the current call — that state can be read again after the
    collection has changed shape, at which point the index points at the
    wrong element (or is out of range).

    Scoped to collections that are actually mutated somewhere outside
    __init__ (append/remove/insert/pop/clear/extend/sort/reverse, or
    reassignment) — an index into a list that's only ever set once at
    construction can't go stale, so flagging those would just be noise.

    This exact pattern was live in tools.py's `start_bezier_handle_drag`:
    resolved an entity by ID, converted it to a list index via
    `.index(entity)`, and stored that index in `v._bezier_handle_drag` for
    later mouseMove callbacks to index `v._entities[entity_index]` directly.
    `v._entities` is reassigned throughout CanvasView's lifetime (load,
    paste, undo/redo, ...), so the index could point at the wrong entity by
    the time a later mouse event reads it. It has since been fixed to store
    the entity_id and re-resolve at each use — this test guards against the
    same shape of bug recurring, here or anywhere else in src/.
    """
    tree = _parse(path)
    findings = _find_persisted_list_indices(tree)
    assert not findings, (
        f"{path.relative_to(ROOT)}: list-derived index persisted onto "
        "instance state (index captured now, list may change shape before "
        "it's read later):\n"
        + "\n".join(
            f"  - line {f.lineno}: '{f.attr_path}' stores '{f.var_name}' "
            f"(from {f.origin_kind} over '{f.collection_attr}' at line {f.origin_lineno})"
            for f in findings
        )
    )


# =============================================================================
# CHECK 4: No structure encodes the same entity's identity two ways at once
# =============================================================================
#
# A dict literal with both an `*_id` key and an `*_index` key for what reads
# as the same entity (matching name prefix, e.g. "entity_id" + "entity_index")
# models identity twice. Whichever one goes stale first — usually the index,
# per Check 3 — silently diverges from the other with nothing to catch it.


@dataclass
class DualIdentity:
    lineno: int
    id_key: str
    index_key: str


def _entity_prefix(key: str) -> str | None:
    match = re.match(r"^(.*)_(?:id|index|idx)$", key)
    return match.group(1) if match else None


def _find_dual_identity_dicts(tree: ast.Module) -> list[DualIdentity]:
    findings: list[DualIdentity] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys_by_prefix: dict[str, dict[str, str]] = {}
        for key_node in node.keys:
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            key = key_node.value
            prefix = _entity_prefix(key)
            if prefix is None:
                continue
            kind = "id" if key.endswith("_id") else "index"
            keys_by_prefix.setdefault(prefix, {})[kind] = key
        for prefix, kinds in keys_by_prefix.items():
            if "id" in kinds and "index" in kinds:
                findings.append(DualIdentity(node.lineno, kinds["id"], kinds["index"]))
    return findings


@pytest.mark.parametrize("path", SRC_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_dual_identity_dict_structures(path: Path):
    """A dict must not carry both an `*_id` key and an `*_index` key for
    what reads as the same entity (e.g. `"entity_id"` and `"entity_index"`
    together). Two representations of the same identity in one structure
    means callers can read whichever is more convenient, including the one
    that goes stale — prefer carrying only the ID and resolving the index
    at the point of use, the way `_segment_from_ref` in tools.py does today
    (it returns only `entity_id`; an earlier version of that same dict also
    returned a plain `entity_index` field alongside an id embedded inside a
    separate `"key"` tuple — this check wouldn't have caught that exact
    shape, since the id wasn't under a top-level `*_id` key at the time; it
    only catches the direct case of both keys present at the top level).
    """
    tree = _parse(path)
    findings = _find_dual_identity_dicts(tree)
    assert not findings, (
        f"{path.relative_to(ROOT)}: dict encodes the same entity's identity "
        "as both an ID and an index:\n"
        + "\n".join(
            f"  - line {f.lineno}: keys '{f.id_key}' and '{f.index_key}'"
            for f in findings
        )
    )


# =============================================================================
# CHECK 5: Delegate/"host" method calls must exist on the actual host class
# =============================================================================
#
# A composition pattern used throughout src/ui/canvas/: `class FooService:
# def __init__(self, host) -> None: self._host = host`, instantiated as
# `self._foo = FooService(self)` from within an owner class (in practice,
# always CanvasView). FooService's methods then call `self._host.<name>(...)`
# extensively, trusting the owner class provides `<name>`.
#
# Renaming or deleting a method on the owner class produces no import-time
# or type-time error here — Python doesn't check attribute existence until
# the call actually executes. This is exactly how a real crash slipped past
# every other check in this suite and `test_imports.py`'s "every module
# imports cleanly" check: `CanvasView`'s `_selected_indices()` and
# `_mutable_selected_indices()` were removed (superseded by ID-based
# `_selected_ids()`/`_mutable_selected_ids()`), but ~40 call sites across 7
# files were never migrated. Nothing caught it until the app actually ran
# and a user hit that exact code path.
#
# This check resolves the delegation graph (who instantiates which service
# with `self` as the host, and under what attribute name the service stores
# it), builds a member registry per host class — real `dir()` on the
# imported class (correctly includes inherited PySide6/Qt methods, which a
# pure-AST registry would misflag as missing) unioned with an AST scan for
# `self.<name> = ...` assignments anywhere in the host class's own methods
# (dynamic instance attributes `dir()` on the bare class can't see, since
# they don't exist until `__init__` or some other method actually runs) —
# then walks every service method for `self.<host_attr>.<name>` reads/calls
# that appear in neither.


def _import_module_for(path: Path):
    rel = path.relative_to(ROOT).with_suffix("")
    return importlib.import_module(".".join(rel.parts))


def _self_assigned_attrs(cls_node: ast.ClassDef) -> set[str]:
    """Every `self.<name> = ...` / `self.<name>: T = ...` assignment
    anywhere within this class's own methods — the dynamic instance
    attributes `dir(cls)` cannot see on the bare, uninstantiated class."""
    attrs: set[str] = set()
    for node in ast.walk(cls_node):
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            attrs.add(target.attr)
    return attrs


def _class_body_level_names(cls_node: ast.ClassDef) -> set[str]:
    """Names declared directly in the class body: methods, properties, and
    both valued and bare-annotation class attributes. `x: int` with no
    value creates no real runtime attribute — dir() won't see it — but is
    a standard idiom for pre-declaring an instance attribute's type ahead
    of assigning it properly elsewhere (e.g. `_grid_action: QAction` on
    `App`, assigned later in a setup method); it still means "this class
    has this member" for our purposes.
    """
    names: set[str] = set()
    for node in cls_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def _find_class_def(class_name: str) -> tuple[Path, ast.ClassDef] | None:
    for path in SRC_FILES:
        tree = _parse(path)
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                return path, node
    return None


def _ancestor_declared_names(class_name: str, _seen: frozenset[str] = frozenset()) -> set[str]:
    """self.<x> = assignments and class-body declarations from this class
    AND every ancestor found in src/ — covers a member a *base* class sets
    in its own __init__ (or declares at class-body level) that a subclass
    instance still has. Without walking up, not just down to subclasses,
    a member set only by a base class's __init__ (e.g. CanvasView setting
    `self._cursor_wx` in its own __init__) looks "missing" when checked
    against a subclass (e.g. DxfCanvas) that never re-declares it itself —
    this was a real false positive caught during calibration.
    """
    if class_name in _seen:
        return set()
    found = _find_class_def(class_name)
    if found is None:
        return set()
    _, node = found
    names = _self_assigned_attrs(node) | _class_body_level_names(node)
    for base in node.bases:
        if isinstance(base, ast.Name):
            names |= _ancestor_declared_names(base.id, _seen | {class_name})
    return names


def _host_member_registry(class_name: str, _seen: frozenset[str] = frozenset()) -> set[str] | None:
    """Union of real dir() (correct for inheritance, including external
    libs like QWidget) and AST-scanned declared/dynamic names collected
    from this class and its full ancestor chain, plus any src/-defined
    subclasses (the concrete runtime instance stored as "host" could be a
    subclass — e.g. DxfCanvas rather than bare CanvasView). Returns None
    if the class can't be found/imported at all, so callers treat "can't
    verify" as "don't flag" rather than guessing.
    """
    if class_name in _seen:
        return set()
    found = _find_class_def(class_name)
    if found is None:
        return None
    path, node = found
    try:
        module = _import_module_for(path)
        cls = getattr(module, class_name)
        members = set(dir(cls))
    except Exception:
        members = set()
    members |= _ancestor_declared_names(class_name)

    for other_path in SRC_FILES:
        other_tree = _parse(other_path)
        for other_node in ast.walk(other_tree):
            if isinstance(other_node, ast.ClassDef) and any(
                isinstance(b, ast.Name) and b.id == class_name for b in other_node.bases
            ):
                sub_members = _host_member_registry(other_node.name, _seen | {class_name})
                if sub_members:
                    members |= sub_members
    return members


@dataclass
class Delegation:
    owner_class: str
    service_class: str
    host_attr_in_service: str


def _resolve_stored_param_attr(cls_node: ast.ClassDef) -> str | None:
    """In `def __init__(self, host, ...): self._host = host`, return
    '_host' — the attribute name the first non-self constructor parameter
    ends up stored under."""
    init = next(
        (
            n for n in cls_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init__"
        ),
        None,
    )
    if init is None or len(init.args.args) < 2:
        return None
    first_param = init.args.args[1].arg
    for node in ast.walk(init):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Attribute)
            and isinstance(node.targets[0].value, ast.Name)
            and node.targets[0].value.id == "self"
            and isinstance(node.value, ast.Name)
            and node.value.id == first_param
        ):
            return node.targets[0].attr
    return None


def _find_delegations() -> list[Delegation]:
    """Find `self.<x> = <ServiceClass>(self)` patterns: an owner class
    instantiates a service class, passing itself as the sole constructor
    argument, and resolves what attribute name the service's own __init__
    stores that argument under.
    """
    delegations: list[Delegation] = []

    for path in SRC_FILES:
        tree = _parse(path)
        for cls_node in ast.walk(tree):
            if not isinstance(cls_node, ast.ClassDef):
                continue
            owner_class = cls_node.name
            for node in ast.walk(cls_node):
                if not (
                    isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Attribute)
                    and isinstance(node.targets[0].value, ast.Name)
                    and node.targets[0].value.id == "self"
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                ):
                    continue
                service_class = node.value.func.id
                args = node.value.args
                if len(args) != 1 or not (isinstance(args[0], ast.Name) and args[0].id == "self"):
                    continue
                found = _find_class_def(service_class)
                if found is None:
                    continue
                _, service_node = found
                host_attr = _resolve_stored_param_attr(service_node)
                if host_attr is None:
                    continue
                delegations.append(Delegation(owner_class, service_class, host_attr))
    return delegations


@dataclass
class MissingHostMember:
    file: str
    lineno: int
    host_attr: str
    member_name: str
    owner_class: str
    service_class: str


def _find_missing_host_members() -> list[MissingHostMember]:
    findings: list[MissingHostMember] = []
    registry_cache: dict[str, set[str] | None] = {}

    for d in _find_delegations():
        if d.owner_class not in registry_cache:
            registry_cache[d.owner_class] = _host_member_registry(d.owner_class)
        registry = registry_cache[d.owner_class]
        if registry is None:
            continue

        found = _find_class_def(d.service_class)
        if found is None:
            continue
        service_path, service_node = found

        for node in ast.walk(service_node):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.ctx, ast.Load)
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
                and node.value.attr == d.host_attr_in_service
            ):
                member_name = node.attr
                if member_name not in registry:
                    findings.append(MissingHostMember(
                        str(service_path.relative_to(ROOT)), node.lineno,
                        d.host_attr_in_service, member_name, d.owner_class, d.service_class,
                    ))
    return findings


_MISSING_HOST_MEMBERS = _find_missing_host_members()


@pytest.mark.parametrize(
    "finding", _MISSING_HOST_MEMBERS,
    ids=lambda f: f"{f.file}:{f.lineno}:{f.host_attr}.{f.member_name}",
)
def test_no_missing_host_member_calls(finding: MissingHostMember):
    """A service class must not call `self.<host_attr>.<name>` unless
    `<name>` actually exists on the owner class that instantiates it (or
    that owner's subclasses).

    This is the check that would have caught the real crash:
    `EditingService.selection_geometry()` called `self._host._selected_indices()`,
    but `CanvasView` (the only class that ever instantiates EditingService,
    passing itself as `host`) no longer defines that method — it was
    removed in favor of ID-based `_selected_ids()`/`_mutable_selected_ids()`,
    and this call site (along with ~40 others across 7 files) was never
    migrated. The app imported fine, every other test in this suite passed,
    and it still crashed the moment a user opened the app and the
    properties panel tried to read the current selection's bounds.
    """
    pytest.fail(
        f"{finding.file}:{finding.lineno} — {finding.service_class} calls "
        f"self.{finding.host_attr}.{finding.member_name}(...), but "
        f"{finding.owner_class} (the only class that instantiates "
        f"{finding.service_class} with itself as host) has no "
        f"'{finding.member_name}' member."
    )


if not _MISSING_HOST_MEMBERS:
    def test_no_missing_host_member_calls_found_none():
        """Placeholder so pytest reports this check ran even when the
        parametrized test above has zero cases to generate (an empty
        parametrize list produces no test items at all, which would
        otherwise make a fully-fixed codebase look like the check never ran)."""
        assert _MISSING_HOST_MEMBERS == []
