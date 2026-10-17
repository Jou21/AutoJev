"use client";

import { BadgeCheck, Camera, Check, Circle, CircleHelp, Clock3, ImagePlus, LoaderCircle, LockKeyhole, Minus, Pause, Route, X, Zap } from "lucide-react";
import { CSSProperties, useId } from "react";
import "./RerunLiveBoard.css";

export type RerunLiveStepState = "pending" | "active" | "waiting" | "done" | "failed" | "blocked" | "skipped" | "unavailable";
export type RerunLiveStep = {
  id: string;
  title: string;
  kind: string;
  source_index?: number;
  checkpoint_id?: string;
  state: RerunLiveStepState;
  label: string;
  description: string;
  occurred_at?: string | null;
};
export type RerunLiveProgress = {
  version: 1;
  steps: RerunLiveStep[];
  completed: number;
  total: number;
  current_id?: string | null;
  notice?: string | null;
};
export type RerunLiveBoardProps = {
  progress?: RerunLiveProgress | null;
  scenarioId?: string;
  active?: boolean;
  selectedStepId?: string | null;
  onSelectStep?: (step: RerunLiveStep) => void;
  compact?: boolean;
};

const stepStates = {
  pending: { label: "Aguardando sua vez", icon: Circle },
  active: { label: "Em execução", icon: LoaderCircle },
  waiting: { label: "Aguardando você", icon: Pause },
  done: { label: "Concluída", icon: Check },
  failed: { label: "Falhou", icon: X },
  blocked: { label: "Bloqueada", icon: LockKeyhole },
  skipped: { label: "Não executada", icon: Minus },
  unavailable: { label: "Sem informação", icon: CircleHelp },
} as const;

function kindPresentation(step: RerunLiveStep) {
  if (step.checkpoint_id || ["capture", "manual_capture", "checkpoint", "wait_for_print"].includes(step.kind)) return { label: "Seu print", icon: ImagePlus };
  if (step.kind === "action") return { label: "Ação", icon: Zap };
  if (step.kind === "assertion") return { label: "Conferência", icon: BadgeCheck };
  if (step.kind === "observation") return { label: "Registro da página", icon: Camera };
  return { label: "Etapa do teste", icon: Route };
}

function gridPosition(index: number): CSSProperties {
  const properties: Record<string, string | number> = {};
  for (const columns of [2, 3, 4]) {
    const row = Math.floor(index / columns);
    const offset = index % columns;
    properties[`--board-row-${columns}`] = row + 1;
    properties[`--board-column-${columns}`] = row % 2 ? columns - offset : offset + 1;
  }
  return properties as CSSProperties;
}

