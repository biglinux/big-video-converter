"""Save a detached recovery candidate for review, never update any branch.

Run before the original manifest verifier. A candidate with different hashes
still fails verification and cannot reach the workflow's publication step.
"""
from pathlib import Path
import base64
import json
import runpy
import subprocess

HERE = Path(__file__).resolve().parent
manifest = runpy.run_path(str(HERE / 'transfer.py'), run_name='recovery_manifest')
api = manifest['api']
SOURCE = manifest['SOURCE']
EXPECTED = manifest['EXPECTED']
blob_sha = manifest['blob_sha']
entries = []
changes = {}
for name, expected in EXPECTED.items():
    path = SOURCE / name
    if not path.is_file() or path.is_symlink():
        raise SystemExit('Recovery did not produce a regular file: ' + name)
    data = path.read_bytes()
    actual = blob_sha(data)
    if actual != expected:
        changes[name] = {'previously_expected': expected, 'candidate': actual}
    # Some files were previously represented only by replay helpers, not blobs.
    created = api('git/blobs', {
        'content': base64.b64encode(data).decode('ascii'), 'encoding': 'base64',
    })
    if created.get('sha') != actual:
        raise SystemExit('Recovery blob changed in transit: ' + name)
    entries.append({'path': name, 'type': 'blob', 'sha': actual,
                    'mode': '100755' if name == 'big-video-converter/usr/bin/big-video-converter' else '100644'})
# Store immutable objects only. No update_ref, push, PR edit or merge here.
tree = api('git/trees', {'base_tree': manifest['BASE_TREE'], 'tree': entries})
commit = api('git/commits', {
    'message': 'Recovery candidate for inspection; tests and manifest approval still required',
    'tree': tree['sha'], 'parents': [manifest['BASE']],
})
print('RECOVERY_CANDIDATE_COMMIT=' + commit['sha'], flush=True)
print('RECOVERY_CANDIDATE_TREE=' + tree['sha'], flush=True)
print('RECOVERY_CHANGED_BLOBS=' + json.dumps(changes, sort_keys=True), flush=True)
subprocess.run(['git', '-C', str(SOURCE), 'add', '--', *EXPECTED], check=True)
local_tree = subprocess.check_output(['git', '-C', str(SOURCE), 'write-tree'], text=True).strip()
if local_tree != tree['sha']:
    raise SystemExit('Candidate has modifications outside the approved file inventory')
