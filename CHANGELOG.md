# Журнал изменений

Все заметные изменения проекта записываются в этот файл.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект придерживается [Семантического версионирования](https://semver.org/lang/ru/spec/v2.0.0.html).

## [Unreleased]

### Добавлено

- Отдельный репозиторий парсера данных: сборка и проверка снапшота, реестр источников и описание задач с распределением
  по людям. Технически: каталог `data/` перенесён из `lowcoware/ostrovok-hack-horkilion` без изменений кода
  (`build.py`, `validate.py`, `sources.yaml`, `requirements.txt`, пустые `loaders/`, `overrides/`, `reports/`,
  `snapshot/`); `docs/data.md` — задачи `P1-DS-01…08`, к заголовкам `P1-DS-02…08` добавлены исполнители, `P1-DS-07`
  разделена на `07.1`–`07.4` и `07.5`–`07.6`; `docs/snapshot-format.md` — дословные разделы CFC-3, CFC-5 и статусы
  виз из спек 0003 и 0002; `.github/workflows/ci.yml` — задачи `data` (`validate.py`) и `changelog` (только на PR);
  `scripts/check-changelog.py` из основного репозитория. (`0cdf3a2`)
- Решения команды в README: размер каталога, города отправления в СНГ, порядок сборки `calibration`, вокзалы в
  направлениях и заполнение аэропортов у городов, чтобы оба исполнителя работали по одному плану. Технически: раздел
  «Решения команды» в `README.md`: Q-006 (30–40 направлений), Q-007 (Беларусь, Казахстан, Узбекистан, Армения),
  `calibration` из фрагментов, склеиваемых `build.py` (сама правка `build.py` ещё не сделана), `stations: []` в `03.1`,
  `cities.airports[]` заполняется в `04.2`. (`b2b7c68`)
- Набор городов `cities` в снапшоте: 62 729 городов, среди них города отправления России, Беларуси, Казахстана,
  Узбекистана и Армении и города остальных стран для будущего каталога, с часовыми поясами и населением. Москва,
  Санкт-Петербург, Казань, Минск, Алматы, Ташкент и Ереван находятся. Технически: загрузчик
  `data/loaders/geonames/load.py` (`cities5000.zip` и `alternateNamesV2.zip` с download.geonames.org, кэш в `cache/`,
  не в git) пишет `out/cities.jsonl`; отбор — RU, BY, KZ, UZ, AM от 15 тыс. жителей или `PPLC`/`PPLA`, остальные страны
  целиком из `cities5000`; `id` — слаг ASCII-названия, при совпадении `-<страна>`, затем `-<geonameid>`; `airports` и
  `stations` пустые до `04.2` и `06.2`; у 32 302 городов нет русского названия в GeoNames, взято английское (из
  городов отправления таких 40: RU 7, UZ 33). `data/snapshot/cities.jsonl`, запись в `manifest.json`, повторная сборка
  даёт тот же `sha256`. (`5a2fe8d`, `8c5adaa`)
- Набор стран `countries` в снапшоте: 199 стран с названиями по-русски и по-английски, все паспорта и направления
  визовой матрицы, так что у `visa` не будет ссылок на неизвестную страну. Технически: загрузчик
  `data/loaders/passport-index-data/countries.py` берёт коды из `passport-index-matrix-iso2.csv`
  (`imorte/passport-index-data`), английское название и geonameId из `countryInfo.txt`, русское из
  `alternateNamesV2.zip` (GeoNames, кэш в `cache/`, не в git) и пишет `out/countries.jsonl`; русское название —
  предпочитаемое в GeoNames, у CD, KM и SC взято другое название той же страны («Заир» и сокращения «о-ва»
  заменены); загрузчик падает, если кода из матрицы нет в GeoNames или нет русского названия.
  `data/snapshot/countries.jsonl`, запись в `manifest.json`, `validate.py` проходит. (`eecadb4`)

[unreleased]: https://github.com/parnickname/ostrovok-parser/commits/main
