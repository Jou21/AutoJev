# Validação das restrições de cobertura — 6 de outubro de 2026

## Resultado e escopo

As duas execuções originalmente apontadas com **14 restrições** usaram o
perfil público `problem_user` do SauceDemo. Portanto, não eram execuções de um
fluxo saudável: esse usuário apresenta comportamentos defeituosos que também
podem impedir a preparação de outros testes.

Após os ajustes de retomada, a execução final em texto terminou com **2
restrições reais de dependência**, sem lacunas ativas de restauração. A
execução visual terminou com o mesmo resultado local, usando a mesma fonte e
perfil. Nenhuma das duas apresentou falha de execução da automação.

Baseline preservado, sem alterar vereditos históricos:

- Texto: `39ce62a2-7b4c-4016-ac0e-3b002b6f2c69`.
- Visual: `53471525-5236-4be2-885c-9bbaa54fc4c8`.
- As 14 restrições incluíam seis preparações de validações obrigatórias,
  dois retornos Continue Shopping, quatro controles de menu no carrinho e
  dois Open Menu no checkout.

## O que causava as lacunas

O explorador confundia duas coisas: uma ação reprovar uma regra de negócio e
a página realmente aberta poder servir como caminho para testar outros
controles. No `problem_user`, um cartão pode abrir o produto errado. O FAIL de
identidade está correto; descartar completamente o destino real, porém,
impedia retomar controles daquela página.

Agora esse destino só pode ensinar uma rota de recuperação quando há clique
nativo bem-sucedido, cartão observado sem ambiguidade, falha local de
identidade e destino de detalhe realmente observado na mesma origem. Não há
URL inventada, alteração de veredito, autenticação contornada ou formulário
submetido por esse atalho. Replay e tentativas continuam limitados.

Os ajustes de retomada também preservam pré-requisitos de carrinho em todo o
caminho, distinguem Cart de Cancel, exercitam menus antes de abandonar uma
rota e não ressuscitam preenchimentos já concluídos como trabalho pendente.

Um diagnóstico intermediário (`0078c3e0-a902-4a09-98b0-663efc71651e`)
expôs 34 lacunas de rota e duas dependências mascaradas. Depois da correção do
destino de produto incorreto, as 34 lacunas de rota deixaram de existir no
resultado final. Uma dívida temporária de Cart no run final foi resolvida
pela execução nativa da etapa 149, com `cart_persistence` aprovado e captura
`151-click.png`; não foi apagada administrativamente.

## Execuções reais, localhost e OpenRouter

Configuração: `typesafe/jev-1.13` e `google/gemini-2.5-flash-lite`, perfil
`problem_user`. Chaves ficam no backend; nenhum segredo faz parte deste
documento. As execuções de diagnóstico têm watchdog próprio de 600 segundos
e teto de US$ 0,08 sobre o custo informado, sem alterar os limites normais do
produto.

| Medição | Econômico · texto | Visual · texto + imagem |
| --- | ---: | ---: |
| Execução | `10b6c8e0-ec2a-419c-8f1d-f3920e10845b` | `e3415a8e-5b2c-4ea8-8dbd-dbbd87ff4351` |
| Ações do explorador | 239 | 239 |
| CTs | 132 | 132 |
| PASS / FAIL / BLOCKED | 79 / 51 / 2 | 79 / 51 / 2 |
| Restrições ativas de retomada | 0 | 0 |
| Dependências mascaradas | 2 | 2 |
| Tokens totais | 258.595 | 411.363 |
| Custo informado | US$ 0,031778182 | US$ 0,045401082 |
| Consultas ao JEV | 1 | 1 |
| Revisões auxiliares válidas | 112 | 111 |
| CTs com análise visual válida | Não solicitada | 111 de 132 |

Valores monetários são os registrados pela API local, não uma conciliação da
fatura do provedor. O par final registrou US$ 0,077179264. Incluindo o diagnóstico
intermediário de US$ 0,031137696, estas três execuções registraram
US$ 0,108316960, sem contabilizar execuções históricas anteriores.
No texto, o limite de 120 requisições auxiliares foi
alcançado: 112 CTs receberam revisão e 20 mantiveram evidência local. Revisões
incrementais podem consumir mais de uma chamada por CT. Ausência de revisão
auxiliar não apaga a verificação nativa nem representa aprovação visual.

No visual, 111 CTs receberam avaliação válida e 21 ficaram explicitamente
indisponíveis por `auxiliary_request_budget_exhausted`. Houve zero respostas
de rate limit e zero avaliações ausentes/inválidas contabilizadas. Portanto,
esses 21 casos **não são limitação do Gemini nem aprovação visual**: o limite
interno de 120 chamadas foi preservado. Nenhum indício visual adicional foi
registrado nessas avaliações; isso não prova que o site não tenha defeitos
visuais. As 51 falhas locais permanecem em ambos os modos.

## Por que restam dois BLOCKED

Os CTs `CT016` e `CT042` da execução em texto tentaram validar o CEP vazio,
respectivamente com carrinho vazio e preenchido. A tentativa ocorreu, mas o
campo Last Name não retém o valor e altera indevidamente First Name. A página
responde exclusivamente `Error: Last Name is required`.

