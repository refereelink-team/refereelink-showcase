from pathlib import Path
from types import SimpleNamespace
import os
import sys

import pytest
from fastapi.testclient import TestClient
from showcase.app import create_app
from showcase.settings import Settings


def test_configuration_is_portable_and_preserves_deployment_overrides(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith('SHOWCASE_'):
            monkeypatch.delenv(key)
    settings = Settings.from_environment()
    assert settings.core_python == Path(sys.executable)
    assert settings.media_root.parent.name == 'runtime'
    assert settings.player_model.parent == settings.media_root.parent / 'weights'
    assert settings.upstream == 'http://127.0.0.1:8000'
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
    with pytest.raises(RuntimeError, match='requires an available CUDA device'):
        with TestClient(create_app()):
            pass


def test_production_startup_explains_missing_pytorch(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'torch', None)
    monkeypatch.setenv('SHOWCASE_MEDIA_ROOT', str(tmp_path / 'media'))
    monkeypatch.setenv('SHOWCASE_STATE_ROOT', str(tmp_path / 'state'))
    with pytest.raises(RuntimeError, match='requires CUDA-enabled PyTorch'):
        with TestClient(create_app()):
            pass


def test_cuda_startup_contract_is_independent_of_remote_deployment(monkeypatch, tmp_path):
    # Stubbed contract only; the marked CUDA test still requires a real GPU.
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(
        is_available=lambda: True, get_device_name=lambda: 'software-test-gpu')))
    monkeypatch.setenv('SHOWCASE_MEDIA_ROOT', str(tmp_path / 'media'))
    monkeypatch.setenv('SHOWCASE_STATE_ROOT', str(tmp_path / 'state'))
    with TestClient(create_app()) as client:
        backend = client.get('/api/health').json()['backend']
        assert backend['device'] == 'cuda'
        assert backend['cuda_available'] is True
        assert backend['gpu_name'] == 'software-test-gpu'
