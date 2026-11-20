"""E-Mükellef sorguları (salt okunur; e-arşiv toplu sorgu, GİB'deki faturayı E-Mükellef'e alır).
Tarayıcının oturum çereziyle doğrudan HTTP isteği atar.

Her sorgu fonksiyonu bir sözlük döner:
    {sorgu, kapsam, durum: "veri" | "kayit_yok" | "hata" | "calistirilmadi", mesaj, kolonlar, satirlar, toplam, sorgu_zamani}
"kayit_yok" yalnız yanıt düzgün geldiğinde ve içinde gerçekten kayıt olmadığında verilir; beklenmedik
yanıt her zaman "hata"dır. Bir sorgunun hatası diğerlerini durdurmaz; oturum düşmüşse OturumYok yükselir.
"""
import base64
import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urlparse

# Adres ofise özeldir (https://<ofis>.emukellef.org); program açılışta ayarlardan okuyup adres_ayarla() ile verir (bkz. ayar.py).
# Tanımsızken hiçbir sayfayla eşleşmeyen, istek atılırsa hemen hata veren bir yer tutucudur.
ADRES = "https://adres-tanimsiz.invalid"
YONETIM = ADRES + "/emukellef/yonetim/"


def adres_ayarla(adres):
    """E-Mükellef adresini ('https://<sunucu>') ayarlar. Tarayıcı işçisi başlamadan ÖNCE çağrılır; çalışırken değiştirilmez
    (adres değişikliği programı yeniden başlatarak yapılır)."""
    global ADRES, YONETIM
    ADRES = str(adres).rstrip("/")
    YONETIM = ADRES + "/emukellef/yonetim/"


GIRIS_YOLU = "/emukellef/yonetim/giris"
AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz",
         "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
OKC_DETAY = "okc_bildirim_goruntule_detay/"
ISTEK_ARASI_SN = 0.3


class OturumYok(Exception):
    """E-Mükellef oturumu yok veya düşmüş."""


# ================================================================ genel yardımcılar
def _simdi():
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def _sayi(v):
    """API'nin noktalı ondalık değeri ("11294.61" veya sayı) -> float. Okunamazsa None."""
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v or "").strip()
    if not s:
        return 0.0
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _tr_sayi(v):
    """Türkçe biçimli tutar metni ("96.035,95", "3.500,00 TRY") -> float. Sayı gelirse olduğu gibi. Okunamazsa None."""
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v or "").strip()
    if not s or s.lower() == "null":
        return 0.0
    t = re.sub(r"[^\d,.\-]", "", s)
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", t):     # "3.500" = üç bin beş yüz
        t = t.replace(".", "")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _topla(degerler):
    try:
        return float(sum((Decimal(str(d)) for d in degerler if isinstance(d, (int, float))), Decimal("0")).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def _okunamayan(satirlar, kolon_indisleri):
    return sum(1 for s in satirlar for i in kolon_indisleri if s[i] is None)


def _sonuc(sorgu, kapsam, durum, mesaj="", kolonlar=None, satirlar=None, toplam=None):
    return {"sorgu": sorgu, "kapsam": kapsam, "durum": durum, "mesaj": mesaj,
            "kolonlar": kolonlar or [], "satirlar": satirlar or [], "toplam": toplam,
            "sorgu_zamani": _simdi()}


def calistirilmadi(sorgu, neden):
    return _sonuc(sorgu, "", "calistirilmadi", neden)


def _baslik(sayfa):
    return {"X-Requested-With": "XMLHttpRequest", "Referer": YONETIM + sayfa, "Origin": ADRES}


def _giris_sayfasi_mi(url):
    # Dikkat: ÖKC penceresinin adresi de ".../modal/giris" ile biter; yalnız gerçek giriş sayfası sayılır.
    return urlparse(url).path.rstrip("/") == GIRIS_YOLU


def _oturum_kontrol(yanit):
    if _giris_sayfasi_mi(yanit.url):
        raise OturumYok("E-Mükellef oturumu yok veya düşmüş; tarayıcıda giriş yapın.")


def _istek(fn, *a, **kw):
    """Kısa bekleme + 429/5xx için sınırlı tekrar."""
    for deneme in range(3):
        time.sleep(ISTEK_ARASI_SN)
        r = fn(*a, **kw)
        if r.status == 429 or r.status >= 500:
            time.sleep(10 * (deneme + 1))
            continue
        return r
    return r


def _post(ctx, sayfa, yol, form, zaman_asimi=120_000):
    """form: sözlük veya (ad, değer) listesi (tekrarlı alanlar için)."""
    adres = yol if yol.startswith("http") else YONETIM + yol       # panel dışındaki (ajax/…) adresler tam yazılır
    if isinstance(form, list):
        r = _istek(ctx.request.post, adres, data=urlencode(form), timeout=zaman_asimi,
                   headers={**_baslik(sayfa), "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
    else:
        r = _istek(ctx.request.post, adres, form=form, headers=_baslik(sayfa), timeout=zaman_asimi)
    _oturum_kontrol(r)
    if r.status != 200:
        raise RuntimeError(f"{yol}: HTTP {r.status}")
    return r


def _get(ctx, sayfa, yol, zaman_asimi=120_000):
    r = _istek(ctx.request.get, YONETIM + yol if not yol.startswith("http") else yol, headers=_baslik(sayfa), timeout=zaman_asimi)
    _oturum_kontrol(r)
    return r


def _ham_json(r, yol):
    try:
        return r.json()
    except Exception:
        if "SMMM Paneli" in r.text():
            raise OturumYok("E-Mükellef oturumu düşmüş; tarayıcıda yeniden giriş yapın.")
        raise RuntimeError(f"{yol}: JSON beklenirken başka yanıt geldi")


def _json(r, yol):
    j = _ham_json(r, yol)
    if not isinstance(j, dict) or "success" not in j:
        raise RuntimeError(f"{yol}: beklenmeyen yanıt yapısı")
    return j


def _tablo_json(r, yol):
    """DataTables yanıtı: {draw, recordsTotal, recordsFiltered, data:[...]}"""
    j = _ham_json(r, yol)
    if not isinstance(j, dict) or not isinstance(j.get("data"), list):
        raise RuntimeError(f"{yol}: liste beklenen yapıda gelmedi")
    toplam = j.get("recordsFiltered", j.get("recordsTotal"))
    if isinstance(toplam, int) and toplam != len(j["data"]):
        raise RuntimeError(f"{yol}: liste eksik geldi ({len(j['data'])}/{toplam})")
    return j["data"]


def _html_metin(v):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(v if v is not None else "")))).strip()


def _mesaj(j):
    d = j.get("data") if isinstance(j, dict) else None
    m = (d.get("msg") if isinstance(d, dict) else None) or (j.get("message") if isinstance(j, dict) else None)
    return _html_metin(m)


def _gecici_hata(m):
    """E-Mükellef'in GİB'e bağlanırken yaşadığı geçici hatalar (bir kez tekrar denenir)."""
    return bool(re.search(r"güvenlik kodu|giriş yapılamad|zaman aşımı|timeout|tekrar deneyin", str(m or ""), re.I))


def _json_tekrarli(ctx, sayfa, yol, form):
    """success=false ve geçici hata mesajı gelirse 5 sn bekleyip bir kez daha dener."""
    j = _json(_post(ctx, sayfa, yol, form), yol)
    if not j.get("success") and _gecici_hata(_mesaj(j)):
        time.sleep(5)
        j = _json(_post(ctx, sayfa, yol, form), yol)
    return j


def _kayit_yok_mesaji(m):
    return bool(re.search(r"bulunama|bulunmamakta|bulunmuyor|kayıt yok|kayit yok", str(m or ""), re.I))


def _tek_liste(d):
    """data içindeki listeyi bulur; birden fazla liste varsa belirsizlik hatası verir."""
    if isinstance(d, list):
        return d
    if not isinstance(d, dict):
        return None
    listeler = [v for k, v in d.items() if isinstance(v, list) and k != "mukellef"]
    if len(listeler) > 1:
        raise RuntimeError("Yanıtta birden fazla liste var; hangisinin kullanılacağı belirsiz.")
    return listeler[0] if listeler else None


def _genel_tablo(liste, atla=("secureId",)):
    """Yapısı önceden bilinmeyen liste -> kolonlar (tüm kayıtların anahtar birleşimi) + satırlar."""
    kolonlar = []
    for x in liste:
        if isinstance(x, dict):
            for k in x:
                if k not in atla and k not in kolonlar:
                    kolonlar.append(k)

    def deger(v):
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return _html_metin(v) if isinstance(v, str) else v
    return kolonlar, [[deger(x.get(k)) if isinstance(x, dict) else None for k in kolonlar] for x in liste]


def _sonraki_ay(ay, yil):
    return (1, yil + 1) if ay == 12 else (ay + 1, yil)


def oturum_var_mi(ctx, zaman_asimi=60_000):
    """Kimlik isteyen sayfaya sorar (yalnız sayfa adresine bakmak yetmez)."""
    try:
        r = ctx.request.get(YONETIM + "dashboard", timeout=zaman_asimi)
        if r.status != 200 or _giris_sayfasi_mi(r.url):
            return False
        return "Yönetim Paneli" in r.text()
    except Exception:
        return False


def odeme_ayi(ay, yil):
    """KDV dönemi -> KDV2 ödemesinin aranacağı ay (KDV1'in hazırlandığı ay)."""
    return _sonraki_ay(ay, yil)


# ================================================================ KDV2 ödeme
def kdv2_odeme(ctx, mid, ay, yil):
    o_ay, o_yil = odeme_ayi(ay, yil)
    sorgu = "KDV 2 Ödeme Sorgulama"
    kapsam = f"Ödeme ayı {AYLAR[o_ay - 1]} {o_yil}, vergi kodu 4017 (KDV tevkifatı)"
    sayfa = "emukellef/kdv2_sorgulama"
    yol = sayfa + "/sorgula"
    try:
        j = _json(_post(ctx, sayfa, yol, {"ay": f"{o_ay:02d}", "yil": str(o_yil), "id": str(mid), "vergikodu": "4017"}), yol)
        if not j.get("success"):
            return _sonuc(sorgu, kapsam, "hata", _mesaj(j) or "GİB yanıt vermedi")
        data = j.get("data")
        if not isinstance(data, dict) or "odemeleralindilar" not in data:
            return _sonuc(sorgu, kapsam, "hata", "Yanıtta ödeme listesi yok (yapı değişmiş olabilir).")
        kolonlar = ["Vergi dönemi", "Ödeme tarihi", "Tutar", "Belge no", "Ödeme türü", "Vergi dairesi", "Vergi"]
        satirlar = [[o.get("vergiDonemFormatted"), o.get("odemeTarihi"), _sayi(o.get("odemeMiktar")),
                     o.get("belgeNo"), o.get("odemeTuru"), o.get("vergiDairesiAdi"), o.get("vergiTurleri")]
                    for o in data["odemeleralindilar"] or []]
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Bu ödeme ayında 4017 ödemesi yok (geç/önceki ay ödemeleri bu sorguya girmez).", kolonlar)
        mesaj = f"{len(satirlar)} ödeme. Tutar damga vergisini (1048) içerebilir; KDV1 109'a yalnız KDV kısmı yazılır."
        if _okunamayan(satirlar, [2]):
            mesaj += " UYARI: okunamayan tutar var."
        return _sonuc(sorgu, kapsam, "veri", mesaj, kolonlar, satirlar, _topla(s[2] for s in satirlar))
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


