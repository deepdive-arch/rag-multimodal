"""Deterministic tests for the isolated Product Demo scenario."""

import hashlib
import sys
from pathlib import Path

import fitz
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "product-demo" / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from demo_fixture_lib import (  # noqa: E402
    PNG_NAME,
    PDF_NAME,
    build_demo_config,
    create_fixtures,
    require_external_api_calls,
    reset_local,
    validate_pdf,
    validate_demo_fixtures,
)


def test_fixture_generation_is_reproducible(tmp_path: Path):
    """Repeated generation keeps both fixture byte streams stable."""
    create_fixtures(tmp_path)
    first = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in (PDF_NAME, PNG_NAME)}
    create_fixtures(tmp_path)
    second = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in (PDF_NAME, PNG_NAME)}
    assert first == second


def test_pdf_generation_and_page_validation(tmp_path: Path):
    """The generated agreement has exactly three pages and required content."""
    create_fixtures(tmp_path)
    assert len(fitz.open(tmp_path / PDF_NAME)) == 3
    assert validate_demo_fixtures(tmp_path)["passed"]


def test_pdf_validator_rejects_wrong_page_count(tmp_path: Path):
    """The PDF validator explicitly enforces the three-page contract."""
    path = tmp_path / PDF_NAME
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()
    assert any("3 páginas" in error for error in validate_pdf(path))


def test_png_generation_and_dimensions(tmp_path: Path):
    """The generated panel is a valid 1600 by 1000 PNG."""
    create_fixtures(tmp_path)
    with Image.open(tmp_path / PNG_NAME) as image:
        assert image.format == "PNG"
        assert image.size == (1600, 1000)


def test_default_namespace_is_rejected():
    """The remote default namespace can never be used by the demo."""
    values = {"PINECONE_NAMESPACE": "default", "DATABASE_PATH": ".tmp/product-demo/rag.db", "UPLOADS_DIR": ".tmp/product-demo/uploads", "DERIVED_DIR": ".tmp/product-demo/uploads/derived"}
    try:
        build_demo_config(values)
    except ValueError as error:
        assert "default" in str(error)
    else:
        raise AssertionError("default namespace was accepted")


def test_paths_outside_demo_root_are_rejected(tmp_path: Path):
    """Database and storage paths outside the isolated root fail closed."""
    values = {"PINECONE_NAMESPACE": "product-demo-rag", "DATABASE_PATH": str(tmp_path / "main.db"), "UPLOADS_DIR": ".tmp/product-demo/uploads", "DERIVED_DIR": ".tmp/product-demo/uploads/derived"}
    try:
        build_demo_config(values)
    except ValueError as error:
        assert "dentro" in str(error)
    else:
        raise AssertionError("outside path was accepted")


def test_reset_local_keeps_other_tmp_directories(tmp_path: Path, monkeypatch):
    """The reset removes only the exact demo root."""
    import demo_fixture_lib

    monkeypatch.setattr(demo_fixture_lib, "PROJECT_ROOT", tmp_path)
    config = build_demo_config({"PINECONE_NAMESPACE": "product-demo-rag", "DATABASE_PATH": ".tmp/product-demo/rag.db", "UPLOADS_DIR": ".tmp/product-demo/uploads", "DERIVED_DIR": ".tmp/product-demo/uploads/derived"}, tmp_path)
    config.local_root.mkdir(parents=True)
    other = tmp_path / ".tmp" / "keep.txt"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("preserve", encoding="utf-8")
    reset_local(config)
    assert not config.local_root.exists()
    assert other.read_text(encoding="utf-8") == "preserve"


def test_external_calls_require_explicit_consent():
    """The shared safety guard blocks remote work by default."""
    try:
        require_external_api_calls(False)
    except PermissionError:
        pass
    else:
        raise AssertionError("external call guard did not block")
    require_external_api_calls(True)


def test_semantic_verifier_contains_tolerant_variants():
    """The TypeScript verifier accepts accents, dates and written percentages variants."""
    source = (SCRIPT_DIR / "verify-demo-scenario.ts").read_text(encoding="utf-8")
    assert "normalize(\"NFD\")" in source
    assert "15/09/2026" in source
    assert "oito pontos percentuais" in source
    assert "95 por cento" in source


def test_report_contract_excludes_sensitive_payloads():
    """The generated report contract does not include secrets or full answers."""
    source = (SCRIPT_DIR / "verify-demo-scenario.ts").read_text(encoding="utf-8")
    for forbidden in ("GOOGLE_API_KEY", "PINECONE_API_KEY", "Authorization", "vector", "answer: result.answer"):
        assert forbidden not in source
    assert "expectedTerms" in source and "sourceFiles" in source
