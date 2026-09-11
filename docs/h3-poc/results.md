# H3 / ComfyUI compatibility gate

| Mode | Workflow | Preflight | Executed | Frames | Result |
| --- | --- | --- | --- | ---: | --- |
| t2v | `H3_T2V_STANDARD@2026-09-10-reference` | yes | no | 124 | PASS |
| i2v | `H3_I2V_STANDARD@2026-09-10-reference` | yes | no | 124 | PASS |
| i2v_first_last | `H3_FIRST_LAST_STANDARD@2026-09-10-reference` | yes | no | 124 | PASS |
| r2v | `H3_R2V_1_IMAGE_REFERENCE@2026-09-10-reference` | yes | no | 124 | PASS |

Reference workflows stay disabled until every enabled mode has an executed PASS on the target H3/ComfyUI workstation and its model/custom-node hashes are recorded.
