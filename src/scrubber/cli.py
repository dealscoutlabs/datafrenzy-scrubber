"""Command line: `python -m scrubber run|status|review|check-network`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__, netguard
from .config import load_config


def _paths(args):
    from .pipeline import Paths
    return Paths(Path(args.input), Path(args.output), Path(args.work), Path(args.state))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scrubber", description="Data Frenzy Local Scrubber")
    ap.add_argument("--input", default=os.environ.get("SCRUBBER_INPUT", "/input"))
    ap.add_argument("--output", default=os.environ.get("SCRUBBER_OUTPUT", "/output"))
    ap.add_argument("--work", default=os.environ.get("SCRUBBER_WORK", "/work"))
    ap.add_argument("--state", default=os.environ.get("SCRUBBER_STATE", "/state"))
    ap.add_argument("--config", default=os.environ.get("SCRUBBER_CONFIG", "/config/scrubber.yaml"))
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="scrub everything in the input folder")
    sub.add_parser("status", help="show progress and counts")
    rv = sub.add_parser("review", help="list, approve or reject files waiting for review")
    rv.add_argument("action", choices=["list", "approve", "reject"])
    rv.add_argument("file", nargs="?", help="input path of the file (as shown by 'review list')")
    rv.add_argument("--by", default="reviewer")
    rv.add_argument("--note", default="")
    sub.add_parser("check-network", help="confirm the container has no network")
    args = ap.parse_args(argv)
    cmd = args.cmd or "run"

    netguard.install()
    if cmd == "check-network":
        ifaces = netguard.network_interfaces()
        print(f"Network interfaces visible: {ifaces}")
        netguard.assert_isolated()
        print("OK: no network. Nothing can leave this machine from the scrubber.")
        return 0
    netguard.assert_isolated()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for noisy in ("presidio-analyzer", "presidio-anonymizer", "tldextract"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    from .pipeline import Runner
    cfg = load_config(args.config)
    runner = Runner(_paths(args), cfg, workers=args.workers)
    if cmd == "run":
        runner.run()
    elif cmd == "status":
        print(json.dumps(runner.summary(), indent=2))
    elif cmd == "review":
        if args.action == "list":
            for r in runner.store.reviews("pending"):
                f = runner.store.file(r["rel"])
                print(f"{r['rel']}\n    output: {f['out_rel']}")
                for reason in json.loads(r["reasons"] or "[]")[:10]:
                    print(f"    - {reason}")
        elif not args.file:
            ap.error("review approve/reject needs a file")
        elif args.action == "approve":
            runner.approve(args.file, args.by, args.note)
            runner.write_summary()
            print(f"Approved and exported: {args.file}")
        else:
            runner.reject(args.file, args.by, args.note)
            runner.write_summary()
            print(f"Rejected (not exported): {args.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
