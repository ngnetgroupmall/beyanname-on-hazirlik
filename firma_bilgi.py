"""Firma bilgileri (kullanıcı isteği 27.11.2026): E-Mükellef'in 'GİB'den Sorgula' işlemi (vergi no, vergi dairesi, vergi türleri,
e-defter, NACE, ortaklık/yöneticilik, sicil) çalıştırılır; güncellenen mükellef kartı okunup firma klasörüne yazılır:
    <Firmalar>\\<firma>\\Genel\\Firma Bilgileri.md   (okunacak özet)
    <Firmalar>\\<firma>\\Genel\\firma_bilgileri.json (program/pano için)
Mükellef kartındaki ŞİFRELER (İVD, e-Devlet, KEP vb. — 'Kurum Bilgileri' sekmesi) hiçbir zaman okunmaz ve yazılmaz.
Yöneticilerin T.C. kimlik numaraları da dosyaya yazılmaz."""
import json
import os
import re
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser

import cikti
import emukellef

KONU = "Genel"
MD_ADI = "Firma Bilgileri.md"
JSON_ADI = "firma_bilgileri.json"
YENILEME_GUN = 30                                   # normal sorguda bu kadar günden eski bilgi kendiliğinden yenilenir
GIB_SORGULARI = ["vergi_numarasi", "vergi_dairesi", "vergi_turu", "edefter", "nace_kod", "ortaklik_yoneticilik", "sicil"]
SORUMLU_DEGIL = "Sorumlu Değil"
_KIMLIK = [("vn", "VKN / TCKN"), ("vd_kodu", "Vergi dairesi"), ("acilis_tarihi", "Açılış tarihi"), ("kapanis_tarihi", "Kapanış tarihi"),
           ("dogum_tarihi", "Doğum tarihi"), ("is_yeri_adres", "İş yeri adresi"), ("ikametgah_adres", "İkametgah adresi")]
_ILETISIM = [("eposta", "eposta_unvan"), ("eposta2", "eposta2_unvan"), ("eposta3", "eposta3_unvan"),
             ("gsm", "gsm_unvan"), ("gsm2", "gsm2_unvan"), ("gsm3", "gsm3_unvan")]
_GIZLI = re.compile(r"sifre|parola|password", re.I)


# ================================================================ dosya yolları
def klasor(firma_klasoru):
    return cikti.firma_klasoru(firma_klasoru) / KONU


def oku(firma_klasoru):
    """Kayıtlı bilgiler (yoksa None). Arayüz iş parçacığından da çağrılır: hata vermez."""
    try:
        yol = cikti.FIRMALAR / str(firma_klasoru) / KONU / JSON_ADI
        return json.loads(yol.read_text(encoding="utf-8"))
    except Exception:
        return None


def eski_mi(firma_klasoru, gun=YENILEME_GUN):
    """Hiç alınmamış, GİB sorgusu başarısız kalmış ya da 'gun' günden eski bilgi yenilenmelidir."""
    b = oku(firma_klasoru)
    try:
        if not b.get("gib_basarili", True):
            return True
        return (datetime.now() - datetime.fromisoformat(b["guncelleme"])).days >= gun
    except Exception:
        return True


