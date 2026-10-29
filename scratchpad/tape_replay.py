"""Replay a recorded tape offline and check HEAD still asks the same questions and builds
the same world from the answers.

    python scratchpad/tape_replay.py <tag>

What `atlas release-audit` runs for every active tape (`atlas.testing.release`). One line
per turn -- ``match``, ``MISMATCH`` (same questions, different world) or ``MISS`` (HEAD asked
something the tape has no answer for) -- and a summary. Exits non-zero on anything but a
clean replay.

**Rebuilt.** The original was lost with the batch in `docs/MISSING_FILES_2026-10-27.tsv`,
together with the profile that recorded the tapes it replayed; this one replays tapes made
by `tape_record.py`, from the profiles in `tape_world.py`. A tape whose profile is not
there was recorded by a harness that no longer exists, and is reported as such rather than
replayed against a different world.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tape_world  # noqa: E402

CASSETTES = Path(__file__).resolve().parent / "cassettes"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: tape_replay.py <tag>")
        return 2
    tag = argv[0]
    tape = CASSETTES / f"{tag}.jsonl"
    marks = CASSETTES / f"{tag}.fingerprints.json"
    if not tape.exists() or not marks.exists():
        print(f"{tag}: MISS -- no tape or no fingerprints on disk")
        return 1
    meta = json.loads(marks.read_text(encoding="utf-8"))
    profile = tape_world.PROFILES.get(str(meta.get("profile", "")))
    if profile is None or "attempts" not in meta or "provider" not in meta:
        print(f"{tag}: MISS -- recorded by a harness this tree does not have "
              f"(profile {meta.get('profile')!r}); re-record it with tape_record.py")
        return 1

    from atlas.providers.cassette import CassetteMiss, ReplayProvider

    tape_world.seat(str(meta["provider"]))
    conn = tape_world.build(profile)
    replay = ReplayProvider.from_file(tape)
    expected = {int(a["turn"]): str(a["fingerprint"]) for a in meta["attempts"]}
    bad: list[str] = []

    def check(record: dict) -> None:
        turn = int(record["turn"])
        want = expected.get(turn)
        if want == record["fingerprint"]:
            print(f"turn {turn}: match {want[:12]}")
        else:
            line = (f"turn {turn}: MISMATCH -- recorded {str(want)[:12]}, "
                    f"replayed {record['fingerprint'][:12]}")
            print(line)
            bad.append(line)

    try:
        tape_world.run(tape_world.play(conn, profile, replay, on_turn=check))
    except CassetteMiss as miss:
        print(f"{tag}: MISS -- {str(miss).splitlines()[0][:300]}")
        return 1
    left = replay.remaining()
    print(f"{tag}: {len(expected) - len(bad)} matched, {len(bad)} mismatched, 0 missed; "
          f"{left} recorded call(s) unused")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