# ================================================================ ÖKC (Z raporu)
class _TabloOkuyucu(HTMLParser):
    """<th> başlıklarını, <tr> satırlarının hücre metinlerini ve data-href değerlerini toplar."""

    def __init__(self):
        super().__init__()
        self.basliklar, self.satirlar = [], []
        self._satir, self._hucre, self._tur = None, [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self._satir = {"hucreler": [], "hrefler": []}
        elif tag in ("td", "th"):
            self._tur, self._hucre = tag, []
        if self._satir is not None and a.get("data-href"):
            self._satir["hrefler"].append(a["data-href"])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._tur:
            metin = re.sub(r"\s+", " ", "".join(self._hucre)).strip()
            if tag == "th":
                self.basliklar.append(metin)
            elif self._satir is not None:
                self._satir["hucreler"].append(metin)
            self._tur = None
        elif tag == "tr" and self._satir is not None:
            if self._satir["hucreler"]:
                self.satirlar.append(self._satir)
            self._satir = None

    def handle_data(self, data):
        if self._tur:
            self._hucre.append(data)


def _okc_detay_coz(href):
    parca = unquote(href.split(OKC_DETAY, 1)[1]).split("?")[0].strip()
    ham = base64.b64decode(parca + "=" * (-len(parca) % 4), validate=False)
    d = json.loads(ham.decode("utf-8"))
    if not isinstance(d, dict) or not isinstance(d.get("detayBilgi"), dict):
        raise ValueError("ÖKC detay verisi beklenen yapıda değil")
    return d


OKC_ALANLAR = [("okcfistutartutar", "Satış toplamı"), ("satisKdv0", "%0"), ("satisKdv1", "%1"),
               ("satisKdv8", "%8"), ("satisKdv10", "%10"), ("satisKdv18", "%18"), ("satisKdv20", "%20"),
               ("kdvToplam", "KDV toplamı"), ("bkkartodemetutar", "Kart"), ("nakitodemetutar", "Nakit"),
               ("yemekkcodemetutar", "Yemek kartı"), ("digerodemetutar", "Diğer ödeme"),
               ("faturatutartutar", "Fatura")]


def okc(ctx, mid, ay, yil):
    sorgu = "Yeni Nesil ÖKC Aylık Satış Rapor Bildirimleri"
    kapsam = f"Dönem {AYLAR[ay - 1]} {yil}"
    sayfa = "emukellef/yeni_nesil_okc_aylik_satis_rapor_bildirimleri"
    try:
        # Sorgu penceresinin açılışı (sayfadaki "Sorgula" düğmesinin yaptığı ilk istek)
        r = _get(ctx, sayfa, sayfa + f"/modal/giris?id={mid}")
        if r.status != 200:
            raise RuntimeError(f"ÖKC pencere açılışı: HTTP {r.status}")
        r = _post(ctx, sayfa, sayfa + "/okc_bildirim_goruntule", {"id": str(mid), "donemAy": f"{ay:02d}", "donemYil": str(yil)})
        html = r.text()
        okuyucu = _TabloOkuyucu()
        okuyucu.feed(html)
        if not any("Bildirim" in b for b in okuyucu.basliklar):
            metin = _html_metin(html)[:200]
            if _kayit_yok_mesaji(metin):
                return _sonuc(sorgu, kapsam, "kayit_yok", metin)
            return _sonuc(sorgu, kapsam, "hata", "Beklenen tablo gelmedi: " + (metin or "boş yanıt"))
        kolonlar = ["Bildirim zamanı", "Bildirim yöntemi", "Sicil no", "Rapor no"] + [ad for _, ad in OKC_ALANLAR]
        satirlar = []
        for s in okuyucu.satirlar:
            detay = next((h for h in s["hrefler"] if OKC_DETAY in h), None)
            if not detay:
                if len(s["hucreler"]) <= 1:   # "kayıt bulunamadı" gibi tek hücreli satır
                    continue
                raise ValueError("Detay bağlantısı olmayan ÖKC satırı: " + " | ".join(s["hucreler"])[:120])
            d = _okc_detay_coz(detay)
            b = d["detayBilgi"]
            h = s["hucreler"]
            satirlar.append([h[5] if len(h) > 5 else "", h[6] if len(h) > 6 else "", d.get("sicilNo") or "",
                             b.get("aylikSatisRaporNo") or ""] + [_sayi(b.get(k)) for k, _ in OKC_ALANLAR])
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Bu dönem için ÖKC bildirimi yok.", kolonlar)
        sayisal = list(range(4, len(kolonlar)))
        toplam = ["TOPLAM", "", "", ""] + [_topla(s[i] for s in satirlar) for i in sayisal]
        mesaj = f"{len(satirlar)} bildirim."
        if any(not s[2] for s in satirlar):
            mesaj += " Sicil no boş geldi; aynı cihazın tekrar bildirimi olabilir, kontrol edin."
        if _okunamayan(satirlar, sayisal):
            mesaj += " UYARI: okunamayan tutar var."
        return _sonuc(sorgu, kapsam, "veri", mesaj, kolonlar, satirlar, toplam)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


# ================================================================ POS ve TÖDEB (aynı yapı)
def _pos_benzeri(ctx, sorgu, sayfa, yol, liste_anahtari, alanlar, mid, ay, yil, iptal):
    kapsam = f"{AYLAR[ay - 1]} {yil}"
    try:
        j = None
        for _ in range(2):
            j = _json(_post(ctx, sayfa, yol, {"mukellef_id": str(mid), "ay": f"{ay:02d}", "yil": str(yil)}), yol)
            if j.get("success") or iptal():
                break
            time.sleep(5)
        if not j.get("success"):
            m = _mesaj(j)
            if _kayit_yok_mesaji(m):
                return _sonuc(sorgu, kapsam, "kayit_yok", m)
            return _sonuc(sorgu, kapsam, "hata", "İki denemede de başarısız: " + (m or "GİB yanıt vermedi"))
        data = j.get("data")
        if not isinstance(data, dict) or liste_anahtari not in data:
            return _sonuc(sorgu, kapsam, "hata", "Yanıtta liste yok (yapı değişmiş olabilir).")
        kolonlar = [ad for _, ad in alanlar] + ["Tutar"]
        satirlar = [[p.get(k) for k, _ in alanlar] + [_sayi(p.get("toplam"))] for p in data[liste_anahtari] or []]
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Bu ay kayıt yok.", kolonlar)
        mesaj = f"{len(satirlar)} satır." + (" UYARI: okunamayan tutar var." if _okunamayan(satirlar, [len(alanlar)]) else "")
        return _sonuc(sorgu, kapsam, "veri", mesaj, kolonlar, satirlar, _topla(s[-1] for s in satirlar))
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


def pos(ctx, mid, ay, yil, iptal=lambda: False):
    return _pos_benzeri(ctx, "Toplu POS Sorgulama", "emukellef/gib_pos_bilgileri", "emukellef/gib_pos_bilgileri/sorgula",
                        "arr_pos", [("pos_banka_adi", "Banka"), ("pos_banka_vkn", "Banka VKN"), ("pos_uye_isy", "Üye işyeri no")],
                        mid, ay, yil, iptal)


def todeb(ctx, mid, ay, yil, iptal=lambda: False):
    return _pos_benzeri(ctx, "TÖDEB Bilgileri", "todeb_bilgileri", "todeb_bilgileri/sorgula",
                        "arr_todeb", [("todeb_banka_adi", "Kuruluş"), ("todeb_banka_vkn", "Kuruluş VKN"), ("todeb_uye_isy", "Üye işyeri no")],
                        mid, ay, yil, iptal)


# ================================================================ 109 sorumlu sıfatıyla ödenen KDV
def sorumlu_109(ctx, mid, ay, yil):
    s_ay, s_yil = _sonraki_ay(ay, yil)
    sorgu = "109 - Sorumlu Sıfatıyla Ödenen KDV"
    kapsam = f"{AYLAR[ay - 1]} {yil} - {AYLAR[s_ay - 1]} {s_yil} aralığı"
    sayfa = "emukellef/sorumlu_sifatiyla_odenen_kdv"
    yol = sayfa + "/toplu_sorgula"
    try:
        j = _json_tekrarli(ctx, sayfa, yol, {"id": str(mid), "baslangic_ay": f"{ay:02d}", "baslangic_yil": str(yil),
                                              "bitis_ay": f"{s_ay:02d}", "bitis_yil": str(s_yil)})
        data = j.get("data") if isinstance(j.get("data"), dict) else {}
        liste = data.get("list")
        if not j.get("success") or not isinstance(liste, list):
            m = _mesaj(j)
            if data.get("notfound") or _kayit_yok_mesaji(m):
                return _sonuc(sorgu, kapsam, "kayit_yok", m or "Kayıt yok.")
            return _sonuc(sorgu, kapsam, "hata", m or "Beklenmeyen yanıt.")
        kolonlar = ["Ödeme tarihi", "Tutar", "Vergi dairesi", "Tahsil şekli"]
        satirlar = [[x.get("odemeTarihi"), _tr_sayi(x.get("odenen")), x.get("vdKodu"), x.get("thsSekli")] for x in liste]
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Kayıt yok.", kolonlar)
        mesaj = (f"{len(satirlar)} ödeme. KDV1 109 satırına hangi ödemenin yazılacağını ödeme tarihine göre seçin; "
                 "KDV2 ödeme sorgusundaki ödemeyle aynısı olabilir, iki kez saymayın.")
        if _okunamayan(satirlar, [1]):
            mesaj += " UYARI: okunamayan tutar var."
        return _sonuc(sorgu, kapsam, "veri", mesaj, kolonlar, satirlar, _topla(s[1] for s in satirlar))
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


# ================================================================ SGK 6661 asgari ücret desteği
# Ekran: emukellef/sgk_6661_asgari_destek ("Sorgulamalar uyg.sgk.gov.tr/IsverenSistemi üzerinden yapılmaktadır"). İstek SGK şubesi
# (işyeri) başına ve yıllıktır. Şube listesinde mükellef numarası yoktur: firma ÜNVANLA eşlenir ve mükellef listesindeki SGK şube
# sayısıyla doğrulanır; tutmuyorsa sorgu yapılmaz. Mükellefin SGK ayrıntı penceresi (SGK şifreleri orada) AÇILMAZ.
SGK6661 = "SGK 6661 Asgari Ücret Desteği"
SGK6661_KOLON = ["Sicil no", "Şube", "Yıl", "Ay", "Faydalanılan gün", "Destek tutarı", "Tahsilat tarihi"]


def _ing_sayi(v):
    """İngilizce biçimli tutar ("2,539.80 TL") -> float. Biçim tam beklenen gibi değilse None (yanlış okumaktansa okunamadı)."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    m = re.fullmatch(r"\s*(-?(?:\d{1,3}(?:,\d{3})+|\d+))\.(\d{2})\s*(?:TL|TRY|₺)?\s*", str(v or ""), re.I)
    return round(float(m.group(1).replace(",", "") + "." + m.group(2)), 2) if m else None


def _tl_yaz(v):
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _unvan_norm(s):
    """Ünvan karşılaştırması için: boşluk/NBSP tek boşluk; harf büyüklüğü ve Türkçe İ/I/ı/i ayrımı yok sayılır."""
    t = re.sub(r"\s+", " ", unescape(str(s or "")).replace("\xa0", " ")).strip().casefold()
    return t.replace("i\u0307", "i").replace("ı", "i")


def _mukellef_sgk(ctx, mid):
    """Mükellef listesinden (ünvan, E-Mükellef'te tanımlı SGK şube sayısı). Şube sayısı okunamazsa None.
    Aynı ünvanlı birden fazla mükellef varsa hata verir (şubeler ünvanla eşlendiğinden ayırt edilemez)."""
    yol = "mukellef/datatable"
    j = _ham_json(_post(ctx, "mukellef", yol, [("draw", "1"), ("start", "0"), ("length", "5000"), ("durum", "1")]), yol)
    if not isinstance(j, dict) or not isinstance(j.get("data"), list):
        raise RuntimeError("Mükellef listesi beklenen yapıda gelmedi.")
    satirlar = [s for s in j["data"] if isinstance(s, list) and len(s) >= 4]
    sgk_kolon = next((i for s in satirlar for i, c in enumerate(s) if "tab=sgk" in str(c)), None)    # SGK şube sayısı sütunu
    unvanlar, bulunan = [], None
    for s in satirlar:
        u = re.search(r'data-copy="([^"]*)"', str(s[1]))
        unvan = unescape(u.group(1)) if u else _html_metin(s[1])
        unvanlar.append(_unvan_norm(unvan))
        m = re.search(r"""value=['"]?(\d+)""", str(s[0]))
        if not m or int(m.group(1)) != int(mid):
            continue
        sayi = None
        if sgk_kolon is not None and sgk_kolon < len(s):
            h = _html_metin(s[sgk_kolon])
            sayi = int(h) if h.isdigit() else (0 if h in ("—", "-", "") else None)
        bulunan = (unvan, sayi)
    if bulunan is None:
        raise RuntimeError("Firma E-Mükellef'in aktif mükellef listesinde bulunamadı.")
    if not _unvan_norm(bulunan[0]) or unvanlar.count(_unvan_norm(bulunan[0])) != 1:
        raise RuntimeError("E-Mükellef'te bu ünvanla birden fazla mükellef var (ya da ünvan boş); SGK şubeleri ünvanla eşlendiğinden ayırt "
                           "edilemiyor. '6661 Sayılı Asgari Destek' ekranından elle sorgulayın.")
    return bulunan


def _sgk_subeleri(ctx):
    """E-Mükellef'te tanımlı tüm SGK şubeleri (aktif + pasif): [{id, unvan, ad, aktif}]."""
    yol = ADRES + "/emukellef/ajax/sgk_sube_filtre"

    def liste(form):
        j = _json(_post(ctx, "emukellef/sgk_6661_asgari_destek", yol, form), "sgk_sube_filtre")
        d = j.get("data")
        if not j.get("success") or not isinstance(d, dict) or not isinstance(d.get("list"), list):
            raise RuntimeError("SGK şube listesi alınamadı.")
        if any(not isinstance(x, dict) or x.get("id") in (None, "") for x in d["list"]):
            raise RuntimeError("SGK şube listesi beklenen yapıda değil.")
        return d["list"]
    aktif = {str(x["id"]) for x in liste([("sube_durum", "1")])}
    tum = []
    for x in liste([]):
        ad = re.search(r"<small[^>]*>(.*?)</small>", str(x.get("content") or ""), re.S)
        tum.append({"id": str(x["id"]), "unvan": str(x.get("unvan") or ""), "ad": _html_metin(ad.group(1)) if ad else "",
                    "aktif": str(x["id"]) in aktif})
    if not aktif <= {x["id"] for x in tum}:
        raise RuntimeError("SGK şube listeleri birbirini tutmuyor (aktif şube tüm listede yok).")
    return tum


def asgari_destek(ctx, mid, ay, yil, iptal=lambda: False):
    """6661 sayılı Kanun asgari ücret desteği: firmanın AKTİF SGK şubeleri için dönem yılının aylık listesi.
    Ocak döneminde, Ocak'ta mahsup edilen Aralık desteği için önceki yıl da sorgulanır."""
    sorgu, kolonlar = SGK6661, SGK6661_KOLON
    yillar = [yil - 1, yil] if ay == 1 else [yil]
    donem = f"{AYLAR[ay - 1]} {yil}"
    kapsam = f"{' ve '.join(str(y) for y in yillar)} yılı (dönem {donem}); SGK İşveren Sistemi, aktif şubeler"
    sayfa = "emukellef/sgk_6661_asgari_destek"
    yol = sayfa + "/toplu_sorgula"
    try:
        unvan, beklenen = _mukellef_sgk(ctx, mid)
        hedef = _unvan_norm(unvan)
        benim = [x for x in _sgk_subeleri(ctx) if _unvan_norm(x["unvan"]) == hedef]
        if beklenen is None:                        # doğrulama yapılamıyorsa sorgu da yapılmaz (yanlış firmanın verisi alınmasın)
            return _sonuc(sorgu, kapsam, "hata", "Firmanın SGK şubeleri doğrulanamadı: mükellef listesinde SGK şube sayısı okunamadı. "
                          "E-Mükellef'te '6661 Sayılı Asgari Destek' ekranından elle sorgulayın.", kolonlar)
        if len(benim) != beklenen:
            return _sonuc(sorgu, kapsam, "hata", f"Firmanın SGK şubeleri eşleştirilemedi: mükellef listesinde {beklenen} SGK şubesi görünüyor, ünvanla "
                          f"{len(benim)} şube bulundu. E-Mükellef'te '6661 Sayılı Asgari Destek' ekranından elle sorgulayın.", kolonlar)
        if not benim:
            return _sonuc(sorgu, kapsam, "kayit_yok", "E-Mükellef'te bu firma için SGK şubesi (işyeri) tanımlı değil.", kolonlar)
        aktif = [x for x in benim if x["aktif"]]
        pasif = [x["ad"] or x["id"] for x in benim if not x["aktif"]]
        pasif_notu = f" {len(pasif)} pasif şube sorgulanmadı ({', '.join(pasif[:5])})." if pasif else ""
        if not aktif:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Firmanın aktif SGK şubesi yok." + pasif_notu, kolonlar)
        satirlar, bos, hatalar, iptal_edildi = [], [], [], False
        for sube in aktif:
            for y in yillar:
                if iptal():
                    iptal_edildi = True
                    break
                etiket = f"{sube['ad'] or sube['id']} {y}"
                try:
                    j = _json_tekrarli(ctx, sayfa, yol, {"sgk_id": sube["id"], "donem_yil": str(y)})
                except OturumYok:
                    raise
                except Exception as e:              # bir şubenin isteği düşerse eldeki satırlar kaybolmasın
                    hatalar.append(f"{etiket}: {str(e)[:150]}")
                    continue
                data, m = j.get("data"), _mesaj(j)
                if j.get("success") is True and isinstance(data, list):
                    if any(not isinstance(x, dict) for x in data):
                        hatalar.append(f"{etiket}: satırlar beklenen yapıda değil")
                        continue
                    if any(_unvan_norm(x.get("unvan")) != hedef for x in data):
                        hatalar.append(f"{etiket}: yanıttaki ünvan firmayla aynı değil; sonuç kullanılmadı")
                        continue
                    if not data:
                        bos.append(etiket)
                    for x in data:
                        gun = str(x.get("faydalanilan_gun_sayisi") or "").strip()
                        tarih = str(x.get("tahsilat_tarihi") or "").strip()
                        t = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", tarih)
                        satirlar.append([str(x.get("sicil_no") or ""), str(x.get("sube_adi") or sube["ad"]), str(x.get("yil") or ""),
                                         str(x.get("ay") or "").zfill(2), int(gun) if gun.isdigit() else gun, _ing_sayi(x.get("destek_tutari")),
                                         f"{t.group(1)}.{t.group(2)}.{t.group(3)}" if t else tarih])
                elif isinstance(data, dict) and data.get("notfound") is True:
                    bos.append(etiket)                  # şubenin o yıl kaydı yok (hata değil); yalnız sunucunun kendi işaretiyle
                else:
                    hatalar.append(f"{etiket}: {m or 'beklenmeyen yanıt'}")
            if iptal_edildi:
                break
        if iptal_edildi:
            hatalar.append("iptal edildi; kalan şubeler sorgulanmadı")
        satirlar.sort(key=lambda r: (r[1], r[2], r[3]))
        sube_notu = f"{len(aktif)} aktif şube sorgulandı." + pasif_notu
        if hatalar:
            return _sonuc(sorgu, kapsam, "hata", f"{sube_notu} Liste EKSİK olabilir — {len(hatalar)} sorgu başarısız: " + "; ".join(hatalar[:5]),
                          kolonlar, satirlar)
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", f"{sube_notu} 6661 asgari ücret desteği kaydı bulunamadı.", kolonlar)

        def tutar(rs):
            return _topla(r[5] for r in rs)

        def gunler(rs):
            return sum(r[4] for r in rs if isinstance(r[4], int))
        hizmet = [r for r in satirlar if r[2] == str(yil) and r[3] == f"{ay:02d}"]
        mahsup = [r for r in satirlar if r[6].endswith(f".{ay:02d}.{yil}")]
        yil_satir = [r for r in satirlar if r[2] == str(yil)]
        son = max(satirlar, key=lambda r: (r[2], r[3]))
        mesaj = f"{sube_notu} {len(satirlar)} aylık kayıt. "
        if hizmet:
            mesaj += f"{donem} hizmet ayı desteği: {_tl_yaz(tutar(hizmet))} TL ({gunler(hizmet)} gün). "
        else:
            mesaj += f"{donem} hizmet ayı için kayıt henüz yok (son kayıt {son[3]}/{son[2]}). "
        if mahsup:
            aylar = ", ".join(sorted({f"{r[3]}/{r[2]}" for r in mahsup}))
            mesaj += f"{donem} içinde mahsup edilen (tahsilat tarihine göre): {_tl_yaz(tutar(mahsup))} TL ({aylar} hizmet ayı, {gunler(mahsup)} gün). "
        else:
            mesaj += f"{donem} içinde mahsup edilen kayıt yok. "
        if yil_satir:
            mesaj += f"{yil} toplamı {_tl_yaz(tutar(yil_satir))} TL ({len({r[3] for r in yil_satir})} ay)."
        if bos:
            mesaj += f" Kaydı olmayan: {', '.join(bos[:6])}."
        if _okunamayan(satirlar, [5]):
            mesaj += " UYARI: okunamayan tutar var; toplamlar eksik olabilir."
        return _sonuc(sorgu, kapsam, "veri", mesaj.strip(), kolonlar, satirlar, tutar(yil_satir) if yil_satir else None)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


# ================================================================ İthalde ödenen KDV
def ithalat(ctx, mid, ay, yil):
    sorgu = "İthalde Ödenen KDV"
    kapsam = f"{AYLAR[ay - 1]} {yil}"
    sayfa = "emukellef/ithalde_odenen_kdv_bilgileri_sorgulama"
    yol = sayfa + "/sorgula"
    try:
        j = _json_tekrarli(ctx, sayfa, yol, {"id": str(mid), "baslangic_ay": f"{ay:02d}", "baslangic_yil": str(yil),
                                              "bitis_ay": f"{ay:02d}", "bitis_yil": str(yil)})
        m = _mesaj(j)
        if not j.get("success"):
            if _kayit_yok_mesaji(m):
                return _sonuc(sorgu, kapsam, "kayit_yok", m)
            return _sonuc(sorgu, kapsam, "hata", m or "GİB yanıt vermedi")
        liste = _tek_liste(j.get("data"))
        if liste is None:
            return _sonuc(sorgu, kapsam, "hata", "Yanıtta liste yok (yapı bilinmiyor). " + m)
        kolonlar, satirlar = _genel_tablo(liste)
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", m or "Kayıt yok.", kolonlar)
        return _sonuc(sorgu, kapsam, "veri", f"{len(satirlar)} kayıt. Kolonlar GİB'in ham alan adlarıdır; ithalat KDV'sini kontrol edin.",
                      kolonlar, satirlar)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


# ================================================================ Araçlar
def _arac(ctx, mid, gecmis):
    sorgu = "Araç Bilgisi (geçmiş)" if gecmis else "Araç Bilgisi (mevcut)"
    kapsam = "Firma adına daha önce kayıtlı (terk edilmiş) araçlar" if gecmis else "Firma adına kayıtlı mevcut araçlar"
    sayfa = "emukellef/gib_arac_bilgisi_sorgula"
    yol = sayfa + ("/gecmis_sorgula" if gecmis else "/mevcut_sorgula")
    form = {"id": str(mid), "tescil_tarihi": ""}
    if gecmis:
        form["terk_tarihi"] = ""
    try:
        j = _json_tekrarli(ctx, sayfa, yol, form)
        ic = (j.get("data") or {}).get("sorgula") if isinstance(j.get("data"), dict) else None
        araclar = (ic.get("data") or {}).get("araclar") if isinstance(ic, dict) and isinstance(ic.get("data"), dict) else None
        if not j.get("success") or not isinstance(araclar, list):
            m = _mesaj(j) or (_html_metin(ic.get("msg")) if isinstance(ic, dict) else "")
            if _kayit_yok_mesaji(m):
                return _sonuc(sorgu, kapsam, "kayit_yok", m)
            return _sonuc(sorgu, kapsam, "hata", m or "Beklenmeyen yanıt.")
        kolonlar = ["Plaka", "Marka", "Tip", "Model yılı", "Cins", "Tescil tarihi", "Tescil durumu", "Terk tarihi", "Terk türü"]
        satirlar = [[a.get("plaka"), a.get("marka"), a.get("tip"), a.get("model"), a.get("cinsAd"),
                     a.get("tescilTarihi"), a.get("tescilDurumu"), a.get("terkTarihi") or "", a.get("terkTuru") or ""] for a in araclar]
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Araç yok.", kolonlar)
        binek = sum(1 for s in satirlar if "OTOMOB" in str(s[4]).upper())
        return _sonuc(sorgu, kapsam, "veri", f"{len(satirlar)} araç; cinsi OTOMOBİL olan (binek): {binek}.", kolonlar, satirlar)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


def arac(ctx, mid):
    return _arac(ctx, mid, gecmis=False)


def arac_gecmis(ctx, mid):
    return _arac(ctx, mid, gecmis=True)


def arac_alim_satim(ctx, mid, yil):
    sorgu = "Araç Alım/Satım Bilgileri"
    kapsam = f"{yil} yılı"
    sayfa = "emukellef/gib_arac_alim_satim_bilgileri_sorgula"
    yol = sayfa + "/toplu_sorgula"
    try:
        j = _json_tekrarli(ctx, sayfa, yol, {"id": str(mid), "yil": str(yil)})
        data = j.get("data") if isinstance(j.get("data"), dict) else {}
        m = _mesaj(j)
        if data.get("notfound") or (not j.get("success") and _kayit_yok_mesaji(m)):
            return _sonuc(sorgu, kapsam, "kayit_yok", m or "Kayıt yok.")
        liste = _tek_liste(data)
        if not j.get("success") or liste is None:
            return _sonuc(sorgu, kapsam, "hata", m or "Beklenmeyen yanıt.")
        kolonlar, satirlar = _genel_tablo(liste)
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Kayıt yok.", kolonlar)
        return _sonuc(sorgu, kapsam, "veri", f"{len(satirlar)} kayıt (ham alan adları).", kolonlar, satirlar)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


# ================================================================ Beyanname / tahakkuk (önceki dönem)
BEYAN_VERGI = ["10", "11"]          # 10 = KDV 1, 11 = KDV 2 (E-Mükellef vergi türü kodları)
MUHSGK_VERGI = ["39"]


def _beyan_tur(metin):
    t = metin.upper().replace(" ", "")
    tur = "KDV1" if "KDV1" in t else ("KDV2" if "KDV2" in t else ("MUHSGK" if "MUHSGK" in t else None))
    belge = "Beyanname" if "BYN" in t else ("Tahakkuk" if "THK" in t else None)
    return tur, belge


def beyanname_tahakkuk(ctx, mid, ay, yil, iptal=lambda: False, vergiler=None, odeme_aylari=None,
                       sorgu="Önceki dönem KDV1/KDV2 beyanname ve tahakkuk", tur_adi="KDV1/KDV2", yalniz_donem=None):
    """Ödeme ayı = seçilen KDV dönemi olan (yani bir ÖNCEKİ döneme ait) beyanname ve tahakkukları indirir.
    Örn. Ocak 2027 seçilirse ödeme ayı Ocak 2027 -> belgeler Aralık 2026 dönemi.
    vergiler: E-Mükellef vergi türü kodları; odeme_aylari: [(ay, yil), ...] (varsayılan yalnız seçilen ay).
    yalniz_donem: (ay, yil) verilirse yalnız vergilendirme dönemi bu olan belgeler alınır (diğerleri listeye de girmez).
    Döner: (sonuç, belgeler[{tur, belge, donem_ay, donem_yil, vkn, bayt, tutar}])"""
    vergiler = vergiler or BEYAN_VERGI
    odeme_aylari = odeme_aylari or [(ay, yil)]
    kapsam = "Ödeme ayı " + ", ".join(f"{AYLAR[a - 1]} {y}" for a, y in odeme_aylari) + " (ödeme tarihine göre)"
    sayfa = "vergi_evrak_bilgisayar_indir"
    yol = sayfa + "/datatable"
    belgeler = []
    try:
        liste = []
        for o_ay, o_yil in odeme_aylari:
            if iptal():                             # her liste isteğinden önce (çok ödeme ayı taranırken de durdurulabilsin)
                if not liste:
                    return _sonuc(sorgu, kapsam, "hata", "İptal edildi; beyanname listesi alınmadı."), []
                break
            form = [("draw", "1"), ("start", "0"), ("length", "-1"), ("month", f"{o_ay:02d}"), ("year", str(o_yil)),
                    ("user_id[]", str(mid))] + [("tax_id[]", k) for k in vergiler]
            liste += _tablo_json(_post(ctx, sayfa, yol, form), yol)
        kolonlar = ["Tür", "Dönem", "Tutar", "Ödeme tarihi", "İndirme"]
        satirlar, vknler, sayac = [], set(), {}
        for satir in liste:
            if iptal():
                satirlar.append(["(iptal)", "", None, "", "İPTAL — kalan belgeler indirilmedi"])
                break
            h = [str(x) for x in satir]
            if len(h) < 6:
                raise ValueError("Beyanname satırı beklenen yapıda değil")
            tur_metin, donem = _html_metin(h[2]), _html_metin(h[3])
            tur, belge = _beyan_tur(tur_metin)
            dm = re.fullmatch(r"(\d{2})/(\d{4})", donem)
            if yalniz_donem is not None and dm and (int(dm.group(1)), int(dm.group(2))) != tuple(yalniz_donem):
                continue                            # aynı ödeme ayındaki başka dönemin belgesi: istenmedi
            m = re.search(r'href="([^"]*goruntule[^"]*)"', h[2]) or re.search(r'href="([^"]*goruntule[^"]*)"', " ".join(h))
            tutar = _tr_sayi(_html_metin(h[4]))
            if not (tur and belge and dm and m):
                satirlar.append([tur_metin, donem, tutar, _html_metin(h[5]), "TANINMADI — indirilmedi"])
                continue
            adres = unescape(m.group(1))
            adres = adres if adres.startswith("http") else ADRES + adres
            vkn = (parse_qs(urlparse(adres).query).get("vn") or [""])[0]
            vknler.add(vkn)
            sayac[(tur, belge, donem)] = sayac.get((tur, belge, donem), 0) + 1
            p = _get(ctx, sayfa, adres)
            bayt = p.body()
            if p.status == 200 and bayt[:5] == b"%PDF-":
                belgeler.append({"tur": tur, "belge": belge, "donem_ay": int(dm.group(1)), "donem_yil": int(dm.group(2)),
                                 "vkn": vkn, "bayt": bayt, "tutar": tutar})
                durum = "indirildi"
            else:
                durum = f"PDF gelmedi (HTTP {p.status})"
            satirlar.append([tur_metin, donem, tutar, _html_metin(h[5]), durum])
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", f"Bu ödeme ayında {tur_adi} belgesi yok.", kolonlar), []
        notlar = [f"{len(belgeler)} belge indirildi."]
        eksik = sum(1 for s in satirlar if s[4] != "indirildi")
        if eksik:
            notlar.append(f"{eksik} belge İNDİRİLEMEDİ.")
        if len(vknler) > 1:
            notlar.append("UYARI: belgelerde birden fazla VKN var.")
        if any(v > 1 for v in sayac.values()):
            notlar.append("Aynı tür/dönem için birden fazla belge var (düzeltme beyannamesi olabilir); hepsi ayrı adla kaydedildi.")
        return _sonuc(sorgu, kapsam, "hata" if eksik else "veri", " ".join(notlar), kolonlar, satirlar), belgeler
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e)), belgeler


VERILEN_AY_SINIRI = 36           # eski bir dönem seçilirse taranacak en çok ödeme ayı (aşılırsa sonuç mesajında yazılır)


def verilen_odeme_aylari(ay, yil, bugun=None):
    """Dönemin beyannamesinin aranacağı ödeme ayları: dönemi izleyen aydan bugünün ayına kadar (sonradan verilen düzeltme
    beyannameleri için). İzleyen ay henüz gelmediyse yalnız o ay. Döner: (aylar, kesildi) — kesildi: sınır yüzünden bugüne
    kadar taranamadı."""
    bugun = bugun or date.today()
    aylar, (a, y) = [], _sonraki_ay(ay, yil)
    while (y, a) <= (bugun.year, bugun.month):
        if len(aylar) >= VERILEN_AY_SINIRI:
            return aylar, True
        aylar.append((a, y))
        a, y = _sonraki_ay(a, y)
    return (aylar or [_sonraki_ay(ay, yil)]), False


def verilen_beyanname(ctx, mid, ay, yil, iptal=lambda: False, bugun=None):
    """Seçilen KDV döneminin VERİLMİŞ beyannameleri: KDV1, KDV2 ve MUHSGK beyanname + tahakkuk (düzeltmeler dahil).
    E-Mükellef listesi ödeme ayına göredir; yalnız vergilendirme dönemi seçilen ay olan belgeler indirilir.
    Döner: (sonuç, belgeler) — bkz. beyanname_tahakkuk."""
    aylar, kesildi = verilen_odeme_aylari(ay, yil, bugun)
    s, belgeler = beyanname_tahakkuk(ctx, mid, ay, yil, iptal=iptal, vergiler=BEYAN_VERGI + MUHSGK_VERGI, odeme_aylari=aylar,
                                     sorgu="Verilen beyanname ve tahakkuk", tur_adi="KDV1/KDV2/MUHSGK", yalniz_donem=(ay, yil))
    if s["durum"] == "kayit_yok":
        s["mesaj"] = (f"E-Mükellef'in beyanname listesinde {AYLAR[ay - 1]} {yil} dönemine ait KDV1/KDV2/MUHSGK belgesi yok "
                      "(beyanname henüz verilmemiş ya da E-Mükellef'e henüz alınmamış olabilir).")
    if kesildi:                                     # eksik kapsam sessiz kalmasın
        son_a, son_y = aylar[-1]
        s["mesaj"] += (f" DİKKAT: yalnız {AYLAR[son_a - 1]} {son_y} ödeme ayına kadar tarandı (ilk {len(aylar)} ay); daha sonra verilmiş "
                       "düzeltme beyannamesi varsa bu listede yoktur.")
    return s, belgeler


def muhsgk(ctx, mid, ay, yil, iptal=lambda: False):
    """MUHSGK beyanname + tahakkuk: önceki dönemin (ödeme ayı = seçilen ay) ve verildiyse seçilen dönemin (ödeme ayı +1)."""
    return beyanname_tahakkuk(ctx, mid, ay, yil, iptal=iptal, vergiler=MUHSGK_VERGI,
                              odeme_aylari=[(ay, yil), _sonraki_ay(ay, yil)],
                              sorgu="MUHSGK beyanname ve tahakkuk", tur_adi="MUHSGK")


# ================================================================ E-arşiv ve e-fatura (gelen/giden)
EARSIV_KOLON = {11: "Satıcı VKN", 12: "Satıcı", 13: "Alıcı VKN", 14: "Alıcı", 15: "Belge no", 16: "Düzenleme tarihi",
                17: "Vergi hariç", 18: "Vergi", 19: "İskonto", 20: "Ödenecek", 21: "KDV oranı", 22: "Tevkifat",
                23: "Matrah %18-20", 24: "KDV %18-20", 25: "Matrah %8-10", 26: "KDV %8-10", 27: "Matrah %1", 28: "KDV %1"}
EARSIV_TUTAR = {17, 18, 19, 20, 23, 24, 25, 26, 27, 28}
# E-fatura listesinin 25-28. kolonları ("Matrah %0/1/10/20") güvenilir değil (örnekte KDV tutarı vardı) -> alınmaz.
EFATURA_KOLON = {13: "Satıcı", 14: "Satıcı VKN", 11: "Alıcı", 12: "Alıcı VKN", 15: "Senaryo", 16: "Tip", 17: "Belge no",
                 18: "Düzenleme tarihi", 19: "Para birimi", 20: "Vergi hariç", 21: "Vergiler", 22: "İskonto", 23: "Ödenecek",
                 24: "KDV oranı", 29: "Tevkifat", 30: "Tevkifat matrahı"}
EFATURA_TUTAR = {20, 21, 22, 23, 30}
YON_ADI = {"gelen": "Gelen", "giden": "Giden"}
SORUN_DURUMU = ("İNDİRİLEMEDİ", "iptal")


def _gib_earsiv_son_gun(simdi_utc=None):
    """GİB e-arşiv günlük sorgusunda izin verilen son gün. GİB 'tarih < (şimdi UTC − 3 gün)' ister:
    19.11.2026 00:40'ta (UTC 18.11 21:40) 16.11 "must be less than '2026/11/15'" ile reddedildi, 15.11 kabul edildi.
    Yani gece 00:00–03:00 arası sınır, yerel tarihe göre hesaplanandan bir gün geridedir (10 dk pay bırakılır)."""
    simdi = simdi_utc or datetime.now(timezone.utc)
    return (simdi - timedelta(days=3, minutes=10)).date()


def _gunler(bas, bit):
    g = bas
    while g <= bit:
        yield g
        g += timedelta(days=1)


def _ay_sonu(ay, yil):
    s_ay, s_yil = _sonraki_ay(ay, yil)
    return date(s_yil, s_ay, 1) - timedelta(days=1)


def _kimlik(satir):
    """Faturanın görüntüleme kimliği. Önce "Gör" bağlantısından okunur: e-faturada bu ETTN (UUID), e-arşivde sayıdır.
    DİKKAT: e-faturada 0. kolondaki onay kutusunun değeri yalnız satır sıra numarasıdır, kimlik değildir."""
    m = re.search(r"""goruntule/(?:pdf|xml|html)/([^/"'?&<>\s]+)""", " ".join(map(str, satir)))
    if m:
        return m.group(1)
    m = re.search(r"""value=['"]?(\d+)""", str(satir[0]))
    return m.group(1) if m else None


def _earsiv_liste(ctx, mid, ay, yil, yon="gelen"):
    yol = "e-arsiv/liste/datatable"
    form = [("draw", "1"), ("start", "0"), ("length", "-1"), ("fatura_taraf", yon), ("ay", f"{ay:02d}"),
            ("yil", str(yil)), ("mukellef_id[]", str(mid))]
    satirlar = []
    for s in _tablo_json(_post(ctx, f"e-arsiv/liste/{yon}", yol, form), yol):
        if not isinstance(s, list) or len(s) < 29:
            raise RuntimeError("E-arşiv liste satırı beklenen yapıda değil (kolon sayısı değişmiş).")
        satirlar.append({"id": _kimlik(s), **{ad: _html_metin(s[i]) for i, ad in EARSIV_KOLON.items()}})
    return satirlar


def _efatura_liste(ctx, mid, ay, yil, yon):
    yol = f"e-fatura/liste/datatable/{yon}"
    form = [("draw", "1"), ("start", "0"), ("length", "-1"), ("invoice_way", yon), ("ay", f"{ay:02d}"),
            ("yil", str(yil)), ("mukellef_id[]", str(mid))]
    satirlar = []
    for s in _tablo_json(_post(ctx, f"e-fatura/liste/{yon}", yol, form), yol):
        if not isinstance(s, list) or len(s) < 31:
            raise RuntimeError("E-fatura liste satırı beklenen yapıda değil (kolon sayısı değişmiş).")
        satirlar.append({"id": _kimlik(s), **{ad: _html_metin(s[i]) for i, ad in EFATURA_KOLON.items()}})
    return satirlar


def fatura_belgesi(ctx, tur, id_, cesit):
    """tur: 'earsiv' | 'efatura'; cesit: 'pdf' | 'xml'. Bayt veya None (PDF için %PDF kontrolü)."""
    kok = "e-arsiv" if tur == "earsiv" else "e-fatura"
    r = _get(ctx, f"{kok}/liste/gelen", f"{kok}/liste/goruntule/{cesit}/{id_}")
    b = r.body()
    if r.status != 200 or not b:
        return None
    if cesit == "pdf" and b[:5] != b"%PDF-":
        return None
    return b


def _faturalari_isle(liste, kolon_tanimi, tutar_kolonlari, belge_anahtari, liste_alanlari, isle, iptal, notlar):
    """Listedeki her fatura için isle() çağrılır (indir + kaydet + döküm kaydı); satırlar ve sorunlu sayısı döner.
    İptal edilirse kalan faturalar indirilmez ama listede ve dökümde (liste değerleriyle) yer alır."""
    satirlar, iptal_edildi = [], False
    for f in liste:
        if not iptal_edildi and iptal():
            iptal_edildi = True
            notlar.append("İndirme iptal edildi; kalan faturalar indirilmedi.")
        fa = {"id": f["id"], "belge_no": f[belge_anahtari], "liste": liste_alanlari(f)}
        if isle is None:
            pdf_d = xml_d = "—"
        else:
            pdf_d, xml_d = isle(fa, indir=not iptal_edildi)
        satirlar.append([_tr_sayi(f[k]) if i in tutar_kolonlari else f[k] for i, k in kolon_tanimi.items()] + [pdf_d, xml_d])
    sorunlu = sum(1 for s in satirlar if s[-1] in SORUN_DURUMU or s[-2] in SORUN_DURUMU)
    return satirlar, sorunlu


def _fatura_sonucu(sorgu, kapsam, kolon_tanimi, tutar_kolonlari, satirlar, sorunlu, gib_sorun, notlar, bos_mesaj):
    kolonlar = list(kolon_tanimi.values()) + ["PDF", "XML"]
    if not satirlar:
        return _sonuc(sorgu, kapsam, "hata" if gib_sorun else "kayit_yok", " ".join(notlar + [bos_mesaj]), kolonlar)
    tutar_indis = [j for j, i in enumerate(kolon_tanimi) if i in tutar_kolonlari]
    toplam = ["TOPLAM"] + [""] * (len(kolonlar) - 1)
    for j in tutar_indis:
        toplam[j] = _topla(s[j] for s in satirlar)
    notlar.append(f"{len(satirlar)} fatura." + (f" {sorunlu} faturanın PDF/XML'i İNDİRİLEMEDİ veya iptal edildi." if sorunlu else ""))
    if _okunamayan(satirlar, tutar_indis):
        notlar.append("UYARI: okunamayan tutar var.")
    return _sonuc(sorgu, kapsam, "hata" if (sorunlu or gib_sorun) else "veri", " ".join(notlar), kolonlar, satirlar, toplam)


def e_arsiv(ctx, mid, ay, yil, yon="gelen", guncelle=True, iptal=lambda: False, gunluk=print, bugun=None, isle=None):
    """E-arşiv faturaları (gelen/giden): (isteğe bağlı) GİB toplu sorgu -> liste -> her fatura için isle(fa, indir)."""
    sorgu = f"{YON_ADI[yon]} E-Arşiv Faturalar"
    kapsam = f"Düzenleme ayı {AYLAR[ay - 1]} {yil}"
    sayfa = f"e-arsiv/sorgula/{yon}"
    notlar, gib_sorun = [], False      # GİB sorgusu tam yapılamadıysa sonuç "hata" sayılır (otomatik tekrar denensin)
    try:
        if guncelle:
            ref_gun = bugun or date.today()
            bas = date(yil, ay, 1)
            s_ay, s_yil = _sonraki_ay(ay, yil)
            ay_sonu_2 = _ay_sonu(s_ay, s_yil)
            bit = min(ay_sonu_2, (bugun - timedelta(days=3)) if bugun else _gib_earsiv_son_gun())
            if bit < bas:
                notlar.append("GİB sorgusu yapılmadı (tarih aralığı henüz oluşmadı).")
            else:
                ilk = {"mukellef_id": str(mid), "tarih": bas.strftime("%d.%m.%Y"), "sorguturu": yon, "faturaturu": "1"}
                ca = _json(_post(ctx, sayfa, "e-arsiv/sorgula/checkauth", ilk), "checkauth")
                if not ca.get("success"):
                    gib_sorun = True
                    notlar.append("GİB toplu sorgu yapılamadı: " + (_mesaj(ca) or "yetki hatası") + " (E-Mükellef'teki mevcut liste kullanıldı).")
                else:
                    # Aktarım sayfanın yaptığı gibi satıcı VKN'si + belge no ile yapılır (giden'de satıcı firmanın kendisidir)
                    mevcut = {(f["Satıcı VKN"], f["Belge no"]) for f in _earsiv_liste(ctx, mid, ay, yil, yon)}
                    mevcut |= {(f["Satıcı VKN"], f["Belge no"]) for f in _earsiv_liste(ctx, mid, s_ay, s_yil, yon)}
                    bulunan, yeni, hatalar, erken = 0, 0, [], None
                    gunler = list(_gunler(bas, bit))
                    gunluk(f"  {sorgu}: GİB'den {bas:%d.%m.%Y} - {bit:%d.%m.%Y} ({len(gunler)} gün) sorgulanıyor…")
                    for i, g in enumerate(gunler, 1):
                        if iptal():
                            gib_sorun = True
                            notlar.append("GİB sorgusu iptal edildi; liste eksik olabilir.")
                            break
                        d = _json(_post(ctx, sayfa, "e-arsiv/sorgula/documents", {**ilk, "tarih": g.strftime("%d.%m.%Y")}), "documents")
                        if not d.get("success"):
                            m = _mesaj(d)
                            if re.search(r"'Date' must be less than", m, re.I) and (ref_gun - g).days <= 5:
                                erken = g                   # GİB son günleri henüz sorgulatmıyor (güncellik sınırı): hata değil
                                break                       # (eski bir gün reddedilirse bu kural işlemez, HATA sayılır)
                            if not _kayit_yok_mesaji(m):
                                hatalar.append(f"{g:%d.%m}: {m[:60]}")
                            continue
                        for b in (d.get("data") or {}).get("result") or []:
                            if iptal():
                                break
                            bulunan += 1
                            no, vkn = str(b.get("documentNumber") or ""), str(b.get("sellerTaxNumber") or "")
                            if not no or not vkn or (vkn, no) in mevcut:
                                continue
                            k = _json(_post(ctx, sayfa, f"e-arsiv/sorgula/document_id/{vkn}/{no}",
                                            {"mukellef_id": str(mid), "sorguturu": yon}), "document_id")
                            if k.get("success"):
                                yeni += 1
                                mevcut.add((vkn, no))
                            else:
                                hatalar.append(f"{no}: {_mesaj(k)[:60]}")
                        if i % 10 == 0:
                            gunluk(f"  {sorgu}: {i}/{len(gunler)} gün sorgulandı, {bulunan} fatura görüldü.")
                    if erken is not None:
                        bit = erken - timedelta(days=1)
                    if bit < bas:
                        notlar.append(f"GİB hiçbir günü sorgulatmadı ({bas:%d.%m.%Y} ve sonrası henüz sorgulanamıyor); E-Mükellef'teki mevcut liste kullanıldı.")
                    else:
                        notlar.append(f"GİB toplu sorgu {bas:%d.%m.%Y}-{bit:%d.%m.%Y}: {bulunan} fatura görüldü, {yeni} yeni fatura E-Mükellef'e alındı.")
                    if bit < ay_sonu_2:
                        notlar.append(f"{bit + timedelta(days=1):%d.%m.%Y} sonrası (son 3 gün) GİB'den sorgulanmadı; geç düşen fatura için birkaç gün sonra tekrar çalıştırın.")
                    if hatalar:
                        gib_sorun = True
                        notlar.append(f"{len(hatalar)} gün/belgede GİB hatası: " + "; ".join(hatalar[:5]))
        liste = _earsiv_liste(ctx, mid, ay, yil, yon)
        disari = [f["Belge no"] for f in liste if not re.search(rf"\.{ay:02d}\.{yil}", f["Düzenleme tarihi"])]
        if disari:
            notlar.append(f"UYARI: {len(disari)} faturanın tarihi dönem dışında görünüyor: {', '.join(disari[:3])}")
        if liste:
            gunluk(f"  {sorgu}: {len(liste)} fatura indiriliyor/kaydediliyor…")

        def alanlar(f):
            return {"tarih": f["Düzenleme tarihi"], "vergi_haric": _tr_sayi(f["Vergi hariç"]), "odenecek": _tr_sayi(f["Ödenecek"]),
                    "satici_vkn": f["Satıcı VKN"], "satici": f["Satıcı"], "alici_vkn": f["Alıcı VKN"], "alici": f["Alıcı"],
                    "karsi": f["Satıcı"] if yon == "gelen" else f["Alıcı"], "karsi_vkn": f["Satıcı VKN"] if yon == "gelen" else f["Alıcı VKN"]}
        satirlar, sorunlu = _faturalari_isle(liste, EARSIV_KOLON, EARSIV_TUTAR, "Belge no", alanlar, isle, iptal, notlar)
        return _fatura_sonucu(sorgu, kapsam, EARSIV_KOLON, EARSIV_TUTAR, satirlar, sorunlu, gib_sorun, notlar,
                              f"Bu ay {yon} e-arşiv faturası yok.")
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", " ".join(notlar + [str(e)]))


def e_fatura(ctx, mid, ay, yil, yon="gelen", guncelle=True, iptal=lambda: False, gunluk=print, isle=None):
    """E-faturalar (gelen/giden): (isteğe bağlı) GİB sorgusu + E-Mükellef'e aktarım -> liste -> her fatura için isle(fa, indir)."""
    sorgu = f"{YON_ADI[yon]} E-Faturalar"
    kapsam = f"Fatura tarihi {AYLAR[ay - 1]} {yil}"
    sayfa = f"e-fatura/sorgula/{yon}"
    notlar, gib_sorun = [], False
    try:
        if guncelle and not iptal():
            form = {"id": str(mid), "tarih_baslangic": date(yil, ay, 1).strftime("%d.%m.%Y"),
                    "tarih_bitis": _ay_sonu(ay, yil).strftime("%d.%m.%Y")}
            j = _json_tekrarli(ctx, sayfa, f"e-fatura/sorgula/sorgula/{yon}", form)
            data = j.get("data")
            if j.get("success") and isinstance(data, list):
                aktar = [x for x in data if isinstance(x, dict) and x.get("id")]   # id dolu = E-Mükellef'te henüz yok
                yeni, hata = 0, 0
                for x in aktar:
                    if iptal():
                        gib_sorun = True
                        notlar.append("Aktarım iptal edildi.")
                        break
                    r = _post(ctx, sayfa, f"e-fatura/sorgula/indir/{yon}", {"id": str(x["id"])})
                    try:
                        ok = bool(r.json().get("success", True))
                    except Exception:
                        ok = True          # sayfa yanıtı yalnız hata için kontrol ediyor
                    yeni += ok
                    hata += not ok
                notlar.append(f"GİB sorgusu: {len(data)} fatura görüldü, {yeni} yeni fatura E-Mükellef'e alındı.")
                if hata:
                    gib_sorun = True
                    notlar.append(f"{hata} fatura E-Mükellef'e aktarılamadı.")
            elif isinstance(data, dict) and data.get("notfound"):
                notlar.append("GİB'de bu dönem için fatura bulunmadı.")
            else:
                gib_sorun = True
                notlar.append("GİB sorgusu yapılamadı: " + (_mesaj(j) or "yanıt yok") + " (E-Mükellef'teki mevcut liste kullanıldı).")
        liste = _efatura_liste(ctx, mid, ay, yil, yon)
        if liste:
            gunluk(f"  {sorgu}: {len(liste)} fatura indiriliyor/kaydediliyor…")

        def alanlar(f):
            return {"tarih": f["Düzenleme tarihi"], "tip": f["Tip"], "senaryo": f["Senaryo"], "para": f["Para birimi"],
                    "vergi_haric": _tr_sayi(f["Vergi hariç"]), "odenecek": _tr_sayi(f["Ödenecek"]),
                    "satici_vkn": f["Satıcı VKN"], "satici": f["Satıcı"], "alici_vkn": f["Alıcı VKN"], "alici": f["Alıcı"],
                    "karsi": f["Satıcı"] if yon == "gelen" else f["Alıcı"], "karsi_vkn": f["Satıcı VKN"] if yon == "gelen" else f["Alıcı VKN"]}
        satirlar, sorunlu = _faturalari_isle(liste, EFATURA_KOLON, EFATURA_TUTAR, "Belge no", alanlar, isle, iptal, notlar)
        notlar.append("Oran kırılımı için Fatura Dökümü Excel'ine bakın (XML'den).")
        return _fatura_sonucu(sorgu, kapsam, EFATURA_KOLON, EFATURA_TUTAR, satirlar, sorunlu, gib_sorun, notlar,
                              f"Bu ay {yon} e-fatura yok.")
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", " ".join(notlar + [str(e)]))


# ================================================================ Tam mükellef listesi
def mukellef_listesi(ctx):
    """E-Mükellef'teki aktif mükellefler: [{id, unvan, vkn}] (e-posta/telefon alınmaz). Eksik gelirse hata verir."""
    yol = "mukellef/datatable"
    r = _post(ctx, "mukellef", yol, [("draw", "1"), ("start", "0"), ("length", "5000"), ("durum", "1")])
    j = _ham_json(r, yol)
    if not isinstance(j, dict) or not isinstance(j.get("data"), list):
        raise RuntimeError("Mükellef listesi beklenen yapıda gelmedi.")
    liste = []
    for s in j["data"]:
        if not isinstance(s, list) or len(s) < 4:
            raise RuntimeError("Mükellef satırı beklenen yapıda değil.")
        mid = re.search(r"""value=['"]?(\d+)""", str(s[0]))
        unvan = re.search(r'data-copy="([^"]*)"', str(s[1]))
        vkn = re.search(r'data-copy="(\d{10,11})"', str(s[3]))
        if not mid:
            continue
        liste.append({"id": int(mid.group(1)), "unvan": unescape(unvan.group(1)) if unvan else _html_metin(s[1]),
                      "vkn": vkn.group(1) if vkn else ""})
    beklenen = j.get("recordsFiltered", j.get("recordsTotal"))
    if isinstance(beklenen, int) and beklenen != len(liste):
        raise RuntimeError(f"Mükellef listesi eksik geldi ({len(liste)}/{beklenen}).")
    return liste


# ================================================================ E-arşiv: Mükellef Adına Düzenlenenler (çapraz kontrol)
ADINA_KOLON = {3: "Satıcı VKN/TCKN", 2: "Satıcı", 4: "Fatura no", 5: "Düzenleme tarihi", 6: "Vergi hariç", 7: "Vergiler",
               8: "Ödenecek", 9: "KDV oranı", 10: "Matrah %18-20", 11: "KDV %18-20", 12: "Matrah %8-10", 13: "KDV %8-10",
               14: "Matrah %1", 15: "KDV %1", 16: "Para birimi", 17: "İptal/İtiraz"}
ADINA_TUTAR = {6, 7, 8, 10, 11, 12, 13, 14, 15}


def _adina_liste(ctx, mid, ay, yil):
    yol = "e-arsiv/liste/datatable_adimaduzenlenen"
    form = [("draw", "1"), ("start", "0"), ("length", "-1"), ("mukellef_id[]", str(mid)),
            ("tarih_baslangic", date(yil, ay, 1).strftime("%d.%m.%Y")), ("tarih_bitis", _ay_sonu(ay, yil).strftime("%d.%m.%Y"))]
    r = _post(ctx, "e-arsiv/liste/adimaduzenlenen", yol, form)
    j = _ham_json(r, yol)
    if not isinstance(j, dict) or not isinstance(j.get("data"), list):
        raise RuntimeError(f"{yol}: liste beklenen yapıda gelmedi")
    satirlar = []
    for s in j["data"]:
        if not isinstance(s, list) or len(s) < 18:
            raise RuntimeError("Adına düzenlenen satırı beklenen yapıda değil (kolon sayısı değişmiş).")
        if not _html_metin(s[4]):          # sondaki boş toplam satırı
            continue
        satirlar.append({ad: _html_metin(s[i]) for i, ad in ADINA_KOLON.items()})
    beklenen = j.get("recordsFiltered", j.get("recordsTotal"))
    if isinstance(beklenen, int) and beklenen != len(satirlar):
        raise RuntimeError(f"{yol}: liste eksik geldi ({len(satirlar)}/{beklenen})")
    return satirlar


def adina_duzenlenen(ctx, mid, ay, yil, guncelle=True, iptal=lambda: False):
    """Mükellef adına düzenlenen e-arşiv faturaları: (isteğe bağlı) aylık GİB sorgusu -> liste -> gelen e-arşiv listesiyle karşılaştırma."""
    sorgu = "E-Arşiv Mükellef Adına Düzenlenenler"
    kapsam = f"Düzenleme ayı {AYLAR[ay - 1]} {yil}; gelen e-arşiv listesiyle karşılaştırma"
    sayfa = "e-arsiv/sorgula/adimaduzenlenen"
    notlar, gib_sorun, iptal_itiraz = [], False, {}
    try:
        if guncelle and not iptal():
            form = {"mukellef_id": str(mid), "ay": f"{ay:02d}", "yil": str(yil), "faturaturu": "1"}
            ca = _json_tekrarli(ctx, sayfa, "e-arsiv/sorgula/checkauth/incoming", form)
            if not ca.get("success"):
                gib_sorun = True
                notlar.append("GİB sorgusu yapılamadı: " + (_mesaj(ca) or "yetki hatası") + " (E-Mükellef'teki mevcut liste kullanıldı).")
            else:
                inc = _json_tekrarli(ctx, sayfa, "e-arsiv/sorgula/incoming", form)
                if not inc.get("success"):
                    m = _mesaj(inc)
                    if not _kayit_yok_mesaji(m):
                        gib_sorun = True
                        notlar.append("GİB sorgusu hata verdi: " + (m or "yanıt yok"))
                else:
                    for fa in (inc.get("data") or {}).get("invoices") or []:
                        durum = fa.get("cancelAppealStatus") or fa.get("cancellationAppealResult")
                        if durum:
                            iptal_itiraz[str(fa.get("documentNumber"))] = str(durum)
                    notlar.append("GİB sorgusu: " + (_mesaj(inc) or "tamam") + ".")
        liste = _adina_liste(ctx, mid, ay, yil)
        gelen = {(f["Satıcı VKN"], f["Belge no"]) for f in _earsiv_liste(ctx, mid, ay, yil)}
        kolonlar = list(ADINA_KOLON.values()) + ["Gelen e-arşiv listesinde"]
        satirlar, eksik_gelen, ii = [], [], []
        for f in liste:
            no, vkn = f["Fatura no"], f["Satıcı VKN/TCKN"]
            listede = (vkn, no) in gelen
            if not listede:
                eksik_gelen.append(no)
            durum = f["İptal/İtiraz"] or iptal_itiraz.get(no, "")
            if durum:
                ii.append(f"{no} ({durum})")
            satirlar.append([_tr_sayi(f[k]) if i in ADINA_TUTAR else (durum if k == "İptal/İtiraz" else f[k])
                             for i, k in ADINA_KOLON.items()] + ["var" if listede else "YOK"])
        burada_yok = sorted(no for v, no in gelen if (v, no) not in {(f["Satıcı VKN/TCKN"], f["Fatura no"]) for f in liste})
        if eksik_gelen:
            notlar.append(f"UYARI: {len(eksik_gelen)} fatura gelen e-arşiv listesinde YOK: {', '.join(eksik_gelen[:5])} — "
                          "gelen e-arşiv GİB sorgusunu tekrarlayın veya faturayı kontrol edin.")
        if burada_yok:
            notlar.append(f"Gelen listede olup burada olmayan {len(burada_yok)} fatura: {', '.join(burada_yok[:5])}.")
        if ii:
            notlar.append(f"ACİL: İPTAL/İTİRAZ durumlu {len(ii)} fatura: {', '.join(ii[:5])}.")
        if not satirlar:
            return _sonuc(sorgu, kapsam, "hata" if gib_sorun else "kayit_yok",
                          " ".join(notlar + ["Bu ay mükellef adına düzenlenen e-arşiv faturası yok."]), kolonlar)
        tutar_indis = [j for j, i in enumerate(ADINA_KOLON) if i in ADINA_TUTAR]
        toplam = [""] * len(kolonlar)
        toplam[1] = "TOPLAM"
        for j in tutar_indis:
            toplam[j] = _topla(s[j] for s in satirlar)
        notlar.append(f"{len(satirlar)} fatura." + ("" if eksik_gelen or burada_yok else " Gelen e-arşiv listesiyle birebir tutuyor."))
        if _okunamayan(satirlar, tutar_indis):
            notlar.append("UYARI: okunamayan tutar var.")
        return _sonuc(sorgu, kapsam, "hata" if gib_sorun else "veri", " ".join(notlar), kolonlar, satirlar, toplam)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", " ".join(notlar + [str(e)]))


# ================================================================ Ek sorgular: iptal/itiraz, borç, alındı, tahsilat, evrak, bildirim, karşıt inceleme
class _TablolarOkuyucu(HTMLParser):
    """Her <table>'ı ayrı toplar (iç içe tablolar dahil); hücrelerin colspan değerini de saklar."""

    def __init__(self):
        super().__init__()
        self.tablolar, self._yigin = [], []
        self._satir, self._hucre, self._tur, self._colspan = None, [], None, 1

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._yigin.append({"basliklar": [], "satirlar": []})
        elif not self._yigin:
            return
        elif tag == "tr":
            self._satir = []
        elif tag in ("td", "th"):
            self._tur, self._hucre = tag, []
            try:
                self._colspan = int(a.get("colspan") or 1)
            except ValueError:
                self._colspan = 1

    def handle_endtag(self, tag):
        if not self._yigin:
            return
        t = self._yigin[-1]
        if tag in ("td", "th") and self._tur and self._satir is not None:
            self._satir.append((self._tur, re.sub(r"\s+", " ", "".join(self._hucre)).strip(), self._colspan))
            self._tur = None
        elif tag == "tr" and self._satir is not None:
            if self._satir and all(x[0] == "th" for x in self._satir) and not t["satirlar"]:
                t["basliklar"] = [x[1] for x in self._satir]
            elif self._satir:
                t["satirlar"].append(self._satir)
            self._satir = None
        elif tag == "table":
            self.tablolar.append(self._yigin.pop())

    def handle_data(self, data):
        if self._tur:
            self._hucre.append(data)


def _html_tablosu(html):
    """En çok veri satırı olan tablo -> (kolonlar, satırlar, düz metin). Tek hücreli/tam colspan'lı ("kayıt yok") satırlar atılır."""
    o = _TablolarOkuyucu()
    o.feed(html or "")
    en_iyi = ([], [])
    for t in o.tablolar:
        genislik = len(t["basliklar"]) or max((len(s) for s in t["satirlar"]), default=0)
        veri = [[h for _, h, _ in s] for s in t["satirlar"]
                if len(s) > 1 and not any(c >= max(genislik, 2) for _, _, c in s)]
        if len(veri) > len(en_iyi[1]):
            en_iyi = (t["basliklar"], veri)
    return en_iyi[0], en_iyi[1], _html_metin(html)


def _tutar_hucresi(h):
    """'1.234,56' gibi Türkçe tutar hücresini sayıya çevirir; diğerleri metin kalır."""
    return _tr_sayi(h) if isinstance(h, str) and re.fullmatch(r"-?\d{1,3}(\.\d{3})*,\d{2}", h.strip()) else h


def _html_sonucu(sorgu, kapsam, html, bos_mesaj):
    kolonlar, satirlar, metin = _html_tablosu(html)
    satirlar = [[_tutar_hucresi(h) for h in s] for s in satirlar]
    if satirlar:
        n = max(len(s) for s in satirlar)
        kolonlar = (kolonlar + [""] * n)[:n] if kolonlar else [f"Kolon {i + 1}" for i in range(n)]
        return _sonuc(sorgu, kapsam, "veri", f"{len(satirlar)} kayıt.", kolonlar, satirlar)
    if _kayit_yok_mesaji(metin):
        return _sonuc(sorgu, kapsam, "kayit_yok", metin[:200] or bos_mesaj)
    return _sonuc(sorgu, kapsam, "hata", "Beklenen tablo gelmedi: " + (metin[:200] or "boş yanıt"))


def _ay_geri(ay, yil, n):
    for _ in range(n):
        ay, yil = (12, yil - 1) if ay == 1 else (ay - 1, yil)
    return ay, yil


def _alti_ay():
    """Bugünden geriye 6 takvim ayı: 5 ay önceki ayın 1'i .. bugün (gelen evrak/bildirim/karşıt inceleme)."""
    bit = date.today()
    a, y = _ay_geri(bit.month, bit.year, 5)
    return date(y, a, 1), bit


class PencereYok(RuntimeError):
    """Ekranın GİB giriş penceresi yerine tam panel sayfası geldi (ekran E-Mükellef tarafında çalışmıyor olabilir)."""


def _panel_sayfasi_mi(ham):
    return "Yönetim Paneli" in ham[:5000] and "<title" in ham[:5000].lower()


def _modal_giris(ctx, sayfa, mid):
    """Sayfadaki 'Sorgula' düğmesinin ilk isteği: E-Mükellef bu adımda firmanın GİB oturumunu açar."""
    r = _get(ctx, sayfa, f"{sayfa}/modal/giris?id={mid}")
    if r.status != 200:
        raise RuntimeError(f"GİB giriş penceresi: HTTP {r.status}")
    ham = r.text()
    if _panel_sayfasi_mi(ham):
        # 18.11.2026: gelen evraklarda sitenin kendi "Sorgula" düğmesi de pencere yerine ana sayfayı aldı (E-Mükellef tarafı).
        raise PencereYok("E-Mükellef bu ekranda GİB giriş penceresi yerine ana sayfayı döndürdü; ekran şu an çalışmıyor olabilir "
                         "(E-Mükellef'te elle deneyin veya daha sonra tekrar çalıştırın).")
    metin = _html_metin(re.sub(r"<script.*?</script>", " ", ham, flags=re.S | re.I))
    if "Başarıyla" not in metin and re.search(r"hata|başarısız|yapılama|güvenlik kodu", metin, re.I):
        raise RuntimeError("GİB girişi yapılamadı: " + metin[:150])
    return r


def _liste_dogrula(liste):
    if not isinstance(liste, list) or any(not isinstance(x, dict) for x in liste):
        raise RuntimeError("Kayıtlar beklenen yapıda değil (liste/sözlük).")
    return liste


def iptal_itiraz(ctx, mid, ay, yil):
    s_ay, s_yil = _sonraki_ay(ay, yil)
    sorgu = "E-Arşiv Portal İptal/İtiraz Talepleri"
    kapsam = f"{AYLAR[ay - 1]} {yil} ve {AYLAR[s_ay - 1]} {s_yil}"
    sayfa = "e_arsiv_portal_iptal_itiraz"
    tum, hatalar = [], []
    try:
        for a, y in ((ay, yil), (s_ay, s_yil)):
            try:
                j = _json_tekrarli(ctx, sayfa, sayfa + "/sorgula", {"mukellef_id": str(mid), "ay": f"{a:02d}", "yil": str(y)})
                m = _mesaj(j)
                if not j.get("success"):
                    if not _kayit_yok_mesaji(m):
                        hatalar.append(f"{AYLAR[a - 1]} {y}: {m or 'yanıt yok'}")
                    continue
                liste = _tek_liste(j.get("data"))
                if liste is None:
                    hatalar.append(f"{AYLAR[a - 1]} {y}: yanıtta liste yok")
                    continue
                tum += _liste_dogrula(liste)
            except OturumYok:
                raise
            except Exception as e:
                hatalar.append(f"{AYLAR[a - 1]} {y}: {e}")
        kolonlar, satirlar = _genel_tablo(tum) if tum else ([], [])
        notlar = [f"ACİL: {len(satirlar)} iptal/itiraz talebi var — faturaları kontrol edin."] if satirlar else []
        if hatalar:
            return _sonuc(sorgu, kapsam, "hata", " ".join(notlar + ["Sorgulanamayan ay: " + "; ".join(hatalar)]), kolonlar, satirlar)
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "İptal/itiraz talebi yok.")
        return _sonuc(sorgu, kapsam, "veri", " ".join(notlar), kolonlar, satirlar)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


