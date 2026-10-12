import h5py, pyarrow.parquet as pq, zipfile, tarfile, os, json, glob

report = {}

# 1. The Well HDF5
for ds, f in [("the-well/shear_flow", "data/test/shear_flow_Reynolds_1e4_Schmidt_1e-1.hdf5"),
              ("the-well/rayleigh_benard", None)]:
    base = f"/data/{ds}"
    if f is None:
        for root, _, files in os.walk(base):
            h5 = [x for x in files if x.endswith(".hdf5")]
            if h5:
                f = os.path.relpath(os.path.join(root, h5[0]), base)
                break
    p = os.path.join(base, f)
    with h5py.File(p, "r") as h:
        def walk(g, pre=""):
            out = []
            for k in list(g.keys())[:8]:
                it = g[k]
                out.append(f"{pre}{k}:{getattr(it, 'shape', '')}")
                if isinstance(it, h5py.Group):
                    out += walk(it, pre + "  ")[:3]
            return out
        keys = walk(h)[:10]
    report[ds] = {"file": f, "size_gb": round(os.path.getsize(p) / 1e9, 2), "structure": keys}

# 2. AirfRANS
z = "/data/airfrans/OF_dataset.zip"
with zipfile.ZipFile(z) as zf:
    names = zf.namelist()
report["airfrans/OF_dataset.zip"] = {"entries": len(names), "first": names[:3],
                                     "size_gb": round(os.path.getsize(z) / 1e9, 1)}
report["airfrans"] = {"dir": sorted(os.listdir("/data/airfrans"))}

# 3. MCB tar listings
for m in ("MCB_A.tar.gz", "MCB_B.tar.gz"):
    p = f"/data/mcb/{m}"
    with tarfile.open(p, "r:gz") as t:
        names = []
        for i, mem in enumerate(t):
            names.append(mem.name)
            if i >= 4:
                break
    report[f"mcb/{m}"] = {"size_gb": round(os.path.getsize(p) / 1e9, 2), "first": names}

# 4. ABC parquet schema
shards = sorted(glob.glob("/data/abc-tactile/*.parquet"))
if shards:
    sch = pq.read_schema(shards[0])
    report["abc-tactile"] = {"shards": len(shards), "schema_cols": sch.names[:12]}

# 5. KABlab inventory
kab = {}
for root, _, files in os.walk("/data/kab-lattice"):
    for x in files:
        if x.endswith((".zip", ".csv", ".xlsx")):
            p = os.path.join(root, x)
            kab[os.path.relpath(p, "/data/kab-lattice")] = round(os.path.getsize(p) / 1e6, 1)
report["kab-lattice"] = dict(list(kab.items())[:15])

# 6. JHTDB
j = "/data/jhtdb"
report["jhtdb"] = sorted(os.listdir(j)) if os.path.isdir(j) else "MISSING"

print(json.dumps(report, indent=1, default=str))
