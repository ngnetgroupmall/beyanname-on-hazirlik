"""Birikimli sonuç: dönemin TÜM çalıştırmalarından her sorgunun en güncel başarılı sonucunu bir araya getirir.

Kullanıcı kararı (19.11.2026): kısmi çalıştırmalar (ör. komut aracıyla tek sorgu) ayrı kısmi özetler üretmesin; Kontrol Özeti ve
Fatura Dökümü her seferinde dönemin tamamını göstersin. Ham sonuç dosyaları (… E-Mukellef Sorgulari ….json) çalıştırma başına
yazılmaya devam eder ve değiştirilmez; birikim, özet yazılırken bu dosyalardan kurulur.
"""
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import cikti
import dokum

BASARILI = ("veri", "kayit_yok")
DURUMLAR = BASARILI + ("hata", "calistirilmadi")
FATURA_SORGU_ADI = {("gelen", "efatura"): "Gelen E-Faturalar", ("giden", "efatura"): "Giden E-Faturalar",
                    ("gelen", "earsiv"): "Gelen E-Arşiv Faturalar", ("giden", "earsiv"): "Giden E-Arşiv Faturalar"}


def zaman(s):
    """Sonucun sorgu zamanı (datetime); okunamıyorsa en eski sayılır."""
    try:
        return datetime.strptime(str(s.get("sorgu_zamani") or ""), "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return datetime.min


def _normal(s):
    """Dosyadan okunan sonucu güvenli biçime getirir (eksik alanlar tamamlanır, bozuk satırlar atılır); geçersizse None."""
    if not isinstance(s, dict) or not isinstance(s.get("sorgu"), str) or not s["sorgu"].strip() or s.get("durum") not in DURUMLAR:
        return None
    kolonlar = s.get("kolonlar") if isinstance(s.get("kolonlar"), list) else []
    satirlar = [r for r in (s.get("satirlar") if isinstance(s.get("satirlar"), list) else []) if isinstance(r, list)]
    toplam = s.get("toplam")
    return {"sorgu": s["sorgu"], "kapsam": str(s.get("kapsam") or ""), "durum": s["durum"], "mesaj": str(s.get("mesaj") or ""),
            "kolonlar": [str(k) for k in kolonlar], "satirlar": satirlar,
            "toplam": toplam if isinstance(toplam, (int, float, list)) and not isinstance(toplam, bool) else None,
            "sorgu_zamani": str(s.get("sorgu_zamani") or "")}


def onceki_sonuclar(klasor, yil, ay, gunluk=lambda m: None, gecerli=None):
    """Dönem klasöründeki kayıtlı sonuç dosyalarının (JSON) içindeki tüm sonuçlar. Klasör açmaz; bozuk dosya/sonuç atlanır."""
    hedef = cikti.firma_klasoru(klasor) / "KDV" / f"{yil}-{ay:02d}" / "01_Kaynaklar" / "E-Mukellef"
    tum = []
    if not hedef.is_dir():
        return tum
    for p in sorted(hedef.glob("*E-Mukellef Sorgulari*.json")):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(j, dict) or j.get("yil") != yil or j.get("ay") != ay or not isinstance(j.get("sonuclar"), list):
                continue
            tum += [n for n in map(_normal, j["sonuclar"]) if n is not None]
            if gecerli is not None:                 # okunabilen (bu döneme ait) sonuç dosyaları
                gecerli.append(p.name)
        except Exception:                           # bozuk dosya birikimi durdurmaz
            gunluk(f"UYARI: eski sonuç dosyası okunamadı, birikime katılmadı: {p.name}")
    return tum


def birlestir(simdiki, oncekiler, sira):
    """simdiki: bu çalıştırmanın sonuçları; oncekiler: kayıtlı dosyalardaki sonuçlar (bu çalıştırmanın dosyası da okunmuş olabilir).
    Her sorgu için en güncel BAŞARILI (veri / kayıt yok) sonuç seçilir; hiç başarılı yoksa en son sonuç.
    Dönen kopyalarda ek alanlar: bu_calistirma (bool); seçilenden daha yeni başarısız bir deneme varsa son_deneme {zaman, durum,
    mesaj}. Önceki çalıştırmadan taşınan sonucun kapsamındaki 'bugün' ifadesi sorgu tarihine çevrilir. sira: gösterim sırası."""
    aday = {}                                       # sorgu -> {(zaman, durum): (sonuç, bu çalıştırmadan mı)}
    for s in oncekiler:
        aday.setdefault(s["sorgu"], {})[(s.get("sorgu_zamani"), s["durum"])] = (s, False)
    for s in simdiki:                               # bu çalıştırmanın sonucu, dosyadaki eş kaydının yerine geçer
        aday.setdefault(s["sorgu"], {})[(s.get("sorgu_zamani"), s["durum"])] = (s, True)
    sonuc = []
    for kayitlar in aday.values():
        # aynı zaman damgasında bu çalıştırmanın sonucu daha yeni sayılır
        liste = sorted(kayitlar.values(), key=lambda k: (zaman(k[0]), k[1]))
        son = liste[-1]
        basarili = next((k for k in reversed(liste) if k[0]["durum"] in BASARILI), None)
        kaynak, simdi_mi = basarili or son
        secilen = dict(kaynak)
        secilen["bu_calistirma"] = simdi_mi
        if basarili is not None and son is not basarili:
            secilen["son_deneme"] = {"zaman": son[0].get("sorgu_zamani") or "", "durum": son[0]["durum"], "mesaj": str(son[0].get("mesaj") or "")[:200]}
        if not simdi_mi:                            # eski sonuç yeni özette "bugün itibarıyla" görünmesin
            gun = str(secilen.get("sorgu_zamani") or "")[:10]
            if gun:
                secilen["kapsam"] = re.sub(r"\bbugün\b", gun, str(secilen.get("kapsam") or ""), flags=re.I)
        sonuc.append(secilen)
    yer = {ad: i for i, ad in enumerate(sira)}
    sonuc.sort(key=lambda s: (yer.get(s["sorgu"], len(yer)), s["sorgu"]))
    return sonuc


def donem_dosyalari(klasor, yil, ay):
    """Arayüzdeki 'önceki indirme' notu için dönemin kayıt dosyaları (JSON çözülmez; yalnız OKUR, klasör açmaz).
    Döner: {klasor_var, donem_var, donem_klasoru, jsonlar [Path], imza (dosya ad/zaman/boyut; okunamazsa None), ozet (en yeni Kontrol Özeti)}"""
    d = {"klasor_var": False, "donem_var": False, "donem_klasoru": "", "jsonlar": [], "imza": None, "ozet": "", "beyanname": []}
    try:
        firma = cikti.firma_klasoru(klasor)
    except (ValueError, OSError):
        return d
    donem = firma / "KDV" / f"{yil}-{ay:02d}"
    d.update(klasor_var=True, donem_klasoru=str(donem), donem_var=donem.is_dir(), beyanname=cikti.verilen_beyannameler(klasor, yil, ay))
    if not d["donem_var"]:
        return d
    emk = donem / "01_Kaynaklar" / "E-Mukellef"
    d["jsonlar"] = sorted(emk.glob("*E-Mukellef Sorgulari*.json")) if emk.is_dir() else []
    try:
        d["imza"] = tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in d["jsonlar"])
    except OSError:
        d["imza"] = None
    rapor = donem / "02_Raporlar"
    ozetler = []
    for p in (rapor.glob("*Kontrol Ozeti*.md") if rapor.is_dir() else []):
        try:
            ozetler.append((p.stat().st_mtime, str(p)))
        except OSError:
            continue
    d["ozet"] = max(ozetler)[1] if ozetler else ""
    return d


