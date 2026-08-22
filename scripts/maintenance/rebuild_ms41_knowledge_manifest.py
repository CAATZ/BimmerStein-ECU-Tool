from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zipfile
from pathlib import Path


MANIFEST_NAMES = {
    "INVENTORY.csv",
    "INVENTORY.json",
    "SHA256SUMS.txt",
    "VALIDATION_SUMMARY.json",
}


def file_type(path: Path) -> str:
    return {
        ".pdf": "PDF",
        ".zip": "ZIP",
        ".xml": "XML",
        ".md": "Markdown",
        ".csv": "CSV",
        ".json": "JSON",
        ".bin": "BIN",
    }.get(path.suffix.lower(), path.suffix.lstrip(".").upper() or "File")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest().upper()


def load_old(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.exists():
        return {}
    with csv_path.open("r", newline="", encoding="utf-8-sig") as stream:
        return {row["RelativePath"]: row for row in csv.DictReader(stream)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    manifests = root / "Manifests"
    old = load_old(manifests / "INVENTORY.csv")
    rows: list[dict[str, object]] = []

    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p.relative_to(root)).lower()):
        rel = str(path.relative_to(root)).replace("/", "\\")
        if path.parent == manifests and path.name in MANIFEST_NAMES:
            continue
        prior = old.get(rel, {})
        kind = file_type(path)
        pages: int | None = None
        zip_entries: int | None = None
        status = "valid"
        error: str | None = None
        title = prior.get("Title", "")
        try:
            if kind == "PDF":
                from pypdf import PdfReader

                reader = PdfReader(path)
                pages = len(reader.pages)
                if not title:
                    title = str((reader.metadata or {}).get("/Title", "") or "")
            elif kind == "ZIP":
                with zipfile.ZipFile(path) as archive:
                    bad = archive.testzip()
                    if bad:
                        raise ValueError(f"bad ZIP member: {bad}")
                    zip_entries = len(archive.infolist())
            elif kind == "JSON":
                json.loads(path.read_text(encoding="utf-8-sig"))
            elif kind == "CSV":
                with path.open("r", newline="", encoding="utf-8-sig") as stream:
                    next(csv.reader(stream), None)
            elif kind == "XML":
                import xml.etree.ElementTree as ET

                ET.parse(path)
        except Exception as exc:
            status = "invalid"
            error = str(exc)

        rows.append(
            {
                "RelativePath": rel,
                "Type": kind,
                "Bytes": path.stat().st_size,
                "SHA256": digest(path),
                "Pages": pages,
                "Title": title,
                "ZipEntries": zip_entries,
                "Status": status,
                "Error": error,
            }
        )

    manifests.mkdir(parents=True, exist_ok=True)
    fields = ["RelativePath", "Type", "Bytes", "SHA256", "Pages", "Title", "ZipEntries", "Status", "Error"]
    with (manifests / "INVENTORY.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (manifests / "INVENTORY.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (manifests / "SHA256SUMS.txt").write_text(
        "".join(f'{str(row["SHA256"]).lower()} *{str(row["RelativePath"]).replace(chr(92), chr(47))}\n' for row in rows),
        encoding="utf-8",
    )
    summary = {
        "TotalFiles": len(rows),
        "PDFCount": sum(row["Type"] == "PDF" for row in rows),
        "PDFPages": sum(int(row["Pages"] or 0) for row in rows),
        "ZIPCount": sum(row["Type"] == "ZIP" for row in rows),
        "XMLCount": sum(row["Type"] == "XML" for row in rows),
        "InvalidCount": sum(row["Status"] != "valid" for row in rows),
        "TotalBytes": sum(int(row["Bytes"]) for row in rows),
    }
    (manifests / "VALIDATION_SUMMARY.json").write_text(json.dumps(summary, indent=4) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return 1 if summary["InvalidCount"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
