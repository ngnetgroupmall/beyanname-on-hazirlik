"""Beyanname Ön Hazırlık — komut aracı: AÇIK olan programa dışarıdan iş verir (Claude'un KDV kontrolünde eksik veriyi indirmesi için).

Program kendi penceresinde, kullanıcı Başlat'a basmış gibi çalışır; oturumlar (E-Mükellef, Luca) programın kendi oturumlarıdır.
Bu araç giriş yapmaz, şifre görmez: giriş gerekiyorsa bunu bildirir, girişi kullanıcı programda yapar.

Kullanım (python -X utf8 komut.py ...):
  durum                                  program açık mı, girişler, çalışan/son komut
  ac                                     program kapalıysa açar (giriş gerekirse kullanıcı yapar)
  sorgular                               sorgu anahtarları ve adları
  firmalar [arama]                       klasörü tanımlı firmalar (aramayla: tüm mükellef listesinde ünvan/VKN)
  calistir --firma <kısa ad|klasör|no> --ay 10 --yil 2026 --sorgu <anahtar> [<anahtar> ...] [--gib-yok] [--pdf] [--bekle 3600]
  toplu --ay 10 --yil 2026 --sorgu <anahtar> [...] [--firma <a> <b> ...] [--gib-yok] [--pdf]
                                         firmalar sırayla (--firma yoksa programdaki 'sık kullanılan firmalar')
  indir --ay 10 --yil 2026 [--firma <a> <b> ...]
                                         verilen KDV1/KDV2/MUHSGK beyanname + tahakkuklarını indirir (Beyanname Takip de güncellenir)
  aktar --bas 18.11.2026 --bit 25.11.2026 [--yukle] [--indir-yok] [--sgk-yok]
                                         E-Mükellef masaüstü programıyla GİB'den E-Mükellef'e aktarma. Varsayılan YALNIZ LİSTELER;
                                         --yukle gerçekten yükler (tüm firmaların yüklenmemiş evrakları) — yalnız kullanıcı onayıyla.
                                         Yüklemede, yüklendiği doğrulanan beyannameler firma klasörlerine indirilir (--indir-yok: indirme).
                                         Mükelleflere gönderim (mail/SMS) hiçbir durumda yapılmaz.
  ozetler --firma <...> --ay 10 --yil 2026    dönemin Kontrol Özeti dosyaları (hangisi tam, hangisi kısmi)
  gunluk [satır]                         program günlüğünün son satırları

Haberleşme: %LOCALAPPDATA%\\KdvOnHazirlik\\komut\\istek.json (bu araç yazar) ve durum.json (program yazar).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

KLASOR = Path(__file__).resolve().parent
if str(KLASOR) not in sys.path:
    sys.path.insert(0, str(KLASOR))
import ayar  # noqa: E402

VERI = ayar.VERI
KOMUT_KLASORU = Path(os.environ.get("KDV_KOMUT_KLASORU") or (VERI / "komut"))      # ortam değişkeni yalnız testler için
ISTEK, DURUM = KOMUT_KLASORU / "istek.json", KOMUT_KLASORU / "durum.json"
FIRMALAR = ayar.kayit_koku()[0] or ayar.VARSAYILAN_KOK      # firma klasörlerinin ana klasörü (programdaki 'Kayıt klasörü')
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
CANLI_SN = 12                                # durum.json bu kadar saniyeden eskiyse program kapalı/donmuş sayılır


def yaz(*a):
    print(*a, flush=True)


def json_oku(yol, varsayilan=None):
    for _ in range(5):                       # program dosyayı değiştirirken okunamayabilir
        try:
            return json.loads(Path(yol).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return varsayilan
        except (OSError, ValueError):
            time.sleep(0.2)
    return varsayilan


def norm(s):
    s = str(s or "").replace("İ", "i").replace("I", "ı").lower()
    return s.translate(str.maketrans("çğıöşüâîû", "cgiosuaiu")).strip()


def durum_oku():
    d = json_oku(DURUM)
    if not isinstance(d, dict):
        return None
    d["canli"] = (time.time() - float(d.get("zaman") or 0)) <= CANLI_SN
    return d


def durum_satiri(d):
    if not d or not d["canli"]:
        return "Program AÇIK DEĞİL (ya da yanıt vermiyor). Açmak için: komut.py ac"
    g = {True: "giriş yapıldı", False: "GİRİŞ YOK", None: "bilinmiyor"}
    s = f"Program açık ({d.get('saat')}). E-Mükellef: {g.get(d.get('emukellef'), '?')}; Luca: {g.get(d.get('luca'), '?')}"
    if d.get("gorunur"):
        s += "; tarayıcı görünür"
    if d.get("pencere"):
        s += f"; AÇIK PENCERE: {d['pencere']} (kullanıcının girişi bekleniyor)"
    s += "; şu an bir işlem ÇALIŞIYOR" if d.get("calisiyor") else "; boşta"
    return s


def firma_kayitlari():
    return [x for x in (json_oku(ayar.firmalar_json(), {}) or {}).get("firmalar", []) if isinstance(x, dict) and x.get("emukellef_id")]


def firma_bul(deger):
    """firmalar.json'da kısa ad / klasör / E-Mükellef no ile tek firma; yoksa hata metni."""
    f = norm(deger)
    uygun = [x for x in firma_kayitlari() if f in (norm(x.get("ad")), norm(x.get("klasor")), str(x.get("emukellef_id")))]
    if len(uygun) != 1:
        return None, (f"Firma bulunamadı: {deger!r}. Klasörü tanımlı firmalar: " + ", ".join(sorted(str(x.get("ad")) for x in firma_kayitlari()))
                      + ". (Listede yoksa kullanıcı programda firmayı seçip bir kez Başlat'a basarak klasörünü tanımlar.)")
    return uygun[0], None


