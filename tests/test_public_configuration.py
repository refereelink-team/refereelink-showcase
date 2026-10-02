from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from fastapi.testclient import TestClient
from showcase.app import create_app
from showcase.settings import Settings


def test_configuration_is_portable_and_preserves_deployment_overrides(monkeypatch, tmp_path):
    for key in list(__import__('os').environ):
        if key.startswith('SHOWCASE_'):
            monkeypatch.delenv(key)
    settings = Settings.from_environment()
    assert settings.core_python == Path(sys.executable)
    assert settings.media_root.parent.name == 'runtime'
    assert settings.player_model.parent == settings.media_root.parent / 'weights'
    assert settings.enforce_cuda is True
    monkeypatch.setenv('SHOWCASE_MEDIA_ROOT', str(tmp_path / 'private-media'))
    monkeypatch.setenv('SHOWCASE_CORE_PYTHON', '/opt/cuda-env/bin/python')
    monkeypatch.setenv('SHOWCASE_UPSTREAM', 'http://upstream.example:8000/')
    overridden = Settings.from_environment()
    assert overridden.media_root == tmp_path / 'private-media'
    assert overridden.core_python == Path('/opt/cuda-env/bin/python')
    assert overridden.upstream == 'http://upstream.example:8000'


def test_production_startup_refuses_cpu_only_environment(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setenv('SHOWCASE_MEDIA_ROOT', str(tmp_path / 'media'))
    monkeypatch.setenv('SHOWCASE_STATE_ROOT', str(tmp_path / 'state'))
    with pytest.raises(RuntimeError, match='remote CUDA host'):
        with TestClient(create_app()):
            pass
