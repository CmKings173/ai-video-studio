"use client";

import { useEffect, useSyncExternalStore, type ReactNode } from "react";

export type Locale = "vi" | "en";
export const LOCALE_KEY = "ai-video-studio.locale";
const CHANGE_EVENT = "studio-locale-change";
let memoryLocale: Locale = "vi";

export function getLocaleSnapshot(): Locale {
  if (typeof window === "undefined") return "vi";
  try {
    const saved = window.localStorage.getItem(LOCALE_KEY);
    return saved === "en" ? "en" : saved === "vi" ? "vi" : memoryLocale;
  } catch { return memoryLocale; }
}

export function setAppLocale(locale: Locale) {
  if (locale !== "vi" && locale !== "en") return;
  memoryLocale = locale;
  if (typeof window === "undefined") return;
  try { window.localStorage.setItem(LOCALE_KEY, locale); } catch { /* Session preference works without storage. */ }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeLocale(onChange: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key === LOCALE_KEY || event.key === null) {
      memoryLocale = "vi";
      onChange();
    }
  };
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

export function useI18n() {
  const locale = useSyncExternalStore(subscribeLocale, getLocaleSnapshot, () => "vi" as Locale);
  return { locale, setLocale: setAppLocale, t: (vi: string, en: string) => locale === "en" ? en : vi };
}

/** For client-side validation messages produced outside a React component. */
export function translateText(vi: string, en: string) {
  return getLocaleSnapshot() === "en" ? en : vi;
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const { locale } = useI18n();
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  return children;
}
