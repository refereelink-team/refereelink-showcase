# Native calibration practice

The **Field Tracking → Team Calibration** tab uses an independent backend session.
It does not reuse the protected prepared demonstration's calibration object, enable
a practice result for match tracking, or overwrite the demonstration bundle.

## Operator flow

1. Open Team Calibration. A fresh active session automatically prepares the entire
   supplied source on the remote CUDA service; there is no segment selection step.
   Preparation uses `start_ms=0` and the backend source's exact `duration_ms`.
   Invalid duration or a source longer than the supported 60 seconds produces an
   explicit error; the frontend never silently truncates media.
2. Click a player box in the clean review video, or choose a player in the list.
   The inspector crops a real representative frame. Assign Home, Away, Home
   goalkeeper, Away goalkeeper, Referee, or Ignore.
3. Check the result. Counts come from backend validation. An insufficient result
   links to related player groups and excluded IDs while preserving manual labels.

Progress has three stages: Prepare players, Label players, and Check result.
Existing sessions restore their clip, labels, and validation instead of preparing
again. Hidden tabs cannot initiate preparation. A shared in-memory coordinator
lets StrictMode and remounted workspaces subscribe to a single attempt. A failed
attempt remains cached until explicit retry; polling does not repeat preparation.
Retry reads the latest revision first. Server errors can be recovered by the
confirmed reset action instead of silently discarding restored labels.

The source preview and review clip use native HTML video playback. The review
clip starts at zero. SVG boxes use the backend's source-pixel coordinates and
recorded frame timestamps; overlays are suppressed during seeking, at gaps, or
when video dimensions do not match the metadata.

Presented frame timestamps drive the overlay clock. Seek generations invalidate
queued callbacks from earlier positions, and paused frames presented before the
browser clears `seeking` are retained until `seeked`. Browsers without a video
frame callback use the native media-event fallback.

New clean review clips use H.264 keyframes at intervals of at most half a second
without changing source frame rate, timestamps, or dimensions. Legacy encoding
defaults are unchanged. An existing session may provide a verified sibling
`review-seekable.mp4` for browser playback. The original `review.mp4` must remain
present, and its recorded path remains the feature-recovery source. The server
only selects an existing derivative; HTTP requests do not transcode media or
modify session state. Runtime migration must verify frame count, every frame PTS,
frame rate, and dimensions before atomically publishing the sibling file. Reload
the video after migration to avoid combining already buffered original bytes with
the derivative at the same URL.

## Quality and preservation

The backend initially marks tracks with fewer than the required clear
observations as ignored. The Weak samples filter keeps them visible; explicit
manual labels can restore them. A weak manually labeled track remains labeled
even if validation excludes its samples. The validator, rather than the browser,
decides which tracks count toward the result.

Switching between match video and calibration keeps the calibration workspace
mounted and pauses its hidden video. Leaving the Field Tracking page stores the
practice ID and local selection in session storage. Returning reads the latest
backend session, so progress and confirmed labels can be restored. Revision checks
prevent an older response from replacing newer labels. Backend availability and
session retention are prerequisites; reconnecting does not fabricate a result.

**Prepare again** explicitly resets the session's clip, labels, and result after
confirmation. Preparation starts only after polling confirms the asynchronous
reset finished and the session is idle and empty. The protected demonstration is
unaffected. Successful results stay isolated; the UI has no activation action.
Isolation is enforced by the backend and documented here rather than repeated as
introductory interface copy.

## API contract

- `POST /api/calibration/sessions` creates a practice session.
- `GET /api/calibration/sessions/{id}` reads its snapshot.
- `POST .../{id}/prepare` sends revision, start_ms, and end_ms.
- `POST .../{id}/labels` sends revision, track_id, and a semantic label.
- `POST .../{id}/validate` and `POST .../{id}/reset` send the current revision.
- `GET .../{id}/metadata` provides the clip identity, dimensions, frame
  timestamps, boxes, and representative track metadata.

