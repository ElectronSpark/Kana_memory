from types import SimpleNamespace
from concurrent.futures import TimeoutError as FutureTimeout

import pytest

from support.feedback_sources_support import URL
import kanamemory.public_excerpt_fetch as retrieval


@pytest.mark.parametrize('mode', ['success', 'redirect', 'deadline'])
def test_pinned_transport_certificate_name_no_redirects_and_total_deadline(monkeypatch, mode):
    now = [100.0]; events = []; body = 'です・ます。'.encode()
    monkeypatch.setattr(retrieval.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(retrieval, 'resolve', lambda *a: [(2, 1, 6, '', ('8.8.8.8', 443))])
    class Sock:
        def settimeout(self, value): assert 0 < value <= 10
        def connect(self, address): events.append(('connect', address))
        def do_handshake(self): events.append(('handshake',))
        def shutdown(self, how): events.append(('shutdown',))
        def close(self): events.append(('socket_close',))
    sock = Sock()
    monkeypatch.setattr(retrieval.socket, 'socket', lambda *a: sock)
    def wrap(raw, **kw):
        assert kw == {'server_hostname': 'www.coelang.tufs.ac.jp', 'do_handshake_on_connect': False}
        events.append(('tls',)); return raw
    monkeypatch.setattr(retrieval.ssl, 'create_default_context', lambda: SimpleNamespace(wrap_socket=wrap))
    class Response:
        status = 302 if mode == 'redirect' else 200
        sent = False
        def getheader(self, key, default=''):
            return {'Content-Type': 'text/plain;charset=utf-8', 'Content-Encoding': 'identity'}.get(key, default)
        def read1(self, maximum):
            if mode == 'deadline': now[0] += 21
            if self.sent: return b''
            self.sent = True; return body
    class Connection:
        def __init__(self, host, timeout, context): self.host, self.timeout, self._context = host, timeout, context
        def request(self, method, path, headers):
            events.append(('request', method, path))
            assert 'Authorization' not in headers and 'Cookie' not in headers
            self.connect()
        def getresponse(self): return Response()
        def close(self): events.append(('close',))
    monkeypatch.setattr(retrieval.http.client, 'HTTPSConnection', Connection)
    if mode == 'success': assert retrieval.fetch(URL)['text'] == body.decode()
    else:
        with pytest.raises(ValueError, match='跳转' if mode == 'redirect' else '超时'): retrieval.fetch(URL)
    assert events.count(('connect', ('8.8.8.8', 443))) == 1 and ('tls',) in events and ('handshake',) in events
    assert len([e for e in events if e[0] == 'request']) == 1 and events[-1] == ('close',)


def test_dns_timeout_does_not_queue_more_tasks_or_continue_http(monkeypatch):
    calls = []
    class Future:
        def result(self, timeout): raise FutureTimeout()
        def done(self): return False
        def cancel(self): calls.append('cancel'); return False
    def submit(*args, **kwargs): calls.append('submit'); return Future()
    monkeypatch.setattr(retrieval, '_DNS', SimpleNamespace(submit=submit))
    monkeypatch.setattr(retrieval, '_DNS_JOB', None)
    with pytest.raises(ValueError, match='解析超时'): retrieval.resolve('www.coelang.tufs.ac.jp', retrieval.time.monotonic() + 20)
    with pytest.raises(ValueError, match='尚未结束'): retrieval.resolve('www.coelang.tufs.ac.jp', retrieval.time.monotonic() + 20)
    assert calls == ['submit', 'cancel']
