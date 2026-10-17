# Reexecução: revisão auxiliar não é asserção ausente — 16/10/2026

## Diagnóstico

Na execução `6e1b97c5-8410-47b6-b6be-f23dc9533efd`, a ação de seleção do
CT001 foi executada. As três conferências locais passaram: opção selecionada,
ordenação dos produtos e ausência de novos erros técnicos.

A única entrada em `missing_original_checks` era `visual_model`. Porém o CT
original não continha uma asserção com esse tipo: tinha somente uma revisão
auxiliar visual solicitada/concluída. `_missing_checks()` transformava o sinal
`visual_assessment.requested` em uma asserção obrigatória inventada. Isso
forçava BLOCKED, execução parcial e o aviso genérico de erro.

## Correção e limites

- Conferências obrigatórias são comparadas com as asserções realmente
  preservadas no original. A revisão auxiliar, sozinha, não inventa pendência.
- `missing_visual_checks` indica somente asserções `visual_model` reais.
  `visual_review_omitted` informa separadamente a revisão visual fora do escopo.
- A reexecução continua local: revisão visual desabilitada, zero chamadas a
  modelos, nenhuma aprovação visual antiga reaproveitada. O resultado explica
  que a revisão original não foi repetida e não equivale a aprovação visual.
- Uma asserção visual obrigatória não repetida continua pendente/BLOCKED;
  falhas reais continuam FAIL. Se a automação falha e faltam conferências
  locais, a execução permanece parcial com as pendências locais verdadeiras.
- A confirmação distingue os dois casos e mantém consentimento obrigatório.
  Passos extras SWS/SQL/print continuam fora da execução.
- Nenhuma execução histórica ou relatório original foi reclassificado.
  A correção passa a valer em novas reexecuções.

## Validação

- 191 testes backend relacionados aprovados, incluindo API e Chromium real
  sobre fixture local com login/redirecionamento e confirmação manual.
- Regressões com três conferências locais cobrem revisão auxiliar assessed,
  pending e unavailable, resultado PASS e ausência de erro artificial.
- Asserção visual real continua bloqueante com ou sem requested=true.
  Falhas de negócio e de execução não são mascaradas.
- 125 testes frontend (82 explorador + 43 HTML) e build aprovados;
  `git diff --check` limpo.
- Pré-confirmação real do CT001 consultada somente por GET: disponível,
  `missing_visual_checks=false`, `visual_review_omitted=true` e três condições
  locais listadas. Nenhuma reexecução do site do usuário foi iniciada por nós.
- Habilidade Browser consultada; navegador integrado indisponível. MCP do
  Playwright confirmou a nova mensagem, consentimento desmarcado e botão de
  execução desabilitado. Captura:
  `data/mcp-validation/rerun-optional-visual-review-confirmation.png`.
- Relatório original preservado com SHA-256
  `1f9f07e3b5f9a5a31804412a0b157fe5af0d6cf50321760eb063e063926b470d`;
  relatório da tentativa histórica preservado com SHA-256
  `b4a357e27552395aba7ce2871c79f0db81ca99fa0f236f3f4468f34370330313`.

Backend atualizado sem execuções ativas. Para testar a correção, inicie uma nova
reexecução do CT001 a partir da execução original; não é necessário refazer a
exploração inteira.
