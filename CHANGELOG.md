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

[unreleased]: https://github.com/parnickname/ostrovok-parser/commits/main
