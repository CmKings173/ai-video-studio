"use client";

import type { AssetDTO } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function OrderedReferences({ label, tag, assets, selected, limit, onChange }: {
  label: string; tag: string; assets: AssetDTO[]; selected: string[]; limit: number;
  onChange: (ids: string[]) => void;
}) {
  const { t } = useI18n();
  function move(index: number, offset: number) {
    const ordered = [...selected];
    [ordered[index], ordered[index + offset]] = [ordered[index + offset], ordered[index]];
    onChange(ordered);
  }
  return <fieldset className="space-y-2 rounded-md border border-[#2c3038] p-3">
    <legend className="px-1 text-sm">{label} · {selected.length}/{limit}</legend>
    {selected.map((id, index) => <div key={id} className="flex flex-wrap items-center gap-2 text-xs">
      <span className="min-w-0 flex-1 break-words">@{tag}{index + 1} · {assets.find((asset) => asset.id === id)?.filename ?? id}</span>
      <Button type="button" variant="ghost" size="sm" disabled={index === 0} aria-label={t(`Đưa ${label} ${index + 1} lên`, `Move ${label} ${index + 1} up`)} onClick={() => move(index, -1)}>↑</Button>
      <Button type="button" variant="ghost" size="sm" disabled={index === selected.length - 1} aria-label={t(`Đưa ${label} ${index + 1} xuống`, `Move ${label} ${index + 1} down`)} onClick={() => move(index, 1)}>↓</Button>
      <Button type="button" variant="ghost" size="sm" aria-label={t(`Bỏ ${label} ${index + 1}`, `Remove ${label} ${index + 1}`)} onClick={() => onChange(selected.filter((item) => item !== id))}>{t("Bỏ", "Remove")}</Button>
    </div>)}
    <select aria-label={t(`Thêm ${label}`, `Add ${label}`)} value="" disabled={selected.length >= limit}
      className="w-full rounded-md border border-[#2c3038] bg-[#0b101a] p-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
      onChange={(event) => { if (event.target.value) onChange([...selected, event.target.value]); }}>
      <option value="">{t("Chọn tài nguyên theo thứ tự…", "Select assets in order…")}</option>
      {assets.filter((asset) => !selected.includes(asset.id)).map((asset) => <option key={asset.id} value={asset.id}>{asset.filename}</option>)}
    </select>
    {limit === 0 && <p className="text-xs text-[#9ea5b0]">{t("Chưa có workflow được xác minh cho loại tài nguyên tham chiếu này.", "No verified workflow supports this reference type.")}</p>}
  </fieldset>;
}
