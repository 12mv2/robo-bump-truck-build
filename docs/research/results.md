# Results

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

This page lists what the truck did, with every number's instrument, day and caveat. The field runs come first: the truck steering itself along a park trail on its camera alone, and laps taught by hand and repeated on an indoor tape oval, with a mode that returns the truck to the taught path. The steering numbers from the oval follow. Each number carries its source line. What the truck has not been shown to do is on [cannot-do-yet](cannot-do-yet.md).

## The field runs

| Day | Where | What the truck did | Caveat |
|---|---|---|---|
| 2026-09-20 | Park trail, camera only | The steering head had the wheel for 99.6 s of a 212 s run, in four stints; the longest covered the whole 18.4 m leg. All four hand-backs were at a turnaround, none on the trail <!-- F:2410 -->. In a second run with cruise on, it held its 0.30–0.38 m/s setpoint: median 0.3 m/s, p90 0.4 <!-- F:2411 -->. | One park <!-- C:278 --> |
| 2026-10-03 | Same trail, camera only | Three laps under the steering head <!-- F:3105 -->. Lap 1 left the trail three times within 1.5 m, at a spot where the truck rolled 13.6° to 16.8° <!-- F:3113 -->. With a +0.07 steering offset to cancel a standing pull, lap 2 made the loop with one hand-back <!-- F:3114 -->. | The side-to-side weave was 0.199–0.219 m against 0.077 m on 2026-09-20, with identical control settings. Three things differed: camera pitch (16.6° then, 20.2–21.5° on 2026-10-03), pose age (195 ms then, 97 ms) and speed (0.87 m/s then, 0.97–1.14 m/s); pitch is untested <!-- F:3115 --> |
| 2026-09-13 | Indoor oval, taught path plus camera | Teach and repeat: three laps driven by hand were recorded as the taught path <!-- F:1785 -->, then the truck drove it with the camera. 15 of 24 lap segments ran with the steering head at full authority, against 2 of 14 for the camera alone that morning. Counter-clockwise, nine laps in a row at 0.57 to 0.82 m/s; clockwise, 6 of 9 <!-- F:1790 C:780-782 -->. | One session; a different battery pack from the morning's, and the obstacle beside the oval had moved <!-- F:1790 --> |
| 2026-09-13 | Indoor oval, return to the taught path | With the camera starved on purpose (minimum rows 8 raised to 14), the truck ran three laps at full steering-head authority with zero frames of human input; worst excursion 0.26 m. The camera was rejected on 375 of 948 moving frames: the taught path carried 290 and the return carried 85 <!-- F:1854 -->. A lap stop ended a run at 3.03 laps with no human <!-- F:1855 -->. | One cell. The one run that night with no taught path loaded had nothing to return to and drove into a treadmill beside the oval <!-- F:1858 --> |
| 2026-09-15 | Indoor oval, battery session | 27 lap units in fully completed cells <!-- F:2228 -->. | Counted by the controller's own progress, not by an independent grader; the session as a whole needed substantial human help <!-- F:2228 F:2231 --> |
| 2026-09-14 | Indoor, stuck truck | A bounded reverse recovery for a stuck truck was written and tested in code <!-- F:1900 -->. On the truck, reverse did not free a wedged truck at 1.6 times the forward break-away margin, and the wheels did not turn <!-- F:2084 -->. | Recovery of a stuck truck is not shown |

The 2026-09-13 oval laps were taught-path assisted <!-- C:780-782 -->; they are not camera-only results. The sample runs in [the data](../../data/README.md) include two from 2026-10-09 that carry recovery events; no graded result is published for them.

video: The truck steering itself along the park trail on camera alone, following view

## The oval steering numbers

The oval is blue tape on an indoor floor. Its rows below come from 2026-09-08 to 2026-09-12, before the taught path existed. The steering law is pure pursuit on a line fitted to the steering head's output.

Sign is the direction of a before/after change, or the sign of a signed quantity; an unsigned single value has none.

