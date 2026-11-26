"""Luca sorgularını çalıştırır ve sonucu E-Mükellef sonuçlarıyla aynı biçimde (emukellef._sonuc) hazırlar.

Hepsi salt okunur: Defteri Kebir, Mizan (seçilen ay / 01.01 - ay sonu), Hesap Planı, Akıllı Entegrasyon alış-satış listeleri,
Bordro Dökümü.
İndirilen dosyalar çağırana "dosya" sözlüğüyle döner (kaydı cikti.luca_kaydet yapar).
"""
import calendar
import re
from datetime import date, datetime

import emukellef
import luca

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# (anahtar, ad) — çalışma sırası
SORGULAR = [
    ("luca_kebir", "Luca Defteri Kebir"),
    ("luca_mizan_ay", "Luca Mizan (seçilen ay)"),
    ("luca_mizan_yil", "Luca Mizan (01.01 - ay sonu)"),
    ("luca_hesap_plani", "Luca Hesap Planı"),
    ("luca_ae_alis", "Luca Akıllı Entegrasyon Alış Belgeleri"),
    ("luca_ae_satis", "Luca Akıllı Entegrasyon Satış Belgeleri"),
    ("luca_bordro", "Luca Bordro Dökümü"),
    ("luca_utts", "Luca UTTS Akaryakıt Giderleri"),
]
ANAHTARLAR = {k for k, _ in SORGULAR}
AD = dict(SORGULAR)
BIRIM = {"luca_kebir": "hesap", "luca_mizan_ay": "hesap", "luca_mizan_yil": "hesap", "luca_ae_alis": "belge", "luca_ae_satis": "belge",
         "luca_bordro": "satır", "luca_utts": "fiş", "luca_hesap_plani": "hesap"}
KDV_KEBIRLERI = ("190", "191", "391", "360")
BOS_BORDRO_SATIRI = 11        # bordrosu olmayan ayda Luca yalnız başlık satırlarını döndürür (19.11.2026'da görüldü)
KAYNAK_ADI = {"gib_efatura_alis": "GİB e-fatura", "gib_efatura_satis": "GİB e-fatura", "gib_ebelge_alis": "GİB e-arşiv",
              "gib_ebelge_satis": "GİB e-arşiv", "turmob_alis": "TÜRMOB", "turmob_satis": "TÜRMOB", "isnet_alis": "İşnet",
              "isnet_satis": "İşnet", "efix": "Luca Ön Muhasebe", "belge_app": "BelgeApp", "qr_alis": "QR", "qr_satis": "QR",
              "treewin": "TreeWin", "gib_530": "GİB"}


