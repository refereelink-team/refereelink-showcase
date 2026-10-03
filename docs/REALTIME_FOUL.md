# Versioned real-time foul experiments

The native foul workspace uses fresh remote CUDA results prepared during the current
backend boot. It plays the original input video with frontend SVG contact markers,
optional player boxes, a candidate timeline, and a native inspector. The detector first
proposes player interactions, verifies received temporal evidence with a trained clip
classifier, merges continuous interactions, and emits review candidates. It does not
turn a close pair, overlap or a fallen player directly into a referee decision.

The demonstration is fixed to qualified `mvit-contact-v3`; the API also supports the
comparison profiles below and historical results. Startup preparation requires verified
CUDA and a valid private qualification record. It schedules fresh tasks through the
existing GPU queue while HTTP remains available. A missing qualification, failed task,
or invalid native descriptor does not fall back to historical output. See
[native foul presentation](NATIVE_FOUL_PRESENTATION.md) for exact boot/result binding,
overlay timing, and replay behavior.

Multi-view adjudication is a separate workflow and is unchanged by this experiment.

## Profiles and default selection

| Profile | Model input | Purpose |
| --- | --- | --- |
| `legacy-v1` | Original VARS rolling-frame scan | Historical baseline, including its original trigger and cooldown behavior |
| `mvit-full-v2` | VARS MViT, whole source image | Isolate source-time sampling, score separation and interaction lifecycle changes |
| `mvit-pair-v2` | VARS MViT, stable pair region with context | Independently test whether local action detail improves verification |
| `multidim-full-v2` | Author MultiDimStacker, one whole live view | Compare a single-view high-resolution clip classifier under the same interaction mechanism |
| `mvit-contact-v3` | VARS MViT, contact-supported local region | Require distinct pre-contact observations and independent received aftermath before fresh model verification |

The API's configured default is guarded by a qualification record. If
`SHOWCASE_FOUL_PROFILE=mvit-contact-v3` is configured without a valid V3 qualification,
the API default remains `legacy-v1`. This compatibility fallback does not unlock the
native V3 demonstration. Experimental results are stored separately. A frozen
configuration must pass real event localization, action classification, and causal
alert-delay acceptance before becoming the default.
No failed or incomplete branch should become the production default. Public job requests
do not accept per-video threshold overrides. Development parameter scans are separate
from the fixed configuration used for comparison videos and later jobs.

`GET /api/catalog` includes a `foul_detection` object containing `default_profile` and
profile descriptors (`id`, `label`, `model_id`, `available`, `experimental`). API clients
can select available comparison profiles explicitly. The native page binds to this
boot's qualified V3 task instead of selecting whichever result ran most recently.
Missing MultiDimStacker runtime source or weights produces an unavailable branch and an
explicit error; the server does not substitute another classifier.

Configure these optional values in the deployment's private environment file:

```dotenv
SHOWCASE_FOUL_PROFILE=legacy-v1
SHOWCASE_PREPARE_FOUL_ON_STARTUP=true
SHOWCASE_FOUL_QUALIFICATION=/srv/refereelink-showcase/runtime/foul-qualification-v3.json
SHOWCASE_MULTIDIM_MODEL=/srv/refereelink-showcase/runtime/weights/author-model.pth.tar
SHOWCASE_MULTIDIM_CODE=/srv/refereelink-showcase/runtime/external/multidim-source
```

These are example paths, not supplied assets. The model implementation and any required
external dependencies must match the strictly loaded author checkpoint.
The startup flag defaults to `true`; set it to `false` to disable automatic native
preparation. A qualification record is an optional deployment asset, but it is required
for native V3 playback. The public repository does not include one.
`SHOWCASE_MULTIDIM_CODE` points to the source directory containing `utils.py`,
`multidim_stacker_mod.py` and `mvaggregate.py`, not an assumed repository root. The protected
team-calibration bundle remains unchanged; a foul experiment cannot activate a practice
calibration or overwrite the demonstration bundle.

## Source-time evidence and scores

