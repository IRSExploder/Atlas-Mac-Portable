"""The geometry pack: a flat, immutable, memory-mapped store of region polygons.

Deliberately **not** SQLite. The pack is written once, offline, and thereafter only read.
Putting it in a database would raise the whole question of locking and sync behaviour for
no benefit, and the obvious mitigation there -- opening it with ``immutable=1`` -- is a
promise to SQLite that the bytes never change. Under OneDrive that promise is breakable,
and SQLite's response to a broken one is stale-page reads and garbage geometry rather
than an honest error. A flat file read through ``mmap`` sidesteps all of it.

**Only the standard library is used here.** Baking needs shapely and topojson, but those
are optional extras; the *runtime* must be able to read a pack with nothing but Python.
``array.array`` decodes doubles at C speed, so this costs nothing.

Layout, all little-endian::

    magic        8   b"ATLASPK1"
    header_len   4   uint32
    body_digest 32   blake2b-256 of the body, verified on open
    header       *   UTF-8 JSON index, not digested; structure-checked on open instead
    body         *   geometry blocks

A geometry block, one per region per level of detail::

    uint32 n_parts
      uint32 n_rings            (per part; ring 0 is the exterior, the rest are holes)
        uint32 n_points         (per ring)
        float64 * 2 * n_points  (lon, lat pairs)
"""

from __future__ import annotations

import json
import mmap
import struct
from array import array
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "BAKER_VERSION",
    "MAGIC",
    "PackCorruptError",
    "Ring",
    "GeometryPack",
    "PackSet",
    "RegionEntry",
    "encode_geometry",
    "write_pack",
]

#: **What baked this pack, and the reason the number exists.**
#:
#: Packs live outside the repository, under ``%LOCALAPPDATA%``. A geometry fix therefore
#: changes the *baker* and not the artefact: the pack on disk keeps whatever the baker was
#: when it was made, and nothing anywhere noticed. That cost a review cycle -- the Gibraltar
#: seam was fixed at `2ea080a`/`3b6c326`, and a reviewer running the audit against a pack
#: baked before it saw a shared-border failure that read exactly like a regression in code
#: that had in fact been correct for days.
#:
#: **Bump this whenever a change alters the geometry a bake produces**, and the audit will
#: tell everyone holding an older pack to re-bake, in those words, instead of handing them
#: a topology error to debug.
BAKER_VERSION = 2

MAGIC = b"ATLASPK1"
_HEADER_STRUCT = struct.Struct("<8sI32s")

#: A ring is a flat sequence of alternating lon/lat values, not a list of pairs.
#: Flat storage halves the object count, which matters at a few million coordinates.
Ring = Sequence[float]


class PackCorruptError(RuntimeError):
    """The pack failed its magic, digest or structural checks."""


@dataclass(frozen=True, slots=True)
class RegionEntry:
    geo_key: str
    name: str
    #: A point guaranteed to lie inside the shape, for placing labels and markers.
    label_point: tuple[float, float]
    bbox: tuple[float, float, float, float]
    #: ``(offset, length)`` into the body, one per level of detail.
    offsets: tuple[tuple[int, int], ...]