# ================================================================ HTML ayrıştırma
class _Kart(HTMLParser):
    """Mükellef kartı (modal/ekle_guncelle): form alanları (bölüm = içinde bulunduğu 'collapse-…' kutusu, etiket = önceki <label>)
    ve tablolar (başlık + satırlar; tablodan önceki son başlık/etiket metniyle)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.divler, self.alanlar, self.tablolar = [], [], []
        self.etiket, self.etiket_ac, self.metin_son = "", False, ""
        self.secim = None                           # açık <select>: {ad, etiket, bolum, coklu, secili:[], ilk}
        self.secenek = None                         # açık <option>: [secili mi, metin]
        self.tablo = self.satir = self.hucre = None
        self.yazi = None                            # açık <textarea>

    def _bolum(self):
        return next((d for d in reversed(self.divler) if d and d.startswith("collapse-")), "")

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            self.divler.append(a.get("id"))
        elif tag == "label":
            self.etiket_ac, self.etiket = True, ""
        elif tag == "input" and a.get("name") and (a.get("type") or "text").lower() in ("text", "hidden", "email", "tel", "number", ""):
            self.alanlar.append({"ad": a["name"], "deger": a.get("value") or "", "etiket": self.etiket.strip(), "bolum": self._bolum()})
        elif tag == "textarea" and a.get("name"):
            self.yazi = {"ad": a["name"], "deger": "", "etiket": self.etiket.strip(), "bolum": self._bolum()}
        elif tag == "select" and a.get("name"):
            self.secim = {"ad": a["name"], "etiket": self.etiket.strip(), "bolum": self._bolum(), "coklu": "multiple" in a,
                          "secili": [], "ilk": None}
        elif tag == "option" and self.secim is not None:
            self.secenek = ["selected" in a, ""]
        elif tag == "table":
            sekme = next((d for d in reversed(self.divler) if d and d.startswith("tab-")), "")
            self.tablo = {"onceki": self.metin_son.strip(), "sekme": sekme, "baslik": [], "satirlar": []}
        elif tag == "tr" and self.tablo is not None:
            self.satir = []
        elif tag in ("td", "th") and self.satir is not None:
            self.hucre = ""

    def handle_endtag(self, tag):
        if tag == "div" and self.divler:
            self.divler.pop()
        elif tag == "label":
            self.etiket_ac = False
        elif tag == "textarea" and self.yazi is not None:
            self.alanlar.append(self.yazi)
            self.yazi = None
        elif tag == "option" and self.secenek is not None and self.secim is not None:
            secili, metin = self.secenek
            metin = re.sub(r"\s+", " ", metin).strip()
            if self.secim["ilk"] is None:
                self.secim["ilk"] = metin
            if secili:
                self.secim["secili"].append(metin)
            self.secenek = None
        elif tag == "select" and self.secim is not None:
            s = self.secim
            deger = s["secili"] if s["coklu"] else (s["secili"][0] if s["secili"] else (s["ilk"] or ""))
            self.alanlar.append({"ad": s["ad"], "deger": deger, "etiket": s["etiket"], "bolum": s["bolum"]})
            self.secim = None
        elif tag in ("td", "th") and self.hucre is not None and self.satir is not None:
            self.satir.append((tag, re.sub(r"\s+", " ", self.hucre).strip()))
            self.hucre = None
        elif tag == "tr" and self.satir is not None and self.tablo is not None:
            if self.satir and all(t == "th" for t, _ in self.satir) and not self.tablo["baslik"]:
                self.tablo["baslik"] = [m for _, m in self.satir]
            elif self.satir:
                self.tablo["satirlar"].append([m for _, m in self.satir])
            self.satir = None
        elif tag == "table" and self.tablo is not None:
            self.tablolar.append(self.tablo)
            self.tablo = None

    def handle_data(self, data):
        if self.etiket_ac:
            self.etiket += data
        if self.secenek is not None:
            self.secenek[1] += data
        if self.yazi is not None:
            self.yazi["deger"] += data
        if self.hucre is not None:
            self.hucre += data
        if data.strip() and self.tablo is None:
            self.metin_son = data


def _kart_coz(html):
    k = _Kart()
    k.feed(html)
    k.close()
    return k


def kart_bilgileri(html):
    """Mükellef kartından firma bilgileri (şifre alanları atlanır)."""
    k = _kart_coz(html)
    alan = {}
    vergi = []
    for a in k.alanlar:
        if _GIZLI.search(a["ad"]):
            continue
        if a["bolum"] == "collapse-mukellef-vergi-turu" and isinstance(a["deger"], str):
            vergi.append({"vergi": a["etiket"] or a["ad"], "donem": a["deger"]})
        elif a["ad"].endswith("[]"):
            alan.setdefault(a["ad"], [])
            alan[a["ad"]] += a["deger"] if isinstance(a["deger"], list) else ([a["deger"]] if a["deger"] else [])
        elif a["ad"] not in alan:
            alan[a["ad"]] = a["deger"]
    if not alan.get("vn") or not vergi:
        raise RuntimeError("Mükellef kartı beklenen yapıda değil (vergi numarası / vergi türleri okunamadı).")
    kimlik = {ad: str(alan.get(k_) or "").strip() for k_, ad in _KIMLIK}
    kimlik["Vergi dairesi"] = re.sub(r"\s*\(\d+\)\s*$", "", kimlik["Vergi dairesi"]) if kimlik["Vergi dairesi"] != "Seçiniz" else ""
    iletisim = []
    for k_, u in _ILETISIM:
        if str(alan.get(k_) or "").strip():
            iletisim.append({"tur": "E-posta" if k_.startswith("eposta") else "Telefon", "deger": str(alan[k_]).strip(),
                             "kisi": str(alan.get(u) or "").strip()})
    return {"unvan": str(alan.get("unvan") or "").strip(), "kimlik": kimlik, "vergi_turleri": vergi,
            "e_defter": {"E-Defter": str(alan.get("e_defter") or ""), "E-Defter geçiş tarihi": str(alan.get("e_defter_gecis_tarihi") or ""),
                         "E-Envanter": str(alan.get("e_envanter") or "")},
            "nace": [n for n in alan.get("nace_kodu[]", []) if n and n != "Seçiniz"], "iletisim": iletisim}


def ortaklik_bilgileri(html):
    """'Ortaklık ve Yöneticilik Bilgisi' sekmesi: ortaklar ve yöneticiler (T.C. kimlik no sütunu atlanır)."""
    k = _kart_coz(html)
    ortak, yonetici = [], []
    for t in k.tablolar:
        b = t["baslik"]
        if not b or not t["satirlar"]:
            continue
        atla = {i for i, x in enumerate(b) if x in ("#", "") or "T.C." in x or "TC Kimlik" in x}
        satirlar = [{b[i]: s[i] for i in range(min(len(b), len(s))) if i not in atla} for s in t["satirlar"]
                    if len(s) > 1 and not any("bulunamadı" in x for x in s)]
        if t.get("sekme") == "tab-yoneticilik-bilgisi" or (t.get("sekme") != "tab-ortaklik-bilgisi" and any("Yönetici" in x for x in b)):
            yonetici += satirlar
        else:
            ortak += satirlar
    return {"ortaklar": ortak, "yoneticiler": yonetici}


def _sorgu_sonucu(html):
    """GİB sorgusunun sonuç kartı -> [{baslik, durum ('tamam'/'uyari'/'hata'), mesaj}]."""
    sonuc = []
    for parca in re.split(r'<div class="row">', html)[1:]:
        ikon = re.search(r'<i class="fa ([^"]*)"', parca)
        metin = [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", x))).strip()
                 for x in re.findall(r'<div class="col-xs-12[^"]*">(.*?)</div>', parca, re.S)]
        metin = [m for m in metin if m]
        if not metin:
            continue
        sinif = ikon.group(1) if ikon else ""
        durum = "tamam" if "text-success" in sinif else ("uyari" if "text-warning" in sinif else "hata")
        sonuc.append({"baslik": metin[0], "durum": durum, "mesaj": " ".join(metin[1:])})
    return sonuc


# ================================================================ E-Mükellef
def gib_sorgula(ctx, mid):
    """E-Mükellef'te 'GİB'den Sorgula' (7 sorgunun hepsi). Mükellef kartını GİB'deki bilgilerle günceller."""
    yol = "mukellef/datatable_secili_islem"
    form = [("islem", "gib-sorgula")] + [("sorgular[]", s) for s in GIB_SORGULARI] + [("id", json.dumps([int(mid)]))]
    j = emukellef._ham_json(emukellef._post(ctx, "mukellef", yol, form, zaman_asimi=300_000), yol)
    if not isinstance(j, dict) or "mesaj" not in j:
        raise RuntimeError("GİB sorgusu beklenmeyen yanıt verdi.")
    sonuc = _sorgu_sonucu(str(j.get("mesaj") or ""))
    if not j.get("durum") and not sonuc:
        raise RuntimeError("GİB sorgusu yapılamadı: " + re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(j.get("mesaj") or "")))).strip()[:200])
    return sonuc