The detector keeps three seconds of received images, original source presentation
timestamps, boxes and tracker associations. Pair proposals use apparent player scale,
relative approach, overlap and motion/posture changes. Unknown teams and an unavailable
ball observation do not exclude a pair. A short ambiguous association may preserve an
interaction episode while withholding the actor's identity.

Temporal inputs are sampled from source PTS. They do not acquire a different action
span when the input FPS changes. The corrected VARS input contains 16 samples at the
author's target rate of 17 Hz within approximately 0.96 seconds of source support.
MultiDimStacker contains 15 samples at 25 Hz: 0.56 seconds between its first and last
target sample, with 0.60 seconds of frame support. Duplicate low-FPS observations are
recorded as repeats and do not create additional visual evidence. Gaps and scene cuts
interrupt evidence continuity.

The whole-image VARS control uses the official TorchVision MViT transform. The pair
branch is a crop experiment, not an additional physical camera. VARS aggregation slots
replicate one source; their count must not be presented as a count of independent views.
MultiDimStacker uses the author's grayscale decode/center-crop preprocessing and
horizontal-flip test-time averaging, with one live-view slot.

The verifier records three distinct scores:

- **Offence score:** `1 - P(no_offence)`, derived from the four-class offence/severity head.
- **Action score:** the selected action's softmax score.
- **Severity score:** the selected offence/severity class's softmax score.

The new trigger does not average action and severity scores. An action label can be
confident without establishing a foul. A `no_offence` decision still rejects a window.
Fresh completed windows, a global threshold configuration and the required confirmation
streak determine eligibility. Cached predictions cannot create additional alerts.
CUDA outputs must be finite; an identical FP32 retry may recover a non-finite FP16
forward, and a remaining failure is recorded rather than replaced by scripted output.

Continuous pair interactions have an episode lifecycle and an emit ledger. An episode
emits at most one candidate. Separate pairs or a new interaction after the preceding one
ends can emit new candidates without a global cooldown suppressing them. Actors are
involved targets, not presumed offender/victim assignments. Track IDs are scoped to a
run and are not jersey numbers.

The current classifiers have no reliable actor attribution head. Saved events
therefore leave `involved_targets`, offender and victim unknown; the proposed
tracker associations remain in `candidate_targets` for diagnostics. A crop
containing a third player is not evidence that the originally proposed pair
committed the classified action.

The globally frozen **research** parameters are offence 0.55, action 0.70,
dynamic evidence 0.25, two fresh windows and 0.20 seconds of post-contact
support. They are defined by `EXPERIMENTAL_CONFIG_V2`, with two model windows
per scheduling tick. They apply to all four inputs without video-specific
overrides. A cached development sweep used a denser schedule; it is selection
evidence rather than an end-to-end acceptance result. Full-pipeline verification
is authoritative. The V2 branches did not qualify for default promotion.

V3 adds an action-agnostic contact gate before scheduling a local classifier.
The contact must be inside the actual sampled source-time interval. At least
two earlier observations establish separately visible bodies; at least two
later observations establish a relative height drop or aspect-ratio change.
Apparent scale, detection quality, contact support and observation spans are
fixed globally by `ContactConfig`. These checks suppress duplicate person boxes
and unsupported nearby crops; none can produce an alert without a fresh model
window. The gate is intentionally conservative and can miss fouls without
visible contact or posture change.

The V3 classifier requires offence score 0.60, action score 0.70 and one fresh
positive window, corroborated by independent received post-contact geometry.
Two different crops of the same source time are not counted as two temporal
votes. `no_offence` still vetoes that window. Overlapping ambiguous associations
can merge into one contact-region event, while distinct reliable pairs remain
independent. Raw model action is retained without a hard-coded tackle label.

Qualification is scoped to the development clip, not cross-match accuracy. The
private qualification JSON binds the immutable configuration, model checkpoint,
verified source manifest and evaluation evidence by SHA-256. Tasks snapshot these
identities at submission and reject mismatched reports. A changed checkpoint,
configuration or source invalidates the qualification instead of borrowing old
acceptance. Model assets, user videos and evaluation annotations stay outside Git.

