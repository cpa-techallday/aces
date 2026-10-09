"""Field surrogate: 3-D Fourier Neural Operator from a voxel design mask to the von Mises and
displacement-magnitude fields of the voxel FEA.

Backend "physicsnemo" uses physicsnemo.models.fno.FNO when the package is importable (the rig's
simlab-ml image); backend "builtin" is a compact FNO3d (Li et al. 2021) in plain PyTorch with the
same data pipeline, so the loop can be developed and validated on the workstation. Both record the
same metrics: relative L2 over solid cells per field on held-out designs, and the error of the
derived peak von Mises.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Dict, Optional, Tuple

import numpy as np


def _torch():
    import torch

    return torch


# ---------------- builtin FNO3d ----------------
def build_builtin(in_ch: int, out_ch: int, modes: Tuple[int, int, int], width: int, layers: int = 4):
    torch = _torch()
    nn = torch.nn

    class SpectralConv3d(nn.Module):
        def __init__(self, cin, cout, m1, m2, m3):
            super().__init__()
            self.m1, self.m2, self.m3 = m1, m2, m3
            scale = 1.0 / (cin * cout)
            shape = (cin, cout, m1, m2, m3)
            self.w = nn.ParameterList([nn.Parameter(scale * torch.randn(*shape, dtype=torch.cfloat)) for _ in range(4)])

        @staticmethod
        def mul(a, b):  # (B, cin, x, y, z) x (cin, cout, x, y, z) -> (B, cout, x, y, z)
            return torch.einsum("bixyz,ioxyz->boxyz", a, b)

        def forward(self, x):
            B = x.shape[0]
            xf = torch.fft.rfftn(x, dim=(-3, -2, -1))
            out = torch.zeros(B, self.w[0].shape[1], x.size(-3), x.size(-2), x.size(-1) // 2 + 1, dtype=torch.cfloat, device=x.device)
            m1, m2, m3 = self.m1, self.m2, self.m3
            out[:, :, :m1, :m2, :m3] = self.mul(xf[:, :, :m1, :m2, :m3], self.w[0])
            out[:, :, -m1:, :m2, :m3] = self.mul(xf[:, :, -m1:, :m2, :m3], self.w[1])
            out[:, :, :m1, -m2:, :m3] = self.mul(xf[:, :, :m1, -m2:, :m3], self.w[2])
            out[:, :, -m1:, -m2:, :m3] = self.mul(xf[:, :, -m1:, -m2:, :m3], self.w[3])
            return torch.fft.irfftn(out, s=(x.size(-3), x.size(-2), x.size(-1)), dim=(-3, -2, -1))

    class FNO3d(nn.Module):
        def __init__(self):
            super().__init__()
            self.lift = nn.Conv3d(in_ch, width, 1)
            self.spec = nn.ModuleList([SpectralConv3d(width, width, *modes) for _ in range(layers)])
            self.skip = nn.ModuleList([nn.Conv3d(width, width, 1) for _ in range(layers)])
            self.proj1 = nn.Conv3d(width, 2 * width, 1)
            self.proj2 = nn.Conv3d(2 * width, out_ch, 1)

        def forward(self, x):
            x = self.lift(x)
            for i, (s, w) in enumerate(zip(self.spec, self.skip)):
                y = s(x) + w(x)
                x = torch.nn.functional.gelu(y) if i < len(self.spec) - 1 else y
            return self.proj2(torch.nn.functional.gelu(self.proj1(x)))

    return FNO3d()


def build_physicsnemo(in_ch: int, out_ch: int, modes: Tuple[int, int, int], width: int, layers: int = 4):
    from physicsnemo.models.fno import FNO

    return FNO(in_channels=in_ch, out_channels=out_ch, decoder_layers=1, decoder_layer_size=2 * width, dimension=3,
               latent_channels=width, num_fno_layers=layers, num_fno_modes=list(modes), padding=0, coord_features=False)


def make_inputs(mask: np.ndarray) -> np.ndarray:
    """(n, 4, nx, ny, nz): mask + normalised coordinates."""
    n, nx, ny, nz = mask.shape
    gx, gy, gz = np.meshgrid((np.arange(nx) + 0.5) / nx, (np.arange(ny) + 0.5) / ny, (np.arange(nz) + 0.5) / nz, indexing="ij")
    coords = np.stack([gx, gy, gz], axis=0).astype(np.float32)[None].repeat(n, axis=0)
    return np.concatenate([mask[:, None].astype(np.float32), coords], axis=1)


def masked_rel_l2(pred, true, mask):
    """relative L2 over solid cells, per sample (torch tensors (B, nx, ny, nz))."""
    torch = _torch()
    m = mask.bool()
    num = torch.sqrt(torch.sum(((pred - true) ** 2) * m, dim=(1, 2, 3)))
    den = torch.sqrt(torch.sum((true ** 2) * m, dim=(1, 2, 3))) + 1e-12
    return num / den


def train(dataset_npz: str, out_dir: str, epochs: int = 200, batch: int = 8, lr: float = 2e-3, modes=(12, 8, 12), width: int = 24,
          layers: int = 4, val_frac: float = 0.2, seed: int = 0, backend: str = "auto", fields=("vm", "umag"),
          device: Optional[str] = None, log_every: int = 20) -> dict:
    from .dataset import load_fields, split_indices

    torch = _torch()
    torch.manual_seed(seed)
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    d = load_fields(dataset_npz)
    mask = d["mask"].astype(np.float32)
    n = mask.shape[0]
    tr, va = split_indices(n, val_frac, seed)
    X = make_inputs(mask)
    scales = {f: float(np.percentile(d[f][mask > 0], 99)) for f in fields}  # robust per-field scale
    Y = np.stack([d[f] / scales[f] for f in fields], axis=1).astype(np.float32)
    backend_note = None
    if backend in ("auto", "physicsnemo"):
        try:
            model = build_physicsnemo(4, len(fields), tuple(modes), width, layers).to(dev)
            # shape check: the model must map (B,4,nx,ny,nz) -> (B,out,nx,ny,nz)
            with torch.no_grad():
                _y = model(torch.zeros(1, 4, *mask.shape[1:], device=dev))
            assert tuple(_y.shape[2:]) == tuple(mask.shape[1:]), f"physicsnemo FNO output shape {tuple(_y.shape)}"
            backend = "physicsnemo"
        except Exception as exc:
            if backend == "physicsnemo":
                raise
            backend_note = f"physicsnemo FNO unavailable ({type(exc).__name__}: {str(exc)[:120]}); using builtin"
            print("[fno]", backend_note, flush=True)
            backend = "builtin"
    if backend == "builtin":
        model = build_builtin(4, len(fields), tuple(modes), width, layers).to(dev)
    nparam = sum(p.numel() for p in model.parameters())
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-5)
    steps = epochs * math.ceil(len(tr) / batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps, pct_start=0.1)
    Xt, Yt, Mt = torch.from_numpy(X), torch.from_numpy(Y), torch.from_numpy(mask)
    os.makedirs(out_dir, exist_ok=True)
    hist = []
    t0 = time.time()
    rng = np.random.default_rng(seed)

    def evaluate(idx):
        model.eval()
        errs = {f: [] for f in fields}
        vm_true, vm_pred = [], []
        with torch.no_grad():
            for s in range(0, len(idx), batch):
                b = idx[s:s + batch]
                xb, yb, mb = Xt[b].to(dev), Yt[b].to(dev), Mt[b].to(dev)
                pb = model(xb)
                for j, f in enumerate(fields):
                    errs[f].append(masked_rel_l2(pb[:, j], yb[:, j], mb).cpu().numpy())
                if "vm" in fields:
                    j = list(fields).index("vm")
                    vm_true.append((yb[:, j] * mb).amax(dim=(1, 2, 3)).cpu().numpy() * scales["vm"])
                    vm_pred.append((pb[:, j] * mb).amax(dim=(1, 2, 3)).cpu().numpy() * scales["vm"])
        out = {f: float(np.concatenate(errs[f]).mean()) for f in fields}
        if vm_true:
            vt, vp = np.concatenate(vm_true), np.concatenate(vm_pred)
            out["vm_max_mape_pct"] = float(100 * np.mean(np.abs(vp - vt) / np.maximum(vt, 1e-9)))
            out["vm_max_r2"] = float(1 - np.sum((vp - vt) ** 2) / max(np.sum((vt - vt.mean()) ** 2), 1e-12))
        model.train()
        return out

    for ep in range(epochs):
        perm = rng.permutation(tr)
        tot = 0.0
        for s in range(0, len(perm), batch):
            b = perm[s:s + batch]
            xb, yb, mb = Xt[b].to(dev), Yt[b].to(dev), Mt[b].to(dev)
            pb = model(xb)
            loss = sum(masked_rel_l2(pb[:, j], yb[:, j], mb).mean() for j in range(len(fields))) / len(fields)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tot += float(loss) * len(b)
        try:
            from gendesign import progress as _progress

            _progress.report("fno-train", step=ep + 1, total=epochs, train_loss=tot / len(tr))
        except Exception:
            pass
        if (ep + 1) % log_every == 0 or ep == epochs - 1:
            ev = evaluate(va)
            hist.append({"epoch": ep + 1, "train_loss": tot / len(tr), "val": ev, "wall_s": time.time() - t0})
            print(f"[fno] epoch {ep + 1}/{epochs} train {tot / len(tr):.4f} val {ev} {time.time() - t0:.0f}s", flush=True)
    final_val = evaluate(va)
    final_tr = evaluate(tr)
    torch.save({"state_dict": model.state_dict(), "backend": backend, "modes": list(modes), "width": width, "layers": layers,
                "fields": list(fields), "scales": scales, "in_ch": 4, "shape": list(mask.shape[1:])}, os.path.join(out_dir, "model.pt"))
    metrics = {"backend": backend, "backend_note": backend_note, "device": dev, "n_params": int(nparam), "n_train": int(len(tr)), "n_val": int(len(va)),
               "epochs": epochs, "batch": batch, "lr": lr, "modes": list(modes), "width": width, "layers": layers,
               "scales": scales, "val": final_val, "train": final_tr, "history": hist, "wall_s": time.time() - t0,
               "dataset": d["_manifest"], "val_indices": va.tolist()}
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    # a few held-out comparisons as PNG
    try:
        _plot_examples(model, Xt, Yt, Mt, va[:3], fields, scales, dev, out_dir)
    except Exception as exc:  # plots are optional
        metrics["plot_error"] = str(exc)
    return metrics


def _plot_examples(model, Xt, Yt, Mt, idx, fields, scales, dev, out_dir):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    torch = _torch()
    model.eval()
    with torch.no_grad():
        pb = model(Xt[idx].to(dev)).cpu().numpy()
    yb, mb = Yt[idx].numpy(), Mt[idx].numpy()
    j = list(fields).index("vm") if "vm" in fields else 0
    fig, axs = plt.subplots(len(idx), 3, figsize=(12, 3.6 * len(idx)))
    axs = np.atleast_2d(axs)
    for r, i in enumerate(idx):
        m = mb[r] > 0
        t = np.where(m, yb[r, j], np.nan) * scales[fields[j]]
        p = np.where(m, pb[r, j], np.nan) * scales[fields[j]]
        k = int(np.unravel_index(np.nanargmax(np.nan_to_num(t, nan=-1)), t.shape)[1])
        vmax = float(np.nanmax(t))
        for c, (img, ttl) in enumerate([(t[:, k, :].T, "FEA"), (p[:, k, :].T, "FNO"), (np.abs(p - t)[:, k, :].T, "|error|")]):
            im = axs[r, c].imshow(img, origin="lower", cmap="inferno", vmin=0, vmax=vmax)
            axs[r, c].set_title(f"sample {int(i)} {ttl} (y-slice {k})")
            plt.colorbar(im, ax=axs[r, c], shrink=0.8)
    fig.suptitle(f"{fields[j]} field, held-out designs")
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "examples.png"), dpi=100)
    plt.close(fig)


class FieldSurrogate:
    """Load a trained FNO and predict fields / peak stress for new masks."""

    def __init__(self, model_dir: str, device: Optional[str] = None):
        torch = _torch()
        ck = torch.load(os.path.join(model_dir, "model.pt"), map_location="cpu", weights_only=False)
        self.ck = ck
        self.dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.fields = list(ck["fields"])
        self.scales = ck["scales"]
        build = build_physicsnemo if ck["backend"] == "physicsnemo" else build_builtin
        self.model = build(ck["in_ch"], len(self.fields), tuple(ck["modes"]), ck["width"], ck["layers"]).to(self.dev)
        self.model.load_state_dict(ck["state_dict"])
        self.model.eval()
        with open(os.path.join(model_dir, "metrics.json"), "r", encoding="utf-8") as f:
            self.metrics = json.load(f)

    def predict(self, mask: np.ndarray) -> Dict[str, np.ndarray]:
        torch = _torch()
        m = np.asarray(mask, dtype=np.float32)
        if m.ndim == 3:
            m = m[None]
        x = torch.from_numpy(make_inputs(m)).to(self.dev)
        with torch.no_grad():
            p = self.model(x).cpu().numpy()
        out = {}
        for j, f in enumerate(self.fields):
            out[f] = p[:, j] * self.scales[f] * m
        return out

    def summarise(self, mask: np.ndarray) -> Dict[str, float]:
        f = self.predict(mask)
        out = {}
        if "vm" in f:
            out["vm_max_MPa"] = float(f["vm"].max())
        if "umag" in f:
            out["umax_mm"] = float(f["umag"].max())
        return out
