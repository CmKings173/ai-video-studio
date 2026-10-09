# Director execution and current output contract

`workflow_registry.execution_scope` is the registry identity for `single_scene`
and `aggregate`. Enabled uniqueness is `(mode, quality_profile, execution_scope)`.
Importers infer scope only for old manifests that omit it. Runtime selection uses
the explicit field and rejects a conflicting provider export configuration.

Migration `a4c8e0f2b6d1` backfills profiles exporting `segments` as `aggregate`,
and all other profiles as `single_scene`. Downgrade refuses simultaneous enabled
scope variants or identities the old export-mode rule cannot represent. Disable
one variant deliberately before downgrading; no migration silently disables it.

Aggregate templates use a single identity per task/profile and a required native
segment binding with `coverage: all_members`. The actual member count is frozen in
the run and execution spec. Measured qualification must still match that count,
continuity boundaries, settings and provider provenance exactly. A dynamic graph
does not qualify an unmeasured member count. Historical fixed bindings remain
readable for already frozen executions.

Generate All returns ordered `execution_groups`, each containing `execution_scope`,
`scene_ids`, `generation_ids`, and an optional `director_run_id`. A CONTINUOUS chain
stays together; CUT starts a new group. Every member and aggregate snapshot is
prepared and qualified before any generation or run is persisted. Persistence uses
the same frozen prompts, seeds and run snapshots in the batch transaction.

The top-level `director_run_id` is deprecated compatibility data. It is populated
only when exactly one aggregate run exists, including batches that also contain
standalone groups. Clients must use `execution_groups` for complete membership and
multiple runs. Older idempotent responses without groups remain readable.

`SceneDTO.selected_generation_fresh` expresses whether the selected output is
completed, available and corresponds to current semantic source inputs. A saved
generation fingerprint covers scene prompt, negative prompt, spec, configuration,
duration, generation-related video settings and effective Product/Brand context.
Editor revision counters, output selection and unrelated presentation changes are
excluded. Generations without this identity are historical and stale until regenerated.

The fingerprint also covers the complete native continuity chain. Editing any
member invalidates outputs that depended on that aggregate plan. Selected outputs
in a native chain must belong to the same frozen execution group; source-equivalent
outputs from different random-seed executions cannot be mixed silently.

Generate All includes scenes without a fresh selected output and expands automatic
eligibility to each affected native continuity chain. The frontend pins that expanded
scene set and all its revision preconditions. Explicit incomplete selections still
receive the predecessor validation error rather than silently breaking continuity.
New assembly rejects
stale selection with `SCENE_SELECTION_STALE`. Historical selection and generation
rows remain available. Variations and regenerations inherit their parent's source
identity, so historical inputs cannot become current by stamping a new revision.
Already enqueued assembly keeps its immutable manifest; publishing its artifact as
current requires the selected source inputs to remain current as well.
