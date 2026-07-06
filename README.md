# Video Downloader

Скрипты для скачивания видео с YummyAnime (Kodik) и YouTube.

## Установка

### 1. Скачать проект

**Через git:**
```bash
git clone https://github.com/arhx/yummy.git
cd yummy
```

**Или скачать ZIP:**
[Скачать](https://github.com/arhx/yummy/archive/refs/heads/master.zip) → распаковать → открыть папку в терминале.

### 2. Установить зависимости

Нужен **Python 3.10+** и **ffmpeg**.

```bash
pip install -r requirements.txt
```

Это установит `yt-dlp` (для YouTube). Скрипты для Kodik работают без внешних зависимостей.

**ffmpeg** — если ещё не установлен:
- Windows: [скачать](https://www.gyan.dev/ffmpeg/builds/) и добавить в PATH
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg`

### 3. Готово

```bash
# Аниме с YummyAnime — выбор озвучки, скачивание сезона
python yummy_download.py "https://ru.yummyani.me/catalog/item/mushoku-tensei-iii-isekai-ittara-honki-dasu"

# YouTube плейлист
python yt_download.py "https://www.youtube.com/watch?v=xxx&list=PLxxx" 1080
```

---

## Использование

### yummy_download.py — Сезон аниме с YummyAnime

```bash
python yummy_download.py <url> [качество]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на страницу аниме на YummyAnime |
| `качество` | `360`, `480`, `720` (по умолчанию — лучшее) |

Скрипт покажет список озвучек (только Kodik), предложит выбрать одну или все, и скачает все серии.

Результат: `Название аниме/Озвучка/episode_01.mp4`

Уже скачанные серии (>10 МБ) пропускаются — можно перезапускать без потери прогресса.

```bash
python yummy_download.py "https://ru.yummyani.me/catalog/item/mushoku-tensei-iii-isekai-ittara-honki-dasu" 720
```

---

### kodik_download.py — Одно видео по ссылке Kodik

```bash
python kodik_download.py <url> [качество] [файл]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на Kodik-плеер |
| `качество` | `360`, `480`, `720` |
| `файл` | Имя выходного файла (по умолчанию `video.mp4`) |

```bash
python kodik_download.py "https://kodikplayer.com/seria/1638990/f1e45c9dbc7b30187a882da0005175cc/720p" 720 episode1.mp4
```

---

### yt_download.py — YouTube видео или плейлист

```bash
python yt_download.py <url> [качество]
```

| Параметр | Описание |
|---|---|
| `url` | Ссылка на видео или плейлист YouTube |
| `качество` | `best`, `4k`, `1080`, `720`, `480`, `360` (по умолчанию `best`) |

```bash
# Одно видео
python yt_download.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" 1080

# Плейлист — скачает все видео в папку
python yt_download.py "https://www.youtube.com/watch?v=xxx&list=PLxxx" 720
```

Плейлист: `Название плейлиста/01. Название видео.mp4`

Уже скачанные видео (>1 МБ) пропускаются.

---

## Как работает Kodik

1. Парсит страницу плеера, извлекает параметры (`type`, `hash`, `id`, подписи)
2. POST на `/ftor` — получает зашифрованные ссылки
3. Декодирует URL (ROT18 + Base64) → HLS-манифест
4. Параллельно скачивает сегменты (8 потоков)
5. Склеивает через ffmpeg в MP4
