"""Which geometry packs a release was verified against, and how to get them.

**"Eight of eight green" was never as portable as it was reported**, and this module exists
because that was said several times before anybody checked. The packs live outside the
repository -- in the user's application-data directory, baked out of band from Natural Earth
sources -- and nothing pinned which build of them a green audit had actually run against. So
one machine could pass every gate while another failed `packs-verify` on all three packs,
and both readings were true.

Two halves, and the second is the one that is usually left out:

**Detection.** A manifest records each pack's baker version, source and content hash.
:func:`compare` reports a pack that is missing, stale, or simply different from the one the
release was verified against. A hash tells you the artefact is not the one somebody signed
off; it does not tell you that anything is wrong with it, and the wording here is careful
about that difference.

**Obtaining.** A hash alone only ever says *no*. A check that can fail and cannot be
satisfied is a check people learn to skip, so every problem this raises carries the exact
command that fixes it, and :func:`repair_plan` assembles them in order for a machine that
has none of them. The packs are *derived* artefacts -- baking is deterministic from a named
Natural Earth source -- so the honest instruction is how to rebuild, not where to download.

**That determinism was a claim before it was true.** Every bake stamped a wall-clock time
into its header, so a re-bake never reproduced the pinned hash and the repair above could
not repair. It is true now: the bake is byte-for-byte a function of the pinned source
(:data:`atlas.map.sources.REVISION`) and the geometry stack that ran it. The lock records
both, so a pack that still differs after a re-bake can say which of the two moved rather
than leaving a reviewer to diff binaries.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

__all__ = [
    "MANIFEST_PATH",
    "ManifestError",
    "PackRecord",
    "compare",
    "digest_of",
    "load",
    "load_provenance",
    "provenance",
    "record_current",
    "repair_plan",
    "write",
]

#: In the repository, next to the code that reads it, because the whole point is that it
#: travels with the release rather than with the machine.
MANIFEST_PATH = Path(__file__).resolve().parents[3] / "packs.lock.json"

#: How much of a pack is hashed. Whole file: a pack is tens of megabytes, hashing it takes
#: well under a second, and a prefix hash would miss exactly the corruption that matters --
#: a truncated download is identical at the front.
_CHUNK = 1 << 20


class ManifestError(ValueError):
    """``packs.lock.json`` exists but cannot be read as a manifest."""


def digest_of(path: Path) -> str:
    """The content hash of one pack, or ``""`` when it is not there."""
    if not path.exists():
        return ""
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            sha.update(chunk)
    return sha.hexdigest()


#: The libraries whose output a bake's bytes depend on. Recorded, not enforced: a different
#: version is often harmless, and when it is not, this is what explains the mismatch.
_TOOLCHAIN = ("shapely", "topojson", "numpy")


def provenance() -> dict[str, str]:
    """What this machine bakes from: the Natural Earth revision and the geometry stack.

    Read from package metadata rather than by importing, so it costs nothing and works on
    a runtime without the bake extras -- where each library simply reads as absent.
    """
    from .sources import REVISION

    out = {"natural_earth": REVISION}
    for name in _TOOLCHAIN:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "absent"
    return out


def load_provenance(path: Path | None = None) -> dict[str, str]:
    """What the recorded manifest was baked from. Empty for a manifest older than this."""
    where = path or MANIFEST_PATH
    try:
        raw = json.loads(where.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = raw.get("baked_with") if isinstance(raw, dict) else None
    return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}


def _what_moved(pinned: dict[str, str], here: dict[str, str]) -> str:
    """The provenance entries on which the pin and this machine disagree, readably."""
    moved = [f"{key} {pinned[key]} (this machine: {here.get(key, 'absent')})"
             for key in sorted(pinned) if pinned[key] != here.get(key, "absent")]
    return "; pinned with " + ", ".join(moved) if moved else ""


@dataclass(frozen=True, slots=True)
class PackRecord:
    """One pack, as the release was verified against it."""

    name: str
    baked_by: int
    source: str
    sha256: str
    bytes: int

    @property
    def rebake(self) -> str:
        """The command that produces exactly this pack."""
        return f"atlas map bake --source {self.source}"


@dataclass(frozen=True, slots=True)
class Problem:
    """One pack that is not what the release was verified against."""

    name: str
    kind: str
    detail: str
    fix: str


def load(path: Path | None = None) -> dict[str, PackRecord]:
    """The recorded manifest, keyed by pack name. Empty when there is none.

    **A lock that exists and cannot be read raises** :class:`ManifestError`. It used to
    load as empty, and empty means "nothing is pinned", which turned the pack gate green on
    a corrupt file -- the one case where it most needs to be red.
    """
    where = path or MANIFEST_PATH
    if not where.exists():
        return {}
    try:
        raw = json.loads(where.read_text(encoding="utf-8"))
        return {
            str(entry["name"]): PackRecord(
                name=str(entry["name"]), baked_by=int(entry.get("baked_by", 0)),
                source=str(entry.get("source", "")), sha256=str(entry.get("sha256", "")),
                bytes=int(entry.get("bytes", 0)))
            for entry in raw["packs"]
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ManifestError(f"{where} could not be read as a pack manifest: {exc}") from exc


def record_current(paths: list[Path]) -> list[PackRecord]:
    """Read the packs on this machine into records fit to be written down."""
    from .pack import GeometryPack

    out: list[PackRecord] = []
    for path in sorted(paths):
        if not path.exists():
            continue
        try:
            with GeometryPack(path) as pack:
                baked_by = int(pack.baked_by)
        except Exception:  # noqa: BLE001 - an unreadable pack is recorded as version 0
            baked_by = 0
        out.append(PackRecord(
            name=path.name, baked_by=baked_by, source=path.stem,
            sha256=digest_of(path), bytes=path.stat().st_size))
    return out


def write(records: list[PackRecord], path: Path | None = None,
          *, baked_with: dict[str, str] | None = None) -> Path:
    """Write the manifest. Sorted, so a re-record produces no spurious diff.

    ``baked_with`` defaults to this machine's :func:`provenance`, which is right for `atlas
    map pin`: the packs being pinned were baked here.
    """
    where = path or MANIFEST_PATH
    where.write_text(json.dumps({
        "$comment": (
            "Which geometry packs this release was verified against. Packs are derived "
            "artefacts baked from Natural Earth sources and live outside the repository, "
            "so without this nothing pinned which build a green audit actually ran on. "
            "Regenerate with `atlas map pin`."),
        "baked_with": dict(sorted((baked_with or provenance()).items())),
        "packs": [
            {"name": r.name, "baked_by": r.baked_by, "source": r.source,
             "sha256": r.sha256, "bytes": r.bytes}
            for r in sorted(records, key=lambda r: r.name)
        ],
    }, indent=2) + "\n", encoding="utf-8")
    return where


def compare(paths: list[Path], recorded: dict[str, PackRecord] | None = None
            ) -> list[Problem]:
    """What is missing or different, with the command that fixes each.

    **A pack that differs is reported as differing, not as broken.** It may be a perfectly
    good pack from a newer source; what is certain is only that it is not the one the
    release was verified against, and saying more than that would be a claim this function
    cannot support.
    """
    from .pack import BAKER_VERSION, GeometryPack

    if recorded is None:
        try:
            known = load()
        except ManifestError as exc:
            return [Problem(
                MANIFEST_PATH.name, "unreadable-lock", str(exc),
                f"git checkout -- {MANIFEST_PATH.name}")]
    else:
        known = recorded
    if not known:
        return []
    # Only consulted when a pack differs, and only with the real manifest: a caller handing
    # in its own records has no provenance to compare against.
    pinned_with = load_provenance() if recorded is None else {}

    problems: list[Problem] = []
    for path in sorted(paths):
        want = known.get(path.name)
        if want is None:
            continue
        if not path.exists():
            problems.append(Problem(
                path.name, "missing", f"{path} is not on this machine", want.rebake))
            continue
        try:
            with GeometryPack(path) as pack:
                baked_by = int(pack.baked_by)
        except Exception as exc:  # noqa: BLE001
            problems.append(Problem(
                path.name, "unreadable", f"could not be opened: {exc}", want.rebake))
            continue
        # Staleness first, because it is the specific failure that started this and it has
        # a clearer message than "the bytes differ".
        if baked_by < BAKER_VERSION:
            problems.append(Problem(
                path.name, "stale",
                f"baked by version {baked_by}, this build bakes {BAKER_VERSION}",
                want.rebake))
            continue
        found = digest_of(path)
        if found != want.sha256:
            problems.append(Problem(
                path.name, "different",
                f"sha256 {found[:12]} where the manifest records {want.sha256[:12]}"
                + _what_moved(pinned_with, provenance()),
                want.rebake))
    return problems


def repair_plan(problems: list[Problem]) -> list[str]:
    """Every command needed to put this machine right, in order and without repeats."""
    seen: set[str] = set()
    plan: list[str] = []
    for problem in problems:
        if problem.fix not in seen:
            seen.add(problem.fix)
            plan.append(problem.fix)
    return plan
