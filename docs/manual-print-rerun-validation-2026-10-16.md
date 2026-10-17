# Aguardar print na reexecução — 16/10/2026

> Registro histórico da primeira implementação de captura do navegador. O fluxo atual foi substituído pelo [envio de imagem escolhida pelo usuário](user-image-print-upload-validation-2026-10-16.md), com Ctrl+V, arrastar/soltar e seleção de arquivo.

## Comportamento entregue

- “Aguardar print” deixa de ser ignorado na reexecução conectada à aplicação.
- O relatório encaminha apenas um plano limitado de capturas salvo no CT escolhido. IDs, posições e ordem são os únicos dados enviados; consultas, integrações e credenciais não são aceitas.
- O usuário confirma o plano na aplicação, prepara o navegador e autoriza a ação original. Cada pausa exige um novo clique em **Capturar print e continuar** na aplicação AutoJev. O navegador mostra apenas a orientação da pausa; a página testada não recebe uma função capaz de confirmar capturas.
- O print é evidência associada ao CT da nova execução, nunca uma conferência aprovada. O resultado e o HTML originais permanecem preservados.
- SWS, SQL e o antigo card de banco continuam sem execução. Marcar “Aguardar print” em SWS/SQL habilita somente a pausa para captura, não a integração.
- Alterações não guardadas no editor modal bloqueiam o encaminhamento; o usuário precisa guardar o rascunho. A edição entre os passos continua sendo guardada automaticamente nesta aba.

## Posições, limites e interrupções

As posições são validadas contra a linha do tempo original. Pausas anteriores à ação ocorrem antes dela e são seguidas de uma nova validação dos pré-requisitos. As demais acontecem após a ação e seu lote de conferências locais, sem interromper cada conferência individual. Essa limitação é explicada na confirmação. A posição planejada permanece nos metadados; o horário real estabelece a cronologia das evidências.

Cada pausa tem até 15 minutos, com limite acumulado de uma hora. As esperas manuais não consomem o orçamento normal de preparação/operação. Sem confirmação não há captura ou continuação automática. Expiração, fechamento do navegador, falha da captura ou cancelamento preservam a ação e as conferências já registradas e indicam a coleta incompleta.

O POST de captura exige confirmação literal, identificador e versão do checkpoint atual, com validação da origem da aplicação. Uma mudança de fase síncrona rejeita cliques duplicados ou antigos. O botão genérico de continuar não ultrapassa uma pausa de captura. O painel restaura a orientação após navegação, sem binding de confirmação exposto à página testada. O relatório não faz POST nem executa a ação diretamente.

## Validação

- Testes unitários e de API: planos inválidos/duplicados/fora da linha do tempo, campos extras, origem não autorizada, nonce antigo, clique duplicado, espera antes/depois da ação, múltiplos prints, retomada genérica, cancelamento, expiração, fechamento e falha de captura.
- Chromium com site exclusivamente local e banco isolado: login manual, pausa antes/depois da ação, espera efetiva até confirmação pelo serviço usado pela aplicação, PNG salvo e associado ao CT001, imagem incorporada ao novo relatório, ação original executada uma única vez, conferências locais preservadas e SHA-256 do relatório original inalterado. Nenhuma chamada de modelo. A API e a interface verificam separadamente o clique e o POST com consentimento e checkpoint atuais.
- Interface: plano seguro no hash, consentimento obrigatório, entrada malformada bloqueada, estados aguardando/salvando, botão de captura, progresso limitado enquanto há espera e rascunho não salvo não descartado silenciosamente.
- Relatório: plano por CT, apenas capturas permitidas no encaminhamento, SWS/SQL bloqueados, exportação offline sem conexão/runtime/transientes e print manual apresentado como evidência, não PASS.
- Build de produção e testes de interface aprovados. Servidor local reiniciado somente após confirmar que não havia execuções ativas.
- Rodada final com confirmação exclusiva pela aplicação: 211 testes de reexecução/API/Chromium/políticas/receitas, mais 63 de login/relatório, e 132 de interface aprovados. O guard final de orçamento também passou nos 75 testes do serviço. As demais regressões relacionadas de relatório/linha do tempo passaram em rodada anterior.
- Conferência visual do consentimento com uma pausa planejada após a etapa 3 no CT001 existente: página consultada sem confirmar/iniciar reexecução externa.

Para usar a apresentação atualizada, reabra o relatório pela aplicação, adicione/guarde a pausa e confirme uma nova reexecução. Uma aba HTML já aberta ou arquivo já baixado mantém o código antigo.

Não foram reexecutados CTs no SauceDemo nem consumidos créditos de provedores nesta validação.
