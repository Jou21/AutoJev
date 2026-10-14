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

## Refinamento: captura anterior com destaque do alvo da ação

Os passos de clique, seleção, preenchimento, tecla e passagem do cursor mostram
a captura anterior à interação, com uma moldura vermelha, ponto central e legenda
correspondente à ação. O destaque acompanha o redimensionamento e a ampliação.
É uma camada de apresentação: os pixels da evidência original e seu download
continuam intactos, sem injetar um marcador na aplicação testada.

Nas novas execuções, o executor registra a caixa do controle e uma captura do
viewport em pixels CSS, depois de trazer o alvo para a área visível. Mudança de
posição, URL, viewport ou geometria inválida impede um destaque potencialmente
enganoso, sem reprovar a ação por uma falha opcional de captura. Campos sensíveis
continuam mascarados; valores digitados e credenciais não entram nos metadados.
A prévia é evidência local, não uma imagem adicional enviada à LLM.

Relatórios históricos podem reutilizar somente uma captura anterior confiável,
da mesma página e do mesmo CT. Como não registraram a posição do alvo, exibem o
aviso **Posição do alvo não registrada nesta execução**, sem inventar coordenadas.
A captura que pertence à ação não gera outra observação sem horário na timeline.

Validação pelo Playwright MCP e Chromium real, sem modelos nem rede externa:

- Fixture local com seleção de menor preço e clique para adicionar um produto;
  efeitos observáveis e geometria preservados até o HTML independente.
- Desktop 1440 × 900, ampliação e mobile 390 × 844: diferença de alinhamento
  inferior a 0,02 pixel CSS; sem vazamento horizontal no celular.
- Abrir uma captura comum depois da ação remove o marcador anterior do modal.
- Download de imagem mantém o arquivo original, sem desenhar sobre a evidência.
- Impressão prepara os destaques inclusive nos CTs ainda não visitados, carrega
  suas imagens e restaura a seleção anterior ao terminar.
- HTML baixado foi reaberto como arquivo local: marcador e navegação funcionaram,
  sem reaparecer o botão **Baixar relatório**. Nenhum erro de JavaScript registrado.
- O relatório histórico foi reapresentado com captura no passo, sem marcador;
  seu artefato original manteve o SHA-256 registrado nesta documentação.

Capturas: `data/mcp-validation/action-preview-click.png`, `action-preview-zoom.png`,
`action-preview-mobile.png`, `action-preview-historical.png` e
`action-preview-offline.png`. HTML de conferência:
`data/mcp-validation/action-preview-offline-verified.html`.

Regressão final: 185 testes backend aprovados (captura, integração Chromium,
timeline, relatórios, localização, API e documentação). `npm test`: build aprovado
e 65 testes aprovados, incluindo 31 do explorador de passos. A regressão adicional
do executor/negócio/cobertura também passou com 257 testes. Um aviso de depreciação
Starlette/AnyIO, sem falha. Validação sintática e `git diff --check` sem erros.
Backend local reiniciado com as alterações; nenhuma publicação realizada.

## Refinamento em 09/10: opção escolhida nos dropdowns

Em novas execuções, o passo de seleção registra os rótulos visíveis da opção
solicitada, da seleção anterior e da seleção observada após a estabilização da
página. O destaque passa a dizer, por exemplo, **Selecionar: Menor preço**.
Um resumo junto à captura distingue a intenção do resultado, com os estados
**Seleção confirmada**, **Seleção diferente da solicitada** ou **Seleção não
confirmada**. A confirmação compara os valores reais no próprio navegador;
rótulos iguais de opções diferentes não bastam para confirmar a seleção.

Os metadados contêm somente rótulos limitados e redigidos, nunca os valores
brutos das opções. Controles sensíveis são excluídos. A leitura é opcional e
não interfere na ação, nas verificações de negócio ou no resultado do CT.
Navegação, substituição do controle e falhas de leitura não produzem uma
confirmação inventada. O menu nativo não é simulado sobre a imagem: a captura
original permanece anterior à ação, fechada e intacta. Relatórios antigos não
ganham rótulos que a execução não registrou.

Conferência pelo Chromium real e Playwright MCP, inteiramente local e sem IA:

- Seleção de **Menor preço**, antes **Nome (A a Z)**, confirmada na captura,
  no resumo e na ampliação.
- Falha proposital no site da fixture, que desfaz a escolha: o resumo mostrou
  a opção realmente mantida; a verificação `selection_matches` falhou e o CT
  continuou **REPROVADO**, sem confundir sucesso da automação com sucesso de negócio.
- Desktop 1440 × 900 e celular 390 × 844, sem vazamento horizontal; rótulo
  longo no zoom permaneceu dentro dos limites da imagem.
- HTML baixado reaberto offline conservou opção, resumo e destaque, sem botão
  redundante de download e sem requisições HTTP/HTTPS ou erros de JavaScript.
