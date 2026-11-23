"""Fatura dökümü: indirilen UBL-TR fatura XML'lerinden (e-fatura ve e-arşiv) Excel dökümü.

Tutarlar faturanın kendi XML'inden okunur (E-Mükellef liste kolonları oranlara göre güvenilir değil).
XML yoksa liste satırındaki değerler yazılır, oran kırılımı boş bırakılır ve "XML yok" notu düşülür.
Bellekte PDF/XML tutulmaz: her fatura için yalnız çözümlenmiş küçük bir kayıt (sözlük) saklanır.
"""
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

KDV_KODU = "0015"
SAYFALAR = [("gelen", "efatura", "Gelen E-Fatura"), ("giden", "efatura", "Giden E-Fatura"),
            ("gelen", "earsiv", "Gelen E-Arşiv"), ("giden", "earsiv", "Giden E-Arşiv")]


# ================================================================ XML okuma
def _ad(el):
    return el.tag.split("}")[1] if "}" in el.tag else el.tag


def _cocuk(el, ad):
    return next((c for c in el if _ad(c) == ad), None) if el is not None else None


def _yol(el, *adlar):
    for a in adlar:
        el = _cocuk(el, a)
        if el is None:
            return None
    return el


def _onar(s):
    """GİB portal e-arşiv XML'lerinde metin çift UTF-8 kodlanmış gelebiliyor ('ÅÄ°' -> 'Şİ'); yalnız o desen varsa düzeltir."""
    if not s or not re.search("[Â-Å][-¿]", s):
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _metin(el):
    return _onar((el.text or "").strip()) if el is not None else ""


def _tutar(el):
    t = _metin(el)
    if not t:
        return None
    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _taraf(parti):
    """AccountingSupplierParty / AccountingCustomerParty -> (VKN/TCKN, ünvan)."""
    p = _cocuk(parti, "Party")
    if p is None:
        return "", ""
    kimlik = ""
    for pi in p:
        if _ad(pi) == "PartyIdentification":
            i = _cocuk(pi, "ID")
            if i is not None and (i.attrib.get("schemeID") or "").upper() in ("VKN", "TCKN"):
                kimlik = _metin(i)
                break
    unvan = _metin(_yol(p, "PartyName", "Name"))
    if not unvan:
        kisi = _cocuk(p, "Person")
        unvan = " ".join(x for x in (_metin(_cocuk(kisi, "FirstName")), _metin(_cocuk(kisi, "FamilyName"))) if x)
    return kimlik, unvan


def _kok(bayt):
    try:
        kok = ET.fromstring(bayt)
    except ET.ParseError as e:
        raise ValueError(f"XML okunamadı: {e}")
    if _ad(kok) != "Invoice":
        raise ValueError("UBL fatura değil")
    return kok


def xml_dogrula(bayt, belge_no, firma_vkn=None, yon=None):
    """Fatura XML'i bu belge mi ve bu firmaya mı ait? (doğru_mu, neden)"""
    try:
        kok = _kok(bayt)
    except ValueError as e:
        return False, str(e)
    no = _metin(_cocuk(kok, "ID"))
    if no != belge_no:
        return False, f"belge no tutmuyor ({no})"
    if firma_vkn and yon in ("gelen", "giden"):
        parti = "AccountingCustomerParty" if yon == "gelen" else "AccountingSupplierParty"
        vkn, _ = _taraf(_cocuk(kok, parti))
        if vkn and vkn != str(firma_vkn):
            return False, f"firma VKN'si faturada {'alıcı' if yon == 'gelen' else 'satıcı'} değil"
    return True, ""


