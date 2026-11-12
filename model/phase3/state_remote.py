"""Data-only state branch persistence, optimistic SHA conflict protection.

Routine restore/save never creates a branch or bootstraps. Initialization is a
separate explicit command after approval. No main writes, workflow dispatches,
environment changes, force pushes, cache dependency, or external database.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

from bootstrap_online import ROOT
from calendar_features import load_calendar
from online_state import StateError, atomic_write, decode, state_lock

STATE_PATH = 'model/phase3/online_state.json'


class RemoteError(RuntimeError):
    pass


class GitHubState:
    def __init__(self, repository, branch, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid repository')
        if branch != 'model-state':
            raise ValueError('Only the isolated model-state branch may be written')
        self.base = 'https://api.github.com/repos/'+repository
        self.branch, self.token = branch, token

    def api(self, method, path, body=None):
        headers = {'Accept':'application/vnd.github+json', 'Authorization':'Bearer '+self.token,
                   'User-Agent':'TDeck-online-state', 'X-GitHub-Api-Version':'2022-11-28'}
        request = urllib.request.Request(self.base+path, method=method, headers=headers,
                    data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            # In particular, never retry a conflicting write with the new SHA
            # and the OLD learned state: that would overwrite another writer.
            raise RemoteError(f'GitHub state {method} failed (HTTP {exc.code})') from None

    def content(self, ref):
        item = self.api('GET', '/contents/'+STATE_PATH+'?ref='+urllib.parse.quote(ref, safe=''))
        if item.get('encoding') != 'base64':
            item = self.api('GET', '/git/blobs/'+item['sha'])
        return base64.b64decode(item['content']).decode('utf-8'), item['sha']

    def restore(self, output, cfg, options, calendar):
        with state_lock(output):
            return self._restore(output,cfg,options,calendar)

    def _restore(self, output, cfg, options, calendar):
        head = self.api('GET', '/git/ref/heads/'+self.branch)['object']['sha']
        raw, current_sha = self.content(head)
        try:
            decode(raw, cfg, options, calendar)
            selected, recovered = raw, False
        except StateError:
            selected, recovered = None, True
            commits = self.api('GET', '/commits?sha='+head+'&path='+STATE_PATH+'&per_page=6')
            for commit in commits:
                try:
                    candidate, _ = self.content(commit['sha'])
                    decode(candidate, cfg, options, calendar)
                    selected = candidate
                    break
                except StateError:
                    continue
            if selected is None:
                raise StateError('No compatible good state in recent branch history')
        atomic_write(output, selected)
        atomic_write(str(output)+'.remote', json.dumps(dict(sha=current_sha, content=raw, head=head)))
        return recovered

    def save(self, path, cfg, options, calendar):
        with state_lock(path):
            return self._save(path,cfg,options,calendar)

    def _save(self, path, cfg, options, calendar):
        raw = Path(path).read_text(encoding='utf-8')
        decode(raw, cfg, options, calendar)
        remote = json.loads(Path(str(path)+'.remote').read_text(encoding='utf-8'))
        if raw == remote['content']:
            return False
        response=self.api('PUT', '/contents/'+STATE_PATH, dict(message='Update incremental M3 checkpoint',
                 branch=self.branch, sha=remote['sha'], content=base64.b64encode(raw.encode()).decode()))
        atomic_write(str(path)+'.remote',json.dumps(dict(sha=response['content']['sha'],content=raw,head=response['commit']['sha'])))
        return True

    def initialize(self, path, cfg, options, calendar):
        with state_lock(path):
            return self._initialize(path,cfg,options,calendar)

    def _initialize(self, path, cfg, options, calendar):
        raw = Path(path).read_text(encoding='utf-8')
        decode(raw, cfg, options, calendar)
        # An orphan data-only commit: the state branch contains no executable
        # workflow source. POST ref fails if already initialized; no overwrite.
        blob = self.api('POST', '/git/blobs', dict(content=base64.b64encode(raw.encode()).decode(), encoding='base64'))
        tree = self.api('POST', '/git/trees', dict(tree=[dict(path=STATE_PATH,mode='100644',type='blob',sha=blob['sha'])]))
        commit = self.api('POST', '/git/commits', dict(message='Initialize incremental M3 checkpoint',tree=tree['sha'],parents=[]))
        self.api('POST', '/git/refs', dict(ref='refs/heads/'+self.branch,sha=commit['sha']))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['restore','save','initialize'])
    p.add_argument('--state', required=True)
    p.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY','dngnyoo/tdeck-parking-data'))
    p.add_argument('--config', default=str(ROOT/'config.json'))
    p.add_argument('--online-config', default=str(ROOT/'online_config.json'))
    args = p.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    options = json.loads(Path(args.online_config).read_text(encoding='utf-8'))
    if options['mode'] not in ['shadow','production']:
        p.error('State persistence is disabled; activation requires Phase D approval')
    token = os.environ.get('GH_TOKEN')
    if not token: p.error('GH_TOKEN is required in the environment')
    client = GitHubState(args.repository, options['state_branch'], token)
    calendar = load_calendar()
    if args.action == 'restore':
        print('Restored state; recovered from history:', client.restore(args.state,cfg,options,calendar))
    elif args.action == 'save':
        print('State committed:', client.save(args.state,cfg,options,calendar))
    else:
        client.initialize(args.state,cfg,options,calendar)
        print('Initialized data-only state branch')


if __name__ == '__main__':
    main()
