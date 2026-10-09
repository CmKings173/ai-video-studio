# Studio Director native manifest bridge

Copy this directory to `ComfyUI/custom_nodes/studio_director_bridge` on the target
runtime. Install Director commit `a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb`
separately. The bridge checks hashes of the source execution/export seam files.
Source drift fails closed; changing the pin requires fresh qualification.

Inputs and execution delegate to the source Director. A ContextVar captures the
exact plan at its existing finalize seam. `studio_director_artifacts` in ComfyUI
history identifies role, member_index, filename, subfolder and type for each
native MP4 belonging to that plan. There is no report parsing or directory scan.
Required segment files must all exist. The source checkout remains intact.

Generate disabled candidate graphs with:

```powershell
python -m apps.api.scripts.import_director_templates --source D:/project/ComfyUI_MiniMaxH3_Director --output ./workflows/h3 --aggregate-members 2
```

Profiles bind `transport=studio_native_segments_v1`, `member_index=0` and the
exact segment count to the bridge node. Optional pre-refine/pre-face bindings
use the same transport and must cover all members when present. These graphs
require one timeline segment per scene member. Partial runs and missing native
exports fail closed. Template import does not approve execution: aggregate
settings/output geometry need measured runtime evidence, including Motion
Context, Refine and FaceRefine combinations.

Manifest accounting has unit coverage. The bridge has **not** run on GPU ComfyUI.
