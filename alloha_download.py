import os
import sys
import subprocess
import time
import http.server
import threading

_wrapper_server = None
_wrapper_port = 8099

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'


def _ensure_wrapper_server():
    global _wrapper_server
    if _wrapper_server:
        return

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            iframe_url = self.path.lstrip('/')
            if not iframe_url.startswith('http'):
                iframe_url = 'https://' + iframe_url
            html = f'''<!DOCTYPE html>
<html><head><title>Player</title></head>
<body style="margin:0;padding:0">
<iframe src="{iframe_url}" style="width:100%;height:100vh;border:none"
  allow="autoplay;encrypted-media" allowfullscreen></iframe>
</body></html>'''
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(html.encode())

        def log_message(self, *a):
            pass

    _wrapper_server = http.server.HTTPServer(('127.0.0.1', _wrapper_port), Handler)
    threading.Thread(target=_wrapper_server.serve_forever, daemon=True).start()


def get_video_info(iframe_url: str, pw_context=None) -> dict | None:
    """Load Alloha iframe in Playwright, intercept bnsi response, return video URLs by quality.

    Returns dict: {'qualities': {'1080': url, '720': url, ...}, 'skip_time': '...'} or None.
    """
    import json
    from playwright.sync_api import sync_playwright

    _ensure_wrapper_server()

    result = {}
    close_browser = pw_context is None

    if pw_context is None:
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
            user_agent=UA,
            ignore_https_errors=True,
            viewport={'width': 1280, 'height': 800},
        )
    else:
        pw = None
        browser = None

    page = pw_context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")

    with page.expect_response(lambda r: 'bnsi' in r.url, timeout=60000) as resp_info:
        wrapper_url = f'http://127.0.0.1:{_wrapper_port}/{iframe_url}'
        page.goto(wrapper_url, wait_until='load', timeout=30000)
        # Alloha player waits for user interaction to fetch manifests — simulate a click.
        try:
            page.wait_for_timeout(1500)
            page.mouse.click(640, 400)
        except Exception:
            pass

    bnsi_resp = resp_info.value
    if bnsi_resp.status == 200:
        try:
            body = bnsi_resp.body()
            data = json.loads(body.decode('utf-8'))

            qualities = {}
            for src in data.get('hlsSource', []):
                for res, urls_str in src.get('quality', {}).items():
                    url = urls_str.split(' or ')[0]
                    qualities[res.rstrip('p')] = url

            result['qualities'] = qualities
            result['skip_time'] = data.get('skipTime', '')
        except Exception as e:
            print(f'  Error parsing bnsi response: {e}')

    page.close()

    if close_browser:
        if browser:
            browser.close()
        if pw:
            pw.stop()

    return result if result.get('qualities') else None


def download_video(m3u8_url: str, output: str):
    """Download video from m3u8 URL using ffmpeg."""
    abs_output = os.path.abspath(output)
    cmd = [
        'ffmpeg', '-y',
        '-user_agent', UA,
        '-referer', 'https://alloha.yani.tv/',
        '-headers', 'Origin: https://alloha.yani.tv\r\n',
        '-fflags', '+igndts',
        '-i', m3u8_url,
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        '-max_muxing_queue_size', '9999',
        abs_output,
    ]

    print(f'  Downloading with ffmpeg...')
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    if proc.returncode != 0:
        stderr_tail = proc.stderr[-600:] if proc.stderr else ''
        print(f'  ffmpeg error: {stderr_tail}')
        return False

    if os.path.exists(abs_output):
        size_mb = os.path.getsize(abs_output) / (1024 * 1024)
        print(f'  Done! {size_mb:.0f} MB -> {abs_output}')
        return True

    print(f'  ERROR: output file not created')
    return False


def main():
    if len(sys.argv) < 2:
        print('Usage: python alloha_download.py <alloha_iframe_url> [quality] [output]')
        print()
        print('  alloha_iframe_url - e.g. https://alloha.yani.tv/?token_movie=...')
        print('  quality           - 1080, 720, 480, 360 (default: best)')
        print('  output            - output filename (default: video.mp4)')
        sys.exit(0)

    iframe_url = sys.argv[1]
    quality = sys.argv[2] if len(sys.argv) > 2 else None
    output = sys.argv[3] if len(sys.argv) > 3 else 'video.mp4'

    if not output.endswith('.mp4'):
        output += '.mp4'

    print('[1/2] Getting video URLs via browser...')
    info = get_video_info(iframe_url)
    if not info:
        print('ERROR: Could not get video URLs')
        sys.exit(1)

    qualities = info['qualities']
    print(f'  Available: {", ".join(sorted(qualities.keys(), key=int))}p')

    if quality and quality in qualities:
        chosen = quality
    else:
        chosen = max(qualities.keys(), key=int)
        if quality:
            print(f'  {quality}p not available, using {chosen}p')

    print(f'  Selected: {chosen}p')
    print('[2/2] Downloading...')
    download_video(qualities[chosen], output)


if __name__ == '__main__':
    main()
