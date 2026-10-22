"""Bounded, data-only reader for MS-NRBF (.NET BinaryFormatter) streams.

The reader only *describes* the wire records as plain Python data.  It never
imports, instantiates or calls anything named by the stream: class records
become ``dict`` objects, arrays become ``list`` objects, strings become
``str`` and primitives become ``bool``/``int``/``float``/``str``/
``Decimal``.  Every failure (malformed, truncated, oversized, unresolved or
cyclic input) raises :class:`ValueError`.

Class objects are dicts with these reserved keys followed by the member
fields in wire order (backing-field names such as ``<X>k__BackingField`` are
kept verbatim)::

    {"__class__": "Ns.Type", "__library__": "Asm, Version=..." | None,
     "__id__": 7, "FieldA": ..., "FieldB": ...}

Object references are resolved to the *same* Python object (shared, not
copied).  Cyclic graphs are rejected unless ``Limits.allow_cycles`` is set.

Specification: [MS-NRBF] Remoting: .NET Binary Format Data Structure.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = [
    "Limits",
    "class_name",
    "find_classes",
    "list_items",
    "read_nrbf",
]

_RT_HEADER = 0
_RT_CLASS_WITH_ID = 1
_RT_SYS_CLASS_MEMBERS = 2
_RT_CLASS_MEMBERS = 3
_RT_SYS_CLASS_TYPES = 4
_RT_CLASS_TYPES = 5
_RT_STRING = 6
_RT_BINARY_ARRAY = 7
_RT_PRIMITIVE_TYPED = 8
_RT_REFERENCE = 9
_RT_NULL = 10
_RT_END = 11
_RT_LIBRARY = 12
_RT_NULL_256 = 13
_RT_NULL_MULTI = 14
_RT_ARRAY_PRIMITIVE = 15
_RT_ARRAY_OBJECT = 16
_RT_ARRAY_STRING = 17

_BT_PRIMITIVE = 0
_BT_STRING = 1
_BT_OBJECT = 2
_BT_SYSTEM_CLASS = 3
_BT_CLASS = 4
_BT_OBJECT_ARRAY = 5
_BT_STRING_ARRAY = 6
_BT_PRIMITIVE_ARRAY = 7

_PRIM_STRUCT: dict[int, tuple[str, int]] = {
    1: ("?", 1),  # Boolean
    2: ("B", 1),  # Byte
    6: ("d", 8),  # Double
    7: ("h", 2),  # Int16
    8: ("i", 4),  # Int32
    9: ("q", 8),  # Int64
    10: ("b", 1),  # SByte
    11: ("f", 4),  # Single
    12: ("q", 8),  # TimeSpan (ticks)
    13: ("q", 8),  # DateTime (raw 64-bit; kind in top 2 bits)
    14: ("H", 2),  # UInt16
    15: ("I", 4),  # UInt32
    16: ("Q", 8),  # UInt64
}
_PRIM_CHAR = 3
_PRIM_DECIMAL = 5
_PRIM_NULL = 17
_PRIM_STRING = 18

_ARR_SINGLE = 0
_ARR_JAGGED = 1
_ARR_RECTANGULAR = 2
_ARR_OFFSETS = {3: _ARR_SINGLE, 4: _ARR_JAGGED, 5: _ARR_RECTANGULAR}


@dataclass(frozen=True)
class Limits:
    """Resource budget enforced while parsing."""

    max_input_bytes: int = 64 * 1024 * 1024
    max_objects: int = 1_000_000
    max_array_length: int = 4_000_000
    max_total_elements: int = 8_000_000
    max_string_bytes: int = 16 * 1024 * 1024
    max_depth: int = 128
    max_members: int = 4096
    max_rank: int = 8
    allow_cycles: bool = False


class _Ref:
    __slots__ = ("id",)

    def __init__(self, ref_id: int) -> None:
        self.id = ref_id


class _NullRun:
    __slots__ = ("count",)

    def __init__(self, count: int) -> None:
        self.count = count


@dataclass(frozen=True)
class _ClassMeta:
    name: str
    library: str | None
    members: tuple[str, ...]
    types: tuple[tuple[int, Any], ...]


class _Parser:
    def __init__(self, data: bytes, limits: Limits) -> None:
        self.data = data
        self.pos = 0
        self.limits = limits
        self.libraries: dict[int, str] = {}
        self.metas: dict[int, _ClassMeta] = {}
        self.objects: dict[int, Any] = {}
        self.containers: list[Any] = []
        self.elements = 0

    # -- primitives -----------------------------------------------------
    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("NRBF stream is truncated")
        out = self.data[self.pos : self.pos + n]
        self.pos += n
        return out

    def u8(self) -> int:
        return self.take(1)[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def length_prefixed(self) -> str:
        length = 0
        for shift in range(0, 35, 7):
            b = self.u8()
            length |= (b & 0x7F) << shift
            if not b & 0x80:
                break
        else:
            raise ValueError("NRBF string length prefix is malformed")
        if length > self.limits.max_string_bytes:
            raise ValueError("NRBF string exceeds the size limit")
        raw = self.take(length)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("NRBF string is not valid UTF-8") from exc

    def budget(self, n: int) -> None:
        self.elements += n
        if self.elements > self.limits.max_total_elements:
            raise ValueError("NRBF stream exceeds the element budget")

    def register(self, obj_id: int, obj: Any) -> None:
        if obj_id == 0:
            # .NET writes negative ids for some objects; zero is never valid.
            raise ValueError("NRBF object id must be non-zero")
        if obj_id in self.objects:
            raise ValueError(f"NRBF duplicate object id {obj_id}")
        if len(self.objects) >= self.limits.max_objects:
            raise ValueError("NRBF stream exceeds the object limit")
        self.objects[obj_id] = obj
        if isinstance(obj, (dict, list)):
            self.containers.append(obj)

    def primitive(self, ptype: int) -> Any:
        spec = _PRIM_STRUCT.get(ptype)
        if spec is not None:
            return struct.unpack("<" + spec[0], self.take(spec[1]))[0]
        if ptype == _PRIM_CHAR:
            first = self.u8()
            n = (
                1
                if first < 0x80
                else 2
                if first >> 5 == 0b110
                else 3
                if first >> 4 == 0b1110
                else 4
            )
            raw = bytes([first]) + self.take(n - 1)
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("NRBF char is not valid UTF-8") from exc
        if ptype == _PRIM_DECIMAL:
            try:
                return Decimal(self.length_prefixed())
            except InvalidOperation as exc:
                raise ValueError("NRBF decimal is malformed") from exc
        if ptype == _PRIM_STRING:
            return self.length_prefixed()
        raise ValueError(f"NRBF unsupported primitive type {ptype}")

    # -- type info ------------------------------------------------------
    def read_class_info(self) -> tuple[int, str, tuple[str, ...]]:
        obj_id = self.i32()
        name = self.length_prefixed()
        count = self.i32()
        if not 0 <= count <= self.limits.max_members:
            raise ValueError("NRBF class member count is out of range")
        members = tuple(self.length_prefixed() for _ in range(count))
        return obj_id, name, members

    def read_member_types(self, count: int) -> tuple[tuple[int, Any], ...]:
        kinds = [self.u8() for _ in range(count)]
        out: list[tuple[int, Any]] = []
        for kind in kinds:
            if kind in (_BT_PRIMITIVE, _BT_PRIMITIVE_ARRAY):
                ptype = self.u8()
                if ptype not in _PRIM_STRUCT and ptype not in (
                    _PRIM_CHAR,
                    _PRIM_DECIMAL,
                    _PRIM_STRING,
                ):
                    raise ValueError(f"NRBF unsupported primitive type {ptype}")
                out.append((kind, ptype))
            elif kind == _BT_SYSTEM_CLASS:
                out.append((kind, self.length_prefixed()))
            elif kind == _BT_CLASS:
                out.append((kind, (self.length_prefixed(), self.i32())))
            elif kind in (_BT_STRING, _BT_OBJECT, _BT_OBJECT_ARRAY, _BT_STRING_ARRAY):
                out.append((kind, None))
            else:
                raise ValueError(f"NRBF unknown binary type {kind}")
        return tuple(out)

    # -- records --------------------------------------------------------
    def read_slot(self, depth: int) -> Any:
        """Read one object-valued record; may return `_NullRun` or `_Ref`."""
        if depth > self.limits.max_depth:
            raise ValueError("NRBF stream exceeds the nesting limit")
        while True:
            rtype = self.u8()
            if rtype == _RT_LIBRARY:
                self.read_library()
                continue
            return self.read_record(rtype, depth)

    def read_library(self) -> None:
        lib_id = self.i32()
        name = self.length_prefixed()
        if lib_id == 0 or lib_id in self.libraries:
            raise ValueError("NRBF library id is invalid or duplicated")
        self.libraries[lib_id] = name

    def read_record(self, rtype: int, depth: int) -> Any:
        if rtype == _RT_STRING:
            obj_id = self.i32()
            value = self.length_prefixed()
            self.register(obj_id, value)
            return value
        if rtype in (_RT_REFERENCE, _RT_NULL, _RT_NULL_256, _RT_NULL_MULTI, _RT_PRIMITIVE_TYPED):
            return self.read_simple(rtype)
        if rtype == _RT_CLASS_WITH_ID:
            obj_id = self.i32()
            meta_id = self.i32()
            meta = self.metas.get(meta_id)
            if meta is None:
                raise ValueError(f"NRBF class metadata {meta_id} is unresolved")
            return self.read_class_body(obj_id, meta, depth)
        if rtype in (_RT_SYS_CLASS_TYPES, _RT_CLASS_TYPES):
            obj_id, meta = self.read_class_meta(rtype)
            return self.read_class_body(obj_id, meta, depth)
        if rtype in (_RT_SYS_CLASS_MEMBERS, _RT_CLASS_MEMBERS):
            raise ValueError("NRBF class records without member types are unsupported")
        if rtype == _RT_ARRAY_PRIMITIVE:
            return self.read_primitive_array()
        if rtype in (_RT_ARRAY_OBJECT, _RT_ARRAY_STRING):
            obj_id = self.i32()
            length = self.read_length()
            arr: list[Any] = []
            self.register(obj_id, arr)
            self.fill_slots(arr, length, depth + 1)
            return arr
        if rtype == _RT_BINARY_ARRAY:
            return self.read_binary_array(depth)
        if rtype == _RT_END:
            raise ValueError("NRBF unexpected end-of-stream record")
        raise ValueError(f"NRBF unknown record type {rtype}")

    def read_simple(self, rtype: int) -> Any:
        if rtype == _RT_REFERENCE:
            return _Ref(self.i32())
        if rtype == _RT_NULL:
            return None
        if rtype == _RT_NULL_256:
            return _NullRun(self.u8())
        if rtype == _RT_NULL_MULTI:
            n = self.i32()
            if n < 0:
                raise ValueError("NRBF null run length is negative")
            return _NullRun(n)
        if rtype == _RT_PRIMITIVE_TYPED:
            return self.primitive(self.u8())
        raise ValueError(f"NRBF unknown record type {rtype}")

    def read_class_meta(self, rtype: int) -> tuple[int, _ClassMeta]:
        obj_id, name, members = self.read_class_info()
        types = self.read_member_types(len(members))
        library: str | None = None
        if rtype == _RT_CLASS_TYPES:
            lib_id = self.i32()
            library = self.libraries.get(lib_id)
            if library is None:
                raise ValueError(f"NRBF library {lib_id} is unresolved")
        if obj_id in self.metas:
            raise ValueError(f"NRBF duplicate class id {obj_id}")
        meta = _ClassMeta(name, library, members, types)
        self.metas[obj_id] = meta
        return obj_id, meta

    def read_length(self) -> int:
        n = self.i32()
        if not 0 <= n <= self.limits.max_array_length:
            raise ValueError("NRBF array length is out of range")
        self.budget(n)
        return n

    def fill_slots(self, out: list[Any], count: int, depth: int) -> None:
        while len(out) < count:
            v = self.read_slot(depth)
            if isinstance(v, _NullRun):
                if v.count > count - len(out):
                    raise ValueError("NRBF null run overruns its array")
                out.extend([None] * v.count)
            else:
                out.append(v)

    def read_class_body(self, obj_id: int, meta: _ClassMeta, depth: int) -> dict[str, Any]:
        if depth > self.limits.max_depth:
            raise ValueError("NRBF stream exceeds the nesting limit")
        self.budget(len(meta.members))
        obj: dict[str, Any] = {
            "__class__": meta.name,
            "__library__": meta.library,
            "__id__": obj_id,
        }
        self.register(obj_id, obj)
        pending_nulls = 0
        for name, (kind, extra) in zip(meta.members, meta.types, strict=True):
            if kind == _BT_PRIMITIVE:
                value = self.primitive(extra)
            elif pending_nulls:
                pending_nulls -= 1
                value = None
            else:
                value = self.read_slot(depth + 1)
                if isinstance(value, _NullRun):
                    if value.count < 1:
                        raise ValueError("NRBF empty null run in class member")
                    pending_nulls = value.count - 1
                    value = None
            obj[name] = value
        if pending_nulls:
            raise ValueError("NRBF null run overruns its class members")
        return obj

    def read_primitive_array(self) -> list[Any]:
        obj_id = self.i32()
        length = self.read_length()
        ptype = self.u8()
        arr = self.read_primitive_elements(length, ptype)
        self.register(obj_id, arr)
        return arr

    def read_primitive_elements(self, length: int, ptype: int) -> list[Any]:
        spec = _PRIM_STRUCT.get(ptype)
        if spec is not None:
            raw = self.take(length * spec[1])
            return list(struct.unpack(f"<{length}{spec[0]}", raw))
        if ptype in (_PRIM_CHAR, _PRIM_DECIMAL, _PRIM_STRING):
            return [self.primitive(ptype) for _ in range(length)]
        raise ValueError(f"NRBF unsupported primitive array type {ptype}")

    def read_binary_array(self, depth: int) -> list[Any]:
        obj_id = self.i32()
        arr_type = self.u8()
        rank = self.i32()
        if not 1 <= rank <= self.limits.max_rank:
            raise ValueError("NRBF array rank is out of range")
        lengths = [self.i32() for _ in range(rank)]
        if arr_type in _ARR_OFFSETS:
            for _ in range(rank):
                self.i32()
            arr_type = _ARR_OFFSETS[arr_type]
        if arr_type not in (_ARR_SINGLE, _ARR_JAGGED, _ARR_RECTANGULAR):
            raise ValueError(f"NRBF unknown array type {arr_type}")
        if arr_type != _ARR_RECTANGULAR and rank != 1:
            raise ValueError("NRBF non-rectangular array must have rank 1")
        total = 1
        for n in lengths:
            if n < 0 or n > self.limits.max_array_length:
                raise ValueError("NRBF array length is out of range")
            total *= n
            if total > self.limits.max_array_length:
                raise ValueError("NRBF array length is out of range")
        self.budget(total)
        kind = self.u8()
        ptype = -1
        if kind in (_BT_PRIMITIVE, _BT_PRIMITIVE_ARRAY):
            ptype = self.u8()
        elif kind == _BT_SYSTEM_CLASS:
            self.length_prefixed()
        elif kind == _BT_CLASS:
            self.length_prefixed()
            self.i32()
        elif kind not in (_BT_STRING, _BT_OBJECT, _BT_OBJECT_ARRAY, _BT_STRING_ARRAY):
            raise ValueError(f"NRBF unknown binary type {kind}")
        flat: list[Any]
        if kind == _BT_PRIMITIVE:
            flat = self.read_primitive_elements(total, ptype)
            self.register(obj_id, flat)
        else:
            flat = []
            self.register(obj_id, flat)
            self.fill_slots(flat, total, depth + 1)
        if arr_type != _ARR_RECTANGULAR or rank == 1:
            return flat
        # Rectangular: reshape into nested lists; replace contents in place so
        # that references to ``obj_id`` see the nested form.
        nested = _reshape(flat, lengths)
        self.objects[obj_id] = nested
        self.containers.append(nested)
        return nested

    # -- driver ---------------------------------------------------------
    def parse(self) -> Any:
        if len(self.data) > self.limits.max_input_bytes:
            raise ValueError("NRBF input exceeds the size limit")
        if self.u8() != _RT_HEADER:
            raise ValueError("NRBF stream does not start with a header record")
        root_id = self.i32()
        self.i32()  # header id
        major = self.i32()
        minor = self.i32()
        if (major, minor) != (1, 0):
            raise ValueError(f"NRBF unsupported version {major}.{minor}")
        while True:
            rtype = self.u8()
            if rtype == _RT_END:
                break
            if rtype == _RT_LIBRARY:
                self.read_library()
                continue
            value = self.read_record(rtype, 0)
            if isinstance(value, _NullRun):
                continue
        if self.pos != len(self.data):
            raise ValueError("NRBF stream has trailing data after the end record")
        if root_id not in self.objects:
            raise ValueError("NRBF root object is not present")
        self.resolve()
        root = self.objects[root_id]
        if not self.limits.allow_cycles:
            _reject_cycles(root)
        return root

    def lookup(self, value: Any) -> Any:
        if isinstance(value, _Ref):
            try:
                return self.objects[value.id]
            except KeyError:
                raise ValueError(f"NRBF unresolved object reference {value.id}") from None
        return value

    def resolve(self) -> None:
        for container in self.containers:
            if isinstance(container, dict):
                for key, value in container.items():
                    if isinstance(value, _Ref):
                        container[key] = self.lookup(value)
            else:
                for i, value in enumerate(container):
                    if isinstance(value, _Ref):
                        container[i] = self.lookup(value)


def _reshape(flat: list[Any], lengths: list[int]) -> list[Any]:
    if len(lengths) == 1:
        return flat
    step = 1
    for n in lengths[1:]:
        step *= n
    return [_reshape(flat[i * step : (i + 1) * step], lengths[1:]) for i in range(lengths[0])]


def _children(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("__class__", "__library__", "__id__") and isinstance(v, (dict, list)):
                yield v
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                yield v


def _reject_cycles(root: Any) -> None:
    if not isinstance(root, (dict, list)):
        return
    on_stack: set[int] = set()
    done: set[int] = set()
    stack: list[tuple[Any, Iterator[Any]]] = [(root, _children(root))]
    on_stack.add(id(root))
    while stack:
        node, it = stack[-1]
        for child in it:
            cid = id(child)
            if cid in on_stack:
                raise ValueError("NRBF object graph is cyclic")
            if cid not in done:
                on_stack.add(cid)
                stack.append((child, _children(child)))
                break
        else:
            stack.pop()
            on_stack.discard(id(node))
            done.add(id(node))


def read_nrbf(data: bytes, limits: Limits | None = None) -> Any:
    """Parse an MS-NRBF stream into plain Python data (see module docs).

    Raises :class:`ValueError` for any malformed, truncated, oversized,
    unresolved or (by default) cyclic input.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ValueError("NRBF input must be bytes")
    lim = limits or Limits()
    raw = bytes(data)
    if len(raw) > lim.max_input_bytes:
        raise ValueError("NRBF input exceeds the size limit")
    try:
        return _Parser(raw, lim).parse()
    except (struct.error, OverflowError, MemoryError, RecursionError) as exc:
        raise ValueError(f"NRBF stream is malformed: {exc}") from exc


