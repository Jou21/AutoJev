# Reexecução individual de CT — 14/10/2026

## Fluxo entregue

1. Abra o relatório pela aplicação, entre na Jornada observada e escolha um CT.
2. Em “Editar passos”, clique em “Reexecutar este teste”. Uma nova aba apresenta
   a disponibilidade, os efeitos e a confirmação; abrir essa tela não inicia nada.
3. Após confirmar, uma execução separada abre um navegador. Prepare manualmente
   login, carrinho, formulário e demais pré-condições da página original.
4. Confirme a preparação e continue. O executor confere novamente a origem,
   a URL, os controles e o estado observado antes de repetir a ação uma única vez.
5. A tentativa guarda suas próprias evidências, conferências e relatório.
   O resultado original não é sobrescrito.

O editor continua aceitando cards visuais de “Consulta em banco de dados” por
arrastar e soltar. Esses rascunhos não são enviados para execução: não há SQL,
conexão ou acesso ao banco da aplicação testada.

## Limites explícitos

- Novas explorações capturam uma receita privada antes de cada ação elegível.
  CTs antigos sem receita apresentam uma explicação; não se tenta adivinhar
  controles nem iniciar outra exploração como se fosse reprodução fiel.
- Não há restauração automática de login, carrinho ou dados. A preparação é
  manual, com prazo absoluto de 300 segundos. Página/estado divergente ou
  controle ausente/ambíguo mantêm a preparação aberta, sem despacho, para
  correção e nova confirmação. Ações sensíveis continuam bloqueadas.
- Esta versão repete a ação e as conferências locais salvas, sem chamar JEV ou
  LLM auxiliar. A análise visual anterior não é refeita. Se uma conferência
  original não for reproduzida, a tentativa fica parcial/bloqueada, nunca
  aprovada por falta de evidência. Uma falha local real permanece reprovada.
- CTs com várias ações ou sem conferências locais não são elegíveis nesta versão.
- O HTML baixado não conserva uma conexão com o backend. Para iniciar uma
  reexecução, reabra o relatório pela aplicação. As receitas privadas não são
  exportadas na API nem no relatório.
- Cancelamento, fechamento do navegador, tempo excedido e reinício do backend
  não provocam um segundo despacho automático. Efeito de ação interrompida
  depois do início é tratado como não confirmado.

## Validação

A habilidade Browser foi consultada e a conexão integrada estava indisponível;
a conferência visual/interativa foi feita pelo MCP do Playwright. Foram usados
um site de fixture local, uma base isolada e nenhum token de provedor.

- Caminho real: relatório conectado → Jornada → CT → editor → reexecução →
  confirmação → navegador preparado → ação → novo relatório.
- A abertura da confirmação fez somente consulta: uma origem, nenhuma tentativa
  nova. Botão desabilitado antes de marcar o consentimento.
- Antes de continuar, a tentativa aguardava intervenção com zero ações.
- Tentativa final `b509a222-8d81-4db8-ad47-8757ff3b2fa1`, origem
  `c737da2d-a95f-434d-a10e-257b95731964`: exatamente uma ação, um CT aprovado,
  `selection_matches` e `no_new_browser_errors` aprovadas, zero tokens/custo.
- Relatório da origem manteve o SHA-256
  `00b9885ec2faf97c95eed0f7e04bc35783026eb4a1864132f10f40ce6d8043be`.
  O relatório histórico do usuário `62dbcc08-8156-411c-873c-d2632e740690`
  também permaneceu intacto; a consulta de disponibilidade explicou a falta
  de receita sem executar nenhuma ação no site externo.
- O download real do relatório da tentativa foi conferido: o HTML não mantém
  o bloco de conexão com a aplicação nem o botão de baixar novamente.
- Testes de estado diferente confirmaram bloqueio sem despacho. A fixture
  recriada com a versão final confirmou portabilidade entre sessões novas.
- Indicadores da tela não marcam ações como concluídas em cancelamentos ou
  bloqueios sem execução. Relatório só fica concluído após existir o arquivo;
  espera de atualização tem limite de 120 segundos mesmo com API indisponível.

Regressão: 207 testes backend diretamente relacionados aprovados; outro lote
Chromium/coverage de 218 testes aprovado (há sobreposição entre os lotes).
102 testes frontend, build, TypeScript e lint dos arquivos frontend alterados
aprovados. Permanece o aviso conhecido de Starlette/AnyIO.

Capturas em `data/mcp-validation/`: `rerun-confirmation.png`,
`rerun-preparation.png` e `rerun-result-final.png`.
HTML exportado na conferência: `rerun-report-offline.html`. Serviços de fixture
e backend isolado foram encerrados; a aplicação normal segue em 3000/8000.

## Correção de preparação prematura — 14/10/2026

A tentativa do usuário `de6a3514-6879-471f-96b0-9a666d7813a7` esperava
`https://www.saucedemo.com/inventory.html`, mas a captura mostrou a tela de login
em `https://www.saucedemo.com/`. Nenhuma ação foi despachada. O fluxo anterior
encerrava a tentativa ao receber confirmação com a página ainda divergente.

O fluxo agora mantém o navegador aberto para corrigir login/página/estado.
Cada confirmação faz uma leitura nova e aplica as mesmas pré-condições estritas.
Corrigir a página por si só não inicia o CT. Uma rejeição incrementa a versão da
preparação e invalida o consentimento anterior na interface; é obrigatória uma
nova confirmação. O prazo de preparação não é reiniciado a cada rejeição.

A interface mostra página esperada × página aberta, motivo humano e instruções.
Bloqueios históricos antes do despacho são avisos “Reexecução não iniciada”, sem
código bruto ou check verde. Tentativas já encerradas continuam preservadas:
para tentar novamente, abra a execução original e inicie outra reexecução.

Validação adicional:

- 58 testes focados de serviço/API/navegador aprovados; lote ampliado relacionado
  com 157 testes aprovado (há sobreposição).
- Regressão real Chromium, site local protegido por login: confirmação prematura
  → espera com zero ações e navegador aberto → login pela interface → continua
  sem executar automaticamente → segunda confirmação → uma ação/CT aprovado.
- 105 testes frontend, build, TypeScript e lint dos arquivos alterados aprovados.
- MCP Playwright: aviso histórico conferido na execução do usuário, somente
  leitura. Preparação versão 1→2 conferida com resposta de API de fixture
  isolada no navegador: consentimento desmarcado e botão desabilitado após
  rejeição. Nenhum POST de execução feito nessa conferência visual.
- Relatórios original e da tentativa mantiveram seus SHA-256. Nenhuma reexecução
  em produção nem chamada aos modelos foi iniciada para validar a correção.

Capturas: `rerun-login-block-explained.png` e
`rerun-preparation-retry-guide.png`, em `data/mcp-validation/`.
