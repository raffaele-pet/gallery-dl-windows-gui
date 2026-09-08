"""Scoped login session over stdin, never exported to a cookie file."""
import json
import sys
from gallery_dl import config, main


def run(payload):
    cookies = {c['name']: c['value'] for c in payload.get('cookies', [])
               if c['domain'].lstrip('.') == 'instagram.com' or c['domain'].endswith('.instagram.com')}
    if cookies:
        config.set(('extractor', 'instagram'), 'cookies', cookies)
    sys.argv = ['gallery-dl', '--config-ignore', '--no-input', '--no-colors',
                '--directory', payload['destination'], '--retries', '2', '--http-timeout', '25',
                '--Print', 'after:__GDL_NEW__{_path}', '--Print', 'skip:__GDL_OLD__{_path}']
    sys.argv.extend(payload['urls'])
    return main()


if __name__ == '__main__':
    # gallery-dl reconfigures stdin: do not start its text decoder first.
    payload = json.loads(sys.stdin.buffer.readline().decode('utf-8'))
    config.set(('output',), 'stdout', 'utf-8')
    config.set(('output',), 'stderr', 'utf-8')
    raise SystemExit(run(payload))