- Preparação de impressão mostrou os rótulos e destaques inclusive em CTs não
  visitados, restaurando o passo selecionado ao terminar.
- As integrações verificaram que os PNGs e os dados originais não foram alterados;
  os novos metadados locais não entram no payload auxiliar da LLM.

Capturas: `data/mcp-validation/dropdown-option-desktop.png`,
`dropdown-option-mobile.png` e `dropdown-option-mismatch.png`.
Arquivo offline: `data/mcp-validation/dropdown-option-offline-20261009.html`.

Regressão: 355 testes backend aprovados em três suítes (231 de captura/relatório/API,
122 de negócio e controle obsoleto, 2 integrações Chromium). `npm test` com build
aprovado e 81 testes frontend. Um aviso de depreciação Starlette/AnyIO, sem falha.
Validação sintática e `git diff --check` sem erros. Backend local atualizado;
nenhuma chamada a modelo ou publicação foi necessária.

## Refinamento em 09/10: comparação didática da opção selecionada

A verificação **Opção selecionada** agora apresenta dois cards: **Opção esperada**
e **Opção encontrada**, com os nomes registrados no navegador. Quando a verificação
passa, ambos ficam verdes com check. Quando falha, o resultado encontrado recebe
destaque vermelho e uma explicação em português. O JSON original fica recolhido
em **Ver dados técnicos**, acessível também pelo teclado.

As cores seguem o resultado real de `selection_matches`, não a igualdade visual
dos textos. Rótulos iguais com valores internos diferentes continuam reprovados.
Os nomes são vinculados somente à ação anterior confiável do mesmo CT, página e
tentativa. Registros históricos sem esses metadados recebem uma explicação simples,
sem inventar nomes. Captura e verificação divergentes não são tratadas como o mesmo
instante. Os dados originais e os resultados dos CTs permanecem intactos.

Validação local pelo Chromium real e Playwright MCP, usando a habilidade de
navegador (o navegador integrado estava indisponível):

- Casos aprovado e reprovado, com os nomes esperados e encontrados corretos.
- Desktop 1440 × 900, celular 390 × 844 sem vazamento horizontal, temas claro e escuro.
- Expansão e recolhimento dos dados técnicos pelo teclado; JSON original preservado.
- HTML baixado reaberto offline, sem requisições HTTP/HTTPS, erros de JavaScript
  ou botão redundante de download.
- Impressão mantém os cards e restaura o passo selecionado e o recolhimento técnico.
- A execução real de 151 CTs foi reapresentada pela API. Quando o nome posterior
  não foi capturado, mas a verificação confirma explicitamente o valor solicitado
  na mesma ação, os dois cards exibem **Name (Z to A)**. A legenda **Opção solicitada
  confirmada no navegador** distingue essa confirmação da recaptura do rótulo.
  Falhas, resultados indefinidos, vínculos ambíguos e dados contraditórios não
  reutilizam o nome solicitado como resultado. O HTML original manteve o mesmo SHA-256.

Capturas: `data/mcp-validation/selection-verification-pass.png`,
`selection-verification-mismatch.png`, `selection-verification-mobile.png` e
`selection-verification-light.png`. Arquivo offline:
`data/mcp-validation/selection-verification-offline-20261009.html`.

Regressão: 208 testes backend de relatório, associação de evidências, localização
e API, mais 2 integrações com Chromium real. `npm test`: build e 81 testes frontend
aprovados. Um aviso de depreciação Starlette/AnyIO, sem falha. `git diff --check`
sem erros. Nenhuma chamada de IA ou publicação foi necessária.

Validação adicional da confirmação histórica: 224 testes backend aprovados e
conferência do CT001 real pelo Playwright MCP. Captura:
`data/mcp-validation/selection-verification-confirmed-label.png`.

## Refinamento em 09/10: comparação visual da ordenação dos produtos

A verificação **Ordenação dos produtos** apresenta duas fileiras de cards:
**Ordem esperada** acima e **Ordem encontrada** abaixo. Os produtos são numerados
e alinhados por posição, com o critério em português (nome ou preço, crescente
ou decrescente). Checks verdes mostram posições correspondentes; diferenças
na fileira encontrada recebem destaque vermelho. Produtos faltantes ou extras
mantêm suas posições, sem reorganizar ou completar a evidência. O JSON original
permanece recolhido em **Ver dados técnicos**.

O resultado registrado da verificação continua soberano. Produtos empatados
podem trocar de posição sem reprovar uma ordenação válida: esses cards ficam
verdes com contorno tracejado e indicação de empate. Em uma verificação reprovada,
um card verde significa apenas **Mesmo produto nesta posição**, não aprovação
da ordenação inteira. Dados ausentes ou malformados não geram nomes, preços ou
confirmações inventados. Preços observados não recebem uma moeda não registrada.

