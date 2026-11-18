# Project memory

## 3D-print files (3MF / STL): always use the `print-3mf` skill

Whenever the user asks for a 3D file, 3D-print file, 3MF, STL, magnet, ornament, keychain,
figurine or "make this printable", invoke the **print-3mf** skill
(`.claude/skills/print-3mf/`) and follow its SKILL.md. Do not use outside services (e.g. Meshy)
unless the user asks for them. Write outputs to the session scratchpad and send them with
SendUserFile.

### What made the Christmas tree go from "doesn't look good" to "looks great" — repeat these

1. **Trace, don't guess.** Measure the reference picture in pixels (green-mask row widths) and use
   the flat/back-view silhouette as the outline at true scale. Guessed polygons and circles gave
   wrong proportions: tiers that looked like balls, pointed ends, a thin waist.
2. **Thickness from the side view.** Each layer's depth comes from side-view row widths (the tree
   tiers were 11.2 / 10.4 / 9.0 mm, the star 7–8 mm). Each layer is thickest at its bottom rim and
   tapers almost linearly toward its top, so it tucks under the layer above.
3. **One pillow per layer, combined with a hard max.** That gives crisp creases where one layer
   overlaps the next. Smooth-blending everything turned the tree into one lumpy blob.
4. **Details read as the real object.** Scalloped rims with lobes hanging *down*; convex tips
   rounded (EDT opening ~0.9 mm); a gentle crown across each layer's width; a round trunk
   (x-only profile); a chunky analytic star (`star5(r-k, 0.56) - k`) with a high edge wall so its
   arms don't sink into the tier below.
5. **Functional parts in true mm.** Hanging hole exactly 4 mm, loop built separately. Then fit the
   rest so the total height is exact.
6. **Finishing pass.** Filleted neck where the loop meets the body (removes the crease and adds
   strength); 0.3 mm 45° bed chamfer against elephant's foot; extra smoothing on pinch lines.
   Check close-ups.
7. **Blender finish.** Volume-preserving Laplacian smoothing with the flat back and the hole
   pinned, then decimation (750k → 262k triangles, 6.8 → 2.9 MB). Verify watertight, hole size
   and bed contact area afterwards.
8. **Present it well.** Blender Cycles studio renders: matte PLA, big soft key light, AgX,
   shadow catcher, ghost-alpha cleanup, `shade_smooth_by_angle`. The flat point-splat preview made
   good geometry look worse than it was.
9. **Loop every iteration.** Render, put it side by side with the reference, name the biggest
   difference, fix it, repeat. Always run `verify_3mf` (watertight, size) before sending.

Default print assumptions: flat back on the bed, no supports, PLA, 0.16–0.2 mm layers, 3 walls,
15 % infill. State honest weight and size, and flag when labels on a design sheet contradict
the drawing.
