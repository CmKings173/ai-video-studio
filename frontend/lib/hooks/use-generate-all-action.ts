import { useRef } from "react";
import { prepareGenerateAll } from "@/lib/api/generations";
import type { VideoDetail } from "@/lib/api/types";
import { useIdempotentAction } from "./use-idempotent-action";

type Prepared = ReturnType<typeof prepareGenerateAll>;

/** Keep an ambiguous action's payload/revisions pinned through output refreshes. */
export function useGenerateAllAction() {
  const editorAction = useIdempotentAction<Prepared["editorInputs"]>();
  const requestAction = useIdempotentAction<Prepared["fingerprint"]>();
  const pending = useRef<{ editorKey: string; batch: Prepared } | null>(null);
  const refreshRequired = useRef(false);
  function reset() {
    pending.current = null;
    editorAction.reset();
    requestAction.reset();
    refreshRequired.current = false;
  }
  return {
    async prepare(video: VideoDetail, refreshVideo: () => Promise<VideoDetail>) {
      if (refreshRequired.current) {
        video = await refreshVideo();
        refreshRequired.current = false;
      }
      const candidate = prepareGenerateAll(video);
      const editorKey = editorAction.getKey(candidate.editorInputs);
      if (!pending.current || pending.current.editorKey !== editorKey) {
        pending.current = { editorKey, batch: candidate };
      }
      const batch = pending.current.batch;
      return { payload: batch.payload, key: requestAction.getKey(batch.fingerprint) };
    },
    reset,
    retire() {
      reset();
      refreshRequired.current = true;
    },
  };
}
