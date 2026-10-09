/** Reserve a new tab during the click gesture before awaiting an API call. */
export function reservePresignedDownloadTab(): Window | null {
  const tab = window.open("about:blank", "_blank");
  if (tab) tab.opener = null;
  return tab;
}

export function navigatePresignedDownload(tab: Window, url: string): void {
  if (tab.closed) throw new Error("Download tab was closed");
  tab.location.replace(url);
}

export function closePresignedDownloadTab(tab: Window | null): void {
  if (tab && !tab.closed) tab.close();
}
