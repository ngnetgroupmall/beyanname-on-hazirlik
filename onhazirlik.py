"""Beyanname Ön Hazırlık — E-Mükellef ve Luca'dan dönem verilerini indirir.

Çalıştırma: "Beyanname On Hazirlik.bat" (veya: python -X utf8 onhazirlik.py). Başka bilgisayar için taşınabilir paket: paketle.py
(paket baslat.pyw ile açılır; kayıt klasörü ve firma eşlemesinin yeri için bkz. ayar.py).
Tarayıcı arka planda (penceresiz) çalışır. Giriş gerekirse program kendi giriş penceresini açar: e-posta + şifre
(istenirse bu bilgisayarda Windows hesabına bağlı şifreli saklanır, bkz. giris_kasasi.py) + güvenlik kodu (captcha;
program çözmez, kullanıcı yazar) + E-Mükellef isterse doğrulama kodu. "Tarayıcıyı göster" elle giriş/sorun giderme yedeğidir.
Not: Chrome profili (%LOCALAPPDATA%\\KdvOnHazirlik\\chrome-profil) oturum çerezlerini saklar.
Tam firma listesi E-Mükellef'ten giriş kontrolünde alınır ve %LOCALAPPDATA%\\KdvOnHazirlik\\mukellefler.json'da tutulur.
Firma -> klasör eşlemesi firmalar.json'dadır; tanımsız firmada Başlat'ta sorulur.
E-Mükellef adresi ofise özeldir; ilk açılışta sorulur (ayar.py). İndirilecekler seçimi "Bu seçimi kaydet" ile ayarlara yazılır ve
sonraki açılışta öyle gelir. Taşınabilir paket GitHub'dan kendini günceller (guncelle.py, baslat.pyw).
Luca: Luca sorgusu seçiliyse Başlat'ta Luca girişi istenir (üye no + kullanıcı adı + parola; captcha yok, bkz. luca_kasasi.py);
firmanın Luca'daki karşılığı (luca_sirket_id) tanımlı değilse Luca firma listesinden seçtirilir ve firmalar.json'a yazılır.
"""
import base64
import json
import msvcrt
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

KLASOR = Path(__file__).resolve().parent
sys.path.insert(0, str(KLASOR))

import ayar  # noqa: E402
import birikim  # noqa: E402
import cikti  # noqa: E402
import emukellef  # noqa: E402
import giris_kasasi  # noqa: E402
import guncelle  # noqa: E402
import luca_kasasi  # noqa: E402
from tarayici import FATURA_SORGULARI, LUCA_SORGULARI, SORGULAR, Isci, acil_metni, kisa_sonuc  # noqa: E402

PROGRAM_ADI = "Beyanname Ön Hazırlık"
VERI = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "KdvOnHazirlik"
GUNLUK = VERI / "gunluk.txt"
MUKELLEF_ONBELLEK = VERI / "mukellefler.json"
LUCA_FIRMA_ONBELLEK = VERI / "luca_firmalar.json"
AYARLAR = VERI / "ayarlar.json"
KOMUT_KLASORU = VERI / "komut"               # komut.py buraya istek bırakır (Claude eksik veriyi programdan çalıştırır)
KOMUT_ISTEK, KOMUT_DURUM = KOMUT_KLASORU / "istek.json", KOMUT_KLASORU / "durum.json"
FIRMALAR_JSON = ayar.firmalar_json()         # program klasöründe varsa o; yoksa (taşınabilir paket) kullanıcı verisinde
HUKUKI_EK = {"ltd", "ltd.", "limited", "şti", "şti.", "sti", "a.ş", "a.ş.", "a.", "ş.", "anonim", "şirketi", "san", "san.",
             "sanayi", "tic", "tic.", "ticaret", "ve", "dış", "iç", "ithalat", "ihracat", "inşaat", "pazarlama", "ins."}

# ---------------------------------------------------------------- görünüm
YAZI = "Segoe UI"
R = {"lacivert": "#1f3a5f", "zemin": "#f1f3f6", "kart": "#ffffff", "cizgi": "#d9dde3", "gri": "#6b7785", "acik_gri": "#8a94a3",
     "yazi": "#1f2933", "yesil": "#1a7f37", "yesil_z": "#e8f5ec", "kirmizi": "#b42318", "kirmizi_z": "#fde8e6",
     "mavi": "#1f5fbf", "mavi_z": "#e3edfb", "sari": "#b36b00", "sari_z": "#fff4de", "yok": "#55606e", "yok_z": "#eef0f3",
     "dugme_z": "#f6f8fb", "dugme_k": "#c8d0db", "secili_z": "#dbe7f6"}

# (grup adı, sorgu anahtarları) — sıra ekrandaki sıradır; SOL_GRUPLAR sol sütuna girer
GRUPLAR = [
    ("Beyannameler", ["beyan", "muhsgk", "sgk6661"]),
    ("Faturalar", ["efatura_gelen", "efatura_giden", "earsiv", "earsiv_giden", "adina", "iptal_itiraz"]),
    ("Ödemeler ve borç", ["kdv2", "s109", "alindi", "tahsilat", "borc"]),
    ("Satış kanalları", ["okc", "pos", "todeb"]),
    ("İthalat ve araç", ["ithalat", "arac", "arac_gecmis", "arac_alim"]),
    ("GİB yazışmaları", ["evrak", "bildirim", "karsit", "etebligat"]),
    ("Luca", ["luca_kebir", "luca_mizan_ay", "luca_mizan_yil", "luca_hesap_plani", "luca_ae_alis", "luca_ae_satis", "luca_bordro", "luca_utts"]),
]
SOL_GRUPLAR = (0, 1, 3, 4)                    # dar ekranda iki sütun — sol: 15 sorgu; sağ (ödemeler, GİB yazışmaları, Luca): 15 sorgu
UC_SUTUN = ((0, 1), (2, 3, 4), (5, 6))        # geniş ekranda üç sütun (alt panel/sonuç kutuları için yer kalsın)
GENIS_EKRAN = 1500                            # ekran bu genişlikteyse pencere genişler ve sorgular üç sütuna dizilir
_gruplu = {k for _, ks in GRUPLAR for k in ks}
_diger = [k for k, _ in SORGULAR if k not in _gruplu]
if _diger:                                   # yeni eklenen sorgu gruba konmadıysa kaybolmasın
    GRUPLAR.append(("Diğer", _diger))
KISA_AD = {"beyan": "Önceki dönem KDV1 / KDV2", "muhsgk": "MUHSGK", "sgk6661": "SGK 6661 asgari ücret desteği", "efatura_gelen": "Gelen e-fatura", "efatura_giden": "Giden e-fatura",
           "earsiv": "Gelen e-arşiv", "earsiv_giden": "Giden e-arşiv", "adina": "Adına düzenlenenler", "iptal_itiraz": "E-arşiv iptal / itiraz",
           "kdv2": "KDV2 ödemesi (4017)", "s109": "109 sorumlu sıfatıyla KDV", "alindi": "Ödeme / alındı", "tahsilat": "GİB tahsilat bilgileri",
           "borc": "Vergi borcu", "okc": "ÖKC (Z raporu)", "pos": "POS tahsilatı", "todeb": "TÖDEB", "ithalat": "İthalde ödenen KDV",
           "arac": "Araçlar (mevcut)", "arac_gecmis": "Araçlar (geçmiş)", "arac_alim": "Araç alım / satım", "evrak": "Gelen evraklar",
           "bildirim": "Bildirimlerim", "karsit": "Karşıt inceleme tutanakları", "etebligat": "E-tebligatlar",
           "luca_kebir": "Defteri Kebir", "luca_mizan_ay": "Mizan (seçilen ay)", "luca_mizan_yil": "Mizan (01.01 – ay sonu)",
           "luca_hesap_plani": "Hesap planı",
           "luca_ae_alis": "Akıllı Ent. alış belgeleri", "luca_ae_satis": "Akıllı Ent. satış belgeleri", "luca_bordro": "Bordro dökümü",
           "luca_utts": "UTTS akaryakıt (GİB'den getir)"}
HAZIR = [("Tümü", [k for k, _ in SORGULAR]), ("Yalnız faturalar", GRUPLAR[1][1]),
         ("Beyanname + ödemeler", GRUPLAR[0][1] + GRUPLAR[2][1]), ("Yalnız GİB yazışmaları", GRUPLAR[5][1]),
         ("Yalnız Luca", GRUPLAR[6][1]), ("Luca hariç", [k for k, _ in SORGULAR if k not in LUCA_SORGULARI]), ("Hiçbiri", [])]
# durum -> (yazı rengi, zemin, sembol)
DURUM_STIL = {"bekliyor": (R["acik_gri"], R["kart"], "○"), "calisiyor": (R["mavi"], R["mavi_z"], "●"),
              "veri": (R["yesil"], R["yesil_z"], "✓"), "kayit_yok": (R["yok"], R["yok_z"], "✓"),
              "hata": (R["kirmizi"], R["kirmizi_z"], "✕"), "calistirilmadi": (R["acik_gri"], R["yok_z"], "–"),
              "acil": ("#ffffff", R["kirmizi"], "!")}
BITEN = ("veri", "kayit_yok", "hata", "calistirilmadi")
# önceki indirmeden gelen sonuç (zeminsiz, eğik): durum -> (yazı rengi, sembol)
ONCEKI_STIL = {"veri": (R["yesil"], "✓"), "kayit_yok": (R["yok"], "✓"), "hata": (R["kirmizi"], "✕"), "calistirilmadi": (R["acik_gri"], "–")}
OTURUM_SAAT = 8          # programda son işlemden sonra arka plan giriş denetiminin (oturumu açık tutan) süreceği saat
# Luca girişi programın içinden yapılır mı? Hayır (kullanıcı kararı 19.11.2026): Luca'nın geçiş sayfası gizli tarayıcıda sunucu hatası
# veriyor (LUCA_NOTLARI.md), bu yüzden Luca'ya görünür tarayıcı penceresinden elle girilir. True yapılırsa program içi giriş formu döner.
LUCA_PROGRAM_ICI_GIRIS = False

# Önceki indirme notu (kullanıcı isteği 20.11.2026): seçilen firma + dönemde daha önce indirilmiş veri ekranda not olarak ve sorgu
# kutularının yanında (tarihli, eğik yazıyla) görünür. Veri dönem klasöründeki sonuç dosyalarından arka planda okunur; sade özet,
# sonuç dosyaları değişmedikçe önbellekten gelir. Aynı anda tek okuma yapılır (_ONCEKI_KILIT).
_ONCEKI_ONBELLEK = {}
_ONCEKI_KILIT = threading.Lock()


def _onceki_sadele(birlesik):
    ad_anahtar = {ad: a for a, ad in SORGULAR}
    sonuclar, fatura = {}, 0
    for s in birlesik:
        a = ad_anahtar.get(s.get("sorgu"))
        if not a:
            continue                                # programdan kaldırılmış / adı değişmiş sorgu
        z = str(s.get("sorgu_zamani") or "")
        sonuclar[a] = {"durum": s["durum"], "kisa": kisa_sonuc(a, s), "mesaj": str(s.get("mesaj") or ""), "kapsam": str(s.get("kapsam") or ""),
                       "zaman": z, "gun": z[:5] if len(z) >= 5 else "?", "acil": s["durum"] == "veri" and bool(acil_metni(s)),
                       "son_deneme": s.get("son_deneme")}
        if a in FATURA_SORGULARI and s["durum"] == "veri":
            fatura += len(s.get("satirlar") or [])
    return {"sonuclar": sonuclar, "fatura": fatura}


def onceki_durumu(klasor, ay, yil):
    """Seçilen firma/dönemin önceki indirmeleri (arayüz için sade): sonuclar = sorgu anahtarı -> {durum, kisa, mesaj, kapsam, zaman,
    gun, acil, son_deneme}; kayit (okunabilen sonuç dosyası sayısı), son (en son kayıtlı sorgu zamanı), fatura, ozet, donem_klasoru;
    dosya (dönem klasöründeki dosya sayısı; yalnız alınmış sonuç yoksa sayılır)."""
    d = birikim.donem_dosyalari(klasor, yil, ay)
    v = {"klasor_var": d["klasor_var"], "donem_var": d["donem_var"], "donem_klasoru": d["donem_klasoru"], "ozet": d["ozet"],
         "beyanname": d["beyanname"], "kayit": 0, "son": "", "sonuclar": {}, "fatura": 0, "dosya": 0}
    if not d["donem_var"]:
        return v
    if d["jsonlar"]:
        anahtar = d["donem_klasoru"].casefold()
        k = _ONCEKI_ONBELLEK.get(anahtar)
        if d["imza"] is not None and k is not None and k[0] == d["imza"]:
            ozet = k[1]
        else:
            birlesik, son, kayit = birikim.donem_sonuclari(klasor, yil, ay, [ad for _, ad in SORGULAR])
            ozet = _onceki_sadele(birlesik) | {"son": son, "kayit": kayit}
            if d["imza"] is not None:
                _ONCEKI_ONBELLEK.pop(anahtar, None)
                _ONCEKI_ONBELLEK[anahtar] = (d["imza"], ozet)
                while len(_ONCEKI_ONBELLEK) > 40:   # en eski kayıt atılır
                    _ONCEKI_ONBELLEK.pop(next(iter(_ONCEKI_ONBELLEK)))
        v.update(ozet)
    if not any(o["durum"] in ("veri", "kayit_yok", "hata") for o in v["sonuclar"].values()):
        v["dosya"] = birikim.dosya_sayisi(d["donem_klasoru"])     # alınmış sonuç yoksa: klasörde elle konmuş dosya var mı
    return v


# ================================================================ yardımcılar
def tek_kopya_kilidi():
    """Programın ikinci kopyasını engeller (aynı Chrome profili iki kez açılamaz)."""
    VERI.mkdir(parents=True, exist_ok=True)
    f = open(VERI / "program.lock", "a+")
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return f


def norm(s):
    """Türkçe harf ve büyük/küçük harf duyarsız arama anahtarı."""
    s = str(s or "").replace("İ", "i").replace("I", "ı").lower()
    return s.translate(str.maketrans("çğıöşüâîû", "cgiosuaiu"))


def json_oku(yol, varsayilan):
    try:
        return json.loads(Path(yol).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return varsayilan


def eslesme_oku():
    """Firma eşlemesi (firmalar.json) -> (veri, hata). Dosya yoksa boş eşleme. Dosya VAR ama okunamıyorsa (bozuk) hata metni döner:
    çağıran taraf böyle bir dosyanın üzerine YAZMAZ (eşlemeler kaybolmasın)."""
    yol = Path(FIRMALAR_JSON)
    if not yol.exists():
        return {"firmalar": []}, None
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
        if not isinstance(veri, dict) or not isinstance(veri.get("firmalar", []), list):
            raise ValueError("beklenen yapı yok")
        temiz = []                                  # yalnız geçerli kayıtlar: sözlük + tam sayı emukellef_id (çöp satır patlatmasın)
        for x in veri.get("firmalar", []):
            if not isinstance(x, dict):
                continue
            try:
                if x.get("emukellef_id") is not None:
                    int(x["emukellef_id"])
            except (TypeError, ValueError):
                continue
            temiz.append(x)
        veri["firmalar"] = temiz
        return veri, None
    except (OSError, ValueError) as e:
        return {"firmalar": []}, f"Firma eşleme dosyası okunamıyor ({yol}): {type(e).__name__}. Dosya düzeltilmeden yeni eşleme kaydedilmez."


def json_yaz(yol, veri):
    yol = Path(yol)
    yol.parent.mkdir(parents=True, exist_ok=True)
    gecici = yol.with_name(yol.name + ".tmp")
    gecici.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(gecici, yol)          # kendi ayar dosyamız: güvenli değiştirme


def unvan_sade(unvan):
    return re.sub(r"^z+\.\s*", "", str(unvan or ""), flags=re.I).strip()


def kisa_ad_oner(unvan):
    u = unvan_sade(unvan)
    kelimeler = []
    for k in u.split():
        if norm(k) in {norm(x) for x in HUKUKI_EK}:
            break
        kelimeler.append(k)
        if len(kelimeler) == 3:
            break
    return " ".join(kelimeler) or u[:30]


def klasor_oner(unvan, klasorler):
    u = norm(unvan_sade(unvan))
    bas = [k for k in klasorler if u.startswith(norm(k))]
    if bas:
        return max(bas, key=len)
    ic = [k for k in klasorler if re.search(r"\b" + re.escape(norm(k)) + r"\b", u)]
    return max(ic, key=len) if ic else ""


def mevcut_klasorler():
    try:
        return sorted((p.name for p in cikti.FIRMALAR.iterdir() if p.is_dir() and not p.name.startswith("_")), key=norm)
    except OSError:
        return []


def eposta_maske(e):
    ad, _, alan = str(e or "").partition("@")
    return (ad[:2] + "•••@" + alan) if alan else (ad[:2] + "•••")


def kart(ust, ic_pay=(12, 9)):
    """Beyaz, ince çerçeveli kutu -> (dış çerçeve, içerik çerçevesi)."""
    dis = tk.Frame(ust, bg=R["kart"], highlightbackground=R["cizgi"], highlightcolor=R["cizgi"], highlightthickness=1)
    ic = tk.Frame(dis, bg=R["kart"])
    ic.pack(fill="both", expand=True, padx=ic_pay[0], pady=ic_pay[1])
    return dis, ic


def baslik(ust, metin, no=None):
    f = tk.Frame(ust, bg=R["kart"])
    if no is not None:
        tk.Label(f, text=str(no), bg=R["lacivert"], fg="white", font=(YAZI, 8, "bold"), width=2).pack(side="left", padx=(0, 7))
    tk.Label(f, text=metin, bg=R["kart"], fg=R["lacivert"], font=(YAZI, 9, "bold")).pack(side="left")
    return f


def dugme(ust, metin, komut, ana=False, kucuk=False, zemin=None):
    """Düz, ince kenarlı düğme. Dönen çerçevenin .d özelliği asıl tk.Button'dır."""
    kenar = R["lacivert"] if (ana or not kucuk) else R["dugme_k"]
    c = tk.Frame(ust, bg=kenar)
    b = tk.Button(c, text=metin, command=komut, relief="flat", bd=0, cursor="hand2",
                  bg=R["lacivert"] if ana else (zemin or (R["dugme_z"] if kucuk else R["kart"])),
                  fg="white" if ana else R["lacivert"], activebackground=R["secili_z"], activeforeground=R["lacivert"],
                  disabledforeground=R["acik_gri"], font=(YAZI, 9, "bold" if (ana or not kucuk) else "normal"),
                  padx=10 if not kucuk else 8, pady=4 if not kucuk else 1)
    b.pack(padx=1, pady=1)
    c.d = b
    return c


# ================================================================ klasör eşleme penceresi
SECIM_ANAHTARI = "indirilecekler"


def secim_kaydi_oku(ayarlar):
    """Ayarlardaki kayıtlı "İndirilecekler" seçimi -> {secili, bilinen, earsiv_gib, pdf, tarih} ya da kayıt yoksa/bozuksa None."""
    k = ayarlar.get(SECIM_ANAHTARI) if isinstance(ayarlar, dict) else None
    if not isinstance(k, dict) or not isinstance(k.get("secili"), list) or not isinstance(k.get("bilinen"), list):
        return None
    gecerli = {a for a, _ in SORGULAR}
    return {"secili": [a for a in k["secili"] if a in gecerli], "bilinen": [a for a in k["bilinen"] if a in gecerli],
            "earsiv_gib": bool(k.get("earsiv_gib", True)), "pdf": bool(k.get("pdf", False)), "tarih": str(k.get("tarih") or "")[:10]}


def secim_baslangici(kayit):
    """Sorgu anahtarı -> açılışta işaretli mi. Kayıt varsa o; kayıtta bilinmeyen (sonradan eklenen) sorgu ve kayıt yokken varsayılan:
    "evrak" (GİB Gelen Evraklar) dışında hepsi işaretli ("evrak" şimdilik düzgün çalışmıyor; kullanıcı kararı 19.11.2026)."""
    return {a: ((a in kayit["secili"]) if kayit and a in kayit["bilinen"] else a != "evrak") for a, _ in SORGULAR}


def adres_sor(ust, mevcut=None):
    """E-Mükellef adresini sorar -> 'https://<sunucu>' ya da vazgeçilirse None. ust: Tk kökü (gizli olabilir)."""
    p = tk.Toplevel(ust)
    p.title("E-Mükellef adresi")
    p.resizable(False, False)
    p.configure(bg=R["kart"])
    sonuc = []
    ana = tk.Frame(p, bg=R["kart"], padx=18, pady=14)
    ana.pack(fill="both", expand=True)
    tk.Label(ana, text="E-Mükellef adresiniz", bg=R["kart"], fg=R["lacivert"], font=(YAZI, 13, "bold")).pack(anchor="w")
    tk.Label(ana, text="Ofisinizin E-Mükellef'e girdiği adresi yazın (tarayıcının adres çubuğundaki adres).\n"
                       "Örnek:  ofisadi.emukellef.org   — yalnız 'ofisadi' yazmanız da yeterlidir.",
             bg=R["kart"], fg=R["gri"], font=(YAZI, 9), justify="left").pack(anchor="w", pady=(2, 10))
    giris = ttk.Entry(ana, width=46, font=(YAZI, 11))
    giris.insert(0, (mevcut or "").replace("https://", ""))
    giris.pack(anchor="w")
    mesaj = tk.Label(ana, text="", bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 9))
    mesaj.pack(anchor="w", pady=(6, 0))

    def tamam(*_):
        adres = ayar.adres_duzelt(giris.get())
        if not adres:
            mesaj.configure(text="Adres anlaşılamadı. Örnek: ofisadi.emukellef.org")
            return
        sonuc.append(adres)
        p.destroy()
    alt = tk.Frame(ana, bg=R["kart"])
    alt.pack(fill="x", pady=(10, 0))
    dugme(alt, "Kaydet", tamam, ana=True).pack(side="left")
    dugme(alt, "Vazgeç", p.destroy).pack(side="left", padx=(8, 0))
    giris.bind("<Return>", tamam)
    p.bind("<Escape>", lambda e: p.destroy())
    p.update_idletasks()
    p.geometry(f"+{max(0, (p.winfo_screenwidth() - p.winfo_reqwidth()) // 2)}+{max(0, (p.winfo_screenheight() - p.winfo_reqheight()) // 3)}")
    p.lift()
    p.attributes("-topmost", True)
    p.after(300, lambda: p.attributes("-topmost", False))
    giris.focus_force()
    p.grab_set()
    ust.wait_window(p)
    return sonuc[0] if sonuc else None


class KlasorPenceresi(tk.Toplevel):
    def __init__(self, ust, kayit):
        super().__init__(ust)
        self.title("Firma klasörü")
        self.resizable(False, False)
        self.sonuc = None
        self.kayit = kayit
        klasorler = mevcut_klasorler()
        cerceve = ttk.Frame(self, padding=12)
        cerceve.pack(fill="both", expand=True)
        ttk.Label(cerceve, text=kayit["unvan"], font=(YAZI, 10, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(cerceve, text=f"E-Mükellef no {kayit['id']}   VKN {kayit.get('vkn') or '—'}", foreground="#555").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(cerceve, text="Dosya adlarında kısa ad:").grid(row=2, column=0, sticky="w")
        self.ad = ttk.Entry(cerceve, width=40)
        self.ad.insert(0, kayit.get("ad") or kisa_ad_oner(kayit["unvan"]))
        self.ad.grid(row=2, column=1, sticky="w", pady=2)
        ttk.Label(cerceve, text="Firmalar altındaki klasör:").grid(row=3, column=0, sticky="w")
        self.klasor = ttk.Combobox(cerceve, width=38, values=klasorler)
        self.klasor.set(klasor_oner(kayit["unvan"], klasorler) or kayit.get("klasor_oneri") or kisa_ad_oner(kayit["unvan"]))
        self.klasor.grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(cerceve, text="Listeden mevcut klasörü seçin; yoksa yeni ad yazın (onayınızla açılır).",
                  foreground="#777").grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 8))
        d = ttk.Frame(cerceve)
        d.grid(row=5, column=0, columnspan=2, sticky="e")
        ttk.Button(d, text="Kaydet", command=self._kaydet).pack(side="left", padx=4)
        ttk.Button(d, text="Vazgeç", command=self.destroy).pack(side="left")
        self.transient(ust)
        self.grab_set()
        self.ad.focus_set()

    def _kaydet(self):
        ad, klasor = self.ad.get().strip(), self.klasor.get().strip()
        if not ad or not klasor or any(c in ad + klasor for c in '\\/:*?"<>|'):
            messagebox.showwarning("Geçersiz ad", "Kısa ad ve klasör adı boş olamaz ve \\ / : * ? \" < > | içeremez.", parent=self)
            return
        yol = cikti.FIRMALAR / klasor
        if not yol.is_dir():
            if not messagebox.askyesno("Yeni firma klasörü", f"{yol}\nklasörü yok. Açılsın mı?", parent=self):
                return
            try:
                if not Path(cikti.FIRMALAR).is_dir():       # kök yoksa kendiliğinden açılmaz (yanlış yere klasör kurulmasın)
                    raise OSError("kayıt klasörüne erişilemiyor")
                yol.mkdir()
            except OSError as e:
                messagebox.showerror("Klasör açılamadı", f"{yol}\n\n{e}", parent=self)
                return
        self.sonuc = {"ad": ad, "klasor": klasor}
        self.destroy()


