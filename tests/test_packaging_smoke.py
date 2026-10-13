"""Packaging smoke guard for the PDF export dependency chain (L15.5.3).

The built .exe failed PDF export with ``No module named 'unittest'`` because
``bootleg-catalog-manager.spec`` listed ``unittest`` in ``excludes`` while
fpdf2's ``fpdf/sign.py`` does ``from unittest.mock import patch`` at module
level.  These tests make that failure mode visible before a rebuild:

1. the exporter and every module it imports at runtime must import cleanly;
2. the spec must not re-exclude ``unittest`` while still requiring the
   hidden imports that pull it into the bundle.
"""

from __future__ import annotations

import ast
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parents[1] / "bootleg-catalog-manager.spec"


def test_pdf_exporter_dependency_chain_imports():
    """Every module the PDF exporter imports must import without ImportError.

    ``fpdf.sign`` is included explicitly because it is the exact module whose
    ``from unittest.mock import patch`` (line 16) failed in the frozen build.
    """
    import bootleg_catalog.core.layout_pdf_exporter  # noqa: F401
    import fpdf  # noqa: F401
    import fpdf.sign  # noqa: F401  — the L15.5.3 failure site
    import unittest.mock  # noqa: F401
    from PIL import Image  # noqa: F401

    # Nothing above raised; the chain the .exe must resolve at runtime is
    # present and importable in a bare interpreter.
    assert SPEC_PATH.is_file()


def _spec_keyword_lists() -> dict[str, list[str]]:
    """Extract the literal ``excludes``/``hiddenimports`` lists from the spec."""
    assert SPEC_PATH.is_file(), f"missing spec file: {SPEC_PATH}"
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"))

    analysis_call = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ):
            analysis_call = node
            break
    assert analysis_call is not None, "no Analysis(...) call found in spec"

    values: dict[str, list[str]] = {}
    for keyword in analysis_call.keywords:
        if keyword.arg in ("excludes", "hiddenimports"):
            values[keyword.arg] = list(ast.literal_eval(keyword.value))
    return values


def test_spec_does_not_exclude_unittest():
    """``unittest`` must stay bundled: fpdf2 needs ``unittest.mock`` at import.

    ``excludes`` wins over ``hiddenimports`` in PyInstaller's module graph
    (an excluded module becomes an ``ExcludedModule`` node, and
    ``add_hiddenimports`` skips any module whose node already exists), so an
    ``excludes`` entry for ``unittest`` silently voids the hidden imports and
    PDF export dies again in the frozen app.
    """
    values = _spec_keyword_lists()
    excludes = values.get("excludes", [])
    hiddenimports = values.get("hiddenimports", [])

    assert "unittest" not in excludes, (
        "spec excludes 'unittest' but fpdf2 (fpdf/sign.py:16) imports "
        "unittest.mock at module level; PDF export will fail in the .exe"
    )
    assert "unittest.mock" not in excludes, (
        "spec excludes 'unittest.mock' which fpdf/sign.py imports directly"
    )
    assert "unittest" in hiddenimports
    assert "unittest.mock" in hiddenimports
