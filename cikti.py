"""Çıktıların firma klasörüne kaydı. Kural: hiçbir dosyanın üzerine yazılmaz.

Klasör düzeni (kdv-genel skill'i):
    D:\\Claude\\Firmalar\\<Firma>\\KDV\\<YYYY-AA>\\01_Kaynaklar\\E-Mukellef\\   sorgu sonuçları, gelen e-arşiv PDF/XML
    D:\\Claude\\Firmalar\\<Firma>\\KDV\\<YYYY-AA>\\04_Beyannameler\\            o dönemin beyanname/tahakkukları
"""
import copy
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

FIRMALAR = Path(r"D:\Claude\Firmalar")
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
DURUM_ADI = {"veri": "Veri var", "kayit_yok": "Kayıt yok", "hata": "HATA", "calistirilmadi": "Çalıştırılmadı"}
SAYFA_ADI = {"Luca UTTS Akaryakıt Giderleri": "Luca UTTS", "KDV 2 Ödeme Sorgulama": "KDV2 Ödeme", "Yeni Nesil ÖKC Aylık Satış Rapor Bildirimleri": "ÖKC (Z raporu)",
             "Toplu POS Sorgulama": "POS", "TÖDEB Bilgileri": "TÖDEB", "109 - Sorumlu Sıfatıyla Ödenen KDV": "109",
             "İthalde Ödenen KDV": "İthalat KDV", "Araç Bilgisi (mevcut)": "Araçlar", "Araç Alım/Satım Bilgileri": "Araç Alım-Satım",
             "Önceki dönem KDV1/KDV2 beyanname ve tahakkuk": "Beyannameler", "Gelen E-Arşiv Faturalar": "Gelen E-Arşiv",
             "MUHSGK beyanname ve tahakkuk": "MUHSGK", "E-Arşiv Mükellef Adına Düzenlenenler": "Adına Düzenlenenler",
             "Araç Bilgisi (geçmiş)": "Araçlar (geçmiş)", "Giden E-Arşiv Faturalar": "Giden E-Arşiv",
             "Gelen E-Faturalar": "Gelen E-Fatura", "Giden E-Faturalar": "Giden E-Fatura",
             "E-Arşiv Portal İptal/İtiraz Talepleri": "İptal-İtiraz", "Vergi Borcu": "Vergi Borcu",
             "Ödeme / Alındı (tüm vergiler)": "Ödeme-Alındı", "GİB Tahsilat Bilgileri": "Tahsilat",
             "GİB Gelen Evraklar": "Gelen Evrak", "GİB Bildirimlerim": "Bildirimler",
             "Gelen Karşıt İnceleme Tutanakları": "Karşıt İnceleme", "E-Tebligatlar": "E-Tebligat",
             "Luca Defteri Kebir": "Luca Kebir (dönem)", "Luca Mizan (seçilen ay)": "Luca Mizan (ay)",
             "Luca Mizan (01.01 - ay sonu)": "Luca Mizan (yıl)", "Luca Akıllı Entegrasyon Alış Belgeleri": "Luca Akıllı Ent. Alış",
             "Luca Akıllı Entegrasyon Satış Belgeleri": "Luca Akıllı Ent. Satış", "Luca Bordro Dökümü": "Luca Bordro"}
AD_SINIRI = 150          # uzantı ve sürüm eki dahil dosya adı
YOL_SINIRI = 240         # tam yol


# ================================================================ klasörler ve adlar
def firma_klasoru(klasor):
    """Firma klasörünü doğrular; yeni firma klasörü AÇMAZ (yapı bozulmasın diye)."""
    k = str(klasor or "").strip()
    if not k or any(c in k for c in '\\/:*?"<>|') or k in (".", ".."):
        raise ValueError(f"Geçersiz firma klasörü adı: {klasor!r}")
    yol = FIRMALAR / k
    if not yol.is_dir():
        raise FileNotFoundError(f"Firma klasörü yok: {yol} (yeni firma klasörünü elle açın veya firmalar.json'u düzeltin)")
    return yol


def donem_klasoru(klasor, yil, ay, *alt, gunluk=print):
    yol = firma_klasoru(klasor) / "KDV" / f"{yil}-{ay:02d}"
    for a in alt:
        yol = yol / a
    if not yol.is_dir():
        yol.mkdir(parents=True)
        gunluk(f"Klasör açıldı: {yol}")
    return yol


def temiz_ad(metin, sinir=None):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", str(metin or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    if sinir and len(s) > sinir:
        s = s[:sinir].rstrip(" .")
    return s


