"use client";

import React from "react";

export interface TabsProps {
  tabs: { id: string; label: string; count?: number }[];
  activeTab: string;
  onChange: (id: string) => void;
  className?: string;
}

export function Tabs({ tabs, activeTab, onChange, className = "" }: TabsProps) {
  return (
    <div className={`flex min-w-0 max-w-full flex-nowrap items-center gap-1 overflow-x-auto border-b border-[#2c3038] ${className}`}>
      {tabs.map((tab) => {
        const isActive = activeTab === tab.id;
        return (
          <button
            type="button"
            aria-pressed={isActive}
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={`flex shrink-0 whitespace-nowrap min-h-[36px] items-center gap-2 rounded-none border-b px-3 text-sm font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-400 ${
              isActive
                ? "border-[#2563eb] bg-[#22252b] text-[#f1f3f5] font-semibold"
                : "border-transparent text-[#9ea5b0] hover:bg-[#22252b] hover:text-[#f1f3f5]"
            }`}
          >
            <span>{tab.label}</span>
            {tab.count !== undefined && (
              <span
                className={`text-xs px-1.5 py-0.2 rounded-full ${
                  isActive ? "bg-[#2563eb] text-white" : "bg-[#22252b] text-[#9ea5b0]"
                }`}
              >
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