def xml_coz(bayt):
    """UBL fatura XML'i -> sözlük (yalnız belge düzeyi vergiler; satır vergileri toplanmaz). Okunamazsa ValueError."""
    kok = _kok(bayt)
    s_vkn, s_ad = _taraf(_cocuk(kok, "AccountingSupplierParty"))
    a_vkn, a_ad = _taraf(_cocuk(kok, "AccountingCustomerParty"))
    lmt = _cocuk(kok, "LegalMonetaryTotal")
    kdv, diger = {}, {}
    for tt in (c for c in kok if _ad(c) == "TaxTotal"):
        for st in (c for c in tt if _ad(c) == "TaxSubtotal"):
            kod = _metin(_yol(st, "TaxCategory", "TaxScheme", "TaxTypeCode"))
            ad = _metin(_yol(st, "TaxCategory", "TaxScheme", "Name")) or kod
            oran = _tutar(_cocuk(st, "Percent"))
            matrah, vergi = _tutar(_cocuk(st, "TaxableAmount")) or Decimal(0), _tutar(_cocuk(st, "TaxAmount")) or Decimal(0)
            if kod == KDV_KODU or (not kod and "KDV" in ad.upper()):
                anahtar = format(oran.normalize(), "f") if oran is not None else "?"
                m, v = kdv.get(anahtar, (Decimal(0), Decimal(0)))
                kdv[anahtar] = (m + matrah, v + vergi)
            else:
                diger[ad] = diger.get(ad, Decimal(0)) + vergi
    tevkifat, tev_matrah, tev_kod = Decimal(0), Decimal(0), []
    for wt in (c for c in kok if _ad(c) == "WithholdingTaxTotal"):
        tevkifat += _tutar(_cocuk(wt, "TaxAmount")) or Decimal(0)
        for st in (c for c in wt if _ad(c) == "TaxSubtotal"):
            tev_matrah += _tutar(_cocuk(st, "TaxableAmount")) or Decimal(0)
            kod = _metin(_yol(st, "TaxCategory", "TaxScheme", "TaxTypeCode"))
            oran = _metin(_cocuk(st, "Percent"))
            if kod:
                tev_kod.append(f"{kod}" + (f" (%{oran})" if oran else ""))
    return {
        "ettn": _metin(_cocuk(kok, "UUID")), "no": _metin(_cocuk(kok, "ID")), "tarih": _metin(_cocuk(kok, "IssueDate")),
        "senaryo": _metin(_cocuk(kok, "ProfileID")), "tip": _metin(_cocuk(kok, "InvoiceTypeCode")),
        "para": _metin(_cocuk(kok, "DocumentCurrencyCode")) or "TRY",
        "kur": _metin(_yol(kok, "PricingExchangeRate", "CalculationRate")),
        "satici_vkn": s_vkn, "satici": s_ad, "alici_vkn": a_vkn, "alici": a_ad,
        "mal_hizmet": _tutar(_cocuk(lmt, "LineExtensionAmount")), "iskonto": _tutar(_cocuk(lmt, "AllowanceTotalAmount")),
        "vergi_haric": _tutar(_cocuk(lmt, "TaxExclusiveAmount")), "vergi_dahil": _tutar(_cocuk(lmt, "TaxInclusiveAmount")),
        "odenecek": _tutar(_cocuk(lmt, "PayableAmount")), "kdv": kdv, "diger_vergi": diger,
        "tevkifat": tevkifat, "tevkifat_kodu": ", ".join(tev_kod), "tevkifat_matrah": tev_matrah,
    }


def _para(p):
    """E-Mükellef listesi 'TL', XML 'TRY' yazabilir: ikisi de Türk lirası."""
    p = str(p or "").strip().upper()
    return "TRY" if p in ("", "TL", "TRL", "TRY", "₺") else p


def kayit_olustur(yon, tur, belge_no, xml, liste, dosya="", durum=""):
    """Döküm için tek fatura kaydı. xml: bayt veya None; liste: E-Mükellef liste alanları (yedek)."""
    s = {"yon": yon, "tur": tur, "dosya": dosya, "durum": durum}
    if xml:
        try:
            s.update(xml_coz(xml))
            s["para"] = _para(s.get("para"))
            s["kaynak"] = "XML"
            return s
        except ValueError as e:
            s["kaynak"] = f"liste ({e})"
    else:
        s["kaynak"] = "liste (XML yok)"
    l = liste or {}
    s.update({"no": belge_no, "tarih": l.get("tarih", ""), "tip": l.get("tip", ""), "senaryo": l.get("senaryo", ""),
              "para": _para(l.get("para")), "vergi_haric": l.get("vergi_haric"), "odenecek": l.get("odenecek"),
              "kdv": {}, "diger_vergi": {}, "tevkifat": None,
              "satici_vkn": l.get("satici_vkn", ""), "satici": l.get("satici", ""),
              "alici_vkn": l.get("alici_vkn", ""), "alici": l.get("alici", "")})
    return s


