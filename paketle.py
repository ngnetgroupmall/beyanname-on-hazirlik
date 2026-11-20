"""Beyanname Ön Hazırlık — taşınabilir paket üretici (programın yazıldığı bilgisayarda çalıştırılır).

Çıktı: <çıktı klasörü>\\Beyanname On Hazirlik\\  ve aynı yerde  "Beyanname On Hazirlik (YYYY-AA-GG).zip"
    Beyanname On Hazirlik.bat  çift tıklanır
    OKU BENI.txt
    calisma\\paket.json          paket numarası (güncellemeyle değişmez; guncelle.py bununla "yeni paket gerekli mi" der)
    calisma\\python\\            kurulu Python'un budanmış kopyası + gereken paketler (hedef bilgisayarda Python gerekmez)
    calisma\\program\\           program dosyaları + surum.json (GitHub'dan otomatik güncellenir; bkz. guncelle.py)

Paket VERİSİZDİR: firma/mükellef listesi, firma eşlemesi, giriş bilgileri, tarayıcı profili, günlük, firma dosyaları, notlar GİRMEZ.
Her kullanıcı kendi E-Mükellef adresini ve girişini yapar; mükellef listesi o girişte indirilir.
Hedef bilgisayar: Windows 10/11 64 bit + Google Chrome. Kullanıcı verisi orada %LOCALAPPDATA%\\KdvOnHazirlik altında tutulur;
paket klasörü yeni paketle değiştirilince ayarlar kalır.

Kullanım: python -X utf8 paketle.py [çıktı klasörü]        (varsayılan: <proje>\\dagitim)
"""
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import surum_yaz

KLASOR = Path(__file__).resolve().parent
PAKET_ADI = "Beyanname On Hazirlik"
PAKET_NO = 1            # Python sürümü ya da kitaplıklar (SITE_PAKETLERI) değişince artırılır; surum_yaz.py --paket-en-az ile eşlenir
# site-packages'tan alınacaklar (programın içe aktardıkları ve onların bağımlılıkları)
SITE_PAKETLERI = ["playwright", "greenlet", "openpyxl", "et_xmlfile", "pypdf", "typing_extensions.py"]
LIB_HARIC = {"site-packages", "test", "idlelib", "turtledemo", "ensurepip", "__pycache__"}

BAT = """@echo off
rem Beyanname On Hazirlik - tasinabilir paket. Bu dosyaya cift tiklayin (masaustune kisayolunu da olusturabilirsiniz).
start "" "%~dp0calisma\\python\\pythonw.exe" -X utf8 "%~dp0calisma\\program\\baslat.pyw"
"""

OKU = """BEYANNAME ÖN HAZIRLIK — taşınabilir paket ({tarih}, sürüm {surum})

GEREKENLER
  - Windows 10 ya da 11 (64 bit)
  - Google Chrome kurulu olmalı (program Chrome'u arka planda kullanır)
  - Python kurmaya gerek yoktur; gereken her şey bu klasörün içindedir.

KURULUM
  1. "Beyanname On Hazirlik" klasörünü bilgisayarda kalıcı ve YAZILABİLİR bir yere kopyalayın (ör. Belgeler ya da
     C:\\Beyanname On Hazirlik; "Program Files" olmasın — program kendini güncelleyebilmelidir).
     Zip içinden çalıştırmayın; önce klasörü çıkarın.
  2. Klasördeki "Beyanname On Hazirlik.bat" dosyasına çift tıklayın.
     İsterseniz dosyaya sağ tıklayıp "Gönder > Masaüstü (kısayol oluştur)" ile masaüstüne kısayol koyun.

İLK AÇILIŞ
  Program boş gelir; içinde hiçbir ofisin verisi yoktur. Sırayla:
  1. E-MÜKELLEF ADRESİNİZ sorulur: ofisinizin E-Mükellef'e girdiği adres (ör. ofisadi.emukellef.org).
     Sonradan üstteki "E-Mükellef: …" yazısına tıklayarak değiştirilebilir.
  2. Firma dosyalarının kaydedileceği ANA KLASÖR sorulur (içinde her firmanın kendi klasörü olacak yer; ör. D:\\Firmalar).
     Sonradan üstteki "Kayıt klasörü" yazısından değiştirilebilir.
  3. E-Mükellef giriş penceresi açılır: e-posta, şifre ve resimdeki güvenlik kodu yazılır; ardından e-postaya gelen
     doğrulama kodu girilir. E-posta ve şifre istenirse bu bilgisayarda (Windows hesabına bağlı, şifreli) saklanır.
     Giriş yapılınca MÜKELLEF LİSTENİZ E-Mükellef'ten kendiliğinden indirilir.
  4. Luca sorguları için "Luca girişi" düğmesiyle açılan tarayıcı penceresinden Luca'ya girilir.
  5. Bir firma ilk kez çalıştırılırken klasörü sorulur; önerilen adı onaylamanız yeterlidir (klasör yoksa açılır).

İNDİRİLECEKLERİ KAYDETME
  Sağdaki "İndirilecekler" bölümünde istediğiniz sorguları işaretleyip "Bu seçimi kaydet" derseniz program bundan sonra o
  seçimle açılır. İşaretleri o an için yine değiştirebilirsiniz; "Kayıtlı seçimimi getir" kayda döner, "Kaydı sil" kaldırır.

GÜNCELLEME
  Program her açılışta internetten (GitHub) yeni sürüm var mı diye bakar; varsa kendiliğinden indirip açılır. Bir şey
  yapmanız gerekmez. İnternet yoksa eldeki sürümle açılır. Yeni sürüm açılamazsa program önceki sürüme döner.
  Nadiren (Python ya da kitaplıklar değiştiğinde) program "yeni paketi indirin" der: yeni paketin klasörünü eskisinin
  yerine kopyalamak yeterlidir; ayarlar kalır.

BİLMENİZ GEREKENLER
  - Ayarlar, giriş bilgileri ve günlük bu bilgisayarda %LOCALAPPDATA%\\KdvOnHazirlik klasöründe tutulur; başka bilgisayara
    taşınmaz (her bilgisayarda bir kez giriş yapılır). Bu bilgiler hiçbir yere gönderilmez.
  - Program açılmazsa: %LOCALAPPDATA%\\KdvOnHazirlik\\baslatma_hatasi.txt dosyasına bakın.
  - Kaldırma: paket klasörünü ve %LOCALAPPDATA%\\KdvOnHazirlik klasörünü silin.
"""

