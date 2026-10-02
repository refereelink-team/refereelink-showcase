# Frontend Design and Validation Record

Validation date: 2026-10-02 (Asia/Taipei). The showcase frontend uses React, Vite, and TypeScript. Model inference, experiment jobs, and serial services are called through the remote gateway on port 8010. The local presentation machine runs only the npm frontend.

## Design reference

Four complete ImageGen concepts define the visual reference: `multiview.png`, `tracking.png`, `foul.png`, and `ball.png`. These concepts and implementation screenshots remain in the task's local visualizations `showcase-concepts` directory. They are not committed as production UI or media assets.

The design uses a cool-gray `#f4f5f7` background, white workspaces, `#1d2433` body text, `#e4e7ec` dividers, and `#baf45b` primary actions. It uses no gradients, glows, or card shadows. Fonts are Inter, system fallbacks, and PingFang SC. Desktop titles are 30 px, tabs 14 px, primary buttons 13 px, and inspector titles 17 px; mobile titles are 24 px. Outer spacing is 24 px, with a single divider between the main content and inspector. Players preserve the source aspect ratio without cropping diagnostic overlays or pitch projections.

Native concept images and desktop screenshots are **1536 × 1024**. The mobile viewport is **390 × 844**; `render-mobile.jpg` captures the complete page at that width.

## Visual comparison ledger

All four concepts and the latest implementation screenshots were inspected with `view_image` during the same QA round. Builds and interaction checks do not substitute for visual comparison.

| Comparison | Concept reference | Implementation evidence and treatment |
| --- | --- | --- |
| Background and color temperature | Cool white/gray with lime actions in all four concepts | The four rendered pages preserve that palette without added gradients, warm backgrounds, or media tints. |
| Browser-style tabs | Four horizontal tabs in the tracking, foul, and ball concepts | Four semantic tabs use a white selected state and a short lime indicator; keyboard navigation and hash routing remain available. The multi-view concept's browser address bar is not reproduced inside the page. |
| Typography and hierarchy | Large title, short subtitle, compact inspector | Browser-computed values confirm title 30 px/700/39 px, tabs 14 px/650/21 px, buttons 13 px/550/19.5 px, and inspector titles 17 px/650/23.8 px. Mobile titles are 24 px. |
| Layout and containers | Main media on the left, open inspector on the right, one divider | The shared workspace and open inspector remain, without adding dashboard cards. Desktop live mode uses three input columns so both RTSP cameras and the phone are visible. |
| Multi-view workflow | Video/live selection left of the title; views and cases beneath the main player | The mode switch was moved to the left of the title to match the reference density. Views and cases are selectable. Model analysis and human review remain separate; offline live mode does not keep displaying an old video's analysis. |
| Media treatment | Actual match footage carries the main task | Supplied clips and real CUDA outputs replace the concept's illustrative match image. Letterboxing preserves the original aspect ratio; concept images do not stand in for videos. |
| Tracking and 2D projection | An additional empty pitch area in the concept | The extra empty pitch was removed to meet the user's same-video requirement. Actual annotated output combines tracking, teams, projection, and unknown states in one video. |
| Foul candidates | Event timeline below the player; event details in the inspector | Actual NDJSON media PTS drives the timeline. Selecting candidate 3 seeks precisely to 2.434 s. The page displays `foul_only` results and separates candidates from human decisions. |
| Positioning and waveforms | Meter-based plane, three anchors, XYZ waveforms at the lower right | Anchors follow the service configuration: (0,0), (8,0), and (4,7). Snapshot data drives the waveforms, with top/middle/bottom numeric ticks. The page explains sensor coordinates and included gravity when Z is approximately 9.8 m/s². |
| Responsive layout | The same visual system across sizes | At 390 px, media and inspector stack vertically. All four routes have `document.scrollWidth === 390`, with no horizontal overflow. |

### Intentional first-screen copy differences

The main page, action, and inspector names retain their intended meanings. Recorded differences remove the concept date, artificial browser address, and illustrative match branding; use actual case names, durations, model metrics, job states, and candidate counts; and add necessary source and offline information. Only the multi-view page retains the video/live switch. The tracking workflow refers to detecting players because ball detection is disabled in the current experiment. Penalties come from rule evaluation of human-entered facts, and an additional-facts disclosure covers the actual backend protocol. Positioning distinguishes demonstration data from device data and explains that acceleration uses sensor coordinates and includes gravity. These changes follow actual data, the user's clarified scope, and required functionality; they do not add decorative features.

## Browser validation

Browser and Chromium checks at `http://localhost:5174` exercised keyboard navigation, desktop/mobile layouts, and actual remote jobs.

- All four tabs supported clicks, Home/ArrowRight/End navigation, and hash routing.
- Actual video playback produced decoded frames and an advancing clock. The tracking video's duration was 22.733333 s; playback reached 1.057309 s with 37 decoded frames and a native resolution of 1272 × 480. Fullscreen entry and Escape exit both passed.
- Selecting candidate 3 produced `HTMLVideoElement.currentTime === 2.434`. A 1.5× playback rate remained 1.5 after switching between source and output video. Volume, speed, seeking, and fullscreen used actual video controls.
- The UI created tracking job `2c9b478594794f7c98605787c4fdeac0`, displayed actual running progress, and reached the completed state: 682 frames at 18.677169 FPS. Returning to the page restored the job. The foul page loaded a completed real foul job with seven candidates.
- The multi-view UI posted `{case_id:"soccernet_action_183",device:"cuda"}` and received HTTP 200/ok, analysis `analysis-soccernet_action_183-ad8a2f2c528c`, model MViT_V2_S, device CUDA, confidence 0.7759, and `inference_ms` 361.3. The complete response remains in `multiview-analysis-receipt.json`. No existing human decision was saved or overwritten. By protocol, GET review still shows the historical analysis associated with that review; this does not indicate failure of the new analysis POST.
- All three physical inputs reported no reception and a 0.0 s buffer. Capture and analysis were disabled while the preview awaited input. Prepared media did not masquerade as live input.
- Saving the unchanged anchor configuration succeeded. Explicitly starting the service demonstration produced simulation/WebSocket updates, with UWB/IMU labeled as demonstration playback. Stopping it through the UI returned `source_mode` to `idle`, both connections to disconnected/stale, `position_valid` and `acceleration_valid` to false, and `hardware_verified` to false.
- All four mobile routes passed the 390 px overflow check. The full positioning-page screenshot confirmed readable ordering, controls, and waveforms.

## Checks and limitations

`npm run lint`, `npm test` (6/6), `npm run format:check`, and `npm run build` passed. Tests cover timeout cancellation, service-error and review-conflict details, rejection of non-JSON success responses, media gateway routing, false hardware-verification states, and candidate-timeline PTS. Production output is built into `web/dist`.

This QA round found no remaining material visual or frontend-function defects that could be corrected within the tested scope. The implementation was compared against the design references with the documented data and functional differences. Physical camera connectivity, UWB/IMU hardware, and per-frame model accuracy were not accepted by frontend QA. To preserve existing user facts, browser QA did not write human reviews; backend tests cover compare-and-swap behavior and rule evaluation. Screenshots establish UI, media, and protocol behavior. A demonstration trajectory does not establish physical-device positioning accuracy.
