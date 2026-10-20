# First drive

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

## Pre-start boundary: historical controller

The bundled `src/orin` is the historical **runs 27–31 DINO snapshot**, with known unresolved issues and different source from the truck's later autostart. Read its [manifest](../src/orin/MANIFEST.md#known-issues-in-this-snapshot). The [public outputs-disabled demo](first-checkpoint.md) is a separate software route.

The powered recipes below are **historical/reference only**. Before powered use, require research/source-owner review of the intended controller and separate supervised bench/field acceptance. That acceptance must include real neutral/stop, kill and manual vetoes, lane loss, stale intent, route end, link/heartbeat loss, process failure, shutdown and recovery. Historical operating rules do not close these gaps.

- With `--owner simple`, lane loss or stale steering intent can straighten steering while leaving speed-dial throttle. `route_end`, `laps_done` and `path_invalid` do not guarantee a stop. Wheel touch overrides **steering only**, not motor throttle. <!-- src/orin/owner_simple.py:51-56,110-142; src/orin/live_trail.py:2872-2883 -->
- Ctrl-C can wait for another camera frame while the output owner continues sending. A hard process kill skips normal release. Do not use process exit or a status flag as proof of motor neutral: the connected FC/receiver configuration determines what happens after release. Stop with the kill dial or mode switch first, then end the process and observe actual motor neutral. Keep kill available, never leave the powered truck unattended, and bring the speed dial to the bottom at the first lost lane or stalled stream. Intended-controller review and supervised stopping/shutdown observations remain necessary. <!-- src/orin/MANIFEST.md:67-70; src/orin/live_trail.py:3038-3040 -->
- Stop phone recording before resetting the host; the encoder has wedged on an Orin power cycle. The phone allows one stream client, so on the historical autostart truck keep AUTO_OFF present before starting another client. See [power isolation](power-and-safety.md#commissioning-and-isolation) and [autostart details](orin-setup.md#autostart-and-the-pause-file). <!-- F:1142, F:2970 -->
- The separate October 8 field kit had powered use but remains undistributed. Its transmitter-loss fixture retained throttle and receipt freshness cannot detect every stale-image prediction. See [field-kit details](ros.md#how-it-connects-to-the-orin-stack); neither its history nor shadow diagnostics validates the DINO snapshot.

Robot Eyes [beta access and streaming](ros.md#robot-eyes-the-phone-app) remain unverified here, and the [OVAL4 head](../model/README.md#getting-the-head-file) is unpublished. These prerequisites concern the historical DINO path; they do not supply the separate field kit.

This page preserves the historical procedure around the first powered drive of the truck: what was needed, the power-up order, and checks that came out of measured failures. It states no result figures. For results see [results](research/results.md); for the full list of failures and fixes see [pitfalls](pitfalls.md); for the kill path and rails see [power-and-safety](power-and-safety.md).

The head used in the recorded drive was trained by the phone's ARKit pose, with no LiDAR and no human labels, and reads camera pixels only <!-- C:160-163 -->; the instruments and graders are on [method](research/method.md).

## Historical pre-power reference — gated

On the research truck, the recorded bench rule kept AUTO_OFF present and the [kill dial](radio-and-controller.md) ready before power-on. Autostart is not supplied; its boot/arming distinction and pause behaviour are recorded in [Orin setup](orin-setup.md#autostart-and-the-pause-file). <!-- F:2976, F:2978 -->

## Historical manual-start reference — gated

This release ships historical Orin code but not autostart. The recorded recipe copied `src/orin` to the Orin, installed its pins, placed the steering head (not yet published; see [model](../model/README.md#getting-the-head-file)) and launched `live_trail.py` by hand with `--owner simple` and `--cam-fwd 0.24`, as recorded in [Install the stack from src/orin](orin-setup.md#install-the-stack-from-srcorin) <!-- src/orin/live_trail.py:1405 at f6ff42a, src/orin/live_trail.py:1679 at f6ff42a -->. The truck's own autostart deploys a later file <!-- src/orin/MANIFEST.md:13 at f6ff42a -->.

## Historical power-up order — gated

| Step | Do | Why |
|---|---|---|
| 1 | Radio on. | The receiver must see the radio first <!-- F:2346 -->. |
| 2 | Battery pack in. | The flight controller boots from the pack, so the receiver boots with it <!-- F:2346 -->. |
| 3 | Orin on. | It comes last, after the radio link exists <!-- F:2346 -->. |

## Historical wheels-up bench reference

The historical first bench drive used wheels off the ground. The kill switch was proven under load with the rear wheels up <!-- F:826 -->, and the first closed-loop run on the bench was also done wheels up <!-- F:827 -->. A later supervised acceptance must prove the kill switch under load before floor operation; this historical result is not acceptance of another setup.

## Historical checks before closed-loop authority

| Check | What to do | Why |
|---|---|---|
| Motion check | Confirm the truck can actually move before a closed-loop speed controller gets authority. | A speed controller with no stall detector wound its output up to 1709 µs and held it for 26.4 s into a truck that never moved <!-- F:1927 -->. A speed loop pushes harder into a blocked vehicle, not softer. |
| Steering centre | Use 1463 µs as straight, not 1500. | On this truck the straight-ahead servo pulse is 1463 µs <!-- F:971, C:458, C:628 -->. |

See [calibration](calibration.md) for the steering numbers, and [pitfalls](pitfalls.md) for related failures.
