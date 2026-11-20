"""Beyanname Ön Hazırlık — GitHub'dan otomatik güncelleme (yalnız taşınabilir pakette çalışır; yalnız standart kitaplık).

Depo (herkese açık) = program dosyaları. Kökünde surum.json bulunur (surum_yaz.py üretir):
    {"surum": "2026.11.20.1", "paket_en_az": 1, "dosyalar": {"onhazirlik.py": "<sha256>", ...}}

Açılışta baslat.pyw şu sırayla çağırır:  kilit_al() -> baslangic() -> (gerekirse süreç yeniden başlar) -> program -> onayla()
  1. Yarım kalmış / onaylanmamış güncelleme varsa (günlük dosyası duruyorsa) eski dosyalar geri yüklenir, o sürüm atlanır.
  2. Depodaki surum.json okunur (kısa zaman aşımı; çevrimdışıysa sessizce geçilir). Sürüm yereldekinden büyükse sha256'sı farklı
     dosyalar program klasöründeki _yeni\\ altına indirilir; sha256 ve sözdizimi doğrulanır. Hepsi tamamsa eskiler _onceki\\ altına
     yedeklenir, yeniler yerine konur, günlük "denemede" olarak kalır.
  3. Program açılıp penceresi kurulunca onayla() günlüğü siler. Açılamazsa baslat.pyw geri_al() ile eski sürüme döner.
Paketin kendisi (Python, kitaplıklar) güncellenmez: surum.json'daki paket_en_az yereldeki paket numarasından (calisma\\paket.json)
büyükse güncelleme uygulanmaz, kullanıcıya yeni paketi indirmesi söylenir.

Geliştirme bilgisayarında (calisma\\paket.json yok) hiçbir şey yapmaz.
"""
import hashlib
import json
import msvcrt
import os
import re
import shutil
import urllib.request
from pathlib import Path

DEPO = "ngnetgroupmall/beyanname-on-hazirlik"                                    # GitHub deposu: "kullanici/depo" (boşsa güncelleme denetlenmez)
DAL = "main"
# kurulum paketi (zip) deponun ayrı "paket" dalında durur; yeni paket gerektiğinde kullanıcıya bu adres verilir
PAKET_ADRESI = f"https://github.com/{DEPO}/raw/paket/Beyanname-On-Hazirlik.zip"
KLASOR = Path(__file__).resolve().parent
VERI = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik"
PAKET = KLASOR.parent / "paket.json"         # yalnız taşınabilir pakette bulunur; güncellemeyle DEĞİŞMEZ
MANIFEST = KLASOR / "surum.json"
GUNLUK = KLASOR / "guncelleme_gunlugu.json"  # varsa: güncelleme yarım kaldı ya da yeni sürüm henüz açılıp onaylanmadı
YENI, ONCEKI = KLASOR / "_yeni", KLASOR / "_onceki"      # program klasörünün içinde: os.replace aynı diskte kalsın
ATLANAN = VERI / "atlanan_surum.txt"
DOSYA_ADI = re.compile(r"[A-Za-z0-9_]+\.pyw?")
SURUM_KALIBI = re.compile(r"\d+(\.\d+){1,5}")
ZAMAN_ASIMI = 6                              # saniye (surum.json); dosya indirmede 30
EN_BUYUK = 5_000_000                         # tek dosya için üst sınır (bayt)


