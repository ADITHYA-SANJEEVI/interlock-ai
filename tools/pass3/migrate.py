"""Upgrade all three domains twice; bootstrap existing owner-only private profiles."""

import os
import subprocess
import sys

from tools.pass3.bootstrap import main

if __name__ == "__main__":
    for domain in ("control", "seller", "reference"):
        for _ in range(2):
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                env={**os.environ, "DB_DOMAIN": domain},
                check=True,
            )
    main()
