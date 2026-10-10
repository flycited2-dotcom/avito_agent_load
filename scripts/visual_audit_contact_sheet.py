"""Create a read-only contact sheet of published ready-price source/card pairs."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("/opt/avito-bridge/state/ready-price/catalog.sqlite"))
    parser.add_argument("--content", type=Path, default=Path("/opt/avito-ready-price/content"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/avito-visual-audit"))
    parser.add_argument("--all", action="store_true", help="Include unpublished content")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.database)
    rows = db.execute("SELECT article,ad_id,source_name FROM source_bindings ORDER BY article").fetchall()
    db.close()
    published = {row[0]: row for row in rows}
    articles = sorted((p.name for p in args.content.iterdir() if p.is_dir())) if args.all else sorted(published)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    font = ImageFont.truetype(font_path, 16)
    title_font = ImageFont.truetype(font_path, 20)
    output_rows = []
    for article in articles:
        folder = args.content / article
        try:
            manifest = json.loads((folder / "content.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        output_rows.append((article, published.get(article), folder, manifest))
    for start in range(0, len(output_rows), 8):
        chunk = output_rows[start:start + 8]
        sheet = Image.new("RGB", (1800, len(chunk) * 250), "white")
        draw = ImageDraw.Draw(sheet)
        for index, (article, publication, folder, manifest) in enumerate(chunk):
            y = index * 250
            draw.text((10, y + 4), article + (" PUBLISHED" if publication else " UNPUBLISHED"), fill="black", font=title_font)
            draw.text((10, y + 30), (publication[2] if publication else manifest.get("name", ""))[:45], fill="black", font=font)
            draw.text((10, y + 55), str(manifest.get("brand", "")) + "  " + str(manifest.get("model", "")), fill="black", font=font)
            for kind, x in (("original", 600), ("card", 1200)):
                path = folder / str(manifest.get(kind, ""))
                draw.text((x, y + 4), kind.upper(), fill="black", font=title_font)
                try:
                    with Image.open(path) as source:
                        image = source.convert("RGB")
                    image.thumbnail((570, 220))
                    sheet.paste(image, (x, y + 28))
                except (OSError, ValueError):
                    draw.text((x, y + 40), "MISSING", fill="red", font=title_font)
            draw.line((0, y + 249, 1800, y + 249), fill="#dddddd")
        sheet.save(args.output / f"sheet-{start // 8 + 1:02d}.jpg", quality=86)
    print(f"rows={len(output_rows)} sheets={(len(output_rows)+7)//8} output={args.output}")


if __name__ == "__main__":
    main()