export function RerunLiveBoard({ progress, scenarioId, active = false, selectedStepId, onSelectStep, compact = false }: RerunLiveBoardProps) {
  const headingId = useId();
  const steps = progress?.steps ?? [];
  const total = progress?.total ?? steps.length;
  const completed = progress?.completed ?? 0;
  const current = steps.find((step) => step.id === progress?.current_id)
    ?? steps.find((step) => step.state === "active" || step.state === "waiting");

  return (
    <section className={`rerun-live-board${compact ? " is-compact" : ""}`} aria-labelledby={headingId}>
      <header className="rerun-board-heading">
        <div className="rerun-board-heading-copy">
          <span className="rerun-board-eyebrow"><Route size={15} aria-hidden="true" /> PASSOS E EVIDÊNCIAS {scenarioId && <span>· {scenarioId}</span>}</span>
          <h2 id={headingId}>Acompanhe cada <span>etapa</span></h2>
          <p>O mesmo roteiro do teste, com o andamento desta reexecução.</p>
        </div>
        <div className="rerun-board-counter" role="status" aria-live="polite" aria-atomic="true">
          <strong>{completed}<span> / {total}</span></strong>
          <span>etapas concluídas</span>
        </div>
      </header>

      <div className="rerun-board-now" data-step-state={current?.state} role="status" aria-live="polite" aria-atomic="true">
        {current ? <><span className={`rerun-board-now-dot state-${current.state}`} aria-hidden="true" /><span>{current.label || stepStates[current.state]?.label || "Etapa atual"}: <strong>{current.title}</strong></span></>
          : <><Clock3 size={15} aria-hidden="true" /><span>{active ? "Preparando a próxima etapa…" : steps.length ? "Confira abaixo o resultado de cada etapa." : "Os passos aparecerão assim que o roteiro estiver disponível."}</span></>}
      </div>

      {steps.length ? (
        <div className="rerun-board-canvas">
          <ol className="rerun-board-grid" aria-label="Etapas da reexecução na ordem planejada">
            {steps.map((step, index) => {
              const state = stepStates[step.state] ?? stepStates.unavailable;
              const stateName = stepStates[step.state] ? step.state : "unavailable";
              const StateIcon = state.icon;
              const kind = kindPresentation(step);
              const KindIcon = kind.icon;
              const isCurrent = step.id === current?.id;
              const isSelected = step.id === selectedStepId;
              const content = <>
                <div className="rerun-board-card-top">
                  <span className="rerun-board-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <span className="rerun-board-kind"><KindIcon size={13} aria-hidden="true" />{kind.label}</span>
                  {stateName === "waiting" && <span className="rerun-board-waiting-badge">SUA VEZ</span>}
                  <span className={`rerun-board-state-symbol ${stateName === "active" ? "is-spinning" : ""}`} aria-hidden="true"><StateIcon size={17} /></span>
                </div>
                <h3>{step.title}</h3>
                <p id={compact ? `${headingId}-description-${index}` : undefined} className="rerun-board-description">{step.description || "Acompanhe o resultado desta etapa durante a reexecução."}</p>
                <div className="rerun-board-card-bottom">
                  <span className="rerun-board-state-label"><StateIcon size={13} aria-hidden="true" />{step.label || state.label}</span>
                  {onSelectStep && <span className="rerun-board-details-hint">Ver detalhes <span aria-hidden="true">↗</span></span>}
                </div>
              </>;
              return <li key={step.id} className={`rerun-board-cell state-${stateName}`} style={gridPosition(index)} data-step-id={step.id} data-step-state={stateName}>
                {onSelectStep ? <button type="button" className={`rerun-board-card${isSelected ? " is-selected" : ""}`} aria-current={isCurrent ? "step" : undefined} aria-pressed={isSelected} aria-describedby={compact ? `${headingId}-description-${index}` : undefined} aria-label={`Etapa ${index + 1}: ${step.title}. ${step.label || state.label}. Ver detalhes.`} onClick={() => onSelectStep(step)}>{content}</button>
                  : <div className={`rerun-board-card${isSelected ? " is-selected" : ""}`} aria-current={isCurrent ? "step" : undefined} aria-describedby={compact ? `${headingId}-description-${index}` : undefined} aria-label={`Etapa ${index + 1}: ${step.title}. ${step.label || state.label}.`}>{content}</div>}
              </li>;
            })}
          </ol>
        </div>
      ) : <div className="rerun-board-empty"><Route size={30} aria-hidden="true" /><strong>Preparando os passos do teste</strong><span>Nenhuma etapa foi marcada como concluída.</span></div>}

      <footer className="rerun-board-footer">
        <div className="rerun-board-legend" aria-label="Legenda dos estados">
          <span><Check size={14} aria-hidden="true" />Concluída</span>
          <span><LoaderCircle size={14} aria-hidden="true" />Em execução</span>
          <span><Pause size={14} aria-hidden="true" />Aguardando você</span>
        </div>
        {compact ? <details className="rerun-board-compact-notice"><summary>{progress?.notice?.includes("conferências locais") ? "Ordem planejada · conferências em bloco" : "Como ler os resultados"}</summary><p>{progress?.notice || "Concluir uma etapa não significa aprovar o teste. As conferências mostram o resultado observado nesta reexecução."}</p></details>
          : <p>{progress?.notice || "Concluir uma etapa não significa aprovar o teste. As conferências mostram o resultado observado nesta reexecução."}</p>}
      </footer>
    </section>
  );
}

export default RerunLiveBoard;
