"""KDV ve kâr/zarar durumu raporu (kullanıcı isteği 20.11.2026). Saf hesap + çıktı üretimi; tarayıcıya ve Tkinter'a dokunmaz.

Kaynak: Luca'dan indirilen seçilen ayın mizanı, mali dönem başından ay sonuna mizan ve Defteri Kebir (fiş ayrıntısı için).

KDV (yalnız KDV1; muhasebe kayıtlarına göre TAHMİN):
  hesaplanan  = ayın 391 alacak hareketi          (kebir varsa: mahsup fişi dışındaki 391 borç düzeltmeleri düşülür)
  indirilecek = ayın 191 borç hareketi            (kebir varsa: mahsup fişi dışındaki 191 alacak düzeltmeleri düşülür)
  devreden    = ay başı 190 borç bakiyesi + ay başı 191 borç bakiyesi − ay başı 391 alacak bakiyesi
                (ay başı = dönem başından mizan − ay mizanı; önceki ayların mahsup fişi kesilmemişse de doğru olsun diye 191/391 katılır)
                mali dönemin ilk ayında: açılış fişindeki 190.
  sonuç = hesaplanan − indirilecek − devreden;  > 0 ise ÖDENECEK KDV (kırmızı), değilse sonraki döneme devreden.
  KDV2 (kullanıcı isteği 20.11.2026): ayın tevkifat tutarı = 360'ın adı TEVKİFAT / SORUMLU SIFATI geçen alt hesaplarının alacak
  hareketi (açılış fişi hariç); ayrı bölümde gösterilir (ödeme çıkıyorsa kırmızı). KDV1'de bu tutar ÖDENMİŞ GİBİ indirilecek KDV
  sayılır: fatura fişinde 191'e de yazılmışsa zaten indirilecek KDV'nin içindedir. İndirilecek KDV'ye EKLEME yalnız şu durumda yapılır
  (çift sayım olmasın diye): tevkifat fişinde hiç 191 satırı yoksa VE ay içinde adı TEVKİFAT geçen 191 hesaplarına yazılan toplam,
  KDV2'den küçükse; eklenen tutar bu ikisinin küçüğüdür. Belirsiz kalan fark eklenmez, uyarı olarak yazılır.
  392, iade / tecil-terkin işlemleri hesaba katılmaz.

Kâr/zarar: firma raporundaki algoritma (scripts/stok_gecici.py; kullanıcı kararı 19.11.2026) buraya taşınmıştır:
  Senaryo 1: son maliyet fişinden (62x borç) sonraki satışlara, son maliyet dönemindeki SMM / net satış oranı uygulanır (maliyet
             atılmış gibi) -> tahmini kâr, tahmini vergi, kalan stok.
  Senaryo 2: maliyet atılmadan 6'lı ve 7'li hesaplar; kâr çıkıyorsa stoktan düşülür -> stok yetiyor mu.
  Stok = 150, 151, 152, 153, 157 (+158 net) + ithalat olduğu KAYITLA doğrulanan 159 alt hesapları (ithal KDV'li gümrük fişi ya da
         beyanname tescil no). Diğer 159 stok sayılmaz.
  Stok yetmiyorsa (kullanıcı isteği 20.11.2026): Senaryo 2'deki kârdan kayıtlı stokun TAMAMI satış maliyeti olarak düşülür
             (elde başka maliyet yok) -> kalan kâr + KKEG = matrah -> vergi. Raporun en üstünde kırmızı bantta gösterilir.
             Aynı bölümde: verginin hiç çıkmaması için ne kadar KDV HARİÇ gider / mal faturası gerektiği de yazılır
             (matrah − 193 / vergi oranı; mal faturasının maliyete atılacağı varsayılır).
  Peşin ödenen vergiler (kullanıcı isteği 20.11.2026): 193'ün (ödenen geçici vergi, kesintiler) net borç bakiyesi hesaplanan vergiden
             düşülür -> ödenecek vergi. Açılıştan gelen ya da dönem içinde alacak çalışan 193 için uyarı verilir.
  Farklar: dönem kapanış hesapları (690/691/692) hareket görmüşse kâr hesabına katılmaz (uyarı verilir); vergi oranı %25 varsayılır.
"""
import base64
import calendar
import collections
import html as _html
import io
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal as D

AYLAR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]
STOK = ("150", "151", "152", "153", "157")
SMM = ("620", "621", "622", "623")
KAPANIS = ("690", "691", "692")
VERGI_ORANI = D("0.25")
S0 = D("0")
BEYAN = re.compile(r"IM\d{6,}")


def d(x):
    """Sayı / '1.234,56' / '1234.56' / boş -> Decimal (2 hane)."""
    if isinstance(x, D):
        v = x
    elif isinstance(x, (int, float)):
        v = D(str(x))
    else:
        s = str(x or "").strip().replace(" ", "")
        if not s:
            return S0
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        try:
            v = D(s)
        except Exception:
            return S0
    return v.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def tl(x):
    x = d(x)
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s


def yuzde(o):
    return ("%" + f"{o * 100:.2f}".replace(".", ",")) if o is not None else "—"


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