def vergi_borcu(ctx, mid):
    sorgu = "Vergi Borcu"
    kapsam = "Toplam borç (bugün itibarıyla)"
    sayfa = "vergi_borc_sorgula"
    try:
        # DİKKAT: formdaki "mukellef_gonder" (mail/SMS ile müşteriye gönderir) bilerek GÖNDERİLMEZ; yalnız bu iki alan gider.
        j = _json_tekrarli(ctx, sayfa, sayfa + "/sorgula", {"mukellef_id": str(mid), "borcturu": "toplam"})
        data = j.get("data") if isinstance(j.get("data"), dict) else {}
        if not j.get("success") or not isinstance(data.get("borc"), dict):
            m = _mesaj(j)
            if _kayit_yok_mesaji(m):
                return _sonuc(sorgu, kapsam, "kayit_yok", m)
            return _sonuc(sorgu, kapsam, "hata", m or "Beklenmeyen yanıt.")
        notlar = []
        if data.get("gonderim"):
            notlar.append("UYARI: E-Mükellef gönderim kaydı döndü; müşteriye bildirim gitmiş olabilir, kontrol edin.")
        toplam = data["borc"].get("toplam") or {}
        asil, gz, top = (_tr_sayi(toplam.get(k)) for k in ("toplam_vabsum", "toplam_gzsum", "toplam_borc"))
        kalemler = _liste_dogrula(data["borc"].get("data") or [])
        if kalemler:
            kolonlar, satirlar = _genel_tablo(kalemler)
            notlar.insert(0, f"Asıl borç {asil}, gecikme zammı {gz}, toplam {top}.")
            return _sonuc(sorgu, kapsam, "veri", " ".join(notlar), kolonlar, satirlar, top)
        if top:
            return _sonuc(sorgu, kapsam, "veri", " ".join(["Borç var (kalem ayrıntısı gelmedi)."] + notlar),
                          ["Asıl borç", "Gecikme zammı", "Toplam borç"], [[asil, gz, top]], top)
        return _sonuc(sorgu, kapsam, "kayit_yok" if not notlar else "hata", " ".join(["Vergi borcu yok."] + notlar))
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


