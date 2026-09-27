# Auditoria de memória e recursos — setembro de 2026

Foram corrigidos ciclos de referências em oito superfícies, a troca insegura do
callback nativo de vídeo, a consulta síncrona ao FFmpeg na criação de prompts e
as gravações redundantes de preferências. Cada correção tem seu próprio commit.
As alterações que já existiam na árvore de trabalho foram preservadas.

## Como repetir a verificação do projeto

A aplicação usa Python/PyGObject, GTK4 e libadwaita; não usa Rust/Relm4.
`static/leak_lint.py` recusou a árvore com “no Rust sources”; isso não é um
resultado limpo. A inspeção Python foi complementada por censos e finalização.
Não há `clippy.toml` aplicável: as regras ficam em `AGENTS.md` e nos testes.

Execute somente numa sessão gráfica isolada, com HOME temporário e diretório
executável para arquivos temporários:

```sh
mkdir -p "$HOME/.cache/bvc-tests"
TMPDIR="$HOME/.cache/bvc-tests" /note/bigdesktop/scripts/headless-gate.sh --accessible \
  python -m pytest -q tests/test_resource_lifetimes.py tests/test_settings_resources.py
```

Os testes usam destrutores de qdata, não apenas notificações de dispose. Confirmam
que a janela foi mapeada, aguardam a animação e contam a finalização dos descendentes.
A suíte completa da árvore de trabalho passou: **395 testes**. Esse número inclui
funcionalidades que já estavam em desenvolvimento, ainda não commitadas.
Ruff passou nos dois novos arquivos de testes e em `signal_connections.py`.

## Correções e evidências

| Commit | Correção | Regressão demonstrada antes |
|---|---|---|
| 88ddc20 | Controller de tooltip obtém seu widget pelo emissor | 5/5 botões não finalizavam |
| 253be18 | Boas-vindas libera o conteúdo ao fechar | Diálogo e conteúdo sobreviviam aos ciclos |
| 5e8e7ee | Informações destrói a janela, desconecta callbacks e ignora resultados tardios | Janela retida, confirmada por reftrace |
| 166d045 | Extra desconecta importação/exportação de perfis | Botões mantinham o diálogo |
| 7a185ca | Presets/IA desconectam callbacks; ações dinâmicas usam dono fraco | 1206/1206 e 195/195 widgets retidos |
| c30f103 | Prompt consulta FFmpeg num worker | Consulta era executada na thread GTK |
| 057afb0 | Preferência já persistida não regrava arquivo | 5/25 repetições alteravam inode/mtime |
| c207f63 | Rede desconecta o botão que captura o diálogo | Subárvore não finalizava |
| 94d3e69 | Opções individuais desconectam o refresh compartilhado | Controles sobreviviam à raiz |
| 78fb4de | Dependências desconecta callbacks após o filho terminar | Janela retida; fechamento durante instalação precisava proteção |
| 8fdc9db | MPV mantém um callback nativo durante a vida do contexto | Troca liberava o trampoline antes do registro nativo |

As correções de lifetime passaram nos testes `new == fin` das árvores observadas.
O sweep cobre boas-vindas, sobre, áudio, codificação, extra, ruído, legendas,
tamanho, presets, IA, informações, opções individuais, dependências, rede,
arquivos, pastas, reset e editor. Nas bibliotecas instaladas, o resultado ainda
contém os vazamentos externos descritos abaixo. A tolerância de três
AdwBreakpoint por diálogo é explícita, não representa uma correção no sistema.

### Memória marginal

Mesma máquina, sessão Wayland isolada, renderer Cairo confirmado, processo
mantido aberto. Três repetições, amostras em 0/5/25 ciclos após aquecimento.
Inclinação de **Anonymous**, em KiB por ciclo entre 5 e 25:

| Jornada | Antes, intervalo das 3 repetições | Depois, intervalo das 3 repetições |
|---|---:|---:|
| Controle | 0–0,4 | 0–0,4 |
| Boas-vindas | 351–364 | −4,6–0 |
| Informações | 1151–1260 | 0,4–12,4 |
| Extra | 723–727 | 0,2–2 |
| Tamanho | 434–437 | −0,4–5,6 |
| Presets | 1188–2115 | 4,2–4,6 |
| IA | 424–425 | 0 |

Não se usa RSS como prova. Os arquivos também contêm Pss, descritores, threads,
CPU e write_bytes. O par heaptrack de informações/presets, 5 versus 25 ciclos,
deixou diferença de 11,60 KB, não os megabytes por ciclo anteriores.
A fila foi exercitada com 0/10/100 entradas, três vezes: 330 linhas criadas e
330 finalizadas, memória estabilizada após aquecimento, sem crescimento de
threads/descritores. A árvore AT-SPI foi verificada separadamente, fora da coleta.

