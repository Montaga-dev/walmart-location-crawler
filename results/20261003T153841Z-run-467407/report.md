# Run 20261003T153841Z-run-467407

| Metric | Value |
| --- | --- |
| Status | complete |
| Stop reason | N/A |
| Started (UTC) | 2026-10-03T15:38:41+00:00 |
| Started (Istanbul) | 2026-10-03T18:38:41+03:00 |
| Finished (UTC) | 2026-10-03T19:06:50+00:00 |
| Finished (Istanbul) | 2026-10-03T22:06:50+03:00 |
| Total execution time (s) | 12489.169 |
| Target requests | 10000 |
| Total attempted requests | 10000 |
| Successful requests | 10000 |
| Failed requests | 0 |
| Cancelled or in-flight requests | 0 |
| Unattempted requests | 0 |
| Success rate (%) | 100.0 |
| Requests per minute | 48.04 |
| Successful requests per minute | 48.04 |
| Locations used | 10 |
| Retry count | 113 |
| Session recoveries | 0 |
| Location refreshes | 2 |
| Blocked responses | 0 |
| CAPTCHA responses | 0 |
| HTTP attempts (setup, redirects, retries) | 11117 |
| HTTP failures | 2 |
| Category product cards | 19254 |

## Requests per type

| Type | Target | Attempted | Successful | Failed |
| --- | --- | --- | --- | --- |
| product | 9600 | 9600 | 9600 | 0 |
| category | 400 | 400 | 400 | 0 |

## Requests per location

| ZIP | City | Target | Attempted | Successful | Failed | Products OK | Categories OK | Retries | Recoveries | Blocked | CAPTCHA |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10001 | New York | 1000 | 1000 | 1000 | 0 | 960 | 40 | 15 | 0 | 0 | 0 |
| 90001 | Los Angeles | 1000 | 1000 | 1000 | 0 | 960 | 40 | 14 | 0 | 0 | 0 |
| 60601 | Chicago | 1000 | 1000 | 1000 | 0 | 960 | 40 | 13 | 0 | 0 | 0 |
| 77001 | Houston | 1000 | 1000 | 1000 | 0 | 960 | 40 | 10 | 0 | 0 | 0 |
| 85001 | Phoenix | 1000 | 1000 | 1000 | 0 | 960 | 40 | 7 | 0 | 0 | 0 |
| 19103 | Philadelphia | 1000 | 1000 | 1000 | 0 | 960 | 40 | 9 | 0 | 0 | 0 |
| 78205 | San Antonio | 1000 | 1000 | 1000 | 0 | 960 | 40 | 8 | 0 | 0 | 0 |
| 92101 | San Diego | 1000 | 1000 | 1000 | 0 | 960 | 40 | 5 | 0 | 0 | 0 |
| 75201 | Dallas | 1000 | 1000 | 1000 | 0 | 960 | 40 | 17 | 0 | 0 | 0 |
| 98101 | Seattle | 1000 | 1000 | 1000 | 0 | 960 | 40 | 15 | 0 | 0 | 0 |

A request is one product or category page read. Session setup,
redirects and retries count as HTTP attempts, not as requests.

## Configuration

