"""Authenticator (Google / Microsoft Authenticator) doğrulama kodunu program üretir (kullanıcı kararı 27.11.2026).

Luca girişinde "İki Aşamalı Doğrulama — Microsoft veya Google Authenticator ile gelen 6 haneli şifre" istenir. Uygulamanın gösterdiği
kod, kurulumda verilen gizli anahtardan hesaplanır (TOTP, RFC 6238). Kullanıcı anahtarı programa da tanıtırsa kodu program üretir.

Anahtar düz metin olarak diske YAZILMAZ: giriş bilgileriyle aynı yöntem (giris_kasasi.py, Windows DPAPI, oturum açmış kullanıcıya
bağlı). Anahtar ve üretilen kod günlüğe yazılmaz. Dosya: %LOCALAPPDATA%\\KdvOnHazirlik\\totp.json
    {"kayitlar": {"luca:<üye no>/<kullanıcı>": {"anahtar_dpapi": "<base64>", "hane": 6, "sure": 30, "ozet": "SHA1"}, ...}}
Kayıt hesaba göredir (iki ayrı Luca hesabı karışmaz). Kabul edilen anahtar biçimleri: kurulum ekranındaki metin (base32),
otpauth://totp/… adresi ve Google Authenticator "Hesapları aktar" QR'ının otpauth-migration://offline?data=… adresi.
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import giris_kasasi

DOSYA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik" / "totp.json"
OZETLER = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


def _ham(anahtar):
    """Base32 anahtarın baytları (boşluk/tire ve eksik '=' dolgusu hoş görülür); geçersizse ValueError."""
    a = "".join(str(anahtar or "").split()).replace("-", "").upper().rstrip("=")
    if len(a) < 16:
        raise ValueError("Anahtar çok kısa; kurulum ekranındaki anahtarın tamamını yazın.")
    try:
        return base64.b32decode(a + "=" * (-len(a) % 8))
    except Exception:
        raise ValueError("Anahtar geçersiz: yalnız A-Z harfleri ve 2-7 rakamlarından oluşmalı.")


# ---------------------------------------------------------------- Google Authenticator dışa aktarma (otpauth-migration)
def _varint(b, i):
    sonuc, kayma = 0, 0
    while True:
        if i >= len(b):
            raise ValueError("bozuk veri")
        c = b[i]
        i += 1
        sonuc |= (c & 0x7F) << kayma
        if not c & 0x80:
            return sonuc, i
        kayma += 7


def _alanlar(b):
    """Protobuf ileti alanları -> [(alan no, değer)] (yalnız varint ve uzunluklu alanlar)."""
    i, cikis = 0, []
    while i < len(b):
        anahtar, i = _varint(b, i)
        no, tur = anahtar >> 3, anahtar & 7
        if tur == 0:
            v, i = _varint(b, i)
        elif tur == 2:
            n, i = _varint(b, i)
            v, i = b[i:i + n], i + n
        else:
            raise ValueError("desteklenmeyen alan")
        cikis.append((no, v))
    return cikis


def _goc_coz(adres):
    """otpauth-migration://offline?data=… -> [{secret(base32), ad, veren, hane, ozet, tur}] (Google Authenticator biçimi)."""
    q = parse_qs(urlparse(adres).query)
    try:
        ham = base64.b64decode(unquote(q["data"][0]) + "===")
        kayitlar = []
        for no, v in _alanlar(ham):
            if no != 1:
                continue
            k = dict(_alanlar(v))
            kayitlar.append({"secret": base64.b32encode(k.get(1, b"")).decode("ascii").rstrip("="),
                             "ad": bytes(k.get(2, b"")).decode("utf-8", "replace"), "veren": bytes(k.get(3, b"")).decode("utf-8", "replace"),
                             "ozet": {0: "SHA1", 1: "SHA1", 2: "SHA256", 3: "SHA512"}.get(k.get(4, 1), "?"),
                             "hane": {0: 6, 1: 6, 2: 8}.get(k.get(5, 1), 0), "tur": k.get(6, 2)})
    except (KeyError, IndexError, ValueError, TypeError):
        raise ValueError("Google Authenticator aktarma adresi okunamadı.")
    return kayitlar


