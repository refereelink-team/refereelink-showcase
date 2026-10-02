# Multi-view video review

The showcase presents the upstream multi-view review workflow in a compact evidence workspace. Python services, inference, and rule evaluation remain on the remote CUDA host. The Mac runs the browser client and frontend checks.

## Operator workflow

1. Open `http://localhost:5174/#/multiview` with the configured remote showcase service available.
2. Select **Case 1**, **Case 2**, and so on. The Chinese UI displays `案例 1`, `案例 2`, etc. The labels use the unfiltered catalog order; filtering does not renumber cases. Match titles, league names, and clocks are intentionally omitted from the case queue.
3. Review all cameras together. Three views use one large view plus two smaller views; four views use a two-by-two grid. Selecting a primary view changes its prominence and audio selection without creating a new playback clock.
4. Use shared play/pause, seek, speed, and ±0.04-second controls. These are fixed time increments, not an assertion of one decoded video frame. Per-view timeline rows also control the shared time.
5. Start analysis when actual CUDA resources and all evidence videos are ready. The model result remains a suggestion. A new result seeks the cameras to a model attention peak, or to the labeled event-time fallback when no peak exists.
6. Confirm model-suggested facts individually, or edit them. Accepting a suggestion retains model provenance and records human confirmation. Editing records human provenance. Team, match context, and foul location are not fabricated from action logits.
7. Click, drag, use arrow keys, or enter X/Y meter coordinates to mark the foul location. Clear and undo are available. Saved geometry identifies the zone and whether the location is in the offender's own penalty area.
8. Save the draft, confirm the review, mark it uncertain, or archive it. Saving a draft preserves the existing review state; a new draft starts as pending. Review state and rule completeness are independent.
9. Open **Rule basis** to see missing facts, conflicts, restart, sanction, geometry, rule version, Law references, quoted rule excerpts, and the deterministic explanation. The optional language-model explanation is requested after saving and must match the saved revision. A failed or unavailable LLM leaves the deterministic template visible with its true source.

A direct link takes the form `/?case=<case_id>#/multiview`. Drafts survive switching cases during the current mounted page session. They do not survive a browser reload. **Restore draft** returns to saved facts, and **Reload review** reads the current server record. A revision conflict preserves the draft and never retries an overwrite automatically.

## Evidence and provenance

- Camera time is `common_time + sync_offset_ms / 1000`. Longer views retain their playable tails; cameras outside their own playable ranges are explicitly labeled.
- Synchronization uses media clocks with drift correction and an independent clock for stalled or unavailable reference cameras. Rendering progress is throttled, and high-frequency playback does not rerender the parent review form.
- Grad-CAM renders the upstream spatial focus rectangle with contain/letterbox compensation and temporal gating. It is not a raw per-pixel heatmap. Low-reliability regions remain hidden; caution regions and event-prior windows retain their source labels.
- View attention comes from actual model weights. Missing spatial evidence is not replaced with a fabricated focus region.
- A saved review and human values, including explicitly cleared facts, are protected from later model prefill. Model suggestions are initially unconfirmed. New suggestions receive a 1.1-second highlight; reduced-motion CSS disables the animation.
- Editing invalidates the displayed rule conclusion and explanation until saving. Changing the action clears rule-derived contact so the backend can derive it again, while preserving an explicit human contact override.
- Loading, analysis, saving, and explanations use case generations. Explanations additionally match the draft generation, request sequence, and persisted revision. Failed loads do not enter the draft cache. Late save responses update the canonical record without applying an unrelated case's state.

The synchronization, geometry rendering, fact vocabulary, and rule-display behavior are adapted from the upstream MIT-licensed `refereelink-backend` multi-view page and components. Protocol types retain upstream compatibility. Extracted inference algorithms in `backend_core` were not modified by this migration.

## Migration coverage

| Workflow | Showcase implementation |
| --- | --- |
| All camera views and primary selection | `SynchronizedEvidencePlayer` |
| Shared playback, offsets, drift correction, fine stepping | `evidencePlayback` and synchronized player |
| Per-view ranges, attention windows, peaks and cursors | Synchronized evidence timeline |
| Grad-CAM region, temporal gating, reliability and attention weights | Evidence tiles and model inspector |
| Analysis peak focus | Token-bound shared seek request |
| Pitch location, numeric coordinates, clear, undo and geometry | `FoulLocationPitch` |
| Safe model prefill, full facts, confidence, source and confirmation | `reviewFacts` and `FactsEditor` |
| Draft, reviewed, uncertain and archived records | `useMultiviewReview` |
| Dirty assessment and explanation invalidation | Review request scope and inspector |
| Full rule status, missing facts, conflicts, Law trace and excerpts | `ReviewAssessment` |
| Template, optional LLM, automatic explanation and revision protection | Review workflow and assessment panel |
| Anonymous case queue, state filter and pending count | Multi-view page |
| Case deep links and restoration | Search-parameter selection and draft cache |
| Real model/media readiness and stale-response protection | Status resource, evidence readiness and request scope |

The original navigation remains intact. Compared with the design concept, the implementation uses actual view-attention weights instead of invented diagnostic metrics, removes decorative timeline thumbnails, and keeps compact source chips below editable values. Review actions remain visible while the inspector body scrolls. Narrow screens stack the evidence and review panels.

## Validation record — 2026-10-02

| Check | Evidence |
| --- | --- |
| Frontend types, formatting, build and whitespace | `npm run lint`, `npm run format:check`, `npm run build`, `git diff --check` passed |
| Software regressions | 27 Node tests passed: offset/range arithmetic, stalled reference clocks, temporal provenance and gating, letterbox geometry, safe prefill, derived-contact invalidation, structured revision conflicts, and request generations |
| Real three-camera playback | All three actual media elements played together; one ±0.04-second step produced `0.04` seconds on every camera |
| Shared seek and primary switch | Seeking to a real attention marker produced `3.179808` seconds on all three cameras; changing primary view retained these times |
| Real CUDA analysis | A four-view call returned `mode=model`, `device=cuda`, and `inference_ms=207.9`; a three-view call returned `inference_ms=357.6`, `gradcam_ms=90.6`, and three localization records |
| New prefill | Real returned facts received the short new-value highlight and unconfirmed source chips; acceptance retained model provenance, while manual edits became human facts |
| Save and rule workflow | A temporary remote server used the real upstream rule engine, fact derivation, explanation writer, and an isolated SQLite review store; pending, reviewed, uncertain, archived and revisions 1–4 were exercised without overwriting user reviews |
| Rule evidence | A deliberately entered test scenario produced a penalty and yellow-card assessment, own-penalty-area geometry, human/model conflict, Law trace, and revision-matched template |
| Draft protection | Editing hid old rule outcomes; location clear/undo worked; switching cases restored the unsaved draft; restoring the draft recovered the canonical record |
| Desktop and mobile | 1536×1024 and 390×844 browser viewports had no horizontal overflow; mobile common stepping also gave `0.04` seconds on all cameras |
| Deployment | Updated static build assets and index on the remote showcase service returned HTTP 200; local browser acceptance used Vite's remote API/media proxy |

The isolated acceptance server proxied actual media and CUDA inference, but only its temporary SQLite store accepted review writes. It was shut down after validation. Generated design concepts and screenshots are local inspection artifacts and are excluded from Git. Test throughput values describe individual model calls, not sustained live multi-device frame rates. The LLM was not configured in the isolated test, so its deterministic fallback was verified; this does not claim an LLM generation acceptance. Reduced-motion behavior was checked in CSS, not by changing the user's system preference.
