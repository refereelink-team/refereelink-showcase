"""Recover pitch geometry from independently observed grass paint lines.

A keypoint model can mistake goal nets and advertisements for pitch landmarks.
This bounded bootstrap fits line-intersection topology and scores the complete
paint layout, rather than optimizing the model's own landmark residual.
"""

from __future__ import annotations
from itertools import combinations
from typing import Optional
import cv2
import numpy as np
from app.config.pitch import SoccerPitchConfiguration


class PaintLineProjection:
    def __init__(self, config: SoccerPitchConfiguration) -> None:
        self.config = config
        self.edges = np.asarray(
            [[config.vertices[a - 1], config.vertices[b - 1]] for a, b in config.edges],
            dtype=np.float64,
        )
        self.samples = np.concatenate([np.linspace(a, b, 12) for a, b in self.edges])

    @staticmethod
    def _bilinear(distance: np.ndarray, points: np.ndarray) -> np.ndarray:
        height, width = distance.shape
        x = np.clip(points[..., 0], 0, width - 1.001)
        y = np.clip(points[..., 1], 0, height - 1.001)
        xi = x.astype(np.int32)
        yi = y.astype(np.int32)
        dx = x - xi
        dy = y - yi
        return (
            distance[yi, xi] * (1 - dx) * (1 - dy)
            + distance[yi, xi + 1] * dx * (1 - dy)
            + distance[yi + 1, xi] * (1 - dx) * dy
            + distance[yi + 1, xi + 1] * dx * dy
        )

    def estimate(
        self,
        frame: np.ndarray,
        seed: Optional[np.ndarray],
        orientation_reference: Optional[np.ndarray] = None,
        excluded_boxes: Optional[np.ndarray] = None,
        *,
        paint_mask: Optional[np.ndarray] = None,
        perspective_families: bool = False,
        candidate_validator=None,
    ) -> Optional[np.ndarray]:
        height, width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        grass = cv2.inRange(hsv, np.array([25, 35, 30]), np.array([95, 255, 255]))
        near_grass = cv2.dilate(grass, np.ones((7, 7), np.uint8))
        paint = ((hsv[:, :, 1] < 90) & (hsv[:, :, 2] > 145) & (near_grass > 0)).astype(np.uint8)
        if paint_mask is not None:
            paint = (paint_mask > 0).astype(np.uint8)
        if excluded_boxes is not None:
            for x1, y1, x2, y2 in excluded_boxes:
                cv2.rectangle(paint, (int(x1) - 3, int(y1) - 3), (int(x2) + 3, int(y2) + 3), 0, -1)
        raw = cv2.HoughLinesP(
            paint * 255,
            1,
            np.pi / 720,
            threshold=15 if perspective_families else 25,
            minLineLength=max(25, width * 0.035) if perspective_families else max(45, width * 0.07),
            maxLineGap=22 if perspective_families else 14,
        )
        if raw is None:
            return None
        lines = []
        for segment in raw.reshape(-1, 4):
            a, b = segment[:2].astype(float), segment[2:].astype(float)
            length = float(np.linalg.norm(b - a))
            normal = np.array([-(b - a)[1], (b - a)[0]]) / max(length, 1e-6)
            samples = np.linspace(a, b, 30)
            support = []
            for sign in [-1, 1]:
                probes = np.round(samples + sign * normal * 5).astype(int)
                valid = (
                    (probes[:, 0] >= 0)
                    & (probes[:, 0] < width)
                    & (probes[:, 1] >= 0)
                    & (probes[:, 1] < height)
                )
                support.append(
                    float(np.mean(grass[probes[valid, 1], probes[valid, 0]] > 0))
                    if valid.any()
                    else 0.0
                )
            if (max(support) if perspective_families else min(support)) < 0.5:
                continue
            line = np.cross(np.r_[a, 1], np.r_[b, 1])
            line /= np.linalg.norm(line[:2])
            angle = float(np.arctan2((b - a)[1], (b - a)[0]))
            if any(
                abs(np.sin(angle - other[2])) < 0.04
                and abs(np.dot(line, np.r_[other[0][0], 1])) < 4
                for other in lines
            ):
                continue
            lines.append((np.array([a, b]), line, angle, length))
        lines.sort(key=lambda item: -item[3])
        if perspective_families:
            merged = []
            for item in lines:
                match = next(
                    (
                        j
                        for j, other in enumerate(merged)
                        if abs(np.sin(item[2] - other[2])) < 0.045
                        and np.max(np.abs(np.c_[item[0], np.ones(2)] @ other[1])) < 5
                    ),
                    None,
                )
                if match is None:
                    merged.append(item)
                    continue
                other = merged[match]
                points = np.concatenate([item[0], other[0]])
                direction = other[0][1] - other[0][0]
                coordinates = points @ direction
                endpoints = points[[np.argmin(coordinates), np.argmax(coordinates)]]
                merged[match] = (
                    endpoints,
                    other[1],
                    other[2],
                    float(np.linalg.norm(endpoints[1] - endpoints[0])),
                )
            lines = sorted(merged, key=lambda item: -item[3])
        if len(lines) < 4:
            return None
        first = lines[0][2]
        second = next((item[2] for item in lines if abs(np.sin(item[2] - first)) > 0.25), None)
        if second is None:
            return None
        groups = [
            [
                item
                for item in lines
                if abs(np.sin(item[2] - angle)) < (0.25 if perspective_families else 0.12)
            ][:6]
            for angle in [first, second]
        ]
        if perspective_families:
            groups = [[], []]
            for item in lines:
                differences = [abs(np.sin(item[2] - angle)) for angle in (first, second)]
                family = int(np.argmin(differences))
                if differences[family] < 0.25 and len(groups[family]) < 6:
                    groups[family].append(item)
        if min(map(len, groups)) < (2 if perspective_families else 3):
            return None
        observed = np.concatenate(
            [np.linspace(item[0][0], item[0][1], 20) for item in groups[0] + groups[1]]
        )
        distance = cv2.distanceTransform(1 - paint, cv2.DIST_L2, 3)
        c = self.config
        x_pairs = [
            (c.length - c.penalty_box_length, c.length - c.goal_box_length),
            (c.length - c.goal_box_length, c.length),
            (c.length - c.penalty_box_length, c.length),
        ]
        y_values = [
            0,
            (c.width - c.penalty_box_width) / 2,
            (c.width - c.goal_box_width) / 2,
            (c.width + c.goal_box_width) / 2,
            (c.width + c.penalty_box_width) / 2,
            c.width,
        ]
        worlds = [
            np.asarray([[x, y] for x in (xs[::-1] if reverse else xs) for y in ys], np.float32)
            for xs in x_pairs
            for ys in combinations(y_values, 2)
            for reverse in [False, True]
        ]
        candidates = []
        for family in [groups, groups[::-1]]:
            for xl in combinations(family[0], 2):
                for yl in combinations(family[1], 2):
                    corners = []
                    for x in xl:
                        for y in yl:
                            intersection = np.cross(x[1], y[1])
                            if abs(intersection[2]) < 1e-7:
                                break
                            corners.append(intersection[:2] / intersection[2])
                    if len(corners) != 4:
                        continue
                    image = np.asarray(corners, np.float32)
                    if cv2.contourArea(cv2.convexHull(image)) < 100:
                        continue
                    for world in worlds:
                        matrix = cv2.getPerspectiveTransform(world, image)
                        if np.isfinite(matrix).all() and abs(np.linalg.det(matrix)) > 1e-8:
                            candidates.append(matrix)
        if not candidates:
            return None
        # Score all hypotheses in vectorized batches. Only the best coarse
        # fits receive the more expensive bidirectional segment check.
        matrices = np.asarray(candidates)
        homogeneous = np.c_[self.samples, np.ones(len(self.samples))]
        projected = np.einsum("nij,kj->nki", matrices, homogeneous)
        denominator = projected[:, :, 2]
        safe = np.abs(denominator) > 1e-7
        pixels = projected[:, :, :2] / np.where(safe, denominator, 1)[:, :, None]
        finite = np.isfinite(pixels).all(axis=2)
        valid = (
            safe
            & finite
            & (pixels[:, :, 0] > 0)
            & (pixels[:, :, 0] < width - 1)
            & (pixels[:, :, 1] > 0)
            & (pixels[:, :, 1] < height - 1)
        )
        pixels = np.nan_to_num(pixels, nan=0, posinf=0, neginf=0)
        distances = np.minimum(self._bilinear(distance, pixels), 15)
        counts = valid.sum(axis=1)
        coarse = (distances * valid).sum(axis=1) / np.maximum(counts, 1)
        coarse[counts < 50] = 100
        if perspective_families:
            # Four intersections can collapse an entire template onto a board.
            # Require the proposed plane to cover a substantial grass region.
            grass_points = np.argwhere(grass > 0)[:, ::-1].astype(float)
            grass_points = grass_points[:: max(1, int(len(grass_points) / 80))]
            try:
                inverses = np.linalg.inv(matrices)
            except np.linalg.LinAlgError:
                return None
            mapped = np.einsum(
                "nij,kj->nki", inverses, np.c_[grass_points, np.ones(len(grass_points))]
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                field = mapped[:, :, :2] / mapped[:, :, 2:3]
            coverage = (
                (field[:, :, 0] >= 0)
                & (field[:, :, 0] <= c.length)
                & (field[:, :, 1] >= 0)
                & (field[:, :, 1] <= c.width)
            ).mean(axis=1)
            coarse[coverage < 0.35] = 100
        shortlist = np.argsort(coarse)[: 200 if perspective_families else 40]
        best_score = 100.0
        best = None
        for index in shortlist:
            matrix = matrices[index]
            if candidate_validator is not None:
                try:
                    validation = candidate_validator(np.linalg.inv(matrix))
                except np.linalg.LinAlgError:
                    continue
                if (
                    validation is not None
                    and validation["accepted"]
                    and validation["score"] < best_score
                ):
                    best_score, best = validation["score"], matrix
                continue
            edges = cv2.perspectiveTransform(self.edges.reshape(-1, 1, 2), matrix).reshape(-1, 2, 2)
            start = edges[:, 0]
            vector = edges[:, 1] - start
            t = np.clip(
                np.sum((observed[:, None] - start) * vector, axis=2)
                / np.maximum(np.sum(vector * vector, axis=1), 1e-9),
                0,
                1,
            )
            errors = np.linalg.norm(
                observed[:, None] - (start + t[:, :, None] * vector), axis=2
            ).min(axis=1)
            # Verify both nested rectangles; midfield circle tangents cannot
            # masquerade as a penalty area solely through four intersections.
            nested = [
                [
                    [c.length - c.penalty_box_length, y_values[1]],
                    [c.length - c.penalty_box_length, y_values[4]],
                ],
                [
                    [c.length - c.goal_box_length, y_values[2]],
                    [c.length - c.goal_box_length, y_values[3]],
                ],
                [[c.length - c.goal_box_length, y_values[2]], [c.length, y_values[2]]],
                [[c.length - c.goal_box_length, y_values[3]], [c.length, y_values[3]]],
            ]
            support = []
            for start_world, end_world in nested:
                probes = cv2.perspectiveTransform(
                    np.linspace(start_world, end_world, 30).reshape(-1, 1, 2), matrix
                ).reshape(-1, 2)
                visible = (
                    (probes[:, 0] > 0)
                    & (probes[:, 0] < width - 1)
                    & (probes[:, 1] > 0)
                    & (probes[:, 1] < height - 1)
                )
                visible_probes = probes[visible]
                enough = (
                    len(visible_probes) >= 6
                    and np.linalg.norm(np.ptp(visible_probes, axis=0)) >= 35
                )
                support.append(
                    enough and float(np.mean(self._bilinear(distance, visible_probes) < 5)) >= 0.55
                )
            if not (support[0] and support[1] and support[2] and support[3]):
                continue
            score = float(coarse[index] + np.mean(np.minimum(errors, 15)))
            if score < best_score:
                best_score, best = score, matrix
        if best is None or best_score > (15.0 if candidate_validator is not None else 8.0):
            return None
        try:
            image_to_world = np.linalg.inv(best)
        except np.linalg.LinAlgError:
            return None
        # Pitch paint is symmetric. Preserve the established field identity
        # using the previous accepted transform, never the held-out landmarks.
        reference = orientation_reference if orientation_reference is not None else seed
        if reference is not None:
            target = cv2.perspectiveTransform(observed.reshape(-1, 1, 2), reference).reshape(-1, 2)
            options = []
            for x_flip in [False, True]:
                for y_flip in [False, True]:
                    mirror = np.array(
                        [
                            [-1 if x_flip else 1, 0, c.length if x_flip else 0],
                            [0, -1 if y_flip else 1, c.width if y_flip else 0],
                            [0, 0, 1],
                        ],
                        dtype=float,
                    )
                    option = mirror @ image_to_world
                    mapped = cv2.perspectiveTransform(observed.reshape(-1, 1, 2), option).reshape(
                        -1, 2
                    )
                    options.append(
                        (
                            float(np.median(np.linalg.norm(mapped - target, axis=1))),
                            option,
                        )
                    )
            image_to_world = min(options, key=lambda item: item[0])[1]
        image_to_world /= image_to_world[2, 2]
        return image_to_world