# pakete girecek program dosyalarında bulunmaması gereken ofise özel izler (yanlışlıkla veri sızmasın diye son denetim)
YASAK_IZ = re.compile(r"[a-z0-9-]+\.emukellef\.org", re.I)
IZINLI_IZ = {"ofisadi.emukellef.org", "ofisim.emukellef.org"}


def veri_denetimi(dosyalar):
    """Program dosyalarında ofise özel adres ve geliştirme bilgisayarındaki firma adları/VKN'leri geçiyor mu -> bulgular listesi."""
    terimler = set()
    try:
        for f in json.loads((KLASOR / "firmalar.json").read_text(encoding="utf-8")).get("firmalar", []):
            terimler |= {str(f.get(k) or "").strip().casefold() for k in ("ad", "klasor", "unvan", "vkn")}
    except (OSError, ValueError):
        pass
    terimler = {t for t in terimler if len(t) >= 5}
    bulgular = []
    for p in dosyalar:
        for no, satir in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), 1):
            kucuk = satir.casefold()
            if any(m.group(0).lower() not in IZINLI_IZ for m in YASAK_IZ.finditer(satir)) or any(t in kucuk for t in terimler):
                bulgular.append(f"{p.name}:{no}: {satir.strip()[:90]}")
    return bulgular


def _kopyala(kaynak: Path, hedef: Path, haric=frozenset()):
    """Klasörü kopyalar; haric: atlanacak klasör adları. __pycache__ her zaman atlanır."""
    def atla(_, adlar):
        return [a for a in adlar if a in haric or a == "__pycache__"]
    shutil.copytree(kaynak, hedef, ignore=atla)