def _bos_ad(klasor, ad, uzantilar):
    """Verilen uzantıların HİÇBİRİ için var olmayan bir ad kökü (ek dahil sınır içinde)."""
    zaman = datetime.now().strftime("%H-%M-%S")
    en_uzun_uzanti = max(len(u) for u in uzantilar)
    adaylar = [ad, f"{ad} ({zaman})"] + [f"{ad} ({zaman}-{i})" for i in range(2, 100)]
    for kok in adaylar:
        if len(kok) + en_uzun_uzanti > AD_SINIRI or len(str(klasor / kok)) + en_uzun_uzanti > YOL_SINIRI:
            raise ValueError(f"Dosya adı/yolu çok uzun: {klasor / kok}")
        if not any((klasor / f"{kok}{u}").exists() for u in uzantilar):
            return kok
    raise FileExistsError(f"Uygun dosya adı bulunamadı: {klasor / ad}")


def _kisalt_ad(klasor, sabit_bas, degisken, sabit_son, uzanti_payi=30):
    """Ad veya tam yol sınırı aşılırsa yalnız değişken kısmı (ör. satıcı adı) kısaltır; sabit kısımlar korunur."""
    sinir = min(AD_SINIRI, YOL_SINIRI - len(str(klasor)) - 1)
    pay = sinir - uzanti_payi - len(sabit_bas) - len(sabit_son)
    if pay < 0:
        return temiz_ad(f"{sabit_bas}{sabit_son}")
    return temiz_ad(f"{sabit_bas}{temiz_ad(degisken, pay)}{sabit_son}")


def guvenli_yaz(hedef: Path, bayt: bytes) -> Path:
    """Önce geçici dosyaya yazar, sonra yeniden adlandırır. Hedef varsa (Windows'ta rename hata verir) üzerine yazmaz."""
    gecici = hedef.with_name(f".~{uuid.uuid4().hex}.tmp")
    gecici.write_bytes(bayt)
    try:
        os.rename(gecici, hedef)
    except Exception:
        gecici.unlink(missing_ok=True)
        raise
    return hedef


def _surumler(klasor, ad, uzanti):
    """Aynı adın kendisi ve önceki saatli sürümleri."""
    desen = re.compile(re.escape(ad) + r"( \(\d{2}-\d{2}-\d{2}(-\d+)?\))?" + re.escape(uzanti) + r"$")
    return [p for p in klasor.glob("*" + uzanti) if desen.fullmatch(p.name)]


def belge_kaydet(klasor, ad, uzanti, bayt, ayni_mi=None, gunluk=print):
    """Tek dosya kaydı. Aynı içerik (ad + saatli sürümler arasında) varsa yazmaz; farklıysa saatli yeni ad verir.
    ayni_mi(eski_bayt, yeni_bayt): bayt farklı ama içerik aynı sayılabilecek dosyalar için (ör. PDF meta verisi)."""
    for p in _surumler(klasor, ad, uzanti):
        try:
            eski = p.read_bytes()
            if eski == bayt or (ayni_mi and ayni_mi(eski, bayt)):
                return p, "ayni"
        except OSError:
            continue
    kok = _bos_ad(klasor, ad, [uzanti])
    yol = guvenli_yaz(klasor / f"{kok}{uzanti}", bayt)
    if kok != ad:
        gunluk(f"UYARI: '{ad}{uzanti}' farklı içerikle zaten var; yeni ad verildi: {yol.name}. Eskisini kontrol edip silin.")
        return yol, "yeni_ad"
    return yol, "yeni"


# ================================================================ E-Mükellef sorgu sonuçları (JSON + Excel)
def _icerik_ozeti(sonuclar):
    s = copy.deepcopy(sonuclar)
    for x in s:
        x.pop("sorgu_zamani", None)
    return json.dumps(s, ensure_ascii=False, sort_keys=True)