def _tl(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _sayi(v):
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if v not in (None, "", " ") else 0.0
    except ValueError:
        return 0.0


def _tarih(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    for bicim in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(v).strip(), bicim).date()
        except ValueError:
            continue
    return None


def ay_araligi(ay, yil):
    return date(yil, ay, 1), date(yil, ay, calendar.monthrange(yil, ay)[1])


def _donem_tarihi(donem, alan):
    try:
        return datetime.strptime(str(donem.get(alan) or ""), "%d/%m/%Y").date()
    except ValueError:
        return None


# ================================================================ dosyaları tabloya çevirme
def kebir_ozeti(satirlar, ay, yil):
    """Kebir satırlarından dönem ayının hareketleri: kebir hesap bazında ve KDV kebirlerinde alt hesap bazında borç/alacak.
    Sütunlar: 0 kebir, 1 kebir adı, 2 tarih, 4 fiş no, 7 hesap kodu, 8 hesap adı, 10 borç, 11 alacak.
    Döner: (kolonlar, tablo, {kebir: (borç, alacak)}, toplam satır sayısı, dönem satır sayısı)"""
    bas, bit = ay_araligi(ay, yil)
    kebir, alt, toplam, donem_say = {}, {}, 0, 0
    for r in satirlar[1:]:
        if len(r) < 12 or r[0] in (None, "", " "):
            continue
        t = _tarih(r[2])
        if t is None:
            continue
        toplam += 1
        if not (bas <= t <= bit):
            continue
        donem_say += 1
        k = str(r[0]).strip()
        b, a = _sayi(r[10]), _sayi(r[11])
        e = kebir.setdefault(k, [str(r[1] or "").strip(), 0.0, 0.0, 0])
        e[1] += b
        e[2] += a
        e[3] += 1
        if k in KDV_KEBIRLERI:
            kod = str(r[7] or "").strip()
            x = alt.setdefault(kod, [str(r[8] or "").strip(), 0.0, 0.0, 0])
            x[1] += b
            x[2] += a
            x[3] += 1
    kolonlar = ["Düzey", "Hesap", "Hesap adı", "Dönem borç", "Dönem alacak", "Satır"]
    tablo = [["kebir", k, v[0], round(v[1], 2), round(v[2], 2), v[3]] for k, v in sorted(kebir.items())]
    tablo += [["alt hesap", k, v[0], round(v[1], 2), round(v[2], 2), v[3]] for k, v in sorted(alt.items())]
    return kolonlar, tablo, {k: (round(v[1], 2), round(v[2], 2)) for k, v in kebir.items()}, toplam, donem_say


def mizan_tablosu(satirlar):
    """Mizan dosyasının satırları: (kolonlar, [[kod, ad, borç, alacak, borç bakiyesi, alacak bakiyesi]])."""
    bas = next((i for i, r in enumerate(satirlar) if r and str(r[0] or "").strip().upper() == "HESAP KODU"), None)
    if bas is None:
        raise luca.LucaHatasi("Mizan dosyası beklenen yapıda değil ('HESAP KODU' başlığı yok).")
    tablo = []
    for r in satirlar[bas + 1:]:
        r = list(r) + [None] * 7
        kod = str(r[0] or "").strip()
        if not kod or not kod[0].isdigit():
            continue
        tablo.append([kod, str(r[1] or "").strip()] + [round(_sayi(v), 2) if v not in (None, "", " ") else None for v in r[3:7]])
    return ["Hesap kodu", "Hesap adı", "Borç", "Alacak", "Borç bakiyesi", "Alacak bakiyesi"], tablo


def hesap_plani_tablosu(satirlar):
    """Hesap Planı Listesi dosyasının satırları: (kolonlar, tablo). Kolonlar dosyanın kendi başlıklarıdır (ilk ikisi HESAP KODU,
    HESAP ADI; sonra döviz kodu, hesap tipi ve — istenmişse — tutar sütunları). Hesap kodu metin olarak korunur; toplam satırı alınmaz."""
    bas = next((i for i, r in enumerate(satirlar) if r and str(r[0] or "").strip().upper() == "HESAP KODU"), None)
    if bas is None or len(satirlar[bas]) < 2 or "HESAP AD" not in str(satirlar[bas][1] or "").upper():
        raise luca.LucaHatasi("Hesap planı dosyası beklenen yapıda değil ('HESAP KODU' / 'HESAP ADI' başlıkları yok).")
    basliklar = [str(b or "").strip() for b in satirlar[bas]]
    while basliklar and not basliklar[-1]:
        basliklar.pop()
    n = len(basliklar)
    tutar = {i for i, b in enumerate(basliklar) if any(k in b.upper() for k in ("BORÇ", "ALACAK", "BAKİYE"))}
    tablo = []
    for r in satirlar[bas + 1:]:
        r = (list(r) + [None] * n)[:n]
        kod = str(r[0] if r[0] is not None else "").strip()
        if not kod or not kod[0].isdigit():         # boş satır / "GENEL TOPLAM :"
            continue
        tablo.append([kod] + [(round(_sayi(v), 2) if v not in (None, "", " ") else None) if i in tutar else str(v if v is not None else "").strip()
                              for i, v in enumerate(r) if i > 0])
    ad = {"HESAP KODU": "Hesap kodu", "HESAP ADI": "Hesap adı", "KOD": "Döviz", "HESAP TİPİ": "Hesap tipi", "BORÇ": "Borç",
          "ALACAK": "Alacak", "BORÇ BAKİYESİ": "Borç bakiyesi", "ALACAK BAKİYESİ": "Alacak bakiyesi"}
    return [ad.get(b.upper(), b) for b in basliklar], tablo


def _iptal_metni(b):
    return " / ".join(str(b.get(k)).strip() for k in ("iptal_itiraz", "iptal_itiraz_durumu", "turmob_iptal_itiraz", "turmob_iptal_itiraz_durum")
                      if str(b.get(k) or "").strip())


def ae_tablosu(liste):
    kolonlar = ["Tarih", "Belge no", "VKN/TCKN", "Ünvan", "Belge türü", "Kaynak", "Onay", "Fiş no", "İptal/itiraz", "ETTN"]
    tablo = []
    for b in liste:
        tablo.append([str(b.get("belge_tarihi") or ""), str(b.get("belge_numarasi") or ""), str(b.get("alici_vkn_tckn") or ""),
                      str(b.get("alici_unvan_ad_soyad") or "").strip(" -"), str(b.get("belge_turu") or ""),
                      KAYNAK_ADI.get(str(b.get("entegrasyon_tur") or ""), str(b.get("entegrasyon_tur") or "")),
                      str(b.get("onay_durumu") or ""), str(b.get("fis_no") or ""), _iptal_metni(b), str(b.get("ettn") or "").split("#")[0]])
    tablo.sort(key=lambda r: (r[0][6:10] + r[0][3:5] + r[0][:2], r[1]))
    return kolonlar, tablo


# ================================================================ çalıştırma
def calistir(anahtar, ctx, pg, sirket_id, ay, yil, gunluk, iptal=lambda: False):
    """Luca'da firma/dönem açılır, sorgu çalışır. Döner: (sonuç, dosya | None).
    dosya: {"tur": 'kebir'|'mizan'|'hesap_plani'|'bordro'|'utts', "bayt", "bas": date, "bit": date}  (kaydı çağıran yapar)
    luca.LucaHatasi çağırana çıkar (oturum düşmesi vb. orada ele alınır)."""
    ad = AD[anahtar]
    donem_id = luca.firma_sec(pg, sirket_id, yil, ay, gunluk, iptal)
    ay_bas, ay_bit = ay_araligi(ay, yil)
    ay_adi = f"{AYLAR[ay - 1]} {yil}"

    if anahtar == "luca_kebir":
        bayt, donem = luca.kebir_indir(ctx, pg, sirket_id, donem_id, gunluk, iptal)
        uyari = luca.kebir_xlsx_kontrol(bayt)
        if uyari:
            raise luca.LucaHatasi("Kebir dosyası beklenen yapıda değil: " + "; ".join(uyari))
        kolonlar, tablo, kdv, toplam, donem_say = kebir_ozeti(luca.xlsx_satirlari(bayt), ay, yil)
        bas, bit = _donem_tarihi(donem, "donem_bas") or date(yil, 1, 1), _donem_tarihi(donem, "donem_bit") or date(yil, 12, 31)
        kapsam = f"Mali dönem {bas:%d.%m.%Y} - {bit:%d.%m.%Y} (tablo: {ay_adi} hareketleri)"
        parca = []
        for k in KDV_KEBIRLERI:
            if k in kdv:
                parca.append(f"{k} borç {_tl(kdv[k][0])} / alacak {_tl(kdv[k][1])}")
            else:
                parca.append(f"{k} hareket yok")
        mesaj = f"Kebir indirildi ({toplam} satır). {ay_adi}: {donem_say} satır; " + "; ".join(parca) + "."
        if not donem_say:
            mesaj += " Dönem ayında Luca'da hiç hareket yok (kayıtlar henüz işlenmemiş olabilir)."
        return emukellef._sonuc(ad, kapsam, "veri", mesaj, kolonlar, tablo), {"tur": "kebir", "bayt": bayt, "bas": bas, "bit": bit}

    if anahtar in ("luca_mizan_ay", "luca_mizan_yil"):
        # mali dönem yıl/ay başından başlamayabilir (yeni firma) ya da ay ortasında bitebilir: aralık dönemin içinde kalır
        d = luca.donem_bilgisi(ctx, pg, sirket_id, donem_id)
        db, de = _donem_tarihi(d, "donem_bas"), _donem_tarihi(d, "donem_bit")
        bas = ay_bas if anahtar == "luca_mizan_ay" else date(yil, 1, 1)       # ay mizanı ay başından, yıl mizanı 01.01'den
        if db is not None and db > bas:
            bas = db
        ay_bit = de if (de is not None and de < ay_bit) else ay_bit
        bayt, donem = luca.mizan_indir(ctx, pg, sirket_id, donem_id, bas, ay_bit, gunluk, iptal)
        kolonlar, tablo = mizan_tablosu(luca.xlsx_satirlari(bayt))
        kapsam = f"{bas:%d.%m.%Y} - {ay_bit:%d.%m.%Y}"
        dosya = {"tur": "mizan", "bayt": bayt, "bas": bas, "bit": ay_bit}
        if not tablo:
            return emukellef._sonuc(ad, kapsam, "kayit_yok", "Bu tarih aralığında Luca'da hareket yok.", kolonlar), dosya
        satir = {r[0]: r for r in tablo}
        parca = []
        for k in KDV_KEBIRLERI:
            r = satir.get(k)
            if r:
                parca.append(f"{k} borç {_tl(r[2] or 0)} / alacak {_tl(r[3] or 0)} (bakiye {'B ' + _tl(r[4]) if r[4] else 'A ' + _tl(r[5] or 0)})")
            else:
                parca.append(f"{k} yok")
        return emukellef._sonuc(ad, kapsam, "veri", f"{len(tablo)} hesap. " + "; ".join(parca) + ".", kolonlar, tablo), dosya

    if anahtar == "luca_hesap_plani":
        bayt, donem = luca.hesap_plani_indir(ctx, pg, sirket_id, donem_id, gunluk, iptal)
        kolonlar, tablo = hesap_plani_tablosu(luca.xlsx_satirlari(bayt))
        bas, bit = _donem_tarihi(donem, "donem_bas") or date(yil, 1, 1), _donem_tarihi(donem, "donem_bit") or date(yil, 12, 31)
        kapsam = f"Mali dönem {bas:%d.%m.%Y} - {bit:%d.%m.%Y}"
        if not tablo:
            return emukellef._sonuc(ad, kapsam, "kayit_yok", "Luca'da bu firma/dönem için hesap planı boş.", kolonlar), None
        tip_i = next((i for i, k in enumerate(kolonlar) if k == "Hesap tipi"), None)
        mesaj = f"Hesap planı indirildi: {len(tablo)} hesap"
        if tip_i is not None:
            say = {}
            for r in tablo:
                say[r[tip_i]] = say.get(r[tip_i], 0) + 1
            mesaj += " (" + ", ".join(f"{v} {k.lower()}" for k, v in sorted(say.items()) if k) + ")"
        return emukellef._sonuc(ad, kapsam, "veri", mesaj + ".", kolonlar, tablo), {"tur": "hesap_plani", "bayt": bayt, "bas": bas, "bit": bit}

    if anahtar in ("luca_ae_alis", "luca_ae_satis"):
        yon = "alis" if anahtar == "luca_ae_alis" else "satis"
        liste = luca.akilli_entegrasyon_listesi(ctx, pg, sirket_id, yon, ay_bas, ay_bit, iptal)
        kapsam = f"Belge tarihi {ay_adi} (Luca'ya getirilmiş belgeler)"
        kolonlar, tablo = ae_tablosu(liste)
        if not tablo:
            return emukellef._sonuc(ad, kapsam, "kayit_yok", "Luca Akıllı Entegrasyon'da bu ay için belge yok "
                                    "(Luca'da 'Belgeleri Getir' yapılmamış olabilir).", kolonlar), None
        fisli = sum(1 for r in tablo if r[7])
        iptalli = [r for r in tablo if r[8]]
        mesaj = f"{len(tablo)} belge; {fisli} muhasebeleşmiş, {len(tablo) - fisli} muhasebeleşmemiş (fiş no boş)."
        if iptalli:
            mesaj = (f"ACİL: {len(iptalli)} belgede iptal/itiraz bilgisi var: " + "; ".join(f"{r[1]} {r[3][:25]} ({r[8]})" for r in iptalli[:5])
                     + (" …" if len(iptalli) > 5 else "") + ". " + mesaj)
        son_tarih = max((r[0][6:10] + r[0][3:5] + r[0][:2] for r in tablo if len(r[0]) == 10), default="")
        if son_tarih:
            mesaj += f" Son belge tarihi {son_tarih[6:8]}.{son_tarih[4:6]}.{son_tarih[:4]}."
        return emukellef._sonuc(ad, kapsam, "veri", mesaj, kolonlar, tablo), None

    if anahtar == "luca_utts":
        return _utts_calistir(ctx, pg, sirket_id, ay, yil, ad, gunluk, iptal)

    if anahtar == "luca_bordro":
        bayt, donem = luca.bordro_indir(ctx, pg, sirket_id, donem_id, ay, yil, gunluk, iptal)
        n = len(luca.xlsx_satirlari(bayt))
        if n <= BOS_BORDRO_SATIRI:                  # yalnız başlık bloğu: o ay bordro yok
            return emukellef._sonuc(ad, ay_adi, "kayit_yok", f"Luca'da {ay_adi} bordrosu yok (rapor yalnız {n} başlık satırı döndürdü)."), None
        mesaj = f"Bordro dökümü indirildi ({n} satır). Kişisel veri içerdiğinden sonuç dosyasına satırlar yazılmadı; dosyanın kendisine bakın."
        return emukellef._sonuc(ad, ay_adi, "veri", mesaj), {"tur": "bordro", "bayt": bayt, "bas": ay_bas, "bit": ay_bit}

    raise ValueError(anahtar)


# ================================================================ UTTS akaryakıt giderleri (kullanıcı kararı 19.11.2026)
UTTS_KOLONLAR = ["Tarih", "Plaka", "Araç tipi", "İstasyon VKN", "İstasyon ünvanı", "UTTS fiş no", "Yakıt", "Miktar", "Birim fiyat",
                 "Matrah", "KDV oranı", "KDV", "Toplam", "Ödeme tipi", "Satış tipi", "Luca fiş no", "ETTN"]


def _num(v):
    """Luca'nın sayıları hem '1.234,56' hem '1234.56' biçiminde gelebilir."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip().replace(" ", "")
    if not s:
        return 0.0
    try:
        if "," in s:
            return float(s.replace(".", "").replace(",", "."))
        if s.count(".") > 1:
            return float(s.replace(".", ""))
        return float(s)
    except ValueError:
        return 0.0


def utts_araliklari(ay, yil, bugun=None):
    """GİB'den getir aralıkları (kullanıcı kararı 19.11.2026): beyan döneminin ilk-son günü + sonraki ayın 1'i-15'i (bugünü geçmez).
    Luca aralığın aynı ay içinde olmasını istediğinden iki ayrı aralıktır. Döner: (aralıklar, notlar)"""
    bugun = bugun or date.today()
    bas, bit = ay_araligi(ay, yil)
    araliklar, notlar = [], []
    if bas > bugun:
        return [], [f"{AYLAR[ay - 1]} {yil} henüz başlamadı; GİB'den getirilmedi."]
    araliklar.append((bas, min(bit, bugun)))
    s_ay, s_yil = (1, yil + 1) if ay == 12 else (ay + 1, yil)
    s_bas = date(s_yil, s_ay, 1)
    if s_bas <= bugun:                          # Aralık döneminde bu aralık yeni yılın Luca döneminde getirilir
        araliklar.append((s_bas, min(date(s_yil, s_ay, 15), bugun)))
    return araliklar, notlar


def utts_tablosu(liste):
    tablo = []
    for k in liste:
        tablo.append([str(k.get("belgeTarihi") or ""), str(k.get("plaka") or ""), "Binek" if str(k.get("aracTipi")) == "1" else "Diğer",
                      str(k.get("mukellefVknTckn") or "").strip(), str(k.get("mukellefUnvan") or "").strip(), str(k.get("uttsFisNo") or ""),
                      str(k.get("yakitAdi") or ""), round(_num(k.get("miktar")), 3), round(_num(k.get("birimTutar")), 4),
                      round(_num(k.get("matrah")), 2), str(k.get("vergiOrani") or ""), round(_num(k.get("vergiTutari")), 2),
                      round(_num(k.get("toplamTutar")), 2), str(k.get("odemeTipi") or ""), str(k.get("satisTipi") or ""),
                      str(k.get("fisNo") or ""), str(k.get("ettn") or "")])
    tablo.sort(key=lambda r: (r[0][6:10] + r[0][3:5] + r[0][:2], r[1], r[5]))
    return UTTS_KOLONLAR, tablo


def _utts_calistir(ctx, pg, sirket_id, ay, yil, ad, gunluk, iptal):
    ay_bas, ay_bit = ay_araligi(ay, yil)
    kapsam = f"Belge tarihi {AYLAR[ay - 1]} {yil}; GİB'den getir: {AYLAR[ay - 1]} tamamı + {AYLAR[ay % 12]} 1-15"
    fr = luca.utts_ekrani(pg, sirket_id)
    notlar, hatalar = [], []

    # 1) GİB'den getir (Luca'nın kendi işlevleriyle; çift kayıt önlenir)
    plakalar = luca.utts_plakalar(fr)
    araliklar, a_notlar = utts_araliklari(ay, yil)
    notlar += a_notlar
    gelen = kaydedilen = atlanan = 0
    if not plakalar:
        notlar.append("Luca'da bu firma için UTTS plakası tanımlı değil (UTTS ekranı > plaka tanımları); GİB'den getirilemedi.")
    elif araliklar:
        mevcut = {str(k.get("ettn") or "").strip() for k in luca.utts_liste(fr, date(ay_bas.year - (ay_bas.month == 1), (ay_bas.month - 2) % 12 + 1, 1),
                                                                            date.today())}
        mevcut.discard("")
        adim = 0
        for a_bas, a_bit in araliklar:
            if a_bas.year != yil:                   # Aralık dönemi: Ocak 1-15 yeni yılın Luca dönemine kaydedilmeli
                try:
                    luca.firma_sec(pg, sirket_id, a_bas.year, a_bas.month, gunluk, iptal)
                    fr = luca.utts_ekrani(pg, sirket_id)
                    if not luca.utts_plakalar(fr):
                        raise luca.LucaHatasi(f"{a_bas.year} döneminde UTTS plakası tanımlı değil")
                    mevcut |= {str(k.get("ettn") or "").strip() for k in luca.utts_liste(fr, a_bas, min(date.today(), date(a_bas.year, 1, 31)))}
                    mevcut.discard("")
                except luca.LucaHatasi as e:
                    notlar.append(f"{a_bas:%d.%m}-{a_bit:%d.%m.%Y} getirilemedi ({str(e)[:80]}); {a_bas.year} döneminde ayrıca çalıştırın.")
                    continue
            haftalar = luca.utts_haftalar(fr, a_bas, a_bit)
            toplam = len(haftalar) * len(plakalar)
            gunluk(f"  {ad}: GİB'den {a_bas:%d.%m.%Y} - {a_bit:%d.%m.%Y} ({len(plakalar)} plaka, {len(haftalar)} hafta) getiriliyor…")
            for h in haftalar:
                for p in plakalar:
                    if iptal():
                        raise luca.Iptal("İptal edildi.")
                    adim += 1
                    r = luca.utts_getir(fr, p["plaka"], p.get("binekMi"), h["start"], h["end"], mevcut)
                    mevcut.update(r.get("ettnler") or [])
                    gelen += r["gelen"]
                    kaydedilen += r["kaydedilen"]
                    atlanan += r["atlanan"]
                    if r["hata"] and not re.search(r"bulunama|kayıt yok|bulunmamakta", str(r["hata"]), re.I):
                        hatalar.append(f"{p['plaka']} {h['start'][8:10]}.{h['start'][5:7]}-{h['end'][8:10]}.{h['end'][5:7]}: {str(r['hata'])[:80]}")
                    hatalar += [f"{p['plaka']}: {m[:80]}" for m in r["kayit_hatalari"]]
            gunluk(f"  {ad}: {a_bas:%d.%m}-{a_bit:%d.%m.%Y} tamamlandı ({adim} istek; şimdiye dek {gelen} fiş geldi, {kaydedilen} yeni kaydedildi).")
        if any(a.year != yil for a, _ in araliklar):  # listelemeden önce beyan döneminin Luca dönemine geri dön
            luca.firma_sec(pg, sirket_id, yil, ay, gunluk, iptal)
            fr = luca.utts_ekrani(pg, sirket_id)
        notlar.append(f"GİB'den getir: {len(plakalar)} plaka, " + ", ".join(f"{a:%d.%m}-{b:%d.%m.%Y}" for a, b in araliklar)
                      + f"; {gelen} fiş geldi, {kaydedilen} yeni fiş Luca'ya kaydedildi, {atlanan} fiş zaten Luca'daydı.")

    # 2) Dönem listesi + ünvan sorgulama (yalnız ünvanı boş kayıtlar)
    liste = luca.utts_liste(fr, ay_bas, ay_bit)
    if liste and any(not str(k.get("mukellefUnvan") or "").strip() for k in liste):
        u = luca.utts_unvan_sorgula(fr, sirket_id, liste)
        notlar.append(f"Ünvan sorgulama: {u['yazilan']} fişe istasyon ünvanı yazıldı"
                      + (f"; ünvanı bulunamayan VKN: {', '.join(u['bulunamayan'][:5])}" if u["bulunamayan"] else "") + ".")
        hatalar += [f"ünvan {m[:80]}" for m in u["hatalar"]]
        liste = luca.utts_liste(fr, ay_bas, ay_bit)
    kolonlar, tablo = utts_tablosu(liste)
    if not tablo:
        durum = "hata" if hatalar else "kayit_yok"
        return emukellef._sonuc(ad, kapsam, durum, " ".join(notlar + [f"{AYLAR[ay - 1]} {yil} için Luca'da UTTS fişi yok."]
                                                            + ([f"Sorun: {'; '.join(hatalar[:5])}"] if hatalar else [])), kolonlar), None

    # 3) Özet + Excel (Luca'nın kendi ayrıntılı Excel çıktısı)
    binek = [r for r in tablo if r[2] == "Binek"]
    t_kdv, b_kdv = sum(r[11] for r in tablo), sum(r[11] for r in binek)
    mesaj = (f"{len(tablo)} fiş; matrah {_tl(sum(r[9] for r in tablo))}, KDV {_tl(t_kdv)}, toplam {_tl(sum(r[12] for r in tablo))}. "
             f"Binek araç (Luca plaka tanımına göre): {len(binek)} fiş, KDV {_tl(b_kdv)} — binek araç giderlerinde gider kısıtlaması "
             f"ve KDV indirimi açısından kontrol edin; diğer araçlar {len(tablo) - len(binek)} fiş, KDV {_tl(t_kdv - b_kdv)}.")
    unvansiz = sum(1 for r in tablo if not r[4])
    if unvansiz:
        mesaj += f" {unvansiz} fişte istasyon ünvanı boş."
    fissiz = sum(1 for r in tablo if not r[15])
    mesaj += f" {len(tablo) - fissiz} fiş muhasebeleşmiş, {fissiz} muhasebeleşmemiş."
    dosya = None
    try:
        bayt = luca.utts_excel(ctx, pg, liste)
        if bayt[:2] == b"PK":
            dosya = {"tur": "utts", "bayt": bayt, "bas": ay_bas, "bit": ay_bit}
        else:
            hatalar.append("Luca'nın UTTS Excel'i beklenen biçimde (xlsx) gelmedi; ayrıntılar sonuç dosyasındaki 'Luca UTTS' sayfasında.")
    except luca.LucaHatasi as e:
        hatalar.append(f"Luca UTTS Excel'i alınamadı ({str(e)[:100]}); ayrıntılar sonuç dosyasındaki 'Luca UTTS' sayfasında.")
    sonuc = emukellef._sonuc(ad, kapsam, "hata" if hatalar else "veri",
                             " ".join(notlar + [mesaj] + ([f"Sorun: {'; '.join(hatalar[:5])}"] if hatalar else [])), kolonlar, tablo)
    return sonuc, dosya
