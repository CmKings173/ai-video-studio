"use client";

import { useRef, useState } from "react";
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";

// Share the same recovery policy for edit and archive on both detail screens.
export function useRevisionRecovery<T>({ refetch, dirty, onRecovered }: {
  refetch: () => Promise<{ data?: T; error?: unknown; isError?: boolean }>;
  dirty: boolean;
  onRecovered: (data: T) => void;
}) {
  const [error, setError] = useState<unknown>(null);
  const [reloadError, setReloadError] = useState<unknown>(null);
  const [recovering, setRecovering] = useState(false);
  const inFlight = useRef(false);
  const conflict = isRevisionConflict(error);

  const recover = async () => {
    if (inFlight.current) return;
    if (dirty && !confirm("Tải lại sẽ thay thế các thay đổi chưa lưu bằng dữ liệu mới nhất. Bạn có muốn tiếp tục?")) return;
    inFlight.current = true;
    setRecovering(true);
    setReloadError(null);
    try {
      const result = await refetch();
      if (result.isError || result.error || !result.data) {
        setReloadError(result.error ?? new Error("Không tải được dữ liệu mới nhất. Bản nháp vẫn được giữ."));
        return;
      }
      onRecovered(result.data);
      setError(null);
    } catch (err) {
      setReloadError(err);
    } finally {
      inFlight.current = false;
      setRecovering(false);
    }
  };

  return {
    message: error ? getErrorMessage(error) : null,
    reloadMessage: reloadError ? getErrorMessage(reloadError) : null,
    conflict, recovering, recover,
    blocked: conflict || recovering,
    fail: (err: unknown) => { setError(err); setReloadError(null); },
    clear: () => { if (!conflict && !inFlight.current) { setError(null); setReloadError(null); } },
  };
}
