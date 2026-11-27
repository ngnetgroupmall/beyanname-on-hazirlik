"""Firma panosu (kontrol masası) görünümü — kullanıcı kararı 27.11.2026 (tasarım: tasarim-onerileri\\claude\\tasarim.html).

Ana ekran, sık kullanılan firmaların seçili dönemdeki durum tablosudur (veri ilerlemesi, faturalar, Luca, KDV2 / MUHSGK / KDV1,
ACİL, son indirme); bir firmaya tıklayınca ayrıntı ve sorgu seçimi sağdaki çekmecede açılır. Klasik ekran kalır (üst bantta geçiş).

Bu modül kendi iş mantığı taşımaz: Uygulama'nın (onhazirlik.py) durum değişkenlerini ve işlevlerini kullanır (aynı BooleanVar'lar,
aynı Başlat/Durdur, aynı toplu işler). Veriler yalnız OKUNUR: önceki indirmeler (onceki_durumu), dönemin beyanname PDF'leri
('Taslak' adlılar verilmiş sayılmaz) ve varsa Beyanname Takip verisi (02_Raporlar\\takip_verisi.json). Bilinmeyen durum '—' kalır.
"""
import json
import re
import threading
import tkinter as tk

import firma_bilgi
from datetime import date, timedelta
from pathlib import Path

AY_KISA = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
BEYAN_TURLERI = ("KDV2", "MUHSGK", "KDV1")
VADE_GUNU = {"KDV2": 25, "MUHSGK": 26, "KDV1": 28}
# hücre biçimleri: (yazı, zemin)
P = {"yesil": ("#1e7a45", "#e3f4ea"), "koyu": ("#ffffff", "#1e8e4e"), "gri": ("#6b7b8d", "#eef1f4"), "kirmizi": ("#b0362a", "#fbe5e3"),
     "mavi": ("#2c5f9e", "#e3edf9"), "sari": ("#9a6408", "#fdf1dc"), "bos": ("#9aa6b2", "#ffffff")}
SUTUNLAR = [("Firma", 185), ("Veriler", 150), ("Faturalar", 72), ("Luca", 58), ("KDV2", 76), ("MUHSGK", 80), ("KDV1", 76),
            ("Uyarı", 120), ("Son indirme", 92)]
CEKMECE = 400                                  # sağ çekmecenin genişliği (piksel)


def vade(tur, ay, yil):
    """Beyanın standart son günü (sonraki ayın 25/26/28'i; hafta sonuysa pazartesi). Resmî tatiller hesaba katılmaz."""
    a, y = (1, yil + 1) if ay == 12 else (ay + 1, yil)
    g = date(y, a, VADE_GUNU[tur])
    while g.weekday() >= 5:
        g += timedelta(days=1)
    return g


def _gun(d):
    return f"{d.day} {AY_KISA[d.month - 1]}"


def _beyan_pdfleri(firma_k, ay, yil):
    """Dönemin VERİLMİŞ beyanname PDF türleri (adında 'Taslak' geçenler sayılmaz)."""
    bulunan = set()
    for hedef, turler in ((firma_k / "KDV" / f"{yil}-{ay:02d}" / "04_Beyannameler", ("KDV1", "KDV2")),
                          (firma_k / "Beyannameler" / "MUHSGK" / f"{yil}-{ay:02d}", ("MUHSGK",))):
        try:
            adlar = [p.name for p in hedef.glob("*.pdf")] if hedef.is_dir() else []
        except OSError:
            continue
        for tur in turler:
            if any(re.search(rf"(?<![0-9A-Za-z]){tur}\s+Beyanname(?![0-9A-Za-z])", a, re.I) and not re.search(r"taslak", a, re.I) for a in adlar):
                bulunan.add(tur)
    return bulunan