def odeme_alindi(ctx, mid, ay, yil):
    s_ay, s_yil = _sonraki_ay(ay, yil)
    sorgu = "Ödeme / Alındı (tüm vergiler)"
    kapsam = f"Ödeme ayı {AYLAR[ay - 1]} {yil} ve {AYLAR[s_ay - 1]} {s_yil}"
    sayfa = "emukellef/gib_odeme_alindi_sorgula"
    kolonlar = ["Vergi", "Vergi dönemi", "Ödeme tarihi", "Tutar", "Belge no", "Ödeme türü", "Vergi dairesi"]
    satirlar, hatalar = [], []
    try:
        for a, y in ((ay, yil), (s_ay, s_yil)):
            try:
                j = _json_tekrarli(ctx, sayfa, sayfa + "/sorgula", {"id": str(mid), "ay": f"{a:02d}", "yil": str(y), "vergikodu": "hepsi"})
                data = j.get("data") if isinstance(j.get("data"), dict) else {}
                if not j.get("success"):
                    m = _mesaj(j)
                    if not (data.get("notfound") or _kayit_yok_mesaji(m)):
                        hatalar.append(f"{AYLAR[a - 1]} {y}: {m or 'yanıt yok'}")
                    continue
                for o in _liste_dogrula(data.get("odemeleralindilar") or []):
                    satir = [o.get("vergiTurleri") or o.get("vergiKodu"), o.get("vergiDonemFormatted"), o.get("odemeTarihi"),
                             _sayi(o.get("odemeMiktar")), o.get("belgeNo"), o.get("odemeTuru"), o.get("vergiDairesiAdi")]
                    satirlar.append(satir)          # tekilleştirme yok: ödeme yalnız kendi ödeme ayında görünür
            except OturumYok:
                raise
            except Exception as e:
                hatalar.append(f"{AYLAR[a - 1]} {y}: {e}")
        toplam = _topla(s[3] for s in satirlar) if satirlar else None
        if hatalar:
            return _sonuc(sorgu, kapsam, "hata", f"{len(satirlar)} ödeme alındı; sorgulanamayan ay: " + "; ".join(hatalar), kolonlar, satirlar, toplam)
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Bu aylarda ödeme yok.", kolonlar)
        return _sonuc(sorgu, kapsam, "veri", f"{len(satirlar)} ödeme (önceki dönem beyannamelerinin ödenip ödenmediğini buradan kontrol edin).",
                      kolonlar, satirlar, toplam)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


