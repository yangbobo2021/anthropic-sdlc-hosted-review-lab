"""Publish the replay result on the corresponding synthetic branch only."""

import json
import os
from pathlib import Path
import subprocess


REPO = 'yangbobo2021/anthropic-sdlc-hosted-review-lab'
OWNER = 'yangbobo2021'


def api(endpoint, payload=None):
    command = ['gh', 'api', endpoint]
    if payload is not None:
        command += ['--method', 'POST', '--input', '-']
    return json.loads(subprocess.check_output(command, input=json.dumps(payload) if payload is not None else None,
                                               text=True, timeout=20))


def main():
    assert os.environ['GITHUB_REPOSITORY'] == REPO
    case = os.environ['REPLAY_CASE']
    assert case in {'clean', 'important'}
    artifact = json.loads(Path('receipt.json').read_text())[case]
    branch = f'codex/hosted-review-{case}'
    prefix = f'repos/{REPO}'
    pulls = api(f'{prefix}/pulls?state=open&base=replay-gate&head={OWNER}:{branch}')
    assert len(pulls) == 1
    pull = pulls[0]
    assert pull['head']['repo']['full_name'] == REPO and pull['head']['ref'] == branch
    assert pull['base']['repo']['full_name'] == REPO and pull['base']['ref'] == 'replay-gate'
    head = pull['head']['sha']
    outcome = artifact['adapter_publish_attempt']['conclusion']
    assert head != artifact['source_sha'] and outcome in {'success', 'failure'}
    run_url = f'https://github.com/{REPO}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    publication = api(f'{prefix}/check-runs', {
        'name': 'hosted-review-replay', 'head_sha': head,
        'status': 'completed', 'conclusion': outcome, 'details_url': run_url,
        'output': {'title': 'Synthetic replay gate',
                   'summary': f'Replays public check {artifact["source_check_id"]} at {artifact["source_sha"]}.',
                   'text': json.dumps(artifact, ensure_ascii=False)},
    })
    assert publication['head_sha'] == head and publication['app']['id'] == 15368
    assert publication['conclusion'] == outcome
    print(json.dumps({'replay_case': case, 'pr': pull['number'], 'sandbox_head': head,
                      'published_check': publication['id'], 'outcome': outcome}))


if __name__ == '__main__':
    main()
