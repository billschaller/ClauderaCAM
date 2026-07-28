import * as THREE from 'three';
import { OrbitControls } from '/static/OrbitControls.js';

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x14161a);

const camera = new THREE.PerspectiveCamera(45, innerWidth / innerHeight, 0.1, 2000);
camera.position.set(0, 55, 55);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
document.body.appendChild(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x2a2015, 0.6));
const key = new THREE.DirectionalLight(0xffffff, 1.6);
key.position.set(-40, 60, 40);
scene.add(key);
const fill = new THREE.DirectionalLight(0xffe0b0, 0.5);
fill.position.set(50, 30, -30);
scene.add(fill);

let mesh = null;
let version = null;

function buildMesh(stock, meta) {
  const { n, ppm, half } = meta;
  const pos = new Float32Array(n * n * 3);
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      const k = (i * n + j) * 3;
      pos[k] = j / ppm - half;          // world x
      pos[k + 1] = stock[i * n + j];    // height
      pos[k + 2] = i / ppm - half;      // -world y (text upright from front)
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
  geo.setIndex(new THREE.BufferAttribute(idx, 1));
  geo.computeVertexNormals();
  const mat = new THREE.MeshStandardMaterial({
    color: 0xc9a227, metalness: 0.75, roughness: 0.38,
    side: THREE.DoubleSide,
  });
  if (mesh) { scene.remove(mesh); mesh.geometry.dispose(); }
  mesh = new THREE.Mesh(geo, mat);
  mesh.scale.y = parseFloat(document.getElementById('zex').value);
  scene.add(mesh);
  document.getElementById('empty').style.display = 'none';
  document.getElementById('panel').style.display = 'block';
}

function renderPanel(meta) {
  document.getElementById('jobname').textContent = meta.job ?? '–';
  const v = document.getElementById('verdict');
  if (meta.stale) {
    v.textContent = 'STALE — toolpaths regenerated; re-run verify';
    v.className = 'stale';
  }
  else if (meta.ok === true) { v.textContent = 'PASS — cleared for metal'; v.className = 'ok'; }
  else if (meta.ok === false) { v.textContent = 'FAIL — do not cut'; v.className = 'fail'; }
  else { v.textContent = ''; }
  const c = document.getElementById('checks');
  c.innerHTML = '';
  for (const ch of meta.checks ?? []) {
    const row = document.createElement('div');
    row.className = 'check';
    row.innerHTML = `<span class="${ch.ok ? 'ok' : 'fail'}">${ch.ok ? '✓' : '✗'} ${ch.name}</span>` +
      `<span class="v">${ch.value.toFixed(3)}mm ${ch.limit}</span>`;
    c.appendChild(row);
  }
}

async function poll() {
  try {
    const meta = await (await fetch('/api/state')).json();
    // commit `version` only AFTER the stock is fetched and built — a failure
    // between the two fetches must be retried, not silently skipped forever
    if (meta.version != null && meta.version !== version && meta.n) {
      const res = await fetch('/api/stock?v=' + encodeURIComponent(meta.version));
      if (res.ok) {
        const buf = await res.arrayBuffer();
        if (buf.byteLength === meta.n * meta.n * 4) {
          buildMesh(new Float32Array(buf), meta);
          renderPanel(meta);
          version = meta.version;
        }
      } // 409 = a newer push landed mid-fetch; next poll picks it up
    }
  } catch (e) { /* server restarting; keep polling */ }
  setTimeout(poll, 1500);
}
poll();

document.getElementById('zex').addEventListener('input', (e) => {
  document.getElementById('zexv').textContent =
    parseFloat(e.target.value).toFixed(1);
  if (mesh) mesh.scale.y = parseFloat(e.target.value);
});

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});

renderer.setAnimationLoop(() => {
  controls.update();
  renderer.render(scene, camera);
});