def tahsilat(ctx, mid, ay, yil):
    b_ay, b_yil = _ay_geri(ay, yil, 1)
    s_ay, s_yil = _sonraki_ay(ay, yil)
    sorgu = "GİB Tahsilat Bilgileri"
    kapsam = f"{AYLAR[b_ay - 1]} {b_yil} - {AYLAR[s_ay - 1]} {s_yil}, tüm vergi daireleri"
    sayfa = "emukellef/gib_tahsilat_bilgileri"
    kolonlar, satirlar, hatalar, bos = None, [], [], 0
    try:
        _modal_giris(ctx, sayfa, mid)
        r = _get(ctx, sayfa, f"{sayfa}/modal/tahsilat_bilgisi?id={mid}")
        if r.status != 200:
            return _sonuc(sorgu, kapsam, "hata", f"Tahsilat penceresi: HTTP {r.status}")
        vdler = list(dict.fromkeys(re.findall(r"""<option[^>]*value=['"](\d{6})['"]""", r.text())))
        if not vdler:
            return _sonuc(sorgu, kapsam, "hata", "Firmanın vergi dairesi seçenekleri okunamadı.")
        for vd in vdler:
            try:
                h = _post(ctx, sayfa, f"{sayfa}/tahsilat_sorgula", {"id": str(mid), "vd": vd, "vergikodu": "hepsi",
                                                                     "basay": f"{b_ay:02d}", "basyil": str(b_yil),
                                                                     "bitay": f"{s_ay:02d}", "bityil": str(s_yil),
                                                                     "vergi_borcu_sorgula": "Sorgula"}).text()
                k, s, metin = _html_tablosu(h)
                if s:
                    n = max(len(x) for x in s)
                    kolonlar = kolonlar or (["Vergi dairesi"] + ((k + [""] * n)[:n] if k else [f"Kolon {i + 1}" for i in range(n)]))
                    satirlar += [[vd] + [_tutar_hucresi(h) for h in x] for x in s]
                elif _kayit_yok_mesaji(metin):
                    bos += 1
                else:
                    hatalar.append(f"VD {vd}: " + (metin[:100] or "boş yanıt"))
            except OturumYok:
                raise
            except Exception as e:
                hatalar.append(f"VD {vd}: {e}")
        if hatalar:
            return _sonuc(sorgu, kapsam, "hata", f"{len(satirlar)} satır alındı; sorgulanamayan: " + "; ".join(hatalar), kolonlar, satirlar)
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", f"Tahsilat yok ({len(vdler)} vergi dairesi sorgulandı).")
        return _sonuc(sorgu, kapsam, "veri", f"{len(satirlar)} tahsilat satırı ({len(vdler)} vergi dairesi).", kolonlar, satirlar)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e), kolonlar, satirlar)


