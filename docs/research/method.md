# Method: how each number was measured

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

This page covers the instruments and procedures behind the numbers in [results](results.md): what each one measures, with what, at what rate, how it is lined up in time, and its known error. Read it before trusting any number on the other pages. Each number carries its source line.

## The weakest instrument comes first

A camera on the ceiling, looking down at the indoor oval, was meant to grade the truck. It is the weakest instrument behind these pages, and it is the weakest thing about every number it produced <!-- C:424 -->.

- **It never graded a whole lap.** Re-graded offline with its search corridor widened to 1.0 m, 18 minutes of video gave 35 fragments, with a median sweep of 79° and a maximum of 193°, where a lap is 360° <!-- F:1839, C:427, C:428 -->. The median fragment lasted 2.9 s, and it lost the truck outright in 18 of the 35 <!-- F:1839, C:429 -->.
- **Every ceiling number is per-fragment.** It cannot be compared with a per-lap number from the phone's own pose, and these pages never set one beside the other <!-- F:1839, C:434 -->.
- **The cause is the grader's splitter, not the corridor.** It ends a fragment the moment the truck's blob is briefly lost, for example when a person steps into frame. It needs to carry a track across short dropouts before it can grade a lap <!-- F:1839 -->.
- **Where a lap is the unit, the phone's own pose grades it instead**, with the limits set out under [the pose grader](#pose-grader) <!-- F:1839, C:436 -->.

No photo of the ceiling camera exists <!-- C:754 -->. That is a limitation of this record, not a gap to fill with a drawing.

## Instruments at a glance