def _excel(firma, donem_adi, sonuclar):
    wb = Workbook()
    ozet = wb.active
    ozet.title = "Özet"
    kalin, lacivert = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3864")
    ozet.append([f"{firma} — {donem_adi} E-Mükellef sorguları"])
    ozet["A1"].font = Font(bold=True, size=13)
    ozet.append([])
    ozet.append(["Sorgu", "Kapsam", "Sonuç", "Açıklama", "Toplam", "Sorgu zamanı"])
    for h in ozet[3]:
        h.font, h.fill = kalin, lacivert
    for x in sonuclar:
        toplam = x["toplam"] if isinstance(x["toplam"], (int, float)) else ""
        ozet.append([x["sorgu"], x["kapsam"], DURUM_ADI.get(x["durum"], x["durum"]), x["mesaj"], toplam, x["sorgu_zamani"]])
        hucre = ozet.cell(row=ozet.max_row, column=3)
        if x["durum"] == "hata":
            hucre.font = Font(bold=True, color="C00000")
        elif x["durum"] in ("kayit_yok", "calistirilmadi"):
            hucre.font = Font(color="808080")
        ozet.cell(row=ozet.max_row, column=4).alignment = Alignment(wrap_text=True, vertical="top")
    kullanilan = set()
    for x in sonuclar:
        if not x["kolonlar"]:
            continue
        ad = SAYFA_ADI.get(x["sorgu"], re.sub(r"[\[\]:*?/\\]", " ", x["sorgu"])[:31])
        while ad in kullanilan:
            ad = ad[:28] + f" {len(kullanilan)}"
        kullanilan.add(ad)
        ws = wb.create_sheet(ad)
        ws.append(x["kolonlar"])
        for h in ws[1]:
            h.font, h.fill = kalin, lacivert
            h.alignment = Alignment(wrap_text=True, vertical="center")
        for s in x["satirlar"]:
            ws.append(s)
        toplam_satiri = None
        if isinstance(x["toplam"], list):
            toplam_satiri = x["toplam"]
        elif isinstance(x["toplam"], (int, float)) and x["satirlar"]:
            i = x["kolonlar"].index("Tutar") if "Tutar" in x["kolonlar"] else None
            if i is not None:
                toplam_satiri = [""] * len(x["kolonlar"])
                toplam_satiri[0 if i else len(x["kolonlar"]) - 1] = "TOPLAM"
                toplam_satiri[i] = x["toplam"]
        if toplam_satiri:
            ws.append(toplam_satiri)
            for h in ws[ws.max_row]:
                h.font = Font(bold=True)
        if not x["satirlar"]:
            ws.append([DURUM_ADI.get(x["durum"], x["durum"]) + ": " + x["mesaj"]])
        ws.freeze_panes = "A2"
    for ws in wb.worksheets:
        for satir in ws.iter_rows(min_row=2):
            for h in satir:
                if isinstance(h.value, float):
                    h.number_format = "#,##0.00"
        for i, kol in enumerate(ws.columns, start=1):
            uz = max((len(str(h.value)) for h in kol if h.value is not None and h.row > 1), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(uz + 2, 10), 60)
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


def emukellef_kaydet(firma, klasor, yil, ay, sonuclar, ek_bilgi, gunluk=print):
    """Sonuçları JSON (iç özet için) + Excel olarak kaydeder. Aynı içerik bugün zaten kayıtlıysa yazmaz."""
    hedef = donem_klasoru(klasor, yil, ay, "01_Kaynaklar", "E-Mukellef", gunluk=gunluk)
    donem_adi = f"{AYLAR[ay - 1]} {yil}"
    ad = temiz_ad(f"{firma} {donem_adi} E-Mukellef Sorgulari (sorgu {datetime.now():%d.%m.%Y})")
    # Yalnız dönemin EN YENİ sonuç dosyası bugünküyse ve içeriği aynıysa yazılmaz (A -> B -> A'da son A da kaydedilir).
    bugunkuler = set(_surumler(hedef, ad, ".json"))
    try:
        en_yeni = max(hedef.glob("*E-Mukellef Sorgulari*.json"), key=lambda p: p.stat().st_mtime, default=None)
    except OSError:
        en_yeni = None
    for p in ([en_yeni] if en_yeni in bugunkuler else []):
        try:
            eski = json.loads(p.read_text(encoding="utf-8"))
            if _icerik_ozeti(eski.get("sonuclar", [])) == _icerik_ozeti(sonuclar):
                x = p.with_suffix(".xlsx")
                if not x.exists():          # önceki denemede Excel yazılamamışsa tamamla
                    guvenli_yaz(x, _excel(firma, donem_adi, sonuclar))
                    gunluk(f"Eksik Excel tamamlandı: {x.name}")
                else:
                    gunluk(f"Aynı sorgu sonuçları bugün zaten kayıtlı, yeniden yazılmadı: {p.name}")
                return [p, x]
        except Exception:
            continue
    kok = _bos_ad(hedef, ad, [".json", ".xlsx"])
    if kok != ad:
        gunluk(f"UYARI: Bugünkü sonuç dosyası farklı içerikle zaten var; yeni ad verildi: {kok}. Eskisini kontrol edip silin.")
    veri = {"schema_version": 1, "firma": firma, "donem": donem_adi, "yil": yil, "ay": ay,
            "olusturma": datetime.now().strftime("%d.%m.%Y %H:%M:%S"), **ek_bilgi, "sonuclar": sonuclar}
    j = guvenli_yaz(hedef / f"{kok}.json", json.dumps(veri, ensure_ascii=False, indent=2).encode("utf-8"))
    x = guvenli_yaz(hedef / f"{kok}.xlsx", _excel(firma, donem_adi, sonuclar))
    return [j, x]


