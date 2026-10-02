from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import sys


@dataclass(frozen=True)
class Settings:
    media_root: Path
    state_root: Path
    upstream: str
    core_root: Path
    core_python: Path
    player_model: Path
    pitch_model: Path
    foul_model: Path
    foul_code: Path
    bundle: Path
    enforce_cuda: bool = True
    job_timeout_s: int = 1800

    @classmethod
    def from_environment(cls):
        root = Path(__file__).resolve().parents[2]
        run = root / 'runtime'
        weights = run / 'weights'
        return cls(
            media_root=Path(os.getenv('SHOWCASE_MEDIA_ROOT', str(run / 'media'))),
            state_root=Path(os.getenv('SHOWCASE_STATE_ROOT', str(run / 'state'))),
            upstream=os.getenv('SHOWCASE_UPSTREAM', 'http://127.0.0.1:8000').rstrip('/'),
            core_root=Path(os.getenv('SHOWCASE_CORE_ROOT', str(Path(__file__).resolve().parents[2] / 'backend_core'))),
            core_python=Path(os.getenv('SHOWCASE_CORE_PYTHON', sys.executable)),
            player_model=Path(os.getenv('SHOWCASE_PLAYER_MODEL', str(weights / 'yolo11s.pt'))),
            pitch_model=Path(os.getenv('SHOWCASE_PITCH_MODEL', str(weights / 'football-pitch-detection.pt'))),
            foul_model=Path(os.getenv('SHOWCASE_FOUL_MODEL', str(weights / 'mvfoul.pth.tar'))),
            foul_code=Path(os.getenv('SHOWCASE_FOUL_CODE', str(root / 'backend_core/third_party/sn-mvfoul/VARS model'))),
            bundle=Path(os.getenv('SHOWCASE_BUNDLE', str(run / 'media/calibration/match-1.npz'))),
        )