Validação local pela habilidade de navegador e Playwright MCP (o navegador
integrado estava indisponível):

- CT001 da execução real de 151 CTs: seis produtos em cada fileira, na ordem
  registrada, com as doze posições visíveis em desktop 1440 × 900, sem rolagem
  vertical adicional no painel do passo.
- Fixtures de ordem invertida e empate de preços, preservando respectivamente
  a reprovação e a aprovação, com explicações e cores adequadas.
- Celular 390 × 844 sem vazamento horizontal da página. As duas fileiras
  compartilham a mesma rolagem lateral, mantendo as posições alinhadas.
- Temas claro e escuro, foco visível e abertura dos dados técnicos pelo teclado.
- HTML baixado reaberto offline, sem botão redundante de download, requisições
  HTTP/HTTPS ou erros de JavaScript; impressão mostra ambas as fileiras e
  restaura o passo selecionado e os dados técnicos recolhidos ao terminar.
- A apresentação histórica foi regenerada pela API, sem repetir a exploração.
  O HTML original manteve o SHA-256
  `fbc1b6ea427cc3b0ef61e37575bc443b7849604d52da9172d926e4161f362db7`.

Capturas: `data/mcp-validation/product-sort-desktop.png`, `product-sort-light.png`,
`product-sort-mobile.png`, `product-sort-mismatch.png` e
`product-sort-equal-price.png`. Arquivo offline:
`data/mcp-validation/product-sort-offline-20261009.html`.

Regressão: 256 testes backend de relatório, evidências, localização e API,
incluindo 32 testes específicos da ordenação. `npm test`: build e 81 testes
frontend aprovados. Um aviso de depreciação Starlette/AnyIO, sem falha.
`git diff --check` sem erros. Backend local atualizado; nenhuma chamada de IA,
nova exploração ou publicação foi necessária.

### Refinamento: um indicador entre cada dupla de produtos

O título **Ordem encontrada** e a contagem de produtos ficam abaixo da fileira
inferior. Cada posição tem um único indicador central, ligado aos dois cards
por linhas verticais curtas. Checks verdes indicam o mesmo produto na posição;
divergências recebem um X vermelho, ausências recebem um traço vermelho,
empates válidos usam setas verdes e comparações sem confirmação ficam neutras.
Os ícones repetidos dentro dos cards foram removidos. Cada indicador também
tem um rótulo acessível com sua posição e significado.

Conferência pela habilidade de navegador e Playwright MCP: no CT001 real,
os seis indicadores estão centralizados entre seus respectivos cards, e o
rodapé está abaixo dos seis produtos encontrados. Em desktop 1440 × 900, a
comparação completa cabe no painel sem rolagem vertical adicional. No celular
390 × 844, cards e indicadores permanecem alinhados na rolagem lateral única,
sem vazamento horizontal da página. Casos de ordem invertida e empate de
preços mantiveram seus resultados originais. O arquivo baixado reaberto offline
preservou os seis pares, sem botão de download redundante, requisições HTTP/HTTPS
ou erros de JavaScript. A impressão também manteve os indicadores e o rodapé.

Capturas: `data/mcp-validation/product-sort-pairs-desktop.png`,
`product-sort-pairs-mobile.png` e `product-sort-pairs-mismatch.png`.
Arquivo offline: `data/mcp-validation/product-sort-pairs-offline-20261009.html`.
Regressão: 282 testes backend aprovados, incluindo 26 novos testes de estrutura,
estados, acessibilidade e preservação das evidências; build e 81 testes frontend
aprovados. Um aviso de depreciação Starlette/AnyIO, sem falha. Backend atualizado,
`git diff --check` limpo, sem nova exploração, chamadas de IA ou publicação.

## Refinamento em 09/10: novas falhas técnicas em linguagem visual

A verificação **Ausência de novas falhas técnicas da aplicação** agora apresenta
dois grupos: **Execução da aplicação** (console e erros de execução) e
**Requisições de rede** (HTTP e conexão). Cada grupo compara **Esperado** e
**Encontrado** com números grandes e um indicador central acessível. Pares
confirmados ficam verdes; contagens positivas em comparação com zero recebem
destaque vermelho. Em uma verificação reprovada com ocorrências, o outro canal
com zero pode continuar verde: esse check compara apenas suas contagens, não
aprova a verificação inteira. O resultado original é sempre preservado.

Contagens ausentes, booleanas, negativas ou de formato incompatível aparecem
como **Não informado**, nunca como zero. Resultados indefinidos e contradições
continuam explicitamente identificados. A expectativa reconhece o formato
atual e o legado conhecido, sem inventar metas para descrições desconhecidas.
O JSON, a mensagem e a descrição original ficam em **Ver dados técnicos**,
recolhidos inicialmente. O rodapé limita a interpretação à verificação e à
captura configurada: zero não promete ausência de todos os erros da aplicação,
e falha técnica não confirma, sozinha, um defeito funcional.