Queued and running operations disable additional mutations. The snapshot's
source_url and video_url select the source preview or clean review clip. The
metadata session_id and clip_id must match before rendering overlays.

## Verification boundary

Frontend API and presentation tests cover session/revision scoping, late-response
rejection, retained weak labels, insufficient-result links, recorded-time overlay
selection, whole-source duration bounds, preparation deduplication, explicit retry,
and restoration guards. These checks do not establish rendered browser
behavior or real CUDA model quality. Full acceptance also requires the remote
backend session tests and a browser pass through automatic preparation, labeling,
checking, tab switching, and reset. Python execution remains remote-only.

```sh
npm test
npm run lint
npm run build
```

## Executed CUDA acceptance

The independent practice flow processed all 579 decoded frames of the supplied
calibration source. Representative crops were manually checked before team labels
were applied. Validation passed with five eligible Home tracks (79 samples), seven
eligible Away tracks (105 samples), and a ready referee mapping (14 samples).
Goalkeeper mapping remained unconfirmed. A manually labeled weak track with four
samples retained its label and was excluded from validation. Internal leave-one-track-out
consistency does not establish accuracy on another match.

Repeating successful validation and reconstructing the session after restart both
preserved the validated result. Resetting a different practice session preserved it
and the prepared demonstration bundle. The demonstration bundle SHA-256 was identical
before and after creation, preparation, labeling, validation, recovery, and reset.

A real calibration-to-tracking-to-calibration sequence completed 682 tracking frames
and another 579-frame calibration. Each GPU lease releases appearance models and
unused allocator cache; CPU feature banks retain the reviewer labels and features.
No appearance model remained loaded after handoff. The CUDA library retained a stable
32 MiB workspace after preparation and subsequent sessions, with no measured growth.
This is bounded library overhead, not a zero-allocation claim.

Active clip preparation canceled between model forwards in 0.128 seconds; the
inference thread exited before the GPU lease was released. An operation deadline
requests the same cancellation. A stalled native CUDA forward cannot be forcibly
killed as a Python thread: deployed supervision uses the supplied systemd service
with KillMode=control-group and TimeoutStopSec=15 as the process-level fallback.
Restart marks interrupted operations as recoverable errors and preserves labels.

The initial native calibration implementation passed 89 frontend tests and 80
remote Python tests, including the real CUDA-health check. These results predate
the subsequent whole-video and seeking changes.

The current whole-video and seeking changes pass 102 frontend tests, TypeScript,
formatting, and a production build. The complete suite on AstraForge passes 90
Python tests with no skips, including real CUDA health, actual 30 and 30000/1001
FPS encoding, and full/ranged derivative responses. Both targeted Ruff checks
and all 58 extracted-source hashes and AST dependency coverage pass.

Five existing prepared sessions received verified playback derivatives. Each
retained all 579 frame timestamps, 30 FPS, and 852 × 480 dimensions while reducing
the maximum keyframe interval from 8.333334 seconds to 0.5 seconds. All 24 protected
file hashes remained identical through migration and service restart, including
original review media, labels and session records, metadata, practice bundles,
runtime settings, source media, and the demonstration bundle. All 18 deployed code
and build files match their local checksums. Complete and ranged playback responses
select the derivative without changing the feature-recovery path.

A fresh independent session prepared the complete source interval of 0–19313 ms
on CUDA, producing 579 decoded review frames, 579 metadata frames, and 48 tracks.
Its maximum keyframe interval is 0.5 seconds and the demonstration bundle remains
unchanged. This verifies the deployed preparation and media paths, not cross-match
classification accuracy.

Rendered backward-seek acceptance remains pending: the computer-use tool reports
`Transport closed`. The Mac frontend and its remote API proxy are running. Media
checks and unit tests do not establish browser picture/overlay synchronization;
the browser must be refreshed and the paused backward-seek interaction checked.
