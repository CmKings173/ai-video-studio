import type { GenerationDTO, SceneDTO } from "@/lib/api/types";
import { continuityChains } from "./eligible-scenes";
import { translateText as t } from "@/lib/i18n";

export function selectionState(scene: Pick<SceneDTO, "selected_generation_id" | "selected_generation_fresh">) {
  return !scene.selected_generation_id ? "NO_SELECTION" : scene.selected_generation_fresh === true ? "FRESH_SELECTION" : "STALE_SELECTION";
}

export async function loadSelectedGeneration(
  id: string, items: GenerationDTO[], fetch: (id: string) => Promise<GenerationDTO>,
) {
  return items.find((item) => item.id === id) ?? await fetch(id);
}

export function editSceneSpec(
  spec: SceneDTO["spec"], changes: Partial<SceneDTO["spec"]>, order: number,
): SceneDTO["spec"] {
  return { ...spec, ...changes, continuity: order === 0 ? "CUT" : changes.continuity ?? spec?.continuity ?? "CUT" };
}

export function executionGroups(scenes: SceneDTO[]) {
  return continuityChains(scenes).map((members) => {
    const first = members[0].scene_order + 1;
    const last = members[members.length - 1].scene_order + 1;
    return {
      members,
      label: t(`Cảnh ${first}${first === last ? "" : `–${last}`}`, `Scene ${first}${first === last ? "" : `–${last}`}`),
      execution: members.length > 1 || members.some((scene) => scene.generation_config?.motion_context?.enabled) ? t("Nhóm Director", "Director aggregate") : t("Độc lập", "Standalone"),
      eligible: members.some((scene) => scene.selected_generation_fresh !== true),
      reasons: members.filter((scene) => scene.selected_generation_fresh !== true).map((scene) =>
        t(`Cảnh ${scene.scene_order + 1} ${selectionState(scene) === "NO_SELECTION" ? "chưa chọn clip" : "đã thay đổi"}`, `Scene ${scene.scene_order + 1} ${selectionState(scene) === "NO_SELECTION" ? "has no selected clip" : "has changed"}`)),
    };
  });
}

export function aggregateGuidance(scene: SceneDTO, scenes: SceneDTO[]) {
  const group = executionGroups(scenes).find((group) => group.members.some((member) => member.id === scene.id));
  return t(`${group?.label ?? `Cảnh ${scene.scene_order + 1}`} cần chạy cùng nhóm Director${group && group.members.length > 1 ? " liên tục" : " Motion Context"}. Lưu cấu hình rồi dùng Tạo toàn bộ.`,
    `${group?.label ?? `Scene ${scene.scene_order + 1}`} must run with its Director ${group && group.members.length > 1 ? "continuity" : "Motion Context"} group. Save settings, then use Generate All.`);
}