# ---------------------------------------------------------------- alt komutlar
def k_durum(a):
    d = durum_oku()
    yaz(durum_satiri(d))
    k = (d or {}).get("komut")
    if d and d["canli"] and k:
        yaz(f"Son komut: {k.get('id')} — {k.get('firma', '')} {k.get('donem', '')} — {k.get('durum')}" + (f" ({k.get('neden')})" if k.get("neden") else ""))
    if ISTEK.is_file():
        yaz("Bekleyen bir istek var (program boşalınca alır).")
    return 0 if d and d["canli"] else 2


def k_ac(a):
    d = durum_oku()
    if d and d["canli"]:
        yaz("Program zaten açık.")
        yaz(durum_satiri(d))
        return 0
    # başlatıcı: geliştirme bilgisayarında program klasöründe, taşınabilir pakette iki üst klasörde (paket kökü)
    adlar = ("Beyanname On Hazirlik.bat", "KDV On Hazirlik.bat")       # ikincisi: programın eski adıyla kurulmuş paket
    bat = next((k / a for a in adlar for k in (KLASOR, KLASOR.parent.parent) if (k / a).is_file()), None)
    if bat is None:
        yaz("Başlatıcı bulunamadı (Beyanname On Hazirlik.bat); programı elle açın.")
        return 2
    subprocess.Popen(["cmd", "/c", "start", "", "/min", str(bat)], cwd=str(bat.parent), creationflags=0x00000008 | 0x00000200)
    son = time.time() + 60
    while time.time() < son:
        time.sleep(1.5)
        d = durum_oku()
        if d and d["canli"] and d.get("emukellef") is not None:
            break
    yaz(durum_satiri(d))
    if d and d["canli"] and d.get("emukellef") is False:
        yaz("E-Mükellef girişi gerekli: programda giriş penceresi açıldı; girişi KULLANICI yapar (şifre + güvenlik kodu).")
    return 0 if d and d["canli"] else 2


