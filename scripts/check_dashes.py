"""Fails if any tracked text file contains an em dash. The project style avoids them."""

import subprocess
import sys

DASH = chr(0x2014)

files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
found = []
for name in files:
    try:
        with open(name, encoding="utf-8") as fh:
            text = fh.read()
    except (UnicodeDecodeError, OSError):
        continue
    for number, line in enumerate(text.splitlines(), 1):
        if DASH in line:
            found.append(f"{name}:{number}: {line.strip()}")
print("\n".join(found) or "No em dashes.")
sys.exit(1 if found else 0)
