"""Reproduce synthetic inventories; no real host or credential inputs are accepted."""
import argparse
import hashlib
import json
from pathlib import Path

RECIPE = 'box-binder-synthetic-v1'
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'skills/box-rclone-binder/tests/fixtures/inventories.json'


def inventory(mode='jwt', count=2):
    hosts = [{'host': 'node%d.example.com' % (i + 1), 'remote_name': 'box',
              'auth_mode': mode, 'config_dir': '/etc/box-binder', 'root_folder_id': '0'}
             for i in range(count)]
    if mode == 'oauth-broker':
        for i, host in enumerate(hosts):
            host['broker_role'] = 'master' if i == 0 else 'slave'
    return {'version': 1, 'hosts': hosts, 'secrets': {'source': 'env',
            'jwt_config_ref': 'BOX_TEST_JWT', 'client_id_ref': 'BOX_TEST_ID',
            'client_secret_ref': 'BOX_TEST_SECRET', 'box_subject_id_ref': 'BOX_TEST_SUBJECT',
            'broker_state_ref': 'BOX_TEST_STATE'}}


def canary():
    return 'SYNTHETIC_' + hashlib.sha256(RECIPE.encode()).hexdigest()[:24]


def render():
    return json.dumps({'recipe': RECIPE, 'synthetic_origin': True,
                       'inventories': {mode: inventory(mode) for mode in
                                       ('jwt', 'ccg-native', 'ccg-mint', 'oauth-broker')}}, indent=2) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--out', type=Path, help='Generate the fixture basename into this directory.')
    args = parser.parse_args()
    expected = render().encode('utf-8')
    path = args.out / FIXTURE.name if args.out else FIXTURE
    if args.check:
        ok = path.is_file() and path.read_bytes() == expected
        print('Synthetic inventories match generator.' if ok else 'Synthetic inventories differ.')
        return 0 if ok else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(expected)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
