"use client";

import { useI18n } from "@/lib/i18n";
import type { VideoDetail } from "@/lib/api/types";
import { batchSummary } from "@/lib/generation/batch-summary";
import { executionGroups } from "@/lib/generation/workspace-state";

export function BatchSummary({ video }: { video: VideoDetail }) {
  const { t } = useI18n();
  const qualityLabel = (value: string) => ({ DRAFT: t("Bản nháp", "Draft"), STANDARD: t("Tiêu chuẩn", "Standard"), HIGH: t("Cao", "High"), BASE: t("Cơ bản", "Base"), HD: t("HD", "HD"), FULL_HD_REFINED: t("Full HD đã tinh chỉnh", "Refined Full HD"), CUSTOM: t("Tùy chỉnh", "Custom") } as Record<string, string>)[value] ?? value;
  const rows = batchSummary(video);
  const groups = executionGroups(video.scenes).filter((group) => group.eligible);
  return <details className="rounded-md border border-[#2c3038] bg-[#181a1e] p-3 text-xs">
    <summary className="cursor-pointer font-semibold rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">{t(`Tạo toàn bộ · ${rows.length} cảnh cần tạo`, `Generate all · ${rows.length} scenes to generate`)}</summary>
    <p className="my-3 text-[#9ea5b0]">{t("Mỗi cảnh dùng cấu hình đã lưu. Nếu một thành viên đã thay đổi, cả nhóm liên tục được tạo lại. Cảnh vô hiệu và nhóm có toàn bộ clip mới được bỏ qua.", "Each scene uses its saved settings. If one member has changed, the whole continuity group is regenerated. Disabled scenes and groups with all fresh clips are skipped.")}</p>
    <ul className="space-y-2 mb-3">{groups.map((group) => <li key={group.members[0].id}>
      <strong>{group.label}</strong> · {group.execution} · {group.reasons.join("; ")}
    </li>)}</ul>
    <div className="overflow-x-auto"><table className="w-full text-left"><thead><tr>{[t("Cảnh", "Scene"), t("Chế độ", "Mode"), t("Chất lượng", "Quality"), t("Tỷ lệ", "Aspect ratio"), t("Thời lượng", "Duration"), t("Seed", "Seed")].map((label) => <th key={label} className="p-2">{label}</th>)}</tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id} className="border-t border-[#2c3038]"><td className="p-2">{row.order}</td><td className="p-2">{row.mode}</td><td className="p-2">{qualityLabel(row.quality)}</td><td className="p-2">{row.ratio === "Custom" ? t("Tùy chỉnh", "Custom") : row.ratio}</td><td className="p-2">{row.seconds}s</td><td className="p-2">{row.seed ?? t("Ngẫu nhiên", "Random")}</td></tr>)}</tbody>
    </table></div>
  </details>;
}
