# Формат снапшота данных

Это справочник по контракту между парсером (этот репозиторий) и бэкендом. Разделы ниже взяты дословно из спек основного
репозитория `lowcoware/ostrovok-hack-horkilion` на коммите `485dd01`:

- «CFC-3. Снапшот данных» и «CFC-5. Синтетика» — спека 0003 (`docs/specs/0003-phase1-plan.md`);
- «Статусы визы» и «Файлы снапшота» — спека 0002 (`docs/specs/0002-visa-and-citizenship.md`).

Формат меняется только через спеку основного проекта: бэкенд читает снапшот по этому описанию. Упоминания каталогов
`internal/`, `proto/` и `api/` относятся к репозиторию бэкенда, здесь их нет.

### CFC-3. Снапшот данных: DS → BE

DS готовит данные файлами, BE читает их при старте `inventory`. Никто не ходит во внешние API во время хода агента, кроме погоды Open-Meteo, как в фазе 0.

Раскладка:

```
data/
  requirements.txt          зависимости загрузчиков с версиями
  loaders/<источник>/       скрипты загрузки, по каталогу на источник
  overrides/                ручные правки поверх источников (visa.csv и т. п.)
  reports/                  отчёты сверки источников (например, visa-diff.md)
  sources.yaml              реестр источников: и взятых, и рассмотренных (build.py кладёт копию в snapshot/)
  build.py                  собирает snapshot/ из loaders/ и overrides/
  validate.py               проверяет snapshot/ по схемам ниже
  snapshot/
    manifest.json
    sources.yaml            копия реестра: по ней бэкенд раскрывает имена из `sources` (вид, ссылка, лицензия)
    <набор>.jsonl
```

Формат наборов:

- JSON Lines, UTF-8, один объект на строку, поля в `snake_case`.
- Коды стран — ISO 3166-1 alpha-2 в верхнем регистре; аэропорты — IATA; координаты — `lat`, `lon` (WGS 84); даты — `YYYY-MM-DD`; деньги — целые рубли; часовые пояса — IANA.
- У каждого объекта стабильный `id`: от него BE считает seed синтетики, поэтому `id` не меняется между пересборками снапшота.
- Поле `sources` — список имён провайдеров из `sources.yaml`.
- У наборов `climate`, `routes`, `rail_routes`, `airports`, `cities` поля `sources` нет: климат ссылается на провайдера `open-meteo`, поэтому он обязан быть в реестре со `status: used`.

`manifest.json`:

```json
{
  "built_at": "2026-10-26T12:00:00Z",
  "datasets": [
    {"name": "visa", "file": "visa.jsonl", "records": 39402, "sha256": "…", "schema_version": 1}
  ]
}
```

`sources.yaml` — реестр для страницы «Откуда данные» (`spec.md`, раздел 6):

```yaml
- provider: passport-index-data
  title: Passport Index Data
  url: https://github.com/imorte/passport-index-data
  license: MIT
  kind: real            # real | synthetic | derived
  status: used          # used | considered
  used_for: [визовый режим]
  fetched_at: 2026-10-26T10:00:00Z
  reason: ""            # для considered — почему не взяли
```

Наборы фазы 1:

| Набор | Поля | Задача DS | Читает BE |
|---|---|---|---|
| `countries` | `code`, `name_ru`, `name_en` | `P1-DS-02` | `catalog.ListCountries` |
| `visa` | `passport`, `destination`, `status`, `days?`, `sources` | `P1-DS-02` | `catalog.GetVisaRequirement`, фильтр подбора |
| `cities` | `id`, `name_ru`, `name_en`, `country_code`, `lat`, `lon`, `timezone`, `population`, `airports[]`, `stations[]` | `P1-DS-04` | `catalog.SearchPlaces`, город отправления |
| `airports` | `iata`, `name`, `city_id`, `country_code`, `lat`, `lon`, `timezone`, `scheduled` | `P1-DS-04` | `transport` |
| `stations` | `code`, `name`, `city_id`, `lat`, `lon` (только Россия) | `P1-DS-06` | `transport` |
| `destinations` | `id`, `city_id`, `name`, `country_code`, `tags[]`, `airports[]`, `stations[]`, `description`, `sources` | `P1-DS-03` | `catalog.SearchDestinations` |
| `climate` | `destination_id`, `month`, `temp_max_c`, `temp_min_c`, `precipitation_mm`, `sea_temp_c?` | `P1-DS-03` | оценка погоды для подбора |
| `hotels` | `id`, `destination_id`, `name`, `lat`, `lon`, `stars?`, `check_in_time?`, `check_out_time?`, `pets?`, `osm_id`, `sources` | `P1-DS-05` | `lodging` провайдер `osm` |
| `routes` | `from_iata`, `to_iata`, `distance_km`, `direct` | `P1-DS-06` | `transport`, время в пути |
| `rail_routes` | `from_station`, `to_station`, `duration_min`, `departures[]` (`HH:MM`), `train_numbers[]`; `departures[i]` — отправление поезда `train_numbers[i]`, массивы одной длины | `P1-DS-06` | `transport`, ж/д |
| `calibration` | одна строка, один объект; поля — в блоке ниже | `P1-DS-07` | синтетика цен во всех провайдерах |

