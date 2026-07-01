#!/usr/bin/env python3
"""Обработчик инбокса: фото+тексты+список → залить фото, привязать описания, добавить в публикацию,
задеплоить, сгенерировать карточки, пересобрать фид. Запуск (из каталога avito-bridge/):

    python scripts/apply_inbox.py            # применить inbox/inbox.yaml

Требует ssh-ключ ~/.ssh/climat_simf_deploy (деплой на VPS). Правит config.yaml/manifest по месту.
"""
from __future__ import annotations
import io, json, subprocess, tempfile
from pathlib import Path
import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"
CFG = ROOT / "config" / "config.yaml"
MANIFEST = ROOT / "avito-descriptions" / "manifest.json"
SSH = ["ssh", "-i", str(Path.home() / ".ssh/climat_simf_deploy"), "-o", "BatchMode=yes",
       "-o", "ConnectTimeout=45", "root@213.109.202.45"]
REMOTE_PHOTOS = "/opt/oasis/staticfiles/manual-photos"
PHOTO_BASE = "https://splithome.ru/static/manual-photos"


def ssh_run(cmd: str) -> str:
    return subprocess.run(SSH + [cmd], capture_output=True, text=True).stdout


def ssh_put(remote: str, data: bytes):
    subprocess.run(SSH + [f"cat > {remote}"], input=data, check=True)


def upload_photo(nc: str) -> str | None:
    src = next((p for p in INBOX.glob(f"photos/{nc}.*")), None)
    if not src:
        print(f"  ! фото не найдено: photos/{nc}.*"); return None
    buf = io.BytesIO()
    Image.open(src).convert("RGB").save(buf, "JPEG", quality=92)
    ssh_run(f"mkdir -p {REMOTE_PHOTOS}")
    ssh_put(f"{REMOTE_PHOTOS}/{nc}.jpg", buf.getvalue())
    ssh_run(f"chmod 644 {REMOTE_PHOTOS}/{nc}.jpg")
    print(f"  ↑ {src.name} → {PHOTO_BASE}/{nc}.jpg")
    return f"{PHOTO_BASE}/{nc}.jpg"


def insert_after(text: str, marker: str, lines: list[str]) -> str:
    new = [ln for ln in lines if ln.strip() and ln.split('"')[1] not in text]  # без дублей
    return text.replace(marker, marker + "".join(new), 1) if new else text


def main():
    items = (yaml.safe_load((INBOX / "inbox.yaml").read_text(encoding="utf-8")) or {}).get("items", [])
    if not items:
        print("inbox/inbox.yaml пуст — нечего делать"); return
    cfgtext = CFG.read_text(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mphotos, sel, forces = [], [], []

    for it in items:
        skey = it.get("series_key")
        for nc in it.get("photos", []) or []:
            url = upload_photo(str(nc))
            if url:
                mphotos.append(f'    "{nc}": "{url}"\n')
        if it.get("description"):
            f = it["description"]
            (ROOT / "avito-descriptions" / f).write_text(
                (INBOX / "descriptions" / f).read_text(encoding="utf-8"), encoding="utf-8")
            key = skey or f'rusklimat|force|{it.get("force_nc")}'
            manifest[key] = f
            print(f"  ✎ описание {f} → {key}")
        if it.get("force_nc"):
            s = f', series: "{it["series_name"]}"' if it.get("series_name") else ""
            forces.append(f'    "{it["force_nc"]}": {{ price: {it["price"]}{s} }}\n')
        elif skey:
            sel.append(f'    - "{skey}"\n')

    cfgtext = insert_after(cfgtext, "  manual_photos:\n", mphotos)
    cfgtext = insert_after(cfgtext, "  force_include:\n", forces)
    cfgtext = insert_after(cfgtext, "  selected_series:\n", sel)
    CFG.write_text(cfgtext, encoding="utf-8", newline="\n")
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("config.yaml / manifest.json обновлены.")

    # деплой + генерация + фид
    tgz = Path(tempfile.gettempdir()) / "inbox.tgz"
    subprocess.run(["tar", "-czf", str(tgz), "config", "avito-descriptions"], cwd=ROOT, check=True)
    subprocess.run(SSH + ["cat > /tmp/inbox.tgz"], input=tgz.read_bytes(), check=True)
    ssh_run("cd /opt/avito-bridge && tar -xzf /tmp/inbox.tgz && export PYTHONPATH=src "
            "&& .venv/bin/python -m avito_bridge.cards_run && systemctl start avito-bridge.service")
    print("Задеплоено, карточки поставлены, фид пересобран. Avito заберёт по расписанию.")


if __name__ == "__main__":
    main()
