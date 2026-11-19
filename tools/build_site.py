"""Build the public website (for Vercel or any static host) into ./site

  python tools/build_site.py

The site has the 3D campus and the simulated flow only. It contains NO server code, NO database and NO API key:
the AI agents run locally (python tools/negotiator/server.py) with each person's own Sarvam API key.
"""
import json, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'site'
if SITE.exists():
    shutil.rmtree(SITE)
SITE.mkdir()
shutil.copy(ROOT / 'zeno-forge.html', SITE / 'index.html')
shutil.copytree(ROOT / 'models', SITE / 'models', ignore=shutil.ignore_patterns('*.zip', '*.blend', '*.fbx', '*.obj', '*.mtl', 'Previews', 'preview*'))
(SITE / 'vercel.json').write_text(json.dumps({'cleanUrls': True, 'headers': [{'source': '/models/(.*)', 'headers': [{'key': 'Cache-Control', 'value': 'public, max-age=31536000, immutable'}]}]}, indent=2))
for bad in ('.env', 'zeno.db'):
    assert not list(SITE.rglob(bad)), f'{bad} must never be in the public site'
size = sum(f.stat().st_size for f in SITE.rglob('*') if f.is_file()) / 1e6
print(f'site/ ready: {size:.1f} MB, {sum(1 for _ in SITE.rglob("*"))} files. Deploy with:  npx vercel deploy site --prod')
