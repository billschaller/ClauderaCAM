//! Rust measurement kernel — an optimization of the Python reference in
//! src/clauderacam/kernel_py.py, which is the SEMANTIC AUTHORITY. Every
//! behavior here mirrors it deliberately, including Python's
//! round-half-even pixel mapping and numpy's inclusive linspace sampling.
//! tests/kernel_parity.py fails if the two implementations drift.

use numpy::ndarray;
use numpy::{IntoPyArray, PyArray1, PyReadonlyArray1};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

const SAFE_Z: f64 = 3.0;

struct Kit {
    r_px: i64,
    w: usize,             // 2*r_px + 1
    foot: Vec<bool>,
    drop: Vec<f32>,
}

fn make_kit(dia: f64, ball: bool, ppm: f64) -> Kit {
    let r_px = (dia / 2.0 * ppm).ceil() as i64;
    let w = (2 * r_px + 1) as usize;
    let mut foot = vec![false; w * w];
    let mut drop = vec![0f32; w * w];
    let radius = dia / 2.0;
    for dy in -r_px..=r_px {
        for dx in -r_px..=r_px {
            let rr = ((dx * dx + dy * dy) as f64).sqrt() / ppm;
            let idx = ((dy + r_px) as usize) * w + (dx + r_px) as usize;
            if rr <= radius + 1e-9 {
                foot[idx] = true;
                if ball {
                    drop[idx] =
                        (radius - (radius * radius - rr * rr).max(0.0).sqrt())
                            as f32;
                }
            }
        }
    }
    Kit { r_px, w, foot, drop }
}

#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (n, ppm, half, step, check, dia, ball, flute_len, shank_d,
                    motion, x0, y0, z0, x1, y1, z1, tool_idx,
                    snap_after=None))]
