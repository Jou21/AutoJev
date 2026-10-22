"use client";

import { useEffect, useState } from "react";
import { ArrowRight, ClipboardList, Clock3, Globe2, Layers3, LoaderCircle, Plus, RefreshCcw, Search } from "lucide-react";
import { filterPlannedExecutions, plannedExecutions, type PlannedExecution, type PlanningGroup } from "./planned-executions";
import "./TestSuitesWorkspace.css";
import "./PlannedExecutions.css";

type Props = {
  request: <T>(path: string, options?: RequestInit & { raw?: boolean }) => Promise<T>;
  navigate: (hash: string) => void;
};

const host = (url: string) => { try { return new URL(url).hostname; } catch { return url; } };
const date = (value: string) => Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "Data não registrada";

export function PlannedExecutionList({ entries, navigate }: { entries: PlannedExecution[]; navigate: Props["navigate"] }) {
  return <div className="planned-list" aria-label="Planejamentos salvos">
    {entries.map(entry => <article className="panel planned-card" key={`${entry.root_id}/${entry.id}`}>
      <span className="suite-app-icon"><ClipboardList size={22} aria-hidden="true" /></span>
      <div className="planned-card-main">
        <div className="planned-card-title"><h2>{entry.name}</h2><span className="suite-status">Rascunho salvo</span></div>
        <div className="planned-card-meta">
          <span title={entry.app_url}><Globe2 size={13} aria-hidden="true" />{host(entry.app_url)}</span>
          <span><Layers3 size={13} aria-hidden="true" />{entry.count} {entry.count === 1 ? "teste" : "testes"}</span>
          <span><Clock3 size={13} aria-hidden="true" />Atualizado: {Number.isFinite(Date.parse(entry.activity_at))
            ? <time dateTime={entry.activity_at}>{date(entry.activity_at)}</time> : date(entry.activity_at)}</span>
        </div>
        <button className="text-button planned-suite-link" title={`Grupo ${entry.root_id}`} onClick={() => navigate(entry.suite_href)}>Ver testes da aplicação<ArrowRight size={13} /></button>
      </div>
      <button className="button secondary planned-continue" aria-label={`Continuar planejamento: ${entry.name}`} onClick={() => navigate(entry.href)}>Continuar<ArrowRight size={15} /></button>
    </article>)}
  </div>;
}

export default function PlannedExecutions({ request, navigate }: Props) {
  const [groups, setGroups] = useState<PlanningGroup[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0), [query, setQuery] = useState(""), [rootId, setRootId] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    request<PlanningGroup[]>("/test-suites", { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setGroups(value);
    }).catch(caught => {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Não foi possível carregar os planejamentos.");
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [request, refresh]);

  const entries = plannedExecutions(groups);
  const availableGroups = groups.filter(group => group.rounds.some(round => round.status === "draft"));
  const effectiveRootId = availableGroups.some(group => group.id === rootId) ? rootId : "";
  const filtered = filterPlannedExecutions(entries, query, effectiveRootId);

  return <div className="view-stack suite-workspace planned-workspace">
    <header className="suite-page-heading">
      <div><h1>Rascunhos</h1><p>Execuções que você salvou para preparar e confirmar depois.</p></div>
      <button className="button primary" onClick={() => navigate("#/exploracoes")}><Plus size={17} />Escolher testes</button>
    </header>
    <div className="planned-toolbar">
      <label className="suite-search"><Search size={17} aria-hidden="true" /><input aria-label="Buscar planejamentos" placeholder="Buscar rascunho ou aplicação…" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <select aria-label="Filtrar por conjunto" value={effectiveRootId} onChange={event => setRootId(event.target.value)}>
        <option value="">Todas as aplicações</option>
        {availableGroups.map(group => <option key={group.id} value={group.id}>{host(group.app_url)} · {group.id.slice(0, 8)}</option>)}
      </select>
      <button className="button ghost" disabled={loading} onClick={() => { setLoading(true); setError(""); setRefresh(value => value + 1); }}><RefreshCcw size={15} className={loading ? "spin" : undefined} />Atualizar</button>
    </div>
    {error && <p className="suite-error" role="alert">Não foi possível atualizar os planejamentos. {error} Tente novamente em Atualizar.</p>}
    <section className="planned-results" aria-label="Execuções planejadas salvas" aria-busy={loading}>
      {loading && !entries.length ? <div className="panel suites-loading" role="status"><LoaderCircle className="spin" size={24} />Carregando planejamentos…</div> : <>
        {(!error || !!entries.length) && <div className="planned-results-heading"><p role="status">{filtered.length} {filtered.length === 1 ? "rascunho salvo" : "rascunhos salvos"}</p><span>Mais recentes primeiro</span></div>}
        {!!filtered.length && <PlannedExecutionList entries={filtered} navigate={navigate} />}
        {!filtered.length && !error && <div className="panel suite-empty planned-empty">
          <ClipboardList size={28} aria-hidden="true" /><h2>{entries.length ? "Nenhum rascunho encontrado" : "Você ainda não tem rascunhos"}</h2>
          <p>{entries.length ? "Ajuste a busca ou a aplicação para encontrar seu rascunho." : "Em Meus testes, marque os testes desejados, clique em Preparar execução e depois em Salvar rascunho."}</p>
          {entries.length ? <button className="button secondary" onClick={() => { setQuery(""); setRootId(""); }}>Limpar filtros</button>
            : <button className="button primary" onClick={() => navigate("#/exploracoes")}>Ir para Meus testes<ArrowRight size={16} /></button>}
        </div>}
      </>}
    </section>
    <p className="planned-footnote">Nada é iniciado automaticamente. Execuções já iniciadas ficam em Meus testes → Execuções.</p>
  </div>;
}
