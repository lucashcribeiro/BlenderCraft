---
name: quality-workflow
description: Use for ANY request to make an organic, artistic or decorative model (animal, pet, character, figurine, toy, creature, plant, sculpture, gift), and for any figure built from references or a measurement dossier. Default staged process with a quality gate after every stage (screenshots iso/front/side, a checklist, a progress note), a measurement dossier written before modelling, and a final target-vs-built table. Aim for collectible-figurine quality, never a primitive toy.
---

# Quality workflow: dossier first, then stages with gates

The result must look like a collectible figure (Pixar-like stylized or
realistic), never a toy made of primitives. Separate spheres, cubes or
cylinders stuck together are NOT acceptable for organic subjects: build
every part as one soft shell with blendercraft:organic-modeling.

## Before anything
- Style: if the request does not say, ask ONE question: "realista ou
  estilizado (miniatura colecionável)?" Default when the user does not care:
  stylized collectible figurine. Also ask the height in mm if it matters
  (default 35-50 mm) and whether it should be articulated.
- If a `generate_3d_model` tool is available, prefer it for realistic or
  figurine requests, then refine its result with these stages (stage 2
  remesh, stage 4b colour regions, stages 5-7). Otherwise model from code.

## Stage 0 - reference and measurement dossier
Write the dossier BEFORE modelling, from the user's photos, references or
your own design, as Markdown saved next to the .blend (or in
`~/Documents/BlenderCraft/<name>-dossie.md` for an unsaved file), in the
user's language, in the format of the user's puppy dossier
(`reference/articulated-puppy-35mm.md` in organic-modeling):
1. Overview table: height, length, width, back and belly heights, head above
   back; each as mm and % of height.
2. One table per part (head, muzzle, ears, eyes, nose, torso halves, legs,
   paws, tail, accessories): size, position (length axis from the rear,
   heights from the ground), % of height.
3. Joints: type (pin hinge, ball and socket, mid-body cap), axis, pivot,
   range, ball/pin and socket/hole diameters, clearance per side.
4. Parts list: every print part and every colour region (one object each),
   with its colour.
5. Target measurements at a reference height (e.g. 35 mm) and the scaling
   rule: multiply everything by height / reference EXCEPT clearances.
Gate: the numbers are consistent (parts add up to the totals, joints fit
inside the parts with walls >= 0.8 mm). Flag contradictions to the user and
say which value you chose. Note: for a known preset (art.PUPPY_35) the
dossier already exists; adapt it.

## Stages (one at a time; never advance before the gate passes)
At every gate: screenshot `iso`, `front` and `right` (add `top`/`bottom`
when useful), write the stage checklist with pass/fail for each item, fix
what failed and re-shoot, then post one short progress line to the user
("Etapa 2/7 ok: formas primárias, proporções conferidas").

1. Block-out / silhouette: big masses only (low-res metaballs). Check the
   silhouette in 3 views against the overview table: head/height ratio,
   length/height, leg length, belly clearance. Fix proportions here; it is
   cheap now and expensive later.
2. Primary forms: one continuous watertight shell per part (blob ->
   `remesh_smooth` -> `fit` to the table). Check: no seams or lumps inside a
   part, smooth transitions (neck, haunches, ears), sizes within 3 % of the
   table (`inspect_object` bounds), left/right symmetric.
3. Secondary forms: eyes with pockets and highlight, muzzle, nose, mouth
   groove, ears, paws with toe grooves, collar/bandana. Check: eyes big, low,
   wide, looking slightly outward; nose on the muzzle tip; nothing floating
   or poking through; colour inserts never overlap their base.
4. Surface and texture:
   a. Physical texture for printing: relief in geometry (fur tufts,
      grooves, pads, fabric folds) with `kit.tufts`, `kit.groove` or
      `kit.displace` (procedural Displace, applied). Minimum 0.4 mm wide,
      0.2 mm deep, nothing finer than the 0.2 mm layer; only where it reads
      at print scale. Enable cavity shading for the gate shots.
   b. Colour and material: one object per colour region (`kit.paint`),
      glossy eyes, subtle variation for presentation renders. For an
      AI/GLB mesh with PBR textures: keep it for preview, then
      `kit.texture_face_colours` + `kit.split_colour_regions` to derive
      closed colour inserts. UV unwrap (Smart UV Project) only when image
      textures are used.
   Check: relief visible but subtle, no relief inside colour pockets, every
   colour is its own object, colours match the dossier.
5. Articulation (if asked): joints from blendercraft:joints and the
   organic-modeling recipe; real EXACT boolean cuts; `measure` each pair
   (min_gap >= clearance - 0.02), `joint_create` + `joint_test` over the
   designed range (colliding_at_rest false), pose 2-3 joints for a shot.
6. Print prep: `mesh_topology` per part (1 component, manifold, not
   inverted), `print_check` (min_wall 0.8, overhangs, below_bed 0), feet or
   base flat on Z = 0, scale at the target height, clearances unscaled,
   mirrored parts equal (volumes within 0.1 %).
7. Final validation and presentation: target-vs-built table for the key
   dossier measurements (target, built, difference %), the measured
   clearance per joint, the joint ranges, then the presentation shots
   (iso + front + side, posed) and `export_file` (3MF for colours, one
   object per part and colour; no pivots, cutters or helpers).

## Critique loop (inside every gate, at least twice before presenting)
Look at the screenshots as a toy designer and list concrete defects
against this checklist, then fix and re-shoot:
1. Silhouette: readable as the subject in solid black from each view?
2. Proportions: match the dossier / appeal rules (head 40-45 %, big low
   eyes, short muzzle, chunky legs)?
3. Seams: no visible seam or crease inside a part; seams only between
   parts, clean and even (the designed gap).
4. Smoothness: no voxel stairs, faceting, dents or lumps; soft edges.
5. Eyes and face: eyes same size, symmetric, highlight on the same side,
   nose centred on the muzzle tip, mouth readable.
6. Symmetry and pose: left/right mirrored; a slight head turn or tilt for
   life if the user wants a pose.
7. Print: nothing floating, no thin spikes, parts fit the bed.
Write the defects you found and what you changed; if a defect remains,
say so in the final answer.
