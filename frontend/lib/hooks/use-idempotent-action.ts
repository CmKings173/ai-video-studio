import { useRef, useCallback } from "react";
import { generateIdempotencyKey } from "@/lib/api/client";

function stableStringify(obj: unknown): string {
  if (obj === null || typeof obj !== "object") {
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    return `[${obj.map(stableStringify).join(",")}]`;
  }
  const keys = Object.keys(obj as Record<string, unknown>).sort();
  return `{${keys
    .map((k) => `${JSON.stringify(k)}:${stableStringify((obj as Record<string, unknown>)[k])}`)
    .join(",")}}`;
}

/**
 * Custom hook to maintain idempotency semantics across mutation actions.
 *
 * Invariants:
 * - Same logical action + same payload retry -> same Idempotency-Key
 * - Changed payload -> new Idempotency-Key
 * - Success -> reset key for the next action
 */
export function useIdempotentAction<TPayload = unknown>() {
  const currentKeyRef = useRef<string>(generateIdempotencyKey());
  const lastFingerprintRef = useRef<string | null>(null);

  const getKey = useCallback((payload?: TPayload): string => {
    const fingerprint = payload !== undefined ? stableStringify(payload) : null;
    if (
      fingerprint !== null &&
      lastFingerprintRef.current !== null &&
      fingerprint !== lastFingerprintRef.current
    ) {
      // User modified payload semantics; issue a new idempotency key
      currentKeyRef.current = generateIdempotencyKey();
    }
    if (fingerprint !== null) {
      lastFingerprintRef.current = fingerprint;
    }
    return currentKeyRef.current;
  }, []);

  const reset = useCallback(() => {
    currentKeyRef.current = generateIdempotencyKey();
    lastFingerprintRef.current = null;
  }, []);

  return {
    getKey,
    reset,
  };
}