| # | Claim | Number | Sample | Sign | Unit |
|---|---|---|---|---|---|
| 1 | Camera lens ahead of the rear axle | 0.24 <!-- F:1394 C:507-508 --> | tape, measured twice | + (forward) | m |
| 2 | Full-lock requests, same run replayed with and without the axle offset | 144 to 0 of 957 frames; median \|gain·κ\| 0.60 to 0.33 <!-- F:1426 C:517-518 --> | 957 frames, 1 run, replay | − | frames; 1/m |
| 3 | Consecutive laps under the steering head | 4, then 7; longest hands-off stretch 63.5 s <!-- F:1440 C:483-484 C:493-495 --> | 2 sets, 1 night, Colin's count | none | laps; s |
| 4 | Oval head, honest figures after an AUC retraction | column error 3–6 cm; correlation +0.49 <!-- F:1031 F:1005 C:543-544 --> | 4 sessions, each held out in turn <!-- F:1005 --> | + (correlation) | cm; r |
| 5 | Oval bend end radii, by phone pose | 0.65–0.82 <!-- F:1046 C:550 --> | 4 sessions <!-- F:1046 --> | none | m |
| 6 | Lap-to-lap repeatability on the oval vs trail-corner dispersion | 0.028–0.031 RMS vs 0.62 σ <!-- F:1029 C:558-559 --> | Colin's hand-driven oval laps; 1 trail corner | none | m |
| 7 | Truck's full-lock radius at oval speed | 0.60 at 0.45 m/s (curvature 1.66 1/m) <!-- F:1073 C:564 --> | fit to pose and servo logs | none | m |
| 8 | Gain bracket at lookahead 0.8 m, full-lock constant 1.429 | gain 1.0: 0.24–0.38 commanded; gain 2.0: 0.80 sign flips/s <!-- F:1025 C:579-580 --> | 2 settings, 1 oval, within 1 hour | none | 1/m; flips/s |
| 9 | Full-lock constant 1.429 to 1.66, as a gain change | −16 <!-- F:1075 C:582-583 --> | arithmetic | − | % |
| 10 | In-bend frames handed a straight line, in the laps that worked | 44 <!-- F:1456 F:1490 C:574-575 --> | in-bend frames, 1 night | none | % |
| 11 | Highest curvature requested in its weak direction vs full lock | 1.17 vs 1.66 <!-- F:1491 F:1468 C:587-588 C:417-418 --> | turns in its weak direction, 1 night | none | 1/m |

**The laps (row 3).** Colin counted four laps, then seven. The camera on the ceiling was not running yet, so the count is his eyes and a 63.5-second hands-off stretch in the log <!-- F:1440 C:493-495 -->. They are two separate sets either side of a crash and a reset, never one figure, and neither was graded by an instrument <!-- C:496-497 -->. Both sets ended in a crash on the exit of the corner <!-- C:498-500 -->.

**The axle offset (rows 1 and 2).** The steering law assumes positions measured from the rear axle. The camera lens sits 0.24 m forward of it; Colin measured it with a tape, twice <!-- F:1394 C:507-508 -->. The code had no forward term, so every goal looked closer than it was and the steering was over-commanded. An outside reviewer found it <!-- C:509-511 -->. Replaying one run's frames through the controller with and without the offset, requests at full lock went from 144 to 0 of 957 frames, and the median \|gain·κ\| from 0.60 to 0.33 1/m <!-- F:1426 C:517-518 -->. That is a replay of the command, not of the drive: the truck never drove those replayed commands <!-- C:518-520 -->. The offset has not been compared against zero on the truck <!-- C:528 -->.

**The oval head's AUC, retracted (row 4).** An earlier AUC for the oval head was inflated by easy negatives: one patch in each row is the centre, so ruling out the far edges scores well. Colin caught it, and it is withdrawn rather than restated <!-- F:1031 C:541-544 -->. The figures that replace it, from four sessions each held out in turn: a column error of 3–6 cm against the driver's line, and a correlation of +0.49 with the driver <!-- F:1031 F:1005 C:543-544 -->.

**The bend radius, corrected (row 5).** Measured from the phone's own pose over four sessions, the oval's bend end radii are 0.65–0.82 m <!-- F:1046 C:550 -->. This corrects an earlier, larger figure, which is withdrawn.

**Why an indoor oval (row 6).** Measured by the pose grader, which scores the phone's own track, Colin's hand-driven oval laps repeat to 0.028–0.031 m RMS lap to lap <!-- F:1029 C:558 -->. At the outdoor trail corner where the controller kept failing, the hand-driven reference line has 0.62 m dispersion, measured as σ per arc-length bin <!-- F:1029 C:558-559 -->. These are different metrics. What they support is narrow: at the one place the controller fails outdoors, the reference line itself is less repeatable than the error being hunted <!-- C:561-562 -->. The price is a harder problem: the oval's bends sit close to the truck's own full-lock radius (row 7).

**Full lock (row 7).** Fitting servo command against curvature from the phone's pose, full lock at the oval's 0.45 m/s is a radius of 0.60 m, curvature 1.66 1/m <!-- F:1073 C:564 -->.

**The gain bracket (rows 8 and 9).** On one oval, within one hour, at lookahead 0.8 m, both ends of gain were driven <!-- F:1025 C:579 -->. Gain 1.0 under-turns: 0.24–0.38 1/m commanded at corner entry. Gain 2.0 hunts: 0.80 sign flips/s, full lock one way to full lock the other <!-- F:1025 C:579-581 -->. Both gains were measured with the full-lock constant at 1.429 <!-- F:1075 C:582 -->. Raising that constant to 1.66, the measured value, is arithmetically the same as lowering gain by 16 % <!-- F:1075 C:582-583 -->. A gain from this period means nothing without the constant it was measured against.

**Handed a straight line (rows 10 and 11).** Even in the laps that worked, counter-clockwise with the curvature cap at 1.5 1/m and a minimum of 10 rows, 44 % of in-bend frames carried a straight-line fit <!-- F:1456 F:1490 C:574-575 -->. The controller was handed a straight line through a bend. Which limit forces that fallback is not settled. In its weak direction the request never exceeded 1.17 1/m, against a full lock of 1.66 1/m <!-- F:1491 F:1468 C:587-588 C:417-418 --> and a tightest bend that needs about 1.6 1/m <!-- F:1491 -->. The truck makes the turn; when it is not getting round, the cause is upstream of the actuator <!-- C:589-590 -->.

