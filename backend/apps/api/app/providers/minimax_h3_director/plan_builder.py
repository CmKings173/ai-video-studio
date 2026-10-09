from __future__ import annotations

from typing import Any

from apps.api.app.services.generation_intent import GenerationIntent

from .capabilities import DIRECTOR_SOURCE
from .contracts import DirectorExecutionSpec
from .qualification import require_settings


class DirectorPlanBuilder:
    """Freeze provider-specific execution data without depending on ComfyUI node ids."""

    PROVIDER_VERSION = "director-contract-v1"

    def build(
        self,
        intent: GenerationIntent,
        *,
        output_prefix: str,
        workflow: Any,
        profile_hash: str,
        custom_node_versions: dict[str, str] | None = None,
        defer_aggregate_qualification: bool = False,
    ) -> DirectorExecutionSpec:
        if intent.audio_policy.get("mode") == "source" and intent.provider_task not in {
            "r2v",
            "v2v",
            "rv2v",
        }:
            raise ValueError("Source audio policy is unsupported for this Director task")
        if intent.provider_task == "rv2v" and any(
            asset.role == "REFERENCE_VIDEO" for asset in intent.assets
        ):
            raise ValueError(
                "Director RV2V uses the source clip as Video 1; "
                "additional reference videos are unsupported"
            )
        if not defer_aggregate_qualification:
            require_settings(
                workflow.profile or {},
                {
                    "motion_context": intent.motion_context,
                    "refine": intent.refine,
                    "face_refine": intent.face_refine,
                    "audio_policy": intent.audio_policy,
                },
                base_canvas=(intent.canvas.width, intent.canvas.height),
                task=intent.provider_task,
            )
        return DirectorExecutionSpec.finalize(
            provider_version=self.PROVIDER_VERSION,
            source_repository=DIRECTOR_SOURCE["repository"],
            source_commit=DIRECTOR_SOURCE["commit"],
            task=intent.provider_task,
            requested_mode=intent.requested_mode,
            prompt=intent.prompt,
            negative_prompt=intent.negative_prompt,
            prompt_provenance=intent.prompt_provenance,
            canvas=intent.canvas,
            quality_profile=intent.quality_profile,
            seed=intent.seed,
            seed_policy=intent.seed_policy,
            steps=intent.steps,
            cfg=intent.cfg,
            fps=intent.fps,
            frames=intent.frames,
            requested_duration_seconds=intent.requested_duration_seconds,
            resolved_duration_seconds=intent.resolved_duration_seconds,
            assets=intent.assets,
            timeline=intent.timeline,
            motion_context=dict(intent.motion_context),
            refine=dict(intent.refine),
            face_refine=dict(intent.face_refine),
            audio_policy=dict(intent.audio_policy),
            output_prefix=output_prefix,
            workflow_id=str(workflow.id),
            workflow_code=str(workflow.code),
            workflow_version=str(workflow.version),
            workflow_hash=str(workflow.workflow_hash),
            slot_map_hash=str(workflow.slot_map_hash),
            profile_hash=profile_hash,
            custom_node_versions=dict(custom_node_versions or {}),
            provenance={
                "source": DIRECTOR_SOURCE,
                "workflow_profile": dict(workflow.profile or {}),
            },
            intent_hash=intent.semantic_hash,
        )
