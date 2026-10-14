import type { GenerationRequest, PromptPreviewDTO } from "@/lib/api/types";
import { isGenerationConfigurationHash } from "@/lib/generation/capabilities";

/** A preview belongs to one scene, revision pair and ordered generation input. */
export interface PromptContext {
  readonly sceneId: string;
  readonly sceneRevision: number;
  readonly videoId: string;
  readonly videoRevision: number;
  readonly settingsJSON: string;
  readonly resolvedConfigurationHash: string | null;
}

export interface AcceptedPrompt {
  readonly context: PromptContext;
  readonly text: string;
  readonly previewConfigurationHash: string;
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
      .filter(([, item]) => item !== undefined).map(([key, item]) => [key, canonical(item)]));
  }
  return value;
}

export function createPromptContext(
  sceneId: string, sceneRevision: number, videoId: string, videoRevision: number,
  settings: GenerationRequest,
  resolvedConfigurationHash: string | null = null,
): PromptContext {
  return Object.freeze({ sceneId, sceneRevision, videoId, videoRevision,
    settingsJSON: JSON.stringify(canonical(settings)), resolvedConfigurationHash });
}

export function isAcceptedPromptCurrent(accepted: Pick<AcceptedPrompt, "context"> | null, current: PromptContext): boolean {
  const saved = accepted?.context;
  return !!saved && saved.sceneId === current.sceneId && saved.videoId === current.videoId
    && saved.sceneRevision === current.sceneRevision && saved.videoRevision === current.videoRevision
    && saved.settingsJSON === current.settingsJSON
    && saved.resolvedConfigurationHash === current.resolvedConfigurationHash;
}

export function acceptPrompt(context: PromptContext, preview: PromptPreviewDTO, text: string): AcceptedPrompt {
  const configuration = preview.resolved_configuration;
  const previewConfigurationHash = configuration?.configuration_hash;
  if (!configuration?.runnable || !isGenerationConfigurationHash(previewConfigurationHash)) {
    throw new Error("A runnable server-resolved configuration with a configuration hash is required.");
  }
  if (context.resolvedConfigurationHash && context.resolvedConfigurationHash !== previewConfigurationHash) {
    throw new Error("The resolved generation configuration changed; preview again before accepting.");
  }
  if (preview.scene_revision !== context.sceneRevision || preview.video_revision !== context.videoRevision) {
    throw new Error("Prompt preview is stale; preview again before accepting.");
  }
  if (!text.trim() || [...text].length > 30000) {
    throw new Error("Prompt must contain text and at most 30000 characters.");
  }
  return Object.freeze({
    context: Object.freeze({ ...context, resolvedConfigurationHash: previewConfigurationHash }),
    text,
    previewConfigurationHash,
  });
}

export function acceptedGenerationRequest(accepted: AcceptedPrompt, current: PromptContext): GenerationRequest {
  if (!isAcceptedPromptCurrent(accepted, current)) {
    throw new Error("Prompt preview is stale; preview again before generating.");
  }
  return { ...JSON.parse(accepted.context.settingsJSON), preview_configuration_hash: accepted.previewConfigurationHash,
    execution_prompt: accepted.text,
    source_scene_revision: accepted.context.sceneRevision, source_video_revision: accepted.context.videoRevision };
}
