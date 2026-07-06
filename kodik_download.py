import re
import sys
import json
import ssl
import base64
import subprocess
import urllib.request
import urllib.parse
import os
import tempfile
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def rot18_decode(encoded: str) -> str:
    chars = []
    for c in encoded:
        if c.isalpha():
            code = ord(c) + 18
            limit = 90 if c <= 'Z' else 122
            if code > limit:
                code -= 26
            chars.append(chr(code))
        else:
            chars.append(c)
    rotated = ''.join(chars)
    return base64.b64decode(rotated).decode('utf-8')


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36',
    'Referer': 'https://kodikplayer.com/',
    'Origin': 'https://kodikplayer.com',
    'Accept': '*/*',
}


def fetch(url: str, extra_headers: dict = None, method: str = 'GET', data: dict = None) -> bytes:
    headers = {**HEADERS}
    if extra_headers:
        headers.update(extra_headers)

    body = urllib.parse.urlencode(data).encode() if data else None
    if method == 'POST':
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        headers['X-Requested-With'] = 'XMLHttpRequest'

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as resp:
        return resp.read()


def parse_player_page(html: str) -> dict:
    info = {}
    for var in ['domain', 'd_sign', 'pd', 'pd_sign', 'ref', 'ref_sign']:
        m = re.search(rf'var\s+{var}\s*=\s*"([^"]+)"', html)
        if m:
            info[var] = m.group(1)

    m = re.search(r"vInfo\.type\s*=\s*'([^']+)'", html)
    if m:
        info['type'] = m.group(1)
    m = re.search(r"vInfo\.hash\s*=\s*'([^']+)'", html)
    if m:
        info['hash'] = m.group(1)
    m = re.search(r"vInfo\.id\s*=\s*'([^']+)'", html)
    if m:
        info['id'] = m.group(1)

    return info


def get_video_links(player_url: str) -> dict:
    print(f'[1/3] Fetching player page...')
    html = fetch(player_url).decode('utf-8')
    info = parse_player_page(html)

    required = ['domain', 'd_sign', 'pd', 'pd_sign', 'ref', 'ref_sign', 'type', 'hash', 'id']
    missing = [k for k in required if k not in info]
    if missing:
        print(f'ERROR: Missing variables: {missing}')
        sys.exit(1)

    post_data = {
        'd': info['domain'],
        'd_sign': info['d_sign'],
        'pd': info['pd'],
        'pd_sign': info['pd_sign'],
        'ref': info['ref'],
        'ref_sign': info['ref_sign'],
        'bad_user': 'false',
        'cdn_is_working': 'true',
        'type': info['type'],
        'hash': info['hash'],
        'id': info['id'],
    }

    print('[2/3] Requesting video links...')
    resp_text = fetch(
        'https://kodikplayer.com/ftor',
        extra_headers={'Referer': player_url},
        method='POST',
        data=post_data,
    ).decode('utf-8')
    resp = json.loads(resp_text)

    links = {}
    if 'links' in resp and isinstance(resp['links'], dict):
        for quality, sources in resp['links'].items():
            for source in sources:
                src = source.get('src', '')
                if '//' not in src:
                    src = rot18_decode(src)
                links[quality] = src
                break

    return links


def download_segment(args):
    url, path, idx, total = args
    for attempt in range(3):
        try:
            data = fetch(url)
            with open(path, 'wb') as f:
                f.write(data)
            return idx, True
        except Exception as e:
            if attempt == 2:
                print(f'\n  FAIL seg {idx}/{total}: {e}')
                return idx, False
    return idx, False


def download_hls(manifest_url: str, output: str, workers: int = 8):
    print('[3/3] Downloading video...')
    manifest = fetch(manifest_url).decode('utf-8')
    base_url = manifest_url.rsplit('/', 1)[0] + '/'

    segments = []
    for line in manifest.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            if line.startswith('./'):
                line = line[2:]
            seg_url = base_url + line
            segments.append(seg_url)

    total = len(segments)
    print(f'  {total} segments to download')

    tmp_dir = tempfile.mkdtemp(prefix='kodik_')
    try:
        tasks = []
        for i, url in enumerate(segments):
            seg_path = os.path.join(tmp_dir, f'seg_{i:05d}.ts')
            tasks.append((url, seg_path, i + 1, total))

        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(download_segment, t): t for t in tasks}
            for future in as_completed(futures):
                idx, ok = future.result()
                done += 1
                pct = done * 100 // total
                print(f'\r  Progress: {done}/{total} ({pct}%)', end='', flush=True)

        print()

        seg_list = os.path.join(tmp_dir, 'concat.txt')
        with open(seg_list, 'w') as f:
            for i in range(len(segments)):
                seg_path = os.path.join(tmp_dir, f'seg_{i:05d}.ts')
                f.write(f"file '{seg_path}'\n")

        abs_output = os.path.abspath(output)
        print(f'  Merging...')
        subprocess.run([
            'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
            '-i', seg_list, '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            abs_output,
        ], capture_output=True)
        size_mb = os.path.getsize(abs_output) / (1024 * 1024)
        print(f'  Done! {size_mb:.0f} MB -> {abs_output}')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main():
    if len(sys.argv) < 2:
        print('Usage: python kodik_download.py <kodik_url> [quality] [output]')
        print()
        print('  kodik_url  - e.g. https://kodikplayer.com/seria/123/hash/720p')
        print('  quality    - 360, 480, 720 (default: best available)')
        print('  output     - output filename (default: video.mp4)')
        print()
        print('Examples:')
        print('  python kodik_download.py "https://kodikplayer.com/seria/1638990/.../720p"')
        print('  python kodik_download.py "https://kodikplayer.com/seria/1638990/.../720p" 720 ep1.mp4')
        sys.exit(0)

    player_url = sys.argv[1]
    quality = sys.argv[2] if len(sys.argv) > 2 else None
    output = sys.argv[3] if len(sys.argv) > 3 else 'video.mp4'

    links = get_video_links(player_url)

    if not links:
        print('ERROR: No video links found')
        sys.exit(1)

    print(f'Available qualities: {", ".join(sorted(links.keys(), key=int))}p')

    if quality and quality in links:
        chosen = quality
    else:
        chosen = max(links.keys(), key=int)
        if quality:
            print(f'Quality {quality}p not available, using {chosen}p')

    url = links[chosen]
    print(f'Selected: {chosen}p')

    if not output.endswith('.mp4'):
        output += '.mp4'

    download_hls(url, output)


if __name__ == '__main__':
    main()
