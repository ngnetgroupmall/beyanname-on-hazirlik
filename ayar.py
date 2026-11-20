"""Bilgisayara göre değişen yerler: kayıt kökü (firma klasörlerinin ana klasörü), firma eşleme dosyası, E-Mükellef adresi ve
kullanıcı ayarları. Tkinter kullanmaz; arayüz (onhazirlik.py) ve komut aracı (komut.py) aynı kuralları buradan alır.

Kullanıcı verisi her bilgisayarda %LOCALAPPDATA%\\KdvOnHazirlik altındadır (ayarlar.json, giriş bilgileri, tarayıcı profili, günlük).
(Klasör adı programın eski adından kalmadır; değiştirilmedi ki mevcut ayarlar kaybolmasın.)

Firma eşlemesi (firma -> klasör / kısa ad / Luca firması):
  - program klasöründe firmalar.json VARSA o kullanılır (programın geliştirildiği bilgisayar; projedeki başka betikler de oradan okur);
  - yoksa (taşınabilir paket) %LOCALAPPDATA%\\KdvOnHazirlik\\firmalar.json. Paket VERİSİZ gelir: dosya ilk eşlemede oluşur; mükellef
    listesi her kullanıcının kendi E-Mükellef girişinde indirilir.

E-Mükellef adresi ofise özeldir (https://<ofis>.emukellef.org); ayarlar.json'daki "emukellef_adresi". Tanımlı değilse program ilk
açılışta sorar.

Kayıt kökü: ayarlar.json'daki "firmalar_klasoru"; tanımlı değilse ve D:\\Claude\\Firmalar varsa o (soru sorulmaz); ikisi de yoksa
program ilk açılışta klasörü seçtirir.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

KLASOR = Path(__file__).resolve().parent
VERI = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik"
AYARLAR = VERI / "ayarlar.json"
# programın geliştirildiği bilgisayardaki kök (ortam değişkeni yalnız testler için: "başka bilgisayar" dalını zorlar)
VARSAYILAN_KOK = Path(os.environ.get("KDV_VARSAYILAN_KOK") or r"D:\Claude\Firmalar")
KOK_ANAHTARI = "firmalar_klasoru"
ADRES_ANAHTARI = "emukellef_adresi"
LOGO = VERI / "logo.png"        # ofisin logosu (raporların başlığında); kullanıcı verisindedir, programla / depoyla DAĞITILMAZ
LOGO_SINIRI = 2_000_000         # bayt
EMUKELLEF_ALANI = "emukellef.org"


def ayarlari_oku():
    try:
        d = json.loads(AYARLAR.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ayar_yaz(**degisiklik):
    """ayarlar.json'a yalnız verilen anahtarları yazar: dosya yeniden okunur, birleştirilir, güvenli değiştirilir (başka yerden
    yazılmış ayar ezilmez). Değeri None olan anahtar silinir. Güncel ayar sözlüğünü döner; yazılamazsa OSError."""
    d = ayarlari_oku()
    for k, v in degisiklik.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    VERI.mkdir(parents=True, exist_ok=True)
    gecici = AYARLAR.with_name(f"{AYARLAR.name}.{os.getpid()}.tmp")
    gecici.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(gecici, AYARLAR)                 # kendi ayar dosyamız
    return d


def logo_oku():
    """Ofis logosunun (PNG) baytları; logo yoksa, okunamıyorsa ya da PNG değilse None (rapor logosuz düzenlenir)."""
    try:
        b = LOGO.read_bytes()
    except OSError:
        return None
    return b if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) <= LOGO_SINIRI else None


def logo_kaydet(bayt):
    """Seçilen logoyu kullanıcı verisine yazar (güvenli değiştirme). PNG değilse / çok büyükse ValueError."""
    if bayt[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Logo PNG biçiminde olmalı.")
    if len(bayt) > LOGO_SINIRI:
        raise ValueError("Logo dosyası çok büyük (en fazla 2 MB).")
    VERI.mkdir(parents=True, exist_ok=True)
    gecici = LOGO.with_name(f"{LOGO.name}.{os.getpid()}.tmp")
    gecici.write_bytes(bayt)
    os.replace(gecici, LOGO)                    # kendi ayar dosyamız


def adres_duzelt(metin):
    """Kullanıcının yazdığı E-Mükellef adresi -> 'https://<sunucu>' ya da geçersizse None.
    Kabul edilenler: 'ofisim' (-> https://ofisim.emukellef.org), 'ofisim.emukellef.org', 'https://ofisim.emukellef.org/emukellef/…'.
    Yalnız https ve yalnız sunucu adı tutulur (yol, kullanıcı adı, kapı numarası atılır)."""
    s = str(metin or "").strip().lower()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        u = urlparse(s)
        sunucu = u.hostname or ""
    except ValueError:
        return None
    if u.scheme != "https" or not sunucu:
        return None
    if "." not in sunucu:
        sunucu += "." + EMUKELLEF_ALANI
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", sunucu):
        return None
    return "https://" + sunucu


def emukellef_adresi(ayarlar=None):
    """Ayarlardaki E-Mükellef adresi ('https://…') ya da tanımlı/geçerli değilse None."""
    ayarlar = ayarlari_oku() if ayarlar is None else ayarlar
    return adres_duzelt(ayarlar.get(ADRES_ANAHTARI)) if isinstance(ayarlar, dict) else None


def firmalar_json():
    """Firma eşleme dosyasının yolu (dosya henüz olmayabilir: ilk eşleme kaydedilince oluşur)."""
    yerel = KLASOR / "firmalar.json"
    return yerel if yerel.is_file() else VERI / "firmalar.json"


def _var(yol):
    try:
        return Path(yol).is_dir()
    except OSError:
        return False


def kayit_koku(ayarlar=None):
    """Firma klasörlerinin ana klasörü -> (yol | None, durum).
    durum: 'ayar' (ayarlardaki klasör, erişilebilir) · 'varsayilan' (ayar yok, D:\\Claude\\Firmalar var) ·
           'erisilemiyor' (ayarlardaki klasör şu an yok: ağ sürücüsü / USB bağlı olmayabilir; yol yine döner) ·
           'yok' (ayar yok, varsayılan da yok: kullanıcı seçmeli)."""
    ayarlar = ayarlari_oku() if ayarlar is None else ayarlar
    secili = str(ayarlar.get(KOK_ANAHTARI) or "").strip() if isinstance(ayarlar, dict) else ""
    if secili:
        return Path(secili), ("ayar" if _var(secili) else "erisilemiyor")
    if _var(VARSAYILAN_KOK):
        return VARSAYILAN_KOK, "varsayilan"
    return None, "yok"