# ================================================================ Beyanname / tahakkuk PDF
def _pdf_metni(bayt):
    from pypdf import PdfReader
    return "\n".join((s.extract_text() or "") for s in PdfReader(io.BytesIO(bayt)).pages)


def _pdf_ayni(eski, yeni):
    """Bayt farklı ama içerik aynı mı (meta veri farkı)? Metni boş/okunamayan PDF'ler aynı SAYILMAZ."""
    try:
        from pypdf import PdfReader
        a, b = PdfReader(io.BytesIO(eski)), PdfReader(io.BytesIO(yeni))
        if len(a.pages) != len(b.pages):
            return False
        ta, tb = _pdf_metni(eski), _pdf_metni(yeni)
        return bool(ta.strip()) and ta == tb
    except Exception:
        return False


def _tahakkuk_fis_no(bayt):
    """Tahakkuk fişinin numarası (ör. 20261124011ra0000203); metinde TEK aday varsa. Bulunamaz / kararsızsa None."""
    try:
        t = _pdf_metni(bayt)
    except Exception:
        return None
    if not re.search(r"TAHAKKUK\s+F[İI]Ş[İI]", t):
        return None
    adaylar = set(re.findall(r"(?<![0-9A-Za-z])\d{10}[0-9A-Za-z]{3}\d{7}(?![0-9A-Za-z])", t))
    return adaylar.pop() if len(adaylar) == 1 else None


def _tahakkuk_ayni(eski, yeni):
    """Aynı tahakkuk fişi mi? Metin aynıysa ya da fiş numaraları eşitse evet: aynı fiş GİB'den ve E-Mükellef'ten farklı sayfa
    düzeniyle gelir (metin tutmaz); numara eşitse ikinci kez kaydedilmez (kullanıcı isteği 25.11.2026)."""
    if _pdf_ayni(eski, yeni):
        return True
    a = _tahakkuk_fis_no(eski)
    return a is not None and a == _tahakkuk_fis_no(yeni)


def _belge_bilgisi(b):
    """PDF metninden düzeltme işareti, tarih (dd.mm.yyyy) ve işlem türü; okunamazsa None'lar."""
    try:
        t = re.sub(r"[ \t]+", " ", _pdf_metni(b["bayt"]))
    except Exception:
        return {"duzeltme": None, "tarih": None, "islem": None}
    if b["belge"] == "Beyanname":
        m = re.search(r"Onay Zaman[ıi]\s*:\s*(\d{2})[./](\d{2})[./](\d{4})", t)
        return {"duzeltme": bool(re.search(r"D[üu]zeltme Nedeni", t)), "tarih": ".".join(m.groups()) if m else None, "islem": None}
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s+\d{2}/\d{4}-\d{2}/\d{4}", t)      # Kabul tarihi + vergilendirme dönemi
    i = re.search(r"[İI]şlem T[üu]r[üu]\s*(\d{4})", t)
    return {"duzeltme": None, "tarih": ".".join(m.groups()) if m else None, "islem": i.group(1) if i else None}


def beyanname_adlari(belgeler):
    """Aynı (tür, dönem) için asıl + düzeltme gelirse düzeltmelere kalıcı ek verir: belge['ek'] = 'Düzeltme 22.10.2026'.
    Asıl belgelerin adı değişmez; ad her çalıştırmada aynı çıkar. Bilgi okunamazsa ek verilmez (çakışmada saatli ad + uyarı).
    Düzeltme beyannamesi: metinde 'Düzeltme Nedeni'; tarih = Onay Zamanı. Tahakkuk: kabul tarihi bir düzeltme beyannamesinin
    onay tarihine eşitse düzeltme sayılır; aynı gün asıl da varsa işlem türü 0010 (normal) değilse düzeltme."""
    for b in belgeler:
        b.update(_belge_bilgisi(b))
        b["ek"] = ""
    gruplar = {}
    for b in belgeler:
        gruplar.setdefault((b["tur"], b["donem_ay"], b["donem_yil"]), []).append(b)
    for grup in gruplar.values():
        byn = [b for b in grup if b["belge"] == "Beyanname"]
        duz_tarih = {b["tarih"] for b in byn if b["duzeltme"] and b["tarih"]}
        asil_tarih = {b["tarih"] for b in byn if b["duzeltme"] is False and b["tarih"]}
        for b in byn:
            if b["duzeltme"] and b["tarih"]:
                b["ek"] = f"Düzeltme {b['tarih']}"
        for b in grup:
            if b["belge"] != "Tahakkuk" or not b["tarih"] or b["tarih"] not in duz_tarih:
                continue
            if b["tarih"] not in asil_tarih or (b["islem"] and b["islem"] != "0010"):
                b["ek"] = f"Düzeltme {b['tarih']}"
    return belgeler


