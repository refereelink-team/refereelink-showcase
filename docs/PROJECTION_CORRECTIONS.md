# Field-marking registration and sequence refinement

The tracking workspace renders the original video and recorded CUDA coordinates.
Field geometry is estimated in the backend from detected landmarks, visible paint,
and camera motion. The browser does not move targets to plausible positions.

## Scope and geometry

The configured match source opts into `source-informed105` only when catalog
metadata and the actual video SHA-256 match. Its template is 105 × 68 m with
16.5 × 40.32 m penalty areas, 5.5 × 18.32 m goal areas, a 9.15 m centre-circle
radius and a 7.32 m goal width. Overall dimensions are source-informed assumptions,
not an independent stadium survey. Symmetric markings do not establish team
identity or defending direction.

Unknown inputs, default CLI calls, and calibration media retain the legacy
120 × 70 m model-only profile. Calibration preparation disables pitch processing.
Existing reports retain their recorded geometry; the frontend never redraws old
coordinates against a different template. This correction is limited to the
verified match video and does not establish accuracy on other matches.

## Registration from visible markings

`FieldPaintEvidence` extracts locally bright, low-saturation paint adjacent to
substantial grass regions and excludes detected people. It retains both field
components when the halfway line separates them, and accepts one-sided grass
support at actual boundaries. Thin advertising fragments are excluded.

`FieldLineRegistration` evaluates the complete template, including touchlines,
endlines, penalty and goal-area rectangles, the halfway line, centre circle and
penalty arcs. Observed paint and predicted markings are checked in both directions.
Straight-line registration requires distinct lines in both canonical families;
three parallel lines plus one crossing line cannot establish transverse scale.
Midfield registration jointly constrains the circle, halfway diameter and a
matched touchline. A circle alone is insufficient.

Local fitting uses bounded, robust least squares with explicit numerical
Jacobians and conditioning checks. Grass-plane probes reject poles, folded
perspective and ill-conditioned transforms. Structured circle constraints retain
priority over a lower unstructured paint-distance score: thick white strokes
otherwise admit several zero-distance alignments.

Adjacent-frame projective optical flow excludes people and checks forward/backward
agreement, distributed support and RANSAC residuals. It transports one accepted
coordinate map, instead of alternating between unrelated model and paint maps.
Loss of accepted geometry produces unavailable coordinates. A correction above
2 m interrupts the coordinate epoch and clears that transition frame.