def anahtar_coz(metin, aranan="luca"):
    """Kullanıcının yapıştırdığı metin -> {"anahtar", "hane", "sure", "ozet"}; geçersizse ValueError.
    Aktarma adresinde birden çok hesap varsa adı/vereni `aranan`ı içeren TEK kayıt alınır (yoksa ya da birden çoksa hata)."""
    metin = str(metin or "").strip()
    kayit = {"anahtar": metin, "hane": 6, "sure": 30, "ozet": "SHA1"}
    if metin.lower().startswith("otpauth-migration://"):
        uygun = [k for k in _goc_coz(metin) if k["tur"] == 2]          # 2 = TOTP (zamana bağlı)
        if len(uygun) > 1:
            uygun = [k for k in uygun if aranan in (k["ad"] + " " + k["veren"]).lower()]
        if len(uygun) != 1:
            raise ValueError("Aktarılan hesaplar arasında tek bir Luca kaydı bulunamadı; Google Authenticator'da yalnız Luca'yı "
                             "seçerek dışa aktarın.")
        k = uygun[0]
        kayit = {"anahtar": k["secret"], "hane": k["hane"], "sure": 30, "ozet": k["ozet"]}
    elif metin.lower().startswith("otpauth://"):
        u = urlparse(metin)
        if u.netloc.lower() != "totp":
            raise ValueError("Bu adres zamana bağlı kod (TOTP) için değil; Authenticator kurulumundaki anahtarı yazın.")
        s = {k.lower(): v[-1] for k, v in parse_qs(u.query).items()}
        try:
            kayit = {"anahtar": s.get("secret", ""), "hane": int(s.get("digits", 6)), "sure": int(s.get("period", 30)),
                     "ozet": s.get("algorithm", "SHA1").upper().replace("-", "")}
        except ValueError:
            raise ValueError("Adresteki hane/süre bilgisi okunamadı.")
    if kayit["hane"] not in (6, 8) or not 15 <= kayit["sure"] <= 120 or kayit["ozet"] not in OZETLER:
        raise ValueError("Bu kurulum türü desteklenmiyor (hane, süre ya da özet yöntemi alışılmışın dışında).")
    _ham(kayit["anahtar"])
    kayit["anahtar"] = "".join(kayit["anahtar"].split()).replace("-", "").upper().rstrip("=")
    return kayit


def kod(kayit, zaman=None):
    """O anki doğrulama kodu (baştaki sıfırlar korunur)."""
    sayac = int((time.time() if zaman is None else zaman) // kayit["sure"])
    ozet = hmac.new(_ham(kayit["anahtar"]), struct.pack(">Q", sayac), OZETLER[kayit["ozet"]]).digest()
    i = ozet[-1] & 0x0F
    sayi = struct.unpack(">I", ozet[i:i + 4])[0] & 0x7FFFFFFF
    return str(sayi % 10 ** kayit["hane"]).zfill(kayit["hane"])


def kalan(kayit, zaman=None):
    """Geçerli kodun değişmesine kalan saniye."""
    z = time.time() if zaman is None else zaman
    return kayit["sure"] - z % kayit["sure"]


def hesap_adi(hizmet, *parcalar):
    return f"{hizmet}:" + "/".join(str(p or "").strip().lower() for p in parcalar)


def _hepsi():
    try:
        veri = json.loads(DOSYA.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    k = veri.get("kayitlar") if isinstance(veri, dict) else None
    if not isinstance(k, dict):
        raise ValueError("totp.json beklenen yapıda değil")
    return k


def _yaz(kayitlar):
    DOSYA.parent.mkdir(parents=True, exist_ok=True)
    gecici = DOSYA.with_name(DOSYA.name + ".tmp")
    gecici.write_text(json.dumps({"kayitlar": kayitlar}, ensure_ascii=False), encoding="utf-8")
    os.replace(gecici, DOSYA)


def oku(ad):
    """Hesabın kayıtlı anahtarı ({"anahtar", "hane", "sure", "ozet"}) ya da yoksa/açılamazsa None."""
    try:
        k = _hepsi().get(ad)
        if not isinstance(k, dict):
            return None
        anahtar = giris_kasasi._dpapi(base64.b64decode(k["anahtar_dpapi"]), False).decode("utf-8")
        kayit = {"anahtar": anahtar, "hane": int(k.get("hane", 6)), "sure": int(k.get("sure", 30)), "ozet": str(k.get("ozet", "SHA1"))}
        _ham(kayit["anahtar"])
        return kayit if kayit["ozet"] in OZETLER else None
    except Exception:
        return None


def kaydet(ad, kayit):
    """Hesabın anahtarını saklar; diğer kayıtlara dokunmaz. Dosya okunamıyorsa üzerine YAZMAZ (hata verir)."""
    kayitlar = _hepsi()
    kayitlar[ad] = {"anahtar_dpapi": base64.b64encode(giris_kasasi._dpapi(kayit["anahtar"].encode("utf-8"), True)).decode("ascii"),
                    "hane": kayit["hane"], "sure": kayit["sure"], "ozet": kayit["ozet"]}
    _yaz(kayitlar)


def sil(ad):
    kayitlar = _hepsi()
    if kayitlar.pop(ad, None) is not None:
        _yaz(kayitlar)
