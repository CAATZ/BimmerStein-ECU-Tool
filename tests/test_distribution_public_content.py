"""Product documents must pass the release scan after their actual encoding."""
import importlib.util
import json
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[1] / "packaging" / "verify_dist.py"
    spec = importlib.util.spec_from_file_location("verify_public_content", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pdf(path, *lines):
    document = canvas.Canvas(str(path), pageCompression=1)
    text = document.beginText(40, 750)
    for line in lines:
        text.textLine(line)
    document.drawText(text)
    document.save()


def test_staged_compressed_pdf_text_cannot_escape_public_scan(tmp_path, verifier):
    manual = tmp_path / "BimmerStein-ECU-Tool-User-Manual.pdf"
    term = verifier.PROHIBITED_PUBLIC_TERMS[1]
    _pdf(manual, f"Prepared using {term}")
    assert term.encode() not in manual.read_bytes()
    with pytest.raises(RuntimeError, match="prohibited public reference"):
        verifier._verify_public_documents(tmp_path)


def test_pdf_line_wrapping_does_not_hide_prohibited_phrase(tmp_path, verifier):
    manual = tmp_path / "BimmerStein-ECU-Tool-User-Manual.pdf"
    _pdf(manual, *verifier.PROHIBITED_PUBLIC_TERMS[0].split())
    with pytest.raises(RuntimeError, match="prohibited public reference"):
        verifier._verify_public_documents(tmp_path)


def test_pdf_metadata_cannot_carry_private_build_provenance(tmp_path, verifier):
    manual = tmp_path / "BimmerStein-ECU-Tool-User-Manual.pdf"
    document = canvas.Canvas(str(manual), pageCompression=1)
    document.setAuthor(r"C:\Users\developer\manual-build")
    document.drawString(40, 750, "BimmerStein ECU Tool")
    document.save()
    with pytest.raises(RuntimeError, match="private provenance"):
        verifier._verify_public_documents(tmp_path)


@pytest.mark.parametrize("name", ["CHANGELOG.md", "RELEASE-METADATA.json"])
@pytest.mark.parametrize("value", [
    "Private " + "And" + "roid" + " development",
    r"C:\Users\developer\release-proof.json",
    "_private/audits/release-proof.json",
])
def test_optional_staged_documents_reject_private_provenance(tmp_path, verifier, name, value):
    path = tmp_path / name
    path.write_text(json.dumps({"notes": value}) if path.suffix == ".json" else value,
                    encoding="utf-8")
    with pytest.raises(RuntimeError, match="private provenance"):
        verifier._verify_public_documents(tmp_path)


@pytest.mark.parametrize("name", ["CHANGELOG.md", "RELEASE-METADATA.json"])
def test_optional_staged_documents_reject_prohibited_attribution(tmp_path, verifier, name):
    (tmp_path / name).write_text(verifier.PROHIBITED_PUBLIC_TERMS[1], encoding="utf-8")
    with pytest.raises(RuntimeError, match="prohibited public reference"):
        verifier._verify_public_documents(tmp_path)


@pytest.mark.parametrize("blank", [False, True])
def test_unreadable_or_textless_pdf_fails_verification(tmp_path, verifier, blank):
    manual = tmp_path / "BimmerStein-ECU-Tool-User-Manual.pdf"
    if blank:
        _pdf(manual)
    else:
        manual.write_bytes(b"not a PDF")
    with pytest.raises(RuntimeError, match="cannot verify packaged PDF text"):
        verifier._verify_public_documents(tmp_path)


def test_backend_metadata_and_required_dependency_names_remain_allowed(tmp_path, verifier):
    (tmp_path / "RELEASE-METADATA.json").write_text(json.dumps({
        "build_backend": "nuitka", "pyqt_license_basis": "GPLv3",
        "vc_runtime_files": {"PyQt5/Qt5/bin/MSVCP140.dll": {"sha256": "abc"}},
    }), encoding="utf-8")
    (tmp_path / "THIRD_PARTY_NOTICES.md").write_text(
        "CPython, Qt, PyQt5, PyInstaller, Nuitka, pyserial and libusb license notices.",
        encoding="utf-8")
    _pdf(tmp_path / "BimmerStein-ECU-Tool-User-Manual.pdf", "BimmerStein ECU Tool")
    verifier._verify_public_documents(tmp_path)
    # Upstream license text is not a product-facing private-development claim.
    license_file = tmp_path / "dependency-LICENSE.txt"
    license_file.write_text("And" + "roid" + " platform license terms", encoding="utf-8")
    verifier._verify_public_terms((license_file,))
