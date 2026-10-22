export type PlanningGroup = {
  id: string;
  app_url: string;
  rounds: {
    id: string;
    name: string;
    status: string;
    count: number;
    created_at: string;
    updated_at?: string | null;
  }[];
};

export type PlannedExecution = PlanningGroup["rounds"][number] & {
  root_id: string;
  app_url: string;
  activity_at: string;
  href: string;
  suite_href: string;
};

const timestamp = (value?: string | null) => {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
};

export function plannedExecutions(groups: PlanningGroup[]): PlannedExecution[] {
  return groups.flatMap(group => group.rounds.filter(round => round.status === "draft").map(round => ({
    ...round,
    root_id: group.id,
    app_url: group.app_url,
    activity_at: Number.isFinite(timestamp(round.updated_at)) ? round.updated_at! : round.created_at,
    href: `#/conjuntos/${encodeURIComponent(group.id)}?plan=${encodeURIComponent(round.id)}`,
    suite_href: `#/conjuntos/${encodeURIComponent(group.id)}`,
  }))).sort((a, b) => (timestamp(b.activity_at) - timestamp(a.activity_at)) || 0);
}

export function filterPlannedExecutions(entries: PlannedExecution[], query: string, rootId = ""): PlannedExecution[] {
  const normalize = (value: string) => value.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
  const search = normalize(query.trim());
  return entries.filter(entry => (!rootId || entry.root_id === rootId)
    && normalize(`${entry.name} ${entry.app_url} ${entry.root_id}`).includes(search));
}
