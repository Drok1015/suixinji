import json
from self_agent.releases import harmony_release


def test_harmony_release_fails_closed_and_compares_native_version(tmp_path):
    p = tmp_path / 'release.json'
    assert not harmony_release(str(p), 200)['available']
    release = {'bundleName': 'com.example.suixinji', 'versionCode': 201,
               'versionName': '0.2.1', 'url': 'https://www.pgyer.com/your-app', 'notes': '更新'}
    p.write_text(json.dumps(release))
    assert harmony_release(str(p), 200)['available']
    assert not harmony_release(str(p), 201)['available']
    assert not harmony_release(str(p), 202)['available']
    for url in ['http://www.pgyer.com/a', 'https://www.pgyer.com.evil.test/a',
                'https://www.pgyer.com@evil.test/a']:
        p.write_text(json.dumps(dict(release, url=url)))
        assert not harmony_release(str(p), 0)['available']
    p.write_text('{')
    assert not harmony_release(str(p), 0)['available']