The model still proposes landmarks and detects/tracks people on CUDA. Geometry
validation does not modify the model's state or replace detections with synthetic
player positions. The [OpenCV homography documentation](https://docs.opencv.org/4.0.0/d9/dab/tutorial_homography.html)
and [SciPy least-squares documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.least_squares.html)
describe the numerical primitives used here.

## Offline sequence refinement

A supplied video can contain a clear penalty area after an initially partial
midfield view. Tracking-only jobs for the verified source therefore perform an
explicit offline pass after causal CUDA inference.

1. Split camera shots at missing/invalid motion, frame gaps and resolution changes.
2. Select strong anchors only when both nested rectangles and their matching
   endline are observed. Partial front-line matches cannot become global anchors.
3. Compare every strong anchor in one shot. A bounded temporal shortlist selects
   a medoid, but anchors outside the shortlist must also agree within 2 m. A
   contradictory shot remains unavailable.
4. Propagate outward from the anchor in both directions. Regular current-frame
   paint corrections prevent correlated optical-flow errors from accumulating.
   Independent complete shapes can refine perspective; partially observed paint
   can make only a conditioned, bounded image-similarity correction.
5. Expire a propagation chain without recent paint support. Never cross a shot
   boundary or substitute a model-only matrix for rejected geometry.
6. Reproject actual detection foot points and the same fresh ball observation.
   A strict position filter rejects repeated jumps rather than releasing them
   after a fixed streak. Missing observations, identity replacements, geometry
   changes and out-of-field positions interrupt trajectories and velocities.

This pass can use future evidence in the same shot. It is deliberately labelled
`offline` and `future_anchor` in frame diagnostics; it is not a live estimator.
Geometry hypotheses never change team labels or other nongeometric observations.
Obsolete geometric event fields and possession are cleared instead of retaining
facts computed under another transform. Source PTS determine velocities.

## Reproducibility and native output

Runtime outputs include immutable causal states and timing records, causal
registration history, refined homographies, refined frame states, source metadata,
and a rendered review video. The report records hashes, anchor consistency,
correction diagnostics, coordinate epochs, coverage and complete processing time.
The native API converts centimetres to metres once and supplies the same output
geometry. Browser trails split at unavailable frames and epoch changes.

Source media, manual evaluation landmarks, model weights, calibration bundles and
raw audit images remain outside Git. Evaluation landmarks are never registration
inputs. Pixel error at a few painted intersections is distinct from absolute
player accuracy in metres, tracker identity accuracy and generalization.

## Validation boundaries

Numerical tests exercise independent synthetic geometry, observability, circle
precision, boundaries, occlusion, horizon rejection, movement, frame gaps,
coordinate transitions, strict outliers, anchor consistency and native records.
They supplement the actual CUDA video comparison; they do not replace it.

The real-video audit uses source-aligned decoded frames and independent held-out
paint intersections. Coordinate coverage is reported beside saved-position speed
statistics and a common identity cohort. Ground-level close-ups remain unavailable
when no reliable field plane is observed. Full processing FPS includes offline
refinement and rendering, and is distinct from the video's 30 FPS playback.

The current browser permission rejection prevents a fresh frontend visual
acceptance in this session. Frontend type/unit/build checks and native API
read-back are separate evidence; generated algorithm diagnostics are not browser
screenshots. Executed measurements are recorded below after final validation.

## Executed production validation — 2026-10-03

The final HTTP-submitted job on an RTX 5060 Ti processed all 682 decoded frames
with zero processing drops. End-to-end throughput was **8.21 FPS**, including
offline refinement, JSON output, video rendering, encoding and contact-sheet
creation. The first wide shot accepted eight complete anchors with maximum
agreement error 148.91 cm against the unchanged 200 cm limit. Two large
correction frames and 108 unsupported close-up frames have unavailable geometry.

Independent evaluation uses one fixed world orientation and manual paint
intersections that were never passed to the estimator:

| Source time | Previously deployed median error | Final median error |
| --- | ---: | ---: |
| 0 s | 118.50 px | 4.91 px |
| 15 s | 422.28 px | 1.95 px |
| 21.6 s | 4.80 px | 3.19 px |
| 22 s | 237.03 px | 3.28 px |

Manual marks have approximately ±5 px uncertainty; individual new residuals
reach 11.9 px. The old 15-second result includes a coordinate-orientation failure.
These checks do not establish surveyed player positions in metres.

Saved-position comparisons use actual source PTS and identical entity IDs, raw
track IDs and bounding boxes, with adjacent detected observations and no missing
frames. Coverage counts all detected observations, separately from the common
cohort used for speed statistics:

| Exact segment | Coverage before → after | Common pairs | Speed p95 before → after |
| --- | ---: | ---: | ---: |
| First wide shot, frames 1–514 | 83.88% → 94.39% | 7,312 | 10.11 → 5.13 m/s |
| Ground-level close-up, frames 515–622 | 41.67% → 0% | 0 | Unavailable field plane |
| Final wide shot, frames 623–682 | 84.96% → 97.08% | 545 | 7.80 → 7.68 m/s |

A reproduced identity-filter defect explained large residual jumps: rebinding
one entity reset every player's filter. The correction now discards only that
entity's filter and velocity history. Geometry boundaries still reset all
coordinate histories. Detection/identity noise and some residual short jumps
remain; this comparison is evidence of improvement on the supplied clip, not
universal tracking accuracy.

All 682 native API observations matched source PTS, boxes, identities, semantics,
projection diagnostics, geometry epochs, player/ball coordinates and unit
conversion. All 60 current core hashes matched the run's recorded manifest. A
fresh legacy calibration run exactly matched all 579 baseline homography statuses,
player observations and ball records. Runtime settings, original source media and
the protected demonstration bundle retained their checksums.

The remote Python suite passed **156 tests with no skips**; Ruff passed for changed
core modules, server and tests. The Mac frontend passed **103 tests**, TypeScript,
formatting and production build. A regression additionally verifies that atomic
publication of refined JSONL cannot double-count job progress. Fresh browser
visual acceptance remains pending because computer-use permission was rejected.

Across 7,857 common saved-position pairs, speed p95 changed from 9.96 to
5.32 m/s and cadence-adjusted second-difference p95 from 0.338 to 0.090 m.
This is a smoothness statistic, not player-position ground truth. Residual
apparent-speed peaks remain: 49.6 m/s in the first wide shot and 24.0 m/s in
the final shot. The largest retained event coincides with an approximately
8.65 px detector foot-point change. Further foot localisation and observation
quality work is required before claiming all individual trajectories are stable.
