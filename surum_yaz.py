"""Beyanname Ön Hazırlık — sürüm dosyasını (surum.json) üretir. Programın geliştirildiği bilgisayarda, GitHub'a göndermeden ÖNCE çalıştırılır.

surum.json, depodaki program dosyalarının listesi ve sha256 özetleridir; kullanıcıların programı açılışta bununla karşılaştırıp
değişen dosyaları indirir (guncelle.py). Dosyalar değişmediyse sürüm numarası da değişmez.

Kullanım: python -X utf8 surum_yaz.py [--paket-en-az N]
    --paket-en-az N   bu sürümün gerektirdiği en düşük kurulum paketi numarası (paketle.py'deki PAKET_NO). Yalnız Python sürümü ya da
                      kitaplıklar değiştiğinde artırılır; o zaman eski paketler kendini güncellemez, kullanıcı yeni paketi indirir.

Sürüm numarası: YIL.AY.GÜN.SIRA (aynı gün ikinci yayın -> SIRA 2).
"""
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

KLASOR = Path(__file__).resolve().parent
MANIFEST = KLASOR / "surum.json"
HARIC = {"paketle.py", "surum_yaz.py"}       # yalnız geliştirme bilgisayarında gereken dosyalar: kullanıcıya gitmez


def program_dosyalari():
    """Kullanıcıya giden dosyalar (paket ve güncelleme aynı listeyi kullanır)."""
    d = [p for p in KLASOR.glob("*.py") if p.name not in HARIC and not p.name.startswith("test_")]
    return sorted(d + list(KLASOR.glob("*.pyw")), key=lambda p: p.name)


def manifest_yaz(paket_en_az=None):
    """surum.json'u günceller -> (manifest, değişti mi)."""
    try:
        eski = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        eski = {}
    dosyalar = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in program_dosyalari()}
    en_az = int(paket_en_az if paket_en_az is not None else eski.get("paket_en_az", 1))
    if eski.get("dosyalar") == dosyalar and eski.get("paket_en_az") == en_az and eski.get("surum"):
        return eski, False
    bugun = date.today()
    onek = f"{bugun.year}.{bugun.month}.{bugun.day}."
    es = str(eski.get("surum") or "")
    sira = int(es.rsplit(".", 1)[1]) + 1 if es.startswith(onek) and es.rsplit(".", 1)[1].isdigit() else 1
    yeni = {"surum": f"{onek}{sira}", "paket_en_az": en_az, "dosyalar": dosyalar}
    MANIFEST.write_text(json.dumps(yeni, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return yeni, True


if __name__ == "__main__":
    en_az = int(sys.argv[sys.argv.index("--paket-en-az") + 1]) if "--paket-en-az" in sys.argv else None
    m, degisti = manifest_yaz(en_az)
    print(f"Sürüm: {m['surum']}  ({len(m['dosyalar'])} dosya, paket_en_az={m['paket_en_az']})"
          + ("  — YENİ; şimdi git commit + push" if degisti else "  — değişiklik yok"))
