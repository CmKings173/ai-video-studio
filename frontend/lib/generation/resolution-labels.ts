type ResolutionKind = "native" | "output";
type JsonRecord = Record<string, unknown>;

const marketingLabels = new Map<string, string>([
  ["854x480", "480p"],
  ["480x854", "480p"],
  ["1280x720", "720p HD"],
  ["720x1280", "720p HD"],
  ["1920x1080", "1080p Full HD"],
  ["1080x1920", "1080p Full HD"],
  ["2560x1440", "1440p QHD"],
  ["1440x2560", "1440p QHD"],
  ["3840x2160", "2160p 4K UHD"],
  ["2160x3840", "2160p 4K UHD"],
]);

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function validDimensions(width: unknown, height: unknown): [number, number] | null {
  if (typeof width !== "number" || !Number.isSafeInteger(width) || width <= 0
    || typeof height !== "number" || !Number.isSafeInteger(height) || height <= 0) return null;
  return [width, height];
}

export function formatDimensions(width: unknown, height: unknown): string | null {
  const dimensions = validDimensions(width, height);
  return dimensions ? `${dimensions[0]} x ${dimensions[1]} px` : null;
}

function formatResolution(width: unknown, height: unknown): string | null {
  const dimensions = validDimensions(width, height);
  const exact = formatDimensions(width, height);
  if (!dimensions || !exact) return null;
  const label = marketingLabels.get(`${dimensions[0]}x${dimensions[1]}`);
  return label ? `${label} - ${exact}` : exact;
}

export function formatResolvedResolution(
  configuration: unknown,
  kind: ResolutionKind = "output",
): string | null {
  const values = record(configuration);
  if (!values) return null;
  return kind === "native"
    ? formatResolution(values.native_width, values.native_height)
    : formatResolution(values.expected_output_width, values.expected_output_height);
}

function formatRecordResolution(value: unknown, widthKey: string, heightKey: string): string | null {
  const item = record(value);
  return item ? formatResolution(item[widthKey], item[heightKey]) : null;
}

function formatSnapshotFields(item: JsonRecord, kind: ResolutionKind): string | null {
  const resolved = record(item.resolved_configuration);
  if (resolved) {
    const formatted = formatResolvedResolution(resolved, kind);
    if (formatted) return formatted;
  }

  const historicalDimensionKeys: ReadonlyArray<readonly [string, string]> = kind === "output"
    ? [["expected_output_width", "expected_output_height"], ["resolved_width", "resolved_height"], ["width", "height"]]
    : [["native_width", "native_height"], ["resolved_width", "resolved_height"], ["width", "height"]];
  for (const [widthKey, heightKey] of historicalDimensionKeys) {
    const formatted = formatRecordResolution(item, widthKey, heightKey);
    if (formatted) return formatted;
  }

  // Frozen pre-DTO snapshots kept executed canvases at these explicit paths.
  const frozenCanvas = formatRecordResolution(record(item.director_execution)?.canvas, "width", "height");
  if (frozenCanvas) return frozenCanvas;
  const specCanvas = formatRecordResolution(record(item.director_execution_spec)?.generation, "width", "height");
  if (specCanvas) return specCanvas;

  if (resolved && kind === "output") {
    const native = formatResolvedResolution(resolved, "native");
    if (native) return native;
  }
  return null;
}

export function formatHistoricalResolution(snapshot: unknown, kind: ResolutionKind = "output"): string | null {
  const item = record(snapshot);
  if (!item) return null;
  const formatted = formatSnapshotFields(item, kind);
  if (formatted) return formatted;

  // Generation history DTOs wrap the immutable execution snapshot here.
  const inputSnapshot = record(item.input_snapshot);
  return inputSnapshot ? formatSnapshotFields(inputSnapshot, kind) : null;
}
