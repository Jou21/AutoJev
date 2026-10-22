type HistorySource = {
  id: string;
  created_at: string;
  initial_count: number;
  initial_status: string;
  rounds: { id: string; name: string; count: number; status: string; created_at: string }[];
  legacy_runs: { id: string; count: number; status: string; created_at: string }[];
};

type HistoryEntry = {
  kind: "initial" | "round" | "linked";
  id: string;
  title: string;
  count: number;
  status: string;
  created_at: string;
  href: string;
};

function timestamp(value: string): number {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

export function suiteExecutionHistory(group: HistorySource): HistoryEntry[] {
  const entries: HistoryEntry[] = [
    { kind: "initial", id: group.id, title: "Execução inicial", count: group.initial_count, status: group.initial_status,
      created_at: group.created_at, href: `#/exploracoes/${encodeURIComponent(group.id)}` },
    ...group.rounds.map(item => ({ kind: "round" as const, id: item.id, title: item.name, count: item.count, status: item.status,
      created_at: item.created_at, href: `#/conjuntos/${encodeURIComponent(group.id)}?${item.status === "draft" ? "plan" : "round"}=${encodeURIComponent(item.id)}` })),
    ...group.legacy_runs.map(item => ({ kind: "linked" as const, id: item.id, title: "Execução vinculada", count: item.count, status: item.status,
      created_at: item.created_at, href: `#/exploracoes/${encodeURIComponent(item.id)}` })),
  ];
  // Compare actual instants, not formatted dates or ISO strings with different offsets.
  // Stable ties preserve the input order; missing dates go to the end.
  return entries.sort((a, b) => (timestamp(b.created_at) - timestamp(a.created_at)) || 0);
}
