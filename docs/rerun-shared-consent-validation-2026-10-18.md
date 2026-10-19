# Autorização e mensagens iguais nas duas telas da reexecução

Nota: a exigência de um segundo clique depois do login foi posteriormente substituída pela [retomada automática após login](rerun-native-login-autoresume-validation-2026-10-18.md). Abaixo está o registro da etapa anterior de sincronização.

## Comportamento

- Marcar ou desmarcar a autorização na aplicação ou no painel flutuante atualiza a outra tela automaticamente.
- A seleção é temporária e pertence somente à execução e à versão de preparação atuais. Não é salva no navegador, nos eventos ou no relatório.
- Uma seleção não inicia login, captura ou ação. Ainda é necessário clicar explicitamente em **Continuar reexecução**.
- O botão fica bloqueado enquanto a seleção é sincronizada ou quando há conflito. Respostas antigas não podem restaurar uma autorização de outra etapa.
- Uma nova preparação, validação, cancelamento ou reinício limpa a seleção anterior.
- Título, orientação, autorização, botão, detalhes e aviso usam a mesma apresentação canônica nas duas telas. As instruções de diagnóstico também usam o nome correto do botão.

## Verificação

- 254 testes backend direcionados passaram no conjunto amplo, incluindo serviço/API de reexecução e painel isolado. Após centralizar também as instruções de diagnóstico, 132 testes relevantes foram novamente executados sem falhas.
- 99 testes de interface passaram, incluindo sete regressões da tela principal e vinte do painel. Os testes comparam os textos de fallback com a mesma função canônica Python.
- Seleção remota habilita a continuação sem exigir uma segunda marcação; desmarcar remotamente volta a bloqueá-la.
- Cobertos conflitos de revisão, polling antigo, troca de execução/versão, validação e falha de sincronização, sem envio automático de formulário.
- O painel só aceita alteração de autorização pelo iframe registrado. Comandos da página testada, origem incorreta, versão antiga e tipos inválidos são rejeitados.
- Build de produção, TypeScript, ESLint e `git diff --check` passaram.

Nenhum CT do usuário foi iniciado pelos testes. A atualização foi limitada ao aplicativo local; não houve publicação externa. A execução que aguardava já estava encerrada na conferência anterior à atualização, sem necessidade de cancelamento adicional.