def _kart_html(ctx, mid, sekme=None):
    yol = f"mukellef/modal/ekle_guncelle?duzenle={int(mid)}" + (f"&tab={sekme}" if sekme else "")
    r = emukellef._get(ctx, "mukellef", yol)
    if r.status != 200:
        raise RuntimeError(f"Mükellef kartı açılamadı (HTTP {r.status}).")
    h = r.text()
    if "SMMM Paneli" in h and "Mali Müşavir Girişi" in h:
        raise emukellef.OturumYok("E-Mükellef oturumu düşmüş; yeniden giriş yapın.")
    return h


def _no(v):
    return re.sub(r"\D", "", str(v or ""))


def topla(ctx, mid, beklenen_vkn="", gib=True, iptal=lambda: False):
    """Mükellef kartı önce okunup VKN doğrulanır (yanlış eşlemede başka mükellefin kartı GİB'den güncellenmesin); sonra GİB sorgusu
    (gib=True), kart ve ortaklık sekmesi yeniden okunur -> bilgi sözlüğü."""
    once = kart_bilgileri(_kart_html(ctx, mid))
    kv, fv = _no(once["kimlik"].get("VKN / TCKN")), _no(beklenen_vkn)
    if fv and kv != fv:
        raise RuntimeError(f"E-Mükellef'teki mükellef kartının numarası ({kv or 'boş'}) firmanınkiyle ({fv}) aynı değil. GİB sorgusu yapılmadı.")
    if iptal():
        raise RuntimeError("Durduruldu.")
    sorgu = gib_sorgula(ctx, mid) if gib else []
    if iptal():
        raise RuntimeError("Durduruldu (GİB sorgusu yapılmıştı; dosya yazılmadı).")
    b = kart_bilgileri(_kart_html(ctx, mid))
    if fv and _no(b["kimlik"].get("VKN / TCKN")) != fv:
        raise RuntimeError("GİB sorgusundan sonra mükellef kartının numarası değişti; kaydedilmedi.")
    b.update(ortaklik_bilgileri(_kart_html(ctx, mid, "sirket-ortaklik-ve-yoneticilik-bilgisi")))
    b["gib_sorgusu"] = sorgu
    b["gib_basarili"] = bool(sorgu) and any(x["durum"] != "hata" for x in sorgu)
    b["emukellef_id"] = int(mid)
    b["guncelleme"] = datetime.now().isoformat(timespec="seconds")
    return b


