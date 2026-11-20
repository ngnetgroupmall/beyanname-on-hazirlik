"""Luca giriş bilgisini (üye/müşteri no, kullanıcı adı, parola) bu bilgisayarda saklar.

E-Mükellef'tekiyle aynı yöntem (giris_kasasi.py): parola düz metin olarak diske YAZILMAZ; Windows DPAPI ile, oturum açmış
Windows kullanıcısına bağlı şifrelenir. Dosya: %LOCALAPPDATA%\\KdvOnHazirlik\\luca_giris.json
"""
import base64
import json
import os
from pathlib import Path

import giris_kasasi

DOSYA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik" / "luca_giris.json"


def oku():
    """(musteri_no, kullanici, parola) ya da kayıt yoksa/açılamazsa (musteri_no veya '', kullanici veya '', None)."""
    try:
        veri = json.loads(DOSYA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", "", None
    if not isinstance(veri, dict):
        return "", "", None
    musteri_no, kullanici = str(veri.get("musteri_no") or ""), str(veri.get("kullanici") or "")
    try:
        parola = giris_kasasi._dpapi(base64.b64decode(veri["parola_dpapi"]), False).decode("utf-8")
    except Exception:
        return musteri_no, kullanici, None
    return musteri_no, kullanici, parola or None


def kaydet(musteri_no, kullanici, parola):
    DOSYA.parent.mkdir(parents=True, exist_ok=True)
    veri = {"musteri_no": musteri_no, "kullanici": kullanici,
            "parola_dpapi": base64.b64encode(giris_kasasi._dpapi(parola.encode("utf-8"), True)).decode("ascii")}
    gecici = DOSYA.with_name(DOSYA.name + ".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
    os.replace(gecici, DOSYA)               # kendi ayar dosyamız


def sil():
    try:
        DOSYA.unlink()
    except OSError:
        pass