def gelen_evrak(ctx, mid):
    bas, bit = _alti_ay()
    sorgu = "GİB Gelen Evraklar"
    kapsam = f"{bas:%d.%m.%Y} - {bit:%d.%m.%Y}"
    sayfa = "emukellef/gib_gelen_evraklar"
    try:
        yedek = None
        try:
            _modal_giris(ctx, sayfa, mid)
        except PencereYok as e:
            # Yedek (19.11.2026): bu ekranın penceresi çalışmıyorsa firmanın GİB oturumunu Bildirimlerim penceresiyle aç, sorguyu yine dene.
            yedek = e
            _modal_giris(ctx, "emukellef/gib_bildirimlerim", mid)
        h = _post(ctx, sayfa, f"{sayfa}/tekli_sorgula", {"id": str(mid), "okunma_durumu": "", "tarih_baslangic": f"{bas:%d.%m.%Y}",
                                                          "tarih_bitis": f"{bit:%d.%m.%Y}", "gib_gelen_evraklar_sorgula": "Sorgula"}).text()
        if _panel_sayfasi_mi(h):
            raise RuntimeError(str(yedek) if yedek else "E-Mükellef sorgu yerine ana sayfayı döndürdü; ekran şu an çalışmıyor olabilir.")
        s = _html_sonucu(sorgu, kapsam, h, "Gelen evrak yok.")
        if yedek is not None:
            # Yedek yolda gelen tablo gerçekten evrak listesi mi (ilgisiz bir tablo 'veri' sayılmasın)
            evrak_tablosu = s["durum"] == "veri" and re.search(r"evrak|konu", " ".join(map(str, s["kolonlar"])), re.I)
            if s["durum"] == "hata" or (s["durum"] == "veri" and not evrak_tablosu):
                return _sonuc(sorgu, kapsam, "hata", f"{yedek} [yedek yol da sonuç vermedi: {s['mesaj'][:120]}]")
            s["mesaj"] += " (Ekranın kendi penceresi çalışmadı; sonuç yedek yoldan alındı.)"
        if s["durum"] == "veri":
            s["mesaj"] += " ACİL: GİB'den gelen evrakları okuyun (ekleri E-Mükellef'ten açılır)."
        return s
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