Assim, não há como atribuir essa resposta à validação do CEP. O defeito de
preenchimento fica **FAIL** (`CT009` e `CT035`); os dois testes de CEP ficam
**BLOCKED**, com campo bloqueador, verificação e capturas antes/depois na
linha do tempo. Repetir indefinidamente ou modificar o estado interno do site
não estabeleceria um teste independente válido.

Uma contraprova nativa via Playwright MCP, em contextos novos do site real,
mostrou:

| Perfil público | Após preencher First Name / Last Name | Resposta ao CEP vazio |
| --- | --- | --- |
| `standard_user` | `AutoJev` / `QA`, ambos preservados | `Error: Postal Code is required` |
| `problem_user` | First Name vira `QA`; Last Name fica vazio | `Error: Last Name is required` |

No mesmo controle saudável, preencher o CEP permitiu clicar Continue e Finish
e observar a confirmação real em `/checkout-complete.html`. Isso valida o
oráculo e a diferença entre os perfis, **não** equivale a uma execução completa
do explorador com `standard_user`.

Os 51 FAILs locais do texto não significam 51 bugs distintos. A classificação
das verificações falhas foi: 22 detalhes com identidade/preço incorretos, 12
adições/remoções sem efeito, 12 verificações de persistência ligadas ao item
fantasma `ITEM NOT FOUND`, três ordenações e dois preenchimentos. A mesma
causa pode reprovar vários contextos; não há falha de execução da automação
nessa execução.

Alguns títulos enriquecidos descrevem o produto errado que abriu, em vez do
produto pretendido. Nessas ocorrências, a intenção, o esperado/observado e o
FAIL são preservados nas asserções locais; o título sozinho não deve servir
para agrupar defeitos ou interpretar a cobertura.

## Verificação independente no navegador

O Playwright MCP conferiu o localhost e controles nativos do SauceDemo:

- Produto incorreto abre um detalhe real; Menu, Dynamic Catalog, Reset,
  Close Menu e Back to Products ainda funcionam e podem ser explorados.
- CT de sobrenome permanece FAIL; CT de CEP permanece BLOCKED com duas
  capturas e a resposta real do campo bloqueador.
- Interface e HTML exportado preservam a distinção entre cobertura,
  verificação de negócio, interação técnica e dependência não validada.
- Capturas verificadas carregam sem imagens quebradas; não houve overflow
  horizontal no desktop inspecionado.

Artefatos locais ignorados pelo Git, em `data/mcp-validation/`:

- `metrics-10b6c8e0-ec2a-419c-8f1d-f3920e10845b.json`.
- `metrics-e3415a8e-5b2c-4ea8-8dbd-dbbd87ff4351.json`.
- `final-gemini-text-coverage.png`.
- `final-gemini-visual-coverage.png`.
- `final-standard_user-postal-oracle.png`.
- `final-problem_user-postal-oracle.png`.
- `final-standard-checkout-complete-oracle.png`.

## Regressões automatizadas

- 754 testes backend fora da integração passaram.
- Oito testes de integração passaram, incluindo a matriz saudável/defeituosa
  nos modos texto e visual; o oráculo de Cancel foi fortalecido para exigir
  especificamente `checkout_form_cancel_progress`, além da rota observada.
- 20 testes frontend passaram, incluindo build; lint passou.

Na fixture saudável, ambos os modos executaram 177 ações e 108 CTs, com 108
PASS, zero lacunas e compra concluída com carrinho vazio e preenchido. Na
fixture com defeitos injetados, cada modo teve 77 CTs: 68 PASS, cinco FAIL e
quatro BLOCKED, continuando depois das falhas e sem trabalho pendente.

A repetição final com o oráculo de Cancel fortalecido passou em texto
(137,85 s) e visual (148,16 s). Nos dois, CT020/etapa 23 verifica Cancel com
carrinho vazio e CT042/etapa 57 verifica Cancel com Backpack, quantidade 1.
Ambos exigem progresso nativo e persistência do conteúdo, não apenas um clique
no Cart do cabeçalho. O visual controlado teve 108/108 avaliações válidas.

Essas fixtures usam Chromium real, API e banco isolados, mas **LLMs simuladas**:
provam a integração e os vereditos locais, não a precisão visual do Gemini.
O limite auxiliar de 200 requisições existe somente na fixture expandida;
produção mantém 120 chamadas para proteger custo. Uma avaliação visual
indisponível deve continuar explícita, nunca virar aprovação.

## O que não está sendo afirmado

Fronteira observada esgotada não significa todos os valores de entrada,
combinações de estados, perfis de usuário, viewports ou comportamentos
temporais possíveis. O resultado é condicionado ao escopo executado e às
regras verificadas. Para testar isoladamente o CEP no `problem_user`, o defeito
do sobrenome precisa deixar de bloquear o formulário; trocar de perfil é uma
contraprova, não uma correção silenciosa do teste original.

Nenhum relatório histórico foi reescrito. Nenhuma publicação no Sites ou push
Git faz parte desta validação.
