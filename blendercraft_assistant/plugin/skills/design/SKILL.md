---
name: design
description: Use when creating or judging how a 3D model looks (any request for something "nice", "clean", "good looking" or a product-like object). Covers proportion, hierarchy of forms, symmetry, consistent bevel and fillet radii, silhouette, and the signs that make a model look amateur, with a self-review checklist for screenshots.
---

# Designer's eye

Aim for an object that looks intentional. Decide the design before modelling;
judge it with screenshots afterwards.

Organic or artistic subjects (animals, pets, characters, figurines, toys,
plants): follow blendercraft:quality-workflow (dossier, stages, gates) and
build with blendercraft:organic-modeling. Never assemble them from separate
primitives. This skill covers product-like and hard-surface objects.

## Plan first (three lines)
1. Purpose and the one dominant form (a block, a wedge, a cylinder).
2. Secondary forms that serve it (a recess, a lip, a foot) and what stays
   plain.
3. Key dimensions in mm, from real-world use (hand, phone, screw size), not
   arbitrary round numbers.

## Proportion
- Pick one ratio family and reuse it: 1:1.618 (golden), 2:3, 3:4, or simple
  1:2 / 1:3. Avoid near-misses like 1:1.1, they look like mistakes.
- Make big things clearly bigger: a secondary form should be about half or
  less of the primary, or equal on purpose. Two forms at 70% and 80% fight.
- Thickness should read as deliberate: walls, rims and plates at 2 mm vs
  4 mm vs 8 mm; keep to two or three thicknesses across the object.
- Keep a visual base heavier than the top for stable objects; lighter on top
  for something meant to feel airy.

## Hierarchy of forms
Primary mass, then secondary features, then small details. Details belong
near where the hand or eye lands, and should be fewer than you think. Group
repeated details with equal spacing (use Array). One accent beats five.

## Symmetry and alignment
- Use real symmetry (Mirror, clipped at the plane) for anything that should
  be symmetric; never hand-place both sides.
- Align edges and centres: features share baselines, centres and spacing.
  Check with `inspect_object` bounding boxes, not by eye alone.
- If you break symmetry, break it clearly (offset by a lot), not by 1 mm.

## Radii and chamfers
- One bevel radius family per object: for example 1 mm for small edges, 2-3 mm
  for main edges, and the outer radius = inner radius + wall thickness on
  nested curves (so parallel curves stay parallel).
- Bevel width scales with part size: roughly 2-5% of the smallest dimension
  for consumer-product softness, under 1% for a crisp technical look.
- Segments: 3-4 for a smooth fillet, 1 for a chamfer. Use angle-limited
  bevels so flat faces stay clean.
- Sharp 90-degree edges everywhere look unfinished and also feel bad in the
  hand. Round the edges the user touches; keep functional edges (mating
  faces) crisp.
- Mixed radii and chamfers on the same level of edge look accidental.

## Silhouette
Look at the `front`, `right` and `top` views; the outline should read as the
object even in solid black. Prefer a clear, simple shape with one interesting
cut or curve. Add a small taper or draft (3-5 degrees) to tall sides, and soft
transitions between a thin neck and a wide base. Leave negative space on
purpose.

## Signs of amateur work
- Default cube dimensions (2 x 2 x 2) or sizes that make no real-world sense.
- Every edge sharp, or every edge rounded by the same large amount.
- Floating or barely touching parts; overlapping details that visibly poke
  through; z-fighting stripes.
- Features not centred, or off by a few mm with no reason.
- Too many tiny details with no big form; or one huge flat slab.
- Stretched or non-uniform scaling (squashed cylinders, oval holes that
  should be round).
- Faceted curves (fewer than 48 segments on a visible circle) or dark
  shading artefacts.
- A uniform grey with no material accent when colour was requested.
- Holes too close to edges (less than 2x hole diameter margin).

## Self-review checklist (do after screenshots)
Take at least `iso` plus one orthographic view, and check each:
1. Proportion: does the main form dominate? Any near-miss ratios?
2. Edges: consistent radii, nothing razor sharp where a hand touches it.
3. Alignment: symmetric, centred, evenly spaced.
4. Intersections: nothing floating, poking through, or z-fighting.
5. Function: does it do the job (holds the phone at a usable angle, fits the
   screw)? Is it printable (see blendercraft:printing)?
Write one or two honest sentences on what is good and what you would still
improve. If you spot a flaw, fix it and re-shoot before you finish.
