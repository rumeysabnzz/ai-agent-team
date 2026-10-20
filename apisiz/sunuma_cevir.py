"""apisiz/raporlar altındaki Markdown raporları sunum biçimlerine çevirir.

Kullanım (depo kökünden):
    pip install -r bolbe-panel/requirements.txt
    python apisiz/sunuma_cevir.py                 # tüm raporlar, PDF + Excel
    python apisiz/sunuma_cevir.py pdf xlsx pptx   # istenen biçimler

Her rapor klasöründe "sunum/" alt klasörüne yazar. Dönüştürücü panelle aynıdır (bolbe-panel/app/export.py).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "bolbe-panel"))

from app import export  # noqa: E402

formats = sys.argv[1:] or ["pdf", "xlsx"]
for md in sorted((ROOT / "raporlar").glob("*/*.md")):
    out_dir = md.parent / "sunum"
    out_dir.mkdir(exist_ok=True)
    for fmt in formats:
        data, _, name = export.export(md.read_text("utf-8"), md.name, fmt)
        (out_dir / name).write_bytes(data)
        print(f"{md.parent.name}/sunum/{name}")
