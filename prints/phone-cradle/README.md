# Phone cradle reference prints

Author: Colin Rooney · © 2026 Rooney Industries LLC · [MIT licence](../../LICENSE)

Five recorded reference meshes are supplied here. They identify a historical design release, not the exact revision fitted later. No fresh print, phone-fit or strength acceptance accompanies this package. Historical truck compute and flight-controller carrier files, generators and combined plates are not supplied. Carrier designs are optional references: secure bands/straps with appropriate supports are a normal alternative. These phone-cradle files are a chosen reference, not a universal complete build kit.

| File / version | Purpose | Print quantity |
|---|---|---|
| [v26_tray.stl](v26_tray.stl) | Phone tray; screen-face captive M4 nuts | 1 |
| [v26_riser.stl](v26_riser.stl) | 20° mounting face and foot | 1 |
| [v66_tbar.stl](v66_tbar.stl) | Foot crossbar at body-post mount | 1 |
| [v21b_pin.stl](v21b_pin.stl) | Standard two-notch retaining pin, 4.2 × 2.85 mm section | 1 |
| [v10_wedge5.stl](v10_wedge5.stl) | Optional historical 5° angle shim between tray and riser | 1 only when wanted |

## Fit before use

This is a fit-specific historical phone envelope: recorded measurements were 148 × 70 × 7.5 mm body, with a 10 mm thick camera section starting 102.5 mm along the phone and a 13 mm wide lens cluster. The design places the phone from x = −56 mm, in y = 7.4..14.9 mm, with deeper camera grooves at x = 46..110 mm. These are design references, not a compatibility list for any named phone or case. Test the selected phone's fit and retention gently, then check screen access, camera field of view and USB connector/cable clearance separately. Neither this design nor the pin dimensions guarantee another printer's or phone's fit.

## Slice individual objects

The ASCII STLs preserve the supplied source normals, vertices and triangle order exactly. They are individual objects in design coordinates, **not preoriented print plates**. Standard STL has no units: import as **millimetres**. Conversion hashes and triangle counts are in [MANIFEST.json](MANIFEST.json).

| Part | Bed orientation |
|---|---|
| Tray | **Screen face DOWN**; captive hex nut pocket opens toward the bed |
| Riser | **BACK down**, the face with the Ø11 head/washer pockets; do not stand it on its foot |
| T-bar | Broad flat face down |
| 5° shim | Flat face down, slope up |
| Pin | Thin face down; shaft and head coplanar |

For tray and riser, rotate −90° around X, then translate the lowest Z to the bed; alternatively explicitly lay the named face on the bed in the slicer. Inspect the resulting orientation.

Recorded profile: PETG, 0.4 mm nozzle, six wall loops, Gyroid infill. No temperature or layer height is specified here. A brim was recorded for tall/thin footprints and optional on large flat faces. Support-free printing was the design aim, not a universal guarantee: inspect slicer islands, bridges and overhangs before printing and choose settings appropriate to the actual printer and material.

## Assembly

![Not-to-scale cradle schematic showing two M4 tray/riser bolt paths from riser back to tray screen-face nuts, plus the separate foot/T-bar joint](../../docs/img/cradle-assembly.svg)

The diagram explains assembly direction only; it is not a fitted-build image. See the truck's [build completion list](../../docs/assembly.md#build-completion-list) for secure mounting choices and body-post checks if using this T-bar.


1. Identify the tray camera face (model y = 0) and screen face (y = 17.2 mm, captive hex pockets). The riser's 20° face mates to the **TRAY CAMERA FACE**. The phone camera points toward the riser side and down; its deeper camera groove determines insertion orientation. Do not reverse the phone to compensate for a reversed mount.
2. Seat two M4 hex nuts in the tray screen-face pockets. Use **two M4 tray/riser bolts and two washers**, at the recorded 16 mm hole spacing. Each bolt runs: **head + washer in RISER BACK Ø11 pocket → solid riser → optional single shim → tray camera face → captive M4 hex nut in TRAY SCREEN face**. Verify the actual head and washer fit the pocket.
3. **M4×25 is a flat reference only**. For the single shim, the author recalls approximately **20–25 mm** bolt length. Consider that practical candidate range, then measure/check the actual stack: head and washer seating, adequate engaged thread, no bottoming and no phone contact. This approximate recollection cannot guarantee fit; 20 mm is not a universal wedge prescription. Do not change geometry to solve a bolt stack.
4. Join the riser foot to the T-bar with **two M4×16 bolts, two washers and two wing nuts**, the recorded reference. Verify as-built clearance and engagement, including around the body posts.
5. The T-bar spans either the front or rear left/right **body-post pair**, reaching through an opening in the body shell. It does **not** attach through a chassis slot. Body-post attachment hardware and its count are not established here.
6. Insert the phone in its groove-defined direction and fit **one** standard v21b two-notch pin gently through its retaining lane. Stop if it binds; verify retention and all clearances before use.

See [printed-part history](../../docs/prints.md) and [parts and remaining build gaps](../../docs/parts.md). Use these as the chosen phone-mount reference and check your selected assembly.
