# Simplificação da experiência — Meus testes e Rascunhos

## Objetivo

Permitir que alguém sem experiência encontre um teste, consulte seu resultado e prepare uma nova execução sem precisar entender “conjuntos”, “rodadas” ou detalhes internos.

Escopo: menus antes dos relatórios HTML/blob. Preservar URLs, identidades, resultados, seleção, confirmação de execução e alterações anteriores. Não executar testes reais, chamar modelos pagos, gravar navegação nem salvar dados reais durante a validação visual.

## Auditoria com Playwright

Janela independente autorizada pelo usuário. Leitura de dados reais; operações de escrita da API bloqueadas. Rascunhos demonstrativos apenas em respostas simuladas no navegador.

Em 1366 × 768, antes das mudanças:

- Lista inicial: quatro a cinco aplicações visíveis; página com aproximadamente 4059 px.
- Catálogo: primeiro teste em y=386; 151 linhas produzem aproximadamente 14089 px de página.
- Histórico: lista inteira acima do resultado; textos longos e comparação vazia sempre visíveis.
- Histórico antigo: execuções vinculadas presentes na lista inicial, mas ausentes da aba de histórico.
- Rascunhos: títulos e instruções usam termos diferentes do restante da jornada.
- Planejador: área já limitada à janela; preservar essa solução, o campo do chat visível e a alternância em telas pequenas.

Percorridos: lista, catálogo, seleção, preparação, editor manual, chat, confirmação de gravação, histórico, execução concluída, lista vazia de planejamentos e retomada de rascunho simulado.

## Implementação

1. Renomear os menus para **Meus testes** e **Rascunhos**, sem alterar as rotas.
2. Cabeçalhos pequenos, busca visível e listas com rolagem própria. Não reduzir a legibilidade para caber mais conteúdo.
3. Catálogo com linhas compactas e ação clara ao selecionar: **Preparar execução**. Seleção não executa testes.
4. Ajuda opcional e curta: escolher testes → ajustar roteiro → revisar e confirmar.
5. Preparador com **Salvar rascunho** e **Revisar execução**; preservar o aceite explícito antes da execução real.
6. Histórico em lista e detalhe lado a lado; reunir tentativas antigas e novas, sem misturar rascunhos com resultados.
7. Resultados longos sob **O que aconteceu**; **Ver execução** para concluídos; comparação somente quando houver alternativa.
8. Rascunhos com origem legível, data de edição e **Continuar**; estado vazio com orientação prática.
9. Após esclarecimento do usuário: a exploração inicial cria uma base duradoura. Adições e reexecuções permanecem nela. **Gerenciar → Arquivar** retira o teste da base ativa, mas mantém definições, IDs, resultados, prints e execuções. **Arquivados → Restaurar** reverte a alteração.
10. Arquivamento persistido separadamente do histórico, com controle de versão para duas abas. Testes arquivados não podem entrar em novos planos, e planos antigos precisam remover ou restaurar esses testes antes de iniciar. Arquivamento indisponível durante uma execução da base.
11. Editor manual simplificado: título, passos e resultado esperado; endereço, descrição e pré-condições opcionais sob divulgação progressiva.

## Validação

- Playwright em 1366 × 768, 1024 × 700 e 390 × 844: navegação, busca, seleção, próximo passo, alternância dos painéis e campo do chat.
- Respostas simuladas para revisão e rascunhos, sem gravar na base.
- TypeScript, lint, testes focados e build sob limite de memória e tempo.
- Capturas de tela para o usuário avaliar cada etapa. Relatórios blob e outros menus fora do redesenho.

## Resultado da validação — 21/10/2026

Implementação concluída no ambiente local. Backend reiniciado somente após confirmar ausência de execuções ativas, conforme autorização do usuário.

### Navegação real

Playwright em janela independente, com bloqueio de escritas reais da API. Percorridos os dois menus, lista de aplicações, catálogo, filtros, seleção, preparação, criação manual, escolha de modelos do chat, ajuda, gravação antes da confirmação, revisão, histórico e retomada de rascunho. Relatórios/downloads não foram acionados.

| Janela | Conferência |
| --- | --- |
| 1920 × 1080 | Lista, roteiro e chat lado a lado; campo de mensagem entre y=922 e y=971. |
| 1366 × 768 | Oito grupos visíveis na página inicial; primeiro teste em y≈274, contra y≈386 antes; editor manual de ≈833 para ≈426 px. |
| 1024 × 700 | Sem transbordamento horizontal; alternância roteiro/chat; campo de mensagem visível entre y=542 e y=591; texto do editor preservado. |
| 390 × 844 | Menu móvel, catálogo, seleção, histórico e rascunhos dentro da largura; campo de mensagem visível; rodapé de seleção acessível. |

Listas longas continuam roláveis — agora em suas próprias áreas, mantendo busca e ações acessíveis. Não foi prometido eliminar toda rolagem.

Com respostas simuladas apenas no navegador:

- Dois testes selecionados continuam sendo exatamente os mesmos dois na revisão.
- Executar permanece desabilitado até o aceite; o botão de execução real não foi acionado.
- Arquivar exige confirmação; retira somente o teste escolhido da seleção; restaurar volta à base ativa.
- Um arquivado não pode ser marcado; seu botão de resultado permanece disponível.
- Retomar rascunho mantém IDs e ordem; estado vazio explica como criar o primeiro.

O comando direto do chat “adicione o CT004” também foi validado: marcou o teste sem chamada ao modelo. O seletor de modelos abre dentro da janela e fecha com Escape. Não foram usadas chaves nem realizadas chamadas pagas.

### Regressão

- **291 testes do frontend aprovados**, incluindo navegação, consentimento, chat, arquivos de relatório e os novos contratos de arquivamento.
- **36 testes focados do backend aprovados**, com SQLite isolado e sem navegação externa: arquivar/restaurar, conflitos de versão, isolamento entre bases, preservação histórica e bloqueio de planos arquivados.
- TypeScript, ESLint nos arquivos alterados, build e `git diff --check` aprovados.
- Ruff E9/F aprovado nos módulos Python alterados e verificação completa aprovada no teste novo. O lint amplo dos módulos antigos ainda aponta estilos preexistentes (C408, I001 e BLE001); não foi feita uma limpeza geral fora do escopo.
- Avisos existentes: depreciação do TestClient/AnyIO e classificação estática de rotas do Vinext. No navegador de QA, a única falha de recurso foi a descoberta de modelos bloqueada intencionalmente pelo bloqueio de POSTs reais.
- Conferência final na API real: nenhum teste arquivado e nenhum rascunho de demonstração persistido na base utilizada para validação.

### Capturas

Arquivos locais em `data/mcp-validation/`, ignorados pelo Git. Capturas de rascunho preenchido e arquivados usam respostas simuladas, não registros criados na aplicação.

- `ux-meus-testes-1366.png` / `ux-meus-testes-390.png`
- `ux-catalogo-1366.png` / `ux-catalogo-1024.png` / `ux-catalogo-390.png`
- `ux-preparar-1920.png` / `ux-preparar-1366.png`
- `ux-chat-1024.png` / `ux-chat-390.png`
- `ux-criar-1366.png` / `ux-revisao-1366.png`
- `ux-execucoes-1366.png` / `ux-execucoes-1024.png` / `ux-execucoes-390.png`
- `ux-rascunhos-1366.png` / `ux-rascunhos-vazio-1366.png` / `ux-rascunhos-vazio-390.png`
- `ux-arquivados-1366.png`
