from __future__ import annotations

import sys
import types

flask = types.ModuleType('flask')
class DummyResponse: pass
class DummyFlask:
    def __init__(self,*a,**k): pass
    def get(self,*a,**k): return lambda f:f
    def post(self,*a,**k): return lambda f:f
flask.Flask=DummyFlask
flask.Response=DummyResponse
flask.request=types.SimpleNamespace(get_json=lambda silent=False:{})
flask.jsonify=lambda *a,**k: (a,k)
flask.send_file=lambda *a,**k: None
flask.send_from_directory=lambda *a,**k: None
sys.modules['flask']=flask

ytdlp=types.ModuleType('yt_dlp')
CALLS = []
class DummyYDL:
    def __init__(self, opts): self.opts=opts
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def extract_info(self,url,download=False):
        CALLS.append((url, dict(self.opts)))
        if url.endswith('/playlists'):
            return {'channel':'Test Channel','channel_url':'https://www.youtube.com/@test','entries':[
                {'_type':'url','id':'PLA','title':'Linear Algebra','url':'https://www.youtube.com/playlist?list=PLA','thumbnail':'https://example/a.jpg','playlist_count':2},
                {'_type':'url','id':'PLB','title':'Previous Exams','url':'https://www.youtube.com/playlist?list=PLB','thumbnail':'https://example/b.jpg','playlist_count':1},
            ]}
        if 'playlist?list=PLA' in url:
            return {'id':'PLA','title':'Linear Algebra','entries':[
                {'_type':'url','id':'AAA','title':'বাংলা ভিডিও 😀','url':'https://www.youtube.com/watch?v=AAA'},
                {'_type':'url','id':'BBB','title':'日本語のタイトル','url':'https://www.youtube.com/watch?v=BBB'},
            ]}
        if 'playlist?list=PLB' in url:
            return {'id':'PLB','title':'Previous Exams','entries':[
                {'_type':'url','id':'AAA','title':'বাংলা ভিডিও 😀','url':'https://www.youtube.com/watch?v=AAA'},
            ]}
        if 'watch?v=' in url:
            return {'id':'AAA' if 'AAA' in url else 'BBB','title':'বাংলা ভিডিও 😀' if 'AAA' in url else '日本語のタイトル','description':'বাংলা\n\nمرحبا بالعالم\n\nहिंदी\n日本語\n한국어\n简体中文','webpage_url':url,'channel':'Test Channel','channel_url':'https://www.youtube.com/@test','timestamp':1789328538,'duration':83,'view_count':1200,'like_count':55,'thumbnail':'https://example.com/a.jpg'}
        return {'channel':'Test Channel','channel_url':'https://www.youtube.com/@test','entries':[
            {'_type':'url','id':'AAA','title':'বাংলা ভিডিও 😀','url':'https://www.youtube.com/watch?v=AAA','timestamp':1789328538,'channel':'Test Channel','channel_url':'https://www.youtube.com/@test'},
            {'_type':'url','id':'BBB','title':'日本語のタイトル','url':'https://www.youtube.com/watch?v=BBB','timestamp':1789240863,'channel':'Test Channel','channel_url':'https://www.youtube.com/@test'},
        ]}
ytdlp.YoutubeDL=DummyYDL
sys.modules['yt_dlp']=ytdlp

import app


def main():
    assert app.validate_source_url('https://www.youtube.com/@test/') == ('channel','https://www.youtube.com/@test')
    playlists, meta = app.discover_playlists('https://www.youtube.com/@test','channel')
    assert len(playlists)==2 and playlists[0]['title']=='Linear Algebra' and meta['channel']=='Test Channel'
    info=app.discover_playlists('https://www.youtube.com/playlist?list=PLA','playlist')[0]
    assert info[0]['id']=='PLA'
    entries, meta=app.discover_channel('https://www.youtube.com/@test')
    assert len(entries)==2
    assert app.extract_video(entries[0]['url'],entries[0],['title','url','id'])['title']=='বাংলা ভিডিও 😀'
    detailed=app.extract_video(entries[0]['url'],entries[0],['published','title','description','duration','views','likes'])
    assert detailed['views']==1200 and '日本語' in detailed['description']
    assert app.make_video_ydl_opts()['noplaylist'] is True
    playlist_a={'id':'PLA','title':'Linear Algebra','url':'https://www.youtube.com/playlist?list=PLA'}
    playlist_b={'id':'PLB','title':'Previous Exams','url':'https://www.youtube.com/playlist?list=PLB'}
    app.cancel_event.clear()
    app.state.update({'extract_id':77,'status':'idle','records':[],'errors':[],'processed':0,'succeeded':0,'failed':0,'selected_fields':['title','url'],'playlists':[playlist_a,playlist_b]})
    app.extraction_worker('https://www.youtube.com/@test',['title','url'],'playlist',[playlist_a,playlist_b],77)
    records=list(app.state['records'])
    assert len(records)==3 and [r['playlist_id'] for r in records]==['PLA','PLA','PLB']
    assert [r['playlist_index'] for r in records]==[1,2,1]
    playlist_calls=[u for u,_ in CALLS if 'playlist?list=' in u]
    assert playlist_calls[-2:]==[playlist_a['url'],playlist_b['url']]
    video_calls=[u for u,o in CALLS if '/watch?v=' in u]
    assert all('list=' not in u for u in video_calls[-3:])
    assert all(o.get('noplaylist') is True for u,o in CALLS if '/watch?v=' in u)
    from tempfile import TemporaryDirectory
    import csv as _csv
    with TemporaryDirectory() as td:
        out=app.Path(td)
        app.export_records(records,['title','url'],'csv',out/'playlist.csv',playlist_mode=True)
        with (out/'playlist.csv').open('r',encoding='utf-8-sig',newline='') as fh:
            rows=list(_csv.reader(fh))
        assert rows[0][0]=='Source' and rows[0][1]=='Playlist' and rows[1][1]=='Linear Algebra' and rows[3][1]=='Previous Exams'
        app.export_records(records,['title','url'],'txt',out/'playlist.txt',playlist_mode=True)
        txt=(out/'playlist.txt').read_text(encoding='utf-8')
        assert 'PLAYLIST 1: Linear Algebra' in txt and 'VIDEO 1 OF 2' in txt and 'PLAYLIST 2: Previous Exams' in txt
    print('PASS: stubbed channel/playlist discovery, playlist-only worker regression, single-video guarding, and grouped TXT/CSV exports')

if __name__=='__main__': main()
