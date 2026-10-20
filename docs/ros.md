# ROS 2 interface and Robot Eyes

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

This page covers the separate ROS 2 repository for the truck: what it does, how it would connect to the Orin stack, where its steering sign differs from the truck controller, and where to find its maintained installation instructions. It also covers the phone app, Robot Eyes, as the truck uses it. This page links the ROS repository; it does not merge it.

Repository: [robo-bump-truck-ros](https://github.com/12mv2/robo-bump-truck-ros)

## Start here

Follow the public [ROS README](https://github.com/12mv2/robo-bump-truck-ros#build-and-test) for the interface build/tests and the [shadow bridge guide](https://github.com/12mv2/robo-bump-truck-ros/blob/main/truck_bridge/README.md#build-and-run) for the outputs-disabled demo. The [checkpoint overview](first-checkpoint.md) is a short route map. ROS commands and troubleshooting stay in the public repository; this page explains the connection to the physical truck and phone app.

## Status

The ROS bridge is a shadow bridge with physical output disabled. It has not been validated on the truck. <!-- ROS:README.md:20 --> It has no vehicle connection and no setting that enables physical output. <!-- ROS:truck_bridge/README.md:5-6 --> A separate, manually launched field integration had historical powered use on October 8, 2026. That does not validate the public shadow bridge.

| Checked | Result |
|---|---|
| Build and publisher/listener delivery test, arm64 Linux containers, ROS 2 Humble and Jazzy | Passed locally <!-- ROS:README.md:18-19 --> |
| Bridge package tests, each of Humble and Jazzy | 27 passed <!-- ROS:README.md:25 --> |
| Driving the truck through public `truck_bridge` | Not done <!-- ROS:README.md:20 --> |
| Public shadow-bridge physical output integration | Pending <!-- ROS:README.md:24 --> |

## What the repository does

A perception model publishes a requested curvature and speed on one topic. The shadow bridge checks each request against a command policy and reports what it would ask of the truck controller. The perception model itself lives in its own repository. <!-- ROS:README.md:3-6 -->

| Piece | What it is |
|---|---|
| `control_cmd` | The topic the perception model publishes to <!-- ROS:truck_bridge/truck_bridge/node.py:35-36 --> |
| `ControlMsg` | Four fields: `desired_curvature` (1/m), `desired_speed` (m/s), `listen_to_steering`, `listen_to_speed` <!-- ROS:control_interfaces/msg/ControlMsg.msg:1-8 --> |
| Example publisher and listener | A fixed request of curvature 0.1 and speed 2.5, published at 10 Hz, for checking delivery <!-- ROS:examples/control_node_pkg/src/control_publisher_node.cpp:13-14 ROS:examples/control_node_pkg/src/main.cpp:11 --> |
| Shadow bridge | Subscribes to `control_cmd`, applies the policy, publishes its decision as JSON on `bridge_status` <!-- ROS:truck_bridge/truck_bridge/node.py:35-37 --> |
| Default limits | Speed capped at 0.5 m/s and a receipt timeout of 0.5 s; proposed smoke-test limits, not a validated vehicle profile <!-- ROS:truck_bridge/truck_bridge/policy.py:25,29-30 --> |

The message has no header, timestamp or frame ID, so the bridge can see when a request arrived but not how old the image behind it was. It reports `source_age_verified=false` for that reason. <!-- ROS:README.md:22-23 ROS:README.md:50-52 --> The bridge rejects negative speed, so the perception model has no reverse authority. <!-- ROS:truck_bridge/README.md:58-59 --> When the perception model reports no usable lane, the policy asks for a stop and hands steering back to the driver; in shadow mode that is a diagnostic, not a physical stop. <!-- ROS:README.md:64-69 -->

The C++ example's 2.5 m/s request exceeds the shadow bridge's 0.5 m/s limit and is rejected. Use the synthetic 0.3 m/s request in the public [shadow bridge guide](https://github.com/12mv2/robo-bump-truck-ros/blob/main/truck_bridge/README.md#build-and-run) for valid shadow diagnostics.

## How it connects to the Orin stack

The recorded Orin runs ROS 2 Humble on JetPack 6.2.1. <!-- ROS:README.md:157 --> The public interface integration concept (truck/output-owner connections unimplemented):

```text
Perception model -> control_cmd -> ROS bridge -> existing truck controller -> existing output owner -> flight controller
```
<!-- ROS:README.md:121 -->

The bridge is an input adapter. It must not open a second serial writer to the flight controller or bypass the existing output owner. <!-- ROS:README.md:124 --> Only the first two links exist in the repository today. The links to the truck controller and output owner are the pending physical integration. <!-- ROS:README.md:24 -->

The truck controller's guards, described on [power-and-safety](power-and-safety.md), are not changed by this repository; the bridge has no output path of its own. <!-- ROS:truck_bridge/README.md:5-6 -->

Separately, the October 8, 2026 field integration used:

```text
perception node -> ROS-to-UDP relay -> controller / existing output owner -> flight controller
```

That manually launched kit is distributed neither here nor in the public interface repository. A complete public package, reproducible rebuild instructions, full configuration exports and acceptance on a new build remain outstanding. It used model steering with human/trigger or speed-dial throttle: `desired_speed` was not throttle authority. A 400 ms command receipt lifetime and optional automatic resume governed command loss; they do not establish transmitter-loss stopping. A known transmitter-loss fixture retained throttle, and its fix was deferred. Receipt freshness also cannot detect every prediction made from a stale or wrong image.

The bundled DINO `src/orin` snapshot is a separate historical controller. Its defaults, dependencies and autostart history do not identify or validate the field kit.

## Steering sign convention

The two sides use opposite signs for curvature.

| Where | Positive curvature means |
|---|---|
| On the wire (`control_cmd`) | Left, in 1/m, with X forward, Y left, Z up, origin under the rear axle <!-- ROS:docs/seam-sign-convention.md:3-4 --> |
| Truck controller | Right <!-- ROS:docs/seam-sign-convention.md:5 --> |

The bridge applies `κ_truck = −κ_wire` exactly once, when it creates the controller's steering request. Do not add another negation in the publisher or the output owner. <!-- ROS:docs/seam-sign-convention.md:8-9 --> So a positive (left) wire curvature becomes a negative normalised steering value on the truck's positive-right scale: with the defaults, a request of 0.5 becomes −0.35. <!-- ROS:truck_bridge/README.md:32-33 -->

This is a required convention, not a test result. Before physical output is enabled, positive, negative and zero curvature must be tested through the connected controller and the wheel direction checked on the bench. <!-- ROS:docs/seam-sign-convention.md:13-14 -->

For the steering numbers on the truck itself, see [calibration](calibration.md).

## Install

Use the public repository’s [build and test instructions](https://github.com/12mv2/robo-bump-truck-ros#build-and-test). For the installer, libtorch setup, recorded-frame replay and troubleshooting, follow [Jetson and perception setup](https://github.com/12mv2/robo-bump-truck-ros/blob/main/docs/jetson-orin-setup.md), or give its [agent brief](https://github.com/12mv2/robo-bump-truck-ros/blob/main/AGENT-BRIEF.md) to your coding agent. The node owner supplies the perception runtime, model and configuration; the public interface repository excludes them. Run demos in the isolated namespaces specified there.

## Robot Eyes (the phone app)

Robot Eyes is a free, closed-source iPhone app by the author, distributed through [TestFlight](https://testflight.apple.com/join/a7jz8f4a). The link was checked on 2026-10-16 and opens Apple’s Robot Eyes: Vision & Pose beta page; enrollment, a compatible build and device installation have not been verified here. App access and streaming must be checked for later perception/drive work, alongside the [unpublished OVAL4 head](../model/README.md#getting-the-head-file). Neither is needed for the synthetic checkpoint.

Its own documentation covers the app; this page covers only how the truck uses it: power-up order, session handling, and what it streams to the Orin.

### Power-up order

Historical powered-operation reference only, subject to the [first-drive boundary](first-drive.md): radio on, then traction pack in, then Orin on. <!-- F:2346 --> The phone is plugged into the Orin's USB. <!-- F:1142 --> Full steps are on [first-drive](first-drive.md) and [power-and-safety](power-and-safety.md).

### Session handling

| Rule | Why |
|---|---|
| Stop the recording before you power-cycle the Orin. | A USB host reset while recording wedged the app's video encoder; only a phone reboot cleared it on the truck, three times in one day, about 70 min lost <!-- F:1142, F:1178 -->. |
| One receiver per phone. | The app serves one stream client at a time; when another receiver released the phone, the truck's own autostart took the stream unattended <!-- F:2970 -->. Autostart is not shipped in this release (see the install section of [orin-setup](orin-setup.md#install-the-stack-from-srcorin)); on a truck with it, touch AUTO_OFF, its pause file, before starting anything else that reads the phone <!-- F:2970 -->. |
| Never delete source recordings. | Offload them instead <!-- F:2978 -->. |

Details of the encoder fault are on [pitfalls](pitfalls.md).

### What it streams to the Orin

The phone streams video to the Orin over the USB cable, and the Orin's drive tool reads it. <!-- F:1142, F:1196, F:2953, F:2970 --> When the encoder is wedged the stream carries status only, or resets its connections. <!-- F:1142 --> A session ran with the drive tool and the video stream both active for about 4 h before the Orin's power bank died. <!-- F:3133 -->

The [historical phone-mount detail](calibration.md#camera-pose-pitch-and-height) provides a mounting reference. For the actual recorded USB data connection, see [the cable](system.md#the-cable) and [logical wiring diagram](wiring-connectors.md#specify-the-ends-and-every-connection); no fixed physical cable route is specified.
