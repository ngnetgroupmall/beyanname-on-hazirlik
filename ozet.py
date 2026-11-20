"""Kontrol özeti (Markdown): KDV kontrolüne başlarken önce bu okunur, ham dosyalara yalnız gerekirse inilir.

Rakamlar sorgu sonuçlarından, fatura XML'lerinden (döküm kayıtları) ve indirilen beyanname/tahakkuk PDF'lerinden gelir.
PDF'ten okunan rakamlar "PDF'ten okundu" diye işaretlenir; çapraz kontrolü tutmayan değer "PDF'ten kontrol edin" notu alır.
"""
import io
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
TUTAR = r"(-?\d{1,3}(?:\.\d{3})*,\d{2})"
EN_FAZLA = 15


# ================================================================ yardımcılar
def _tl(v):
    """Sayı -> '1.234,56' (yoksa '—')."""
    if v is None or v == "":
        return "—"
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"))
    except Exception:
        return str(v)
    s = f"{d:,.2f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _d(metin):
    """'1.234,56' -> Decimal"""
    return Decimal(metin.replace(".", "").replace(",", "."))


def _kolon(sonuc, ad):
    try:
        return sonuc["kolonlar"].index(ad)
    except (ValueError, KeyError):
        return None


def _sonuc_bul(sonuclar, sorgu_adi):
    return next((s for s in sonuclar if s.get("sorgu") == sorgu_adi), None)


def _hucre(s):
    return str(s if s is not None else "").replace("|", "/").replace("\n", " ").strip()


# ================================================================ beyanname PDF'leri
def _pdf_metni(yol):
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(yol)).pages)


def _ara(desen, metin):
    m = re.search(desen, metin)
    return _d(m.group(1)) if m else None


def kdv1_oku(metin):
    """KDV1 beyanname metninden temel alanlar (bulunamayan None)."""
    a = {"onay": (re.search(r"Onay Zaman[ıi]\s*:\s*([\d.]+\s*-\s*[\d:]+)", metin) or [None, None])[1],
         "duzeltme": bool(re.search(r"D[üu]zeltme Nedeni", metin)),
         "matrah": _ara(r"Matrah Toplam[ıi]\s+" + TUTAR, metin),
         "hesaplanan": _ara(r"Toplam Katma De[ğg]er Vergisi\s+" + TUTAR, metin),
         "onceki_devreden": _ara(r"[ÖO]nceki D[öo]nemden Devreden\s+" + TUTAR, metin),
         "indirimler": _ara(r"[İI]ndirimler Toplam[ıi]\s+" + TUTAR, metin),
         "sorumlu_109": _ara(r"Sorumlu S[ıi]fat[ıi]yla Beyan Edilerek [ÖO]denen KDV\s+" + TUTAR, metin)}
    a["sonraki_devreden"] = a["odenecek"] = None
    a["capraz"] = None
    if a["hesaplanan"] is not None and a["indirimler"] is not None:
        fark = a["indirimler"] - a["hesaplanan"]
        a["sonraki_devreden"] = fark if fark > 0 else Decimal(0)
        a["odenecek"] = -fark if fark < 0 else Decimal(0)
        # Çapraz kontrol: "Ödenmesi Gereken / Sonraki Döneme Devreden / İade ... / Tecil ..." etiket bloğundan sonraki ilk 3 tutar
        m = re.search(r"Sonraki D[öo]neme Devreden Katma De[ğg]er Vergisi.*?Tecil Edilecek Katma De[ğg]er Vergisi\s+" + TUTAR +
                      r"\s+" + TUTAR + r"\s+" + TUTAR + r"\s+" + TUTAR, metin, re.S)
        if m:
            odenecek_pdf, devreden_pdf = _d(m.group(2)), _d(m.group(3))
            a["capraz"] = (odenecek_pdf == a["odenecek"] and devreden_pdf == a["sonraki_devreden"])
    return a


def kdv2_oku(metin):
    m = re.search(r"Genel TOPLAM\s+" + TUTAR + r"\s*" + TUTAR, metin)
    return {"onay": (re.search(r"Onay Zaman[ıi]\s*:\s*([\d.]+\s*-\s*[\d:]+)", metin) or [None, None])[1],
            "duzeltme": bool(re.search(r"D[üu]zeltme Nedeni", metin)),
            "matrah": _d(m.group(1)) if m else None, "tevkif_kdv": _d(m.group(2)) if m else None}


def tahakkuk_oku(metin):
    t = re.sub(r"[ \t]+", " ", metin)
    toplam = _ara(r"TOPLAM\s+" + TUTAR, t)
    vade = re.search(r"\b(\d{2}/\d{2}/\d{4})(?=KDV|MUHSGK|\d{4}|\s*TOPLAM)", t)
    kabul = re.search(r"(\d{2}/\d{2}/\d{4})\s+\d{2}/\d{4}-\d{2}/\d{4}", t)
    return {"toplam": toplam, "vade": vade.group(1) if vade else None, "kabul": kabul.group(1) if kabul else None}