def kilit_al():
    """Programın ikinci kopyasını engeller (aynı Chrome profili iki kez açılamaz; güncelleme de tek kopyada yapılır).
    Kilit dosyasını döner (süreç boyunca açık tutulur); başka kopya açıksa None."""
    VERI.mkdir(parents=True, exist_ok=True)
    f = open(VERI / "program.lock", "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return None
    return f


def paket_mi():
    return PAKET.is_file()


def _oku(yol):
    try:
        d = json.loads(Path(yol).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _yaz(yol, veri):
    yol = Path(yol)
    gecici = yol.with_name(yol.name + ".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(gecici, yol)


def surum_sayi(s):
    """'2026.11.20.2' -> (2026, 11, 20, 2); geçersizse ()."""
    s = str(s or "")
    return tuple(int(x) for x in s.split(".")) if SURUM_KALIBI.fullmatch(s) else ()


def yerel_surum():
    return str(_oku(MANIFEST).get("surum") or "")


def paket_no():
    try:
        return int(_oku(PAKET).get("paket") or 0)
    except (TypeError, ValueError):
        return 0


def _sha(yol):
    try:
        return hashlib.sha256(Path(yol).read_bytes()).hexdigest()
    except OSError:
        return ""


def _manifest_gecerli(m):
    d = m.get("dosyalar")
    return (bool(surum_sayi(m.get("surum"))) and isinstance(d, dict) and bool(d)
            and all(isinstance(a, str) and DOSYA_ADI.fullmatch(a) and isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)
                    for a, h in d.items())
            and isinstance(m.get("paket_en_az", 1), int))


def _indir(ad, zaman_asimi):
    adres = f"https://raw.githubusercontent.com/{DEPO}/{DAL}/{ad}"
    istek = urllib.request.Request(adres, headers={"User-Agent": "beyanname-on-hazirlik", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(istek, timeout=zaman_asimi) as r:
        veri = r.read(EN_BUYUK + 1)
    if len(veri) > EN_BUYUK:
        raise ValueError(f"{ad}: beklenenden büyük")
    return veri


def _temizle(klasor):
    shutil.rmtree(klasor, ignore_errors=True)


def bekleyen_var():
    return GUNLUK.is_file()


def geri_al():
    """Günlükteki güncellemeyi geri alır: yedekteki dosyalar yerine konur, yeni eklenenler silinir, sürüm 'atlanan' diye işaretlenir.
    Geri alınan sürümü (metin) döner; günlük yoksa None."""
    g = _oku(GUNLUK)
    if not GUNLUK.is_file():
        return None
    for ad in g.get("eklenen") or []:
        if isinstance(ad, str) and DOSYA_ADI.fullmatch(ad):
            try:
                (KLASOR / ad).unlink()
            except OSError:
                pass
    for ad in (g.get("degisen") or []) + ["surum.json"]:
        if not isinstance(ad, str) or not (DOSYA_ADI.fullmatch(ad) or ad == "surum.json"):
            continue
        yedek = ONCEKI / ad                     # yedek yoksa dosya zaten eski hâlindedir (yedekleme, değiştirmeden önce biter)
        if yedek.is_file():
            try:
                shutil.copy2(yedek, KLASOR / (ad + ".geri"))
                os.replace(KLASOR / (ad + ".geri"), KLASOR / ad)
            except OSError:
                pass
    try:
        VERI.mkdir(parents=True, exist_ok=True)
        ATLANAN.write_text(str(g.get("yeni") or ""), encoding="utf-8")
    except OSError:
        pass
    try:
        GUNLUK.unlink()
    except OSError:
        pass
    _temizle(YENI)
    return str(g.get("yeni") or "?")


def onayla():
    """Yeni sürüm açıldı: günlük silinir (yedek bir sonraki güncellemeye kadar kalır)."""
    try:
        GUNLUK.unlink()
    except OSError:
        pass


def _uygula(uzak, veriler):
    """veriler: ad -> bayt (doğrulanmış). Yedekle, yerine koy, sürümü yaz. Hata olursa geri alır ve yükseltir."""
    _temizle(YENI)
    _temizle(ONCEKI)
    YENI.mkdir()
    ONCEKI.mkdir()
    for ad, veri in veriler.items():
        (YENI / ad).write_bytes(veri)
    degisen = [ad for ad in veriler if (KLASOR / ad).is_file()]
    eklenen = [ad for ad in veriler if ad not in degisen]
    for ad in degisen:
        shutil.copy2(KLASOR / ad, ONCEKI / ad)
    if MANIFEST.is_file():
        shutil.copy2(MANIFEST, ONCEKI / "surum.json")
    _yaz(GUNLUK, {"durum": "uygulaniyor", "eski": yerel_surum(), "yeni": uzak["surum"], "degisen": degisen, "eklenen": eklenen})
    try:
        for ad in veriler:
            os.replace(YENI / ad, KLASOR / ad)
        _yaz(MANIFEST, uzak)
        _yaz(GUNLUK, {"durum": "denemede", "eski": _oku(ONCEKI / "surum.json").get("surum", ""), "yeni": uzak["surum"],
                      "degisen": degisen, "eklenen": eklenen})
    except Exception:
        geri_al()
        raise
    _temizle(YENI)


def baslangic():
    """Açılış denetimi. Hiç hata yükseltmez. Dönen sözlük:
        {"yeniden": bool   -> dosyalar değişti; süreç yeniden başlatılmalı,
         "mesaj": str      -> program günlüğüne yazılacak bilgi (boş olabilir),
         "uyari": str      -> kullanıcıya gösterilecek uyarı (yeni paket gerekli vb.; boş olabilir)}"""
    s = {"yeniden": False, "mesaj": "", "uyari": ""}
    try:
        if not paket_mi():
            return s
        if bekleyen_var():                          # önceki açılış yarım kaldı ya da yeni sürüm açılamadan kapandı
            geri = geri_al()
            s.update(yeniden=True, mesaj=f"Güncelleme ({geri}) tamamlanamadı; önceki sürüme dönüldü.")
            return s
        if not DEPO:
            return s
        uzak = json.loads(_indir("surum.json", ZAMAN_ASIMI).decode("utf-8"))
        if not isinstance(uzak, dict) or not _manifest_gecerli(uzak):
            return s
        yerel = yerel_surum()
        if surum_sayi(uzak["surum"]) <= surum_sayi(yerel):
            return s
        try:
            if ATLANAN.read_text(encoding="utf-8").strip() == uzak["surum"]:
                return s                            # bu sürüm bu bilgisayarda açılamamıştı; bir sonrakini bekle
        except OSError:
            pass
        if int(uzak.get("paket_en_az", 1)) > paket_no():
            s["uyari"] = (f"Programın yeni sürümü ({uzak['surum']}) yeni kurulum paketini gerektiriyor; bu sürüm kendiliğinden "
                          f"güncellenemez. Yeni paketi indirin: {PAKET_ADRESI}")
            return s
        veriler = {}
        for ad, ozet in uzak["dosyalar"].items():
            if _sha(KLASOR / ad) == ozet:
                continue
            veri = _indir(ad, 30)
            if hashlib.sha256(veri).hexdigest() != ozet:
                return s                            # depo yarım güncellenmiş / önbellek eski: bir sonraki açılışta yeniden denenir
            compile(veri, ad, "exec")               # sözdizimi bozuk dosya yerine konmaz (SyntaxError -> dışarıdaki except)
            veriler[ad] = veri
        if not veriler:                             # dosyalar zaten aynı: yalnız sürüm bilgisi yenilenir
            _yaz(MANIFEST, uzak)
            return s
        _uygula(uzak, veriler)
        s.update(yeniden=True, mesaj=f"Program güncellendi: {yerel or '?'} → {uzak['surum']}")
    except Exception:
        pass                                        # çevrimdışı, depo yok, disk yazılamıyor…: eldeki sürümle açılır
    return s
