"""Static AST dependency extraction; does not import/run backend locally."""
from __future__ import annotations
import argparse
import ast
from collections import deque
import hashlib
import json
from pathlib import Path
import shutil

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--target', type=Path, required=True)
args = parser.parse_args()
source = args.source.resolve()
target = args.target.resolve()
seeds = ('tools/run_night_ablation.py', 'tools/render_diagnostic_video.py')
overrides = {
    'app/field_ingest/__init__.py': '"""Capture-frame domain types used by the standalone inference core.\n\nThe original eager FieldIngestService export is intentionally omitted: the\nshowcase core does not host the original ingest/session/transport service.\n"""\n',
    'app/services/__init__.py': '"""Frame encoding helpers for the standalone inference core.\n\nThe original lazy WebSocketPublisher export is intentionally omitted.\nShowcase HTTP/WebSocket delivery belongs to its separate server package.\n"""\n',
    'tools/__init__.py': '"""Reproducible offline CUDA inference and rendering tools."""\n',
}
module_files = {}
for folder in ('app', 'tools'):
    for path in (source / folder).rglob('*.py'):
        relative = path.relative_to(source)
        name = '.'.join(relative.with_suffix('').parts)
        if name.endswith('.__init__'):
            name = name[:-9]
        module_files[name] = str(relative)
for relative in overrides:
    name = '.'.join(Path(relative).with_suffix('').parts)
    if name.endswith('.__init__'):
        name = name[:-9]
    module_files[name] = relative

queue = deque(seeds)
selected = set()
external_imports = set()
edges = {}
while queue:
    relative = queue.popleft()
    if relative in selected:
        continue
    selected.add(relative)
    for parent in Path(relative).parents:
        if str(parent) == '.':
            continue
        initializer = str(parent / '__init__.py')
        if (source / initializer).is_file() or initializer in overrides:
            queue.append(initializer)
    content = overrides.get(relative) or (source / relative).read_text()
    tree = ast.parse(content)
    current_module = '.'.join(Path(relative).with_suffix('').parts)
    package = current_module[:-9] if current_module.endswith('.__init__') else current_module.rsplit('.', 1)[0]
    dependencies = set()
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split('.')
                prefix = '.'.join(parts[:len(parts) - node.level + 1])
                module = '.'.join(part for part in (prefix, node.module or '') if part)
            else:
                module = node.module or ''
            names = [module] + [f'{module}.{alias.name}' for alias in node.names]
        for name in names:
            if name in module_files:
                dependencies.add(module_files[name])
                queue.append(module_files[name])
            elif name.split('.')[0] not in ('app', 'tools'):
                external_imports.add(name.split('.')[0])
            elif name.startswith(('app.', 'tools.')):
                # "from module import class" aliases are attributes, not modules.
                if not any(name.startswith(module + '.') for module in module_files):
                    raise RuntimeError(f'Unresolved internal import: {relative}: {name}')
    edges[relative] = sorted(dependencies)

assert not any(path.startswith(('app/server/', 'app/multiview/', 'app/referee_alerts/', 'app/modes/')) for path in selected)
assert not any(path in selected for path in ('app/field_ingest/service.py', 'app/services/publisher.py'))
for relative in sorted(selected):
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if relative in overrides:
        destination.write_text(overrides[relative])
    else:
        shutil.copy2(source / relative, destination)
for relative in ('LICENSE', 'NOTICE.md'):
    shutil.copy2(source / relative, target / relative)
upstream = json.loads((source / 'source-manifest.json').read_text())
manifest = {
    'schema': 'refereelink-extracted-core-v1',
    'upstream': {key: value for key, value in upstream.items() if key != 'python_files_sha256'},
    'extraction_entrypoints': list(seeds),
    'initializer_changes': list(overrides),
    'python_files': {relative: {'source_sha256': hashlib.sha256((source / relative).read_bytes()).hexdigest() if (source / relative).is_file() else None, 'extracted_sha256': hashlib.sha256((target / relative).read_bytes()).hexdigest(), 'modified_for_extraction': relative in overrides} for relative in sorted(selected)},
    'dependency_edges': edges,
    'external_import_names': sorted(external_imports),
}
(target / 'source-manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps({'python_files': len(selected), 'files': sorted(selected), 'external_imports': sorted(external_imports)}, indent=2))
