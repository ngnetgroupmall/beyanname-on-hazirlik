# Beyanname Ön Hazırlık

Mali müşavirlik ofisleri için: seçilen mükellef ve dönemin beyanname hazırlık verilerini **E-Mükellef** ve **Luca**'dan
indirip firma klasörüne düzenli kaydeden Windows programı (beyannameler, e-fatura / e-arşiv, ödemeler, ÖKC, POS, ithalat,
GİB yazışmaları, Luca kebir / mizan / bordro…).

Program **verisiz** gelir: içinde hiçbir ofisin mükellef listesi ya da giriş bilgisi yoktur. Her kullanıcı kendi E-Mükellef
adresini ve girişini yapar; mükellef listesi o girişte indirilir. Giriş bilgileri yalnız o bilgisayarda (Windows hesabına
bağlı, şifreli) saklanır; hiçbir yere gönderilmez.

## Kurulum

1. [Releases](../../releases/latest) sayfasından `Beyanname On Hazirlik (...).zip` dosyasını indirin.
2. Zip'i yazılabilir bir klasöre çıkarın (ör. Belgeler). *Program Files* olmasın.
3. `Beyanname On Hazirlik.bat` dosyasına çift tıklayın.

Gerekenler: Windows 10/11 (64 bit) ve Google Chrome. Python kurmak gerekmez. Ayrıntı: paketteki `OKU BENI.txt`.

## İlk açılış

1. **E-Mükellef adresi** sorulur (ör. `ofisadi.emukellef.org`).
2. Firma dosyalarının kaydedileceği **ana klasör** sorulur.
3. E-Mükellef'e giriş yapılır; mükellef listeniz kendiliğinden gelir.
4. Luca sorguları için "Luca girişi" ile Luca'ya girilir.

**İndirilecekleri kaydetme:** istediğiniz sorguları işaretleyip *Bu seçimi kaydet* derseniz program bundan sonra o seçimle açılır.

## Otomatik güncelleme

Program her açılışta bu depodaki `surum.json` dosyasına bakar; yeni sürüm varsa değişen dosyaları indirir, doğrular (sha256)
ve yeni sürümle açılır. Yeni sürüm açılamazsa önceki sürüme döner. İnternet yoksa eldeki sürümle çalışır.

## Geliştirici notu (yeni sürüm yayınlama)

```
python -X utf8 surum_yaz.py      # surum.json'u yeniler (dosyalar değiştiyse sürüm numarası artar)
git add -A
git commit -m "Sürüm ..."
git push
```

- `guncelle.py` içindeki `DEPO` bu deponun adıdır; depo **herkese açık** olmalıdır.
- `.gitattributes` satır sonu dönüşümünü kapatır; kaldırmayın (sha256 özetleri bozulur).
- Python sürümü ya da kitaplıklar değişirse: `paketle.py` içindeki `PAKET_NO` artırılır, `surum_yaz.py --paket-en-az N`
  çalıştırılır, `python -X utf8 paketle.py` ile yeni zip üretilip Releases'a yüklenir.
- `firmalar.json` ve not dosyaları `.gitignore` içindedir; depoya ofis verisi girmez. `paketle.py` de pakete girecek
  dosyalarda ofise özel iz bulursa paketi üretmez.