def beyanname_kaydet(firma, klasor, belge, gunluk=print):
    """belge: {tur: KDV1/KDV2, belge: Beyanname/Tahakkuk, donem_ay, donem_yil, vkn, bayt, ek?} -> kendi döneminin 04_Beyannameler'i.
    Birden fazla belge kaydedilecekse önce beyanname_adlari(belgeler) çağrılır (düzeltmeler ayrı adla)."""
    if belge["tur"] == "MUHSGK":      # kdv-genel kural 9: MUHSGK işleri <Firma>\Beyannameler\MUHSGK\<YYYY-AA>\
        hedef = firma_klasoru(klasor) / "Beyannameler" / "MUHSGK" / f"{belge['donem_yil']}-{belge['donem_ay']:02d}"
        if not hedef.is_dir():
            hedef.mkdir(parents=True)
            gunluk(f"Klasör açıldı: {hedef}")
    else:
        hedef = donem_klasoru(klasor, belge["donem_yil"], belge["donem_ay"], "04_Beyannameler", gunluk=gunluk)
    vkn = f" (VKN {belge['vkn']})" if belge.get("vkn") else ""
    ek = f" {belge['ek']}" if belge.get("ek") else ""
    ad = temiz_ad(f"{firma} {AYLAR[belge['donem_ay'] - 1]} {belge['donem_yil']} {belge['tur']} {belge['belge']}{ek}{vkn}")
    ayni_mi = _tahakkuk_ayni if belge["belge"] == "Tahakkuk" else _pdf_ayni
    return belge_kaydet(hedef, ad, ".pdf", belge["bayt"], ayni_mi=ayni_mi, gunluk=gunluk)


def verilen_beyannameler(klasor, yil, ay):
    """Dönemin kayıtlı BEYANNAME PDF'leri hangi türlerde var -> ['KDV1', 'KDV2', 'MUHSGK'] içinden bulunanlar.
    Yalnız okur; klasör açmaz. (KDV: dönemin 04_Beyannameler'i; MUHSGK: Beyannameler\\MUHSGK\\<YYYY-AA>.)"""
    try:
        firma = firma_klasoru(klasor)
    except (ValueError, OSError):
        return []
    bulunan = set()
    for hedef, turler in ((firma / "KDV" / f"{yil}-{ay:02d}" / "04_Beyannameler", ("KDV1", "KDV2")),
                          (firma / "Beyannameler" / "MUHSGK" / f"{yil}-{ay:02d}", ("MUHSGK",))):
        try:
            adlar = [p.name for p in hedef.glob("*.pdf")] if hedef.is_dir() else []
        except OSError:
            continue
        for tur in turler:
            if any(re.search(rf"(?<![0-9A-Za-z]){tur}\s+Beyanname(?![0-9A-Za-z])", a, re.I) for a in adlar):
                bulunan.add(tur)
    return [t for t in ("KDV1", "KDV2", "MUHSGK") if t in bulunan]


# ================================================================ E-fatura / e-arşiv PDF/XML
TUR_KLASOR = {("gelen", "earsiv"): "Gelen E-Arsiv", ("giden", "earsiv"): "Giden E-Arsiv",
              ("gelen", "efatura"): "Gelen E-Fatura", ("giden", "efatura"): "Giden E-Fatura"}


def fatura_klasoru(klasor, yil, ay, yon, tur, gunluk=print):
    return donem_klasoru(klasor, yil, ay, "01_Kaynaklar", "E-Mukellef", TUR_KLASOR[(yon, tur)], gunluk=gunluk)


def _fatura_on_ek(firma, yil, ay, yon, tur, belge_no):
    return f"{firma} {AYLAR[ay - 1]} {yil} {TUR_KLASOR[(yon, tur)]} Fatura {temiz_ad(belge_no)}"


