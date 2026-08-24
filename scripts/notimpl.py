"""Fail loudly for a Makefile target whose phase has not been handed over yet.

Phase 0 declares the whole task interface so the Makefile is reviewable up front,
but an unimplemented target must not exit 0 and look like a no-op success.
"""

import sys


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write("usage: notimpl.py <phase-number> <target-name>\n")
        return 64
    phase, target = sys.argv[1], sys.argv[2]
    sys.stderr.write(f"{target}: not implemented until phase {phase}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
