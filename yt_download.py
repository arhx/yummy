import subprocess
import sys
import os
import re


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip('. ')


def run_ytdlp(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-m', 'yt_dlp', *args], capture_output=True, text=True, encoding='utf-8')


def get_formats_info(url: str) -> str:
    r = run_ytdlp('-F', '--no-playlist', url)
    return r.stdout


def list_playlist(url: str) -> list[dict]:
    r = run_ytdlp(
        '--flat-playlist', '--print',
        '%(playlist_index)s\t%(id)s\t%(title)s\t%(duration_string)s',
        url,
    )
    items = []
    for line in r.stdout.strip().splitlines():
        parts = line.split('\t', 3)
        if len(parts) >= 3:
            items.append({
                'index': parts[0],
                'id': parts[1],
                'title': parts[2],
                'duration': parts[3] if len(parts) > 3 else '?',
            })
    return items


def get_playlist_title(url: str) -> str:
    r = run_ytdlp('--flat-playlist', '--print', '%(playlist_title)s', '--playlist-items', '1', url)
    title = r.stdout.strip().splitlines()
    return title[0] if title else 'YouTube Playlist'


def download_single(url: str, output: str, quality: str = 'best'):
    fmt = build_format(quality)
    cmd = [
        'yt-dlp',
        '-f', fmt,
        '--merge-output-format', 'mp4',
        '--no-playlist',
        '-o', output,
        url,
    ]
    print(f'  Downloading...')
    subprocess.run(cmd)
    if os.path.exists(output):
        size_mb = os.path.getsize(output) / (1024 * 1024)
        print(f'  Done! {size_mb:.0f} MB -> {os.path.abspath(output)}')


def download_playlist(url: str, quality: str = 'best'):
    print('Fetching playlist info...')
    playlist_title = get_playlist_title(url)
    items = list_playlist(url)

    if not items:
        print('ERROR: Empty playlist or failed to fetch')
        sys.exit(1)

    safe_title = sanitize_filename(playlist_title)
    out_dir = os.path.abspath(safe_title)
    os.makedirs(out_dir, exist_ok=True)

    print(f'Playlist: {playlist_title}')
    print(f'Videos: {len(items)}')
    print(f'Output: {out_dir}')
    print()

    for item in items:
        idx = item['index'].zfill(2)
        safe_name = sanitize_filename(item['title'])
        output = os.path.join(out_dir, f'{idx}. {safe_name}.mp4')

        if os.path.exists(output):
            size_mb = os.path.getsize(output) / (1024 * 1024)
            if size_mb > 1:
                print(f'[{idx}] Already exists ({size_mb:.0f} MB), skipping')
                continue

        print(f'\n[{idx}] {item["title"]} [{item["duration"]}]')
        video_url = f'https://www.youtube.com/watch?v={item["id"]}'
        download_single(video_url, output, quality)

    print(f'\nAll done! -> {out_dir}')


def build_format(quality: str) -> str:
    q = quality.rstrip('p')
    if q == 'best':
        return 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    if q in ('2160', '4k', '4K'):
        h = 2160
    elif q in ('1440', '2k', '2K'):
        h = 1440
    elif q in ('1080', 'fhd', 'FHD'):
        h = 1080
    elif q in ('720', 'hd', 'HD'):
        h = 720
    elif q in ('480', '360'):
        h = int(q)
    else:
        return 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best'
    return f'bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={h}]+bestaudio/best[height<={h}]/best'


def main():
    if len(sys.argv) < 2:
        print('Usage: python yt_download.py <url> [quality]')
        print()
        print('  url      - YouTube video or playlist URL')
        print('  quality  - best, 4k, 1080, 720, 480, 360 (default: best)')
        print()
        print('Single video:')
        print('  python yt_download.py "https://www.youtube.com/watch?v=xxx" 1080')
        print()
        print('Playlist:')
        print('  python yt_download.py "https://www.youtube.com/watch?v=xxx&list=PLxxx" 720')
        print()
        print('Already downloaded videos are skipped.')
        sys.exit(0)

    url = sys.argv[1]
    quality = sys.argv[2] if len(sys.argv) > 2 else 'best'

    is_playlist = 'list=' in url

    if is_playlist:
        download_playlist(url, quality)
    else:
        safe_name = 'video.mp4'
        r = run_ytdlp('--print', '%(title)s', '--no-playlist', url)
        if r.stdout.strip():
            safe_name = sanitize_filename(r.stdout.strip()) + '.mp4'
        output = os.path.abspath(safe_name)
        print(f'Video: {r.stdout.strip()}')
        download_single(url, output, quality)


if __name__ == '__main__':
    main()