## Not measured

Not measured: the axle offset against zero on the truck, or a lap count by an instrument; see [cannot-do-yet](cannot-do-yet.md).

## Related pages

- [method](method.md): how each number on this page was measured.
- [cannot-do-yet](cannot-do-yet.md): what the truck has not been shown to do.
- [pitfalls](../pitfalls.md): the walls the truck hit, each with its cause and fix.
- [calibration](../calibration.md): steering centre and limits, camera pitch and height, intrinsics.
- [sample data](../../data/README.md): five sample runs and the controller log format.

## Background: the phone-bump experiment this project grew from

Before the truck drove, a hand-held phone tested whether bumps could label where not to drive, with no manual image labeling <!-- C:41 -->: bump the phone body near an obstacle, the accelerometer marks when, LiDAR depth marks where, and a 385-parameter head on a frozen DINOv2-S/14 backbone learns from the labels <!-- C:75-78 -->. How the labels were made is in [method](method.md#background-the-phone-bump-labels).

- 2026-08-25, own kitchen: 3.5 min, 21 bumps, held-out AUC 0.932 <!-- F:177 C:49-52 -->.
- 2026-08-27, rearranged kitchen: 30 bumps gave 147,731 labeled patches over 927 frames <!-- F:216 C:51 -->. On the same held-out labels the retrained head scored 0.9403 and the old head 0.9224 <!-- F:207 C:52 C:58 -->; the old head's median frame no-go fraction went from 0.42 to 0.37 <!-- F:207 C:59 -->.
- 0.932 and 0.9224 come from different recordings; only 0.9403 against 0.9224 is a like-for-like comparison <!-- C:56-58 -->.
- The AUC is agreement with the LiDAR-assisted labels on unseen time blocks, not an independent physical check <!-- C:92-94 -->. The truck's steering head does not use these labels: it learns from the phone's pose <!-- C:157-160 -->.

## Why it is built this way

**Train expensive, ship cheap.** The phone's expensive sensors do the teaching, and the historical deployed head reads camera pixels alone <!-- C:165-166 -->. There are two teachers, and each trained a different head <!-- C:154 -->. LiDAR depth taught the earlier phone-bump head where not to drive, as scaffolding only: "the trained head never sees depth" <!-- C:77-78 -->. The phone's ARKit pose taught the steering head, with no LiDAR and no human labels <!-- C:160-162 -->. The historical truck used a phone camera and that steering head (weights are not included), 385 parameters on a frozen DINOv2-S/14 backbone <!-- C:75-76, docs/reports/2026-10-02-model-identity.md:11 -->.

**Self-labelling.** The label comes from the act. In the phone-bump experiment the bump is the label <!-- C:26 -->: bump the phone body near an obstacle, the accelerometer marks when and LiDAR marks where, with no manual image labeling <!-- C:41, C:160 -->. 30 bumps gave 147,731 labeled patches over 927 frames <!-- F:216, C:50-51 -->. On the truck the driven path is the label: the steering head learns the curvature to a point 3 m along where the truck actually went <!-- C:160 -->, and in teach and repeat, laps driven by hand become the path the truck repeats <!-- F:1785 -->. Three referees grade the student; none of them draws a label: the ceiling camera, which never graded a whole lap <!-- C:427 -->; the pose grader, the phone's own track against a path driven by hand <!-- C:350, C:447 -->; and Colin, who designs the tests, drives and referees <!-- C:282-283 -->. The idea came from the [phone-bump experiment](../research/results.md#background-the-phone-bump-experiment-this-project-grew-from).

**Measured, not claimed.** No number goes on these pages until it is in the research record <!-- C:7 -->, and every number carries its source in a comment beside it. [Corrections](../research/corrections.md) lists each number or claim that was withdrawn or corrected, with the claim it reverses and the date <!-- C:745 -->. Being wrong is part of the work, and these pages say so plainly <!-- C:707, C:709-710 -->.

## What the truck does

A phone on the truck is its only perception sensor, and an Orin turns the phone's camera into steering <!-- C:163 -->. On a park trail, on camera alone, the steering head had the wheel for 99.6 s of a 212 s run; its longest stint covered the whole 18.4 m leg, and every hand-back was at a turnaround <!-- F:2410 -->. On an indoor tape oval it ran teach and repeat: three laps driven by hand became the taught path <!-- F:1785 -->, and with that path and the camera together 15 of 24 lap segments ran at full steering-head authority, against 2 of 14 for the camera alone <!-- F:1790 C:780-782 -->. With the camera starved on purpose, a return mode brought the truck back to the taught path: three laps with no human input, worst excursion 0.26 m <!-- F:1854 -->. Recovery of a stuck truck is not shown yet: reversing did not free a wedged truck <!-- F:2084 -->. The limits are on [cannot-do-yet](../research/cannot-do-yet.md).