Структура `calibration` (все деньги в рублях; уровень цен страны — множитель к российскому):

```json
{
  "bed_price_rub": {"2": 1900, "3": 2700, "4": 4200, "5": 7500},
  "country_price_level": {"RU": 1.0, "TR": 0.9},
  "season_factor": [1.1, 1.0, 1.0, 1.0, 1.15, 1.35, 1.35, 1.35, 1.15, 1.0, 1.0, 1.25],
  "airfare": {"base_rub": 2500, "rub_per_km_short": 7, "rub_per_km_long": 5, "long_from_km": 2000, "transfer_minutes": 120},
  "rail": {"rub_per_hour": 450},
  "transfer": {"taxi_base_rub": 300, "taxi_rub_per_km": 40, "pets_surcharge_rub": 500,
               "shuttle_rub_per_person": 700, "public_rub_per_person": 120},
  "fx_rub": {"USD": 0.0},
  "tourist_tax_rub_per_night": {"sochi": 100},
  "sources": ["..."]
}
```

- `bed_price_rub` — цена за гостя за ночь по звёздам на российском уровне; `country_price_level` — для каждой страны каталога;
- `season_factor` — 12 множителей, январь первым;
- `airfare.transfer_minutes` — время на пересадку при стыковке рейсов;
- `tourist_tax_rub_per_night` — по `id` города, только где налог введён;
- `fx_rub` — курсы ЦБ РФ на дату сборки, справочно.

Типы и проверки — `internal/dataset/dataset.go`; бэкенд не стартует, если нет 12 сезонных множителей, цены для 2–5 звёзд или уровня цен страны любого направления.

Проверки:

- `data/validate.py` падает на нарушении схемы, дубле `id` или неизвестном имени в `sources`.
- `inventory` не стартует, если набора из манифеста нет на диске или `sha256` не совпадает (как с неизвестным `*_PROVIDER`).
- CI запускает `validate.py`, когда меняется `data/`.

Хранение: снапшот до 50 МБ лежит в git. Если больше — архив прикладывается к GitHub Release, а `scripts/fetch-data.sh` скачивает его по версии из `data/snapshot.version`; это потолок, а не стартовое решение.


### CFC-5. Синтетика: DS → BE

- DS отдаёт параметры (`calibration`), BE пишет формулы.
- Цена детерминирована: seed — от `id` объекта и даты, как у отелей в фазе 0. Одинаковый вход — одинаковая цена в поиске и в `GetOffer`.
- Цены в рублях: зарубежные уровни цен переводятся по курсу ЦБ из `calibration`.


### Статусы визы

Один набор значений для API, proto и снапшота:

| API (`VisaStatus`) | proto (`VisaRequirement.Status`) | passport-index-data | Без визы для подбора |
|---|---|---|---|
| `home` | `STATUS_HOME` | `-1` (паспорт = страна) | да |
| `visa_free` | `STATUS_VISA_FREE` | `visa free` или число дней | да |
| `visa_on_arrival` | `STATUS_VISA_ON_ARRIVAL` | `visa on arrival` | да |
| `eta` | `STATUS_ETA` | `eta` | нет, оформляется онлайн |
| `e_visa` | `STATUS_E_VISA` | `e-visa` | нет, оформляется онлайн |
| `visa_required` | `STATUS_VISA_REQUIRED` | `visa required` | нет |
| `no_admission` | `STATUS_NO_ADMISSION` | `no admission` | нет, направление не предлагается |


### Файлы снапшота

Формат каталога `data/`, манифест и общие правила — спека 0003, CFC-3. Для этой спеки два набора:

`data/snapshot/countries.jsonl`, одна страна на строку:

```json
{"code": "BY", "name_ru": "Беларусь", "name_en": "Belarus"}
```

`data/snapshot/visa.jsonl`, одна пара «паспорт — страна» на строку, все 199 паспортов:

```json
{"passport": "RU", "destination": "TR", "status": "visa_free", "days": 60, "sources": ["passport-index-data"]}
{"passport": "RU", "destination": "EG", "status": "visa_on_arrival", "days": 30, "sources": ["passport-index-data", "mid-ru"]}
```

`status` — значения столбца «API» из таблицы статусов. `days` — необязательное. `sources` — имена провайдеров из манифеста. Ручные правки по справке МИД лежат в `data/overrides/visa.csv` (`passport,destination,status,days,comment`) и вливаются в `visa.jsonl` скриптом сборки снапшота; бэкенд читает только `visa.jsonl`.

