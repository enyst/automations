"""Local control plane only: submit one Cloud worker; never run verification here."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = Path(__file__).resolve().parent


class LauncherError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(method, url, key, body=None, session=False, text=False):
    headers = {'Content-Type': 'application/json',
               'X-Session-API-Key' if session else 'Authorization': key if session else 'Bearer ' + key}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise LauncherError('response_too_large')
        return raw.decode().strip() if text else json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raise LauncherError('http_' + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise LauncherError('network_unavailable') from None


def get_secret(name):
    """Fetch a named secret: from the agent server when AGENT_SERVER_URL is set, else from the OpenHands API."""
    url = os.environ.get("AGENT_SERVER_URL", "").rstrip("/")
    key = os.environ.get("SESSION_API_KEY") or os.environ.get("OH_SESSION_API_KEYS_0", "")
    if url:  # local Agent Canvas
        url = f"{url}/api/settings/secrets/{name}"
    else:  # OpenHands Cloud / Enterprise
        api = os.environ["OPENHANDS_CLOUD_API_URL"].rstrip("/")
        url = f"{api}/api/v1/sandboxes/{os.environ['SANDBOX_ID']}/settings/secrets/{name}"
    with urllib.request.urlopen(urllib.request.Request(
        url, headers={"X-Session-API-Key": key}
    )) as r:
        return r.read().decode().strip()


def fire_callback(status="COMPLETED", error=None):
    """Signal run completion. MUST be called on every exit path — success AND error."""
    url = os.environ.get("AUTOMATION_CALLBACK_URL", "")
    if not url: return
    body = {"status": status, "run_id": os.environ.get("AUTOMATION_RUN_ID", "")}
    if error: body["error"] = error
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=json.dumps(body).encode(), headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('AUTOMATION_CALLBACK_API_KEY', '')}",
        }))
    except Exception as e: print(f"Callback error: {e}")


def save(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def start_body(config, repos, identifier, prompt):
    if len(repos) != 1 or repos[0] != {'url': 'https://github.com/OpenHands/OpenHands', 'provider': 'github', 'ref': 'main'}:
        raise LauncherError('unexpected_repositories')
    # Cloud's repository selection performs cloning; no local checkout or subprocess.
    repository = urllib.parse.urlsplit(repos[0]['url']).path.strip('/')
    return {'conversation_id': identifier, 'selected_repository': repository,
            'selected_branch': repos[0]['ref'], 'llm_model': config['model'],
            'title': 'Cloud verify-openhands: ' + config['feature_id'], 'public': False,
            'initial_message': {'role': 'user', 'content': [{'type': 'text', 'text': prompt}], 'run': False}}


def run(status_only=False):
    if not os.environ.get('AGENT_SERVER_URL') or os.environ.get('SANDBOX_ID'):
        raise LauncherError('local_launcher_required')
    config = json.loads((HERE / 'config.json').read_text())
    if config['cloud_url'] != 'https://app.all-hands.dev':
        raise LauncherError('unexpected_cloud_origin')
    root = Path(config['state_directory']).expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with (root / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = root / 'state.json'
        key = get_secret('OPENHANDS_API_KEY')  # Fetch only the required credential.
        def cloud(method, suffix, body=None):
            return request(method, config['cloud_url'] + suffix, key, body)
        user = cloud('GET', '/api/v1/users/me')
        if user.get('id') != config['owner_id'] or user.get('org_id') != config['owner_id']:
            raise LauncherError('wrong_cloud_identity')
        if path.exists():
            state = json.loads(path.read_text())
        else:
            if status_only:
                return {'phase': 'not_submitted'}
            state = {'conversation_id': str(uuid.uuid4()), 'phase': 'prepared', 'sent': False}
            save(path, state)  # Persist intent BEFORE any remote side effect.
        cid = state['conversation_id']
        if state['phase'] == 'create_pending':
            raise LauncherError('creation_uncertain_inspect_saved_conversation_id')
        if state['phase'] == 'prepared':
            if status_only:
                return state
            prompt = (HERE / 'prompt.txt').read_text()
            repos = json.loads((HERE / 'repos_config.json').read_text())
            # Explicit id makes an ambiguous POST recoverable; never generate another worker.
            state['phase'] = 'create_pending'
            save(path, state)
            task = cloud('POST', '/api/v1/app-conversations', start_body(config, repos, cid, prompt))
            state.update(phase='starting', start_task_id=task['id'])
            save(path, state)
        if state['phase'] == 'starting':
            for attempt in range(7 if not status_only else 1):
                task = cloud('GET', '/api/v1/app-conversations/start-tasks?ids=' + state['start_task_id'])[0]
                if task['status'] in {'READY', 'ERROR', 'FAILED'}:
                    break
                if not status_only and attempt < 6:
                    time.sleep(5)
            if task['status'] != 'READY':
                state['startup_status'] = task['status']
                save(path, state)
                return state
            if task['app_conversation_id'].replace('-', '') != cid.replace('-', ''):
                raise LauncherError('conversation_identity_mismatch')
            state.update(phase='ready', sandbox_id=task['sandbox_id'])
            save(path, state)
        if state['phase'] == 'send_pending':
            raise LauncherError('send_uncertain_inspect_saved_conversation_id')
        conversation = cloud('GET', '/api/v1/app-conversations?ids=' + cid)[0]
        if not conversation or conversation.get('agent_kind') != 'openhands':
            raise LauncherError('normal_openhands_worker_required')
        if conversation.get('llm_model') != config['model']:
            raise LauncherError('unexpected_worker_model')
        if not state['sent'] and conversation.get('execution_status') != 'idle':
            # Some Cloud versions run initial_message even when run=False.
            state.update(sent=True, phase='submitted', started_from_initial_message=True)
        if not state['sent'] and not status_only:
            # Mark uncertain BEFORE sending. An HTTP failure never auto-resends the task.
            state.update(sent=True, phase='send_pending')
            save(path, state)
            cloud('POST', '/api/v1/app-conversations/' + cid + '/send-message',
                  {'content': [{'type': 'text', 'text': (HERE / 'prompt.txt').read_text()}], 'run': True})
            state['phase'] = 'submitted'
        state['execution_status'] = conversation.get('execution_status')
        state['url'] = config['cloud_url'] + '/canvas/conversations/' + cid.replace('-', '')
        save(path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    try:
        state = run(args.status)
        print(json.dumps(state))
        fire_callback()
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, LauncherError) else type(error).__name__
        print(json.dumps({'error': code}))
        fire_callback('FAILED', code)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
