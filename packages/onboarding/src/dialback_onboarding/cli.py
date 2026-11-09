"""`dialback-onboarding sweep`: spec 0041's read-only sweep, in shadow mode. Exits non-zero, with no
report written, on any read error, an unreadable sources file, or a number outside 555-0100 to 0199."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from dialback_onboarding.anchor import Settings
from dialback_onboarding.ramp import RampError, mint_token, reader
from dialback_onboarding.sweep import RefusedNumber, load_sources, parse_time, sweep, write_report


def _time(raw: str) -> dt.datetime:
    try:
        return parse_time(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {raw!r}") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dialback-onboarding", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("sweep", help="decide new vendors' anchors and write a JSON report")
    s.add_argument("--dataset", required=True, type=uuid.UUID, help="the dataset whose vendors to read")
    s.add_argument("--since", required=True, type=_time, help="keep vendors created at or after this time")
    s.add_argument("--out", required=True, type=Path, help="where to write the report")
    s.add_argument("--sources", required=True, type=Path,
                   help="stand-in registry and website facts, e.g. packages/onboarding/fixtures/sources.json")
    s.add_argument("--ramp-url", help="the Ramp mock's URL (default: $RAMP_BASE_URL)")
    s.add_argument("--as-of", type=_time, help="the time domain ages are measured at (default: now)")
    return parser


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if env is None else env
    base_url, secret = args.ramp_url or env.get("RAMP_BASE_URL"), env.get("MOCK_RAMP_SECRET")
    if not base_url or not secret:
        print("dialback-onboarding: needs --ramp-url (or RAMP_BASE_URL) and MOCK_RAMP_SECRET", file=sys.stderr)
        return 1
    settings = Settings(as_of=args.as_of or dt.datetime.now(dt.UTC))
    try:
        sources = load_sources(args.sources)
        get = reader(base_url, mint_token(secret.encode(), args.dataset))
        report = sweep(get, dataset_id=args.dataset, since=args.since, sources=sources, settings=settings)
        write_report(report, args.out)
    except (RampError, RefusedNumber, ValueError, OSError) as exc:
        print(f"dialback-onboarding: {exc} (no report written)", file=sys.stderr)
        return 1
    counts = report["counts"]
    print(f"wrote {args.out}: {counts['vendors']} new vendor(s), {counts['anchored']} anchored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