# ================================================================ kaydetme
def _degisenler(eski, yeni):
    if not eski:
        return []
    d = []
    for ad in ("Vergi dairesi", "İş yeri adresi", "Kapanış tarihi"):
        if (eski.get("kimlik") or {}).get(ad, "") != yeni["kimlik"].get(ad, ""):
            d.append(f"{ad}: {(eski.get('kimlik') or {}).get(ad) or '—'} → {yeni['kimlik'].get(ad) or '—'}")
    ev = {v["vergi"]: v["donem"] for v in eski.get("vergi_turleri") or []}
    for v in yeni["vergi_turleri"]:
        if v["vergi"] in ev and ev[v["vergi"]] != v["donem"]:
            d.append(f"{v['vergi']}: {ev[v['vergi']]} → {v['donem']}")
    for ad in ("E-Defter", "E-Envanter"):
        if (eski.get("e_defter") or {}).get(ad, "") != yeni["e_defter"].get(ad, ""):
            d.append(f"{ad}: {(eski.get('e_defter') or {}).get(ad) or '—'} → {yeni['e_defter'].get(ad) or '—'}")
    if sorted(eski.get("nace") or []) != sorted(yeni["nace"]):
        d.append("NACE kodu değişti")
    isim = lambda xs: sorted(str(x.get("Unvan / Ad Soyad") or x) for x in xs or [])
    if isim(eski.get("yoneticiler")) != isim(yeni["yoneticiler"]):
        d.append("Yönetici listesi değişti")
    if isim(eski.get("ortaklar")) != isim(yeni["ortaklar"]):
        d.append("Ortak listesi değişti")
    return d


def _tablo(satirlar):
    if not satirlar:
        return ["—"]
    b = list(dict.fromkeys(k for s in satirlar for k in s))
    temiz = lambda v: str(v or "").replace("|", "/")
    return ["| " + " | ".join(b) + " |", "|" + "---|" * len(b)] + ["| " + " | ".join(temiz(s.get(k)) for k in b) + " |" for s in satirlar]


