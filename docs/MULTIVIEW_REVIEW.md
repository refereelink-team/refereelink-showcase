# Multi-view video review

The showcase presents the upstream multi-view review workflow in a compact evidence workspace. Python services, inference, and rule evaluation remain on the remote CUDA host. The Mac runs the browser client and frontend checks.

## Operator workflow

1. Open `http://localhost:5174/#/multiview` with the configured remote showcase service available.
2. Select **Case 1**, **Case 2**, and so on. The Chinese UI displays `案例 1`, `案例 2`, etc. The labels use the unfiltered catalog order; filtering does not renumber cases. Match titles, league names, and clocks are intentionally omitted from the case queue.
3. Review all cameras together. Three views use one large view plus two smaller views; four views use a two-by-two grid. Selecting a primary view changes its prominence and audio selection without creating a new playback clock.
4. Use shared play/pause, seek, speed, and ±0.04-second controls. These are fixed time increments, not an assertion of one decoded video frame. Per-view timeline rows also control the shared time.
5. Start analysis when actual CUDA resources and all evidence videos are ready. Timeline attention windows, peaks, and video focus regions appear only after a successful analysis in the current case visit; historical results do not display playback focus before analysis. Switching cases or reopening the page hides this focus. A failed analysis retry retains the current visit's last successful attribution without seeking again; a successful result replaces it and clears an obsolete peak request when no valid peak exists. The model result remains a suggestion. A new result seeks the cameras to an explicitly returned model or review peak. A no-box result may use a returned review timestamp only when its API metadata declares that source. If neither is available, playback stays at its current time. Spatial attribution remains separate from event timing.
6. Confirm model-suggested facts individually, or edit them. Accepting a suggestion retains model provenance and records human confirmation. Editing records human provenance. Team, match context, and foul location are not fabricated from action logits.
7. Click, drag, use arrow keys, or enter X/Y meter coordinates to mark the foul location. Clear and undo are available. Saved geometry identifies the zone and whether the location is in the offender's own penalty area.
8. Save the draft, confirm the review, mark it uncertain, or archive it. Saving a draft preserves the existing review state; a new draft starts as pending. Review state and rule completeness are independent.
9. Open **Rule basis** to see missing facts, conflicts, restart, sanction, geometry, rule version, Law references, quoted rule excerpts, and the deterministic explanation. The optional language-model explanation is requested after saving and must match the saved revision. A failed or unavailable LLM leaves the deterministic template visible with its true source.

A direct link takes the form `/?case=<case_id>#/multiview`. Drafts survive switching cases during the current mounted page session. They do not survive a browser reload. **Restore draft** returns to saved facts, and **Reload review** reads the current server record. A revision conflict preserves the draft and never retries an overwrite automatically.

## Evidence and provenance

- Camera time is `common_time + sync_offset_ms / 1000`. Longer views retain their playable tails; cameras outside their own playable ranges are explicitly labeled.
- Synchronization uses media clocks with drift correction and an independent clock for stalled or unavailable reference cameras. Rendering progress is throttled, and high-frequency playback does not rerender the parent review form.
- Grad-CAM renders the upstream normalized spatial rectangles exactly, with contain/letterbox compensation and no presentation padding. It is not a raw per-pixel heatmap or player tracking. When real `spatial_bins` are provided, each rectangle appears only in its source-video sample interval; gaps and invalid or overlapping bins have no substitute rectangle. An explicit empty array supplies no spatial region. Aggregate responses with omitted or null `spatial_bins` use their static rectangle only with a valid, explicitly returned model or review interval. Hidden regions remain hidden, and caution regions retain their source labels.
- Spatial rendering follows actual video presentation timestamps through `requestVideoFrameCallback` where supported. A completed seek or ready-data event can restore a paused frame using its loaded `currentTime` until a presentation callback provides the exact frame timestamp; other browsers continue using media events and `currentTime`. Seeking, loading, and active-playback stalls clear the current attribution until a real frame is available. A preload stall cannot discard an already loaded paused frame; paused media events also restore that frame when presentation callbacks are throttled in a hidden tab. Shared playback synchronization remains independent of this display clock. A flat temporal response may use the backend's selected review interval; that interval is not represented as a model-discovered event time.
- Per-view source chips distinguish **Attribution Hidden** from **Attribution Unavailable** and preserve actual diagnostic reasons in tooltips. Display labels distinguish **Spatial** and **Temporal** provenance. Grad-CAM spatial evidence is labeled **Visual Attribution**; model-derived temporal focus is **Temporal Saliency** and event-assisted review intervals are **Temporal Focus**. The common legend uses **Focus Window** and **Focus Peak**. Review-interval tooltips say **Selected review interval**, while genuine Grad-CAM tooltips retain their algorithm. Missing temporal evidence is **Temporal Unavailable**. Method tooltips retain the actual algorithm, and low-reliability evidence remains marked **Limited Signal** or **Low Signal · Hidden**. Optical-flow and scripted spatial fallbacks use **Motion Cue** and **Rule Cue**.
- The restored upstream configuration uses prior-assisted review: it samples one 0.96-second window centered on the existing event annotation in common time, applies each camera's source offset, and uses event-weighted spatial aggregation. A returned `event_prior` window can supply review focus when the Grad-CAM temporal response is unavailable. The frontend accepts these explicit windows and preserves source metadata; it never guesses a missing window from catalog timestamps. Hidden spatial attribution remains hidden, while a valid review interval can still be displayed and selected.
- Production attribution again uses one configured pre-attention `norm1` patch layer. CLS exclusion and disconnected-gradient checks remain in place. The backend's optional multi-layer diagnostic API remains available, but the restored production caller does not use the earlier two-layer experiment.
- View attention comes from actual model weights. Missing spatial evidence is not replaced with a fabricated focus region.
- A saved review and human values, including explicitly cleared facts, are protected from later model prefill. Model suggestions are initially unconfirmed. New suggestions receive a 1.1-second highlight; reduced-motion CSS disables the animation.
- Editing invalidates the displayed rule conclusion and explanation until saving. Changing the action clears rule-derived contact so the backend can derive it again, while preserving an explicit human contact override.
- Loading, analysis, saving, and explanations use case generations. Explanations additionally match the draft generation, request sequence, and persisted revision. Failed loads do not enter the draft cache. Late save responses update the canonical record without applying an unrelated case's state.