def beyanname_ozeti(klasorler):
    """Verilen klasörlerdeki beyanname/tahakkuk PDF'lerinden satırlar (Markdown)."""
    satirlar = []
    for klasor in klasorler:
        if not klasor or not Path(klasor).is_dir():
            continue
        for yol in sorted(Path(klasor).glob("*.pdf")):
            ad = yol.name
            tur = "KDV1" if "KDV1" in ad else "KDV2" if "KDV2" in ad else "MUHSGK" if "MUHSGK" in ad else None
            if not tur:
                continue
            try:
                metin = _pdf_metni(yol)
            except Exception:
                satirlar.append(f"- {ad}: PDF okunamadı.")
                continue
            ek = " (düzeltme)" if "Düzeltme" in ad else ""
            if "Tahakkuk" in ad:
                t = tahakkuk_oku(metin)
                satirlar.append(f"- {tur} tahakkuk{ek}: toplam {_tl(t['toplam'])}, vade {t['vade'] or '—'}, kabul {t['kabul'] or '—'}")
            elif tur == "KDV1":
                k = kdv1_oku(metin)
                cap = "" if k["capraz"] else " — **PDF'ten kontrol edin** (çapraz kontrol tutmadı/okunamadı)"
                satirlar.append(f"- KDV1 beyanname{ek} (onay {k['onay'] or '—'}): matrah {_tl(k['matrah'])}; hesaplanan {_tl(k['hesaplanan'])}; "
                                f"önceki dönemden devreden {_tl(k['onceki_devreden'])}; indirimler toplamı {_tl(k['indirimler'])}; "
                                f"109 (sorumlu sıfatıyla) {_tl(k['sorumlu_109'])}; **sonraki döneme devreden {_tl(k['sonraki_devreden'])}**; "
                                f"ödenecek {_tl(k['odenecek'])}{cap}")
            elif tur == "KDV2":
                k = kdv2_oku(metin)
                satirlar.append(f"- KDV2 beyanname{ek} (onay {k['onay'] or '—'}): matrah {_tl(k['matrah'])}; tevkif edilen KDV {_tl(k['tevkif_kdv'])}")
            else:
                satirlar.append(f"- MUHSGK beyanname{ek}: dosya {ad}")
    return satirlar


# ================================================================ özet metni
def _kisa_zaman(z):
    """'19.11.2026 00:41:51' -> '19.11 00:41'"""
    z = str(z or "")
    return f"{z[:5]} {z[11:16]}" if len(z) >= 16 else (z or "?")


