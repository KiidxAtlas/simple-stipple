"""Tests for the bounded data-only MS-NRBF reader."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from simple_stipple.core.formats.nrbf import (
    Limits,
    class_name,
    find_classes,
    list_items,
    read_nrbf,
)

SAMPLE = Path(
    "/Users/atlasp./dev/projects/work/aa-laser/starfx-jobs/AR RECEIVER ENGRAVINGS/accurate arms receiver logo NEW.sfx"
)


def lps(text: str) -> bytes:
    raw = text.encode()
    n = len(raw)
    prefix = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        prefix.append(b | (0x80 if n else 0))
        if not n:
            break
    return bytes(prefix) + raw


def i32(v: int) -> bytes:
    return struct.pack("<i", v)


def header(root: int = 1) -> bytes:
    return b"\x00" + i32(root) + i32(-1) + i32(1) + i32(0)


END = b"\x0b"


def class_rec(
    obj_id: int, name: str, members: list[tuple[str, int, bytes]], lib: int | None = None
) -> bytes:
    """members: (name, BinaryType, additional-info bytes)."""
    out = bytes([5 if lib is not None else 4]) + i32(obj_id) + lps(name) + i32(len(members))
    out += b"".join(lps(m[0]) for m in members)
    out += bytes(m[1] for m in members)
    out += b"".join(m[2] for m in members)
    if lib is not None:
        out += i32(lib)
    return out


def library(lib_id: int, name: str = "Asm") -> bytes:
    return b"\x0c" + i32(lib_id) + lps(name)


PRIM_I32 = (0, bytes([8]))
PRIM_DBL = (0, bytes([6]))
OBJ = (2, b"")
STR = (1, b"")


def test_class_with_primitives_string_and_library() -> None:
    data = (
        header()
        + library(2, "My.Lib")
        + class_rec(
            1,
            "My.Point",
            [("X", *PRIM_I32), ("Y", *PRIM_DBL), ("Name", *STR), ("Flag", 0, bytes([1]))],
            lib=2,
        )
        + i32(-5)
        + struct.pack("<d", 2.5)
        + b"\x06"
        + i32(3)
        + lps("hi")
        + b"\x01"
        + END
    )
    root = read_nrbf(data)
    assert root == {
        "__class__": "My.Point",
        "__library__": "My.Lib",
        "__id__": 1,
        "X": -5,
        "Y": 2.5,
        "Name": "hi",
        "Flag": True,
    }
    assert class_name(root) == "My.Point"


def test_member_references_forward_and_shared_and_class_with_id() -> None:
    meta = class_rec(1, "Node", [("Left", *OBJ), ("Right", *OBJ), ("Tag", *OBJ)], lib=None)
    # Left -> inline Node (id 2, via ClassWithId) whose members are refs/null
    inner = b"\x01" + i32(2) + i32(1) + b"\x0a" + b"\x0a" + b"\x09" + i32(4)
    # Right -> reference to id 2 (shared), Tag -> forward ref to string 4 defined later
    data = (
        header()
        + meta
        + inner
        + b"\x09"
        + i32(2)
        + b"\x09"
        + i32(4)
        + b"\x06"
        + i32(4)
        + lps("late")
        + END
    )
    # string id 4 appears after the root completes; spec allows later definitions.
    root = read_nrbf(data)
    assert root["Left"] is root["Right"]
    assert root["Left"]["__class__"] == "Node"
    assert root["Left"]["Left"] is None
    assert root["Left"]["Tag"] == "late"
    assert root["Tag"] == "late"


def test_null_runs_in_members_and_arrays() -> None:
    members = [("A", *OBJ), ("B", *OBJ), ("C", *OBJ), ("D", *PRIM_I32)]
    data = header() + class_rec(1, "T", members, lib=None) + b"\x0d\x03" + i32(9) + END
    root = read_nrbf(data)
    assert (root["A"], root["B"], root["C"], root["D"]) == (None, None, None, 9)
    # ArraySingleObject with ObjectNullMultiple (4-byte) + a string
    arr = b"\x10" + i32(1) + i32(5) + b"\x0e" + i32(4) + b"\x06" + i32(2) + lps("z")
    assert read_nrbf(header() + arr + END) == [None, None, None, None, "z"]


def test_primitive_arrays_and_string_array() -> None:
    doubles = b"\x0f" + i32(1) + i32(3) + bytes([6]) + struct.pack("<3d", 1.0, -2.0, 0.5)
    assert read_nrbf(header() + doubles + END) == [1.0, -2.0, 0.5]
    ints = b"\x0f" + i32(1) + i32(2) + bytes([8]) + struct.pack("<2i", 7, -7)
    assert read_nrbf(header() + ints + END) == [7, -7]
    strs = b"\x11" + i32(1) + i32(2) + b"\x06" + i32(2) + lps("a") + b"\x09" + i32(2)
    assert read_nrbf(header() + strs + END) == ["a", "a"]
    chars = b"\x0f" + i32(1) + i32(2) + bytes([3]) + "é".encode() + b"x"
    assert read_nrbf(header() + chars + END) == ["é", "x"]


def test_binary_array_single_rectangular_and_jagged() -> None:
    single = (
        b"\x07"
        + i32(1)
        + b"\x00"
        + i32(1)
        + i32(3)
        + bytes([0])
        + bytes([8])
        + struct.pack("<3i", 1, 2, 3)
    )
    assert read_nrbf(header() + single + END) == [1, 2, 3]
    rect = (
        b"\x07"
        + i32(1)
        + b"\x02"
        + i32(2)
        + i32(2)
        + i32(3)
        + bytes([0])
        + bytes([8])
        + struct.pack("<6i", 1, 2, 3, 4, 5, 6)
    )
    assert read_nrbf(header() + rect + END) == [[1, 2, 3], [4, 5, 6]]
    jagged = (
        b"\x07"
        + i32(1)
        + b"\x01"
        + i32(1)
        + i32(2)
        + bytes([7, 8])
        + b"\x0f"
        + i32(2)
        + i32(1)
        + bytes([8])
        + i32(10)
        + b"\x0f"
        + i32(3)
        + i32(2)
        + bytes([8])
        + i32(20)
        + i32(30)
    )
    # PrimitiveArray element kind carries the primitive type byte (8 = Int32)
    assert read_nrbf(header() + jagged + END) == [[10], [20, 30]]


def test_generic_list_helpers_and_find_classes() -> None:
    items = (
        b"\x10"
        + i32(2)
        + i32(4)
        + b"\x06"
        + i32(3)
        + lps("a")
        + b"\x06"
        + i32(4)
        + lps("b")
        + b"\x0d\x02"
    )
    lst = class_rec(
        1, "System.Collections.Generic.List`1[[X]]", [("_items", 5, b""), ("_size", *PRIM_I32)]
    )
    data = header() + lst + items + i32(2) + END
    root = read_nrbf(data)
    assert list_items(root) == ["a", "b"]
    assert list_items([1]) == [1]
    assert [c["__id__"] for c in find_classes(root, "List")] == [1]
    with pytest.raises(ValueError):
        list_items({"__class__": "x"})


def test_datetime_decimal_timespan_primitives() -> None:
    members = [("D", 0, bytes([5])), ("T", 0, bytes([13])), ("S", 0, bytes([12]))]
    data = header() + class_rec(1, "P", members) + lps("1.50") + struct.pack("<qq", 123, 456) + END
    root = read_nrbf(data)
    assert str(root["D"]) == "1.50" and root["T"] == 123 and root["S"] == 456


# -- malformed / hostile input ---------------------------------------------
def simple_root() -> bytes:
    return header() + class_rec(1, "T", [("A", *PRIM_I32)]) + i32(1) + END


def test_every_truncation_fails_with_value_error() -> None:
    data = simple_root()
    for n in range(len(data)):
        with pytest.raises(ValueError):
            read_nrbf(data[:n])


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x01" + b"\x00" * 16,  # no header record
        header() + b"\x63",  # unknown record type
        header() + END,  # missing root
        header(7) + class_rec(1, "T", []) + END,  # root id absent
        simple_root() + b"\x00",  # trailing data
        header() + b"\x01" + i32(1) + i32(99) + END,  # unresolved metadata id
        header() + class_rec(1, "T", [("A", *OBJ)]) + b"\x09" + i32(42) + END,  # unresolved ref
        header() + b"\x06" + i32(0) + lps("x") + END,  # zero object id
        header() + b"\x06" + i32(1) + lps("x") + b"\x06" + i32(1) + lps("y") + END,  # duplicate id
        header() + b"\x0f" + i32(1) + i32(-1) + bytes([8]) + END,  # negative length
        header() + b"\x0f" + i32(1) + i32(2) + bytes([99]) + END,  # bad primitive type
        header() + b"\x02" + i32(1) + lps("T") + i32(0) + END,  # untyped class record
        header() + b"\x06" + i32(1) + b"\x05\xff\xfe" + END,  # string runs past the end
        header() + b"\x06" + i32(1) + b"\x02\xff\xfe" + END,  # invalid UTF-8
        header() + b"\x06" + i32(1) + b"\xff\xff\xff\xff\xff\x7f" + END,  # overlong length prefix
        b"\x00" + i32(1) + i32(-1) + i32(2) + i32(0) + END,  # unsupported version
        header() + b"\x10" + i32(1) + i32(2) + b"\x0e" + i32(5) + END,  # null run overruns array
        header()
        + class_rec(1, "T", [("A", *OBJ)])
        + b"\x0e"
        + i32(2)
        + END,  # null run overruns members
    ],
)
def test_malformed_inputs_raise_value_error(data: bytes) -> None:
    with pytest.raises(ValueError):
        read_nrbf(data)


def test_non_bytes_input_rejected() -> None:
    with pytest.raises(ValueError):
        read_nrbf("text")  # type: ignore[arg-type]


def test_cycles_rejected_unless_allowed() -> None:
    data = header() + class_rec(1, "Loop", [("Self", *OBJ)]) + b"\x09" + i32(1) + END
    with pytest.raises(ValueError, match="cyclic"):
        read_nrbf(data)
    root = read_nrbf(data, Limits(allow_cycles=True))
    assert root["Self"] is root
    # Cycle through an array
    arr = header() + b"\x10" + i32(1) + i32(1) + b"\x09" + i32(1) + END
    with pytest.raises(ValueError):
        read_nrbf(arr)


def test_shared_non_cyclic_objects_are_allowed() -> None:
    shared = b"\x10" + i32(1) + i32(2) + b"\x06" + i32(2) + lps("s") + b"\x09" + i32(2) + END
    assert read_nrbf(header() + shared) == ["s", "s"]


# -- limits ----------------------------------------------------------------
def test_input_size_limit() -> None:
    with pytest.raises(ValueError, match="size"):
        read_nrbf(simple_root(), Limits(max_input_bytes=10))


def test_array_length_limit_checked_before_allocation() -> None:
    huge = header() + b"\x0f" + i32(1) + i32(2**31 - 1) + bytes([6])
    with pytest.raises(ValueError):
        read_nrbf(huge)
    with pytest.raises(ValueError):
        read_nrbf(header() + b"\x10" + i32(1) + i32(2**31 - 1))
    # Rectangular product overflow beyond the limit
    rect = (
        header() + b"\x07" + i32(1) + b"\x02" + i32(2) + i32(100000) + i32(100000) + bytes([0, 8])
    )
    with pytest.raises(ValueError):
        read_nrbf(rect)


def test_total_element_budget() -> None:
    arr = b"\x0f" + i32(1) + i32(100) + bytes([2]) + bytes(100)
    with pytest.raises(ValueError, match="budget"):
        read_nrbf(header() + arr + END, Limits(max_total_elements=50))


def test_object_count_limit() -> None:
    arr = b"\x10" + i32(1) + i32(3) + b"".join(b"\x06" + i32(i) + lps("x") for i in (2, 3, 4))
    with pytest.raises(ValueError, match="object limit"):
        read_nrbf(header() + arr + END, Limits(max_objects=3))


def test_string_size_limit() -> None:
    with pytest.raises(ValueError, match="string"):
        read_nrbf(header() + b"\x06" + i32(1) + lps("x" * 20) + END, Limits(max_string_bytes=10))


def test_nesting_depth_limit() -> None:
    depth = 150
    body = b""
    for i in range(1, depth + 1):
        body += class_rec(i, "N", [("Child", *OBJ)])
    body += b"\x0a"
    with pytest.raises(ValueError, match="nesting"):
        read_nrbf(header() + body + END, Limits(max_depth=100))
    assert read_nrbf(header() + body + END, Limits(max_depth=200)) is not None


def test_member_count_limit() -> None:
    names = b"".join(lps(f"m{i}") for i in range(10))
    rec = b"\x04" + i32(1) + lps("T") + i32(10) + names
    with pytest.raises(ValueError):
        read_nrbf(header() + rec + END, Limits(max_members=5))


# -- real StarFX sample ----------------------------------------------------
@pytest.mark.skipif(not SAMPLE.exists(), reason="StarFX sample job not available")
def test_receiver_sfx_sample_parses_as_data() -> None:
    root = read_nrbf(SAMPLE.read_bytes())
    assert root["__class__"] == "StarFX_Control.Classes.JobBase"
    objs = list_items(root["<JobObjects>k__BackingField"])
    assert len(objs) == 14
    cad = [o["<CadEntity>k__BackingField"] for o in objs if o["<CadEntity>k__BackingField"]]
    kinds = {c["__class__"].rsplit(".", 1)[-1] for c in cad}
    assert kinds == {"VectorEntity", "RectangleEntity", "CircleEntity", "MultilineTextEntity"}
    vec = next(c for c in cad if c["__class__"].endswith("VectorEntity"))
    data = vec["_relativeVectorFormatData"]
    assert isinstance(data, list) and len(data) == 7038 and all(isinstance(v, float) for v in data)
    rect = next(c for c in cad if c["__class__"].endswith("RectangleEntity"))
    sub = list_items(rect["BasePolylineEntity+subPathList"])[0]
    pts = list_items(sub["pointList"])
    assert pts and {"xVal", "yVal", "zVal"} <= set(pts[0])


@pytest.mark.skipif(not SAMPLE.exists(), reason="StarFX sample job not available")
def test_receiver_sfx_sample_rejects_every_coarse_truncation() -> None:
    data = SAMPLE.read_bytes()
    for n in (0, 1, 100, len(data) // 2, len(data) - 1):
        with pytest.raises(ValueError):
            read_nrbf(data[:n])