fn measure<'py>(
    py: Python<'py>,
    n: usize,
    ppm: f64,
    half: f64,
    step: f64,
    check: bool,
    dia: PyReadonlyArray1<f64>,
    ball: PyReadonlyArray1<u8>,
    flute_len: PyReadonlyArray1<f64>,
    shank_d: PyReadonlyArray1<f64>,
    motion: PyReadonlyArray1<u8>,
    x0: PyReadonlyArray1<f64>,
    y0: PyReadonlyArray1<f64>,
    z0: PyReadonlyArray1<f64>,
    x1: PyReadonlyArray1<f64>,
    y1: PyReadonlyArray1<f64>,
    z1: PyReadonlyArray1<f64>,
    tool_idx: PyReadonlyArray1<u16>,
    snap_after: Option<PyReadonlyArray1<i64>>,
) -> PyResult<Bound<'py, PyDict>> {
    let dia = dia.as_slice()?;
    let ball = ball.as_slice()?;
    let flute_len = flute_len.as_slice()?;
    let shank_d = shank_d.as_slice()?;
    let motion = motion.as_slice()?;
    let x0 = x0.as_slice()?;
    let y0 = y0.as_slice()?;
    let z0 = z0.as_slice()?;
    let x1 = x1.as_slice()?;
    let y1 = y1.as_slice()?;
    let z1 = z1.as_slice()?;
    let tool_idx = tool_idx.as_slice()?;
    let empty_snaps: [i64; 0] = [];
    let snap_after_s: &[i64] = match &snap_after {
        Some(a) => a.as_slice()?,
        None => &empty_snaps,
    };

    let ntools = dia.len();
    let kits: Vec<Kit> = (0..ntools)
        .map(|t| make_kit(dia[t], ball[t] != 0, ppm))
        .collect();
    let skits: Vec<Kit> = (0..ntools)
        .map(|t| make_kit(shank_d[t], false, ppm))
        .collect();
    let px_area = (1.0 / ppm) * (1.0 / ppm);

    let mut stock = vec![0f32; n * n];
    let nm = motion.len();
    let mut volume = vec![0f64; nm];
    let mut contact = vec![0f64; nm];
    let mut contact_x = vec![0f64; nm];
    let mut contact_y = vec![0f64; nm];
    let mut contact_samples = vec![0u32; nm];
    let mut efrac = vec![0f64; nm];
    let mut shank_over = vec![0f64; nm];
    let mut worst_rapid = 0f64;
    let (mut rapid_x, mut rapid_y, mut rapid_z) = (0f64, 0f64, 0f64);
    let mut min_cut_z = 0f64;
    let mut snap: Vec<f32> = Vec::new();
    let mut snapshots: Vec<Vec<f32>> = Vec::new();
    let mut sp = 0usize;

    let ni = n as i64;
    for k in 0..nm {
        // 'mv breaks skip the move body but still reach the snapshot point
        // below — snap_after must fire even for skipped vertical lifts
        'mv: {
        let t = tool_idx[k] as usize;
        let kit = &kits[t];
        let rapid = motion[k] == 0;
        let lateral = x1[k] != x0[k] || y1[k] != y0[k];
        let dz = z1[k] - z0[k];
        if rapid && !lateral && dz >= -1e-9 {
            break 'mv; // pure vertical lift off the just-cut position: safe
        }
        let lxy = ((x1[k] - x0[k]).powi(2) + (y1[k] - y0[k]).powi(2)).sqrt();
        let ll = lxy.max(dz.abs());
        // move-start snapshot bbox (contact is measured against it)
        let (mut bi0, mut bi1, mut bj0, mut bj1) = (0i64, 0i64, 0i64, 0i64);
        if check && !rapid {
            let i_a = ((half - y0[k]) * ppm).round_ties_even() as i64;
            let i_b = ((half - y1[k]) * ppm).round_ties_even() as i64;
            let j_a = ((x0[k] + half) * ppm).round_ties_even() as i64;
            let j_b = ((x1[k] + half) * ppm).round_ties_even() as i64;
            bi0 = (i_a.min(i_b) - kit.r_px - 2).max(0);
            bi1 = (i_a.max(i_b) + kit.r_px + 3).min(ni);
            bj0 = (j_a.min(j_b) - kit.r_px - 2).max(0);
            bj1 = (j_a.max(j_b) + kit.r_px + 3).min(ni);
            let (h, w) = ((bi1 - bi0) as usize, (bj1 - bj0) as usize);
            snap.resize(h * w, 0.0);
            for r in 0..h {
                let src = (bi0 as usize + r) * n + bj0 as usize;
                snap[r * w..r * w + w]
                    .copy_from_slice(&stock[src..src + w]);
            }
        }
        let skit = &skits[t];
        let fl = flute_len[t];

        let ns = ((ll / step) as i64 + 1).max(2) as usize;
        for si in 0..ns {
            let tt = si as f64 / (ns - 1) as f64;
            let x = x0[k] + (x1[k] - x0[k]) * tt;
            let y = y0[k] + (y1[k] - y0[k]) * tt;
            let z = z0[k] + (z1[k] - z0[k]) * tt;
            let ic = ((half - y) * ppm).round_ties_even() as i64;
            let jc = ((x + half) * ppm).round_ties_even() as i64;
            if ic < 0 || ic >= ni || jc < 0 || jc >= ni {
                continue;
            }
            let i0 = (ic - kit.r_px).max(0);
            let i1b = (ic + kit.r_px + 1).min(ni);
            let j0 = (jc - kit.r_px).max(0);
            let j1b = (jc + kit.r_px + 1).min(ni);
            if i0 >= i1b || j0 >= j1b {
                continue;
            }
            let mut any = false;
            if rapid {
                if check && (!lateral || z < SAFE_Z - 0.1) {
                    let mut mx = f32::MIN;
                    for ii in i0..i1b {
                        for jj in j0..j1b {
                            let fi = ((ii - (ic - kit.r_px)) as usize) * kit.w
                                + (jj - (jc - kit.r_px)) as usize;
                            if kit.foot[fi] {
                                any = true;
                                let s = stock[ii as usize * n + jj as usize];
                                if s > mx {
                                    mx = s;
                                }
                            }
                        }
                    }
                    if any {
                        let v = (mx as f64) - z;
                        if v > worst_rapid {
                            worst_rapid = v;
                            rapid_x = x;
                            rapid_y = y;
                            rapid_z = z;
                        }
                    }
                }
            } else {
                let mut over_max = f64::MIN;
                let mut removed_sum = 0f64;
                let mut removing_n = 0usize;
                let mut foot_n = 0usize;
                let bw = (bj1 - bj0) as usize;
                for ii in i0..i1b {
                    for jj in j0..j1b {
                        let fi = ((ii - (ic - kit.r_px)) as usize) * kit.w
                            + (jj - (jc - kit.r_px)) as usize;
                        if !kit.foot[fi] {
                            continue;
                        }
                        foot_n += 1;
                        any = true;
                        // numpy promotes f32 array + f64 scalar to f64 and
                        // rounds ONCE on assignment — mirror that exactly
                        let surf = z + kit.drop[fi] as f64;
                        let cell = ii as usize * n + jj as usize;
                        let live = stock[cell] as f64;
                        if check {
                            let sv = snap[((ii - bi0) as usize) * bw
                                + (jj - bj0) as usize] as f64;
                            let ov = sv - surf;
                            if ov > over_max {
                                over_max = ov;
                            }
                            let rm = live - surf;
                            if rm > 1e-9 {
                                removed_sum += rm;
                                removing_n += 1;
                            }
                        }
                        if live > surf {
                            stock[cell] = surf as f32;
                        }
                    }
                }
                if !any {
                    continue;
                }
                if check {
                    let over = over_max;
                    if over > 1e-6 {
                        contact_samples[k] += 1;
                        if over > contact[k] {
                            contact[k] = over;
                            contact_x[k] = x;
                            contact_y[k] = y;
                        }
                    }
                    volume[k] += removed_sum * px_area;
                    let ef = removing_n as f64 / foot_n as f64;
                    if ef > efrac[k] {
                        efrac[k] = ef;
                    }
                    // shank clearance vs live stock (walls persist)
                    let si0 = (ic - skit.r_px).max(0);
                    let si1b = (ic + skit.r_px + 1).min(ni);
                    let sj0 = (jc - skit.r_px).max(0);
                    let sj1b = (jc + skit.r_px + 1).min(ni);
                    let mut smx = f32::MIN;
                    let mut sany = false;
                    for ii in si0..si1b {
                        for jj in sj0..sj1b {
                            let fi = ((ii - (ic - skit.r_px)) as usize)
                                * skit.w
                                + (jj - (jc - skit.r_px)) as usize;
                            if skit.foot[fi] {
                                sany = true;
                                let s = stock[ii as usize * n + jj as usize];
                                if s > smx {
                                    smx = s;
                                }
                            }
                        }
                    }
                    if sany {
                        let so = (smx as f64) - (z + fl);
                        if so > shank_over[k] {
                            shank_over[k] = so;
                        }
                    }
                }
                if z < min_cut_z {
                    min_cut_z = z;
                }
            }
        }
        } // end 'mv
        if sp < snap_after_s.len() && snap_after_s[sp] == k as i64 {
            snapshots.push(stock.clone());
            sp += 1;
        }
    }

    let out = PyDict::new(py);
    let stock_arr =
        ndarray::Array2::from_shape_vec((n, n), stock).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(e.to_string())
        })?;
    out.set_item("stock", stock_arr.into_pyarray(py))?;
    let snaps = PyList::empty(py);
    for s in snapshots {
        let arr = ndarray::Array2::from_shape_vec((n, n), s).map_err(|e| {
            pyo3::exceptions::PyValueError::new_err(e.to_string())
        })?;
        snaps.append(arr.into_pyarray(py))?;
    }
    out.set_item("snapshots", snaps)?;
    out.set_item("volume", PyArray1::from_vec(py, volume))?;
    out.set_item("contact", PyArray1::from_vec(py, contact))?;
    out.set_item("contact_x", PyArray1::from_vec(py, contact_x))?;
    out.set_item("contact_y", PyArray1::from_vec(py, contact_y))?;
    out.set_item("contact_samples", PyArray1::from_vec(py, contact_samples))?;
    out.set_item("efrac", PyArray1::from_vec(py, efrac))?;
    out.set_item("shank_over", PyArray1::from_vec(py, shank_over))?;
    out.set_item("worst_rapid", worst_rapid)?;
    out.set_item("rapid_x", rapid_x)?;
    out.set_item("rapid_y", rapid_y)?;
    out.set_item("rapid_z", rapid_z)?;
    out.set_item("min_cut_z", min_cut_z)?;
    Ok(out)
}

#[pymodule]
fn clauderacam_kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(measure, m)?)?;
    Ok(())
}
