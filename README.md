# ClauderaCAM

[![ci](https://github.com/billschaller/ClauderaCAM/actions/workflows/ci.yml/badge.svg)](https://github.com/billschaller/ClauderaCAM/actions/workflows/ci.yml)

Claude-drivable 2.5D heightmap CAM for the Makera Carvera Air.
STL relief → rough / semi / finish / cutout → **physical stock-simulation
verification** → live 3D viewer → .nc. Built from the exact code that cut
the brass Mango coin, then hardened by a 42-agent adversarial review whose
35 confirmed findings are each either fixed or a named check (see DESIGN.md
"2026-07-28: adversarial review & hardening").

## Setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .          # pure-python (works everywhere)
.venv/bin/python -m pip install ./kernel      # optional Rust kernel (~10-50x
                                              # faster verify; needs cargo)
```

The two kernels are parity-locked (`tests/kernel_parity.py`): bit-exact
stock, epsilon-exact measurements. The Python one is the semantic reference.

## MCP (primary interface)

`.mcp.json` in this repo registers the server for Claude Code sessions
started here. From elsewhere:

```sh
claude mcp add --scope user clauderacam -- \
  <repo>/.venv/bin/python -m clauderacam.mcp_server
```

Tools: `load_job`, `generate`, `verify`, `preview`, `view` (starts the
localhost viewer app and loads the simulated stock into it). There is
deliberately no upload tool — .nc files reach the machine only by hand.

The viewer is a stage-aware inspection app: the program's logical
operations (recovered from the .nc bytes themselves) appear as a
selectable list with per-stage time estimates; selecting one shows the
simulated stock as it will exist after that operation, with the material
that stage removes highlighted, plus a stage-detail card (removed volume,
tool contact, windowed chip load and cutting power — each drawn as a
utilization bar against its limit), the tool library with the active
stage's tool marked, and the full check list. Claude runs the job; the
viewer exists so a human — including a CAM expert — can audit it.

## CLI

```sh
.venv/bin/clauderacam all jobs/mango.toml       # generate + verify + preview
.venv/bin/clauderacam view jobs/mango.toml      # live 3D viewer at :8323
```

## The rule

**Nothing goes to the machine without `verify` returning PASS.** The verifier
strictly parses the program (anything it cannot model — unknown tools, arcs,
modal G-less lines — is fatal, never skipped), carves every move into
virtual stock, and checks:

- *geometry (simulation facts):* rapids (lateral and descending), true
  per-move tool contact for every tool (footprint max vs move-start stock,
  limits calibrated to metal evidence), shank/holder clearance against
  standing stock, depth vs stock bottom, gouging below the target surface,
  surface completeness, cutout sever-through, the fixture keep-out zone,
  and the spindle-state dialect (M5 before M6, dwell after M3)
- *cutting physics (model verdicts, provenance-annotated in physics.py):*
  per-material sustained chip load and spindle power over 0.25s windows,
  rubbing (starvation feed), plunge rate, machine feed caps, a sustained-
  power heat proxy, and an enclosed-chip gumming proxy — materials carry
  specific cutting energy, chip-load floors and evacuation limits; tools
  carry flutes, flute length and shank geometry; the machine model is the
  Carvera Air's 200W / 13k RPM spindle

`tests/negative_suite.py` proves the gate can fail: 24 distilled hazards —
slams, stalls, rubbing, gumming, shank crashes, rapid plunges, arcs in
disguise — that must each be caught. `tests/stage_model.py` proves the
per-stage model tells the truth: stage snapshots partition the carve
bit-exactly and per-stage stats are measured, not inferred.

## Golden test

```sh
.venv/bin/python tests/golden_mango.py
```

Regenerates the mango job (`assets/mango-coin-d52.stl` — yes, that's a real
cat) and asserts byte-identical toolpaths against the G-code in
`tests/golden/` that cut the physical coin, then verifies the assembled
program. Run it after touching anything in `ops/`, `engine.py`, or
`heightmap.py`.

## Reference suite

```sh
.venv/bin/python tests/reference_suite.py
```

`assets/generate_references.py` synthesizes four deterministic shapes, each
paired with a job in `jobs/` that stresses a different hazard:

| job | exercises |
|---|---|
| `terraces` | layered-rough terracing on 0.22mm steps, simplify on flats, cutout with tabs |
| `dome` | 2mm flat rough (smaller tool = relaxed slope budget), two-ball finish chain, no cutout |
| `ripple` | 1mm ball finishing **directly** after rough — legal only because every slope is inside the engagement budget |
| `pocketfield` | graduated feature access by tool BODY (Ø7 down to 1.3mm slots) — honestly annotated: the shallow features are bottomed by the 2mm ball's contact circle; true tool exclusion awaits depth-stepped clearing |

Together with mango these cover the supported tool set: flat 3.175, flat 2.0,
ball 2.0, ball 1.0 — the geometries validated by cut metal. V-bits and
tapered ball noses need a conical offset model plus a real-world tip
engagement limit, so they are roadmap, not shipped guesses.

## Project law

`CLAUDE.md` is the constitution: the verification gate, the golden-test
contract, and the incident-traced safety rules. Read it before contributing —
human or model.

## License & credits

MIT (see LICENSE). Vendors [three.js](https://threejs.org) r160 (MIT) for the
viewer. Named for Claude + Carvera: designed and written by
[Claude Code](https://claude.com/claude-code) working alongside a human
machinist, and proven by cutting real metal before it was ever a package.
