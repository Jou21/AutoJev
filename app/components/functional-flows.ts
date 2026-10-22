export type FlowMembership = { id: string; name: string; entity: string; operation: string; description: string; confidence: "identified" | "suggested"; reason: string };
export type FlowClassification = { version: string; memberships: FlowMembership[]; unclassified_reason?: string | null };
export type FunctionalFlow = Pick<FlowMembership, "id" | "name" | "entity" | "operation" | "description" | "confidence">;
export type FlowCase = { id: string; status?: string; flow_classification?: FlowClassification };

export function flowMemberships(item: Pick<FlowCase, "flow_classification">): FlowMembership[] {
  return Array.isArray(item.flow_classification?.memberships) ? item.flow_classification.memberships : [];
}
export function matchesFunctionalFlow(item: Pick<FlowCase, "flow_classification">, identity: string): boolean {
  const memberships = flowMemberships(item);
  return identity === "all" || (identity === "unclassified" ? !memberships.length : memberships.some(member => member.id === identity));
}
export function functionalFlowStats(items: FlowCase[], identity = "all") {
  const counts = { PASS: 0, FAIL: 0, BLOCKED: 0, SKIPPED: 0 };
  const related = [...new Map(items.filter(item => matchesFunctionalFlow(item, identity)).map(item => [item.id, item])).values()];
  for (const item of related) counts[Object.hasOwn(counts, item.status || "") ? item.status as keyof typeof counts : "SKIPPED"] += 1;
  const evaluated = counts.PASS + counts.FAIL;
  const suggested = related.filter(item => flowMemberships(item).some(member => member.id === identity && member.confidence === "suggested")).length;
  return { total: related.length, counts, evaluated, suggested, approval: evaluated ? Math.round(counts.PASS * 1000 / evaluated) / 10 : null };
}
export function caseFlowLabel(item: Pick<FlowCase, "flow_classification">): string {
  const members = flowMemberships(item);
  return members.length ? members.slice(0, 2).map(member => member.name + (member.confidence === "suggested" ? " (sugerido)" : "")).join(" · ")
    + (members.length > 2 ? ` · +${members.length - 2} fluxo(s)` : "") : "Não classificado";
}