def mevcut_faturalar(hedef, firma, yil, ay, yon, tur, belge_no):
    """Bu belge numarasıyla kaydedilmiş adaylar: [(xml yolu|None, pdf yolu|None)] (aynı ad kökü bir aday).
    DİKKAT: GİB portal e-arşiv numaraları satıcıya göre baştan başlar; aynı numara farklı satıcıda olabilir,
    doğru aday çağıran tarafından XML'deki karşı taraf VKN'siyle seçilir."""
    on_ek = _fatura_on_ek(firma, yil, ay, yon, tur, belge_no)
    kokler = {}
    if hedef.is_dir():
        for p in hedef.iterdir():
            if p.name.startswith(on_ek) and p.name[len(on_ek):len(on_ek) + 1] in (" ", ".") and p.suffix.lower() in (".xml", ".pdf"):
                kokler.setdefault(p.with_suffix(""), {})[p.suffix.lower()] = p
    return [(d.get(".xml"), d.get(".pdf")) for _, d in sorted(kokler.items())]


def mevcut_fatura(hedef, firma, yil, ay, yon, tur, belge_no):
    """Geriye uyumluluk: ilk aday."""
    a = mevcut_faturalar(hedef, firma, yil, ay, yon, tur, belge_no)
    return a[0] if a else (None, None)


def fatura_kaydet(firma, klasor, yil, ay, fatura, gunluk=print, yon="gelen", tur="earsiv"):
    """fatura: {belge_no, karsi, karsi_vkn, pdf, xml}. XML aynıysa (fatura değişmemiş) PDF dahil yeniden yazmaz;
    önceki denemede PDF eksik kalmışsa tamamlar. Döner: (kaydedilen yollar, durum)."""
    hedef = fatura_klasoru(klasor, yil, ay, yon, tur, gunluk)
    bas = _fatura_on_ek(firma, yil, ay, yon, tur, fatura["belge_no"]) + " "
    karsi = fatura.get("karsi") or fatura.get("satici") or fatura.get("karsi_vkn") or fatura.get("satici_vkn") or ""
    ad = _kisalt_ad(hedef, bas, karsi, "", uzanti_payi=25)
    if fatura.get("xml"):
        for p in _surumler(hedef, ad, ".xml"):
            try:
                if p.read_bytes() == fatura["xml"]:
                    pdf_yol = p.with_suffix(".pdf")
                    if fatura.get("pdf") and not pdf_yol.exists():      # önceki denemede PDF eksik kalmışsa tamamla
                        guvenli_yaz(pdf_yol, fatura["pdf"])
                        return [p, pdf_yol], "tamamlandi"
                    return [p], "ayni"
            except OSError:
                continue
    elif fatura.get("pdf"):                                             # XML yoksa PDF içeriğine bak (mükerrer olmasın)
        for p in _surumler(hedef, ad, ".pdf"):
            try:
                eski = p.read_bytes()
                if eski == fatura["pdf"] or _pdf_ayni(eski, fatura["pdf"]):
                    return [p], "ayni"
            except OSError:
                continue
    uzantilar = [u for u, b in ((".pdf", fatura.get("pdf")), (".xml", fatura.get("xml"))) if b]
    if not uzantilar:
        return [], "yok"
    kok = _bos_ad(hedef, ad, uzantilar)
    yollar = [guvenli_yaz(hedef / f"{kok}{u}", fatura["pdf"] if u == ".pdf" else fatura["xml"]) for u in uzantilar]
    if kok != ad:
        gunluk(f"UYARI: {fatura['belge_no']} için farklı içerikli dosya zaten var; yeni ad verildi: {kok}")
    return yollar, "yeni"


# ================================================================ Fatura dökümü Excel'i
def _excel_icerik_imzasi(yol):
    try:
        from openpyxl import load_workbook
        wb = load_workbook(yol, read_only=True)
        d = wb.properties.description or ""
        wb.close()
        return d[len("icerik:"):] if d.startswith("icerik:") else None
    except Exception:
        return None


