import type { GenerationRequest, PromptPreviewDTO } from "@/lib/api/types";

/** A preview belongs to one scene, revision pair and ordered generation input. */
export interface PromptContext {
  readonly sceneId: string;
  readonly sceneRevision: number;
  readonly videoId: string;
  readonly videoRevision: number;
  readonly settingsJSON: string;
}

export interface AcceptedPrompt {
  readonly context: PromptContext;
  readonly text: string;
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
): PromptContext {
  return Object.freeze({ sceneId, sceneRevision, videoId, videoRevision,
    settingsJSON: JSON.stringify(canonical(settings)) });
}

export function isAcceptedPromptCurrent(accepted: AcceptedPrompt | null, current: PromptContext): boolean {
  const saved = accepted?.context;
  return !!saved && saved.sceneId === current.sceneId && saved.videoId === current.videoId
    && saved.sceneRevision === current.sceneRevision && saved.videoRevision === current.videoRevision
    && saved.settingsJSON === current.settingsJSON;
}

export function acceptPrompt(context: PromptContext, preview: PromptPreviewDTO, text: string): AcceptedPrompt {
  if (preview.scene_revision !== context.sceneRevision || preview.video_revision !== context.videoRevision) {
    throw new Error("Prompt preview is stale; preview again before accepting.");
  }
  if (!text.trim() || [...text].length > 30000) {
    throw new Error("Prompt must contain text and at most 30000 characters.");
  }
  return Object.freeze({ context: Object.freeze({ ...context }), text });
}

export function acceptedGenerationRequest(accepted: AcceptedPrompt, current: PromptContext): GenerationRequest {
  if (!isAcceptedPromptCurrent(accepted, current)) {
    throw new Error("Prompt preview is stale; preview again before generating.");
  }
  return { ...JSON.parse(accepted.context.settingsJSON), execution_prompt: accepted.text,
    source_scene_revision: accepted.context.sceneRevision, source_video_revision: accepted.context.videoRevision };
}
