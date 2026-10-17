# Login pelo botão Continuar — 16/10/2026

## Comportamento restaurado

Após preencher usuário e senha no navegador, o clique explícito em **Continuar exploração** (ou **Continuar reexecução** na aplicação) tenta enviar o formulário de login preenchido. Se a pessoa já entrou pelo site, apenas confere a preparação. Preencher campos ou abrir a execução, por si só, não envia o login.

O executor possui `attempt_filled_login`, separado do limite de uma tentativa de login automático com credenciais configuradas. A confirmação autoriza uma única tentativa local; não preenche, substitui ou tenta adivinhar valores. Usa a identificação de controles e as proteções existentes para origem, destino, envio GET, CAPTCHA/OTP, ambiguidade e controles alterados.

Na reexecução, uma nova observação após o login passa pelo mesmo resolver e pelos mesmos pré-requisitos da receita antes do único despacho da ação original. Login recusado ou incompleto retorna à pausa e só uma nova confirmação permite tentar novamente. A exploração normal usa o mesmo caminho de confirmação e verifica cancelamento/prazo antes de avançar para a IA ou para ações.

O painel e o consentimento explicam a tentativa de login e mostram progresso/resultado legível. Eventos recebem somente status, códigos permitidos e mensagens fixas, não valores dos campos. Valores usados nas proteções de requisição e redação ficam exclusivamente na memória privada do executor, limpa ao fechar. Campos anônimos de usuário em formulários com senha também são ocultados no compositor de capturas, inclusive antes da tentativa, sem alterar seu conteúdo.

O aviso antigo de acesso não autenticado a `/inventory.html` não é tratado como rejeição de uma nova tentativa bem-sucedida que ainda está carregando.

## Validação isolada

- 60 testes Chromium do executor/login automático: botão amarelo real, envio único, campos intactos, nova confirmação para retry, erro antigo, controles alterados, origens inseguras, GET, CAPTCHA/OTP e redação de valores refletidos após o retorno.
- 3 testes de bitmap para campos de login anônimos: texto/e-mail/telefone ocultos; formulário de checkout sem senha preservado.
- 6 testes E2E Chromium de reexecução: login pelo site ou pelo Continue, com e sem print antes/depois. Primeiro clique com campos vazios não envia; preencher não envia; confirmação seguinte autentica e executa o CT exatamente uma vez. Origem e relatório original permanecem intactos.
- 3 testes Chromium de exploração normal: sucesso, rejeição e cancelamento durante o login, sem credenciais em prompts ou dados persistidos.
- 154 testes de serviço/API de reexecução aprovados.
- 129 testes de API, projeção de anexo, relatórios, preparação e ordenação aprovados (grupo parcialmente sobreposto).
- 142 testes frontend completos, TypeScript e build de produção aprovados.
- 96 regressões de políticas/capturas aprovadas.

A execução adicional ampla de exploração (`test_exploration.py` + `test_exploration_integration.py`) foi interrompida após 9min20s, com 77 casos aprovados e sem falhas reportadas até então. Ela não constitui validação completa dessa suíte. Os testes específicos de login/reexecução acima foram concluídos separadamente.

Toda autenticação de teste usa formulários descartáveis locais. Não foram usados login/senha reais nem iniciadas ações no SauceDemo do usuário.

## Ativação

A reexecução `77261a37-a6b4-44c1-9208-439b7f61e1ad`, que o usuário autorizou cancelar, já havia terminado por `preparation_timeout` quando seu estado foi conferido. Não foi cancelada nem modificada. O backend foi atualizado após confirmar que não existiam execuções ativas.

Para usar o comportamento corrigido, atualizar a aplicação e iniciar uma nova reexecução. A sessão anterior, encerrada por prazo, não é retomada automaticamente.
