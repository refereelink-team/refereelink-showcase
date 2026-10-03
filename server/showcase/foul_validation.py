"""Bind accepted detector tasks to their measured code and asset snapshots.

This module inspects files only. API qualification must never import the model
runtime or infer a pass from the configured profile name.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re


V2_CONFIGURATION = {
    'offence_threshold': 0.55, 'action_threshold': 0.70,
    'confirmation_windows': 2, 'stride_s': 0.20, 'post_contact_s': 0.20,
    'evidence_threshold': 0.25, 'max_windows_per_tick': 2,
}
HASH_FIELDS = ('config_sha256', 'model_sha256', 'core_manifest_sha256',
               'external_source_sha256')
_SHA256 = re.compile(r'^[a-f0-9]{64}$')


def configuration_sha256(config: dict) -> str:
    data = json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


class FoulValidation:
    """Cache unchanged files while rechecking every inventory entry's metadata."""

    def __init__(self, settings):
        self.settings = settings
        self._digests: dict[str, tuple[tuple[int, int, int, int], str]] = {}

    def file_sha256(self, path: Path) -> str:
        path = path.resolve(strict=True)
        stat = path.stat()
        key = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        cached = self._digests.get(str(path))
        if cached and cached[0] == key:
            return cached[1]
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        value = digest.hexdigest()
        self._digests[str(path)] = (key, value)
        return value

    def core_sha256(self) -> str:
        root = self.settings.core_root.resolve()
        manifest = root / 'source-manifest.json'
        inventory = json.loads(manifest.read_text()).get('python_files')
        if not isinstance(inventory, dict) or not inventory:
            raise ValueError('Source manifest has no Python inventory')
        for relative, entry in inventory.items():
            path = (root / relative).resolve()
            if (not path.is_relative_to(root) or not path.is_file()
                    or self.file_sha256(path) != entry.get('extracted_sha256')):
                raise ValueError('Core source inventory no longer matches its manifest')
        return self.file_sha256(manifest)

    def external_sha256(self, profile: str) -> str:
        if profile == 'multidim-full-v2':
            root = self.settings.multidim_code
            paths = [root / name for name in
                     ('utils.py', 'multidim_stacker_mod.py', 'mvaggregate.py')]
        else:
            root = self.settings.foul_code
            paths = sorted(root.rglob('*.py'))
        if not paths:
            raise ValueError('External model source is missing')
        # Preserve the author adapter's ordered relative-name/file-byte digest.
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    def assets(self, profile: str) -> dict:
        model = (self.settings.multidim_model if profile == 'multidim-full-v2'
                 else self.settings.foul_model)
        return {
            'model_sha256': self.file_sha256(model),
            'core_manifest_sha256': self.core_sha256(),
            'external_source_sha256': self.external_sha256(profile),
        }

    def qualification(self) -> dict | None:
        path = self.settings.foul_qualification
        if path is None:
            path = self.settings.core_root.parent / 'runtime/foul-qualification-v3.json'
        try:
            record = json.loads(path.read_text())
            if (record.get('detection_profile') != 'mvit-contact-v3'
                    or record.get('status') != 'passed'
                    or record.get('scope') != 'development_clip'
                    or not isinstance(record.get('config'), dict)
                    or not all(_SHA256.fullmatch(record.get(key, '')) for key in
                               (*HASH_FIELDS, 'evidence_sha256'))
                    or configuration_sha256(record['config']) != record['config_sha256']):
                return None
            verified_at = datetime.fromisoformat(record['verified_at'].replace('Z', '+00:00'))
            if verified_at.tzinfo is None:
                return None
            evidence = Path(record['evidence_path'])
            if not evidence.is_absolute():
                evidence = path.parent / evidence
            if self.file_sha256(evidence) != record['evidence_sha256']:
                return None
            actual = self.assets('mvit-contact-v3')
            if any(actual[key] != record[key] for key in actual):
                return None
            # Runtime paths and configuration internals remain private.
            return {key: record[key] for key in
                    ('detection_profile', 'status', 'scope', 'verified_at',
                     *HASH_FIELDS, 'evidence_sha256')}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def snapshot(self, profile: str, qualification: dict | None = None) -> dict:
        if profile == 'mvit-contact-v3':
            qualification = qualification or self.qualification()
            if qualification is None:
                raise ValueError('Contact profile has no matching acceptance evidence')
            return {key: qualification[key] for key in HASH_FIELDS}
        return {**self.assets(profile),
                'config_sha256': configuration_sha256(V2_CONFIGURATION)}
