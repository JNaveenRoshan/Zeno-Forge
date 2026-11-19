"""Build the public website (for Vercel or any static host) into ./site

  python tools/build_site.py

The site has the 3D campus and the simulated flow only. It contains NO server code, NO database and NO API key:
the AI agents run locally (python tools/negotiator/server.py) with each person's own Sarvam API key.
"""
import gzip, json, re, shutil, struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'
link = (SITE / '.vercel' / 'project.json').read_bytes() if (SITE / '.vercel' / 'project.json').exists() else None   # keep the Vercel project link
if SITE.exists():
    shutil.rmtree(SITE)
SITE.mkdir()
if link:
    (SITE / '.vercel').mkdir(); (SITE / '.vercel' / 'project.json').write_bytes(link)
# public site opens at night in the monsoon (the local app keeps its own defaults)
html = (ROOT / 'zeno-forge.html').read_text(encoding='utf-8')
for a, b in [("const sim = { clock: 0, tod: 10.5, daySpeed: 1 / 60, weather: 'clear',", "const sim = { clock: 0, tod: 21.0, daySpeed: 1 / 60, weather: 'monsoon',"),
             ('<option value="monsoon">', '<option value="monsoon" selected>')]:
    assert html.count(a) == 1, f'default not found: {a}'
    html = html.replace(a, b)
(SITE / 'index.html').write_text(html, encoding='utf-8')
shutil.copytree(ROOT / 'models', SITE / 'models', ignore=shutil.ignore_patterns('*.zip', '*.blend', '*.fbx', '*.obj', '*.mtl', 'Previews', 'preview*'))
# one gzipped bundle of every model the page uses (the page reads it with DecompressionStream): far fewer, smaller downloads
i = html.index('const KLIST = {'); kl = html[i:html.index('};', i)]
kd = dict(re.findall(r"(\w+): '(models/[^']+/)'", html[html.index('const KDIR = {'):i]))
idx, blobs, off = {}, [], 0
for d, f in sorted(set(re.findall(r"'([a-z]+)/([^']+)'", kl))):
    b = (ROOT / (kd[d] + f + '.glb')).read_bytes(); idx[f'{d}/{f}'] = [off, len(b)]; blobs.append(b); off += len(b)
head = json.dumps(idx, separators=(',', ':')).encode()
(SITE / 'models' / 'kits.pack').write_bytes(gzip.compress(struct.pack('<I', len(head)) + head + b''.join(blobs), 9))
print(f'kits.pack: {len(idx)} models, {off / 1e6:.1f} MB -> {(SITE / "models" / "kits.pack").stat().st_size / 1e6:.1f} MB')
(SITE / 'vercel.json').write_text(json.dumps({'cleanUrls': True, 'headers': [{'source': '/models/(.*)', 'headers': [{'key': 'Cache-Control', 'value': 'public, max-age=31536000, immutable'}]}, {'source': '/models/kits.pack', 'headers': [{'key': 'Content-Type', 'value': 'application/octet-stream'}, {'key': 'Cache-Control', 'value': 'public, max-age=3600, must-revalidate'}]}]}, indent=2))
for bad in ('.env', 'zeno.db'):
    assert not list(SITE.rglob(bad)), f'{bad} must never be in the public site'
size = sum(f.stat().st_size for f in SITE.rglob('*') if f.is_file()) / 1e6
print(f'site/ ready: {size:.1f} MB, {sum(1 for _ in SITE.rglob("*"))} files. Deploy with:  npx vercel deploy site --prod')
