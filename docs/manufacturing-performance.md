# Manufacturing performance investigation

Measured on 2026-10-28 against the saved Chevrolet V8 build in the local playground. The main cost was
constructing local sampling meshes, rather than running process rules or calling the plugin framework.
Moving spatial rejection into refinement reduced manufacturing time from **51.73 s to 8.48 s**,
**6.1× faster**, with **all 102 findings identical**, including their measurements and reported locations.

## Workload and measurement

The design declares 53 part types: 34 made parts checked as FDM and 19 purchased parts excluded from
manufacturing. Repeated assembly instances do not multiply this work. Of the made parts, 33 have
measurable walls; the exhaust manifold reports its existing unmeasurable-wall finding.

Environment: Python 3.11.17, NumPy 2.4.6, SciPy 1.17.1, trimesh 5.1.1, libigl 2.6.3, Linux, 32 available
CPUs. The checks use 4,000 initial wall samples, 0.01 mm mesh tolerance, 0.01 mm position tolerance and
0.05° angular tolerance. The mesh files are reused unchanged. Loading STEP/STL and computing part metrics
are outside the timed region: the pipeline's earlier analyses already perform that work.

The initial profile reproduced the roadmap's approximately 53-second manufacturing stage:

| Operation | Cumulative time | Share of manufacturing |
| --- | ---: | ---: |
| Manufacturing, 34 parts | 52.30 s | 100% |
| Wall checks | 52.13 s | 99.7% |
| Local surface preparation, `_around` | 45.56 s | 87.1% |
| `subdivide_to_size`, inside preparation | 32.92 s | 62.9% |
| Wall measurements, `_thickness` | 5.52 s | 10.6% |
| Overhangs | 0.124 s | 0.24% |
| Bed contact | 0.040 s | 0.08% |

Rows are inclusive and overlap; they must not be added together. The optimized profile comprises two
fresh-body runs, 8.84 s and 8.91 s. Independent runs without the profiler took 51.72 s and 51.73 s before
the change, and 8.34 s and 8.61 s after it; the headline compares their medians. The before/after runs
use the same profiling harness, loaded model, settings and mesh digests, with the previous local sampler
restored for the baseline. These are manufacturing-kernel measurements, not whole-build times.
The V8 is an FDM workload; it does not establish timings for resin, CNC, sheet metal or molding.

| Part | Original, median without profiler | Optimized, median without profiler |
| --- | ---: | ---: |
| Pushrod | 16.40 s | 0.27 s |
| Wrist pin | 3.95 s | 0.14 s |
| Keeper pin | 4.10 s | 0.15 s |
| Main cap | 3.21 s | 0.16 s |
| Camshaft | 0.94 s | 0.55 s |
| Cylinder bank | 1.84 s | 1.17 s |

The input mesh content, concatenated in made-part declaration order, has SHA-256
`640740443909b2475a3b640dca92fb8ef7973046c3e99cef4bd23eb9fefa29b5`.

## Why the old algorithm did so much work

`Manufacturing.run` selects each made part's process and calls `process.checks(body)`. FDM's three
checks delegate wall measurements to `Body.walls`, shared with explicit wall requirements. That
measurement samples the surface, casts inward rays and measures local thickness, then refines up to
16 promising regions. A region's radius is approximately √(surface area / initial samples); its desired
point spacing is one eighth of the initial thickness reading, with up to 8,000 requested points.

The expensive step was the preparation of each region:

1. Select original triangles whose bounding spheres meet the region.
2. Subdivide those triangles in full until their edges meet the region's size.
3. Construct the refined mesh, calculate its triangle centres and extract the nearby portion.
4. Sample that portion and discard points outside the sampling ball.

A long, narrow triangle can meet a small region while extending far beyond it. Step 2 refines the
distant surface too. Refinement repeats this across 16 regions per part. Edge deduplication, sorting,
array allocation and mesh bookkeeping dominate the profile. The sample cap is applied **after** that
work, so it does not bound preparation cost.

Triangle count alone is consequently a poor predictor. In the baseline profile, the 10,390-triangle
pushrod took 16.46 s, while the 61,360-triangle camshaft took 0.93 s. The 500-triangle wrist pin took
3.98 s. Their long faces and the size of their sampling regions matter more than their raw mesh size.

libigl already provides an accelerated C++ AABB tree for rays and nearest points. A new ray library
would leave the dominant 45.56-second preparation step untouched.

## Implemented algorithm

`geometry/sampling.py:surface_patch` performs spatially restricted edge bisection. Before each
refinement level, it discards triangles whose axis-aligned boxes cannot meet the final sampling
region. Every descendant lies in its parent's triangle and box, so those discarded branches cannot
produce a retained triangle centre. Refinement proceeds only on the remaining branches.