```json
{
  "locations": [
    {
      "zip": "10001",
      "name": "New York",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "90001",
      "name": "Los Angeles",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "60601",
      "name": "Chicago",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "77001",
      "name": "Houston",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "85001",
      "name": "Phoenix",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "19103",
      "name": "Philadelphia",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "78205",
      "name": "San Antonio",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "92101",
      "name": "San Diego",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "75201",
      "name": "Dallas",
      "product_requests": 960,
      "category_requests": 40
    },
    {
      "zip": "98101",
      "name": "Seattle",
      "product_requests": 960,
      "category_requests": 40
    }
  ],
  "categories": [
    "https://www.walmart.com/browse/clothing/womens-dresses/5438_133162_3074670_538874",
    "https://www.walmart.com/browse/mens-shirts/mens-t-shirts/5438_133197_6286551_3187021",
    "https://www.walmart.com/browse/clothing/nike-women-s/best-rated/5438_3317124_7807333_9051496?facet=customer_rating%3A4+-+5+Stars",
    "https://www.walmart.com/browse/mens-shoes/all-mens-shoes/5438_1045804_1045807_426207",
    "https://www.walmart.com/browse/laptops/all-laptop-computers/3944_1089430_3951_132960",
    "https://www.walmart.com/browse/tv-home-theater/all-tvs/3944_1060825_447913",
    "https://www.walmart.com/browse/audio/headphones/3944_133251_1095191",
    "https://www.walmart.com/browse/printers-supplies/printers/3944_1089430_37807_163957",
    "https://www.walmart.com/browse/home/coffee-makers/4044_90548_90546_1115306_5438660",
    "https://www.walmart.com/browse/fryers/air-fryers/4044_90548_90546_4824_9960466",
    "https://www.walmart.com/browse/home/vacuum-cleaners/4044_90548_4047_3525958",
    "https://www.walmart.com/browse/home/refrigerators/4044_90548_9963786",
    "https://www.walmart.com/browse/pots-pans/cookware-sets/4044_623679_8140341_599265",
    "https://www.walmart.com/browse/home/bed-sheets-pillowcases/4044_539103_5055527",
    "https://www.walmart.com/browse/bath/bath-towels/4044_539095_7211899",
    "https://www.walmart.com/browse/home/storage-bins/4044_90828_1230055_9867281",
    "https://www.walmart.com/browse/dog-food-and-treats/all-dog-food/5440_202072_6432755_1015000",
    "https://www.walmart.com/browse/pets/cat-food/5440_202073_1749780",
    "https://www.walmart.com/browse/baby/diapers/5427_486190_1101406",
    "https://www.walmart.com/browse/baby/all-baby-strollers/5427_118134_999403",
    "https://www.walmart.com/browse/games-puzzles/board-games/4171_4191_133123",
    "https://www.walmart.com/browse/toys/building-sets-blocks/4171_4186",
    "https://www.walmart.com/browse/toys/all-bicycles/4171_1081404_7499461",
    "https://www.walmart.com/browse/sports-outdoors/camping-tents/4125_546956_4128_887708_2294923",
    "https://www.walmart.com/browse/skin-care/moisturizers/1085666_1007039_9837318",
    "https://www.walmart.com/browse/beauty/shampoos/shampoos/1085666_3147628_5752434",
    "https://www.walmart.com/browse/toothpaste/all-toothpaste/1005862_1007221_1023020_8164419",
    "https://www.walmart.com/browse/household-essentials/laundry-detergents/1115193_1071967_1149379",
    "https://www.walmart.com/browse/clothing/shop-all-backpacks/5438_1045799_4662138_4951948",
    "https://www.walmart.com/browse/home/luggage-sets/4044_7333633_6723035",
    "https://www.walmart.com/browse/watches/watches/3891_3906_1012759",
    "https://www.walmart.com/browse/clothing/sunglasses/5438_1045799_4350993",
    "https://www.walmart.com/browse/feature/office-chairs/14503_1743450_1072386",
    "https://www.walmart.com/browse/home/desks/desks/4044_103150_97116_91851",
    "https://www.walmart.com/browse/power-tools/power-drills/1072864_1031899_1066925_3413875",
    "https://www.walmart.com/browse/tools/hand-tools/1072864_1031899_1067609",
    "https://www.walmart.com/browse/oils-and-fluids/motor-oil/91083_1104294_1072084",
    "https://www.walmart.com/browse/automotive-tires/car-tires/91083_1077064_8752379_4838964_1063465",
    "https://www.walmart.com/browse/dogs/dog-toys/5440_202072_2243339",
    "https://www.walmart.com/browse/sports-outdoors/yoga-mats/4125_4134_1078384_1078386"
  ],
  "product_file": "product_ids.json",
  "use_proxy": false,
  "proxy_count": 0,
  "sessions_per_ip": 4,
  "http_per_minute_per_ip": 55,
  "max_seconds": 18000
}
```
