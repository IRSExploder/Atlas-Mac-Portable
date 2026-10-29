"""Record a tape: play a profile from `tape_world.py` against a real model, and keep every
call it made and the world's fingerprint after every turn.

    python scratchpad/tape_record.py pt80-modern-day api-modern-tur [--provider gemini]

Needs the chosen provider's key in the environment (`ATLAS_GEMINI_API_KEY` or
`ATLAS_ANTHROPIC_API_KEY`), and it spends quota: one run is a few dozen calls. Writes
`cassettes/<tag>.jsonl` and `cassettes/<tag>.fingerprints.json`, then prints the INDEX
entry to add. **It never marks a tape active by itself** -- that is a decision about what
gates a release, and somebody should read the run first.

``--mock`` records against the mock provider instead. That proves the harness round-trips
(record, then replay to the same fingerprints) without a key; a mock tape must never be
marked active, because it gates nothing a real model would do.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tape_world  # noqa: E402

CASSETTES = Path(__file__).resolve().parent / "cassettes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag")
    parser.add_argument("profile", choices=sorted(tape_world.PROFILES))
    parser.add_argument("--provider", choices=("claude", "gemini"), default=None)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--out", type=Path, default=CASSETTES)
    args = parser.parse_args()

    from atlas.providers.api_choice import api_provider, chosen
    from atlas.providers.cassette import RecordingProvider
    from atlas.providers.mock import MockProvider

    profile = tape_world.PROFILES[args.profile]
    choice = args.provider or chosen()
    inner = MockProvider() if args.mock else api_provider(choice)
    if not args.mock and not inner.available():
        print(f"no {choice} key in the environment; nothing recorded")
        return 2
    tape = args.out / f"{args.tag}.jsonl"
    if tape.exists():
        print(f"{tape} exists; choose a new tag rather than appending to a recording")
        return 2

    tape_world.seat(choice)
    conn = tape_world.build(profile)
    started = time.monotonic()
    records = tape_world.run(tape_world.play(
        conn, profile, RecordingProvider(inner, tape),
        on_turn=lambda r: print(f"turn {r['turn']}: {r['status']} {r['fingerprint'][:12]}")))
    calls = sum(1 for line in tape.read_text(encoding="utf-8").splitlines() if line.strip())

    meta = {"profile": args.profile, "preset": profile.preset, "provider": choice,
            "mock": args.mock, "turns": profile.turns, "days": profile.days,
            "calls": calls, "seconds": round(time.monotonic() - started),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "attempts": records}
    (args.out / f"{args.tag}.fingerprints.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {calls} call(s) over {profile.turns} turns in {meta['seconds']}s")
    print("INDEX entry, once the run has been read:")
    print(json.dumps({"tag": args.tag, "status": "active", "preset": profile.preset,
                      "profile": args.profile, "why": "..."}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
