"""Small public-page retriever, with no model-selected tools or browser state."""
from hashlib import sha256
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, unquote

from . import app_clock

# Permission to retrieve, not a linguistic-truth or licensing certificate.
HOSTS = frozenset({'www.coelang.tufs.ac.jp', 'www.irodori.jpf.go.jp', 'www.jpf.go.jp',
    'www.marugoto.org', 'www.jlpt.jp', 'www.ninjal.ac.jp', 'www.gavo.t.u-tokyo.ac.jp'})
EXTRACTOR = 'html-text-v1'
MAX_RAW = 1024 * 1024
MAX_TEXT = 65536
MAX_SECONDS = 20
_DNS = ThreadPoolExecutor(max_workers=1, thread_name_prefix='kana_public_dns')
_DNS_LOCK = threading.Lock()
_DNS_JOB = None


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0: raise ValueError('资料获取已超时，未保存不完整正文。')
    return min(10, seconds)


def resolve(host, deadline):
    global _DNS_JOB
    with _DNS_LOCK:
        if _DNS_JOB is not None and not _DNS_JOB.done(): raise ValueError('前次地址解析尚未结束，请稍后再试。')
        _DNS_JOB = _DNS.submit(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
        job = _DNS_JOB
    try: return job.result(timeout=min(5, remaining(deadline)))
    except FutureTimeout:
        # OS DNS may finish later, but the sole resolver worker never performs
        # HTTP. No retry queue or abandoned HTTP fetch is created.
        job.cancel()
        raise ValueError('地址解析超时，未继续获取网页。') from None


def allowed_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('请填写完整的公开 HTTPS 页面地址。')
    p = urlsplit(value)
    decoded = unquote(value)
    keys = [re.sub(r'[^a-z]', '', unquote(key).lower()) for key, _ in parse_qsl(p.query)]
    if (p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in {None, 443}
            or any(any(term in key for term in ('token', 'secret', 'password', 'apikey', 'authorization', 'session')) for key in keys)
            or any('%' in unquote(key) for key, _ in parse_qsl(p.query))
            or any(ord(c) < 32 for c in decoded)
            or re.search(r'sk-(?:proj-)?[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9_.-]{12,}', decoded)):
        raise ValueError('仅获取支持的公开资料站点，不支持账户、私网或带凭据的地址。')
    return urlunsplit(('https', p.hostname, p.path or '/', p.query, ''))


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'template', 'noscript'}: self.hidden.append(tag)
        if not self.hidden and tag in {'p', 'br', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'section'}: self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]: self.hidden.pop()
        elif tag in {'p', 'div', 'li', 'tr', 'section'}: self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def extract(raw, content_type):
    if not isinstance(raw, bytes) or len(raw) > MAX_RAW: raise ValueError('资料过大，未保存截断正文。')
    mime = content_type.split(';', 1)[0].strip().lower()
    if mime not in {'text/html', 'application/xhtml+xml', 'text/plain'}:
        raise ValueError('暂只支持文本及HTML页面；PDF等资料可粘贴所需段落并保留待核。')
    declared = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', content_type, re.I)
    if declared is None and mime != 'text/plain':
        declared = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9_-]+)', raw[:4096].decode('ascii', errors='ignore'), re.I)
    charset = declared[1].lower() if declared else 'utf-8'
    if charset not in {'utf-8', 'utf8', 'shift_jis', 'shift-jis', 'sjis', 'cp932', 'euc-jp', 'euc_jp', 'iso-2022-jp', 'us-ascii'}:
        raise ValueError('资料编码暂不支持，未猜测或替换字符。')
    text = raw.decode(charset, errors='strict')
    if mime != 'text/plain':
        parser = TextParser(); parser.feed(text); parser.close(); text = ''.join(parser.parts)
    text = '\n'.join(' '.join(line.split()) for line in text.splitlines() if line.strip())
    if not text or len(text.encode('utf-8')) > MAX_TEXT: raise ValueError('页面正文为空或过大，未截断为核据。')
    return text


def fetch(url):
    url = allowed_url(url); p = urlsplit(url)
    deadline = time.monotonic() + MAX_SECONDS
    addresses = resolve(p.hostname, deadline)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('资料地址未解析到受支持的公网地址。')
    # Pin this checked endpoint, retaining the original hostname for certificate
    # validation and SNI. No second DNS lookup, redirects, proxy, cookies or auth.
    endpoint = addresses[0]
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw_socket = socket.socket(endpoint[0], socket.SOCK_STREAM)
            self.sock = raw_socket
            try:
                raw_socket.settimeout(remaining(deadline)); raw_socket.connect(endpoint[4])
                self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host, do_handshake_on_connect=False)
                self._pinned_socket = self.sock
                self.sock.settimeout(remaining(deadline)); self.sock.do_handshake()
            except BaseException:
                raw_socket.close(); raise
    connection = PinnedHTTPS(p.hostname, timeout=remaining(deadline), context=ssl.create_default_context())
    def abort():
        sock = getattr(connection, '_pinned_socket', None) or getattr(connection, 'sock', None)
        if sock is not None:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        connection.close()
    watchdog = threading.Timer(max(.001, deadline - time.monotonic()), abort)
    watchdog.daemon = True; watchdog.start()
    try:
        connection.request('GET', p.path + ('?' + p.query if p.query else ''), headers={
            'Accept': 'text/html, text/plain', 'Accept-Encoding': 'identity', 'User-Agent': 'KanaMemory-SourceReview/1'})
        response = connection.getresponse()
        if 300 <= response.status < 400: raise ValueError('页面发生跳转，请填写其最终公开地址。')
        if response.status != 200: raise ValueError('资料未成功获取。')
        if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
            raise ValueError('暂不支持压缩的资料响应。')
        chunks, size = [], 0
        while True:
            wait = remaining(deadline)
            sock = getattr(connection, '_pinned_socket', None)
            if sock is not None: sock.settimeout(wait)
            part = response.read1(min(65536, MAX_RAW + 1 - size))
            if not part: break
            chunks.append(part); size += len(part)
            if size > MAX_RAW: raise ValueError('资料过大，未保存截断正文。')
        remaining(deadline)
        raw = b''.join(chunks)
        text = extract(raw, response.getheader('Content-Type', ''))
        return {'mode': 'retrieved', 'url': url, 'final_url': url, 'retrieved_at': app_clock.utc_now().isoformat(),
            'raw_sha256': sha256(raw).hexdigest(), 'text': text, 'text_sha256': sha256(text.encode('utf-8')).hexdigest(),
            'extractor': EXTRACTOR}
    finally:
        watchdog.cancel(); connection.close()
