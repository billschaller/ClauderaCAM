import * as THREE from 'three';
import { OrbitControls } from '/static/OrbitControls.js';

// ---------------------------------------------------------------- three.js
const view = document.getElementById('view');
const $ = (id) => document.getElementById(id);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14161a);

const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);
camera.position.set(0, 55, 55);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
view.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x2a2015, 0.6));
const key = new THREE.DirectionalLight(0xffffff, 1.6);
key.position.set(-40, 60, 40);
scene.add(key);
const fill = new THREE.DirectionalLight(0xffe0b0, 0.5);
fill.position.set(50, 30, -30);
scene.add(fill);

function resize() {
  const w = view.clientWidth || 1, h = view.clientHeight || 1;
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  renderer.setSize(w, h);
}
addEventListener('resize', resize);
resize();

// ------------------------------------------------------------------- state
let sessions = [];             // last /api/sessions payload
let selSid = location.hash.slice(1) || null;
let meta = null;               // committed state of the selected session
let committed = { sid: null, version: null };
let selStage = 0;
let buffers = new Map();       // stage -> Float32Array for committed version
let lastCur = null, lastPrev = null;
let refreshing = false;        // one state/buffer refresh at a time

const BRASS = [0.79, 0.64, 0.15];
const FRESH = [0.85, 0.87, 0.92];      // fresh-machined: bright, silvery
const COPPER = [0.72, 0.45, 0.20];     // copper-clad sheet, as fixtured
const SUBSTRATE = [0.85, 0.81, 0.60];  // FR-4 glass/epoxy under the copper

let mesh = null, geoKey = '';

// The carve grid a session serves. A mill job serves the whole n×n grid; a
// [pcb] session serves the CROP of it that is the modelled sheet (nx/ny at
// pixel offsets i_off/j_off) — the pixel indices stay in simulate.py's ONE
// mapping, so nothing here re-derives a grid centre (Article IV).
function gridOf(m) {
  return { nx: m.nx ?? m.n, ny: m.ny ?? m.n, ppm: m.ppm, half: m.half,
           ioff: m.i_off ?? 0, joff: m.j_off ?? 0, pcb: m.kind === 'pcb' };
}

function ensureGeometry(g) {
  const key = `${g.nx}x${g.ny}@${g.ppm}/${g.half}+${g.ioff},${g.joff}`;
  if (mesh && geoKey === key) return;
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); }
  const n = g.nx * g.ny;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  for (let i = 0; i < g.ny; i++) {
    for (let j = 0; j < g.nx; j++) {
      const k = (i * g.nx + j) * 3;
      pos[k] = (j + g.joff) / g.ppm - g.half;       // world x
      pos[k + 2] = (i + g.ioff) / g.ppm - g.half;   // -world y (upright)
    }
  }
  const idx = new Uint32Array((g.ny - 1) * (g.nx - 1) * 6);
  let p = 0;
  for (let i = 0; i < g.ny - 1; i++) {
    for (let j = 0; j < g.nx - 1; j++) {
      const a = i * g.nx + j, b = a + 1, c = a + g.nx, d = c + 1;
      idx[p++] = a; idx[p++] = c; idx[p++] = b;
      idx[p++] = b; idx[p++] = c; idx[p++] = d;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, metalness: g.pcb ? 0.45 : 0.75,
    roughness: g.pcb ? 0.55 : 0.38,
    vertexColors: true, side: THREE.DoubleSide,
  });
  mesh = new THREE.Mesh(geo, mat);
  mesh.scale.y = parseFloat($('zex').value);
  geoKey = key;
  scene.add(mesh);
}

function updateMesh(cur, prev, m) {
  const g = gridOf(m);
  ensureGeometry(g);
  lastCur = cur; lastPrev = prev;
  const pos = mesh.geometry.attributes.position.array;
  const col = mesh.geometry.attributes.color.array;
  const hl = $('hl').checked;
  const base = g.pcb ? COPPER : BRASS;
  const cut = g.pcb ? SUBSTRATE : FRESH;
  for (let i = 0; i < g.nx * g.ny; i++) {
    const k = i * 3;
    pos[k + 1] = cur[i];
    // fresh = removed by THIS stage (vs previous stage's stock; the first
    // stage compares against the uncut top plane z=0)
    const pv = prev ? prev[i] : 0;
    const c = (hl && pv - cur[i] > 5e-4) ? cut : base;
    col[k] = c[0]; col[k + 1] = c[1]; col[k + 2] = c[2];
  }
  mesh.geometry.attributes.position.needsUpdate = true;
  mesh.geometry.attributes.color.needsUpdate = true;
  mesh.geometry.computeVertexNormals();
  mesh.visible = true;
  $('empty').style.display = 'none';
}

