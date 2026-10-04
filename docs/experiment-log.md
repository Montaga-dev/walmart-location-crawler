# Deney Notları

1–4 Ekim 2026 testlerinden mevcut çözümü belirleyen bulgular.
Kurulum ve çalıştırma için [README](../README.md).

## Neden bu akış seçildi?

- **Ürün ve kategori verisi HTML'den okunuyor.** Sayfadaki `__NEXT_DATA__`
  JSON'u kullanılıyor. Ürün GraphQL'i kısa denemelerde çalışsa da uzun
  rotasyon testinde 1.290 HTTP denemesinden yalnızca 33'ü doğrulanmış ürün
  verdi; 1.247 CAPTCHA yanıtı alındı. HTML akışı 10k çekimini tamamladı.
- **Konum için GraphQL kullanılıyor.** Yeni misafir oturumu önce bir ürün
  sayfasını açıyor, ardından `UpdatePostalCode` ile ZIP'i ayarlıyor.
  Sonrasında ürün HTML'inden ZIP, mağaza ve `SHIPPING` doğrulanıyor.
- **İstemci `curl_cffi`.** Varsayılan `httpx` engellendi; Chrome/Firefox
  User-Agent ile kısa testlerde çalıştı. Uzun süreli doğrulama `curl_cffi`
  ile yapıldığı için bu istemci korundu. TLS parmak izinin tek başına
  belirleyici olduğu gösterilmedi.
- **Her yanıtın konumu kontrol ediliyor.** Bazı HTTP 200 yanıtları istenen
  ZIP yerine varsayılan konumu taşıdı. Aynı oturumda tekrar okumak bu
  hataları düzeltebildi. Yanlış konum başarı sayılmıyor; sınırlı tekrar,
  ZIP yenileme ve gerektiğinde yeni oturum açma uygulanıyor.
- **Hız sınırı 55 HTTP/dk.** Kurulum, yönlendirmeler ve tekrarlar bu sınıra
  dahil. Yerel modda dört worker aynı sınırı paylaşıyor. Ürünlerin tam
  adresleri ortak saklanarak tekrarlanan yönlendirmeler azaltılıyor;
  ürün yanıtları ve oturum çerezleri paylaşılmıyor.

## UpdatePostalCode başlık testi — 4 Ekim 2026

Elle eklenen 18 başlık, ikişer kez tek tek çıkarılarak denendi.
Aşağıdaki beş başlık çıkarıldığında çağrı başarısız oldu ve kodda tutuldu:

| Başlık | Çıkarıldığında alınan HTTP durumu |
| --- | --- |
| `content-type` | 415 |
| `x-apollo-operation-name` | 418 |
| `x-o-platform` | 429 |
| `x-o-platform-version` | 429 |
| `x-o-segment` | 429 |

Diğer 13 başlık birlikte çıkarıldığında, iki yeni misafir oturumu dahil
konum doğrulaması başarılıydı. Çerezler ve `curl_cffi` tarafından eklenen
Chrome başlıkları korundu; sonuç bu endpoint ve test edilen yapılandırma
için geçerlidir.

## Tamamlanan 10k çekimi — 3 Ekim 2026

Yerel bağlantı, dört worker ve ortak 55 HTTP/dk sınırı kullanıldı.
On ZIP'in her birinde aynı 960 ürün ve 40 kategori sayfası okundu.

| Ölçüm | Sonuç |
| --- | ---: |
| Hedef / başarılı okuma | 10.000 / 10.000 |
| Ürün / kategori sayfası okuması | 9.600 / 400 |
| Benzersiz ürün / kategori URL'si | 960 / 40 |
| Süre | 3 saat 28 dakika 9 saniye |
| Başarılı okuma / dakika | 48,04 |
| Toplam HTTP denemesi | 11.117 |
| Tekrar / ZIP yenileme / oturum yenileme | 113 / 2 / 0 |
| HTTP hatası / son durumda başarısız okuma | 2 / 0 |
| CAPTCHA / block yanıtı | 0 / 0 |

10.000 sayısı benzersiz ürün sayısı değil, konumlar boyunca tamamlanan
ürün ve kategori okumalarının toplamıdır. HTTP sayısı kurulum,
yönlendirme ve tekrarları da içerir.

Kaynak: [çekim raporu](../results/20261003T153841Z-run-467407/report.md)
ve [özet](../results/20261003T153841Z-run-467407/summary.json).
