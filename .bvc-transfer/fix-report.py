"""Record recovered immutable source blobs without writing trees or refs.

The Actions token must not publish workflows or modify this PR. Review and
publication are performed separately through the authenticated connector.
The original manifest verifier remains active after this diagnostic helper.
"""
from pathlib import Path
import base64
import json
import runpy

HERE = Path(__file__).resolve().parent
manifest = runpy.run_path(str(HERE / 'transfer.py'), run_name='recovery_manifest')
api = manifest['api']
source = manifest['SOURCE']
expected = manifest['EXPECTED']
entries = []
changes = {}
for name, previous in expected.items():
    path = source / name
    if not path.is_file() or path.is_symlink():
        raise SystemExit('Missing regular source file: ' + name)
    data = path.read_bytes()
    actual = manifest['blob_sha'](data)
    result = api('git/blobs', {
        'content': base64.b64encode(data).decode('ascii'), 'encoding': 'base64',
    })
    if result.get('sha') != actual:
        raise SystemExit('Blob changed during upload: ' + name)
    if actual != previous:
        changes[name] = {'previously_expected': previous, 'candidate': actual}
    entries.append({'path': name, 'type': 'blob', 'sha': actual,
                    'mode': '100755' if name == 'big-video-converter/usr/bin/big-video-converter' else '100644'})
print('RECOVERY_CANDIDATE_ENTRIES=' + json.dumps(entries, sort_keys=True), flush=True)
print('RECOVERY_CHANGED_BLOBS=' + json.dumps(changes, sort_keys=True), flush=True)
# No tree creation, branch update, push, PR edit, merge or automatic approval.