def markdown(b, firma_adi=""):
    t = datetime.fromisoformat(b["guncelleme"]).strftime("%d.%m.%Y %H:%M")
    s = [f"# {b.get('unvan') or firma_adi} — Firma Bilgileri", "",
         f"Kaynak: E-Mükellef mükellef kartı (GİB'den sorgulanarak güncellendi) · Güncelleme: {t}",
         "Bu dosyayı Beyanname Ön Hazırlık programı yazar; elle düzeltme bir sonraki güncellemede silinir.", ""]
    if b.get("degisenler"):
        s += ["## Son güncellemede değişenler", ""] + [f"- {x}" for x in b["degisenler"]] + [""]
    s += ["## Kimlik ve sicil", "", "| Alan | Bilgi |", "|---|---|"]
    s += [f"| {k} | {str(v).replace('|', '/')} |" for k, v in b["kimlik"].items() if v]
    sorumlu = [v for v in b["vergi_turleri"] if v["donem"] and v["donem"] != SORUMLU_DEGIL]
    degil = [v["vergi"] for v in b["vergi_turleri"] if not v["donem"] or v["donem"] == SORUMLU_DEGIL]
    s += ["", "## Vergi türleri (sorumlu olunanlar)", "", "| Vergi | Dönem |", "|---|---|"]
    s += [f"| {v['vergi']} | {v['donem']} |" for v in sorumlu] or ["| — | — |"]
    if degil:
        s += ["", "Sorumlu olunmayanlar: " + ", ".join(degil)]
    s += ["", "## E-Defter / E-Envanter", ""] + [f"- {k}: {v or '—'}" for k, v in b["e_defter"].items()]
    s += ["", "## NACE kodları", ""] + ([f"- {n}" for n in b["nace"]] or ["—"])
    s += ["", "## Yöneticiler", ""] + _tablo(b["yoneticiler"])
    s += ["", "## Ortaklar", ""] + _tablo(b["ortaklar"])
    if b.get("iletisim"):
        s += ["", "## İletişim (E-Mükellef kaydı)", "", "| Tür | Bilgi | Kişi |", "|---|---|---|"]
        s += [f"| {x['tur']} | {x['deger']} | {x['kisi']} |" for x in b["iletisim"]]
    if b.get("gib_sorgusu"):
        s += ["", "## GİB sorgusunun sonucu", ""]
        s += [f"- {'✔' if x['durum'] == 'tamam' else ('⚠' if x['durum'] == 'uyari' else '✖')} {x['baslik']}: {x['mesaj']}" for x in b["gib_sorgusu"]]
    return "\n".join(s) + "\n"


def _atomik_yaz(yol, metin):
    gecici = yol.with_name(yol.name + ".yaziliyor")
    gecici.write_text(metin, encoding="utf-8")
    os.replace(gecici, yol)


def kaydet(firma_klasoru, b, firma_adi=""):
    """Bilgileri firma klasörüne yazar -> (md yolu, değişenler). 'Genel' alt klasörü yoksa açılır (firma klasörü açılmaz)."""
    k = klasor(firma_klasoru)
    k.mkdir(exist_ok=True)
    b["degisenler"] = _degisenler(oku(firma_klasoru), b)
    _atomik_yaz(k / MD_ADI, markdown(b, firma_adi))     # önce okunacak dosya; tarihi taşıyan JSON en son (yarım kalırsa yeniden denenir)
    _atomik_yaz(k / JSON_ADI, json.dumps(b, ensure_ascii=False, indent=1))
    return k / MD_ADI, b["degisenler"]


def ozet_satirlari(b):
    """Pano çekmecesi için kısa satırlar."""
    if not b:
        return []
    sorumlu = [f"{v['vergi']} {v['donem'].replace(' Aylık', ' ay').replace('Aylık', 'aylık')}" if v["donem"] not in ("Sorumlu",) else v["vergi"]
               for v in b.get("vergi_turleri") or [] if v["donem"] and v["donem"] != SORUMLU_DEGIL]
    s = []
    k = b.get("kimlik") or {}
    if k.get("Vergi dairesi"):
        s.append(("Vergi dairesi", k["Vergi dairesi"].replace(" VERGİ DAİRESİ MÜDÜRLÜĞÜ", " VD").replace(" Vergi Dairesi Müdürlüğü", " VD")))
    if sorumlu:
        s.append(("Beyanlar", ", ".join(sorumlu)))
    e = b.get("e_defter") or {}
    s.append(("E-defter", (e.get("E-Defter") or "—") + (f" · E-envanter: {e['E-Envanter']}" if e.get("E-Envanter") and e["E-Envanter"] != SORUMLU_DEGIL else "")))
    if b.get("nace"):
        s.append(("NACE", b["nace"][0][:70] + (f" (+{len(b['nace']) - 1})" if len(b["nace"]) > 1 else "")))
    y = [str(x.get("Unvan / Ad Soyad") or "") for x in b.get("yoneticiler") or [] if not x.get("Çıkış Tarihi") or x.get("Çıkış Tarihi") in ("—", "-", "")]
    if y:
        s.append(("Yönetici", ", ".join(n.title() for n in y if n)))
    o = [str(x.get("Unvan / Ad Soyad") or next(iter(x.values()), "")) for x in b.get("ortaklar") or []]
    if o:
        s.append(("Ortak", ", ".join(n.title() for n in o if n)))
    if k.get("Açılış tarihi"):
        s.append(("Açılış", k["Açılış tarihi"]))
    return s
