# Etapa inicial concluída antes da pausa para print

## Causa

O navegador já observava a página preparada, mas a timeline do CT era publicada somente depois da ação e de suas conferências. Uma pausa após “Antes da ação” mostrava o print ativo e o primeiro card ainda pendente.

## Correção

- Persiste uma observação real quando o roteiro alcança a etapa inicial, antes de abrir uma pausa posicionada depois dela.
- O mapa principal e o painel flutuante usam esse evento da execução atual para concluir o card e atualizar o contador; não inferem conclusão pela posição ou pela fase.
- Uma pausa antes da primeira etapa mantém essa etapa pendente.
- A observação inicial é imutável. Revalidações feitas após pausas continuam protegendo a execução e ficam preservadas separadamente; não substituem a captura que o usuário já viu como concluída.
- A timeline final mantém um único card inicial e as posições dos prints. Cancelar não conclui a ação nem as conferências ainda não executadas.
- Execuções e relatórios antigos não são reescritos.

## Verificação

- 297 testes direcionados passaram: execução, projeção, evidências, cancelamentos, API, ordenação dos prints e sincronização de prévias.
- Dois testes integrados com Playwright passaram, usando a aplicação React real e backend/banco temporários. As duas telas mostraram **1/7**, a etapa inicial verde com check, a pausa para print amarela e a ação seguinte pendente.
- O teste de execução confirmou que, durante essa pausa, não há ação despachada, CT aprovado ou confirmação de upload.
- Lint dos novos arquivos e `git diff --check` sem problemas. O serviço existente ainda tem avisos de lint anteriores sobre seus tratamentos genéricos de erros.

```bash
backend/.venv/bin/pytest -q backend/tests/test_rerun_initial_observation_execution.py backend/tests/test_rerun_initial_observation.py
AUTOJEV_FRONTEND_E2E=1 backend/.venv/bin/pytest -q backend/tests/test_rerun_before_observation_browser.py
```

Os testes de navegador não executam CTs do usuário. A ativação local foi feita após conferir que não havia execuções ativas.