def k_sorgular(a):
    sys.path.insert(0, str(KLASOR))
    from tarayici import LUCA_SORGULARI, SORGULAR
    for k, ad in SORGULAR:
        yaz(f"{k:16} {ad}" + ("   [Luca]" if k in LUCA_SORGULARI else ""))
    return 0


def k_firmalar(a):
    kayit = firma_kayitlari()
    if not a.arama:
        for x in sorted(kayit, key=lambda x: norm(x.get("ad"))):
            yaz(f"{x.get('ad')} | klasör {x.get('klasor')} | E-Mükellef no {x.get('emukellef_id')} | Luca {'tanımlı' if x.get('luca_sirket_id') else 'TANIMSIZ'}")
        return 0
    q = norm(" ".join(a.arama)).split()
    tanimli = {int(x["emukellef_id"]): x for x in kayit}
    liste = (json_oku(VERI / "mukellefler.json", {}) or {}).get("liste", [])
    n = 0
    for m in liste:
        anahtar = norm(f"{m.get('unvan')} {m.get('vkn')} {(tanimli.get(int(m['id'])) or {}).get('ad') or ''}")
        if all(p in anahtar for p in q):
            t = tanimli.get(int(m["id"]))
            yaz(f"{m.get('unvan')} | VKN {m.get('vkn') or '—'} | E-Mükellef no {m['id']} | " + (f"kısa ad {t.get('ad')}, klasör {t.get('klasor')}" if t else "klasör TANIMSIZ"))
            n += 1
            if n >= 15:
                yaz("… (ilk 15 sonuç)")
                break
    if not n:
        yaz("Eşleşen mükellef yok.")
    return 0


def _sonuc_yaz(k):
    b = k.get("bitis") or {}
    s = b.get("sayim") or {}
    ad = {"bitti": "BİTTİ", "hata": "HATA", "reddedildi": "REDDEDİLDİ"}.get(k.get("durum"), str(k.get("durum")))
    yaz(f"SONUÇ: {ad}" + (f" — {k.get('neden')}" if k.get("neden") else "") + (f" — DURDURULDU ({b.get('durdu')})" if b.get("durdu") else ""))
    if s:
        yaz(f"  {s.get('veri', 0)} veri var, {s.get('kayit_yok', 0)} kayıt yok, {s.get('hata', 0)} hata, {s.get('calistirilmadi', 0)} çalıştırılmadı")
    for anahtar, x in (k.get("sorgular") or {}).items() if isinstance(k.get("sorgular"), dict) else []:
        yaz(f"  [{x.get('durum')}] {anahtar}: {x.get('kisa') or ''} — {str(x.get('mesaj') or '')[:600]}")
    for a_ in (b.get("acil") or [])[:5]:
        yaz("  ACİL: " + str(a_)[:300])
    for ad, m in (b.get("hatalar") or [])[:8]:
        yaz(f"  HATA: {ad}: {str(m)[:220]}")
    for etiket, alan in (("Kontrol Özeti", "ozet"), ("Fatura dökümü", "dokum"), ("Dönem klasörü", "donem_klasoru"), ("E-Tebligat klasörü", "tebligat_klasoru")):
        if b.get(alan):
            yaz(f"  {etiket}: {b[alan]}")
    x = k.get("aktar")
    if isinstance(x, dict):
        yaz(f"  Aktarma [{x.get('durum')}]: {x.get('mesaj') or ''} (listelenen {x.get('bulunan', 0)}, yüklenmemiş {x.get('isaretli', 0)})")
        for s_ in (x.get("evraklar") or [])[:60] if x.get("durum") == "liste" else []:
            yaz("    • " + str(s_)[:200])
        for s_ in (x.get("yuklenen") or [])[:60]:
            yaz("    ✓ " + str(s_)[:220])
    if k.get("aktar_not"):
        yaz("  " + str(k["aktar_not"]).replace("\n", "\n  "))
    t = k.get("toplu")
    if isinstance(t, dict):
        yaz(f"  {t.get('baslik')}: {len(t.get('firmalar') or [])} firma")
        for f in t.get("firmalar") or []:
            yaz(f"    • {f.get('ad')}: {f.get('durum')}")
        if t.get("kalan"):
            yaz("  Yapılmayan: " + ", ".join(t["kalan"]))
        if t.get("atlanan"):
            yaz("  Atlanan: " + ", ".join(t["atlanan"]))


