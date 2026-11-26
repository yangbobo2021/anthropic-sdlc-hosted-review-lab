"""Replay a public Claude review on its original SHA through the delivery adapter."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'source'
CLAUDE_APP_ID = 1236702
APP_NAME = 'Claude Code Review'
CONTROL_APP_IDS = {'build': 900001, 'review-evidence': 900002}
CONTROL_REVIEWER = 'sdlc-replay-independent-human'

sys.path.insert(0, str(SOURCE))
import github_evidence as adapter


def public_get(endpoint):
    request = Request(f'https://api.github.com/{endpoint}', headers={
        'User-Agent': 'sdlc-review-acceptance',
        'Accept': 'application/vnd.github+json',
    })
    with urlopen(request, timeout=18) as response:
        return json.load(response)


def observe(case):
    repo = case['repo']
    root = f'repos/{repo}'
    pull_endpoint = f'{root}/pulls/{case["pr"]}'
    pull = public_get(pull_endpoint)
    assert pull['state'] == 'open' and pull['head']['sha'] == case['sha']
    assert pull['base']['repo']['private'] is False and pull['base']['repo']['full_name'] == repo
    actual = public_get(f'{root}/check-runs/{case["check_id"]}')
    assert actual['name'] == APP_NAME and actual['app']['id'] == CLAUDE_APP_ID
    assert actual['app']['slug'] == 'claude' and actual['app']['owner']['login'] == 'anthropics'
    assert actual['head_sha'] == case['sha'] and actual['status'] == 'completed'
    text = actual['output']['text'] or ''
    lines = text.strip().splitlines()
    marker = re.fullmatch(r'<!-- bughunter-severity: (.*) -->', lines[-1]) if lines else None
    counts = json.loads(marker.group(1)) if marker else None
    assert counts == case['counts'], (case['check_id'], counts)
    return pull_endpoint, pull, actual


def run_case(name, case):
    pull_endpoint, pull, actual = observe(case)
    repo, head = case['repo'], case['sha']
    root = f'repos/{repo}'
    policy = json.loads((SOURCE / 'policy.json').read_text())
    policy['human_approvers'] = [CONTROL_REVIEWER]
    policy['claude_app_id'] = CLAUDE_APP_ID
    for check in policy['checks']:
        check['app_id'] = CONTROL_APP_IDS[check['name']]
    artifact = {'requested_at': datetime.now(timezone.utc).isoformat(),
                'case': name, 'source_pr': pull['html_url'], 'source_sha': head,
                'source_check': {key: actual[key] for key in ('id', 'html_url', 'status', 'conclusion', 'name')},
                'source_check_id': actual['id'], 'source_app_id': CLAUDE_APP_ID,
                'source_title': actual['output']['title'], 'counts': case['counts'],
                'output_text_sha256': sha256((actual['output']['text'] or '').encode()).hexdigest(),
                'synthetic_controls': ['independent human approval', 'build success', 'review-evidence success'],
                'external_writes': 0, 'authenticated_live_check_reads': 0}

    def read_or_capture(endpoint, payload=None):
        if payload is not None:
            assert endpoint == f'{root}/check-runs'
            assert payload['head_sha'] == head
            assert 'adapter_publish_attempt' not in artifact
            artifact['adapter_publish_attempt'] = payload
            return {'captured_locally': True}
        if endpoint == pull_endpoint:
            refreshed = public_get(endpoint)
            assert refreshed['state'] == 'open' and refreshed['head']['sha'] == head
            return refreshed
        if endpoint == f'{pull_endpoint}/reviews?per_page=100&page=1':
            return [{'user': {'type': 'User', 'login': CONTROL_REVIEWER}, 'state': 'APPROVED', 'commit_id': head}]
        if endpoint.startswith(f'{root}/commits/{head}/check-runs?check_name=Claude%20Code%20Review&app_id={CLAUDE_APP_ID}&'):
            rows = public_get(endpoint)
            matching = [row for row in rows['check_runs']
                        if row['app']['id'] == CLAUDE_APP_ID and row['head_sha'] == head and row['name'] == APP_NAME]
            assert matching and max(row['id'] for row in matching) == actual['id']
            selected = next(row for row in matching if row['id'] == actual['id'])
            assert selected['output']['text'] == actual['output']['text']
            artifact['authenticated_live_check_reads'] += 1
            return rows
        for control, app_id in CONTROL_APP_IDS.items():
            if endpoint.startswith(f'{root}/commits/{head}/check-runs?check_name={control}&app_id={app_id}&'):
                return {'check_runs': [{'id': app_id, 'head_sha': head, 'app': {'id': app_id}, 'name': control,
                                        'status': 'completed', 'conclusion': 'success'}]}
        raise AssertionError(f'Unexpected API route: {endpoint}')

    with TemporaryDirectory(prefix='sdlc0051-replay-') as scratch:
        event_path, policy_path = (Path(scratch) / name for name in ('event.json', 'policy.json'))
        event_path.write_text(json.dumps({'pull_request': pull}))
        policy_path.write_text(json.dumps(policy))
        env = {'GITHUB_REPOSITORY': repo, 'GITHUB_EVENT_NAME': 'pull_request_target',
               'GITHUB_EVENT_PATH': str(event_path)}
        with patch.dict(os.environ, env), patch.object(adapter, 'api', read_or_capture), patch.object(
            sys, 'argv', ['github_evidence.py', 'required', str(policy_path)]
        ):
            artifact['exit'] = adapter.main()

    expected = case['expected']
    assert artifact['authenticated_live_check_reads'] == 1
    assert artifact['adapter_publish_attempt']['conclusion'] == expected
    assert artifact['exit'] == (0 if expected == 'success' else 1)
    return artifact


def main():
    cases = json.loads((ROOT / 'cases.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=cases)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    labels = [args.case] if args.case else cases
    result = {name: run_case(name, cases[name]) for name in labels}
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    for name, artifact in result.items():
        print(f"{name}: app {artifact['source_app_id']} run {artifact['source_check_id']}, "
              f"adapter exit {artifact['exit']} / {artifact['adapter_publish_attempt']['conclusion']}")


if __name__ == '__main__':
    sys.exit(main())
