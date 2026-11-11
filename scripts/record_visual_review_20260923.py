"""Record the 43-image contact-sheet review as hash-bound publication approvals."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from avito_bridge import profile_publish as pp


CONTENT = Path("/opt/avito-ready-price/content")
OUTPUT = Path("/opt/avito-bridge/state/ready-price/visual-approvals.json")
# These seven pairs visibly showed the wrong variant/product or only a brand logo.
EXCLUDED = {
    "00-00000323", "00-00000369", "00-00001238", "00-00001381",
    "00-00001198", "00-00001374", "00-00001423",
}


def main() -> None:
    directories = {p.name: p for p in CONTENT.iterdir() if p.is_dir() and (p / "content.json").is_file()}
    if len(directories) != 43 or not EXCLUDED <= directories.keys():
        raise ValueError("Content inventory changed since 43-pair visual review")
    now = datetime.now(timezone.utc).isoformat()
    approvals = {}
    for article, directory in sorted(directories.items()):
        if article in EXCLUDED:
            continue
        content = json.loads((directory / "content.json").read_text(encoding="utf-8"))
        if content.get("article") != article:
            raise ValueError(f"Article mismatch: {article}")
        approval = {"passed": True, "brand": content["brand"], "model": content["model"],
                    "reviewed_at": now, "method": "side_by_side_product_image_review"}
        for kind in ("card", "original"):
            approval[kind + "_sha256"] = hashlib.sha256((directory / content[kind]).read_bytes()).hexdigest()
        approvals[article] = approval
    if OUTPUT.exists():
        raise FileExistsError(f"Refusing to overwrite {OUTPUT}")
    pp._write_atomic_bytes(OUTPUT, json.dumps(approvals, ensure_ascii=False, indent=2).encode("utf-8"))
    print(json.dumps({"reviewed": len(directories), "approved": len(approvals),
                      "held": sorted(EXCLUDED), "output": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
