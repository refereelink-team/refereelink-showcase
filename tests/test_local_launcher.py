"""Launch wiring only: the capture interpreter does not start a backend or emulate CUDA."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'checkout with spaces'
    (root / 'deploy').mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / 'deploy/start-local.sh'
    shutil.copyfile(source, root / 'deploy/start-local.sh')
    python = root / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text(f'#!{sys.executable}\n' + '''
import json, os, sys
print(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd(),
                  "upstream": os.getenv("SHOWCASE_UPSTREAM"),
                  "bytecode": os.getenv("PYTHONDONTWRITEBYTECODE")}))
''')
    python.chmod(0o755)
    return root


def launch(checkout, **overrides):
    env = {key: value for key, value in os.environ.items() if not key.startswith('SHOWCASE_')}
    env.update(overrides)
    return subprocess.run(['bash', str(checkout / 'deploy/start-local.sh')],
                          cwd=checkout.parent, env=env, capture_output=True, text=True, timeout=10)


def test_default_launcher_uses_checkout_python_loopback_and_checkout_cwd(checkout):
    result = launch(checkout)
    assert result.returncode == 0, result.stderr
    captured = json.loads(result.stdout)
    assert captured['args'] == ['-m', 'uvicorn', 'showcase.app:app', '--app-dir',
                                str(checkout / 'server'), '--host', '127.0.0.1', '--port', '8010']
    assert captured['cwd'] == str(checkout)
    assert captured['bytecode'] == '1'


def test_launcher_loads_optional_private_configuration(checkout):
    (checkout / 'deploy/showcase.env').write_text(
        'SHOWCASE_HOST=127.0.0.1\nSHOWCASE_PORT=8020\nSHOWCASE_UPSTREAM=http://127.0.0.1:8002\n')
    result = launch(checkout)
    assert result.returncode == 0, result.stderr
    captured = json.loads(result.stdout)
    assert captured['args'][-4:] == ['--host', '127.0.0.1', '--port', '8020']
    assert captured['upstream'] == 'http://127.0.0.1:8002'


def test_launcher_supports_separate_cuda_interpreter_and_config_path(checkout):
    python = checkout / 'custom environment/python'
    python.parent.mkdir()
    shutil.move(checkout / '.venv/bin/python', python)
    config = checkout / 'custom environment/local.env'
    config.write_text(f'SHOWCASE_PYTHON="{python}"\nSHOWCASE_PORT=8021\n')
    result = launch(checkout, SHOWCASE_ENV_FILE=str(config))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['args'][-1] == '8021'


def test_launcher_reports_missing_interpreter_without_fallback(checkout):
    (checkout / '.venv/bin/python').unlink()
    result = launch(checkout)
    assert result.returncode != 0
    assert 'Backend Python not found' in result.stderr
    assert 'SHOWCASE_PYTHON' in result.stderr
    assert not result.stdout


def test_launcher_does_not_ignore_explicit_missing_configuration(checkout):
    result = launch(checkout, SHOWCASE_ENV_FILE=str(checkout / 'missing.env'))
    assert result.returncode != 0
    assert 'SHOWCASE_ENV_FILE does not exist' in result.stderr
    assert not result.stdout