def donem_sonuclari(klasor, yil, ay, sira):
    """Dönemin kayıtlı sonuçları birleşik (bkz. birlestir) -> (sonuçlar, en son sorgu zamanı 'GG.AA.YYYY SS:DD' ya da '',
    okunabilen sonuç dosyası sayısı)."""
    gecerli = []
    onceki = onceki_sonuclar(klasor, yil, ay, gecerli=gecerli)
    en_son = max((zaman(s) for s in onceki), default=datetime.min)
    return birlestir([], onceki, sira), (en_son.strftime("%d.%m.%Y %H:%M") if en_son != datetime.min else ""), len(gecerli)


def dosya_sayisi(klasor, sinir=9999):
    """Klasördeki (alt klasörler dahil) dosya sayısı; sinir'de durur."""
    sayi = 0
    try:
        for p in Path(klasor).rglob("*"):
            if p.is_file():
                sayi += 1
                if sayi >= sinir:
                    break
    except OSError:
        pass
    return sayi


def _hucre(satir, kol, ad):
    i = kol.get(ad)
    return satir[i] if i is not None and i < len(satir) else ""


def _tutar(v):
    if isinstance(v, bool) or v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def kayitlar_diskten(firma_ad, firma_vkn, klasor, yil, ay, yon, tur, sonuc):
    """Önceki bir çalıştırmadan taşınan fatura sorgusunun döküm kayıtlarını, sonuç satırları + diskteki XML'lerden yeniden kurar
    (satır sırasıyla). Aynı belgenin birden fazla XML sürümü varsa ödenecek tutarı satırla tutan, yoksa en yenisi alınır;
    XML'i bulunamayan/uymayan fatura liste değerleriyle 'XML yok' olarak girer."""
    hedef = (cikti.firma_klasoru(klasor) / "KDV" / f"{yil}-{ay:02d}" / "01_Kaynaklar" / "E-Mukellef" / cikti.TUR_KLASOR[(yon, tur)])
    kol = {ad: i for i, ad in enumerate(sonuc.get("kolonlar") or [])}
    durum_var = "PDF" in kol and "XML" in kol
    kayitlar = []
    for r in sonuc.get("satirlar") or []:
        no = str(_hucre(r, kol, "Belge no") or "").strip()
        if not no:
            continue
        liste = {"tarih": _hucre(r, kol, "Düzenleme tarihi"), "vergi_haric": _hucre(r, kol, "Vergi hariç"), "odenecek": _hucre(r, kol, "Ödenecek"),
                 "satici_vkn": _hucre(r, kol, "Satıcı VKN"), "satici": _hucre(r, kol, "Satıcı"), "alici_vkn": _hucre(r, kol, "Alıcı VKN"),
                 "alici": _hucre(r, kol, "Alıcı"), "tip": _hucre(r, kol, "Tip"), "senaryo": _hucre(r, kol, "Senaryo"), "para": _hucre(r, kol, "Para birimi")}
        karsi = "".join(c for c in str(liste["satici_vkn" if yon == "gelen" else "alici_vkn"] or "") if c.isdigit())
        beklenen = _tutar(liste["odenecek"])
        uygunlar = []                               # (tutar tutuyor mu, değişme zamanı, bayt, ad)
        if hedef.is_dir():
            for ax, _ap in cikti.mevcut_faturalar(hedef, firma_ad, yil, ay, yon, tur, no):
                if ax is None:
                    continue
                try:
                    b = ax.read_bytes()
                    if not dokum.xml_dogrula(b, no, firma_vkn or None, yon)[0]:
                        continue
                    x = dokum.xml_coz(b)
                    if karsi and (x["satici_vkn"] if yon == "gelen" else x["alici_vkn"]) != karsi:
                        continue
                    tutar = x.get("odenecek")
                    tutuyor = beklenen is not None and tutar is not None and abs(Decimal(str(tutar)) - beklenen) <= Decimal("0.01")
                    uygunlar.append((tutuyor, ax.stat().st_mtime, b, ax.name))
                except Exception:
                    continue
        xml, dosya = None, ""
        if uygunlar:
            _, _, xml, dosya = max(uygunlar, key=lambda u: (u[0], u[1]))
        durum = f"PDF {_hucre(r, kol, 'PDF')}, XML {_hucre(r, kol, 'XML')}" if durum_var else ""
        kayitlar.append(dokum.kayit_olustur(yon, tur, no, xml, liste, dosya, durum=durum))
    return kayitlar


def dokum_kayitlari(firma, klasor, yil, ay, birlesik, simdiki_kayitlar):
    """Birikimli döküm kayıtları: bu çalıştırmada yenilenen fatura türleri için eldeki kayıtlar, taşınanlar için diskten.
    Başarılı sonucu olmayan ve hiç faturası gelmemiş tür (hata/çalıştırılmadı) 'çalıştırılmış' sayılmaz: dökümde 'fatura yok'
    görünmesin. Döner: (kayıtlar, sonucu olan türler {(yön, tür)})"""
    tum, turler = [], set()
    for (yon, tur), ad in FATURA_SORGU_ADI.items():
        s = next((x for x in birlesik if x["sorgu"] == ad), None)
        if s is None:
            continue
        if s.get("bu_calistirma"):
            k = [x for x in simdiki_kayitlar if x.get("yon") == yon and x.get("tur") == tur]
        else:
            k = kayitlar_diskten(firma["ad"], firma.get("vkn"), klasor, yil, ay, yon, tur, s)
        if s["durum"] in BASARILI or k:
            turler.add((yon, tur))
            tum += k
    return tum, turler
