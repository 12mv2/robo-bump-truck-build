# Assembly: make it easy to fix

Author: Colin Rooney · © 2026 Rooney Industries LLC · MIT licence

I want to drive this thing, learn something, fix what broke, and keep going. It is still an RC truck. Rollovers and crashes are part of the fun; spending the rest of the afternoon rebuilding a precious mount is less fun. Protect the expensive phone and compute, and make the ordinary repairs quick.

This is mounting guidance alongside the historical [prints](prints.md), [parts](parts.md) and [commissioning boundaries](build-checkpoints.md), not a newly accepted drive recipe.

## Build completion list

The five [cradle reference files](../prints/phone-cradle/README.md) are a chosen phone-mount reference: four core parts and an optional single shim, rather than a universal truck kit. Secure band/strap mounting is a normal choice; optional trays can locate and support equipment. Use historical carrier designs if they suit your build, or choose secure supports with bands or straps.

- Choose a secure mount and check board support, electrical separation, fan, connector and FC servo-rail clearance, equipment retention and shell removal.
- If using the reference T-bar, check its selected body-post interface and hardware on your truck. Historical black-block identity and hardware counts remain unknown; they do not block an alternative mount.
- Fit the selected cradle and phone. Check the two M4 tray/riser bolts for actual engagement and no phone contact; see the [cradle instructions](../prints/phone-cradle/README.md#assembly).
- Match cable data capability, connector ends, pinout and polarity. Choose convenient routing with slack and strain relief; no fixed cable route or exhaustive band/fastener count is required.
- Full firmware, parameter and radio model/mixer exports, and the intended controller package, remain necessary before powered commissioning under the [first-drive boundary](first-drive.md).

Check your chosen mounting arrangement before powered use. Controller review and stopping checks remain separate requirements.

## Unpowered assembly sequence

1. Gather the selected [parts](parts.md) and choose secure band/strap mounting, with trays/supports if useful. Historical carrier files are optional references, not required kit deliverables.
2. Position and support the equipment. Keep the FC top servo rail, upward leads, USB/SD and telemetry access clear.
3. Retain equipment with suitable bands, straps or selected fasteners. Keep the Orin fan, linkage and gears clear; provide electrical separation where needed.
4. If choosing the reference cradle, follow its [joint schematic and instructions](../prints/phone-cradle/README.md#assembly). The optional single shim goes between tray and riser; its T-bar meets a body-post pair through the shell opening.
5. Choose cable routes with lifting slack and strain relief. Check secure retention, shell removal and phone camera/screen/USB clearance without applying power.

## Locate with a tray, retain for the job

I have tried bolts, gaffer tape, painter's tape and rubber bands. Overall, rubber bands are my favourite. They are quick to remove, let me reposition things, and give a little when the truck takes a hit. That is my practical preference, not a controlled crash comparison. Bands break and age; replace tired ones.

The tray's job is to locate and support the equipment. If something rarely comes out, bolting it to its tray can make sense. For the removable tray/equipment assembly, bands can make the difference between a quick repair and taking half the truck apart. Leave enough cable slack to lift the assembly out without pulling on a socket, and support the cable before it reaches the connector.

Route bands around the supports, clear of the board and its connectors. The flight-controller sandwich keeps its top servo rail and upward leads clear. Preserve the specific [print constraints](prints.md). Keep bands and straps away from exposed contacts, the Orin fan, steering linkage and gears. Use an insulating pad where needed between electronics and supports; a layer of tape is not a substitute for a sound mechanical fit.

Retain batteries in suitable holders/straps so a rollover cannot throw a pack into the wiring. Give the phone its correctly fitted cradle and retaining pin. Check that the shell can go on and come off without snagging a cable. A removable assembly still needs to stay attached when the truck is upside down.

## Keep the truck looking like a truck

My preference is to leave the shell mostly stock apart from the phone mount. The T-bar passes through the shell opening at the body-post interface; the wedge belongs between tray and riser, not at the T-bar foot. The [print page](prints.md) explains the selected-stack fit checks.

I considered Ethernet and USB bulkheads on top of the shell; they were not fitted. Neat, easy access would be nice. But those cables would tether the shell to the Orin, adding another unplugging step every time it comes off. Choose the inconvenience you prefer. There is no universal winner.

If my mounting choices sound like a personality test, blame my horoscope. That is a joke: I do not believe in astrology.

## Do the useful check, then keep going

Tiny-wire soldering was unpleasant. My prototype receiver connection ended up as a blue-tape blob. I would rather start with the correct [preterminated receiver harness](wiring-connectors.md) than repeat that repair.

For new wiring, disconnect every supply, including flight-controller USB; check continuity, polarity and the single receiver power path before applying power. Then use the existing supervised commissioning boundary to observe neutral, direction and stopping. A tidy harness does not resolve the recorded servo-supply or throttle/link-loss limits.

For mounting changes, check fit, retention, clearances and cable strain before moving on. Do the minimum useful test that answers the change you made. Once the separate drive acceptance is satisfied, drive it, expect repairs, and have fun. Fast iteration works when the phone survives and the next repair takes minutes.