def encode_geometry(parts: Sequence[Sequence[Ring]]) -> bytes:
    """Serialise one region's geometry at one level of detail.

    ``parts`` is a list of polygons; each polygon is a list of rings, exterior first.
    """
    out = bytearray()
    out += struct.pack("<I", len(parts))
    for rings in parts:
        out += struct.pack("<I", len(rings))
        for ring in rings:
            if len(ring) % 2:
                raise ValueError("a ring must hold an even number of values (lon/lat pairs)")
            out += struct.pack("<I", len(ring) // 2)
            buffer = array("d", ring)
            # The format is little-endian by contract, so swap on a big-endian host.
            if struct.pack("<d", 1.0) != struct.pack("=d", 1.0):  # pragma: no cover
                buffer.byteswap()
            out += buffer.tobytes()
    return bytes(out)


def decode_geometry(raw: bytes | memoryview) -> list[list[array]]:
    """Inverse of :func:`encode_geometry`. Returns rings as ``array('d')`` of lon/lat."""
    parts: list[list[array]] = []
    cursor = 0
    size = len(raw)

    def count() -> int:
        nonlocal cursor
        if cursor + 4 > size:
            raise PackCorruptError(
                f"geometry block of {size} bytes ends mid-structure at byte {cursor}")
        (value,) = struct.unpack_from("<I", raw, cursor)
        cursor += 4
        return value

    n_parts = count()
    for _ in range(n_parts):
        n_rings = count()
        rings: list[array] = []
        for _ in range(n_rings):
            n_points = count()
            width = n_points * 2 * 8
            if cursor + width > size:
                raise PackCorruptError(
                    f"geometry block of {size} bytes claims a ring of {n_points} points "
                    f"at byte {cursor}")
            buffer = array("d")
            buffer.frombytes(raw[cursor : cursor + width])
            if struct.pack("<d", 1.0) != struct.pack("=d", 1.0):  # pragma: no cover
                buffer.byteswap()
            rings.append(buffer)
            cursor += width
        parts.append(rings)
    if cursor != size:
        raise PackCorruptError(
            f"geometry block of {size} bytes holds {size - cursor} byte(s) past its end")
    return parts


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _index_problem(header: object, body_size: int) -> str | None:
    """Why ``header`` cannot index a body of ``body_size`` bytes, or ``None`` if it can.

    **The digest covers the body and not the header**, so the header is the one part of
    a pack nothing else vouches for. A single digit changed in one offset used to open
    cleanly -- the body was intact -- and then fail on first draw, reading a length of
    billions out of whatever bytes the bad offset pointed at. Everything here is read
    from the parsed index alone, without touching the body, so it costs nothing on open.

    The strongest check is the last one. ``write_pack`` lays blocks end to end in the
    order it indexes them, so the ``(offset, length)`` pairs of an honest pack tile the
    body exactly: no gap, no overlap, nothing past the end. A wrong offset that still
    lands inside the body cannot also keep that tiling, and so is caught here rather
    than decoded as somebody else's geometry.
    """
    if not isinstance(header, dict):
        return "the header is not an object"
    for field_name in ("pack_id", "source"):
        if not isinstance(header.get(field_name), str):
            return f"the header has no {field_name}"
    lods = header.get("lods")
    if not isinstance(lods, list) or not lods or not all(_is_number(x) for x in lods):
        return "the header has no levels of detail"
    regions = header.get("regions")
    if not isinstance(regions, list):
        return "the header has no region index"

    blocks: list[tuple[int, int, str]] = []
    for ordinal, item in enumerate(regions):
        if not isinstance(item, dict):
            return f"region #{ordinal} is not an object"
        key = item.get("geo_key")
        if not isinstance(key, str) or not key:
            return f"region #{ordinal} has no geo_key"
        if not isinstance(item.get("name"), str):
            return f"{key} has no name"
        label = item.get("label_point")
        if not isinstance(label, list) or len(label) != 2 or not all(map(_is_number, label)):
            return f"{key} has a malformed label_point"
        bbox = item.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(map(_is_number, bbox)):
            return f"{key} has a malformed bbox"
        offsets = item.get("offsets")
        if not isinstance(offsets, list) or len(offsets) != len(lods):
            return f"{key} has {len(offsets) if isinstance(offsets, list) else 'no'} " \
                   f"offset(s) for {len(lods)} level(s) of detail"
        for pair in offsets:
            if (not isinstance(pair, list) or len(pair) != 2
                    or not _is_count(pair[0]) or not _is_count(pair[1])):
                return f"{key} has a malformed offset {pair!r}"
            offset, length = pair
            # Four bytes is the smallest block there is: a part count of zero.
            if length < 4 or offset + length > body_size:
                return (f"{key} points at bytes {offset}..{offset + length} of a "
                        f"{body_size}-byte body")
            blocks.append((offset, length, key))

    cursor = 0
    for offset, length, key in sorted(blocks):
        if offset != cursor:
            # Which entry is wrong cannot be told from here -- one bad offset displaces
            # its neighbours -- so this names where the tiling broke, not a culprit.
            kind = "overlap" if offset < cursor else "a gap"
            return (f"its blocks do not tile the body: {kind} at byte {cursor}, where the "
                    f"next block ({key}) starts at byte {offset}")
        cursor += length
    if cursor != body_size:
        return f"the index covers {cursor} of the body's {body_size} bytes"
    return None


def write_pack(
    destination: Path,
    *,
    pack_id: str,
    source: str,
    lods: Sequence[float],
    entries: Sequence[tuple[RegionEntry, Sequence[bytes]]],
    extra: dict | None = None,
) -> Path:
    """Write a pack. ``entries`` pairs each region with its encoded blocks, one per LOD.

    **The stamp is applied here rather than by the baker**, because this is the function
    that produces the artefact: anything written by this tree was written by this tree's
    geometry code, and a caller cannot forget to say so.
    """
    body = bytearray()
    index: list[dict] = []

    for entry, blocks in entries:
        if len(blocks) != len(lods):
            raise ValueError(f"{entry.geo_key} has {len(blocks)} blocks for {len(lods)} LODs")
        offsets: list[tuple[int, int]] = []
        for block in blocks:
            offsets.append((len(body), len(block)))
            body += block
        index.append(
            {
                "geo_key": entry.geo_key,
                "name": entry.name,
                "label_point": list(entry.label_point),
                "bbox": list(entry.bbox),
                "offsets": [list(o) for o in offsets],
            }
        )

    header = json.dumps(
        {
            "pack_id": pack_id,
            "source": source,
            "lods": list(lods),
            "regions": index,
            **(extra or {}),
            # Last, so no caller can overwrite it from `extra` by accident.
            "baked_by": BAKER_VERSION,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    digest = blake2b(bytes(body), digest_size=32).digest()

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_suffix(destination.suffix + ".tmp")
    with staging.open("wb") as handle:
        handle.write(_HEADER_STRUCT.pack(MAGIC, len(header), digest))
        handle.write(header)
        handle.write(body)
    staging.replace(destination)
    return destination


class GeometryPack:
    """Read-only, memory-mapped access to a baked pack.

    The digest is verified on open. A pack that has been truncated, partially synced or
    otherwise damaged fails here, loudly, rather than producing plausible-looking
    nonsense geometry halfway through a render.
    """

    __slots__ = ("_path", "_file", "_map", "_header", "_body_at", "_entries", "_by_key")

    def __init__(self, path: Path | str, *, verify: bool = True) -> None:
        self._path = Path(path)
        self._file: BinaryIO = self._path.open("rb")
        try:
            self._map = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError as exc:  # empty file
            self._file.close()
            raise PackCorruptError(f"{self._path} is empty") from exc

        # Slicing the mmap yields bytes rather than a view. That matters: a live
        # memoryview keeps an exported pointer, and closing the mmap underneath one
        # raises BufferError -- so every failure path below would die trying to clean up
        # instead of reporting the actual corruption. Header parsing happens once, so
        # the copy costs nothing.
        if self._map.size() < _HEADER_STRUCT.size:
            self.close()
            raise PackCorruptError(f"{self._path} is too small to be a pack")

        magic, header_len, digest = _HEADER_STRUCT.unpack(self._map[: _HEADER_STRUCT.size])
        if magic != MAGIC:
            self.close()
            raise PackCorruptError(f"{self._path} is not an ATLAS geometry pack")

        start = _HEADER_STRUCT.size
        if start + header_len > self._map.size():
            size = self._map.size()
            self.close()
            raise PackCorruptError(
                f"{self._path} declares a {header_len}-byte header in a {size}-byte file")
        try:
            self._header = json.loads(self._map[start : start + header_len])
        except (ValueError, UnicodeDecodeError) as exc:
            self.close()
            raise PackCorruptError(f"{self._path} has an unreadable header") from exc
        self._body_at = start + header_len

        # Before the digest, and whether or not it is asked for: the digest vouches only
        # for the body, and this is the header's only check.
        problem = _index_problem(self._header, self._map.size() - self._body_at)
        if problem is not None:
            self.close()
            raise PackCorruptError(
                f"{self._path} has a damaged index: {problem}. Re-run `atlas map bake`.")

        if verify:
            actual = blake2b(self._map[self._body_at :], digest_size=32).digest()
            if actual != digest:
                self.close()
                raise PackCorruptError(
                    f"{self._path} failed its integrity check; the file has been modified "
                    "or truncated since it was baked. Re-run `atlas map bake`."
                )

        self._entries = tuple(
            RegionEntry(
                geo_key=item["geo_key"],
                name=item["name"],
                label_point=tuple(item["label_point"]),  # type: ignore[arg-type]
                bbox=tuple(item["bbox"]),  # type: ignore[arg-type]
                offsets=tuple((o[0], o[1]) for o in item["offsets"]),
            )
            for item in self._header["regions"]
        )
        self._by_key = {entry.geo_key: entry for entry in self._entries}

    # -- metadata ----------------------------------------------------------

    @property
    def pack_id(self) -> str:
        return self._header["pack_id"]

    @property
    def baked_by(self) -> int:
        """Which baker made this pack. ``0`` for one made before the stamp existed, which
        is older than any version that carries one and so compares correctly."""
        try:
            return int(self._header.get("baked_by", 0))
        except (TypeError, ValueError):
            return 0

    @property
    def source(self) -> str:
        return self._header["source"]

    @property
    def lods(self) -> tuple[float, ...]:
        return tuple(self._header["lods"])

    @property
    def entries(self) -> tuple[RegionEntry, ...]:
        return self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, geo_key: str) -> bool:
        return geo_key in self._by_key

    def entry(self, geo_key: str) -> RegionEntry:
        return self._by_key[geo_key]

    def keys(self) -> Iterator[str]:
        return iter(self._by_key)

    # -- geometry ----------------------------------------------------------

    def geometry(self, geo_key: str, lod: int = 0) -> list[list[array]]:
        """Return polygons for ``geo_key`` as ``[[exterior, *holes], ...]``.

        Coordinates are flat ``array('d')`` runs of lon/lat.
        """
        entry = self._by_key.get(geo_key)
        if entry is None:
            raise KeyError(geo_key)
        if not 0 <= lod < len(entry.offsets):
            raise IndexError(f"lod {lod} out of range for {geo_key}")
        offset, length = entry.offsets[lod]
        start = self._body_at + offset
        # Also a bytes slice, for the same reason as in __init__: a leaked view would
        # make the pack impossible to close. Blocks are a few KB each, and the mmap
        # still means only the pages actually touched are ever read from disk.
        return decode_geometry(self._map[start : start + length])

    def close(self) -> None:
        for closeable in (getattr(self, "_map", None), getattr(self, "_file", None)):
            try:
                if closeable is not None:
                    closeable.close()
            except (ValueError, OSError):  # pragma: no cover
                pass

    def __enter__(self) -> GeometryPack:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class PackSet:
    """Several packs behind one reader, addressed by the source in a ``geo_key``.

    The decoupling that lets a world hold regions at more than one granularity. A
    ``geo_key`` already names its source -- ``admin0-50m:FRA``, ``admin1-10m:FRA-2653``
    -- so which pack answers a lookup is a property of the key rather than of the world,
    and a region simply references geometry instead of being one-to-one with a pack
    entry.

    That is what makes promotion possible: a nation held as one country-level region can
    be replaced by a dozen province-level ones, with no change to the pack, no second
    world, and no code between here and the renderer needing to know which kind it is
    looking at. The surface deliberately matches :class:`GeometryPack` so every consumer
    takes either.

    Packs are consulted in the order given, and a key found in more than one resolves to
    the first -- so listing the finer pack first makes it win, which is what promotion
    wants.
    """

    __slots__ = ("_packs", "_index")

    def __init__(self, packs: Sequence[GeometryPack]) -> None:
        if not packs:
            raise ValueError("a PackSet needs at least one pack")
        self._packs = tuple(packs)
        # geo_key -> pack. Built once: the alternative is a prefix parse on every
        # lookup, and a key whose source is unknown should fail as a plain KeyError
        # rather than as a string-splitting accident.
        self._index: dict[str, GeometryPack] = {}
        for pack in self._packs:
            for entry in pack.entries:
                self._index.setdefault(entry.geo_key, pack)

    @classmethod
    def open(cls, paths: Sequence[Path | str], *, verify: bool = True) -> PackSet:
        opened: list[GeometryPack] = []
        try:
            for path in paths:
                opened.append(GeometryPack(path, verify=verify))
        except Exception:
            for pack in opened:
                pack.close()
            raise
        return cls(opened)

    @property
    def packs(self) -> tuple[GeometryPack, ...]:
        return self._packs

    @property
    def pack_id(self) -> str:
        # Every pack that could answer a lookup, in resolution order. A scene is only
        # comparable to another built from the same geometry, and "admin1-10m" alone
        # would claim that a province world and a country world were drawn from the same
        # thing.
        return "+".join(pack.pack_id for pack in self._packs)

    @property
    def lods(self) -> tuple[float, ...]:
        # Packs are baked with the same LOD ladder; the first is representative.
        return self._packs[0].lods

    @property
    def entries(self) -> tuple[RegionEntry, ...]:
        return tuple(pack.entry(key) for key, pack in self._index.items())

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, geo_key: str) -> bool:
        return geo_key in self._index

    def entry(self, geo_key: str) -> RegionEntry:
        return self._index[geo_key].entry(geo_key)

    def keys(self) -> Iterator[str]:
        return iter(self._index)

    def geometry(self, geo_key: str, lod: int = 0) -> list[list[array]]:
        pack = self._index.get(geo_key)
        if pack is None:
            raise KeyError(geo_key)
        return pack.geometry(geo_key, lod)

    def close(self) -> None:
        for pack in self._packs:
            pack.close()

    def __enter__(self) -> PackSet:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