Validação pela habilidade de navegador e Playwright MCP, com o navegador
integrado indisponível:

- CT001 real de 151 CTs: os dois canais exibem 0 × 0 com checks verdes.
  Em desktop 1440 × 900, o componente cabe inteiro sem rolagem adicional
  no painel do passo; conectores centralizados entre os respectivos cards.
- Fixture reprovada com duas falhas de rede: 0 × 2 em vermelho; execução
  com 0 × 0 em verde, mantendo o CT e a verificação reprovados.
- Fixture sem confirmação e com contagem ausente: **Não informado**, sem
  aprovação inferida ou preenchimento artificial de zero.
- Celular 390 × 844 sem vazamento horizontal, canais empilhados com seus
  pares lado a lado; dados técnicos alcançáveis pela rolagem do painel.
- Temas claro e escuro, rótulos acessíveis e abertura/recolhimento pelo teclado.
- HTML baixado reaberto offline com os dois pares intactos, sem botão
  redundante de download, chamadas HTTP/HTTPS ou erros de JavaScript.
- Impressão conserva os quatro cards e restaura os dados técnicos recolhidos.
- HTML original da execução real manteve o SHA-256 já documentado, sem
  repetir a exploração nem alterar evidências ou resultados históricos.

Capturas: `data/mcp-validation/technical-check-desktop.png`,
`technical-check-mobile.png`, `technical-check-light.png`,
`technical-check-failure.png` e `technical-check-unknown-mobile.png`.
Arquivo offline: `data/mcp-validation/technical-check-offline-20261009.html`.

Regressão: 325 testes backend aprovados, incluindo 43 novos testes de apresentação
técnica; build e 81 testes frontend aprovados. Um aviso de depreciação
Starlette/AnyIO, sem falha. Backend local atualizado; `git diff --check` limpo.
Nenhuma chamada a modelos, nova exploração ou publicação foi necessária.

## 2026-10-13 — Resumo didático do CT e resultados por etapa

O resumo apresenta o resultado preservado do caso, a ação solicitada e uma
lista das conferências registradas, com explicações simples e indicadores
individuais. A justificativa do JEV, as probabilidades, a documentação e o
veredito detalhado permanecem disponíveis em uma seção inicialmente recolhida.
Uma revisão de imagens sem indícios não é apresentada como aprovação visual
completa. Resultados ausentes não são convertidos em aprovações.

A lateral mostra o tipo, a finalidade e o resultado de cada etapa:

- Conferência: aprovada, reprovada ou sem resultado registrado.
- Ação: concluída, não concluída ou sem confirmação de conclusão.
- Captura: evidência registrada em azul, nunca uma verificação aprovada.
- Sinal técnico: ocorrência para consulta, sem reprovação automática do CT.

Validação com a habilidade Browser (indisponível nesta sessão) e fallback para
Playwright MCP, usando a execução salva `62dbcc08-8156-411c-873c-d2632e740690`:

- CT001: resumo da seleção `Name (Z to A)` e três conferências aprovadas;
  seis etapas e resumo visíveis na lateral em 1440 × 900, sem rolagem extra.
- CT012: reprovação por indício visual preservada, apesar das verificações
  locais aprovadas. A etapa visual aparece reprovada de forma independente.
- Celular 390 × 844: cards empilhados e sem vazamento horizontal.
- Temas claro/escuro, navegação pelo teclado e impressão com restauração
  dos detalhes recolhidos após a impressão.
- HTML baixado reaberto offline, com os resultados por etapa e sem botão
  redundante de download, erros JavaScript ou requisições HTTP/HTTPS.
- HTML original preservado com o mesmo SHA-256 documentado anteriormente.

Capturas: `data/mcp-validation/ct-summary-desktop-final.png`,
`ct-summary-mobile-final.png` e `ct-summary-light.png`.
Arquivo offline: `data/mcp-validation/ct-summary-offline-20261013.html`.

Regressão específica: 336 testes backend e 86 testes frontend aprovados,
incluindo 55 novos casos de resumo/metadados e cinco novos testes da lateral.
Build aprovado; `git diff --check` limpo. A suíte backend completa foi
interrompida após 6min41s, com 616 testes aprovados e nenhuma falha reportada
até esse ponto; não se declara aprovação da suíte inteira. O aviso de
depreciação Starlette/AnyIO já existente permanece.

Nenhuma exploração foi repetida, nenhum resultado histórico foi alterado
e não houve chamadas pagas aos modelos. Para ver o novo layout, basta reabrir
o relatório pela aplicação; arquivos HTML já baixados não se atualizam sozinhos.
