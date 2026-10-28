# Набор eval (P2-DS-03.1, P3-DS-01, P3-DS-02)

Случаи для проверки агента по состоянию итогового плана: формат — CFC-11 (спека 0005 основного проекта), выписан в
[docs/data-phase3.md](../docs/data-phase3.md). Прогонщик живёт в репозитории бэкенда: файлы отсюда копируются туда как
есть, случаи отбираются по полю `split`, а не по папке.

## Что где лежит

| Папка | Что | Формат |
|---|---|---|
| `eval/cases/` | dev-набор, на нём чинят агента | `<id>.yaml` по CFC-11, одна сессия `turns`; диалоги памяти — `memory-NN-*.json` (ниже) |
| `eval/holdout/` | holdout, `split: holdout`, до фазы 4 бэкенду не копируется | то же; текст запросов здесь и в журнале изменений не пересказывается |

Теги CFC-11: `vague`, `multi_intent`, `change`, `off_topic`, `foreign_citizen`, `visa`, `injection`; свои теги
(`kids`, `sea`, `rail`, `phase1`, `phase2`, `memory` и т. п.) нужны для среза метрик. Состав на сейчас: dev — 18
случаев в YAML и 5 диалогов памяти, теги `vague` 5, `multi_intent` 4, `change` 6, `off_topic` 3; holdout — 5 случаев,
теги `vague`, `multi_intent`, `change`, `off_topic`. Случаи на `foreign_citizen`, `visa`, `injection` пишет Саня.

Правила: один случай проверяет одну вещь; в `expect` только ключи из CFC-11 (`stage`, `constraints`, `destination`,
`components`, `checks_without_fail`, `order`, `max_clarifications`); города и направления — `id` из снапшота
(`moscow`, `sochi`, `kazan`, `istanbul`…); ожидаемый визовый статус сверен с `visa.jsonl` для паспорта РФ.

## Допущения DS

Закрытого списка ключей `expect` и точных правил сверки в спеке нет, поэтому:

- `stage` читается как «этап, до которого сценарий должен дойти»; для запросов без выбора это `inspire`, `search`
  или `compare`, после `select` — `assemble`, после `checkout` — `checkout`, а после оплаты — `post_purchase`;
- значения `origin` и `destination` — `id` городов и направлений снапшота; даты — через `dates.nights`;
- `child_ages_len` и `preferences.moods` взяты из примера CFC-11, остальные пути — из `Constraints` в `openapi.yaml`;
- текст ответов агента случаи не проверяют.

Бэкенд пришлёт поправки по итогам прогона, файлы приводятся к ним в `P3-DS-03.1`.

## Диалоги памяти (P2-DS-03.1)

Пять пар сессий `memory-NN-<ключ>.json`: в первой сессии пользователь называет одно устойчивое предпочтение вместе с
разовой поездкой, во второй не повторяет его, и агент должен подставить значение из профиля. Формат CFC-11 второй
сессии не описывает, поэтому они остаются JSON с массивом `sessions` (допущение DS), но словарь приведён к контракту:
`rail` вместо `train`, `origin` вместо `origin_city_id`, пути из `Constraints`, допущения профиля — `field`, `value`,
`origin: profile`.

| Файл | Ключ профиля | Что называет первая сессия | Что должна выдать вторая |
|---|---|---|---|
| `memory-01-origin.json` | `origin` | живёт в Казани | допущение `origin: kazan` из профиля, вопроса «откуда вылетаете» нет |
| `memory-02-pets.json` | `pets` | едет с собакой | допущение `pets: true`, отели с `pets: false` не предлагаются |
| `memory-03-family.json` | `adults`, `child_ages` | двое взрослых, дети 4 и 9 лет | допущения `adults: 2`, `child_ages: [4, 9]` |
| `memory-04-free-cancellation.json` | `preferences.free_cancellation` | бронировать только с бесплатной отменой | допущение `preferences.free_cancellation: true`, невозвратные тарифы не показываются |
| `memory-05-transport.json` | `preferences.transport_modes` | только поезд | допущение `preferences.transport_modes: ["rail"]`, план на поезде |

Структура файла:

```json
{
  "id": "memory-01-origin",
  "split": "dev",
  "tags": ["memory", "vague"],
  "kind": "memory",
  "profile_key": "origin",
  "owner": "eval-memory-01-origin",
  "sessions": [
    {"turns": [{"role": "user", "text": "..."}]},
    {"turns": [{"role": "user", "text": "..."}],
     "expect": {
       "assumptions": [{"field": "origin", "value": "kazan", "origin": "profile"}],
       "constraints": {"origin": "kazan"},
       "must_not_ask": ["origin"],
       "must_not_carry_over": ["destination", "dates"]
     }}
  ],
  "note": "..."
}
```

`sessions` — две сессии одного `owner`: между ними память профиля сохраняется, диалог нет; `must_not_carry_over` —
что из первой сессии во второй попасть не должно (направление и даты в профиль не попадают).
