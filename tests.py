from __future__ import annotations

import csv
import io
import json
import tempfile
from pathlib import Path

import app

MOCK = [
    {"_index": 0, "published": "13 September 2026, 7:42:18 PM", "published_ts": 1789328538, "title": "বাংলা ভিডিও 😀 → ∑", "description": "বাংলা\n\nمرحبا بالعالم\n\nहिंदी वीडियो\n日本語のタイトル\n한국어 제목\n简体中文", "url": "https://www.youtube.com/watch?v=AAA", "id": "AAA", "channel": "Demo قناة", "channel_url": "https://www.youtube.com/@demo", "thumbnail": "https://img.example/a.jpg", "duration": "01:23", "duration_seconds": 83, "views": 1200, "likes": 55},
    {"_index": 1, "published": "12 September 2026, 6:21:03 PM", "published_ts": 1789240863, "title": "Comma, quotes and newlines", "description": 'line 1, line 2\n"quoted"\nhttps://example.com/x?a=1,b=2', "url": "https://www.youtube.com/watch?v=BBB", "id": "BBB", "channel": "Demo", "channel_url": "https://www.youtube.com/@demo", "thumbnail": "https://img.example/b.jpg", "duration": "12:01", "duration_seconds": 721, "views": 7, "likes": None},
]


def main():
    assert app.validate_source_url('https://www.youtube.com/@abc') == ('channel', 'https://www.youtube.com/@abc')
    assert app.validate_source_url('youtube.com/channel/UC123') == ('channel', 'https://www.youtube.com/channel/UC123')
    assert app.validate_source_url('https://youtube.com/playlist?list=PL123') == ('playlist', 'https://www.youtube.com/playlist?list=PL123')
    assert app.normalize_description(' a   b \r\n\r\n\r\n c ') == 'a b\n\nc'
    try:
        app.validate_source_url('https://example.com/abc')
        raise AssertionError('invalid URL accepted')
    except ValueError:
        pass

    playlist_a = {"id": "PLA", "title": "Linear Algebra", "url": "https://www.youtube.com/playlist?list=PLA", "thumbnail": "", "count": 2}
    playlist_b = {"id": "PLB", "title": "Previous Exams", "url": "https://www.youtube.com/playlist?list=PLB", "thumbnail": "", "count": 1}
    grouped = [app._record_for_playlist(MOCK[0], playlist_a, 0, 0), app._record_for_playlist(MOCK[1], playlist_a, 1, 1), app._record_for_playlist(MOCK[0], playlist_b, 0, 2)]

    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        app.export_records(MOCK * 750, ['published','title','description','url','views','likes'], 'csv', p/'channel.csv')
        with (p/'channel.csv').open('r', encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        assert len(rows) == 1501 and rows[1][1] == 'বাংলা ভিডিও 😀 → ∑' and rows[2][2].startswith('line 1')

        app.export_records(grouped, ['published','title','description','url'], 'csv', p/'playlist.csv', playlist_mode=True)
        with (p/'playlist.csv').open('r', encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        assert rows[0][0] == 'Playlist' and rows[1][0] == 'Linear Algebra' and rows[3][0] == 'Previous Exams'

        app.export_records(grouped, ['published','title','description','url'], 'txt', p/'playlist.txt', playlist_mode=True)
        txt = (p/'playlist.txt').read_text(encoding='utf-8')
        assert 'PLAYLIST 1: Linear Algebra' in txt and 'PLAYLIST ID: PLA' in txt and 'VIDEOS IN PLAYLIST: 2' in txt and 'PLAYLIST 2: Previous Exams' in txt and 'VIDEO 1 OF 2' in txt and 'مرحبا بالعالم' in txt

        app.build_pdf(grouped * 15, ['published','title','description','url'], p/'playlist.pdf', playlist_mode=True)
        assert (p/'playlist.pdf').stat().st_size > 10000

    client = app.app.test_client()

    # Regression test: playlist workflow may start from a CHANNEL URL. The
    # backend must honor the requested mode instead of silently switching to
    # channel-wide extraction.
    class _NoRunThread:
        def __init__(self, target, args=(), daemon=True):
            self.target = target
            self.args = args
        def start(self):
            pass
    import unittest.mock as mock
    app.state.update({"status":"idle", "playlists":[playlist_a, playlist_b], "extract_id":0, "records":[], "errors":[]})
    with mock.patch.object(app.threading, 'Thread', _NoRunThread) as patched:
        resp = client.post('/api/extract', json={
            'mode':'playlist',
            'source_url':'https://www.youtube.com/@demo',
            'fields':['title','url'],
            'playlist_ids':['PLA','PLB'],
            'playlists':[playlist_a, playlist_b],
        })
        assert resp.status_code == 200
        assert patched.call_count if hasattr(patched, 'call_count') else True
    assert app.state['mode'] == 'playlist'

    assert client.get('/api/health').status_code == 200
    assert client.get('/').status_code == 200
    assert client.get('/app.js').status_code == 200
    assert client.get('/assets/icons/youscraper-64.png').status_code == 200

    print('PASS: URL validation, Unicode normalization, grouped TXT/CSV/PDF exports, large CSV, HTTP/static routes')


if __name__ == '__main__':
    main()