Border and rectangular pair crops use constant gray square padding before
the official transform so center cropping cannot truncate either actor.
Synthetic CUDA warm-up initializes the foul model, person detector and semantic
feature extractor before source-paced replay. Semantic warm-up covers every tail
batch size from 1 to the extractor's configured maximum; warming only the largest
batch leaves cold shape-dependent CUDA initialization in the arrival loop.
Acceptance includes a fresh-process source-paced replay, independently of earlier
jobs' kernel caches. Warm-up does not update any tracker,
calibration or feature bank. Startup time, playback FPS, offline processing FPS
and measured wall-clock alert delay are reported separately.

## HTTP and saved-result compatibility

Submit a CUDA task with `POST /api/experiments/jobs`:

```json
{
  "kind": "foul",
  "case_id": "foul-1",
  "detection_profile": "mvit-full-v2"
}
```

Omitting `detection_profile` remains supported and uses the explicitly configured
server default. Supplying a foul profile to a tracking-only job, an unknown profile or
an unavailable model is rejected. Active-task deduplication includes the detection
profile and input hash. A frozen configuration's version must change if its detection
behavior changes; replacing parameters under the same version cannot establish a new
validated configuration.

Jobs, artifact descriptors and reports carry `detection_profile` and `model_id`.
The current model identifiers are `mvit-v2-s-vars`, `mvit-v2-local` and
`multidim-stacker`. The local identifier records an input-crop variant of the
same MViT checkpoint. Registration
checks the reported profile and model against the requested task before exposing its
output. Modern profiles also require a recorded strict checkpoint load with a positive
tensor count and no missing or unexpected keys. The candidate JSONL must exist, its
record count must equal the report, and each candidate must carry the requested profile
and model in its evidence. A report that claims zero candidates cannot expose a stale
nonempty event file. Older unversioned records belong only to `legacy-v1`; they cannot satisfy a
newer profile's validation or populate its timeline.

A selected task owns its own artifact set. The native page uses
`GET /api/foul/preparation` to identify the current boot's exact task, result, and
revision for each clip. `GET /api/foul/results/{id}` returns the validated source-bound
descriptor, and its `/frames` endpoint provides revision-bound native observations.
While preparation is pending, failed, disabled, or lacks a usable result, the page
does not display a historical result in its place. Other profiles remain accessible
through the experiment APIs; their artifacts retain their own profile and model
identity and do not populate the native V3 demonstration.

A candidate-first scan may legitimately find no interaction to classify. Zero forwards
are accepted only when the report explicitly records all four values:

```json
{
  "interaction_candidates": 0,
  "foul_actual_forward_windows": 0,
  "foul_skip_reason": "no_interaction_candidates",
  "foul_candidates": 0
}
```

This is evidence of a complete empty scan, not evidence that a foul-model forward ran.
All completeness, source, CUDA provenance and rendered-frame checks still apply.

For `mvit-contact-v3`, a complete empty scan instead records
`contact_supported_candidates: 0`, `foul_actual_forward_windows: 0`,
`foul_skip_reason: "no_supported_contacts"` and `foul_candidates: 0`.
The contact gate may reject proposed interactions before invoking the classifier;
these counters describe scheduling opportunities, not ground-truth incidents.

## Additive event schema

`candidate-events.jsonl` retains its existing wrapper and event fields. New fields live
inside `event.evidence` and `event.foul_details`; old records remain readable. The
following is a schematic example, not an evaluation annotation:

```json
{
  "media_pts_seconds": 12.4,
  "source_frame_id": 406,
  "event": {
    "id": "candidate-example",
    "event_type": "foul_candidate",
    "confidence": 0.74,
    "reviewed": false,
    "foul_details": {
      "action": "Tackle",
      "severity": "Offence + No Card",
      "offence_score": 0.74,
      "action_score": 0.61,
      "severity_score": 0.48
    },
    "evidence": {
      "source": "mvfoul",
      "detection_profile": "mvit-full-v2",
      "model_id": "mvit-v2-s-vars",
      "episode_id": "interaction:0:example",
      "event_time_s": 12.4,
      "emitted_time_s": 13.5,
      "evidence_start_s": 12.4,
      "evidence_end_s": 13.5,
      "region_xyxy": [200, 180, 400, 360],
      "source_width": 852,
      "source_height": 480,
      "involved_targets": [
        {"track_id": null, "entity_id": null, "team": "unknown", "identity_reliable": false}
      ],
      "offender": null,
      "victim": null
    }
  }
}
```

