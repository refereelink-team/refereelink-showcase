"""Experiment provenance contracts; execute on the remote CUDA test host."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "backend_core/tools/source_provenance.py"
SPEC = importlib.util.spec_from_file_location("source_provenance", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def inventory(root, relative="source.py"):
    payload = {"python_files": {relative: {
        "extracted_sha256": hashlib.sha256(b"original source").hexdigest(),
    }}}
    (root / "source-manifest.json").write_text(json.dumps(payload))
    return payload


def test_modified_source_cannot_be_reported_as_verified(tmp_path):
    inventory(tmp_path)
    (tmp_path / "source.py").write_text("modified source")
    with pytest.raises(RuntimeError, match="Source provenance mismatch: source.py"):
        MODULE.verified_source_manifest(tmp_path)


def test_missing_source_fails_before_experiment(tmp_path):
    inventory(tmp_path)
    with pytest.raises(RuntimeError, match="Source provenance mismatch"):
        MODULE.verified_source_manifest(tmp_path)


def test_inventory_cannot_escape_source_root(tmp_path):
    inventory(tmp_path, "../outside.py")
    with pytest.raises(RuntimeError, match="Source provenance mismatch"):
        MODULE.verified_source_manifest(tmp_path)


def test_verified_snapshot_survives_later_manifest_changes(tmp_path):
    inventory(tmp_path)
    (tmp_path / "source.py").write_bytes(b"original source")
    snapshot = MODULE.verified_source_manifest(tmp_path)
    (tmp_path / "source-manifest.json").write_text("{}")
    assert snapshot["verification"]["verified"] is True
    assert snapshot["verification"]["python_file_count"] == 1
    assert snapshot["python_files"]["source.py"]["extracted_sha256"]


def test_empty_inventory_is_not_verification(tmp_path):
    (tmp_path / "source-manifest.json").write_text("{}")
    with pytest.raises(RuntimeError, match="no Python inventory"):
        MODULE.verified_source_manifest(tmp_path)
