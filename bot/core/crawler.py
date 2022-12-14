"""Async Playwright-based website crawler with JS rendering, video downloading, auth support"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag, unquote

import httpx
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

MIME_MAP = {
    'text/html': 'html', 'text/css': 'css',
    'application/javascript': 'js', 'text/javascript': 'js', 'application/x-javascript': 'js',
    'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif',
    'image/svg+xml': 'svg', 'image/webp': 'webp',
    'image/x-icon': 'ico', 'image/vnd.microsoft.icon': 'ico',
    'font/woff': 'woff', 'font/woff2': 'woff2', 'font/ttf': 'ttf', 'font/otf': 'otf',
    'application/font-woff': 'woff', 'application/font-woff2': 'woff2',
    'application/vnd.ms-fontobject': 'eot',
    'audio/mpeg': 'mp3', 'audio/ogg': 'ogg',
    'video/mp4': 'mp4', 'video/webm': 'webm',
    'application/json': 'json', 'application/pdf': 'pdf',
}

IMPORTANT_PATHS = {
    '/': 10, '/index': 10, '/home': 10,
    '/about': 8, '/about-us': 8, '/contact': 7, '/contact-us': 7,
    '/products': 7, '/services': 7, '/pricing': 7, '/features': 7,
    '/blog': 6, '/news': 6, '/faq': 6, '/help': 6, '/support': 6,
    '/terms': 5, '/privacy': 5, '/login': 4, '/signup': 4, '/register': 4,
}

VIDEO_DOMAINS = {
    'youtube.com', 'www.youtube.com', 'youtu.be',
    'vimeo.com', 'www.vimeo.com',
    'tiktok.com', 'www.tiktok.com',
    'dailymotion.com', 'www.dailymotion.com',
    'facebook.com', 'www.facebook.com', 'fb.watch',
    'instagram.com', 'www.instagram.com',
    'twitter.com', 'www.twitter.com', 'x.com',
}


def _url_to_filepath(url: str, base_domain: str) -> str:
    parsed = urlparse(url)
    path = unquote(parsed.path).lstrip('/')
    if not path or path.endswith('/'):
        path = path + 'index.html'
    elif '.' not in path.split('/')[-1]:
        path = path + '/index.html'
    path = re.sub(r'[<>:"|?*]', '_', path)
    if parsed.query:
        qhash = hashlib.md5(parsed.query.encode()).hexdigest()[:8]
        name, ext = os.path.splitext(path)
        path = f"{name}_{qhash}{ext}"
    return path


def _score_link(url: str, depth: int, page_url: str) -> float:
    """Score a URL for crawl priority. Lower score = higher priority."""
    parsed = urlparse(url)
    score = depth * 10.0

    for pattern, bonus in IMPORTANT_PATHS.items():
        if parsed.path.lower().rstrip('/') == pattern or parsed.path.lower().startswith(pattern + '/'):
            score -= bonus

    if parsed.path.lower().endswith(('.css', '.js', '.png', '.jpg', '.gif', '.svg', '.woff', '.woff2', '.ico')):
        score += 20

    if not parsed.query:
        score -= 2
    else:
        score += 5

    page_parsed = urlparse(page_url)
    if parsed.netloc == page_parsed.netloc:
        score -= 5

    depth_penalty = max(0, depth - 2) * 8
    score += depth_penalty

    return max(0, score)


def _is_video_url(url: str) -> bool:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    return any(domain.endswith(d) for d in VIDEO_DOMAINS)


class CrawlProgress:
    def __init__(self):
        self.pages_discovered = 0
        self.pages_downloaded = 0
        self.assets_downloaded = 0
        self.videos_downloaded = 0
        self.video_size = 0
        self.total_size = 0
        self.errors = []
        self.current_url = ''
        self.status = 'starting'
        self.cancelled = False

    def to_text(self) -> str:
        err_count = len(self.errors)
        lines = [
            f"**Status** {self.status}",
            f"**Current** `{self.current_url[:50]}`" if self.current_url else "",
            f"**Pages** {self.pages_downloaded} downloaded / {self.pages_discovered} discovered",
            f"**Assets** {self.assets_downloaded} downloaded",
        ]
        if self.videos_downloaded:
            lines.append(f"**Videos** {self.videos_downloaded} downloaded ({_fmt_size(self.video_size)})")
        if self.total_size:
            lines.append(f"**Total size** {_fmt_size(self.total_size)}")
        if err_count:
            lines.append(f"**Errors** {err_count}")
        return '\n'.join(l for l in lines if l)


def _fmt_size(size_bytes: float) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size_bytes < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}TB"


class _PriorityQueue:
    """Async priority queue backed by heapq. Items are (priority, counter, item)."""

    def __init__(self):
        self._heap: list = []
        self._counter = 0
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()
        self._unfinished = 0
        self._finished = asyncio.Event()
        self._finished.set()

    async def put(self, priority: float, item):
        async with self._lock:
            import heapq
            heapq.heappush(self._heap, (priority, self._counter, item))
            self._counter += 1
            self._unfinished += 1
            self._finished.clear()
            self._not_empty.set()

    async def get(self, timeout: float = 2):
        try:
            await asyncio.wait_for(self._not_empty.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError

        async with self._lock:
            if not self._heap:
                self._not_empty.clear()
                raise asyncio.TimeoutError
            import heapq
            _, _, item = heapq.heappop(self._heap)
            if not self._heap:
                self._not_empty.clear()
            return item

    def task_done(self):
        self._unfinished -= 1
        if self._unfinished <= 0:
            self._finished.set()

    async def join(self):
        await self._finished.wait()

    def empty(self):
        return len(self._heap) == 0

    def qsize(self):
        return len(self._heap)


class WebCloner:
    """Crawls a website using Playwright, downloads assets, rewrites paths."""

    def __init__(
        self,
        start_url: str,
        output_dir: str,
        max_pages: int = 50,
        max_depth: int = 3,
        concurrency: int = 10,
        page_timeout: int = 30000,
        asset_timeout: int = 15000,
        max_time: int = 300,
        cookies_file: str = None,
        crawl_scope: str = 'same-domain',
    ):
        self.start_url = start_url
        self.output_dir = output_dir
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.concurrency = concurrency
        self.page_timeout = page_timeout
        self.asset_timeout = asset_timeout
        self.max_time = max_time
        self.cookies_file = cookies_file
        self.crawl_scope = crawl_scope
        self._start_time = time.time()

        parsed = urlparse(start_url)
        self.scheme = parsed.scheme
        self.base_domain = parsed.netloc
        self.base_origin = f"{self.scheme}://{self.base_domain}"

        self.progress = CrawlProgress()
        self.visited: set[str] = set()
        self.queue = _PriorityQueue()
        self.asset_local_map: dict[str, str] = {}
        self._html_paths: dict[str, str] = {}
        self._discovered_videos: list[str] = []
        self._semaphore = asyncio.Semaphore(concurrency)
        self._asset_semaphore = asyncio.Semaphore(concurrency * 2)
        self._cancel_event = asyncio.Event()

    def cancel(self):
        self._cancel_event.set()
        self.progress.cancelled = True
        self.progress.status = 'cancelling'

    async def run(self) -> str:
        os.makedirs(self.output_dir, exist_ok=True)
        self.progress.status = 'launching browser'

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-extensions',
                ],
            )
            context = await browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                viewport={'width': 1920, 'height': 1080},
                ignore_https_errors=True,
            )

            if self.cookies_file and os.path.exists(self.cookies_file):
                try:
                    with open(self.cookies_file, 'r') as f:
                        cookies = json.load(f)
                    await context.add_cookies(cookies)
                    self.progress.status = 'cookies loaded'
                    logger.info(f"Loaded {len(cookies)} cookies from {self.cookies_file}")
                except Exception as e:
                    logger.error(f"Failed to load cookies: {e}")

            page = await context.new_page()

            await self.queue.put(0, (self.start_url, 0))

            workers = [asyncio.create_task(self._worker(context))
                       for _ in range(min(self.concurrency, self.max_pages))]

            try:
                await asyncio.wait_for(self.queue.join(), timeout=self.max_time)
            except asyncio.TimeoutError:
                self._cancel_event.set()
                self.progress.status = 'time limit reached'
                self._drain_queue()

            for w in workers:
                w.cancel()

            try:
                await asyncio.wait_for(
                    asyncio.gather(*workers, return_exceptions=True),
                    timeout=10,
                )
            except asyncio.TimeoutError:
                logger.warning("Worker cleanup timed out, force-closing browser")

            try:
                await browser.close()
            except Exception:
                pass

        self.progress.status = 'downloading videos'
        await self._download_videos()

        self.progress.status = 'rewriting paths'
        self._rewrite_all_html()
        self.progress.status = 'done'

        return self.output_dir

    def _drain_queue(self):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except Exception:
                break

    def _is_same_domain(self, url: str) -> bool:
        parsed = urlparse(url)
        if self.crawl_scope == 'subdomains':
            base_parts = self.base_domain.split('.')
            url_parts = parsed.netloc.split('.')
            return url_parts[-len(base_parts):] == base_parts
        return parsed.netloc == self.base_domain

    async def _worker(self, context):
        while not self._cancel_event.is_set():
            if time.time() - self._start_time > self.max_time:
                self.progress.status = 'time limit reached'
                break
            try:
                url, depth = await self.queue.get(timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return

            try:
                async with self._semaphore:
                    if not self._cancel_event.is_set():
                        await self._crawl_page(context, url, depth)
            except Exception as e:
                logger.error(f"Error crawling {url}: {e}")
                self.progress.errors.append(f"{url}: {str(e)[:80]}")
            finally:
                self.queue.task_done()

    async def _auto_scroll(self, page):
        """Scroll page to trigger lazy-loaded content."""
        try:
            await page.evaluate('''async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 500;
                    const timer = setInterval(() => {
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if (totalHeight >= document.body.scrollHeight || totalHeight > 30000) {
                            clearInterval(timer);
                            window.scrollTo(0, 0);
                            resolve();
                        }
                    }, 100);
                    setTimeout(() => { clearInterval(timer); resolve(); }, 5000);
                });
            }''')
            await page.wait_for_timeout(500)
        except Exception:
            pass

    async def _crawl_page(self, context, url: str, depth: int):
        if url in self.visited:
            return
        if len(self.visited) >= self.max_pages:
            return
        if self._cancel_event.is_set():
            return

        self.visited.add(url)
        self.progress.pages_discovered = len(self.visited)
        self.progress.current_url = url
        self.progress.status = f'crawling page {self.progress.pages_downloaded + 1}'

        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until='domcontentloaded',
                                   timeout=self.page_timeout)
            if not resp or resp.status >= 400:
                self.progress.errors.append(f"{url}: HTTP {resp.status if resp else 'no response'}")
                return

            await page.wait_for_timeout(1500)
            await self._auto_scroll(page)
            await page.wait_for_timeout(500)

            html = await page.content()
            local_path = _url_to_filepath(url, self.base_domain)
            full_path = os.path.join(self.output_dir, local_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(html)

            self._html_paths[url] = full_path
            self.progress.pages_downloaded += 1

            links = await page.evaluate('''() => {
                const urls = new Set();
                document.querySelectorAll('a[href]').forEach(a => {
                    try { urls.add(new URL(a.href, location.href).href); } catch(e) {}
                });
                return [...urls];
            }''')

            for link in links:
                clean = urldefrag(link)[0]
                if self._is_same_domain(clean) and clean not in self.visited:
                    if len(self.visited) < self.max_pages and not self._cancel_event.is_set():
                        score = _score_link(clean, depth + 1, url)
                        await self.queue.put(score, (clean, depth + 1))

            asset_urls = await page.evaluate('''() => {
                const urls = new Set();
                document.querySelectorAll(
                    'img[src], link[href], script[src], source[src], video[src], audio[src], ' +
                    'link[rel="stylesheet"][href], link[rel="icon"][href], link[rel="shortcut icon"][href]'
                ).forEach(el => {
                    const val = el.getAttribute('src') || el.getAttribute('href');
                    if (val && !val.startsWith('data:')) {
                        try { urls.add(new URL(val, location.href).href); } catch(e) {}
                    }
                });
                document.querySelectorAll('style').forEach(s => {
                    const m = s.textContent.match(/url\\(([^)]+)\\)/g);
                    if (m) m.forEach(u => {
                        const inner = u.slice(4, -1).trim().replace(/['"]/g, '');
                        if (!inner.startsWith('data:')) {
                            try { urls.add(new URL(inner, location.href).href); } catch(e) {}
                        }
                    });
                });
                document.querySelectorAll('video source[src], video[src]').forEach(el => {
                    const val = el.getAttribute('src');
                    if (val && !val.startsWith('data:')) {
                        try { urls.add(new URL(val, location.href).href); } catch(e) {}
                    }
                });
                return [...urls];
            }''')

            video_urls = []
            regular_assets = []
            for u in asset_urls:
                if _is_video_url(u):
                    video_urls.append(u)
                else:
                    regular_assets.append(u)

            if video_urls:
                self._discovered_videos.extend(video_urls)

            await self._download_assets(regular_assets)

        except Exception as e:
            if 'Timeout' in str(type(e).__name__) or 'timeout' in str(e).lower():
                self.progress.errors.append(f"{url}: timeout")
            else:
                self.progress.errors.append(f"{url}: {str(e)[:80]}")
        finally:
            await page.close()

    async def _download_videos(self):
        """Download discovered videos using yt-dlp."""
        if not self._discovered_videos:
            return

        try:
            import yt_dlp
        except ImportError:
            logger.warning("yt-dlp not installed, skipping video downloads")
            return

        video_dir = os.path.join(self.output_dir, 'videos')
        os.makedirs(video_dir, exist_ok=True)

        for video_url in self._discovered_videos:
            if self._cancel_event.is_set():
                break
            try:
                ydl_opts = {
                    'outtmpl': os.path.join(video_dir, '%(title)s.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'socket_timeout': 30,
                    'retries': 2,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    if info:
                        filename = ydl.prepare_filename(info)
                        if os.path.exists(filename):
                            self.asset_local_map[video_url] = filename
                            size = os.path.getsize(filename)
                            self.progress.video_size += size
                            self.progress.videos_downloaded += 1
                            self.progress.total_size += size
            except Exception as e:
                logger.debug(f"Video download failed {video_url}: {e}")

    async def _download_assets(self, urls: list[str]):
        async with httpx.AsyncClient(
            timeout=self.asset_timeout,
            follow_redirects=True,
            verify=False,
            limits=httpx.Limits(max_connections=self.concurrency),
        ) as client:
            tasks = []
            for url in urls:
                if url in self.asset_local_map:
                    continue
                if self._cancel_event.is_set():
                    break
                tasks.append(self._fetch_asset(client, url))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_asset(self, client: httpx.AsyncClient, url: str):
        if url in self.asset_local_map:
            return
        async with self._asset_semaphore:
            try:
                resp = await client.get(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': '*/*',
                    'Referer': self.base_origin + '/',
                })
                if resp.status_code != 200:
                    return

                content_type = resp.headers.get('content-type', '').split(';')[0].strip().lower()
                ext = MIME_MAP.get(content_type)
                if not ext:
                    path_ext = os.path.splitext(urlparse(url).path)[1].lower().lstrip('.')
                    if path_ext:
                        ext = path_ext
                    else:
                        return

                local_path = _url_to_filepath(url, self.base_domain)
                if not any(local_path.endswith(e) for e in [f'.{ext}']):
                    base, _ = os.path.splitext(local_path)
                    local_path = base + '.' + ext

                full_path = os.path.join(self.output_dir, local_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)

                with open(full_path, 'wb') as f:
                    f.write(resp.content)

                self.asset_local_map[url] = full_path
                self.progress.assets_downloaded += 1
                self.progress.total_size += len(resp.content)

            except Exception as e:
                logger.debug(f"Asset download failed {url}: {e}")

    def _rewrite_all_html(self):
        for url, html_path in self._html_paths.items():
            if not os.path.exists(html_path):
                continue
            try:
                with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
                    html = f.read()

                html_dir = os.path.dirname(html_path)

                for abs_url, local_file in self.asset_local_map.items():
                    rel = os.path.relpath(local_file, html_dir)
                    for pattern in [
                        f'"{abs_url}"',
                        f"'{abs_url}'",
                        f'url({abs_url})',
                        f'url("{abs_url}")',
                        f"url('{abs_url}')",
                    ]:
                        replacement = pattern.replace(abs_url, rel)
                        html = html.replace(pattern, replacement)

                html = re.sub(
                    r'(src|href|action)=["\']https?://[^"\']*' + re.escape(self.base_domain) + r'[^"\']*["\']',
                    lambda m: m.group(0),
                    html
                )

                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html)
            except Exception as e:
                logger.error(f"Error rewriting {html_path}: {e}")