def bildirimler(ctx, mid):
    bas, bit = _alti_ay()
    sorgu = "GİB Bildirimlerim"
    kapsam = f"{bas:%d.%m.%Y} - {bit:%d.%m.%Y}"
    sayfa = "emukellef/gib_bildirimlerim"
    try:
        _modal_giris(ctx, sayfa, mid)
        j = _ham_json(_post(ctx, sayfa, f"{sayfa}/sorgula", {"mukellef_id": str(mid), "baslangic_tarihi": f"{bas:%d.%m.%Y}",
                                                             "bitis_tarihi": f"{bit:%d.%m.%Y}"}), "bildirim")
        # Yanıt "success": "no_action" olabilir; asıl içerik data.func[...].data içindeki HTML tablodur.
        func = ((j.get("data") or {}).get("func") or {}) if isinstance(j, dict) and isinstance(j.get("data"), dict) else {}
        html = next((v.get("data") for v in func.values() if isinstance(v, dict) and isinstance(v.get("data"), str)), None)
        if html is None:
            return _sonuc(sorgu, kapsam, "hata", "Beklenmeyen yanıt: " + (_mesaj(j)[:150] if isinstance(j, dict) else ""))
        return _html_sonucu(sorgu, kapsam, html, "Bildirim yok.")
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


