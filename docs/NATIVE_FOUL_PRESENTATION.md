# Native foul presentation

The foul page plays the original input video. React renders player boxes, contact
regions, model prompts, and the event timeline from recorded CUDA results. The
annotated MP4 remains a downloadable diagnostic artifact and is never the native
player's source.

## Startup and demonstration playback

On each verified CUDA backend startup, the service schedules a fresh qualified
inference task for each foul clip through the existing GPU queue and lease.
`GET /api/foul/preparation` identifies this boot and its exact tasks. A clip is
ready only after its completed result passes native descriptor validation; a
zero-event result is ready too. Missing qualification and failed preparation
never fall back to an older result or another algorithm.

The frontend waits for the current boot's descriptor and complete frame pages,
then starts muted, looping playback. The presentation status is
`正在实时运行`. This is a demonstration replay of startup-prepared CUDA inference,
not new per-frame inference during browser playback. Each event notification becomes
visible only when playback reaches its recorded emission PTS. Event review retains
already revealed evidence even when seeking before that emission time.

Returning to the beginning starts a new replay epoch and clears the timeline,
selected event, right-hand details, and target inspection. Reset is driven by
seek/presented-frame transitions, not `ended`, which native looping video may
never emit. A restart gate rejects delayed tail frames from the previous epoch.
Boot, task, revision, and clip changes reset the presentation independently.

## Result binding

The selected `candidate-events` artifact determines the matching `frame-states`
artifact by task ID. `GET /api/foul/results/{frame_artifact_id}` validates its
source, report, detector snapshot, and sibling records before returning an
immutable presentation descriptor. Frames are paginated through the descriptor's
`frames_url` with an explicit `revision`. A mixed revision or incomplete page
sequence cannot become renderable data.

The frontend also checks the descriptor's case, profile, source URL, and current
qualified detector fingerprint. Case changes, profile changes, new tasks, and
late responses cannot substitute a previous result. Historical results retain
their own model identity; insufficient historical files require a new detection
task rather than falling back to a rendered video.

## Playback and interaction

- The video and SVG use the same `contain` geometry. Overlays are disabled when
  the decoded dimensions do not match the recorded source dimensions.
- Presented video PTS drives overlays through `observeVideoPresentation`.
  Requested seek positions are not treated as displayed frames. Frame selection
  uses recorded source PTS, with only a half-millisecond rounding tolerance and
  a bounded observation age. No boxes are interpolated across missing frames.
- Player tracking is optional. Only valid, currently detected boxes render.
  Track labels describe IDs from that run, not jersey numbers. Clicking a
  tracked player pauses the video and opens a compact native inspection row.
- Timeline events point to the recorded interaction time. Clicking one pauses
  playback and enters explicit evidence review. Contact rectangles also appear
  automatically during normal playback and scrubbing, without selecting an event.
  These prepared evidence markers follow the recorded contact frame, independently
  of the later notification time; they do not reveal the right-hand details or
  timeline early. A static contact rectangle is valid only within 0.15 seconds of
  its observed time, never as a trajectory. Looping back to the beginning hides it
  until playback reaches the contact again. The event-region toggle controls these
  SVG markers without changing the original video.
- Normal replay prompts appear at the recorded `emitted_time_s` for two media
  seconds. Rewinding before emission hides them. Speed changes do not affect
  their interval, and simultaneous prompts remain independently accessible.
  Historical records without an emission timestamp do not acquire a fabricated
  prompt time.
- The common timeline is also the playback scrubber. Fine controls step by
  0.04 seconds. Fullscreen includes video, controls, and the timeline.

Unknown event participants remain unknown. A nearby player is not assigned as
offender or victim; the interface highlights only explicitly associated targets.
Showing a prepared CUDA result is a recorded replay, not proof of live camera
ingestion or a new model inference during playback.

## Verification

Run `npm run build` and `npm test` locally. The presentation tests cover variable
PTS cadence, gaps, backward seeks, causal prompt timing, simultaneous prompts,
observation filtering, task pairing, revision changes, and incomplete pagination.
Run the Python adapter tests on the remote CUDA host. Browser acceptance should
check event selection, scrubbing, overlay toggles, player inspection, fullscreen,
case/profile switching, and source-size mismatch behavior.

This presentation layer does not modify the qualified detection algorithm,
its thresholds, weights, configuration fingerprint, or multiview review.