`region_xyxy` uses original image pixels, bounded by `source_width` and `source_height`.
`model_region_xyxy`, when present, describes the independently sampled crop.
`involved_targets` may contain observed runtime track IDs, optional stable entity IDs,
team/role observations and identity-reliability metadata. Unknown actor attribution is
preserved. The adapter leaves field coordinates empty: a ball location is not a foul
location.

Timeline positions and candidate selection use `event_time_s` rather than the later
alert frame. During demonstration playback, timeline entries, notifications, and
right-hand details become visible only at the recorded `emitted_time_s`; returning to
the beginning clears the replay's revealed events. Prepared contact rectangles are
separate visual evidence markers, shown only near the recorded contact observation
even if the later notification has not appeared. They are not new model computation
during playback. `emitted_time_s` records when the candidate becomes available in source time.
Older records without these fields retain their recorded `media_pts_seconds`; the UI
does not invent an alert time or involved player. Actor IDs, action and severity classes
remain model evidence for review.

## Evaluation and timing boundaries

Development annotations must remain outside Git and outside the detector's inputs.
Time, region and actor labels used to assess a clip cannot become trigger conditions.
Select global parameters on the confirmed development event, then freeze them before
running comparison videos. Unannotated comparison clips require manual review and
cannot be treated as an all-negative test set or used to claim cross-match accuracy.

Run model inference, Python tests and video-result rendering on the remote CUDA host.
The Mac runs only frontend checks and media playback. The runner exposes
`--foul-profile`, `--multidim-model`, `--multidim-code` and `--causal-replay` for controlled
CUDA comparisons; its development threshold arguments are not public per-video UI
controls.

Report these quantities separately:

- Source/playback FPS describes the supplied video and rendered playback cadence.
- Offline processing FPS includes the recorded processing/rendering/JSON timing scope.
- Synchronized model and pipeline latency measure computation.
- Source-time confirmation delay is `emitted_time_s - event_time_s`.
- Real alert latency requires source-FPS arrival pacing and the measured alert wall time,
  including queue/computation lag. Offline timestamp differences do not prove it.
- Peak CUDA allocation/reservation and any deferred proposal/window budget are recorded
  resource constraints.

Synthetic model warmup is excluded from evidence and real forward-window counts. A
sequential causal replay processes only arrived frames and does not use future frames;
if computation falls behind source arrivals, that lag must remain in measured latency.

The author MultiDimStacker was trained for a 720p input. The supplied project clips are
480p; resizing them to 720p does not create missing detail. Record this resolution
mismatch, external source/checkpoint hashes and any branch that could not finish within
the device budget. A successful strict load is necessary, but it is not task acceptance.

Node checks may run locally:

```bash
npm test
npm run lint
npm run format:check
npm run build
```

API/profile regression tests run on the remote host. Browser acceptance is independent
of those checks and must cover visible original-media playback, case changes, current-boot
readiness, event seeking, overlay toggles, replay clearing, keyboard controls, and
narrow-screen layout. Experimental profile selection is checked through the API rather
than a profile selector on the fixed native demonstration page. A blocked browser
permission remains a pending acceptance boundary rather than being replaced with an
API-only claim.

## Upstream provenance

[VARS](https://github.com/SoccerNet/sn-mvfoul) and the
[MultiDimStacker author implementation](https://github.com/druefena/MVFoul) are clip
classifiers, not complete continuous-event detectors. The latter's GPL-3.0 source is
obtained as an external runtime dependency; this repository does not vendor its network
implementation or checkpoint. Preserve its license and authorship with deployed source,
and comply with its terms when distributing combined deployments. Runtime source,
weights, user videos, calibration bundles, annotations and generated reports remain
outside Git.

[E2E-Spot](https://github.com/jhong93/spot) is a possible later event-localization route.
It is not a replacement implemented by this release; context, causality and actor
association would need their own adaptation and acceptance.
