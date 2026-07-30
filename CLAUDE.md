# The ClauderaCAM Constitution

This project emits toolpaths for a physical machine spinning sharp carbide at
12,000 RPM near human hands, clamps, and expensive stock. Every rule below
exists because something real broke or nearly broke. When in doubt, the
conservative reading of any article wins.

## Article I — The Verification Gate

No G-code file may be described as ready, safe, done, or cuttable unless
`verify` returns PASS on that exact file. Not the intent, not the config —
the bytes that would go to the machine.

The gate REFUSES what it cannot fully model, rather than skipping it: an
unknown tool, an arc in any spelling, a modal G-less line — each is fatal,
never ignored. An unmodeled move is an unverified move (the 2026-07-28
review found the previous parser silently dropped all three, letting a cut
through the fixture keep-out verify PASS).

Never weaken, special-case, or delete a check to make a job pass. Thresholds
change only with a physical-world justification written into DESIGN.md.
If verification is inconvenient, the toolpath is wrong, not the verifier.

## Article II — Incidents Make Law

Every safety requirement in DESIGN.md is traced to a real incident (a snapped
1mm ball nose; a rough pass that swept within 1.7mm of the corner clamps; a
0.075mm mask-registration error that produced a false FAIL). This is the
mechanism: when a new failure mode is discovered, it becomes a new check and
a new DESIGN.md entry. A check may be removed only when the incident it
guards against has become *impossible*, not merely unlikely.

## Article III — The Golden Test

`tests/golden_mango.py` regenerates the job that cut a real brass coin and
asserts **byte-identical** toolpaths. Run it after any change to
`heightmap.py`, `offset.py`, `engine.py`, `emit.py`, or anything in `ops/`.

An intentional output change requires: (1) an explanation of the numerical
difference in the commit message, (2) re-verification of the new output,
(3) explicitly re-blessed reference files. "The diff looks fine" is not one
of the three. The golden assets live in `assets/` (the coin STL) and
`tests/golden/` (its toolpaths AND the fully assembled program — the
preamble, tool-change and postamble lines are golden too, because the
geometric simulator is blind to them). `tests/reference_suite.py` must also
pass (four synthetic jobs, each stressing a different hazard), and
`tests/negative_suite.py` must catch all of its hazards — the negative
controls are what prove the gate can fail; without them an always-PASS
verifier clears the whole suite, which is exactly what the 2026-07-28
review demonstrated.

## Article IV — One Coordinate Convention

The carve mapping in `simulate.py` is the single source of truth:
`i = round((half − y)·ppm)`, `j = round((x + half)·ppm)`, inverted as
`x = j/ppm − half`, `y = half − i/ppm`. Never re-derive a grid center
(e.g. `(n−1)/2`) anywhere else. Half a truncated pixel is a real distance.

## Article V — Emission Sovereignty

All G-code flows through `emit.py`. No external post-processors, no ad-hoc
string building elsewhere. Dialect invariants for the Carvera: `G4 P` is in
SECONDS; every line ≤ 128 characters; `M5` before every `M6`; integer tool
numbers; `G4 P2` spin-up dwell after `M3`; no arcs — the simulator cannot
verify what it cannot model, so the emitter refuses to emit it.

## Article VI — Previews Tell the Truth

Every preview renders the *simulated stock* — the result of carving the
actual G-code — never the target model. A pretty picture of the intent has
misled us before; the machine executes the file.

## Article VII — No Hands on the Machine

There is no tool that uploads to or controls the physical machine, and none
may be added without an explicit maintainer decision recorded in DESIGN.md.
Verified files reach the Carvera by human hand.

## Article VIII — Scope Discipline

ClauderaCAM is 2.5D heightmap CAM. No undercuts, no rest machining, no
general 3-axis ambitions. Features that require abandoning the heightmap
representation belong in another project.

## Article IX — Physics Tells the Truth About Itself

Geometric checks are facts about the simulation. Physics checks (chip load,
power, heat, gumming) are MODEL verdicts: every limit in physics.py states
its provenance, every proxy says what it does NOT model, and every
threshold is anchored to metal evidence — the flawless jobs bound the
limits from below, the incidents bound them from above. A physics limit
changes only the way Article II changes any threshold: with new
physical-world evidence written into DESIGN.md. Never present a model
verdict as a measured fact.

## Article X — Two Kernels, One Truth

The measurement kernel exists twice: kernel_py.py (pure Python, the
semantic authority) and kernel/ (Rust, the optimization). They must agree
to the bit on carved stock and to epsilon on measurements —
tests/kernel_parity.py enforces it. A change to one without the other is a
broken build, not a divergence to debate.

## Article XI — The Tool Crib

A job may only use tools the shop physically holds: every job tool must
match an entry in the machine's inventory file (jobs/inventory.toml, or
[machine] inventory) on type, diameter, shank and flute count, claim no
more reach than the entry records, and the entry must have quantity on
hand. No inventory file, no job. Inventory geometry is MEASURED or
catalog-sourced — never guessed: this article exists because a config
once carried an invented 14mm drill reach and sent the operator hunting
for a bit that does not exist. When a dimension is unknown, the entry is
absent and the job refuses to load — that refusal is the feature.

## Working rules

- Core dependencies stay at numpy, scipy, pillow, mcp; the Rust kernel uses
  pyo3 + rust-numpy only. The viewer's three.js is vendored; no CDNs, no
  build step for the frontend, no framework.
- Match the existing code style: numpy-first, small modules, docstrings that
  cite the requirement or incident a piece of code serves.
- The MCP server runs on stdio — nothing in the process may print to stdout
  except the protocol. The viewer logs nowhere.
- The PCB lane's binaries are EXTERNAL, optional dependencies like the Rust
  kernel: the pinned FlatCAM fork is a geometry engine, not a post-processor
  — its phase output is strictly parsed and re-emitted through emit.py, so
  Article V holds — and gerbv is the ground-truth rasterizer whose parser
  shares no lineage with the generator's. Configured paths, loud skips when
  absent, never imported by core code.
- Time estimates are estimates; verification results are facts. Report them
  with exactly that confidence split.