The synchronization, geometry rendering, fact vocabulary, and rule-display behavior are adapted from the upstream MIT-licensed `refereelink-backend` multi-view page and components. Protocol types retain upstream compatibility, including recognition of historical temporal-source values. Extracted inference algorithms in `backend_core` were not modified by this migration.

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
| Anonymous case queue and state filter | Multi-view page |
| Case deep links and restoration | Search-parameter selection and draft cache |
| Real model/media readiness and stale-response protection | Status resource, evidence readiness and request scope |

The original navigation remains intact. Compared with the design concept, the implementation uses actual view-attention weights instead of invented diagnostic metrics, removes decorative timeline thumbnails, and keeps compact source chips below editable values. Review actions remain visible while the inspector body scrolls. Narrow screens stack the evidence and review panels.

## Current frontend validation — restored review intervals

The frontend regression suite contains 35 passing Node tests. Type checking, formatting, production build, and whitespace checks passed. Fresh deployment acceptance completed on the remote CUDA host and local browser. The historical records below describe previous builds and do not establish current runtime performance.

| Check | Current executed evidence |
| --- | --- |
| Upstream regressions | All 170 multi-view and event tests passed on the remote CUDA host; this suite is separate from showcase CI |
| Real event-window inference | Four installed official cases used one 0.96-second source window from 2.52 to 3.48 seconds around their existing 3.0-second annotation. Returned region protocols passed validation; No Offence returned no spatial records |
| Browser readiness and focus | After a complete reload, all three case-144 videos were ready with no pre-analysis focus. Fresh CUDA analysis selected common time 3.18 seconds, displayed two aggregate CAM rectangles, and retained the hidden motion fallback |
| Synchronization and gating | A shared step moved all videos to 3.22 seconds. Seeking to the end removed every spatial overlay; seeking back to 3.18 restored the eligible aggregate regions |
| UI copy and diagnostics | Visible page text and title tooltips contained neither prior nor the corresponding Chinese term. Actual model/review metadata remains available through the API. No warning/error logs were observed |
| Runtime timing | Four event-window task times were 1697.8, 813.4, 753.8, and 768.5 ms. Model construction and HTTP/persistence are excluded; the first call includes CUDA warmup. These are not sustained live FPS or accuracy measurements |
| Deployment | Updated upstream Python sources and showcase static assets are deployed; both remote services are active. No human review facts were saved or overwritten |

## Historical migration validation — 2026-10-02

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

## Historical timestamp-independent experiment

At this historical diagnostic snapshot, the video path excluded annotated event timestamps from model sampling, spatial aggregation, temporal localization, and playback focus. Legacy capture metadata remains readable for old manifests, but it has no effect on inference. The existing classifier searches the complete common playable interval with 0.96-second windows and a 0.48-second stride, including the final tail. It selects a positive-class window first, then ranks by offence probability. This sliding classifier search is not a newly trained event detector and does not establish localization accuracy.