The retained patch keeps the previous candidate selection, centroid margin, edge-splitting order,
face winding and mapping back to original faces. Areas and barycentric sampling need triangle
coordinates, so the patch uses independent triangle vertices and does not build shared topology.
The wall sampler retains its existing point count, seed, spatial filtering and thickness calculations.

This is a change to the shared measuring toolkit. FDM, resin, sheet-metal and molding wall checks, wall
requirements and interference's wall measurements use it through the same `wall_thickness` function.
No process-plugin interface or manufacturing thresholds change. No new exception handling, fallback
algorithm, dependency, cache or part-specific special case is introduced.

Validation compares the retained triangles, original face indices and seeded samples with full
refinement on planar, cylindrical, spherical and mixed-edge cases. A long-face regression measures
the refinement working set instead of asserting a fragile wall-clock limit. All five processes are
also exercised by the existing manufacturing regressions. The V8 wall-point count remains 408,683;
all 102 findings match exactly with the locked geometry dependencies.

The broader backend run (`pytest -m "not examples"`) finished with 207 passing tests and one failure
in `test_hitlcad_s_own_plugins_and_an_installed_one_load_alike`: the generated skill does not contain
the expected `reference/lasercut.md` link. That same test fails with the original sampler restored;
the failure is in the existing skill-rendering changes, outside the sampling implementation. Ruff
and all three import-layer contracts pass for this change.

### Alternative evaluated: clip first, then sample directly

A prototype clipped each source triangle to the sampling region's enclosing box and sampled the
clipped surface. It took 7.82 s, and avoided subdivision entirely. However, its different point
locations changed the cylinder-bank wall result from 0.74 mm/fail to 0.98 mm/pass. The original warning
comes from a small region near a taper; changing sampling also changes the nearest-neighbour filter
that decides whether readings represent a wall.

That prototype was not adopted. It demonstrates both a useful longer-term direction and the weakness
of treating one sampled minimum as a geometric certificate. A faster sampler needs its own coverage
and accuracy contract, rather than matching performance by changing random seeds or sample caps.

Spatial pruning is not a constant-memory guarantee: a degenerate fan of skinny triangles that all
cross the region can still leave a large retained patch. A stress experiment with a 2,000,000 mm-wide
triangle and its apex inside the region exposed this limit. Eliminating that case requires changing
the retained tessellation or sampling representation; pruning cannot discard contributing triangles.

## What existing projects do

