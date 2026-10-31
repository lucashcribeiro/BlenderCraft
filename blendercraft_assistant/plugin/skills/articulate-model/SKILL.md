---
name: articulate-model
description: Use when the user imported a ready model (STL/3MF from MakerWorld/Meshy, or a SolidCraft project) and wants it ARTICULATED for print-in-place - cut into parts with real joints (legs, waist, neck, tail), fix an existing articulated model whose joints weld, apply or create an articulation template, pose the legs standing or flat, or measure a figure whose legs are in print pose. Covers prepare_mesh, analyze_figure, apply_articulation_template, cut_part, add_joint, articulate_figure, set_pose, measure_standing, compare_surface and the export.
---

# Articulate an imported model (print-in-place)

The binding spec is `reference/projeto-executivo-articulacao-quadrupedes.md`
(read sections 1, 5, 7, 8 and 11 before the first cut). Its core rule: **the
sculpt is the product**. Never remesh, smooth or decimate the whole model;
only the cut zones change, and you prove it with `compare_surface`.

## Hard rules
- Clearances are absolute and never scale: **0.30 mm** per side print-in-place,
  0.20 mm assembled. Walls >= 1.2 mm, pins embedded >= 1.5 mm in each wall.
- Every hole, socket, pocket and gap is a real EXACT boolean; no overlapping
  solids (the 2.0 CACHORRINHO welded because pins and balls overlapped).
- One watertight shell per part (`mesh_topology`); the tools refuse and roll
  back otherwise.
- Check left/right mirrors (legs, pads) every time.
- Ask before cutting: show the plan (helpers + screenshots + table) and wait
  for the user's OK.
- Each tool call must stay short (< 60 s): one cut + one joint per call on
  heavy meshes.
- **If the legs are in print pose, never refuse to measure**: use
  `measure_standing` (or `set_pose standing` -> measure -> `set_pose print`).

## Templates
To articulate a new model, apply the default template **cachorrinho-2.0**
(the executive spec) unless the user names another
(`list_articulation_templates`). To create a new template from a finished,
split model (SolidCraft names: 01- front body, 02- rear body,
04-perna-frente|tras-x+|-, 06-bola-, 07-furo-, 08-pino-perna-, 09-pino-meio,
10-calota-meio, 11- pads), use `extract_articulation_template` (pass
`standing_height_mm` when the model is saved in print pose).

## Workflow: one-piece STL -> articulated toy
1. **Import** (File > Abrir modelo...). Scale uniformly to the height the
   user wants (default: as imported).
2. **Prepare without damage**: `prepare_mesh(name, split_shells=true)` -
   local repair only, keeps the hidden copy "<name> (STL original)", reports
   shells and near shells. Shells closer than 0.3 mm (e.g. legs Meshy made
   separate) become their own parts; the joint opens the real clearance.
   Never pass voxel_mm/target_faces on the final surface.
3. **Landmarks**: `analyze_figure(name, others=[separate shells])`. If the
   legs are not standing, `measure_standing` first.
4. **Plan**: `apply_articulation_template(name, options={others: [...]})` -
   returns the measurement table, notes and the steps (waist cut + ball, per
   leg cut + pin joint swept 0-90 deg outward) and draws helpers in
   collection BC_Plano. Take `screenshot`s (iso, front, side, bottom), show
   the table, ask for confirmation. Adjust options (clearance_mm, legs,
   waist, leg_range_deg, leg_axis 'walk') if the user wants.
5. **Build in short calls**: `articulate_figure(name, plan={steps: <steps>,
   only: [i, i+1]})` for each cut + joint pair (helpers are removed
   automatically). Read every table row: `ok` (gap >= clearance - 0.02),
   `built_range_deg` covering the target, `retained`. On a failed step: read
   the error (shell volumes, gap, walls), fix the step (pivot, size_mm,
   drop_shells_under_mm3 for decorative loose bits) and rerun only it.
6. **Verify**: `mesh_topology` + `print_check(min_wall_mm=1.2)` on every
   part; `compare_surface(original="<name> (STL original)")` - 100 %
   identical outside the changed zones (waist, under the thighs);
   `joint_test` 0-90 deg on each leg in 5 deg steps.
7. **Print pose**: `set_pose print` (legs flat, body on the bed), screenshot,
   then `export_file` .3mf with all parts (not the hidden original, not the
   pivots) - separate objects, assembled, in mm. `set_pose rest` undoes the
   posing.

## Manual tools (when the template does not fit)
- `cut_part(name, cutter, new_part_name, gap_mm=0.3)`: plane / box /
  cylinder / sphere / union cutters; the new part is inside the cutter.
- `add_joint(parent, child, type, pivot, axis, size_mm, clearance_mm,
  range_deg)`: **pin** (pivot inside the child: hole in the child, pin fused
  to the parent and anchored in both walls), **ball** (pivot in the parent:
  ball + neck on the child, socket with lip and mouth in the parent; neck_mm,
  mouth_mm), **cap** (spherical cap + concave face, guide only).
  range_deg is +-r or [min, max] about the axis (right-hand rule). relief
  carves the parent where the child turns (relief_radius_mm). Measures and
  rolls back below clearance - 0.02.
- `fit_clearance(female, male, clearance_mm, pivot, axis, range_deg)`: real
  clearance between existing parts (pins in holes, balls in sockets, legs in
  pockets).
- `boolean_parts(target, tools, union|difference|intersect)`: fuse a ball
  into a leg, subtract hole solids (07-furo) and so on.

## Fixing an existing articulated model (like the 2.0 CACHORRINHO)
Parts are already split and named. For each joint: fuse the male parts that
belong to a part (`boolean_parts union`: leg + its 06-bola), open the real
clearance (`fit_clearance` female=body, male=leg, pivot = pin centre, axis =
pin axis, range_deg [0, 90] outward; female=leg, male=pin for the hole), fuse
the pin into the body, delete hole solids, then measure every pair (>= 0.28
mm), `measure_standing` for the dossier table, mirror check, export.

## Report to the user
Table per joint: target clearance vs measured, range target vs free range,
retained, walls; per part: shells, manifold; compare_surface percentage;
timings; what you could not verify.
