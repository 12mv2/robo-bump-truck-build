# Robo Bump Truck — how to build one

Author: [Colin Rooney](https://vaguebutexciting.dev/) · © 2026 Rooney Industries LLC

Build a phone-camera RC trail truck using the recorded hardware and mounting references. **V1 finishes with a mechanically assembled, checked, unpowered truck ready for software and configuration; the reproducible autonomous controller package and full powered commissioning exports remain incomplete.**

![Body-off truck with mounted compute and flight-controller equipment; phone mount absent](docs/img/truck-assembly-2026-10-14.jpg)

Recorded build, 2026-10-14: body removed; phone and phone mount absent. Use this as a mounting reference for your own truck.

<a id="eventual-physical-build--historical-reference"></a>

**[Build yours → the assembly walkthrough](docs/assembly.md)**

Start there: check the shopping list, mount the equipment, fit the phone cradle, route and check the wiring, then reach the software checkpoint. Secure bands or straps with suitable supports are normal mounting choices; historical compute/flight-controller trays are optional references. The five supplied STL files cover a phone cradle, not a universal truck kit. Check your own phone and truck fit.

You can assemble unpowered parts and print the cradle without ROS. The walkthrough links the separate software and configuration prerequisites when you reach them. For orientation, see [how the system connects](docs/system.md).

## Project story and field reports

[Watch the historical trail run (15 s)](docs/img/field-run-trail-follow.mp4). Read the [research results and design story](docs/research/results.md), [method](docs/research/method.md), [corrections](docs/research/corrections.md) and [limits](docs/research/cannot-do-yet.md).

The [Vague but Exciting blog](https://vaguebutexciting.dev/) records the experiments:

- [Log 1 — I rearranged my kitchen to fool my robot](https://vaguebutexciting.dev/posts/kitchen-robot-eyes-log1/)
- [Log 2 — My robot drove itself down a trail. Then it met a corner.](https://vaguebutexciting.dev/posts/robot-drove-itself-then-a-corner-log2/)
- [Log 3 — My robot lapped my kitchen on its own](https://vaguebutexciting.dev/posts/robot-lapped-kitchen-log3/)

These are dated reports. Independent fresh-follow validation of this guide remains pending; no new build or powered acceptance pass is claimed.

For reference: [historical code snapshot](src/orin/MANIFEST.md), [model information](model/README.md), [community models](model/community.md) and [data descriptions](data/README.md) (sample downloads are not yet available).

## Licence

- Code: MIT, © 2026 Rooney Industries LLC ([LICENSE](LICENSE)).
- Data and photos: CC BY 4.0 ([data/LICENSE](data/LICENSE)). Attribution: Colin Rooney, Rooney Industries LLC.
- Third party: DINOv2-S/14 is Apache-2.0; see [model/NOTICE](model/NOTICE) for attribution and retained licence/copyright notice.
