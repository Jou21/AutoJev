"use client";

import { ChevronDown, X } from "lucide-react";
import { functionalFlowStats, type FlowCase, type FunctionalFlow } from "./functional-flows";
import "./FunctionalFlows.css";

type Props = { flows: FunctionalFlow[]; cases: FlowCase[]; selected: string; onSelect: (identity: string) => void; compact?: boolean; overview?: boolean };
const labels = { PASS: "aprovados", FAIL: "reprovados", BLOCKED: "bloqueados", SKIPPED: "sem validação" };

export default function FunctionalFlows({ flows, cases, selected, onSelect, compact = false, overview = false }: Props) {
  const all = functionalFlowStats(cases), unclassified = functionalFlowStats(cases, "unclassified");
  const active = selected === "unclassified" ? "Não classificados" : flows.find(flow => flow.id === selected)?.name || "Todos os testes";
  const current = functionalFlowStats(cases, selected);
  const select = (identity: string) => onSelect(identity === selected ? "all" : identity);
  const options = <>
    <option value="all">Todos os testes ({all.total})</option>
    <option value="unclassified">Não classificados ({unclassified.total})</option>
    {flows.map(flow => {
      const stats = functionalFlowStats(cases, flow.id);
      return <option key={flow.id} value={flow.id}>{flow.name} ({stats.total}){flow.confidence === "suggested" ? " · sugerido" : ""}{stats.counts.FAIL ? ` · ${stats.counts.FAIL} ${stats.counts.FAIL === 1 ? "reprovado" : "reprovados"}` : ""}</option>;
    })}
  </>;
  const card = (flow: FunctionalFlow) => {
    const stats = functionalFlowStats(cases, flow.id);
    return <button type="button" key={flow.id} className="functional-flow-card" data-select-flow={flow.id} aria-pressed={selected === flow.id} onClick={() => select(flow.id)}>
      <span className="flow-card-top"><span className="flow-confidence" data-flow-confidence={flow.confidence}>{flow.confidence === "identified" ? "Identificado" : "Sugerido"}</span>{stats.counts.FAIL > 0 && <span className="flow-failure">Há falhas</span>}</span>
      <strong className="flow-card-name" title={flow.description}>{flow.name}</strong><span className="sr-only">{flow.description}</span>
      <span className="flow-card-total"><b>{stats.total}</b> CTs relacionados</span>
      <span className="flow-card-stats">{(Object.keys(labels) as (keyof typeof labels)[]).map(status => <span className={`flow-stat ${status.toLowerCase()}`} key={status}><b>{stats.counts[status]}</b> {labels[status]}</span>)}</span>
      <span className="flow-card-approval">{stats.approval === null ? "Sem CTs avaliados (aprovado/reprovado)" : `${stats.approval.toLocaleString("pt-BR")}% de aprovação · ${stats.evaluated}/${stats.total} CTs avaliados`}</span>
      {stats.suggested > 0 && <span className="flow-card-suggestions">{stats.suggested} associação(ões) sugerida(s)</span>}
    </button>;
  };
  if (compact) return <div className="functional-flow-compact"><label>Funcionalidade<select aria-label="Filtrar por fluxo funcional" value={selected} onChange={event => onSelect(event.target.value)}>{options}</select></label>{selected !== "all" && <button type="button" className="text-button" onClick={() => onSelect("all")}>Remover filtro de fluxo</button>}{overview && flows.length > 0 && <details className="functional-flow-popover"><summary>Ver funcionalidades ({flows.length})</summary><div><p className="functional-flow-note">Agrupamentos identificados nos registros, não jornadas completas. Um teste pode aparecer em mais de um grupo. Sugestões precisam de revisão.</p>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Named keyboard-scrollable region. */}
      <div className="functional-flow-list" role="region" aria-label="Funcionalidades disponíveis" tabIndex={0}>{flows.map(card)}</div></div></details>}</div>;
  return <section className="functional-flows-panel" aria-label="Fluxos funcionais">
    <div className="functional-flow-filter-bar">
      <label className="functional-flow-select"><span>Fluxos funcionais</span><select aria-label="Filtrar por fluxo funcional" value={selected} onChange={event => onSelect(event.target.value)}>{options}</select></label>
      <div className="functional-flows-tools">
        <button type="button" aria-pressed={selected === "unclassified"} onClick={() => select("unclassified")}>Não classificados <span>{unclassified.total}</span></button>
        {selected !== "all" && <button type="button" className="functional-flow-reset" aria-label="Remover filtro de fluxo" onClick={() => onSelect("all")}><X size={13} />Limpar fluxo</button>}
      </div>
      <p className="functional-flow-context" role="status" aria-live="polite" aria-atomic="true"><span>{current.total} {current.total === 1 ? "CT exibido" : "CTs exibidos"}</span><span className="sr-only"> · {active}</span>{current.counts.FAIL > 0 && <span className="flow-failure">{current.counts.FAIL} {current.counts.FAIL === 1 ? "reprovado" : "reprovados"}</span>}</p>
    </div>
    {flows.length ? <details className="functional-flows-overview">
      <summary><ChevronDown size={14} /><span>Explorar fluxos</span><span className="flow-overview-count">{flows.length}</span></summary>
      <p className="functional-flow-note">Selecione um fluxo para filtrar os CTs abaixo. Um CT pode pertencer a mais de um fluxo. As contagens respeitam os filtros da lista; não representam cobertura ou uma jornada completa. Aprovação considera somente CTs aprovados/reprovados.</p>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- A named scroll region needs keyboard focus for arrow/PageDown scrolling. */}
      <div className="functional-flow-list" role="region" aria-label="Fluxos disponíveis" tabIndex={0}>{flows.map(card)}</div>
    </details> : <p className="functional-flow-empty">Nenhum fluxo identificado com os metadados disponíveis. Todos os CTs permanecem acessíveis abaixo.</p>}
  </section>;
}