| Project | Approach inspected | Consequence for hitlcad |
| --- | --- | --- |
| [CGAL surface mesh segmentation](https://doc.cgal.org/latest/Surface_mesh_segmentation/index.html) | Shape Diameter Function: rays sampled in a cone about each facet's inward normal; reject outliers and average lengths. The documented example uses 25 rays per facet. | Useful thickness evidence, but its smoothed local diameter is a different quantity from our minimum wall. It is not a behavior-preserving replacement. |
| [TU Delft masbpy](https://github.com/tudelft3d/masbpy/blob/841f80143b232ed90f7dcf67beac61de752653bb/masbpy/ma.py) | Shrinking balls over an oriented point cloud, using nearest-neighbour queries and convergence criteria. | The underlying idea already appears in our wall measurement. Their sampled point-cloud distances and our triangle-surface distances have different error properties. The profile does not justify replacing this part first. |
| [CuraEngine support generation](https://github.com/Ultimaker/CuraEngine/blob/47b03886dc200b00f17fd40d7739a9da5a7b8745/src/support.cpp) | Offset lower-layer outlines by a distance derived from layer height and support angle, then subtract them from current outlines. Layers are processed in parallel. | Layer polygons are a natural representation for print support. This needs slicing and layer settings; it would be a separate process-aware analysis, rather than a general wall-thickness optimization. |
| [PrusaSlicer bridge detection](https://github.com/prusa3d/PrusaSlicer/blob/30ef59195e0f3ee6f270b185bb5f9fb5f349f81f/src/libslic3r/src/libslic3r/BridgeDetector.cpp) | Find anchors against lower slices, clip candidate lines through bridge regions and score directions by supported coverage and span. | A better model for bridge direction and actual printability than our projected nearest/opposite-support heuristic. It also requires layer geometry and is not interchangeable with our current checks. |
| [Open3D RaycastingScene](https://www.open3d.org/docs/latest/python_api/open3d.t.geometry.RaycastingScene.html) and [implementation](https://github.com/isl-org/Open3D/blob/b6c5e196384ad71e75b6e6f9c5da22d046221f1d/cpp/open3d/t/geometry/RaycastingScene.cpp) | Shared acceleration structures with batched ray and closest-point queries; the CPU implementation uses Embree and TBB. | A candidate for benchmarking the remaining query cost. It does not address surface preparation and brings different numerical precision and dependency costs. No speedup was measured here. |
| [libigl Python AABB bindings](https://github.com/libigl/libigl-python-bindings/blob/2c0442da97245d52e41dd2c228bf73c04073edc7/src/AABB.cpp) | Exposes persistent trees and batched ray/nearest queries. | This is already our query mechanism. More Python threads should not be assumed to help without checking binding behavior and native thread use. |
| [trimesh sampling](https://trimesh.org/trimesh.sample.html) and [remeshing](https://trimesh.org/trimesh.remesh.html) | Surface sampling and mesh refinement are separate operations. Even sampling uses rejection and may return fewer points than requested. | Sample density is approximate; a point budget is not a bound on the cost of constructing a mesh to sample. Keep sampling work spatially restricted. |

The source-level conclusion is to choose representations for the measurement being made: local surface
samples for wall evidence, layer polygons for printing support, B-rep features for cutter corners and
bends, and a volume-connectivity representation for resin drainage. A single increasingly dense mesh
is not the best intermediate representation for every process.

## Remaining design work, in priority order

1. **Give adaptive wall sampling an explicit accuracy and coverage policy.** Refinement candidates are
   currently selected before knife-edge readings are filtered, and the seven-neighbour/third-reading
   rule depends on sample placement. Separate genuine wall evidence from taper evidence before
   spending the refinement budget. Evaluate a direct sampler against a corpus of analytic walls,
   rims, fins, narrow necks and threshold cases, including the cylinder-bank discrepancy. Preserve
   area weighting for typical wall statistics. Sampled evidence should not imply an exact global
   minimum.
2. **Reuse evidence and findings across builds with explicit dependencies.** The current
   `Body.walls` cache lasts one build, and `Manufacturing.run` does not use `ResultCache`. Cache wall
   evidence independently of process thresholds; cache findings separately by process settings.
   Moving a part in an assembly need not invalidate local manufacturing evidence. Keys need the
   actual geometry and tolerances: mesh content alone is insufficient for checks that read STEP
   features. Include implementation/plugin versions, and define what external processes may depend
   on before assuming all plugin checks are geometry-only. This improves repeated builds, not the
   cold algorithm measured above.
3. **Profile each process on representative workloads before changing its algorithm.** CNC visibility
   and molding undercuts still refine the entire surface in `_split`; local pruning does not apply
   to a query that intentionally covers every region. Area-weighted surface samples with original
   face identities could avoid that tessellation. FDM's `_unheld` compares each sampled point with
   every support sample; bounded spatial queries could reduce candidate pairs, but choosing a few
   nearest neighbours is insufficient to find support on the opposite side. Resin drainage builds
   a dense bounding-box grid and queries exterior air as well as hollows; its resolution/cost tradeoff
   needs separate study, potentially using adaptive volume connectivity or a distance transform.
   These are code-derived opportunities, not measured V8 bottlenecks.
4. **Benchmark query backends and concurrency after work reduction.** Rays and nearest-point queries
   now dominate the optimized wall measurement. Measure an alternative backend with the same
   numerical tests and workloads. Pick one level of parallelism deliberately; nested part workers
   and native query workers can compete for the same cores.

The existing division between process policy (`Process.checks`) and shared geometry measurement
(`Body`, `geometry/`) is useful. Keeping this separation of concerns puts work reduction and evidence
reuse in the measuring layer, while process thresholds remain in plugins. A new hierarchy of
per-process runners would duplicate responsibilities without addressing the measured cost.

## Reproduce

Use an existing successful build whose sources and `design.json` still match its saved geometry:

```bash
uv run python scripts/profile_manufacturing.py /path/to/design \
  --repeat 3 --out /tmp/manufacturing.json
uv run python scripts/profile_manufacturing.py /path/to/design \
  --repeat 1 --out /tmp/manufacturing-profile.json --profile /tmp/manufacturing.prof
python -m pstats /tmp/manufacturing.prof
```

The script loads the saved model through `load_built`, preloads part geometry, then creates fresh
measurement bodies for each repetition. It records per-part times, settings, mesh digests, dependency
versions, wall-point counts and findings. It does not rebuild parts, time exports, or use cached
manufacturing results. It covers the selected design's own parts, matching one invocation of the
manufacturing analysis; components can be measured using their own saved build directories.