def karsit_inceleme(ctx, mid):
    bas, bit = _alti_ay()
    sorgu = "Gelen Karşıt İnceleme Tutanakları"
    kapsam = f"{bas:%d.%m.%Y} - {bit:%d.%m.%Y}, tüm durumlar"
    sayfa = "emukellef/gelen_karsit_inceleme_tutanaklari"
    try:
        j = _json_tekrarli(ctx, sayfa, sayfa + "/sorgula", {"id": str(mid), "durum": "", "baslangic": f"{bas:%d.%m.%Y}",
                                                            "bitis": f"{bit:%d.%m.%Y}"})
        data = j.get("data") if isinstance(j.get("data"), dict) else {}
        if not j.get("success"):
            if data.get("code") == "notfound":
                return _sonuc(sorgu, kapsam, "kayit_yok", _mesaj(j) or "Kayıt yok.")
            return _sonuc(sorgu, kapsam, "hata", _mesaj(j) or "Beklenmeyen yanıt.")
        liste = _liste_dogrula(data.get("list"))
        kolonlar, satirlar = _genel_tablo(liste)
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", "Kayıt yok.", kolonlar)
        return _sonuc(sorgu, kapsam, "veri", f"ACİL: {len(satirlar)} karşıt inceleme tutanağı — cevap süresini kontrol edin.", kolonlar, satirlar)
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", str(e))


# ================================================================ E-tebligat
TEBLIGAT_TAMAM = ("notfound", "already-downloaded")
TEBLIGAT_YOK = re.compile(r"^\s*listelenecek tebligat bulunamad", re.I)     # kurumda hiç tebligat yok (hata değil)


def _tarih_zaman(metin):
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?", str(metin or ""))
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4) or 0), int(m.group(5) or 0))
    except ValueError:                                 # 00.00.0000 gibi geçersiz tarih
        return None


def imzali_pdf_cikar(bayt):
    """E-tebligat dosyası CMS/PKCS#7 imzalı pakettir; içindeki PDF'i (%PDF- .. son %%EOF) çıkarır, açılamazsa None."""
    if not bayt:
        return None
    if bayt[:5] == b"%PDF-":
        return bayt
    i, j = bayt.find(b"%PDF-"), bayt.rfind(b"%%EOF")
    if i < 0 or j <= i:
        return None
    pdf = bayt[i:j + 5]
    try:
        from pypdf import PdfReader
        if len(PdfReader(io.BytesIO(pdf)).pages) < 1:
            return None
    except Exception:
        return None
    return pdf


def etebligat(ctx, mid, guncelle=True, iptal=lambda: False):
    """E-tebligatlar: (isteğe bağlı) GİB/kurum sorgusu -> son 6 ayın listesi -> her tebligatın imzalı dosyası.
    Döner: (sonuç, belgeler[{zaman, teblig, aciklama, birim, bayt}])"""
    bas, bit = _alti_ay()
    sorgu = "E-Tebligatlar"
    kapsam = f"{bas:%d.%m.%Y} - {bit:%d.%m.%Y} (gönderim tarihi)"
    sayfa = "etebligat_toplu_sorgula"
    notlar, hatalar, belgeler = [], [], []
    try:
        if guncelle and not iptal():
            # DİKKAT: formdaki "mukellef_gonder" (mail/SMS ile müşteriye gönderir) bilerek GÖNDERİLMEZ.
            j = _json_tekrarli(ctx, sayfa, sayfa + "/gib_sorgula", {"mukellef_id": str(mid), "tarih_baslangic": f"{bas:%d.%m.%Y}",
                                                                    "tarih_bitis": f"{bit:%d.%m.%Y}"})
            data = j.get("data") if isinstance(j.get("data"), dict) else {}
            if data.get("gonder"):
                notlar.append("UYARI: E-Mükellef gönderim bilgisi döndü; müşteriye bildirim gitmiş olabilir, kontrol edin.")
            kurumlar = data.get("tebligatlar")
            if not isinstance(kurumlar, dict):
                hatalar.append("Kurum sorgusu: " + (_mesaj(j) or "beklenmeyen yanıt"))
            else:
                yeni = []
                for kurum, v in kurumlar.items():
                    d = v.get("data") if isinstance(v, dict) and isinstance(v.get("data"), dict) else {}
                    ad = d.get("kurum_adi") or kurum
                    if isinstance(v, dict) and v.get("success"):
                        yeni.append(f"{ad}: {_html_metin(d.get('msg')) or 'yeni tebligat indirildi'}")
                    elif d.get("error_code") not in TEBLIGAT_TAMAM and not TEBLIGAT_YOK.search(_html_metin(d.get("msg"))):
                        hatalar.append(f"{ad}: {_html_metin(d.get('msg')) or d.get('error_code') or 'hata'}")
                    # Yalnız "Listelenecek tebligat bulunamadı." (hiç tebligatı olmayan firma) hata sayılmaz; başka "bulunamadı"lar hatadır
                notlar.append("Kurum sorgusu: " + ("; ".join(yeni) if yeni else "yeni tebligat yok") + ".")
        satirlar_ham = []
        for yil in sorted({bas.year, bit.year}):
            form = [("draw", "1"), ("start", "0"), ("length", "-1"), ("user_id[]", str(mid)), ("ay", "tum-aylar"), ("yil", str(yil)),
                    ("birimler", ""), ("okundu", ""), ("aciklama", "")]
            satirlar_ham += _tablo_json(_post(ctx, sayfa, sayfa + "/datatable", form), sayfa + "/datatable")
        kolonlar = ["Birim", "Gönderim zamanı", "Tebliğ zamanı", "Açıklama", "İndirme"]
        satirlar, acil = [], []
        for s in satirlar_ham:
            if iptal():
                notlar.append("İndirme iptal edildi; bazı tebligatlar indirilmedi.")
                hatalar.append("iptal")
                break
            h = [str(x) for x in s]
            if len(h) < 7:
                raise RuntimeError("E-tebligat satırı beklenen yapıda değil.")
            gonderim, teblig = _tarih_zaman(_html_metin(h[3])), _tarih_zaman(_html_metin(h[4]))
            birim, aciklama = _html_metin(h[2]), _html_metin(h[5])
            if gonderim is None:                       # sessizce atlanmasın: hata olarak görünsün
                hatalar.append(f"gönderim tarihi okunamayan tebligat ({_html_metin(h[3]) or '-'}): {aciklama}")
                continue
            if not (bas <= gonderim.date() <= bit):
                continue
            m = re.search(r"""href=['"]([^'"]*goruntule[^'"]*)['"]""", " ".join(h))
            durum = "bağlantı yok"
            if m:
                adres = unescape(m.group(1))
                adres = adres if adres.startswith("http") else ADRES + (adres if adres.startswith("/") else "/" + adres)
                r = _get(ctx, sayfa, adres)
                b = r.body()
                if r.status == 200 and b:
                    belgeler.append({"zaman": gonderim, "teblig": teblig, "aciklama": aciklama, "birim": birim, "bayt": b})
                    durum = "indirildi"
                else:
                    durum = f"İNDİRİLEMEDİ (HTTP {r.status})"
            if durum != "indirildi":
                hatalar.append(f"{gonderim:%d.%m.%Y} {aciklama}: {durum}")
            yakin = teblig or gonderim
            if (date.today() - yakin.date()).days <= 30:
                acil.append(f"{yakin:%d.%m.%Y} {aciklama} ({birim})")
            satirlar.append([birim, f"{gonderim:%d.%m.%Y %H:%M}", f"{teblig:%d.%m.%Y %H:%M}" if teblig else "", aciklama, durum])
        if acil:
            notlar.insert(0, f"ACİL: son 30 günde {len(acil)} tebligat: " + "; ".join(acil[:5]) + " — okuyun ve süreleri takip edin.")
        if hatalar:
            return _sonuc(sorgu, kapsam, "hata", " ".join(notlar + [f"{len(satirlar)} tebligat. Sorun: " + "; ".join(hatalar[:5])]), kolonlar, satirlar), belgeler
        if not satirlar:
            return _sonuc(sorgu, kapsam, "kayit_yok", " ".join(notlar + ["Bu aralıkta e-tebligat yok."]), kolonlar), []
        return _sonuc(sorgu, kapsam, "veri", " ".join(notlar + [f"{len(satirlar)} tebligat."]), kolonlar, satirlar), belgeler
    except OturumYok:
        raise
    except Exception as e:
        return _sonuc(sorgu, kapsam, "hata", " ".join(notlar + [str(e)])), belgeler
