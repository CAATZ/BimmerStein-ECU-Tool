#!/usr/bin/env python3
"""Verify that the Soft-BSL sources and shipped payloads are the reviewed pair."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from .preprocess_asm import preprocess_text
    from .softbsl_host import load_agent
except ImportError:  # Direct execution from this directory.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from engines.softbsl.preprocess_asm import preprocess_text
    from engines.softbsl.softbsl_host import load_agent


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "agent_manifest.json"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_sha256(path: Path) -> str:
    """Hash UTF-8 text after universal-newline decoding for cross-platform checkouts."""
    return _sha256(path.read_text(encoding="utf-8").encode("utf-8"))


def verify_manifest() -> list[str]:
    """Return human-readable mismatches; an empty list means all artifacts match."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    errors: list[str] = []

    assembler = manifest["assembler"]
    assembler_path = ROOT / assembler["path"]
    if not assembler_path.is_file():
        errors.append(f"missing assembler: {assembler_path.name}")
    elif _text_sha256(assembler_path) != assembler["text_sha256"]:
        errors.append(f"assembler hash mismatch: {assembler_path.name}")

    for family, record in manifest["agents"].items():
        source_path = ROOT / record["source"]
        payload_path = ROOT / record["payload"]
        if not source_path.is_file():
            errors.append(f"{family}: missing source {source_path.name}")
            continue
        if not payload_path.is_file():
            errors.append(f"{family}: missing payload {payload_path.name}")
            continue

        if _text_sha256(source_path) != record["source_text_sha256"]:
            errors.append(f"{family}: source hash mismatch")

        normalized, _equ = preprocess_text(source_path.read_text(encoding="utf-8"))
        if _sha256(normalized.encode("utf-8")) != record["preprocessed_sha256"]:
            errors.append(f"{family}: preprocessed-source hash mismatch")

        payload = load_agent(str(payload_path))
        if len(payload) != record["payload_size"]:
            errors.append(
                f"{family}: payload size {len(payload)} != {record['payload_size']}")
        if _sha256(payload) != record["payload_sha256"]:
            errors.append(f"{family}: payload hash mismatch")

    return errors


def main() -> int:
    errors = verify_manifest()
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Soft-BSL agent sources, assembler, and runtime payloads match the manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
