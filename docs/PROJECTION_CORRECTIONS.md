# Projection corrections

The browser renders original source video and recorded CUDA coordinates. This
correction operates on image-to-pitch estimation; the browser does not move
players to plausible positions or infer coordinates from an exported video.

## Source-bound geometry

Default CLI calls, unknown inputs, and the calibration source retain the legacy
120 × 70 m template, 20.15 m penalty-area depth, and model-only projection. The
showcase opts the configured match source into `source-informed105` only when
both catalog metadata and the actual video SHA-256 match. Replacing that video
falls back to legacy behavior. Foul-only jobs do not enable paint projection.

The opted-in profile uses a 105 × 68 m template, 16.5 × 40.32 m penalty areas,
5.5 × 18.32 m goal areas, and a 7.32 m goal width. The area dimensions follow
[IFAB Law 1](https://www.theifab.com/laws/latest/the-field-of-play/). The overall
field dimensions are a source-informed assumption, supported by the visible
venue context and [published stadium dimensions](https://live.mancity.com/news/mens/etihad-stadium-pitch-progress-gallery-63758757),
not an independent survey or a verified identification of every supplied clip.

Inference, paint fitting, diagnostic rendering, and the native API use the same
runtime geometry. Each immutable report records `pitch_profile_id`,
`paint_enabled`, and `pitch_geometry_m`. Old reports without geometry retain
legacy geometry; old coordinates are never redrawn with the new template.

## Independent field-marking registration

The existing model fitting, camera refresh schedule, and temporal fallback remain
the control path. An independent fitter uses white markings adjacent to grass and
excludes detected people. Bounded line-family candidates must support the nested
goal-area and penalty-area structure. Bidirectional line-distance scoring checks
candidates against observed markings; a partial centre circle alone is insufficient.

Paint registration keeps its own accepted matrix, image reference, similarity
motion estimator, and position filter. Forward/backward optical flow and distributed
support check transport under camera pan, zoom, and rotation. Rejected candidates
do not write into model state. Loss of support returns to the independently
maintained model path. Coordinate-source transitions emit a frame with unavailable
projection and null world coordinates, interrupting the native trajectory instead
of presenting a coordinate change as player movement.

Paint processing is explicitly opt-in. Team-calibration preparation disables pitch
processing and neither constructs nor runs the paint estimators.

## Executed comparison

Fresh remote RTX 5060 Ti runs processed all input frames without drops:

| Source and profile | Frames | Processing FPS | Compatibility |
| --- | ---: | ---: | --- |
| Calibration, legacy | 579 | 21.12 | Every saved coordinate and homography status matches the original baseline |
| Match, source-informed105 | 682 | 17.25 | Every saved coordinate and homography status matches the validated candidate |

A post-deployment job submitted through the production HTTP API processed 682
frames at 17.52 FPS without drops. All 682 native API records exactly matched
the pipeline boxes, IDs, labels, source timestamps, and centimetre-to-metre
conversion. Its recorded source manifest matched all 58 checked core files;
stale revisions returned HTTP 409. Deployment preserved the existing runtime
configuration and demonstration calibration bundle checksums.

Paint registration contributes 23 match frames. Three coordinate transitions at
source frames 631, 652, and 679 explicitly interrupt positions and trails.

Manually identified painted intersections provide image-space regression checks:

| Source time | Legacy median corner error | New median corner error | New fit source |
| --- | ---: | ---: | --- |
| 21.6 s | 197.77 px | 4.80 px | Paint |
| 22.0 s | 185.76 px | 237.02 px | Model fallback; worse than legacy |

The evaluation permits one consistent global field symmetry and has approximately
3–5 px annotation uncertainty. These landmarks do not establish absolute player
accuracy in metres, team direction, or general football accuracy. The second row
is retained explicitly because paint registration does not solve the weak fallback.

For adjacent detected observations with identical boxes, entity IDs, raw tracker
IDs, and actual source PTS, 8,037 pairs have finite positions in both runs. Their
saved-position speed p95 changes from 17.51 to 11.66 m/s. Overall observed projection
coverage falls from 90.4% to 81.4%; this denominator includes all detected person
records with zero missing frames, and counts finite coordinates rather than only
in-field markers visible in the browser. The common cohort and coverage must be
read together. Geometry also changed, so this whole-clip comparison cannot be
attributed to paint alone.

Against the same-geometry model-only control, paint does not change the main wide
view or replay coordinates. In the final wide segment, common-cohort speed p95
changes from 11.48 to 7.27 m/s relative to legacy, and intervals above 12 m/s from
17 to 1. Ground-level replay speed remains implausible (approximately 284 m/s p95
in the paired comparison). A separate calibration clip regressed when the 105 m
profile was applied globally; source isolation prevents that profile being used
there. The fresh legacy calibration comparison verifies exact non-regression.

## Remaining limits

The inherited model fallback can still accept semantically wrong landmarks. Its
position filter can release held positions after repeated rejected measurements;
that behavior is retained. Ground-level replay, occlusion, weak markings, landmark
ambiguity, and unmeasured dimensions remain substantial limitations. This release
provides a bounded correction and source isolation, not a claim that projection
is consistently accurate throughout either video.

Runtime evidence includes reports, homography logs, source-PTS frame records,
per-frame diagnostics, and common-cohort audits. Raw user media and those artifacts
remain outside Git. Prepared-video processing FPS is distinct from 30 FPS source
playback and sustained live-camera operation. Browser interaction with the new
calibration layout remains a separate acceptance check.
