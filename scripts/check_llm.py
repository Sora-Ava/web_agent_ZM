"""Check only LLM settings in the project's .env; never print the API key."""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values


def main() -> int:
    parser = argparse.ArgumentParser(description='Test .env LLM configuration without starting the app/browser.')
    parser.add_argument('--tools', action='store_true', help='Also test a minimal function/tool call.')
    args = parser.parse_args()
    env_path = Path(__file__).resolve().parents[1] / '.env'
    if not env_path.is_file():
        print('[FAIL] Project .env does not exist.')
        return 1
    env = dotenv_values(env_path)
    missing = [name for name in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL') if not env.get(name)]
    if missing:
        print('[FAIL] Missing settings: ' + ', '.join(missing))
        return 1
    base = env['LLM_BASE_URL'].strip().rstrip('/')
    key = env['LLM_API_KEY']
    model = env['LLM_MODEL'].strip()
    parsed = urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        print('[FAIL] LLM_BASE_URL must be an HTTP(S) URL without embedded credentials.')
        return 1
    if base.endswith('/chat/completions'):
        print('[FAIL] LLM_BASE_URL must not include /chat/completions (usually ends in /v1).')
        return 1
    try:
        timeout = float(env.get('MODEL_TIMEOUT_S') or '60')
        if not 0 < timeout <= 300:
            raise ValueError()
    except ValueError:
        print('[FAIL] MODEL_TIMEOUT_S must be between 0 and 300 seconds.')
        return 1

    def redact(text):
        for secret in (key, env.get('SITE_USERNAME'), env.get('SITE_PASSWORD')):
            if secret:
                text = text.replace(secret, '[REDACTED]').replace(json.dumps(secret)[1:-1], '[REDACTED]')
        return text

    payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Connectivity check. Reply with exactly: OK'}], 'stream': False}
    if args.tools:
        payload['messages'][0]['content'] = 'Call the ping function with message OK. This is only a protocol test.'
        payload['tools'] = [{'type': 'function', 'function': {'name': 'ping', 'description': 'A harmless protocol test.',
            'parameters': {'type': 'object', 'properties': {'message': {'type': 'string'}}, 'required': ['message'], 'additionalProperties': False}}}]
        payload['tool_choice'] = {'type': 'function', 'function': {'name': 'ping'}}

    print('[OK] LLM configuration loaded from project .env. API key is hidden.', flush=True)
    started = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(base + '/chat/completions', json=payload, headers={'Authorization': 'Bearer ' + key})
        print(f'HTTP {response.status_code}; elapsed {time.monotonic() - started:.2f}s')
        if not response.is_success:
            hints = {400: 'Request format or model compatibility error.', 401: 'Check API key.',
                     403: 'Check API/model permissions.', 404: 'Check base URL and model name.',
                     429: 'Rate limit or quota reached.'}
            print('[FAIL] ' + hints.get(response.status_code, 'Provider returned an error.'))
            return 1
        message = response.json()['choices'][0]['message']
        if args.tools:
            calls = message.get('tool_calls') or []
            if len(calls) != 1 or calls[0]['function']['name'] != 'ping':
                print('[FAIL] Expected one ping tool call; provider/model may not support tools.')
                return 1
            arguments = json.loads(calls[0]['function']['arguments'])
            if arguments.get('message') != 'OK':
                print('[FAIL] Tool arguments did not match the test request.')
                return 1
            print('[PASS] Tool call ping(message=OK) is valid. No action was executed.')
        else:
            content = message.get('content')
            if not isinstance(content, str) or not content.strip():
                print('[FAIL] Response has no text content.')
                return 1
            print('[PASS] LLM returned text: ' + redact(content.strip())[:300])
        return 0
    except httpx.TimeoutException:
        print('[FAIL] Request timed out. Check connectivity or MODEL_TIMEOUT_S.')
    except httpx.ConnectError:
        print('[FAIL] Could not connect. Check network, proxy and LLM_BASE_URL.')
    except (ValueError, KeyError, IndexError, TypeError):
        print('[FAIL] Response does not match the Chat Completions format.')
    except httpx.HTTPError as exc:
        print('[FAIL] HTTP transport error: ' + type(exc).__name__)
    return 1


if __name__ == '__main__':
    sys.exit(main())
