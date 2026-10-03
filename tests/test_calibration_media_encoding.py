"""Exercise clean review seek cadence without importing CUDA model dependencies."""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE = Path(__file__).resolve().parents[1] / 'backend_core/app/classification/team_calibration/clip.py'


def encoder_args():
    # The argument builder has no model dependencies; execute its actual source.
    tree = ast.parse(SOURCE.read_text())
    node = next(item for item in tree.body
                if isinstance(item, ast.FunctionDef) and item.name == '_browser_video_args')
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['_browser_video_args']


def test_legacy_encoder_options_are_unchanged():
    assert encoder_args()('ffmpeg', 'source.mp4', 'review.mp4') == [
        'ffmpeg', '-y', '-i', 'source.mp4', '-c:v', 'libx264',
        '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', 'review.mp4',
    ]


@pytest.mark.parametrize('fps', [0., -1., float('nan'), float('inf')])
def test_invalid_seek_cadence_rejected(fps):
    with pytest.raises(ValueError):
        encoder_args()('ffmpeg', 'source.mp4', 'review.mp4', seek_fps=fps)


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_frames',
        '-show_entries', 'frame=key_frame,best_effort_timestamp_time:stream=width,height,r_frame_rate',
        '-show_streams', '-of', 'json', str(path),
    ], text=True))


@pytest.mark.parametrize('rate,fps', [('30', 30.), ('30000/1001', 30000 / 1001)])
def test_short_gop_preserves_all_frames_timestamps_and_resolution(tmp_path, rate, fps):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real encoder/decoder acceptance requires ffmpeg and ffprobe')
    source, output = tmp_path / 'raw.mp4', tmp_path / 'review.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'testsrc2=size=160x90:rate={rate}',
        '-frames:v', '61', '-c:v', 'mpeg4', '-pix_fmt', 'yuv420p', str(source),
    ], check=True, capture_output=True)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    args = encoder_args()('ffmpeg', str(source), str(output), seek_fps=fps)
    subprocess.run(args, check=True, capture_output=True)
    before, after = probe(source), probe(output)
    assert len(before['frames']) == len(after['frames']) == 61
    assert [float(frame['best_effort_timestamp_time']) for frame in after['frames']] == pytest.approx(
        [float(frame['best_effort_timestamp_time']) for frame in before['frames']], abs=1e-6,
    )
    assert [(stream['width'], stream['height'], stream['r_frame_rate'])
            for stream in after['streams']] == [
                (stream['width'], stream['height'], stream['r_frame_rate'])
                for stream in before['streams']]
    key_times = [float(frame['best_effort_timestamp_time'])
                 for frame in after['frames'] if frame['key_frame']]
    assert len(key_times) >= 4
    assert max(right - left for left, right in zip(key_times, key_times[1:])) <= .500001
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