def k_calistir(a):
    kayit, hata = firma_bul(a.firma)
    if hata:
        yaz(hata)
        return 2
    if not (1 <= a.ay <= 12 and 2020 <= a.yil <= 2100):
        yaz("Geçersiz dönem.")
        return 2
    istek = {"firma": int(kayit["emukellef_id"]), "ay": a.ay, "yil": a.yil, "sorgular": list(dict.fromkeys(a.sorgu)),
             "earsiv_gib": not a.gib_yok, "pdf": bool(a.pdf)}
    return _istek_birak(istek, f"{kayit.get('ad')} — {AYLAR[a.ay - 1]} {a.yil} — {', '.join(istek['sorgular'])}", a.bekle)


def _firma_idleri(degerler):
    """--firma değerleri -> (E-Mükellef no listesi | 'sik', hata metni)."""
    if not degerler:
        return "sik", None
    idler = []
    for d in degerler:
        kayit, hata = firma_bul(d)
        if hata:
            return None, hata
        idler.append(int(kayit["emukellef_id"]))
    return list(dict.fromkeys(idler)), None


def k_toplu(a):
    if not (1 <= a.ay <= 12 and 2020 <= a.yil <= 2100):
        yaz("Geçersiz dönem.")
        return 2
    idler, hata = _firma_idleri(a.firma)
    if hata:
        yaz(hata)
        return 2
    istek = {"tur": "toplu", "ay": a.ay, "yil": a.yil, "firmalar": idler, "sorgular": list(dict.fromkeys(a.sorgu)),
             "earsiv_gib": not a.gib_yok, "pdf": bool(a.pdf)}
    kapsam = "sık kullanılan firmalar" if idler == "sik" else f"{len(idler)} firma"
    return _istek_birak(istek, f"toplu sorgu — {kapsam} — {AYLAR[a.ay - 1]} {a.yil} — {', '.join(istek['sorgular'])}", a.bekle)


def k_indir(a):
    if not (1 <= a.ay <= 12 and 2020 <= a.yil <= 2100):
        yaz("Geçersiz dönem.")
        return 2
    idler, hata = _firma_idleri(a.firma)
    if hata:
        yaz(hata)
        return 2
    istek = {"tur": "indir", "ay": a.ay, "yil": a.yil, "firmalar": idler}
    kapsam = "sık kullanılan firmalar" if idler == "sik" else f"{len(idler)} firma"
    return _istek_birak(istek, f"verilen beyanname indirme — {kapsam} — {AYLAR[a.ay - 1]} {a.yil}", a.bekle)


def k_aktar(a):
    for t in (a.bas, a.bit):
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", t):
            yaz("Tarihleri gg.aa.yyyy biçiminde verin (ör. 25.11.2026).")
            return 2
    istek = {"tur": "aktar", "tarih_bas": a.bas, "tarih_bit": a.bit, "yukle": bool(a.yukle), "indir": not a.indir_yok, "sgk": not a.sgk_yok}
    return _istek_birak(istek, f"GİB'den E-Mükellef'e aktarma — {a.bas} – {a.bit} — " + ("YÜKLEME" if a.yukle else "yalnız listeleme"), a.bekle)


