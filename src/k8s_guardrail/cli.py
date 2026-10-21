from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from k8s_guardrail.validator import ManifestVerificationEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="k8s-guardrail", description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Manifest files, or '-' for stdin")
    parser.add_argument("--no-security-context", action="store_true",
                        help="Skip securityContext checks")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    engine = ManifestVerificationEngine(enforce_security_context=not args.no_security_context)
    failed = False
    reports = {}
    for path in args.files:
        text = sys.stdin.read() if str(path) == "-" else path.read_text()
        result = engine.verify_string(text)
        failed |= not result.is_valid
        reports[str(path)] = result

    if args.format == "json":
        print(json.dumps({p: r.to_dict() for p, r in reports.items()}, indent=2))
    else:
        for p, r in reports.items():
            print(f"{p}: {'OK' if r.is_valid else 'FAIL'} ({len(r.issues)} issue(s))")
            for i in r.issues:
                print(f"  [{i.severity.value}] {i.rule} {i.resource}: {i.message}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
