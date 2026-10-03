"""Independently validate and track a pitch plane using visible field markings.

Model landmarks propose identities; grass-supported lines and curves validate
them. A single accepted coordinate system is transported between adjacent
frames. Loss of evidence never restores an unrelated model matrix.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np

from app.config.pitch import SoccerPitchConfiguration
from app.geometry.pitch_lines import PaintLineProjection


@dataclass
class RegistrationResult:
    homography: np.ndarray | None
    status: str
    coordinate_transition: bool
    quality: dict
    epoch: int


def project(points, matrix):
    homogeneous = np.c_[np.asarray(points).reshape(-1, 2), np.ones(len(points))]
    mapped = homogeneous @ matrix.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return mapped[:, :2] / mapped[:, 2:3]


class FieldPaintEvidence:
    """Extract real paint, retaining one-sided grass support at pitch boundaries."""

    def __init__(self, frame, boxes=None):
        self.height, self.width = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        grass = cv2.inRange(hsv, (25, 30, 25), (95, 255, 255))
        grass = cv2.morphologyEx(grass, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(grass)
        if count > 1:
            areas = stats[1:, cv2.CC_STAT_AREA]
            significant = 1 + np.flatnonzero(
                areas >= max(float(areas.max()) * 0.08, frame.shape[0] * frame.shape[1] * 0.005)
            )
            # A thick white halfway line can split the pitch into two regions.
            # Retain substantial field components instead of deleting one half.
            grass = np.isin(labels, significant).astype(np.uint8) * 255
        self.grass = grass
        self.allowed = np.full(grass.shape, 255, np.uint8)
        if boxes is not None:
            for x1, y1, x2, y2 in np.asarray(boxes).reshape(-1, 4):
                cv2.rectangle(
                    self.allowed, (int(x1) - 5, int(y1) - 5), (int(x2) + 5, int(y2) + 5), 0, -1
                )
        probes = np.argwhere((grass > 0) & (self.allowed > 0))[:, ::-1]
        self.plane_probes = probes[:: max(1, math.ceil(len(probes) / 200))]
        value = hsv[:, :, 2].astype(np.float32)
        background = cv2.GaussianBlur(value, (21, 21), 0)
        adjacent = cv2.dilate(grass, np.ones((9, 9), np.uint8)) > 0
        # Remove narrow grass-colored board fragments connected to the field.
        # The opened region retains a boundary halo, including one-sided paint.
        interior = cv2.morphologyEx(grass, cv2.MORPH_OPEN, np.ones((17, 17), np.uint8))
        adjacent &= cv2.dilate(interior, np.ones((7, 7), np.uint8)) > 0
        threshold = max(90.0, float(np.median(value[grass > 0])) * 1.15) if np.any(grass) else 145.0
        paint = adjacent & (hsv[:, :, 1] < 100) & (value > threshold) & (value - background > 7)
        stroke = cv2.morphologyEx(
            paint.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
        )
        self.paint = (stroke & (self.allowed > 0)).astype(np.uint8)
        self.distance = cv2.distanceTransform(1 - self.paint, cv2.DIST_L2, 3)
        raw = cv2.HoughLinesP(
            self.paint * 255,
            1,
            np.pi / 720,
            threshold=20,
            minLineLength=max(30, self.width * 0.045),
            maxLineGap=16,
        )
        self.lines = []
        if raw is not None:
            for endpoints in raw.reshape(-1, 4):
                a, b = endpoints[:2].astype(float), endpoints[2:].astype(float)
                length = np.linalg.norm(b - a)
                tangent = (b - a) / max(length, 1)
                normal = np.array([-tangent[1], tangent[0]])
                samples = np.linspace(a, b, 20)
                side = []
                for sign in (-1, 1):
                    xy = np.round(samples + sign * normal * 6).astype(int)
                    xy[:, 0] = np.clip(xy[:, 0], 0, self.width - 1)
                    xy[:, 1] = np.clip(xy[:, 1], 0, self.height - 1)
                    side.append(np.mean(grass[xy[:, 1], xy[:, 0]] > 0))
                if max(side) < 0.6:
                    continue
                line = np.cross(np.r_[a, 1], np.r_[b, 1])
                line /= np.linalg.norm(line[:2])
                if any(
                    abs(np.dot(tangent, other["tangent"])) > 0.995
                    and abs(np.dot(line, np.r_[other["a"], 1])) < 4
                    for other in self.lines
                ):
                    continue
                self.lines.append(
                    {
                        "a": a,
                        "b": b,
                        "tangent": tangent,
                        "line": line,
                        "length": float(length),
                        "boundary": min(side) < 0.5,
                    }
                )
        self.lines.sort(key=lambda item: -item["length"])
        pixels = np.argwhere(self.paint > 0)[:, ::-1].astype(float)
        self.observed = pixels[:: max(1, math.ceil(len(pixels) / 500))]

    def distances(self, points):
        points = np.asarray(points)
        x = np.clip(points[:, 0], 0, self.width - 1.001)
        y = np.clip(points[:, 1], 0, self.height - 1.001)
        xi, yi = x.astype(int), y.astype(int)
        dx, dy = x - xi, y - yi
        values = (
            (1 - dx) * (1 - dy) * self.distance[yi, xi]
            + dx * (1 - dy) * self.distance[yi, xi + 1]
            + (1 - dx) * dy * self.distance[yi + 1, xi]
            + dx * dy * self.distance[yi + 1, xi + 1]
        )
        outside = np.linalg.norm(points - np.c_[x, y], axis=1)
        return values + outside

    def unoccluded(self, points):
        xy = np.round(points).astype(int)
        inside = (
            (xy[:, 0] >= 1)
            & (xy[:, 0] < self.width - 1)
            & (xy[:, 1] >= 1)
            & (xy[:, 1] < self.height - 1)
        )
        result = np.zeros(len(points), bool)
        result[inside] = self.allowed[xy[inside, 1], xy[inside, 0]] > 0
        return result


class FieldLineRegistration:
    def __init__(self, config: SoccerPitchConfiguration, fps: float):
        self.config, self.fps = config, float(fps)
        self.bootstrap = PaintLineProjection(config)
        # Merge subdivided collinear template edges: scoring weights follow length.
        c = config
        lo, hi = (c.width - c.penalty_box_width) / 2, (c.width + c.penalty_box_width) / 2
        gl, gh = (c.width - c.goal_box_width) / 2, (c.width + c.goal_box_width) / 2
        segments = [
            ((0, 0), (c.length, 0)),
            ((0, c.width), (c.length, c.width)),
            ((0, 0), (0, c.width)),
            ((c.length, 0), (c.length, c.width)),
            ((c.length / 2, 0), (c.length / 2, c.width)),
        ]
        for end, sign in ((0, 1), (c.length, -1)):
            for depth, lower, upper in (
                (c.penalty_box_length, lo, hi),
                (c.goal_box_length, gl, gh),
            ):
                x = end + sign * depth
                segments += [
                    ((x, lower), (x, upper)),
                    ((end, lower), (x, lower)),
                    ((end, upper), (x, upper)),
                ]
        self.curves = [np.array(segment, float) for segment in segments]
        theta = np.linspace(0, 2 * np.pi, 121)
        self.curves.append(
            np.c_[
                c.length / 2 + c.centre_circle_radius * np.cos(theta),
                c.width / 2 + c.centre_circle_radius * np.sin(theta),
            ]
        )
        for end, sign in ((0, 1), (c.length, -1)):
            center = end + sign * c.penalty_spot_distance
            ratio = (c.penalty_box_length - c.penalty_spot_distance) / c.centre_circle_radius
            if abs(ratio) < 1:
                angle = np.arccos(ratio)
                arc_theta = np.linspace(-angle, angle, 41) + (0 if sign == 1 else np.pi)
                self.curves.append(
                    np.c_[
                        center + c.centre_circle_radius * np.cos(arc_theta),
                        c.width / 2 + c.centre_circle_radius * np.sin(arc_theta),
                    ]
                )
        self.samples = np.concatenate(
            [
                np.linspace(a, b, max(2, math.ceil(np.linalg.norm(b - a) / 100)), endpoint=False)
                for curve in self.curves
                for a, b in zip(curve, curve[1:])
            ]
        )
        self.homography = None
        self.epoch = 0
        self.previous_gray = None
        self.previous_mask = None
        self.previous_frame = None
        self.last_fit = -10000
        self.last_attempt = -10000
        self.last_bootstrap = -10000
        self.transport_error = 0.0
        self.last_quality = {}
        self.record_history = False
        self.records = []

    def _motion(self, frame, evidence):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mask = cv2.bitwise_and(evidence.grass, evidence.allowed)
        previous, previous_mask = self.previous_gray, self.previous_mask
        self.previous_gray, self.previous_mask = gray, mask
        if previous is None or previous.shape != gray.shape:
            return None, None
        points = cv2.goodFeaturesToTrack(previous, 240, 0.01, 9, mask=previous_mask)
        if points is None or len(points) < 12:
            return None, None
        current, valid, _ = cv2.calcOpticalFlowPyrLK(previous, gray, points, None)
        back, valid_back, _ = cv2.calcOpticalFlowPyrLK(gray, previous, current, None)
        fb = np.linalg.norm(points.reshape(-1, 2) - back.reshape(-1, 2), axis=1)
        keep = (valid.ravel() > 0) & (valid_back.ravel() > 0) & (fb < 1.2)
        destination = np.round(current.reshape(-1, 2)).astype(int)
        inside = (
            (destination[:, 0] >= 0)
            & (destination[:, 0] < gray.shape[1])
            & (destination[:, 1] >= 0)
            & (destination[:, 1] < gray.shape[0])
        )
        allowed = np.zeros(len(keep), bool)
        allowed[inside] = mask[destination[inside, 1], destination[inside, 0]] > 0
        keep &= allowed
        source, target = points.reshape(-1, 2)[keep], current.reshape(-1, 2)[keep]
        if len(source) < 12:
            return None, None
        transform, inliers = cv2.findHomography(source, target, cv2.RANSAC, 2.0)
        if transform is None or inliers is None:
            return None, None
        good = inliers.ravel() > 0
        hull = cv2.contourArea(cv2.convexHull(source[good])) if good.sum() >= 3 else 0
        residual = np.linalg.norm(project(source[good], transform) - target[good], axis=1)
        error = float(np.median(residual))
        if good.sum() < 12 or good.mean() < 0.65 or hull < gray.size * 0.025 or error > 1.0:
            return None, None
        probes = np.array(
            [[0, 0], [gray.shape[1], 0], [0, gray.shape[0]], [gray.shape[1], gray.shape[0]]], float
        )
        moved = project(probes, transform)
        if (
            not np.isfinite(moved).all()
            or np.max(np.linalg.norm(moved - probes, axis=1)) > frame.shape[1] * 0.2
        ):
            return None, None
        return transform, {"motion_inliers": int(good.sum()), "motion_error_px": error}

    def _score(self, evidence, image_to_world):
        if not self._valid_plane(evidence, image_to_world):
            return None
        try:
            matrix = np.linalg.inv(image_to_world)
        except np.linalg.LinAlgError:
            return None
        pixels = project(self.samples, matrix)
        finite = np.isfinite(pixels).all(axis=1)
        pixels = np.nan_to_num(pixels, nan=-1e6, posinf=-1e6, neginf=-1e6)
        visible = finite & evidence.unoccluded(pixels)
        if visible.sum() < 40:
            return None
        distances = evidence.distances(pixels[visible])
        support = distances < max(4.0, evidence.width * 0.005)
        points = pixels[visible][support]
        hull = cv2.contourArea(cv2.convexHull(points.astype(np.float32))) if len(points) >= 3 else 0
        if len(points) < 25 or hull < evidence.width * evidence.height * 0.009:
            return None
        template = np.full((evidence.height, evidence.width), 255, np.uint8)
        line_directions = []
        families = set()
        family_coordinates = {"x": set(), "y": set()}
        supported_segments = 0
        supported_ids = []
        for index, curve in enumerate(self.curves):
            image = project(curve, matrix)
            if not np.isfinite(image).all() or np.max(np.abs(image)) > 1e6:
                continue
            cv2.polylines(template, [np.round(image).astype(np.int32)], False, 0, 1)
            clipped = []
            for a, b in zip(image, image[1:]):
                valid, start, end = cv2.clipLine(
                    (0, 0, evidence.width, evidence.height),
                    tuple(np.round(a).astype(int)),
                    tuple(np.round(b).astype(int)),
                )
                if valid:
                    clipped.append(
                        np.linspace(
                            start,
                            end,
                            max(2, math.ceil(np.linalg.norm(np.asarray(end) - start) / 5)),
                        )
                    )
            if not clipped:
                continue
            samples = np.concatenate(clipped)
            usable = evidence.unoccluded(samples)
            if usable.sum() < 8:
                continue
            distance = evidence.distances(samples[usable])
            matched = samples[usable][distance < 4]
            minimum_fraction = 0.25 if index < 17 else 0.6
            if (
                len(matched) >= 8
                and np.mean(distance < 4) >= minimum_fraction
                and np.linalg.norm(np.ptp(matched, axis=0)) >= 60
            ):
                supported_segments += 1
                supported_ids.append(index)
                if index < 17:
                    family = "x" if abs(curve[-1, 0] - curve[0, 0]) < 1 else "y"
                    families.add(family)
                    family_coordinates[family].add(float(curve[0, 0 if family == "x" else 1]))
                    direction = image[-1] - image[0]
                    line_directions.append(direction / max(np.linalg.norm(direction), 1))
        reverse_distance = cv2.distanceTransform(template, cv2.DIST_L2, 3)
        observed = evidence.observed.astype(int)
        reverse = (
            reverse_distance[observed[:, 1], observed[:, 0]] if len(observed) else np.array([100.0])
        )
        directions = any(
            abs(a[0] * b[1] - a[1] * b[0]) > 0.15 for a in line_directions for b in line_directions
        )
        independent_shape = min(map(len, family_coordinates.values())) >= 2 or (
            len(line_directions) >= 2 and any(i >= 17 for i in supported_ids)
        )
        # Three infinite lines leave projective degrees of freedom unresolved.
        accepted = (
            independent_shape
            and directions
            and len(families) == 2
            and supported_segments >= 3
            and support.mean() >= 0.55
            and np.mean(reverse < 6) >= 0.3
            and np.median(distances) < 4
        )
        score = float(np.mean(np.minimum(distances, 15)) + np.mean(np.minimum(reverse, 15)))
        return {
            "accepted": bool(accepted),
            "line_error_px": float(np.median(distances)),
            "line_support_fraction": float(support.mean()),
            "paint_coverage": float(np.mean(reverse < 6)),
            "supported_segments": int(supported_segments),
            "support_area_fraction": float(hull / (evidence.width * evidence.height)),
            "supported_ids": supported_ids,
            "score": score,
        }

    def _valid_plane(self, evidence, matrix):
        if matrix is None or np.shape(matrix) != (3, 3) or not np.isfinite(matrix).all():
            return False
        normalized = (
            np.diag([1 / self.config.length, 1 / self.config.width, 1.0])
            @ matrix
            @ np.diag([evidence.width, evidence.height, 1.0])
        )
        if np.linalg.cond(normalized) > 1e5:
            return False
        pixels = evidence.plane_probes
        if len(pixels) < 20:
            return False
        denominator = np.c_[pixels, np.ones(len(pixels))] @ matrix[2]
        if (
            np.min(denominator) * np.max(denominator) <= 0
            or np.min(np.abs(denominator)) < np.max(np.abs(denominator)) * 0.02
        ):
            return False
        # The source-informed clip uses a consistent longitudinal direction.
        # Symmetric paint cannot infer a team's defending end.
        center = np.mean(pixels, axis=0)
        axes = project([center, center + [10, 0], center + [0, 10]], matrix)
        return bool(np.isfinite(axes).all() and axes[1, 0] < axes[0, 0] and axes[2, 1] > axes[0, 1])

    def _orient(self, matrix, evidence):
        center = np.array([evidence.width / 2, evidence.height * 0.6])
        axes = project([center, center + [10, 0], center + [0, 10]], matrix)
        if not np.isfinite(axes).all():
            return matrix
        x = (
            np.array([[-1, 0, self.config.length], [0, 1, 0], [0, 0, 1]])
            if axes[1, 0] > axes[0, 0]
            else np.eye(3)
        )
        y = (
            np.array([[1, 0, 0], [0, -1, self.config.width], [0, 0, 1]])
            if axes[2, 1] < axes[0, 1]
            else np.eye(3)
        )
        return y @ x @ matrix

    def _refine(self, evidence, seed, constraints=None):
        if seed is None or not np.isfinite(seed).all():
            return None
        try:
            inverse = np.linalg.inv(seed)
        except np.linalg.LinAlgError:
            return None
        pixels = project(self.samples, inverse)
        finite = np.isfinite(pixels).all(axis=1)
        pixels = np.nan_to_num(pixels, nan=-1e6, posinf=-1e6, neginf=-1e6)
        chosen = finite & evidence.unoccluded(pixels) & (evidence.distances(pixels) < 12)
        if chosen.sum() < 30:
            return None
        world = self.samples[chosen][:: max(1, int(chosen.sum() / 300))]
        anchors = np.array([[0.1, 0.2], [0.9, 0.2], [0.9, 0.9], [0.1, 0.9]], float)
        scale = np.array([evidence.width, evidence.height], float)
        from scipy.optimize import least_squares

        if constraints is not None:
            circle_pixels, halfway_line, touchline_line = constraints
            c = self.config
            normalizer = np.array(
                [
                    [1 / c.centre_circle_radius, 0, -c.length / (2 * c.centre_circle_radius)],
                    [0, 1 / c.centre_circle_radius, -c.width / (2 * c.centre_circle_radius)],
                    [0, 0, 1],
                ]
            )
            world_conic = normalizer.T @ np.diag([1.0, 1.0, -1.0]) @ normalizer
            circle_homogeneous = np.c_[circle_pixels, np.ones(len(circle_pixels))]
            halfway_world = np.c_[
                np.full(7, c.length / 2),
                np.linspace(
                    c.width / 2 - c.centre_circle_radius, c.width / 2 + c.centre_circle_radius, 7
                ),
            ]
            touchline_world = np.c_[
                np.linspace(
                    c.length / 2 - c.centre_circle_radius * 3,
                    c.length / 2 + c.centre_circle_radius * 3,
                    7,
                ),
                np.zeros(7),
            ]
            data_length = len(circle_pixels) + 14
        else:
            data_length = len(world)

        def matrix(parameters):
            destination = anchors + parameters.reshape(4, 2)
            edges = np.roll(destination, -1, axis=0) - destination
            turns = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
            if np.any(turns <= 0.02):
                raise np.linalg.LinAlgError("Non-convex refinement bounds")
            equations, targets = [], []
            for (x, y), (u, v) in zip(anchors, destination):
                equations.extend(
                    ([x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y])
                )
                targets.extend((u, v))
            normalized = np.r_[np.linalg.solve(equations, targets), 1.0].reshape(3, 3)
            scaling = np.diag(np.r_[scale, 1.0])
            delta = scaling @ normalized @ np.linalg.inv(scaling)
            return delta @ inverse

        def residual(parameters):
            try:
                candidate = matrix(parameters)
                if constraints is not None:
                    image_to_world = np.linalg.inv(candidate)
                    image_conic = image_to_world.T @ world_conic @ image_to_world
                    values = circle_homogeneous @ image_conic.T
                    circle_distance = np.sum(values * circle_homogeneous, axis=1) / np.maximum(
                        2 * np.linalg.norm(values[:, :2], axis=1), 1e-9
                    )
                    halfway_distance = (
                        np.c_[project(halfway_world, candidate), np.ones(7)] @ halfway_line
                    )
                    touchline_distance = (
                        np.c_[project(touchline_world, candidate), np.ones(7)] @ touchline_line
                    )
                    return np.r_[
                        circle_distance,
                        halfway_distance * 3,
                        touchline_distance * 3,
                        parameters * 0.5,
                    ]
                image = project(world, candidate)
            except np.linalg.LinAlgError:
                return np.full(data_length + 8, 1e4)
            image = np.nan_to_num(image, nan=-1e4, posinf=-1e4, neginf=-1e4)
            return np.r_[evidence.distances(image), parameters * 0.5]

        def jacobian(parameters):
            step = 0.0005
            identity = np.eye(8) * step
            return np.column_stack(
                [
                    (residual(parameters + offset) - residual(parameters - offset)) / (2 * step)
                    for offset in identity
                ]
            )

        result = least_squares(
            residual,
            np.zeros(8),
            jac=jacobian,
            bounds=(-0.22, 0.22),
            loss="soft_l1",
            f_scale=3.0,
            max_nfev=45,
        )
        if not np.isfinite(result.x).all():
            return None
        try:
            candidate = matrix(result.x)
        except np.linalg.LinAlgError:
            return None
        denominator = np.c_[world, np.ones(len(world))] @ candidate[2]
        if np.any(np.abs(denominator) < 1e-5) or np.min(denominator) * np.max(denominator) <= 0:
            return None
        data_jacobian = result.jac[:data_length]
        singular = np.linalg.svd(data_jacobian, compute_uv=False)
        if len(singular) < 8 or singular[-1] < singular[0] * 1e-5:
            return None
        try:
            refined = np.linalg.inv(candidate)
            return refined if self._valid_plane(evidence, refined) else None
        except np.linalg.LinAlgError:
            return None

    def _ellipse_seeds(self, evidence, seed):
        """Recover midfield from a circle, its diameter and a touchline.

        A circle alone is ambiguous. Disk-preserving projective transforms
        jointly align the halfway chord and its touchline intersection.
        """
        if seed is None:
            return []
        try:
            predicted = project(self.curves[17], np.linalg.inv(seed))
        except np.linalg.LinAlgError:
            return []
        if not np.isfinite(predicted).all():
            return []
        curved = evidence.paint.copy() * 255
        for line in evidence.lines:
            if line["length"] > evidence.width * 0.22:
                cv2.line(curved, tuple(line["a"].astype(int)), tuple(line["b"].astype(int)), 0, 6)
        points = np.argwhere(curved > 0)[:, ::-1].astype(np.float32)
        lower = predicted.min(axis=0) - [evidence.width * 0.15, evidence.height * 0.17]
        upper = predicted.max(axis=0) + [evidence.width * 0.15, evidence.height * 0.17]
        grass_rows = np.argwhere(evidence.grass > 0)[:, 0]
        if len(grass_rows):
            lower[1] = max(lower[1], np.percentile(grass_rows, 2) + evidence.height * 0.05)
        points = points[((points >= lower) & (points <= upper)).all(axis=1)]
        points = points[:: max(1, math.ceil(len(points) / 1200))]
        if len(points) < 40:
            return []
        best = None
        rng = np.random.default_rng(0)
        for _ in range(450):
            try:
                ellipse = cv2.fitEllipse(points[rng.choice(len(points), 10, replace=False)])
            except cv2.error:
                continue
            center, axes, angle = ellipse
            if (
                min(axes) < 25
                or max(axes) < 110
                or max(axes) > evidence.width * 2
                or max(axes) / min(axes) > 15
            ):
                continue
            angle = np.deg2rad(angle)
            rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
            normalized = (points - np.asarray(center)) @ rotation / (np.asarray(axes) / 2)
            distance = np.abs(np.linalg.norm(normalized, axis=1) - 1) * min(axes) / 2
            support = distance < 1.8
            angles = np.arctan2(normalized[:, 1], normalized[:, 0])
            coverage = len(np.unique(np.floor((angles[support] + np.pi) / (2 * np.pi) * 24)))
            if coverage >= 8 and support.sum() >= 50 and (best is None or support.sum() > best[0]):
                best = int(support.sum()), points[support]
        if best is None:
            return []
        center, axes, angle = cv2.fitEllipse(best[1])
        angle = np.deg2rad(angle)
        rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        basis = rotation @ np.diag(np.asarray(axes) / 2)
        normalize = np.eye(3)
        normalize[:2, :2] = np.linalg.inv(basis)
        normalize[:2, 2] = -normalize[:2, :2] @ np.asarray(center)
        proposals = []
        c = self.config
        for halfway in evidence.lines[:12]:
            if halfway["length"] < 100:
                continue
            chord = np.linalg.inv(normalize).T @ halfway["line"]
            chord /= np.linalg.norm(chord[:2])
            d = -chord[2]
            if abs(d) >= 0.8:
                continue
            n = chord[:2]
            tangent = np.array([-n[1], n[0]])
            if (basis @ tangent)[1] < 0:
                tangent = -tangent
            if (basis @ n)[0] < 0:
                n, d = -n, -d
            boost = (
                np.array(
                    [
                        [n[0], n[1], -d],
                        [tangent[0] * np.sqrt(1 - d * d), tangent[1] * np.sqrt(1 - d * d), 0],
                        [-d * n[0], -d * n[1], 1],
                    ]
                )
                @ normalize
            )
            external = []
            for touchline in evidence.lines[:12]:
                if (
                    touchline["length"] < 100
                    or abs(np.dot(halfway["tangent"], touchline["tangent"])) > 0.95
                ):
                    continue
                intersection = np.cross(halfway["line"], touchline["line"])
                if abs(intersection[2]) < 1e-6:
                    continue
                image = intersection[:2] / intersection[2]
                if image[1] >= center[1] - min(axes) / 3:
                    continue
                circle_line = np.linalg.inv(normalize).T @ touchline["line"]
                if abs(circle_line[2]) / np.linalg.norm(circle_line[:2]) < 1.3:
                    continue
                external.append((image[1], image, touchline["line"]))
            # The closest external painted line separates the playing surface
            # from parallel advertisement borders farther beyond the pitch.
            if not external:
                continue
            for _, image, touchline_equation in sorted(external, key=lambda item: -item[0])[:1]:
                normalized = project([image], boost)[0]
                target = -c.width / (2 * c.centre_circle_radius)
                t = (normalized[1] - target) / (1 - target * normalized[1])
                if not np.isfinite(t) or abs(t) >= 0.8:
                    continue
                vertical = np.array([[np.sqrt(1 - t * t), 0, 0], [0, 1, -t], [0, -t, 1]])
                world = np.array(
                    [
                        [c.centre_circle_radius, 0, c.length / 2],
                        [0, c.centre_circle_radius, c.width / 2],
                        [0, 0, 1],
                    ]
                )
                candidate = self._orient(world @ vertical @ boost, evidence)
                constrained = self._refine(
                    evidence, candidate, (best[1], halfway["line"], touchline_equation)
                )
                if constrained is not None:
                    candidate = constrained
                score = self._score(evidence, candidate)
                if score is not None:
                    proposals.append((score["score"], candidate))
        return [candidate for _, candidate in sorted(proposals, key=lambda item: item[0])[:4]]

    def update(self, frame, seed, boxes, frame_index):
        evidence = FieldPaintEvidence(frame, boxes)
        continuity = self.previous_frame is not None and frame_index == self.previous_frame + 1
        discontinuity = not continuity and self.previous_frame is not None
        if not continuity and self.previous_frame is not None:
            self.homography = None
            self.previous_gray = None
            self.last_attempt = -10000
            self.last_bootstrap = -10000
            self.last_fit = -10000
            self.transport_error = 0.0
            self.last_quality = {}
            self.epoch += 1
        self.previous_frame = frame_index
        motion, motion_quality = self._motion(frame, evidence)
        previous = self.homography
        lost = previous is not None and motion is None
        transported = None
        if previous is not None and motion is not None:
            transported = previous @ np.linalg.inv(motion)
            if not self._valid_plane(evidence, transported):
                transported = None
            self.transport_error = math.hypot(
                self.transport_error, motion_quality["motion_error_px"]
            )
        elif previous is not None:
            self.homography = None
            self.epoch += 1
        candidate, quality = None, None
        interval = max(1, round(self.fps * 0.2))
        if frame_index - self.last_attempt >= interval:
            self.last_attempt = frame_index
            proposals = []
            if transported is not None:
                proposals.append((transported, True))
            elif seed is not None:
                proposals.append((self._orient(seed, evidence), True))
                proposals += [(matrix, False) for matrix in self._ellipse_seeds(evidence, seed)]
            for proposal, refine in proposals:
                refined = self._refine(evidence, proposal) if refine else proposal
                if refined is None:
                    continue
                score = self._score(evidence, refined)
                if score is not None and score["accepted"]:
                    score["structured_circle_fit"] = not refine
                    if (
                        quality is None
                        or score["structured_circle_fit"]
                        and not quality.get("structured_circle_fit")
                        or score["structured_circle_fit"]
                        == bool(quality.get("structured_circle_fit"))
                        and score["score"] < quality["score"]
                    ):
                        candidate, quality = refined, score
            if candidate is None and frame_index - self.last_bootstrap >= self.fps:
                self.last_bootstrap = frame_index

                def validate_nested(matrix):
                    score = self._score(evidence, self._orient(matrix, evidence))
                    if score is not None:
                        supported = set(score["supported_ids"])
                        score["accepted"] &= (
                            ({5, 8} <= supported or {11, 14} <= supported)
                            and score["line_support_fraction"] >= 0.75
                            and score["line_error_px"] <= 2
                        )
                    return score

                nested = self.bootstrap.estimate(
                    frame,
                    seed,
                    transported,
                    boxes,
                    paint_mask=evidence.paint,
                    perspective_families=True,
                    candidate_validator=validate_nested,
                )
                if nested is not None:
                    nested = self._orient(nested, evidence)
                    score = self._score(evidence, nested)
                    if score is not None and score["accepted"]:
                        candidate, quality = nested, score
        transition = discontinuity
        source = "unavailable"
        if candidate is not None:
            if transported is not None:
                feet = np.array(
                    [[(b[0] + b[2]) / 2, b[3]] for b in np.asarray(boxes).reshape(-1, 4)]
                )
                if len(feet):
                    deviation = np.linalg.norm(
                        project(feet, candidate) - project(feet, transported), axis=1
                    )
                    transition |= (
                        np.median(deviation[np.isfinite(deviation)]) > 200
                        if np.any(np.isfinite(deviation))
                        else True
                    )
            elif previous is None or lost:
                transition |= self.last_fit > -10000
            if transition and not discontinuity and not lost:
                self.epoch += 1
            self.homography = candidate
            self.last_fit = frame_index
            self.transport_error = 0.0
            self.last_quality = quality
            source = "field_lines"
            status = "fresh"
        elif (
            transported is not None
            and frame_index - self.last_fit <= self.fps * 16
            and self.transport_error <= 6
        ):
            self.homography = transported
            source = "transport"
            status = "reused"
        else:
            if self.homography is not None:
                self.epoch += 1
            self.homography = None
            status = "unavailable"
        diagnostics = {
            **self.last_quality,
            **(motion_quality or {}),
            "accepted": self.homography is not None,
            "source": source,
            "geometry_epoch": self.epoch,
            "transport_age_frames": max(0, frame_index - self.last_fit),
            "transport_error_px": self.transport_error,
            "observed_lines": len(evidence.lines),
        }
        if self.record_history:
            self.records.append(
                {
                    "frame": frame_index,
                    "motion": motion,
                    "grass_probes": evidence.plane_probes.copy(),
                    "dimensions": (evidence.width, evidence.height),
                    "homography": None if self.homography is None else self.homography.copy(),
                    "boxes": np.asarray(boxes, dtype=float).reshape(-1, 4).copy(),
                    "quality": dict(diagnostics),
                    "epoch": self.epoch,
                    "transition": bool(transition),
                }
            )
        return RegistrationResult(
            self.homography, status, bool(transition), diagnostics, self.epoch
        )


class OfflinePaintCorrector:
    """Correct short motion chains against current paint without changing field identity.

    The four-parameter image similarity preserves the metric plane supplied by
    an independently validated anchor. It cannot invent a new projective
    calibration from an underconstrained view. Missing paint eventually ends
    propagation instead of allowing an indefinitely drifting coordinate map.
    """

    def __init__(self, evidence, config, interval):
        self.evidence = evidence
        self.registration = FieldLineRegistration(config, 30.0)
        self.interval = interval
        self.diagnostics = {}
        self.summary = {"cached_paint_frames": len(evidence), "successful_corrections": 0}
        self.last_supported = None
        self.last_index = None

    def __call__(self, index, homography):
        if self.last_index is None or abs(index - self.last_index) != 1:
            self.last_supported = index
        self.last_index = index
        if (
            self.last_supported is not None
            and abs(index - self.last_supported) > self.interval * 12
        ):
            self.diagnostics[index] = {"accepted": False, "reason": "paint_support_expired"}
            return None
        evidence = self.evidence.get(index)
        if evidence is None:
            return homography
        # Circle + diameter + touchline or a nested penalty rectangle provides
        # full projective observability. Use those independently constrained
        # shapes to correct perspective drift before the bounded local update.
        full_candidates = self.registration._ellipse_seeds(evidence, homography)
        if not full_candidates:
            refined = self.registration._refine(evidence, homography)
            if refined is not None:
                score = self.registration._score(evidence, refined)
                if score is not None and score["accepted"] and score["supported_segments"] >= 6:
                    full_candidates = [refined]
        for candidate in full_candidates:
            score = self.registration._score(evidence, candidate)
            if score is None or not score["accepted"]:
                continue
            deviation = np.linalg.norm(
                project(evidence.plane_probes, candidate)
                - project(evidence.plane_probes, homography),
                axis=1,
            )
            if np.median(deviation) > 500:
                continue
            self.last_supported = index
            self.summary["successful_corrections"] += 1
            self.diagnostics[index] = {
                **score,
                "method": "independent_field_shape",
                "correction_median_cm": float(np.median(deviation)),
                "coordinate_transition": bool(np.median(deviation) > 200),
            }
            return candidate
        inverse = np.linalg.inv(homography)
        pixels = project(self.registration.samples, inverse)
        finite = np.isfinite(pixels).all(axis=1)
        pixels = np.nan_to_num(pixels, nan=-1e6, posinf=-1e6, neginf=-1e6)
        usable = finite & evidence.unoccluded(pixels) & (evidence.distances(pixels) < 12)
        selected = pixels[usable]
        if len(selected) < 40:
            self.diagnostics[index] = {"accepted": False, "reason": "insufficient_paint"}
            return homography
        selected = selected[:: max(1, math.ceil(len(selected) / 400))]
        center = np.mean(selected, axis=0)
        radius = max(float(np.sqrt(np.mean(np.sum((selected - center) ** 2, axis=1)))), 100.0)

        def warp(parameters):
            angle, scale = parameters[2] / radius, math.exp(parameters[3] / radius)
            rotation = scale * np.array(
                [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]
            )
            matrix = np.eye(3)
            matrix[:2, :2] = rotation
            matrix[:2, 2] = center + parameters[:2] - rotation @ center
            return matrix

        def residual(parameters):
            moved = project(selected, warp(parameters))
            return np.r_[evidence.distances(moved), parameters * 0.12]

        def jacobian(parameters):
            offsets = np.eye(4) * 0.1
            return np.column_stack(
                [
                    (residual(parameters + offset) - residual(parameters - offset)) / 0.2
                    for offset in offsets
                ]
            )

        from scipy.optimize import least_squares

        result = least_squares(
            residual,
            np.zeros(4),
            jac=jacobian,
            bounds=(-12.0, 12.0),
            loss="soft_l1",
            f_scale=2.0,
            max_nfev=30,
        )
        singular = np.linalg.svd(result.jac[: len(selected)], compute_uv=False)
        if len(singular) < 4 or singular[-1] < singular[0] * 0.01:
            self.diagnostics[index] = {"accepted": False, "reason": "ambiguous_paint"}
            return homography
        corrected = homography @ np.linalg.inv(warp(result.x))
        error_before = float(np.mean(np.minimum(evidence.distances(selected), 10)))
        error_after = float(
            np.mean(np.minimum(evidence.distances(project(selected, warp(result.x))), 10))
        )
        accepted = (
            self.registration._valid_plane(evidence, corrected)
            and error_after <= error_before + 0.05
            and error_after < 3.0
        )
        deviation = np.linalg.norm(
            project(evidence.plane_probes, corrected) - project(evidence.plane_probes, homography),
            axis=1,
        )
        self.diagnostics[index] = {
            "accepted": bool(accepted),
            "paint_error_before_px": error_before,
            "paint_error_after_px": error_after,
            "similarity_adjustment_px": result.x.tolist(),
            "supported_samples": len(selected),
            "correction_median_cm": float(np.median(deviation)),
            "coordinate_transition": bool(accepted and np.median(deviation) > 200),
        }
        if accepted:
            self.last_supported = index
            self.summary["successful_corrections"] += 1
            return corrected
        return homography


def prepare_offline_corrector(input_path, records, config):
    """Read source paint once, caching sparse frames for bounded sequence correction."""
    capture = cv2.VideoCapture(str(input_path))
    if not capture.isOpened():
        raise RuntimeError("Cannot reopen source video for field-paint refinement")
    interval = max(1, round((capture.get(cv2.CAP_PROP_FPS) or 30.0) * 0.2))
    evidence = {}
    required = {record["frame"] - 1: index for index, record in enumerate(records)}
    try:
        frame_number = 0
        while True:
            valid, frame = capture.read()
            if not valid:
                break
            index = required.get(frame_number)
            if index is not None and (
                index % interval == 0
                or records[index].get("quality", {}).get("source") == "field_lines"
            ):
                evidence[index] = FieldPaintEvidence(frame, records[index].get("boxes"))
            frame_number += 1
    finally:
        capture.release()
    if required and frame_number <= max(required):
        raise RuntimeError("Source video ended before recorded inference frames")
    return OfflinePaintCorrector(evidence, config, interval)
