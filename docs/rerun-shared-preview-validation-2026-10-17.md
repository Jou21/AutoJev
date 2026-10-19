# Reexecução: recolher pelo J e compartilhar a prévia

## Comportamento

- O J recolhe e reabre o painel inclusive nas pausas de preparação e de print.
- Atualizações periódicas e recriação do iframe respeitam a escolha de recolher.
- Colar, escolher, trocar ou remover uma imagem sincroniza a prévia entre o painel e a aplicação principal.
- A sincronização mantém o teste pausado. Apenas a confirmação explícita anexa a imagem ao relatório e continua o CT.
- A imagem ainda não confirmada fica somente em memória, restrita à execução e à pausa atuais. Não entra nos eventos, armazenamento do navegador ou chamadas de IA.
- Revisões concorrentes e respostas atrasadas não podem substituir silenciosamente uma escolha mais recente.

## Verificação

- Playwright com a interface React real, backend e banco temporários: painel → aplicação, remoção, aplicação → painel, recolher/reabrir pelo J. Nenhum CT ou anexo do usuário foi executado.
- Playwright do painel: recolhimento durante atenção/polling, recriação do iframe, teclado, preservação da imagem e rejeição de comandos da página testada.
- 132 testes backend direcionados, 17 testes de sincronização/UI e o teste integrado das duas telas passaram.
- Build de produção concluído. O endpoint de prévia foi conferido no backend local com resposta `Cache-Control: no-store` e rejeição de execução encerrada.

Para executar o teste integrado, com o frontend local iniciado:

```bash
AUTOJEV_FRONTEND_E2E=1 backend/.venv/bin/pytest -q backend/tests/test_preview_two_surfaces_browser.py
```

O teste redireciona as requisições para um backend temporário e não inicia reexecuções reais. As capturas das duas interfaces ficam no diretório temporário do pytest.