No editor, a biblioteca GTK corrigida elimina o crescimento de GskGLImage, mas
Anonymous sem ajuste do alocador continua variável: diferença de 15,6–122,3 MiB
entre 5 e 25 visitas em três execuções. Com `MALLOC_TRIM_THRESHOLD_=0`, apenas
para diagnóstico, essa diferença cai para 2480/2588/2500 KiB. O par heaptrack
válido registra 68,17 KB adicionais ao encerrar, com pico adicional de 1,94 MB.
Isso sustenta retenção predominante do alocador, não uma alegação de memória plana
ou de ausência absoluta de retenção. Não foi introduzido malloc_trim na aplicação.

O heaptrack inicialmente caiu na inicialização, dentro de alocação/GVfs; esses
pares são inválidos e foram descartados. O par concluído usou
`PYTHONMALLOC=malloc GTK_A11Y=none` nas duas execuções. Acessibilidade e abertura
real já haviam sido confirmadas nos sweeps separados. Símbolos ausentes em parte
das dependências limitam a atribuição dos pequenos resíduos.

### CPU, disco e processos

A medição válida usa o loop nativo GLib, com dez segundos de aquecimento e
intervalos de sessenta segundos. As três execuções registraram 1/1/0 ticks
(CLK_TCK=100, no máximo 10 ms de CPU por minuto), zero write_bytes e nenhum
crescimento de descritores/threads. O polling Python da primeira sonda gerava
carga própria e não serve como baseline. Dados em `idle-native-*.jsonl`.

Salvar o mesmo valor agora faz zero substituições de arquivo; valores diferentes
mantêm a persistência atômica e a proteção de falhas existentes. Não foi removido
fsync de alterações reais sem redefinir esse contrato. Consultas FFmpeg de prompts
saem da thread GTK e resultados após fechamento não acessam o diálogo.

No editor aquecido com GTK privado, 5/25 visitas mantêm 80 descritores e 24 threads.
São recursos reutilizados do player, não crescimento por visita. Na janela de
instalação, testes com um filho inofensivo e com executável inexistente confirmam
bloqueio de fechamento enquanto ativo, recuperação de erro de spawn e liberação
após término. Nenhum gerenciador de pacotes real foi executado.

## Dependências: patches locais, não instalados nem enviados

Quatro relatos com patches sugeridos estão em `~/relatos-upstream/`:

- `libadwaita-breakpoint-bin-bvc.md`: libadwaita 1.9.3 não libera os elementos de
  sua lista de breakpoints. Controle sem conversor: +15 em cinco ciclos; privado
  corrigido: zero crescimento. Testes da biblioteca: 65 passam; dois testes About
  falham por diferença de newline de release notes, explicitada no relato.
- `gtk-dmabuf-cache-bvc.md`: GTK 4.22.4 não agenda coleta de cache no downloader
  DMA-BUF. Gtk.GLArea puro: +55 GskGLImage em cinco ciclos; privado corrigido: zero
  após 35s. Cairo também passa por esse downloader. Vulkan não foi validado.
- `gtk-filechooser-gesture-bvc.md`: gesto long-press criado sem dono no chooser.
  Arquivo e pasta: +5 objetos em cinco ciclos cada; com patch: zero. A interação
  touch ainda precisa validação específica.
- `python-mpv-update-callback-bvc.md`: python-mpv 1.0.8 substitui o wrapper ctypes
  antes de registrar o callback nativo. Reprodutor seguro verifica a ordem de
  ownership: falha no original, passa no patch. O contrato de sincronização nativo
  ainda merece revisão upstream; o projeto evita essa troca repetida.

Nenhuma biblioteca do sistema foi substituída. Builds de depuração ficam em cache
e foram carregados apenas via LD_LIBRARY_PATH nas sessões isoladas. Não houve push
nem comunicação com upstream.

## Evidência local e limites

Diretório: `~/.cache/bvc-resource-audit/`.

- `before.json`, `after.json`, `additional-before.json`, `additional-after.json`,
  `editor-fixed.json`, `chooser-fixed.json`: censos por jornada.
- `memory-{baseline,current}-{1,2,3}.jsonl`, `memory-trim.jsonl`: medidas dos diálogos.
- `editor-{system,patched}-{1,2,3}.jsonl`, `editor-trim*.jsonl`: editor e alocador.
- `heap-diff.txt`, `editor-heap-valid-diff.txt`: comparação curta/longa concluída.
- `full-gate-complete.log`: 395 testes; `driver.py`, `surfaces.toml`, `measure.py`:
  sondas e receitas; controles mínimos também acompanham os relatos upstream.

A inclusão dos arquivos de tamanho foi autorizada posteriormente: `size_dialog.py`,
`size_target.py`, seus testes e a regressão de finalização entram juntos num commit.
A integração da funcionalidade em arquivos preexistentes continua no working tree,
incluindo as duas conexões de tamanho nas opções individuais; não foi incorporada
junto com as demais alterações preexistentes de `conversion_page.py`.

Não foram medidos todos os codecs/dispositivos, servidor de rede real, instalação
privilegiada nem startup com cache de páginas frio. Os resultados não afirmam
cobertura desses ambientes. Não foi feito teste de boot, pois nenhuma integração
de inicialização ou pacote foi alterada.