# -- convenience accessors ------------------------------------------------
def class_name(obj: Any) -> str | None:
    """Return the .NET type name of a class object, else ``None``."""
    if isinstance(obj, dict):
        name = obj.get("__class__")
        return name if isinstance(name, str) else None
    return None


def list_items(obj: Any) -> list[Any]:
    """Return the logical items of a ``System.Collections.Generic.List<T>``.

    A plain list is returned as-is.  Raises ``ValueError`` for anything that
    does not look like a serialized ``List<T>``.
    """
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and "_items" in obj and "_size" in obj:
        items, size = obj["_items"], obj["_size"]
        if isinstance(items, list) and isinstance(size, int) and 0 <= size <= len(items):
            return items[:size]
    raise ValueError("NRBF value is not a List<T>")


def find_classes(root: Any, match: str | Callable[[str], bool]) -> Iterator[dict[str, Any]]:
    """Yield every distinct class object under ``root`` whose type matches.

    ``match`` is either a substring of the type name or a predicate.  Safe on
    shared and cyclic graphs (each object is visited once).
    """
    pred = (lambda n: match in n) if isinstance(match, str) else match
    seen: set[int] = set()
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        if not isinstance(node, (dict, list)) or id(node) in seen:
            continue
        seen.add(id(node))
        name = class_name(node)
        if name is not None and pred(name) and isinstance(node, dict):
            yield node
        if isinstance(node, dict):
            stack.extend(reversed([v for k, v in node.items() if not k.startswith("__")]))
        else:
            stack.extend(reversed(node))