# ================================================================ giriş penceresi
class LucaFirmaPenceresi(tk.Toplevel):
    """Firmanın Luca'daki karşılığını seçtirir (Luca firma listesi; yazarak aranır). sonuc: sirket_id ya da None."""

    def __init__(self, ust, kayit, luca_firmalar):
        super().__init__(ust)
        self.title("Luca firması")
        self.resizable(False, False)
        self.configure(bg=R["kart"])
        self.sonuc = None
        self.tum = [(str(i), str(a)) for i, a in luca_firmalar]
        ana = tk.Frame(self, bg=R["kart"], padx=16, pady=14)
        ana.pack(fill="both", expand=True)
        tk.Label(ana, text=unvan_sade(kayit["unvan"]), bg=R["kart"], fg=R["yazi"], font=(YAZI, 10, "bold"), wraplength=440,
                 justify="left").pack(anchor="w")
        tk.Label(ana, text="Bu firmanın Luca'daki karşılığını seçin (bir kez sorulur, firmalar.json'a kaydedilir).", bg=R["kart"],
                 fg=R["gri"], font=(YAZI, 9), wraplength=440, justify="left").pack(anchor="w", pady=(0, 8))
        self.ara = ttk.Entry(ana, width=58)
        self.ara.pack(anchor="w")
        lc = tk.Frame(ana, bg=R["kart"])
        lc.pack(fill="x", pady=(6, 0))
        self.liste = tk.Listbox(lc, height=12, width=58, exportselection=False, activestyle="dotbox", font=(YAZI, 9))
        kay = ttk.Scrollbar(lc, orient="vertical", command=self.liste.yview)
        self.liste.config(yscrollcommand=kay.set)
        self.liste.pack(side="left")
        kay.pack(side="left", fill="y")
        self.bilgi = tk.Label(ana, text="", bg=R["kart"], fg=R["acik_gri"], font=(YAZI, 8))
        self.bilgi.pack(anchor="w", pady=(4, 0))
        alt = tk.Frame(ana, bg=R["kart"])
        alt.pack(fill="x", pady=(10, 0))
        dugme(alt, "Seç", self._sec, ana=True).pack(side="left", padx=(0, 6))
        dugme(alt, "Vazgeç", self.destroy).pack(side="left")
        self.ara.bind("<KeyRelease>", self._suz)
        self.ara.bind("<Return>", lambda e: self._sec())
        self.ara.bind("<Down>", lambda e: (self.liste.focus_set(), self.liste.selection_set(0)) if self.gorunen else None)
        self.liste.bind("<Double-Button-1>", lambda e: self._sec())
        self.liste.bind("<Return>", lambda e: self._sec())
        self.bind("<Escape>", lambda e: self.destroy())
        oneri = (kayit.get("ad") or kisa_ad_oner(kayit["unvan"]) or "").split()
        self.ara.insert(0, oneri[0] if oneri else "")
        self.gorunen = []
        self._suz()
        if not self.gorunen:                        # öneri tutmadıysa tam listeyi göster
            self.ara.delete(0, "end")
            self._suz()
        self.transient(ust)
        self.update_idletasks()
        self.geometry(f"+{ust.winfo_rootx() + 380}+{ust.winfo_rooty() + 120}")
        self.grab_set()
        self.ara.focus_set()
        self.ara.select_range(0, "end")

    def _suz(self, event=None):
        if event is not None and event.keysym in ("Down", "Up", "Return"):
            return
        parcalar = norm(self.ara.get().strip()).split()
        self.gorunen = [x for x in self.tum if all(pc in norm(x[1]) for pc in parcalar)]
        self.liste.delete(0, "end")
        for _, ad in self.gorunen:
            self.liste.insert("end", ad)
        if len(self.gorunen) == 1:
            self.liste.selection_set(0)
        self.bilgi.configure(text=f"{len(self.gorunen)} / {len(self.tum)} Luca firması")

    def _sec(self):
        s = self.liste.curselection()
        if not s and len(self.gorunen) == 1:
            s = (0,)
        if not s:
            self.bilgi.configure(text="Listeden bir firma seçin.", fg=R["kirmizi"])
            return
        self.sonuc = self.gorunen[s[0]]
        self.destroy()


class SikFirmaPenceresi(tk.Toplevel):
    """Sık kullanılan firmaları seçer: solda arama + tüm firmalar, sağda seçilenler. sonuc: id listesi (sırayla) ya da None."""

    def __init__(self, ust, tum, idler):
        super().__init__(ust)
        self.tum = tum
        self.secilen = [i for i in idler if any(t["id"] == i for t in tum)]
        self.sonuc = None
        self.title("Sık kullanılan firmalar")
        self.configure(bg=R["kart"])
        ana = tk.Frame(self, bg=R["kart"], padx=16, pady=12)
        ana.pack(fill="both", expand=True)
        tk.Label(ana, text="Sık kullanılan firmalar", bg=R["kart"], fg=R["lacivert"], font=(YAZI, 13, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Label(ana, text="Toplu sorguda bu firmalar sırayla sorgulanır. Çift tıklayarak ekleyip çıkarabilirsiniz.", bg=R["kart"], fg=R["gri"],
                 font=(YAZI, 9)).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.ara = ttk.Entry(ana)
        self.ara.grid(row=2, column=0, sticky="we")
        self.ara.bind("<KeyRelease>", lambda e: self._ciz())
        self.sayi = tk.Label(ana, text="", bg=R["kart"], fg=R["gri"], font=(YAZI, 9), anchor="w")
        self.sayi.grid(row=2, column=2, sticky="w")
        self.sol = tk.Listbox(ana, width=52, height=18, selectmode="extended", exportselection=False, font=(YAZI, 9))
        self.sag = tk.Listbox(ana, width=44, height=18, selectmode="extended", exportselection=False, font=(YAZI, 9))
        self.sol.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        self.sag.grid(row=3, column=2, sticky="nsew", pady=(4, 0))
        orta = tk.Frame(ana, bg=R["kart"])
        orta.grid(row=3, column=1, padx=8)
        dugme(orta, "Ekle →", self._ekle, kucuk=True).pack(pady=(0, 6))
        dugme(orta, "← Çıkar", self._cikar, kucuk=True).pack()
        self.sol.bind("<Double-Button-1>", lambda e: self._ekle())
        self.sol.bind("<Return>", lambda e: self._ekle())
        self.sag.bind("<Double-Button-1>", lambda e: self._cikar())
        self.sag.bind("<Delete>", lambda e: self._cikar())
        alt = tk.Frame(ana, bg=R["kart"])
        alt.grid(row=4, column=0, columnspan=3, sticky="e", pady=(10, 0))
        dugme(alt, "Kaydet", self._kaydet, ana=True).pack(side="right")
        dugme(alt, "Vazgeç", self.destroy, kucuk=True).pack(side="right", padx=(0, 8))
        ana.grid_columnconfigure(0, weight=1)
        ana.grid_columnconfigure(2, weight=1)
        ana.grid_rowconfigure(3, weight=1)
        self.transient(ust)
        self.bind("<Escape>", lambda e: self.destroy())
        self._ciz()
        self.update_idletasks()
        self.geometry(f"+{ust.winfo_rootx() + 40}+{ust.winfo_rooty() + 40}")
        self.grab_set()
        self.ara.focus_set()

    def _ciz(self):
        parcalar = norm(self.ara.get().strip()).split()
        self.gorunen = [t for t in self.tum if t["id"] not in self.secilen and all(p in t["anahtar"] for p in parcalar)]
        self.sol.delete(0, "end")
        for t in self.gorunen:
            self.sol.insert("end", t["goster"])
        self.sag.delete(0, "end")
        for i in self.secilen:
            t = next(t for t in self.tum if t["id"] == i)
            self.sag.insert("end", unvan_sade(t["unvan"]) + ("" if t.get("klasor") else "   — klasör tanımsız"))
        self.sayi.configure(text=f"Seçilen: {len(self.secilen)} firma")

    def _ekle(self):
        for n in self.sol.curselection():
            if self.gorunen[n]["id"] not in self.secilen:
                self.secilen.append(self.gorunen[n]["id"])
        self._ciz()

    def _cikar(self):
        cik = {self.secilen[n] for n in self.sag.curselection()}
        self.secilen = [i for i in self.secilen if i not in cik]
        self._ciz()

    def _kaydet(self):
        self.sonuc = list(self.secilen)
        self.destroy()


class AktarPenceresi(tk.Toplevel):
    """'GİB'den aktar' onayı: arama aralığı (beyannamenin GİB'e yüklendiği tarih) ve seçenekler. sonuc: işçi görevinin parametreleri ya da None."""

    def __init__(self, ust):
        super().__init__(ust)
        self.sonuc = None
        self.title("GİB'den E-Mükellef'e aktar")
        self.resizable(False, False)
        self.configure(bg=R["kart"])
        ana = tk.Frame(self, bg=R["kart"], padx=18, pady=14)
        ana.pack(fill="both", expand=True)
        tk.Label(ana, text="GİB'den E-Mükellef'e aktar", bg=R["kart"], fg=R["lacivert"], font=(YAZI, 13, "bold")).pack(anchor="w")
        tk.Label(ana, text="E-Mükellef masaüstü programı sürülerek, seçilen tarih aralığında GİB'e verilmiş beyanname ve tahakkuklar "
                           "E-Mükellef sitesine aktarılır.\n\n"
                           "• TÜM firmalar kapsanır; yalnız siteye daha önce yüklenmemiş evraklar aktarılır (program kendisi işaretler).\n"
                           "• Mükelleflere mail / SMS / bildirim GÖNDERİLMEZ.\n"
                           "• E-Mükellef masaüstü programı açık olmalı; işlem sürerken programa dokunmayın, GİB'e bağlanan başka bir "
                           "program açmayın. Yükleme başladıktan sonra durdurulamaz.",
                 bg=R["kart"], fg=R["yazi"], font=(YAZI, 9), wraplength=440, justify="left").pack(anchor="w", pady=(4, 10))
        g = tk.Frame(ana, bg=R["kart"])
        g.pack(anchor="w")
        bugun = date.today()
        self.bas, self.bit = ttk.Entry(g, width=12), ttk.Entry(g, width=12)
        self.bas.insert(0, date.fromordinal(bugun.toordinal() - 7).strftime("%d.%m.%Y"))
        self.bit.insert(0, bugun.strftime("%d.%m.%Y"))
        tk.Label(g, text="Verilme tarihi", bg=R["kart"], font=(YAZI, 9)).grid(row=0, column=0, sticky="w")
        self.bas.grid(row=0, column=1, padx=(8, 4))
        tk.Label(g, text="–", bg=R["kart"], font=(YAZI, 9)).grid(row=0, column=2)
        self.bit.grid(row=0, column=3, padx=(4, 0))
        self.sgk, self.liste = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        for metin, v in (("MUHSGK varsa SGK tahakkuk ve hizmet belgelerini de aktar", self.sgk),
                         ("Yalnız listele (deneme): yüklenecek evrakları göster, yükleme yapma", self.liste)):
            tk.Checkbutton(ana, text=metin, variable=v, bg=R["kart"], activebackground=R["kart"], font=(YAZI, 9), anchor="w").pack(anchor="w", pady=(6, 0))
        self.mesaj = tk.Label(ana, text="", bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 9), wraplength=440, justify="left")
        self.mesaj.pack(anchor="w", pady=(6, 0))
        alt = tk.Frame(ana, bg=R["kart"])
        alt.pack(anchor="w", pady=(8, 0))
        dugme(alt, "Başlat", self._tamam, ana=True).pack(side="left")
        dugme(alt, "Vazgeç", self.destroy, kucuk=True).pack(side="left", padx=(8, 0))
        self.transient(ust)
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._tamam())
        self.update_idletasks()
        self.geometry(f"+{ust.winfo_rootx() + max(0, (ust.winfo_width() - self.winfo_reqwidth()) // 2)}"
                      f"+{ust.winfo_rooty() + max(0, (ust.winfo_height() - self.winfo_reqheight()) // 3)}")
        self.grab_set()

    def _tamam(self):
        try:
            b, s = (datetime.strptime(k.get().strip(), "%d.%m.%Y").date() for k in (self.bas, self.bit))
        except ValueError:
            self.mesaj.configure(text="Tarihleri gg.aa.yyyy biçiminde yazın (ör. 25.11.2026).")
            return
        if b > s or s > date.today() or (s - b).days > 92:
            self.mesaj.configure(text="Başlangıç bitişten sonra olamaz, bitiş bugünden ileri olamaz, aralık 3 ayı geçemez.")
            return
        self.sonuc = {"tarih_bas": b.strftime("%d.%m.%Y"), "tarih_bit": s.strftime("%d.%m.%Y"), "sgk": self.sgk.get(),
                      "yalniz_listele": self.liste.get()}
        self.destroy()


