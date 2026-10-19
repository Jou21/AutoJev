# Reconhecimento da tela de login na reexecução

## Alteração

A preparação agora reconhece os sinais de login observados no navegador antes de classificar uma mudança de URL no mesmo site. O aviso passa a ser “Faça login para continuar”, com instrução direta para preencher os campos e confirmar a continuação, tanto na aplicação quanto no painel flutuante.

- A comparação de URLs fica em detalhes quando o login foi identificado.
- Uma página comum incorreta mantém o aviso de página diferente.
- Uma origem externa mantém o aviso de segurança e não recebe tentativa de login.
- Exibir ou atualizar o aviso não envia formulários. A confirmação e a conferência dos pré-requisitos continuam obrigatórias.
- Depois de entrar, uma divergência no estado ou na página volta a apresentar o motivo correto, sem aviso de login desatualizado.
- Mensagens históricas de bloqueio permanecem genéricas; o texto específico da tela de login é usado somente na preparação observada.

## Validação

- 173 testes direcionados de serviço, API, reexecução e preparação passaram.
- Nove regressões específicas cobrem login com e sem mudança de URL, senha recusada, campos incompletos, etapas adicionais, origem externa e revalidação após o login.
- 86 testes da interface e 40 testes do painel/Chromium passaram, conferindo a orientação específica e a permanência da confirmação explícita.
- Build, TypeScript, ESLint das alterações e `git diff --check` passaram.

A alteração foi limitada ao ambiente local existente. A execução que aguardava já tinha encerrado antes da atualização; não foi necessário cancelá-la. Nenhuma publicação externa ou nova execução de CT foi iniciada.
