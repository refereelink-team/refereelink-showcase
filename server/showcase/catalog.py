from __future__ import annotations
import json
from pathlib import Path
from fastapi import HTTPException


CLIPS = (
    {'id': 'calibration', 'title': '赛前标定片段', 'filename': '标定用视频.mp4', 'decoded_frames': 579, 'duration_seconds': 19.313},
    {'id': 'foul-1', 'title': '实时犯规 · 片段一', 'filename': '实时犯规1.mp4', 'decoded_frames': 175, 'duration_seconds': 5.842},
    {'id': 'foul-2', 'title': '实时犯规 · 片段二', 'filename': '实时犯规2.mp4', 'decoded_frames': 189, 'duration_seconds': 6.331},
    {'id': 'tracking-projection', 'title': '场地跟踪与投影', 'filename': '跟踪与投影.mp4', 'decoded_frames': 682, 'duration_seconds': 22.729,
     # Source-informed configuration; this is not a surveyed venue measurement.
     'pitch_profile_id': 'source-informed105',
     'profile_source_sha256': 'a15b2942f4c50cd6c41774ccae3923cd0327fb22d35626c1ddba6dd6c84fe534'},
)


class MediaCatalog:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def safe_path(self, relative: str) -> Path:
        target = (self.root / relative).resolve()
        if not target.is_relative_to(self.root):
            raise HTTPException(404, 'Media not found')
        if not target.is_file():
            raise HTTPException(404, 'Media not found')
        return target

    def clip(self, clip_id: str) -> dict:
        value = next((item for item in CLIPS if item['id'] == clip_id), None)
        if value is None:
            raise HTTPException(404, 'Unknown presentation clip')
        return value

    def artifacts(self) -> list[dict]:
        path = self.root / 'artifacts.json'
        if not path.exists():
            return []
        try:
            values = json.loads(path.read_text())
        except (OSError, ValueError):
            return []
        result = []
        for item in values:
            try:
                self.safe_path(item['path'])
                if item['case_id'] not in {clip['id'] for clip in CLIPS}:
                    continue
                result.append(item)
            except (KeyError, TypeError, HTTPException):
                continue
        return result

    def public_artifact(self, artifact: dict) -> dict:
        return {**{key: value for key, value in artifact.items() if key != 'path'},
                'url': '/api/experiments/artifacts/' + artifact['id']}

    def artifact(self, artifact_id: str) -> dict:
        result = next((item for item in self.artifacts() if item['id'] == artifact_id), None)
        if result is None:
            raise HTTPException(404, 'Artifact not ready')
        return result

    def clips(self) -> list[dict]:
        artifacts = self.artifacts()
        return [
            {**clip, 'source_url': '/media/input/' + clip['id'] + '.mp4',
             'poster_url': '/media/posters/' + clip['id'] + '.jpg',
             'artifacts': [self.public_artifact(item) for item in artifacts if item['case_id'] == clip['id']]}
            for clip in CLIPS
        ]
