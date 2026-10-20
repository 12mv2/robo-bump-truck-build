# Pitfalls

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

This page lists the walls this truck hit: what was seen, the cause, the fix where there is one, and the number that shows it. Each number carries its source line. Where no fix has a measured result, the pitfall is written as open.

| Pitfall | Status |
|---|---|
| [Steering centre moved, endpoints did not](#steering-centre-moved-endpoints-did-not) | Fixed for one cause; other freezes not explained |
| [Camera pitch slipped and the far rows vanished](#camera-pitch-slipped-and-the-far-rows-vanished) | Open |
| [Low calibration error, wrong focal length](#low-calibration-error-wrong-focal-length) | Caught by an independent check |
| [Phone encoder wedges, and one phone has one receiver](#phone-encoder-wedges-and-one-phone-has-one-receiver) | Workaround |
| [Speed loop winds up into a blocked truck](#speed-loop-winds-up-into-a-blocked-truck) | Fixed (stall guard, measured); vehicle cause open |
| [The Orin bank runs out before the traction pack](#the-orin-bank-runs-out-before-the-traction-pack) | Planning note |

For the power-up order and the guards, see [power-and-safety](power-and-safety.md). For the checks to run before the first powered drive, see [first-drive](first-drive.md).

## Steering centre moved, endpoints did not

**Seen.** On the indoor tape oval on 2026-09-08, driven by hand at crawl speed, the steering kept freezing: it stuck where it was for about 3 s and released when the driver stopped <!-- F:964, F:965 -->. A fresh pack changed nothing.

**Cause (one of them).** Earlier that evening the steering centre was moved from 1500 µs to 1463 µs, the truck's real straight. With the maximum endpoint still at 1900 µs, the servo had 437 µs of travel one way and 363 µs the other, so one side could be driven past the steering linkage's mechanical stop <!-- F:971, C:628-629 -->.

**Fix.** Maximum endpoint from 1900 µs to 1826 µs, giving 363 µs each way <!-- F:971, C:630 -->. When you move the centre, move the endpoints with it.

**Number.** One run before and one run after, same oval and same driver, but different lengths and speeds, so not a controlled comparison. Stops of 0.6 s or longer went from 11 in 193 s to 3 in 140 s, and time stopped from 14 % to 4 % <!-- F:972, C:603, C:604 -->. These are one measurement seen two ways, not two confirmations.

**Still open.** This was one cause, not the only one. The log's own freeze list also held full lock the other way and dead centre <!-- F:982, F:983, C:610 -->, so the freeze may return under load at other angles. The explanation offered for those was inferred and never instrumented. The fix it points at, a separate supply for the steering servo, is not fitted <!-- C:617 -->.

## Camera pitch slipped and the far rows vanished

**Seen.** On 2026-10-02 the truck would not take the corners of the indoor oval. Loop timing, flight-controller output, code and the steering head were checked and cleared <!-- F:3039, F:3041, F:3042 -->.

**Cause.** The phone sat lower in its mount. Its pitch at rest, read at launch, was 16.4° to 17.6° on every recorded drive before 2026-10-01 and 9.2° to 10.8° on every launch that night, with the same phone and the same mount <!-- F:3043 -->. Tilted down, the camera lost the far floor. Frames where fewer than 8 rows were kept rose from 9 % on a good earlier drive to 15 % to 33 % <!-- F:3043 -->.

**Fix.** Partial only. After the phone was re-seated, pitch at rest read 16.0° and the next run kept rows on all but 9 % of frames, the earlier picture <!-- F:3043 -->. The steering was still poor on that run <!-- F:3043 -->. **Status: open.** The cause of the remaining poor steering is not known.

Read the phone's pitch at rest before every drive and compare it with the last good drive.

See the [historical phone-mount detail and joint schematic](calibration.md#camera-pose-pitch-and-height). The image is a mounting reference, not a pitch measurement or proof of the later re-seating result.

## Low calibration error, wrong focal length

**Seen.** A phone camera calibration that looked excellent. Detection was clean: reprojection rms 0.206 px, worst view 0.275 px, over 12 views <!-- F:2787, F:2788 -->.

**Cause.** The board poses came from a perspective warp drawn on a screen, not from tilting a physical board, and the phone viewed the screens at an angle <!-- F:2769, F:2800 -->. That gives a fit that agrees with itself but is not physical. The fitted focal length was 42.47 % off the phone's own value, and the phone's own value agrees with a checkerboard fit to 0.2 % <!-- F:2788, F:2789 -->. The distortion coefficients came out large, a sign of an unconstrained fit <!-- F:2790 -->.

**Fix.** The check that caught it: compare the recovered focal length with an independent value. A low reprojection error measures only how well the points agree with each other, which a non-physical geometry satisfies; a check that shares the fit's assumptions would have passed <!-- F:2804, F:2805, F:2806, F:2807 -->. Calibrate with a physical board and keep its tilt modest, 20° to 25°, moving the phone <!-- F:2812 -->.

The calibration procedure is on [calibration](calibration.md).

## Phone encoder wedges, and one phone has one receiver

**Seen.** Rebooting or power-cycling the Orin while the phone app was recording wedged the app's video encoder. The stream sent status only or reset its connections. Relaunching the app or replugging USB did not clear it <!-- F:1142 -->.

**Cause.** A USB host reset while recording. The encoder error has a code, -12903 <!-- F:1178 -->.

**Fix.** Only a phone reboot cleared it on the truck. It happened three times in one day and cost about 70 min <!-- F:1142 -->. Stop the recording before you power-cycle the Orin. The app now recreates its encoder itself, up to 3 times 10 s apart, before it asks for a phone reboot. That cleared the fault on the first try on a desk bench; on the truck, an app relaunch did not, so the truck fault may be a different one under the same code <!-- F:1178 -->.

**A second, related wall.** The phone app serves one stream client at a time. When another receiver on the Orin released the phone, the truck's own autostart connected to it unattended and took the stream; two receivers cannot both read one phone <!-- F:2970 -->. Plan one receiver per phone. Autostart is not shipped in this release (see the install section of [orin-setup](orin-setup.md#install-the-stack-from-srcorin)); the truck's own behaves like this:

This release does not ship autostart; on a truck that has it:

At power-up the Orin starts the trail stack and begins sending commands; seven guards zero the output, and the Orin never arms the flight controller. Whether the controller arms at boot is set by its parameters and the radio, so keep AUTO_OFF touched whenever the truck is on the bench. <!-- F:2976, F:2978 -->

On a truck with that autostart, touch AUTO_OFF, its pause file, before starting anything else that reads the phone. The app itself is covered in [ros](ros.md).

See the [documented phone USB connection](system.md#the-cable) and [session handling](ros.md#session-handling); these describe the data connection without prescribing a fixed cable route.

## Speed loop winds up into a blocked truck

**Seen.** A closed-loop cruise controller read a speed of 0.00 m/s against a 0.34 m/s setpoint <!-- F:1927 -->.

**Cause.** The speed loop had no stall detector. Its integrator wound up to its +60 clamp and the throttle to 1709 µs, and held there for 26.4 s into a truck that never moved <!-- F:1927 -->. Nothing in the build stopped it; a person noticed. A fixed throttle sits at one value; a speed loop keeps adding throttle against a blocked wheel.

**Fix.** A stall guard outside the control loop. It is kill-only: it can stop the drive tool but never command anything, and it stops it when throttle is commanded and the pose speed stays under 0.05 m/s. On the retry it fired at 2.5 s, against 26.4 s unguarded <!-- F:1928 -->. The guard has since been replaced by motion checks inside the drive code <!-- F:1928 -->. See [power-and-safety](power-and-safety.md).

**Still open.** The truck still did not move on the retry; the cause was at the vehicle and was not found <!-- F:1928 -->. Before any speed loop gets authority, run the motion check on [first-drive](first-drive.md), and keep the [kill dial](radio-and-controller.md) in hand.

## The Orin bank runs out before the traction pack

**Seen.** In one session the NiMH traction pack outlasted the Orin's power bank. The bank was fresh at 18:39 and dead at about 22:45, with the drive tool and the video stream running: about 4 h <!-- F:3133 -->.

**Planning note, not a fix.** The compute supply, not the drive pack, set the length of that session. **Status: open.** Plan sessions around the bank, or carry a second one.

See the [power-domain diagram](wiring-connectors.md#specify-the-ends-and-every-connection) for the separate compute supply; it does not establish bank mounting or runtime.