| Instrument or procedure | Measures | With | Rate | Time alignment | Known error |
|---|---|---|---|---|---|
| [Ceiling camera grader](#ceiling-camera-grader) | Truck's cross-track distance from the tape, per fragment | A machine-vision camera on the ceiling, a blob tracker and a traced tape outline | Not recorded in the source lines | Against the phone: see [clock alignment](#clock-alignment) | Never graded a whole lap; calibration up to 0.48 m wrong on one side <!-- F:1347, C:374 --> |
| [Ceiling camera metre](#ceiling-camera-metre) | Pixels to metres on the floor | Tape-measure spans on the floor plus pose-to-camera pairs from hand laps, fitted as a floor-plane homography | Not recorded in the source lines | Not applicable | ±2 % after a fix; scale off about 8 % on a later day <!-- F:1273, F:1347 --> |
| [Pose grader](#pose-grader) | Truck's cross-track distance from a reference path | The phone's own tracked pose against a path driven by hand | Not recorded in the source lines | Not applicable to the path; against the ceiling camera, see [clock alignment](#clock-alignment) | Measures agreement with the driver's path, not with the world <!-- C:447 --> |
| [Clock alignment](#clock-alignment) | Ceiling-camera clock minus phone clock | A sweep of trial offsets, scored on paired points | Steps of 0.1 s <!-- F:1253 --> | This is the alignment | 0.0 ± 0.2 s <!-- F:1253 --> |
| [Latency rig](#latency-rig) | Glass-to-application delay of the phone camera stream | A screen the Orin flashes on its own clock, filmed by the phone | Camera at 15 fps <!-- F:646 --> | Flash schedule on the Orin's clock; the receiver timestamps the frame | An upper bound; includes the monitor's lag <!-- F:645, C:231 --> |
| [Line-fit curvature](#line-fit-curvature) | Curvature of the line handed to the controller | `fit_ground_kappa`, on the line fitted to the steering head's per-row output | Per line fit | Not applicable | Reads high: a true 1.00 1/m reads 1.62 to 2.31 <!-- F:1109, S:224 --> |
| [Path-recorder peak curvature](#path-recorder-peak-curvature) | Peak of a per-point curvature along one pass | `record_path.py` | Once per recorded pass | Not applicable | A peak, not a radius <!-- S:225 --> |
| [Circle fit](#circle-fit) | Radius of a bend | A circle fitted to the recorded path, with its residual | Not applicable | Not applicable | Quoted with its residual <!-- S:226 --> |
| [Vibration check](#vibration-check) | Jitter in the phone's tracked path | The phone's own pose, truck-mounted against hand-held | Not recorded in the source lines | Not applicable | Truck jitter below a hand-held walk <!-- F:1351, S:230 --> |

## Ceiling camera grader

**Measures.** How far the truck's blob sits from the traced tape outline, per fragment and by section of the loop <!-- F:1274, F:1839 -->.

**Known error.** Three kinds, beyond the whole-lap gap above.

- **Calibration.** On one afternoon the traced outline was up to 0.48 m off the real tape on the left side of the loop <!-- F:1347, S:228, C:374, C:440 -->. Through sectors 140° to 240° about the loop centre it sat 0.19 to 0.48 m off the tape, because the far-left third was too washed out to segment <!-- F:1347, S:228 -->. Through the right bend it was good to 0.03 m <!-- F:1347, S:228 -->. Every number quoted from the left side that day was junk <!-- F:1347, C:440 -->.
- **A search wall, not an excursion.** The grader looks for the truck only within a corridor either side of the tape. At the default 0.45 m nothing could exceed 0.42 m, and on one afternoon 18 of 31 passes sat at that wall <!-- F:1346, S:227, C:730-731 -->. A value at the wall is the instrument's limit, not a measured excursion. The offline re-grade widened the corridor to 1.0 m <!-- F:1839, S:227 -->.
- **One cell, read through it.** That same afternoon, a cell that raised the line-fit curvature cap drove worse, at 0.197 m from the line <!-- F:1348 -->. That is a ceiling-camera number from the day of the bad calibration, so read it as per-fragment and as one cell against others, not as the truck against the tape.

**Cannot show.** A whole lap. A number comparable with a pose number. Any figure from a sector the traced outline did not reach.

## Ceiling camera metre

**Procedure.** Tape-measure spans laid across the floor in view of the camera; their pixel lengths, with pose-to-camera pairs from hand laps, fix a floor-plane homography from pixels to metres <!-- F:1239 -->. Each span is held out in turn to test the fit.

**Known error.**

- **A hook cap read as tape.** On one fit, the blue-black hook cap at the end of the tape measure passed the blue test, so the top end of every across-span sat on the cap. The pixel spans were 3 % to 7 % too long and the metre about 3 % short <!-- F:1271, C:439 -->. The metre was corrected twice in all <!-- C:438 -->.
- **After the fix.** With span ends moved to where the tape's own centre crosses, leave-one-span-out error was −1.7 % to +1.8 % on nine across-spans, and the fit is quoted at ±2 % <!-- F:1273 -->. That figure is marked provisional in the source.
- **A later day.** On the afternoon of the bad calibration, two tape spans read 620 and 636 px/m against the calibration's 583, so the scale was about 8 % off <!-- F:1347, S:229, C:441 -->.

**Cannot show.** A metre from one night set beside a metre from another. The camera was recalibrated between nights <!-- C:380-383 -->.

## Pose grader

**Measures.** The phone's own tracked pose during a drive, against a reference path recorded by driving the same loop by hand. The output is signed cross-track distance from that path.

**What it is.** Agreement with the driver's path, not with the world <!-- C:447 -->. Its reference is the driver's own path. It measures whether the truck went where the driver went <!-- C:448 -->. It can grade a cell alongside the ceiling camera; it does not replace it <!-- F:1263 -->.

**Checked against the ceiling camera.** On seven laps driven by hand, 1,340 paired points, the two agreed on signed cross-track to rms 0.036 m, p90 0.061 m and maximum 0.092 m <!-- F:1252, C:385, C:386 -->. The pairs were limited to where the calibration traced the tape and the truck was in frame <!-- F:1252, C:386 -->. On average the pose read 0.017 m further outside the loop than the ceiling camera, mostly at the bend apex <!-- F:1252 -->. The ceiling metre used for this check was the one corrected the next morning for the hook-cap bias, about 3 % short <!-- F:1271, F:1277 -->.

**Cannot show.** Whether the driver's path was a good path. Agreement with the ceiling camera outside those seven laps.

## Clock alignment

**Procedure.** The ceiling camera and the phone run on separate clocks. To pair their points, trial offsets were swept from −1.0 s to +1.5 s in 0.1 s steps, twice, each sweep independent of the other <!-- F:1253 -->.

**Result.** Ceiling-camera clock minus phone clock is 0.0 ± 0.2 s <!-- F:1253 -->. "0.0" means the offset is below the pairing's resolution, not that latency is zero <!-- F:1282 -->. An earlier written value of +0.5 s is ruled out <!-- F:1253, F:1282 -->.

**Cannot show.** An offset smaller than the ±0.2 s band <!-- F:1253 -->.

## Latency rig

**Procedure.** The Orin serves a page that flashes black to white on a schedule set by its own clock. The phone films that screen, and the receiver timestamps the frame in which the flash appears <!-- F:643 -->. The camera ran at 15 fps, with a flash period of 3.000 s, which is exactly 45 frames <!-- F:646 -->.

**Result.** About 60 ms. Precisely: 11 samples, mean 60.5 ms, standard deviation 4.6 ms, range 49.8 to 65.4 ms <!-- F:643, C:230 -->.

**Known error.**

- **An upper bound.** It includes the monitor's own display lag, about 5 to 20 ms, which this method cannot separate <!-- F:645, C:231 -->.
- **Filtering.** 4 further detections returned 322 to 990 ms and were excluded as the camera's auto-exposure recovering after the screen went dark <!-- F:644, C:233 -->. The rule was about whether a detection was a real flash, not about its value, and it was set before the values were seen <!-- F:644 -->.
- **Not bias-corrected.** Because 3.000 s is a whole number of frames, every flash landed at the same frame phase and the sampling was not uniform, so the tool's half-period correction must not be applied <!-- F:646, C:236 -->.
- **Sub-frame precision is not available** from this rig <!-- F:648 -->.

**Cannot show.** The camera path's delay without the monitor's lag. Any precision finer than one frame.

## Line-fit curvature

**Measures.** `fit_ground_kappa` returns the maximum curvature of the fitted line near the goal point, in 1/m. A curvature cap uses it to reject a fit and fall back to a straight line <!-- F:1108-1109 -->.

**Known error.** It is an upper bound, not an unbiased curvature. Against synthetic arcs at the real row distances, a true 1.00 1/m reads 1.62, 2.31 or 2.09, depending on the phone mount angle <!-- F:1109, S:224 -->. With the cap at 1.5, only true curvature up to about 0.65 1/m gets through, while the indoor oval's bend needs 1.49 <!-- F:1348, S:224 -->.

**Cannot show.** A true curvature. Treat any cap set on it as a cap on a reading that runs high.

## Path-recorder peak curvature

**Measures.** `record_path.py` prints the peak of a per-point curvature estimate along one recorded pass <!-- S:225 -->.

**Known error.** It is a peak of a noisy estimate, not a radius. Read as a bend radius, it makes a bend look tighter than it is <!-- S:225 -->. On the indoor oval it read 0.46 m where circle fits to the recorded path give 0.62 to 0.63 m <!-- F:1346, S:225 -->. See [pitfalls](../pitfalls.md) for what else went wrong on the build.

**Cannot show.** The radius of a bend. Use a circle fit for that.

## Circle fit

**Measures.** The radius of a bend, from a circle fitted to the recorded path.

**Rule.** This is the curvature measurement to quote, always with its residual <!-- S:226 -->.

## Vibration check

**Measures.** Jitter in the phone's own tracked path, with the phone bolted to the truck and with it carried by hand.

**Result.** Over four sessions, median path jitter on the truck was 0.4 to 7.4 mm, against 16.8 mm for a hand-held walk, with no thermal throttling <!-- F:1351, S:230 -->. The phone tracks better bolted to the truck than carried. The source rules out building vibration handling <!-- S:230 -->.

**Cannot show.** Vibration on rougher ground than an indoor floor.

## Stream checks

The checks behind the stream figures on [system](../system.md).

- **Pose tracking state.** In the first end-to-end check, the phone reported `normal` tracking on 732 of 735 poses sent to the Orin <!-- F:1349 -->.
- **Latency.** The glass-to-application figure on system is the [latency rig](#latency-rig) above: about 60 ms, an upper bound <!-- C:230-231 -->.

## Related pages

- [Results](results.md): every number these instruments produced.
- [Calibration](../calibration.md): steering limits, camera pitch and height, camera intrinsics.
- [Pitfalls](../pitfalls.md): what went wrong on the build, with the numbers that show it.
- [Sample data](../../data/README.md): five sample runs and the controller log format, including one pass replayed through the ceiling camera's tracker.
- [ROS 2 interface and Robot Eyes](../ros.md): what the phone streams to the Orin.

## Background: the phone-bump labels

The truck does not use this procedure; its steering head learns from the phone's pose <!-- C:157-160 -->. It is kept because the project grew from it, and [results](results.md#background-the-phone-bump-experiment-this-project-grew-from) quotes its numbers.

**Procedure.** Colin walked a room holding the phone and bumped the phone body with his free hand near each obstacle; the phone never touched the obstacle <!-- C:25-30 -->. The accelerometer marked when; LiDAR depth marked where. Patches showing the bump point in the frames 0.5–12 s before the bump became no-go; floor the phone passed over 1–8 s later became go <!-- C:88-91 -->. LiDAR depth placed labels only: the trained head sees image features alone <!-- C:75-78 -->.

**Scoring.** AUC on contiguous time blocks, never random frames. It is agreement with the LiDAR-assisted labels on time the head never saw, not an independent physical check <!-- C:92-94 -->.
