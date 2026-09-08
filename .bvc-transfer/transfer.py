"""Recover the exact reviewed source tree after the local executor failed.

This file and the other transfer helpers are removed from the final tree.
Publication is restricted to the review branch and requires every blob, the
complete source tree and all 128 regression tests to match the reviewed state.
It never merges a PR, approves a review, or changes the main branch.
"""
from pathlib import Path
import base64
import hashlib
import json
import os
import runpy
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

REPO = 'biglinux/big-video-converter'
BRANCH = 'fix/backend-stability-no-ui-20260907'
BASE = 'cc0b6d86ac68fb8f262397816d4c7ad864426580'
BASE_TREE = 'd72bdf1dac44bce0b586106b8d4b979ec44f3ead'
TESTED_TREE = 'c6cf7fe86ac94de993f9d65a78e3932e5176da50'
P = 'big-video-converter/usr/share/big-video-converter/'
EXPECTED = {
 '.github/workflows/backend-tests.yml': '79b1fbd0ad16c5e99aec4f153aa94893e7d7ded6',
 'big-video-converter/usr/bin/big-video-converter': 'e175a8ed92123941943fddf5ee8084ebd0d71996',
 P+'queue_manager.py': '3865a79d48dbf8c187efc32264b8ac544b00bcfe',
 P+'ui/audio_dialog.py': '3377aa24bc29e22e39f1fca7bba9179b9d8d2250',
 P+'ui/conversion_page.py': '6030a2ec8238252046cc47be174399ad0faa9ce2',
 P+'ui/extra_dialog.py': 'e5a9297fe7a39485570a9d7c45dcd5b489d45324',
 P+'ui/mpv_player.py': '1a65b042a7203a30f0cecc552187e531403489b1',
 P+'ui/noise_dialog.py': '794fdaece5cba0c0f25cf23e551883c091b9d740',
 P+'ui/progress_page.py': '9d97f4530ef7b00e8bf386d5dbcb9d3bc0a36d64',
 P+'ui/subtitles_dialog.py': '484d695b52508386aab7c191f53476b70d39a68e',
 P+'ui/video_edit_page.py': '81c86d3af105ca58c89586e77d6078532e3d4d84',
 P+'ui/video_encoding_dialog.py': '631e4c71e066cf9647070fb79e10db21e396c6c5',
 P+'ui/video_options_dialog.py': '51a2f83e8da2dbd792a97b2d7f7cbe23a08129b9',
 P+'ui/video_processing.py': 'cc3051b2e7097e605693341bf91e76aed4c3f6fc',
 P+'utils/conversion.py': 'da3439a2191f94726547f17b0e3f94017ed1e132',
 P+'utils/ffmpeg_options.py': '4f1c9b5c3a372b2e099808983d3ee2b1d1082ddf',
 P+'utils/file_info.py': 'a1bb084f2ca90ac32976926ff2e450af2bdcbea5',
 P+'utils/gpu_selector.py': 'ed3be7a1b6bdf5cff9076717437ca5e1d041d037',
 P+'utils/media_validation.py': '76b89c460607ae3e7ad793ed477bf4fb80ccb2db',
 P+'utils/segment_batch.py': '82142f9377d5690d3cd264a3765c54f9bdad0a40',
 P+'utils/settings_manager.py': '265c11089196267912c1fb72a08caad3f60a0f9f',
 P+'utils/signal_connections.py': 'e622417aacc1637f7d257f44893c60061fafa6b4',
 P+'utils/subtitle_processor.py': '199d4e4b7dfd5ab82b8e3bc75620f825e01c4cd9',
 P+'utils/subtitle_timing.py': '236e03c84de271cc982b613d10add05d110a0d9b',
 P+'utils/video_settings.py': '62938c86b2f07868a1eecf0bf2a683ff94a0c99f',
 'docs/backend-stability.md': 'e90fdf267b370a34f7c95fd380d0398a3e2fad1c',
 'tests/conftest.py': 'f492746ef298fc061479005977c32c3caaeeab34',
 'tests/test_audio_pipeline.py': 'a1b97a78c6e5e01d97495d3d6ac66f1388036d74',
 'tests/test_cli.py': '20e93f1369bc95e3168e77477041e1752f8330c1',
 'tests/test_gtk.py': '286b12c49abb38d45f998b8a61f64c65c2646e5c',
 'tests/test_media_subtitles.py': 'b2f0ec08ff6a682605da9dec471e49928ebb2c0a',
 'tests/test_options_settings.py': 'f827ccfdbc44c6d6dc304c334425829c87f8016c',
 'tests/test_supervisor.py': 'e5e84aac4777aa058420dedf89a6f5a2b1dff5f0',
}
PREPARED = {
 '.github/workflows/backend-tests.yml', 'docs/backend-stability.md',
 'tests/conftest.py', 'tests/test_audio_pipeline.py', 'tests/test_cli.py',
 'tests/test_gtk.py', 'tests/test_media_subtitles.py',
 'tests/test_options_settings.py', 'tests/test_supervisor.py',
 P+'utils/ffmpeg_options.py', P+'utils/media_validation.py',
 P+'utils/segment_batch.py', P+'utils/signal_connections.py',
 P+'utils/subtitle_processor.py', P+'utils/subtitle_timing.py',
}
HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ['BVC_REPO']).resolve()


