# Relatório premium — validação local em 08/10/2026

## Entrega

O relatório HTML agora tem oito áreas: Visão geral, Testes, Resultados,
Jornada observada, Evidências, Linha do tempo, Regras e cobertura e
Configurações. O arquivo continua independente, sem dependências remotas.

- Painel com resultados reais, natureza das verificações, atividade e custos JEV/LLM/total.
- Tabela de CTs com busca, filtros de resultado/categoria/página, paginação e CSV.
- Detalhes de um CT com navegação anterior/seguinte e a linha do tempo original.
- Galeria sem repetir arquivos compartilhados; ampliação e download da captura original.
- Jornada reconstruída das URLs observadas, preservando retornos.
- Preferências temporárias de tema, densidade e exibição de evidências.
- Download do HTML completo, sem incorporar filtros temporários.
- Impressão de todos os casos, restaurando os filtros ao terminar/cancelar e respeitando
  as preferências de ocultar capturas, detalhes técnicos e observações.

Relatórios existentes são reapresentados ao clicar novamente em **Abrir relatório**.
Abas `blob:` já abertas não recebem atualizações. O cache considera versão, snapshot
e datas dos arquivos de apresentação, incluindo CSS e JavaScript. O artefato original
é preservado; a apresentação atualizada usa um arquivo separado.

## Integridade dos dados

Resultados, evidências e histórico persistidos não são alterados. Um cartão canônico
por CT mantém suas capturas e verificações. A galeria e a jornada reutilizam essas
imagens, sem novas cópias em base64 no arquivo. Cada captura compartilhada aponta
para todos os seus CTs.

O tempo de ação soma as ações registradas do CT, sem usar o horário de documentação.
Tempos desconhecidos aparecem como indisponíveis. O marco de encerramento usa o
mesmo fim de processamento da duração total, incluindo a geração do relatório.

A tabela de condições resume o resultado atual de cada CT. Verificações de
tentativas anteriores permanecem nas linhas do tempo. Contagem de CTs por URL não
é percentual de cobertura. O mapa não representa um teste end-to-end único nem
comprova todos os cenários possíveis. Vídeos não registrados não são oferecidos.

HTML/JSON escapam dados da aplicação, URLs sensíveis continuam redigidas e o CSV
protege valores que poderiam ser interpretados como fórmulas.

## Conferência pelo Playwright MCP

Execução reapresentada: `51a55534-db9c-47a1-a6ef-8a443c98c7b6`.
Nenhuma exploração nem chamada aos modelos foi iniciada para validar o layout.

- 150 CTs, 149 aprovados e 1 reprovado, preservados.
- Busca por CT033 e filtro de reprovação retornaram o mesmo caso.
- Paginação de 15 casos e seleção de 50 casos funcionaram.
- Filtro de categoria visual retornou 1 caso.
- Galeria com 296 capturas únicas; nenhuma referência sem origem ou imagem quebrada.
- Ampliação, fechamento por Escape e nome original no download conferidos.
- Linha do tempo com 150 inícios de CT e 2 marcos; filtro de reprovação retornou 1 evento.
- Configurações de tema/densidade/exibição não alteraram os CTs.
- Impressão expôs temporariamente as 150 linhas e 150 detalhes, restaurando o filtro de 1 caso.
- CSV filtrado continha somente CT033, em português.
- HTML baixado foi reaberto como arquivo local, com navegação, filtro, detalhes e
  captura ampliada funcionando; nenhuma requisição HTTP/HTTPS foi necessária.
- Tela de 390 × 844: documento com largura de 390 pixels; tabela rolável dentro
  de seu contêiner e modal de captura sem vazamento horizontal.
- Nenhum erro de console na sessão conferida.

O SHA-256 do relatório histórico original permaneceu:
`249b7394f3dd69b6d14751683e406fe981fe1564f0b0b11e04eebf41f16e1219`.

Capturas da validação local estão em `data/mcp-validation/premium-*.png` (artefatos
locais, fora do controle de versão).

## Testes automatizados

Suíte de regressão backend: 253 testes aprovados; um aviso de depreciação do
Starlette/AnyIO, sem falha. Inclui relatório, localização, apresentação premium,
API/cache, linhas do tempo, documentação, adapter, orçamento visual e exploração.

`npm test`: build aprovado e 34 testes frontend aprovados.
`node --check backend/autojev/adapters/report_assets/premium.js` e
`git diff --check` sem erros.

Tudo validado no localhost. Nenhuma publicação foi feita.

## Refinamento: resultado do CT em dois painéis

A tela **Resultado do teste** agora apresenta a lista de passos, verificações e
observações à esquerda e somente o registro selecionado à direita. O resumo do CT
fica acessível no primeiro item da lista. As capturas podem ser ampliadas sem sair
do passo. Anterior/próximo e as teclas de direção, Home e End navegam pelos registros.

A transformação acontece apenas na apresentação do CT aberto: os nós originais
da linha do tempo, seus IDs, resultados e imagens são movidos, não duplicados.
Links de evidência selecionam o passo correspondente. Cada CT preserva sua seleção
durante a navegação entre casos. A impressão continua mostrando todos os passos,
restaurando a seleção ao terminar; o HTML baixado mantém a apresentação independente.

Conferência adicional pelo Playwright MCP, sem novas explorações/chamadas de IA:

