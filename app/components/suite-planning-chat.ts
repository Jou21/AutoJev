// Explicit CT additions are deterministic draft edits, not model actions.
// Ambiguous requests remain suggestions and still require the user's acceptance.
export function directCaseAddition(message: string, knownIds: string[]): { ids: string[]; missing: string[] } | null {
  const text = message.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  const command = text.match(/^(?:por favor[, :]?\s*)?(?:(?:quero que (?:voce |vc )?|(?:quero|gostaria de|preciso|pode|poderia|vamos) )?)(?:adicione|adiciona|adicionar|inclua|inclui|incluir|selecione|selecionar|marque|marcar|coloque|colocar|insira|inserir)\b\s*(.+)$/);
  if (!command) return null;
  const body = command[1];
  const mentions = [...body.matchAll(/\bct[\s-]*(\d+)\b/g)];
  if (!mentions.length || /\bct[\s-]*\d+\s*(?:a|ate|-)\s*(?:ct[\s-]*)?\d+\b/.test(body)) return null;
  const remainder = body.replace(/\bct[\s-]*\d+\b/g, " ").replace(/[.,!?;:]/g, " ").trim();
  const allowed = new Set("o a os as e tambem por favor na no ao aos da do dos das rodada execucao reexecucao planejamento plano rascunho lista fila ct cts caso casos de teste testes para pra pro essa esse esta este atual".split(" "));
  if (remainder && remainder.split(/\s+/).some(word => !allowed.has(word))) return null;
  const ids: string[] = [], missing: string[] = [];
  for (const mention of mentions) {
    const digits = mention[1].replace(/^0+(?=\d)/, "");
    const matches = knownIds.filter(id => /^CT\d+$/i.test(id) && id.slice(2).replace(/^0+(?=\d)/, "") === digits);
    if (matches.length === 1) ids.push(matches[0]);
    else missing.push(mention[0].toUpperCase());
  }
  return { ids: [...new Set(ids)], missing: [...new Set(missing)] };
}

export function caseAdditionReply(added: string[], alreadySelected: string[]): string {
  const parts = [];
  if (added.length) parts.push(`${added.join(", ")} ${added.length === 1 ? "foi adicionado" : "foram adicionados"} ao rascunho da execução.`);
  if (alreadySelected.length) parts.push(`${alreadySelected.join(", ")} ${alreadySelected.length === 1 ? "já estava selecionado" : "já estavam selecionados"}; não dupliquei os casos.`);
  parts.push("Nada foi executado. Revise os testes antes de confirmar a execução.");
  return parts.join(" ");
}