def guard():
    if os.environ.get('GITHUB_REPOSITORY') != REPO:
        raise SystemExit('This operation is restricted to the reviewed repository')
    if os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH:
        raise SystemExit('This operation is restricted to the review branch')


def git(*args):
    return subprocess.check_output(['git','-C',str(SOURCE),*args],text=True).strip()


def blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def api(path, data=None, method=None):
    guard()
    request = urllib.request.Request(
        'https://api.github.com/repos/' + REPO + '/' + path,
        data=None if data is None else json.dumps(data).encode('utf-8'),
        method=method or ('GET' if data is None else 'POST'),
        headers={'Authorization':'Bearer ' + os.environ['GH_TOKEN'],
                 'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2022-11-28',
                 'User-Agent':'bvc-reviewed-source-transfer',
                 'Content-Type':'application/json'},
    )
    try:
        with urllib.request.urlopen(request,timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Print no token, request headers or credential-bearing URLs.
        try:
            message=json.loads(error.read()).get('message','')
        except (ValueError,AttributeError):
            message=''
        raise RuntimeError(f'GitHub request failed: HTTP {error.code}: {message}') from None


def summary(text):
    print(text,flush=True)
    destination=os.environ.get('GITHUB_STEP_SUMMARY')
    if destination:
        with open(destination,'a',encoding='utf-8') as file:
            file.write(text+'\n\n')


def verify():
    failures=[]
    for name,expected in EXPECTED.items():
        path=SOURCE/name
        actual=blob_sha(path.read_bytes()) if path.is_file() else 'MISSING'
        if actual!=expected:
            failures.append(name)
            print(f'MISMATCH {name}: expected={expected} actual={actual}',flush=True)
            if path.is_file():
                print(f'BYTES {path.stat().st_size}',flush=True)
        else:
            print(f'VERIFIED {name} {actual}',flush=True)
    if failures:
        for name in failures:
            print('--- TRANSFER DIFF '+name,flush=True)
            subprocess.run(['git','-C',str(SOURCE),'diff','--',name],check=False)
        summary('Transfer incomplete: '+', '.join(failures)+'. The review branch is not ready for merging.')
        raise SystemExit('Refusing publication: source differs from the tested manifest')
    subprocess.run(['git','-C',str(SOURCE),'add','--',*EXPECTED],check=True)
    tree=git('write-tree')
    if tree!=TESTED_TREE:
        raise SystemExit(f'Unexpected full tree: {tree}; expected {TESTED_TREE}')
    print('VERIFIED_LOCAL_TREE='+tree,flush=True)
    return tree


def prepare():
    guard()
    if git('rev-parse','HEAD')!=BASE or git('rev-parse','HEAD^{tree}')!=BASE_TREE:
        raise SystemExit('Transfer requires the pinned baseline')
    for name in sorted(PREPARED):
        expected=EXPECTED[name]
        result=api('git/blobs/'+expected)
        if result.get('encoding')!='base64':
            raise SystemExit('Unsupported GitHub blob encoding')
        data=base64.b64decode(result['content'])
        if blob_sha(data)!=expected:
            raise SystemExit('Prepared blob mismatch: '+name)
        path=SOURCE/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    for name in ('restore_ui.py','restore_settings.py','restore_monitor.py',
                 'restore_backend.py','refine_backend.py'):
        print('Applying transfer helper: '+name,flush=True)
        runpy.run_path(str(HERE/name),run_name='__main__')
    # Follow-up transfer repairs are still required to match every original hash.
    for path in sorted(HERE.glob('fix-*.py')):
        print('Applying transfer repair: '+path.name,flush=True)
        runpy.run_path(str(path),run_name='__main__')
    verify()


def publish():
    guard()
    verify()
    root=ET.parse(SOURCE/'results.xml').getroot()
    suites=[root] if root.tag=='testsuite' else list(root.iter('testsuite'))
    counts={key:sum(int(s.get(key,0)) for s in suites)
            for key in ('tests','failures','errors','skipped')}
    if counts!={'tests':128,'failures':0,'errors':0,'skipped':0}:
        raise SystemExit(f'Refusing publication: tests are not all passing: {counts}')
    summary('Verified again on GitHub: '+json.dumps(counts))
    reference=api('git/ref/heads/'+BRANCH)
    parent=reference['object']['sha']
    if parent!=os.environ['GITHUB_SHA']:
        raise SystemExit('Review branch moved during verification; not overwriting it')
    entries=[]
    for name,expected in EXPECTED.items():
        data=(SOURCE/name).read_bytes()
        result=api('git/blobs',{'content':base64.b64encode(data).decode('ascii'),'encoding':'base64'})
        if result.get('sha')!=expected:
            raise SystemExit('Remote content mismatch: '+name)
        entries.append({'path':name,'mode':'100755' if name=='big-video-converter/usr/bin/big-video-converter' else '100644',
                        'type':'blob','sha':expected})
    tree=api('git/trees',{'base_tree':BASE_TREE,'tree':entries})
    if tree.get('sha')!=TESTED_TREE:
        raise SystemExit('Remote tree does not match the tested source')
    summary('VERIFIED_REMOTE_TREE='+TESTED_TREE)
    commit=api('git/commits',{
        'message':'fix: stabilize conversion safety and lifecycle without redesigning GTK UI\n\n128 regression tests pass locally and on the transfer runner. The final\nsource tree matches the reviewed manifest byte-for-byte. Remove all\ntemporary transfer helpers from the source tree.',
        'tree':TESTED_TREE,'parents':[parent]})
    # Fast-forward only: no merge, no force push, and no modification of main.
    api('git/refs/heads/'+BRANCH,{'sha':commit['sha'],'force':False},method='PATCH')
    summary('PUBLISHED_COMMIT='+commit['sha'])
    params=urllib.parse.urlencode({'head':'biglinux:'+BRANCH,'base':'main','state':'open'})
    prs=api('pulls?'+params)
    matching=[p for p in prs if p['head']['ref']==BRANCH and p['base']['ref']=='main'
              and p['head']['repo']['full_name']==REPO]
    if len(matching)!=1:
        summary('Source published; could not identify one existing draft PR. No PR was merged.')
        return
    pr=matching[0]
    body='''## Melhorias de código, sem redesenhar a interface

Mantém as telas, controles e fluxo GTK4/libadwaita existentes. Não inclui catálogo TOML, novos formatos, limite de tamanho ou reorganização visual.

### Implementado
- Argumentos separados em vez de `eval`; validação compartilhada da gramática de opções FFmpeg, sem saídas posicionais extras.
- Temporários privados, publicação sem sobrescrever destinos existentes e validação de mídia antes de autorizar movimentação do original para a lixeira. Sem exclusão permanente como fallback.
- Identidade e conclusão idempotente de trabalhos, cancelamento da árvore de processos, leitura de pipes com timeout e limite de logs.
- Resultados agregados de segmentos; falha ou cancelamento impede etapas seguintes.
- Preservação de faixas de áudio/legendas, tratamento de vídeo sem áudio, recorte de cues sobrepostos e distinção de legendas do mesmo idioma.
- Persistência imediata dos ajustes do editor, correções no cache de matiz/recorte, equalização personalizada e respostas assíncronas antigas.
- Limpeza das conexões de diálogos, preferências XDG e importação JSON validada/transacional, sem transportar opções destrutivas.
- Suíte de regressão, workflow de testes e documentação das mudanças de comportamento.

### Validação
**128 testes passaram localmente e novamente no executor GitHub**, incluindo conversões FFmpeg reais, supervisão GLib e aplicação/diálogos GTK sob Xvfb. Também passaram `bash -n`, ShellCheck, compilação Python e verificação de whitespace.

Os 33 arquivos e a árvore completa foram conferidos por hash contra a versão testada. Os auxiliares de transferência não estão no diff final.

### Limites
Não é o encerramento de todos os 48 itens da auditoria. Não houve ensaio com GPUs físicas nem com o plugin/modelos GTCRN. Os testes do pipeline de redução de ruído usam um filtro pass-through para validar canais, tempo e erros, não qualidade do modelo. Acessibilidade completa com leitor de tela, traduções integrais, HDR e editores externos ainda exigem validação própria. A documentação `docs/backend-stability.md` especifica as limitações de legendas e publicação em filesystems sem hard links.

Base revisada: `cc0b6d86ac68fb8f262397816d4c7ad864426580`.
Árvore testada: `c6cf7fe86ac94de993f9d65a78e3932e5176da50`.

**Este PR não foi mesclado.**
'''
    api('pulls/'+str(pr['number']),{'body':body},method='PATCH')
    summary('PR_URL='+pr['html_url'])
    summary('The PR remains a draft until the final connector review confirms its head and test results.')


if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('prepare','publish'):
        raise SystemExit('Usage: transfer.py prepare|publish')
    prepare() if sys.argv[1]=='prepare' else publish()
