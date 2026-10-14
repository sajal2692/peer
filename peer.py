#!/usr/bin/env python3
"""Runs peer from this checkout. install.sh links this file onto PATH as `peer`.

The code lives in the peer package under src/; see src/peer/__init__.py.
"""

import sys
from pathlib import Path

if __name__ == "__main__":
    # resolve() follows the symlink on PATH back to the checkout.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
    from peer.cli import main

    sys.exit(main())
