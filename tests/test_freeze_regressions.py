"""Regression coverage for the Render failures observed on 27 October 2026."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from test_native_territory import app, franchise, login, HEADERS
from app.extensions import db
from app.models import WorkerHeartbeat
from app.franchise import territory_sources as sources
from app.franchise.territory_boundary_cache import cached_boundary_json


@pytest.mark.parametrize('aware', [False, True])
@pytest.mark.parametrize('age, online', [(30, True), (240, False)])
def test_worker_status_accepts_naive_and_aware_utc(aware, age, online):
    heartbeat = datetime.now(timezone.utc) - timedelta(seconds=age)
    if not aware:
        heartbeat = heartbeat.replace(tzinfo=None)
    assert WorkerHeartbeat(heartbeat_at=heartbeat).is_online is online


def test_worker_status_converts_non_utc_offsets_and_handles_missing_timestamp():
    assert WorkerHeartbeat(heartbeat_at=datetime.now(timezone(timedelta(hours=2)))).is_online
    assert not WorkerHeartbeat(heartbeat_at=None).is_online


def test_operations_page_accepts_database_round_trip_heartbeat(app, franchise):
    client = login(app, franchise)
    worker = WorkerHeartbeat(worker_id='test', heartbeat_at=datetime.now(timezone.utc))
    db.session.add(worker)
    db.session.commit()
    db.session.expire_all()
    assert db.session.get(WorkerHeartbeat, worker.id).heartbeat_at.tzinfo is None
    assert client.get('/admin/operations').status_code == 200


def test_fresh_checked_boundary_copy_never_contacts_publisher(tmp_path):
    data = {'features': [], '_source_meta': {'retrieved_at': '2026-10-27T06:00:00+00:00'}}
    cached_boundary_json(lambda *_: data, 'https://publisher.example/query', {'town': 'A'}, tmp_path)
    def unavailable(*_):
        pytest.fail('A fresh checked copy must be returned before network access')
    cached = cached_boundary_json(unavailable, 'https://publisher.example/query', {'town': 'A'}, tmp_path)
    assert cached['features'] == []
    assert cached['_source_meta'] == data['_source_meta']
    assert cached['_boundary_cache']


def test_older_checked_copy_refreshes_then_falls_back_on_failure(tmp_path):
    url = 'https://publisher.example/query'
    cached_boundary_json(lambda *_: {'features': []}, url, {}, tmp_path)
    path = next(tmp_path.glob('*.json'))
    saved = json.loads(path.read_text(encoding='utf-8'))
    saved['stored_at'] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    path.write_text(json.dumps(saved), encoding='utf-8')
    calls = []
    def unavailable(*args):
        calls.append(args)
        raise ConnectionError('offline')
    assert cached_boundary_json(unavailable, url, {}, tmp_path)['_boundary_cache'] == saved['stored_at']
    assert len(calls) == 1
    refreshed = cached_boundary_json(lambda *_: {'features': ['updated']}, url, {}, tmp_path)
    assert refreshed['features'] == ['updated']


class Response:
    is_redirect = False
    headers = {}
    def __init__(self, url):
        self.url = url
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def raise_for_status(self):
        pass
    def iter_content(self, size):
        yield b'{"features": []}'


def test_interactive_budget_shrinks_between_requests_and_resets(monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(sources.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(sources, '_cache', {})
    def get(url, **kwargs):
        calls.append(kwargs['timeout'])
        return Response(url)
    monkeypatch.setattr(sources.requests, 'get', get)
    with sources.interactive_source_budget():
        sources.public_json(sources.MAIN + '/query', {'page': 1})
        clock[0] += 5
        sources.public_json(sources.MAIN + '/query', {'page': 2})
        clock[0] += 1
        with pytest.raises(sources.requests.Timeout):
            sources.public_json(sources.MAIN + '/query', {'page': 3})
    sources.public_json(sources.MAIN + '/query', {'background': True})
    assert calls == [(2, 3), (0.5, 0.5), (8, 20)]


def test_failed_boundary_route_shares_budget_across_fallbacks(app, franchise, monkeypatch):
    client = login(app, franchise)
    clock = [100.0]
    calls = []
    monkeypatch.setattr(sources.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(sources, '_cache', {})
    def unavailable(url, **kwargs):
        calls.append(url)
        clock[0] += 6
        raise sources.requests.Timeout('publisher unavailable')
    monkeypatch.setattr(sources.requests, 'get', unavailable)
    response = client.post(f'/franchise/{franchise.id}/territory/boundaries',
                           json={'query': 'Randburg', 'kind': 'auto'}, headers=HEADERS)
    assert response.status_code == 503
    assert 'temporarily unavailable' in response.json['error']
    assert len(calls) == 1  # Other publishers cannot each start another long wait.
    assert sources._interactive_deadline.get() is None


def test_streamed_boundary_response_stops_when_budget_expires(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(sources.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(sources, '_cache', {})
    class SlowResponse(Response):
        def iter_content(self, size):
            clock[0] += 7
            yield b'{"features": []}'
    monkeypatch.setattr(sources.requests, 'get', lambda url, **kwargs: SlowResponse(url))
    with sources.interactive_source_budget():
        with pytest.raises(sources.requests.Timeout):
            sources.public_json(sources.MAIN + '/query')
    assert not sources._cache
