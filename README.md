# ClauderaCAM

Claude-drivable 2.5D heightmap CAM for the Makera Carvera Air.
STL relief → rough / semi / finish / cutout → **physical stock-simulation
verification** → live 3D viewer → .nc. Built from the exact code that cut
the brass Mango coin. See DESIGN.md for the incident-traced requirements.

## Setup

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
```

## MCP (primary interface)

`.mcp.json` in this repo registers the server for Claude Code sessions
started here. From elsewhere:

```sh
claude mcp add --scope user clauderacam -- \
  /home/dad/scratch/carvera/clauderacam/.venv/bin/python -m clauderacam.mcp_server
```

Tools: `load_job`, `generate`, `verify`, `preview`, `view` (starts the
localhost viewer app and loads the simulated stock into it). There is
deliberately no upload tool — .nc files reach the machine only by hand.

## CLI

```sh
.venv/bin/clauderacam all jobs/mango.toml       # generate + verify + preview
.venv/bin/clauderacam view jobs/mango.toml      # live 3D viewer at :8323
```

## The rule

**Nothing goes to the machine without `verify` returning PASS.** The verifier
carves every move into virtual stock and checks rapids, ball engagement,
surface completeness, and the fixture keep-out zone.

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
| `pocketfield` | graduated feature access: Ø7 pocket (rough enters) down to 1.3mm slots (1mm ball only), depths sized to the engagement limit |

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
