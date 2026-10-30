---
name: printing
description: Use when a model will be 3D printed or exported to STL/3MF, or the user mentions printing, a slicer, FDM, supports, tolerance or fit. Gives the numeric rules (walls, overhangs, clearances, minimum detail, orientation, bridging, supports) and how to check them with mesh_topology and print_check.
---

# 3D printing rules (FDM, 0.4 mm nozzle, 0.2 mm layers)

Assume FDM with a 0.4 mm nozzle unless the user says otherwise (resin/SLA
allows thinner walls and finer detail, so say what you assumed). Units: mm.

## Numbers to design to
| Feature | Minimum | Better |
|---|---|---|
| Wall thickness | 0.8 mm (2 perimeters) | 1.2-2 mm; structural parts 2.4 mm+ |
| Top / bottom solid | 0.8 mm | 1.0-1.2 mm |
| Embossed / engraved detail | 0.4 mm wide, 0.4 mm deep | 0.6-1 mm; text height 6 mm+, stroke 0.8 mm |
| Small hole diameter | 2 mm prints OK | add 0.2-0.4 mm to the nominal (holes shrink) |
| Pin / post diameter | 3 mm | 4 mm+; 5 mm for load |
| Gap between parts that must stay separate | 0.4 mm | 0.5 mm |
| Bridge span without support | up to 10 mm | under 5 mm |
| Overhang angle | 45 deg from vertical | steeper (closer to vertical) is always better |
| Sharp internal corners | allowed | fillet 0.5 mm+ to avoid stress cracks |

## Clearances (fit between separate parts)
- Press / snug fit: 0.1-0.15 mm per side.
- Normal sliding or removable fit: 0.2-0.3 mm per side.
- Loose fit and print-in-place articulation (hinge, ball joint): 0.3-0.5 mm per
  side (0.4 as the default).
- Clearance is per side: a hole for a 5 mm pin is 5.4-5.6 mm for a sliding
  fit. Vertical gaps (Z) need at least one layer height more than XY gaps.
- State the clearance you used in your answer; the user's printer may need
  tuning.

## Overhangs and supports
- `print_check` `overhang_deg` is measured from vertical (slicer convention).
  The default 45 flags faces leaning more than 45 deg from vertical. Faces
  resting on the bed are ignored.
- Prefer designing out supports: chamfer 45 deg instead of horizontal shelves,
  teardrop or diamond shapes for horizontal holes, split the model so each
  part has a flat face on the bed.
- If a surface must overhang, make it a 45 deg chamfer or a bridge under 5 mm.
- When supports are unavoidable, say where they are needed and that the
  surface touching them will be rougher; keep functional surfaces away from
  supports.

## Orientation
- Largest flat face down on Z = 0 (no part below the bed: `below_bed_mm` must
  be 0).
- Layers are weak in tension: orient so loads run along layers, not across
  them (print pins and clips lying flat, never upright).
- Put cosmetic faces on the sides or top, not touching supports.
- Move the object so its lowest point is at Z = 0 before export.

## Strength and stability
- Add a base or a brim area for tall thin parts (height more than 4x base
  width wobbles). Fillets at thin joins (2 mm+).
- Holes and slots: 3+ mm of material between a hole edge and a free edge.
- Avoid features thinner than 2 perimeters in a stressed area.

## Checks before saying "printable"
1. `mesh_topology` on every part to be printed: 1 component (unless
   separate parts are intended), manifold, no boundary or non-manifold edges,
   not inverted.
2. `print_check` with the right settings: `min_wall_mm` 0.8 (1.2 if the
   user wants robust), `overhang_deg` 45. Read `thin_faces`, `min_wall_mm`,
   `overhang_area_mm2`, `below_bed_mm`.
3. `measure` the key dimensions and the gap between mating parts
   (`measure` a and b: the gap must be at least the clearance you designed).
4. Take a `bottom` and an `iso` screenshot to see what touches the bed.
5. Apply scale and modifiers consistently; export with `export_file`
   (STL/3MF are always mm, so the slicer will read the right size).
Report the numbers, and be honest about the thin or overhanging spots you
could not remove. Fixes in order of preference: thicken with Solidify or
offset, add a chamfer, split the part, then Remesh VOXEL (0.2-0.3 mm) only for
broken meshes.

## Export
Use 3MF for multi-colour or multi-part, STL for single colour. One object per
printable part, named clearly. Do not export helper objects (cutters, pivots,
empties, reference guides): hide or delete them first.
