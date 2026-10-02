# Guided multi-view adjudication

The right-hand inspector guides a reviewer through the most consequential facts first. It uses the existing synchronized evidence player and the upstream rule service; it does not replace either with a separate demonstration implementation.

## Review flow

1. **Action:** establish whether an offence occurred, then identify the action and whether contact occurred. A confirmed no-offence answer goes directly to the result.
2. **Severity:** establish carelessness, recklessness or excessive force, and whether the ball was in play. A dive omits the irrelevant intensity question. **Next** continues to the location supplement by default. Only confirmed no-offence or dive paths bypass questions that do not apply.
3. **Location, optional:** mark a point on the existing pitch. Offender team and home-team defending direction appear only when penalty-area ownership is relevant. Coordinates, clear and undo retain the existing pitch controls.
4. **Context, optional:** identify ordinary play, stopping a promising attack (SPA), or denying an obvious goal-scoring opportunity (DOGSO). Whether the offender attempted to play the ball is requested only when it affects the outcome. Victim team and contact region are available under additional details.
5. **Recommendation:** distinguish conclusions supported by the confirmed facts from conditional outcomes. Missing-fact links return to the question that can narrow the recommendation.

Every core question permits **Unknown**. The default flow visits both supplement pages before showing the recommendation; optional means their fields may remain blank, not that the pages are omitted. Next, Back and Skip do not confirm facts or save a revision. Skipping an optional page preserves any values already entered. Draft facts and the current step survive switching cases during the mounted page session; they do not survive a browser reload. The synchronized cameras, timeline and Grad-CAM presentation remain independent of the guide.

## Suggestions and incomplete facts

Model suggestions retain their source and confidence until the reviewer accepts or edits them. **Accept this page's suggestions** confirms only visible, nonblank and unconfirmed model fields. Next never silently accepts a suggestion. Manual changes use human provenance and are protected from later model prefill. Newly supplied suggestions receive a brief highlight; reduced-motion settings disable its animation.

The result shows restart and sanction independently. A conclusion is definite only when the remaining unknown facts cannot change it. Conditional cards state their assumptions explicitly and merge equivalent outcomes. Three cards are initially visible; additional alternatives can be expanded. If the core facts are too sparse, the guide asks the most useful next question instead of enumerating every possible action.

Missing location never becomes a guessed coordinate. For example, a supported physical offence may produce a penalty recommendation **if it occurred in the offender's own penalty area**, and a direct free-kick recommendation otherwise. Other unresolved conditions remain visible. Unsupported rule paths are identified as requiring manual review rather than silently assuming contact.

Incomplete, conflicting or conditional results save as **Awaiting confirmation** (the existing `uncertain` review state). A definite restart and sanction with no blocking conflict can be explicitly confirmed. Optional facts that do not affect either conclusion may remain unknown. Confidence, camera weights, runtime diagnostics and the rule trace are available in the result's evidence disclosure.

## API and compatibility

The read-only POST endpoint at `POST /api/multiview/cases/{case_id}/review/preview` evaluates the current draft without creating a review revision. Its response contains `assessment`, `restart_resolution`, `sanction_resolution`, `scenarios`, `required_facts`, `optional_facts`, `next_fact` and `can_finalize`. Each resolution is `resolved`, `conditional` or `unavailable`; conditional scenarios retain their assumptions, outstanding unknowns and rule trace. The frontend debounces edits by 200 ms and discards responses from an earlier draft or case visit. A failed preview preserves the draft and does not enable confirmation using an old result.

Preview and strict saving share the rule-dependency logic. Conditions are evaluated in a separate hypothetical context: neither hypotheses nor fabricated location points are written into the supplied facts or saved record. Preview does not invoke CUDA inference or a language model. Conditional wording uses deterministic templates; existing saved-record explanations remain revision-bound.

The existing review PUT request accepts an additive `preserve_unknowns` flag. Existing clients retain their previous behavior when the flag is omitted. The guide sends `preserve_unknowns: true`, preventing an action label from automatically confirming contact and preventing explicitly supplied victim-team facts from being overwritten. The service reevaluates eligibility at save time instead of trusting the client's selected review state. Revision conflicts retain the draft and never trigger an automatic overwrite.

When historical records contain rule-derived contact or victim-team values, the guide copies them into the draft as unconfirmed suggestions. It does not modify the historical saved record. Explicit human values and confirmed model values keep their provenance.

## Validation boundaries

Frontend regression checks run on macOS with `npm test`, `npm run lint`, `npm run format:check` and `npm run build`. Helper tests cover optional traversal, confirmed no-offence branching, legacy-value handling, page-scoped adoption, provenance and preview request generations. Browser acceptance additionally covers the short flow, skipped supplements, return links, case switching, keyboard interaction, narrow layouts and existing video synchronization.

Python rule and API checks run on the remote CUDA host. They must cover unknown location and ownership, SPA/DOGSO dependencies, irrelevant missing facts, unsupported paths, strict-save compatibility, unchanged input facts and non-persisting preview requests. These software checks do not establish model accuracy, sustained live frame rates or physical-device acceptance. User footage, runtime credentials and screenshots remain outside Git.
