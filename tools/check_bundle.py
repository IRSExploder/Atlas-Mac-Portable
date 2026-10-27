"""Check an exported Atlas file bundle (zip of `NNN_<name>` files + MANIFEST.md).

Usage:  python3 -I tools/check_bundle.py <bundle.zip> [--missing-out missing.txt]

What it does, in order:
  1. Tests the zip's own CRCs.
  2. Compares every file against MANIFEST.md (present? same size?).
  3. Validates each file by type: PNG chunk CRCs + IDAT inflate, Python parses, JSON/TOML
     load, CSV is rectangular, git loose objects hash back to their own name.
  4. Finds the git index(es) in the bundle (`NNN_index`) and matches every file - and every
     blob inside a loose git object - against it by git blob hash, so you learn exactly
     which tracked repo files the bundle actually carries and which are missing.

It never executes anything from the bundle.
"""

import argparse
import ast
import collections
import csv
import hashlib
import io
import json
import os
import re
import struct
import sys
import tempfile
import tomllib
import zipfile
import zlib

PREFIX = re.compile(r"^\d{3}_")
LOOSE = re.compile(r"^\d{3}_[0-9a-f]{38}$")


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def parse_index(data: bytes):
    """Yield (path, blob_sha, size) from a git index v2/v3. Returns (entries, checksum_ok)."""
    assert data[:4] == b"DIRC", "not a git index"
    ver, n = struct.unpack(">II", data[4:12])
    off, out = 12, []
    for _ in range(n):
        start = off
        size = struct.unpack(">I", data[off + 36 : off + 40])[0]
        sha = data[off + 40 : off + 60].hex()
        flags = struct.unpack(">H", data[off + 60 : off + 62])[0]
        off += 62
        if ver >= 3 and flags & 0x4000:
            off += 2
        end = data.index(b"\0", off)
        out.append((data[off:end].decode(), sha, size))
        off = end + 1
        if ver < 4:
            while (off - start) % 8:
                off += 1
    return out, hashlib.sha1(data[:-20]).digest() == data[-20:]


def check_file(name: str, b: bytes):
    """Raise on a structurally broken file; return None if it looks healthy."""
    n = PREFIX.sub("", name)
    if n.endswith(".png"):
        assert b[:8] == b"\x89PNG\r\n\x1a\n", "bad PNG signature"
        o, idat, ended = 8, b"", False
        while o < len(b):
            (ln,) = struct.unpack(">I", b[o : o + 4])
            t, d = b[o + 4 : o + 8], b[o + 8 : o + 8 + ln]
            (crc,) = struct.unpack(">I", b[o + 8 + ln : o + 12 + ln])
            assert zlib.crc32(t + d) & 0xFFFFFFFF == crc, f"CRC mismatch in {t!r}"
            idat += d if t == b"IDAT" else b""
            o += 12 + ln
            if t == b"IEND":
                ended = True
                break
        assert ended, "no IEND chunk (truncated)"
        zlib.decompress(idat)
    elif n.endswith(".py"):
        ast.parse(b.decode("utf-8"))
    elif n.endswith(".json"):
        json.loads(b)
    elif n.endswith(".toml"):
        tomllib.loads(b.decode())
    elif n.endswith(".csv"):
        rows = [r for r in csv.reader(io.StringIO(b.decode("utf-8-sig"))) if r]
        assert len({len(r) for r in rows}) <= 1, "ragged CSV rows"
    elif n.endswith((".md", ".qml", ".txt")):
        b.decode("utf-8")
    elif LOOSE.match(name):
        raw = zlib.decompress(b)
        hdr = raw[: raw.index(b"\0")].decode()
        assert int(hdr.split()[1]) == len(raw) - len(hdr) - 1, "object length mismatch"
        assert hashlib.sha1(raw).hexdigest().endswith(n), "object hash mismatch"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle")
    ap.add_argument("--missing-out")
    args = ap.parse_args()

    with zipfile.ZipFile(args.bundle) as z:
        bad_member = z.testzip()
        print(f"zip CRC test: {'OK' if bad_member is None else 'FAILED at ' + bad_member}")
        files = {
            os.path.basename(i.filename): z.read(i)
            for i in z.infolist()
            if not i.is_dir()
        }

    # 2. manifest
    manifest = files.get("MANIFEST.md", b"").decode()
    rows = [r for r in manifest.splitlines() if re.match(r"^\| \d{3} ", r)]
    m_missing, m_size = [], []
    for r in rows:
        c = [x.strip() for x in r.strip().strip("|").split("|")]
        fn, exp = c[1], int(c[4].split()[0])
        if fn not in files:
            m_missing.append(fn)
        elif len(files[fn]) != exp:
            m_size.append((fn, exp, len(files[fn])))
    print(f"manifest: {len(rows)} rows, {len(m_missing)} missing, {len(m_size)} size mismatches")
    for x in m_missing + m_size:
        print("   ", x)

    # 3. per-file
    issues = []
    for name, b in sorted(files.items()):
        try:
            check_file(name, b)
        except Exception as e:  # noqa: BLE001 - report everything
            issues.append((name, repr(e)))
        if not b:
            issues.append((name, "empty file"))
    print(f"per-file checks: {len(files) - len(issues)}/{len(files)} clean")
    for x in issues:
        print("   ", x)

    # 4. match against git index
    present = collections.defaultdict(list)
    for name, b in files.items():
        present[blob_sha(b)].append(name)
        if b"\r\n" in b:
            present[blob_sha(b.replace(b"\r\n", b"\n"))].append(name)
        if LOOSE.match(name):
            raw = zlib.decompress(b)
            if raw.startswith(b"blob "):
                present[hashlib.sha1(raw).hexdigest()].append(name + " (loose object)")
    indexes = sorted(n for n in files if PREFIX.sub("", n) == "index")
    if not indexes:
        print("no git index in bundle: cannot tell which repo files are missing")
        return 0
    best = None
    for ix in indexes:
        entries, ok = parse_index(files[ix])
        have = [e for e in entries if e[1] in present]
        print(f"\ngit index {ix}: {len(entries)} tracked files, checksum {'OK' if ok else 'BAD'}, "
              f"{len(have)} present in bundle")
        tot = collections.Counter(e[0].split("/")[0] if "/" in e[0] else "(root)" for e in entries)
        got = collections.Counter(e[0].split("/")[0] if "/" in e[0] else "(root)" for e in have)
        for top, cnt in tot.most_common():
            print(f"    {top:22s} {got[top]:5d} / {cnt}")
        if best is None or len(entries) > len(best):
            best = entries
    src = [e for e in best if e[0].startswith("src/")]
    src_have = [e for e in src if e[1] in present]
    print(f"\nsource code: {len(src_have)}/{len(src)} files, "
          f"{sum(e[2] for e in src_have)}/{sum(e[2] for e in src)} bytes present")
    if args.missing_out:
        with open(args.missing_out, "w") as fh:
            for path, sha, size in best:
                if sha not in present:
                    fh.write(f"{path}\t{size}\n")
        print(f"missing-file list written to {args.missing_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