| Check | Evidence |
| --- | --- |
| Frontend regression suite | All 29 Node tests passed; types, formatting, production build, and whitespace checks passed |
| Remote upstream regression suite | All 126 multi-view and event tests passed on the CUDA host, including prior independence, common ranges, VFR timestamps, positive-class ranking, view-order mapping, and unavailable localization |
| Real timestamp-independence comparison | The same three-view clip was analyzed with ignored legacy timestamps of 0 and 999 seconds. Both scans covered 0–5.04 seconds in 10 windows, selected window 4, and returned the same action, sanction, view weights, and model-derived timestamp of 1.98 seconds |
| Real scan timing | Scan task times were 5,199.9 ms and 4,289.8 ms. They include metadata, decoding, preprocessing, all window forwards, attribution, and lock wait; first model initialization, persistence, and HTTP serialization are excluded |
| Browser acceptance | Real three-view playback retained identical media times; English spatial/temporal labels were visible, historical event anchors were absent, and the desktop had no horizontal overflow or new warning/error logs |
| Deployment at that snapshot | The diagnostic upstream analysis implementation and showcase production assets were deployed; both remote services were active |

The timing values describe two individual full-clip scans, not sustained real-time frame rates. Analysis acceptance did not save or overwrite human review facts. At that snapshot, archived event-prior focus was suppressed and fresh analysis used model-only focus. The current restored configuration accepts explicitly returned review intervals again.

## Historical attribution quality experiment

The previous diagnostic backend corrected its Transformer attribution target. The final MViT block output has zero patch-token gradients because the head reads CLS; the previous CLS-inclusive channel weighting could still produce a misleading spatial response. That experiment excluded CLS and combined pre-attention `norm1` patch CAMs from blocks 10 and 15. It aligns the 7-by-7 semantic grid to the native 14-by-14 finer grid, then normalizes each aligned layer independently per view and averages them equally. That experiment did not interpolate time or introduce an event prior. Target layers, actual gradient statistics, and fusion ordering are retained in diagnostics.

| Check | Executed evidence |
| --- | --- |
| Frontend regressions | All 33 Node tests passed; types, formatting, production build, and whitespace checks passed |
| Upstream CUDA regressions | All 158 multi-view and event tests passed, including patch-only gradients, multiple resolutions and normalization order, hook cleanup, disconnected layers, spatial-bin geometry, offsets, and timestamp independence |
| Actual model path | Four installed official cases used the two configured layers, genuine patch gradients, and an 8-by-14-by-14 fused CAM; every returned region passed protocol validation |
| Classification stability | Each case retained its original classification, selected scan window, and offence probability. Attribution peaks changed with the corrected CAM |
| Real browser acceptance | After a complete reload and fresh CUDA analysis, all four videos paused at 2.46 seconds. Three eligible views rendered actual spatial-bin rectangles; the low-signal primary view remained hidden. Synchronized stepping, moving outside the sample window, resuming playback, and seeking back restored or removed regions correctly. No warning/error logs were observed |
| Pause and seek state | Loaded paused frames survive late buffer-wait events, and ready paused media events clear the parent waiting flag. Policy tests cover ready, seeking, loading, and playing states |

A counterfactual diagnostic masked approximately 20% of the largest versus smallest CAM tokens in every view of the same actual selected batch. The metric is the target-logit drop under the high-response mask minus its drop under the low-response mask; masks use nearest-neighbor expansion and normalized-zero fill. The fused method scored 0.246702, 0.586329, 0.658935, and 2.084101 on official cases 183, 2, 78, and 144, respectively, compared with 0.129886, 0.169182, 0.001513, and -0.204896 for the old implementation. All four improved over the old method. This small development set was used to select the layer combination and is not a held-out accuracy benchmark.

The model still explains a clip classifier rather than locating contact or tracking players. Source-video rectangles can highlight background cues; the center crop covers roughly half the width of a 16:9 frame. Only the selected 0.96-second inference window has spatial samples, spaced by 0.12 seconds. Low-signal regions remain labeled or hidden. Case 2 still receives a yellow-card model suggestion. Neither deletion diagnostics nor improved rendering establish correct sanction or contact localization.

In that historical experiment, the four individual full-clip tasks took 4,184.1–5,691.4 ms; attribution took 140.8–362.9 ms on the remote RTX 5060 Ti. These timings exclude initial model construction and HTTP/persistence work and do not establish sustained live FPS. Runtime reports, source videos, model weights, and screenshots remain outside Git. The showcase's extracted `backend_core` inference algorithms remain unchanged; this update consumes the upstream spatial-bin protocol.