def paketle(cikti: Path):
    py = Path(sys.base_prefix)
    surum = f"{sys.version_info.major}{sys.version_info.minor}"
    for ad in ("python.exe", "pythonw.exe", f"python{surum}.dll"):     # kaynak önce doğrulanır (eski paket silinmeden)
        if not (py / ad).is_file():
            raise SystemExit(f"Python kurulumunda {ad} bulunamadı: {py}")
    dosyalar = surum_yaz.program_dosyalari()        # paket ve otomatik güncelleme aynı dosya listesini kullanır
    if not {"baslat.pyw", "onhazirlik.py", "guncelle.py"} <= {p.name for p in dosyalar}:
        raise SystemExit("Program dosyaları eksik (baslat.pyw / onhazirlik.py / guncelle.py).")
    bulgular = veri_denetimi(dosyalar)
    if bulgular:
        raise SystemExit("Program dosyalarında ofise özel veri var; paket üretilmedi:\n  " + "\n  ".join(bulgular[:20]))
    manifest, _ = surum_yaz.manifest_yaz()          # surum.json güncel değilse yenilenir (sürüm numarası artar)
    cikti.mkdir(parents=True, exist_ok=True)
    kok = cikti / PAKET_ADI
    # paket geçici bir klasörde kurulur; ancak eksiksiz hazır olunca yerine alınır (hata olursa eski paket ve zip kalır)
    gecici_kok = cikti / (PAKET_ADI + f".yapim.{os.getpid()}")
    if gecici_kok.exists():
        shutil.rmtree(gecici_kok)
    hedef_py, hedef_pr = gecici_kok / "calisma" / "python", gecici_kok / "calisma" / "program"
    hedef_py.mkdir(parents=True)
    hedef_pr.mkdir(parents=True)
    # ---- Python çalışma zamanı
    for ad in ("python.exe", "pythonw.exe", "python3.dll", f"python{surum}.dll", "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        if (py / ad).is_file():
            shutil.copy2(py / ad, hedef_py / ad)
    _kopyala(py / "DLLs", hedef_py / "DLLs")
    _kopyala(py / "tcl", hedef_py / "tcl", haric={"demos"})
    _kopyala(py / "Lib", hedef_py / "Lib", haric=LIB_HARIC)
    sp, hedef_sp = py / "Lib" / "site-packages", hedef_py / "Lib" / "site-packages"
    hedef_sp.mkdir()
    for ad in SITE_PAKETLERI:
        k = sp / ad
        if k.is_dir():
            _kopyala(k, hedef_sp / ad, haric={"tests"})
        elif k.is_file():
            shutil.copy2(k, hedef_sp / ad)
        else:
            raise SystemExit(f"Gerekli paket kurulu değil: {ad} ({sp})")
        for bilgi in sp.glob(f"{ad.removesuffix('.py')}-*.dist-info"):       # lisans / sürüm bilgisi
            _kopyala(bilgi, hedef_sp / bilgi.name)
    # yalıtılmış kip: hedef bilgisayardaki PYTHONPATH, kayıt defteri ve kullanıcı site-packages'ı etkilemez.
    # 'import site' satırı bilerek YOK: site modülü hiç yüklenmez (kullanıcı site-packages'ı ve .pth dosyaları işlenmez).
    (hedef_py / f"python{surum}._pth").write_text("Lib\nLib\\site-packages\nDLLs\n..\\program\n", encoding="ascii")
    # ---- program (yalnız kod + sürüm dosyası; veri yok)
    for p in dosyalar:
        shutil.copy2(p, hedef_pr / p.name)
    shutil.copy2(surum_yaz.MANIFEST, hedef_pr / "surum.json")
    tarih = datetime.now()
    # paket bilgisi program klasörünün DIŞINDA: otomatik güncelleme buna dokunmaz
    (gecici_kok / "calisma" / "paket.json").write_text(json.dumps(
        {"paket": PAKET_NO, "uretim": f"{tarih:%d.%m.%Y %H:%M}", "python": sys.version.split()[0], "ilk_surum": manifest["surum"]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    # ---- kök dosyaları (geçici klasöre)
    (gecici_kok / f"{PAKET_ADI}.bat").write_text(BAT.replace("\n", "\r\n"), encoding="ascii", newline="")
    (gecici_kok / "OKU BENI.txt").write_text(OKU.format(tarih=f"{tarih:%d.%m.%Y}", surum=manifest["surum"]).replace("\n", "\r\n"),
                                             encoding="utf-8-sig", newline="")
    # ---- zip (önce .tmp'ye; hazır olunca yerine alınır, böylece hata eski zip'i bozmaz)
    zip_yolu = cikti / f"{PAKET_ADI} ({tarih:%Y-%m-%d}).zip"
    zip_gecici = zip_yolu.with_name(zip_yolu.name + f".{os.getpid()}.tmp")
    with zipfile.ZipFile(zip_gecici, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in sorted(gecici_kok.rglob("*")):
            if p.is_file():
                z.write(p, Path(PAKET_ADI) / p.relative_to(gecici_kok))
    adet = sum(1 for p in gecici_kok.rglob("*") if p.is_file())
    boyut = sum(p.stat().st_size for p in gecici_kok.rglob("*") if p.is_file())
    # ---- her şey hazır: eski paketi/zip'i şimdi değiştir
    if kok.exists():                            # yalnız bu betiğin ürettiği klasör değiştirilir
        if not (kok / "calisma" / "paket.json").is_file():
            shutil.rmtree(gecici_kok, ignore_errors=True)
            zip_gecici.unlink(missing_ok=True)
            raise SystemExit(f"{kok} var ama bu betiğin ürettiği bir paket değil; dokunulmadı. Başka çıktı klasörü verin.")
        shutil.rmtree(kok)
    os.rename(gecici_kok, kok)
    os.replace(zip_gecici, zip_yolu)
    print(f"Paket: {kok}  ({adet} dosya, {boyut / 1e6:.0f} MB)")
    print(f"Zip:   {zip_yolu}  ({zip_yolu.stat().st_size / 1e6:.0f} MB)")
    print(f"Sürüm: {manifest['surum']} (paket no {PAKET_NO}) — paket verisizdir (firma/mükellef bilgisi içermez)")
    return kok, zip_yolu


if __name__ == "__main__":
    paketle(Path(sys.argv[1]) if len(sys.argv) > 1 else KLASOR.parent / "dagitim")
