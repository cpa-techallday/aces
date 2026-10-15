#!/usr/bin/env python3
"""JHTDB channel-flow pull via giverny: mean profiles + wall gradient -> friction factor.

Datasets: 'channel' (Re_tau ~ 1000) and 'channel5200' (Re_tau ~ 5200).
Token: JHTDB public testing token.

Derived (from data + published Re_tau only, no literature values baked in):
  dudy_wall           -> nu = dudy_w * h^2 / Re_tau^2
  u_tau = Re_tau nu/h ; tau_w = u_tau^2 (rho=1)
  U_b = (1/2h) * integral U dy ; Re_b = 2h U_b/nu
  cf  = tau_w / (0.5 rho U_b^2) ; Darcy f = 4 cf
"""
import json, math, time
import numpy as np
from givernylocal.turbulence_dataset import turb_dataset
from givernylocal.turbulence_toolkit import getData

TOKEN = "edu.jhu.pha.turbulence.testing-201406"
H = 1.0
MAXPTS = 4096

def ygrid(n=240):
    s = np.linspace(-1, 1, n)
    return np.sinh(4.0 * s) / np.sinh(4.0)

def columns(nx=4, nz=4):
    xs = np.linspace(0.3, 8 * math.pi - 0.3, nx)
    zs = np.linspace(0.3, 3 * math.pi - 0.3, nz)
    return [(x, z) for x in xs for z in zs]

def pull_profile(ds, t, ys, cols):
    U = np.zeros(len(ys)); V = np.zeros(len(ys)); W = np.zeros(len(ys))
    per_y = len(cols); batch = MAXPTS // per_y
    for i0 in range(0, len(ys), batch):
        chunk = ys[i0:i0 + batch]
        pts = np.array([[x, y, z] for y in chunk for (x, z) in cols], dtype=np.float64)
        out = np.array(getData(ds, "velocity", t, "none", "lag8", "field", pts))
        out = out.reshape(len(chunk), per_y, -1)[:, :, :3]
        U[i0:i0+len(chunk)] = out[:, :, 0].mean(axis=1)
        V[i0:i0+len(chunk)] = out[:, :, 1].mean(axis=1)
        W[i0:i0+len(chunk)] = out[:, :, 2].mean(axis=1)
        print(f"    y-rows {i0}-{i0+len(chunk)-1} ok", flush=True)
        time.sleep(1)
    return U, V, W

def wall_gradient(ds, t, cols):
    pts = np.array([[x, y, z] for (x, z) in cols for y in (-1.0, 1.0)], dtype=np.float64)
    out = np.array(getData(ds, "velocity", t, "none", "fd4noint", "gradient", pts))
    # expect (npts, 9): dudx dudy dudz dvdx ... dwdz ; dudy = col 1
    g = out.reshape(len(cols), 2, -1)
    return float((np.abs(g[:, 0, 1]).mean() + np.abs(g[:, 1, 1]).mean()) / 2.0)

def run(title, re_tau, t):
    ds = turb_dataset(dataset_title=title, output_path="/data/giverny_output", auth_token=TOKEN)
    ys = ygrid(); cols = columns()
    U, V, W = pull_profile(ds, t, ys, cols)
    dudy_w = wall_gradient(ds, t, cols)
    nu = dudy_w * H**2 / re_tau**2
    u_tau = re_tau * nu / H
    tau_w = u_tau**2
    U_b = 0.5 * np.trapezoid(U, ys) / H
    Re_b = 2 * H * U_b / nu
    cf = 2 * tau_w / U_b**2
    return {
        "dataset": title, "re_tau": re_tau, "time": t, "columns_xz": len(cols),
        "note": "cf = tau_w/(0.5*rho*Ub^2); Darcy f = 4*cf. Mean over 16 (x,z) columns, one snapshot.",
        "derived": {"dudy_wall": dudy_w, "nu": nu, "u_tau": u_tau, "tau_w": tau_w,
                    "U_bulk": float(U_b), "Re_bulk": float(Re_b),
                    "cf": float(cf), "darcy_f": float(4 * cf)},
        "profile": {"y": [float(v) for v in ys],
                    "y_plus": [float((1 - abs(v)) * re_tau) for v in ys],
                    "U": [float(v) for v in U], "V": [float(v) for v in V], "W": [float(v) for v in W],
                    "U_plus": [float(v / u_tau) for v in U]},
    }

def main():
    out = {}
    for title, rt, t in [("channel", 1000, 0.0), ("channel5200", 5200, 0.0)]:
        print(f"pulling {title} ...", flush=True)
        try:
            out[title] = run(title, rt, t)
            d = out[title]["derived"]
            print(f"  Re_b={d['Re_bulk']:.0f}  cf={d['cf']:.5f}  Darcy f={d['darcy_f']:.5f}", flush=True)
        except Exception as e:
            print(f"  FAILED {title}: {type(e).__name__}: {e}", flush=True)
            out[title] = {"error": f"{type(e).__name__}: {e}"}
    with open("/data/jhtdb-channel.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("written /data/jhtdb-channel.json")

if __name__ == "__main__":
    main()