def dokum_kaydet(firma, klasor, yil, ay, bayt, icerik, gunluk=print):
    """Aynı içerikli döküm (önceki sorgu tarihleri dahil) zaten varsa yazmaz."""
    hedef = donem_klasoru(klasor, yil, ay, "01_Kaynaklar", "E-Mukellef", gunluk=gunluk)
    on_ek = temiz_ad(f"{firma} {AYLAR[ay - 1]} {yil} Fatura Dokumu")
    # Yalnız EN YENİ dökümle karşılaştırılır: en yeni dosya her zaman güncel (birikimli) döküm olsun
    try:
        eskiler = sorted((p for p in hedef.glob("*.xlsx") if p.name.startswith(on_ek)), key=lambda p: p.stat().st_mtime)
        if eskiler and _excel_icerik_imzasi(eskiler[-1]) == icerik:
            gunluk(f"Aynı fatura dökümü zaten var, yeniden yazılmadı: {eskiler[-1].name}")
            return eskiler[-1], "ayni"
    except OSError:
        pass
    ad = temiz_ad(f"{on_ek} (sorgu {datetime.now():%d.%m.%Y})")
    kok = _bos_ad(hedef, ad, [".xlsx"])
    if kok != ad:
        gunluk(f"UYARI: bugünkü fatura dökümü farklı içerikle zaten var; yeni ad verildi: {kok}. Eskisini kontrol edin.")
    return guvenli_yaz(hedef / f"{kok}.xlsx", bayt), "yeni"


# ================================================================ E-tebligat
def tebligat_kaydet(firma, klasor, belge, pdf, gunluk=print):
    """belge: {zaman, aciklama, birim, bayt (imzalı paket)}; pdf: içinden çıkarılan PDF veya None.
    Firmalar\\<klasor>\\E-Tebligat\\ altına .imz (imzalı orijinal) + .pdf. Aynı imzalı dosya (adı farklı olsa da) varsa yazmaz,
    PDF'i eksikse tamamlar. Döner: (yollar, durum)"""
    hedef = firma_klasoru(klasor) / "E-Tebligat"
    if not hedef.is_dir():
        hedef.mkdir(parents=True)
        gunluk(f"Klasör açıldı: {hedef}")
    imzali = belge["bayt"]
    uz_ham = ".pdf" if imzali[:5] == b"%PDF-" else ".imz"
    for p in hedef.glob("*" + uz_ham):
        try:
            if p.read_bytes() == imzali:
                yollar = [p]
                if uz_ham == ".imz" and pdf and not p.with_suffix(".pdf").exists():
                    yollar.append(guvenli_yaz(p.with_suffix(".pdf"), pdf))
                    return yollar, "tamamlandi"
                return yollar, "ayni"
        except OSError:
            continue
    ad = _kisalt_ad(hedef, f"{firma} E-Tebligat {belge['zaman']:%Y-%m-%d %H-%M} ", f"{belge['aciklama']} {belge['birim']}", "", uzanti_payi=25)
    uzantilar = [uz_ham] + ([".pdf"] if uz_ham == ".imz" and pdf else [])
    kok = _bos_ad(hedef, ad, uzantilar)
    yollar = [guvenli_yaz(hedef / f"{kok}{uz_ham}", imzali)]
    if uz_ham == ".imz" and pdf:
        yollar.append(guvenli_yaz(hedef / f"{kok}.pdf", pdf))
    return yollar, "yeni"


# ================================================================ Kontrol özeti (Markdown)
def ozet_kaydet(klasor, yil, ay, firma, metin, gunluk=print):
    """KDV\\<dönem>\\02_Raporlar\\'<Firma> <Ay YYYY> Kontrol Ozeti (sorgu GG.AA.YYYY).md'. Sorgu zamanı satırı dışında
    aynı içerik bugün (veya önceki sorgu tarihlerinde) varsa yazmaz."""
    hedef = donem_klasoru(klasor, yil, ay, "02_Raporlar", gunluk=gunluk)
    on_ek = temiz_ad(f"{firma} {AYLAR[ay - 1]} {yil} Kontrol Ozeti")

    def govde(t):
        return "\n".join(s for s in t.splitlines() if not s.startswith("Sorgu zamanı:"))
    # Yalnız EN YENİ özetle karşılaştırılır: en yeni dosya her zaman güncel (birikimli) özet olsun
    try:
        eskiler = sorted((p for p in hedef.glob("*.md") if p.name.startswith(on_ek)), key=lambda p: p.stat().st_mtime)
        if eskiler and govde(eskiler[-1].read_text(encoding="utf-8")) == govde(metin):
            gunluk(f"Aynı kontrol özeti zaten var, yeniden yazılmadı: {eskiler[-1].name}")
            return eskiler[-1], "ayni"
    except OSError:
        pass
    ad = temiz_ad(f"{on_ek} (sorgu {datetime.now():%d.%m.%Y})")
    kok = _bos_ad(hedef, ad, [".md"])
    return guvenli_yaz(hedef / f"{kok}.md", metin.encode("utf-8")), "yeni"


