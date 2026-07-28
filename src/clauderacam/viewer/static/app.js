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
let version = null;   // committed only after buffers for the view are built
let meta = null;
let sel = 0;          // selected stage index
let buffers = new Map();          // stage -> Float32Array, current version
let lastCur = null, lastPrev = null;   // for recoloring on toggle

const BRASS = [0.79, 0.64, 0.15];
const FRESH = [0.85, 0.87, 0.92];      // fresh-machined: bright, silvery

let mesh = null, geoN = 0;

function ensureGeometry(n, ppm, half) {
  if (mesh && geoN === n) return;
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); }
  const pos = new Float32Array(n * n * 3);
  const col = new Float32Array(n * n * 3);
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const k = (i * n + j) * 3;
      pos[k] = j / ppm - half;       // world x
      pos[k + 2] = i / ppm - half;   // -world y (text upright from front)
    }
  }
  const idx = new Uint32Array((n - 1) * (n - 1) * 6);
  let p = 0;
  for (let i = 0; i < n - 1; i++) {
    for (let j = 0; j < n - 1; j++) {
      const a = i * n + j, b = a + 1, c = a + n, d = c + 1;
      idx[p++] = a; idx[p++] = c; idx[p++] = b;
      idx[p++] = b; idx[p++] = c; idx[p++] = d;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  const mat = new THREE.MeshStandardMaterial({
    color: 0xffffff, metalness: 0.75, roughness: 0.38,
    vertexColors: true, side: THREE.DoubleSide,
  });
  mesh = new THREE.Mesh(geo, mat);
  mesh.scale.y = parseFloat($('zex').value);
  geoN = n;
  scene.add(mesh);
}

function updateMesh(cur, prev, n, ppm, half) {
  ensureGeometry(n, ppm, half);
  lastCur = cur; lastPrev = prev;
  const pos = mesh.geometry.attributes.position.array;
  const col = mesh.geometry.attributes.color.array;
  const hl = $('hl').checked;
  for (let i = 0; i < n * n; i++) {
    const k = i * 3;
    pos[k + 1] = cur[i];
    // fresh = removed by THIS stage (vs previous stage's stock; the first
    // stage compares against the uncut top plane z=0)
    const pv = prev ? prev[i] : 0;
    const c = (hl && pv - cur[i] > 5e-4) ? FRESH : BRASS;
    col[k] = c[0]; col[k + 1] = c[1]; col[k + 2] = c[2];
  }
  mesh.geometry.attributes.position.needsUpdate = true;
  mesh.geometry.attributes.color.needsUpdate = true;
  mesh.geometry.computeVertexNormals();
  $('empty').style.display = 'none';
  const side = $('side');
  if (side.style.display !== 'flex') { side.style.display = 'flex'; resize(); }
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

async function fetchStage(k, v, n) {
  if (buffers.has(k)) return buffers.get(k);
  const res = await fetch(`/api/stock?v=${encodeURIComponent(v)}&stage=${k}`);
  if (!res.ok) throw new Error('version changed');   // 409: next poll retries
  const buf = await res.arrayBuffer();
  if (buf.byteLength !== n * n * 4) throw new Error('bad size');
  const a = new Float32Array(buf);
  buffers.set(k, a);
  return a;
}

// ------------------------------------------------------------ sidebar bits
function renderHeader() {
  $('jobname').textContent = meta.job ?? '–';
  const v = $('verdict');
  if (meta.stale) { v.textContent = 'STALE'; v.className = 'badge stale'; }
  else if (meta.ok === true) { v.textContent = 'PASS'; v.className = 'badge ok'; }
  else if (meta.ok === false) { v.textContent = 'FAIL'; v.className = 'badge fail'; }
  else { v.textContent = ''; v.className = 'badge'; }
  const s = meta.stock_size, t = meta.stock_thickness;
  $('jobfacts').innerHTML =
    `<b>${esc(meta.material ?? '?')}</b> · ${esc(meta.machine ?? '')}<br>` +
    `stock ${s}×${s}×${t} mm · part Ø${meta.model_d} · <b>${esc(meta.nc ?? '')}</b><br>` +
    `${meta.nstages} stages · ~${fmtTime(meta.total_est_s ?? 0)} total ` +
    `<span style="color:var(--dimmer)">(est, + tool changes)</span>` +
    (meta.stale ? `<br><span style="color:var(--warn)">toolpaths were ` +
      `regenerated — re-run verify</span>` : '');
}

function renderStages() {
  const box = $('stages');
  box.innerHTML = '';
  for (const st of meta.stages ?? []) {
    const row = document.createElement('div');
    row.className = 'stage' + (st.index === sel ? ' sel' : '');
    row.innerHTML =
      `<span class="idx">${st.index + 1}</span>` +
      `<span class="slabel">${esc(st.label)}</span>` +
      `<span class="tchip">T${st.tool ?? '?'}</span>` +
      `<span class="stime">${fmtTime(st.est_s)}</span>`;
    row.onclick = () => showStage(st.index).catch(() => {});
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
  const st = meta.stages[sel];
  if (!st) return;
  const t = (meta.tools ?? []).find((x) => x.num === st.tool);
  $('dtool').innerHTML = t
    ? `<span class="tchip">T${t.num}</span> <b>${esc(t.type)} Ø${t.diameter}` +
      `</b> · ${t.flutes} flute${t.flutes > 1 ? 's' : ''} · ` +
      `${t.rpm.toLocaleString()} rpm`
    : '—';
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
  const active = meta.stages[sel]?.tool;
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

function renderAll() {
  renderHeader(); renderStages(); renderDetail(); renderTools();
  renderChecks();
}

// ------------------------------------------------------------ interactions
async function showStage(k) {
  if (!meta || !meta.nstages) return;
  sel = Math.max(0, Math.min(k, meta.nstages - 1));
  const cur = await fetchStage(sel, meta.version, meta.n);
  const prev = sel > 0 ? await fetchStage(sel - 1, meta.version, meta.n)
                       : null;
  updateMesh(cur, prev, meta.n, meta.ppm, meta.half);
  renderStages(); renderDetail(); renderTools();
}

async function poll() {
  try {
    const m = await (await fetch('/api/state')).json();
    // commit `version` only AFTER the buffers for the current view are
    // fetched and built — a failure in between must be retried, not
    // silently skipped forever
    if (m.version != null && m.version !== version && m.n && m.nstages) {
      const want = (meta && meta.nstages === m.nstages)
        ? Math.min(sel, m.nstages - 1) : m.nstages - 1;
      const fresh = new Map();
      const saveBuffers = buffers;
      buffers = fresh;
      try {
        const cur = await fetchStage(want, m.version, m.n);
        const prev = want > 0 ? await fetchStage(want - 1, m.version, m.n)
                              : null;
        meta = m; version = m.version; sel = want;
        updateMesh(cur, prev, m.n, m.ppm, m.half);
        renderAll();
      } catch (e) {
        buffers = saveBuffers;   // keep showing the old state; retry on poll
      }
    }
  } catch (e) { /* server restarting; keep polling */ }
  setTimeout(poll, 1500);
}
poll();

$('zex').addEventListener('input', (e) => {
  $('zexv').textContent = parseFloat(e.target.value).toFixed(1);
  if (mesh) mesh.scale.y = parseFloat(e.target.value);
});
$('hl').addEventListener('change', () => {
  if (meta && lastCur) updateMesh(lastCur, lastPrev, meta.n, meta.ppm, meta.half);
});
$('checksHead').addEventListener('click', () => {
  $('checkList').classList.toggle('open');
});

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});
