---
name: joints
description: Use when parts must move or fit together - pins, hinges, snap-fits, sockets, print-in-place articulations, anything with a clearance gap. Gives the pin-and-hole recipe with clearance and a catch, then the validation with joint_create, joint_test and mesh_topology.
---

# Joints: pin, clearance hole, catch, and validation

Parts that move or mate are separate objects with a deliberate gap. Decide the
axis, the pivot point (mm, world) and the clearance first (see
blendercraft:printing: 0.3-0.5 mm per side for print-in-place, 0.2-0.3 mm for
assembled parts).

## Recipe: hinge pin (two knuckles on one pin)
1. Model the parts in their closed or rest pose, as separate objects, and name
   them (`Arm`, `Base`).
2. Pin: cylinder along the axis, diameter 3-5 mm. Give one end a head
   (diameter pin + 2 mm, 1.5 mm tall) so it cannot slip out. Print the pin
   lying down if it is a separate part (layers along its length).
3. Hole in the moving part: diameter = pin + 2 x clearance (e.g. pin 4 mm,
   clearance 0.4 mm -> 4.8 mm hole). Cut with a cylinder Boolean (EXACT),
   cutter 0.5 mm longer than the part each side.
4. Fixed part: hole for the pin at the pin diameter + 0.1 mm (snug) or fuse the
   pin to it with a Boolean UNION so it cannot turn.
5. Side gap between the moving part and neighbouring faces: at least 0.4 mm
   (0.5 for safety), or the part fuses to its neighbour.
6. Catch (locking so the pin stays): a small bump ring on the pin
   (0.3-0.4 mm proud) with a matching recess, or a chamfered head that snaps
   past a lip of 0.3 mm; leave the lip on a flexible 1.2 mm+ wall.

## Recipe: socket and snap-fit (assembled)
- Plug into socket with 0.2-0.3 mm clearance per side, a 45 deg lead-in chamfer
  of 0.5-1 mm on both parts, and the plug tip slightly smaller.
- Snap hook: length 8-12 mm, thickness 1.2-1.6 mm, hook depth 0.6-0.8 mm,
  flexing in the plane of the layers; fillet the root (r >= 1 mm).

## Recipe: print-in-place hinge
- Axis along X or Y lying on the bed (never vertical).
- Pin and hole with 0.4 mm clearance per side, cones instead of flat shoulders
  (45 deg) so no support is needed inside the knuckle.
- Parts must not touch anywhere, including the end faces: verify with
  `measure` (a and b) that every gap is at least 0.4 mm.

## Validate with the tools
1. `joint_create(part, point, axis, description)` makes the pivot empty
   `BC_Pivo_<part>` at `point` (mm, world) and parents the part to it. `axis`
   is X, Y or Z. Put `point` on the pin's centre line.
2. `joint_test(pivot, start, end, step)`: sweeps the angle, stops at the first
   collision. Read `colliding_at_rest` (must be false), `free_min`,
   `free_max` (the range the part turns freely), and the collisions list. Use
   a small `step` (2-5 deg) so thin obstacles are not missed; check the range
   covers what the user needs (e.g. 0 to 90 deg).
3. `joint_set_angle(pivot, degrees)` to pose the part (e.g. at `free_max` and
   at 0) and take `screenshot`s at both extremes. Put the angle back to 0 afterwards;
   `joint_test` restores it by itself.
4. `mesh_topology` on each part: manifold, one component, not inverted; the
   Boolean holes should leave no non-manifold edges.
5. `measure(a, b)` between the pin and the hole wall: gap equals the clearance
   you designed (not 0).
6. If `colliding_at_rest` is true or the range is too small: widen the
   clearance by 0.1-0.2 mm, trim the colliding corner with a chamfer or
   Boolean, or move the pivot point; run `joint_test` again.

Report the free range, the clearance, and the clearances you could not
verify. The joint pivot and any helper empties are not printable: do not
export them. The user can turn the pivot with the panel slider; `undo` removes
your changes if they are wrong.