def _istek_birak(istek, aciklama, bekle):
    """İsteği programa bırakır ve sonucu bekler (programın kendi penceresinde, kullanıcı düğmeye basmış gibi çalışır)."""
    d = durum_oku()
    if not d or not d["canli"]:
        yaz(durum_satiri(d))
        return 2
    kid = time.strftime("%H%M%S-") + uuid.uuid4().hex[:6]
    istek = {"id": kid, **istek}
    KOMUT_KLASORU.mkdir(parents=True, exist_ok=True)
    gecici = ISTEK.with_name(f"istek-{kid}.tmp")
    gecici.write_text(json.dumps(istek, ensure_ascii=False), encoding="utf-8")
    try:
        os.rename(gecici, ISTEK)                 # Windows'ta hedef varsa hata verir: bekleyen isteğin üzerine yazılmaz
    except OSError:
        try:
            gecici.unlink()
        except OSError:
            pass
        yaz("Bekleyen başka bir istek var; önce onun bitmesini bekleyin (komut.py durum).")
        return 2
    yaz(f"İstek bırakıldı ({kid}): {aciklama}")
    bas, alindi, uyarildi = time.time(), False, False
    while time.time() - bas < bekle:
        time.sleep(2)
        d = durum_oku()
        if not d or not d["canli"]:
            yaz("Program kapandı ya da yanıt vermiyor; istek geri çekildi." if _geri_cek(kid) else "Program kapandı ya da yanıt vermiyor.")
            return 2
        k = d.get("komut") or {}
        if k.get("id") != kid:                   # araya başka komut girdiyse kendi sonucumuz geçmiştedir
            k = (d.get("gecmis") or {}).get(kid) or {}
        if k.get("id") != kid:
            if not alindi and not uyarildi and time.time() - bas > 25:
                uyarildi = True
                yaz("Program isteği henüz almadı: " + durum_satiri(d))
            continue
        if not alindi:
            alindi = True
            if k.get("durum") == "calisiyor":
                yaz("Program isteği aldı, çalışıyor…")
        if k.get("durum") in ("bitti", "hata", "reddedildi"):
            _sonuc_yaz(k)
            b = k.get("bitis") or {}
            return 0 if k.get("durum") == "bitti" and not b.get("eksik") and not b.get("durdu") else 1
    yaz(f"Zaman aşımı ({bekle} sn). " + ("İstek alınmamıştı, geri çekildi." if not alindi and _geri_cek(kid) else
                                         "Program hâlâ çalışıyor olabilir: komut.py durum (işi YENİDEN BAŞLATMAYIN)"))
    return 3


def _geri_cek(kid):
    """Yalnız KENDİ bekleyen isteğimizi siler (başka bir isteğe dokunmaz)."""
    try:
        if (json_oku(ISTEK) or {}).get("id") != kid:
            return False
        ISTEK.unlink()
        return True
    except OSError:
        return False


def k_ozetler(a):
    kayit, hata = firma_bul(a.firma)
    if hata:
        yaz(hata)
        return 2
    klasor = FIRMALAR / str(kayit.get("klasor")) / "KDV" / f"{a.yil}-{a.ay:02d}" / "02_Raporlar"
    dosyalar = sorted(klasor.glob("*Kontrol Ozeti*.md"), key=lambda p: p.stat().st_mtime) if klasor.is_dir() else []
    if not dosyalar:
        yaz(f"Kontrol Özeti yok: {klasor}")
        return 1
    yaz(f"{klasor}")
    for p in dosyalar:
        t = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^(\d+) veri var, (\d+) kayıt yok, \*\*(\d+) hata\*\*, (\d+) çalıştırılmadı", t, re.M)
        birikimli = "**BİRİKİMLİ ÖZET:**" in t
        sec = re.search(r"^- (?:Bu çalıştırmada seçilmeyen|Dönemde hiç çalıştırılmamış sorgular) \(bilgisi yok\): (.*)$", t, re.M)
        secilmeyen = len(sec.group(1).split(", ")) if sec else 0
        toplam = sum(map(int, m.groups())) if m else 0
        tur = "BİRİKİMLİ" if birikimli else ("TAM" if not secilmeyen else ("KISMİ" if toplam <= secilmeyen else "çoğu sorgu"))
        yaz(f"  {time.strftime('%d.%m.%Y %H:%M', time.localtime(p.stat().st_mtime))} | {tur}: {toplam} sorgunun sonucu var"
            + (f" ({m.group(3)} hata, {m.group(4)} çalıştırılmadı)" if m else "") + f", {secilmeyen} sorgu yok | {p.name}")
    if dosyalar and "**BİRİKİMLİ ÖZET:**" in dosyalar[-1].read_text(encoding="utf-8", errors="replace"):
        yaz(f"OKUNACAK: {dosyalar[-1].name} (en yeni özet birikimlidir: dönemin tamamını içerir; eskilere bakmaya gerek yok).")
    else:
        yaz("Not: birikimsiz (eski) özetler yalnız o çalıştırmada seçilen sorguları içerir; en kapsamlı olanı temel alın, sonrakileri ek olarak okuyun.")
    return 0


