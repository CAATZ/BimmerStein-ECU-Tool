"""User-imported XDF patch catalogs and per-image rollback records."""

import hashlib
import json
import os
from collections import Counter
from pathlib import Path
import re
import tempfile

from app_paths import user_data_path
from backup_manager import _lock_file
from ms42_ms43_image import image_identity
import ms42_ms43_xdf_patches as patches


class EditedPatchImage(ValueError):
    """A locally known receipt can be explicitly checked against a modified copy."""


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _rollback_steps(operations):
    # Different XDF views can encode the same edit. Undo uses the owner and exact bytes.
    return [(op["kind"], op["group_id"], op["spans"]) for op in operations]


def _atomic_bytes(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = tempfile.NamedTemporaryFile(dir=path.parent, delete=False)
    temp = Path(stream.name)
    try:
        with stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_json(path, value):
    _atomic_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _read_limited(path, limit):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("The selected file exceeds the size limit.")
    return data


class ExternalPatchLibrary:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else user_data_path("external_patches")

    def import_file(self, path):
        return self.import_bytes(Path(path).name, _read_limited(path, 16 << 20))

    def import_bytes(self, filename, raw):
        if (not filename or Path(filename).name != filename or "/" in filename or "\\" in filename
                or not filename.lower().endswith(".xdf") or len(raw) > 16 << 20):
            raise ValueError("Select an XDF file of at most 16 MiB.")
        digest = _digest(raw)
        catalogs = self.directory / "catalogs"
        catalogs.mkdir(parents=True, exist_ok=True)
        destination = catalogs / digest
        if not destination.exists():
            # Publish the validated file and its original display name as a unit.
            with tempfile.TemporaryDirectory(dir=catalogs) as parent:
                staged = Path(parent) / "catalog"
                staged.mkdir()
                (staged / "list.xdf").write_bytes(raw)
                patches.load_catalog(staged / "list.xdf")
                (staged / "name.json").write_text(json.dumps(filename), encoding="utf-8")
                try:
                    staged.rename(destination)
                except OSError:
                    if not destination.is_dir():
                        raise
        self.catalog(digest)
        return digest

    def catalog(self, catalog_id):
        if not isinstance(catalog_id, str) or not re.fullmatch("[0-9a-f]{64}", catalog_id):
            raise ValueError("Invalid patch catalog identifier.")
        result = patches.load_catalog(self.directory / "catalogs" / catalog_id / "list.xdf")
        if result.digest != catalog_id:
            raise ValueError("The saved patch catalog has changed. Import the original XDF.")
        return result

    def catalogs(self, data=None):
        result = []
        for path in sorted((self.directory / "catalogs").glob("*/list.xdf")):
            catalog = self.catalog(path.parent.name)
            if data is not None and not patches.catalog_matches(catalog, data):
                continue
            filename = json.loads((path.parent / "name.json").read_text(encoding="utf-8"))
            result.append(dict(id=catalog.digest, title=catalog.title, version=catalog.version,
                               family=catalog.family, edition=catalog.edition, filename=filename))
        counts = Counter(c["filename"].casefold() for c in result)
        for c in result:
            c["label"] = (c["filename"] if counts[c["filename"].casefold()] == 1 else
                          f"{c['filename']} ({c['version']} · {c['id'][:10]})")
        return result

    def _preference(self, data):
        identity = image_identity(data)
        key = _digest((identity["family"] + "/" + identity["software_id"]).encode("ascii"))
        return self.directory / "preferences" / (key + ".json")

    def select(self, data, catalog_id):
        if not patches.catalog_matches(self.catalog(catalog_id), data):
            raise ValueError("The patch catalog does not match the loaded image.")
        _atomic_json(self._preference(data), catalog_id)

    def preferred(self, data, available=None):
        choices = [c["id"] for c in (self.catalogs(data) if available is None else available)]
        if len(choices) < 2:
            return next(iter(choices), None)
        history = self.receipt(data)
        if history and history["operations"] and history.get("catalog_digest") in choices:
            return history["catalog_digest"]
        path = self._preference(data)
        selected = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        return selected if selected in choices else None

    def _receipt_path(self, data):
        return self.directory / "rollback" / (_digest(data) + ".json")

    def receipt(self, data):
        path = self._receipt_path(data)
        if not path.is_file():
            return None
        history = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(history, dict) or history.get("output_sha256") != path.stem
                or not isinstance(history.get("operations"), list)):
            raise ValueError("The saved rollback record does not match this BIN.")
        return history

    def save_receipt(self, data, receipt, *, parent=None):
        path = self._receipt_path(data)
        if receipt.get("output_sha256") != path.stem:
            raise ValueError("Rollback record does not match the resulting BIN.")
        if parent is not None:
            parent_id = _digest(parent)
            if receipt.get("parent_sha256") != parent_id:
                raise ValueError("The source BIN does not match this build.")
            if parent_id == path.stem:
                return
            snapshot = self.directory / "originals" / (parent_id + ".bin")
            if not snapshot.exists():
                _atomic_bytes(snapshot, parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        with (path.parent / ".lock").open("a+b") as lock:
            _lock_file(lock)
            old = self.receipt(data)
            if old is None:
                _atomic_json(path, receipt)
            elif (_rollback_steps(old["operations"]) != _rollback_steps(receipt["operations"])
                  or old["operations"] and old["catalog_digest"] != receipt["catalog_digest"]):
                raise ValueError("This BIN already has a different rollback history. Preserve its original record.")
            if parent is not None:
                local = self.directory / "originals" / (path.stem + ".parents.json")
                parents = json.loads(local.read_text(encoding="utf-8")) if local.is_file() else []
                if parent_id not in parents:
                    _atomic_json(local, parents + [parent_id])

    def previous(self, data):
        # A portable record can support selective undo. Only a local build can
        # identify its exact parent, and two distinct parents may produce the same BIN.
        local = self.directory / "originals" / (_digest(data) + ".parents.json")
        if not local.is_file():
            return None
        parents = json.loads(local.read_text(encoding="utf-8"))
        if not isinstance(parents, list) or len(parents) != 1:
            return None
        parent = parents[0]
        if not isinstance(parent, str) or not re.fullmatch("[0-9a-f]{64}", parent):
            raise ValueError("Invalid parent digest in rollback record.")
        path = self.directory / "originals" / (parent + ".bin")
        if not path.is_file():
            return None
        original = _read_limited(path, len(data))
        if _digest(original) != parent:
            raise ValueError("The saved original BIN has changed.")
        return original

    def build(self, data, catalog_id, *, install=(), remove=(), changes=(), allow_unbased=False, reference=None):
        catalog = self.catalog(catalog_id)
        history = self.receipt(data)
        if history and not history["operations"]:
            history = None
        if history and history["catalog_digest"] != catalog_id:
            raise ValueError("Use the recorded catalog for this BIN or open its original base.")
        result, history = patches.build(catalog, data, install=install, remove=remove,
                                        changes=changes, history=history, allow_unbased=allow_unbased, reference=reference)
        self.save_receipt(result, history, parent=data)
        return result

    def export_receipt(self, data, path):
        history = self.receipt(data)
        if not history:
            raise ValueError("This BIN has no recorded patch changes.")
        _atomic_json(Path(path), history)

    def import_receipt(self, data, path, *, allow_edited=False):
        return self.import_receipt_bytes(data, _read_limited(path, 16 << 20), allow_edited=allow_edited)

    def import_receipt_bytes(self, data, raw, *, allow_edited=False):
        if len(raw) > 16 << 20:
            raise ValueError("Rollback record exceeds 16 MiB.")
        history = json.loads(raw)
        if not isinstance(history, dict):
            raise ValueError("Invalid XDF rollback record.")
        recorded = history.get("output_sha256")
        if not isinstance(recorded, str) or not re.fullmatch("[0-9a-f]{64}", recorded):
            raise ValueError("Invalid recorded BIN digest.")
        if recorded != _digest(data):
            known = self.directory / "rollback" / (recorded + ".json")
            if not known.is_file() or json.loads(known.read_text(encoding="utf-8")) != history:
                raise ValueError("Select the rollback record exported for this BIN.")
            if not allow_edited:
                raise EditedPatchImage("This known record belongs to a different BIN digest.")
            # An edited copy has its own unrelated tuning. Whole-parent restore would lose it.
            history = dict(history, parent_sha256=_digest(data), output_sha256=_digest(data))
        catalog = self.catalog(history.get("catalog_digest"))
        patches.validate_history(catalog, data, history)
        self.save_receipt(data, history)
        return catalog.digest