class LucaGirisPenceresi(tk.Toplevel):
    """Luca girişi programın içinde: üye (müşteri) no + kullanıcı adı + parola (kayıtlıysa sorulmaz).
    Luca ek kod isterse (iki aşamalı doğrulama kapalıyken güvenlik kodu) Luca ekranının görüntüsü gösterilir; kodu kullanıcı yazar
    (program çözmez). Bilgiler gizli tarayıcıdaki gerçek Luca giriş formuna yazılır; parola günlüğe yazılmaz.
    sonra: giriş başarılı olunca çağrılır (ör. Başlat'ı sürdürmek için); lucasiz: 'Luca'sız devam et' komutu (varsa düğme çıkar)."""

    def __init__(self, uyg, sonra=None, lucasiz=None):
        super().__init__(uyg.kok)
        self.uyg, self.sonra, self.lucasiz = uyg, sonra, lucasiz
        self.title("Luca Girişi")
        self.resizable(False, False)
        self.configure(bg=R["kart"])
        self.bekliyor = False
        self.bekleyen = None                        # (üye no, kullanıcı, parola, sakla) — Luca kabul edince saklanır
        self.k_musteri, self.k_kullanici, self.k_parola = luca_kasasi.oku()
        self.duzenle = not self.k_parola
        ana = tk.Frame(self, bg=R["kart"], padx=18, pady=14)
        ana.pack(fill="both", expand=True)
        tk.Label(ana, text="Luca Girişi", bg=R["kart"], fg=R["lacivert"], font=(YAZI, 13, "bold")).pack(anchor="w")
        tk.Label(ana, text=("Luca sorguları için giriş gerekir. Tarayıcı arka planda çalışır; girişi buradan yapın." if LUCA_PROGRAM_ICI_GIRIS
                            else "Luca sorguları için giriş gerekir."), bg=R["kart"],
                 fg=R["gri"], font=(YAZI, 9), wraplength=380, justify="left").pack(anchor="w", pady=(0, 10))
        self.govde = tk.Frame(ana, bg=R["kart"])
        self.govde.pack(fill="both", expand=True)
        self.mesaj = tk.Label(ana, text="", bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 9), wraplength=380, justify="left")
        self.mesaj.pack(anchor="w", pady=(8, 0))
        alt = tk.Frame(ana, bg=R["kart"])
        alt.pack(fill="x", pady=(10, 0))
        if LUCA_PROGRAM_ICI_GIRIS:                  # program içi giriş açıkken yedek bağlantı; kapalıyken ana düğme zaten bunu yapar
            e = tk.Label(alt, text="Tarayıcıda elle giriş yap", bg=R["kart"], fg=R["mavi"], cursor="hand2", font=(YAZI, 9, "underline"))
            e.pack(side="left")
            e.bind("<Button-1>", lambda ev: self._elle())
        if lucasiz is not None:
            s = tk.Label(alt, text="Luca'sız devam et", bg=R["kart"], fg=R["mavi"], cursor="hand2", font=(YAZI, 9, "underline"))
            s.pack(side="right")
            s.bind("<Button-1>", lambda ev: self._lucasiz())
        self.sakla = tk.BooleanVar(value=True)
        self.musteri = self.kullanici = self.parola = None
        self.resim = self.kod = None                # Luca'nın istediği doğrulamanın görüntüsü ve kod kutusu
        if LUCA_PROGRAM_ICI_GIRIS:
            self._form()
        else:
            self._tarayici_bilgisi()
        self.transient(uyg.kok)
        self.protocol("WM_DELETE_WINDOW", self._vazgec)
        self.bind("<Escape>", lambda ev: self._vazgec())
        self.update_idletasks()
        x = uyg.kok.winfo_rootx() + max(0, (uyg.kok.winfo_width() - self.winfo_reqwidth()) // 2)
        y = uyg.kok.winfo_rooty() + max(0, (uyg.kok.winfo_height() - self.winfo_reqheight()) // 4)
        self.geometry(f"+{x}+{y}")
        self.grab_set()
        if LUCA_PROGRAM_ICI_GIRIS and not self.duzenle:     # kayıtlı bilgiyle kendiliğinden dener
            self.after(150, self._giris)

    def _tarayici_bilgisi(self):
        """Program içi giriş kapalıyken: kısa açıklama + Luca için tarayıcı penceresini açan düğme."""
        g = self.govde
        tk.Label(g, text="Luca, programın gizli tarayıcısında açılmıyor; giriş tarayıcı penceresinden yapılır.\n\n"
                         "1. Aşağıdaki düğmeye basın; bir Chrome penceresi açılır.\n"
                         "2. O pencerede Luca'ya girip \"LUCA MALİ MÜŞAVİR PAKETİ\"ni açın.\n"
                         "3. Program Luca'yı birkaç saniyede algılar ve kendiliğinden devam eder.",
                 bg=R["kart"], fg=R["yazi"], font=(YAZI, 9), wraplength=380, justify="left").pack(anchor="w")
        b = dugme(g, "Tarayıcıda Luca'ya giriş yap", self._elle, ana=True)
        b.pack(anchor="w", pady=(10, 0))
        self.b_giris = b.d

    # ---- 2. adım: Luca ek kod istiyor (güvenlik kodu / doğrulama kodu). Görüntü yalnız gösterilir; kodu kullanıcı yazar.
    def dogrulama_goster(self, png, mesaj=""):
        for w in self.govde.winfo_children():
            w.destroy()
        g = self.govde
        tk.Label(g, text=mesaj or "Luca güvenlik kodu istiyor.", bg=R["kart"], fg=R["yazi"], font=(YAZI, 9), wraplength=520,
                 justify="left").pack(anchor="w", pady=(0, 6))
        try:
            self.resim = tk.PhotoImage(data=base64.b64encode(png))
            if self.resim.width() > 640:
                self.resim = self.resim.subsample(2)
            tk.Label(g, image=self.resim, bg="#f2f2f2", bd=1, relief="solid").pack(anchor="w")
        except tk.TclError:
            tk.Label(g, text="Görüntü gösterilemedi; 'Tarayıcıda elle giriş yap' ile deneyin.", bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 9)).pack(anchor="w")
        s = tk.Frame(g, bg=R["kart"])
        s.pack(anchor="w", pady=(8, 0))
        tk.Label(s, text="Görüntüdeki kod", bg=R["kart"], font=(YAZI, 9)).pack(side="left")
        self.kod = ttk.Entry(s, width=16, font=(YAZI, 12))
        self.kod.pack(side="left", padx=(8, 8))
        self.kod.bind("<Return>", lambda ev: self._kod_gonder())
        b = dugme(s, "Gönder", self._kod_gonder, ana=True)
        b.pack(side="left")
        self.b_giris = b.d
        dugme(s, "Yeni kod", self._yeni_kod, kucuk=True).pack(side="left", padx=(8, 0))
        self.bekle(False, "")
        self.update_idletasks()
        self.kod.focus_set()

    def _kod_gonder(self):
        if self.bekliyor or self.kod is None or not self.bekleyen:
            return
        kod = self.kod.get().strip()
        if not kod:
            self.mesaj.configure(text="Görüntüdeki kodu yazın.", fg=R["kirmizi"])
            return
        m, k, pr, _ = self.bekleyen
        self.bekle(True, "Kod gönderiliyor…")
        self.uyg.isci.gonder("luca_giris", musteri_no=m, kullanici=k, parola=pr, kod=kod)

    def _yeni_kod(self):
        if self.bekliyor or not self.bekleyen:
            return
        m, k, pr, _ = self.bekleyen
        self.bekle(True, "Yeni kod isteniyor…")
        self.uyg.isci.gonder("luca_giris", musteri_no=m, kullanici=k, parola=pr)

    def _form(self):
        for w in self.govde.winfo_children():
            w.destroy()
        g = self.govde
        if self.duzenle:
            for satir, (etiket, ad, deger, gizli) in enumerate((("Üye (müşteri) no", "musteri", self.k_musteri, False),
                                                                ("Kullanıcı adı", "kullanici", self.k_kullanici, False),
                                                                ("Parola", "parola", "", True))):
                tk.Label(g, text=etiket, bg=R["kart"], font=(YAZI, 9)).grid(row=satir, column=0, sticky="w", pady=2)
                kutu = ttk.Entry(g, width=30, show="•" if gizli else "")
                kutu.insert(0, deger or "")
                kutu.grid(row=satir, column=1, sticky="w", padx=(8, 0), pady=2)
                kutu.bind("<Return>", lambda ev: self._giris())
                setattr(self, ad, kutu)
        else:
            self.musteri = self.kullanici = self.parola = None
            s = tk.Frame(g, bg=R["yesil_z"])
            s.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 4))
            tk.Label(s, text=f"Kayıtlı hesap: {self.k_musteri} / {self.k_kullanici}", bg=R["yesil_z"], fg=R["yazi"], font=(YAZI, 9)).pack(side="left", padx=8, pady=4)
            dg = tk.Label(s, text="Değiştir", bg=R["yesil_z"], fg=R["mavi"], cursor="hand2", font=(YAZI, 9, "underline"))
            dg.pack(side="right", padx=8)
            dg.bind("<Button-1>", lambda ev: self._degistir())
        tk.Checkbutton(g, text="Giriş bilgilerini bu bilgisayarda sakla (Windows hesabınıza bağlı, şifreli)", variable=self.sakla,
                       bg=R["kart"], activebackground=R["kart"], font=(YAZI, 9), anchor="w").grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 8))
        b = dugme(g, "Giriş yap", self._giris, ana=True)
        b.grid(row=4, column=1, sticky="w", padx=(8, 0))
        self.b_giris = b.d
        if self.duzenle:
            (self.musteri if not self.k_musteri else (self.kullanici if not self.k_kullanici else self.parola)).focus_set()

    def _degistir(self):
        if self.bekliyor:
            return
        self.duzenle = True
        self._form()

    def _giris(self):
        if self.bekliyor:
            return
        if self.duzenle:
            m, k, pr = self.musteri.get().strip(), self.kullanici.get().strip(), self.parola.get()
        else:
            m, k, pr = self.k_musteri, self.k_kullanici, self.k_parola
        if not m or not k or not pr:
            self.mesaj.configure(text="Üye no, kullanıcı adı ve parolayı yazın.", fg=R["kirmizi"])
            return
        self.bekleyen = (m, k, pr, self.sakla.get())
        self.bekle(True, "Luca'ya giriş yapılıyor… (yarım dakika kadar sürebilir)")
        self.uyg.isci.gonder("luca_giris", musteri_no=m, kullanici=k, parola=pr)

    def bekle(self, bekliyor, mesaj=""):
        self.bekliyor = bool(bekliyor)
        try:
            self.b_giris.configure(state="disabled" if bekliyor else "normal")
        except tk.TclError:
            pass
        self.mesaj.configure(text=mesaj, fg=R["gri"] if bekliyor else R["kirmizi"])

    def sonuc(self, veri):
        if veri.get("durum") == "tamam":
            if self.bekleyen:
                m, k, pr, sakla = self.bekleyen
                self.bekleyen = None
                try:
                    if sakla:
                        luca_kasasi.kaydet(m, k, pr)
                    else:
                        luca_kasasi.sil()
                except Exception as e:
                    self.uyg._yaz(f"UYARI: Luca giriş bilgisi saklanamadı ({type(e).__name__}).")
            sonra = self.sonra
            self.kapat()
            if sonra is not None:
                self.uyg.kok.after(200, sonra)
        else:
            son, self.bekleyen = self.bekleyen, None
            if not self.duzenle or self.kod is not None:    # kayıtlı bilgi kabul edilmedi ya da kod adımındaydı: giriş alanlarına dön
                if son:                             # üye no ve kullanıcı adı yeniden yazdırılmaz (parola yeniden istenir)
                    self.k_musteri, self.k_kullanici = son[0], son[1]
                self.duzenle = True
                self.kod = None
                self._form()
            self.bekle(False, veri.get("mesaj") or "Luca girişi yapılamadı.")

    def _elle(self):
        if self.bekliyor:                           # giriş denemesi sürerken tarayıcı görevi reddedilir
            self.mesaj.configure(text="Giriş denemesi sürüyor; bitince yeniden deneyin.", fg=R["gri"])
            return
        if not self.uyg.tarayici_goster(True, hizmet="luca"):          # işçi meşgul: pencere açık kalır, bekleyen işlem değişmez
            self.mesaj.configure(text="Bir işlem sürüyor; bitince yeniden deneyin.", fg=R["gri"])
            return
        sonra = self.sonra
        self.kapat()
        self.uyg.luca_elle = sonra if sonra is not None else True       # Luca algılanınca sürdürülür

    def _lucasiz(self):
        if self.bekliyor:
            self.mesaj.configure(text="Giriş denemesi sürüyor; bitince yeniden deneyin.", fg=R["gri"])
            return
        komut = self.lucasiz
        self.kapat()
        self.uyg.kok.after(100, komut)

    def _vazgec(self):
        self.kapat()

    def kapat(self):
        self.uyg.luca_p = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class GirisPenceresi(tk.Toplevel):
    """E-Mükellef girişi programın içinde: e-posta + şifre (kayıtlıysa sorulmaz) + güvenlik kodu, gerekirse doğrulama kodu.
    Bilgiler gizli tarayıcıdaki gerçek giriş formuna yazılır; şifre günlüğe yazılmaz."""

    def __init__(self, uyg):
        super().__init__(uyg.kok)
        self.uyg = uyg
        self.title("E-Mükellef Girişi")
        self.resizable(False, False)
        self.configure(bg=R["kart"])
        self.resim = None
        self.bekliyor = False                       # işçiden yanıt beklenirken yeni gönderim yapılmaz
        self.bekleyen = None                        # (e-posta, şifre, sakla) — site kabul edince saklanır
        self.kayitli_kul, self.kayitli_sifre = giris_kasasi.oku()
        self.duzenle = not self.kayitli_sifre       # kayıt yoksa alanlar açık gelir
        ana = tk.Frame(self, bg=R["kart"], padx=18, pady=14)
        ana.pack(fill="both", expand=True)
        tk.Label(ana, text="E-Mükellef Girişi", bg=R["kart"], fg=R["lacivert"], font=(YAZI, 13, "bold")).pack(anchor="w")
        tk.Label(ana, text="Tarayıcı arka planda çalışıyor; girişi buradan yapın.", bg=R["kart"], fg=R["gri"],
                 font=(YAZI, 9)).pack(anchor="w", pady=(0, 10))
        self.govde = tk.Frame(ana, bg=R["kart"])
        self.govde.pack(fill="both", expand=True)
        self.mesaj = tk.Label(ana, text="", bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 9), wraplength=360, justify="left")
        self.mesaj.pack(anchor="w", pady=(8, 0))
        alt = tk.Frame(ana, bg=R["kart"])
        alt.pack(fill="x", pady=(10, 0))
        self.b_elle = tk.Label(alt, text="Tarayıcıda elle giriş yap", bg=R["kart"], fg=R["mavi"], cursor="hand2", font=(YAZI, 9, "underline"))
        self.b_elle.pack(side="left")
        self.b_elle.bind("<Button-1>", lambda e: self._elle())
        self.sakla = tk.BooleanVar(value=True)
        self.kul = self.sifre = self.kod = self.dogrulama = None
        self._adim1()
        self.transient(uyg.kok)
        self.protocol("WM_DELETE_WINDOW", self._vazgec)
        self.bind("<Escape>", lambda e: self._vazgec())
        self.update_idletasks()
        x = uyg.kok.winfo_rootx() + max(0, (uyg.kok.winfo_width() - self.winfo_reqwidth()) // 2)
        y = uyg.kok.winfo_rooty() + max(0, (uyg.kok.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")
        self.grab_set()
        self.captcha_iste()

    # ---- 1. adım: e-posta + şifre + güvenlik kodu
    def _temizle(self):
        for w in self.govde.winfo_children():
            w.destroy()

    def _adim1(self):
        self._temizle()
        g = self.govde
        if self.duzenle:
            tk.Label(g, text="E-posta", bg=R["kart"], font=(YAZI, 9)).grid(row=0, column=0, sticky="w", pady=2)
            self.kul = ttk.Entry(g, width=36)
            self.kul.insert(0, self.kayitli_kul or "")
            self.kul.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=2)
            tk.Label(g, text="Şifre", bg=R["kart"], font=(YAZI, 9)).grid(row=1, column=0, sticky="w", pady=2)
            self.sifre = ttk.Entry(g, width=36, show="•")
            self.sifre.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)
        else:
            self.kul = self.sifre = None
            s = tk.Frame(g, bg=R["yesil_z"])
            s.grid(row=0, column=0, columnspan=2, sticky="we", pady=(0, 4))
            tk.Label(s, text=f"Kayıtlı hesap: {eposta_maske(self.kayitli_kul)}", bg=R["yesil_z"], fg=R["yazi"], font=(YAZI, 9)).pack(side="left", padx=8, pady=4)
            d = tk.Label(s, text="Değiştir", bg=R["yesil_z"], fg=R["mavi"], cursor="hand2", font=(YAZI, 9, "underline"))
            d.pack(side="right", padx=8)
            d.bind("<Button-1>", lambda e: self._degistir())
        tk.Checkbutton(g, text="E-posta ve şifreyi bu bilgisayarda sakla (Windows hesabınıza bağlı, şifreli)", variable=self.sakla,
                       bg=R["kart"], activebackground=R["kart"], font=(YAZI, 9), anchor="w").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 8))
        tk.Label(g, text="Güvenlik kodu", bg=R["kart"], font=(YAZI, 9)).grid(row=3, column=0, sticky="nw", pady=2)
        c = tk.Frame(g, bg=R["kart"])
        c.grid(row=3, column=1, sticky="w", padx=(8, 0))
        self.captcha = tk.Label(c, text="Güvenlik kodu yükleniyor…", bg="#f2f2f2", fg=R["gri"], width=30, height=4, font=(YAZI, 9))
        self.captcha.pack(side="left")
        y = dugme(c, "Yenile", self.captcha_iste, kucuk=True)
        y.pack(side="left", padx=(8, 0))
        self.b_yenile = y.d
        tk.Label(g, text="Resimdeki kod", bg=R["kart"], font=(YAZI, 9)).grid(row=4, column=0, sticky="w", pady=(8, 2))
        self.kod = ttk.Entry(g, width=16, font=(YAZI, 12))
        self.kod.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(8, 2))
        self.kod.bind("<Return>", lambda e: self._giris())
        b = dugme(g, "Giriş yap", self._giris, ana=True)
        b.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        self.b_giris = b.d
        (self.kul if self.duzenle and not (self.kayitli_kul or "") else (self.sifre if self.duzenle else self.kod)).focus_set()

    def _degistir(self):
        self.duzenle = True
        self._adim1()
        self.captcha_iste()

    def captcha_iste(self):
        if self.bekliyor:
            return
        self.bekle(True, "Güvenlik kodu yükleniyor…")
        self.uyg.isci.gonder("giris_formu")

    def captcha_goster(self, png, mesaj=""):
        """İşçiden gelen captcha resmi (PNG). Program resmi yalnız gösterir."""
        if self.dogrulama is not None:              # 2. adımdayken form başa döndü
            self.dogrulama = None
            self._adim1()
        try:
            self.resim = tk.PhotoImage(data=base64.b64encode(png))
            if self.resim.width() < 200:
                self.resim = self.resim.zoom(2)
            self.captcha.configure(image=self.resim, text="", width=self.resim.width(), height=self.resim.height())
        except tk.TclError:
            self.captcha.configure(image="", text="Resim gösterilemedi; Yenile'ye basın.")
        self.bekle(False, mesaj)
        if self.kod is not None:
            self.kod.delete(0, "end")
            self.kod.focus_set()

    def _giris(self):
        if self.bekliyor:
            return
        if self.duzenle:
            kul, sifre = self.kul.get().strip(), self.sifre.get()
        else:
            kul, sifre = self.kayitli_kul, self.kayitli_sifre
        kod = self.kod.get().strip()
        if not kul or not sifre or not kod:
            self.mesaj.configure(text="E-posta, şifre ve güvenlik kodunu yazın.", fg=R["kirmizi"])
            return
        self.bekleyen = (kul, sifre, self.sakla.get())
        self.bekle(True, "Giriş yapılıyor…")
        self.uyg.isci.gonder("giris_yap", kul_adi=kul, sifre=sifre, captcha=kod)

    # ---- 2. adım: doğrulama kodu (E-Mükellef isterse)
    def kod_adimi(self, mesaj):
        self._sakla()                               # e-posta/şifre kabul edildi
        self._temizle()
        g = self.govde
        tk.Label(g, text=mesaj or "E-Mükellef doğrulama kodu istiyor.", bg=R["kart"], fg=R["yazi"], font=(YAZI, 9),
                 wraplength=360, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        tk.Label(g, text="Doğrulama kodu", bg=R["kart"], font=(YAZI, 9)).grid(row=1, column=0, sticky="w")
        self.dogrulama = ttk.Entry(g, width=16, font=(YAZI, 12))
        self.dogrulama.grid(row=1, column=1, sticky="w", padx=(8, 0))
        self.dogrulama.bind("<Return>", lambda e: self._dogrula())
        b = dugme(g, "Onayla", self._dogrula, ana=True)
        b.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        self.b_giris = b.d
        self.b_yenile = None
        self.bekle(False, "")
        self.dogrulama.focus_set()

    def _dogrula(self):
        kod = self.dogrulama.get().strip()
        if not kod or self.bekliyor:
            return
        self.bekle(True, "Doğrulanıyor…")
        self.uyg.isci.gonder("giris_kod", kod=kod)

    # ---- ortak
    def _sakla(self):
        if not self.bekleyen:
            return
        kul, sifre, sakla = self.bekleyen
        self.bekleyen = None
        try:
            if sakla:
                giris_kasasi.kaydet(kul, sifre)
            else:
                giris_kasasi.sil()
        except Exception as e:
            self.uyg._yaz(f"UYARI: giriş bilgisi saklanamadı ({type(e).__name__}).")

    def bekle(self, bekliyor, mesaj=""):
        self.bekliyor = bool(bekliyor)
        for b in (getattr(self, "b_giris", None), getattr(self, "b_yenile", None)):
            if b is not None:
                try:
                    b.configure(state="disabled" if bekliyor else "normal")
                except tk.TclError:
                    pass
        self.mesaj.configure(text=mesaj, fg=R["gri"] if bekliyor else R["kirmizi"])

    def sonuc(self, veri):
        if veri.get("durum") == "tamam":
            self._sakla()
            self.kapat()
        else:
            self.bekleyen = None
            self.bekle(False, veri.get("mesaj") or "Giriş yapılamadı.")

    def _elle(self):
        self.kapat()
        self.uyg.tarayici_goster(True)

    def _vazgec(self):
        self.uyg.giris_ertelendi = True
        self.kapat()

    def kapat(self):
        self.uyg.giris_p = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


# ================================================================ ana pencere
class RaporPenceresi(tk.Toplevel):
    """KDV ve kâr/zarar durumu raporunu ekranda gösterir (kdv_kar.rapor_kur yapısı). Firma/dönem pencerede saklanır: 'PDF' ve
    'Excel' düğmeleri ana ekrandaki güncel seçimi değil, BU raporu kaydeder."""
    RENK = {"kirmizi": R["kirmizi"], "yesil": R["yesil"], "kalin": R["yazi"], "": R["yazi"]}

    def __init__(self, uyg, bilgi):
        super().__init__(uyg.kok)
        self.uyg, self.bilgi = uyg, bilgi
        r = bilgi["rapor"]
        self.title(f"KDV ve kâr / zarar durumu — {r['firma']} · {r['donem']}")
        self.configure(bg=R["zemin"])
        en, boy = 900, min(820, uyg.kok.winfo_screenheight() - 120)
        self.geometry(f"{en}x{boy}+{max(0, (self.winfo_screenwidth() - en) // 2)}+{max(0, (self.winfo_screenheight() - 79 - boy) // 2)}")
        self.minsize(700, 420)
        alt = tk.Frame(self, bg=R["zemin"])
        alt.pack(side="bottom", fill="x", padx=12, pady=10)
        dugme(alt, "PDF'i aç / kaydet", lambda: uyg.rapor_kaydet(bilgi, "pdf", self), ana=True).pack(side="left")
        dugme(alt, "Excel olarak kaydet", lambda: uyg.rapor_kaydet(bilgi, "xlsx", self)).pack(side="left", padx=(8, 0))
        dugme(alt, "Kapat", self.destroy).pack(side="right")
        kayit = bilgi.get("kayit") or {}
        if kayit.get("yol"):                        # rapor düzenlenirken PDF kendiliğinden kaydedildi: tıklanınca açılır
            k_yazi = tk.Label(alt, text="PDF kaydedildi: …\\" + "\\".join(Path(kayit["yol"]).parts[-2:]), bg=R["zemin"], fg=R["mavi"],
                              cursor="hand2", font=(YAZI, 8, "underline"))
            k_yazi.bind("<Button-1>", lambda e: uyg._ac(kayit["yol"]))
        else:
            k_yazi = tk.Label(alt, text=("PDF kaydedilemedi: " + kayit["hata"][:70]) if kayit.get("hata")
                              else "Dosya, dönemin 02_Raporlar klasörüne kaydedilir ve açılır.", bg=R["zemin"],
                              fg=R["kirmizi"] if kayit.get("hata") else R["gri"], font=(YAZI, 8))
        k_yazi.pack(side="left", padx=12)
        tuval = tk.Canvas(self, bg=R["zemin"], highlightthickness=0)
        kaydir = ttk.Scrollbar(self, orient="vertical", command=tuval.yview)
        tuval.configure(yscrollcommand=kaydir.set)
        kaydir.pack(side="right", fill="y")
        tuval.pack(side="left", fill="both", expand=True)
        ic = tk.Frame(tuval, bg=R["zemin"])
        pencere = tuval.create_window((0, 0), window=ic, anchor="nw")
        ic.bind("<Configure>", lambda e: tuval.configure(scrollregion=tuval.bbox("all")))
        tuval.bind("<Configure>", lambda e: tuval.itemconfigure(pencere, width=e.width))
        self.bind("<MouseWheel>", lambda e: tuval.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.bind("<Escape>", lambda e: self.destroy())

        ust = tk.Frame(ic, bg=R["zemin"])
        ust.pack(fill="x", padx=14, pady=(12, 4))
        # ofis logosu (kullanıcı verisinde; PDF'te de başlığın sağında çıkar). Tıklanınca / logo yokken bağlantıyla seçilir.
        logo_kutu = tk.Frame(ust, bg=R["zemin"])
        logo_kutu.pack(side="right", anchor="n")
        self.logo_yazi = tk.Label(logo_kutu, bg=R["zemin"], fg=R["mavi"], cursor="hand2", font=(YAZI, 8, "underline"))
        self.logo_yazi.pack(anchor="e")
        self.logo_yazi.bind("<Button-1>", lambda e: self._logo_sec())
        self.logo_don = tk.Label(logo_kutu, text="Varsayılan logoya dön", bg=R["zemin"], fg=R["mavi"], cursor="hand2",
                                 font=(YAZI, 8, "underline"))
        self.logo_don.bind("<Button-1>", lambda e: self._logo_varsayilan())
        self.logo_resim = None                      # PhotoImage başvurusu pencerede tutulur (yoksa görüntü kaybolur)
        self._logo_goster()
        tk.Label(ust, text="KDV ve Kâr / Zarar Durumu", bg=R["zemin"], fg=R["lacivert"], font=(YAZI, 15, "bold")).pack(anchor="w")
        tk.Label(ust, text=f"{r['firma']} · {r['donem']} · düzenleme {r['uretim']} · kaynak: Luca mizan ve Defteri Kebir", bg=R["zemin"],
                 fg=R["gri"], font=(YAZI, 9)).pack(anchor="w")
        if r.get("bant"):                           # stok yetmiyorsa: en üstte kırmızı bant (tüm stok maliyete atılırsa çıkacak vergi)
            bant = tk.Frame(ic, bg=R["kirmizi"])
            bant.pack(fill="x", padx=14, pady=(6, 0))
            tk.Label(bant, text=r["bant"][1], bg=R["kirmizi"], fg="white", font=(YAZI, 16, "bold")).pack(side="right", padx=12, pady=7)
            sol_b = tk.Frame(bant, bg=R["kirmizi"])
            sol_b.pack(side="left", padx=12, pady=7)
            tk.Label(sol_b, text=r["bant"][0], bg=R["kirmizi"], fg="white", font=(YAZI, 11, "bold"), anchor="w", justify="left",
                     wraplength=560).pack(anchor="w")
            tk.Label(sol_b, text=r["bant"][2], bg=R["kirmizi"], fg="white", font=(YAZI, 10, "bold"), anchor="w", justify="left",
                     wraplength=560).pack(anchor="w", pady=(3, 0))
        kutular = tk.Frame(ic, bg=R["zemin"])
        kutular.pack(fill="x", padx=14, pady=(6, 2))
        for n, (et, deger, stil) in enumerate(r["ozet"]):
            cerceve = tk.Frame(kutular, bg=self.RENK[stil] if stil in ("kirmizi", "yesil") else R["lacivert"])
            cerceve.pack(side="left", fill="both", expand=True, padx=(0 if n == 0 else 8, 0))
            k = tk.Frame(cerceve, bg=R["kirmizi_z"] if stil == "kirmizi" else R["kart"])
            k.pack(fill="both", expand=True, padx=(5, 1), pady=1)
            tk.Label(k, text=et, bg=k["bg"], fg=R["gri"], font=(YAZI, 8), anchor="w", wraplength=max(110, 800 // len(r["ozet"]) - 24), justify="left").pack(fill="x", padx=9, pady=(6, 0))
            tk.Label(k, text=deger, bg=k["bg"], fg=self.RENK[stil], font=(YAZI, 15 if len(r["ozet"]) <= 3 else 11, "bold"), anchor="w").pack(fill="x", padx=9, pady=(0, 6))
        for b in r["bolumler"]:
            dis, k = kart(ic)
            dis.pack(fill="x", padx=14, pady=(10, 0))
            tk.Label(k, text=b["baslik"], bg=R["kart"], fg=R["lacivert"], font=(YAZI, 10, "bold"), anchor="w").pack(fill="x", pady=(0, 5))
            tablo = tk.Frame(k, bg=R["cizgi"])
            tablo.pack(fill="x")
            adet = len(b["kolonlar"])
            for i, ad in enumerate(b["kolonlar"]):
                tablo.grid_columnconfigure(i, weight=3 if i == 0 else 1, uniform=None)
                tk.Label(tablo, text=ad, bg=R["lacivert"], fg="white", font=(YAZI, 9, "bold"), anchor="w" if i == 0 else "e",
                         padx=8, pady=3).grid(row=0, column=i, sticky="nsew")
            for n, s in enumerate(b["satirlar"]):
                stil = b["stil"].get(n, "")
                zemin = R["kirmizi_z"] if stil == "kirmizi" else R["kart"]
                for i in range(adet):
                    v = s[i] if i < len(s) else ""
                    sayi = i > 0 and (i == adet - 1 or adet > 3)
                    tk.Label(tablo, text=str(v), bg=zemin, fg=self.RENK[stil], font=(YAZI, 9, "bold" if stil else "normal"),
                             anchor="e" if sayi else "w", padx=8, pady=2, wraplength=0 if sayi else 430, justify="left"
                             ).grid(row=n + 1, column=i, sticky="nsew", pady=(1, 0))
            if b.get("not"):
                tk.Label(k, text=b["not"], bg=R["kart"], fg=R["gri"], font=(YAZI, 8), anchor="w", justify="left",
                         wraplength=820).pack(fill="x", pady=(5, 0))
        if r["uyarilar"]:
            dis, k = kart(ic)
            dis.pack(fill="x", padx=14, pady=(10, 0))
            tk.Label(k, text="Uyarılar ve notlar", bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 10, "bold"), anchor="w").pack(fill="x", pady=(0, 4))
            for u in r["uyarilar"]:
                tk.Label(k, text="•  " + u, bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 9), anchor="w", justify="left",
                         wraplength=820).pack(fill="x", pady=1)
        tk.Frame(ic, bg=R["zemin"], height=14).pack()
        self.transient(uyg.kok)
        self.lift()
        self.focus_set()

    def _logo_goster(self):
        bayt = ayar.logo_oku()
        self.logo_resim = None
        if bayt:
            try:
                resim = tk.PhotoImage(data=base64.b64encode(bayt), format="png")
                oran = max(1, -(-resim.height() // 64), -(-resim.width() // 170))    # en çok 64 x 170 piksel
                self.logo_resim = resim.subsample(oran) if oran > 1 else resim
            except tk.TclError:
                self.logo_resim = None
        if self.logo_resim is not None:
            self.logo_yazi.configure(image=self.logo_resim, text="")
        else:
            self.logo_yazi.configure(image="", text="Ofis logosu ekle (PNG)")
        if ayar.ozel_logo_var():                    # kendi logosunu seçmiş: varsayılana dönebilir
            self.logo_don.pack(anchor="e")
        else:
            self.logo_don.pack_forget()

    def _logo_varsayilan(self):
        try:
            ayar.logo_sil()
        except OSError as e:
            messagebox.showerror("Ofis logosu", f"Logo kaldırılamadı: {e}", parent=self)
            return
        self._logo_goster()
        self.uyg._yaz("Kendi logonuz kaldırıldı; raporlarda programın varsayılan logosu çıkar.")

    def _logo_sec(self):
        yol = filedialog.askopenfilename(parent=self, title="Kendi logonuz (PNG) — raporların başlığında varsayılanın yerine çıkar",
                                         filetypes=[("PNG resmi", "*.png")])
        if not yol:
            return
        try:
            bayt = Path(yol).read_bytes()
            tk.PhotoImage(data=base64.b64encode(bayt), format="png")      # açılabiliyor mu
            ayar.logo_kaydet(bayt)
        except (OSError, ValueError, tk.TclError) as e:
            messagebox.showerror("Ofis logosu", f"Logo kullanılamadı: {e if isinstance(e, ValueError) else 'dosya açılamadı ya da PNG değil'}",
                                 parent=self)
            return
        self._logo_goster()
        self.uyg._yaz("Kendi logonuz kaydedildi; rapor PDF'lerinin başlığında varsayılan logonun yerine çıkar.")


class Uygulama:
    def __init__(self, kok, isci_sinifi=Isci, otomatik=True, acilis_bilgisi=""):
        self.kok = kok
        self.surum = guncelle.yerel_surum()
        kok.title(PROGRAM_ADI + (f" — sürüm {self.surum}" if self.surum else ""))
        kok.configure(bg=R["zemin"])
        ekran_boy = kok.winfo_screenheight()        # görev çubuğu (~48) ve pencere başlığı (~31) için pay bırak
        ekran_en = kok.winfo_screenwidth()
        self.uc_sutun = ekran_en >= GENIS_EKRAN     # Luca grubuyla 30 sorgu: geniş ekranda üç sütun
        en, boy = (min(1500, ekran_en - 36) if self.uc_sutun else 1240), min(850, ekran_boy - 120)
        kok.geometry(f"{en}x{boy}+{max(0, (kok.winfo_screenwidth() - en) // 2)}+{max(0, (ekran_boy - 79 - boy) // 2)}")
        kok.minsize(1100, 620)
        self.olaylar = queue.Queue()
        self.isci = isci_sinifi(self.olaylar)
        self.calisiyor = False
        self.kosu = False                           # Başlat/tekrar çalışıyor mu (Durdur düğmesi)
        self.toplu = None                           # sık kullanılan firmaların toplu sorgusu sürüyorsa durumu (bkz. _toplu_baslat)
        self.kosu_baslangic = None
        self.emk, self.gorunur = None, False        # giriş durumu, tarayıcı penceresi görünür mü
        self.luca = None                            # Luca oturumu (None: bakılmadı, False: giriş yok, True: bağlı)
        self.luca_p = None                          # açık Luca giriş penceresi
        self.luca_elle = None                       # görünür tarayıcıda Luca'ya elle giriş bekleniyor (True ya da sonra çağrılacak işlev)
        self.luca_firmalar = [tuple(x) for x in json_oku(LUCA_FIRMA_ONBELLEK, {}).get("liste", []) if isinstance(x, list) and len(x) == 2]
        self.son_kosu_firma = None                  # son çalıştırmanın firma no'su ("tekrar"da Luca eşlemesi aktarımı için)
        self.giris_p = None
        self.giris_ertelendi = False
        self.komut = None                           # komut.py ile gelen son/çalışan istek (durum.json'a yazılır)
        self.komut_gecmis = {}                      # son komutların sonuçları (id -> kayıt); istemci kendi sonucunu buradan da bulur
        self.komut_onceki = None                    # komuttan önceki kullanıcı seçimleri (koşu bitince geri yüklenir)
        self.komut_ayar = None                      # komutun arayüzde yaptığı ayarlar (kullanıcı koşu sırasında değiştirdiyse ezilmez)
        self.komut_sira = None                      # komutun işçideki görev sırası ('gorev_bitti' olayı bununla eşleşir)
        self.son_hata = ""
        self.durum_dosya_zamani = 0.0
        try:                                        # program kapalıyken kalmış eski istek çalıştırılmaz
            KOMUT_KLASORU.mkdir(parents=True, exist_ok=True)
            if KOMUT_ISTEK.is_file():
                KOMUT_ISTEK.unlink()
        except OSError:
            pass
        self.son_kontrol = self.son_etkinlik = time.time()
        kok.bind_all("<Button-1>", lambda e: setattr(self, "son_etkinlik", time.time()), add="+")
        kok.bind_all("<Key>", lambda e: setattr(self, "son_etkinlik", time.time()), add="+")
        self.secili_id = None
        self.tum, self.gorunen = [], []
        self.bitis = None
        self.sorgu_durumu, self.sorgu_bilgi = {}, {}
        self.onceki = None                          # seçilen firma/dönemde önceden indirilmiş verinin özeti (arka planda okunur)
        self.onceki_sira = 0                        # yalnız en son istenen okuma kabul edilir
        self.onceki_zamanlayici = None
        self.onceki_yollar = {}                     # nottaki bağlantıların açtığı dosya/klasör
        self.kosu_hedef = None                      # son Başlat'ın (firma no, ay, yıl): sorgu kutularındaki canlı sonuçlar buna aittir
        self.beyan_kosu = False                     # 'Verilen beyannameyi indir' görevi gönderildi ve bitmedi (Başlat kilitli, Durdur açık)
        self.beyan_sira = None                      # o görevin işçideki sırası ('gorev_bitti' olayı bununla eşleşir)
        self.ayarlar = ayar.ayarlari_oku()
        self.kayitli_secim = secim_kaydi_oku(self.ayarlar)      # kullanıcının kaydettiği "İndirilecekler" seçimi (yoksa None)
        # kayıt kökü: firma klasörlerinin ana klasörü (bilgisayara göre değişir; bkz. ayar.py). Tanımlı ama erişilemiyorsa ayar korunur.
        self.kok_yolu, self.kok_durumu = ayar.kayit_koku(self.ayarlar)
        if self.kok_yolu is not None:
            cikti.FIRMALAR = Path(self.kok_yolu)
        self.eslesme_hatasi = None                  # firmalar.json bozuksa uyarı metni
        self.kok_soruluyor = 0                       # >0: kayıt klasörü penceresi/seçicisi açık (arka plan giriş denetimi beklesin)
        onceki = date.today().replace(day=1)
        self.ay_no, self.yil_no = (12, onceki.year - 1) if onceki.month == 1 else (onceki.month - 1, onceki.year)
        self._listeyi_kur()
        try:
            ttk.Style().configure("TEntry", padding=3)
        except tk.TclError:
            pass

        self._ust_bant()
        govde = tk.Frame(kok, bg=R["zemin"])
        govde.pack(fill="both", expand=True, padx=12, pady=12)
        sol = tk.Frame(govde, bg=R["zemin"], width=372)
        sol.pack(side="left", fill="y")
        sol.pack_propagate(False)
        sag = tk.Frame(govde, bg=R["zemin"])
        sag.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self._sol_sutun(sol)
        self._sag_sutun(sag)

        self._suz()
        self._donem_guncelle()
        self._durum_goster()
        self._kok_goster()
        kok.protocol("WM_DELETE_WINDOW", self._kapat)
        kok.after(150, self._olaylari_isle)
        kok.after(1000, self._saniye)
        self._yaz("Program açıldı. Tarayıcı arka planda çalışır; giriş gerekirse giriş penceresi açılır.")
        self.ara.focus_set()
        if self.eslesme_hatasi:
            self._yaz("UYARI: " + self.eslesme_hatasi)
        if not self._kok_var():
            self._yaz("Kayıt klasörü seçilmedi." if self.kok_yolu is None else f"UYARI: kayıt klasörüne erişilemiyor: {self.kok_yolu}")
        if acilis_bilgisi:
            self._yaz(acilis_bilgisi)
        if otomatik:
            kok.after(400, self._ilk_acilis)

    def _ilk_acilis(self):
        """Kayıt klasörü yoksa önce o sorulur (giriş penceresiyle üst üste binmesin), sonra tarayıcı arka planda açılır."""
        if not self._kok_var():
            self._kok_gerekli()
        self.isci.gonder("tarayici_ac", gorunur=False)

    # ------------------------------------------------------------ kayıt klasörü (firma klasörlerinin ana klasörü)
    def _kok_var(self):
        try:
            return self.kok_yolu is not None and Path(self.kok_yolu).is_dir()
        except OSError:
            return False

    def _isci_bos(self):
        kuyruk = getattr(self.isci, "gorevler", None)
        return kuyruk is None or kuyruk.empty()

    def _kok_goster(self):
        """Üst banttaki kayıt klasörü yazısı (tıklanınca değiştirilir)."""
        if self.kok_yolu is None:
            metin, renk = "Kayıt klasörü seçilmedi — seçmek için tıklayın", "#f0b43c"
        else:
            yol = str(self.kok_yolu)
            kisa = yol if len(yol) <= 44 else yol[:14] + "…" + yol[-28:]
            metin, renk = (f"Kayıt klasörü: {kisa}", "#c6d3e4") if self._kok_var() else (f"Kayıt klasörüne erişilemiyor: {kisa}", "#f0b43c")
        self.kok_yazi.configure(text=metin, fg=renk)

    def _kok_penceresi(self):
        if self.kok_yolu is None or not self._kok_var():
            self._kok_gerekli()
            return
        if messagebox.askyesno("Kayıt klasörü", f"Firma dosyalarının kaydedildiği ana klasör:\n\n{self.kok_yolu}\n\n"
                               "Her firmanın kendi klasörü bunun içindedir. Değiştirmek ister misiniz?", default="no", parent=self.kok):
            self._kok_sec()

    def _kok_gerekli(self):
        self.kok_soruluyor += 1                      # pencere açıkken arka plan giriş denetimi beklesin (üstüne pencere açılmasın)
        try:
            return self._kok_gerekli_ic()
        finally:
            self.kok_soruluyor -= 1

    def _kok_gerekli_ic(self):
        """Kayıt klasörü kullanılabilir mi; değilse kullanıcıya sorar / uyarır. Kullanılabilir (hâle geldiyse) True."""
        if self._kok_var():
            return True
        if self.kok_yolu is not None:               # tanımlı ama erişilemiyor: ayar korunur, başka klasöre kendiliğinden geçilmez
            if not messagebox.askyesno("Kayıt klasörüne erişilemiyor",
                                       f"{self.kok_yolu}\n\nklasörüne şu an erişilemiyor (ağ sürücüsü ya da harici disk bağlı olmayabilir). "
                                       "Bağlayıp yeniden deneyin.\n\nBunun yerine BAŞKA bir kayıt klasörü seçmek ister misiniz?",
                                       default="no", parent=self.kok):
                self._kok_goster()
                return False
        else:
            messagebox.showinfo("Kayıt klasörü", "Firma dosyalarının kaydedileceği ANA klasörü seçin.\n\nİçinde her firmanın kendi klasörü "
                                "olur (ör. D:\\Firmalar). Sonradan üstteki 'Kayıt klasörü' yazısına tıklayarak değiştirebilirsiniz.",
                                parent=self.kok)
        return self._kok_sec()

    def _kok_sec(self):
        self.kok_soruluyor += 1
        try:
            return self._kok_sec_ic()
        finally:
            self.kok_soruluyor -= 1

    def _kok_sec_ic(self):
        """Klasör seçtirir ve kayıt klasörü yapar. Seçildiyse True."""
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():
            messagebox.showinfo("Kayıt klasörü", "Bir işlem sürerken kayıt klasörü değiştirilemez; bitince yeniden deneyin.", parent=self.kok)
            return False
        ilk = str(self.kok_yolu) if self._kok_var() else str(Path.home())
        secilen = filedialog.askdirectory(parent=self.kok, title="Firma dosyalarının kaydedileceği ana klasör", initialdir=ilk, mustexist=True)
        if not secilen:
            return False
        yol = Path(secilen)
        try:
            if not yol.is_dir():
                raise OSError("klasör bulunamadı")
            if (yol / "KDV").is_dir() and not messagebox.askyesno(
                    "Kayıt klasörü", f"{yol}\n\nBu klasör bir FİRMA klasörüne benziyor (içinde 'KDV' klasörü var). Ana klasör, firma "
                    "klasörlerinin ÜSTÜNDEKİ klasördür.\n\nYine de bu klasör kullanılsın mı?", default="no", parent=self.kok):
                return False
            deneme = yol / ".kdv_onhazirlik_yazma_denemesi.tmp"     # yazma yetkisi: is_dir bunu göstermez
            deneme.write_bytes(b"")
            deneme.unlink()
        except OSError as e:
            messagebox.showerror("Kayıt klasörü", f"{yol}\n\nklasörü kullanılamıyor (yazılamıyor ya da erişilemiyor):\n{e}", parent=self.kok)
            return False
        try:
            self.ayarlar = ayar.ayar_yaz(**{ayar.KOK_ANAHTARI: str(yol)})
        except OSError as e:
            messagebox.showerror("Kayıt klasörü", f"Ayar kaydedilemedi: {e}", parent=self.kok)
            return False
        self._kok_uygula(yol)
        return True

    def _kok_uygula(self, yol):
        """Yeni kayıt klasörüne geçer: eski çalıştırmanın durumu ve önbellekler temizlenir (eski sonuç yeni klasöre yazılmasın)."""
        self.kok_yolu, self.kok_durumu = Path(yol), "ayar"
        cikti.FIRMALAR = Path(yol)
        _ONCEKI_ONBELLEK.clear()
        self.onceki, self.kosu_hedef, self.bitis = None, None, None
        self.isci.gonder("son_unut", sessiz=True)
        self._durumlari_sifirla()
        self._alt_goster("gunluk")
        secili = self.secili_id
        self._listeyi_kur()
        self.secili_id = secili
        self._suz()
        self._firma_goster()
        self._kok_goster()
        self._yaz(f"Kayıt klasörü: {yol}")

    # ------------------------------------------------------------ üst bant
    def _ust_bant(self):
        ust = tk.Frame(self.kok, bg=R["lacivert"], height=54)
        ust.pack(fill="x")
        ust.pack_propagate(False)
        tk.Label(ust, text=PROGRAM_ADI, bg=R["lacivert"], fg="white", font=(YAZI, 13, "bold")).pack(side="left", padx=(18, 12))
        self.kok_yazi = tk.Label(ust, text="", bg=R["lacivert"], fg="#c6d3e4", font=(YAZI, 9), cursor="hand2")
        self.kok_yazi.pack(side="left")
        self.kok_yazi.bind("<Button-1>", lambda e: self._kok_penceresi())
        self.adres_yazi = tk.Label(ust, text="", bg=R["lacivert"], fg="#c6d3e4", font=(YAZI, 9), cursor="hand2")
        self.adres_yazi.pack(side="left", padx=(10, 0))
        self.adres_yazi.bind("<Button-1>", lambda e: self._adres_degistir())
        self._adres_goster()
        self.b_tarayici = tk.Button(ust, text="Tarayıcıyı göster", command=lambda: self.tarayici_goster(not self.gorunur), relief="flat", bd=0,
                                    bg="#2d4d78", fg="white", activebackground="#3a5f90", activeforeground="white", font=(YAZI, 9),
                                    padx=10, pady=3, cursor="hand2")
        self.b_tarayici.pack(side="right", padx=(8, 18))
        self.b_giris = tk.Button(ust, text="Giriş yap", command=self.giris_ac, relief="flat", bd=0, bg="#e8a317", fg="#1f2933",
                                 activebackground="#f0b43c", font=(YAZI, 9, "bold"), padx=10, pady=3, cursor="hand2")
        self.durum_yazi = tk.Label(ust, text="", bg=R["lacivert"], fg="white", font=(YAZI, 9))
        self.durum_yazi.pack(side="right")
        self.nokta = tk.Canvas(ust, width=12, height=12, bg=R["lacivert"], highlightthickness=0)
        self.nokta.pack(side="right", padx=(0, 6))
        self.nokta_no = self.nokta.create_oval(1, 1, 11, 11, fill="#9aa3af", outline="")
        self.b_luca = tk.Button(ust, text="Luca girişi", command=self.luca_giris_ac, relief="flat", bd=0, bg="#2d4d78", fg="white",
                                activebackground="#3a5f90", activeforeground="white", font=(YAZI, 9), padx=8, pady=2, cursor="hand2")
        self.luca_yazi = tk.Label(ust, text="", bg=R["lacivert"], fg="white", font=(YAZI, 9))
        self.luca_yazi.pack(side="right", padx=(0, 18))
        self.luca_nokta = tk.Canvas(ust, width=12, height=12, bg=R["lacivert"], highlightthickness=0)
        self.luca_nokta.pack(side="right", padx=(0, 6))
        self.luca_nokta_no = self.luca_nokta.create_oval(1, 1, 11, 11, fill="#9aa3af", outline="")

    def _adres_goster(self):
        sunucu = emukellef.ADRES.replace("https://", "")
        self.adres_yazi.configure(text=f"·  E-Mükellef: {sunucu.removesuffix('.' + ayar.EMUKELLEF_ALANI)}")

    def _adres_degistir(self):
        """E-Mükellef adresini (ofisi) değiştirir. Adres çalışırken değişmez: ayar yazılır ve program kapanır.
        Başka ofisin verisi karışmasın diye mükellef listesi önbelleği, kayıtlı giriş ve firma eşlemesi ayrılır."""
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():
            messagebox.showinfo("E-Mükellef adresi", "Bir işlem sürerken adres değiştirilemez; bitince yeniden deneyin.", parent=self.kok)
            return
        eski = emukellef.ADRES
        yeni = adres_sor(self.kok, eski)
        if not yeni or yeni == eski:
            return
        if not messagebox.askyesno("E-Mükellef adresi", f"E-Mükellef adresi değiştirilsin mi?\n\n{eski}\n→ {yeni}\n\nBu, BAŞKA bir ofis "
                                   "hesabına geçmek demektir: kayıtlı E-Mükellef girişi ve mükellef listesi silinir, firma → klasör "
                                   "eşlemeleri ayrı bir dosyaya alınır (eski adrese dönerseniz geri gelir). Program kapanır; yeniden "
                                   "açtığınızda yeni adresle giriş yaparsınız.", default="no", parent=self.kok):
            return
        try:
            self.ayarlar = ayar.ayar_yaz(**{ayar.ADRES_ANAHTARI: yeni, "son_firmalar": None})
        except OSError as e:
            messagebox.showerror("E-Mükellef adresi", f"Ayar kaydedilemedi: {e}", parent=self.kok)
            return
        yol = Path(FIRMALAR_JSON)

        def yedek(adres):
            return yol.with_name(f"firmalar.{adres.replace('https://', '')}.json")
        try:
            MUKELLEF_ONBELLEK.unlink(missing_ok=True)
            giris_kasasi.sil()
            if yol.is_file():
                os.replace(yol, yedek(eski))
            if yedek(yeni).is_file():
                os.replace(yedek(yeni), yol)
        except OSError as e:
            messagebox.showwarning("E-Mükellef adresi", f"Eski ofisin dosyaları tam ayrılamadı: {e}", parent=self.kok)
        messagebox.showinfo("E-Mükellef adresi", "Adres kaydedildi. Program şimdi kapanacak; yeniden açın.", parent=self.kok)
        self._kapat()

    def _durum_goster(self):
        if self.emk is True:
            renk, metin = "#35c46a", f"E-Mükellef: giriş yapıldı · {self.onbellek_adet} mükellef" if self.onbellek_adet else "E-Mükellef: giriş yapıldı"
        elif self.emk is False:
            renk, metin = "#e8a317", "E-Mükellef: giriş gerekli" + (" — açılan tarayıcıda giriş yapın" if self.gorunur else "")
        else:
            renk, metin = "#9aa3af", "E-Mükellef: bağlanılıyor…"
        self.nokta.itemconfigure(self.nokta_no, fill=renk)
        self.durum_yazi.configure(text=metin)
        self.b_tarayici.configure(text="Tarayıcıyı gizle" if self.gorunur else "Tarayıcıyı göster")
        if self.emk is False and not self.gorunur:
            self.b_giris.pack(side="right", padx=(8, 0), before=self.durum_yazi)
        else:
            self.b_giris.pack_forget()
        self.luca_nokta.itemconfigure(self.luca_nokta_no, fill="#35c46a" if self.luca is True else "#9aa3af")
        self.luca_yazi.configure(text="Luca: bağlı" if self.luca is True else "Luca: giriş yapılmadı")
        if self.luca is True:
            self.b_luca.pack_forget()
        else:
            self.b_luca.pack(side="right", padx=(0, 14), before=self.luca_yazi)

    # ------------------------------------------------------------ sol sütun
    def _sol_sutun(self, sol):
        # 1) firma
        dis, k = kart(sol)
        dis.pack(fill="x")
        ilk_kart = dis
        b = baslik(k, "FİRMA", 1)
        b.pack(fill="x", pady=(0, 7))
        tk.Label(b, text="ünvan veya VKN yazarak arayın", bg=R["kart"], fg=R["acik_gri"], font=(YAZI, 8)).pack(side="right")
        self.ara = ttk.Entry(k)
        self.ara.pack(fill="x")
        self.ara.bind("<KeyRelease>", self._suz)
        self.ara.bind("<Return>", lambda e: self._ilkini_sec())
        self.ara.bind("<Down>", lambda e: (self.liste.focus_set(), self.liste.selection_set(0)) if self.gorunen else None)
        self.sonlar = tk.Frame(k, bg=R["kart"])
        self.sonlar.pack(fill="x", pady=(6, 5))
        self.sik_satir = tk.Frame(k, bg=R["kart"])          # sık kullanılan firmalar + toplu sorgu (kullanıcı isteği 26.11.2026)
        self.sik_satir.pack(fill="x", pady=(0, 5))
        lc = tk.Frame(k, bg=R["cizgi"])
        lc.pack(fill="x")
        self.liste = tk.Listbox(lc, height=5, exportselection=False, activestyle="none", relief="flat", bd=0, highlightthickness=0,
                                font=(YAZI, 9), selectbackground=R["secili_z"], selectforeground=R["yazi"])
        kay = ttk.Scrollbar(lc, orient="vertical", command=self.liste.yview)
        self.liste.config(yscrollcommand=kay.set)
        kay.pack(side="right", fill="y", padx=(0, 1), pady=1)
        self.liste.pack(side="left", fill="x", expand=True, padx=(1, 0), pady=1)
        self.liste.bind("<<ListboxSelect>>", self._listeden_sec)
        self.liste.bind("<Return>", self._listeden_sec)
        self.liste_bilgi = tk.Label(k, text="", bg=R["kart"], fg=R["acik_gri"], font=(YAZI, 8), anchor="w")
        self.liste_bilgi.pack(fill="x")
        sc = tk.Frame(k, bg=R["yesil"])
        sc.pack(fill="x", pady=(5, 0))
        self.secilen_kutu = tk.Frame(sc, bg=R["yesil_z"])
        self.secilen_kutu.pack(fill="x", padx=(3, 0))
        self.secilen_cubuk = sc
        self.secilen_ad = tk.Label(self.secilen_kutu, text="", bg=R["yesil_z"], fg=R["yazi"], font=(YAZI, 9, "bold"), anchor="w",
                                   justify="left", wraplength=320)
        self.secilen_ad.pack(fill="x", padx=8, pady=(5, 0))
        self.secilen_alt = tk.Label(self.secilen_kutu, text="", bg=R["yesil_z"], fg=R["yok"], font=(YAZI, 8), anchor="w",
                                    justify="left", wraplength=320)
        self.secilen_alt.pack(fill="x", padx=8, pady=(0, 5))

        # 2) dönem
        dis, k = kart(sol)
        dis.pack(fill="x", pady=(10, 0))
        baslik(k, "KDV DÖNEMİ", 2).pack(anchor="w", pady=(0, 7))
        d = tk.Frame(k, bg=R["kart"])
        d.pack(fill="x")
        geri = dugme(d, "◀", lambda: self._donem_kaydir(-1), kucuk=True, zemin=R["kart"])
        geri.pack(side="left")
        ileri = dugme(d, "▶", lambda: self._donem_kaydir(1), kucuk=True, zemin=R["kart"])
        ileri.pack(side="right")
        for b in (geri.d, ileri.d):
            b.configure(font=(YAZI, 10), pady=3)
        dc = tk.Frame(d, bg=R["dugme_k"])
        dc.pack(side="left", fill="x", expand=True, padx=8)
        self.donem_yazi = tk.Label(dc, text="", bg=R["kart"], fg=R["yazi"], font=(YAZI, 13, "bold"))
        self.donem_yazi.pack(fill="both", expand=True, padx=1, pady=1)
        self.donem_ipucu = tk.Label(k, text="", bg=R["kart"], fg=R["gri"], font=(YAZI, 8), anchor="w", justify="left", wraplength=340)
        self.donem_ipucu.pack(fill="x", pady=(6, 0))
        # önceki indirme notu (firma seçilince görünür; içerik _onceki_goster)
        self.onceki_cubuk = tk.Frame(k, bg=R["dugme_k"])
        self.onceki_kutu = tk.Frame(self.onceki_cubuk, bg=R["yok_z"])
        self.onceki_kutu.pack(fill="x", padx=(3, 0))
        self.onceki_ust = tk.Frame(self.onceki_kutu, bg=R["yok_z"])
        self.onceki_ust.pack(fill="x", padx=8, pady=(5, 0))
        self.onceki_baslik = tk.Label(self.onceki_ust, text="", bg=R["yok_z"], fg=R["yazi"], font=(YAZI, 9, "bold"), anchor="w")
        self.onceki_baslik.pack(side="left")
        self.onceki_l_klasor = tk.Label(self.onceki_ust, text="Klasör", bg=R["yok_z"], fg=R["mavi"], cursor="hand2", font=(YAZI, 8, "underline"))
        self.onceki_l_klasor.bind("<Button-1>", lambda e: self._ac(self.onceki_yollar.get("klasor")))
        self.onceki_l_ozet = tk.Label(self.onceki_ust, text="Özeti aç", bg=R["yok_z"], fg=R["mavi"], cursor="hand2", font=(YAZI, 8, "underline"))
        self.onceki_l_ozet.bind("<Button-1>", lambda e: self._ac(self.onceki_yollar.get("ozet")))
        self.onceki_yazi = tk.Label(self.onceki_kutu, text="", bg=R["yok_z"], fg=R["yok"], font=(YAZI, 8), anchor="w", justify="left",
                                    wraplength=320)
        self.onceki_alt = tk.Frame(self.onceki_kutu, bg=R["yok_z"], height=5)
        self.onceki_alt.pack(fill="x")

        # 3) seçenekler
        dis, k = kart(sol)
        dis.pack(fill="x", pady=(10, 0))
        baslik(k, "SEÇENEKLER", 3).pack(anchor="w", pady=(0, 5))
        ks = self.kayitli_secim or {}
        self.earsiv_gib = tk.BooleanVar(value=ks.get("earsiv_gib", True))
        self.pdf = tk.BooleanVar(value=ks.get("pdf", False))       # varsayılan: yalnız XML (kullanıcı kararı 18.11.2026)
        for metin, v in (("Önce GİB'den yeniden sorgula (önerilir, daha yavaş)", self.earsiv_gib),
                         ("Fatura PDF'lerini de indir (XML her zaman iner)", self.pdf)):
            tk.Checkbutton(k, text=metin, variable=v, bg=R["kart"], activebackground=R["kart"], font=(YAZI, 9), anchor="w",
                           bd=0, highlightthickness=0).pack(fill="x")

        # büyük düğme (çerçeve = kenarlık rengi: Başlat'ta yeşil, Durdur'da kırmızı)
        alt_dugmeler = tk.Frame(sol, bg=R["zemin"])
        alt_dugmeler.pack(side="bottom", fill="x", before=ilk_kart)          # yer daralırsa kartlar kısalır, düğmeler kaybolmaz
        # ikincil düğme (kullanıcı isteği 20.11.2026): beyanname verildikten sonra yalnız verilen beyannameyi indirir
        self.beyan_cerceve = tk.Frame(alt_dugmeler, bg=R["lacivert"])
        self.beyan_cerceve.pack(side="right", fill="y", padx=(8, 0))
        self.b_beyan = tk.Button(self.beyan_cerceve, text="Verilen beyannameyi\nindir", command=self._verilen_indir, relief="flat", bd=0,
                                 cursor="hand2", font=(YAZI, 9, "bold"), bg=R["kart"], fg=R["lacivert"], activebackground=R["secili_z"],
                                 activeforeground=R["lacivert"], disabledforeground=R["acik_gri"], padx=9, justify="center")
        self.b_beyan.pack(fill="both", expand=True, padx=1, pady=1)
        # GİB'den E-Mükellef'e aktarma (kullanıcı isteği 25.11.2026): E-Mükellef masaüstü programı sürülür (emk_masaustu.py)
        self.aktar_cerceve = tk.Frame(alt_dugmeler, bg=R["lacivert"])
        self.aktar_cerceve.pack(side="right", fill="y", padx=(8, 0))
        self.b_aktar = tk.Button(self.aktar_cerceve, text="GİB'den\naktar", command=self._gib_aktar, relief="flat", bd=0,
                                 cursor="hand2", font=(YAZI, 9, "bold"), bg=R["kart"], fg=R["lacivert"], activebackground=R["secili_z"],
                                 activeforeground=R["lacivert"], disabledforeground=R["acik_gri"], padx=9, justify="center")
        self.b_aktar.pack(fill="both", expand=True, padx=1, pady=1)
        self.basla_cerceve = tk.Frame(alt_dugmeler, bg=R["yesil"])
        self.basla_cerceve.pack(side="left", fill="both", expand=True)
        self.b_basla = tk.Button(self.basla_cerceve, text="", command=self._baslat_durdur, relief="flat", bd=0, cursor="hand2",
                                 font=(YAZI, 13, "bold"), bg=R["yesil"], fg="white", activebackground="#22994a",
                                 activeforeground="white", disabledforeground="#cfe8d6", pady=9)
        self.b_basla.pack(fill="x", padx=2, pady=2)
        # KDV ve kâr/zarar raporu (kullanıcı isteği 20.11.2026): Luca mizanları + kebirden ekranda rapor; PDF / Excel kaydedilebilir
        self.rapor_cerceve = tk.Frame(sol, bg=R["lacivert"])
        self.rapor_cerceve.pack(side="bottom", fill="x", before=ilk_kart, pady=(0, 6))
        self.b_rapor = tk.Button(self.rapor_cerceve, text="KDV ve kâr / zarar raporu", command=self._kdvkar_raporu, relief="flat", bd=0,
                                 cursor="hand2", font=(YAZI, 10, "bold"), bg=R["kart"], fg=R["lacivert"], activebackground=R["secili_z"],
                                 activeforeground=R["lacivert"], disabledforeground=R["acik_gri"], pady=5)
        self.b_rapor.pack(fill="x", padx=1, pady=1)

    # ------------------------------------------------------------ sağ sütun
    def _sag_sutun(self, sag):
        dis, k = kart(sag)
        dis.pack(fill="x")
        baslik(k, "İNDİRİLECEKLER").pack(anchor="w", pady=(0, 6))
        h = tk.Frame(k, bg=R["kart"])
        h.pack(fill="x")
        tk.Label(h, text="Hazır seçimler:", bg=R["kart"], fg=R["gri"], font=(YAZI, 8)).pack(side="left", padx=(0, 6))
        for ad, anahtarlar in HAZIR:
            dugme(h, ad, lambda a=anahtarlar: self._hazir_sec(a), kucuk=True).pack(side="left", padx=(0, 5))
        # kullanıcının kendi seçimi (kullanıcı isteği 20.11.2026): kaydedilir, sonraki açılışta kendiliğinden gelir
        h2 = tk.Frame(k, bg=R["kart"])
        h2.pack(fill="x", pady=(5, 0))
        tk.Label(h2, text="Kendi seçimim:", bg=R["kart"], fg=R["gri"], font=(YAZI, 8)).pack(side="left", padx=(0, 6))
        dugme(h2, "Bu seçimi kaydet", self._secim_kaydet, kucuk=True).pack(side="left", padx=(0, 5))
        self.b_kayitli = dugme(h2, "Kayıtlı seçimimi getir", self._secim_getir, kucuk=True)
        self.b_kayitli.pack(side="left", padx=(0, 5))
        self.b_kayit_sil = dugme(h2, "Kaydı sil", self._secim_kaydi_sil, kucuk=True)
        self.b_kayit_sil.pack(side="left", padx=(0, 8))
        self.kayit_yazi = tk.Label(h2, text="", bg=R["kart"], fg=R["acik_gri"], font=(YAZI, 8))
        self.kayit_yazi.pack(side="left")
        il = tk.Frame(k, bg=R["kart"])
        il.pack(fill="x", pady=(8, 2))
        self.il_sure = tk.Label(il, text="", bg=R["kart"], fg=R["yok"], font=(YAZI, 9))
        self.il_sure.pack(side="right")
        self.il_hata = tk.Label(il, text="", bg=R["kart"], fg=R["kirmizi"], font=(YAZI, 9, "bold"))
        self.il_hata.pack(side="right", padx=(0, 10))
        self.il_yazi = tk.Label(il, text="", bg=R["kart"], fg=R["yazi"], font=(YAZI, 9, "bold"))
        self.il_yazi.pack(side="right", padx=(10, 10))
        self.cubuk = tk.Canvas(il, height=12, bg="#e3e7ed", highlightthickness=1, highlightbackground=R["dugme_k"])
        self.cubuk.pack(side="left", fill="x", expand=True)
        self.cubuk.bind("<Configure>", lambda e: self._ilerleme())

        # Kayıtlı seçim varsa açılışta o gelir; kayıttan sonra programa eklenen sorgular varsayılan değerleriyle gelir.
        self.secim = {k_: tk.BooleanVar(value=v) for k_, v in secim_baslangici(self.kayitli_secim).items()}
        self.grup_var, self.kapsam_yazi, self.durum_yazilari, self.kutular = [], {}, {}, {}
        g = tk.Frame(k, bg=R["kart"])
        g.pack(fill="x", pady=(6, 0))
        adet = 3 if self.uc_sutun else 2
        sutunlar = [tk.Frame(g, bg=R["kart"]) for _ in range(adet)]
        for n, s in enumerate(sutunlar):
            s.pack(side="left", fill="both", expand=True, padx=(0 if n == 0 else 9, 0 if n == adet - 1 else 9), anchor="n")
        for no, (ad, anahtarlar) in enumerate(GRUPLAR):
            if self.uc_sutun:
                s = sutunlar[next((n for n, grup in enumerate(UC_SUTUN) if no in grup), adet - 1)]
            else:
                s = sutunlar[0 if no in SOL_GRUPLAR else 1]
            gv = tk.BooleanVar(value=True)
            self.grup_var.append(gv)
            bas = tk.Frame(s, bg=R["kart"])
            bas.pack(fill="x", pady=(5, 0))
            tk.Checkbutton(bas, text=ad.upper(), variable=gv, command=lambda n=no: self._grup_sec(n), bg=R["kart"], fg=R["lacivert"],
                           activebackground=R["kart"], activeforeground=R["lacivert"], font=(YAZI, 8, "bold"), bd=0,
                           highlightthickness=0, anchor="w").pack(side="left")
            tk.Label(bas, text=f"{len(anahtarlar)} sorgu", bg=R["kart"], fg=R["acik_gri"], font=(YAZI, 8)).pack(side="right")
            tk.Frame(s, bg=R["cizgi"], height=1).pack(fill="x", pady=(1, 2))
            for a in anahtarlar:
                r = tk.Frame(s, bg=R["kart"])
                r.pack(fill="x")
                kutu = tk.Checkbutton(r, text=KISA_AD.get(a, a), variable=self.secim[a], bg=R["kart"], activebackground=R["kart"],
                                      font=(YAZI, 9), bd=0, highlightthickness=0, anchor="w", pady=0)
                kutu.pack(side="left")
                self.kutular[a] = kutu
                dy = tk.Label(r, text="", bg=R["kart"], font=(YAZI, 8), padx=6, cursor="hand2")
                dy.pack(side="right")                   # sonuç kutusu kapsam yazısından ÖNCE yerleşir: yer daralırsa kapsam kısalır
                dy.bind("<Button-1>", lambda e, a_=a: self._sonuc_ayrinti(a_))
                self.durum_yazilari[a] = dy
                kp = tk.Label(r, text="", bg=R["kart"], fg=R["acik_gri"], font=(YAZI, 8))
                kp.pack(side="left", padx=(2, 0))
                self.kapsam_yazi[a] = kp
        for v in self.secim.values():
            v.trace_add("write", lambda *_: self._secim_degisti())

        # alt panel: günlük / sonuç
        dis, k = kart(sag, ic_pay=(12, 8))
        dis.pack(fill="both", expand=True, pady=(10, 0))
        ab = tk.Frame(k, bg=R["kart"])
        ab.pack(fill="x", pady=(0, 4))
        self.alt_baslik = tk.Label(ab, text="GÜNLÜK", bg=R["kart"], fg=R["lacivert"], font=(YAZI, 9, "bold"))
        self.alt_baslik.pack(side="left")
        self.alt_gecis = tk.Label(ab, text="", bg=R["kart"], fg=R["mavi"], cursor="hand2", font=(YAZI, 9))
        self.alt_gecis.pack(side="right")
        self.alt_gecis.bind("<Button-1>", lambda e: self._alt_goster("gunluk" if self.alt_gorunen == "sonuc" else "sonuc"))
        self.alt_icerik = tk.Frame(k, bg=R["kart"])
        self.alt_icerik.pack(fill="both", expand=True)
        self.gunluk = scrolledtext.ScrolledText(self.alt_icerik, height=4, font=("Consolas", 9), state="disabled", wrap="word",
                                                relief="flat", bd=0, bg="#fbfcfd", fg=R["yazi"])
        self.gunluk.tag_config("hata", foreground=R["kirmizi"])
        self.gunluk.tag_config("uyari", foreground=R["sari"])
        self.gunluk.tag_config("baslik", font=("Consolas", 9, "bold"))
        self.gunluk.tag_config("zaman", foreground=R["acik_gri"])
        self.sonuc = tk.Frame(self.alt_icerik, bg=R["kart"])
        self.alt_gorunen = None
        self._alt_goster("gunluk")
        self._secim_degisti()
        self._kayit_goster()

    # ------------------------------------------------------------ seçimler ve ilerleme
    def _grup_sec(self, no):
        deger = self.grup_var[no].get()
        for a in GRUPLAR[no][1]:
            self.secim[a].set(deger)

    def _hazir_sec(self, anahtarlar):
        for a, v in self.secim.items():
            v.set(a in anahtarlar)

    def _kayit_goster(self):
        """'Kendi seçimim' satırındaki bilgi yazısı ve düğmelerin durumu."""
        ks = self.kayitli_secim
        if ks:
            self.kayit_yazi.configure(text=f"Kayıtlı: {len(ks['secili'])} sorgu" + (f" ({ks['tarih']})" if ks.get("tarih") else "")
                                      + " — program bu seçimle açılır")
        else:
            self.kayit_yazi.configure(text="Kayıt yok — program varsayılan seçimle açılır")
        for b in (self.b_kayitli, self.b_kayit_sil):
            b.d.configure(state="normal" if ks else "disabled")

    def _secim_kaydet(self):
        if self.komut_onceki is not None:           # komut (komut.py) işaretleri geçici olarak değiştirmiştir
            messagebox.showinfo("Seçimi kaydet", "Dışarıdan verilen bir komut çalışıyor; işaretler geçici. Bitince yeniden deneyin.",
                                parent=self.kok)
            return
        secili = [a for a, _ in SORGULAR if self.secim[a].get()]
        if not secili:
            messagebox.showinfo("Seçimi kaydet", "Hiç sorgu seçili değil; önce indirilecekleri işaretleyin.", parent=self.kok)
            return
        kayit = {"secili": secili, "bilinen": [a for a, _ in SORGULAR], "earsiv_gib": bool(self.earsiv_gib.get()),
                 "pdf": bool(self.pdf.get()), "tarih": datetime.now().strftime("%d.%m.%Y")}
        try:
            self.ayarlar = ayar.ayar_yaz(**{SECIM_ANAHTARI: kayit})
        except OSError as e:
            messagebox.showerror("Seçimi kaydet", f"Seçim kaydedilemedi: {e}", parent=self.kok)
            return
        self.kayitli_secim = secim_kaydi_oku(self.ayarlar)
        self._kayit_goster()
        self._yaz(f"İndirilecekler seçimi kaydedildi ({len(secili)} sorgu); program bundan sonra bu seçimle açılır.")

    def _secim_getir(self):
        ks = self.kayitli_secim
        if not ks:
            return
        for a, v in secim_baslangici(ks).items():
            self.secim[a].set(v)
        self.earsiv_gib.set(ks.get("earsiv_gib", True))
        self.pdf.set(ks.get("pdf", False))

    def _secim_kaydi_sil(self):
        if not self.kayitli_secim or not messagebox.askyesno(
                "Kaydı sil", "Kayıtlı seçim silinsin mi?\n\nProgram bundan sonra varsayılan seçimle açılır (şu anki işaretler değişmez).",
                default="no", parent=self.kok):
            return
        try:
            self.ayarlar = ayar.ayar_yaz(**{SECIM_ANAHTARI: None})
        except OSError as e:
            messagebox.showerror("Kaydı sil", f"Kayıt silinemedi: {e}", parent=self.kok)
            return
        self.kayitli_secim = None
        self._kayit_goster()
        self._yaz("Kayıtlı indirilecekler seçimi silindi.")

    def _secim_degisti(self):
        for no, (_, anahtarlar) in enumerate(GRUPLAR):
            hepsi = all(self.secim[a].get() for a in anahtarlar)
            if self.grup_var[no].get() != hepsi:
                self.grup_var[no].set(hepsi)
        self._dugme_guncelle()

    def _dugme_guncelle(self):
        if not hasattr(self, "b_basla") or not hasattr(self, "secim"):
            return
        self.b_beyan.configure(state="disabled" if (self.calisiyor or self.kosu or self.beyan_kosu) else "normal")
        if hasattr(self, "b_aktar"):
            self.b_aktar.configure(state="disabled" if (self.calisiyor or self.kosu or self.beyan_kosu) else "normal")
        if hasattr(self, "b_rapor"):
            self.b_rapor.configure(state="disabled" if (self.calisiyor or self.kosu or self.beyan_kosu) else "normal")
        if getattr(self, "b_toplu", None) is not None:
            try:
                self.b_toplu.configure(state="disabled" if (self.calisiyor or self.kosu or self.beyan_kosu or self.toplu) else "normal")
            except tk.TclError:
                pass
        if self.kosu or self.beyan_kosu:
            self.basla_cerceve.configure(bg=R["kirmizi"])
            self.b_basla.configure(text="■  Durdur", bg=R["kart"], fg=R["kirmizi"], activebackground=R["kirmizi_z"],
                                   activeforeground=R["kirmizi"], state="normal")
        else:
            n = sum(v.get() for v in self.secim.values())
            self.basla_cerceve.configure(bg=R["yesil"])
            self.b_basla.configure(text=f"▶  Başlat   ({n} sorgu)", bg=R["yesil"], fg="white", activebackground="#22994a",
                                   activeforeground="white", state="disabled" if self.calisiyor else "normal")

    def _durum_yaz(self, a):
        dy = self.durum_yazilari[a]
        durum = self.sorgu_durumu.get(a) if self._canli() else None
        bilgi = self.sorgu_bilgi.get(a, {})
        if durum not in DURUM_STIL:
            o = self._onceki_sonuc(a)
            if o is None:
                dy.configure(text="", bg=R["kart"])
                return
            renk, sembol = ONCEKI_STIL.get(o["durum"], (R["acik_gri"], "–"))     # önceki indirme: tarihli, zeminsiz, eğik
            ek = ""
            if o["durum"] == "veri" and o.get("acil"):
                renk, ek = R["kirmizi"], " · ACİL"
            if o.get("son_deneme"):                 # son denemesi başarısız, eski veri
                renk, sembol = R["sari"], "⚠"
            dy.configure(text=f"{sembol} {o['kisa']}{ek} · {o['gun']}", fg=renk, bg=R["kart"], font=(YAZI, 8, "italic"))
            return
        stil = "acil" if (durum == "veri" and bilgi.get("acil")) else durum
        renk, zemin, sembol = DURUM_STIL[stil]
        yazi = bilgi.get("kisa") or {"bekliyor": "bekliyor", "calisiyor": "sorgulanıyor…"}.get(durum, durum)
        if stil == "acil":
            yazi += " · ACİL"
        dy.configure(text=f"{sembol} {yazi}", fg=renk, bg=zemin, font=(YAZI, 8, "bold" if stil in ("hata", "calisiyor", "acil") else "normal"))

    def _durumlari_ciz(self):
        for a in self.durum_yazilari:
            self._durum_yaz(a)
        self._ilerleme()

    def _canli(self):
        """Sorgu kutularında son Başlat'ın canlı sonuçları mı gösterilir: çalışırken her zaman; sonra yalnız aynı firma/dönem seçiliyse."""
        return bool(self.kosu) or (self.kosu_hedef is not None and self.kosu_hedef == (self.secili_id, self.ay_no, self.yil_no))

    def _onceki_hedefi(self):
        t = self._secili()
        if not t or not t.get("klasor") or not self._kok_var():
            return None
        return (t["id"], t["klasor"], self.ay_no, self.yil_no, str(cikti.FIRMALAR))     # kök de hedefin parçası: değişince eski okuma geçersiz

    def _onceki_sonuc(self, a):
        v = self.onceki
        if not v or v.get("hedef") != self._onceki_hedefi() or not isinstance(v.get("sonuclar"), dict):
            return None
        if self.kosu and self.kosu_hedef != (self.secili_id, self.ay_no, self.yil_no):
            return None                             # başka firma/dönem için çalışma sürüyor: kutular onun canlı sonuçlarındadır
        return v["sonuclar"].get(a)

    def _onceki_yenile(self, gecikme=150):
        """Seçilen firma/dönemin önceki indirmelerini arka planda okutur (dosya okuma arayüzü dondurmasın)."""
        if not hasattr(self, "onceki_cubuk") or not hasattr(self, "cubuk"):
            return                                  # arayüz kurulurken
        hedef = self._onceki_hedefi()
        if self.onceki is not None and self.onceki.get("hedef") != hedef:
            self.onceki = None                      # başka firma/dönem: eski not ve önceki sonuçlar hemen kalkar
        self._onceki_goster()
        self._durumlari_ciz()
        if self.onceki_zamanlayici is not None:
            try:
                self.kok.after_cancel(self.onceki_zamanlayici)
            except tk.TclError:
                pass
            self.onceki_zamanlayici = None
        if hedef is None:
            return
        self.onceki_sira += 1
        sira = self.onceki_sira
        self.onceki_zamanlayici = self.kok.after(gecikme, lambda: self._onceki_baslat(sira, hedef))

    def _onceki_baslat(self, sira, hedef):
        self.onceki_zamanlayici = None
        if sira != self.onceki_sira:
            return
        kuyruk = self.olaylar

        def calis():
            with _ONCEKI_KILIT:                     # aynı anda tek okuma; sırası gelene kadar eskimiş istek hiç okumaz
                if sira != self.onceki_sira:
                    return
                try:
                    veri = onceki_durumu(hedef[1], hedef[2], hedef[3])
                except Exception as e:              # okuma hatası notta görünür; program çalışmayı sürdürür
                    veri = {"okuma_hatasi": f"{type(e).__name__}: {str(e)[:150]}"}
                kuyruk.put(("onceki", {**veri, "sira": sira, "hedef": hedef}))
        threading.Thread(target=calis, name="onceki-indirme", daemon=True).start()

    def _onceki_goster(self):
        """Dönem kartındaki 'önceki indirme' notu."""
        hedef = self._onceki_hedefi()
        if hedef is None:
            self.onceki_cubuk.pack_forget()
            return
        v = self.onceki if (self.onceki and self.onceki.get("hedef") == hedef) else None
        yollar, metin = {}, ""
        s = (v.get("sonuclar") or {}) if v else {}
        basarili = sum(1 for o in s.values() if o["durum"] in ("veri", "kayit_yok"))
        hata = sum(1 for o in s.values() if o["durum"] == "hata")
        if v is None:
            renk, zemin, bas = R["dugme_k"], R["yok_z"], "Önceki indirmelere bakılıyor…"
        elif v.get("okuma_hatasi"):
            renk, zemin, bas, metin = R["sari"], R["sari_z"], "Önceki indirmeler okunamadı", v["okuma_hatasi"]
        elif basarili or hata:                      # en az bir sorgu gerçekten çalışmış (veri / kayıt yok / hata)
            toplam = len(SORGULAR)
            eski = sum(1 for o in s.values() if o.get("son_deneme"))
            acil = sum(1 for o in s.values() if o.get("acil"))
            parca = [f"{basarili}/{toplam} sorgunun sonucu var"]
            if hata:
                parca.append(f"{hata} hata")
            if toplam - basarili - hata:
                parca.append(f"{toplam - basarili - hata} sorgu hiç alınmadı")
            if v.get("fatura"):
                parca.append(f"{v['fatura']} fatura")
            metin = f"En son kayıtlı sorgu {v.get('son') or '—'} · {v.get('kayit', 0)} sonuç kaydı\n" + " · ".join(parca)
            if eski:
                metin += f"\n{eski} sorgunun son denemesi başarısız (eski veri gösteriliyor)"
            if acil:
                metin += f"\nACİL uyarı: {acil} sorguda"
            if not v.get("beyanname"):
                metin += "\nVerilen beyanname henüz indirilmedi."
            renk, zemin = (R["sari"], R["sari_z"]) if (hata or eski or acil) else (R["mavi"], R["mavi_z"])
            bas = "Bu dönem daha önce indirildi"
            yollar = {"ozet": v.get("ozet") or "", "klasor": v.get("donem_klasoru") or ""}
        elif s:                                     # sonuç kaydı var ama hiçbir sorgu çalışmamış (hepsi 'çalıştırılmadı')
            renk, zemin, bas = R["sari"], R["sari_z"], "Önceki çalıştırmalarda veri alınamadı"
            metin = f"{len(s)} sorgu çalıştırılamadı (giriş yoktu ya da durduruldu)."
            if v.get("dosya"):
                metin += f" Dönem klasöründe {v['dosya']} dosya var."
            yollar = {"klasor": v.get("donem_klasoru") or ""}
        elif v.get("dosya"):
            renk, zemin, bas = R["sari"], R["sari_z"], "Dönem klasöründe dosya var"
            metin = f"{v['dosya']} dosya var ama programla indirme kaydı yok (elle konmuş olabilir)."
            yollar = {"klasor": v.get("donem_klasoru") or ""}
        else:
            renk, zemin, bas = R["dugme_k"], R["yok_z"], "Bu dönem için daha önce indirme yok"
        if v and v.get("beyanname"):                # dönemin beyanname PDF'i klasörde (düğmeyle ya da sonraki dönemin sorgusuyla inmiş)
            metin += ("\n" if metin else "") + "Verilen beyanname indirilmiş: " + ", ".join(v["beyanname"])
        self.onceki_yollar = yollar
        self.onceki_cubuk.configure(bg=renk)
        for w in (self.onceki_kutu, self.onceki_ust, self.onceki_baslik, self.onceki_l_klasor, self.onceki_l_ozet, self.onceki_yazi, self.onceki_alt):
            w.configure(bg=zemin)
        self.onceki_baslik.configure(text=bas)
        self.onceki_yazi.configure(text=metin)
        for ad, l in (("klasor", self.onceki_l_klasor), ("ozet", self.onceki_l_ozet)):
            if yollar.get(ad):
                if not l.winfo_manager():
                    l.pack(side="right", padx=(8, 0))
            else:
                l.pack_forget()
        if metin and not self.onceki_yazi.winfo_manager():
            self.onceki_yazi.pack(fill="x", padx=8, pady=(1, 0), before=self.onceki_alt)
        elif not metin:
            self.onceki_yazi.pack_forget()
        if not self.onceki_cubuk.winfo_manager():
            self.onceki_cubuk.pack(fill="x", pady=(8, 0))

    def _durumlari_sifirla(self):
        self.sorgu_durumu.clear()
        self.sorgu_bilgi.clear()
        for a in self.durum_yazilari:
            self._durum_yaz(a)
        self._ilerleme()

    def _ilerleme(self):
        d = list(self.sorgu_durumu.values()) if self._canli() else []
        toplam = len(d)
        c = self.cubuk
        c.delete("all")
        if not toplam:
            self.il_yazi.configure(text="")
            self.il_hata.configure(text="")
            return
        en, boy = max(1, c.winfo_width()), c.winfo_height()
        x = 0
        for renk, adet in ((R["yesil"], sum(v in ("veri", "kayit_yok") for v in d)), (R["kirmizi"], d.count("hata")),
                           ("#9aa3af", d.count("calistirilmadi")), (R["mavi"], d.count("calisiyor"))):
            if adet:
                g = en * adet / toplam
                c.create_rectangle(x, 0, x + g, boy, fill=renk, outline="")
                x += g
        biten = sum(v in BITEN for v in d)
        self.il_yazi.configure(text=f"{biten} / {toplam} tamamlandı")
        self.il_hata.configure(text=f"{d.count('hata')} hata" if d.count("hata") else "")

    def _sonuc_ayrinti(self, a):
        canli = self._canli() and a in self.sorgu_durumu
        bilgi = self.sorgu_bilgi.get(a) if canli else None
        if bilgi and bilgi.get("mesaj"):
            messagebox.showinfo(KISA_AD.get(a, a), (f"Kapsam: {bilgi['kapsam']}\n\n" if bilgi.get("kapsam") else "") + bilgi["mesaj"], parent=self.kok)
            return
        o = None if canli else self._onceki_sonuc(a)
        if o:
            son = o.get("son_deneme") if isinstance(o.get("son_deneme"), dict) else None
            metin = (f"Önceki indirme: {o['zaman'] or '—'} (bu dönemin kayıtlı sonuçlarından)\n"
                     + (f"Kapsam: {o['kapsam']}\n" if o.get("kapsam") else "") + "\n" + (o["mesaj"] or "—"))
            if son:
                metin += (f"\n\nSON DENEME BAŞARISIZ ({son.get('zaman', '')}): {son.get('mesaj', '')}\n"
                          "Yukarıdaki veri bundan önceki başarılı sorgudandır; güncel değilse sorguyu yeniden çalıştırın.")
            messagebox.showinfo(KISA_AD.get(a, a) + " — önceki indirme", metin, parent=self.kok)

    def _saniye(self):
        try:
            self._saniye_isle()
            self._komut_bak()
            self._durum_dosyasi_yaz()
        except Exception as e:                      # zamanlayıcı hiçbir hatada durmasın
            try:
                self._yaz(f"UYARI: zamanlayıcı hatası: {type(e).__name__}: {str(e)[:150]}")
            except Exception:
                pass
        finally:
            self.kok.after(1000, self._saniye)

    def _saniye_isle(self):
        if self.kosu and self.kosu_baslangic:
            s = int(time.time() - self.kosu_baslangic)
            self.il_sure.configure(text=f"geçen süre {s // 60:02d}:{s % 60:02d}")
        # arka plan giriş denetimi: 5 dk'da bir (durum göstergesi; oturum da açık kalır). Görünür tarayıcıda elle giriş beklenirken 5 sn.
        # Güvenlik: programda OTURUM_SAAT saattir işlem yapılmadıysa denetim durur; oturum E-Mükellef'in kendi süresiyle kapanır.
        aralik = 5 if (self.gorunur and (self.emk is False or (self.luca_elle and self.luca is not True))) else 300
        etkin = time.time() - self.son_etkinlik < OTURUM_SAAT * 3600
        if (etkin and not self.calisiyor and not self.toplu and self.giris_p is None and self.luca_p is None and not self.kok_soruluyor
                and time.time() - self.son_kontrol >= aralik):
            self.son_kontrol = time.time()
            self.isci.gonder("giris_kontrol", sessiz=True)

    # ------------------------------------------------------------ komut kanalı (komut.py)
    # Claude KDV kontrolünde eksik veri görürse komut.py ile istek bırakır; program isteği KENDİ penceresinde, kullanıcı Başlat'a
    # basmış gibi çalıştırır (aynı oturumlar, aynı kayıt kuralları). Giriş gerekiyorsa istek reddedilir; girişi kullanıcı yapar.
    def _durum_dosyasi_yaz(self, zorla=False):
        if not zorla and time.time() - self.durum_dosya_zamani < 3:
            return
        self.durum_dosya_zamani = time.time()
        pencere = "E-Mükellef girişi" if self.giris_p is not None else ("Luca girişi" if (self.luca_p is not None or self.luca_elle) else None)
        try:
            json_yaz(KOMUT_DURUM, {"zaman": time.time(), "saat": datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "pid": os.getpid(),
                                   "emukellef": self.emk, "luca": self.luca, "gorunur": self.gorunur, "calisiyor": self.calisiyor,
                                   "pencere": pencere, "komut": self.komut, "gecmis": self.komut_gecmis})
        except OSError:
            pass                                    # okuyan o an dosyayı tutuyor olabilir; bir sonraki turda yazılır

    def _komut_bak(self):
        if (self.calisiyor or self.kosu or self.beyan_kosu or self.toplu or self.giris_p is not None or self.luca_p is not None
                or self.luca_elle):
            return
        if self.kok.grab_current() is not None:     # açık bir soru penceresi var
            return
        kuyruk = getattr(self.isci, "gorevler", None)
        if (kuyruk is not None and not kuyruk.empty()) or not self.olaylar.empty():
            return                                  # işçide bekleyen görev / işlenmemiş olay varken başlama
        try:
            if not KOMUT_ISTEK.is_file():
                return
            ham = KOMUT_ISTEK.read_text(encoding="utf-8")
            KOMUT_ISTEK.unlink()
        except OSError:
            return
        try:
            istek = json.loads(ham)
        except ValueError:
            istek = None
        self._komut_calistir(istek)

    def _komut_calistir(self, istek):
        kid = re.sub(r"[^0-9A-Za-z_-]", "", str(istek.get("id") or ""))[:40] if isinstance(istek, dict) else ""
        simdi = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        def reddet(neden):
            self.komut = {"id": kid, "durum": "reddedildi", "neden": neden, "bitis_saati": simdi}
            self._komut_gecmise_yaz()
            self._yaz(f"Komut reddedildi: {neden}")
            self._durum_dosyasi_yaz(zorla=True)
        if not isinstance(istek, dict) or not kid:
            return reddet("istek dosyası okunamadı")
        if not self._kok_var():                     # komut kullanıcıya soru soramaz
            return reddet("kayıt klasörü seçilmemiş ya da erişilemiyor; kullanıcı programda 'Kayıt klasörü'nü düzeltmeli")
        f = istek.get("firma")
        adaylar = [x for x in self.tum if (isinstance(f, int) and not isinstance(f, bool) and x["id"] == f)
                   or (isinstance(f, str) and f.strip() and (str(x["id"]) == f.strip() or norm(f) in (norm(x.get("ad") or "-"), norm(x.get("klasor") or "-"))))]
        if len(adaylar) != 1:
            return reddet(f"firma bulunamadı ya da birden fazla eşleşti: {str(f)[:60]}")
        firma = adaylar[0]
        if not firma.get("klasor"):
            return reddet("firma klasörü tanımlı değil (kullanıcı programda firmayı seçip bir kez Başlat'a basarak tanımlar)")
        ay, yil = istek.get("ay"), istek.get("yil")
        if not (isinstance(ay, int) and isinstance(yil, int) and 1 <= ay <= 12 and 2020 <= yil <= 2100):
            return reddet("dönem geçersiz")
        gecerli = [k for k, _ in SORGULAR]
        sorgular = istek.get("sorgular")
        if not isinstance(sorgular, list) or not sorgular or any(k not in gecerli for k in sorgular):
            return reddet("sorgu listesi boş ya da bilinmeyen sorgu içeriyor (komut.py sorgular)")
        sorgular = [k for k in gecerli if k in sorgular]
        luca_var = any(k in LUCA_SORGULARI for k in sorgular)
        if any(k not in LUCA_SORGULARI for k in sorgular) and self.emk is not True:
            return reddet("E-Mükellef girişi yok; girişi kullanıcı programda yapmalı")
        if luca_var and self.luca is not True:
            return reddet("Luca girişi yok; kullanıcı programda 'Luca girişi' ile tarayıcıdan giriş yapmalı (ya da isteği Luca sorguları olmadan verin)")
        if luca_var and not firma.get("luca"):
            return reddet("bu firma için Luca firması tanımlı değil (kullanıcı programda Luca sorgusuyla bir kez Başlat'a basarak tanımlar)")
        # arayüz, kullanıcı Başlat'a basmış gibi ayarlanır; işaretler koşu bitince eski hâline döner
        self.komut_onceki = {"secim": {k: v.get() for k, v in self.secim.items()}, "gib": self.earsiv_gib.get(), "pdf": self.pdf.get(),
                             "firma": self.secili_id, "donem": (self.ay_no, self.yil_no), "ara": self.ara.get()}
        self.ara.delete(0, "end")
        self._suz()
        self._firma_sec(firma["id"])
        self.ay_no, self.yil_no = ay, yil
        self._donem_guncelle()
        self._hazir_sec(sorgular)
        self.earsiv_gib.set(bool(istek.get("earsiv_gib", True)))
        self.pdf.set(bool(istek.get("pdf", False)))
        self.komut_ayar = {"secim": {k: v.get() for k, v in self.secim.items()}, "gib": self.earsiv_gib.get(), "pdf": self.pdf.get(),
                           "firma": firma["id"], "donem": (ay, yil)}
        self.komut = {"id": kid, "durum": "calisiyor", "firma": firma.get("ad"), "donem": f"{cikti.AYLAR[ay - 1]} {yil}",
                      "istenen": sorgular, "baslangic": simdi}
        self.son_hata = ""
        self._yaz(f"Komut alındı (komut.py): {firma.get('ad')} — {cikti.AYLAR[ay - 1]} {yil} — {len(sorgular)} sorgu: {', '.join(sorgular)}")
        self.komut_sira = None
        self._baslat()
        if not self.kosu:
            self._komut_bitir("hata", "çalıştırma başlatılamadı (program günlüğüne bakın)")
        else:
            self.komut_sira = getattr(self.isci, "sira", None)      # az önce gönderilen 'baslat' görevinin sırası
        self._durum_dosyasi_yaz(zorla=True)

    def _komut_bitir(self, durum, neden=None, bitis=None):
        """Çalışan komutu kapatır: sonuçları durum.json'a yazar, kullanıcının önceki işaretlerini geri yükler."""
        k = self.komut or {}
        sorgular = {}
        for a in k.get("istenen") or []:
            b = self.sorgu_bilgi.get(a) or {}
            sorgular[a] = {"durum": self.sorgu_durumu.get(a) or "calistirilmadi", "kisa": b.get("kisa") or "",
                           "mesaj": str(b.get("mesaj") or "")[:600], "kapsam": b.get("kapsam") or ""}
        k.update({"durum": durum, "bitis_saati": datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "sorgular": sorgular})
        if neden:
            k["neden"] = neden
        if bitis:
            k["bitis"] = {a: bitis.get(a) for a in ("durdu", "sayim", "eksik", "fatura_sayisi", "acil", "hatalar", "ozet", "dokum",
                                                    "donem_klasoru", "tebligat_klasoru")}
        self.komut = k
        self.komut_sira = None
        self._komut_gecmise_yaz()
        # kullanıcının komuttan önceki seçimleri geri gelir; koşu sırasında kendisi değiştirdiyse onun değişikliği kalır
        o, c, self.komut_onceki, self.komut_ayar = self.komut_onceki, self.komut_ayar, None, None
        if o and c:
            for a, v in o["secim"].items():
                if a in self.secim and self.secim[a].get() == c["secim"].get(a):
                    self.secim[a].set(v)
            if self.earsiv_gib.get() == c["gib"]:
                self.earsiv_gib.set(o["gib"])
            if self.pdf.get() == c["pdf"]:
                self.pdf.set(o["pdf"])
            if self.secili_id == c["firma"] and (self.ay_no, self.yil_no) == c["donem"] and not self.ara.get():
                self.ay_no, self.yil_no = o["donem"]
                self.ara.insert(0, o["ara"])
                self.secili_id = o["firma"]
                self._suz()
                self._donem_guncelle()              # firma kutusunu da yeniler
        self._yaz(f"Komut tamamlandı ({k.get('id')}): {durum}" + (f" — {neden}" if neden else "")
                  + ". Firma, dönem ve işaretler komuttan önceki hâline döndürüldü.")
        self._durum_dosyasi_yaz(zorla=True)

    def _komut_gecmise_yaz(self):
        k = self.komut or {}
        if k.get("id"):
            self.komut_gecmis[k["id"]] = k
            for eski in list(self.komut_gecmis)[:-5]:   # son 5 komut tutulur
                del self.komut_gecmis[eski]

    # ------------------------------------------------------------ firma listesi
    def _listeyi_kur(self):
        """E-Mükellef önbelleği (tam liste) + firmalar.json (klasör eşlemesi) birleşimi."""
        veri, self.eslesme_hatasi = eslesme_oku()
        eslesme = {}
        for x in veri.get("firmalar", []):
            try:
                if isinstance(x, dict) and x.get("emukellef_id"):
                    eslesme[int(x["emukellef_id"])] = x
            except (TypeError, ValueError):
                continue
        onbellek = json_oku(MUKELLEF_ONBELLEK, {}).get("liste", [])
        kok_var = self._kok_var()

        def klasor(e):
            """(klasör, öneri): eşlemedeki klasör bu bilgisayarın kayıt klasöründe yoksa tanımsız sayılır, adı öneri olur
            (başka bilgisayardan gelen eşleme). Kayıt klasörüne erişilemiyorken eşleme olduğu gibi kalır."""
            k = e.get("klasor")
            if not k or not kok_var:
                return k, None
            try:
                var = (Path(cikti.FIRMALAR) / str(k)).is_dir()
            except OSError:
                var = False
            return (k, None) if var else (None, k)
        tum, gorulen = [], set()
        for m in onbellek:
            e = eslesme.get(int(m["id"]), {})
            k, oneri = klasor(e)
            tum.append({"id": int(m["id"]), "unvan": m["unvan"], "vkn": m.get("vkn", ""), "ad": e.get("ad"), "klasor": k, "klasor_oneri": oneri,
                        "luca": str(e.get("luca_sirket_id") or "")})
            gorulen.add(int(m["id"]))
        for mid, e in eslesme.items():
            if mid not in gorulen:
                k, oneri = klasor(e)
                tum.append({"id": mid, "unvan": e.get("unvan") or e.get("ad") or str(mid), "vkn": e.get("vkn", ""), "ad": e.get("ad"), "klasor": k,
                            "klasor_oneri": oneri, "luca": str(e.get("luca_sirket_id") or "")})
        for t in tum:
            t["goster"] = unvan_sade(t["unvan"]) + (f"   ({t['vkn']})" if t["vkn"] else "") + ("" if t.get("klasor") else "   — klasör tanımsız")
            t["anahtar"] = norm(t["unvan"] + " " + t["vkn"] + " " + (t.get("ad") or ""))
        self.tum = sorted(tum, key=lambda t: norm(unvan_sade(t["unvan"])))
        self.onbellek_adet = len(onbellek)

    def _sonlari_ciz(self):
        for w in self.sonlar.winfo_children():
            w.destroy()
        idler = [i for i in self.ayarlar.get("son_firmalar", []) if any(t["id"] == i for t in self.tum)][:6]
        if not idler:
            tk.Label(self.sonlar, text="Son kullanılan firmalar burada görünür.", bg=R["kart"], fg=R["acik_gri"], font=(YAZI, 8)).grid(row=0, column=0, sticky="w")
            return
        tk.Label(self.sonlar, text="Son kullanılanlar:", bg=R["kart"], fg=R["gri"], font=(YAZI, 8)).grid(row=0, column=0, columnspan=3, sticky="w")
        for n, i in enumerate(idler):
            t = next(t for t in self.tum if t["id"] == i)
            secili = i == self.secili_id
            ad = (t.get("ad") or kisa_ad_oner(t["unvan"]))[:16]
            b = tk.Button(self.sonlar, text=ad, command=lambda i_=i: self._firma_sec(i_), relief="flat", bd=0, cursor="hand2", font=(YAZI, 8),
                          bg=R["lacivert"] if secili else R["dugme_z"], fg="white" if secili else R["lacivert"],
                          activebackground=R["secili_z"], padx=6, pady=1)
            b.grid(row=1 + n // 3, column=n % 3, sticky="we", padx=(0, 4), pady=(3, 0))
        for c in range(3):
            self.sonlar.grid_columnconfigure(c, weight=1, uniform="son")

    def _suz(self, event=None):
        if event is not None and event.keysym in ("Down", "Up", "Return"):
            return
        parcalar = norm(self.ara.get().strip()).split()
        self.gorunen = [t for t in self.tum if all(p in t["anahtar"] for p in parcalar)]
        self.liste.delete(0, "end")
        for n, t in enumerate(self.gorunen):
            self.liste.insert("end", t["goster"])
            if t["id"] == self.secili_id:
                self.liste.selection_set(n)
        kaynak = "E-Mükellef listesi" if self.onbellek_adet else "kayıtlı firmalar — tam liste giriş yapınca gelir"
        self.liste_bilgi.configure(text=f"{len(self.gorunen)} / {len(self.tum)} firma · {kaynak}")
        self._sonlari_ciz()
        self._sik_ciz()

    def _ilkini_sec(self):
        if self.gorunen:
            self._firma_sec(self.gorunen[0]["id"])

    def _listeden_sec(self, event=None):
        s = self.liste.curselection()
        if s:
            self._firma_sec(self.gorunen[s[0]]["id"], listeyi_elleme=True)

    def _firma_sec(self, mid, listeyi_elleme=False):
        self.secili_id = mid
        if not listeyi_elleme:
            self.liste.selection_clear(0, "end")
            for n, t in enumerate(self.gorunen):
                if t["id"] == mid:
                    self.liste.selection_set(n)
                    self.liste.see(n)
        self._sonlari_ciz()
        self._firma_goster()

    def _secili(self):
        return next((t for t in self.tum if t["id"] == self.secili_id), None)

    def _firma_goster(self):
        t = self._secili()
        if not t:
            renk, zemin = R["dugme_k"], R["yok_z"]
            self.secilen_ad.configure(text="Firma seçilmedi", fg=R["gri"])
            self.secilen_alt.configure(text="Listeden seçin ya da yazarak arayın.")
        else:
            tanimli = bool(t.get("klasor"))
            renk, zemin = (R["yesil"], R["yesil_z"]) if tanimli else (R["sari"], R["sari_z"])
            kok_adi = Path(cikti.FIRMALAR).name or str(cikti.FIRMALAR)
            yer = (f"Kayıt yeri: {kok_adi}\\{t['klasor']}\\KDV\\{self.yil_no}-{self.ay_no:02d}" if tanimli
                   else "Klasör tanımlı değil — Başlat'ta sorulacak.")
            if not self._kok_var():
                yer = "Kayıt klasörü seçilmedi ya da erişilemiyor (üstteki yazıya tıklayın)."
            self.secilen_ad.configure(text=unvan_sade(t["unvan"]), fg=R["yazi"])
            luca_ad = next((a for i, a in self.luca_firmalar if i == t.get("luca")), "") if t.get("luca") else ""
            luca_bilgi = f" · Luca: {luca_ad or 'tanımlı'}" if t.get("luca") else " · Luca: tanımsız"
            self.secilen_alt.configure(text=f"VKN {t['vkn'] or '—'} · E-Mükellef no {t['id']}{luca_bilgi}\n{yer}")
        self.secilen_cubuk.configure(bg=renk)
        for w in (self.secilen_kutu, self.secilen_ad, self.secilen_alt):
            w.configure(bg=zemin)
        self._onceki_yenile()                       # firma ya da dönem değişti (ya da firma kaydı güncellendi)

    # ------------------------------------------------------------ dönem
    def _donem(self):
        return self.ay_no, self.yil_no

    def _donem_kaydir(self, yon):
        ay, yil = self.ay_no + yon, self.yil_no
        if ay < 1:
            ay, yil = 12, yil - 1
        elif ay > 12:
            ay, yil = 1, yil + 1
        if 2020 <= yil <= 2100:
            self.ay_no, self.yil_no = ay, yil
            self._donem_guncelle()

    def _donem_guncelle(self):
        ay, yil = self._donem()
        o_ay, o_yil = emukellef.odeme_ayi(ay, yil)
        p_ay, p_yil = (12, yil - 1) if ay == 1 else (ay - 1, yil)
        A = cikti.AYLAR
        bu, onc, ode = f"{A[ay - 1]} {yil}", f"{A[p_ay - 1]} {p_yil}", f"{A[o_ay - 1]} {o_yil}"
        self.donem_yazi.configure(text=bu)
        self.b_beyan.configure(text=f"Verilen beyannameyi\nindir · {bu}")
        self.donem_ipucu.configure(text=f"Önceki dönem beyannamesi: {onc} · KDV2 ödeme ayı: {ode}")
        kapsam = {"beyan": onc, "muhsgk": f"{A[p_ay - 1]} – {A[ay - 1]}", "sgk6661": f"{yil - 1} – {yil}" if ay == 1 else f"{yil} yılı", "efatura_gelen": bu, "efatura_giden": bu, "earsiv": bu,
                  "earsiv_giden": bu, "adina": "çapraz kontrol", "iptal_itiraz": f"{A[ay - 1]} – {A[o_ay - 1]}",
                  "kdv2": f"ödeme ayı {A[o_ay - 1]}", "s109": f"{A[ay - 1]} – {A[o_ay - 1]}", "alindi": f"{A[ay - 1]} – {A[o_ay - 1]}",
                  "tahsilat": f"{A[p_ay - 1]} – {A[o_ay - 1]}", "borc": "bugün", "okc": bu, "pos": bu, "todeb": bu, "ithalat": bu,
                  "arac": "bugün", "arac_gecmis": "bugün", "arac_alim": str(yil), "evrak": "son 6 ay", "bildirim": "son 6 ay",
                  "karsit": "son 6 ay", "etebligat": "son 6 ay", "luca_kebir": f"{yil} yılı", "luca_mizan_ay": bu,
                  "luca_mizan_yil": f"Ocak – {A[ay - 1]}", "luca_hesap_plani": f"{yil} yılı", "luca_ae_alis": bu, "luca_ae_satis": bu, "luca_bordro": bu,
                  "luca_utts": f"{A[ay - 1]} + {A[ay % 12]} 1–15"}
        for a, w in self.kapsam_yazi.items():
            w.configure(text=kapsam.get(a, ""))
        self._firma_goster()

    # ------------------------------------------------------------ günlük ve sonuç paneli
    def _yaz(self, metin):
        zaman = datetime.now().strftime("%H:%M:%S")
        etiket = "hata" if "HATA" in metin else ("uyari" if ("UYARI" in metin or "ACİL" in metin) else ("baslik" if metin.startswith("=====") else None))
        if etiket == "hata":
            self.son_hata = metin[:300]
        self.gunluk.config(state="normal")
        self.gunluk.insert("end", zaman + "  ", "zaman")
        self.gunluk.insert("end", metin + "\n", etiket or ())
        self.gunluk.see("end")
        self.gunluk.config(state="disabled")
        try:
            with open(GUNLUK, "a", encoding="utf-8") as g:
                g.write(f"{datetime.now():%d.%m.%Y %H:%M:%S} {metin}\n")
        except OSError:
            pass

    def _alt_goster(self, hangi):
        if hangi == "sonuc" and self.bitis is None:
            hangi = "gunluk"
        self.gunluk.pack_forget()
        self.sonuc.pack_forget()
        (self.sonuc if hangi == "sonuc" else self.gunluk).pack(fill="both", expand=True)
        self.alt_gorunen = hangi
        self.alt_baslik.configure(text="SONUÇ" if hangi == "sonuc" else "GÜNLÜK")
        self.alt_gecis.configure(text=("Günlüğü göster ▸" if hangi == "sonuc" else ("Sonucu göster ▸" if self.bitis else "")))
        if hangi == "gunluk":
            self.gunluk.see("end")

    def _sonuc_kur(self, b):
        """Çalıştırma bitince: özet satırı, ACİL ve HATA kutuları, dosya düğmeleri."""
        self.bitis = b
        for w in self.sonuc.winfo_children():
            w.destroy()
        s = b.get("sayim", {})
        ust = tk.Frame(self.sonuc, bg=R["kart"])
        ust.pack(fill="x", pady=(0, 5))
        durdu = b.get("durdu")
        tk.Label(ust, text="Bitti" if not durdu else f"Durduruldu ({durdu})", bg=R["kart"], fg=R["yazi"] if not durdu else R["kirmizi"],
                 font=(YAZI, 11, "bold")).pack(side="left")
        parca = [f"{s.get('veri', 0)} sorguda veri var", f"{s.get('kayit_yok', 0)} kayıt yok", f"{s.get('hata', 0)} hata"]
        if s.get("calistirilmadi"):
            parca.append(f"{s['calistirilmadi']} çalıştırılmadı")
        if b.get("fatura_sayisi"):
            parca.append(f"{b['fatura_sayisi']} fatura")
        tk.Label(ust, text=f"{b.get('firma', '')} · {b.get('donem', '')} — " + " · ".join(parca), bg=R["kart"], fg=R["yok"],
                 font=(YAZI, 9)).pack(side="left", padx=(10, 0))

        def kutu(etiket, metin, renk, zemin, dugme_adi=None, komut=None):
            c = tk.Frame(self.sonuc, bg=renk)
            c.pack(fill="x", pady=(0, 5))
            ic = tk.Frame(c, bg=zemin)
            ic.pack(fill="x", padx=(4, 0))
            if dugme_adi:
                dugme(ic, dugme_adi, komut, kucuk=True, zemin=R["kart"]).pack(side="right", padx=8, pady=4)
            tk.Label(ic, text=etiket, bg=zemin, fg=renk, font=(YAZI, 9, "bold")).pack(side="left", padx=(8, 6), pady=4)
            tk.Label(ic, text=metin, bg=zemin, fg=R["yazi"], font=(YAZI, 9), anchor="w", justify="left", wraplength=560).pack(side="left", fill="x", pady=4)

        acil = b.get("acil") or []
        for a in acil[:2]:
            tebligat = "E-Tebligat" in a and b.get("tebligat_klasoru")
            kutu("ACİL", a[:230] + ("…" if len(a) > 230 else ""), R["kirmizi"], R["kirmizi_z"],
                 "Tebligat klasörünü aç" if tebligat else None, (lambda: self._ac(b["tebligat_klasoru"])) if tebligat else None)
        if len(acil) > 2:
            kutu("ACİL", f"{len(acil) - 2} uyarı daha var; günlüğe ve Kontrol Özeti'ne bakın.", R["kirmizi"], R["kirmizi_z"])
        hatalar = b.get("hatalar") or []
        if b.get("eksik"):
            if hatalar:
                ad, mesaj = hatalar[0]
                metin = f"{ad}: {mesaj[:170]}" + ("…" if len(mesaj) > 170 else "") + (f"  (+{len(hatalar) - 1} hata daha)" if len(hatalar) > 1 else "")
            else:
                metin = f"{s.get('calistirilmadi', 0)} sorgu çalıştırılmadı."
            kutu("HATA" if hatalar else "EKSİK", metin, R["sari"], R["sari_z"], "Yalnız hatalıları tekrar dene", self._tekrar)
        alt = tk.Frame(self.sonuc, bg=R["kart"])
        alt.pack(fill="x", pady=(2, 0))
        for ad, yol, ana in (("Kontrol Özetini aç", b.get("ozet"), True), ("Dönem klasörünü aç", b.get("donem_klasoru"), False),
                             ("Fatura dökümü (Excel)", b.get("dokum"), False)):
            d = dugme(alt, ad, lambda y=yol: self._ac(y), ana=ana)
            d.pack(side="left", padx=(0, 8))
            if not yol:
                d.d.configure(state="disabled")
        self._alt_goster("sonuc")

    def _ac(self, yol):
        try:
            if yol and Path(yol).exists():
                os.startfile(yol)
            else:
                self._yaz(f"UYARI: açılacak dosya/klasör bulunamadı: {yol}")
        except OSError as e:
            self._yaz(f"UYARI: açılamadı: {e}")

    # ------------------------------------------------------------ görevler
    def _gorev(self, ad, **kw):
        if self.calisiyor:
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin veya Durdur'a basın.")
            return False
        self.isci.gonder(ad, **kw)
        return True

    def tarayici_goster(self, goster, hizmet="emukellef"):
        """Tarayıcıyı görünür/gizli açma görevini gönderir; işçi meşgulse False döner."""
        if not self._gorev("tarayici_ac", gorunur=bool(goster), hizmet_adi=hizmet):
            return False
        self.giris_ertelendi = bool(goster)         # görünür modda giriş tarayıcıdan yapılır
        self.son_kontrol = time.time()
        if not goster:
            self.luca_elle = None
        return True

    def luca_giris_ac(self, sonra=None, lucasiz=None):
        if self.calisiyor:
            self._yaz("Bir işlem sürüyor; bitince Luca girişi yapabilirsiniz.")
            return
        if self.luca_p is None and self.giris_p is None:
            self.luca_p = LucaGirisPenceresi(self, sonra=sonra, lucasiz=lucasiz)

    def _luca_eslesme_kaydet(self, t, sirket_id):
        """firmalar.json'daki firma kaydına luca_sirket_id yazar (kayıt, klasör eşlemesiyle önceden açılmıştır)."""
        veri, hata = eslesme_oku()
        if hata:                                    # bozuk dosyanın üzerine yazılmaz
            raise RuntimeError(hata)
        bulundu = False
        for x in veri.get("firmalar", []):
            if int(x.get("emukellef_id") or 0) == t["id"]:
                x["luca_sirket_id"] = int(sirket_id) if str(sirket_id).isdigit() else sirket_id
                bulundu = True
        if not bulundu:
            raise KeyError("firma kaydı yok")
        json_yaz(FIRMALAR_JSON, veri)

    def _luca_firma_sor(self, t):
        """Seçili firmanın Luca karşılığı tanımlı değilse sorar ve kaydeder; güncel firma kaydını (ya da None) döner."""
        if not self.luca_firmalar:
            messagebox.showwarning("Luca firma listesi yok", "Luca firma listesi henüz alınamadı. Luca'ya giriş yapıp yeniden deneyin.", parent=self.kok)
            return None
        p = LucaFirmaPenceresi(self.kok, t, self.luca_firmalar)
        self.kok.wait_window(p)
        if not p.sonuc:
            return None
        try:
            self._luca_eslesme_kaydet(t, p.sonuc[0])
        except Exception as e:
            self._yaz(f"HATA: Luca firması kaydedilemedi ({type(e).__name__}).")
            return None
        self._yaz(f"Luca firması tanımlandı: {unvan_sade(t['unvan'])} -> {p.sonuc[1]}")
        secili = t["id"]
        self._listeyi_kur()
        self.secili_id = secili
        self._suz()
        self._firma_goster()
        return self._secili()

    def giris_ac(self):
        self.giris_ertelendi = False
        if self.giris_p is None and self.luca_p is None:
            self.giris_p = GirisPenceresi(self)

    def _eslesme_kaydet(self, t, ad, klasor):
        veri, hata = eslesme_oku()
        if hata:                                    # bozuk dosyanın üzerine yazılmaz
            raise RuntimeError(hata)
        eski = next((x for x in veri.get("firmalar", []) if int(x.get("emukellef_id") or 0) == t["id"]), {})
        firmalar = [x for x in veri.get("firmalar", []) if int(x.get("emukellef_id") or 0) != t["id"]]
        yeni = {"ad": ad, "klasor": klasor, "emukellef_id": t["id"], "vkn": t.get("vkn", ""), "unvan": t["unvan"]}
        if eski.get("luca_sirket_id"):              # önceden tanımlı Luca karşılığı korunur
            yeni["luca_sirket_id"] = eski["luca_sirket_id"]
        firmalar.append(yeni)
        veri["firmalar"] = firmalar
        json_yaz(FIRMALAR_JSON, veri)

    def _baslat_durdur(self):
        if self.toplu:                              # toplu sorgu: kalan firmalar da iptal
            self.toplu["iptal"] = True
            self.isci.iptal()
            self._yaz("Durdurma istendi; açık istek bitince durur, kalan firmalar sorgulanmaz.")
            if not (self.calisiyor or self.kosu):
                self._toplu_bitir()
            return
        if self.kosu or self.beyan_kosu:
            self.isci.iptal()
            self._yaz("Durdurma istendi; açık istek bitince durur.")
        else:
            self._baslat()

    def _baslat(self):
        if self.toplu:
            self._yaz("Toplu sorgu sürüyor; bitmesini bekleyin ya da Durdur'a basın.")
            return
        if not self._kok_gerekli():                 # kayıt klasörü yoksa önce o (firma listesi değişebilir: seçim sonra okunur)
            return
        t = self._secili()
        if not t:
            messagebox.showwarning("Firma seçin", "Önce listeden firma seçin.", parent=self.kok)
            return
        secim = {k: v.get() for k, v in self.secim.items()}
        if not any(secim.values()):          # alt seçenekler tek başına sayılmaz
            messagebox.showwarning("Seçim yok", "En az bir sorgu işaretleyin.", parent=self.kok)
            return
        if self.calisiyor or self.beyan_kosu:
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin.")
            return
        luca_secili = any(secim.get(k) for k in LUCA_SORGULARI)
        emk_secili = any(v for k, v in secim.items() if k not in LUCA_SORGULARI)
        if emk_secili and self.emk is not True and not self.gorunur:
            self._yaz("E-Mükellef girişi gerekli; giriş yaptıktan sonra Başlat'a basın.")
            self.giris_ac()
            return
        if not t.get("klasor"):
            t = self._klasor_tanimla(t)
            if t is None:
                return
        if luca_secili:
            if self.luca is not True and self.gorunur and self.luca_elle:     # elle giriş bekleniyor: pencere yeniden açılmaz
                self.luca_elle = self._baslat
                self._yaz("Açılan tarayıcı penceresinde Luca'ya giriş yapıp 'LUCA MALİ MÜŞAVİR PAKETİ'ni açın; program algılayınca kendiliğinden başlar.")
                return
            if self.luca is not True:               # önce Luca girişi; başarılı olunca Başlat kendiliğinden sürer
                self._yaz("Luca sorguları seçili; Luca girişi gerekli.")
                self.luca_giris_ac(sonra=self._baslat, lucasiz=lambda: (self._hazir_sec([k for k, v in self.secim.items()
                                                                                         if v.get() and k not in LUCA_SORGULARI]),
                                                                        self._baslat()))
                return
            if not t.get("luca"):
                t = self._luca_firma_sor(t)
                if t is None:
                    self._yaz("Luca firması seçilmedi; Luca sorgularının işaretini kaldırıp yeniden Başlat'a basabilirsiniz.")
                    return
        ay, yil = self._donem()
        self.kosu_hedef = (t["id"], ay, yil)
        firma = {"ad": t["ad"], "klasor": t["klasor"], "emukellef_id": t["id"], "vkn": t.get("vkn", ""), "unvan": t.get("unvan", ""),
                 "luca_sirket_id": t.get("luca") or ""}
        self.son_kosu_firma = t["id"]
        secim.update({"firma": firma, "ay": ay, "yil": yil, "earsiv_gib": self.earsiv_gib.get(), "pdf": self.pdf.get()})
        son = [t["id"]] + [i for i in self.ayarlar.get("son_firmalar", []) if i != t["id"]]
        self.ayarlar["son_firmalar"] = son[:6]
        try:
            self.ayarlar = ayar.ayar_yaz(son_firmalar=son[:6])
        except OSError:
            pass
        self._sonlari_ciz()
        self._durumlari_sifirla()                   # yeni çalıştırmada renkler sıfırlanır
        self.bitis = None
        self._alt_goster("gunluk")
        self._kosu_basla()
        self.isci.gonder("baslat", secim=secim)     # işçiye kopya gider; işçi Tkinter'a dokunmaz

    # ------------------------------------------------------------ sık kullanılan firmalar ve toplu sorgu (kullanıcı isteği 26.11.2026)
    # Liste E-Mükellef adresine göre saklanır (başka ofisin aynı numaralı mükellefi karışmaz). Toplu sorgu, seçili sorguları ve dönemi
    # listedeki her firma için SIRAYLA, Başlat'a basılmış gibi çalıştırır; eksik klasör / Luca firması başta bir kez sorulur.
    def _sik_anahtar(self):
        return emukellef.ADRES.replace("https://", "").strip("/")

    def _sik_idler(self):
        d = self.ayarlar.get("sik_firmalar")
        liste = d.get(self._sik_anahtar(), []) if isinstance(d, dict) else []
        cikis = []
        for i in liste if isinstance(liste, list) else []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if i not in cikis:
                cikis.append(i)
        return cikis

    def _sik_ciz(self):
        if not hasattr(self, "sik_satir"):
            return
        for w in self.sik_satir.winfo_children():
            w.destroy()
        n = sum(1 for i in self._sik_idler() if any(t["id"] == i for t in self.tum))
        tk.Label(self.sik_satir, text=f"Sık kullanılanlar: {n} firma", bg=R["kart"], fg=R["gri"], font=(YAZI, 8)).pack(side="left")
        d = tk.Label(self.sik_satir, text="Düzenle", bg=R["kart"], fg=R["mavi"], cursor="hand2", font=(YAZI, 8, "underline"))
        d.pack(side="left", padx=(8, 0))
        d.bind("<Button-1>", lambda e: self._sik_duzenle())
        self.b_toplu = tk.Button(self.sik_satir, text="Toplu sorgula" if not self.toplu else "Toplu sorgu sürüyor…",
                                 command=self._toplu_baslat, relief="flat", bd=0, cursor="hand2", font=(YAZI, 8, "bold"),
                                 bg=R["dugme_z"], fg=R["lacivert"], activebackground=R["secili_z"], disabledforeground=R["acik_gri"], padx=8, pady=1)
        self.b_toplu.pack(side="right")
        self._dugme_guncelle()

    def _sik_duzenle(self):
        if self.toplu:
            return
        p = SikFirmaPenceresi(self.kok, self.tum, self._sik_idler())
        self.kok.wait_window(p)
        if p.sonuc is None:
            return
        d = self.ayarlar.get("sik_firmalar")
        d = dict(d) if isinstance(d, dict) else {}
        d[self._sik_anahtar()] = list(p.sonuc)
        try:
            self.ayarlar = ayar.ayar_yaz(sik_firmalar=d)
        except OSError as e:
            messagebox.showerror("Sık kullanılan firmalar", f"Liste kaydedilemedi: {e}", parent=self.kok)
            return
        self._yaz(f"Sık kullanılan firmalar kaydedildi: {len(p.sonuc)} firma.")
        self._sik_ciz()

    def _toplu_baslat(self):
        if self.toplu:
            return
        if not self._kok_gerekli():
            return
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin.")
            return
        idler = [i for i in self._sik_idler() if any(t["id"] == i for t in self.tum)]
        if not idler:
            messagebox.showinfo("Toplu sorgula", "Önce 'Düzenle' ile sık kullanılan firmaları seçip kaydedin.", parent=self.kok)
            return
        secim = {k: v.get() for k, v in self.secim.items()}
        if not any(secim.values()):
            messagebox.showwarning("Seçim yok", "En az bir sorgu işaretleyin.", parent=self.kok)
            return
        luca_secili = any(secim.get(k) for k in LUCA_SORGULARI)
        emk_secili = any(v for k, v in secim.items() if k not in LUCA_SORGULARI)
        if emk_secili and self.emk is not True and not self.gorunur:
            self._yaz("E-Mükellef girişi gerekli; giriş yaptıktan sonra 'Toplu sorgula'ya yeniden basın.")
            self.giris_ac()
            return
        if luca_secili:
            if self.luca is not True and self.gorunur and self.luca_elle:
                self.luca_elle = self._toplu_baslat
                self._yaz("Açılan tarayıcı penceresinde Luca'ya giriş yapıp 'LUCA MALİ MÜŞAVİR PAKETİ'ni açın; toplu sorgu kendiliğinden başlar.")
                return
            if self.luca is not True:
                self._yaz("Luca sorguları seçili; Luca girişi gerekli.")
                self.luca_giris_ac(sonra=self._toplu_baslat)
                return
        ay, yil = self._donem()
        ayarlar_ = {"earsiv_gib": self.earsiv_gib.get(), "pdf": self.pdf.get()}      # onaydaki değerler sabitlenir
        onceki_secili = self.secili_id
        plan, atlanan = [], []
        luca_uyarildi = False
        for mid in idler:
            t = next((x for x in self.tum if x["id"] == mid), None)
            if t is None:
                atlanan.append((str(mid), "firma listesinde yok"))
                continue
            ad = t.get("ad") or kisa_ad_oner(t["unvan"])
            if not t.get("klasor"):
                t = self._klasor_tanimla(t)
                if t is None:
                    atlanan.append((ad, "klasör tanımlanmadı"))
                    continue
                ad = t.get("ad") or ad
            s, notu = dict(secim), ""
            if luca_secili and not t.get("luca"):
                t2 = None
                if self.luca_firmalar:
                    t2 = self._luca_firma_sor(t)
                elif not luca_uyarildi:
                    luca_uyarildi = True
                    self._yaz("UYARI: Luca firma listesi alınamadı; Luca firması tanımsız firmalarda Luca sorguları çalıştırılmayacak.")
                if t2 is not None and t2.get("luca"):
                    t = t2
                else:
                    for k in LUCA_SORGULARI:
                        if k in s:
                            s[k] = False
                    notu = "Luca firması tanımsız: Luca sorguları yok"
            n = sum(1 for v in s.values() if v)
            if not n:
                atlanan.append((ad, "çalışacak sorgu kalmadı (" + (notu or "seçim yok") + ")"))
                continue
            plan.append({"id": t["id"], "ad": ad, "secim": s, "not": notu, "n": n, "luca": "" if notu else (t.get("luca") or "")})
        if onceki_secili is not None and any(x["id"] == onceki_secili for x in self.tum):
            self._firma_sec(onceki_secili)          # sorular seçimi değiştirmiş olabilir: eski seçime dön
        if not plan:
            messagebox.showwarning("Toplu sorgula", "Sorgulanacak firma kalmadı.\n\n" + "\n".join(f"• {a}: {n}" for a, n in atlanan), parent=self.kok)
            return
        metin = (f"{cikti.AYLAR[ay - 1]} {yil} dönemi için {len(plan)} firma sırayla sorgulanacak:\n\n"
                 + "\n".join(f"• {p_['ad']} ({p_['n']} sorgu)" + (f" — {p_['not']}" if p_["not"] else "") for p_ in plan[:30])
                 + (f"\n… ve {len(plan) - 30} firma daha" if len(plan) > 30 else "")
                 + ("\n\nAtlanacak:\n" + "\n".join(f"• {a}: {n}" for a, n in atlanan) if atlanan else "")
                 + "\n\nSürerken 'Durdur' kalan firmaları da iptal eder. Başlasın mı?")
        if not messagebox.askyesno("Toplu sorgula", metin, parent=self.kok):
            return
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():     # soru açıkken başka iş başlamış olabilir
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin.")
            return
        self.toplu = {"plan": plan, "i": 0, "ay": ay, "yil": yil, "ayar": ayarlar_, "sonuclar": [], "atlanan": atlanan,
                      "gorev_sira": None, "gorev_bitti": False, "bitis": None, "iptal": False, "durdu": "", "baslangic": time.time()}
        self._yaz(f"===== Toplu sorgu başladı: {len(plan)} firma · {cikti.AYLAR[ay - 1]} {yil} =====")
        self._sik_ciz()
        self._toplu_sonraki()

    def _toplu_sonraki(self):
        tp = self.toplu
        if tp is None:
            return
        tp["gorev_bitti"], tp["gorev_sira"], tp["bitis"] = False, None, None
        if tp["iptal"] or tp["i"] >= len(tp["plan"]):
            self._toplu_bitir()
            return
        p = tp["plan"][tp["i"]]
        emk_gerek = any(v for k, v in p["secim"].items() if k not in LUCA_SORGULARI)
        luca_gerek = any(p["secim"].get(k) for k in LUCA_SORGULARI)
        if emk_gerek and self.emk is not True and not self.gorunur:
            tp["durdu"] = "E-Mükellef oturumu kapandı"
        elif luca_gerek and self.luca is not True:
            tp["durdu"] = "Luca oturumu kapandı"
        if tp["durdu"]:
            self._toplu_bitir()
            return
        t = next((x for x in self.tum if x["id"] == p["id"]), None)
        if t is None or not t.get("klasor"):
            tp["sonuclar"].append((p["ad"], "atlandı: firma kaydı ya da klasörü bulunamadı", None))
            tp["i"] += 1
            self._toplu_sonraki()
            return
        self._firma_sec(t["id"])                    # sonuçlar ekranda bu firmanın kutularında görünsün
        ay, yil = tp["ay"], tp["yil"]
        firma = {"ad": t["ad"], "klasor": t["klasor"], "emukellef_id": t["id"], "vkn": t.get("vkn", ""), "unvan": t.get("unvan", ""),
                 "luca_sirket_id": t.get("luca") or p.get("luca") or ""}
        secim = dict(p["secim"])
        secim.update({"firma": firma, "ay": ay, "yil": yil, **tp["ayar"]})
        self.kosu_hedef = (t["id"], ay, yil)
        self.son_kosu_firma = t["id"]
        self._durumlari_sifirla()
        self.bitis = None
        self._alt_goster("gunluk")
        self._yaz(f"----- Toplu sorgu {tp['i'] + 1}/{len(tp['plan'])}: {p['ad']} -----")
        self._kosu_basla()
        self.isci.gonder("baslat", secim=secim)
        tp["gorev_sira"] = getattr(self.isci, "sira", None)

    def _toplu_kaydet(self):
        tp = self.toplu
        if tp is None or tp["i"] >= len(tp["plan"]):
            return
        p, b = tp["plan"][tp["i"]], tp["bitis"]
        if b:
            s = b.get("sayim") or {}
            durum = f"{s.get('veri', 0)} veri, {s.get('kayit_yok', 0)} kayıt yok"
            if s.get("hata"):
                durum += f", {s['hata']} HATA"
            if s.get("calistirilmadi"):
                durum += f", {s['calistirilmadi']} çalıştırılmadı"
            if b.get("acil"):
                durum += f", ACİL {len(b['acil'])}"
        else:
            durum = "durduruldu" if tp["iptal"] else "sonuç üretmeden bitti (günlüğe bakın)"
        tp["sonuclar"].append((p["ad"], durum, b))
        tp["i"] += 1
        tp["gorev_bitti"] = True

    def _toplu_bitir(self):
        tp, self.toplu = self.toplu, None
        if tp is None:
            return
        self._sik_ciz()
        self._dugme_guncelle()
        kalan = tp["plan"][tp["i"]:]
        s = int(time.time() - tp["baslangic"])
        satirlar = [f"• {ad}: {durum}" for ad, durum, _ in tp["sonuclar"]]
        if kalan:
            neden = tp["durdu"] or ("durduruldu" if tp["iptal"] else "çalıştırılmadı")
            satirlar.append(f"\nSorgulanmayan {len(kalan)} firma ({neden}): " + ", ".join(p_["ad"] for p_ in kalan))
        if tp["atlanan"]:
            satirlar.append("\nAtlanan: " + ", ".join(f"{a} ({n})" for a, n in tp["atlanan"]))
        sorunlu = kalan or any(b is None or (b.get("eksik") or b.get("acil")) for _, _, b in tp["sonuclar"])
        baslik_ = f"Toplu sorgu — {cikti.AYLAR[tp['ay'] - 1]} {tp['yil']} ({len(tp['sonuclar'])} firma, {s // 60:02d}:{s % 60:02d})"
        self._yaz(f"===== {baslik_} =====")
        for x in satirlar:
            self._yaz(x.strip())
        metin = "\n".join(satirlar) + "\n\nAyrıntı her firmanın kendi Kontrol Özeti'nde ve günlükte."
        if tp["durdu"]:
            metin += f"\n\n{tp['durdu']}: giriş yapıp 'Toplu sorgula'ya yeniden basın (sorgulanmış firmaların sonuçları korunur)."
        self.kok.after(60, lambda: (messagebox.showwarning if sorunlu else messagebox.showinfo)(baslik_, metin, parent=self.kok))

    def _klasor_tanimla(self, t):
        """Firma klasörü tanımlı değilse sorar ve kaydeder; güncel firma kaydını (vazgeçilirse None) döner."""
        p = KlasorPenceresi(self.kok, t)
        self.kok.wait_window(p)
        if not p.sonuc:
            return None
        try:
            self._eslesme_kaydet(t, p.sonuc["ad"], p.sonuc["klasor"])
        except (RuntimeError, OSError) as e:
            self._yaz(f"HATA: firma klasörü eşlemesi kaydedilemedi: {e}")
            messagebox.showerror("Firma klasörü", f"Eşleme kaydedilemedi:\n{e}", parent=self.kok)
            return None
        self._yaz(f"Firma klasörü tanımlandı: {t['unvan']} -> {p.sonuc['klasor']} (kısa ad: {p.sonuc['ad']})")
        secili = t["id"]
        self._listeyi_kur()
        self.secili_id = secili
        self._suz()
        self._firma_goster()
        return self._secili()

    def _verilen_indir(self):
        """Seçili firma + dönemin VERİLMİŞ beyannamelerini (KDV1, KDV2, MUHSGK; beyanname + tahakkuk) indirir. Başka sorgu çalışmaz."""
        if not self._kok_gerekli():
            return
        t = self._secili()
        if not t:
            messagebox.showwarning("Firma seçin", "Önce listeden firma seçin.", parent=self.kok)
            return
        if self.calisiyor or self.kosu or self.beyan_kosu:
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin.")
            return
        if self.emk is not True and not self.gorunur:
            self._yaz("E-Mükellef girişi gerekli; giriş yaptıktan sonra 'Verilen beyannameyi indir'e yeniden basın.")
            self.giris_ac()
            return
        if not t.get("klasor"):
            t = self._klasor_tanimla(t)
            if t is None:
                return
        ay, yil = self._donem()
        firma = {"ad": t["ad"], "klasor": t["klasor"], "emukellef_id": t["id"], "vkn": t.get("vkn", ""), "unvan": t.get("unvan", "")}
        self._alt_goster("gunluk")
        self.isci.gonder("verilen_beyanname", firma=firma, ay=ay, yil=yil)
        self.beyan_kosu = True                      # hemen kilitle: işçinin 'calisiyor' olayı gelene kadar Başlat'a basılamasın
        self.beyan_sira = getattr(self.isci, "sira", None)
        self._dugme_guncelle()                      # Başlat -> Durdur (indirme durdurulabilir)

    def _gib_aktar(self):
        """GİB'de verilmiş, E-Mükellef sitesine henüz yüklenmemiş beyanname / tahakkukları E-Mükellef masaüstü programıyla aktarır.
        Firma seçimine bağlı değildir: aralıktaki TÜM firmaların yüklenmemiş evrakları (programın kendi işaretlediği) aktarılır."""
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin.")
            return
        p = AktarPenceresi(self.kok)
        self.kok.wait_window(p)
        if not p.sonuc:
            return
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():     # pencere açıkken başka iş başlamış olabilir
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin.")
            return
        self._alt_goster("gunluk")
        self.isci.gonder("emk_aktar", **p.sonuc)
        self.beyan_kosu = True                      # ortak tek-görev kilidi (Başlat kilitli, Durdur açık)
        self.beyan_sira = getattr(self.isci, "sira", None)
        self._dugme_guncelle()

    def _aktar_sonucu(self, b):
        baslik_ = "GİB'den E-Mükellef'e aktarma"
        if b.get("durum") == "yuklendi":
            messagebox.showinfo(baslik_, f"{b.get('mesaj', '')}\n\n" + "\n".join(f"  • {x[:110]}" for x in (b.get("yuklenen") or [])[:14])
                                + "\n\nMükelleflere gönderim yapılmadı. Belgeleri klasöre almak için 'Verilen beyannameyi indir'i kullanın.", parent=self.kok)
        elif b.get("durum") == "liste":
            messagebox.showinfo(baslik_, f"{b.get('mesaj', '')}\n\n" + "\n".join(f"  • {x[:110]}" for x in (b.get("evraklar") or [])[:18]), parent=self.kok)
        elif b.get("durum") in ("kayit_yok", "iptal"):
            messagebox.showinfo(baslik_, b.get("mesaj") or "Yüklenecek evrak yok.", parent=self.kok)
        else:
            messagebox.showwarning(baslik_, "Aktarma tamamlanamadı:\n" + (b.get("mesaj") or "bilinmeyen hata"), parent=self.kok)

    def _kdvkar_raporu(self):
        """Seçili firma + dönem için KDV ve kâr/zarar durumu raporu: Luca'dan ayın mizanı, dönem başından mizan ve kebir indirilir."""
        if not self._kok_gerekli():
            return
        t = self._secili()
        if not t:
            messagebox.showwarning("Firma seçin", "Önce listeden firma seçin.", parent=self.kok)
            return
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():
            self._yaz("Bir işlem sürüyor; bitmesini bekleyin.")
            return
        if not t.get("klasor"):
            t = self._klasor_tanimla(t)
            if t is None:
                return
        if self.luca is not True and self.gorunur and self.luca_elle:     # elle giriş bekleniyor: pencere yeniden açılmaz
            self.luca_elle = self._kdvkar_raporu
            self._yaz("Açılan tarayıcı penceresinde Luca'ya giriş yapıp 'LUCA MALİ MÜŞAVİR PAKETİ'ni açın; rapor kendiliğinden başlar.")
            return
        if self.luca is not True:                   # rapor Luca kayıtlarından düzenlenir: önce Luca girişi
            self._yaz("KDV ve kâr/zarar raporu Luca kayıtlarından düzenlenir; Luca girişi gerekli.")
            self.luca_giris_ac(sonra=self._kdvkar_raporu)
            return
        if not t.get("luca"):
            t = self._luca_firma_sor(t)
            if t is None:
                self._yaz("Luca firması seçilmedi; rapor düzenlenmedi.")
                return
        ay, yil = self._donem()
        firma = {"ad": t["ad"], "klasor": t["klasor"], "emukellef_id": t["id"], "vkn": t.get("vkn", ""), "unvan": t.get("unvan", ""),
                 "luca_sirket_id": t.get("luca") or ""}
        self._alt_goster("gunluk")
        self.isci.gonder("kdv_kar_raporu", firma=firma, ay=ay, yil=yil)
        self.beyan_kosu = True                      # ortak tek-görev kilidi (Başlat kilitli, Durdur açık)
        self.beyan_sira = getattr(self.isci, "sira", None)
        self._dugme_guncelle()

    def _kdvkar_sonucu(self, b):
        if b.get("durum") == "veri" and b.get("rapor"):
            RaporPenceresi(self, b)
        elif b.get("durum") == "kayit_yok":
            messagebox.showinfo(f"KDV ve kâr/zarar — {b.get('firma', '')} · {b.get('donem', '')}", b.get("mesaj") or "Kayıt yok.", parent=self.kok)
        else:
            messagebox.showwarning(f"KDV ve kâr/zarar — {b.get('firma', '')} · {b.get('donem', '')}",
                                   "Rapor düzenlenemedi:\n" + (b.get("mesaj") or "bilinmeyen hata"), parent=self.kok)

    def rapor_kaydet(self, b, tur, ust=None):
        """Rapor penceresindeki 'PDF' / 'Excel' düğmeleri: dosya dönemin 02_Raporlar klasörüne yazılır ve açılır."""
        if self.calisiyor or self.kosu or self.beyan_kosu or not self._isci_bos():
            messagebox.showinfo("Rapor", "Bir işlem sürüyor; bitince yeniden deneyin.", parent=ust or self.kok)
            return
        self.isci.gonder("rapor_kaydet", rapor=b["rapor"], firma=b["firma"], klasor=b["klasor"], yil=b["yil"], ay=b["ay"], tur=tur)
        self.beyan_kosu = True
        self.beyan_sira = getattr(self.isci, "sira", None)
        self._dugme_guncelle()

    def _beyan_bitti(self):
        if self.beyan_kosu:
            self.beyan_kosu, self.beyan_sira = False, None
            self._dugme_guncelle()

    def _beyanname_sonucu(self, b):
        """'Verilen beyannameyi indir' bitti: sonucu gösterir, klasörü açmayı önerir, dönem notunu yeniler."""
        self._onceki_yenile(gecikme=200)
        baslik_ = f"Verilen beyanname — {b.get('firma', '')} · {b.get('donem', '')}"
        if b.get("durum") == "veri":
            klasorler = b.get("klasorler") or []
            if not b.get("yeni"):
                bas = f"Yeni belge yok; {b.get('ayni', 0)} belgenin hepsi zaten kayıtlıydı (yeniden kaydedilmedi):\n"
            else:
                bas = f"{len(b.get('belgeler') or [])} belge bulundu ({b.get('yeni', 0)} yeni kaydedildi, {b.get('ayni', 0)} zaten vardı):\n"
            metin = (bas
                     + "\n".join(f"  • {x}" for x in (b.get("belgeler") or [])[:12])
                     + "\n\nKlasör:\n" + "\n".join(f"  {k}" for k in klasorler))
            if klasorler and messagebox.askyesno(baslik_, metin + "\n\nKlasör açılsın mı?", parent=self.kok):
                for k in klasorler:
                    self._ac(k)
        elif b.get("durum") == "kayit_yok":
            messagebox.showinfo(baslik_, b.get("mesaj") or "Bu dönem için verilmiş beyanname bulunamadı.", parent=self.kok)
        else:
            kismi = (f"\n\nİndirilebilenler ({len(b['belgeler'])}): " + ", ".join(b["belgeler"][:12])) if b.get("belgeler") else ""
            messagebox.showwarning(baslik_, "Beyanname indirilemedi ya da eksik indi:\n" + (b.get("mesaj") or "bilinmeyen hata") + kismi, parent=self.kok)

    def _tekrar(self):
        if self.calisiyor or self.toplu:
            return
        hedef = [a for a, dr in self.sorgu_durumu.items() if dr in ("hata", "calistirilmadi")]
        if any(a not in LUCA_SORGULARI for a in hedef) and self.emk is not True and not self.gorunur:
            self.giris_ac()
            return
        firma_ek = None
        if any(a in LUCA_SORGULARI for a in hedef):
            if self.luca is not True:
                self.luca_giris_ac(sonra=self._tekrar)
                return
            t = self._secili()
            if t and t["id"] == self.son_kosu_firma:    # çalıştırmadan sonra tanımlanan Luca firması eski seçime aktarılır
                if not t.get("luca"):
                    t = self._luca_firma_sor(t)
                if t and t.get("luca"):
                    firma_ek = {"luca_sirket_id": t["luca"]}
        self._alt_goster("gunluk")
        self._kosu_basla()
        self.isci.gonder("tekrar", firma_ek=firma_ek)

    def _kosu_basla(self):
        self.kosu = True
        self.kosu_baslangic = time.time()
        self._durumlari_ciz()                       # 'tekrar' başka firma seçiliyken başlarsa o firmanın önceki sonuçları kalmasın
        self.il_sure.configure(text="geçen süre 00:00")
        self._dugme_guncelle()

    # ------------------------------------------------------------ işçi olayları
    def _olaylari_isle(self):
        try:
            for _ in range(200):                    # bir turda en çok 200 olay; arayüz donmasın
                try:
                    tur, veri = self.olaylar.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._olay_isle(tur, veri)
                except Exception as e:              # tek olaydaki hata döngüyü durdurmasın
                    try:
                        self._yaz(f"UYARI: arayüz olayı işlenemedi ({tur}): {type(e).__name__}: {str(e)[:150]}")
                    except Exception:
                        pass
        finally:
            self.kok.after(150, self._olaylari_isle)

    def _olay_isle(self, tur, veri):
        if tur == "log":
            self._yaz(veri)
        elif tur == "ayrinti":
            try:
                with open(GUNLUK, "a", encoding="utf-8") as g:
                    g.write(veri + "\n")
            except OSError:
                pass
        elif tur == "durum":
            self._durum_olayi(veri)
        elif tur == "sorgu_durum" and veri.get("anahtar") in self.durum_yazilari:
            a = veri["anahtar"]
            self.sorgu_durumu[a] = veri["durum"]
            if "kisa" in veri:
                self.sorgu_bilgi[a] = veri
            elif veri["durum"] in ("bekliyor", "calisiyor"):
                self.sorgu_bilgi.pop(a, None)
            self._durum_yaz(a)
            self._ilerleme()
        elif tur == "mukellefler" and veri:
            try:
                json_yaz(MUKELLEF_ONBELLEK, {"guncelleme": datetime.now().strftime("%d.%m.%Y %H:%M"), "liste": veri})
            except OSError as e:
                self._yaz(f"UYARI: firma listesi önbelleğe yazılamadı ({e}); liste bu oturumda güncel değil.")
            secili = self.secili_id
            self._listeyi_kur()
            self.secili_id = secili
            self._suz()
            self._firma_goster()
            self._durum_goster()
        elif tur == "calisiyor":
            self.calisiyor = bool(veri)
            if not self.calisiyor and self.kosu:
                self.kosu = False
                s = int(time.time() - (self.kosu_baslangic or time.time()))
                self.il_sure.configure(text=f"süre {s // 60:02d}:{s % 60:02d}")
                self._durumlari_ciz()               # koşu sırasında başka firma seçildiyse kutular onun önceki sonuçlarına döner
            self._dugme_guncelle()
            if not self.calisiyor:
                self._luca_elle_surdur()            # işçi meşgulken algılanan elle Luca girişi: bekleyen işlem şimdi sürer
            if not self.calisiyor and self.toplu and self.toplu.get("gorev_bitti"):
                self._toplu_sonraki()               # toplu sorgu: sıradaki firma (aynı turda; arada boşta kalınmaz)
        elif tur == "bitti":
            self._sonuc_kur(veri)
            if self.toplu and self.toplu.get("gorev_sira") is not None and not self.toplu.get("gorev_bitti"):
                self.toplu["bitis"] = veri          # toplu sorgudaki o anki firmanın sonucu
            if self.komut and self.komut.get("durum") == "calisiyor":
                self._komut_bitir("bitti", bitis=veri)
            self._onceki_yenile(gecikme=400)        # yeni sonuç dosyaları nota ve kutulara yansısın
        elif tur == "beyanname_bitti":              # pencere ayrı turda açılır: olay döngüsü ('calisiyor' vb.) beklemesin
            self._beyan_bitti()
            self.kok.after(60, lambda v=veri: self._beyanname_sonucu(v))
        elif tur == "aktar_bitti":
            self._beyan_bitti()
            self.kok.after(60, lambda v=veri: self._aktar_sonucu(v))
        elif tur == "kdvkar_bitti":
            self._beyan_bitti()
            self._onceki_yenile(gecikme=300)        # inen mizan / kebir dosyaları nota yansısın
            self.kok.after(60, lambda v=veri: self._kdvkar_sonucu(v))
        elif tur == "rapor_kaydedildi":
            self._beyan_bitti()
            if veri.get("yol"):
                self._ac(veri["yol"])
            else:
                self.kok.after(60, lambda v=veri: messagebox.showerror("Rapor", "Rapor kaydedilemedi:\n" + (v.get("hata") or "bilinmeyen hata"),
                                                                       parent=self.kok))
        elif tur == "onceki":                       # arka planda okunan önceki indirmeler (yalnız en son istek, hâlâ seçiliyse)
            if veri.get("sira") == self.onceki_sira and veri.get("hedef") == self._onceki_hedefi():
                self.onceki = veri
                self._onceki_goster()
                self._durumlari_ciz()
        elif tur == "gorev_bitti" and self.beyan_kosu and veri.get("sira") == self.beyan_sira:
            self._beyan_bitti()                     # beyanname görevi sonuç olayı üretmeden bitti (ör. başlamadan durduruldu)
        elif tur == "gorev_bitti" and self.toplu and veri.get("sira") == self.toplu.get("gorev_sira"):
            self._toplu_kaydet()                    # toplu sorgudaki firmanın görevi bitti; sıradaki 'calisiyor' False'ta başlar
        elif tur == "gorev_bitti":                  # işçide bir görev bitti: komutun KENDİ görevi 'bitti' olayı üretmeden bittiyse hata
            if (self.komut and self.komut.get("durum") == "calisiyor" and self.komut_sira is not None
                    and veri.get("sira") == self.komut_sira):
                self._komut_bitir("hata", self.son_hata or "çalıştırma sonuç üretmeden bitti")
        elif tur == "giris_formu" and self.giris_p is not None:
            self.giris_p.captcha_goster(veri["captcha"], veri.get("mesaj", ""))
        elif tur == "giris_kod" and self.giris_p is not None:
            self.giris_p.kod_adimi(veri.get("mesaj", ""))
        elif tur == "giris_sonuc" and self.giris_p is not None:
            self.giris_p.sonuc(veri)
        elif tur == "luca_giris_sonuc" and self.luca_p is not None:
            self.luca_p.sonuc(veri)
        elif tur == "luca_giris_dogrulama" and self.luca_p is not None:
            self.luca_p.dogrulama_goster(veri["resim"], veri.get("mesaj", ""))
        elif tur == "luca_firmalar" and veri:
            self.luca_firmalar = [(str(i), str(a)) for i, a in veri]
            try:
                json_yaz(LUCA_FIRMA_ONBELLEK, {"guncelleme": datetime.now().strftime("%d.%m.%Y %H:%M"), "liste": [list(x) for x in self.luca_firmalar]})
            except OSError:
                pass
            self._firma_goster()

    def _luca_elle_surdur(self):
        """Elle Luca girişi algılandıysa bekleyen işlemi (Başlat/tekrar) sürdürür; işçi meşgulse boşalınca yeniden denenir."""
        if self.luca is not True or not self.luca_elle or self.luca_p is not None:
            return
        if self.calisiyor:
            return                                  # 'calisiyor' False olayında yeniden çağrılır; bekleyen işlem korunur
        sonra, self.luca_elle = self.luca_elle, None
        if callable(sonra):
            self.kok.after(300, sonra)

    def _durum_olayi(self, veri):
        self.emk, self.gorunur = veri.get("emukellef"), bool(veri.get("gorunur"))
        self.luca = veri.get("luca")
        self.son_kontrol = time.time()
        self._durum_goster()
        if self.luca is True:
            if self.luca_p is not None:             # elle girişle bağlandıysa pencere kapanır
                self.luca_p.sonuc({"durum": "tamam"})
            else:                                   # görünür tarayıcıda elle giriş algılandı: bekleyen işlem sürer
                self._luca_elle_surdur()
        if self.emk is True:
            self.giris_ertelendi = False
            if self.giris_p is not None:
                self.giris_p.sonuc({"durum": "tamam"})
        elif (self.emk is False and not self.gorunur and self.giris_p is None and self.luca_p is None and not self.giris_ertelendi
              and not self.kok_soruluyor):
            self.giris_ac()

    def _kapat(self):
        if self.calisiyor and self.kosu and not messagebox.askyesno("Çıkış", "İşlem sürüyor. Yine de kapatılsın mı?", parent=self.kok):
            return
        self.isci.kapat()
        self.isci.thread.join(timeout=8)
        try:
            KOMUT_DURUM.unlink()
        except OSError:
            pass
        self.kok.destroy()


def main(kilit=None, acilis_bilgisi="", acilis_uyarisi=""):
    """kilit: başlatıcının (baslat.pyw) aldığı tek kopya kilidi; verilmezse burada alınır (geliştirme bilgisayarı)."""
    if kilit is None:
        kilit = tek_kopya_kilidi()
        if kilit is None:
            tk.Tk().withdraw()
            messagebox.showinfo(PROGRAM_ADI, "Program zaten açık.")
            return
    kok = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    # E-Mükellef adresi ofise özeldir: tarayıcı işçisi başlamadan önce belli olmalı (ilk açılışta sorulur)
    adres = ayar.emukellef_adresi()
    if not adres:
        kok.withdraw()
        guncelle.onayla()                           # pencere açılabildi: yeni sürüm çalışıyor
        adres = adres_sor(kok)
        if not adres:
            kok.destroy()
            return
        try:
            ayar.ayar_yaz(**{ayar.ADRES_ANAHTARI: adres})
        except OSError as e:
            messagebox.showerror(PROGRAM_ADI, f"Adres kaydedilemedi: {e}")
            kok.destroy()
            return
        kok.deiconify()
    emukellef.adres_ayarla(adres)
    Uygulama(kok, acilis_bilgisi=acilis_bilgisi)
    guncelle.onayla()                               # program açıldı: güncelleme (varsa) kalıcı
    if acilis_uyarisi:
        kok.after(800, lambda: messagebox.showwarning(PROGRAM_ADI, acilis_uyarisi, parent=kok))
    kok.mainloop()


if __name__ == "__main__":
    main()
