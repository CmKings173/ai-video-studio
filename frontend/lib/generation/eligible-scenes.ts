import type { GenerationDTO, GenerationRequest, SceneDTO } from "@/lib/api/types";
import { reuseGenerationSettings } from "@/lib/generation/history-settings";

/** Native chains contain enabled scenes with actual adjacent storyboard positions. */
export function continuityChains(scenes: SceneDTO[]): SceneDTO[][] {
  const chains: SceneDTO[][] = [];
  const ordered = [...scenes].filter((scene) => scene.enabled)
    .sort((a, b) => a.scene_order - b.scene_order || a.id.localeCompare(b.id));
  for (const scene of ordered) {
    const previous = chains.at(-1);
    const last = previous?.at(-1);
    if (previous && last && scene.spec?.continuity === "CONTINUOUS"
      && last.scene_order === scene.scene_order - 1) {
      previous.push(scene);
    } else {
      chains.push([scene]);
    }
  }
  return chains;
}

export function sceneRequiresAggregate(scene: SceneDTO, scenes: SceneDTO[]): boolean {
  return continuityChains(scenes).some((chain) => chain.length > 1 && chain.some((member) => member.id === scene.id));
}

export function directGenerationRequiresAggregate(scene: SceneDTO, scenes: SceneDTO[], input: Pick<GenerationRequest, "motion_context"> = scene.generation_config ?? {}): boolean {
  return sceneRequiresAggregate(scene, scenes) || (input?.motion_context ?? scene.generation_config?.motion_context)?.enabled === true;
}

export function historyGenerationRequiresAggregate(scene: SceneDTO, scenes: SceneDTO[], parent: GenerationDTO): boolean {
  return directGenerationRequiresAggregate(scene, scenes)
    || directGenerationRequiresAggregate(scene, scenes, reuseGenerationSettings(parent));
}

/** A stale native member requires regenerating its complete continuity chain. */
export function eligibleScenes(scenes: SceneDTO[]): SceneDTO[] {
  return continuityChains(scenes).filter((chain) => chain.some((scene) => scene.selected_generation_fresh !== true)).flat();
}
