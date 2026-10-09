import type { VideoDTO } from "@/lib/api/types";

// The pointer preserves the latest promoted artifact; READY certifies currency.
export function isCurrentFinal(
  video: Pick<VideoDTO, "status" | "current_final_video_id"> | undefined,
  finalId: string,
): boolean {
  return video?.status === "READY" && video.current_final_video_id === finalId;
}
