"""Print a summary of a .glb file: extensions, meshes/parts, triangle counts, materials, textures.
Usage: python tools/inspect_glb.py file.glb [more.glb ...]"""
import json, struct, sys


def read_glb(path):
    with open(path, "rb") as f:
        data = f.read()
    magic, ver, length = struct.unpack_from("<4sII", data, 0)
    assert magic == b"glTF", "not a GLB"
    off, js, binc = 12, None, b""
    while off < length:
        clen, ctype = struct.unpack_from("<I4s", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            js = json.loads(chunk)
        elif ctype == b"BIN\x00":
            binc = chunk
        off += 8 + clen
    return js, binc


for path in sys.argv[1:]:
    g, b = read_glb(path)
    print("=" * 70, "\n", path, f"bin={len(b)/1e6:.1f}MB")
    print("extensionsUsed:", g.get("extensionsUsed"), "required:", g.get("extensionsRequired"))
    acc = g.get("accessors", [])
    tris = 0
    print("meshes:", len(g.get("meshes", [])), "nodes:", len(g.get("nodes", [])), "materials:", len(g.get("materials", [])))
    for i, m in enumerate(g.get("meshes", [])):
        t = 0
        for p in m["primitives"]:
            if "indices" in p:
                t += acc[p["indices"]]["count"] // 3
            else:
                t += acc[p["attributes"]["POSITION"]]["count"] // 3
        tris += t
    print("total triangles:", tris)
    names = [n.get("name", "") for n in g.get("nodes", [])]
    print("node names (first 80):", names[:80])
    print("materials:", [m.get("name") for m in g.get("materials", [])])
    imgs = g.get("images", [])
    bvs = g.get("bufferViews", [])
    print("images:", len(imgs))
    for im in imgs:
        size = bvs[im["bufferView"]]["byteLength"] if "bufferView" in im else 0
        print("   ", im.get("name"), im.get("mimeType"), f"{size/1e6:.2f}MB")
    # bounding box from POSITION min/max of all meshes (local, ignores node transforms)
    mn = [1e9] * 3; mx = [-1e9] * 3
    for m in g.get("meshes", []):
        for p in m["primitives"]:
            a = acc[p["attributes"]["POSITION"]]
            if "min" in a:
                mn = [min(x, y) for x, y in zip(mn, a["min"])]; mx = [max(x, y) for x, y in zip(mx, a["max"])]
    print("local bbox:", [round(v, 2) for v in mn], [round(v, 2) for v in mx])