def _fis_no(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


Satir = collections.namedtuple("Satir", "t ana kod ad net borc alacak fis evrak acik")


def kebir_oku(satirlar, bas, son):
    """Luca Defteri Kebir satırları (luca.xlsx_satirlari) -> [Satir] (bas <= tarih <= son).
    Sütunlar: 0 kebir, 2 tarih, 4 fiş no, 5 evrak no, 7 hesap kodu, 8 hesap adı, 9 açıklama, 10 borç, 11 alacak."""
    cikan = []
    for r in satirlar[1:]:
        if len(r) < 12 or r[0] in (None, "", " ") or r[7] in (None, "", " "):
            continue
        t = _tarih(r[2])
        if t is None or not (bas <= t <= son):
            continue
        b, a = d(r[10]), d(r[11])
        cikan.append(Satir(t, str(r[0]).strip(), str(r[7]).strip(), str(r[8] or "").strip(), b - a, b, a, _fis_no(r[4]),
                           str(r[5] or ""), str(r[9] or "")))
    return cikan


def _acilis_fisleri(sat, bas):
    """Dönemin ilk günündeki açılış fişleri: açıklamasında AÇILIŞ geçen ya da gelir/gider satırı olmayıp özkaynak (5xx) içeren fişler."""
    fisler = collections.defaultdict(list)
    for s in sat:
        if s.t == bas:
            fisler[(s.t, s.fis)].append(s)
    return {k for k, ss in fisler.items()
            if any("AÇILIŞ" in s.acik.upper() or "ACILIS" in s.acik.upper() for s in ss)
            or (any(s.ana[:1] == "5" for s in ss) and not any(s.ana[:1] in ("6", "7") for s in ss))}


# ================================================================ KDV
def _tevkifat_adi(ad):
    a = str(ad or "").upper().replace("İ", "I")
    return "TEVK" in a or "SORUMLU" in a


def kdv2_durumu(mizan_ay, sat, ay, yil, haric=frozenset()):
    """Ayın KDV2 (sorumlu sıfatıyla / tevkifat) tutarı. haric: sayılmayacak fişler (açılış fişi).
    Döner: {toplam, hesaplar: [[kod ad, tutar]], karsilik_191, eksik, belirsiz, kaynak}.
      karsilik_191: ay içinde adı TEVKİFAT geçen 191 alt hesaplarına yazılan borç (hangi fişte olursa olsun);
      eksik: KDV1'de ödenmiş gibi indirilecek KDV'ye EKLENECEK tutar = min(191 satırı hiç olmayan tevkifat fişlerinin tutarı,
             KDV2 − karsilik_191). Çift sayımı önlemek için dar tutulur;
      belirsiz: KDV2'nin, 191'e yazıldığı adından anlaşılamayan ve eklenmeyen kısmı (uyarı olarak yazılır)."""
    ay_bas = date(yil, ay, 1)
    hesap, karsilik, fissiz = collections.OrderedDict(), S0, S0
    if sat is not None:
        fis = collections.defaultdict(list)
        for s in sat:
            if s.t >= ay_bas and s.ana in ("360", "191") and (s.t, s.fis) not in haric:
                fis[(s.t, s.fis)].append(s)
        for ss in fis.values():
            karsilik += sum((s.borc for s in ss if s.ana == "191" and _tevkifat_adi(s.ad)), S0)
            tv = [s for s in ss if s.ana == "360" and s.alacak > 0 and _tevkifat_adi(s.ad)]
            if not tv:
                continue
            for s in tv:
                h = hesap.setdefault(s.kod, [s.ad, S0])
                h[1] += s.alacak
            if not any(s.ana == "191" and s.borc > 0 for s in ss):
                fissiz += sum((s.alacak for s in tv), S0)
        kaynak = "kebir"
    else:                                           # kebir yok: ay mizanındaki YAPRAK hesapların adlarından (üst hesap toplamı alınmaz)
        tum = [str(r[0]).strip() for r in mizan_ay or []]
        for r in mizan_ay or []:
            kod = str(r[0]).strip()
            if (kod.startswith("360.") and _tevkifat_adi(r[1]) and d(r[3]) > 0
                    and not any(k != kod and k.startswith(kod + ".") for k in tum)):
                hesap[kod] = [str(r[1]), d(r[3])]
        kaynak = "mizan"
    toplam = sum((h[1] for h in hesap.values()), S0)
    acik = max(toplam - karsilik, S0) if kaynak == "kebir" else S0
    eksik = min(fissiz, acik)
    return {"toplam": toplam, "hesaplar": [[f"{k} {h[0]}", h[1]] for k, h in sorted(hesap.items())],
            "karsilik_191": karsilik, "eksik": eksik, "belirsiz": acik - eksik, "kaynak": kaynak}


def _mz(tablo, kod):
    """Mizan tablosunda TAM kodlu (ana hesap) satırın (borç, alacak) hareketi; yoksa (0, 0)."""
    for r in tablo or []:
        if str(r[0]).strip() == kod:
            return d(r[2]), d(r[3])
    return S0, S0


def kdv_durumu(mizan_ay, mizan_yil, sat, ay, yil, donem_bas):
    """mizan_ay / mizan_yil: luca_sorgu.mizan_tablosu satırları; sat: kebir_oku çıktısı ya da None (kebir alınamadıysa)."""
    ay_bas = date(yil, ay, 1)
    ilk_ay = donem_bas >= ay_bas
    uyari = []
    b190a, a190a = _mz(mizan_ay, "190")
    b191a, a191a = _mz(mizan_ay, "191")
    b391a, a391a = _mz(mizan_ay, "391")
    b190y, a190y = _mz(mizan_yil, "190")
    b191y, a191y = _mz(mizan_yil, "191")
    b391y, a391y = _mz(mizan_yil, "391")
    hesaplanan, indirilecek = a391a, b191a
    duz391 = duz191 = S0
    mahsup_var = None
    ac190 = ac191 = ac391 = S0
    acilis = set()
    if sat is not None:
        aylik = [s for s in sat if s.t >= ay_bas]
        acilis = _acilis_fisleri(sat, donem_bas) if ilk_ay else set()
        fis = collections.defaultdict(list)
        for s in aylik:
            if s.ana in ("190", "191", "391", "360"):
                fis[(s.t, s.fis)].append(s)
        # KDV mahsup fişi: aynı fişte 391 borç (kapatma) VE 191 alacak (kapatma). Karma fişlerde ayrım yapılamaz (nadir).
        mahsup = {k for k, ss in fis.items() if k not in acilis and any(s.ana == "391" and s.borc > 0 for s in ss)
                  and any(s.ana == "191" and s.alacak > 0 for s in ss)}
        mahsup_var = bool(mahsup)
        for k, ss in fis.items():
            for s in ss:
                if k in acilis:
                    ac190 += s.net if s.ana == "190" else S0
                    ac191 += s.borc if s.ana == "191" else S0
                    ac391 += s.alacak if s.ana == "391" else S0
                elif k not in mahsup:
                    duz391 += s.borc if s.ana == "391" else S0
                    duz191 += s.alacak if s.ana == "191" else S0
        hesaplanan = a391a - ac391 - duz391
        indirilecek = b191a - ac191 - duz191
        if duz391 or duz191:
            uyari.append(f"Ay içinde mahsup fişi dışında 391 borç ({tl(duz391)}) / 191 alacak ({tl(duz191)}) kaydı var (iade, düzeltme ya da ters "
                         "kayıt); tutarlardan düşüldü, fişleri kontrol edin.")
        if ac191 or ac391:
            uyari.append(f"Açılış fişinde 191 ({tl(ac191)}) / 391 ({tl(ac391)}) bakiyesi var; önceki yılın mahsubu yapılmamış olabilir. "
                         "Bu tutarlar ayın KDV'sine katılmadı.")
    elif b391a or a191a:
        uyari.append(f"Ay içinde 391 borç ({tl(b391a)}) / 191 alacak ({tl(a191a)}) hareketi var (mahsup fişi ya da düzeltme); kebir "
                     "alınamadığı için ayrılamadı, tutarlar brüt alındı.")
    if ilk_ay:
        if sat is None:
            devreden = b190a - a190a
            uyari.append("Mali dönemin ilk ayı ve kebir alınamadı: devreden KDV, ayın 190 net hareketinden alındı (açılış fişi ayrılamadı).")
        else:
            devreden = ac190
            if not ac190 and (b190a or a190a):
                uyari.append("Mali dönemin ilk ayı: açılış fişinde 190 bulunamadı; devreden KDV 0 alındı. Açılış fişini kontrol edin.")
        bas191 = bas391 = S0
    else:
        bas190 = (b190y - a190y) - (b190a - a190a)
        bas191 = (b191y - a191y) - (b191a - a191a)
        bas391 = (a391y - b391y) - (a391a - b391a)
        devreden = bas190 + bas191 - bas391
        if abs(bas191) >= D("0.01") or abs(bas391) >= D("0.01"):
            uyari.append(f"Ay başında 191 ({tl(bas191)}) ve 391 ({tl(bas391)}) bakiye veriyor: önceki ay(lar)ın KDV mahsup fişi kesilmemiş "
                         f"görünüyor. Devreden KDV buna göre hesaplandı (190 ay başı {tl(bas190)}).")
    if devreden < 0:
        uyari.append(f"Önceki aylardan {tl(-devreden)} TL ödenecek KDV kalmış görünüyor (190 / 191 / 391 ay başı bakiyeleri); devreden 0 alındı.")
        devreden = S0
    k2 = kdv2_durumu(mizan_ay, sat, ay, yil, acilis)
    indirilecek += k2["eksik"]                      # 191'e yazılmamış tevkifat: ödenmiş gibi indirilecek KDV
    if k2["eksik"]:
        uyari.append(f"Tevkifat fişlerinin {tl(k2['eksik'])} TL'lik kısmında 191 kaydı yok; bu tutar KDV2 ödenmiş kabul edilerek "
                     "indirilecek KDV'ye eklendi. Fişleri kontrol edin.")
    if k2["belirsiz"] >= D("0.01"):
        uyari.append(f"KDV2'nin {tl(k2['belirsiz'])} TL'lik kısmının 191'e yazıldığı hesap adından anlaşılamadı (adı TEVKİFAT geçen 191 "
                     "hesaplarında karşılığı yok). Normal indirilecek KDV hesabına yazılmışsa sorun yok; hiç yazılmamışsa KDV1 bu kadar "
                     "fazla çıkar. Bu tutar eklenmedi; fişleri kontrol edin.")
    if k2["kaynak"] == "mizan" and k2["toplam"] > 0:
        uyari.append("Kebir alınamadığı için KDV2 tevkifatının 191'e kaydedilip kaydedilmediği doğrulanamadı; indirilecek KDV'ye ekleme "
                     "yapılmadı.")
    sonuc = hesaplanan - indirilecek - devreden
    return {"hesaplanan": hesaplanan, "indirilecek": indirilecek, "devreden": devreden, "sonuc": sonuc, "odenecek": sonuc > 0, "kdv2": k2,
            "ay_sonu_190": b190y - a190y, "mahsup_var": mahsup_var, "uyarilar": uyari,
            "hareket": [["190 Devreden KDV", b190a, a190a, b190y - a190y], ["191 İndirilecek KDV", b191a, a191a, b191y - a191y],
                        ["391 Hesaplanan KDV", b391a, a391a, b391y - a391y]]}


# ================================================================ kâr / zarar (scripts/stok_gecici.py algoritması)
def kar_zarar(sat, yil, ay, oran_v=VERGI_ORANI):
    ay_bas = date(yil, ay, 1)

    def top(kosul):
        return sum((s.net for s in sat if kosul(s)), S0)

    uyari = []
    stok_ana = top(lambda s: s.ana in STOK) + top(lambda s: s.ana == "158")
    # --- 159: ithalat olduğu MUHASEBE KAYDIYLA doğrulanan alt hesaplar stok sayılır (yalnız hesap adına güvenilmez)
    fis_ithal_kdv = {(s.t, s.fis) for s in sat if s.kod.startswith("191.02") and s.fis > 0}
    h159 = collections.OrderedDict()
    for s in sorted(sat, key=lambda x: (x.kod, x.t)):
        if s.ana != "159":
            continue
        h = h159.setdefault(s.kod, {"ad": s.ad, "bakiye": S0, "kanit": [], "hareket": 0})
        h["bakiye"] += s.net
        if "AÇILIŞ" not in s.acik.upper():
            h["hareket"] += 1
        k1 = f"ithal KDV içeren gümrük fişi {s.fis:05d}"
        if (s.t, s.fis) in fis_ithal_kdv and k1 not in h["kanit"]:
            h["kanit"].append(k1)
        m_ = BEYAN.search(s.evrak) or BEYAN.search(s.acik)
        if m_ and ("beyanname " + m_.group(0)) not in h["kanit"]:
            h["kanit"].append("beyanname " + m_.group(0))
    ith, eski = collections.OrderedDict(), S0
    for kod, h in h159.items():
        if abs(h["bakiye"]) < D("0.005"):
            continue
        if h["kanit"]:
            ith[kod] = [h["ad"], h["bakiye"], "; ".join(h["kanit"][:3])]
        elif "ITHALAT" in h["ad"].upper().replace("İ", "I"):
            uyari.append(f"159 kontrol: {kod} {h['ad']} ({tl(h['bakiye'])} TL) adında ithalat geçiyor ama muhasebe kaydında gümrük / ithal KDV "
                         "kanıtı yok; stok SAYILMADI.")
        if kod not in ith and h["hareket"] == 0:
            eski += h["bakiye"]
    stok_ith = sum((v[1] for v in ith.values()), S0)
    stok = stok_ana + stok_ith
    s159 = top(lambda s: s.ana == "159") - stok_ith
    if eski > 0:
        uyari.append(f"159 kontrol: yıl içinde hiç hareketi olmayan (yalnız açılış) 159 bakiyeleri {tl(eski)} TL; eski avans olabilir, stok sayılmadı.")

    def ns_f(s):
        return s.ana in ("600", "601", "602", "610", "611", "612")
    ns_ytd = -top(ns_f)
    mal = collections.OrderedDict()                 # maliyet fişleri (62x borç), tarihe göre
    for s in sorted(sat, key=lambda x: x.t):
        if s.ana in SMM and s.net > 0:
            mal[s.t] = mal.get(s.t, S0) + s.net
    smm_kayitli = top(lambda s: s.ana in SMM)
    donemler, onceki_t = [], date.min
    for t, tut in mal.items():
        ns_d = -top(lambda s: ns_f(s) and onceki_t < s.t <= t)
        donemler.append((t, ns_d, tut, (tut / ns_d) if ns_d else None))
        onceki_t = t
    son_mal = onceki_t if mal else None
    ns_maliyetli = -top(lambda s: ns_f(s) and son_mal is not None and s.t <= son_mal)
    ns_maliyetsiz = ns_ytd - ns_maliyetli
    oran_son = donemler[-1][3] if donemler and donemler[-1][3] else None
    oran_kum = (smm_kayitli / ns_maliyetli) if ns_maliyetli else None

    kapanis = top(lambda s: s.ana in KAPANIS)
    kapanis_var = any(s.ana in KAPANIS for s in sat)
    gelir6 = -top(lambda s: s.ana[:1] == "6" and s.ana not in KAPANIS)
    gider7 = top(lambda s: s.ana[:1] == "7")
    kar_mevcut = gelir6 - gider7                    # maliyetsiz dönem için SMM yok
    if kapanis_var:
        uyari.append(f"Dönem kapanış hesaplarında (690 / 691 / 692) hareket var (net {tl(kapanis)} TL); kâr hesabına katılmadı. "
                     "Gelir tablosu hesapları kapatılmışsa aşağıdaki kâr gerçeği göstermez.")
    kkeg = top(lambda s: s.ana in ("689", "681"))
    personel_var = any(s.t >= ay_bas and (s.ana == "335" or (s.ana[:1] == "7" and any(k in s.ad.upper() for k in ("ÜCRET", "MAAŞ", "MAAS"))))
                       for s in sat)
    if not personel_var:
        uyari.append(f"PERSONEL FİŞİ YOK: {AYLAR[ay - 1]} {yil} için ücret / 335 hareketi bulunamadı. Bordro fişi kesilmemişse rapordaki kâr "
                     "olduğundan yüksek görünür.")
    # Peşin ödenen vergiler: 193 net borç bakiyesi (ödenen geçici vergi + kesintiler) hesaplanan vergiden düşülür
    pesin_net = top(lambda s: s.ana == "193")
    pesin = max(pesin_net, S0)
    p_acilis = sum((s.net for s in sat if s.ana == "193" and "AÇILIŞ" in s.acik.upper()), S0)
    p_alacak = sum((s.alacak for s in sat if s.ana == "193"), S0)
    if p_acilis:
        uyari.append(f"193'te açılıştan gelen {tl(p_acilis)} TL var (önceki yıl 371'e devredilmemiş olabilir); peşin ödenen vergiye dahil "
                     "edildi, kontrol edin.")
    if p_alacak:
        uyari.append(f"193 dönem içinde {tl(p_alacak)} TL alacak çalışmış (iade, mahsup ya da 371'e devir olabilir); vergiden düşülen tutar "
                     f"193'ün net bakiyesidir ({tl(pesin)} TL).")
    if pesin_net < 0:
        uyari.append(f"193 alacak bakiyesi veriyor ({tl(pesin_net)} TL); peşin ödenen vergi 0 alındı, hesabı kontrol edin.")
    s1 = None
    if oran_son is not None:
        smm_tah = (ns_maliyetsiz * oran_son).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        kar1 = kar_mevcut - smm_tah
        matrah1 = max(kar1 + kkeg, S0)
        s1 = {"oran": oran_son, "smm": smm_tah, "kar": kar1, "matrah": matrah1,
              "vergi": (matrah1 * oran_v).quantize(D("0.01"), rounding=ROUND_HALF_UP), "kalan": stok - smm_tah}
        if s1["kalan"] < 0:
            uyari.append(f"STOK YETERSİZ (Senaryo 1): tahmini satış maliyeti {tl(smm_tah)} TL, kayıtlı stok {tl(stok)} TL; {tl(-s1['kalan'])} TL "
                         "eksik. Alış faturası / ithalat kaydı eksik olabilir ya da stok hesabı düzeltilmelidir.")
    else:
        uyari.append("Yıl içinde maliyet fişi (62x) yok; Senaryo 1 için maliyet oranı hesaplanamadı. Kâr, maliyet atılmadan (Senaryo 2) gösterilir.")
    # Senaryo 2'de de vergi: 689'a (ve 681'e) atılan kanunen kabul edilmeyen giderler kâra eklenir (kullanıcı isteği 20.11.2026)
    matrah2 = max(kar_mevcut + kkeg, S0)
    vergi2 = (matrah2 * oran_v).quantize(D("0.01"), rounding=ROUND_HALF_UP)
    kalan2 = stok - kar_mevcut if kar_mevcut > 0 else stok
    if kar_mevcut > 0 and kalan2 < 0:
        uyari.append(f"STOK YETERSİZ (Senaryo 2): maliyet atılmadan kâr {tl(kar_mevcut)} TL; bu kârı karşılayacak stok yok (stok {tl(stok)} TL, "
                     f"{tl(-kalan2)} TL eksik).")
    # Stok yetmiyorsa: maliyet olarak en fazla kayıtlı stokun tamamı yazılabilir -> Senaryo 2 kârından tüm stok düşülür
    yetersiz = bool(s1 and s1["kalan"] < 0) or bool(kar_mevcut > 0 and kalan2 < 0)
    tum_stok = None
    if yetersiz:
        maliyet = max(stok, S0)
        kar3 = kar_mevcut - maliyet
        matrah3 = max(kar3 + kkeg, S0)
        vergi3 = (matrah3 * oran_v).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        # verginin hiç çıkmaması için gereken KDV hariç gider / mal faturası: matrah, peşin ödenen verginin karşıladığı matraha inmeli
        gereken = max(matrah3 - (pesin / oran_v), S0).quantize(D("0.01"), rounding=ROUND_HALF_UP)
        tum_stok = {"maliyet": maliyet, "kar": kar3, "matrah": matrah3, "vergi": vergi3, "gereken": gereken}
    if any(u.startswith("STOK YETERSİZ") for u in uyari) and s159 > 0:
        uyari.append(f"Not: 159 Verilen Sipariş Avansları hesabında ithalat dışı {tl(s159)} TL var (stok sayılmadı). Mallar teslim alındıysa "
                     f"153'e aktarılmalıdır; aktarılırsa stok {tl(stok + s159)} TL olur.")
    return {"stok": stok, "stok_ana": stok_ana, "stok_ith": stok_ith, "ith": ith, "s159": s159, "ns_ytd": ns_ytd,
            "ns_maliyetsiz": ns_maliyetsiz, "ns_maliyetli": ns_maliyetli, "smm_kayitli": smm_kayitli, "son_mal": son_mal,
            "donemler": donemler, "oran_kum": oran_kum, "gelir6": gelir6, "gider7": gider7, "kar_maliyetsiz": kar_mevcut,
            "kkeg": kkeg, "s1": s1, "kalan2": kalan2, "matrah2": matrah2, "vergi2": vergi2, "tum_stok": tum_stok, "pesin": pesin, "oran_v": oran_v, "uyarilar": uyari,
            "yetersiz1": bool(s1 and s1["kalan"] < 0), "yetersiz2": bool(kar_mevcut > 0 and kalan2 < 0)}


# ================================================================ rapor (ekran, PDF ve Excel aynı yapıdan üretilir)
def rapor_kur(firma, ay, yil, donem_bas, mizan_ay, mizan_yil, kebir_satirlari, notlar=()):
    """Döner: {firma, donem, uretim, ozet: [(etiket, değer, stil)], bolumler: [{baslik, kolonlar, satirlar, stil: {satır no: stil},
    not}], uyarilar: [metin]}. stil: 'kirmizi' | 'yesil' | 'kalin' | ''. Tutarlar biçimlenmiş metindir."""
    son = date(yil, ay, calendar.monthrange(yil, ay)[1])
    donem = f"{AYLAR[ay - 1]} {yil}"
    sat = kebir_oku(kebir_satirlari, donem_bas, son) if kebir_satirlari else None
    k = kdv_durumu(mizan_ay, mizan_yil, sat, ay, yil, donem_bas)
    uyarilar = list(notlar) + ["KDV: " + u for u in k["uyarilar"]]
    k2 = k["kdv2"]
    # KDV1'de indirilecek KDV'nin içinde olduğu bilinen tevkifat: 191 tevkifat hesaplarındaki karşılık + ödenmiş kabul edilerek eklenen
    dahil = min(k2["toplam"], k2["karsilik_191"] + k2["eksik"]) if k2["kaynak"] == "kebir" else S0
    ozet = [("ÖDENECEK KDV (KDV1)" if k["odenecek"] else "Sonraki döneme devreden KDV (KDV1)", tl(abs(k["sonuc"])) + " TL",
             "kirmizi" if k["odenecek"] else "yesil"),
            ("ÖDENECEK KDV2 (tevkifat)" if k2["toplam"] > 0 else "KDV2 (tevkifat)", tl(k2["toplam"]) + " TL" if k2["toplam"] > 0 else "yok",
             "kirmizi" if k2["toplam"] > 0 else "")]
    bant = None                                     # en üstte kırmızı bant: (metin, tutar) — stok yetmiyorsa
    bolumler = [{
        "baslik": f"KDV durumu — {donem}", "kolonlar": ["Kalem", "Tutar (TL)"],
        "satirlar": [["Hesaplanan KDV (391, ay içi)", tl(k["hesaplanan"])],
                     ["İndirilecek KDV (191, ay içi" + (f"; {tl(dahil)} TL KDV2 tevkifatı dahil)" if dahil > 0 else ")"),
                      tl(k["indirilecek"])],
                     ["Önceki dönemden devreden KDV (190, ay başı)", tl(k["devreden"])],
                     ["ÖDENECEK KDV (KDV1)" if k["odenecek"] else "SONRAKİ DÖNEME DEVREDEN KDV", tl(abs(k["sonuc"]))]],
        "stil": {3: "kirmizi" if k["odenecek"] else "yesil"},
        "not": ("Muhasebe kayıtlarına göre tahmindir. " + ("Ayın KDV2 tevkifatı ödenmiş kabul edilmiş, indirilecek KDV'ye dahil edilmiştir"
                                                           + (" (doğrulanamayan kısım için uyarılara bakın). " if dahil < k2["toplam"] else ". ")
                                                           if dahil > 0 else "")
                + "İade ve tecil-terkin işlemleri hesaba katılmamıştır. " + ("Luca'da bu ayın KDV mahsup fişi kesilmiş. " if k["mahsup_var"] else
                                            ("Luca'da bu ayın KDV mahsup fişi henüz kesilmemiş. " if k["mahsup_var"] is False else ""))
                + f"Luca'da ay sonu 190 bakiyesi: {tl(k['ay_sonu_190'])} TL.")},
        {"baslik": f"KDV2 — sorumlu sıfatıyla beyan edilecek tevkifat ({donem})", "kolonlar": ["Hesap", "Tutar (TL)"],
         "satirlar": [[h[0], tl(h[1])] for h in k2["hesaplar"]]
         + [["ÖDENECEK KDV2" if k2["toplam"] > 0 else "KDV2 yok (ay içinde tevkifat kaydı bulunamadı)", tl(k2["toplam"])]],
         "stil": {len(k2["hesaplar"]): "kirmizi" if k2["toplam"] > 0 else "kalin"},
         "not": ("360'ın adı TEVKİFAT / SORUMLU SIFATI geçen alt hesaplarının ay içi alacak hareketidir"
                 + (" (kebir alınamadı; mizandaki hesap adlarından)" if k2["kaynak"] == "mizan" else "") + ". "
                 + ((f"Bu tutarın {tl(min(k2['karsilik_191'], k2['toplam']))} TL'si 191 tevkifat hesabına da yazılmış. "
                     if k2["karsilik_191"] else "")
                    + (f"{tl(dahil)} TL'si KDV1'de ödenmiş gibi indirilecek KDV sayılmıştır." if dahil > 0
                       else "KDV1'e etkisi doğrulanamadı; uyarılara bakın.") if k2["toplam"] > 0 else ""))},
        {"baslik": "KDV hesaplarının hareketi (mizan)", "kolonlar": ["Hesap", "Ay borç", "Ay alacak", "Ay sonu bakiye (B−A)"],
         "satirlar": [[h[0], tl(h[1]), tl(h[2]), tl(h[3])] for h in k["hareket"]], "stil": {}, "not": ""}]
    if sat is None:
        uyarilar.append("Kâr/zarar: Defteri Kebir alınamadığı için kâr/zarar hesaplanamadı.")
    elif not sat:
        uyarilar.append("Kâr/zarar: kebirde bu döneme ait hareket yok; kâr/zarar hesaplanamadı.")
    else:
        z = kar_zarar(sat, yil, ay)
        uyarilar += ["Kâr/zarar: " + u for u in z["uyarilar"]]
        s1 = z["s1"]
        kar = s1["kar"] if s1 else z["kar_maliyetsiz"]
        ozet.append((("KÂR" if kar > 0 else "ZARAR") + (" (maliyet atılmış gibi, tahmini)" if s1 else " (maliyet atılmadan)"),
                     tl(abs(kar)) + " TL", "yesil" if kar > 0 else "kalin"))
        yetersiz = z["yetersiz1"] or z["yetersiz2"]
        ozet.append(("Stok" + (" — YETERSİZ !" if yetersiz else ""), tl(z["stok"]) + " TL", "kirmizi" if yetersiz else ""))
        pesin = z["pesin"]

        def vergi_satirlari(vergi):
            """Hesaplanan vergi -> peşin ödenen (193) -> ödenecek vergi / peşin fazlası satırları ve ödenecek satırının sırası (0'dan)."""
            net = vergi - pesin
            return [[f"Hesaplanan vergi ({yuzde(z['oran_v'])})", tl(vergi)],
                    ["Peşin ödenen vergiler (193: ödenen geçici vergi ve kesintiler)", tl(-pesin)],
                    ["ÖDENECEK VERGİ" if net > 0 else ("Ödenecek vergi yok — peşin ödenen vergi fazlası" if net < 0 else "Ödenecek vergi yok"),
                     tl(abs(net))]], net

        vergi = s1["vergi"] if s1 else z["vergi2"]
        if vergi > 0 or pesin > 0:                  # zararda da 689 matrah çıkarabilir (kâr + KKEG > 0)
            net = vergi - pesin
            sen = "Senaryo 1" if s1 else "Senaryo 2"
            ozet.insert(len(ozet) - 1, (f"Ödenecek vergi — {sen} (%25; 689 dahil, 193 düşülmüş)" if net > 0
                                        else f"Ödenecek vergi yok — {sen}" + (" (193 fazlası)" if net < 0 else ""),
                                        tl(abs(net)) + " TL", "kalin"))
        stok_et = "Stok (153 + ithalat 159)" if z["stok_ith"] else "Kayıtlı stok (159 hariç)"
        ts = z["tum_stok"]
        if ts:
            vs, net3 = vergi_satirlari(ts["vergi"])
            bant = ("STOK YETERSİZ ! Kayıtlı stokun tamamı maliyete atılırsa ÖDENECEK VERGİ (193 düşülmüş)", tl(max(net3, S0)) + " TL",
                    f"Verginin hiç çıkmaması için gereken KDV hariç gider / mal faturası: {tl(ts['gereken'])} TL" if ts["gereken"] > 0
                    else "Peşin ödenen vergi (193) yeterli; ek gider / mal faturası gerekmiyor.")
            bolumler.insert(0, {
                "baslik": f"Stok yetersiz — tüm stok maliyete atılırsa (dönem başı – {donem} sonu, kümülatif)", "kolonlar": ["Kalem", "Tutar (TL)"],
                "satirlar": [[("Kâr" if z["kar_maliyetsiz"] > 0 else "Zarar") + " (maliyet atılmadan; Senaryo 2)", tl(z["kar_maliyetsiz"])],
                             [stok_et + " — tamamı satış maliyeti olarak", tl(-ts["maliyet"])],
                             ["Kalan " + ("kâr" if ts["kar"] > 0 else "zarar"), tl(ts["kar"])],
                             ["Kanunen kabul edilmeyen giderler (689, 681)", tl(z["kkeg"])],
                             ["Vergi matrahı", tl(ts["matrah"])]] + vs
                + [["VERGİ ÇIKMAMASI İÇİN GEREKEN KDV HARİÇ GİDER / MAL FATURASI", tl(ts["gereken"])]],
                "stil": {2: "kalin", 7: "kirmizi" if net3 > 0 else "yesil", 8: "kirmizi" if ts["gereken"] > 0 else "yesil"},
                "not": "Stok, satışların maliyetini karşılamıyor: maliyet olarak en fazla kayıtlı stokun tamamı yazılabilir. Bu durumda "
                       "kalan kâr üzerinden çıkacak vergi budur (kurumlar vergisi oranı %25). Son satır: ödenecek verginin sıfırlanması "
                       "için bu kadar KDV hariç gider ya da (satılıp maliyete atılacak) mal alış faturası kaydedilmelidir"
                       + (f"; 193 hiç dikkate alınmazsa matrahı sıfırlamak için {tl(ts['matrah'])} TL gerekir" if pesin > 0 else "")
                       + ". Alış / ithalat kaydı eksikse önce onlar tamamlanmalıdır."})
        kum = f"dönem başı – {donem} sonu, kümülatif"
        if z["donemler"]:
            bolumler.append({"baslik": "Önceki dönemlerde satış maliyeti oranı",
                             "kolonlar": ["Maliyet fişi tarihi", "Dönem net satışı", "Satış maliyeti", "Oran"],
                             "satirlar": [[t.strftime("%d.%m.%Y"), tl(n), tl(s), yuzde(o)] for t, n, s, o in z["donemler"]]
                             + [["Toplam", tl(z["ns_maliyetli"]), tl(z["smm_kayitli"]), yuzde(z["oran_kum"])]],
                             "stil": {len(z["donemler"]): "kalin"},
                             "not": "Senaryo 1, en son maliyet fişinin oranını kullanır (tek bir düzeltme fişi bu oranı bozabilir)."})
        if s1:
            bolumler.append({"baslik": f"Senaryo 1 — satış maliyeti atılmış gibi ({kum})", "kolonlar": ["Kalem", "Tutar (TL)"],
                             "satirlar": [["Maliyeti atılmamış net satış (son maliyet fişinden sonra)", tl(z["ns_maliyetsiz"])],
                                          [f"Tahmini satış maliyeti ({yuzde(s1['oran'])})", tl(s1["smm"])],
                                          ["TAHMİNİ TİCARİ " + ("KÂR" if s1["kar"] > 0 else "ZARAR"), tl(s1["kar"])],
                                          ["Kanunen kabul edilmeyen giderler (689, 681)", tl(z["kkeg"])],
                                          ["Tahmini vergi matrahı", tl(s1["matrah"])]] + vergi_satirlari(s1["vergi"])[0]
                             + [[stok_et, tl(z["stok"])],
                                ["KALAN STOK", tl(s1["kalan"])]],
                             "stil": {2: "yesil" if s1["kar"] > 0 else "kalin", 7: "kalin", 9: "kirmizi" if z["yetersiz1"] else "kalin"},
                             "not": ("STOK YETERSİZ ! " if z["yetersiz1"] else "") + "Tahmindir: fiili stok sayımı ve amortisman dikkate "
                                    "alınmamıştır. Vergi, kurumlar vergisi oranıyla (%25) hesaplanmış; 193'teki peşin ödenen vergiler "
                                    "(ödenen geçici vergi, kesintiler) düşülmüştür."})
        bolumler.append({"baslik": f"Senaryo 2 — maliyet atılmadan ({kum})", "kolonlar": ["Kalem", "Tutar (TL)"],
                         "satirlar": [["6'lı hesaplar: gelirler − kayıtlı maliyet ve giderler", tl(z["gelir6"])],
                                      ["7'li hesaplar: henüz yansıtılmamış giderler", tl(-z["gider7"])],
                                      [("KÂR" if z["kar_maliyetsiz"] > 0 else "ZARAR") + " (maliyet atılmadan)", tl(z["kar_maliyetsiz"])],
                                      ["Kanunen kabul edilmeyen giderler (689, 681)", tl(z["kkeg"])],
                                      ["Vergi matrahı (kâr + kanunen kabul edilmeyen giderler)", tl(z["matrah2"])]]
                         + vergi_satirlari(z["vergi2"])[0]
                         + [[stok_et, tl(z["stok"])],
                            ["Kâr stoktan düşülürse KALAN STOK", tl(z["kalan2"])]],
                         "stil": {2: "yesil" if z["kar_maliyetsiz"] > 0 else "kalin", 7: "kalin", 9: "kirmizi" if z["yetersiz2"] else "kalin"},
                         "not": ("Vergi, kurumlar vergisi oranıyla (%25) hesaplanmış; 193'teki peşin ödenen vergiler düşülmüştür. ") + ("STOK YETERSİZ ! " if z["yetersiz2"] else ("Zarar var; stoktan düşülecek kâr yok. " if z["kar_maliyetsiz"] <= 0
                                                                           else "Stok bu kârı karşılıyor. "))
                                + f"159 hesabındaki ithalat dışı tutar ({tl(z['s159'])} TL) stok sayılmamıştır."})
        if z["stok_ith"]:
            bolumler.append({"baslik": "Stok nasıl hesaplandı", "kolonlar": ["Hesap", "Kayıttaki kanıt", "Tutar (TL)"],
                             "satirlar": [["Stok hesapları (150, 151, 152, 153, 157, 158)", "—", tl(z["stok_ana"])]]
                             + [[f"{kod} {v[0]}", v[2], tl(v[1])] for kod, v in z["ith"].items()] + [["Stok toplamı", "", tl(z["stok"])]],
                             "stil": {len(z["ith"]) + 1: "kalin"},
                             "not": "İthalat olduğu muhasebe kayıtlarıyla (ithal KDV içeren gümrük fişi, beyanname no) doğrulanan 159 alt "
                                    "hesapları stok sayılmıştır."})
    return {"firma": firma, "donem": donem, "uretim": datetime.now().strftime("%d.%m.%Y"), "ozet": ozet, "bolumler": bolumler,
            "uyarilar": uyarilar, "odenecek": bool(k["odenecek"]), "bant": bant}


RENK = {"kirmizi": "#b42318", "yesil": "#1a7f37", "kalin": "#1f2933", "": "#1f2933"}


def html(r, logo=None):
    """Raporun yazdırılabilir HTML'i (PDF, Chrome ile bundan üretilir). logo: ofis logosunun PNG baytları (başlığın sağına konur)."""
    e = _html.escape
    resim = (f"<img class='logo' src='data:image/png;base64,{base64.b64encode(logo).decode('ascii')}' alt=''>" if logo else "")
    p = ["<!doctype html><html lang='tr'><head><meta charset='utf-8'><style>",
         "body{font-family:'Segoe UI',Arial,sans-serif;font-size:10.5pt;color:#1f2933;margin:0}",
         "h1{font-size:16pt;color:#1f3a5f;margin:0 0 2px}h2{font-size:11.5pt;color:#1f3a5f;margin:18px 0 6px}",
         ".alt{color:#6b7785;font-size:9pt;margin-bottom:12px}.kutular{display:flex;gap:10px;margin:10px 0 4px}",
         ".kutu{flex:1;border:1px solid #d9dde3;border-left:5px solid #1f3a5f;padding:8px 10px}.kutu .e{font-size:8.5pt;color:#6b7785}",
         ".kutu .d{font-size:14pt;font-weight:700}table{border-collapse:collapse;width:100%}th{background:#1f3a5f;color:#fff;text-align:left;",
         "padding:5px 8px;font-size:9.5pt}td{padding:4px 8px;border-bottom:1px solid #e3e7ed}td.s,th.s{text-align:right}",
         ".not{color:#6b7785;font-size:8.5pt;margin-top:4px}.uyari{border:1.5px solid #b42318;color:#b42318;padding:6px 10px;margin:4px 0;",
         "font-size:9.5pt}tr{page-break-inside:avoid}.bas{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}",
         ".logo{max-height:62px;max-width:150px;object-fit:contain}.bant{background:#b42318;color:#fff;padding:9px 14px;margin:8px 0 2px;",
         "display:flex;justify-content:space-between;align-items:center;font-size:11pt;font-weight:700}.bant b{font-size:15pt;white-space:nowrap}",
         ".bant-alt{font-size:10pt;font-weight:600;margin-top:4px}",
         "</style></head><body>",
         f"<div class='bas'><div><h1>KDV ve Kâr / Zarar Durumu</h1><div class='alt'>{e(r['firma'])} · {e(r['donem'])} · düzenleme "
         f"{e(r['uretim'])} · kaynak: Luca mizan ve Defteri Kebir</div></div>{resim}</div>"
         + (f"<div class='bant'><div><span>{e(r['bant'][0])}</span><div class='bant-alt'>{e(r['bant'][2])}</div></div>"
            f"<b>{e(r['bant'][1])}</b></div>" if r.get("bant") else "")
         + "<div class='kutular'>"]
    for et, deger, stil in r["ozet"]:
        p.append(f"<div class='kutu' style='border-left-color:{RENK[stil]}'><div class='e'>{e(et)}</div>"
                 f"<div class='d' style='color:{RENK[stil]};font-size:{14 if len(r['ozet']) <= 3 else 10.5}pt'>{e(deger)}</div></div>")
    p.append("</div>")
    for b in r["bolumler"]:
        p.append(f"<h2>{e(b['baslik'])}</h2><table><tr>" + "".join(
            f"<th class='{'s' if i else ''}'>{e(k)}</th>" for i, k in enumerate(b["kolonlar"])) + "</tr>")
        for n, s in enumerate(b["satirlar"]):
            stil = b["stil"].get(n, "")
            st = f" style='color:{RENK[stil]};font-weight:700'" if stil else ""
            p.append(f"<tr{st}>" + "".join(f"<td class='{'s' if i and (i == len(s) - 1 or len(s) > 3) else ''}'>{e(str(v))}</td>"
                                           for i, v in enumerate(s)) + "</tr>")
        p.append("</table>" + (f"<div class='not'>{e(b['not'])}</div>" if b.get("not") else ""))
    if r["uyarilar"]:
        p.append("<h2>Uyarılar ve notlar</h2>" + "".join(f"<div class='uyari'>{e(u)}</div>" for u in r["uyarilar"]))
    p.append("</body></html>")
    return "".join(p)


def _tutar(v):
    """'1.234,56' biçimli metin sayıysa float, değilse metnin kendisi (Excel'de sayı olarak dursun)."""
    s = str(v).strip()
    if re.fullmatch(r"-?\d{1,3}(\.\d{3})*,\d{2}", s):
        return float(s.replace(".", "").replace(",", "."))
    return v


def excel(r):
    """Raporun Excel'i (bayt): tek sayfa, bölümler alt alta."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "KDV ve Kar-Zarar"
    kalin, beyaz, dolgu = Font(bold=True), Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
    renk = {"kirmizi": Font(bold=True, color="B42318"), "yesil": Font(bold=True, color="1A7F37"), "kalin": kalin}
    ws.append(["KDV ve Kâr / Zarar Durumu"])
    ws["A1"].font = Font(bold=True, size=14, color="1F3A5F")
    ws.append([f"{r['firma']} · {r['donem']} · düzenleme {r['uretim']} · kaynak: Luca mizan ve Defteri Kebir"])
    ws.append([])
    if r.get("bant"):
        ws.append([r["bant"][0], _tutar(r["bant"][1].removesuffix(" TL"))])
        for c in ws[ws.max_row]:
            c.font, c.fill = Font(bold=True, color="FFFFFF", size=12), PatternFill("solid", fgColor="B42318")
        ws.cell(ws.max_row, 2).number_format = "#,##0.00"
        ws.append([r["bant"][2]])
        ws.cell(ws.max_row, 1).font = Font(bold=True, color="B42318")
    for et, deger, stil in r["ozet"]:
        ws.append([et, _tutar(deger.removesuffix(" TL"))])
        for c in ws[ws.max_row]:
            c.font = renk.get(stil, kalin)
        ws.cell(ws.max_row, 2).number_format = "#,##0.00"
    for b in r["bolumler"]:
        ws.append([])
        ws.append([b["baslik"]])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=11, color="1F3A5F")
        ws.append(b["kolonlar"])
        for c in ws[ws.max_row]:
            c.font, c.fill = beyaz, dolgu
        for n, s in enumerate(b["satirlar"]):
            ws.append([_tutar(v) for v in s])
            for c in ws[ws.max_row]:
                if isinstance(c.value, float):
                    c.number_format = "#,##0.00"
                if b["stil"].get(n):
                    c.font = renk[b["stil"][n]]
        if b.get("not"):
            ws.append([b["not"]])
            ws.cell(ws.max_row, 1).font = Font(italic=True, color="6B7785", size=9)
    if r["uyarilar"]:
        ws.append([])
        ws.append(["Uyarılar ve notlar"])
        ws.cell(ws.max_row, 1).font = Font(bold=True, size=11, color="B42318")
        for u in r["uyarilar"]:
            ws.append([u])
            ws.cell(ws.max_row, 1).font = Font(color="B42318")
            ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=False)
    ws.column_dimensions["A"].width = 62
    for h in "BCD":
        ws.column_dimensions[h].width = 24
    cikti = io.BytesIO()
    wb.save(cikti)
    return cikti.getvalue()
