-- Read-only inventory; does not infer actors or transfer ownership.
-- Run separately against the intended deployment with a read-only DB role.
WITH inconsistent_videos AS (
    SELECT v.id, v.project_id, v.product_id, v.brand_id, v.created_by,
           p.created_by AS project_owner,
           pr.created_by AS product_owner,
           pb.created_by AS product_brand_owner,
           vb.created_by AS explicit_brand_owner
    FROM videos v
    LEFT JOIN projects p ON p.id = v.project_id
    LEFT JOIN products pr ON pr.id = v.product_id
    LEFT JOIN brands pb ON pb.id = pr.brand_id
    LEFT JOIN brands vb ON vb.id = v.brand_id
    WHERE p.id IS NULL OR p.created_by <> v.created_by
       OR (v.product_id IS NOT NULL AND
           (pr.id IS NULL OR pr.created_by <> v.created_by))
       OR (pr.brand_id IS NOT NULL AND
           (pb.id IS NULL OR pb.created_by <> v.created_by))
       OR (v.brand_id IS NOT NULL AND
           (vb.id IS NULL OR vb.created_by <> v.created_by))
)
SELECT iv.*, 'GENERATION' AS child_kind, g.id AS child_id,
       g.output_asset_id AS output_asset_id
FROM inconsistent_videos iv
LEFT JOIN scene_generations g ON g.video_id = iv.id
UNION ALL
SELECT iv.*, 'DIRECTOR_RUN', dr.id, NULL
FROM inconsistent_videos iv JOIN director_runs dr ON dr.video_id = iv.id
UNION ALL
SELECT iv.*, 'FINAL_VIDEO', f.id, f.output_asset_id
FROM inconsistent_videos iv JOIN final_videos f ON f.video_id = iv.id
ORDER BY id, child_kind, child_id;

-- Read-only rollout check for active/retryable jobs that point at an output
-- created before provenance was introduced. Do not update these rows here.
WITH output_links AS (
    SELECT 'GENERATION' AS parent_kind, g.id AS parent_id, g.status AS parent_status,
           g.video_id, g.output_asset_id AS asset_id
    FROM scene_generations g
    WHERE g.output_asset_id IS NOT NULL
    UNION ALL
    -- A retry may encounter persisted output intent before the parent pointer
    -- was attached. The worker's object-key prefix identifies a review
    -- candidate only; it is not ownership evidence or authorization.
    SELECT 'GENERATION_UNLINKED', g.id, g.status, g.video_id, a.id
    FROM scene_generations g
    JOIN assets a ON a.role = 'GENERATED_VIDEO'
        AND a.object_key LIKE 'outputs/generated_video/' || g.id || '/%'
    WHERE g.output_asset_id IS DISTINCT FROM a.id
    UNION ALL
    SELECT 'DIRECTOR_GENERATION', g.id, g.status, g.video_id, ga.asset_id
    FROM scene_generations g
    JOIN generation_assets ga ON ga.generation_id = g.id
    WHERE left(ga.role, 9) = 'DIRECTOR_'
    UNION ALL
    SELECT 'FINAL_VIDEO', f.id, f.status, f.video_id, f.output_asset_id
    FROM final_videos f
    WHERE f.output_asset_id IS NOT NULL
    UNION ALL
    SELECT 'FINAL_VIDEO_UNLINKED', f.id, f.status, f.video_id, a.id
    FROM final_videos f
    JOIN assets a ON a.role = 'FINAL_VIDEO'
        AND a.object_key LIKE 'outputs/final_video/' || f.id || '/%'
    WHERE f.output_asset_id IS DISTINCT FROM a.id
)
SELECT link.parent_kind, link.parent_id, link.parent_status, link.video_id,
       v.created_by AS video_owner, p.created_by AS project_owner,
       a.id AS asset_id, a.status AS asset_status, a.created_by AS asset_actor,
       a.resource_owner_id, a.ownership_provenance
FROM output_links link
JOIN videos v ON v.id = link.video_id
LEFT JOIN projects p ON p.id = v.project_id
JOIN assets a ON a.id = link.asset_id
WHERE a.ownership_provenance IS NULL
  AND (
      (link.parent_kind IN ('GENERATION', 'GENERATION_UNLINKED', 'DIRECTOR_GENERATION')
       AND link.parent_status IN ('CREATED', 'DISPATCHING', 'QUEUED', 'RUNNING', 'COLLECTING'))
      OR (link.parent_kind IN ('FINAL_VIDEO', 'FINAL_VIDEO_UNLINKED')
          AND link.parent_status IN ('QUEUED', 'ASSEMBLING', 'CANCEL_REQUESTED'))
  )
ORDER BY link.parent_kind, link.parent_status, link.parent_id, a.id;
