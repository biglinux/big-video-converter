# Revisão contextual R1 — implementação parcial, não aprovação visual

## Referência

Big Design System 2026 — clareza contextual, R14, 21/09/2026, fornecido pelo
usuário. Regras aplicadas: ações e controles com nome; estados inequívocos;
agrupamento por intenção; configurações sem efeito não parecem ativas;
preferências ocultas são preservadas; widgets, foco e semântica nativos.
Não se transplantam os applets, BigSearch nem tipos Rust para o conversor Python.

## Limite da evidência desta rodada

As imagens corretas de fila, áudio e limpeza de áudio foram abertas. O runtime
local reportou GTK 4.22.5/libadwaita 1.9.4 e o mapeamento das bibliotecas do bundle.
Em seguida, o transporte do executor falhou inclusive para echo e print. Não
foram concluídos novos recortes ampliados nem capturas depois das alterações.
Não há aprovação estética, nota AAA+, afirmação de WCAG cumprida ou ensaio com
usuários. Não confundir teste de componente com validação visual.

## Implementação

- Áudio reorganizado em grupos e ComboRows nativos, sem uma ilustração e uma
  sombra por parâmetro. Operação primeiro; conversão só aparece se reencode.
- Resumo explícito do resultado e indicação de salvamento automático para
  conversões futuras. Não se promete alterar trabalhos já em execução.
- Valores personalizados, codec e canais persistem ao alternar copiar/remover.
- Sincronização bidirecional, inclusive das entradas de texto, e desconexão das
  assinaturas ao fechar.
- Patch de limpeza de áudio oculta parâmetros de operações desativadas sem
  esconder a capacidade nem apagar valores. Sliders/switches recebem nomes
  acessíveis e os diálogos usam apresentação adaptativa nativa.

O aplicador tools/apply_contextual_noise_review.py só aceita o blob auditado.
Ele materializa a alteração de noise_dialog.py antes dos testes e da exportação.
O patch e source.tar.gz no artefato do CI incluem esse arquivo modificado.
A branch, antes de executar o aplicador, ainda tem noise_dialog.py da base.

## Testes

O workflow executa testes nativos funcionais de seleção, visibilidade,
preservação de dados e lifetime. Sua biblioteca de sistema é registrada no log.
Ele NÃO cria screenshots e NÃO substitui a revisão estética no runtime exato
GTK 4.22.5/libadwaita 1.9.4. Ausência de GTK falha, não vira skip aprovado.
O pacote contém apenas esta revisão de UI sobre a base 58ec88d; não contém R02
nem as antigas funcionalidades R03/R04 anunciadas sem artefatos.

## Pendências

- Reexecutar no runtime exato e avaliar inteiro/recortes 200%, claro/escuro,
  alto contraste, fonte ampliada, teclado e área real estreita.
- Concluir fila, thumbnails, presets/resolução por item, progresso, editor,
  boas-vindas e demais diálogos, sem marcar como implementadas nesta revisão.
- Revisar e traduzir os novos textos em todos os idiomas; neste patch são
  marcados para gettext, com fallback inglês. Não é entrega stable.
- Reaplicar o diff sobre R02 e executar os testes desse conjunto antes de
  integrar ao checkpoint cumulativo.

## Referências oficiais consultadas

- https://docs.gtk.org/gtk4/css-properties.html
- https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.StyleManager.html
- https://www.w3.org/WAI/WCAG2/supplemental/patterns/o4p06-clear-labels/
- https://base-ui.com/react/overview/accessibility
