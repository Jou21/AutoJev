# Jornada em S e editor visual — 13/10/2026

Atualização: a reexecução real individual foi implementada em 14/10. Consulte
[a validação e os limites atuais](ct-rerun-validation-2026-10-14.md). As limitações
descritas abaixo registram o estado da entrega de 13/10.

A apresentação em S também foi substituída por uma lista vertical em 14/10,
conforme nova preferência do usuário. Veja a
[validação da lista](journey-list-validation-2026-10-14.md).

## Entregue nesta etapa

- Mapa em tabuleiro serpenteado, com numeração e conexões entre páginas na
  ordem da primeira visita. URLs com variações de consulta continuam distintas.
  Retornos e repetições são mantidos na sequência real, recolhida abaixo.
- Cada casa abre todos os CTs da página, com busca e opções de ver resultado
  ou editar o roteiro. O editor também está acessível em Resultados.
- Etapas registradas são somente leitura. Cards de “Consulta em banco de dados”
  podem ser inseridos por arrastar e soltar ou por botão, movidos por teclado
  (Alt + setas) ou botões, e removidos sem alterar os registros originais.
- Cards extras são rascunhos visuais não executados, sem SQL, conexão ou
  credenciais. Nenhum CT novo ou aprovado é criado por inserir um card.
- Rascunhos são guardados por CT nesta aba e incluídos no HTML baixado após
  guardar. Não há gravação em banco, armazenamento do navegador ou alteração
  automática de arquivos HTML já baixados.

## Limite explícito da reexecução

A reexecução real não está implementada nesta etapa. O controle
“Ver disponibilidade de reexecução” informa que nada foi iniciado.
Foi solicitada uma confirmação de escopo ao usuário, pois “só o visual neste
momento” pode referir-se somente ao card de banco ou ao fluxo inteiro.

O backend ainda não oferece reexecução de CT. Relatórios históricos guardam
referências voláteis de elementos e evidências, não uma receita completa do
estado anterior, autenticação e preparação. Uma implementação futura precisa
preservar receitas antes das ações, resolver os controles no navegador atual,
verificar pré-condições e produzir um resultado separado do histórico.
Uma nova exploração com uma ação não é tratada como reprodução fiel de um CT.

## Verificação

Habilidade Browser consultada; nenhum navegador integrado disponível. A
validação visual/interativa usou o MCP do Playwright com a execução salva
`62dbcc08-8156-411c-873c-d2632e740690`, sem repetir testes do site ou chamar modelos.

- Desktop 1440 × 1000: 14 casas, quatro colunas em S, 13 conexões.
- Seletor da primeira página: todos os 29 CTs disponíveis; busca por CT001
  retorna somente esse caso. Resultados originais dos 151 CTs preservados.
- Arrastar o card de banco para depois de “Selecionar opção” insere exatamente
  um rascunho no intervalo correto, mantendo os seis registros do CT001.
- Movimento com Alt + seta e botões, salvamento por CT e reabertura do HTML
  offline preservam o rascunho. Exportação não copia modal aberto ou filtros.
- Celular 390 × 844: percurso em uma coluna, editor com áreas de rolagem
  próprias e sem vazamento horizontal. Temas claro e escuro conferidos.
- Editor oculto na impressão; navegação e CTs originais continuam disponíveis.
- Nenhuma requisição HTTP/HTTPS ou erro JavaScript durante a edição offline.
- Reexecução permanece explicitamente não iniciada; nenhum acesso a banco.

Regressão: 375 testes backend relacionados e 95 testes frontend aprovados;
build concluído. Permanecem os avisos conhecidos de Starlette/AnyIO e
classificação de rota do vinext. `git diff --check` limpo.

Capturas em `data/mcp-validation/`: `journey-board-desktop.png`,
`journey-board-light.png`, `journey-editor-final-desktop.png` e
`journey-editor-mobile.png`. Rascunho exportado: `journey-draft-offline.html`.
O artefato original da execução não foi sobrescrito.