# ================================================================ Excel
def _f(d):
    if isinstance(d, Decimal):
        return float(d.quantize(Decimal("0.01")))
    return d


def _oranlar(satirlar):
    oran = set()
    for s in satirlar:
        oran |= set(s.get("kdv", {}).keys())
    return sorted(oran, key=lambda x: (x == "?", float(x) if x != "?" else 0))


def _diger_adlar(satirlar):
    ad = []
    for s in satirlar:
        for k in s.get("diger_vergi", {}):
            if k not in ad:
                ad.append(k)
    return ad


def excel(firma, donem_adi, kayitlar, calistirilan):
    """kayitlar: kayit_olustur() sözlükleri; calistirilan: {(yon, tur)} çalıştırılan sorgular.
    Döner: (xlsx baytı, içerik imzası, notlar)."""
    notlar = []
    wb = Workbook()
    ozet = wb.active
    ozet.title = "KDV Özeti"
    kalin, lacivert = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3864")
    ozet.append([f"{firma} — {donem_adi} fatura dökümü (tutarlar fatura XML'lerinden)"])
    ozet["A1"].font = Font(bold=True, size=13)
    ozet.append([])
    ozet.append(["Tür", "Fatura tipi", "Para birimi", "KDV oranı", "Fatura adedi", "Matrah", "KDV", "Tevkifat (KDV)"])
    for h in ozet[3]:
        h.font, h.fill = kalin, lacivert
    imza = [firma, donem_adi]
    for (yon, tur, baslik) in SAYFALAR:
        satirlar = sorted((k for k in kayitlar if k["yon"] == yon and k["tur"] == tur),
                          key=lambda s: (s.get("tarih") or "", s.get("no") or ""))
        oranlar, digerler = _oranlar(satirlar), _diger_adlar(satirlar)
        karsi = "Satıcı" if yon == "gelen" else "Alıcı"
        kolonlar = (["Tarih", "Belge no", "ETTN", "Senaryo", "Tip", f"{karsi} VKN/TCKN", karsi, "Para birimi", "Kur",
                     "Mal/hizmet", "İskonto", "Vergi hariç"]
                    + [x for o in oranlar for x in (f"Matrah %{o}", f"KDV %{o}")] + ["KDV toplam"] + digerler
                    + ["Tevkifat (KDV)", "Tevkifat kodu", "Vergi dahil", "Ödenecek", "Kaynak", "İndirme", "Dosya"])
        ws = wb.create_sheet(baslik)
        ws.append(kolonlar)
        for h in ws[1]:
            h.font, h.fill = kalin, lacivert
            h.alignment = Alignment(wrap_text=True, vertical="center")
        if (yon, tur) not in calistirilan:
            ws.append(["Bu sorgu çalıştırılmadı."])
            ozet.append([baslik, "", "", "", "çalıştırılmadı", "", "", ""])
            continue
        grup = {}
        for s in satirlar:
            kdv = s.get("kdv", {})
            kdv_toplam = sum((v for _, v in kdv.values()), Decimal(0)) if kdv else None
            vkn, ad = (s.get("satici_vkn"), s.get("satici")) if yon == "gelen" else (s.get("alici_vkn"), s.get("alici"))
            satir = ([s.get("tarih"), s.get("no"), s.get("ettn", ""), s.get("senaryo", ""), s.get("tip", ""), vkn, ad,
                      s.get("para", ""), s.get("kur", ""), _f(s.get("mal_hizmet")), _f(s.get("iskonto")), _f(s.get("vergi_haric"))]
                     + [_f(x) for o in oranlar for x in kdv.get(o, (None, None))] + [_f(kdv_toplam)]
                     + [_f(s.get("diger_vergi", {}).get(d)) for d in digerler]
                     + [_f(s.get("tevkifat")), s.get("tevkifat_kodu", ""), _f(s.get("vergi_dahil")), _f(s.get("odenecek")),
                        s.get("kaynak"), s.get("durum", ""), s.get("dosya", "")])
            ws.append(satir)
            imza.append([baslik] + [str(x) for x in satir[:-1]])
            tip, para = s.get("tip") or "?", s.get("para") or "?"
            for o, (m, v) in kdv.items():
                a, mm, vv, tv = grup.get((tip, para, o), (0, Decimal(0), Decimal(0), Decimal(0)))
                grup[(tip, para, o)] = (a + 1, mm + m, vv + v, tv)
            if s.get("tevkifat"):
                a, mm, vv, tv = grup.get((tip, para, "tevkifat"), (0, Decimal(0), Decimal(0), Decimal(0)))
                grup[(tip, para, "tevkifat")] = (a + 1, mm, vv, tv + s["tevkifat"])
        if satirlar:
            toplam_satiri = ["TOPLAM"] + [""] * (len(kolonlar) - 1)
            paralar = {s.get("para") or "?" for s in satirlar}
            for i, k in enumerate(kolonlar):
                if i >= 9 and k not in ("Tevkifat kodu", "Kaynak", "İndirme", "Dosya"):
                    degerler = [r[i].value for r in ws.iter_rows(min_row=2, max_row=ws.max_row) if isinstance(r[i].value, (int, float))]
                    if degerler and len(paralar) == 1:
                        toplam_satiri[i] = round(sum(degerler), 2)
            if len(paralar) > 1:
                toplam_satiri[1] = "farklı para birimleri var; toplam alınmadı"
            ws.append(toplam_satiri)
            for h in ws[ws.max_row]:
                h.font = Font(bold=True)
            for (tip, para, o), (a, m, v, tv) in sorted(grup.items()):
                if o == "tevkifat":
                    ozet.append([baslik, tip, para, "tevkifat", a, "", "", _f(tv)])
                else:
                    ozet.append([baslik, tip, para, f"%{o}", a, _f(m), _f(v), ""])
            ozet.append([baslik + " — toplam", "", "", "", len(satirlar), "", "", ""])
            ozet[ozet.max_row][0].font = Font(bold=True)
            xml_yok = sum(1 for s in satirlar if s.get("kaynak") != "XML")
            if xml_yok:
                notlar.append(f"{baslik}: {xml_yok} faturanın XML'i yok; tutarları E-Mükellef listesinden yazıldı, oran kırılımı yok "
                              "(KDV özetinde bu faturalar eksik).")
            if any((s.get("para") or "TRY") != "TRY" for s in satirlar):
                notlar.append(f"{baslik}: dövizli fatura var; tutarlar fatura para birimindedir, kur kolonuna bakın.")
            if any(str(s.get("tip", "")).upper() == "IADE" for s in satirlar):
                notlar.append(f"{baslik}: iade faturası var; KDV özetinde ayrı satırda gösterildi.")
        else:
            ws.append(["Bu dönem fatura yok."])
            ozet.append([baslik, "", "", "", 0, "", "", ""])
        ws.freeze_panes = "C2"
    if notlar:
        ozet.append([])
        ozet.append(["Notlar"])
        ozet[ozet.max_row][0].font = Font(bold=True)
        for n in notlar:
            ozet.append([n])
    for ws in wb.worksheets:
        for satir in ws.iter_rows(min_row=2):
            for h in satir:
                if isinstance(h.value, float):
                    h.number_format = "#,##0.00"
        for i, kol in enumerate(ws.columns, start=1):
            uz = max((len(str(h.value)) for h in kol if h.value is not None and h.row > 1), default=8)
            ws.column_dimensions[get_column_letter(i)].width = min(max(uz + 2, 9), 50)
    imza.append(["__sonucu_olan_turler__"] + sorted(f"{y}/{t}" for y, t in calistirilan))     # "çalıştırılmadı" -> "fatura yok" farkı da içeriktir
    icerik = hashlib.sha256(json.dumps(imza, ensure_ascii=False).encode()).hexdigest()
    wb.properties.description = "icerik:" + icerik
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue(), icerik, notlar