- Desktop 1440 × 900 e notebook 1366 × 768: documento limitado à altura da janela;
  lista e conteúdo com rolagem própria e sem cortes abaixo do cartão. O wrapper
  nativo `::details-content` recebeu o dimensionamento flex necessário.
- CT043, com 54 registros: End selecionou o último registro e rolou somente a lista.
- CT002: clique, navegação por teclado, anterior/próximo, captura ampliada e retorno
  de foco funcionaram; trocar de CT e voltar preservou o passo selecionado.
- Link para `timeline-4354303333-6` abriu o passo correto do CT033, mantendo FAIL.
- Impressão revelou todos os painéis temporariamente e restaurou o estado anterior.
- Tema claro, densidade compacta e ocultação de capturas continuaram funcionando.
- Mobile 390 × 844: largura do documento de 390 pixels e lista compacta de 190 pixels,
  acima do detalhe selecionado, sem vazamento horizontal.
- HTML baixado reaberto como arquivo local: seleção de passos, modal e galeria
  funcionaram sem requisições HTTP/HTTPS; 296 capturas únicas, nenhuma quebrada.
- Nenhum erro de console no relatório conferido; SHA-256 do artefato histórico
  permaneceu igual ao registrado acima.

Capturas: `data/mcp-validation/step-explorer-desktop.png`,
`step-explorer-1366.png` e `step-explorer-mobile.png`.

Regressão desta alteração: 97 testes backend aprovados (relatórios, localização,
apresentação premium, API, linhas do tempo e documentação); `npm test` com build
e 43 testes aprovados, incluindo nove testes do JavaScript real do explorador de
passos. Nenhuma dependência nova. Validações sintática e `git diff --check` limpas.

## Refinamento: resultado ocupando toda a área de conteúdo

A tela **Resultado do teste** agora preenche a área à direita do menu, sem margem
externa ou borda de cartão. O título da tela, o retorno à lista, a navegação entre
CTs, a impressão e o download ficam em uma faixa compacta. O cabeçalho do CT e os
dois painéis fazem parte da mesma superfície. A lista de passos tem largura limitada
a 340 pixels em telas grandes, deixando o restante para a evidência selecionada.

Os controles de exportação são movidos, não duplicados, e voltam ao cabeçalho
original nas demais áreas do relatório. A alteração é somente de apresentação;
resultados, imagens e a linha do tempo persistidos permanecem intactos.

Conferência adicional pelo Playwright MCP no mesmo relatório:

- 1440 × 900, 1366 × 768 e 2470 × 1295: conteúdo começa em x=220, y=0 e termina
  exatamente nas bordas direita e inferior da janela, sem rolagem externa vertical.
- Em 1366 × 768, o cabeçalho de navegação ocupa 61 pixels; o CT ocupa todo o
  restante. Lista e detalhe têm rolagem independente.
- 390 × 844: controles reorganizados em linhas, lista acima do detalhe e documento
  com largura de 390 pixels, sem vazamento horizontal.
- Seleção de um passo, ampliação de captura e retorno por Escape funcionaram.
- Alternar entre Visão geral e Resultados preservou o passo selecionado, com uma
  única instância dos botões Imprimir e Baixar relatório.
- Preparação de impressão revelou os registros; o término restaurou somente o
  passo anteriormente selecionado.
- Nenhum erro de console no relatório conferido. Nenhuma exploração ou chamada
  de IA foi iniciada e nenhuma publicação foi feita.

Capturas: `data/mcp-validation/full-canvas-desktop.png`, `full-canvas-step.png`,
`full-canvas-notebook.png` e `full-canvas-mobile.png`.

Regressão desta alteração: 98 testes backend aprovados (relatórios, localização,
apresentação premium, API, linhas do tempo e documentação); `npm test` com build e
46 testes aprovados, incluindo 12 testes do explorador de passos. Nenhuma
dependência nova.

## Refinamento: exportação sem botão redundante de download

O HTML exportado não contém mais o botão **Baixar relatório**. O controle permanece
no relatório aberto pela aplicação. A exportação usa uma cópia isolada da
apresentação original, antes de filtros e seleção de passos, retirando somente esse
botão. Impressão, CSV, download de capturas, navegação e evidências são preservados.
O arquivo exportado não cria outra cópia de exportação ao inicializar sem o botão.

Conferência pelo Playwright MCP: o arquivo
`data/mcp-validation/report-export-no-download.html` foi baixado pela interface
e reaberto como arquivo local. Não havia botão de download do relatório nem em
Resultados nem em Visão geral; o botão continuava no relatório da aplicação.
A impressão foi acionada, a seleção de passos e a ampliação de captura funcionaram,
os 150 CTs permaneceram presentes e nenhuma imagem canônica estava quebrada.
O CSV filtrado por CT033 também foi baixado normalmente pelo arquivo local.
Nenhum erro de JavaScript ou requisição HTTP/HTTPS foi registrado no arquivo local.
Captura: `data/mcp-validation/report-export-no-download.png`.

Regressão backend: 69 testes aprovados (relatório, apresentação premium e API).
`npm test`: build aprovado e 48 testes aprovados, incluindo 14 do explorador de
passos/exportação. Validação sintática e `git diff --check` sem erros.
Nenhuma exploração, chamada de IA ou publicação foi necessária para essa alteração.
