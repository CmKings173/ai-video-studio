import { z } from "zod";

const dimension = z.coerce.number().int().min(256).max(4096).multipleOf(2);
const common = {
  fit_mode: z.enum(["FIT_PAD", "CENTER_CROP"]),
  transition: z.enum(["CUT", "CROSSFADE"]),
  crossfade_seconds: z.coerce.number().min(0.1).max(1.5),
  audio_mode: z.enum(["KEEP_SCENE_AUDIO", "MUTE_SCENE_AUDIO"]),
  background_audio_asset_id: z.string().optional().nullable(),
  background_volume: z.coerce.number().min(0).max(1),
  fps: z.coerce.number().refine(
    (value): value is 24 | 25 | 30 => value === 24 || value === 25 || value === 30,
    { message: "FPS must be 24, 25, or 30" },
  ),
};

export const assemblyFormSchema = z.union([
  z.object({ ...common, delivery_preset: z.literal(""), width: dimension, height: dimension }),
  z.object({
    ...common,
    delivery_preset: z.enum(["SOCIAL_VERTICAL_1080", "LANDSCAPE_FHD", "SQUARE_1080", "PORTRAIT_4_5", "PORTRAIT_3_4", "LANDSCAPE_4_3", "ULTRAWIDE_2560_1080"]),
    // Presets are resolved by the API. Hidden custom values do not participate.
    width: z.unknown().transform(() => 256),
    height: z.unknown().transform(() => 256),
  }),
]);

export type AssemblyFormValues = z.infer<typeof assemblyFormSchema>;
