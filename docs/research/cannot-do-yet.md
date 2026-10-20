# Cannot do yet

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

This page lists what the truck has not been shown to do. Some limits are measured failures, and some were never tested; the table says which. Read [results](results.md) against this page: a number there holds only inside the conditions it was measured in. How the numbers were produced is in [method](method.md), and the walls the build hit are in [pitfalls](../pitfalls.md).

| Limit | Status |
|---|---|
| [Corners outdoors](#corners-outdoors) | Measured failure |
| [A solved corner on the oval](#a-solved-corner-on-the-oval) | Measured failure |
| [Other venues, operators, vehicles, obstacles, night, rain](#other-venues-other-operators-other-vehicles-dynamic-obstacles-night-rain) | Not tested |
| [Outdoor thermal sustainability](#outdoor-thermal-sustainability) | Indoor pass, outdoor failure |
| [A graded whole lap](#a-graded-whole-lap) | Measured failure |
| [The axle offset as a driven cause](#the-axle-offset-as-a-driven-cause) | Not tested on the vehicle |
| [Anything from 2026-09-13 onward](#anything-from-2026-09-13-onward) | Taught-path assisted |
| [Outdoor transfer of the oval result](#outdoor-transfer-of-the-oval-result) | Not tested |
| [The brownout](#the-brownout) | Inferred, never instrumented |
| [The curvature feed-forward's field value](#the-curvature-feed-forwards-field-value) | Replay only |
| [Speed as a controlled variable](#speed-as-a-controlled-variable) | Not tested |
| [Collision outcomes as an independent referee](#collision-outcomes-as-an-independent-referee) | Not tested |
| [IMU bump-to-map on the truck](#imu-bump-to-map-on-the-truck) | Not implemented |

## Corners outdoors

**Corners. At all.** Two field days, every setting tried, unsolved <!-- C:274-275 -->.

Supporting line: lookahead swept from 1.0 to 3.0 at gain 1.5 outdoors, and the truck left the trail in the bend every time <!-- F:1026 -->.

## A solved corner on the oval

**A solved corner.** Counter-clockwise every corner made, seven laps; clockwise the tightest corner missed every time, at cruise and slowed <!-- C:774-776 -->.

Supporting line: clockwise, the compound corner was made, then missed, then made, then missed; the entry went from never to sometimes, the exit did not move <!-- F:1509 -->.

## Other venues, other operators, other vehicles, dynamic obstacles, night, rain

**Other venues, other operators, other vehicles, dynamic obstacles, night, rain.** One park <!-- C:278, C:792-793 -->.

Not tested. No measurement stands behind this limit; the absence is the content.

## Outdoor thermal sustainability

**Outdoor thermal sustainability:** the 30-minute soak was indoors, and the one outdoor test failed <!-- C:288, C:793-794 -->.

Supporting lines: no thermal droop over 30 minutes, indoors on the bench <!-- F:628 -->, and the findings log marks outdoor thermal as untested <!-- F:632 -->. In the outdoor outing, frame delivery halved to 2.5 fps late in the outing <!-- F:325 -->; the cause was not resolved, and thermal was one of three candidates <!-- F:325 -->.

## A graded whole lap

**A graded whole lap.** The ceiling camera never produced one. Every ceiling number in this era is per-fragment <!-- C:777-778 -->.

Supporting line: re-graded offline, the ceiling camera's grader produced 35 fragments from 18 minutes of video, with a median sweep of 79 degrees and a maximum of 193 degrees where a lap is 360 <!-- F:1839 -->.

## The axle offset as a driven cause

**The axle offset as a driven cause.** Never A/B'd against zero on the vehicle <!-- C:779 -->.

Supporting line: the findings log records it as not yet A/B'd against the offset set to zero that night <!-- F:1447 -->.

## Anything from 2026-09-13 onward

**Anything from 2026-09-13 onward.** Those laps were taught-path assisted: camera alone scored 2 of 14 <!-- C:780-782 -->.

Supporting line: 15 of 24 lap segments with the taught path, against 2 of 14 for the camera alone <!-- F:1790 -->.

## Outdoor transfer of the oval result

**Outdoor transfer of anything measured on the oval.** The oval head is an oval head. The trail/no-trail gate for the blind stretch of the bend was specified and never driven <!-- C:783-784 -->.

Not tested. No measurement stands behind this limit; the absence is the content.

## The brownout

**The brownout.** Inferred, never instrumented; the servo rail still runs off the speed controller's built-in regulator, and the standalone supply is still not fitted <!-- C:785-786 -->.

Supporting line: there is no voltage or current telemetry to confirm the brownout directly <!-- F:992 -->.

## The curvature feed-forward's field value

**The curvature feed-forward's field value.** `--kff` fires on 12 % of deep-bend frames <!-- C:787-789, F:1052 --> and is inert at trail lookahead: 48 of 48 frames logged `few_pts` on a replay of a straight outdoor stretch <!-- C:787-789, F:1053 -->. Replay only.

## Speed as a controlled variable

**Speed as a controlled variable.** It was not one in this era. Never compare two cells without their speeds <!-- C:790-791 -->.

Not tested. No measurement stands behind this limit; the absence is the content.

## Collision outcomes as an independent referee

**Collision outcomes (once on the truck) are the future independent referee** <!-- C:93-94 -->.

Not tested. No measurement stands behind this limit; the absence is the content.

## IMU bump-to-map on the truck

IMU bump-to-map on the truck: not implemented (no code path on the truck does it; the phone experiment validated the labelling idea only). <!-- author statement, 2026-10-10 -->

The obstacle head has been scored on truck frames display-only (F:911) and truck-viewpoint results are unclaimed (C:104, C:111, C:157-160). <!-- F:911, C:104, C:111, C:157-160 -->
