import os
import re
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


def _norm_tokens(s: str) -> set:
    return set(re.findall(r'\w+', s.lower()))


def _pick_source(sources: list, dub_hint: str | None) -> dict:
    """Select the hlsSource matching the requested dubbing.

    Alloha returns several audio tracks (requested dub, other dubs, original) in
    one bnsi response. The requested translation is normally first, but match by
    label when we have a hint so we never fall back to the original audio.
    """
    if not sources:
        return {}
    if dub_hint:
        want = _norm_tokens(dub_hint)
        best, best_score = None, 0
        for src in sources:
            score = len(want & _norm_tokens(src.get('label', '')))
            if score > best_score:
                best, best_score = src, score
        if best is not None:
            return best
    return sources[0]


def get_video_info(iframe_url: str, pw_context=None, dub_hint: str | None = None) -> dict | None:
    """Load Alloha iframe in Playwright, intercept bnsi response, return video URLs by quality.

    Returns dict: {'qualities': {'1080': [url, ...], ...}, 'label': '...', 'skip_time': '...'}
    or None. Each quality maps to a LIST of candidate CDN URLs (vkvideo returns
    several hosts joined by ' or '); try them in order to survive 403s.
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

            src = _pick_source(data.get('hlsSource', []), dub_hint)
            qualities = {}
            for res, urls_str in src.get('quality', {}).items():
                urls = [u.strip() for u in urls_str.split(' or ') if u.strip()]
                if urls:
                    qualities[res.rstrip('p')] = urls

            result['qualities'] = qualities
            result['label'] = src.get('label', '')
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


def _ffmpeg_download(m3u8_url: str, abs_output: str) -> tuple[bool, str]:
    cmd = [
        'ffmpeg', '-y',
        '-user_agent', UA,
        '-referer', 'https://alloha.yani.tv/',
        '-headers', 'Origin: https://alloha.yani.tv\r\n',
        '-i', m3u8_url,
        '-map', '0',
        '-c', 'copy',
        '-bsf:a', 'aac_adtstoasc',
        '-avoid_negative_ts', 'make_zero',
        '-max_muxing_queue_size', '9999',
        abs_output,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    return proc.returncode == 0, (proc.stderr or '')


def download_video(m3u8_urls, output: str):
    """Download video via ffmpeg. Accepts a single URL or a list of CDN candidates.

    vkvideo returns several hosts for the same segment; the first often 403s, so
    fall through the list until one succeeds. Note: no ``-fflags +igndts`` — that
    flag makes ffmpeg regenerate DTS at 1/90000 steps and destroys intra-segment
    timing, producing choppy ("glued") playback.
    """
    if isinstance(m3u8_urls, str):
        m3u8_urls = [m3u8_urls]

    abs_output = os.path.abspath(output)
    print(f'  Downloading with ffmpeg...')

    last_err = ''
    for i, url in enumerate(m3u8_urls):
        ok, err = _ffmpeg_download(url, abs_output)
        if ok and os.path.exists(abs_output):
            size_mb = os.path.getsize(abs_output) / (1024 * 1024)
            print(f'  Done! {size_mb:.0f} MB -> {abs_output}')
            return True
        last_err = err
        if i + 1 < len(m3u8_urls):
            host = url.split('/')[2] if '://' in url else url[:40]
            print(f'  Host {host} failed, trying next CDN...')

    stderr_tail = last_err[-600:] if last_err else ''
    print(f'  ffmpeg error: {stderr_tail}')
    return False


# ---------------------------------------------------------------------------
# Fragment-level downloader.
#
# Alloha serves fMP4/CMAF HLS (init.mp4 + hundreds of .m4s fragments). ffmpeg
# and yt-dlp mis-handle the per-fragment DTS reset, so we pull every fragment
# through the Playwright session, byte-concat init + fragments and remux to a
# flat mp4. Fully resumable.
#
# CRITICAL — request fingerprint: the vkvideo CDN classifies requests by their
# browser headers. Requests WITHOUT the browser `sec-fetch-*` headers (i.e. a
# bare scripted GET) are treated as a bot and hard-throttled to ~65-80 requests
# before a multi-minute per-IP 403 ban. The real hls.js player sends the full
# fetch fingerprint and is never throttled (it bursts 18 segments in 6 s fine).
# So we replicate the player's EXACT headers — sec-fetch-*, `accept: */*`,
# `accept-encoding: ...zstd`, and the FULL iframe URL as Referer — and then the
# CDN lets us download at full speed. This was the root cause of all the 403s.
# ---------------------------------------------------------------------------

def _player_headers(referer: str, extra: dict | None = None) -> dict:
    """Exact header set the real hls.js player sends to the vkvideo CDN.

    `referer` must be the full Alloha iframe URL. `extra` carries the per-movie
    auth tokens captured from the live player (``authorizations``,
    ``accepts-controls``) plus client hints — WITHOUT these the CDN throttles us
    to ~80 requests then a per-IP 403 ban; WITH them the download is unlimited.
    """
    h = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'origin': 'https://alloha.yani.tv',
        'referer': referer,
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'cross-site',
    }
    if extra:
        h.update(extra)
    return h


def _capture_bnsi(pw_context, iframe_url: str, timeout: int = 60000, log=print):
    """Load the real player (HEADED — see note) and return (bnsi_data, auth_headers).

    IMPORTANT: run the browser context HEADED. The CDN only honours our requests
    once the player has ACTUALLY reached playback — i.e. it played through the
    preroll ad and fetched a real segment with HTTP 200, establishing a valid
    session. In headless the player often fails at the ad and never establishes
    the session, so the sniffed auth tokens 403 anyway. So here we wait until the
    player itself fetches a segment successfully before returning.
    """
    import json
    auth = {}
    played = {'ok': 0}
    _AUTH_KEYS = ('authorizations', 'accepts-controls', 'accept-language',
                  'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform')

    def _on_req(r):
        if 'vkvideo.cloud' in r.url and 'authorizations' not in auth:
            try:
                h = r.all_headers()
            except Exception:
                return
            if 'authorizations' in h:
                for k in _AUTH_KEYS:
                    if h.get(k):
                        auth[k] = h[k]

    def _on_resp(r):
        u = r.url
        if 'vkvideo.cloud' in u and ('.m4s' in u or 'seg-' in u) and r.status == 200:
            played['ok'] += 1

    pw_context.on('request', _on_req)
    pw_context.on('response', _on_resp)
    page = pw_context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
    try:
        with page.expect_response(lambda r: 'bnsi' in r.url, timeout=timeout) as resp_info:
            page.goto(f'http://127.0.0.1:{_wrapper_port}/{iframe_url}', wait_until='load', timeout=30000)
            try:
                page.wait_for_timeout(1200)
                page.mouse.click(640, 400)
            except Exception:
                pass
        resp = resp_info.value
        if resp.status != 200:
            return None, auth
        # Wait (up to ~50 s, covers the preroll ad) until the player really plays:
        # it has both sent the auth token AND pulled a segment with 200.
        for _ in range(100):
            if auth.get('authorizations') and played['ok'] >= 1:
                break
            page.wait_for_timeout(500)
        if played['ok'] == 0:
            log('  (player never reached playback — run HEADED; content may not download)')
        return json.loads(resp.body().decode('utf-8')), auth
    except Exception as e:
        print(f'  bnsi capture error: {e}')
        return None, auth
    finally:
        for ev, cb in (('request', _on_req), ('response', _on_resp)):
            try:
                pw_context.remove_listener(ev, cb)
            except Exception:
                pass
        page.close()


def _http_get(pw_context, url: str, referer: str = 'https://alloha.yani.tv/',
              extra: dict | None = None, timeout: int = 30000):
    return pw_context.request.get(url, headers=_player_headers(referer, extra), timeout=timeout)


def _resolve_playlist(pw_context, iframe_url: str, quality: str | None, dub_hint: str | None):
    """Fetch a fresh signed token; return (init_url, [seg_urls], chosen_quality, label, auth).

    `auth` is the per-movie header set sniffed from the live player (authorizations
    / accepts-controls / client hints) — pass it to every CDN request for unlimited
    download. Returns None on failure. Segment order/count are stable across calls,
    so a refreshed token lets us resume the same fragment list from a new signature.
    """
    data, auth = _capture_bnsi(pw_context, iframe_url)
    if not data:
        return None

    src = _pick_source(data.get('hlsSource', []), dub_hint)
    qmap = {}
    for res, urls_str in src.get('quality', {}).items():
        urls = [u.strip() for u in urls_str.split(' or ') if u.strip()]
        if urls:
            qmap[res.rstrip('p')] = urls
    if not qmap:
        return None

    chosen = quality if (quality and quality in qmap) else max(qmap, key=int)

    for master_url in qmap[chosen]:
        try:
            r = _http_get(pw_context, master_url, referer=iframe_url, extra=auth)
        except Exception:
            continue
        if r.status != 200:
            continue
        base = master_url.rsplit('/', 1)[0]
        media_rel = next((l.strip() for l in r.text().splitlines()
                          if l.strip() and not l.startswith('#')), None)
        if not media_rel:
            continue
        media_url = media_rel if media_rel.startswith('http') else base + '/' + media_rel
        try:
            rm = _http_get(pw_context, media_url, referer=iframe_url, extra=auth)
        except Exception:
            continue
        if rm.status != 200:
            continue
        mbase = media_url.rsplit('/', 1)[0]
        init_url, segs = None, []
        for line in rm.text().splitlines():
            line = line.strip()
            if line.startswith('#EXT-X-MAP'):
                m = re.search(r'URI="([^"]+)"', line)
                if m:
                    u = m.group(1)
                    init_url = u if u.startswith('http') else mbase + '/' + u
            elif line and not line.startswith('#'):
                segs.append(line if line.startswith('http') else mbase + '/' + line)
        if init_url and segs:
            return init_url, segs, chosen, src.get('label', ''), auth
    return None


def _assemble(init_path: str, parts_dir: str, n: int, abs_out: str) -> bool:
    """Assemble the final mp4 = raw byte-concat of init + fragments IN ORDER.

    IMPORTANT: do NOT run this through ffmpeg. These CMAF fragments each carry an
    absolute `tfdt`, so a plain byte-concat (init.mp4 + seg_0000.m4s + seg_0001.m4s
    + ...) is already a valid fragmented mp4 whose timestamps ffprobe/players read
    correctly. Any `ffmpeg -c copy` remux (to mp4/mkv, with or without -copyts)
    COLLAPSES every fragment's per-segment timestamps onto one PTS — the video then
    freezes ~1 s into each 6 s segment and jumps ahead while audio keeps playing.
    Re-muxing / raw-h264 re-timing was the cause of the "choppy" output; the fix is
    to just concatenate the bytes and ship that.
    """
    try:
        with open(abs_out, 'wb') as out:
            with open(init_path, 'rb') as f:
                out.write(f.read())
            for j in range(n):
                with open(os.path.join(parts_dir, f'seg_{j:04d}.m4s'), 'rb') as f:
                    out.write(f.read())
    except Exception:
        return False
    return os.path.exists(abs_out) and os.path.getsize(abs_out) > 1024 * 1024


def download_episode(iframe_url: str, output: str, pw_context, quality: str | None = None,
                     dub_hint: str | None = None, pace: float = 0.3, cooldown: int = 120,
                     max_stuck: int = 8, log=print) -> bool:
    """Download a full Alloha fMP4 episode fragment-by-fragment. Resumable.

    With the full real-player header set INCLUDING the per-movie auth tokens
    (authorizations / accepts-controls, sniffed live in _capture_bnsi) the vkvideo
    CDN does not throttle us at all — verified 238/238 fragments back-to-back with
    zero 403. So we download at full speed; the cooldown/refresh path is only a
    safety net for token expiry.

    pace      seconds to wait between successful fragments (small = fast)
    cooldown  seconds of silence after an unexpected 403 before refreshing token
    max_stuck consecutive cooldowns without progress before giving up (resumable)
    """
    abs_out = os.path.abspath(output)
    parts_dir = abs_out + '.parts'
    os.makedirs(parts_dir, exist_ok=True)
    init_path = os.path.join(parts_dir, 'init.mp4')

    def resolve_with_retry(stuck_start=0):
        """Resolve the playlist, cooling down + retrying while the CDN throttles us."""
        stuck = stuck_start
        while True:
            pl = _resolve_playlist(pw_context, iframe_url, quality, dub_hint)
            if pl:
                return pl
            stuck += 1
            if stuck > max_stuck:
                return None
            wait = cooldown * min(stuck, 3)
            log(f'  Playlist unavailable (throttled). Cooldown {wait}s (#{stuck})...')
            time.sleep(wait)

    pl = resolve_with_retry()
    if not pl:
        log('  ERROR: could not resolve playlist (still throttled after retries; re-run to resume)')
        return False
    init_url, segs, chosen, label, auth = pl
    n = len(segs)
    tok = 'auth OK' if auth.get('authorizations') else 'NO auth token (may throttle)'
    log(f'  Quality {chosen}p | Track: {label} | {n} fragments (~{n * 6 // 60} min) | {tok}')

    def fetch_to(url, path) -> int:
        try:
            r = _http_get(pw_context, url, referer=iframe_url, extra=auth)
        except Exception:
            return 0
        if r.status == 200:
            body = r.body()
            if body:
                with open(path, 'wb') as f:
                    f.write(body)
                return 200
            return 0
        return r.status

    def have(path):
        return os.path.exists(path) and os.path.getsize(path) > 0

    if not have(init_path):
        fetch_to(init_url, init_path)

    i, stuck = 0, 0
    already = sum(1 for j in range(n) if have(os.path.join(parts_dir, f'seg_{j:04d}.m4s')))
    if already:
        log(f'  Resuming: {already}/{n} fragments already cached')

    while i < n:
        part = os.path.join(parts_dir, f'seg_{i:04d}.m4s')
        if have(part):
            i += 1
            continue
        if not have(init_path):
            fetch_to(init_url, init_path)
        st = fetch_to(segs[i], part)
        if st == 200:
            i += 1
            stuck = 0
            if i % 20 == 0 or i == n:
                log(f'  {i}/{n} fragments')
            time.sleep(pace)
            continue

        stuck += 1
        if stuck > max_stuck:
            log(f'  Giving up at {i}/{n} after {stuck} cooldowns. Re-run to resume.')
            return False
        wait = cooldown * min(stuck, 3)
        log(f'  Fragment {i + 1}/{n}: HTTP {st} (throttled). Cooldown {wait}s (#{stuck}), refreshing token...')
        time.sleep(wait)
        pl = _resolve_playlist(pw_context, iframe_url, chosen, dub_hint)
        if pl and len(pl[1]) == n:
            init_url, segs, chosen, label, auth = pl

    # All fragments cached -> assemble (raw byte-concat, NO ffmpeg remux).
    log('  Assembling...')
    ok = _assemble(init_path, parts_dir, n, abs_out)
    if ok:
        size_mb = os.path.getsize(abs_out) / (1024 * 1024)
        log(f'  Done! {size_mb:.0f} MB, {n} fragments -> {abs_out}')
        try:
            for fn in os.listdir(parts_dir):
                os.remove(os.path.join(parts_dir, fn))
            os.rmdir(parts_dir)
        except Exception:
            pass
        return True
    log(f'  ffmpeg remux failed: {proc.stderr[-400:]}')
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

    from playwright.sync_api import sync_playwright
    _ensure_wrapper_server()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=False, channel='chrome',
        args=['--disable-blink-features=AutomationControlled', '--no-sandbox',
              '--autoplay-policy=no-user-gesture-required'],
    )
    ctx = browser.new_context(user_agent=UA, ignore_https_errors=True,
                              viewport={'width': 1280, 'height': 800})
    try:
        print('Downloading Alloha episode (fragment mode)...')
        download_episode(iframe_url, output, ctx, quality=quality)
    finally:
        browser.close()
        pw.stop()


if __name__ == '__main__':
    main()
