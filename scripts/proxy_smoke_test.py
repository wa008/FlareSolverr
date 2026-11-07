#!/usr/bin/env python3
"""Small sequential proxy check. Credentials are read locally, never printed."""
import argparse
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import time
import urllib.error
import urllib.request


def load_config(path):
    config = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('export '):
            line = line[7:]
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        config[key.strip()] = value
    config.update(os.environ)
    return config


def fetch(endpoint, url, proxy=None):
    payload = {'cmd': 'request.get', 'url': url, 'maxTimeout': 60000,
               'waitInSeconds': 2, 'returnScreenshot': False}
    if proxy:
        payload['proxy'] = proxy
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        try:
            response = opener.open(request, timeout=180)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            result = json.load(response)
        solution = result.get('solution', {})
        page = solution.get('response', '')
        pre = re.search(r'<pre\b[^>]*>(.*?)</pre>', page, flags=re.S | re.I)
        text = html.unescape(pre.group(1) if pre else page).strip()
        if result.get('status') != 'ok':
            raise ValueError(result.get('message', 'FlareSolverr error'))
        if 'ipinfo.io' in url:
            info = json.loads(text)
        else:
            info = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        address = str(ipaddress.ip_address(info['ip']))
        return {'ok': True, 'ip': address, 'country': info.get('country', info.get('loc')),
                'seconds': round(time.monotonic() - started, 3)}
    except Exception as error:
        return {'ok': False, 'error': str(error)[:500],
                'seconds': round(time.monotonic() - started, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8193/v1')
    parser.add_argument('--country', default='fr')
    parser.add_argument('--rounds', type=int, choices=range(1, 6), default=3)
    args = parser.parse_args()
    config = load_config(args.env_file)
    for name in ('PROXY_URL', 'PROXY_USERNAME', 'PROXY_PASSWORD_PREFIX'):
        if not config.get(name):
            parser.error('Missing configuration: ' + name)

    baseline = fetch(args.endpoint, 'https://ipinfo.io/json')
    print(json.dumps({'test': 'direct', **baseline}), flush=True)
    if not baseline['ok']:
        return 1
    for round_number in range(1, args.rounds + 1):
        session = secrets.token_hex(5)
        proxy = {'url': config['PROXY_URL'], 'username': config['PROXY_USERNAME'],
                 'password': f"{config['PROXY_PASSWORD_PREFIX']}_country-{args.country}_session-{session}_lifetime-1h"}
        for url in ('https://ipinfo.io/json', 'https://www.cloudflare.com/cdn-cgi/trace'):
            result = fetch(args.endpoint, url, proxy)
            verified = (result['ok'] and result['ip'] != baseline['ip']
                        and result['country'] == args.country.upper())
            print(json.dumps({'test': 'proxy', 'round': round_number, 'session': session,
                              'url': url, **result, 'verified': verified}), flush=True)
            if not verified:
                return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
