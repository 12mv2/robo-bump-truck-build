# Model: the steering head

<!-- code cites in this page: src/orin/live_trail.py at f6ff42a, the snapshot of trail-stack commit 92fd500 -->

Author: Colin Rooney · © 2026 Rooney Industries LLC

This page preserves the project's own historical steering-head record: the oval head (`OVAL4`). It is a small logistic head that reads the features of a frozen DINOv2-S/14 backbone. The build path is in the [README](../README.md); how the head fits into the truck is on [system](../docs/system.md).

## The file

| Item | Value |
|---|---|
| Name | the oval head (`OVAL4`) <!-- C:351 --> |
| File | `centre_head_lag0_r8c_v1_OVAL4.npy` <!-- docs/reports/2026-10-02-model-identity.md:11 --> |
| Size | 3,208 bytes <!-- docs/reports/2026-10-02-model-identity.md:11 --> |
| sha256 (whole file) | `4b78e28749ca465b37a3ede775a272daa84e8cc7b793f7fc627ba00a83926777` <!-- docs/reports/2026-10-02-model-identity.md:11, B32:15 --> |
| Parameters | 385: one weight for each of the backbone's 384 features, plus one bias <!-- C:75-76 --> |
| Kind | logistic head: a per-patch probability through a sigmoid <!-- src/orin/live_trail.py:2165 at f6ff42a, src/orin/live_trail.py:2595-2596 at f6ff42a --> |
| Backbone | DINOv2-S/14 (ViT-S/14), frozen: the backbone is not retrained <!-- C:75, C:38 --> |
| Runtime input | RGB patch features only: the head never sees depth <!-- C:77-78, C:163 --> |

The runs log a 12-character hash for the head. That hash is taken over the parameter array, not over the file, so it will not match the file's sha256 above <!-- src/orin/live_trail.py:2216 at f6ff42a, docs/reports/2026-10-02-model-identity.md:11 -->. Check the file with the whole-file sha256.

## Input size

The head's input is 476×350 pixels, which the backbone cuts into a 34×25 grid of 14-pixel patches <!-- C:75-76, src/orin/live_trail.py:233-234 at f6ff42a -->. That is not the stream size. The phone streams 480×360 at 15 fps, and every frame is resized to 476×350 before the backbone sees it <!-- S:245 -->.

## Output

For each frame the head gives a probability for every patch in rows 8 to 24 of the grid: 17 rows of 34 patches <!-- src/orin/live_trail.py:2166-2167 at f6ff42a, src/orin/live_trail.py:1389 at f6ff42a, F:1005 -->. The steering law fits a line to that output and pure pursuit turns it into one curvature; see [system](../docs/system.md).

## How the truck loads it

| Step | What happens |
|---|---|
| 1 | The controller is started with the head file's path. NumPy loads the file: the first 384 values are the weights, the last is the bias <!-- src/orin/live_trail.py:2165 at f6ff42a --> |
| 2 | DINOv2-S/14 loads through `torch.hub` from the upstream code and weights held in the Orin's torch.hub cache <!-- docs/reports/2026-10-02-model-identity.md:9-10, src/orin/live_trail.py:2175 at f6ff42a --> |
| 3 | The backbone's weights file, `dinov2_vits14_pretrain.pth`, is 88,283,115 bytes <!-- docs/reports/2026-10-02-model-identity.md:9 -->. On CUDA the backbone runs in half precision (fp16) <!-- src/orin/live_trail.py:2176 at f6ff42a --> |
| 4 | There is no ONNX or TensorRT engine in the perception path: PyTorch loads the weights file directly <!-- docs/reports/2026-10-02-model-identity.md:14 --> |

The backbone runs in 18.2 ms in fp16 on the Orin, 37 to 40 % of a 15 fps frame <!-- S:245 -->. The standing truck setup that uses this head runs a lookahead of 0.6 m and a gain of 1.6 <!-- S:56 -->.

## Training

