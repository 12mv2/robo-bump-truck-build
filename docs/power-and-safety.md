# Power and safety

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

Before powered commissioning, read the [first-drive/source-owner boundary](first-drive.md). The intended controller needs source-owner review and separate supervised bench/field acceptance. All supplies, numbers, power-up actions, guards and workarounds below describe the recorded build; they are not a guarantee of stopping or a commissioned recipe for a new setup. Each historical number carries its source line.

## Commissioning and isolation

Before powered checks, obtain the [missing radio/controller inputs](radio-and-controller.md#missing-commissioning-inputs), including firmware/parameter export, receiver/output mapping, mixes/failsafe, system identity and kill/manual-override/expiry behaviour.

- Trace the drive/motor, servo regulator, flight-controller/module, flight-controller USB and compute-bank paths separately, including any bench or added supplies.
- Record a full shutdown/isolation plan covering every power path. Pack removal alone leaves flight-controller USB power alive; disconnecting USB alone can leave pack/module power. Confirm the intended isolated state at every rail before handling wiring.
- Later supervised acceptance must retain armed/disarmed channel/output checks and actual neutral/stop observations for kill, manual override, link/heartbeat loss, intent expiry and process failure. Stop on unexpected output or any unverified power path; a status flag alone is insufficient.

## Three supplies

| Supply | Fed from | Feeds |
|---|---|---|
| Drive | Traction pack | The speed controller and motor. The steering servo runs from the speed controller's built-in regulator <!-- C:616 -->. |
| Flight controller | Traction pack, and also the Orin's USB | The flight controller and the radio receiver, which boots with it <!-- F:2346 --> |
| Compute | A separate power bank | The Orin <!-- F:3133 --> |

Pulling the traction pack while the Orin is up does not power off the flight controller, because the Orin's USB still feeds it; it does drop the servo supply <!-- F:2346 -->. Pulling the pack is not a full power-off.

The [recorded wiring diagram](wiring-connectors.md#specify-the-ends-and-every-connection) separates traction, servo-rail, flight-controller/receiver and compute power domains, including the additional USB supply path. It depicts logical connections, not measured continuity or complete isolation of a current assembly.

## Power-up order

Radio on, then traction pack in, then Orin on <!-- F:2346 -->.

| Step | Why |
|---|---|
| 1. Radio on | The receiver must see the radio before it boots. Powered for about 60 s with the radio off, it drops into its WiFi mode; the flight controller then reports every radio channel as 0 and a radio failsafe <!-- F:1141 -->. |
| 2. Traction pack in | The flight controller boots from the pack, so the receiver boots with the pack <!-- F:2346 -->. |
| 3. Orin on | The Orin starts last, after the radio link is up. |

If the receiver enters WiFi mode, an Orin soft reboot does not remove receiver power. Two historical soft reboots did nothing; a power cycle with the radio on linked it in seconds <!-- F:1242 -->. Use the complete isolation procedure below.

## Receiver power cycle

This recovery procedure is distinct from the rapid binding sequence on [radio and controller](radio-and-controller.md#bind-and-power-up). It remains within the [powered-use boundary](first-drive.md) and the isolation requirements above.

1. Keep the chassis stationary, kill engaged and the flight controller disarmed under the existing supervised boundary. If resetting or shutting down the Orin, stop phone recording first; host resets while recording can wedge the encoder <!-- F:1142 -->.
2. Remove traction-pack/module power to the flight controller **and** disconnect flight-controller USB from every host. Isolate any added receiver/bench supply too. USB alone is not enough while the pack/module still feeds the FC; pack removal alone is not enough while USB feeds it.
3. Verify that the receiver really is unpowered before starting the off interval. If any supply path is uncertain, stop and trace it. Keep it unpowered for the historical **10 s** interval <!-- F:1141 -->; this is a recorded recovery interval, not a new acceptance result.
4. Restore the documented order: **radio on → traction pack in → Orin on**, with the radio link established before compute starts <!-- F:2346 -->. Maintain kill/disarmed safeguards. Retain one receiver power path through the intended FC connection; do not add an ESC/BEC supply alongside TELEM1 power.

## What happens at power-up

Historical autostart is not supplied. On the recorded truck, AUTO_OFF paused it on the bench; keep that pause in place before another phone client starts. The app serves only one stream client. See [autostart and the pause file](orin-setup.md#autostart-and-the-pause-file) for the dated service details and boot/arming distinction. <!-- F:1380, F:2970, F:2976, F:2978 -->

The historical owner checked the conditions below before output. Its release/zero-override behaviour is not proof of motor neutral; the flight controller and receiver configuration determine what follows.

| Guard | Output is zero when |
|---|---|
| Radio input fresh | Radio input from the flight controller is stale |
| Flight controller heartbeat fresh | The heartbeat is stale |
| The [kill dial](radio-and-controller.md) | The knob is at kill, or cannot be read |
| The mode switch | The switch is not in its middle position |
| Flight controller armed | The flight controller reports disarmed |
| Flight controller in manual mode | It is in any other mode |
| Wheel-copy gate | The gate is closed |

Use the [annotated historical radio](radio-and-controller.md#advanced-historical-research-configuration) and [controls table](radio-and-controller.md#controls-by-function) to locate the mode and kill controls; their pictured labels do not establish stopping on a new setup.

## Known software limits

The historical DINO owner can retain speed-dial throttle after lane loss, stale intent or route end; wheel override affects steering only. Camera waits can delay Ctrl-C and a hard kill skips release. See the complete [powered-use and shutdown boundary](first-drive.md#pre-start-boundary-historical-controller) before launch.

With `--fc --dry-run`, the historical source connects to the flight controller and exchanges telemetry/parameter traffic; only actuator override sends are disabled. It is not equivalent to a physically disconnected compute-only check. See [checkpoint boundaries](build-checkpoints.md). <!-- src/orin/live_trail.py:673-692,2062-2068,2158 -->

## Hazards found on this truck

### A speed loop pushes harder into a blocked truck

A closed-loop cruise controller with no stall detector saw a speed of 0.00 m/s against a 0.34 m/s setpoint. Its integrator wound up to its clamp and the throttle to 1709 µs, and it held there for 26.4 s into a truck that never moved <!-- F:1927 -->. Nothing in the build stopped it; a person noticed. A fixed throttle would have sat at one value; a speed loop keeps adding throttle against a blocked wheel.

The fix was a stall guard outside the control loop. It can only stop the drive tool, never command anything, and it stops it when throttle is commanded and the pose speed stays under 0.05 m/s. On the retry it fired at 2.5 s, against 26.4 s unguarded <!-- F:1928 -->. The truck did not move on the retry either; that cause was at the vehicle and was not found that session <!-- F:1928 -->. The guard has since been replaced by motion checks inside the drive code; the numbers here are kept as the measurement of the hazard <!-- F:1928 -->.

Plan a stall check into any speed controller before its first drive.

### Steering centre is not 1500 µs

On this truck the steering servo points straight at 1463 µs, not 1500 <!-- F:971 C:628 -->. Pose data put the zero crossing at 1461.2 µs, 1.8 µs away <!-- F:1077 C:217 -->. After the centre moved to 1463 µs with the maximum endpoint still at 1900, the servo had 437 µs of travel one way and 363 the other, so one side could be driven past the steering linkage's mechanical stop. Bringing the maximum to 1826 made it 363 each way <!-- F:971 C:630 -->.

After changing the centre, remeasure and recheck **both safe mechanical endpoints** without linkage binding, following the [own-build calibration checklist](calibration.md#own-build-measurement-checklist). Equal pulse-width spans alone do not establish mechanical clearance; the 363 µs spans above are this truck’s historical result.

### Power-cycling the Orin while the phone records

Rebooting or power-cycling the Orin while the phone app records wedges the app's video encoder. Relaunching the app or replugging USB did not clear it; only a phone reboot did. It happened three times in one day and cost about 70 min <!-- F:1142 -->. Stop the recording before you power-cycle the Orin. The app is covered in [ros.md](ros.md).

More failures and their fixes are on [pitfalls](pitfalls.md).

## Batteries

| Pack | Facts |
|---|---|
| Traction pack | 7-cell NiMH; measured 9.33 V when fresh <!-- F:3117 --> |
| Second traction pack | Not full when checked: 7.91 V at rest <!-- F:3117 --> |
| Orin power bank | In one session the NiMH traction pack (recorded that session as "the 8.4 V NiMH", not a measured voltage) outlasted it: the bank was fresh at 18:39 and dead about 22:45 with the drive tool and the video stream running, about 4 h <!-- F:3133 --> |

Check each pack at rest before a session.

## Open items

| Item | State |
|---|---|
| Separate servo supply | Not fitted <!-- C:617 -->. The steering servo still runs from the speed controller's built-in regulator <!-- C:616 -->. |
| Power module readings | Never checked against a meter, so this page quotes no current or pack voltage from it. |