# ================================================================ Luca dosyaları (kebir, mizan, bordro)
LUCA_KLASORU = "Luca"        # 01_Kaynaklar altında mizan dosyalarının klasörü (kullanıcı kararı 19.11.2026); kebir: Kebirler; bordro: Beyannameler\MUHSGK\<dönem>


def _xlsx_hucre_ozeti(bayt):
    """Excel'in ilk sayfasındaki hücre değerlerinin özeti. Luca aynı raporu her indirmede farklı baytla üretir
    (hücreler aynı); 'aynı içerik' karşılaştırması bu yüzden hücreler üzerinden yapılır."""
    import warnings
    from openpyxl import load_workbook
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = load_workbook(io.BytesIO(bayt), read_only=True)
    try:
        ws = wb.worksheets[0]
        ws.reset_dimensions()                    # Luca dosyalarında boyut bilgisi yanlış (A1)
        h = hashlib.sha256()
        for r in ws.iter_rows(values_only=True):
            h.update(repr(r).encode("utf-8"))
        return h.hexdigest()
    finally:
        wb.close()


def luca_kaydet(firma, klasor, yil, ay, tur, bayt, bas, bit, gunluk=print):
    """tur: 'kebir' | 'mizan' | 'hesap_plani' | 'utts' | 'bordro'; bas/bit: raporun tarih aralığı (date). Döner: (yol, 'yeni'|'ayni'|'yeni_ad').
    Aynı aralığın daha önce (başka gün de olsa) indirilmiş AYNI içerikli dosyası varsa yeniden yazılmaz; hiçbir dosyanın üzerine yazılmaz."""
    if tur == "kebir":
        hedef = donem_klasoru(klasor, yil, ay, "01_Kaynaklar", "Kebirler", gunluk=gunluk)
        on_ek = temiz_ad(f"{firma} Luca Defteri Kebir {bas:%Y-%m-%d}_{bit:%Y-%m-%d}")
    elif tur == "mizan":
        hedef = donem_klasoru(klasor, yil, ay, "01_Kaynaklar", LUCA_KLASORU, gunluk=gunluk)
        on_ek = temiz_ad(f"{firma} Luca Mizan {bas:%Y-%m-%d}_{bit:%Y-%m-%d}")
    elif tur == "hesap_plani":                  # firmanın o mali dönemde kullandığı hesap planı (kullanıcı isteği 20.11.2026)
        hedef = donem_klasoru(klasor, yil, ay, "01_Kaynaklar", LUCA_KLASORU, gunluk=gunluk)
        on_ek = temiz_ad(f"{firma} Luca Hesap Plani {bas:%Y-%m-%d}_{bit:%Y-%m-%d}")
    elif tur == "utts":                         # Luca UTTS ekranının ayrıntılı Excel'i (kullanıcı kararı 19.11.2026)
        hedef = donem_klasoru(klasor, yil, ay, "01_Kaynaklar", LUCA_KLASORU, gunluk=gunluk)
        on_ek = temiz_ad(f"{firma} Luca UTTS Akaryakit {bas:%Y-%m-%d}_{bit:%Y-%m-%d}")
    elif tur == "bordro":                       # kdv-genel kural 9: MUHSGK işleri <Firma>\Beyannameler\MUHSGK\<YYYY-AA>\
        hedef = firma_klasoru(klasor) / "Beyannameler" / "MUHSGK" / f"{yil}-{ay:02d}"
        if not hedef.is_dir():
            hedef.mkdir(parents=True)
            gunluk(f"Klasör açıldı: {hedef}")
        on_ek = temiz_ad(f"{firma} {AYLAR[ay - 1]} {yil} Luca Bordro Dokumu")
    else:
        raise ValueError(tur)
    ozet = _xlsx_hucre_ozeti(bayt)
    for p in sorted(hedef.glob("*.xlsx")):
        if p.name.startswith(on_ek + " (indirme "):
            try:
                if _xlsx_hucre_ozeti(p.read_bytes()) == ozet:
                    return p, "ayni"
            except Exception:
                continue
    def ayni(eski, _yeni):
        try:
            return _xlsx_hucre_ozeti(eski) == ozet
        except Exception:                           # bozuk/yarım eski dosya: aynı sayılmaz, yeni adla kaydedilir
            return False
    ad = temiz_ad(f"{on_ek} (indirme {datetime.now():%d.%m.%Y})")
    return belge_kaydet(hedef, ad, ".xlsx", bayt, ayni_mi=ayni, gunluk=gunluk)
