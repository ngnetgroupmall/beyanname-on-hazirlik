"""E-Mükellef giriş bilgisini bu bilgisayarda saklar (kullanıcı kararı 19.11.2026).

Şifre düz metin olarak diske YAZILMAZ: Windows DPAPI (CryptProtectData) ile, oturum açmış Windows kullanıcısına bağlı
şifrelenir; dosya başka bir kullanıcı ya da başka bir bilgisayarda açılamaz.
Dosya: %LOCALAPPDATA%\\KdvOnHazirlik\\giris.json  {"kul_adi": "...", "sifre_dpapi": "<base64>"}
"""
import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

DOSYA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik" / "giris.json"
_EK = b"KdvOnHazirlik/E-Mukellef"          # ek entropi: başka bir programın aynı blobu yanlışlıkla açmaması için


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(bayt):
    tampon = ctypes.create_string_buffer(bayt, len(bayt))
    return _Blob(len(bayt), ctypes.cast(tampon, ctypes.POINTER(ctypes.c_char))), tampon


def _dpapi(bayt, sifrele):
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    giris, _t1 = _blob(bayt)
    ek, _t2 = _blob(_EK)
    cikis = _Blob()
    if sifrele:
        ok = crypt32.CryptProtectData(ctypes.byref(giris), None, ctypes.byref(ek), None, None, 0x01, ctypes.byref(cikis))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(giris), None, ctypes.byref(ek), None, None, 0x01, ctypes.byref(cikis))
    if not ok:
        raise OSError("Windows şifreleme (DPAPI) başarısız")
    try:
        return ctypes.string_at(cikis.pbData, cikis.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(cikis.pbData, ctypes.c_void_p))


def oku():
    """(kul_adi, sifre) ya da kayıt yoksa/açılamazsa (kul_adi veya '', None)."""
    try:
        veri = json.loads(DOSYA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", None
    if not isinstance(veri, dict):              # bozuk/yabancı içerik: kayıt yok say
        return "", None
    kul_adi = str(veri.get("kul_adi") or "")
    try:
        sifre = _dpapi(base64.b64decode(veri["sifre_dpapi"]), False).decode("utf-8")
    except Exception:
        return kul_adi, None
    return kul_adi, sifre or None


def kaydet(kul_adi, sifre):
    DOSYA.parent.mkdir(parents=True, exist_ok=True)
    veri = {"kul_adi": kul_adi, "sifre_dpapi": base64.b64encode(_dpapi(sifre.encode("utf-8"), True)).decode("ascii")}
    gecici = DOSYA.with_name(DOSYA.name + ".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
    os.replace(gecici, DOSYA)               # kendi ayar dosyamız


def sil():
    try:
        DOSYA.unlink()
    except OSError:
        pass