| Part | What is on record |
|---|---|
| Data | Four sessions on the kitchen's blue-tape oval: 207 m, 20 laps, 2,838 frames, three sessions clockwise and one counter-clockwise <!-- F:996-997 -->. Trained on 2026-09-08 <!-- F:994 --> |
| Labels | ARKit pose alone: the pure-pursuit curvature to a point 3 m along where the truck actually went <!-- C:160 -->. No LiDAR, and no manual image labeling <!-- C:161-162 --> |
| Colour fusion | It replaced 0 to 1 % of rows indoors, so on the oval the labels are effectively pose only <!-- F:1011 --> |
| Fit | A logistic head on frozen features. About 2 minutes, fully local <!-- C:77 -->; 62 to 145 s per session on a Mac's GPU <!-- F:1011 --> |

**Not on record:** the trainer settings (regularisation, iteration limit, class weighting) that produced this exact file. A trainer for the leave-one-session-out evaluation exists, but no line ties its settings to `OVAL4`, so none are given here.

Teacher: the phone's ARKit pose trained this head; LiDAR taught only the earlier phone-bump head, and neither head sees depth when it runs <!-- C:160-163, C:167-168 -->. The graders, and how the phone-bump labels were made, are on [method](../docs/research/method.md).

## What was measured

Everything here is replay: each session was held out in turn and the head was scored on how it would have turned compared with how Colin drove. None of it is driving. Whether the head steers the oval in closed loop is on [results](../docs/research/results.md) <!-- F:1012 -->.

| Measurement | Result |
|---|---|
| Column error against the driver | 3 to 6 cm (0.48 to 0.75 patches) <!-- F:1031, F:1005, C:543-544 --> |
| Correlation with the driver | +0.49 <!-- F:1031, C:543-544 --> |
| Oval data alone against oval plus trail data | Oval alone is better on the oval: mean held-out correlation +0.487 against +0.440 <!-- F:1006 --> |
| Signed column error | At most 0.30 patches (1.7 cm) in either direction <!-- F:1008 --> |
| Under-turning | The head turns less than the driver did, 3 to 5 times harder counter-clockwise than clockwise <!-- F:1008 -->. The corpus has three clockwise sessions against one counter-clockwise <!-- F:997 --> |

An earlier AUC for this head was inflated by easy negatives and is withdrawn; it is not quoted here <!-- C:540-545 -->.

## What it does not cover

The oval head is an oval head. Nothing measured on it transfers outdoors <!-- C:783-784 -->; see [cannot do yet](../docs/research/cannot-do-yet.md#outdoor-transfer-of-the-oval-result).

## Background: the phone-bump head

Before the truck drove, a separate head on the same backbone, also 385 parameters, learned "what must I not hit?" from bumps of a hand-held phone: the accelerometer says when, LiDAR says where <!-- C:157-160, C:75-77 -->. LiDAR is not in the oval head's labels <!-- C:161 -->. That work is on [results](../docs/research/results.md#background-the-phone-bump-experiment-this-project-grew-from).

## Getting the head file

Not yet published. OVAL4 belongs to the historical DINO snapshot described here; it does not supply the separate later perception runtime.

Collaborator weights are not included in this repository. Robot Eyes remains closed source; the [public ROS repository](https://github.com/12mv2/robo-bump-truck-ros#build-and-test) is authoritative for setup, build, demo and replay guidance.

For voluntary external model contributions, see the small [community model-link catalog](community.md), currently empty. A link is not a claim of compatibility or endorsement.

## Licence

| Part | Licence |
|---|---|
| The head weights (`OVAL4`) | MIT, © 2026 Rooney Industries LLC, the same licence as the [README](../README.md) |
| The backbone, DINOv2-S/14 | Apache License 2.0, by Meta <!-- facebookresearch/dinov2/README.md:660, facebookresearch/dinov2/MODEL_CARD.md:32 --> |

The backbone is not part of this repository. The truck loads it from upstream, [facebookresearch/dinov2](https://github.com/facebookresearch/dinov2), and it is not redistributed here. The attribution is in [NOTICE](NOTICE).

The upstream repository carries other licences for other models. The X-ray and cell DINO variants are under non-commercial licences, not Apache 2.0 <!-- facebookresearch/dinov2/README.md:160, facebookresearch/dinov2/README.md:756-757 -->. Check the licence of any checkpoint other than DINOv2-S/14 before you use it.
