# Video Downloader

Скрипты для скачивания видео с YummyAnime (Kodik) и YouTube.

## Требования

- **Python 3.10+** (только стандартная библиотека, без pip-зависимостей)
- **ffmpeg** — для склейки HLS-сегментов в MP4
- **yt-dlp** — для YouTube (`pip install yt-dlp`)

## Скрипты

### yummy_download.py — Скачать сезон аниме с YummyAnime

```bash
python yummy_download.py "https://ru.yummyani.me/catalog/item/mushoku-tensei-iii-isekai-ittara-honki-dasu"
```

Скрипт:
1. Получает список озвучек (только Kodik)
2. Предлагает выбрать одну или все
3. Скачивает все серии в максимальном качестве

Результат: `Название аниме/Озвучка/episode_01.mp4`

```
python yummy_download.py <url> [качество]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на страницу аниме |
| `качество` | `360`, `480`, `720` (по умолчанию — лучшее) |

Уже скачанные серии (>10 МБ) пропускаются.

---

### kodik_download.py — Скачать одно видео по ссылке Kodik

```bash
python kodik_download.py "https://kodikplayer.com/seria/1638990/f1e45c9dbc7b30187a882da0005175cc/720p" 720 episode1.mp4
```

```
python kodik_download.py <url> [качество] [файл]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на Kodik-плеер |
| `качество` | `360`, `480`, `720` |
| `файл` | Имя выходного файла (по умолчанию `video.mp4`) |

---

### yt_download.py — Скачать видео или плейлист с YouTube

```bash
# Одно видео
python yt_download.py "https://www.youtube.com/watch?v=xxx" 1080

# Плейлист целиком
python yt_download.py "https://www.youtube.com/watch?v=xxx&list=PLxxx" 720
```

```
python yt_download.py <url> [качество]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на видео или плейлист YouTube |
| `качество` | `best`, `4k`, `1080`, `720`, `480`, `360` (по умолчанию `best`) |

Плейлист скачивается в папку `Название плейлиста/01. Название видео.mp4`.
Уже скачанные видео (>1 МБ) пропускаются.

## Как работает Kodik

1. Парсит страницу плеера, извлекает параметры (`type`, `hash`, `id`, подписи)
2. POST на `/ftor` — получает зашифрованные ссылки
3. Декодирует URL (ROT18 + Base64) → HLS-манифест
4. Параллельно скачивает сегменты (8 потоков)
5. Склеивает через ffmpeg в MP4
