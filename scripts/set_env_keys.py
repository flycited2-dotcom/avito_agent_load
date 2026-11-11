"""Safely merge selected secrets from JSON stdin into a dotenv file."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ALLOWED = {"AVITO_CLIENT_ID", "AVITO_CLIENT_SECRET"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    values = json.load(sys.stdin)
    if set(values) != ALLOWED:
        raise ValueError("Expected exactly the main Avito credential keys")
    if any(not isinstance(value, str) or not value or "\n" in value or "\r" in value
           for value in values.values()):
        raise ValueError("Invalid credential value")

    path = args.path
    old = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    kept = [
        line for line in old
        if not ("=" in line and line.split("=", 1)[0].strip() in ALLOWED)
    ]
    text = "\n".join(kept + [f"{key}={values[key]}" for key in sorted(ALLOWED)]) + "\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    print("updated=" + ",".join(sorted(ALLOWED)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
