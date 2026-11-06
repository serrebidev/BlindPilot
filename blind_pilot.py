"""Stable BlindPilot application entry point.

Based on the original Claude Code Reader application by doubletaponair
(https://github.com/doubletaponair/claude-code-reader) and distributed under
the MIT License. A compatibility module keeps older imports and integrations
working after the implementation was renamed for BlindPilot.
"""

import sys


def _main() -> int:
    if "--print-plan-token" in sys.argv:
        from accessible_ai.plan_token import main as plan_token_main

        return plan_token_main(sys.argv[1:])

    from blindpilot_app import main

    return main()


if __name__ == "__main__":
    sys.exit(_main())
