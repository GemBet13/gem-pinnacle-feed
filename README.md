# gem-pinnacle-feed

Публичные снимки прематч-линии **Pinnacle** по футболу (sport 29): исходы 1X2, форы и тоталы.

- Источник: открытый гостевой API `guest.api.arcadia.pinnacle.com`, без логина и без ключей.
- Это не калькулятор, не советы по ставкам и не линия Fonbet.
- Секреты в репозиторий не входят.

## Файлы (ветка `feed`)

| Файл | Содержание |
|---|---|
| [latest.json](https://raw.githubusercontent.com/GemBet13/gem-pinnacle-feed/feed/latest.json) | Текущий снимок ещё не начавшихся матчей |
| [closings.json](https://raw.githubusercontent.com/GemBet13/gem-pinnacle-feed/feed/closings.json) | Последний снимок каждого матча за 0–30 мин до старта (предпочитаем 0–15), окно ~4 дня |
| [status.json](https://raw.githubusercontent.com/GemBet13/gem-pinnacle-feed/feed/status.json) | Короткий статус последнего прогона |
| [punctuality.json](https://raw.githubusercontent.com/GemBet13/gem-pinnacle-feed/feed/punctuality.json) | Фактическое опоздание cron |

Запасные адреса той же ветки:

- GitHub Pages: `https://gembet13.github.io/gem-pinnacle-feed/latest.json`
- jsDelivr (может кэшировать до нескольких часов, проверяйте поле `ts`): `https://cdn.jsdelivr.net/gh/GemBet13/gem-pinnacle-feed@feed/latest.json`

Ветка `feed` публикуется одним коммитом с force-push, чтобы не раздувать историю.

## Расписание

GitHub Actions, cron UTC `3,18,33,48 * * * *` (четыре раза в час, в стороне от :00/:15/:30/:45).  
Запуски по cron у GitHub часто опаздывают или пропускаются; фактическая задержка пишется в `punctuality.json`.

## Формат события

```json
{"id": 123, "lg": "England - Premier League", "h": "Home", "a": "Away",
 "start": "2026-11-27T16:00:00Z", "x12": [2.1, 3.4, 3.5],
 "ah": [[-0.5, 1.95, 1.95]], "tot": [[2.5, 1.90, 2.00]]}
```

Коэффициенты в десятичном виде. Closing считается на стороне сбора: из снимков, попавших в окно 0–30 минут до `start`, остаётся самый близкий к началу матча.
