# YummyAnime / Kodik Downloader

Скрипты для скачивания аниме с [YummyAnime](https://ru.yummyani.me) через плеер Kodik.

## Требования

- **Python 3.10+** (используется только стандартная библиотека, без pip-зависимостей)
- **ffmpeg** — для склейки HLS-сегментов в MP4

## Быстрый старт

### Скачать сезон с YummyAnime

```bash
python yummy_download.py "https://ru.yummyani.me/catalog/item/mushoku-tensei-iii-isekai-ittara-honki-dasu"
```

Скрипт:
1. Получает список доступных озвучек (только Kodik)
2. Предлагает выбрать одну или все
3. Скачивает все серии выбранной озвучки в максимальном качестве

Результат сохраняется в папку `Название аниме/Озвучка/episode_01.mp4`.

#### Параметры

```
python yummy_download.py <url> [качество]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на страницу аниме на YummyAnime |
| `качество` | `360`, `480` или `720` (по умолчанию — лучшее доступное) |

#### Примеры

```bash
# Скачать в 720p
python yummy_download.py "https://ru.yummyani.me/catalog/item/mushoku-tensei-iii-isekai-ittara-honki-dasu" 720

# Скачать в 480p (экономия трафика)
python yummy_download.py "https://ru.yummyani.me/catalog/item/one-piece-tv" 480
```

Уже скачанные серии (>10 МБ) пропускаются — можно перезапускать без потери прогресса.

---

### Скачать одно видео по ссылке Kodik

```bash
python kodik_download.py "https://kodikplayer.com/seria/1638990/f1e45c9dbc7b30187a882da0005175cc/720p"
```

#### Параметры

```
python kodik_download.py <url> [качество] [файл]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на Kodik-плеер |
| `качество` | `360`, `480` или `720` |
| `файл` | Имя выходного файла (по умолчанию `video.mp4`) |

#### Примеры

```bash
python kodik_download.py "https://kodikplayer.com/seria/1638990/.../720p" 720 episode1.mp4

# Ссылка с параметрами сезона/эпизода тоже работает
python kodik_download.py "https://kodikplayer.com/season/120995/hash/720p?episode=1" 720 ep1.mp4
```

## Как это работает

1. Парсит страницу плеера Kodik, извлекает параметры (`type`, `hash`, `id`, подписи)
2. POST-запрос на внутренний API Kodik — получает зашифрованные ссылки на видео
3. Декодирует URL (ROT18 + Base64) → HLS-манифест (.m3u8)
4. Параллельно скачивает TS-сегменты (8 потоков)
5. Склеивает через ffmpeg в MP4