def _takip(donem_klasoru, ay, yil):
    """02_Raporlar\\takip_verisi.json'daki beyanlar (tur -> kayıt); yoksa, bozuksa ya da dönem tutmuyorsa {}."""
    try:
        d = json.loads((Path(donem_klasoru) / "02_Raporlar" / "takip_verisi.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict) or d.get("yil") != yil or d.get("ay") != ay or not isinstance(d.get("beyanlar"), list):
        return {}
    return {b["tur"]: b for b in d["beyanlar"] if isinstance(b, dict) and b.get("tur") in BEYAN_TURLERI}


def beyan_hucresi(tur, ay, yil, pdf, takip, bugun=None, kdv2_odenen=None):
    """-> (yazı, biçim, bekliyor mu). Bilinmeyen durum '—' (gri) kalır; PDF yokluğu tek başına 'bekliyor' sayılmaz.
    kdv2_odenen: KDV 2 Ödeme Sorgulama'da bu vergi dönemi için görülen 4017 ödemelerinin toplamı (damga dahil olabilir)."""
    bugun = bugun or date.today()
    t = takip.get(tur)
    if t and str(t.get("beyan_durum") or "") == "yok":
        return "Yok", "gri", False
    if tur == "KDV2" and kdv2_odenen:
        try:
            odenecek = float((t or {}).get("odenecek") or 0)
        except (TypeError, ValueError):
            odenecek = 0
        if odenecek and kdv2_odenen + 1 < odenecek:
            return "Kısmi ödeme", "sari", True
        return "Ödendi", "koyu", False
    if t:
        bd, od = str(t.get("beyan_durum") or ""), str(t.get("odeme_durum") or "")
        if bd == "verildi" or tur in pdf:
            return ("Ödendi" if tur == "KDV2" and od == "odendi" else "Verildi"), "koyu", False
        try:
            sg = date.fromisoformat(str(t.get("son_gun") or ""))
        except ValueError:
            sg = vade(tur, ay, yil)
        return ("Süre geçti", "kirmizi", True) if sg < bugun else (_gun(sg), "sari", True)
    if tur in pdf:
        return "Verildi", "koyu", False
    return "—", "bos", False


def satir_oku(oh, t, ay, yil, kok):
    """Bir firmanın pano satırı (yalnız okur). oh: onhazirlik modülü (onceki_durumu, SORGULAR, LUCA_SORGULARI, cikti)."""
    s = {"id": t["id"], "ad": t.get("ad") or oh.kisa_ad_oner(t["unvan"]), "unvan": t["unvan"], "vkn": t.get("vkn") or "",
         "klasor": t.get("klasor"), "luca_id": t.get("luca") or "", "sonuclar": {}, "acil": [], "fatura": 0, "son": "", "kok": kok,
         "beyan": {}, "okuma_hatasi": ""}
    if t.get("klasor"):
        try:
            with oh._ONCEKI_KILIT:
                v = oh.onceki_durumu(t["klasor"], ay, yil)
            s["sonuclar"] = {a: o["durum"] for a, o in (v.get("sonuclar") or {}).items()}
            s["acil"] = [oh.KISA_AD.get(a, a) for a, o in (v.get("sonuclar") or {}).items() if o.get("acil")]
            s["fatura"], s["son"] = v.get("fatura") or 0, v.get("son") or ""
            firma_k = oh.cikti.firma_klasoru(t["klasor"])
            pdf = _beyan_pdfleri(firma_k, ay, yil)
            takip = _takip(v.get("donem_klasoru") or firma_k / "KDV" / f"{yil}-{ay:02d}", ay, yil)
            odenen = ((v.get("sonuclar") or {}).get("kdv2") or {}).get("odemeler", {}).get(f"{ay:02d}/{yil}")
        except Exception as e:                      # okuma hatası satırda görünür; pano çalışmayı sürdürür
            s["okuma_hatasi"] = f"{type(e).__name__}"
            pdf, takip, odenen = set(), {}, None
    else:
        pdf, takip, odenen = set(), {}, None
    for tur in BEYAN_TURLERI:
        s["beyan"][tur] = beyan_hucresi(tur, ay, yil, pdf, takip, kdv2_odenen=odenen)
    return s


class Pano(tk.Frame):
    def __init__(self, ust, uyg, oh):
        self.oh, self.uyg, R = oh, uyg, oh.R
        super().__init__(ust, bg=R["zemin"])
        self.R, self.YAZI = R, oh.YAZI
        self.satirlar = {}                          # firma no -> satır verisi
        self.okunan_donem = None
        self.okuma_sira = 0
        self.yenile_is = None
        self.bekleyen_idler = set()
        self.filtre = "tum"
        self.acik_grup = None                       # çekmecede ayrıntısı açık grup (başta kapalı: yedi grup sığsın)
        self.satir_wl = {}                          # firma no -> {hücre adı: widget}
        self._kur()

    # ------------------------------------------------------------ kurulum
    def _kur(self):
        R, Y = self.R, self.YAZI
        ust = tk.Frame(self, bg=R["zemin"])
        ust.pack(fill="x", padx=14, pady=(10, 0))
        d = tk.Frame(ust, bg=R["zemin"])
        d.pack(side="left")
        self._kucuk_dugme(d, "◂", lambda: self.uyg._donem_kaydir(-1)).pack(side="left")
        self.donem_l = tk.Label(d, text="", bg=R["zemin"], fg=R["yazi"], font=(Y, 17, "bold"), width=12)
        self.donem_l.pack(side="left", padx=6)
        self._kucuk_dugme(d, "▸", lambda: self.uyg._donem_kaydir(1)).pack(side="left")
        self.vade_f = tk.Frame(ust, bg=R["zemin"])
        self.vade_f.pack(side="left", padx=(16, 0))
        self.kpi_f = tk.Frame(self, bg=R["zemin"])
        self.kpi_f.pack(fill="x", padx=14, pady=(10, 0))
        self.kpi = []
        for n in range(5):
            k = tk.Frame(self.kpi_f, bg=R["kart"], highlightthickness=1, highlightbackground=R["cizgi"])
            k.grid(row=0, column=n, sticky="we", padx=(0 if n == 0 else 10, 0))
            self.kpi_f.grid_columnconfigure(n, weight=1, uniform="kpi")
            sayi = tk.Label(k, text="", bg=R["kart"], font=(Y, 20, "bold"))
            sayi.pack(side="left", padx=(12, 8), pady=6)
            yazi = tk.Label(k, text="", bg=R["kart"], fg=R["gri"], font=(Y, 9), justify="left", anchor="w")
            yazi.pack(side="left", fill="x")
            self.kpi.append((k, sayi, yazi))

        ana = tk.Frame(self, bg=R["zemin"])
        ana.pack(fill="both", expand=True, padx=14, pady=10)
        # ---- tablo kartı
        tk_ = tk.Frame(ana, bg=R["kart"], highlightthickness=1, highlightbackground=R["cizgi"])
        tk_.pack(side="left", fill="both", expand=True)
        arac = tk.Frame(tk_, bg=R["kart"])
        arac.pack(fill="x", padx=10, pady=8)
        tk.Label(arac, text="Firmalar", bg=R["kart"], fg=R["yazi"], font=(Y, 11, "bold")).pack(side="left", padx=(0, 8))
        self.filtre_d = {}
        for anahtar, ad in (("tum", "Tümü"), ("bekleyen", "Beyan bekleyen"), ("acil", "ACİL"), ("hata", "Hatalı")):
            b = tk.Label(arac, text=ad, font=(Y, 9), padx=9, pady=2, cursor="hand2", bd=0)
            b.pack(side="left", padx=(0, 5))
            b.bind("<Button-1>", lambda e, a=anahtar: self._filtre(a))
            self.filtre_d[anahtar] = (b, ad)
        l = tk.Label(arac, text="+ Listeyi düzenle", bg=R["kart"], fg=R["mavi"], font=(Y, 9, "underline"), cursor="hand2")
        l.pack(side="left", padx=(6, 0))
        l.bind("<Button-1>", lambda e: self.uyg._sik_duzenle())
        self.b_toplu = self._dugme(arac, "▶  Tümünü sorgula", lambda: self.uyg._toplu_baslat(), dolu=True)
        self.b_toplu.pack(side="right")
        self.b_beyan = self._dugme(arac, "⇩  Beyannameleri indir", lambda: self.uyg._toplu_beyan_baslat())
        self.b_beyan.pack(side="right", padx=(0, 6))
        self.b_aktar = self._dugme(arac, "⇪  GİB'den aktar", lambda: self.uyg._gib_aktar())
        self.b_aktar.pack(side="right", padx=(0, 6))
        tk.Frame(tk_, bg=R["cizgi"], height=1).pack(fill="x")
        self.bas_f = bas = tk.Frame(tk_, bg="#fafbfc")
        bas.pack(fill="x")
        for n, (ad, gen) in enumerate(SUTUNLAR):
            bas.grid_columnconfigure(n, minsize=gen, weight=1 if n == 0 else 0)
            tk.Label(bas, text=ad.upper(), bg="#fafbfc", fg="#6b7b8d", font=(Y, 8, "bold"), anchor="w", padx=0).grid(row=0, column=n, sticky="we", padx=8, pady=6)
        tk.Frame(tk_, bg=R["cizgi"], height=1).pack(fill="x")
        govde = tk.Frame(tk_, bg=R["kart"])
        govde.pack(fill="both", expand=True)
        self.tuval = tk.Canvas(govde, bg=R["kart"], highlightthickness=0, bd=0)
        kay = tk.Scrollbar(govde, orient="vertical", command=self.tuval.yview)
        self.tuval.configure(yscrollcommand=kay.set)
        kay.pack(side="right", fill="y")
        self.tuval.pack(side="left", fill="both", expand=True)
        self.tablo = tk.Frame(self.tuval, bg=R["kart"])
        self.tablo_id = self.tuval.create_window(0, 0, window=self.tablo, anchor="nw")
        self.tablo.bind("<Configure>", lambda e: (self.tuval.configure(scrollregion=self.tuval.bbox("all")), self.after_idle(self._hizala)))
        self.tuval.bind("<Configure>", lambda e: self.tuval.itemconfigure(self.tablo_id, width=e.width))
        self.tuval.bind("<Enter>", lambda e: self.tuval.bind_all("<MouseWheel>", self._tekerlek))
        self.tuval.bind("<Leave>", lambda e: self.tuval.unbind_all("<MouseWheel>"))
        for n, (_, gen) in enumerate(SUTUNLAR):
            self.tablo.grid_columnconfigure(n, minsize=gen, weight=1 if n == 0 else 0)
        serit = tk.Frame(tk_, bg="#fafbfc", highlightthickness=1, highlightbackground="#eef1f4")
        serit.pack(fill="x", side="bottom")
        self.serit_l = tk.Label(serit, text="", bg="#fafbfc", fg="#46586b", font=("Consolas", 9), anchor="w", width=1)    # uzun satır genişletmesin
        self.serit_l.pack(side="left", fill="x", expand=True, padx=10, pady=5)
        g = tk.Label(serit, text="Günlüğü aç ▸", bg="#fafbfc", fg=R["mavi"], font=(Y, 9), cursor="hand2")
        g.pack(side="right", padx=10)
        g.bind("<Button-1>", lambda e: self.uyg._gorunum_uygula("klasik"))

        # ---- çekmece
        self.cek = tk.Frame(ana, bg=R["kart"], highlightthickness=1, highlightbackground="#c9d6e4", width=CEKMECE)
        self.cek.pack(side="right", fill="y", padx=(12, 0), before=tk_)     # çekmece yerini önce alır; tablo kalanı doldurur
        self.cek.pack_propagate(False)
        tk.Frame(self.cek, bg="#2c6fb7", width=3).pack(side="left", fill="y")
        ic = tk.Frame(self.cek, bg=R["kart"])
        ic.pack(side="left", fill="both", expand=True)
        self.cek_bas = tk.Frame(ic, bg=R["kart"])
        self.cek_bas.pack(fill="x", padx=14, pady=(12, 6))
        self.cek_ad = tk.Label(self.cek_bas, text="", bg=R["kart"], fg=R["yazi"], font=(Y, 12, "bold"), anchor="w", justify="left", wraplength=CEKMECE - 90)
        self.cek_ad.pack(fill="x")
        self.cek_alt = tk.Label(self.cek_bas, text="", bg=R["kart"], fg=R["gri"], font=(Y, 9), anchor="w", justify="left", wraplength=CEKMECE - 50)
        self.cek_alt.pack(fill="x")
        self.cek_roz = tk.Frame(self.cek_bas, bg=R["kart"])
        self.cek_roz.pack(fill="x", pady=(6, 0))
        tk.Frame(ic, bg=R["cizgi"], height=1).pack(fill="x")
        self.cek_alt_e = tk.Frame(ic, bg=R["kart"])
        self.cek_alt_e.pack(side="bottom", fill="x", padx=14, pady=10)
        self.b_basla = tk.Button(self.cek_alt_e, text="", command=lambda: self.uyg._baslat_durdur(), relief="flat", bd=0, cursor="hand2",
                                 font=(Y, 12, "bold"), bg="#1e8e4e", fg="white", activebackground="#22994a", activeforeground="white",
                                 disabledforeground="#cfe8d6", pady=8)
        self.b_basla.pack(fill="x")
        iki = tk.Frame(self.cek_alt_e, bg=R["kart"])
        iki.pack(fill="x", pady=(7, 0))
        iki.grid_columnconfigure(0, weight=1, uniform="iki")
        iki.grid_columnconfigure(1, weight=1, uniform="iki")
        self.b_rapor = self._dugme(iki, "Kâr / zarar raporu", lambda: self.uyg._kdvkar_raporu())
        self.b_rapor.grid(row=0, column=0, sticky="we")
        self.b_ver = self._dugme(iki, "Beyanname indir", lambda: self.uyg._verilen_indir())
        self.b_ver.grid(row=0, column=1, sticky="we", padx=(6, 0))
        tk.Frame(ic, bg=R["cizgi"], height=1).pack(fill="x", side="bottom")
        # grup listesi kaydırılır (27.11.2026): kısayol düğmeleri + 7 grup küçük ekrana sığmıyor, alttaki gruplar (Luca) görünmüyordu
        kap = tk.Frame(ic, bg=R["kart"])
        kap.pack(fill="both", expand=True, padx=(14, 2), pady=8)
        self.cek_tuval = tk.Canvas(kap, bg=R["kart"], highlightthickness=0, bd=0)
        self.cek_kaydirac = tk.Scrollbar(kap, orient="vertical", command=self.cek_tuval.yview)
        self.cek_tuval.configure(yscrollcommand=self.cek_kaydirac.set)
        self.cek_tuval.pack(side="left", fill="both", expand=True)
        self.cek_ic = tk.Frame(self.cek_tuval, bg=R["kart"])
        self.cek_ic_id = self.cek_tuval.create_window(0, 0, window=self.cek_ic, anchor="nw")
        self.cek_ic.bind("<Configure>", lambda e: self._cek_kaydir_guncelle())
        self.cek_tuval.bind("<Configure>", lambda e: (self.cek_tuval.itemconfigure(self.cek_ic_id, width=e.width),
                                                      self._cek_kaydir_guncelle()))
        self.cek_tuval.bind("<Enter>", lambda e: self.cek_tuval.bind_all("<MouseWheel>", self._cek_tekerlek))
        self.cek_tuval.bind("<Leave>", lambda e: self.cek_tuval.unbind_all("<MouseWheel>"))
        for gv in self.uyg.grup_var:
            gv.trace_add("write", lambda *_: self.after_idle(self._cekmece_gruplar))
        self._filtre_ciz()
        self.donem_degisti()

    def _dugme(self, ust, metin, komut, dolu=False):
        """Çerçeveli düğme: dönen Frame'in .d'si asıl düğmedir (pack/grid Frame'e yapılır, state .d'ye)."""
        R = self.R
        c = tk.Frame(ust, bg="#1e8e4e" if dolu else R["lacivert"])
        b = tk.Button(c, text=metin, command=komut, relief="flat", bd=0, cursor="hand2", font=(self.YAZI, 9, "bold"),
                      bg="#1e8e4e" if dolu else R["kart"], fg="white" if dolu else R["lacivert"],
                      activebackground="#22994a" if dolu else R["secili_z"], activeforeground="white" if dolu else R["lacivert"],
                      disabledforeground=R["acik_gri"], padx=11, pady=4)
        b.pack(fill="both", expand=True, padx=1, pady=1)
        c.d = b
        return c

    def _kucuk_dugme(self, ust, metin, komut):
        return tk.Button(ust, text=metin, command=komut, relief="flat", bd=0, cursor="hand2", font=(self.YAZI, 10), bg=self.R["kart"],
                         fg=self.R["lacivert"], activebackground=self.R["secili_z"], width=3, highlightthickness=1,
                         highlightbackground=self.R["dugme_k"])

    def _hap(self, ust, metin, bicim, kalin=True):
        fg, bg = P[bicim]
        return tk.Label(ust, text=metin, fg=fg, bg=bg, font=(self.YAZI, 8, "bold" if kalin else "normal"), padx=7, pady=1)

    def _hizala(self):
        """Başlık satırı ayrı ızgarada (kaydırılmaz); sütun genişliklerini tablonun gerçek sütun genişliklerine eşitler."""
        if not self.satir_wl:
            return
        basliklar = {int(w.grid_info()["column"]): w for w in self.bas_f.grid_slaves(row=0)}
        degisti = False
        for n, (_, gen) in enumerate(SUTUNLAR):
            bb = self.tablo.grid_bbox(n, 0)
            if not bb or bb[2] <= 1:
                continue
            gerek = basliklar[n].winfo_reqwidth() + 16 if n in basliklar else 0        # başlık yazısı + iki yanda 8 piksel
            if n and gerek > bb[2] and int(self.tablo.grid_columnconfigure(n, "minsize")) < gerek:
                self.tablo.grid_columnconfigure(n, minsize=gerek)                    # dar sütun başlığa göre genişler
                degisti = True
            self.bas_f.grid_columnconfigure(n, minsize=max(bb[2], gerek if n else 0), weight=0)
        if degisti:
            self.after_idle(self._hizala)

    def _tekerlek(self, e):
        self.tuval.yview_scroll(int(-e.delta / 120), "units")

    # ------------------------------------------------------------ dışarıdan çağrılanlar (onhazirlik.py kancaları)
    def donem_degisti(self):
        ay, yil = self.uyg.ay_no, self.uyg.yil_no
        self.donem_l.configure(text=f"{self.oh.cikti.AYLAR[ay - 1]} {yil}")
        for w in self.vade_f.winfo_children():
            w.destroy()
        for tur in BEYAN_TURLERI:
            f = tk.Label(self.vade_f, text=f"{tur} son gün  {_gun(vade(tur, ay, yil))}", bg="#1f3756", fg="white", font=(self.YAZI, 9),
                         padx=9, pady=4)
            f.pack(side="left", padx=(0, 6))
        if self.okunan_donem != (ay, yil):
            self.satirlar = {}
            self._ciz()
        self.yenile()

    def secim_degisti(self):
        self._satir_vurgula()
        self._cekmece()

    def yenile(self, idler=None, gecikme=250):
        """Pano verisini arka planda yeniden okur (idler verilirse yalnız o firmalar). İstekler birleştirilir."""
        if idler is None:
            self.bekleyen_idler = None
        elif self.bekleyen_idler is not None:
            self.bekleyen_idler |= set(idler)
        if self.yenile_is is not None:
            try:
                self.after_cancel(self.yenile_is)
            except tk.TclError:
                pass
        self.yenile_is = self.after(gecikme, self._oku)

    def veri_geldi(self, v):
        """İşçi dışı okuma iş parçacığının sonucu (olaylar kuyruğundan)."""
        if v.get("sira") != self.okuma_sira or v.get("donem") != (self.uyg.ay_no, self.uyg.yil_no) or v.get("kok") != str(self.oh.cikti.FIRMALAR):
            return
        if v.get("tam"):
            self.satirlar = {s["id"]: s for s in v["satirlar"]}
        else:
            for s in v["satirlar"]:
                self.satirlar[s["id"]] = s
        self.okunan_donem = v["donem"]
        self._ciz()

    def dugmeler(self):
        u = self.uyg
        mesgul = bool(u.calisiyor or u.kosu or u.beyan_kosu or u.toplu)
        for b in (self.b_toplu, self.b_beyan, self.b_aktar, self.b_rapor, self.b_ver):
            try:
                b.d.configure(state="disabled" if mesgul else "normal")
            except tk.TclError:
                pass
        if u.kosu or u.beyan_kosu:
            self.b_basla.configure(text="■  Toplu işi durdur" if u.toplu else "■  Durdur", bg=self.R["kirmizi"], activebackground="#c0392b",
                                   state="normal")
        else:
            t = u._secili()
            n = sum(v.get() for v in u.secim.values())
            ad = (t.get("ad") or self.oh.kisa_ad_oner(t["unvan"])) if t else "Firma seçin"
            self.b_basla.configure(text=f"▶  Sorgula  ·  {n} sorgu" if t else "Firma seçin", bg="#1e8e4e", activebackground="#22994a",
                                   state="normal" if (t and not u.calisiyor) else "disabled")

    def son_satir(self, zaman, metin):
        self.serit_l.configure(text=f"{zaman}  {metin[:150]}", fg=self.R["kirmizi"] if "HATA" in metin else "#46586b")

    def tik(self):
        """Saniyede bir: çalışan firmanın satırı ve çekmece canlı güncellenir."""
        if self.winfo_ismapped() and self.uyg.kosu:
            self._canli_hucre()
            self._cekmece_gruplar()

    # ------------------------------------------------------------ okuma
    def _sik_firmalar(self):
        return [t for t in (next((x for x in self.uyg.tum if x["id"] == i), None) for i in self.uyg._sik_idler()) if t]

    def _oku(self):
        self.yenile_is = None
        firmalar = self._sik_firmalar()
        idler, self.bekleyen_idler = self.bekleyen_idler, set()
        tam = idler is None or self.okunan_donem != (self.uyg.ay_no, self.uyg.yil_no)
        if not tam:
            firmalar = [t for t in firmalar if t["id"] in idler]
            if not firmalar:
                return
        self.okuma_sira += 1
        sira, ay, yil, kok = self.okuma_sira, self.uyg.ay_no, self.uyg.yil_no, str(self.oh.cikti.FIRMALAR)
        kuyruk, oh = self.uyg.olaylar, self.oh
        firmalar = [dict(t) for t in firmalar]      # iş parçacığı arayüz listesine dokunmaz

        def calis():
            satirlar = []
            for t in firmalar:
                if sira != self.okuma_sira or str(oh.cikti.FIRMALAR) != kok:
                    return                          # eskimiş istek ya da kayıt klasörü değişti
                satirlar.append(satir_oku(oh, t, ay, yil, kok))
            kuyruk.put(("pano_veri", {"sira": sira, "donem": (ay, yil), "kok": kok, "tam": tam, "satirlar": satirlar}))
        threading.Thread(target=calis, name="pano-okuma", daemon=True).start()

    # ------------------------------------------------------------ çizim
    def _filtre(self, a):
        self.filtre = a
        self._filtre_ciz()
        self._ciz()

    def _filtre_ciz(self):
        R = self.R
        sayi = self._sayilar()
        for a, (b, ad) in self.filtre_d.items():
            n = {"tum": sayi["firma"], "bekleyen": sayi["bekleyen_firma"], "acil": sayi["acil"], "hata": sayi["hata"]}[a]
            secili = self.filtre == a
            kirmizi = a == "acil" and n
            b.configure(text=f"{ad} {n}", bg=R["lacivert"] if secili else (R["kirmizi_z"] if kirmizi else "#f3f5f8"),
                        fg="white" if secili else (R["kirmizi"] if kirmizi else "#3a4b5d"))

    def _sayilar(self):
        s = list(self.satirlar.values())
        bekleyen = [(tur, b) for x in s for tur, b in x["beyan"].items() if b[2]]
        return {"firma": len(self._sik_firmalar()), "tam": sum(1 for x in s if self._ilerleme(x)[0] and not self._hata_n(x)),
                "bekleyen": len(bekleyen), "bekleyen_firma": sum(1 for x in s if any(b[2] for b in x["beyan"].values())),
                "acil": sum(1 for x in s if x["acil"]), "hata": sum(1 for x in s if self._hata_n(x))}

    @staticmethod
    def _hata_n(x):
        return sum(1 for d in x["sonuclar"].values() if d == "hata")

    def _ilerleme(self, x):
        toplam = len(self.oh.SORGULAR)
        return sum(1 for d in x["sonuclar"].values() if d in ("veri", "kayit_yok")), toplam

    def _kpi_ciz(self):
        R, s = self.R, self._sayilar()
        ay, yil = self.uyg.ay_no, self.uyg.yil_no
        yakin = sorted(b[0] for x in self.satirlar.values() for b in x["beyan"].values() if b[2] and b[1] == "sari")
        degerler = [(s["firma"], f"sık kullanılan firma\n{self.oh.cikti.AYLAR[ay - 1]} {yil}", R["yazi"], False),
                    (s["tam"], "firmanın verileri\nhatasız indirilmiş", R["yesil"], False),
                    (s["bekleyen"], "beyan bekliyor" + ("\n(Beyanname Takip verisinden)" if s["bekleyen"] else "\nTakip verisi olan firmalarda"), R["sari"], False),
                    (s["acil"], "firmada ACİL uyarı", R["kirmizi"], bool(s["acil"])),
                    (s["hata"], "firmada hatalı sorgu", R["yazi"], False)]
        for (k, sayi, yazi), (n, metin, renk, vurgu) in zip(self.kpi, degerler):
            zemin = "#fff6f5" if vurgu else R["kart"]
            for w in (k, sayi, yazi):
                w.configure(bg=zemin)
            k.configure(highlightbackground="#ebb0a9" if vurgu else R["cizgi"])
            sayi.configure(text=str(n), fg=renk)
            yazi.configure(text=metin)
        _ = yakin

    def _gorunur_satirlar(self):
        sira = [t["id"] for t in self._sik_firmalar()]
        cikis = []
        for i in sira:
            x = self.satirlar.get(i)
            if x is None:
                t = next((t for t in self.uyg.tum if t["id"] == i), None)
                x = {"id": i, "ad": (t.get("ad") or self.oh.kisa_ad_oner(t["unvan"])) if t else str(i), "vkn": (t or {}).get("vkn") or "",
                     "klasor": (t or {}).get("klasor"), "sonuclar": {}, "acil": [], "fatura": 0, "son": "", "beyan": {}, "yukleniyor": True}
            if self.filtre == "bekleyen" and not any(b[2] for b in x["beyan"].values()):
                continue
            if self.filtre == "acil" and not x["acil"]:
                continue
            if self.filtre == "hata" and not self._hata_n(x):
                continue
            cikis.append(x)
        return cikis

    def _ciz(self):
        R, Y = self.R, self.YAZI
        for w in self.tablo.winfo_children():
            w.destroy()
        self.satir_wl = {}
        satirlar = self._gorunur_satirlar()
        if not self._sik_firmalar():
            tk.Label(self.tablo, text="Sık kullanılan firma listesi boş.\n\n'+ Listeyi düzenle' ile her gün çalıştığınız firmaları ekleyin;\n"
                                      "pano bu firmaların dönem durumunu gösterir.", bg=R["kart"], fg=R["gri"], font=(Y, 10),
                     justify="center").grid(row=0, column=0, columnspan=len(SUTUNLAR), pady=60)
        elif not satirlar:
            tk.Label(self.tablo, text="Bu süzgece uyan firma yok.", bg=R["kart"], fg=R["gri"], font=(Y, 10)).grid(
                row=0, column=0, columnspan=len(SUTUNLAR), pady=40)
        for r, x in enumerate(satirlar):
            self._satir(r * 2, x)
            tk.Frame(self.tablo, bg="#eef1f4", height=1).grid(row=r * 2 + 1, column=0, columnspan=len(SUTUNLAR), sticky="we")
        self._satir_vurgula()
        self._canli_hucre()
        self._filtre_ciz()
        self._kpi_ciz()
        self._cekmece()

    def _satir(self, r, x):
        R, Y = self.R, self.YAZI
        w = {}

        def hucre(col, **kw):
            f = tk.Frame(self.tablo, bg=R["kart"])
            f.grid(row=r, column=col, sticky="nsew")
            w.setdefault("hucreler", []).append(f)
            return f
        f = hucre(0)
        # tek satır (kullanıcı isteği 27.11.2026: VKN yazılmaz, ekrana daha çok firma sığsın); VKN çekmecede görünür
        tk.Label(f, text=x["ad"], bg=R["kart"], fg=R["yazi"], font=(Y, 10, "bold"), anchor="w").pack(side="left", padx=(10, 4), pady=6)
        if not x.get("klasor"):
            tk.Label(f, text="klasör yok", bg=R["kart"], fg=R["sari"], font=(Y, 8), anchor="w").pack(side="left", pady=6)
        f = hucre(1)
        cub = tk.Canvas(f, width=86, height=8, bg=R["kart"], highlightthickness=0)
        cub.pack(side="left", padx=(8, 6), pady=8)
        yz = tk.Label(f, text="", bg=R["kart"], fg="#8492a2", font=(Y, 9))
        yz.pack(side="left")
        w["cubuk"], w["ilerleme"] = cub, yz
        self._cubuk_ciz(x, cub, yz)
        f = hucre(2)
        if x.get("yukleniyor"):
            tk.Label(f, text="okunuyor…", bg=R["kart"], fg="#9aa6b2", font=(Y, 8)).pack(anchor="w", padx=8, pady=6)
        elif x["fatura"]:
            self._hap(f, str(x["fatura"]), "yesil").pack(anchor="w", padx=8, pady=6)
        else:
            tk.Label(f, text="—", bg=R["kart"], fg="#9aa6b2", font=(Y, 9)).pack(anchor="w", padx=8, pady=6)
        f = hucre(3)
        metin, bicim = self._luca(x)
        (self._hap(f, metin, bicim) if bicim != "bos" else tk.Label(f, text=metin, bg=R["kart"], fg="#9aa6b2", font=(Y, 9))).pack(anchor="w", padx=8, pady=6)
        for n, tur in enumerate(BEYAN_TURLERI):
            f = hucre(4 + n)
            b = x["beyan"].get(tur)
            if not b or b[1] == "bos":
                tk.Label(f, text="—", bg=R["kart"], fg="#9aa6b2", font=(Y, 9)).pack(anchor="w", padx=8, pady=6)
            else:
                self._hap(f, b[0], b[1]).pack(anchor="w", padx=8, pady=6)
        f = hucre(7)
        if x["acil"]:
            tk.Label(f, text="● " + x["acil"][0][:16] + (f" +{len(x['acil']) - 1}" if len(x["acil"]) > 1 else ""), bg=R["kart"], fg=R["kirmizi"],
                     font=(Y, 9, "bold"), anchor="w").pack(fill="x", padx=8, pady=6)
        elif x.get("okuma_hatasi"):
            tk.Label(f, text="okunamadı", bg=R["kart"], fg=R["sari"], font=(Y, 9), anchor="w").pack(fill="x", padx=8, pady=6)
        else:
            tk.Label(f, text="—", bg=R["kart"], fg="#9aa6b2", font=(Y, 9), anchor="w").pack(fill="x", padx=8, pady=6)
        f = hucre(8)
        son = x["son"]
        bugun = date.today().strftime("%d.%m.%Y")
        metin = ("bugün " + son[11:16]) if son.startswith(bugun) else (son[:5] + " " + son[11:16] if son else "hiç indirilmedi")
        w["son"] = tk.Label(f, text=metin, bg=R["kart"], fg="#8492a2", font=(Y, 9), anchor="w")
        w["son"].pack(fill="x", padx=8, pady=6)
        for h in w["hucreler"]:
            for el in [h] + list(h.winfo_children()):
                el.bind("<Button-1>", lambda e, i=x["id"]: self._sec(i))
                el.configure(cursor="hand2")
        self.satir_wl[x["id"]] = w

    def _cubuk_ciz(self, x, cub, yz, canli=None):
        cub.delete("all")
        cub.create_rectangle(0, 0, 86, 8, fill="#e6ebf0", outline="")
        if canli is not None:
            bitti, toplam, hata = canli
            if toplam:
                cub.create_rectangle(0, 0, 86 * bitti / toplam, 8, fill="#2c6fb7", outline="")
            yz.configure(text=f"çalışıyor {bitti}/{toplam}", fg="#2c5f9e")
            return
        n, toplam = self._ilerleme(x)
        h = self._hata_n(x)
        if toplam:
            cub.create_rectangle(0, 0, 86 * n / toplam, 8, fill="#1e8e4e", outline="")
            if h:
                cub.create_rectangle(86 * n / toplam, 0, 86 * (n + h) / toplam, 8, fill="#c0392b", outline="")
        yz.configure(text=f"{n}/{toplam}" + (f" · {h} hata" if h else ""), fg=self.R["kirmizi"] if h else "#8492a2")

    def _luca(self, x):
        luca = [x["sonuclar"].get(a) for a in self.oh.LUCA_SORGULARI]
        if any(d == "hata" for d in luca):
            return "hata", "kirmizi"
        alinan = sum(1 for d in luca if d in ("veri", "kayit_yok"))
        if alinan and alinan == len(luca):
            return "tam", "yesil"
        if alinan:
            return f"{alinan}/{len(luca)}", "mavi"
        return ("yok" if x["sonuclar"] else "—"), ("gri" if x["sonuclar"] else "bos")

    def _canli_hucre(self):
        """Çalışan işin hedef firmasının satırında ilerleme (kosu_hedef: firma, ay, yıl)."""
        u = self.uyg
        hedef = u.kosu_hedef if u.kosu else None
        for i, w in self.satir_wl.items():
            x = self.satirlar.get(i)
            if x is None:
                continue
            if hedef and hedef[0] == i and hedef[1:] == (u.ay_no, u.yil_no):
                d = list(u.sorgu_durumu.values())
                bitti = sum(1 for v in d if v in ("veri", "kayit_yok", "hata", "calistirilmadi"))
                self._cubuk_ciz(x, w["cubuk"], w["ilerleme"], canli=(bitti, len(d), 0))
                w["son"].configure(text="şimdi · çalışıyor", fg="#2c5f9e")
            elif w.get("canliydi"):
                self._cubuk_ciz(x, w["cubuk"], w["ilerleme"])
            w["canliydi"] = bool(hedef and hedef[0] == i)

    def _satir_vurgula(self):
        R = self.R
        for i, w in self.satir_wl.items():
            se = i == self.uyg.secili_id
            zemin = "#eaf2fb" if se else R["kart"]
            for h in w["hucreler"]:
                h.configure(bg=zemin)
                for el in h.winfo_children():
                    if isinstance(el, tk.Canvas):
                        el.configure(bg=zemin)
                    elif isinstance(el, tk.Label) and el.cget("bg") in (R["kart"], "#eaf2fb"):
                        el.configure(bg=zemin)
            w["hucreler"][0].configure(highlightthickness=0)

    def _sec(self, i):
        self.uyg._firma_sec(i)                       # Klasik ekranla aynı seçim (Başlat bu firmayı çalıştırır)

    # ------------------------------------------------------------ çekmece
    def _cekmece(self):
        R, Y, u = self.R, self.YAZI, self.uyg
        t = u._secili()
        for w in self.cek_roz.winfo_children():
            w.destroy()
        if not t:
            self.cek_ad.configure(text="Firma seçilmedi")
            self.cek_alt.configure(text="Tablodan bir firmaya tıklayın.")
        else:
            self.cek_ad.configure(text=self.oh.unvan_sade(t["unvan"]))
            ay, yil = u.ay_no, u.yil_no
            self.cek_alt.configure(text=f"VKN {t.get('vkn') or '—'}\n" + (f"Klasör: {t['klasor']}\\KDV\\{yil}-{ay:02d}" if t.get("klasor")
                                                                        else "Klasör tanımlı değil (Başlat'ta sorulur)"))
            x = self.satirlar.get(t["id"]) or {}
            self._hap(self.cek_roz, "Klasör tanımlı" if t.get("klasor") else "Klasör yok", "yesil" if t.get("klasor") else "sari").pack(side="left", padx=(0, 5))
            self._hap(self.cek_roz, "Luca tanımlı" if t.get("luca") else "Luca tanımsız", "yesil" if t.get("luca") else "gri").pack(side="left", padx=(0, 5))
            if x.get("son"):
                self._hap(self.cek_roz, "Son: " + x["son"][:5] + " " + x["son"][11:16], "mavi", kalin=False).pack(side="left")
        self._cekmece_gruplar()
        self.dugmeler()

    def _cek_kaydir_guncelle(self):
        """Kaydırma alanını içeriğe göre ayarlar; içerik sığıyorsa kaydırma çubuğu gizlenir ve en üste dönülür."""
        t = self.cek_tuval
        t.configure(scrollregion=(0, 0, t.winfo_width(), self.cek_ic.winfo_reqheight()))
        sigmiyor = self.cek_ic.winfo_reqheight() > t.winfo_height() > 1
        if sigmiyor and not self.cek_kaydirac.winfo_ismapped():
            self.cek_kaydirac.pack(side="right", fill="y", before=t)
        elif not sigmiyor and self.cek_kaydirac.winfo_ismapped():
            self.cek_kaydirac.pack_forget()
            t.yview_moveto(0)

    def _cek_tekerlek(self, e):
        if self.cek_ic.winfo_reqheight() > self.cek_tuval.winfo_height():
            self.cek_tuval.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")

    def _grup_durumu(self, anahtarlar, kaynak):
        d = [kaynak.get(a) for a in anahtarlar if kaynak.get(a)]
        if not d:
            return "", "bos"
        if "calisiyor" in d:
            return "çalışıyor", "mavi"
        if "hata" in d:
            return "hata", "kirmizi"
        if "bekliyor" in d:
            return "sırada", "gri"
        if all(x == "kayit_yok" for x in d):
            return "kayıt yok", "gri"
        if all(x in ("veri", "kayit_yok") for x in d):
            return ("tamam" if len(d) == len(anahtarlar) else f"{len(d)}/{len(anahtarlar)}"), "yesil"
        return f"{sum(1 for x in d if x in ('veri', 'kayit_yok'))}/{len(anahtarlar)}", "gri"

    def _cekmece_gruplar(self):
        if not self.winfo_exists():
            return
        R, Y, u = self.R, self.YAZI, self.uyg
        for w in self.cek_ic.winfo_children():
            w.destroy()
        t = u._secili()
        if not t:
            return
        canli = bool(u.kosu_hedef and u.kosu_hedef == (t["id"], u.ay_no, u.yil_no) and (u.kosu or u.sorgu_durumu))
        kaynak = dict(u.sorgu_durumu) if canli else dict((self.satirlar.get(t["id"]) or {}).get("sonuclar") or {})
        x = self.satirlar.get(t["id"]) or {}
        # kısayollar (kullanıcı isteği 27.11.2026): yalnız hatalı ya da bu dönemde hiç sorgulanmamış sorguları çalıştırır
        okundu = not (u.kosu or u.calisiyor) and self.okunan_donem == (u.ay_no, u.yil_no) and t["id"] in self.satirlar
        for tur, (metin, fg, bg, kenar, aktif) in (("hata", ("↻  Hatalı {n} sorguyu yeniden sorgula", "#b0362a", "#fff6f5", "#e7a59d", "#fbe5e3")),
                                                    ("eksik", ("▶  Bu ay sorgulanmamış {n} sorguyu sorgula", "#2c5f9e", "#f3f7fd", "#9fbde4", "#e3edf9"))):
            # birikimli dönem sonuçları (canlı kutular yalnız son çalıştırmayı gösterir; 'sorgulanmamış' onlardan hesaplanmaz)
            liste = self._kisayol_sorgulari(tur, self.satirlar[t["id"]].get("sonuclar") or {}) if okundu else []
            if not liste:
                continue
            hb = tk.Button(self.cek_ic, text=metin.format(n=len(liste)), command=lambda t_=tur: self._kisayol_sorgula(t_),
                           relief="flat", bd=0, cursor="hand2", font=(Y, 9, "bold"), bg=bg, fg=fg, activebackground=aktif,
                           activeforeground=fg, highlightthickness=1, highlightbackground=kenar, pady=4,
                           state="disabled" if (u.beyan_kosu or u.toplu) else "normal")
            hb.pack(fill="x", pady=(0, 2))
            ozet = ", ".join(self.oh.KISA_AD.get(a, a) for a in liste[:8]) + (f" … (+{len(liste) - 8})" if len(liste) > 8 else "")
            tk.Label(self.cek_ic, text=ozet, bg=R["kart"], fg=fg, font=(Y, 8), anchor="w", justify="left",
                     wraplength=CEKMECE - 50).pack(fill="x", pady=(0, 6))
        if x.get("acil"):
            a = tk.Frame(self.cek_ic, bg="#fbe5e3", highlightthickness=0)
            a.pack(fill="x", pady=(0, 8))
            tk.Frame(a, bg="#c0392b", width=4).pack(side="left", fill="y")
            tk.Label(a, text="ACİL: " + ", ".join(x["acil"]), bg="#fbe5e3", fg="#8e2a20", font=(Y, 9, "bold"), anchor="w", justify="left",
                     wraplength=CEKMECE - 80).pack(side="left", fill="x", padx=8, pady=6)
        for no, (ad, anahtarlar) in enumerate(self.oh.GRUPLAR):
            satir = tk.Frame(self.cek_ic, bg=R["kart"])
            satir.pack(fill="x", pady=1)
            acik = u.grup_var[no].get()
            sec = sum(1 for a in anahtarlar if u.secim[a].get())
            anah = tk.Canvas(satir, width=30, height=16, bg=R["kart"], highlightthickness=0, cursor="hand2")
            anah.pack(side="left", pady=5)
            renk = "#1e8e4e" if acik else ("#7fbf9a" if sec else "#c9d2dc")
            anah.create_oval(0, 0, 16, 16, fill=renk, outline="")
            anah.create_oval(14, 0, 30, 16, fill=renk, outline="")
            anah.create_rectangle(8, 0, 22, 16, fill=renk, outline="")
            x0 = 16 if (acik or sec) else 2
            anah.create_oval(x0, 2, x0 + 12, 14, fill="white", outline="")
            anah.bind("<Button-1>", lambda e, n=no: self._grup_degistir(n))
            ad_l = tk.Label(satir, text=("▾ " if self.acik_grup == no else "▸ ") + ad, bg=R["kart"], fg=R["yazi"], font=(Y, 9, "bold"),
                            cursor="hand2", anchor="w", width=17)
            ad_l.pack(side="left", padx=(8, 0))
            ad_l.bind("<Button-1>", lambda e, n=no: self._grup_ac(n))
            tk.Label(satir, text=f"{sec}/{len(anahtarlar)} seçili", bg=R["kart"], fg="#6b7b8d", font=(Y, 8)).pack(side="left")
            metin, bicim = self._grup_durumu(anahtarlar, kaynak)
            if metin:
                self._hap(satir, metin, bicim).pack(side="right")
            if self.acik_grup == no:
                k = tk.Frame(self.cek_ic, bg="#f7f9fb", highlightthickness=1, highlightbackground="#e6ebf0")
                k.pack(fill="x", padx=(38, 0), pady=(0, 4))
                for a in anahtarlar:
                    r = tk.Frame(k, bg="#f7f9fb")
                    r.pack(fill="x", padx=6)
                    tk.Checkbutton(r, text=self.oh.KISA_AD.get(a, a), variable=u.secim[a], bg="#f7f9fb", activebackground="#f7f9fb",
                                   font=(Y, 9), bd=0, highlightthickness=0, anchor="w").pack(side="left")
                    d = kaynak.get(a)
                    if d:
                        m, b = {"veri": ("veri", "yesil"), "kayit_yok": ("kayıt yok", "gri"), "hata": ("hata", "kirmizi"),
                                "calisiyor": ("çalışıyor", "mavi"), "bekliyor": ("sırada", "gri"),
                                "calistirilmadi": ("çalışmadı", "gri")}.get(d, (d, "gri"))
                        self._hap(r, m, b, kalin=False).pack(side="right")

        self._firma_bilgisi(t)

    def _firma_bilgisi(self, t):
        """Çekmecenin sonunda E-Mükellef/GİB firma bilgileri özeti (Genel\\Firma Bilgileri.md) ve güncelleme bağlantısı."""
        R, Y, u = self.R, self.YAZI, self.uyg
        k = tk.Frame(self.cek_ic, bg=R["kart"])
        k.pack(fill="x", pady=(10, 0))
        tk.Frame(k, bg=R["cizgi"], height=1).pack(fill="x", pady=(0, 6))
        bas = tk.Frame(k, bg=R["kart"])
        bas.pack(fill="x")
        tk.Label(bas, text="Firma bilgileri", bg=R["kart"], fg=R["yazi"], font=(Y, 9, "bold")).pack(side="left")
        b = firma_bilgi.oku(t["klasor"]) if t.get("klasor") else None
        mesgul = bool(u.calisiyor or u.kosu or u.beyan_kosu or u.toplu)
        def bag(metin, komut):
            l = tk.Label(bas, text=metin, bg=R["kart"], fg="#9aa7b4" if mesgul else R["mavi"], font=(Y, 8), cursor="" if mesgul else "hand2")
            l.pack(side="right", padx=(8, 0))
            if not mesgul:
                l.bind("<Button-1>", lambda e: komut())
        bag("↻ GİB'den güncelle", u._firma_bilgi_guncelle)
        if b:
            yol = firma_bilgi.klasor(t["klasor"]) / firma_bilgi.MD_ADI
            bag("Dosyayı aç", lambda: u._ac(yol))
        if not t.get("klasor"):
            satirlar, alt = [], "Firma klasörü tanımlı değil."
        elif not b:
            satirlar, alt = [], "Henüz alınmadı. 'GİB'den güncelle' ile alınır; sonra firma sorgulanırken ayda bir kendiliğinden yenilenir."
        else:
            satirlar = firma_bilgi.ozet_satirlari(b)
            try:
                from datetime import datetime as _dt
                alt = "Güncelleme: " + _dt.fromisoformat(b["guncelleme"]).strftime("%d.%m.%Y %H:%M")
            except Exception:
                alt = ""
        for ad, deger in satirlar:
            s = tk.Frame(k, bg=R["kart"])
            s.pack(fill="x", pady=(2, 0))
            tk.Label(s, text=ad, bg=R["kart"], fg="#6b7b8d", font=(Y, 8), width=12, anchor="nw").pack(side="left", anchor="n")
            tk.Label(s, text=deger, bg=R["kart"], fg=R["yazi"], font=(Y, 8), anchor="w", justify="left",
                     wraplength=CEKMECE - 150).pack(side="left", fill="x")
        if alt:
            tk.Label(k, text=alt, bg=R["kart"], fg="#8a97a5", font=(Y, 8), anchor="w", justify="left",
                     wraplength=CEKMECE - 50).pack(fill="x", pady=(4, 0))

    def _kisayol_sorgulari(self, tur, kaynak):
        """'hata': son sonucu HATA olanlar; 'eksik': bu dönem için hiç sonucu olmayan ya da çalıştırılmamış kalanlar."""
        if tur == "hata":
            return [a for a, _ in self.oh.SORGULAR if kaynak.get(a) == "hata"]
        return [a for a, _ in self.oh.SORGULAR if kaynak.get(a) in (None, "calistirilmadi")]

    def _kisayol_sorgula(self, tur):
        """Seçili firmanın hatalı ya da sorgulanmamış sorgularını (ekrandaki işaretlere dokunmadan) çalıştırır."""
        u = self.uyg
        t = u._secili()
        if not t or u.calisiyor or u.kosu or u.beyan_kosu or u.toplu:
            return
        if self.okunan_donem != (u.ay_no, u.yil_no) or t["id"] not in self.satirlar:
            return                                  # tablo başka dönemin verisini gösteriyor / henüz okunmadı
        liste = self._kisayol_sorgulari(tur, self.satirlar[t["id"]].get("sonuclar") or {})
        if not liste:
            return
        ad = "Hatalı sorgular yeniden" if tur == "hata" else "Bu ay sorgulanmamış sorgular"
        u._yaz(f"{ad} sorgulanıyor ({len(liste)}): " + ", ".join(self.oh.KISA_AD.get(a, a) for a in liste))
        u._baslat({"secim": {a: a in liste for a in u.secim}, "firma": t["id"], "donem": (u.ay_no, u.yil_no)})

    def _hatalilari_sorgula(self):
        self._kisayol_sorgula("hata")

    def _grup_degistir(self, no):
        u = self.uyg
        hepsi = all(u.secim[a].get() for a in self.oh.GRUPLAR[no][1])
        u.grup_var[no].set(not hepsi)
        u._grup_sec(no)
        self._cekmece_gruplar()
        self.dugmeler()

    def _grup_ac(self, no):
        self.acik_grup = None if self.acik_grup == no else no
        self._cekmece_gruplar()
