import re
import sys
import json
import ssl
import urllib.request
import urllib.parse
import os
from pathlib import Path

import kodik_download
import alloha_download

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'uk,en-US;q=0.9,en;q=0.8,ru;q=0.7',
}

SITE = 'https://ru.yummyani.me'


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        return resp.read().decode('utf-8')


def fetch_json(url: str):
    headers = {**HEADERS, 'Accept': 'application/json'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        return json.loads(resp.read().decode('utf-8'))


def extract_slug(url: str) -> str:
    url = url.rstrip('/')
    return url.rsplit('/', 1)[-1]


def get_anime_id(slug: str) -> tuple[int, str]:
    url = f'{SITE}/catalog/item/{slug}'
    html = fetch_text(url)

    m = re.search(r'anime_id[^0-9]{0,10}(\d+)', html)
    if not m:
        print(f'ERROR: Could not find anime_id on page {url}')
        sys.exit(1)
    anime_id = int(m.group(1))

    title = slug
    name_m = re.search(r'"@type"\s*:\s*"TVSeries"[^}]*"name"\s*:\s*"([^"]+)"', html)
    if not name_m:
        name_m = re.search(r'<title>([^<|]+)', html)
    if name_m:
        title = name_m.group(1).strip()

    return anime_id, title


def detect_player(iframe_url: str) -> str:
    if 'kodikplayer.com' in iframe_url or 'kodik.info' in iframe_url:
        return 'kodik'
    if 'alloha' in iframe_url:
        return 'alloha'
    return 'unknown'


def get_episodes(anime_id: int) -> dict[str, list[dict]]:
    """Fetch videos API and group episodes by player+dubbing."""
    url = f'{SITE}/api/anime/{anime_id}/videos'
    data = fetch_json(url)

    groups = {}
    for video in data.get('response', []):
        iframe_url = video.get('iframe_url', '')
        player = detect_player(iframe_url)
        if player == 'unknown':
            continue

        dubbing = video.get('data', {}).get('dubbing', 'Unknown')
        ep_num = video.get('number', '?')
        key = f'[{player.upper()}] {dubbing}'

        if key not in groups:
            groups[key] = {'player': player, 'dubbing': dubbing, 'episodes': []}

        if not iframe_url.startswith('http'):
            iframe_url = 'https:' + iframe_url

        groups[key]['episodes'].append({
            'episode': ep_num,
            'url': iframe_url,
        })

    for key in groups:
        groups[key]['episodes'].sort(key=lambda e: int(e['episode']) if str(e['episode']).isdigit() else 0)

    return groups


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '_', name)


def download_kodik_episode(episode_url: str, output: str, quality: str = None):
    links = kodik_download.get_video_links(episode_url)
    if not links:
        print(f'  No links found')
        return

    if quality and quality in links:
        chosen = quality
    else:
        chosen = max(links.keys(), key=int)

    url = links[chosen]
    print(f'  Quality: {chosen}p')
    kodik_download.download_hls(url, output)


def download_alloha_episode(episode_url: str, output: str, quality: str = None, pw_context=None, dub: str = None):
    # Alloha is fMP4/CMAF behind an aggressively rate-limited CDN; download the
    # whole episode fragment-by-fragment through the browser session (resumable).
    alloha_download.download_episode(
        episode_url, output, pw_context, quality=quality, dub_hint=dub,
    )


def main():
    if len(sys.argv) < 2:
        print('Usage: python yummy_download.py <yummyanime_url> [quality]')
        print()
        print('  yummyanime_url - e.g. https://ru.yummyani.me/catalog/item/mushoku-tensei-iii-...')
        print('  quality        - 360, 480, 720 (default: best) [Kodik only]')
        print()
        print('Supports: Kodik, Alloha players')
        print('The script will show available dubs and let you choose.')
        sys.exit(0)

    page_url = sys.argv[1]
    quality = sys.argv[2] if len(sys.argv) > 2 else None

    slug = extract_slug(page_url)
    print(f'Fetching anime info for: {slug}')
    anime_id, title = get_anime_id(slug)
    print(f'  Title: {title}')
    print(f'  ID: {anime_id}')

    print(f'\nFetching episodes...')
    groups = get_episodes(anime_id)

    if not groups:
        print('ERROR: No episodes found (Kodik or Alloha)')
        sys.exit(1)

    keys = sorted(groups.keys())
    print(f'\nAvailable dubs ({len(keys)}):')
    for i, key in enumerate(keys, 1):
        g = groups[key]
        eps = g['episodes']
        ep_nums = ', '.join(str(e['episode']) for e in eps)
        print(f'  {i}. {key} ({len(eps)} ep: {ep_nums})')

    print()
    choice = input('Choose dub number (or "all" for all dubs): ').strip()

    if choice.lower() == 'all':
        selected_keys = keys
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(keys):
                selected_keys = [keys[idx]]
            else:
                print('Invalid choice')
                sys.exit(1)
        except ValueError:
            print('Invalid input')
            sys.exit(1)

    safe_title = sanitize_filename(title)

    # For Alloha, create shared browser context for efficiency
    pw_context = None
    pw = None
    browser = None
    has_alloha = any(groups[k]['player'] == 'alloha' for k in selected_keys)

    if has_alloha:
        from playwright.sync_api import sync_playwright
        alloha_download._ensure_wrapper_server()
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=False,
            channel='chrome',
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--autoplay-policy=no-user-gesture-required',
            ],
        )
        pw_context = browser.new_context(
            user_agent=HEADERS['User-Agent'],
            ignore_https_errors=True,
            viewport={'width': 1280, 'height': 800},
        )

    try:
        for key in selected_keys:
            g = groups[key]
            episodes = g['episodes']
            player = g['player']
            dubbing = g['dubbing']
            safe_dub = sanitize_filename(dubbing)
            out_dir = os.path.abspath(os.path.join(safe_title, safe_dub))
            os.makedirs(out_dir, exist_ok=True)

            print(f'\n{"="*60}')
            print(f'Downloading: {key} ({len(episodes)} episodes)')
            print(f'Output dir: {out_dir}')
            print(f'{"="*60}')

            for ep in episodes:
                ep_num = str(ep['episode'])
                output = os.path.join(out_dir, f'episode_{ep_num.zfill(2)}.mp4')

                if os.path.exists(output):
                    size_mb = os.path.getsize(output) / (1024 * 1024)
                    if size_mb > 10:
                        print(f'\n  Episode {ep_num} already exists ({size_mb:.0f} MB), skipping')
                        continue

                print(f'\n--- Episode {ep_num} [{player.upper()}] ---')
                try:
                    if player == 'kodik':
                        download_kodik_episode(ep['url'], output, quality)
                    elif player == 'alloha':
                        download_alloha_episode(ep['url'], output, quality=quality, pw_context=pw_context, dub=dubbing)
                except Exception as e:
                    print(f'  ERROR downloading episode {ep_num}: {e}')
                    continue
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()

    print(f'\n\nAll done!')


if __name__ == '__main__':
    main()
