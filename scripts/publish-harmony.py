"""Upload signed HAPs; activate update metadata after signature and uploaded-package verification.
Credentials are read from .credentials-pgyer.json and never printed.
"""
import argparse
import json
import time
import zipfile
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
API = 'https://www.pgyer.com/apiv2/app/'


def api(client, key, action, fields):
    result = client.post(API + action, data={'_api_key': key, **fields}).json()
    if result.get('code') != 0:
        raise RuntimeError(f'Pgyer {action} failed, code={result.get("code")}')
    return result['data']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('hap', type=Path)
    parser.add_argument('--notes', default='鸿蒙版随心记：聊天、分类记忆和更新检查')
    parser.add_argument('--activate', '--activate-verified', dest='activate_verified', action='store_true',
                        help='Publish update metadata after verifying signature and uploaded package')
    args = parser.parse_args()
    cfg = json.loads((ROOT / '.credentials-pgyer.json').read_text())
    if not args.hap.name.endswith('-signed.hap'):
        raise ValueError('Only signed HAPs may be uploaded; unsigned packages are rejected')
    with zipfile.ZipFile(args.hap) as z:
        info = json.loads(z.read('pack.info'))['summary']['app']
    if info['bundleName'] != 'com.example.suixinji':
        raise ValueError('Wrong bundle name')
    draft = ROOT / 'data/harmony-upload.json'
    active = ROOT / 'data/harmony-release.json'
    with httpx.Client(timeout=120) as client:
        if args.activate_verified:
            receipt = json.loads(draft.read_text())
            if receipt['versionCode'] != int(info['version']['code']):
                raise ValueError('Receipt version differs from HAP')
            result = client.get(API + 'buildInfo', params={
                '_api_key': cfg['apiKey'], 'buildKey': receipt['buildKey']}).json()
            if result.get('code') != 0:
                raise RuntimeError(f'Pgyer buildInfo failed, code={result.get("code")}')
            data = result['data']
            if data['buildIdentifier'] != info['bundleName']:
                raise ValueError('Published bundle differs')
            if str(data['buildVersionNo']) != str(receipt['versionCode']):
                raise ValueError('Published version differs')
            if active.exists() and json.loads(active.read_text())['versionCode'] >= receipt['versionCode']:
                raise ValueError('Update versionCode must increase')
            active.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print('Verified update metadata activated. Run scripts/deploy.sh to publish it.')
            return
        token = api(client, cfg['apiKey'], 'getCOSToken', {
            'buildType': 'harmonyos', 'buildUpdateDescription': args.notes})
        with args.hap.open('rb') as f:
            r = client.post(token['endpoint'], data=token['params'],
                            files={'file': (args.hap.name, f, 'application/octet-stream')})
        r.raise_for_status()
        data = None
        for _ in range(60):
            result = client.get(API + 'buildInfo', params={'_api_key': cfg['apiKey'], 'buildKey': token['key']}).json()
            if result.get('code') == 0:
                data = result['data']
                break
            if result.get('code') != 1247:
                raise RuntimeError(f'Pgyer publication failed, code={result.get("code")}')
            time.sleep(3)
        if not data:
            raise RuntimeError('Publication timed out; do not activate updates')
        if data['buildIdentifier'] != info['bundleName'] or str(data['buildVersionNo']) != str(info['version']['code']):
            raise ValueError('Pgyer package metadata differs')
        receipt = {'bundleName': info['bundleName'], 'versionCode': int(info['version']['code']),
                   'versionName': info['version']['name'], 'notes': args.notes,
                   'buildKey': data['buildKey'], 'url': 'https://www.pgyer.com/' + data['buildShortcutUrl']}
        draft.parent.mkdir(exist_ok=True)
        draft.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        print('HAP uploaded; receipt saved in data/harmony-upload.json.')
        print('After signature and Pgyer certificate verification, use --activate and scripts/deploy.sh.')


if __name__ == '__main__':
    try:
        main()
    except (httpx.HTTPError, ValueError, KeyError, OSError, RuntimeError) as error:
        # HTTP errors can contain auth query parameters. Never print raw exceptions.
        print('Publishing did not complete:', type(error).__name__)
        raise SystemExit(1) from None
