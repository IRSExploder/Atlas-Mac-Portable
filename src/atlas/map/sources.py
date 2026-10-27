"""Where map geometry comes from, and the on-disk cache for it.

Natural Earth is public domain. The GeoJSON builds in the ``natural-earth-vector``
repository are used rather than the shapefile distributions, so nothing has to read DBF
or handle projection metadata -- the data arrives in lon/lat as plain JSON.

Downloads are cached under ``%LOCALAPPDATA%\\Atlas\\cache``. Baking is an offline,
one-time step: the game never touches the network at runtime.

**Every source is read at one pinned commit, and checked against its digest.** The URLs
used to point at ``master``, so "baking is deterministic from a named Natural Earth source"
was only true until upstream next pushed: two machines baking a week apart could be handed
different GeoJSON under the same name, and ``packs.lock.json`` would report the packs as
different with nothing to say why. A bake is now a function of :data:`REVISION` and the
baker, and a download that is not the file the pin names is refused before it is baked
rather than discovered afterwards as a hash that will not match.

Moving to newer Natural Earth data is a deliberate act: change :data:`REVISION`, update
each ``sha256``, re-bake and re-pin.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..config.paths import app_data_root

__all__ = [
    "REVISION",
    "SOURCES",
    "NaturalEarthSource",
    "SourceMismatchError",
    "cache_root",
    "fetch",
    "sha256_of",
]

#: The ``natural-earth-vector`` commit every source is read from. See the module doc.
REVISION = "ca96624a56bd078437bca8184e78163e5039ad19"

_BASE = f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{REVISION}/geojson/"


class SourceMismatchError(RuntimeError):
    """A downloaded or cached source is not the file :data:`REVISION` pins."""


@dataclass(frozen=True, slots=True)
class NaturalEarthSource:
    id: str
    filename: str
    description: str
    #: Property holding a stable unique code, used to build ``geo_key``.
    key_property: str
    #: Property holding the human-readable name.
    name_property: str
    #: Roughly how many regions this yields, for choosing sensibly.
    approx_regions: int
    #: Drop features whose ``scalerank`` is above this. Natural Earth ranks a feature by
    #: the smallest scale it is worth drawing at, so this is the dataset's own answer to
    #: "which of these matter". The marine layer needs it: 306 bays, lagoons and fjords
    #: make a naval map nobody can read and a turn nobody can afford, while the seas a
    #: war is fought across all rank near the top.
    max_scalerank: int | None = None
    #: Keep only these ``featurecla`` values. Natural Earth's marine layer mixes water a
    #: fleet sails with water it does not: the Amazon River and the Lagoa dos Patos are in
    #: there beside the North Sea, and a naval map should not offer to base a battleship
    #: in a lagoon.
    keep_featurecla: tuple[str, ...] | None = None
    #: The GeoJSON's sha256 at :data:`REVISION`. ``None`` leaves a source unchecked, which
    #: is for tests and experiments; every shipped source carries one.
    sha256: str | None = None

    @property
    def url(self) -> str:
        return _BASE + self.filename


SOURCES: dict[str, NaturalEarthSource] = {
    "admin0-110m": NaturalEarthSource(
        id="admin0-110m",
        filename="ne_110m_admin_0_countries.geojson",
        sha256="6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f",
        description="Countries, coarse. Fast to bake; good for a whole-world overview.",
        key_property="ADM0_A3",
        name_property="ADMIN",
        approx_regions=177,
    ),
    "admin0-50m": NaturalEarthSource(
        id="admin0-50m",
        filename="ne_50m_admin_0_countries.geojson",
        sha256="3e458fc036ad0a66411f2c1e6cac49c5d7bfb81cb1123bc513b22511a2b7fdeb",
        description="Countries, medium detail. The sensible default for a nation-level game.",
        key_property="ADM0_A3",
        name_property="ADMIN",
        approx_regions=242,
    ),
    "admin1-10m": NaturalEarthSource(
        id="admin1-10m",
        filename="ne_10m_admin_1_states_provinces.geojson",
        sha256="22d0e3ad85eb3e27f17cabf8ba2d50e554fbc27a87796ff891d958185da62fb5",
        description="States and provinces, fine detail. ~4600 regions; a much heavier bake.",
        key_property="adm1_code",
        name_property="name",
        approx_regions=4600,
    ),
    "marine-10m": NaturalEarthSource(
        id="marine-10m",
        filename="ne_10m_geography_marine_polys.geojson",
        sha256="53f865e8ffa966cdd402145c82c5cd14ee7ce974cd0eb9a3f59f03a4cfd2d66c",
        description="Seas, straits and channels. The water a fleet sails and fights over.",
        key_property="ne_id",
        name_property="name",
        approx_regions=100,
        # The seas a 1936 war is fought across -- North Sea, Baltic, Channel, Skagerrak,
        # Biscay, Mediterranean, Tyrrhenian, Adriatic, Aegean, Black Sea -- all rank 4 or
        # better. Past that the layer is bays and fjords.
        max_scalerank=4,
        keep_featurecla=("ocean", "sea", "gulf", "strait", "channel", "sound", "bay"),
    ),
}


def cache_root() -> Path:
    return app_data_root() / "cache"


def sha256_of(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            sha.update(chunk)
    return sha.hexdigest()


def _check(source: NaturalEarthSource, path: Path, *, cached: bool) -> None:
    if source.sha256 is None:
        return
    found = sha256_of(path)
    if found == source.sha256:
        return
    fix = (f"re-download it: atlas map bake --source {source.id} --refresh" if cached
           else "upstream no longer serves the pinned file; nothing was baked")
    raise SourceMismatchError(
        f"{path} has sha256 {found[:12]} where natural-earth-vector@{REVISION[:12]} "
        f"pins {source.sha256[:12]}; {fix}")


def fetch(source: NaturalEarthSource, *, refresh: bool = False) -> Path:
    """Download ``source`` if it is not already cached, and return the local path.

    The cache is keyed by :data:`REVISION`, so a file fetched under an older pin -- or
    from ``master``, before there was one -- is never mistaken for the pinned one. Either
    way the file is checked against the source's digest before it is returned.

    ``requests`` is imported lazily so that the runtime, which never fetches anything,
    does not need the bake extras installed.
    """
    target = cache_root() / REVISION[:12] / source.filename
    if target.exists() and not refresh:
        _check(source, target, cached=True)
        return target

    import requests

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + ".part")

    with requests.get(source.url, timeout=180, stream=True) as response:
        response.raise_for_status()
        with staging.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                handle.write(chunk)

    try:
        _check(source, staging, cached=False)
    except SourceMismatchError:
        staging.unlink(missing_ok=True)
        raise
    staging.replace(target)
    return target
