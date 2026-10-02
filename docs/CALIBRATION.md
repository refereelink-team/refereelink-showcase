# Native calibration practice

The **Field Tracking → Team Calibration** tab uses an independent backend session.
It does not reuse the protected prepared demonstration's calibration object, enable
a practice result for match tracking, or overwrite the demonstration bundle.

## Operator flow

1. Preview the original calibration video. Pause and enter the segment's start and
   end in seconds, or use the current playback position. The native video's
   duration bounds the selection; the maximum segment length is 60 seconds.
2. Prepare the players. The backend queues genuine inference on the remote CUDA
   service. The browser polls the practice session and does not run inference.
3. Click a player box in the clean review video, or choose a player in the list.
   The inspector crops a real representative frame from the clean clip. Assign
   Home, Away, Home goalkeeper, Away goalkeeper, Referee, or Ignore.
4. Check the result. Counts come from the backend validation report. An
   insufficient result links back to the related player groups and exact excluded
   IDs, while keeping all manual labels. Correct a label and check again.

The source preview and review clip use native HTML video playback. The review
clip starts at zero. SVG boxes use the backend's source-pixel coordinates and
recorded frame timestamps; overlays are suppressed during seeking, at gaps, or
when video dimensions do not match the metadata.

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

**Choose another segment** is an explicit reset of the practice clip, labels, and
practice result, with a confirmation. The protected demonstration is unaffected.
A successful practice result remains clearly marked as practice and has no
activation action.

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
selection, and segment bounds. These checks do not establish rendered browser
behavior or real CUDA model quality. Full acceptance also requires the remote
backend session tests and a browser pass through selection, preparation, labeling,
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

Frontend checks passed 89 tests, TypeScript and a production build. The final combined
remote Python suite passed 80 tests, including the real CUDA-health check. New rendered
browser interaction remains a separate acceptance boundary.