def _zaman(s):
    try:
        return datetime.strptime(str(s.get("sorgu_zamani") or ""), "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None


def _birikim_satirlari(sonuclar):
    """Birikimli özette: hangi sonuç bu çalıştırmadan, hangisi önceki çalıştırmalardan; son denemesi başarısız olanlar."""
    if not any("bu_calistirma" in x for x in sonuclar):
        return []                                   # birikimsiz (eski biçim) çağrı
    s = []
    yeni = [x["sorgu"] for x in sonuclar if x.get("bu_calistirma")]
    eski = [x for x in sonuclar if not x.get("bu_calistirma")]
    s.append(f"- Bu çalıştırmada yenilenen {len(yeni)} sorgu" + (": " + ", ".join(yeni) if yeni and eski else "") + ".")
    if eski:
        # Aynı çalıştırmanın sorguları zamanca yakındır: 30 dakikadan uzun boşlukla kümelere ayrılır. En kalabalık küme yalnız
        # sayısıyla, diğerleri sorgu adlarıyla yazılır (özet kısa kalsın).
        sirali = sorted(eski, key=lambda x: _zaman(x) or datetime.min)
        kumeler = []
        for x in sirali:
            z = _zaman(x)
            if kumeler and z and kumeler[-1][-1][0] and (z - kumeler[-1][-1][0]).total_seconds() <= 1800:
                kumeler[-1].append((z, x))
            else:
                kumeler.append([(z, x)])
        buyuk = max(kumeler, key=len)
        parca = []
        for k in kumeler:
            ilk, son = k[0][0], k[-1][0]
            aralik = "zamanı bilinmiyor" if ilk is None else (f"{ilk:%d.%m.%Y %H:%M}" + (f"–{son:%H:%M}" if son and f"{son:%H:%M}" != f"{ilk:%H:%M}" else ""))
            adlar = ", ".join(x["sorgu"] for _, x in k)
            parca.append(f"**{aralik}** → " + (f"{len(k)} sorgu (aşağıda ayrıca belirtilmeyenlerin tümü)" if k is buyuk and len(k) > 6 else adlar))
        s.append("- Önceki çalıştırmalardan alınanlar (sorgu zamanıyla; eskiyse yeniden çalıştırın): " + "; ".join(parca) + ".")
    for x in sonuclar:
        d = x.get("son_deneme")
        if d:
            s.append(f"- **Son deneme başarısız — {x['sorgu']}:** {_kisa_zaman(d.get('zaman'))} denemesi "
                     f"{'HATA' if d.get('durum') == 'hata' else 'çalıştırılmadı'} ({str(d.get('mesaj') or '')[:160]}); "
                     f"aşağıdaki veri {_kisa_zaman(x.get('sorgu_zamani'))} tarihli önceki başarılı sorgudan.")
    return s


def _durum_bolumu(sonuclar):
    say = {d: [s for s in sonuclar if s["durum"] == d] for d in ("veri", "kayit_yok", "hata", "calistirilmadi")}
    s = [f"{len(say['veri'])} veri var, {len(say['kayit_yok'])} kayıt yok, **{len(say['hata'])} hata**, {len(say['calistirilmadi'])} çalıştırılmadı."]
    s += _birikim_satirlari(sonuclar)
    for x in say["hata"]:
        s.append(f"- HATA — {x['sorgu']}: {x['mesaj'][:300]}")
    for x in say["calistirilmadi"]:
        s.append(f"- Çalıştırılmadı — {x['sorgu']}: {x['mesaj'][:150]}")
    if say["hata"] or say["calistirilmadi"]:
        s.append("Bu sorguların verisi eksik; kontrol sırasında tam kabul etme.")
    return s


def _acil_bolumu(sonuclar, dokum_kayitlari):
    s = [f"- {x['sorgu']}: {x['mesaj'][:300]}" for x in sonuclar if "ACİL" in (x.get("mesaj") or "")]
    xml_yok = [k for k in dokum_kayitlari if k.get("kaynak") != "XML"]
    if xml_yok:
        s.append(f"- XML'i olmayan {len(xml_yok)} fatura (oran kırılımı yok): " + ", ".join(k.get("no", "") for k in xml_yok[:EN_FAZLA]))
    return s or ["Yok."]


FATURA_SORGU_ADI = {("gelen", "efatura"): "Gelen E-Faturalar", ("giden", "efatura"): "Giden E-Faturalar",
                    ("gelen", "earsiv"): "Gelen E-Arşiv Faturalar", ("giden", "earsiv"): "Giden E-Arşiv Faturalar"}


IADE_TIPLERI = ("IADE", "TEVKIFATIADE")


def _fatura_bolumu(dokum_kayitlari, sonuclar):
    import dokum
    s = []
    for yon, tur, baslik in dokum.SAYFALAR:
        sonuc = _sonuc_bul(sonuclar, FATURA_SORGU_ADI[(yon, tur)])
        if sonuc is None:
            s.append(f"- {baslik}: bu çalıştırmada seçilmedi.")
            continue
        k = [x for x in dokum_kayitlari if x["yon"] == yon and x["tur"] == tur]
        if sonuc["durum"] in ("hata", "calistirilmadi"):
            s.append(f"- **{baslik}: {'HATA' if sonuc['durum'] == 'hata' else 'çalıştırılmadı'} — veri eksik olabilir** ({len(k)} fatura alındı). {sonuc['mesaj'][:200]}")
            if not k:
                continue
        elif not k:
            s.append(f"- {baslik}: fatura yok ({sonuc['kapsam']}).")
            continue
        oran, iade_oran, doviz, iade_say = {}, {}, [], 0
        tev = iade_tev = Decimal(0)
        for x in k:
            if (x.get("para") or "TRY") != "TRY":       # dövizli faturalar TL toplamlarına katılmaz
                doviz.append(x)
                continue
            iade = str(x.get("tip", "")).upper() in IADE_TIPLERI
            iade_say += iade
            hedef = iade_oran if iade else oran          # iade tipli faturalar normal toplamlara karışmaz
            for o, (m, v) in (x.get("kdv") or {}).items():
                a, mm, vv = hedef.get(o, (0, Decimal(0), Decimal(0)))
                hedef[o] = (a + 1, mm + m, vv + v)
            if iade:
                iade_tev += x.get("tevkifat") or Decimal(0)
            else:
                tev += x.get("tevkifat") or Decimal(0)

        def dagilim(d_):
            return "; ".join(f"%{o}: matrah {_tl(m)}, KDV {_tl(v)} ({a} fatura)"
                             for o, (a, m, v) in sorted(d_.items(), key=lambda z: (z[0] == "?", float(z[0]) if z[0] != "?" else 0)))
        s.append(f"- **{baslik}: {len(k)} fatura** — {dagilim(oran) or 'oran kırılımı yok'}" + (f"; tevkifat (KDV) {_tl(tev)}" if tev else "")
                 + (f"; **iade tipli {iade_say} fatura AYRI (yukarıdaki toplamlara katılmadı): {dagilim(iade_oran) or 'oran kırılımı yok'}"
                    + (f"; iade tevkifatı {_tl(iade_tev)}" if iade_tev else "") + "**" if iade_say else "")
                 + (f"; {len(doviz)} dövizli fatura bu toplamlara katılmadı" if doviz else ""))
        karsi = "satici" if yon == "gelen" else "alici"
        istisna = [x for x in k if str(x.get("tip", "")).upper() in ("IADE", "TEVKIFAT", "TEVKIFATIADE") or (x.get("para") or "TRY") != "TRY" or x.get("tevkifat")]
        for x in istisna[:EN_FAZLA]:
            s.append(f"  - {x.get('tarih', '')} {x.get('no', '')} {_hucre(x.get(karsi))[:40]} — tip {x.get('tip') or '—'}, {x.get('para') or 'TRY'}, "
                     f"ödenecek {_tl(x.get('odenecek'))}" + (f", tevkifat {_tl(x.get('tevkifat'))} ({x.get('tevkifat_kodu', '')})" if x.get("tevkifat") else ""))
        if len(istisna) > EN_FAZLA:
            s.append(f"  - … {len(istisna) - EN_FAZLA} istisna daha (Fatura Dökümü Excel'inde).")
    return s


def _toplam_kolon(sonuc, kolon_adi):
    if not sonuc or sonuc["durum"] != "veri":
        return None
    i = _kolon(sonuc, kolon_adi)
    if i is None:
        return None
    degerler = [r[i] if i < len(r) else None for r in sonuc["satirlar"]]
    if not degerler or any(not isinstance(v, (int, float)) for v in degerler):
        return None                                  # okunamayan hücre varsa toplam verilmez ('—'), yanlışlıkla 0 görünmesin
    return sum((Decimal(str(v)) for v in degerler), Decimal(0))


def _satis_bolumu(sonuclar):
    s = []
    okc = _sonuc_bul(sonuclar, "Yeni Nesil ÖKC Aylık Satış Rapor Bildirimleri")
    pos = _sonuc_bul(sonuclar, "Toplu POS Sorgulama")
    todeb = _sonuc_bul(sonuclar, "TÖDEB Bilgileri")
    if okc and okc["durum"] == "veri":
        alan = {a: _toplam_kolon(okc, a) for a in ("Satış toplamı", "KDV toplamı", "Kart", "Nakit", "%1", "%10", "%20")}
        s.append(f"- ÖKC ({len(okc['satirlar'])} bildirim): satış {_tl(alan['Satış toplamı'])}, KDV {_tl(alan['KDV toplamı'])}, kart {_tl(alan['Kart'])}, "
                 f"nakit {_tl(alan['Nakit'])}; oranlara göre satış %1 {_tl(alan['%1'])}, %10 {_tl(alan['%10'])}, %20 {_tl(alan['%20'])}")
    elif okc:
        s.append(f"- ÖKC: {okc['mesaj'][:150]}")
    pos_t = Decimal(str(pos["toplam"])) if pos and isinstance(pos.get("toplam"), (int, float)) else None
    tod_t = Decimal(str(todeb["toplam"])) if todeb and isinstance(todeb.get("toplam"), (int, float)) else None
    if pos:
        s.append(f"- POS: {_tl(pos_t) if pos_t is not None else pos['mesaj'][:120]}")
    if todeb:
        s.append(f"- TÖDEB: {_tl(tod_t) if tod_t is not None else todeb['mesaj'][:120]}")
    kart = _toplam_kolon(okc, "Kart") if okc else None
    if kart is not None and pos_t is not None:
        s.append(f"- ÖKC kart − POS = {_tl(kart - pos_t)} (fark varsa e-arşiv/fatura ile kartlı satış veya başka dönem tahsilatı olabilir; KDV1 'kredi kartı ile tahsil edilen' satırı için kontrol edin)")
    return s or ["Satış kanalı sorgusu çalıştırılmadı."]


def _odeme_bolumu(sonuclar):
    s = []
    for ad in ("KDV 2 Ödeme Sorgulama", "109 - Sorumlu Sıfatıyla Ödenen KDV", "Ödeme / Alındı (tüm vergiler)", "Vergi Borcu", "GİB Tahsilat Bilgileri"):
        x = _sonuc_bul(sonuclar, ad)
        if not x:
            continue
        if x["durum"] != "veri":
            s.append(f"- {ad}: {('HATA — ' if x['durum'] == 'hata' else '')}{x['mesaj'][:200]}")
            continue
        s.append(f"- **{ad}** ({x['kapsam']}): {x['mesaj'][:200]}" + (f" Toplam {_tl(x['toplam'])}." if isinstance(x.get("toplam"), (int, float)) else ""))
        if ad in ("KDV 2 Ödeme Sorgulama", "109 - Sorumlu Sıfatıyla Ödenen KDV", "Ödeme / Alındı (tüm vergiler)"):
            for r in x["satirlar"][:EN_FAZLA]:
                s.append("  - " + " | ".join(_tl(v) if isinstance(v, float) else _hucre(v) for v in r[:6]))
            if len(x["satirlar"]) > EN_FAZLA:
                s.append(f"  - … {len(x['satirlar']) - EN_FAZLA} satır daha (sonuç Excel'inde).")
    return s or ["Ödeme sorgusu çalıştırılmadı."]


def _sgk6661_bolumu(x, ay, yil):
    """SGK 6661 asgari ücret desteği: mesaj + ay bazında (şubeler toplanmış) liste; dönem ayı ve dönemde mahsup edilen işaretlenir."""
    if x["durum"] != "veri":
        return [f"- {('HATA — ' if x['durum'] == 'hata' else '')}{x['mesaj'][:400]}"] + (
            ["  - (Hatalı sorgunun eldeki satırları sonuç Excel'inde; liste eksik olabilir.)"] if x["durum"] == "hata" and x.get("satirlar") else [])
    s = [f"- ({x['kapsam']}) {x['mesaj'][:600]}"]
    k = {ad: _kolon(x, ad) for ad in ("Sicil no", "Şube", "Yıl", "Ay", "Faydalanılan gün", "Destek tutarı", "Tahsilat tarihi")}
    if any(v is None for v in k.values()):
        return s
    aylar = {}                                      # (yıl, ay) -> [tutar, gün, tahsilat tarihleri, şubeler, okunamayan]
    for r in x["satirlar"]:
        if len(r) <= max(k.values()):
            continue
        a = aylar.setdefault((str(r[k["Yıl"]]), str(r[k["Ay"]])), [Decimal(0), 0, set(), set(), False])
        t = r[k["Destek tutarı"]]
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            a[0] += Decimal(str(t))
        else:
            a[4] = True
        if isinstance(r[k["Faydalanılan gün"]], int):
            a[1] += r[k["Faydalanılan gün"]]
        a[2].add(_hucre(r[k["Tahsilat tarihi"]]))
        a[3].add((_hucre(r[k["Sicil no"]]), _hucre(r[k["Şube"]])))     # şube = sicil no + ad (aynı adlı iki şube ayrı sayılır)
    for (y, a_), (tutar, gun, tarihler, subeler, eksik) in sorted(aylar.items()):
        isaret = []
        if ay and yil and (y, a_) == (str(yil), f"{ay:02d}"):
            isaret.append("dönem ayı")
        if ay and yil and any(str(t).endswith(f".{ay:02d}.{yil}") for t in tarihler):
            isaret.append("dönemde mahsup")
        s.append(f"  - {a_}/{y}: {_tl(tutar)}{' (okunamayan tutar var)' if eksik else ''} | {gun} gün | tahsilat {', '.join(sorted(tarihler))}"
                 + (f" | {len(subeler)} şube" if len(subeler) > 1 else "") + (f" ← {', '.join(isaret)}" if isaret else ""))
    return s


def _diger_bolumu(sonuclar):
    adlar = ("E-Arşiv Mükellef Adına Düzenlenenler", "E-Arşiv Portal İptal/İtiraz Talepleri", "İthalde Ödenen KDV", "Araç Bilgisi (mevcut)",
             "Araç Bilgisi (geçmiş)", "Araç Alım/Satım Bilgileri", "E-Tebligatlar", "GİB Gelen Evraklar", "GİB Bildirimlerim",
             "Gelen Karşıt İnceleme Tutanakları")
    s = []
    for x in sonuclar:
        tebligat = x["sorgu"] == "E-Tebligatlar" and x["durum"] == "hata" and x.get("satirlar")   # hatalı ama listesi var
        if x["sorgu"] not in adlar or (x["durum"] != "veri" and not tebligat):
            continue
        s.append(f"- {x['sorgu']}: {'HATA (liste eksik olabilir) — ' if tebligat else ''}{x['mesaj'][:250]}")
        if x["sorgu"] == "E-Tebligatlar":             # tebligatlar tek tek: gönderim | tebliğ | birim | açıklama
            for r in x["satirlar"][:EN_FAZLA]:
                h = [_hucre(v) for v in r] + [""] * 5
                s.append(f"  - gönderim {h[1]} | tebliğ {h[2] or '—'} | {h[0]} | {h[3]} | {h[4]}")
            if len(x["satirlar"]) > EN_FAZLA:
                s.append(f"  - … {len(x['satirlar']) - EN_FAZLA} tebligat daha (sonuç Excel'inde).")
    return s or ["Yok."]


# ================================================================ Luca (muhasebe kayıtları)
LUCA_ADLARI = {"kebir": "Luca Defteri Kebir", "mizan_ay": "Luca Mizan (seçilen ay)", "mizan_yil": "Luca Mizan (01.01 - ay sonu)",
               "hesap_plani": "Luca Hesap Planı",
               "ae_alis": "Luca Akıllı Entegrasyon Alış Belgeleri", "ae_satis": "Luca Akıllı Entegrasyon Satış Belgeleri",
               "bordro": "Luca Bordro Dökümü", "utts": "Luca UTTS Akaryakıt Giderleri"}
KDV_HESAP_ONEKLERI = ("190", "191", "391", "360")


def _tamam(sonuc):
    """Sorgu çalıştı ve sonucu güvenilir mi (veri var / kayıt yok)."""
    return sonuc is not None and sonuc["durum"] in ("veri", "kayit_yok")


def _sayi(v):
    return Decimal(str(v)) if isinstance(v, (int, float)) else Decimal(0)


def _luca_belge_karsilastir(baslik, luca_sonuc, emk_sonuclar, emk_kayitlari, karsi_alan):
    """E-Mükellef'ten inen faturalar ile Luca Akıllı Entegrasyon listesini karşılaştırır: önce ETTN, yoksa VKN + belge no.
    emk_sonuclar: o yönün e-fatura VE e-arşiv sorgu sonuçları (seçilmeyen None). Luca listesi iki türü birlikte içerdiğinden
    ikisi de hatasız bitmişse karşılaştırılır; aksi hâlde nedenini yazar (yoksa Luca'daki belgeler yanlışlıkla "E-Mükellef'te yok" görünür)."""
    if not _tamam(luca_sonuc):
        return []
    if not emk_sonuclar or not all(_tamam(s) for s in emk_sonuclar):
        return [f"  - {baslik}: E-Mükellef fatura sorguları bu çalıştırmada seçilmediği ya da hatalı olduğu için Luca listesiyle karşılaştırılmadı."]
    luca_ettn, luca_anahtar, luca_satir = set(), set(), {}
    for r in luca_sonuc["satirlar"]:
        r = [_hucre(v) for v in r] + [""] * 10
        ettn, anahtar = r[9].upper(), (re.sub(r"\D", "", r[2]), r[1].upper())
        if ettn:
            luca_ettn.add(ettn)
        luca_anahtar.add(anahtar)
        luca_satir[ettn or anahtar] = r
    emk_ettn, emk_anahtar, lucada_yok = set(), set(), []
    for x in emk_kayitlari:
        ettn = str(x.get("ettn") or "").upper()
        anahtar = (re.sub(r"\D", "", str(x.get(karsi_alan + "_vkn") or "")), str(x.get("no") or "").upper())
        if ettn:
            emk_ettn.add(ettn)
        emk_anahtar.add(anahtar)
        if not ((ettn and ettn in luca_ettn) or anahtar in luca_anahtar):
            lucada_yok.append(x)
    emkde_yok = [r for k, r in luca_satir.items()
                 if not ((r[9].upper() and r[9].upper() in emk_ettn) or (re.sub(r"\D", "", r[2]), r[1].upper()) in emk_anahtar)]
    s = [f"  - {baslik}: E-Mükellef {len(emk_kayitlari)} belge, Luca Akıllı Ent. {len(luca_sonuc['satirlar'])} belge; "
         f"**E-Mükellef'te olup Luca'da olmayan {len(lucada_yok)}**, Luca'da olup E-Mükellef'te olmayan {len(emkde_yok)}."]
    if lucada_yok:
        s.append("    - Luca'da olmayanlar (Luca'ya getirilmemiş olabilir): " + "; ".join(
            f"{x.get('tarih', '')} {x.get('no', '')} {_hucre(x.get(karsi_alan))[:25]}" for x in lucada_yok[:EN_FAZLA])
            + (f" … (+{len(lucada_yok) - EN_FAZLA})" if len(lucada_yok) > EN_FAZLA else ""))
    if emkde_yok:
        s.append("    - E-Mükellef'te olmayanlar: " + "; ".join(f"{r[0]} {r[1]} {r[3][:25]}" for r in emkde_yok[:EN_FAZLA])
                 + (f" … (+{len(emkde_yok) - EN_FAZLA})" if len(emkde_yok) > EN_FAZLA else ""))
    return s


def _xml_kdv(dokum_kayitlari, yon):
    """Yöndeki TL faturaların (iade tipliler ayrı) XML KDV toplamı -> (normal, iade)."""
    normal = iade = Decimal(0)
    for x in dokum_kayitlari:
        if x.get("yon") != yon or (x.get("para") or "TRY") != "TRY":
            continue
        v = sum((v for _, v in (x.get("kdv") or {}).values()), Decimal(0))
        if str(x.get("tip", "")).upper() in IADE_TIPLERI:
            iade += v
        else:
            normal += v
    return normal, iade


def _luca_bolumu(sonuclar, dokum_kayitlari):
    bul = {k: _sonuc_bul(sonuclar, ad) for k, ad in LUCA_ADLARI.items()}
    if not any(bul.values()):
        return ["Luca sorguları bu çalıştırmada seçilmedi."]
    s = []
    for k in ("kebir", "mizan_ay", "mizan_yil", "hesap_plani", "ae_alis", "ae_satis", "bordro", "utts"):
        x = bul[k]
        if x is None:
            continue
        if x["durum"] in ("hata", "calistirilmadi"):
            s.append(f"- **{x['sorgu']}: {'HATA' if x['durum'] == 'hata' else 'çalıştırılmadı'} — veri yok.** {x['mesaj'][:200]}")
            continue
        s.append(f"- **{x['sorgu']}** ({x['kapsam']}): {x['mesaj'][:500]}")
        if k == "kebir":                            # KDV kebirlerinin alt hesapları (dönem ayı)
            for r in x["satirlar"]:
                if r and r[0] == "alt hesap":
                    s.append(f"  - {r[1]} {_hucre(r[2])[:40]}: borç {_tl(r[3])}, alacak {_tl(r[4])} ({r[5]} satır)")
        elif k in ("mizan_ay", "mizan_yil"):
            alt = [r for r in x["satirlar"] if str(r[0]).startswith(KDV_HESAP_ONEKLERI) and "." in str(r[0])]
            for r in alt[:EN_FAZLA * 2]:
                bakiye = f"B {_tl(r[4])}" if r[4] else (f"A {_tl(r[5])}" if r[5] else "0,00")
                s.append(f"  - {r[0]} {_hucre(r[1])[:40]}: borç {_tl(r[2] or 0)}, alacak {_tl(r[3] or 0)}, bakiye {bakiye}")
            if len(alt) > EN_FAZLA * 2:
                s.append(f"  - … {len(alt) - EN_FAZLA * 2} KDV alt hesabı daha (sonuç Excel'inde).")
    # --- karşılaştırmalar (yalnız iki taraf da hatasızsa)
    kars = []
    kars += _luca_belge_karsilastir("Alış belgeleri", bul["ae_alis"],
                                    [_sonuc_bul(sonuclar, FATURA_SORGU_ADI[("gelen", "efatura")]),
                                     _sonuc_bul(sonuclar, FATURA_SORGU_ADI[("gelen", "earsiv")])],
                                    [x for x in dokum_kayitlari if x.get("yon") == "gelen"], "satici")
    kars += _luca_belge_karsilastir("Satış belgeleri", bul["ae_satis"],
                                    [_sonuc_bul(sonuclar, FATURA_SORGU_ADI[("giden", "efatura")]),
                                     _sonuc_bul(sonuclar, FATURA_SORGU_ADI[("giden", "earsiv")])],
                                    [x for x in dokum_kayitlari if x.get("yon") == "giden"], "alici")
    keb = bul["kebir"]
    if keb is not None and keb["durum"] == "veri":
        donem = {r[1]: (_sayi(r[3]), _sayi(r[4])) for r in keb["satirlar"] if r and r[0] == "kebir"}
        for yon, hesap, taraf, adlar, ek in (("gelen", "191", 0, (("gelen", "efatura"), ("gelen", "earsiv")), "tevkifat, ithalat, makbuz, fişi kesilmemiş faturalar"),
                                             ("giden", "391", 1, (("giden", "efatura"), ("giden", "earsiv")), "ÖKC (yazarkasa) satışları, fişi kesilmemiş faturalar")):
            sorgular = [_sonuc_bul(sonuclar, FATURA_SORGU_ADI[a]) for a in adlar]
            if not all(_tamam(q) for q in sorgular):
                continue
            normal, iade = _xml_kdv(dokum_kayitlari, yon)
            luca_tutar = donem.get(hesap, (Decimal(0), Decimal(0)))[taraf]
            kars.append(f"  - Bilgi — {'gelen' if yon == 'gelen' else 'giden'} fatura XML'lerinde KDV {_tl(normal)}"
                        + (f" (+ iade tipli {_tl(iade)})" if iade else "") + f"; Luca {hesap} dönem {'borç' if taraf == 0 else 'alacak'} {_tl(luca_tutar)}; "
                        f"fark {_tl(normal - luca_tutar)}. Fark tek başına eksik fatura demek değildir ({ek}, döviz ve iadeler farka neden olur).")
    if kars:
        s.append("- Karşılaştırma (E-Mükellef ↔ Luca):")
        ilgili = [x for x in [bul["ae_alis"], bul["ae_satis"], bul["kebir"]] + [_sonuc_bul(sonuclar, a) for a in FATURA_SORGU_ADI.values()]
                  if x is not None and _tamam(x) and _zaman(x) is not None]
        zamanlar = sorted(_zaman(x) for x in ilgili)
        if zamanlar and (zamanlar[-1] - zamanlar[0]).total_seconds() > 2 * 3600:
            s.append(f"  - **Dikkat:** karşılaştırılan veriler farklı zamanlarda alındı (en eski {zamanlar[0]:%d.%m %H:%M}, en yeni "
                     f"{zamanlar[-1]:%d.%m %H:%M}); arada kayıt değişmiş olabilir. Kesin karşılaştırma için iki tarafı birlikte yeniden çalıştırın.")
        s += kars
    return s


def _klasor_dokumu(donem_klasoru, ek_klasorler):
    """Dönem klasöründeki dosyalar (fatura alt klasörleri yalnız sayıyla) + ek klasörler (beyanname, MUHSGK, e-tebligat)."""
    s = []
    kok = Path(donem_klasoru)
    if kok.is_dir():
        for p in sorted(kok.rglob("*")):
            if not p.is_file() or p.name.startswith(".~") or ("Kontrol Ozeti" in p.name and p.suffix == ".md"):
                continue                               # önceki özetler listelenmez (yoksa her çalıştırmada içerik değişir)
            g = p.relative_to(kok).as_posix()
            if re.search(r"E-Mukellef/(Gelen|Giden) E-(Arsiv|Fatura)/", g):
                continue
            s.append(f"- {g}")
        e = kok / "01_Kaynaklar" / "E-Mukellef"
        for alt in ("Gelen E-Fatura", "Giden E-Fatura", "Gelen E-Arsiv", "Giden E-Arsiv"):
            if (e / alt).is_dir():
                xml = len(list((e / alt).glob("*.xml")))
                pdf = len(list((e / alt).glob("*.pdf")))
                s.append(f"- 01_Kaynaklar/E-Mukellef/{alt}/ — {xml} XML, {pdf} PDF")
    for etiket, k in ek_klasorler:
        if k and Path(k).is_dir():
            dosya = sorted(p.name for p in Path(k).iterdir() if p.is_file())
            s.append(f"- {etiket}: {k} — {len(dosya)} dosya")
    return s or ["—"]


def _donem_coz(donem_adi):
    """'Ekim 2026' -> (10, 2026); çözülemezse (None, None)."""
    m = re.fullmatch(r"\s*(\S+)\s+(\d{4})\s*", str(donem_adi or ""))
    return (AYLAR.index(m.group(1)) + 1, int(m.group(2))) if m and m.group(1) in AYLAR else (None, None)


def ozet_metni(firma, donem_adi, sonuclar, dokum_kayitlari, beyanname_klasorleri, donem_klasoru, secilmeyen, ek_klasorler):
    sat = [f"# {firma['ad']} — {donem_adi} KDV ön hazırlık kontrol özeti", "",
           f"Sorgu zamanı: {datetime.now():%d.%m.%Y %H:%M}  ",
           f"Ünvan/VKN: {firma.get('unvan') or firma['ad']} / {firma.get('vkn') or '—'} — E-Mükellef no {firma.get('emukellef_id')}"
           + (f" · Luca firma no {firma['luca_sirket_id']}" if firma.get("luca_sirket_id") else "") + "  ",
           "Kaynak: Beyanname Ön Hazırlık programı (E-Mükellef/GİB sorguları, fatura XML'leri, beyanname PDF'leri, Luca raporları). "
           "Kontrole bu dosyayla başla; ayrıntı gerekirse aşağıdaki dosyalara in. PDF'ten okunan rakamlar kesin kaynak değildir.", ""]
    if any("bu_calistirma" in x for x in sonuclar):
        sat[-1:] = ["**BİRİKİMLİ ÖZET:** dönemin tüm çalıştırmalarından her sorgunun en güncel başarılı sonucunu içerir; en yeni özet dosyası "
                    "dönemin tamamıdır (eski özet dosyalarına bakmaya gerek yok). Sonuçların sorgu zamanları 'Durum' bölümündedir.", ""]
    sat += ["## Durum", ""] + _durum_bolumu(sonuclar)
    if secilmeyen:
        sat.append(("- Dönemde hiç çalıştırılmamış sorgular (bilgisi yok): " if any("bu_calistirma" in x for x in sonuclar)
                    else "- Bu çalıştırmada seçilmeyen (bilgisi yok): ") + ", ".join(secilmeyen))
    sat += [""]
    sat += ["## ACİL", ""] + _acil_bolumu(sonuclar, dokum_kayitlari) + [""]
    beyan = beyanname_ozeti(beyanname_klasorleri)
    sat += ["## Önceki dönem beyannameleri (PDF'ten okundu)", ""] + (beyan or ["Önceki dönem beyanname PDF'i bulunamadı."]) + [""]
    sat += ["## Faturalar (XML'den)", ""] + _fatura_bolumu(dokum_kayitlari, sonuclar) + [""]
    sat += ["## Satış kanalları", ""] + _satis_bolumu(sonuclar) + [""]
    sat += ["## Ödemeler ve borç", ""] + _odeme_bolumu(sonuclar) + [""]
    sgk = _sonuc_bul(sonuclar, "SGK 6661 Asgari Ücret Desteği")
    if sgk:
        sat += ["## SGK 6661 asgari ücret desteği", ""] + _sgk6661_bolumu(sgk, *_donem_coz(donem_adi)) + [""]
    sat += ["## Luca (muhasebe kayıtları)", ""] + _luca_bolumu(sonuclar, dokum_kayitlari) + [""]
    sat += ["## Diğer bulgular", ""] + _diger_bolumu(sonuclar) + [""]
    yok = [f"- {x['sorgu']} ({x['kapsam']}): {x['mesaj'][:120]}" for x in sonuclar if x["durum"] == "kayit_yok"]
    sat += ["## Kayıt yok (sorgu kapsamıyla)", ""] + (yok or ["—"]) + [""]
    sat += [f"## Dosyalar ({donem_klasoru})", ""] + _klasor_dokumu(donem_klasoru, ek_klasorler) + [""]
    return "\n".join(sat).rstrip() + "\n"
