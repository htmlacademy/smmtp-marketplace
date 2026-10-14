# Маркетплейс smmtp

Плагины со скиллами для Codex, Claude Code и Claude Cowork от [smmtp](https://smmtp.ru). Каждый скилл можно поставить отдельно через `npx skills`.

| Скилл | Назначение | Скиллы |
|---|---|---|
| [`crosspost`](plugins/amplify/README.md) | Кросспостинг через сервис smmtp: один пост сразу на несколько площадок с превью по каждой | `setup`, `crosspost` |
| [`news`](plugins/news/README.md) | Поиск новостей в архиве по дате, категории, сайту, автору и языку, чтение текста материала | `search` |

## Установка плагином

### Claude Code

```
/plugin marketplace add htmlacademy/smmtp-marketplace
/plugin install amplify@smmtp
/plugin install news@smmtp
```

Обновление — `/plugin marketplace update smmtp`.

### Codex

```bash
codex plugin marketplace add htmlacademy/smmtp-marketplace
codex plugin add amplify@smmtp
codex plugin add news@smmtp
```

Обновление — `codex plugin marketplace upgrade`.

### Claude Cowork

Customize → Plugins → Add marketplace, адрес `htmlacademy/smmtp-marketplace`. Затем установить нужные плагины из списка.

## Установка отдельных скиллов

[`npx skills`](https://github.com/vercel-labs/skills) ставит скилл без плагина, в `.claude/skills/` для Claude Code и в `.agents/skills/` для Codex. Здесь у скилла нет префикса плагина, поэтому имя у него полное:

| Скилл в плагине | Отдельный скилл |
|---|---|
| `amplify:setup` | `amplify-setup` |
| `amplify:crosspost` | `amplify-crosspost` |
| `news:search` | `news-search` |

```bash
npx skills add htmlacademy/smmtp-marketplace --list
npx skills add htmlacademy/smmtp-marketplace --skill amplify-setup --skill amplify-crosspost
npx skills add htmlacademy/smmtp-marketplace --skill news-search
```

## Требования

Python 3.9 или новее, только стандартная библиотека. В Codex и Cowork адрес сервиса должен быть доступен из песочницы: что настроить, подскажет сам скилл, когда упрётся в сеть.

## Разработка

Устройство репозитория и правила правок — [AGENTS.md](AGENTS.md), инструменты —
[scripts/README.md](scripts/README.md).

## Лицензия

[MIT](LICENSE)
