# H3 benchmark protocol

Benchmark execution is intentionally separate from smoke tests because it
requires the target GPU, ComfyUI server, model files and reference media.

Run on the target workstation after the offline preflight passes:

```powershell
.venv\Scripts\python.exe -m apps.api.management.benchmark_h3 --all --execute `
  --repeat 3 --resolution 480x864 --duration 5 --steps 8 `
  --media FIRST_FRAME=D:\media\first.png --media LAST_FRAME=D:\media\last.png
```

The command writes `benchmark.json` with workflow/model hashes, prompt IDs,
wall time, execution time, frame count, requested matrix values and peak VRAM
observed from ComfyUI system stats. Record p50/p95 execution time, failure rate
and peak VRAM for each mode/resolution/duration/steps combination before
enabling a workflow in production.

Status: **NOT_RUN** in this development environment.
