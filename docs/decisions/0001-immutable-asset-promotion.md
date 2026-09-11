# ADR-0001: Promote browser uploads into immutable asset keys

## Status

Accepted

## Context

Presigned browser uploads and PostgreSQL transactions cannot commit atomically.
Writing directly to the final object key allowed a replayed or stale upload URL
to replace bytes referenced by a completed generation or final video.

## Decision

Store the final immutable key in PostgreSQL, upload browser bytes to a
deterministic staging key, validate size/content/checksum/media, and conditionally
promote to the immutable key before setting the row `READY`. The reconciler
repeats either incomplete half after a crash.

## Consequences

Staging cleanup is safe only after reconciliation. Final manifests retain both
the object key/checksum and relational references, so deleting referenced audio
or video assets is rejected.
