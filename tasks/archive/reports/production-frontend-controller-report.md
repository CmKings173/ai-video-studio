# Generation editor checkpoint, Oct6

Current feature checkout, HEAD01b740d, no commits/push/reset/clean. This checkpoint implements generation UI against the final backend contract report. Assembly delivery selectors and real enhancement capability integration are pending the delivery slice, so this is not completion of the full frontend brief.

Implemented: authenticated capability discovery, Auto/Text/First/Last/Both/References mode choices, DRAFT/STANDARD/HIGH and all seven public generation ratios, random/fixed safe-integer seeds; no primary raw step control. The selected executable workflow ID is pinned into preview and accepted generation settings. Unqualified combinations display an explicit unavailable state and block preview/generation. Scene configuration can still be saved as a draft. Ordered reference selectors expose @ImageN/@VideoN/@AudioN with reorder/remove and graph-bounded capacities; stream durations/FPS/category sums are validated without container fallback. The backend remains authoritative.

`accepted-prompt.ts` freezes scene/video ID, revision pair and canonical ordered settings, keeps the accepted text byte-for-byte, rejects stale revision/config/order and returns a fresh request copy. The actual editor captures each preview request's context; late responses from old settings cannot be accepted. Editing text requires acceptance again. Parent creation actions retain idempotency keys across ambiguous retries. Creation keys include scene identity. Variation sends only parent ID; regeneration targets the frozen parent endpoint; reuse explicitly revision-patches a new scene config without mutating parent history. History displays declared vs observed metadata, exact frozen prompt, gated parent actions and real download links. Enhancement is explicitly unavailable until the dedicated backend capability slice is integrated.

Generate All retains all Phase0 retry/completion semantics. A deterministic summary sorts eligible enabled/unselected scenes and displays each stored mode/profile/ratio/duration/seed. Existing read-only Prompt AI cache now keys scene and video revisions.

## Changed files

- frontend/lib/api/types.ts and generations.ts: DTO/config/capability/last-frame contracts, preview settings body and regeneration endpoint.
- frontend/lib/generation/{accepted-prompt,capabilities,history-settings,batch-summary}.ts: pure semantic helpers.
- frontend/components/generation/{generation-editor,ordered-references,history-details,batch-summary}.tsx: focused UI components.
- frontend/app/videos/[videoId]/page.tsx: editor/history/batch wiring, minimal parent requests and target identity; obsolete monolithic generation dialog removed.
- frontend/tests/{accepted-prompt,generation-capabilities,history-settings,generation-actions,generation-editor,batch-summary}.test.cjs: semantic and actual component/mutation boundary regressions.

## Verification

- TDD helper imports initially fail, then 9 accepted-prompt,7 capability,2 reuse and1 batch tests pass. Four actual editor callback tests use synthetic capability/preview boundaries only; no target execution claimed. Three actual page mutation tests verify exact accepted text, scene-separated keys, minimal parent body and ambiguous retries.
- `yarn install --frozen-lockfile --offline`: already up-to-date,0.41s.
- Latest `yarn test`:53passed,0skipped,3.51s. Existing27Phase0 cases remain green.
- `yarn typecheck`:PASS3.15s after correcting the input-only type of mode derivation (nullable scene ratio is not a generation request override).
- `yarn lint`:PASS7.92s. Initial scoped lint/typecheck also PASS.
- `node tests/build-preserving-next-env.cjs`: first full build PASS; final build result appended below. No user-owned next-env bytes changed; SHA2560f70629890b72a0a82e91972cc032c04b658b26c265373cb711cf576bfbf8fcc before and after browser dev run.

## Actual browser proof

Real isolated API fixture, new SQLite and current metadata model, no enabled workflows/workers/operator data. API session63850/pid26140, frontend session76313, loopback18080/18030. IAB real login and video1e29f9a9-73c1-40af-ad81-88bc27b23b6a with three scenes. Changed scene0 to AUTO/HIGH/16:9/FIXED42 through the form, PATCH returned200; scene/video revisions1->2. Reopening restored all values. Read-only SQLite verification:scene0revision2 exact config,scene1/2revision1 empty config. Capabilities/assets reads returned200; unqualified mode options and preview disabled. Browser error/warning logs[] at the checkpoint. No GPU generation/qualified prompt browser flow was simulated; that flow has component-boundary tests instead.

Screenshot: `C:/Users/Admin/.codex/visualizations/2026/10/05/01a10b2b-cb15-7183-928f-04c39ebbb46b/production-scene-config-saved.png` (also embedded to user). Mobile screenshot `production-scene-config-mobile.png`. Actual measured modal geometry:320viewport/288wide/271client=scroll;768viewport/736wide/719client=scroll;1024viewport/768wide/751client=scroll, so no horizontal modal overflow. The1440requested override was observed as768viewport and is not claimed as1440verification. Viewport reset, owned fixture tab closed, both owned server sessions stopped withCtrl-C. ChatGPT conversation/tunnel untouched.

Final preserving production build PASS: compiled2.2s, TypeScript4.8s, generated11pages with all routes. Same next-env SHA256 verified afterward. Latest history buttons additionally use live capabilities to gate parent re-execution; Variation moved beside Regenerate in the history component. Batch summary added and prompt-cache revisions corrected before the final tests/typecheck/build. Independent Carver review active, only its report writable.

Pending: independent checkpoint review, delivery/enhancement integration, final frontend/full-branch tests and target/deployment UAT. NOT_READY for production.