// ------------------------------------------------- the 2D overlay (WS6)
// Silk and scrub carve nothing, so there is no stock to render: what the
// program actually DRAWS is the truthful preview (Article VI). Lines live
// just above the sheet top plane; the gerber reference layers are labelled
// as gerber and default to off.
let ovGroup = null;
const ovOn = new Map();       // layer key -> user's show/hide choice

function disposeGroup(gr) {
  if (!gr) return;
  scene.remove(gr);
  gr.traverse((o) => { if (o.geometry) o.geometry.dispose();
                       if (o.material) o.material.dispose(); });
}

function lineObj(polylines, color, y, opacity) {
  let count = 0;
  for (const pl of polylines) count += Math.max(0, pl.length - 1);
  const pos = new Float32Array(count * 6);
  let p = 0;
  for (const pl of polylines) {
    for (let i = 0; i + 1 < pl.length; i++) {
      pos[p++] = pl[i][0];     pos[p++] = y; pos[p++] = -pl[i][1];
      pos[p++] = pl[i + 1][0]; pos[p++] = y; pos[p++] = -pl[i + 1][1];
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const mat = new THREE.LineBasicMaterial({
    color: new THREE.Color(color), transparent: opacity < 1,
    opacity: opacity, depthWrite: false });
  return new THREE.LineSegments(geo, mat);
}

function rectPoly(r) {
  return [[[r[0], r[1]], [r[2], r[1]], [r[2], r[3]], [r[0], r[3]],
           [r[0], r[1]]]];
}

function renderOverlay(m) {
  disposeGroup(ovGroup);
  ovGroup = null;
  if (m.kind !== 'pcb') return;
  ovGroup = new THREE.Group();
  const s = m.sheet;
  if (s) {
    ovGroup.add(lineObj(rectPoly(s.board), '#7aa2ff', 0.03, 0.9));
    ovGroup.add(lineObj(rectPoly([s.x0, s.y0, s.x1, s.y1]), '#4a5060',
                        0.03, 0.8));
  }
  for (const L of (m.overlay?.layers ?? [])) {
    const polys = L.kind === 'polys' ? L.polys : L.polylines;
    const o = lineObj(polys ?? [], L.color, L.kind === 'polys' ? 0.05 : 0.08,
                      L.kind === 'polys' ? 0.8 : 1.0);
    o.visible = ovOn.has(L.key) ? ovOn.get(L.key) : !!L.on;
    o.userData.key = L.key;
    ovGroup.add(o);
  }
  scene.add(ovGroup);
}

function setLayerVisible(key, on) {
  ovOn.set(key, on);
  ovGroup?.traverse((o) => { if (o.userData?.key === key) o.visible = on; });
}

function frameView(m) {
  const s = m.sheet;
  const cx = s ? (s.x0 + s.x1) / 2 : 0;
  const cy = s ? (s.y0 + s.y1) / 2 : 0;
  const span = s ? Math.max(s.x1 - s.x0, s.y1 - s.y0) : 2 * (m.half ?? 30);
  controls.target.set(cx, 0, -cy);
  camera.position.set(cx, span * 0.85, -cy + span * 0.85);
  controls.update();
}

function topView() {
  const t = controls.target;
  const d = camera.position.distanceTo(t);
  camera.position.set(t.x, t.y + d, t.z + 1e-3);
  controls.update();
}

// --------------------------------------------------------------- utilities
function esc(s) {
  return String(s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;',
              '"': '&quot;', "'": '&#39;' }[c]));
}
function fmtTime(s) {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(r).padStart(2, '0')}`
           : `${m}:${String(r).padStart(2, '0')}`;
}
function fmtLen(mm) {
  return mm >= 1000 ? (mm / 1000).toFixed(1) + ' m' : mm.toFixed(0) + ' mm';
}
function fmtVol(v) {
  return v >= 1000 ? (v / 1000).toFixed(2) + ' cm³' : v.toFixed(1) + ' mm³';
}
function showSide() {
  const side = $('side');
  if (side.style.display !== 'flex') { side.style.display = 'flex'; resize(); }
}

async function fetchStage(sid, k, v, npx) {
  if (buffers.has(k)) return buffers.get(k);
  const res = await fetch(`/api/session/${encodeURIComponent(sid)}/stock` +
    `?v=${encodeURIComponent(v)}&stage=${k}`);
  if (!res.ok) throw new Error('version changed');   // 409: next poll retries
  const buf = await res.arrayBuffer();
  if (buf.byteLength !== npx * 4) throw new Error('bad size');
  const a = new Float32Array(buf);
  buffers.set(k, a);
  return a;
}

function stagePixels(m) {
  const g = gridOf(m);
  return g.nx * g.ny;
}

// ------------------------------------------------------------ sidebar bits
function verdictOf(s) {
  if (s.status === 'loading') return ['loading', 'verifying…'];
  if (s.status === 'error') return ['error', 'ERROR'];
  if (s.stale) return ['stale', 'STALE'];
  if (s.ok === true) return ['ok', 'PASS'];
  if (s.ok === false) return ['fail', 'FAIL'];
  return ['loading', '…'];
}

function renderSessions() {
  const box = $('sessList');
  box.innerHTML = '';
  for (const s of sessions) {
    const [cls, txt] = verdictOf(s);
    const row = document.createElement('div');
    row.className = 'sess' + (s.sid === selSid ? ' sel' : '');
    row.innerHTML =
      `<span class="dot ${cls}"></span>` +
      `<span class="slbl">${esc(s.job || s.label)}</span>` +
      `<span class="sst ${cls === 'ok' ? 'ok-t' : cls === 'fail' || cls === 'error' ? 'bad-t' : ''}">${txt}</span>` +
      `<span class="x" title="close session">×</span>`;
    row.onclick = () => { selectSession(s.sid); };
    row.querySelector('.x').onclick = async (e) => {
      e.stopPropagation();
      await fetch('/api/close', { method: 'POST',
        body: JSON.stringify({ sid: s.sid }) });
      tick();
    };
    box.appendChild(row);
    if (s.status === 'error' && s.error) {
      const err = document.createElement('div');
      err.className = 'serr';
      err.textContent = s.error;
      box.appendChild(err);
    }
  }
  if (!sessions.length) {
    box.innerHTML = '<div id="status">no sessions — open a job file below,' +
      ' or push one with the view tool / CLI</div>';
  }
}

function renderHeader() {
  $('jobname').textContent = meta.job ?? '–';
  const v = $('verdict');
  if (meta.stale) { v.textContent = 'STALE'; v.className = 'badge stale'; }
  else if (meta.ok === true) { v.textContent = 'PASS'; v.className = 'badge ok'; }
  else if (meta.ok === false) { v.textContent = 'FAIL'; v.className = 'badge fail'; }
  else if (meta.kind === 'pcb') {
    v.textContent = 'UNVERIFIED'; v.className = 'badge stale';
  } else { v.textContent = ''; v.className = 'badge'; }
  const s = meta.stock_size, t = meta.stock_thickness;
  const nst = (meta.stages ?? []).length;
  if (meta.kind === 'pcb') {
    const b = meta.sheet.board;
    const g = meta.gate ?? {};
    $('jobfacts').innerHTML =
      `<b>${esc(meta.material ?? '?')}</b> · ${esc(meta.machine ?? '')} · ` +
      `program <b>${esc(meta.program)}</b> (${(meta.phases ?? []).join(' + ')})<br>` +
      `board ${(b[2] - b[0]).toFixed(1)}×${(b[3] - b[1]).toFixed(1)} mm at ` +
      `machine ${b[0].toFixed(1)},${b[1].toFixed(1)} · sheet ${t} mm thick, ` +
      `window ${meta.sheet.x0.toFixed(1)}…${meta.sheet.x1.toFixed(1)} × ` +
      `${meta.sheet.y0.toFixed(1)}…${meta.sheet.y1.toFixed(1)}<br>` +
      `<b>${esc(meta.nc ?? '')}</b> · ${nst} stage${nst === 1 ? '' : 's'} · ` +
      `~${fmtTime(meta.total_est_s ?? 0)} ` +
      `<span style="color:var(--dimmer)">(est) of ~` +
      `${fmtTime(meta.chain_est_s ?? 0)} for the chain</span><br>` +
      `<span style="color:var(--dimmer)">` +
      (meta.carves
        ? `sheet sim ${(meta.sim.mm_per_px).toFixed(3)} mm/px, virgin sheet ` +
          `per program (upper-bound engagement)`
        : `no stock simulation — this program removes no material`) +
      `</span><br>` +
      (g.ran
        ? `<span class="${meta.ok ? 'ok-t' : 'bad-t'}">PCB gate ` +
          `${esc(g.verdict)}</span> over ${(meta.checks ?? []).length} ` +
          `checks` + (g.sheet_sim ? ' incl. the sheet sim' : '')
        : `<span style="color:var(--warn)">PCB gate NOT RUN — ` +
          `${esc(g.note || 'no verdict')}</span>`) +
      (meta.stale ? `<br><span style="color:var(--warn)">programs were ` +
        `regenerated — re-run the gate</span>` : '');
  } else {
    $('jobfacts').innerHTML =
      `<b>${esc(meta.material ?? '?')}</b> · ${esc(meta.machine ?? '')}<br>` +
      `stock ${s}×${s}×${t} mm · part Ø${meta.model_d} · <b>${esc(meta.nc ?? '')}</b><br>` +
      `${meta.nstages} stages · ~${fmtTime(meta.total_est_s ?? 0)} total ` +
      `<span style="color:var(--dimmer)">(est, + tool changes)</span>` +
      (meta.stale ? `<br><span style="color:var(--warn)">toolpaths were ` +
        `regenerated — re-run verify</span>` : '');
  }
  const dl = $('dl');
  if (meta.has_program && meta.sid) {
    dl.style.display = 'inline-block';
    dl.href = `/api/session/${encodeURIComponent(meta.sid)}/program`;
    const clear = meta.ok === true && !meta.stale;
    dl.className = clear ? '' : 'warn';
    dl.textContent = clear
      ? `⬇ download ${meta.nc ?? 'program'} (verified PASS)`
      : `⬇ download ${meta.nc ?? 'program'} — NOT cleared for metal`;
    // these are the bytes the verdict judged, not the live file; still,
    // never let a FAIL/STALE program leave without an explicit yes
    dl.onclick = clear ? null : (e) => {
      if (!confirm('This program did NOT pass verification (or is ' +
                   'stale). Download anyway?')) e.preventDefault();
    };
  } else {
    dl.style.display = 'none';
  }
}

function renderStages() {
  const box = $('stages');
  box.innerHTML = '';
  for (const st of meta.stages ?? []) {
    const row = document.createElement('div');
    row.className = 'stage' + (st.index === selStage ? ' sel' : '');
    row.innerHTML =
      `<span class="idx">${st.index + 1}</span>` +
      `<span class="slabel">${esc(st.label)}</span>` +
      `<span class="tchip">${st.overlay ? '2D' : 'T' + (st.tool ?? '?')}` +
      `</span>` +
      `<span class="stime">${fmtTime(st.est_s)}</span>`;
    row.onclick = () => showStage(st.index).catch(() => {});
    box.appendChild(row);
  }
}

function renderOverlayCard() {
  const box = $('ovLayers');
  const layers = meta.overlay?.layers ?? [];
  box.innerHTML = '';
  for (const L of layers) {
    const on = ovOn.has(L.key) ? ovOn.get(L.key) : !!L.on;
    const row = document.createElement('div');
    row.className = 'ovrow';
    row.innerHTML =
      `<input type="checkbox" ${on ? 'checked' : ''}>` +
      `<span class="swatch" style="background:${esc(L.color)}"></span>` +
      `<span class="ovl">${esc(L.label)}</span>`;
    row.querySelector('input').onchange = (e) =>
      setLayerVisible(L.key, e.target.checked);
    box.appendChild(row);
    const note = document.createElement('div');
    note.className = 'ovnote';
    note.textContent = L.note ?? '';
    box.appendChild(note);
  }
  $('ovNotes').innerHTML = (meta.overlay?.notes ?? [])
    .map((n) => `<div class="note">${esc(n)}</div>`).join('');
}

function renderRunSheet() {
  const box = $('runsheet');
  box.innerHTML = '';
  for (const st of meta.run_sheet ?? []) {
    const here = st.kind === 'program' && st.program === meta.program;
    const row = document.createElement('div');
    row.className = 'rstep' + (here ? ' here' : '');
    const est = st.est_s ? ` · ~${fmtTime(st.est_s)}` : '';
    const file = st.file ? ` · ${esc(st.file)}` : '';
    row.innerHTML =
      `<span class="rn">${st.n}</span>` +
      `<span class="rkind ${esc(st.kind)}">` +
      `${st.kind === 'offmachine' ? 'bench' : esc(st.kind)}</span>` +
      `<span class="rbody"><span class="rt">${esc(st.title)}</span>` +
      `<span class="rd">${esc(st.detail)}${file}${est}` +
      (st.missing ? ' · <b>program not on disk</b>' : '') +
      `</span></span>`;
    if (st.kind === 'program' && !st.missing && !here) {
      row.style.cursor = 'pointer';
      row.onclick = () => {
        const s = sessions.find((x) => x.job === `${meta.board} ${st.program}`);
        if (s) selectSession(s.sid);
      };
    }
    box.appendChild(row);
  }
}

function bar(label, val, limit, unit, digits) {
  const r = limit > 0 ? val / limit : 0;
  const cls = r < 0.7 ? 'ok' : r < 1 ? 'warn' : 'bad';
  const w = Math.min(100, Math.max(1.5, r * 100));
  return `<div class="metric"><span class="mk">${label}</span>` +
    `<span class="mv">${val.toFixed(digits)} / ${limit.toFixed(digits)} ` +
    `${unit}</span></div>` +
    `<div class="bar"><div class="fill ${cls}" style="width:${w}%"></div></div>`;
}

function renderDetail() {
  const st = meta.stages[selStage];
  if (!st) return;
  const t = (meta.tools ?? []).find((x) => x.num === st.tool);
  $('dtool').innerHTML = t
    ? `<span class="tchip">T${t.num}</span> <b>${esc(t.type)} Ø${t.diameter}` +
      `</b> · ${t.flutes} flute${t.flutes > 1 ? 's' : ''} · ` +
      `${t.rpm.toLocaleString()} rpm`
    : `<b>${esc(st.tool_desc ?? '—')}</b>`;
  if (st.overlay) {
    // an overlay stage has no carve facts because there is no carve: show
    // only what the bytes support, and say why the rest is missing
    const rows = [
      ['est. machining time', '~' + fmtTime(st.est_s)],
      [st.dose_s !== undefined ? 'firing strokes' : 'scrub laps',
       st.moves.toLocaleString()],
      [st.dose_s !== undefined ? 'firing travel' : 'path at depth',
       fmtLen(st.cut_mm)],
      ['feed', st.max_feed.toFixed(0) + ' mm/min'],
      [st.dose_s !== undefined ? 'dose' : 'commanded Z (spring preload)',
       st.dose_s !== undefined ? 'S' + st.dose_s : st.min_z.toFixed(3) + ' mm'],
      ['material removed', 'none — see the note'],
    ];
    $('dgrid').innerHTML = rows.map(([k, v]) =>
      `<span class="k">${k}</span><span class="v">${v}</span>`).join('');
    $('dbars').innerHTML = `<div class="note">${esc(st.note ?? '')}</div>`;
    return;
  }
  const rows = [
    ['est. machining time', '~' + fmtTime(st.est_s)],
    ['moves', st.moves.toLocaleString()],
    ['cutting path', fmtLen(st.cut_mm)],
    ['rapid path', fmtLen(st.rapid_mm)],
    ['material removed', fmtVol(st.volume_mm3)],
    ['deepest cut', st.min_z.toFixed(3) + ' mm'],
    ['max feed', st.max_feed.toFixed(0) + ' mm/min'],
    ['max radial engagement', (st.max_efrac * 100).toFixed(0) + '% of footprint'],
  ];
  $('dgrid').innerHTML = rows.map(([k, v]) =>
    `<span class="k">${k}</span><span class="v">${v}</span>`).join('');
  $('dbars').innerHTML =
    bar('tool contact (bite)', st.max_contact, st.contact_limit, 'mm', 2) +
    bar('chip load (0.25s window)', st.peak_chip_mm3, st.chip_limit_mm3,
        'mm³/tooth', 3) +
    bar('cutting power (0.25s window)', st.peak_power_w, st.power_limit_w,
        'W', 0);
}

function renderTools() {
  const box = $('tools');
  box.innerHTML = '';
  const active = meta.stages[selStage]?.tool;
  for (const t of meta.tools ?? []) {
    const usedIn = (t.stages ?? [])
      .map((i) => meta.stages[i]?.label ?? i).join(', ') || 'unused';
    const row = document.createElement('div');
    row.className = 'tool' + (t.num === active ? ' active' : '');
    row.innerHTML =
      `<span class="tchip">T${t.num}</span>` +
      `<div class="tinfo">${esc(t.type)} Ø${t.diameter} · ${t.flutes}F · ` +
      `${t.rpm.toLocaleString()} rpm` +
      `<div class="tmeta">flute ${t.flute_length} mm · shank Ø${t.shank} · ` +
      `used in: ${esc(usedIn)}</div>` +
      `<div class="tmeta">max bite ${t.contact.toFixed(2)} / ` +
      `${t.contact_limit.toFixed(2)} mm</div></div>`;
    box.appendChild(row);
  }
}

function renderChecks() {
  const checks = meta.checks ?? [];
  const okN = checks.filter((c) => c.ok).length;
  $('checksVerdict').innerHTML = okN === checks.length
    ? `<span class="ok-t">✓ ${okN}/${checks.length} checks pass</span>`
    : `<span class="bad-t">✗ ${checks.length - okN} of ${checks.length} ` +
      `checks failing</span>`;
  const list = $('checkList');
  list.innerHTML = '';
  for (const ch of checks) {
    const row = document.createElement('div');
    row.className = 'check';
    row.innerHTML =
      `<span class="${ch.ok ? 'ok-t' : 'bad-t'}">${ch.ok ? '✓' : '✗'} ` +
      `${esc(ch.name)}</span>` +
      `<span class="cv">${ch.value.toFixed(3)} ${esc(ch.limit)}</span>`;
    list.appendChild(row);
  }
}

function setCardsVisible(on) {
  for (const id of ['jobCard', 'stagesCard', 'detailCard', 'toolsCard',
                    'checksCard']) {
    $(id).style.display = on ? '' : 'none';
  }
  const pcb = on && meta && meta.kind === 'pcb';
  const stock = on && (meta?.nstages ?? 0) > 0;
  $('runCard').style.display = pcb ? '' : 'none';
  $('overlayCard').style.display =
    pcb && (meta.overlay?.layers ?? []).length ? '' : 'none';
  $('toolsCard').style.display = on && (meta?.tools ?? []).length
    ? '' : 'none';
  if (mesh) mesh.visible = stock;
  if (ovGroup) ovGroup.visible = on;
  // an overlay-only [pcb] program has no stock and still has something true
  // to show; only a session with neither falls back to the empty message
  $('empty').style.display = (stock || pcb) ? 'none' : 'flex';
}

function renderAll() {
  renderHeader(); renderStages(); renderDetail(); renderTools();
  renderChecks(); renderOverlayCard(); renderRunSheet();
  setCardsVisible(true);
}

// ------------------------------------------------------------ interactions
function selectSession(sid) {
  selSid = sid;
  location.hash = sid;
  renderSessions();
  tick();
}

async function showStage(k) {
  if (!meta) return;
  const nst = (meta.stages ?? []).length;
  selStage = Math.max(0, Math.min(k, Math.max(0, nst - 1)));
  if (meta.nstages > 0) {
    const npx = stagePixels(meta);
    const cur = await fetchStage(meta.sid, selStage, meta.version, npx);
    const prev = selStage > 0
      ? await fetchStage(meta.sid, selStage - 1, meta.version, npx) : null;
    updateMesh(cur, prev, meta);
  }
  renderStages(); renderDetail(); renderTools();
}

async function refreshSelected(s) {
  // pull the selected session's committed view up to (s.sid, s.version)
  if (refreshing) return;
  refreshing = true;
  try {
    const res = await fetch(
      `/api/session/${encodeURIComponent(s.sid)}/state`);
    if (!res.ok) return;
    const m = await res.json();
    // an overlay-only [pcb] program serves no stock and is still a session
    if (m.status !== 'ready' || (!m.nstages && m.kind !== 'pcb')) return;
    const newSid = committed.sid !== m.sid;
    const want = (meta && committed.sid === s.sid &&
                  meta.nstages === m.nstages)
      ? Math.min(selStage, Math.max(0, m.nstages - 1))
      : Math.max(0, m.nstages - 1);
    const saved = buffers;
    buffers = new Map();
    try {
      let cur = null, prev = null;
      if (m.nstages > 0) {
        const npx = stagePixels(m);
        cur = await fetchStage(m.sid, want, m.version, npx);
        prev = want > 0
          ? await fetchStage(m.sid, want - 1, m.version, npx) : null;
      }
      meta = m; selStage = want;
      committed = { sid: m.sid, version: m.version };
      if (cur) updateMesh(cur, prev, m);
      renderOverlay(m);
      renderAll();
      if (newSid) frameView(m);
    } catch (e) {
      buffers = saved;   // version raced; the next poll retries
    }
  } finally { refreshing = false; }
}

async function tick() {
  try {
    const data = await (await fetch('/api/sessions')).json();
    sessions = data.sessions ?? [];
    if (sessions.length) showSide();
    if (!sessions.find((s) => s.sid === selSid)) {
      // pick the most recently updated session (list is sorted ascending)
      selSid = sessions.length ? sessions[sessions.length - 1].sid : null;
      if (selSid) location.hash = selSid;
    }
    renderSessions();
    const s = sessions.find((x) => x.sid === selSid);
    if (!s) {
      setCardsVisible(false);
      $('empty').textContent = 'no sessions — open a job file, or run ' +
        'the view tool / `clauderacam view <job>`';
    } else if (s.status === 'ready' &&
               (committed.sid !== s.sid || committed.version !== s.version)) {
      await refreshSelected(s);
    } else if (s.status !== 'ready' && committed.sid !== s.sid) {
      setCardsVisible(false);
      $('empty').textContent = s.status === 'loading'
        ? `verifying ${s.label}…` : `${s.label}: ${s.error || 'error'}`;
    }
  } catch (e) { /* server restarting; keep polling */ }
}

async function loadFiles() {
  try {
    const data = await (await fetch('/api/files')).json();
    const sel = $('fileSel');
    sel.innerHTML = '';
    for (const f of data.files ?? []) {
      const o = document.createElement('option');
      o.value = f; o.textContent = f;
      sel.appendChild(o);
    }
    if (data.files?.length) showSide();
  } catch (e) { /* ignore */ }
}

$('openBtn').addEventListener('click', async () => {
  const f = $('fileSel').value;
  if (!f) return;
  const res = await fetch('/api/open', { method: 'POST',
    body: JSON.stringify({ path: f }) });
  if (res.ok) {
    const { sid } = await res.json();
    selectSession(sid);
  }
});
addEventListener('hashchange', () => {
  const sid = location.hash.slice(1);
  if (sid && sid !== selSid) { selSid = sid; tick(); }
});
$('zex').addEventListener('input', (e) => {
  $('zexv').textContent = parseFloat(e.target.value).toFixed(1);
  if (mesh) mesh.scale.y = parseFloat(e.target.value);
});
$('hl').addEventListener('change', () => {
  if (meta && lastCur) updateMesh(lastCur, lastPrev, meta);
});
$('topBtn').addEventListener('click', topView);
$('fitBtn').addEventListener('click', () => { if (meta) frameView(meta); });
$('checksHead').addEventListener('click', () => {
  $('checkList').classList.toggle('open');
});

loadFiles();
tick();
setInterval(tick, 1000);
setInterval(loadFiles, 10000);

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});