def k_gunluk(a):
    try:
        satirlar = (VERI / "gunluk.txt").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        yaz("Günlük dosyası yok.")
        return 1
    for s in satirlar[-max(1, min(a.satir, 200)):]:
        yaz(s[:400])
    return 0


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Beyanname Ön Hazırlık komut aracı")
    alt = p.add_subparsers(dest="komut", required=True)
    alt.add_parser("durum").set_defaults(f=k_durum)
    alt.add_parser("ac").set_defaults(f=k_ac)
    alt.add_parser("sorgular").set_defaults(f=k_sorgular)
    x = alt.add_parser("firmalar")
    x.add_argument("arama", nargs="*")
    x.set_defaults(f=k_firmalar)
    for ad, f in (("calistir", k_calistir), ("ozetler", k_ozetler)):
        x = alt.add_parser(ad)
        x.add_argument("--firma", required=True)
        x.add_argument("--ay", type=int, required=True)
        x.add_argument("--yil", type=int, required=True)
        if ad == "calistir":
            x.add_argument("--sorgu", nargs="+", required=True)
            x.add_argument("--gib-yok", action="store_true", help="e-fatura/e-arşiv/e-tebligatta GİB'den yeniden sorgulama (hızlı, eksik olabilir)")
            x.add_argument("--pdf", action="store_true")
            x.add_argument("--bekle", type=int, default=3600)
        x.set_defaults(f=f)
    for ad, f in (("toplu", k_toplu), ("indir", k_indir)):
        x = alt.add_parser(ad)
        x.add_argument("--ay", type=int, required=True)
        x.add_argument("--yil", type=int, required=True)
        x.add_argument("--firma", nargs="+", help="kısa ad / klasör / E-Mükellef no; verilmezse sık kullanılan firmalar")
        if ad == "toplu":
            x.add_argument("--sorgu", nargs="+", required=True)
            x.add_argument("--gib-yok", action="store_true")
            x.add_argument("--pdf", action="store_true")
        x.add_argument("--bekle", type=int, default=4 * 3600)
        x.set_defaults(f=f)
    x = alt.add_parser("aktar")
    x.add_argument("--bas", required=True, help="verilme tarihi başlangıcı gg.aa.yyyy")
    x.add_argument("--bit", required=True, help="verilme tarihi bitişi gg.aa.yyyy")
    x.add_argument("--yukle", action="store_true", help="gerçekten yükle (varsayılan: yalnız listele) — yalnız kullanıcı onayıyla")
    x.add_argument("--indir-yok", action="store_true", help="yüklenen beyannameleri firma klasörlerine indirme")
    x.add_argument("--sgk-yok", action="store_true", help="MUHSGK'nın SGK tahakkuk/hizmet belgelerini aktarma")
    x.add_argument("--bekle", type=int, default=2 * 3600)
    x.set_defaults(f=k_aktar)
    x = alt.add_parser("gunluk")
    x.add_argument("satir", nargs="?", type=int, default=25)
    x.set_defaults(f=k_gunluk)
    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
