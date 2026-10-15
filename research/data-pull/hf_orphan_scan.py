import os, json
from collections import defaultdict

hub = "/mnt/nvme2/hf_cache/hub"
blobs = os.path.join(hub, "blobs")

# all real blob files (sharded layout: blobs/XX/<hash>, skip .lock)
real = {}
for root, _, files in os.walk(blobs):
    for f in files:
        if f.endswith(".lock"):
            continue
        p = os.path.join(root, f)
        if os.path.isfile(p) and not os.path.islink(p):
            real[os.path.realpath(p)] = os.path.getsize(p)

# referenced: resolve every symlink found under any model dir
model_refs = defaultdict(set)
models = [d for d in os.listdir(hub) if d.startswith("models--")]
for m in models:
    mdir = os.path.join(hub, m)
    for root, _, files in os.walk(mdir):
        for f in files:
            p = os.path.join(root, f)
            if os.path.islink(p):
                t = os.path.realpath(p)
                if t in real:
                    model_refs[m].add(t)

referenced = set().union(*model_refs.values()) if model_refs else set()
orphans = sorted(set(real) - referenced)
orphan_bytes = sum(real[b] for b in orphans)

print(f"blob files: {len(real)}, {sum(real.values())/1e9:.1f} GB")
print(f"referenced: {len(referenced)}, {sum(real[b] for b in referenced)/1e9:.1f} GB")
print(f"ORPHAN: {len(orphans)}, {orphan_bytes/1e9:.1f} GB")
print()
qwen = "models--QuantTrio--Qwen3-235B-A22B-Instruct-2507-AWQ"
qwen_excl = []
for m in sorted(models):
    refs = model_refs.get(m, set())
    sz = sum(real[b] for b in refs) / 1e9
    excl = [b for b in refs if sum(1 for mm in models if b in model_refs.get(mm, set())) == 1]
    excl_sz = sum(real[b] for b in excl) / 1e9
    print(f"{sz:9.1f} GB (excl {excl_sz:8.1f})  {m}")
    if m == qwen:
        qwen_excl = sorted(excl)

json.dump({"orphans": orphans, "qwen_exclusive": qwen_excl},
          open("/tmp/hf-orphans.json", "w"))
print("\nwrote /tmp/hf-orphans.json")
