"""Verify the extracted source inventory before a reproducible experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def verified_source_manifest(root: Path) -> dict:
    """Capture a verified snapshot, refusing missing or modified source files."""
    root = root.resolve()
    manifest_path = root / "source-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    inventory = manifest.get("python_files")
    if not isinstance(inventory, dict) or not inventory:
        raise RuntimeError("Source manifest has no Python inventory")
    mismatches = []
    for relative, entry in inventory.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            mismatches.append(relative)
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != entry.get("extracted_sha256"):
            mismatches.append(relative)
    if mismatches:
        raise RuntimeError("Source provenance mismatch: " + ", ".join(mismatches))
    return {
        **manifest,
        "verification": {
            "verified": True,
            "python_file_count": len(inventory),
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        },
    }
