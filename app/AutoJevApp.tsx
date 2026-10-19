"use client";

/* eslint-disable react-hooks/set-state-in-effect -- API hydration and prop-backed editor state are synchronized in effects. */
/* eslint-disable @next/next/no-img-element -- screenshots are dynamic assets from user-configured backends. */

import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Bot,
  Boxes,
  BrainCircuit,
  Check,
  CheckCircle2,
  CircleDollarSign,
  ChevronDown,
  ChevronRight,
  CircleStop,
  Clock3,
  ClipboardPaste,
  CloudCog,
  Code2,
  Copy,
  Database,
  Eye,
  FileCheck2,
  FileText,
  Focus,
  Gauge,
  Globe2,
  Hash,
  History,
  ImagePlus,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  Menu,
  MonitorPlay,
  MoreHorizontal,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Plus,
  RefreshCcw,
  Rocket,
  Save,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  SquareArrowOutUpRight,
  TerminalSquare,
  Trash2,
  Unplug,
  Upload,
  WandSparkles,
  X,
  Zap,
} from "lucide-react";
import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useId,
  useRef,
  useState,
} from "react";
import { RerunLiveBoard, type RerunLiveProgress, type RerunLiveStep } from "./components/RerunLiveBoard";

const DEFAULT_API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

type View =
  | "dashboard"
  | "new"
  | "explorations"
  | "providers"
  | "settings"
  | "detail";

type ScenarioCapturePlan = { version: 1; checkpoints: { id: string; gap: number; order: number }[] };
type RerunCaptureCheckpoint = { id: string; gap: number; order: number; version: number };
type AppRoute = { view: View; explorationId?: string; rerunScenarioId?: string; capturePlan?: ScenarioCapturePlan; capturePlanError?: string; liveRerun?: boolean };

type ScenarioRerunPreflight = {
  available: boolean;
  code?: string;
  reason?: string;
  title?: string;
  url?: string;
  scenario_id?: string;
  source_exploration_id?: string;
  checks?: string[];
  effects?: string[];
  manual_preparation_required?: boolean;
  timeline_count?: number;
  action_index?: number;
  scope?: { local_checks?: boolean; visual_review?: boolean; database_steps?: boolean; missing_visual_checks?: boolean; visual_review_omitted?: boolean };
};

type Model = {
  id?: string;
  model_id?: string;
  name?: string;
  label?: string | null;
  provider_id?: string;
  provider_name?: string;
  enabled?: boolean;
  input_modalities?: string[];
  output_modalities?: string[];
  supports_vision?: boolean;
  capabilities_known?: boolean;
  architecture?: {
    input_modalities?: string[];
    output_modalities?: string[];
  };
};

type Provider = {
  id: string;
  name: string;
  base_url: string;
  enabled?: boolean;
  active?: boolean;
  api_key_configured?: boolean;
  has_api_key?: boolean;
  models?: Model[];
  created_at?: string;
};

type ExplorationEvent = {
  id?: string;
  level?: string;
  type?: string;
  title?: string;
  message?: string;
  detail?: string;
  timestamp?: string;
  created_at?: string;
  status?: string;
  payload?: Record<string, unknown>;
};

type ScenarioAssertion = {
  kind?: string;
  expected?: unknown;
  expected_display?: string;
  actual?: unknown;
  passed?: boolean;
  message?: string;
};

type ScenarioTechnicalSignal = {
  kind?: string;
  type?: string;
  message?: string;
  url?: string;
  method?: string;
  occurred_at?: string;
  status_code?: number | null;
  classification?: string;
  label?: string;
  impact?: string;
  classification_reason?: string;
  affects_verdict?: boolean;
  association?: { kind?: string; step_number?: number; scenario_id?: string };
};

type ScenarioTimelineEntry = {
  id?: string;
  kind: "observation" | "action" | "assertion" | "signal";
  occurred_at?: string;
  title?: string;
  message?: string;
  url?: string;
  page_url?: string | null;
  observation?: {
    page_url?: string | null;
    page_url_source?: "event" | "observation" | null;
    phase?: string | null;
    summary?: string;
    request_url?: string | null;
    method?: string | null;
    status_code?: number | null;
  };
  screenshot?: string | null;
  action_preview?: { screenshot?: string | null };
  image_source?: "user_upload" | string;
  attached_at?: string;
  presentation_position?: { status: "planned" | "unresolved"; gap?: number; order?: number; reason?: string };
  signal?: ScenarioTechnicalSignal;
  assertion?: ScenarioAssertion;
  step_number?: number;
  target?: string | null;
};

type ScenarioCase = {
  id: string;
  evidence_exploration_id?: string;
  title: string;
  description?: string;
  preconditions?: string[];
  steps?: string[];
  expected_result?: string;
  actual_result?: string;
  status: string;
  url?: string;
  created_at?: string;
  screenshots?: string[];
  evidence?: {
    action_dispatched?: boolean;
    rerun_blocked_reason?: string;
    rerun_blocked_explanation?: string;
    timeline?: ScenarioTimelineEntry[];
    presentation_steps?: ScenarioTimelineEntry[];
    technical_signals?: ScenarioTechnicalSignal[];
    assertions?: ScenarioAssertion[];
    visual_assessment?: { requested?: boolean; status?: string; reason?: string; accepted_count?: number; uncertain_count?: number; limitations?: string[]; context_limited?: boolean; review_scope?: string };
    documentation_provenance?: { source?: string; reason?: string; evidence_mode?: string; model_input?: string };
    verdict?: { kind?: string; label?: string; explanation?: string; historical?: boolean };
  };
};

type CoverageRestriction = {
  id?: string;
  reason?: string;
  source_url?: string;
  target_url?: string;
  action?: string;
  name?: string;
  target?: string;
  field_name?: string;
  validation_probe?: { name?: string; blocking_fields?: { name?: string }[] };
  status?: string;
  resolution?: { action_step?: number; screenshot?: string; source_url?: string; assertions_passed?: boolean };
  dependency?: { kind?: string; source?: string; name?: string; item_id?: string; action_step?: number; screenshot?: string };
};

type VerificationCounts = {
  business_verified?: number;
  interaction_only?: number;
  execution_failures?: number;
  technical_failures?: number;
  business_failures?: number;
  visual_findings?: number;
  interaction_failures?: number;
  not_validated?: number;
};

type UsageBucket = {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  tokens?: number;
  cost?: number | null;
  cost_known?: boolean;
  cost_complete?: boolean;
};

type RerunPreparationConsentSnapshot = { preparation_version: number; revision: number; checked: boolean };
type RerunPreparationPresentation = {
  kicker: string;
  title: string;
  instruction: string;
  consent_label: string;
  button_label: string;
  details_label: string;
  note: string;
  feedback: string;
};
type RerunPreparation = {
  version?: number;
  attempts?: number;
  expected_url?: string;
  current_url?: string;
  reason_code?: string | null;
  reason_label?: string | null;
  instruction?: string;
  consent?: RerunPreparationConsentSnapshot | null;
  presentation?: Partial<RerunPreparationPresentation>;
  manual_login?: { status: "waiting" | "ready" | "blocked"; reason_code?: string | null };
  login_attempt?: {
    status: "success" | "rejected" | "manual_required";
    reason?: string;
    reason_label?: string;
  };
};

type Exploration = {
  id: string;
  app_url: string;
  status: string;
  rerun?: {
    running?: boolean;
    phase?: "queued" | "preparing" | "waiting_preparation" | "validating_preparation" | "executing" | "waiting_capture" | "capturing_print" | "generating_report" | "finished";
    source_exploration_id?: string;
    source_scenario_id?: string;
    report_exploration_id?: string;
    report_scenario_id?: string;
    exploration_id?: string;
    reason?: string;
    reason_label?: string;
    action_dispatched?: boolean;
    preparation?: RerunPreparation;
    capture_checkpoint?: RerunCaptureCheckpoint;
    capture_count?: number;
    captures_completed?: number;
    capture_timeout_seconds?: number;
    outcome?: string;
    finished_at?: string;
    step_progress?: RerunLiveProgress;
  };
  created_at?: string;
  updated_at?: string;
  started_at?: string;
  finished_at?: string;
  duration_summary?: { started_at?: string | null; ended_at?: string | null; total_seconds?: number | null; ongoing?: boolean };
  visual_review_summary?: {
    total: number; assessed: number; pending: number; unavailable: number;
    rate_limited: number; incomplete_response: number;
    context_limited?: number;
    unavailable_reasons?: { reason: string; count: number; label: string }[];
  };
  visual_review_resume?: {
    running?: boolean; total?: number; completed?: number; failed?: number; pending?: number;
    requests_used?: number; request_budget?: number; max_attempts?: number;
    context_limited_count?: number; reason?: string | null;
  };
  progress?: number | null;
  overall_progress?: number | null;
  phase?: "preparing" | "exploring" | "waiting_input" | "documenting" | "reporting" | "complete";
  phase_label?: string;
  phase_description?: string;
  phase_progress?: { current?: number; total?: number | null; percentage?: number | null };
  browser_progress?: { current?: number; total?: number | null; percentage?: number | null };
  postprocessing?: {
    current?: number;
    total?: number;
    enriched?: number;
    local_evidence?: number;
    stage?: string | null;
    reason?: string | null;
    pending?: number;
    deferred?: number;
    requests?: number;
  };
  completion?: {
    type?: string | null;
    reason?: string | null;
    message?: string | null;
    known_states?: number | null;
    frontier_states?: number | null;
    frontier_candidates?: number | null;
    blocked_candidates?: number | null;
    restorations_abandoned?: number | null;
    restrictions?: CoverageRestriction[];
    resolved_restrictions?: CoverageRestriction[];
  };
  verification_summary?: VerificationCounts;
  current_step?: string;
  current_action?: string;
  screenshot_url?: string | null;
  report_available?: boolean;
  report_url?: string | null;
  agent?: { provider_id?: string; provider?: string; model_id?: string; model?: string };
  auxiliary_llm?: {
    provider_id?: string;
    provider?: string;
    model_id?: string;
    model?: string;
  };
  options?: Record<string, unknown>;
  counters?: {
    pages?: number;
    pages_visited?: number;
    actions?: number;
    issues?: number;
    scenarios?: number;
    test_scenarios?: number;
  };
  pages_visited?: number;
  steps_completed?: number;
  actions?: { result?: { success?: boolean; finished_at?: string } | null }[];
  actions_count?: number;
  issues_count?: number;
  scenarios_count?: number;
  summary?: {
    total_scenarios?: number;
    pass?: number;
    fail?: number;
    blocked?: number;
    skipped?: number;
    issues?: number;
  };
  events?: ExplorationEvent[];
  activity?: ExplorationEvent[];
  logs?: ExplorationEvent[];
  scenarios?: ScenarioCase[];
  scenario_contexts?: ScenarioCase[];
  error?: string | null;
  usage?: UsageBucket & {
    by_role?: { jev?: UsageBucket; auxiliary?: UsageBucket };
  };
};

type Toast = {
  id: number;
  tone: "success" | "error" | "info";
  message: string;
};

type ProviderDraft = {
  name: string;
  base_url: string;
  api_key: string;
  enabled: boolean;
};

type RequestOptions = RequestInit & { raw?: boolean };

const NAV_ITEMS: Array<{
  view: Exclude<View, "detail">;
  hash: string;
  label: string;
  icon: typeof LayoutDashboard;
}> = [
  { view: "dashboard", hash: "#/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { view: "new", hash: "#/nova-exploracao", label: "Nova exploração", icon: Rocket },
  { view: "explorations", hash: "#/exploracoes", label: "Explorações", icon: History },
  { view: "providers", hash: "#/provedores", label: "Provedores de IA", icon: CloudCog },
  { view: "settings", hash: "#/configuracoes", label: "Configurações", icon: Settings },
];

const STATUS_LABELS: Record<string, string> = {
  queued: "Na fila",
  pending: "Preparando",
  running: "Em execução",
  waiting_input: "Aguardando sua intervenção",
  postprocessing: "Preparando resultados",
  completed: "Concluída",
  success: "Concluída",
  partial: "Cobertura parcial",
  failed: "Falhou",
  error: "Falhou",
  cancelled: "Cancelada",
  canceled: "Cancelada",
};

const STATUS_TONES: Record<string, string> = {
  queued: "neutral",
  pending: "neutral",
  running: "running",
  waiting_input: "warning",
  postprocessing: "running",
  completed: "success",
  success: "success",
  partial: "warning",
  failed: "error",
  error: "error",
  cancelled: "warning",
  canceled: "warning",
};

function normalizeBaseUrl(url: string) {
  return url.replace(/\/$/, "");
}

function isOpenRouterUrl(value: string) {
  try {
    const hostname = new URL(value).hostname.toLowerCase();
    return hostname === "openrouter.ai" || hostname.endsWith(".openrouter.ai");
  } catch {
    return false;
  }
}

function normalizeTargetUrl(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return "";
  return /^[a-z][a-z\d+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

function explorationFocusArea(options?: Record<string, unknown>) {
  const value = options?.focus_area ?? options?.focusArea;
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function explorationEvidenceMode(options?: Record<string, unknown>): "text" | "visual" {
  const value = options?.auxiliary_evidence_mode ?? options?.auxiliaryEvidenceMode;
  return value === "visual" ? "visual" : "text";
}

export function visualUnavailabilityLabel(reason: string) {
  if (reason.startsWith("auxiliary_rate_limit")) return "limitados pelo provedor";
  return ({
    documentation_time_limit: "sem revisão antes do limite de espera das revisões auxiliares",
    TimeoutError: "com tempo de resposta da LLM auxiliar excedido",
    auxiliary_timeout: "com tempo de resposta da LLM auxiliar excedido",
    auxiliary_provider_pause_exceeds_budget: "com espera exigida pelo provedor maior que o limite desta execução",
    auxiliary_request_budget_exhausted: "sem revisão após esgotar o orçamento de chamadas auxiliares",
    visual_assessment_not_returned: "com resposta visual ausente ou inválida",
    visual_assessment_schema_invalid: "com resposta visual ausente ou inválida",
    visual_findings_invalid: "com resposta visual ausente ou inválida",
    auxiliary_response_invalid: "com resposta auxiliar ausente ou inválida",
    auxiliary_response_schema_invalid: "com resposta auxiliar ausente ou inválida",
    screenshot_not_available: "sem captura disponível para a revisão visual",
    after_screenshot_not_available: "sem captura posterior à ação disponível",
    vision_rejected_text_fallback: "com entrada de imagem recusada pelo modelo",
    auxiliary_authentication_rejected: "com autenticação do provedor recusada",
    auxiliary_provider_error: "com falha na requisição ao provedor",
    auxiliary_provider_temporary_error: "com falha temporária na requisição ao provedor",
    auxiliary_unavailable: "com provedor indisponível",
    auxiliary_backlog_full: "sem revisão por fila auxiliar cheia",
    exploration_cancelled: "sem revisão devido ao cancelamento da execução",
    backend_shutdown: "sem revisão devido ao encerramento do backend",
    documentation_interrupted: "com revisão auxiliar interrompida",
    auxiliary_worker_stopped: "com processamento auxiliar interrompido",
    auxiliary_worker_unavailable: "sem processamento auxiliar disponível",
    visual_review_cancelled: "com retomada visual interrompida a pedido do usuário",
    visual_review_context_unavailable: "sem contexto preservado suficiente para retomar a análise visual",
    visual_review_retry_exhausted: "sem revisão válida após o limite de tentativas",
    auxiliary_review_retry_exhausted: "sem revisão válida após o limite de tentativas",
    auxiliary_review_attempts_exhausted: "sem revisão válida após o limite de tentativas",
    local_evidence_mode: "preservados somente com evidência local",
  } as Record<string, string>)[reason] || "sem revisão visual válida; motivo não classificado";
}

export function visualReviewCounts(scenarios: ScenarioCase[]) {
  const counts = { total: scenarios.length, assessed: 0, pending: 0, unavailable: 0, rateLimited: 0, incompleteResponse: 0, contextLimited: 0, unavailableReasons: [] as { reason: string; count: number; label: string }[] };
  const unavailableReasons = new Map<string, number>();
  for (const scenario of scenarios) {
    const visual = scenario.evidence?.visual_assessment;
    if (visual?.status === "assessed" && visual.reason !== "visual_findings_invalid") {
      counts.assessed += 1;
      if (visual.context_limited === true || visual.review_scope === "saved_capture_only_legacy") counts.contextLimited += 1;
    }
    else if (visual?.status === "pending") counts.pending += 1;
    else {
      counts.unavailable += 1;
      const reason = visual?.reason || "reason_not_recorded";
      unavailableReasons.set(reason, (unavailableReasons.get(reason) || 0) + 1);
      if (reason.startsWith("auxiliary_rate_limit")) counts.rateLimited += 1;
      if (["visual_assessment_not_returned", "visual_assessment_schema_invalid", "visual_findings_invalid", "auxiliary_response_invalid", "auxiliary_response_schema_invalid"].includes(reason)) counts.incompleteResponse += 1;
    }
  }
  counts.unavailableReasons = [...unavailableReasons].sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0).map(([reason, count]) => ({ reason, count, label: visualUnavailabilityLabel(reason) }));
  return counts;
}

export function VisualReviewSummary({ scenarios, active = false, summary, requestBudget, requests }: { scenarios: ScenarioCase[]; active?: boolean; summary?: Exploration["visual_review_summary"]; requestBudget?: number; requests?: number }) {
  // Prefer the shared API summary; the local equivalent also supports older
  // backend versions while a live run finishes without a backend restart.
  const counts = summary?.unavailable_reasons ? { ...summary, unavailableReasons: summary.unavailable_reasons, contextLimited: summary.context_limited ?? visualReviewCounts(scenarios).contextLimited } : visualReviewCounts(scenarios);
  const title = counts.pending || (active && !counts.total) ? "Análise visual em andamento"
    : !counts.assessed ? "Análise visual não realizada"
    : counts.unavailable ? "Análise visual parcial" : counts.contextLimited ? "Capturas analisadas · contexto parcial" : "Capturas analisadas";
  return <section className="visual-review-summary panel" aria-label="Resultado da análise visual">
    <Eye size={20} />
    <div><strong>{title}</strong>
      <p>{counts.assessed} de {counts.total} CTs receberam uma análise visual válida · {counts.pending} em análise · {counts.unavailable} indisponíveis.</p>
      {counts.contextLimited > 0 && <p><strong>{counts.contextLimited} revisões com contexto textual limitado.</strong> As imagens salvas foram analisadas, mas o DOM e o texto visível originais não foram preservados. Essas revisões não equivalem a uma análise com o contexto semântico completo da tela.</p>}
      {typeof requestBudget === "number" && <p className="field-hint">Limite local registrado: {requestBudget} chamadas auxiliares{typeof requests === "number" ? ` · ${requests} realizadas` : ""}. É um limite de requisições HTTP, não de CTs.</p>}
      {counts.unavailableReasons.map((item) => <p key={item.reason}>{item.count} {item.label}. <small>Código: {item.reason}</small></p>)}
      {(counts.unavailable > 0 || counts.pending > 0) && <p>O que está pendente aqui é a revisão visual adicional das capturas pela LLM. Ações e verificações locais já registradas continuam válidas. O limite local de chamadas não é um limite de testes nem indica falta de crédito no provedor.</p>}
      <small>Os resultados de aprovação ou reprovação das verificações locais continuam válidos. Análise visual indisponível não significa aprovação visual; ausência de indícios não garante cobertura visual completa.</small>
    </div>
  </section>;
}

export function VisualReviewResumeControls({ state, pending, initialBudget = 300, initiallyExpanded = false, onResume, onCancel }: {
  state?: Exploration["visual_review_resume"];
  pending: number;
  initialBudget?: number;
  initiallyExpanded?: boolean;
  onResume: (options: { max_requests: number; max_attempts: number }) => Promise<void | boolean>;
  onCancel: () => Promise<void>;
}) {
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const [budget, setBudget] = useState(String(initialBudget));
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const amount = Number(budget);
  const validBudget = Number.isInteger(amount) && amount >= 1 && amount <= 2000;
  const running = !!state?.running;
  const lastBatchMessage = ({
    auxiliary_request_budget_exhausted: "O último lote atingiu o limite de chamadas. As revisões restantes continuam pendentes; nenhum novo lote será iniciado automaticamente.",
    auxiliary_authentication_rejected: "O provedor recusou a autenticação na retomada. Verifique a chave nas configurações do provedor antes de tentar novamente.",
    vision_rejected_text_fallback: "O modelo recusou imagens na retomada. Verifique a compatibilidade visual do modelo.",
    backend_restart_interrupted: "A retomada foi interrompida pelo reinício do backend. As revisões já salvas foram preservadas.",
    cancelled: "A retomada foi interrompida. As revisões já salvas e a exploração original foram preservadas.",
    auxiliary_review_batch_timeout: "O tempo de espera deste lote terminou. As revisões já salvas foram preservadas; as restantes continuam pendentes.",
    auxiliary_review_batch_failed: "Não foi possível concluir este lote de revisões. As evidências e a exploração original foram preservadas; confira a atividade antes de tentar novamente.",
    completed_with_unavailable_reviews: "O lote terminou, mas algumas revisões continuam indisponíveis. Consulte o motivo de cada CT antes de autorizar outro lote.",
  } as Record<string, string>)[state?.reason || ""];
  const start = async (event: FormEvent) => {
    event.preventDefault();
    if (!consent || !validBudget || busy || running) return;
    setBusy(true);
    try {
      const accepted = await onResume({ max_requests: amount, max_attempts: 3 });
      if (accepted === false) return;
      setConsent(false);
      setExpanded(false);
    } finally { setBusy(false); }
  };
  const cancel = async () => {
    setBusy(true);
    try { await onCancel(); } finally { setBusy(false); }
  };
  if (!running && pending < 1) return null;
  return <section className="panel visual-review-resume" aria-label="Retomar análise visual">
    <div className="visual-review-resume-head"><div><strong>{running ? "Revisões visuais retomadas" : "Complete as revisões visuais pendentes"}</strong><p>Reutiliza as capturas salvas, sem abrir o navegador nem repetir a exploração. Os resultados locais dos CTs são preservados.</p></div>
      {running ? <button type="button" className="button secondary" disabled={busy} onClick={() => void cancel()}>{busy ? <Spinner /> : <CircleStop size={16} />}Parar revisões visuais</button>
        : <button type="button" className="button secondary" aria-expanded={expanded} disabled={busy} onClick={() => setExpanded((value) => !value)}><RefreshCcw size={16} />Retomar revisões visuais pendentes</button>}
    </div>
    {running && <p role="status">{state?.completed || 0} de {state?.total || pending} revisões concluídas · {state?.failed || 0} ainda indisponíveis · {state?.requests_used || 0} de {state?.request_budget || 0} chamadas utilizadas. Até {state?.max_attempts || 3} tentativas por revisão, contando a primeira.</p>}
    {!running && lastBatchMessage && <p role="status">{lastBatchMessage}</p>}
    {!!state?.context_limited_count && <p className="field-hint">{state.context_limited_count} CT(s) de execuções anteriores têm contexto textual limitado. A revisão usa as evidências preservadas, sem reconstruir dados que não foram salvos.</p>}
    {!running && expanded && <form onSubmit={(event) => void start(event)} className="visual-review-resume-form">
      <div className="callout warning-callout" role="note"><AlertCircle size={18} /><div><strong>Novas chamadas podem gerar custo na LLM.</strong><span>Este lote tem um limite independente e finito. São até 3 tentativas por revisão, contando a primeira; falhas de autenticação ou limite de chamadas não serão repetidas automaticamente. Abrir esta tela não inicia chamadas.</span></div></div>
      <p className="field-hint">Capturas de execuções antigas podem não ter o DOM e o texto visível originais salvos. Nesse caso, a revisão terá contexto textual limitado e não será equivalente à análise com o contexto semântico completo.</p>
      <label className="field-group"><span>Limite de chamadas deste lote</span><input type="number" min="1" max="2000" step="1" required value={budget} onChange={(event) => setBudget(event.target.value)} /><small>1 a 2.000 requisições HTTP. Retentativas também consomem o limite; o cache não consome chamadas. Não é uma promessa de revisar todos os CTs neste lote.</small></label>
      <label className="review-cost-consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />Entendo que esta retomada pode gerar custo adicional na LLM.</label>
      <div className="modal-actions"><button type="button" className="button ghost" onClick={() => setExpanded(false)}>Voltar</button><button type="submit" className="button primary" disabled={!consent || !validBudget || busy}>{busy ? <Spinner /> : <Play size={16} />}Iniciar revisões com este limite</button></div>
    </form>}
  </section>;
}

export function screenshotRequestUrl(src: string, revision: string, attempt: number) {
  if (/^(?:data|blob):/i.test(src)) return src;
  const url = new URL(src);
  url.searchParams.set("v", `${revision}${attempt ? `-retry-${attempt}` : ""}`);
  return url.href;
}

export function ScreenshotPreview({ src, revision = "latest", alt }: { src?: string | null; revision?: string; alt: string }) {
  // Reset by source/revision: a late error from an old capture cannot hide a
  // newer one. Only the user can retry; there is no endless polling loop.
  return <ScreenshotRequest key={`${src || "empty"}-${revision}`} src={src} revision={revision} alt={alt} />;
}

function ScreenshotRequest({ src, revision, alt }: { src?: string | null; revision: string; alt: string }) {
  const [attempt, setAttempt] = useState(0);
  const [failedAttempt, setFailedAttempt] = useState<number | null>(null);
  if (!src) return <div className="no-screenshot"><MonitorPlay size={32} /><strong>Aguardando captura do browser</strong><span>O preview aparecerá quando o executor publicar a primeira evidência.</span></div>;
  if (failedAttempt === attempt) return <div className="no-screenshot screenshot-load-error" role="status">
    <AlertCircle size={28} /><strong>Não foi possível carregar a captura</strong>
    <span>Isso não altera o resultado dos testes. Tente carregar a evidência novamente.</span>
    <button className="button secondary" onClick={() => setAttempt((current) => current + 1)}><RefreshCcw size={16} />Tentar novamente</button>
    <a className="text-button" href={src} target="_blank" rel="noreferrer">Abrir imagem original</a>
  </div>;
  return <img key={attempt} src={screenshotRequestUrl(src, revision, attempt)} alt={alt} onError={() => setFailedAttempt(attempt)} />;
}

function listFrom<T>(value: unknown, keys: string[]): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === "object") {
    const object = value as Record<string, unknown>;
    for (const key of keys) {
      if (Array.isArray(object[key])) return object[key] as T[];
    }
  }
  return [];
}

function modelValue(model: Model) {
  return model.model_id || model.id || model.name || "";
}

function modelLabel(model: Model) {
  return model.label || model.name || model.model_id || model.id || "Modelo sem nome";
}

function modelAcceptsImages(model: Model) {
  if (typeof model.supports_vision === "boolean") return model.supports_vision;
  const inputModalities = model.input_modalities || model.architecture?.input_modalities || [];
  const outputModalities = model.output_modalities || model.architecture?.output_modalities || [];
  return inputModalities.some((modality) => modality.trim().toLowerCase() === "image")
    && (!outputModalities.length || outputModalities.some((modality) => modality.trim().toLowerCase() === "text"));
}

function modelInputCapabilitiesKnown(model: Model) {
  if (model.capabilities_known === true) return true;
  const inputModalities = model.input_modalities || model.architecture?.input_modalities || [];
  return inputModalities.length > 0 || model.supports_vision === true;
}

const FREE_MODEL_PREFERENCE = [
  "nvidia/nemotron-3-ultra-550b-a55b:free",
  "nvidia/nemotron-3-super-120b-a12b:free",
  "qwen/qwen3.8-27b:free",
  "z-ai/glm-5.2:free",
  "google/gemma-4-31b-it:free",
];

const JEV_MODEL_PREFERENCE = ["typesafe/jev-1.13", "typesafe/jev-latest"];
const AUXILIARY_MODEL_PREFERENCE = ["google/gemini-2.5-flash-lite"];

function defaultModel(models: Model[], role: "agent" | "auxiliary") {
  if (role === "agent") {
    const jevModel = JEV_MODEL_PREFERENCE.find((preferred) =>
      models.some((model) => modelValue(model) === preferred),
    );
    if (jevModel) return jevModel;
  } else {
    // Explicit default requested by the user, not a paid fallback on failure.
    const preferredModel = AUXILIARY_MODEL_PREFERENCE.find((preferred) =>
      models.some((model) => modelValue(model) === preferred),
    );
    if (preferredModel) return preferredModel;
  }
  const freeModels = models.filter((model) => modelValue(model).endsWith(":free"));
  if (!freeModels.length) return models[0] ? modelValue(models[0]) : "";
  return (
    FREE_MODEL_PREFERENCE.find((preferred) =>
      freeModels.some((model) => modelValue(model) === preferred),
    )
    || modelValue(freeModels[0])
  );
}

function providerIsEnabled(provider: Provider) {
  return provider.enabled ?? provider.active ?? true;
}

function explorationStatus(status?: string) {
  const value = (status || "pending").toLowerCase();
  return {
    value,
    label: STATUS_LABELS[value] || value,
    tone: STATUS_TONES[value] || "neutral",
  };
}

function isLive(status?: string) {
  const value = (status || "").toLowerCase();
  return ["queued", "pending", "running", "waiting_input", "postprocessing"].includes(value);
}

function formatDate(value?: string, withTime = true) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  }).format(date);
}

function hostFromUrl(value?: string) {
  if (!value) return "Aplicação sem URL";
  try {
    return new URL(value).host;
  } catch {
    return value;
  }
}

export function explorationDuration(exploration: Exploration, now = Date.now()) {
  const duration = exploration.duration_summary;
  const first = Date.parse(duration?.started_at || exploration.started_at || exploration.created_at || "");
  if (Number.isNaN(first)) return "—";
  const recordedEnds = [exploration.finished_at, exploration.updated_at, ...(exploration.events || []).map((event) => event.created_at)].map((value) => Date.parse(value || "")).filter(Number.isFinite);
  const last = isLive(exploration.status) ? now : duration?.ended_at ? Date.parse(duration.ended_at) : recordedEnds.length ? Math.max(...recordedEnds) : NaN;
  if (Number.isNaN(last)) return "—";
  const totalSeconds = !isLive(exploration.status) && typeof duration?.total_seconds === "number" ? duration.total_seconds : Math.max(0, Math.floor((last - first) / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

export function awaitingRerunReport(exploration: Exploration | null, now = Date.now()) {
  // A rerun's terminal browser status can precede its asynchronous HTML report.
  // Keep fetching until the artifact or failure event arrives, with a finite
  // grace period so a broken renderer/API cannot leave an endless polling loop.
  const finished = Date.parse(exploration?.finished_at || exploration?.rerun?.finished_at || exploration?.updated_at || exploration?.created_at || "");
  const reportFailed = (exploration?.events || []).some((event) => event.type === "report_failed");
  return !!exploration?.rerun && !exploration.report_available && !reportFailed
    && Number.isFinite(finished) && finished <= now + 30000 && now - finished < 120000;
}

export function explorationDetailState(explorationId: string | undefined, selected: Exploration | null, loading: boolean, settledId: string | null, now = Date.now()) {
  const exploration = selected?.id === explorationId ? selected : null;
  return {
    exploration,
    loading: loading || (!exploration && settledId !== explorationId),
    shouldPoll: !exploration || isLive(exploration.status) || !!exploration.visual_review_resume?.running || awaitingRerunReport(exploration, now),
  };
}

export function scenarioRerunPhaseStepState(exploration: Exploration, stepKey: string, now = Date.now()): "done" | "active" | "pending" {
  const rerun = exploration.rerun;
  if (!rerun) return "pending";
  // Dispatch alone is not proof that the browser finished the action. A closed
  // or cancelled browser can leave its real-world effect unknown.
  const actionCompleted = (typeof exploration.steps_completed === "number" && exploration.steps_completed >= 1)
    || (exploration.actions || []).some((action) => typeof action.result?.success === "boolean");
  const live = isLive(exploration.status);
  if (stepKey === "preparing") {
    if (["executing", "waiting_capture", "capturing_print"].includes(rerun.phase || "") || actionCompleted) return "done";
    return live && ["queued", "preparing", "waiting_preparation", "validating_preparation"].includes(rerun.phase || "") ? "active" : "pending";
  }
  if (stepKey === "executing") {
    if (actionCompleted) return "done";
    return live && rerun.phase === "executing" ? "active" : "pending";
  }
  if (stepKey === "capture") {
    if ((rerun.capture_count || 0) > 0 && (rerun.captures_completed || 0) >= (rerun.capture_count || 0)) return "done";
    return live && ["waiting_capture", "capturing_print"].includes(rerun.phase || "") ? "active" : "pending";
  }
  if (stepKey === "reporting") {
    if (exploration.report_available === true) return "done";
    const reportFailed = (exploration.events || []).some((event) => event.type === "report_failed");
    return !reportFailed && (rerun.phase === "generating_report" || rerun.phase === "finished") && awaitingRerunReport(exploration, now) ? "active" : "pending";
  }
  return "pending";
}

export function scenarioRerunPreparationKey(exploration: Exploration | null) {
  const preparation = exploration?.rerun?.preparation;
  return `${exploration?.id || ""}:${preparation?.version ?? preparation?.attempts ?? 0}`;
}

export function scenarioRerunPreparationConsent(exploration: Exploration | null, confirmation: { key: string; confirmed: boolean }) {
  return exploration?.rerun?.phase === "waiting_preparation"
    && confirmation.key === scenarioRerunPreparationKey(exploration) && confirmation.confirmed;
}

export function useScenarioRerunPreparationConsent(exploration: Exploration | null, request: ApiRequest, busy = false) {
  const key = scenarioRerunPreparationKey(exploration);
  const explorationId = exploration?.id;
  const preparation = exploration?.rerun?.preparation;
  const preparationVersion = preparation?.version ?? preparation?.attempts ?? 0;
  const active = exploration?.rerun?.phase === "waiting_preparation" && isLive(exploration.status) && !busy;
  // Older reports/servers do not advertise shared consent. Keep their existing
  // local-only authorization, without sending an unsupported new API request.
  const shared = !!preparation && Object.hasOwn(preparation, "consent");
  const [state, setState] = useState({ key: "", checked: false, ready: false, pending: false, error: "" });
  const sessionRef = useRef<{
    pending: boolean;
    ready: boolean;
    checked: boolean;
    consumed: boolean;
    change: (checked: boolean) => void;
    retry: () => void;
  } | null>(null);
  useEffect(() => {
    if (!active || !shared || !explorationId) {
      sessionRef.current = null;
      if (!active) setState({ key, checked: false, ready: false, pending: false, error: "" });
      return;
    }
    let disposed = false;
    let revision: number | null = null;
    let polling = false;
    let mutationVersion = 0;
    let unsynchronized = false;
    let desiredChecked = false;
    const controllers = new Set<AbortController>();
    const path = `/explorations/${encodeURIComponent(explorationId)}/preparation-consent`;
    const current = () => !disposed && sessionRef.current === session;
    const publish = (checked: boolean, ready: boolean, pending: boolean, error = "") => {
      if (!current()) return;
      session.checked = checked; session.ready = ready; session.pending = pending;
      setState({ key, checked, ready, pending, error });
    };
    const call = async (options: RequestOptions = {}) => {
      const controller = new AbortController();
      controllers.add(controller);
      try {
        const snapshot = await request<RerunPreparationConsentSnapshot>(options.method === "POST" ? path : `${path}?preparation_version=${preparationVersion}`, {
          ...options, cache: "no-store", signal: controller.signal,
        });
        if (!snapshot || snapshot.preparation_version !== preparationVersion || !Number.isInteger(snapshot.revision)
          || snapshot.revision < 0 || typeof snapshot.checked !== "boolean") throw new Error("A preparação mudou. Atualize a reexecução antes de continuar.");
        return snapshot;
      } finally { controllers.delete(controller); }
    };
    const refresh = async () => {
      if (!current() || session.pending || session.consumed || polling || unsynchronized) return;
      polling = true;
      const mutation = mutationVersion;
      try {
        const snapshot = await call();
        if (!current() || session.pending || session.consumed || mutation !== mutationVersion || unsynchronized) return;
        if (revision !== null && snapshot.revision < revision) return;
        revision = snapshot.revision;
        publish(snapshot.checked, true, false);
      } catch (caught) {
        if (current() && !session.pending && !session.consumed && mutation === mutationVersion && !unsynchronized) {
          publish(session.checked, false, false, `Não foi possível sincronizar a autorização. ${errorMessage(caught)}`);
        }
      } finally { polling = false; }
    };
    const change = async (checked: boolean) => {
      if (!current() || session.pending || session.consumed) return;
      mutationVersion += 1;
      unsynchronized = true;
      desiredChecked = checked;
      publish(checked, false, true);
      try {
        if (revision === null) {
          const initial = await call();
          if (!current()) return;
          revision = initial.revision;
        }
        const snapshot = await call({ method: "POST", body: JSON.stringify({ preparation_version: preparationVersion, expected_revision: revision, checked }) });
        if (!current() || session.consumed) return;
        revision = snapshot.revision;
        unsynchronized = false;
        publish(snapshot.checked, true, false);
      } catch (caught) {
        if (!current() || session.consumed) return;
        const conflict = typeof caught === "object" && caught !== null && "status" in caught && caught.status === 409;
        if (conflict) {
          try {
            const latest = await call();
            if (!current() || session.consumed) return;
            revision = latest.revision;
            unsynchronized = false;
            publish(latest.checked, true, false, "A autorização mudou na outra janela. Confira o estado atualizado antes de continuar.");
          } catch {
            if (current()) publish(session.checked, false, false, "A autorização mudou na outra janela. Tente sincronizar novamente antes de continuar.");
          }
        } else publish(session.checked, false, false, `A autorização ainda não está sincronizada. ${errorMessage(caught)}`);
      }
    };
    const session = {
      pending: false, ready: false, checked: false, consumed: false,
      change(checked: boolean) { void change(checked); },
      retry() {
        if (!current() || session.pending || session.consumed) return;
        if (unsynchronized) void change(desiredChecked); else void refresh();
      },
    };
    sessionRef.current = session;
    publish(false, false, false);
    void refresh();
    const interval = window.setInterval(() => void refresh(), 1000);
    return () => {
      disposed = true;
      for (const controller of controllers) controller.abort();
      window.clearInterval(interval);
      if (sessionRef.current === session) sessionRef.current = null;
    };
  }, [active, shared, explorationId, preparationVersion, key, request]);
  const checked = active && state.key === key && state.checked;
  const ready = active && (!shared || state.key === key && state.ready);
  const pending = active && shared && state.key === key && state.pending;
  return {
    checked, ready, pending, shared,
    error: active && state.key === key ? state.error : "",
    setChecked(value: boolean) {
      if (!active) return;
      if (shared) sessionRef.current?.change(value);
      else setState({ key, checked: value, ready: true, pending: false, error: "" });
    },
    retry() { sessionRef.current?.retry(); },
    consume() {
      if (!active || !checked || !ready || pending) return false;
      if (shared) {
        const session = sessionRef.current;
        if (!session || !session.checked || !session.ready || session.pending || session.consumed) return false;
        session.consumed = true;
      }
      setState({ key, checked: false, ready: false, pending: false, error: "" });
      return true;
    },
  };
}

export function scenarioRerunPreparationPresentation(preparation?: RerunPreparation, validating = false): RerunPreparationPresentation {
  // Canonical vocabulary mirrors rerun_preparation_presentation.py. New
  // servers provide these same fields to the app and isolated browser panel.
  const login = preparation?.reason_code === "authentication_required";
  const loginReady = preparation?.manual_login?.status === "ready";
  const fallback: RerunPreparationPresentation = {
    kicker: validating ? "Conferindo a preparação" : loginReady ? "Login reconhecido" : login ? "Login necessário" : "Prepare a página",
    title: validating ? "Entrando e conferindo preparação…" : loginReady ? "Login realizado. Autorize para continuar" : login ? "Faça login para continuar" : "Prepare a página para continuar",
    instruction: validating ? "Aguarde a conferência do login, da página e dos pré-requisitos. Não é necessário confirmar novamente."
      : loginReady ? "Seu login foi reconhecido e os pré-requisitos foram conferidos. Marque a autorização para o teste continuar automaticamente."
        : login ? "Marque a autorização e faça login pelo próprio site. O AutoJev identifica o login e continua automaticamente após conferir a página e os pré-requisitos. Se preferir, preencha usuário e senha e clique em “Continuar reexecução” para o AutoJev tentar entrar."
          : "Prepare a página e os dados iniciais do teste. Depois, marque a autorização e clique em “Continuar reexecução”. Se houver um login preenchido no site autorizado, o AutoJev tentará entrar. A ação do teste só será executada se os pré-requisitos forem confirmados.",
    consent_label: "Autorizo tentar entrar com o login preenchido e executar a ação original somente se a conferência dos pré-requisitos passar.",
    button_label: validating ? "Entrando e conferindo preparação…" : "Continuar reexecução",
    details_label: "Ver detalhes da preparação",
    note: validating ? "Aguarde o resultado da conferência." : login || loginReady ? "Com a autorização marcada, o teste continua automaticamente após o login e a conferência dos pré-requisitos." : "Marcar a autorização não inicia o teste. Clique em “Continuar reexecução” para confirmar.",
    feedback: loginReady && !validating ? "Login reconhecido; aguardando sua autorização para continuar." : (preparation?.login_attempt?.reason_label || preparation?.reason_label || "").slice(0, 2000),
  };
  for (const name of Object.keys(fallback) as (keyof RerunPreparationPresentation)[]) {
    const provided = preparation?.presentation?.[name];
    if (typeof provided === "string" && (provided || name === "feedback")) fallback[name] = provided;
  }
  return fallback;
}

export function scenarioRerunFeedback(exploration: Exploration) {
  const rerun = exploration.rerun;
  if (!rerun || isLive(exploration.status)) return null;
  const blocked = (exploration.scenarios || []).find((scenario) => scenario.status.toLowerCase() === "blocked");
  const reason = rerun.reason || blocked?.evidence?.rerun_blocked_reason || exploration.error;
  const preparationMessages: Record<string, string> = {
    before_url_changed: "A página aberta não é a mesma em que o teste original começou. Faça login e abra a página esperada antes de confirmar a preparação.",
    before_effect_changed: "A página apresenta conteúdo ou controles diferentes do teste original. Recrie as condições iniciais antes de tentar novamente.",
    before_state_changed: "Os dados ou as opções da página estão diferentes do teste original. Recrie as condições iniciais antes de tentar novamente.",
    missing_target: "O controle usado pelo teste não foi encontrado na página preparada.",
    ambiguous_target: "Mais de um controle corresponde ao original. A ação não foi repetida para evitar escolher o controle errado.",
    cross_origin_source: "A página aberta está fora do site autorizado para este teste.",
    authentication_required: "O login ou outra etapa de autenticação precisa ser concluído antes de repetir o teste.",
  };
  const actionCompleted = (exploration.steps_completed || 0) > 0 || (exploration.actions_count || 0) > 0
    || (exploration.counters?.actions || 0) > 0 || (exploration.actions || []).some((action) => typeof action.result?.success === "boolean");
  const dispatched = rerun.action_dispatched === true || blocked?.evidence?.action_dispatched === true;
  const notStarted = !actionCompleted && !dispatched && exploration.status.toLowerCase() !== "failed"
    && (!!blocked || !!preparationMessages[reason || ""]);
  if (!notStarted && !exploration.error) return null;
  return {
    notStarted,
    title: notStarted ? "Reexecução não iniciada" : "A execução encontrou um erro",
    nextStep: notStarted ? "Inicie uma nova reexecução pela execução original e prepare o login e a página esperada antes de continuar." : undefined,
    detail: rerun.reason_label || blocked?.evidence?.rerun_blocked_explanation || preparationMessages[reason || ""]
      || (notStarted ? "A preparação do navegador não pôde ser confirmada. Nenhuma ação do CT foi executada; o relatório original permanece preservado." : exploration.error || "Não foi possível concluir a reexecução."),
  };
}

export function ScenarioRerunPreparation({ preparation, validating = false, compact = false }: { preparation?: RerunPreparation; validating?: boolean; compact?: boolean }) {
  const presentation = scenarioRerunPreparationPresentation(preparation, validating);
  const loginAttempt = preparation?.login_attempt;
  const loginAttemptLabel = loginAttempt?.status === "success" ? "Login realizado"
    : loginAttempt?.status === "rejected" ? "Login não realizado" : "Ajuste o login";
  const pageComparison = <div className="rerun-page-comparison" aria-label="Comparação da página para reexecução">
    <div><span>Página esperada pelo teste</span><strong title={preparation?.expected_url}>{preparation?.expected_url || "Consulte a página inicial do CT original"}</strong></div>
    <ArrowRight size={18} aria-hidden="true" />
    <div><span>Página aberta no navegador</span><strong title={preparation?.current_url}>{preparation?.current_url || "Aguardando leitura do navegador"}</strong></div>
  </div>;
  return <div className={`rerun-preparation-guide${compact ? " is-compact" : ""}`}>
    {presentation.feedback && <p className="rerun-preparation-reason" role="status">{loginAttempt?.status === "success" ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}<span>{presentation.feedback}</span></p>}
    <p className={compact ? "rerun-preparation-next-step" : undefined}>{presentation.instruction}</p>
    <details className="rerun-preparation-instructions"><summary>{presentation.details_label}</summary>
      {pageComparison}
      {loginAttempt && <p><strong>{loginAttemptLabel}.</strong> {loginAttempt.reason_label || (loginAttempt.status === "success" ? "O login foi concluído; os pré-requisitos do teste ainda serão conferidos." : "Confira o login no navegador antes de tentar novamente.")}</p>}
      {preparation?.instruction && preparation.instruction !== presentation.instruction && <p>{preparation.instruction}</p>}
      <p>Nenhuma ação do CT foi executada.</p>
    </details>
  </div>;
}

function formatTokens(value?: number) {
  return new Intl.NumberFormat("pt-BR").format(value || 0);
}

function formatCost(usage?: UsageBucket) {
  if (!usage?.cost_known || typeof usage.cost !== "number" || !Number.isFinite(usage.cost) || usage.cost < 0) return "—";
  return `$${usage.cost.toFixed(6)}${usage.cost_complete === false ? " (parcial)" : ""}`;
}

export function CostBreakdown({ usage }: { usage?: Exploration["usage"] }) {
  const costs = [
    { key: "jev", label: "Custo JEV", icon: BrainCircuit, bucket: usage?.by_role?.jev },
    { key: "auxiliary", label: "Custo LLM", icon: Sparkles, bucket: usage?.by_role?.auxiliary },
    { key: "total", label: "Custo total", icon: CircleDollarSign, bucket: usage },
  ];
  return <div className="cost-breakdown" role="group" aria-label="Custos da execução em dólares americanos">
    {costs.map(({ key, label, icon: Icon, bucket }) => {
      const value = formatCost(bucket);
      const note = !bucket && key !== "total"
        ? "Divisão por modelo não registrada nesta execução."
        : value === "—"
          ? "Custo não informado pelo provedor."
          : bucket?.cost_complete === false
            ? "Valor parcial: há chamadas sem custo informado."
            : "";
      return <div className="cost-breakdown-item" key={key}>
        <Icon size={17} aria-hidden="true" /><span>{label}</span><strong>{value}</strong>
        {note && <small>{note}</small>}
      </div>;
    })}
  </div>;
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return "Não foi possível concluir a solicitação.";
}

export function validateScenarioCapturePlan(value: unknown): ScenarioCapturePlan {
  const record = (item: unknown): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item);
  const exactKeys = (item: Record<string, unknown>, keys: string[]) => Object.keys(item).length === keys.length && keys.every((key) => Object.hasOwn(item, key));
  if (!record(value) || !exactKeys(value, ["version", "checkpoints"]) || value.version !== 1 || !Array.isArray(value.checkpoints) || value.checkpoints.length > 100) throw new Error("O plano de prints é inválido. Reabra o relatório pela aplicação e tente novamente.");
  const ids = new Set<string>(), positions = new Set<string>();
  const checkpoints = value.checkpoints.map((item) => {
    if (!record(item) || !exactKeys(item, ["id", "gap", "order"]) || typeof item.id !== "string" || !/^db-[0-9]{1,9}$/.test(item.id)
      || typeof item.gap !== "number" || !Number.isInteger(item.gap) || item.gap < 0 || item.gap > 10000
      || typeof item.order !== "number" || !Number.isInteger(item.order) || item.order < 0 || item.order > 10000
      || ids.has(item.id) || positions.has(`${item.gap}:${item.order}`)) throw new Error("O plano de prints contém uma pausa inválida ou repetida. Reabra o relatório pela aplicação.");
    ids.add(item.id); positions.add(`${item.gap}:${item.order}`);
    return { id: item.id, gap: item.gap, order: item.order };
  });
  return { version: 1, checkpoints };
}

export function validateCapturePlanForPreflight(plan: ScenarioCapturePlan | undefined, preflight: ScenarioRerunPreflight | null) {
  if (!plan?.checkpoints.length) return "";
  if (!preflight || !Number.isInteger(preflight.timeline_count) || (preflight.timeline_count || 0) < 0) return "Não foi possível conferir a posição dos prints neste CT. Atualize a aplicação e reabra o relatório.";
  if (plan.checkpoints.some((item) => item.gap > preflight.timeline_count!)) return "Uma pausa para print está fora dos passos deste CT. Reabra o relatório e confira as posições.";
  return "";
}

export function parseAppHash(hash: string): AppRoute {
  const rerun = hash.match(/^#\/reexecutar\/([^/?]+)\/([^/?]+)(?:\?(.*))?$/);
  try {
    if (rerun) {
      const route: AppRoute = { view: "detail", explorationId: decodeURIComponent(rerun[1]), rerunScenarioId: decodeURIComponent(rerun[2]) };
      if (rerun[3] !== undefined) {
        try {
          if (rerun[3].length > 30000) throw new Error("O plano de prints excedeu o limite permitido.");
          const parameters = new URLSearchParams(rerun[3]);
          if ([...parameters].length !== 1 || !parameters.has("capture_plan")) throw new Error("A solicitação contém parâmetros de reexecução desconhecidos. Reabra o relatório pela aplicação.");
          route.capturePlan = validateScenarioCapturePlan(JSON.parse(parameters.get("capture_plan")!));
        } catch (caught) { route.capturePlanError = caught instanceof SyntaxError ? "O plano de prints não pôde ser lido. Reabra o relatório pela aplicação." : errorMessage(caught); }
      }
      return route;
    }
  } catch { return { view: "dashboard" }; }
  const detail = hash.match(/^#\/exploracoes\/([^/]+)(\/acompanhar)?$/);
  try {
    if (detail) return { view: "detail", explorationId: decodeURIComponent(detail[1]), ...(detail[2] ? { liveRerun: true } : {}) };
  } catch { return { view: "dashboard" }; }
  const route = NAV_ITEMS.find((item) => item.hash === hash);
  return { view: route?.view || "dashboard" };
}

function parseRoute(): AppRoute {
  return parseAppHash(typeof window === "undefined" ? "#/dashboard" : window.location.hash || "#/dashboard");
}

export function connectReportToApplication(html: string, sourceExplorationId: string, appOrigin: string, apiBase?: string) {
  const url = new URL(appOrigin);
  if (!["http:", "https:"].includes(url.protocol) || url.origin !== appOrigin || !sourceExplorationId) return html;
  let apiUrl: string | undefined;
  if (typeof apiBase === "string" && !/[?#]/.test(apiBase)
      && ![...apiBase].some((character) => character.charCodeAt(0) <= 32 || character.charCodeAt(0) === 127)) {
    try {
      const api = new URL(apiBase);
      if (["http:", "https:"].includes(api.protocol) && !api.username && !api.password && !api.search && !api.hash) {
        apiUrl = api.href.replace(/\/$/, "");
      }
    } catch { /* An invalid optional API base leaves the offline report usable. */ }
  }
  const runtime = JSON.stringify({ app_url: url.origin, source_exploration_id: sourceExplorationId, ...(apiUrl ? { api_url: apiUrl } : {}) }).replace(/</g, "\\u003c");
  const clean = html.replace(/<script\b(?=[^>]*\bid=["']report-app-runtime["'])[^>]*>[\s\S]*?<\/script\s*>/gi, "");
  return clean.replace(/<head\b[^>]*>/i, (head) => head + '<script id="report-app-runtime" type="application/json">' + runtime + "</script>");
}

export async function confirmScenarioRerun(request: ApiRequest, sourceId: string, scenarioId: string, consent: boolean, available: boolean, capturePlan?: ScenarioCapturePlan) {
  if (!consent || !available) throw new Error("Confirme os efeitos da reexecução antes de iniciar.");
  const plan = capturePlan === undefined ? undefined : validateScenarioCapturePlan(capturePlan);
  return request<Exploration>(`/explorations/${encodeURIComponent(sourceId)}/scenarios/${encodeURIComponent(scenarioId)}/rerun`, {
    method: "POST", body: JSON.stringify({ confirm: true, ...(plan ? { capture_plan: plan } : {}) }),
  });
}

export function scenarioRerunCaptureKey(exploration: Exploration | null) {
  const checkpoint = exploration?.rerun?.capture_checkpoint;
  return checkpoint ? `${exploration?.id || ""}:${checkpoint.id}:${checkpoint.version}` : "";
}

export const RERUN_PRINT_MAX_BYTES = 10 * 1024 * 1024;

export function rerunPrintFileError(file: { size: number; type: string }) {
  if (!file.size) return "Esta imagem está vazia. Escolha outro arquivo.";
  if (file.size > RERUN_PRINT_MAX_BYTES) return "A imagem deve ter no máximo 10 MB. Escolha uma imagem menor.";
  if (file.type && !["image/png", "image/jpeg", "image/webp"].includes(file.type)) return "Use uma imagem PNG, JPG ou WEBP. Outros arquivos não podem ser anexados.";
  return "";
}

export function rerunPrintImageType(bytes: Uint8Array) {
  if (bytes.length >= 8 && [137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => bytes[index] === value)) return "image/png";
  if (bytes.length >= 3 && bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "image/jpeg";
  if (bytes.length >= 12 && [82, 73, 70, 70].every((value, index) => bytes[index] === value) && [87, 69, 66, 80].every((value, index) => bytes[index + 8] === value)) return "image/webp";
  return "";
}

export function rerunPrintDataUrlError(value: unknown) {
  if (typeof value !== "string") return "Adicione uma imagem antes de continuar.";
  const header = value.match(/^data:image\/(?:png|jpeg|webp);base64,/);
  if (!header) return "Não foi possível ler a imagem. Adicione um arquivo PNG, JPG ou WEBP novamente.";
  const encoded = value.slice(header[0].length);
  if (encoded.length > Math.ceil(RERUN_PRINT_MAX_BYTES / 3) * 4) return "A imagem deve ter no máximo 10 MB. Escolha uma imagem menor.";
  if (!encoded || encoded.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) return "Não foi possível ler a imagem. Adicione um arquivo PNG, JPG ou WEBP novamente.";
  const size = encoded.length / 4 * 3 - (encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0);
  if (size > RERUN_PRINT_MAX_BYTES) return "A imagem deve ter no máximo 10 MB. Escolha uma imagem menor.";
  return "";
}

export function rerunPrintPasteIsBlocked(target: Element | null, activeElement: Element | null, modalOpen: boolean) {
  const selector = "input, textarea, select, [contenteditable]:not([contenteditable='false']), [role='textbox'], dialog, [role='dialog']";
  return modalOpen || !!target?.closest(selector) || !!activeElement?.closest(selector);
}

async function readRerunPrintImage(file: File) {
  const invalid = rerunPrintFileError(file);
  if (invalid) throw new Error(invalid);
  const type = rerunPrintImageType(new Uint8Array(await file.slice(0, 12).arrayBuffer()));
  if (!type || file.type && file.type !== type) throw new Error("Este arquivo não é uma imagem PNG, JPG ou WEBP válida. Escolha outra imagem.");
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => typeof reader.result === "string" ? resolve(reader.result.replace(/^data:[^;]*;/, `data:${type};`)) : reject(new Error("Não foi possível ler a imagem."));
    reader.onerror = () => reject(new Error("Não foi possível ler a imagem. Tente escolher o arquivo novamente."));
    reader.onabort = () => reject(new Error("A leitura da imagem foi interrompida. Tente novamente."));
    reader.readAsDataURL(file);
  });
  const invalidData = rerunPrintDataUrlError(dataUrl);
  if (invalidData) throw new Error(invalidData);
  return { dataUrl, name: file.name || "Print colado", size: file.size };
}

export async function captureScenarioRerunPrint(request: ApiRequest, exploration: Exploration, checkpoint: RerunCaptureCheckpoint, imageDataUrl: string) {
  const current = exploration.rerun?.capture_checkpoint;
  if (exploration.rerun?.phase !== "waiting_capture" || !current || current.id !== checkpoint.id || current.version !== checkpoint.version
    || !/^db-[0-9]{1,9}$/.test(checkpoint.id) || !Number.isInteger(checkpoint.version) || checkpoint.version < 1) throw new Error("Esta pausa já mudou. Atualize a execução antes de capturar.");
  const invalidImage = rerunPrintDataUrlError(imageDataUrl);
  if (invalidImage) throw new Error(invalidImage);
  return request<Exploration>(`/explorations/${encodeURIComponent(exploration.id)}/capture-print`, {
    method: "POST", body: JSON.stringify({ confirm: true, checkpoint_id: checkpoint.id, checkpoint_version: checkpoint.version, image_data_url: imageDataUrl }),
  });
}

type RerunPrintPreview = { dataUrl: string; name: string; size: number };
type RerunPrintPreviewSnapshot = {
  checkpoint_id: string;
  checkpoint_version: number;
  revision: number;
  changed: boolean;
  image?: { data_url: string; name: string; size: number } | null;
};
type RerunPrintPreviewSession = {
  pending: boolean;
  beginSelection: () => boolean;
  cancelSelection: () => void;
  share: (image: RerunPrintPreview | null) => Promise<void>;
  retry: () => void;
};

export function ScenarioRerunCapture({ checkpoint, completed = 0, total = 0, busy = false, error = "", timeoutSeconds = 900, onCapture, request, explorationId }: {
  checkpoint?: RerunCaptureCheckpoint;
  completed?: number;
  total?: number;
  busy?: boolean;
  error?: string;
  timeoutSeconds?: number;
  onCapture: (imageDataUrl: string) => void;
  request?: ApiRequest;
  explorationId?: string;
}) {
  const [image, setImage] = useState<RerunPrintPreview | null>(null);
  const [selectionError, setSelectionError] = useState("");
  const [selecting, setSelecting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [feedbackVersion, setFeedbackVersion] = useState(0);
  const [previewSyncStatus, setPreviewSyncStatus] = useState<"local" | "loading" | "syncing" | "synced" | "error">("local");
  const [previewSyncError, setPreviewSyncError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const capturePanel = useRef<HTMLElement>(null);
  const captureHeading = useRef<HTMLHeadingElement>(null);
  const selectionVersion = useRef(0);
  const captureSubmitted = useRef(false);
  const dragDepth = useRef(0);
  const previewSession = useRef<RerunPrintPreviewSession | null>(null);
  const previewCheckpointId = checkpoint?.id;
  const previewCheckpointVersion = checkpoint?.version;
  const chooserId = useId();
  const selectImage = useCallback(async (files: File[]) => {
    if (busy || !checkpoint || previewSession.current?.pending) return;
    const version = ++selectionVersion.current;
    captureSubmitted.current = false;
    setSelectionError("");
    if (files.length !== 1) { setSelecting(false); setSelectionError("Adicione uma imagem por pausa. Escolha apenas um arquivo."); setFeedbackVersion((value) => value + 1); return; }
    const session = previewSession.current;
    if (session && !session.beginSelection()) return;
    setSelecting(true);
    try {
      const selected = await readRerunPrintImage(files[0]);
      if (version === selectionVersion.current) {
        setImage(selected); setFeedbackVersion((value) => value + 1);
        if (session && previewSession.current === session) void session.share(selected);
      }
    } catch (caught) {
      if (version === selectionVersion.current) {
        setSelectionError(errorMessage(caught)); setFeedbackVersion((value) => value + 1);
        if (session && previewSession.current === session) session.cancelSelection();
      }
    } finally { if (version === selectionVersion.current) setSelecting(false); }
  }, [busy, checkpoint]);
  useEffect(() => {
    const paste = (event: ClipboardEvent) => {
      if (busy || !checkpoint || rerunPrintPasteIsBlocked(event.target instanceof Element ? event.target : null, document.activeElement, !!document.querySelector("dialog[open], [role='dialog'][aria-modal='true']"))) return;
      const files = Array.from(event.clipboardData?.files || []);
      if (!files.length) return;
      event.preventDefault();
      void selectImage(files);
    };
    window.addEventListener("paste", paste);
    return () => window.removeEventListener("paste", paste);
  }, [busy, checkpoint, selectImage]);
  useEffect(() => {
    setImage(null); setSelectionError(""); setSelecting(false); setDragging(false);
    captureSubmitted.current = false;
    selectionVersion.current += 1;
    return () => { selectionVersion.current += 1; };
  }, [checkpoint?.id, checkpoint?.version]);
  useEffect(() => {
    if (!request || !explorationId || !previewCheckpointId || previewCheckpointVersion === undefined || busy) {
      previewSession.current = null;
      setPreviewSyncStatus("local"); setPreviewSyncError("");
      return;
    }
    // These images are temporary, shared previews, not attached evidence. Keep
    // them out of exploration state, browser storage, events and model input.
    const checkpointId = previewCheckpointId;
    const checkpointVersion = previewCheckpointVersion;
    const path = `/explorations/${encodeURIComponent(explorationId)}/capture-print/preview`;
    const query = `checkpoint_id=${encodeURIComponent(checkpointId)}&checkpoint_version=${checkpointVersion}`;
    let disposed = false;
    let revision: number | null = null;
    let polling = false;
    let unsynchronized = false;
    let desiredImage: RerunPrintPreview | null = null;
    const controllers = new Set<AbortController>();
    const current = () => !disposed && previewSession.current === session;
    const call = async (options: RequestOptions = {}, known = false) => {
      const controller = new AbortController();
      controllers.add(controller);
      try {
        return await request<RerunPrintPreviewSnapshot>(options.method === "POST" ? path : `${path}?${query}${known && revision !== null ? `&known_revision=${revision}` : ""}`, {
          ...options, cache: "no-store", signal: controller.signal,
        });
      } finally { controllers.delete(controller); }
    };
    const checkSnapshot = (snapshot: RerunPrintPreviewSnapshot) => {
      if (!snapshot || snapshot.checkpoint_id !== checkpointId || snapshot.checkpoint_version !== checkpointVersion
        || !Number.isInteger(snapshot.revision) || snapshot.revision < 0 || typeof snapshot.changed !== "boolean") throw new Error("A pausa do print mudou. Atualize a execução.");
      if (snapshot.changed && !Object.hasOwn(snapshot, "image")) throw new Error("Não foi possível sincronizar a prévia do print.");
      if (snapshot.image && (rerunPrintDataUrlError(snapshot.image.data_url) || !Number.isFinite(snapshot.image.size)
        || snapshot.image.size <= 0 || snapshot.image.size > RERUN_PRINT_MAX_BYTES)) throw new Error("A prévia recebida não é uma imagem válida.");
      return snapshot;
    };
    const applySnapshot = (snapshot: RerunPrintPreviewSnapshot, localImage?: RerunPrintPreview | null) => {
      revision = snapshot.revision;
      if (snapshot.changed) {
        const shared = snapshot.image;
        setImage(shared ? localImage?.dataUrl === shared.data_url ? localImage : {
          dataUrl: shared.data_url, name: String(shared.name || "Print sincronizado").slice(0, 256), size: shared.size,
        } : null);
        captureSubmitted.current = false;
      }
      setPreviewSyncStatus("synced");
    };
    const refresh = async () => {
      if (!current() || session.pending || polling || unsynchronized) return;
      polling = true;
      const selection = selectionVersion.current;
      try {
        const snapshot = checkSnapshot(await call({}, true));
        // A GET started before a local paste/removal must not put its old
        // selection back while the new file is being decoded or synchronized.
        if (!current() || session.pending || selection !== selectionVersion.current || unsynchronized) return;
        if (revision !== null && snapshot.revision < revision) return;
        applySnapshot(snapshot);
        setPreviewSyncError("");
      } catch (caught) {
        if (current() && !session.pending && selection === selectionVersion.current && !unsynchronized) {
          setPreviewSyncStatus("error");
          setPreviewSyncError(`Não foi possível atualizar a prévia entre as janelas. ${errorMessage(caught)}`);
        }
      } finally { polling = false; }
    };
    const share = async (selected: RerunPrintPreview | null) => {
      if (!current()) return;
      session.pending = true;
      unsynchronized = true;
      desiredImage = selected;
      setPreviewSyncStatus("syncing"); setPreviewSyncError("");
      try {
        if (revision === null) {
          const initial = checkSnapshot(await call());
          if (!current()) return;
          revision = initial.revision;
        }
        const snapshot = checkSnapshot(await call({ method: "POST", body: JSON.stringify({
          checkpoint_id: checkpointId, checkpoint_version: checkpointVersion,
          expected_revision: revision, image_data_url: selected?.dataUrl ?? null,
        }) }));
        if (!current()) return;
        unsynchronized = false;
        applySnapshot(snapshot, selected);
      } catch (caught) {
        if (!current()) return;
        const conflict = typeof caught === "object" && caught !== null && "status" in caught && caught.status === 409;
        if (conflict) {
          try {
            const latest = checkSnapshot(await call());
            if (!current()) return;
            unsynchronized = false;
            applySnapshot(latest);
            setPreviewSyncError("O print mudou em outra janela. A prévia mais recente está aqui; confira antes de confirmar ou escolha outra imagem.");
          } catch {
            if (current()) {
              setPreviewSyncStatus("error");
              setPreviewSyncError("O print mudou em outra janela e ainda não foi possível atualizar a prévia. Tente sincronizar novamente.");
            }
          }
        } else {
          setPreviewSyncStatus("error");
          setPreviewSyncError(`A imagem ainda não está sincronizada com a outra janela. ${errorMessage(caught)}`);
        }
      } finally { if (current()) session.pending = false; }
    };
    const session: RerunPrintPreviewSession = {
      pending: false,
      beginSelection() {
        if (!current() || session.pending) return false;
        session.pending = true;
        setPreviewSyncStatus("syncing"); setPreviewSyncError("");
        return true;
      },
      cancelSelection() {
        if (!current()) return;
        session.pending = false;
        setPreviewSyncStatus(unsynchronized ? "error" : revision === null ? "loading" : "synced");
      },
      share,
      retry() {
        if (!current() || session.pending) return;
        if (unsynchronized) void share(desiredImage);
        else void refresh();
      },
    };
    previewSession.current = session;
    setPreviewSyncStatus("loading"); setPreviewSyncError("");
    void refresh();
    const interval = window.setInterval(() => void refresh(), 1000);
    return () => {
      disposed = true;
      for (const controller of controllers) controller.abort();
      if (previewSession.current === session) previewSession.current = null;
      window.clearInterval(interval);
    };
  }, [request, explorationId, previewCheckpointId, previewCheckpointVersion, busy]);
  useEffect(() => {
    if (!feedbackVersion && !error) return;
    captureHeading.current?.focus({ preventScroll: true });
    capturePanel.current?.scrollIntoView({ block: "start", behavior: "instant" });
  }, [feedbackVersion, error]);
  useEffect(() => { if (!busy || error) captureSubmitted.current = false; }, [busy, error]);
  const removeImage = () => {
    if (busy || previewSession.current?.pending) return;
    selectionVersion.current += 1; captureSubmitted.current = false; setSelecting(false); setImage(null); setSelectionError(""); setFeedbackVersion((value) => value + 1);
    if (fileInput.current) fileInput.current.value = "";
    if (previewSession.current) void previewSession.current.share(null);
  };
  const previewSyncBusy = previewSyncStatus === "syncing";
  const previewSyncBlocked = !!request && !!explorationId && previewSyncStatus !== "synced";
  const captureLimit = Number.isFinite(timeoutSeconds) && timeoutSeconds > 0 ? timeoutSeconds : 900;
  const captureLimitLabel = captureLimit >= 60 ? `${Math.ceil(captureLimit / 60)} minutos` : `${Math.ceil(captureLimit)} segundos`;
  return <section ref={capturePanel} className="panel rerun-capture" aria-label="Aguardar print">
    <div className="rerun-capture-heading"><span className="rerun-capture-symbol"><ImagePlus size={23} /></span><div><span className="section-kicker"><span className="live-pulse" />{busy ? "Enviando sua imagem" : "O teste espera por você"}</span><h2 ref={captureHeading} tabIndex={-1}>{busy ? "Anexando seu print ao relatório…" : image ? "Confira o print e confirme o envio" : "Adicione o print que você quiser"}</h2><p>Vale uma imagem de qualquer tela ou aplicativo. Cole com <kbd>Ctrl</kbd> + <kbd>V</kbd>, arraste um arquivo ou escolha uma imagem abaixo.</p></div></div>
      <div className="rerun-capture-meta"><span><MonitorPlay size={15} />Print {Math.min(completed + 1, total || completed + 1)} de {total || completed + 1}</span>{checkpoint && <span>Posição planejada: {checkpoint.gap === 0 ? "antes da primeira etapa" : `após a etapa ${checkpoint.gap}`}</span>}<span><Clock3 size={14} />Até {captureLimitLabel} para confirmar</span></div>
    <input ref={fileInput} id={chooserId} className="rerun-print-input" type="file" accept="image/png,image/jpeg,image/webp" disabled={busy || previewSyncBusy || !checkpoint} onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ""; if (files.length) void selectImage(files); }} />
    <div className="rerun-capture-workspace">
    <div className={`rerun-print-dropzone ${dragging ? "is-dragging" : ""} ${image ? "has-image" : ""} ${busy ? "is-busy" : ""}`} onDragEnter={(event) => { event.preventDefault(); if (!busy) { dragDepth.current += 1; setDragging(true); } }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = busy ? "none" : "copy"; }} onDragLeave={(event) => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }} onDrop={(event) => { event.preventDefault(); dragDepth.current = 0; setDragging(false); if (!busy) void selectImage(Array.from(event.dataTransfer.files)); }}>
      {image ? <>
        <div className="rerun-print-preview"><img src={image.dataUrl} alt="Prévia do print escolhido para anexar" onError={() => { removeImage(); setSelectionError("Não foi possível abrir esta imagem. Cole ou escolha outro arquivo PNG, JPG ou WEBP."); }} /></div>
        <div className="rerun-print-file"><span className="rerun-print-ready"><ImagePlus size={17} /></span><div>
          <strong title={image.name}>{image.name}</strong>
          <small>{image.size < 1024 * 1024 ? `${Math.max(1, Math.round(image.size / 1024))} KB` : `${(image.size / 1024 / 1024).toFixed(1)} MB`} · {request && explorationId ? previewSyncBusy ? "Sincronizando prévia" : "Ainda não salvo no relatório" : "Selecionado, ainda não enviado"}</small>
        </div><button type="button" className="button secondary small" disabled={busy || selecting || previewSyncBusy} onClick={() => fileInput.current?.click()}><RefreshCcw size={14} />Trocar</button><button type="button" className="icon-button" aria-label="Remover imagem escolhida" disabled={busy || selecting || previewSyncBusy} onClick={removeImage}><Trash2 size={17} /></button></div>
      </> : <div className="rerun-print-empty"><span className="rerun-print-empty-icon">{selecting ? <Spinner size={23} /> : dragging ? <Upload size={25} /> : <ClipboardPaste size={25} />}</span><h3>{selecting ? "Preparando sua imagem…" : dragging ? "Solte a imagem aqui" : "Cole ou arraste seu print aqui"}</h3><p>O print não precisa ser da página testada.<br />PNG, JPG ou WEBP · Até 10 MB · Uma imagem por pausa</p><button type="button" className="button secondary" onClick={() => fileInput.current?.click()} disabled={busy || selecting || previewSyncBusy || !checkpoint}><Upload size={15} />Escolher imagem</button><small>Ou use Ctrl + V nesta tela</small></div>}
    </div>
    <div className="rerun-capture-confirmation">
      <div className={`rerun-print-confirm-status${image ? " is-selected" : ""}`} role="status" aria-live="polite" aria-atomic="true"><span aria-hidden="true">{busy || selecting || previewSyncBusy ? <Spinner size={20} /> : image ? <ClipboardPaste size={20} /> : <ImagePlus size={20} />}</span><strong>{busy ? "Enviando seu print…" : selecting ? "Verificando a imagem…" : previewSyncBusy ? "Sincronizando a prévia nas duas janelas…" : image ? "Print selecionado. Falta confirmar o envio." : "Primeiro, adicione uma imagem."}</strong></div>
      <p className="rerun-print-confirm-copy">{request && explorationId ? previewSyncStatus === "synced" ? "Prévia sincronizada nas duas janelas. Só será salva no relatório quando você confirmar abaixo." : "A prévia aparece nas duas janelas. Para salvar no relatório e retomar o teste, confirme o envio abaixo." : image ? "Confira a prévia. Para salvar esta imagem no novo relatório e retomar o teste, clique no botão abaixo." : "Depois de escolher seu print, confira a prévia e confirme o envio aqui."}</p>
    {(selectionError || error || previewSyncError) && <p className="rerun-error" role="alert">{selectionError || error || previewSyncError}</p>}
      {previewSyncStatus === "error" && <button type="button" className="button secondary small" disabled={busy || selecting} onClick={() => previewSession.current?.retry()}><RefreshCcw size={14} />Tentar sincronizar</button>}
      <button type="button" className="button primary" onClick={() => { if (!image || selecting || busy || previewSyncBlocked || previewSession.current?.pending || captureSubmitted.current || !checkpoint) return; captureSubmitted.current = true; onCapture(image.dataUrl); }} disabled={busy || selecting || previewSyncBlocked || !image || !checkpoint}>{busy || selecting || previewSyncBusy ? <Spinner /> : <ArrowRight size={17} />}{busy ? "Salvando print…" : "Anexar print e continuar"}</button>
      <p className="rerun-capture-note"><ShieldCheck size={15} /><span>Você escolhe a imagem; ela não aprova nem reprova o teste. Sem confirmação, a execução não avança automaticamente.</span></p>
    </div>
    </div>
  </section>;
}

function resolveAssetUrl(value?: string | null) {
  if (!value) return null;
  if (/^https?:\/\//i.test(value) || value.startsWith("data:")) return value;
  try {
    const api = new URL(DEFAULT_API_URL);
    return new URL(value, api.origin).toString();
  } catch {
    return value;
  }
}

function activityCopy(event: ExplorationEvent) {
  const payload = event.payload || {};
  const scenario = typeof payload.scenario_id === "string" ? payload.scenario_id : "Caso de teste";
  const routingReason = String(payload.reason || "");
  const routingLabels: Record<string, string> = {
    single_clear_control: "controle único e claro",
    clear_safe_frontier: "próxima opção segura e inequívoca",
    conventional_form: "formulário convencional",
    known_navigation: "navegação conhecida",
    ambiguous_duplicate_controls: "controles com o mesmo nome",
    ambiguous_business_control: "resultado ou controle de negócio ambíguo",
    new_complex_state: "nova tela com escolhas de naturezas diferentes",
    two_attempts_without_progress: "duas tentativas sem progresso",
    no_safe_deterministic_candidate: "nenhuma ação local segura conhecida",
    action_requires_jev_judgment: "ação que exige julgamento de QA",
  };
  const fallback: Record<string, { title: string; detail?: string }> = {
    started: { title: "Exploração iniciada", detail: "O navegador foi preparado para o JEV." },
    browser_launched: { title: "Aplicação aberta no navegador" },
    coverage_scope: { title: "Escopo de cobertura definido" },
    auxiliary_evidence_mode: { title: "Modo de evidência configurado", detail: payload.mode === "visual" ? "A LLM auxiliar receberá texto + screenshot." : "A LLM auxiliar receberá somente texto estruturado; screenshots ficam locais." },
    jev_invocation_policy: { title: "Política econômica do JEV ativada", detail: "Ações óbvias serão resolvidas localmente; ambiguidades e falta de progresso escalam para o JEV." },
    decision_routed_locally: { title: "Ação resolvida sem chamar o JEV", detail: routingLabels[routingReason] || event.message },
    jev_consulted: { title: "JEV acionado para uma decisão de QA", detail: routingLabels[routingReason] || event.message },
    action_started: { title: `Executando ação ${String(payload.step || "")}`.trim(), detail: String(payload.action || event.message || "") },
    action_completed: { title: `Ação ${String(payload.step || "")} concluída`.trim(), detail: event.message },
    waiting_input: { title: "Aguardando uma ação sua", detail: event.message },
    limit_reached: { title: "Limite de ações atingido", detail: "A execução foi preservada com cobertura parcial." },
    coverage_complete: { title: "Cobertura concluída", detail: "O JEV esgotou as opções alcançáveis." },
    coverage_complete_with_restrictions: { title: "Cobertura alcançável concluída", detail: "As verificações que não puderam ser concluídas foram documentadas com suas limitações." },
    coverage_stalled: { title: "Exploração bloqueada", detail: event.message },
    scenario_parallel_started: { title: "CTs ativados em paralelo", detail: "Cada evidência válida já começa a virar caso de teste enquanto o navegador continua." },
    scenario_draft_created: { title: `${scenario} preservado ao vivo`, detail: "A evidência local já está segura; a LLM auxiliar pode aprimorá-la em segundo plano." },
    scenario_draft_updated: { title: `${scenario} atualizado`, detail: "Uma tentativa mais recente substituiu a evidência provisória sem duplicar o caso." },
    scenario_draft_removed: { title: `${scenario} provisório removido`, detail: "Uma nova tentativa mostrou que não havia falha verificável." },
    scenario_enrichment_backlog_full: { title: `${scenario} preservado localmente`, detail: "A fila auxiliar atingiu o limite de segurança, mas o CT e sua evidência não foram perdidos." },
    scenario_enrichment_worker_unavailable: { title: `${scenario} preservado localmente`, detail: "O worker auxiliar ficou indisponível; a exploração continuou e manteve o CT local." },
    scenario_preparation_started: { title: "Preparando os casos de teste", detail: event.message },
    scenario_drafts_ready: { title: "Todos os CTs já estão seguros", detail: event.message },
    scenario_enrichment_started: { title: "LLM auxiliar aprimorando os CTs", detail: event.message },
    scenario_enriched: { title: `${scenario} aprimorado`, detail: "Documentado pela LLM auxiliar a partir da evidência capturada." },
    scenario_enrichment_skipped: { title: `${scenario} preservado`, detail: "A evidência local foi mantida; a LLM auxiliar não concluiu esse aprimoramento." },
    scenario_omitted_no_evidence: { title: "Ação sem evidência não virou CT", detail: "A interação foi executada, mas não mudou a tela nem produziu uma validação confiável." },
    scenario_postprocessing_completed: { title: "Casos de teste finalizados", detail: event.message },
    scenario_created: { title: `${scenario} criado`, detail: "Documentado pela LLM auxiliar." },
    scenario_documentation_fallback: { title: `${scenario} criado com evidência local`, detail: "O caso continua válido; a LLM auxiliar não concluiu o enriquecimento no tempo disponível." },
    report_generation_started: { title: "Montando o relatório", detail: event.message },
    report_ready: { title: "Relatório pronto", detail: "O relatório completo já pode ser aberto." },
    report_failed: { title: "Falha ao montar o relatório", detail: event.message },
    failed: { title: "Execução interrompida", detail: event.message },
    cancelled: { title: "Execução cancelada", detail: event.message },
  };
  return fallback[event.type || ""] || {
    title: event.title || event.message || event.type || "Atualização da exploração",
    detail: event.detail || (event.title ? event.message : undefined),
  };
}

function Spinner({ size = 16 }: { size?: number }) {
  return <LoaderCircle size={size} className="spin" aria-hidden="true" />;
}

function StatusBadge({ status, completionType }: { status?: string; completionType?: string | null }) {
  if ((status || "").toLowerCase() === "completed" && completionType === "coverage_complete_with_restrictions") {
    return <span className="status-badge status-warning"><span className="status-dot" />Concluída com restrições</span>;
  }
  const current = explorationStatus(status);
  return (
    <span className={`status-badge status-${current.tone}`}>
      <span className="status-dot" />
      {current.label}
    </span>
  );
}

function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: typeof History;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon size={24} /></span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

function Modal({
  title,
  description,
  onClose,
  children,
  wide = false,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop">
      <section
        className={`modal-card${wide ? " modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <div className="modal-head">
          <div>
            <h2 id="modal-title">{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button ref={closeRef} className="icon-button" onClick={onClose} aria-label="Fechar janela">
            <X size={18} />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

function ArchitectureFlow({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`architecture-flow${compact ? " compact" : ""}`} aria-label="Arquitetura do AutoJev">
      <div className="architecture-node node-primary">
        <span className="node-icon"><BrainCircuit size={20} /></span>
        <div><strong>JEV</strong><small>Agente QA · decide</small></div>
      </div>
      <span className="flow-arrow"><ArrowRight size={17} /></span>
      <div className="architecture-node">
        <span className="node-icon"><Code2 size={20} /></span>
        <div><strong>Playwright</strong><small>Executor controlado</small></div>
      </div>
      <span className="flow-arrow"><ArrowRight size={17} /></span>
      <div className="architecture-node">
        <span className="node-icon"><Globe2 size={20} /></span>
        <div><strong>Browser</strong><small>Aplicação real</small></div>
      </div>
      <span className="flow-arrow"><ArrowRight size={17} /></span>
      <div className="architecture-node node-observation">
        <span className="node-icon"><Eye size={20} /></span>
        <div><strong>Observações</strong><small>Screenshot + DOM</small></div>
      </div>
      <span className="flow-arrow"><ArrowRight size={17} /></span>
      <div className="architecture-node node-auxiliary">
        <span className="node-icon"><Sparkles size={20} /></span>
        <div><strong>LLM auxiliar</strong><small>Interpreta evidências</small></div>
      </div>
      <span className="flow-arrow"><ArrowRight size={17} /></span>
      <div className="architecture-node">
        <span className="node-icon"><FileCheck2 size={20} /></span>
        <div><strong>Cenários</strong><small>Testes reproduzíveis</small></div>
      </div>
    </div>
  );
}

export default function AutoJevApp() {
  const [route, setRoute] = useState<AppRoute>({ view: "dashboard" });
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [explorations, setExplorations] = useState<Exploration[]>([]);
  const [selectedExploration, setSelectedExploration] = useState<Exploration | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [settledExplorationId, setSettledExplorationId] = useState<string | null>(null);
  const [apiState, setApiState] = useState<"checking" | "online" | "offline">("checking");
  const [connectionError, setConnectionError] = useState("");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [providerModal, setProviderModal] = useState(false);
  const [reportData, setReportData] = useState<unknown | null>(null);
  const [pollInterval, setPollInterval] = useState(3000);
  const modelCatalogRefreshes = useRef(new Set<string>());
  const activeExplorationId = useRef<string | undefined>(undefined);

  const apiBase = normalizeBaseUrl(DEFAULT_API_URL);

  const request = useCallback(
    async <T,>(path: string, options: RequestOptions = {}): Promise<T> => {
      const { raw, ...fetchOptions } = options;
      const response = await fetch(`${apiBase}${path}`, {
        ...fetchOptions,
        headers: {
          ...(fetchOptions.body ? { "Content-Type": "application/json" } : {}),
          ...fetchOptions.headers,
        },
      });
      if (!response.ok) {
        let message = `A API respondeu com erro ${response.status}.`;
        try {
          const body = (await response.json()) as Record<string, unknown>;
          message = String(body.detail || body.message || message);
        } catch {
          // Mantém a mensagem baseada no status quando não há JSON.
        }
        throw Object.assign(new Error(message), { status: response.status });
      }
      if (raw) return response as T;
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    },
    [apiBase],
  );

  const toast = useCallback((message: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, tone, message }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 4200);
  }, []);

  const loadCore = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    const results = await Promise.allSettled([
      request<unknown>("/providers"),
      request<unknown>("/models"),
      request<unknown>("/explorations"),
    ]);

    const successful = results.filter((result) => result.status === "fulfilled").length;
    if (results[0].status === "fulfilled") {
      const loadedProviders = listFrom<Provider>(results[0].value, ["providers", "items", "data"]);
      setProviders(loadedProviders);
      const openRouterProviders = loadedProviders.filter((provider) =>
        (provider.has_api_key || provider.api_key_configured)
        && isOpenRouterUrl(provider.base_url)
        && !modelCatalogRefreshes.current.has(provider.id)
        && (
          !provider.models?.some((model) => modelValue(model) === "typesafe/jev-1.13")
          || provider.models?.some((model) => !modelInputCapabilitiesKnown(model))
        )
      );
      if (openRouterProviders.length) {
        openRouterProviders.forEach((provider) => modelCatalogRefreshes.current.add(provider.id));
        await Promise.allSettled(
          openRouterProviders.map((provider) => request(`/providers/${encodeURIComponent(provider.id)}/discover-models`, { method: "POST" })),
        );
        const refreshed = await request<unknown>("/providers").catch(() => null);
        if (refreshed) setProviders(listFrom<Provider>(refreshed, ["providers", "items", "data"]));
      }
    }
    if (results[1].status === "fulfilled") {
      setModels(listFrom<Model>(results[1].value, ["models", "items", "data"]));
    }
    if (results[2].status === "fulfilled") {
      setExplorations(listFrom<Exploration>(results[2].value, ["explorations", "items", "data"]));
    }

    if (successful > 0) {
      setApiState("online");
      setConnectionError("");
    } else {
      setApiState("offline");
      const rejected = results.find((result) => result.status === "rejected") as PromiseRejectedResult | undefined;
      setConnectionError(rejected ? errorMessage(rejected.reason) : "Servidor indisponível.");
    }
    setLoading(false);
  }, [request]);

  const loadExploration = useCallback(async (id: string, quiet = false) => {
    if (!quiet) setDetailLoading(true);
    try {
      const data = await request<Exploration>(`/explorations/${encodeURIComponent(id)}`);
      if (activeExplorationId.current === id) setSelectedExploration(data);
      setExplorations((current) => current.map((item) => {
        if (item.id !== data.id) return item;
        return {
          ...item,
          status: data.status,
          progress: data.progress,
          updated_at: data.updated_at,
          current_action: data.current_action,
          counters: data.counters,
          usage: data.usage,
          screenshot_url: data.screenshot_url,
        };
      }));
      setApiState("online");
      setConnectionError("");
      return data;
    } catch (error) {
      if (activeExplorationId.current === id) {
        setConnectionError(errorMessage(error));
        if (!quiet) toast(errorMessage(error), "error");
      }
      return null;
    } finally {
      if (activeExplorationId.current === id) {
        setDetailLoading(false);
        setSettledExplorationId(id);
      }
    }
  }, [request, toast]);

  useEffect(() => {
    const saved = window.localStorage.getItem("autojev-poll-interval");
    if (saved && Number(saved) >= 1500) setPollInterval(Number(saved));
    const onHash = () => {
      const nextRoute = parseRoute();
      activeExplorationId.current = nextRoute.explorationId;
      setRoute(nextRoute);
      setMobileNavOpen(false);
    };
    if (!window.location.hash) window.location.hash = "#/dashboard";
    onHash();
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    void loadCore();
  }, [loadCore]);

  useEffect(() => {
    if (route.view !== "detail" || !route.explorationId) {
      setSelectedExploration(null);
      return;
    }
    void loadExploration(route.explorationId);
  }, [route, loadExploration]);

  useEffect(() => {
    if (route.view !== "detail" || !route.explorationId || !explorationDetailState(route.explorationId, selectedExploration, detailLoading, settledExplorationId).shouldPoll) return;
    const timer = window.setInterval(async () => {
      if (!explorationDetailState(route.explorationId, selectedExploration, detailLoading, settledExplorationId).shouldPoll) {
        window.clearInterval(timer);
        return;
      }
      const data = await loadExploration(route.explorationId as string, true);
      if (data && !isLive(data.status)) void loadCore(true);
    }, pollInterval);
    return () => window.clearInterval(timer);
  }, [route, pollInterval, loadExploration, loadCore, selectedExploration, detailLoading, settledExplorationId]);

  useEffect(() => {
    if (route.view === "detail" || !explorations.some((item) => isLive(item.status))) return;
    const timer = window.setInterval(() => void loadCore(true), pollInterval);
    return () => window.clearInterval(timer);
  }, [route.view, explorations, pollInterval, loadCore]);

  const navigate = (hash: string) => {
    if (window.location.hash === hash) setRoute(parseRoute());
    else window.location.hash = hash;
  };

  const refreshAll = async () => {
    await loadCore(true);
    if (route.view === "detail" && route.explorationId) {
      await loadExploration(route.explorationId, true);
    }
    toast("Dados atualizados.", "success");
  };

  const currentNavView = route.view === "detail" ? "explorations" : route.view;
  const activeUsage = explorations
    .filter((item) => isLive(item.status))
    .reduce(
      (total, item) => ({
        prompt_tokens: total.prompt_tokens + (item.usage?.prompt_tokens || 0),
        completion_tokens: total.completion_tokens + (item.usage?.completion_tokens || 0),
        total_tokens: total.total_tokens + (item.usage?.total_tokens || 0),
        cost: total.cost + (item.usage?.cost || 0),
        cost_known: total.cost_known || Boolean(item.usage?.cost_known),
        cost_complete: total.cost_complete && Boolean(item.usage?.cost_known) && item.usage?.cost_complete !== false,
      }),
      { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost: 0, cost_known: false, cost_complete: true },
    );
  const hasActiveUsage = explorations.some((item) => isLive(item.status));
  const totalModels = useMemo(
    () => providers.reduce((total, provider) => total + (provider.models?.filter((model) => model.enabled !== false).length || 0), 0) || models.filter((model) => model.enabled !== false).length,
    [providers, models],
  );

  const renderView = () => {
    switch (route.view) {
      case "new":
        return (
          <NewExplorationView
            providers={providers}
            models={models}
            loading={loading}
            request={request}
            toast={toast}
            navigate={navigate}
            reloadModels={loadCore}
            onCreated={(exploration) => setExplorations((current) => [exploration, ...current])}
          />
        );
      case "explorations":
        return <ExplorationsView explorations={explorations} loading={loading} navigate={navigate} />;
      case "providers":
        return (
          <ProvidersView
            providers={providers}
            loading={loading}
            request={request}
            toast={toast}
            reload={() => loadCore(true)}
            onAdd={() => setProviderModal(true)}
          />
        );
      case "settings":
        return (
          <SettingsView
            apiBase={apiBase}
            apiState={apiState}
            pollInterval={pollInterval}
            setPollInterval={(value) => {
              setPollInterval(value);
              window.localStorage.setItem("autojev-poll-interval", String(value));
              toast("Preferência de atualização salva.", "success");
            }}
          />
        );
      case "detail":
        return (
          <ExplorationDetailView
            key={route.explorationId}
            liveRerun={route.liveRerun}
            exploration={explorationDetailState(route.explorationId, selectedExploration, detailLoading, settledExplorationId).exploration}
            loading={explorationDetailState(route.explorationId, selectedExploration, detailLoading, settledExplorationId).loading}
            providers={providers}
            request={request}
            toast={toast}
            navigate={navigate}
            onRefresh={() => route.explorationId && loadExploration(route.explorationId)}
            onCancelled={() => {
              if (route.explorationId) void loadExploration(route.explorationId);
              void loadCore(true);
            }}
            onReport={setReportData}
            apiBase={apiBase}
            pollInterval={pollInterval}
          />
        );
      default:
        return (
          <DashboardView
            explorations={explorations}
            providers={providers}
            totalModels={totalModels}
            loading={loading}
            navigate={navigate}
          />
        );
    }
  };

  return (
    <div className={`app-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <a className="skip-link" href="#main-content">Pular para o conteúdo</a>
      {mobileNavOpen && <button className="nav-scrim" onClick={() => setMobileNavOpen(false)} aria-label="Fechar menu" />}
      <aside className={`sidebar${mobileNavOpen ? " mobile-open" : ""}`}>
        <div className="brand-row">
          <button className="brand" onClick={() => navigate("#/dashboard")} aria-label="Ir para o dashboard">
            <span className="brand-mark"><Bot size={22} strokeWidth={2.1} /></span>
            <span className="brand-copy"><strong>AutoJev</strong><small>Autonomous QA</small></span>
          </button>
          <button className="mobile-close icon-button" onClick={() => setMobileNavOpen(false)} aria-label="Fechar menu">
            <X size={18} />
          </button>
        </div>

        <nav className="primary-nav" aria-label="Navegação principal">
          <span className="nav-section-label">Workspace</span>
          {NAV_ITEMS.slice(0, 3).map((item) => {
            const Icon = item.icon;
            const active = currentNavView === item.view;
            return (
              <button key={item.view} className={`nav-item${active ? " active" : ""}`} onClick={() => navigate(item.hash)} title={sidebarCollapsed ? item.label : undefined}>
                <Icon size={18} />
                <span>{item.label}</span>
                {item.view === "explorations" && explorations.filter((item) => isLive(item.status)).length > 0 && (
                  <span className="nav-count">{explorations.filter((item) => isLive(item.status)).length}</span>
                )}
              </button>
            );
          })}
          <span className="nav-section-label secondary-label">Sistema</span>
          {NAV_ITEMS.slice(3).map((item) => {
            const Icon = item.icon;
            const active = currentNavView === item.view;
            return (
              <button key={item.view} className={`nav-item${active ? " active" : ""}`} onClick={() => navigate(item.hash)} title={sidebarCollapsed ? item.label : undefined}>
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          {hasActiveUsage && (
            <div className="usage-indicator" title="Consumo acumulado das explorações em andamento">
              <div className="usage-indicator-heading"><Activity size={14} /><strong>Uso ao vivo</strong><span className="live-mini"><span />ao vivo</span></div>
              <div className="usage-indicator-values">
                <span><Hash size={13} /><strong>{formatTokens(activeUsage.total_tokens)}</strong><small>tokens</small></span>
                <span><CircleDollarSign size={13} /><strong>{formatCost(activeUsage)}</strong><small>custo total</small></span>
              </div>
            </div>
          )}
          <div className={`api-indicator api-${apiState}`} title={`API: ${apiBase}`}>
            <span className="api-dot" />
            <div><strong>{apiState === "online" ? "API conectada" : apiState === "checking" ? "Verificando API" : "API indisponível"}</strong><small>FastAPI · {apiState === "online" ? "operacional" : "localhost:8000"}</small></div>
          </div>
          <button className="collapse-button" onClick={() => setSidebarCollapsed((value) => !value)} aria-label={sidebarCollapsed ? "Expandir menu lateral" : "Recolher menu lateral"}>
            {sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
            <span>Recolher menu</span>
          </button>
        </div>
      </aside>

      <div className="app-main">
        <header className="mobile-header">
          <button className="icon-button" onClick={() => setMobileNavOpen(true)} aria-label="Abrir menu">
            <Menu size={20} />
          </button>
          <button className="mobile-brand" onClick={() => navigate("#/dashboard")}><span className="brand-mark"><Bot size={19} /></span><strong>AutoJev</strong></button>
          <button className="icon-button" onClick={() => void refreshAll()} aria-label="Atualizar dados"><RefreshCcw size={18} /></button>
        </header>

        {connectionError && (
          <div className="connection-banner" role="status">
            <Unplug size={17} />
            <span><strong>Não foi possível acessar a API.</strong> A interface continuará disponível e tentará novamente quando você atualizar.</span>
            <button onClick={() => void loadCore()}>Tentar novamente</button>
          </div>
        )}

        <main id="main-content" className="page-content">
          {renderView()}
        </main>
      </div>

      <div className="toast-stack" aria-live="polite">
        {toasts.map((item) => (
          <div className={`toast toast-${item.tone}`} key={item.id}>
            {item.tone === "success" ? <CheckCircle2 size={18} /> : item.tone === "error" ? <AlertCircle size={18} /> : <Activity size={18} />}
            <span>{item.message}</span>
            <button onClick={() => setToasts((current) => current.filter((toastItem) => toastItem.id !== item.id))} aria-label="Fechar aviso"><X size={15} /></button>
          </div>
        ))}
      </div>

      {providerModal && (
        <AddProviderModal
          request={request}
          onClose={() => setProviderModal(false)}
          onSaved={async () => {
            setProviderModal(false);
            await loadCore(true);
            toast("Provedor adicionado com sucesso.", "success");
          }}
          toast={toast}
        />
      )}

      {reportData !== null && (
        <Modal title="Relatório da exploração" description="Resultado consolidado pelo AutoJev." onClose={() => setReportData(null)} wide>
          <div className="report-preview">
            {typeof reportData === "string" ? <pre>{reportData}</pre> : <pre>{JSON.stringify(reportData, null, 2)}</pre>}
          </div>
        </Modal>
      )}
      {route.explorationId && route.rerunScenarioId && <ScenarioRerunDialog
        key={`${route.explorationId}/${route.rerunScenarioId}/${JSON.stringify(route.capturePlan)}/${route.capturePlanError || ""}`}
        sourceId={route.explorationId}
        scenarioId={route.rerunScenarioId}
        capturePlan={route.capturePlan}
        capturePlanError={route.capturePlanError}
        request={request}
        onClose={() => navigate(`#/exploracoes/${encodeURIComponent(route.explorationId!)}`)}
        onCreated={(created) => {
          setExplorations((current) => [created, ...current.filter((item) => item.id !== created.id)]);
          toast("Nova execução criada. Prepare o navegador e confirme a continuação para repetir o CT.", "success");
          navigate(`#/exploracoes/${encodeURIComponent(created.id)}/acompanhar`);
        }}
      />}
    </div>
  );
}

export function ScenarioRerunConsent({ preflight, loading, error, busy, consent, capturePlan, onConsent, onConfirm, onClose }: {
  preflight: ScenarioRerunPreflight | null;
  loading: boolean;
  error: string;
  busy: boolean;
  consent: boolean;
  capturePlan?: ScenarioCapturePlan;
  onConsent: (value: boolean) => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const checkpointCount = capturePlan?.checkpoints.length || 0;
  return <div className="modal-form rerun-confirmation">
    {loading ? <p className="rerun-loading" role="status"><Spinner />Verificando se o CT pode ser reexecutado…</p> : error ? <p className="rerun-error" role="alert">{error}</p> : preflight && <>
      <div className="rerun-case"><span className="section-kicker">{preflight.scenario_id || "Caso de teste original"}</span><strong>{preflight.title || "Reexecução de um CT"}</strong>{preflight.url && <span>{preflight.url}</span>}</div>
      {!preflight.available ? <div className="rerun-unavailable" role="status"><AlertCircle size={20} /><div><strong>Reexecução indisponível para este CT</strong><p>{preflight.reason || "Esta execução não preservou uma receita segura para repetir a ação. Execute uma nova exploração para registrar receitas de reexecução."}</p></div></div> : <>
        <div className="rerun-effects"><strong>Antes de confirmar</strong><p>A ação original será repetida uma única vez e pode alterar dados na aplicação testada. Não é uma simulação.</p>
          <ul>{(preflight.effects?.length ? preflight.effects : ["Um novo navegador será aberto para preparar o teste.", "Somente a ação original deste CT será repetida.", "Um novo resultado e novas evidências serão registrados, preservando a execução original."]).map((effect, index) => <li key={index}>{effect}</li>)}</ul>
        </div>
        <p className="rerun-local-note"><ShieldCheck size={17} />As verificações locais salvas serão repetidas, sem chamadas à LLM. {checkpointCount ? "Somente as pausas para print serão aplicadas. SWS e SQL continuam bloqueados e não serão executados." : "Nenhuma pausa para print foi incluída. SWS e SQL não serão executados."}</p>
        {checkpointCount > 0 && <div className="rerun-capture-plan"><span className="rerun-capture-plan-icon"><ImagePlus size={22} /></span><div><strong>{checkpointCount} {checkpointCount === 1 ? "pausa para print" : "pausas para print"} nesta reexecução</strong><p>Em cada pausa, adicione a imagem que você quiser na aplicação AutoJev: cole com Ctrl + V, arraste um arquivo ou escolha uma imagem PNG, JPG ou WEBP de até 10 MB. Pode ser de qualquer tela ou aplicativo; não há captura automática do navegador. Confira a prévia e clique em «Anexar print e continuar». A confirmação ocorre somente na aplicação AutoJev, nunca pela página testada. O print não aprova nem reprova o teste.</p><p>As pausas planejadas antes da ação ocorrem antes dela; as demais acontecem após a ação e suas conferências locais. A posição no rascunho fica registrada, sem interromper cada conferência individual.</p><p>Prazo de confirmação: até 15 minutos por pausa, com limite de 1 hora somando todas as pausas. Ao esgotar o prazo, a execução termina sem anexar ou continuar automaticamente.</p><ol>{[...capturePlan!.checkpoints].sort((left, right) => left.gap - right.gap || left.order - right.order).map((item) => <li key={item.id}><span>Aguardar print</span><small>Posição planejada: {item.gap === 0 ? "antes da primeira etapa" : `após a etapa ${item.gap}`}</small></li>)}</ol></div></div>}
        {preflight.scope?.missing_visual_checks ? <p className="rerun-local-note">Nem todas as conferências originais poderão ser repetidas: a conferência visual da LLM ficará pendente. A reexecução não confirmará essa parte do teste.</p> : preflight.scope?.visual_review_omitted && <p className="rerun-local-note">A revisão visual auxiliar não será repetida. As verificações locais podem ser concluídas normalmente; isso não equivale a uma aprovação visual.</p>}
        <div className="rerun-preparation"><strong>Preencha o login e continue pelo AutoJev</strong><p>Se precisar entrar, preencha usuário e senha no novo navegador. Ao clicar em «Continuar reexecução», o AutoJev tentará enviar um formulário de login preenchido e seguro. Se já estiver conectado, apenas conferirá a preparação. Recrie as pré-condições do CT e abra a página esperada; a ação original só será executada após essa conferência.</p></div>
        <label className="rerun-consent"><input type="checkbox" checked={consent} disabled={busy} onChange={(event) => onConsent(event.target.checked)} /><span>Entendo que esta reexecução é real, pode tentar entrar com o login que eu preencher e pode modificar dados da aplicação{checkpointCount ? " e aguardará minha confirmação para cada print; SWS e SQL não serão executados." : " e não executará SWS ou SQL."}</span></label>
      </>}
    </>}
    <div className="modal-actions"><button type="button" className="button ghost" disabled={busy} onClick={onClose}>Voltar sem reexecutar</button><button type="button" className="button primary" disabled={loading || busy || !!error || !preflight?.available || !consent} onClick={onConfirm}>{busy ? <Spinner /> : <Play size={16} />}{busy ? "Criando execução…" : "Confirmar reexecução real"}</button></div>
  </div>;
}

function ScenarioRerunDialog({ sourceId, scenarioId, capturePlan, capturePlanError, request, onClose, onCreated }: {
  sourceId: string;
  scenarioId: string;
  capturePlan?: ScenarioCapturePlan;
  capturePlanError?: string;
  request: ApiRequest;
  onClose: () => void;
  onCreated: (exploration: Exploration) => void;
}) {
  const [preflight, setPreflight] = useState<ScenarioRerunPreflight | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const confirming = useRef(false);
  const planError = capturePlanError || (!loading && preflight ? validateCapturePlanForPreflight(capturePlan, preflight) : "");
  useEffect(() => {
    const controller = new AbortController();
    void request<ScenarioRerunPreflight>(`/explorations/${encodeURIComponent(sourceId)}/scenarios/${encodeURIComponent(scenarioId)}/rerun`, { signal: controller.signal })
      .then((value) => { if (!controller.signal.aborted) setPreflight(value); })
      .catch((caught) => { if (!controller.signal.aborted) setError(errorMessage(caught)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [request, sourceId, scenarioId]);
  const confirm = async () => {
    if (confirming.current || !consent || !preflight?.available || loading || error || planError) return;
    confirming.current = true;
    setBusy(true);
    setError("");
    try {
      const created = await confirmScenarioRerun(request, sourceId, scenarioId, consent, preflight.available, capturePlan);
      if (!created || typeof created.id !== "string" || !created.id) throw new Error("A API não informou a nova execução. Atualize o histórico antes de tentar novamente.");
      onCreated(created);
    } catch (caught) { setError(errorMessage(caught)); }
    finally { confirming.current = false; setBusy(false); }
  };
  return <Modal title="Reexecutar um caso de teste" description="Nova execução isolada. As versões anteriores e suas evidências ficam preservadas no histórico do teste; o relatório de origem mostra o resultado mais recente." onClose={() => { if (!confirming.current) onClose(); }}>
    <ScenarioRerunConsent preflight={preflight} loading={loading} error={planError || error} busy={busy} consent={consent} capturePlan={capturePlan} onConsent={setConsent} onConfirm={() => void confirm()} onClose={onClose} />
  </Modal>;
}

function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="page-heading">
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

function DashboardView({
  explorations,
  providers,
  totalModels,
  loading,
  navigate,
}: {
  explorations: Exploration[];
  providers: Provider[];
  totalModels: number;
  loading: boolean;
  navigate: (hash: string) => void;
}) {
  const liveCount = explorations.filter((item) => isLive(item.status)).length;
  const completed = explorations.filter((item) => ["completed", "success"].includes(item.status?.toLowerCase())).length;
  const scenarioCount = explorations.reduce((total, item) => total + (item.counters?.scenarios || item.counters?.test_scenarios || item.scenarios_count || 0), 0);
  const passCount = explorations.reduce((total, item) => total + (item.summary?.pass || 0), 0);
  const failCount = explorations.reduce((total, item) => total + (item.summary?.fail || 0), 0);
  const blockedCount = explorations.reduce((total, item) => total + (item.summary?.blocked || 0), 0);
  const verifiedCount = passCount + failCount + blockedCount;
  const passRate = verifiedCount ? `${Math.round((passCount / verifiedCount) * 100)}%` : "—";
  const latest = [...explorations].sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()).slice(0, 5);

  return (
    <div className="view-stack dashboard-view">
      <PageHeader
        eyebrow="Centro de operações"
        title="Boa exploração começa com uma boa decisão."
        description="O JEV conduz o teste, o Playwright executa cada ação e sua LLM auxiliar interpreta as evidências — sem assumir o controle do navegador."
        actions={<button className="button primary" onClick={() => navigate("#/nova-exploracao")}><Plus size={17} />Nova exploração</button>}
      />

      <section className="metric-grid" aria-label="Resumo do AutoJev">
        <MetricCard icon={Activity} label="Explorações" value={loading ? "—" : String(explorations.length)} note={liveCount > 0 ? `${liveCount} em execução agora` : "Nenhuma execução ativa"} tone="amber" />
        <MetricCard icon={FileCheck2} label="Cenários gerados" value={loading ? "—" : String(scenarioCount)} note="Com evidências rastreáveis" />
        <MetricCard icon={CheckCircle2} label="Taxa de aprovação" value={loading ? "—" : passRate} note={verifiedCount ? `${passCount} cenários aprovados` : "Aguardando cenários verificados"} tone="teal" />
        <MetricCard icon={AlertCircle} label="Falhas" value={loading ? "—" : String(failCount)} note={blockedCount ? `${blockedCount} cenário(s) bloqueado(s)` : `${completed} exploração(ões) concluída(s)`} />
      </section>

      <section className="panel architecture-panel">
        <div className="panel-heading architecture-heading">
          <div>
            <span className="section-kicker"><Network size={15} />Arquitetura desacoplada</span>
            <h2>JEV no comando. Modelos intercambiáveis.</h2>
            <p>A decisão permanece no núcleo do agente; a LLM auxiliar recebe somente screenshot e DOM para interpretar a observação.</p>
          </div>
          <button className="text-button" onClick={() => navigate("#/provedores")}>Configurar modelos <ArrowRight size={15} /></button>
        </div>
        <ArchitectureFlow />
        <div className="architecture-note">
          <ShieldCheck size={18} />
          <span><strong>Limite de responsabilidade:</strong> somente o executor Playwright interage com o browser. Troque Claude, Gemini, modelos locais ou qualquer endpoint OpenAI-compatible sem alterar o núcleo.</span>
        </div>
      </section>

      <div className="dashboard-lower-grid">
        <section className="panel recent-panel">
          <div className="panel-heading row-heading">
            <div><span className="section-kicker">Atividade recente</span><h2>Últimas explorações</h2></div>
            {explorations.length > 0 && <button className="text-button" onClick={() => navigate("#/exploracoes")}>Ver todas <ArrowRight size={15} /></button>}
          </div>
          {loading ? <ListSkeleton rows={3} /> : latest.length === 0 ? (
            <EmptyState icon={History} title="Ainda não há explorações" description="Inicie uma exploração para acompanhar ações, evidências e cenários em tempo real." action={<button className="button secondary" onClick={() => navigate("#/nova-exploracao")}><Rocket size={16} />Criar primeira exploração</button>} />
          ) : (
            <div className="recent-list">
              {latest.map((exploration) => (
                <button className="recent-row" key={exploration.id} onClick={() => navigate(`#/exploracoes/${encodeURIComponent(exploration.id)}`)}>
                  <span className="app-avatar"><Globe2 size={18} /></span>
                  <span className="recent-main"><strong>{hostFromUrl(exploration.app_url)}</strong><small>{exploration.app_url}</small></span>
                  <StatusBadge status={exploration.status} completionType={exploration.completion?.type} />
                  <span className="recent-time">{formatDate(exploration.created_at)}</span>
                  <ChevronRight size={17} className="row-chevron" />
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="panel quick-panel">
          <div className="panel-heading"><span className="section-kicker">Preparação</span><h2>Pronto para explorar?</h2><p>Três pontos deixam sua execução reproduzível.</p></div>
          <div className="checklist">
            <ChecklistItem done={providers.some(providerIsEnabled)} title="Provedor conectado" detail={providers.some(providerIsEnabled) ? "Há pelo menos um endpoint ativo." : "Adicione um endpoint OpenAI-compatible."} onClick={() => navigate("#/provedores")} />
            <ChecklistItem done={totalModels > 0} title="Modelos selecionáveis" detail={totalModels > 0 ? `${totalModels} modelo(s) pronto(s) para uso.` : "Descubra ou cadastre modelos no provedor."} onClick={() => navigate("#/provedores")} />
            <ChecklistItem done={explorations.length > 0} title="Primeira execução" detail={explorations.length > 0 ? "O histórico já está sendo registrado." : "Defina a URL e os dois papéis de IA."} onClick={() => navigate("#/nova-exploracao")} />
          </div>
        </section>
      </div>
    </div>
  );
}

function MetricCard({ icon: Icon, label, value, note, tone = "default" }: { icon: typeof Activity; label: string; value: string; note: string; tone?: string }) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <div className="metric-top"><span className="metric-icon"><Icon size={19} /></span><span className="metric-label">{label}</span></div>
      <strong className="metric-value">{value}</strong>
      <span className="metric-note">{note}</span>
    </article>
  );
}

function ChecklistItem({ done, title, detail, onClick }: { done: boolean; title: string; detail: string; onClick: () => void }) {
  return (
    <button className="checklist-item" onClick={onClick}>
      <span className={`check-icon${done ? " done" : ""}`}>{done ? <Check size={15} /> : <span />}</span>
      <span><strong>{title}</strong><small>{detail}</small></span>
      <ChevronRight size={16} />
    </button>
  );
}

function ListSkeleton({ rows = 4 }: { rows?: number }) {
  return <div className="list-skeleton" aria-label="Carregando">{Array.from({ length: rows }, (_, index) => <div className="skeleton-row" key={index}><span /><i /><em /></div>)}</div>;
}

type ApiRequest = <T>(path: string, options?: RequestOptions) => Promise<T>;

function NewExplorationView({
  providers,
  models,
  loading,
  request,
  toast,
  navigate,
  reloadModels,
  onCreated,
}: {
  providers: Provider[];
  models: Model[];
  loading: boolean;
  request: ApiRequest;
  toast: (message: string, tone?: Toast["tone"]) => void;
  navigate: (hash: string) => void;
  reloadModels: (quiet?: boolean) => Promise<void>;
  onCreated: (exploration: Exploration) => void;
}) {
  const enabledProviders = providers.filter(providerIsEnabled);
  const [url, setUrl] = useState("");
  const [focusMode, setFocusMode] = useState<"all" | "focused">("all");
  const [focusArea, setFocusArea] = useState("");
  const [agentProvider, setAgentProvider] = useState("");
  const [agentModel, setAgentModel] = useState("");
  const [auxProvider, setAuxProvider] = useState("");
  const [auxModel, setAuxModel] = useState("");
  const [viewport, setViewport] = useState("1440x900");
  const [auxiliaryEvidenceMode, setAuxiliaryEvidenceMode] = useState<"text" | "visual">("text");
  const [auxiliaryMaxRequests, setAuxiliaryMaxRequests] = useState("300");
  const [captureConsoleErrors, setCaptureConsoleErrors] = useState(true);
  const [captureNetworkErrors, setCaptureNetworkErrors] = useState(true);
  const [credentialsOpen, setCredentialsOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refreshingVisualModelsFor, setRefreshingVisualModelsFor] = useState("");
  const visualDiscoveryAttempted = useRef(new Set<string>());

  const modelsFor = useCallback((providerId: string) => {
    const provider = providers.find((item) => item.id === providerId);
    const nested = provider?.models || [];
    const flat = models.filter((model) => model.provider_id === providerId);
    const source = nested.length > 0 ? nested : flat;
    return source.filter((model) => model.enabled !== false);
  }, [providers, models]);

  const auxiliaryModelsFor = useCallback((providerId: string) => {
    const available = modelsFor(providerId);
    return auxiliaryEvidenceMode === "visual"
      ? available.filter(modelAcceptsImages)
      : available;
  }, [auxiliaryEvidenceMode, modelsFor]);

  useEffect(() => {
    if (!agentProvider && enabledProviders[0]) setAgentProvider(enabledProviders[0].id);
    if (!auxProvider && enabledProviders[0]) setAuxProvider(enabledProviders[0].id);
  }, [enabledProviders, agentProvider, auxProvider]);

  useEffect(() => {
    const available = modelsFor(agentProvider);
    if (!available.some((model) => modelValue(model) === agentModel)) setAgentModel(defaultModel(available, "agent"));
  }, [agentProvider, agentModel, modelsFor]);

  useEffect(() => {
    const available = auxiliaryModelsFor(auxProvider);
    if (!available.some((model) => modelValue(model) === auxModel)) setAuxModel(defaultModel(available, "auxiliary"));
  }, [auxProvider, auxModel, auxiliaryModelsFor]);

  useEffect(() => {
    if (auxiliaryEvidenceMode !== "visual" || !auxProvider || loading) return;
    const provider = providers.find((item) => item.id === auxProvider);
    if (!provider || !isOpenRouterUrl(provider.base_url) || !(provider.has_api_key || provider.api_key_configured)) return;
    const catalog = provider.models?.length
      ? provider.models
      : models.filter((model) => model.provider_id === auxProvider);
    if ((catalog.length > 0 && catalog.every(modelInputCapabilitiesKnown)) || visualDiscoveryAttempted.current.has(auxProvider)) return;

    visualDiscoveryAttempted.current.add(auxProvider);
    setRefreshingVisualModelsFor(auxProvider);
    void (async () => {
      try {
        await request(`/providers/${encodeURIComponent(auxProvider)}/discover-models`, { method: "POST" });
        await reloadModels(true);
        toast("Compatibilidade visual dos modelos atualizada.", "success");
      } catch (error) {
        toast(`Não foi possível atualizar os modelos visuais: ${errorMessage(error)}`, "error");
      } finally {
        setRefreshingVisualModelsFor((current) => current === auxProvider ? "" : current);
      }
    })();
  }, [auxProvider, auxiliaryEvidenceMode, loading, models, providers, reloadModels, request, toast]);

  const availableAuxiliaryModels = auxiliaryModelsFor(auxProvider);
  const refreshingVisualModels = refreshingVisualModelsFor === auxProvider;
  const visualAuxiliaryModelMissing = auxiliaryEvidenceMode === "visual"
    && !availableAuxiliaryModels.some((model) => modelValue(model) === auxModel);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const targetUrl = normalizeTargetUrl(url);
    try {
      new URL(targetUrl);
    } catch {
      toast("Informe um domínio ou URL válida.", "error");
      return;
    }
    if (!targetUrl || !agentProvider || !agentModel || !auxProvider || !auxModel) {
      toast(
        auxiliaryEvidenceMode === "visual" && auxProvider && availableAuxiliaryModels.length === 0
          ? "O modo visual exige uma LLM auxiliar que aceite imagens. Escolha outro provedor ou use o modo econômico."
          : "Preencha a URL e selecione os dois modelos.",
        "error",
      );
      return;
    }
    if (visualAuxiliaryModelMissing) {
      toast("Selecione uma LLM auxiliar compatível com entrada de imagem.", "error");
      return;
    }
    if (focusMode === "focused" && !focusArea.trim()) {
      toast("Informe a tela, menu ou submenu que deve ser explorado.", "error");
      return;
    }
    if ((username || password) && (!username.trim() || !password)) {
      setCredentialsOpen(true);
      toast("Informe usuário e senha juntos, ou deixe ambos vazios para fazer login manualmente.", "error");
      return;
    }
    const requestBudget = Number(auxiliaryMaxRequests);
    if (!Number.isInteger(requestBudget) || requestBudget < 1 || requestBudget > 2000) {
      toast("Defina um limite de 1 a 2.000 chamadas auxiliares.", "error");
      return;
    }
    setUrl(targetUrl);
    setSubmitting(true);
    try {
      const [viewportWidth, viewportHeight] = viewport.split("x").map(Number);
      const normalizedFocusArea = focusMode === "focused" ? focusArea.trim() : "";
      const explorationOptions: Record<string, unknown> = {
        mode: focusMode === "focused" ? "focused" : "full",
        max_steps: null,
        max_duration_seconds: null,
        capture_screenshots: true,
        auxiliary_evidence_mode: auxiliaryEvidenceMode,
        auxiliary_max_requests: requestBudget,
        auxiliary_review_max_attempts: 3,
        capture_console_errors: captureConsoleErrors,
        capture_network_errors: captureNetworkErrors,
        generate_test_cases: true,
        generate_html_report: true,
        viewport_width: viewportWidth,
        viewport_height: viewportHeight,
      };
      if (normalizedFocusArea) explorationOptions.focus_area = normalizedFocusArea;
      const body: Record<string, unknown> = {
        app_url: targetUrl,
        agent: { provider_id: agentProvider, model_id: agentModel },
        auxiliary_llm: { provider_id: auxProvider, model_id: auxModel },
        options: explorationOptions,
      };
      // Collapsing the credentials section changes only visibility, not the
      // login setup. Values remain ephemeral and are never saved in the UI.
      if (username || password) body.credentials = { username: username.trim(), password };
      const created = await request<Exploration>("/explorations", { method: "POST", body: JSON.stringify(body) });
      onCreated(created);
      toast("Exploração iniciada. O JEV já está preparando o navegador.", "success");
      navigate(`#/exploracoes/${encodeURIComponent(created.id)}`);
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setSubmitting(false);
    }
  };

  const noModels = !loading && (enabledProviders.length === 0 || enabledProviders.every((provider) => modelsFor(provider.id).length === 0));

  return (
    <div className="view-stack new-view">
      <PageHeader eyebrow="Nova execução" title="Prepare a exploração" description="Defina o alvo e escolha, de forma independente, quem decide e quem interpreta as evidências." />
      {noModels && (
        <div className="callout warning-callout"><AlertCircle size={19} /><div><strong>Você precisa de um modelo ativo.</strong><span>Conecte um provedor e cadastre ou descubra seus modelos antes de iniciar.</span></div><button className="button secondary small" onClick={() => navigate("#/provedores")}>Configurar provedores</button></div>
      )}
      <form onSubmit={submit} className="exploration-form">
        <div className="form-main-column">
          <section className="panel form-section">
            <div className="section-number">01</div>
            <div className="form-section-content">
              <div className="form-section-heading"><div><h2>Aplicação alvo</h2><p>Informe a URL inicial. O JEV descobrirá as rotas durante a navegação.</p></div><Globe2 size={21} /></div>
              <label className="field-label" htmlFor="target-url">URL da aplicação</label>
              <div className="input-with-icon"><Globe2 size={17} /><input id="target-url" type="text" inputMode="url" required placeholder="https://app.exemplo.com ou www.exemplo.com" value={url} onChange={(event) => setUrl(event.target.value)} onBlur={() => setUrl(normalizeTargetUrl(url))} autoComplete="url" /></div>
              <span className="field-hint">A aplicação deve estar acessível pelo servidor que executa o Playwright.</span>
              <fieldset className="focus-area-field">
                <legend>Tela, menu ou submenu <em>opcional</em></legend>
                <p>Escolha se o JEV deve percorrer toda a aplicação ou esgotar primeiro um recorte funcional.</p>
                <div className="scope-mode-grid">
                  <label className={focusMode === "all" ? "selected" : ""}>
                    <input type="radio" name="exploration-scope" value="all" checked={focusMode === "all"} onChange={() => setFocusMode("all")} />
                    <span><strong>Toda a aplicação</strong><small>Descobrir e testar todos os recursos acessíveis.</small></span>
                    <i><Check size={13} /></i>
                  </label>
                  <label className={focusMode === "focused" ? "selected" : ""}>
                    <input type="radio" name="exploration-scope" value="focused" checked={focusMode === "focused"} onChange={() => setFocusMode("focused")} />
                    <span><strong>Tela/menu específico</strong><small>Concentrar o exploratório em uma área escolhida.</small></span>
                    <i><Check size={13} /></i>
                  </label>
                </div>
                {focusMode === "focused" && (
                  <div className="focus-area-input">
                    <label className="field-label" htmlFor="focus-area">Qual área deve ser explorada?</label>
                    <div className="input-with-icon"><Focus size={17} /><input id="focus-area" type="text" required maxLength={500} placeholder="Ex.: Configurações > Usuários > Permissões" value={focusArea} onChange={(event) => setFocusArea(event.target.value)} aria-describedby="focus-area-hint" /></div>
                    <span className="field-hint" id="focus-area-hint">Informe o caminho visível dos menus ou descreva a funcionalidade. O JEV poderá navegar para frente e voltar entre telas dentro deste escopo.</span>
                  </div>
                )}
              </fieldset>
            </div>
          </section>

          <section className="panel form-section model-section">
            <div className="section-number">02</div>
            <div className="form-section-content">
              <div className="form-section-heading"><div><h2>Inteligência da exploração</h2><p>Um modelo conduz o raciocínio; o outro transforma observações em contexto útil.</p></div><BrainCircuit size={21} /></div>
              <div className="model-choice-grid">
                <ModelRoleCard
                  roleTitle="JEV · Agente QA"
                  description="Decide a próxima ação e mantém o objetivo da exploração."
                  icon={<BrainCircuit size={20} />}
                  accent="amber"
                  providers={enabledProviders}
                  providerValue={agentProvider}
                  modelValueSelected={agentModel}
                  onProvider={setAgentProvider}
                  onModel={setAgentModel}
                  modelsFor={modelsFor}
                />
                <ModelRoleCard
                  roleTitle="LLM auxiliar"
                  description="Documenta as evidências; não controla o navegador."
                  icon={<Sparkles size={20} />}
                  accent="teal"
                  providers={enabledProviders}
                  providerValue={auxProvider}
                  modelValueSelected={auxModel}
                  onProvider={setAuxProvider}
                  onModel={setAuxModel}
                  modelsFor={auxiliaryModelsFor}
                  emptyModelMessage={auxiliaryEvidenceMode === "visual"
                    ? refreshingVisualModels ? "Verificando modelos com visão..." : "Nenhum modelo com visão neste provedor"
                    : undefined}
                />
              </div>
              <fieldset className="auxiliary-evidence-field">
                <legend>Evidência enviada à LLM auxiliar</legend>
                <p>O Playwright sempre preserva screenshots no relatório. Aqui você decide se a imagem também será enviada ao modelo.</p>
                <div className="scope-mode-grid evidence-mode-grid">
                  <label className={auxiliaryEvidenceMode === "text" ? "selected" : ""}>
                    <input type="radio" name="auxiliary-evidence-mode" value="text" checked={auxiliaryEvidenceMode === "text"} onChange={() => setAuxiliaryEvidenceMode("text")} />
                    <span className="evidence-mode-copy"><span className="evidence-mode-title"><Code2 size={16} /><strong>Econômico · texto</strong><em>Recomendado</em></span><small>Envia texto visível e DOM semântico compacto. A imagem continua salva localmente, sem entrar no prompt.</small></span>
                    <i><Check size={13} /></i>
                  </label>
                  <label className={auxiliaryEvidenceMode === "visual" ? "selected" : ""}>
                    <input type="radio" name="auxiliary-evidence-mode" value="visual" checked={auxiliaryEvidenceMode === "visual"} onChange={() => setAuxiliaryEvidenceMode("visual")} />
                    <span className="evidence-mode-copy"><span className="evidence-mode-title"><Eye size={16} /><strong>Visual · texto + imagem</strong></span><small>Acrescenta o screenshot em baixa resolução. A lista da LLM auxiliar exibirá somente modelos que aceitam imagens.</small></span>
                    <i><Check size={13} /></i>
                  </label>
                </div>
                {auxiliaryEvidenceMode === "visual" && (
                  <div className={`visual-model-filter-note ${availableAuxiliaryModels.length ? "compatible" : "unavailable"}`} role="status">
                    {refreshingVisualModels ? <Spinner /> : availableAuxiliaryModels.length ? <CheckCircle2 size={16} /> : <AlertCircle size={16} />}
                    <span>
                      {refreshingVisualModels
                        ? "Consultando o catálogo do OpenRouter para identificar os modelos que aceitam imagens..."
                        : availableAuxiliaryModels.length
                        ? `${availableAuxiliaryModels.length} modelo(s) com entrada de imagem disponível(is) neste provedor.`
                        : "Este provedor não informou nenhum modelo compatível com imagens. Escolha outro provedor ou use o modo econômico."}
                    </span>
                  </div>
                )}
              </fieldset>
              <div className="separation-note"><ShieldCheck size={17} /><span>As credenciais dos providers ficam no backend. O browser só recebe comandos validados pelo executor.</span></div>
            </div>
          </section>

          <section className="panel form-section">
            <div className="section-number">03</div>
            <div className="form-section-content">
              <div className="form-section-heading"><div><h2>Parâmetros de execução</h2><p>A exploração termina pela cobertura: o JEV continua até esgotar as opções alcançáveis do escopo.</p></div><Gauge size={21} /></div>
              <div className="coverage-mode-note"><CheckCircle2 size={19} /><div><strong>Sem teto fixo de ações ou duração</strong><span>Você pode cancelar quando quiser. O AutoJev só conclui normalmente depois de percorrer toda a cobertura alcançável.</span></div></div>
              <div className="field-grid two-columns">
                <label className="field-group"><span>Viewport</span><select value={viewport} onChange={(event) => setViewport(event.target.value)}><option value="1440x900">Desktop · 1440 × 900</option><option value="1280x720">Notebook · 1280 × 720</option><option value="390x844">Mobile · 390 × 844</option></select></label>
                <div className="field-group"><span>Evidências</span><div className="static-field"><Eye size={16} />Screenshot + DOM</div></div>
              </div>
              <div className="auxiliary-budget-settings">
                <label className="field-group"><span>Limite de chamadas da LLM auxiliar</span><input type="number" min="1" max="2000" step="1" required value={auxiliaryMaxRequests} onChange={(event) => setAuxiliaryMaxRequests(event.target.value)} /><small>Teto de requisições HTTP, não meta de gasto nem quantidade de CTs. CTs novos recebem revisão em paralelo; revisitas visuais são agrupadas para revisar a evidência final. Retentativas também consomem o limite; cache idêntico não. Um limite menor pode deixar revisões pendentes.</small></label>
                <p className="field-hint">Até 3 tentativas por revisão, contando a primeira. A exploração e os testes locais continuam se o limite auxiliar for atingido. No modo visual, as revisões pendentes podem ser retomadas depois com sua confirmação.</p>
              </div>
              <div className="toggle-list">
                <label className="toggle-row"><span><strong>Capturar erros de console</strong><small>Registra exceções e mensagens relevantes emitidas pela aplicação.</small></span><input aria-label="Capturar erros de console" type="checkbox" checked={captureConsoleErrors} onChange={(event) => setCaptureConsoleErrors(event.target.checked)} /><i /></label>
                <label className="toggle-row"><span><strong>Capturar falhas de rede</strong><small>Inclui requisições malsucedidas nas evidências da exploração.</small></span><input aria-label="Capturar falhas de rede" type="checkbox" checked={captureNetworkErrors} onChange={(event) => setCaptureNetworkErrors(event.target.checked)} /><i /></label>
              </div>
            </div>
          </section>

          <section className="panel form-section credentials-section">
            <div className="section-number">04</div>
            <div className="form-section-content">
              <button type="button" className="credentials-toggle" onClick={() => setCredentialsOpen((value) => !value)} aria-expanded={credentialsOpen}>
                <span className="form-section-heading"><span><h2>Credenciais da aplicação <em>opcional</em></h2><p>{username && password ? "Credenciais configuradas · login automático, mesmo com esta seção recolhida." : "O Playwright tentará fazer login automaticamente. Sem credenciais, o login será manual."}</p></span><KeyRound size={21} /></span>
                <ChevronDown size={18} className={credentialsOpen ? "rotated" : ""} />
              </button>
              {credentialsOpen && (
                <>
                <div className="field-grid two-columns credential-fields">
                  <label className="field-group"><span>Usuário ou e-mail</span><input type="text" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="qa@empresa.com" /></label>
                  <label className="field-group"><span>Senha</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="••••••••••••" /></label>
                </div>
                <p className="field-hint">Sua senha não é enviada à IA nem salva no banco. Se houver rejeição, CAPTCHA ou autenticação adicional, você continuará manualmente.</p>
                </>
              )}
            </div>
          </section>
        </div>

        <aside className="launch-card panel">
          <span className="launch-icon"><MonitorPlay size={23} /></span>
          <span className="section-kicker">Resumo da execução</span>
          <h2>{url ? hostFromUrl(url) : "Aplicação não definida"}</h2>
          <div className="launch-summary">
            <SummaryRow label="Agente JEV" value={agentModel || "Selecione um modelo"} />
            <SummaryRow label="LLM auxiliar" value={auxModel || "Selecione um modelo"} />
            <SummaryRow label="Evidência da LLM" value={auxiliaryEvidenceMode === "visual" ? "Visual · texto + imagem" : "Econômico · somente texto"} />
            <SummaryRow label="Chamadas auxiliares" value={`Até ${auxiliaryMaxRequests || "—"} · 3 tentativas/revisão`} />
            <SummaryRow label="Escopo" value={focusMode === "focused" ? focusArea.trim() || "Defina uma área" : "Aplicação inteira"} />
            <SummaryRow label="Conclusão" value="Ao esgotar a cobertura" />
            <SummaryRow label="Limite" value="Sem teto fixo" />
            <SummaryRow label="Viewport" value={viewport.replace("x", " × ")} />
          </div>
          <div className="launch-route"><BrainCircuit size={17} /><span /><Code2 size={17} /><span /><Globe2 size={17} /><span /><Sparkles size={17} /></div>
          <button type="submit" className="button primary launch-button" disabled={submitting || noModels || visualAuxiliaryModelMissing}>{submitting ? <><Spinner />Iniciando exploração…</> : <><Play size={17} fill="currentColor" />Iniciar exploração</>}</button>
          <p className="launch-hint"><ShieldCheck size={14} />O JEV não enviará ações diretamente à LLM auxiliar.</p>
        </aside>
      </form>
    </div>
  );
}

function ModelRoleCard({
  roleTitle,
  description,
  icon,
  accent,
  providers,
  providerValue,
  modelValueSelected,
  onProvider,
  onModel,
  modelsFor,
  emptyModelMessage,
}: {
  roleTitle: string;
  description: string;
  icon: ReactNode;
  accent: string;
  providers: Provider[];
  providerValue: string;
  modelValueSelected: string;
  onProvider: (value: string) => void;
  onModel: (value: string) => void;
  modelsFor: (providerId: string) => Model[];
  emptyModelMessage?: string;
}) {
  return (
    <div className={`model-role-card accent-${accent}`}>
      <div className="model-role-head"><span className="model-role-icon">{icon}</span><div><strong>{roleTitle}</strong><small>{description}</small></div></div>
      <label className="field-group"><span>Provedor</span><select value={providerValue} onChange={(event) => onProvider(event.target.value)} disabled={!providers.length}><option value="">Selecione o provedor</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>
      <div className="field-group"><span>Modelo</span><ModelCombobox models={modelsFor(providerValue)} value={modelValueSelected} onChange={onModel} disabled={!providerValue} emptyMessage={emptyModelMessage} /></div>
    </div>
  );
}

function ModelCombobox({
  models,
  value,
  onChange,
  disabled,
  emptyMessage = "Nenhum modelo disponível",
}: {
  models: Model[];
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  emptyMessage?: string;
}) {
  const listboxId = useId();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selected = models.find((model) => modelValue(model) === value);
  const normalizedQuery = query.trim().toLowerCase();
  const filtered = models.filter((model) => {
    if (!normalizedQuery) return true;
    return `${modelLabel(model)} ${modelValue(model)}`.toLowerCase().includes(normalizedQuery);
  });

  useEffect(() => {
    if (!open) setQuery(selected ? modelLabel(selected) : "");
  }, [open, selected]);

  const choose = (model: Model) => {
    onChange(modelValue(model));
    setQuery(modelLabel(model));
    setOpen(false);
  };

  return (
    <div className="model-combobox">
      <input
        aria-autocomplete="list"
        aria-controls={listboxId}
        aria-expanded={open}
        aria-label="Pesquisar modelo"
        disabled={disabled || !models.length}
        role="combobox"
        value={query}
        placeholder={models.length ? "Pesquisar modelo..." : emptyMessage}
        onFocus={() => { setOpen(true); setQuery(""); }}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
          if (event.key === "Enter" && filtered[0]) { event.preventDefault(); choose(filtered[0]); }
        }}
      />
      <ChevronDown size={15} className="model-combobox-chevron" />
      {open && !disabled && (
        <div className="model-combobox-menu" id={listboxId} role="listbox">
          {filtered.length ? filtered.map((model) => {
            const id = modelValue(model);
            return (
              <button
                type="button"
                role="option"
                aria-selected={id === value}
                className={id === value ? "selected" : ""}
                key={id}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => choose(model)}
              >
                <strong>{modelLabel(model)}</strong>
                {modelLabel(model) !== id && <small>{id}</small>}
              </button>
            );
          }) : <div className="model-combobox-empty">Nenhum modelo encontrado.</div>}
        </div>
      )}
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return <div className="summary-row"><span>{label}</span><strong title={value}>{value}</strong></div>;
}

function ExplorationsView({ explorations, loading, navigate }: { explorations: Exploration[]; loading: boolean; navigate: (hash: string) => void }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const filtered = explorations.filter((item) => {
    const matchesQuery = !query || item.app_url.toLowerCase().includes(query.toLowerCase()) || item.id.toLowerCase().includes(query.toLowerCase());
    const status = explorationStatus(item.status).value;
    const matchesFilter = filter === "all" || (filter === "live" ? isLive(status) : filter === "completed" ? ["completed", "success"].includes(status) : filter === "failed" ? ["failed", "error"].includes(status) : status === filter);
    return matchesQuery && matchesFilter;
  });

  return (
    <div className="view-stack explorations-view">
      <PageHeader eyebrow="Histórico" title="Explorações" description="Acompanhe execuções em andamento e recupere evidências de cada sessão." actions={<button className="button primary" onClick={() => navigate("#/nova-exploracao")}><Plus size={17} />Nova exploração</button>} />
      <section className="panel table-panel">
        <div className="table-toolbar">
          <div className="search-box"><Search size={17} /><input aria-label="Buscar explorações" placeholder="Buscar por aplicação ou ID" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          <div className="filter-tabs" aria-label="Filtrar por status">
            {[{ value: "all", label: "Todas" }, { value: "live", label: "Em andamento" }, { value: "completed", label: "Concluídas" }, { value: "partial", label: "Parciais" }, { value: "failed", label: "Falhas" }].map((item) => <button key={item.value} className={filter === item.value ? "active" : ""} onClick={() => setFilter(item.value)}>{item.label}</button>)}
          </div>
        </div>
        {loading ? <ListSkeleton rows={5} /> : explorations.length === 0 ? (
          <EmptyState icon={MonitorPlay} title="Nenhuma exploração registrada" description="Quando o JEV explorar sua primeira aplicação, a execução aparecerá aqui com status e evidências." action={<button className="button primary" onClick={() => navigate("#/nova-exploracao")}><Rocket size={16} />Começar agora</button>} />
        ) : filtered.length === 0 ? (
          <EmptyState icon={Search} title="Nenhum resultado" description="Tente outro termo ou remova o filtro de status." action={<button className="button ghost" onClick={() => { setQuery(""); setFilter("all"); }}>Limpar filtros</button>} />
        ) : (
          <div className="exploration-table-wrap">
            <table className="data-table">
              <thead><tr><th>Aplicação</th><th>Status</th><th>Progresso</th><th>Cenários</th><th>Tokens</th><th>Custo total</th><th>Iniciada</th><th><span className="sr-only">Ações</span></th></tr></thead>
              <tbody>{filtered.map((item) => {
                const progress = item.overall_progress ?? item.progress ?? (["completed", "success"].includes(item.status?.toLowerCase()) ? 100 : undefined);
                const determinate = typeof progress === "number";
                const progressValue = typeof progress === "number" ? progress : 0;
                return (
                  <tr key={item.id} onClick={() => navigate(`#/exploracoes/${encodeURIComponent(item.id)}`)}>
                    <td><div className="table-app"><span className="app-avatar"><Globe2 size={17} /></span><span><strong>{hostFromUrl(item.app_url)}</strong><small>{explorationFocusArea(item.options) ? `Escopo: ${explorationFocusArea(item.options)}` : "Aplicação inteira"}</small></span></div></td>
                    <td><StatusBadge status={item.status} completionType={item.completion?.type} /></td>
                    <td><div className={`table-progress ${determinate ? "" : "indeterminate"}`}><span><i style={determinate ? { width: `${Math.min(100, Math.max(0, progressValue))}%` } : undefined} /></span><small>{determinate ? `${Math.round(progressValue)}%` : "cobrindo"}</small></div></td>
                    <td><div className="scenario-cell"><strong>{item.counters?.scenarios || item.counters?.test_scenarios || item.scenarios_count || 0}</strong>{item.summary && <small>{item.summary.pass || 0} aprovados · {item.summary.fail || 0} reprovados</small>}</div></td>
                    <td>{formatTokens(item.usage?.total_tokens)}</td>
                    <td>{formatCost(item.usage)}</td>
                    <td><span className="date-cell">{formatDate(item.created_at)}</span></td>
                    <td><button className="icon-button" aria-label={`Abrir exploração de ${hostFromUrl(item.app_url)}`} onClick={(event) => { event.stopPropagation(); navigate(`#/exploracoes/${encodeURIComponent(item.id)}`); }}><ChevronRight size={18} /></button></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function ProvidersView({
  providers,
  loading,
  request,
  toast,
  reload,
  onAdd,
}: {
  providers: Provider[];
  loading: boolean;
  request: ApiRequest;
  toast: (message: string, tone?: Toast["tone"]) => void;
  reload: () => Promise<void>;
  onAdd: () => void;
}) {
  return (
    <div className="view-stack providers-view">
      <PageHeader eyebrow="OpenAI-compatible endpoints" title="Provedores de IA" description="Conecte endpoints e disponibilize modelos diferentes para o agente JEV e para a interpretação auxiliar." actions={<button className="button primary" onClick={onAdd}><Plus size={17} />Adicionar endpoint</button>} />
      <div className="provider-intro callout"><Network size={20} /><div><strong>Uma conexão, múltiplos papéis.</strong><span>O mesmo provedor pode expor vários modelos. A escolha de JEV e LLM auxiliar acontece por exploração.</span></div></div>
      {loading ? <div className="provider-stack"><div className="provider-skeleton" /><div className="provider-skeleton" /></div> : providers.length === 0 ? (
        <section className="panel"><EmptyState icon={CloudCog} title="Nenhum provedor conectado" description="Adicione LiteLLM, Ollama ou qualquer endpoint compatível com a API da OpenAI." action={<button className="button primary" onClick={onAdd}><Plus size={16} />Adicionar primeiro endpoint</button>} /></section>
      ) : (
        <div className="provider-stack">{providers.map((provider, index) => <ProviderCard key={provider.id} provider={provider} initiallyOpen={index === 0} request={request} toast={toast} reload={reload} />)}</div>
      )}
    </div>
  );
}

function ProviderCard({ provider, initiallyOpen, request, toast, reload }: { provider: Provider; initiallyOpen: boolean; request: ApiRequest; toast: (message: string, tone?: Toast["tone"]) => void; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(initiallyOpen);
  const [name, setName] = useState(provider.name);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [apiKey, setApiKey] = useState("");
  const [enabled, setEnabled] = useState(providerIsEnabled(provider));
  const [providerModels, setProviderModels] = useState<Model[]>(provider.models || []);
  const [newModelId, setNewModelId] = useState("");
  const [newModelLabel, setNewModelLabel] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "discover" | "delete" | "toggle" | "">("");
  const [dirty, setDirty] = useState(false);
  const isOpenRouter = isOpenRouterUrl(baseUrl);

  useEffect(() => {
    setName(provider.name);
    setBaseUrl(provider.base_url);
    setEnabled(providerIsEnabled(provider));
    setProviderModels(provider.models || []);
    setApiKey("");
    setDirty(false);
  }, [provider]);

  const patchProvider = async (payload: Record<string, unknown>) => request(`/providers/${encodeURIComponent(provider.id)}`, { method: "PATCH", body: JSON.stringify(payload) });

  const persistDraft = async (announce = false) => {
    const payload: Record<string, unknown> = {
      name,
      base_url: baseUrl,
      enabled,
      models: providerModels.map((model) => ({
        id: modelValue(model),
        model_id: modelValue(model),
        label: model.label || null,
        enabled: model.enabled !== false,
        input_modalities: model.input_modalities || model.architecture?.input_modalities || [],
        output_modalities: model.output_modalities || model.architecture?.output_modalities || [],
        supports_vision: modelAcceptsImages(model),
        capabilities_known: modelInputCapabilitiesKnown(model),
      })),
    };
    if (apiKey.trim()) payload.api_key = apiKey.trim();
    await patchProvider(payload);
    if (announce) toast("Configurações do provedor salvas.", "success");
    setApiKey("");
    setDirty(false);
    await reload();
  };

  const toggleEnabled = async (value: boolean) => {
    setEnabled(value);
    setBusy("toggle");
    try {
      await patchProvider({ enabled: value });
      toast(value ? "Provedor ativado." : "Provedor pausado.", "success");
      await reload();
    } catch (error) {
      setEnabled(!value);
      toast(errorMessage(error), "error");
    } finally {
      setBusy("");
    }
  };

  const save = async () => {
    setBusy("save");
    try {
      await persistDraft(true);
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setBusy("");
    }
  };

  const test = async () => {
    setBusy("test");
    try {
      if (dirty) await persistDraft();
      const result = await request<Record<string, unknown>>(`/providers/${encodeURIComponent(provider.id)}/test`, { method: "POST" });
      if (result?.ok === false) {
        throw new Error(String(result.message || "O provedor recusou o teste de conexão."));
      }
      const latency = result?.latency_ms ? ` · ${result.latency_ms} ms` : "";
      toast(`${String(result?.message || "Conexão validada")}${latency}`, "success");
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setBusy("");
    }
  };

  const discover = async () => {
    setBusy("discover");
    try {
      if (dirty) await persistDraft();
      const result = await request<unknown>(`/providers/${encodeURIComponent(provider.id)}/discover-models`, { method: "POST" });
      const discovered = listFrom<Model>(result, ["models", "items", "data"]);
      if (discovered.length) setProviderModels(discovered);
      await reload();
      toast(`${discovered.length || "Novos"} modelo(s) encontrado(s).`, "success");
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setBusy("");
    }
  };

  const addModel = () => {
    const id = newModelId.trim();
    if (!id) return;
    if (providerModels.some((model) => modelValue(model) === id)) {
      toast("Esse modelo já está na lista.", "error");
      return;
    }
    setProviderModels((current) => [...current, { id, model_id: id, label: newModelLabel.trim() || null, enabled: true }]);
    setNewModelId("");
    setNewModelLabel("");
    setDirty(true);
  };

  const remove = async () => {
    if (!window.confirm(`Remover o endpoint “${provider.name}”? Essa ação não pode ser desfeita.`)) return;
    setBusy("delete");
    try {
      await request(`/providers/${encodeURIComponent(provider.id)}`, { method: "DELETE" });
      toast("Provedor removido.", "success");
      await reload();
    } catch (error) {
      toast(errorMessage(error), "error");
      setBusy("");
    }
  };

  const change = <T,>(setter: (value: T) => void, value: T) => {
    setter(value);
    setDirty(true);
  };

  const activeCount = providerModels.filter((model) => model.enabled !== false).length;

  return (
    <section className={`provider-card${open ? " expanded" : ""}${enabled ? " enabled" : ""}`}>
      <div className="provider-summary">
        <button className="provider-expand" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          <span className="endpoint-icon"><Database size={19} /></span>
          <span className="provider-identity"><strong>{provider.name}</strong><small>{provider.base_url}</small></span>
        </button>
        <div className="provider-meta">
          <span className="model-count"><span className={activeCount ? "online-dot" : "offline-dot"} />{activeCount}/{providerModels.length} modelos</span>
          {dirty && <span className="unsaved-mark">Alterações não salvas</span>}
          <label className="compact-switch" title={enabled ? "Desativar endpoint" : "Ativar endpoint"}><input aria-label={enabled ? "Desativar endpoint" : "Ativar endpoint"} type="checkbox" checked={enabled} disabled={busy === "toggle"} onChange={(event) => void toggleEnabled(event.target.checked)} /><i /></label>
          <button className="icon-button expand-button" onClick={() => setOpen((value) => !value)} aria-label={open ? "Recolher provedor" : "Expandir provedor"}>{open ? <ChevronDown size={19} /> : <ChevronRight size={19} />}</button>
        </div>
      </div>
      {open && (
        <div className="provider-body">
          <div className="provider-fields field-grid two-columns">
            <label className="field-group"><span>Nome</span><input value={name} onChange={(event) => change(setName, event.target.value)} /></label>
            <label className="field-group"><span>Base URL</span><input type="url" value={baseUrl} onChange={(event) => change(setBaseUrl, event.target.value)} /></label>
          </div>
          <label className="field-group api-key-field"><span>API key <small>{provider.api_key_configured || provider.has_api_key ? "· configurada" : isOpenRouter ? "· obrigatória" : "· opcional"}</small></span><div className="api-key-input"><KeyRound size={16} /><input type="password" value={apiKey} onChange={(event) => { setApiKey(event.target.value); if (event.target.value) setDirty(true); }} placeholder={provider.api_key_configured || provider.has_api_key ? "Deixe vazio para manter a chave atual" : "Cole a chave do endpoint"} autoComplete="new-password" /></div><em>{isOpenRouter ? "Ao testar, a chave nova é salva e validada antes de qualquer exploração." : "A chave existente nunca é retornada pela API."}</em></label>
          <div className="provider-actions">
            <button className="button secondary small" type="button" onClick={() => void test()} disabled={!!busy}>{busy === "test" ? <Spinner /> : <Zap size={15} />}Testar conexão</button>
            <button className="button secondary small" type="button" onClick={() => void discover()} disabled={!!busy}>{busy === "discover" ? <Spinner /> : <WandSparkles size={15} />}Descobrir modelos</button>
            <button className="button primary small save-provider" type="button" onClick={() => void save()} disabled={!!busy || !name.trim() || !baseUrl.trim()}>{busy === "save" ? <Spinner /> : <Save size={15} />}Salvar alterações</button>
          </div>

          <div className="models-section">
            <div className="models-heading"><div><span className="section-kicker">Modelos</span><h3>Disponíveis neste endpoint</h3></div><span>{activeCount} ativo(s)</span></div>
            {providerModels.length === 0 ? <div className="models-empty"><Boxes size={20} /><span>Nenhum modelo cadastrado. Descubra automaticamente ou adicione abaixo.</span></div> : (
              <div className="model-list">
                {providerModels.map((model, index) => {
                  const id = modelValue(model);
                  return (
                    <div className="model-row" key={`${id}-${index}`}>
                      <label className="model-enabled"><input aria-label={`Ativar modelo ${id}`} type="checkbox" checked={model.enabled !== false} onChange={(event) => { setProviderModels((current) => current.map((item, currentIndex) => currentIndex === index ? { ...item, enabled: event.target.checked } : item)); setDirty(true); }} /><span><Check size={13} /></span></label>
                      <div className="model-name"><strong>{modelLabel(model)}</strong>{model.label && model.label !== id && <small>{id}</small>}</div>
                      <code>{id}</code>
                      <button type="button" className="icon-button danger-icon" onClick={() => { setProviderModels((current) => current.filter((_, currentIndex) => currentIndex !== index)); setDirty(true); }} aria-label={`Remover modelo ${id}`}><Trash2 size={16} /></button>
                    </div>
                  );
                })}
              </div>
            )}
            <div className="add-model-row"><input aria-label="ID do modelo" value={newModelId} onChange={(event) => setNewModelId(event.target.value)} placeholder="Model ID (obrigatório)" onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addModel(); } }} /><input aria-label="Nome do modelo" value={newModelLabel} onChange={(event) => setNewModelLabel(event.target.value)} placeholder="Nome amigável (opcional)" onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addModel(); } }} /><button type="button" className="button secondary small" onClick={addModel} disabled={!newModelId.trim()}><Plus size={15} />Adicionar</button></div>
          </div>
          <div className="provider-danger"><button type="button" onClick={() => void remove()} disabled={!!busy}>{busy === "delete" ? <Spinner /> : <Trash2 size={14} />}Remover endpoint</button></div>
        </div>
      )}
    </section>
  );
}

function AddProviderModal({ request, onClose, onSaved, toast }: { request: ApiRequest; onClose: () => void; onSaved: () => Promise<void>; toast: (message: string, tone?: Toast["tone"]) => void }) {
  const [draft, setDraft] = useState<ProviderDraft>({ name: "OpenRouter", base_url: "https://openrouter.ai/api/v1", api_key: "", enabled: true });
  const [saving, setSaving] = useState(false);
  const isOpenRouter = isOpenRouterUrl(draft.base_url);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      const payload: Record<string, unknown> = { name: draft.name.trim(), base_url: draft.base_url.trim(), enabled: draft.enabled, models: [] };
      if (draft.api_key.trim()) payload.api_key = draft.api_key.trim();
      const provider = await request<{ id: string }>("/providers", { method: "POST", body: JSON.stringify(payload) });
      const discovered = await request<unknown>(`/providers/${encodeURIComponent(provider.id)}/discover-models`, { method: "POST" });
      const models = listFrom<Model>(discovered, ["models", "items", "data"]);
      toast(`${models.length} modelo(s) do OpenRouter carregado(s).`, "success");
      await onSaved();
    } catch (error) {
      toast(errorMessage(error), "error");
      setSaving(false);
    }
  };
  return (
    <Modal title="Adicionar endpoint" description="Conecte qualquer servidor compatível com a API da OpenAI." onClose={onClose}>
      <form className="modal-form" onSubmit={submit}>
        <label className="field-group"><span>Nome do provedor</span><input required placeholder="Ex.: LiteLLM local" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
        <label className="field-group"><span>Base URL</span><input type="url" required placeholder="http://localhost:4000" value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} /></label>
        <label className="field-group"><span>API key <small>{isOpenRouter ? "obrigatória" : "opcional"}</small></span><input type="password" required={isOpenRouter} placeholder="A chave será armazenada somente pelo backend" value={draft.api_key} onChange={(event) => setDraft({ ...draft, api_key: event.target.value })} autoComplete="new-password" /></label>
        <div className="modal-note"><ShieldCheck size={17} /><span>O AutoJev nunca exibe novamente uma chave salva.</span></div>
        <div className="modal-actions"><button type="button" className="button ghost" onClick={onClose}>Cancelar</button><button type="submit" className="button primary" disabled={saving}>{saving ? <><Spinner />Conectando…</> : <><Plus size={16} />Adicionar endpoint</>}</button></div>
      </form>
    </Modal>
  );
}

export function ExplorationDetailView({
  exploration,
  loading,
  providers,
  request,
  toast,
  navigate,
  onRefresh,
  onCancelled,
  onReport,
  apiBase,
  pollInterval,
  liveRerun = false,
}: {
  exploration: Exploration | null;
  loading: boolean;
  providers: Provider[];
  request: ApiRequest;
  toast: (message: string, tone?: Toast["tone"]) => void;
  navigate: (hash: string) => void;
  onRefresh: () => void;
  onCancelled: () => void;
  onReport: (data: unknown) => void;
  apiBase: string;
  pollInterval: number;
  liveRerun?: boolean;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [openingReport, setOpeningReport] = useState(false);
  const [capturePendingKey, setCapturePendingKey] = useState("");
  const [captureSubmittedKey, setCaptureSubmittedKey] = useState("");
  const [captureError, setCaptureError] = useState({ key: "", message: "" });
  const captureRequests = useRef(new Set<string>());
  const [detailTab, setDetailTab] = useState<"overview" | "scenarios" | "coverage" | "activity" | "evidence" | "config">("overview");
  const [selectedRerunStepId, setSelectedRerunStepId] = useState<string | null>(null);
  const preparationConsent = useScenarioRerunPreparationConsent(exploration, request, resuming || cancelling);
  if (loading && !exploration) return <div className="detail-loading"><Spinner size={24} /><span>Carregando exploração…</span></div>;
  if (!exploration) {
    return <section className="panel"><EmptyState icon={AlertCircle} title="Exploração não encontrada" description="O registro pode ter sido removido ou ainda não está disponível." action={<button className="button secondary" onClick={() => navigate("#/exploracoes")}><ArrowLeft size={16} />Voltar ao histórico</button>} /></section>;
  }

  const status = explorationStatus(exploration.status);
  const rerun = exploration.rerun;
  const rerunFeedback = scenarioRerunFeedback(exploration);
  const rerunPreparationConfirmed = preparationConsent.checked;
  const validatingRerunPreparation = rerun?.phase === "validating_preparation";
  const preparationPresentation = scenarioRerunPreparationPresentation(rerun?.preparation, validatingRerunPreparation);
  const capturePhase = rerun?.phase === "waiting_capture" || rerun?.phase === "capturing_print";
  const captureKey = scenarioRerunCaptureKey(exploration);
  const captureBusy = rerun?.phase === "capturing_print" || !!captureKey && (capturePendingKey === captureKey || captureSubmittedKey === captureKey);
  const phase = exploration.phase || (isLive(exploration.status) ? "exploring" : "complete");
  const configuredStepLimit = typeof exploration.options?.max_steps === "number" ? exploration.options.max_steps : null;
  const phaseProgress = exploration.phase_progress || {
    current: exploration.counters?.actions || exploration.actions_count || 0,
    total: configuredStepLimit,
    percentage: exploration.progress,
  };
  const recordedProgress = exploration.overall_progress ?? exploration.progress ?? (["completed", "success", "partial"].includes(status.value) ? 100 : undefined);
  const overallProgress = capturePhase && typeof recordedProgress === "number" ? Math.min(recordedProgress, 95) : recordedProgress;
  const determinateProgress = typeof overallProgress === "number";
  const overallProgressValue = typeof overallProgress === "number" ? overallProgress : 0;
  const events = exploration.events || exploration.activity || exploration.logs || [];
  const waitingForInput = exploration.status.toLowerCase() === "waiting_input";
  const waitingEvent = [...events].reverse().find((event) => event.type === "waiting_input");
  const screenshot = resolveAssetUrl(exploration.screenshot_url);
  const providerName = (id?: string) => providers.find((provider) => provider.id === id)?.name || id || "—";
  const counters = {
    pages: exploration.counters?.pages_visited || exploration.counters?.pages || exploration.pages_visited || 0,
    actions: exploration.counters?.actions || exploration.actions_count || 0,
    issues: exploration.counters?.issues || exploration.issues_count || 0,
    scenarios: exploration.counters?.scenarios || exploration.counters?.test_scenarios || exploration.scenarios_count || 0,
  };
  const postprocessing = exploration.postprocessing || {};
  const visualEvidenceMode = explorationEvidenceMode(exploration.options) === "visual";
  const enrichmentCopy = visualEvidenceMode
    ? `${exploration.visual_review_summary?.assessed ?? postprocessing.enriched ?? 0} revisões visuais válidas`
    : `${postprocessing.enriched || 0} documentados pela LLM`;
  const completion = exploration.completion || {};
  const actionLimitReached = completion.type === "limit_reached" || completion.reason === "action_limit";
  const completedWithRestrictions = completion.type === "coverage_complete_with_restrictions";
  const browserPhaseActive = ["exploring", "waiting_input"].includes(phase);
  const terminalOutcomeVisible = phase === "complete" && !isLive(exploration.status);
  const parallelScenarioPipelineStarted = events.some((event) => event.type === "scenario_parallel_started");
  const scenariosRunningInParallel = isLive(exploration.status) && browserPhaseActive && parallelScenarioPipelineStarted;
  const phaseSteps = rerun ? [
    { key: "preparing", label: "Preparar navegador", detail: "pré-condições manuais" },
    { key: "executing", label: "Reexecutar o CT", detail: "uma ação original" },
    ...((rerun.capture_count || 0) > 0 ? [{ key: "capture", label: "Capturas manuais", detail: `${rerun.captures_completed || 0} de ${rerun.capture_count} salvas` }] : []),
    { key: "reporting", label: "Novo relatório", detail: exploration.report_available ? "pronto" : "pendente" },
  ] : [
    { key: "exploring", label: "Explorar", detail: `${exploration.browser_progress?.current ?? counters.actions} ações` },
    {
      key: "documenting",
      label: scenariosRunningInParallel ? "CTs + revisão em paralelo" : "CTs e evidências",
      detail: scenariosRunningInParallel
        ? `${counters.scenarios} preservados ao vivo`
        : `${postprocessing.total || counters.scenarios} casos`,
    },
    { key: "reporting", label: "Relatório", detail: exploration.report_available ? "pronto" : "pendente" },
  ];
  const phaseStepState = (stepKey: string): "done" | "active" | "pending" => {
    if (rerun) return scenarioRerunPhaseStepState(exploration, stepKey);
    if (phase === "complete") return "done";
    if (stepKey === "exploring") {
      if (browserPhaseActive || phase === "preparing") return "active";
      return ["documenting", "reporting"].includes(phase) ? "done" : "pending";
    }
    if (stepKey === "documenting") {
      if (phase === "documenting" || scenariosRunningInParallel) return "active";
      return phase === "reporting" ? "done" : "pending";
    }
    return phase === "reporting" ? "active" : "pending";
  };
  const phaseHeadline = rerun
    ? rerunFeedback?.notStarted ? "Reexecução não iniciada"
      : rerun.phase === "generating_report" ? "Montando o relatório da reexecução"
      : validatingRerunPreparation ? "Entrando e conferindo preparação…"
      : rerun.phase === "waiting_capture" ? "Aguardando seu print para continuar"
      : rerun.phase === "capturing_print" ? "Salvando a captura solicitada"
      : rerun.phase === "waiting_preparation" ? preparationPresentation.title
      : rerun.phase === "executing" ? "Reexecutando a ação original do CT"
        : rerun.phase === "finished" ? `Reexecução ${status.label.toLocaleLowerCase("pt-BR")}` : "Preparando a reexecução do CT"
    : terminalOutcomeVisible && actionLimitReached
    ? `Limite de ${exploration.browser_progress?.total || configuredStepLimit || counters.actions} ações atingido`
    : terminalOutcomeVisible && completedWithRestrictions
      ? "Exploração concluída com restrições"
      : phase === "documenting"
        ? visualEvidenceMode ? "Analisando as capturas dos CTs" : "Revisando as evidências dos CTs"
        : exploration.phase_label || status.label;
  const phaseDescription = rerun
    ? rerunFeedback?.notStarted ? `${rerunFeedback.detail} O resultado original não foi alterado.`
      : validatingRerunPreparation ? "Se houver um formulário de login preenchido e seguro, o AutoJev tentará enviá-lo. Depois conferirá a página, os controles e as condições iniciais antes de executar a ação salva."
      : rerun.phase === "waiting_capture" ? "O CT está pausado. Cole, arraste ou escolha sua imagem no painel acima e confirme em «Anexar print e continuar». Pode ser um print de qualquer tela ou aplicativo."
      : rerun.phase === "capturing_print" ? "Sua imagem será anexada ao novo relatório como evidência enviada por você. Aguarde o salvamento; a ação original não será repetida."
      : rerun.phase === "waiting_preparation" ? preparationPresentation.instruction
      : rerun.phase === "executing" ? "Somente a ação salva e suas verificações locais serão repetidas. Não há exploração autônoma nem chamadas à LLM."
        : `Esta é uma execução separada. As versões anteriores e suas evidências ficam preservadas no histórico do teste; o relatório de origem mostra o resultado mais recente. ${(rerun.capture_count || 0) > 0 ? `${rerun.captures_completed || 0} de ${rerun.capture_count} prints manuais registrados; SWS e SQL não foram executados.` : "SWS e SQL não são executados."}`
    : terminalOutcomeVisible && actionLimitReached
    ? "A automação não falhou na ação 67: ela concluiu o limite configurado e preservou tudo o que encontrou. A cobertura ficou parcial porque ainda havia opções a explorar."
    : terminalOutcomeVisible && completedWithRestrictions
      ? "A cobertura alcançável terminou. As verificações que não puderam ser concluídas foram preservadas com suas evidências e restrições, sem serem contadas como testes aprovados."
      : phase === "documenting" && visualEvidenceMode
        ? "Os CTs e verificações locais já foram preservados. A LLM auxiliar está analisando as capturas; não reexecuta testes nem controla o navegador."
        : exploration.phase_description || (isLive(exploration.status) ? `Atualização automática a cada ${pollInterval / 1000}s` : "Resultados preservados e prontos para consulta.");

  const cancel = async () => {
    setCancelling(true);
    try {
      await request(`/explorations/${encodeURIComponent(exploration.id)}/cancel`, { method: "POST" });
      toast("Cancelamento solicitado.", "success");
      onCancelled();
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setCancelling(false);
    }
  };

  const resume = async () => {
    if (resuming || (rerun && !preparationConsent.consume())) return;
    setResuming(true);
    try {
      await request(`/explorations/${encodeURIComponent(exploration.id)}/resume`, { method: "POST" });
      toast(rerun ? "Tentando entrar, se houver login preenchido, e conferindo a preparação. A ação original só será executada se os pré-requisitos corresponderem." : "Exploração retomada.", rerun ? "info" : "success");
      onRefresh();
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setResuming(false);
    }
  };

  const capturePrint = async (imageDataUrl: string) => {
    const checkpoint = rerun?.capture_checkpoint;
    if (!checkpoint || rerun?.phase !== "waiting_capture" || !captureKey || captureRequests.current.has(captureKey)) return;
    captureRequests.current.add(captureKey);
    setCapturePendingKey(captureKey);
    setCaptureError({ key: captureKey, message: "" });
    try {
      await captureScenarioRerunPrint(request, exploration, checkpoint, imageDataUrl);
      setCaptureSubmittedKey(captureKey);
      toast("Print enviado. O CT continua após salvar sua imagem, sem repetir a ação original.", "info");
      onRefresh();
    } catch (caught) {
      captureRequests.current.delete(captureKey);
      setCaptureError({ key: captureKey, message: errorMessage(caught) });
      onRefresh();
    } finally { setCapturePendingKey(""); }
  };

  const resumeVisualReviews = async (options: { max_requests: number; max_attempts: number }) => {
    try {
      await request(`/explorations/${encodeURIComponent(exploration.id)}/visual-reviews/resume`, { method: "POST", body: JSON.stringify(options) });
      toast("Revisões visuais pendentes iniciadas. A exploração não será repetida.", "success");
      onRefresh();
      return true;
    } catch (error) { toast(errorMessage(error), "error"); return false; }
  };

  const cancelVisualReviews = async () => {
    try {
      await request(`/explorations/${encodeURIComponent(exploration.id)}/visual-reviews/cancel`, { method: "POST" });
      toast("Parada das revisões solicitada. Os CTs e a exploração foram preservados.", "success");
      onRefresh();
    } catch (error) { toast(errorMessage(error), "error"); }
  };

  const openReport = async () => {
    setOpeningReport(true);
    try {
      const reportExplorationId = rerun?.report_exploration_id || exploration.id;
      const reportScenarioId = rerun?.report_scenario_id;
      const response = await fetch(`${apiBase}/explorations/${encodeURIComponent(reportExplorationId)}/report`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Não foi possível abrir o relatório (${response.status}).`);
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const data = await response.json() as Record<string, unknown>;
        const url = data.url || data.report_url || data.download_url;
        if (typeof url === "string") window.open(url, "_blank", "noopener,noreferrer");
        else onReport(data);
      } else if (contentType.includes("text/html")) {
        const html = await response.text();
        const connectedHtml = connectReportToApplication(html, reportExplorationId, window.location.origin, apiBase);
        const blobUrl = URL.createObjectURL(new Blob([connectedHtml], { type: "text/html" }));
        window.open(blobUrl + (reportScenarioId ? `#view/results/${encodeURIComponent(reportScenarioId)}` : ""), "_blank", "noopener,noreferrer");
        window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      } else {
        const blob = await response.blob();
        const blobUrl = URL.createObjectURL(blob);
        window.open(blobUrl, "_blank", "noopener,noreferrer");
        window.setTimeout(() => URL.revokeObjectURL(blobUrl), 60000);
      }
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setOpeningReport(false);
    }
  };

  const preparationPanel = waitingForInput && !capturePhase ? (
    <section className={`panel intervention-card intervention-wide ${rerun ? "rerun-intervention" : ""}`}>
      <span className="section-kicker"><span className="live-pulse" />{rerun ? preparationPresentation.kicker : "Ação necessária"}</span>
      <div><h2>{rerun ? preparationPresentation.title : "Faça login no navegador"}</h2>{rerun ? <ScenarioRerunPreparation compact={liveRerun} preparation={rerun.preparation} validating={validatingRerunPreparation} /> : <p>{waitingEvent?.message || "Preencha usuário e senha no navegador. Ao continuar, o AutoJev tentará enviar um formulário de login preenchido e seguro."}</p>}{rerun && <>
        <label className="rerun-consent"><input type="checkbox" checked={rerunPreparationConfirmed} disabled={resuming || validatingRerunPreparation || preparationConsent.pending} onChange={(event) => preparationConsent.setChecked(event.target.checked)} /><span>{preparationPresentation.consent_label}</span></label>
        {preparationConsent.pending && <p role="status">Sincronizando autorização…</p>}
        {preparationConsent.error && <p className="rerun-error" role="alert">{preparationConsent.error}</p>}
        {preparationConsent.shared && preparationConsent.error && !preparationConsent.ready && !preparationConsent.pending && !validatingRerunPreparation && <button type="button" className="button secondary small" onClick={() => preparationConsent.retry()}><RefreshCcw size={14} />Tentar sincronizar</button>}
      </>}</div>
      <button className="button primary" onClick={() => void resume()} disabled={resuming || (!!rerun && (!rerunPreparationConfirmed || !preparationConsent.ready || preparationConsent.pending))}>{resuming || validatingRerunPreparation || preparationConsent.pending ? <Spinner /> : <Play size={16} />}{rerun ? resuming ? "Entrando e conferindo preparação…" : preparationConsent.pending ? "Sincronizando autorização…" : preparationPresentation.button_label : "Continuar exploração"}</button>
      {rerun && <p className="rerun-capture-note">{preparationPresentation.note}</p>}
    </section>
  ) : null;
  const showLiveRerun = !!rerun && liveRerun;
  const capturePanel = capturePhase && <ScenarioRerunCapture key={captureKey || exploration.id} checkpoint={rerun?.capture_checkpoint} completed={rerun?.captures_completed} total={rerun?.capture_count} busy={captureBusy} error={captureError.key === captureKey ? captureError.message : ""} timeoutSeconds={rerun?.capture_timeout_seconds} onCapture={(imageDataUrl) => void capturePrint(imageDataUrl)} request={request} explorationId={exploration.id} />;
  const rerunControls = <div id="rerun-live-controls" className={showLiveRerun ? "rerun-live-controls" : "rerun-standard-controls"}>
    {capturePanel}
    {showLiveRerun && preparationPanel}
  </div>;
  const provenancePanel = rerun && <section className="panel rerun-provenance" aria-label="Origem da reexecução"><RefreshCcw size={20} /><div><strong>Reexecução de {rerun.source_scenario_id || "um CT"}</strong><p>Nova execução com evidências próprias. As versões anteriores e suas evidências ficam preservadas no histórico do teste; o relatório de origem mostra o resultado mais recente. Verificações locais{(rerun.capture_count || 0) > 0 ? ` e ${rerun.capture_count} pausa(s) para print com confirmação manual` : " apenas"}; SWS, SQL e análises da LLM não são executados.</p>{rerun.source_exploration_id && <button type="button" className="text-button" onClick={() => navigate(`#/exploracoes/${encodeURIComponent(rerun.source_exploration_id!)}`)}>Abrir execução original <ArrowRight size={14} /></button>}</div></section>;

  return (
    <div className={`view-stack detail-view ${rerun ? "rerun-detail" : ""} ${showLiveRerun ? "rerun-live-detail" : ""}`}>
      <div className="detail-breadcrumb"><button onClick={() => navigate("#/exploracoes")}><ArrowLeft size={15} />Explorações</button><ChevronRight size={14} /><span>{hostFromUrl(exploration.app_url)}</span></div>
      <header className="detail-header">
        {rerun && <button type="button" className="icon-button rerun-compact-back" aria-label="Voltar para explorações" onClick={() => navigate("#/exploracoes")}><ArrowLeft size={18} /></button>}
        <div className="detail-title"><span className="app-avatar large"><Globe2 size={22} /></span><div><div className="detail-title-line"><h1>{rerun ? `Reexecução de ${rerun.source_scenario_id || "um CT"}` : hostFromUrl(exploration.app_url)}</h1>{terminalOutcomeVisible && actionLimitReached ? <span className="status-badge status-warning"><span className="status-dot" />Limite anterior atingido</span> : terminalOutcomeVisible && completedWithRestrictions ? <span className="status-badge status-warning"><span className="status-dot" />Concluída com restrições</span> : <StatusBadge status={exploration.status} completionType={completion.type} />}</div><a href={exploration.app_url} target="_blank" rel="noreferrer">{exploration.app_url}<SquareArrowOutUpRight size={13} /></a></div></div>
        <div className="page-actions">
          <button className="button secondary" onClick={onRefresh}><RefreshCcw size={16} />Atualizar</button>
          {isLive(exploration.status) ? (
            <button className="button danger" onClick={() => void cancel()} disabled={cancelling}>{cancelling ? <Spinner /> : <CircleStop size={16} />}Cancelar</button>
          ) : exploration.report_available ? (
            <button className="button primary" onClick={() => void openReport()} disabled={openingReport}>{openingReport ? <Spinner /> : <FileText size={16} />}Abrir relatório</button>
          ) : (
            <button className="button secondary" disabled><FileText size={16} />Relatório indisponível</button>
          )}
        </div>
      </header>

      {rerun && <details className="rerun-origin-details"><summary><ShieldCheck size={14} />Sobre esta reexecução <ChevronDown size={13} /></summary>{provenancePanel}</details>}

      {rerun && <nav className="rerun-view-switch" aria-label="Telas da reexecução">
        <button type="button" className={showLiveRerun ? "active" : ""} aria-current={showLiveRerun ? "page" : undefined} onClick={() => navigate(`#/exploracoes/${encodeURIComponent(exploration.id)}/acompanhar`)}><MonitorPlay size={16} />Acompanhar reexecução</button>
        <button type="button" className={!showLiveRerun ? "active" : ""} aria-current={!showLiveRerun ? "page" : undefined} onClick={() => navigate(`#/exploracoes/${encodeURIComponent(exploration.id)}`)}><Gauge size={16} />Visão geral e resultados</button>
      </nav>}

      {rerun ? <ScenarioRerunLiveView rerun={rerun} showLiveContent={showLiveRerun} active={isLive(exploration.status)} selectedStepId={selectedRerunStepId} onSelectStep={(step) => {
        setSelectedRerunStepId(step.id);
        if (capturePhase && step.state === "waiting" && step.checkpoint_id) document.getElementById("rerun-live-controls")?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      }} pollInterval={pollInterval} phaseHeadline={phaseHeadline} controls={rerunControls} /> : rerunControls}

      {(rerunFeedback || exploration.error) && <div className={`callout ${rerunFeedback?.notStarted ? "warning-callout" : "error-callout"}`} role="status"><AlertCircle size={19} /><div><strong>{rerunFeedback?.title || "A execução encontrou um erro"}</strong><span>{rerunFeedback?.detail || exploration.error}</span>{rerunFeedback?.nextStep && <span>{rerunFeedback.nextStep}</span>}</div></div>}

      {!showLiveRerun && <>
      <section className={`execution-command panel phase-${phase} ${rerunFeedback?.notStarted ? "execution-not-started" : ""}`} aria-live="polite">
        <div className="execution-command-head">
          <span className={`phase-symbol ${isLive(exploration.status) ? "live" : rerunFeedback?.notStarted ? "warning" : status.value === "failed" ? "error" : ""}`}>
            {isLive(exploration.status) ? <Spinner size={22} /> : status.value === "failed" || rerunFeedback?.notStarted ? <AlertCircle size={22} /> : <CheckCircle2 size={22} />}
          </span>
          <div className="execution-command-copy">
            <span className="section-kicker">{isLive(exploration.status) ? <><span className="live-pulse" />Agora no AutoJev</> : "Resultado da execução"}</span>
            <h2>{phaseHeadline}</h2>
            <p>{phaseDescription}</p>
            {(phase === "documenting" || scenariosRunningInParallel) && (
              <div className="ct-live-copy">
                {scenariosRunningInParallel ? (
                  <>
                    <strong>Exploração + criação contínua de CTs</strong>
                    <span>{counters.scenarios} casos já preservados · {enrichmentCopy}</span>
                  </>
                ) : (
                  <>
                    <strong>CT {Math.min(phaseProgress.current || 0, phaseProgress.total || 0)} de {phaseProgress.total || counters.scenarios}</strong>
                    <span>{counters.scenarios} casos já preservados · {enrichmentCopy}</span>
                  </>
                )}
              </div>
            )}
          </div>
          <div className="overall-progress">{determinateProgress ? <strong>{Math.round(overallProgressValue)}<small>%</small></strong> : <strong className="progress-running">Em andamento</strong>}<span>{determinateProgress ? "processamento" : "explorando o escopo"}</span></div>
        </div>
        {(phase === "documenting" || scenariosRunningInParallel) && (postprocessing.pending || 0) > 0 && <p className="ct-queue-copy">{postprocessing.pending} revisões auxiliares pendentes · {postprocessing.requests || 0} chamadas realizadas{(postprocessing.deferred || 0) > 0 ? ` · ${postprocessing.deferred} aguardando nova tentativa` : ""}. Os CTs locais já podem ser consultados.</p>}
        <div className={`large-progress ${determinateProgress ? "" : "indeterminate"}`}><span style={determinateProgress ? { width: `${Math.min(100, Math.max(0, overallProgressValue))}%` } : undefined} /></div>
        <div className={`phase-track ${rerun && (rerun.capture_count || 0) > 0 ? "with-manual-captures" : ""}`} aria-label="Fases da execução">
          {phaseSteps.map((step, index) => {
            const stepState = phaseStepState(step.key);
            return (
              <div key={step.key} className={`phase-step ${stepState}`}>
                <span>{stepState === "done" ? <Check size={14} /> : index + 1}</span>
                <div><strong>{step.label}</strong><small>{step.detail}</small></div>
              </div>
            );
          })}
        </div>
        <div className="live-counters"><DetailCounter icon={Globe2} value={counters.pages} label="páginas" /><DetailCounter icon={Zap} value={counters.actions} label="ações" /><DetailCounter icon={AlertCircle} value={counters.issues} label="sinais técnicos" /><DetailCounter icon={FileCheck2} value={counters.scenarios} label="CTs" /><DetailCounter icon={Hash} value={formatTokens(exploration.usage?.total_tokens)} label="tokens" /><DetailCounter icon={Clock3} value={explorationDuration(exploration)} label="duração total" /></div>
        <CostBreakdown usage={exploration.usage} />
      </section>

      <VerificationStrengthSummary counts={exploration.verification_summary} />

      {explorationEvidenceMode(exploration.options) === "visual" && <VisualReviewSummary scenarios={exploration.scenarios || []} active={isLive(exploration.status)} summary={exploration.visual_review_summary} requestBudget={typeof exploration.options?.auxiliary_max_requests === "number" ? exploration.options.auxiliary_max_requests : undefined} requests={postprocessing.requests} />}
      {visualEvidenceMode && !isLive(exploration.status) && <VisualReviewResumeControls
        state={exploration.visual_review_resume}
        pending={exploration.visual_review_resume?.pending ?? ((exploration.visual_review_summary?.pending ?? visualReviewCounts(exploration.scenarios || []).pending) + (exploration.visual_review_summary?.unavailable ?? visualReviewCounts(exploration.scenarios || []).unavailable))}
        initialBudget={typeof exploration.options?.auxiliary_max_requests === "number" ? exploration.options.auxiliary_max_requests : 300}
        onResume={resumeVisualReviews}
        onCancel={cancelVisualReviews}
      />}

      {preparationPanel}

      <section className="detail-workspace panel">
        <div className="detail-tabs" role="tablist" aria-label="Detalhes da exploração">
          <button role="tab" aria-selected={detailTab === "overview"} className={detailTab === "overview" ? "active" : ""} onClick={() => setDetailTab("overview")}><Gauge size={16} />Visão geral</button>
          <button role="tab" aria-selected={detailTab === "scenarios"} className={detailTab === "scenarios" ? "active" : ""} onClick={() => setDetailTab("scenarios")}><FileCheck2 size={16} />Casos de teste <span>{exploration.scenarios?.length ?? counters.scenarios}</span></button>
          <button role="tab" aria-selected={detailTab === "coverage"} className={detailTab === "coverage" ? "active" : ""} onClick={() => setDetailTab("coverage")}><Focus size={16} />Cobertura e restrições <span>{completion.restrictions?.length || completion.blocked_candidates || 0}</span></button>
          <button role="tab" aria-selected={detailTab === "activity"} className={detailTab === "activity" ? "active" : ""} onClick={() => setDetailTab("activity")}><Activity size={16} />Atividade <span>{events.length}</span></button>
          <button role="tab" aria-selected={detailTab === "evidence"} className={detailTab === "evidence" ? "active" : ""} onClick={() => setDetailTab("evidence")}><Eye size={16} />Evidência</button>
          <button role="tab" aria-selected={detailTab === "config"} className={detailTab === "config" ? "active" : ""} onClick={() => setDetailTab("config")}><Settings size={16} />Configuração</button>
        </div>

        <div className="detail-tab-content" role="tabpanel">
          {detailTab === "overview" && (
            <div className="overview-grid">
              <section className="result-summary-card">
                <span className="section-kicker">Leitura rápida</span>
                <h2>{actionLimitReached ? "A automação parou pelo limite, não por uma falha na etapa 67" : phaseHeadline}</h2>
                <p>{actionLimitReached ? `Foram executadas ${counters.actions} ações e criados ${counters.scenarios} casos de teste. O número 67 representa os CTs gerados.` : phaseDescription}</p>
                {actionLimitReached && (
                  <div className="coverage-summary warning">
                    <AlertCircle size={18} />
                    <div><strong>Cobertura parcial</strong><span>{completion.frontier_candidates ?? "Algumas"} opções ainda estavam na fila de exploração quando o limite foi alcançado.</span></div>
                  </div>
                )}
                {completedWithRestrictions && (
                  <div className="coverage-summary warning">
                    <AlertCircle size={18} />
                    <div><strong>Concluída com restrições</strong><span>{completion.blocked_candidates ?? "Algumas"} verificação(ões) não puderam ser concluídas. Isso não significa a mesma quantidade de telas ou defeitos.</span><button className="text-button" onClick={() => setDetailTab("coverage")}>Examinar as restrições <ArrowRight size={14} /></button></div>
                  </div>
                )}
                {counters.issues > 0 && (
                  <div className="coverage-summary">
                    <ShieldCheck size={18} />
                    <div><strong>Sinais técnicos dentro da evidência</strong><span>Cada ocorrência aparece na linha do tempo do CT correspondente. Registros sem vínculo com um CT ficam nas observações da execução, com a página e o momento registrados. Um sinal de console ou rede não confirma sozinho um defeito funcional.</span><button className="text-button" onClick={() => setDetailTab("scenarios")}>Examinar os casos de teste <ArrowRight size={14} /></button></div>
                  </div>
                )}
                <div className="result-facts">
                  <div><small>Estado da cobertura</small><strong>{actionLimitReached ? "Execução antiga limitada" : completedWithRestrictions ? "Concluída com restrições" : status.label}</strong></div>
                  <div><small>Estados descobertos</small><strong>{completion.known_states ?? counters.pages}</strong></div>
                  <div><small>{visualEvidenceMode ? "Revisões visuais válidas" : "CTs documentados pela LLM"}</small><strong>{visualEvidenceMode ? exploration.visual_review_summary?.assessed ?? postprocessing.enriched ?? 0 : postprocessing.enriched ?? 0}</strong></div>
                  <div><small>CTs com evidência local</small><strong>{postprocessing.local_evidence || Math.max(0, counters.scenarios - (postprocessing.enriched || 0))}</strong></div>
                </div>
              </section>

              <section className="browser-preview-card">
                <div className="panel-heading row-heading"><div><span className="section-kicker"><Eye size={14} />Última evidência</span><h2>Onde a navegação terminou</h2></div><button className="text-button" onClick={() => setDetailTab("evidence")}>Ver maior <ArrowRight size={14} /></button></div>
                <div className="browser-frame compact-browser">
                  <div className="browser-chrome"><span className="window-dots"><i /><i /><i /></span><div className="browser-address"><ShieldCheck size={12} />{exploration.app_url}</div><MoreHorizontal size={16} /></div>
                  <div className="browser-canvas"><ScreenshotPreview src={screenshot} revision={exploration.updated_at || "latest"} alt={`Último screenshot de ${hostFromUrl(exploration.app_url)}`} /></div>
                </div>
              </section>

              <section className="recent-activity-card">
                <div className="panel-heading row-heading"><div><span className="section-kicker">Últimas mudanças</span><h2>O que acabou de acontecer</h2></div><button className="text-button" onClick={() => setDetailTab("activity")}>Ver tudo <ArrowRight size={14} /></button></div>
                {events.length === 0 ? <div className="activity-empty"><Activity size={20} /><span>A atividade aparecerá conforme a exploração avança.</span></div> : <div className="activity-list compact-list">{[...events].reverse().slice(0, 5).map((event, index) => { const copy = activityCopy(event); return <div className="activity-item" key={event.id || `${event.timestamp}-${index}`}><span className={`activity-marker ${event.level || event.status || event.type || "info"}`}><Activity size={12} /></span><div><strong>{copy.title}</strong>{copy.detail && <p>{copy.detail}</p>}<small>{formatDate(event.timestamp || event.created_at)}</small></div></div>; })}</div>}
              </section>
            </div>
          )}

          {detailTab === "scenarios" && (
            <ScenarioTimelinePanel key={exploration.id} scenarios={exploration.scenarios || []} contexts={exploration.scenario_contexts || []} explorationId={exploration.id} apiBase={apiBase} isRunning={isLive(exploration.status)} />
          )}

          {detailTab === "coverage" && <CoverageRestrictionsPanel restrictions={completion.restrictions || []} resolvedRestrictions={completion.resolved_restrictions || []} blockedCount={completion.blocked_candidates || 0} explorationId={exploration.id} apiBase={apiBase} />}

          {detailTab === "activity" && (
            <section className="activity-card activity-card-wide">
              <div className="panel-heading row-heading"><div><span className="section-kicker">Linha do tempo explicada</span><h2>Atividade da exploração</h2><p>Navegação, criação dos CTs e relatório aparecem como etapas separadas.</p></div>{isLive(exploration.status) && <span className="live-mini"><span />ao vivo</span>}</div>
              {events.length === 0 ? <div className="activity-empty"><Activity size={20} /><span>A atividade detalhada aparecerá conforme a exploração avança.</span></div> : <div className="activity-list">{[...events].reverse().slice(0, 120).map((event, index) => { const copy = activityCopy(event); return <div className="activity-item" key={event.id || `${event.timestamp}-${index}`}><span className={`activity-marker ${event.level || event.status || event.type || "info"}`}><Activity size={12} /></span><div><strong>{copy.title}</strong>{copy.detail && <p>{copy.detail}</p>}<small>{formatDate(event.timestamp || event.created_at)}</small></div></div>; })}</div>}
            </section>
          )}

          {detailTab === "evidence" && (
            <section className="browser-panel evidence-panel">
              <div className="panel-heading row-heading"><div><span className="section-kicker"><Eye size={14} />Última observação</span><h2>Evidência do navegador</h2></div>{screenshot && <a className="text-button" href={screenshot} target="_blank" rel="noreferrer">Abrir imagem <SquareArrowOutUpRight size={14} /></a>}</div>
              <div className="browser-frame"><div className="browser-chrome"><span className="window-dots"><i /><i /><i /></span><div className="browser-address"><ShieldCheck size={12} />{exploration.app_url}</div><MoreHorizontal size={16} /></div><div className="browser-canvas"><ScreenshotPreview src={screenshot} revision={exploration.updated_at || "latest"} alt={`Último screenshot de ${hostFromUrl(exploration.app_url)}`} /></div></div>
              <div className="evidence-strip"><span><Eye size={15} />Screenshot salvo no relatório</span><span><Code2 size={15} />DOM estruturado</span><span><ShieldCheck size={15} />{explorationEvidenceMode(exploration.options) === "visual" ? "Modo configurado: texto + imagem" : "Modo configurado: somente texto"}</span></div>
            </section>
          )}

          {detailTab === "config" && (
            <section className="configuration-card configuration-wide">
              <div className="panel-heading"><span className="section-kicker">Modelos da execução</span><h2>Configuração usada</h2></div>
              <div className="configuration-grid">
                <div className="exploration-scope"><span className="model-role-icon"><Focus size={18} /></span><div><small>Escopo da exploração</small><strong>{explorationFocusArea(exploration.options) || "Aplicação inteira"}</strong><em>{explorationFocusArea(exploration.options) ? "O JEV prioriza e esgota este recorte funcional." : "O JEV explora todos os recursos que conseguir descobrir."}</em></div></div>
                <div className="config-model"><span className="model-role-icon amber"><BrainCircuit size={18} /></span><div><small>JEV · agente QA</small><strong>{exploration.agent?.model_id || exploration.agent?.model || "—"}</strong><em>{providerName(exploration.agent?.provider_id || exploration.agent?.provider)}</em></div></div>
                <div className="config-model"><span className="model-role-icon teal"><Sparkles size={18} /></span><div><small>LLM auxiliar</small><strong>{exploration.auxiliary_llm?.model_id || exploration.auxiliary_llm?.model || "—"}</strong><em>{providerName(exploration.auxiliary_llm?.provider_id || exploration.auxiliary_llm?.provider)}</em></div></div>
                <div className="config-model"><span className="model-role-icon teal">{explorationEvidenceMode(exploration.options) === "visual" ? <Eye size={18} /> : <Code2 size={18} />}</span><div><small>Evidência da LLM auxiliar</small><strong>{explorationEvidenceMode(exploration.options) === "visual" ? "Visual · texto + imagem" : "Econômico · somente texto"}</strong><em>O screenshot permanece salvo localmente nos dois modos.</em></div></div>
              </div>
            </section>
          )}
        </div>
      </section>
      </>}
      {showLiveRerun && !isLive(exploration.status) && <div className="rerun-live-end"><ShieldCheck size={17} /><p>Esta reexecução terminou. Os cards mostram apenas os registros desta tentativa. As versões anteriores e suas evidências ficam preservadas no histórico do teste; o relatório de origem mostra o resultado mais recente.</p><button type="button" className="text-button" onClick={() => navigate(`#/exploracoes/${encodeURIComponent(exploration.id)}`)}>Ver resultados completos <ArrowRight size={15} /></button></div>}
    </div>
  );
}

export function ScenarioRerunLiveView({ rerun, active = false, selectedStepId, onSelectStep, pollInterval = 3000, phaseHeadline, controls, showLiveContent = true }: {
  rerun: NonNullable<Exploration["rerun"]>;
  active?: boolean;
  selectedStepId?: string | null;
  onSelectStep?: (step: RerunLiveStep) => void;
  pollInterval?: number;
  phaseHeadline?: string;
  controls?: ReactNode;
  showLiveContent?: boolean;
}) {
  const preparationPresentation = scenarioRerunPreparationPresentation(rerun.preparation, rerun.phase === "validating_preparation");
  const progress = rerun.step_progress?.version === 1 ? rerun.step_progress : undefined;
  const selected = progress?.steps.find((step) => step.id === selectedStepId)
    || progress?.steps.find((step) => step.id === progress.current_id) || progress?.steps[0];
  return <section className={showLiveContent ? "rerun-live-page" : "rerun-standard-page"} aria-label={showLiveContent ? "Acompanhamento dos passos da reexecução" : undefined}>
    {showLiveContent && <div className="rerun-live-page-heading"><div><span className="section-kicker"><MonitorPlay size={14} />{active ? "Reexecução ao vivo" : "Passos da reexecução"}</span><h2>Seu teste, <span>passo a passo</span></h2><p>{phaseHeadline || "Acompanhe a ação, as conferências e os prints desta tentativa."}</p></div><span className={`rerun-live-sync ${active ? "is-live" : ""}`}>{active && <span className="live-pulse" />}{active ? `Atualização a cada ${pollInterval / 1000}s` : "Evidências preservadas"}</span></div>}
    <div className={showLiveContent ? "rerun-live-workspace" : "rerun-standard-workspace"}>
    {controls}
    {showLiveContent && !controls && active && ["waiting_capture", "waiting_preparation"].includes(rerun.phase || "") && <button type="button" className="rerun-live-attention" aria-controls="rerun-live-controls" onClick={() => document.getElementById("rerun-live-controls")?.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" })}>{rerun.phase === "waiting_capture" ? <ImagePlus size={18} /> : <KeyRound size={18} />}<span>{rerun.phase === "waiting_capture" ? "Aguardando seu print" : preparationPresentation.title}<small>{rerun.phase === "waiting_capture" ? "Cole, arraste ou escolha uma imagem para continuar" : preparationPresentation.instruction}</small></span><ArrowRight size={18} /></button>}
    {showLiveContent && <RerunLiveBoard compact progress={progress} scenarioId={rerun.source_scenario_id} active={active} selectedStepId={selected?.id} onSelectStep={onSelectStep} />}
    {showLiveContent && selected && (!controls || selected.state !== "waiting") && <div className="rerun-live-step-detail" data-step-state={selected.state}>
      <span className="rerun-live-detail-icon">{selected.state === "done" ? <CheckCircle2 size={22} /> : selected.state === "failed" || selected.state === "blocked" ? <AlertCircle size={22} /> : selected.state === "waiting" ? <ImagePlus size={22} /> : <Focus size={22} />}</span>
      <div><span className="section-kicker">{selected.id === progress?.current_id ? "Etapa atual" : "Etapa selecionada"} · {selected.label}</span><h3>{selected.title}</h3><p>{selected.description}</p>{selected.occurred_at && <time dateTime={selected.occurred_at}>{formatDate(selected.occurred_at)}</time>}</div>
    </div>}
    </div>
  </section>;
}

export function scenarioScreenshotUrl(apiBase: string, explorationId: string, screenshot?: string | null) {
  if (!screenshot || /(^|[\\/])\.\.([\\/]|$)/.test(screenshot) || /[?#]/.test(screenshot) || [...screenshot].some((character) => character.charCodeAt(0) < 32)) return null;
  const filename = screenshot.replace(/\\/g, "/").split("/").pop();
  if (!filename || !/\.(?:png|jpe?g|webp)$/i.test(filename)) return null;
  try {
    const api = new URL(apiBase);
    if (!["http:", "https:"].includes(api.protocol)) return null;
    return `${apiBase.replace(/\/$/, "")}/explorations/${encodeURIComponent(explorationId)}/screenshots/${encodeURIComponent(filename)}`;
  } catch {
    return null;
  }
}

function safeScenarioPageUrl(value?: string) {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? parsed.href : null;
  } catch {
    return null;
  }
}

function scenarioSignals(scenario: ScenarioCase) {
  return scenario.evidence?.technical_signals || scenario.evidence?.timeline?.flatMap((entry) => entry.signal ? [entry.signal] : []) || [];
}

function scenarioTimeline(scenario: ScenarioCase): ScenarioTimelineEntry[] {
  if (scenario.evidence?.timeline?.length) return scenario.evidence.timeline;
  // Older servers may only expose the preserved screenshots and assertions.
  // They are evidence, not a reconstructed execution timestamp.
  return [
    ...(scenario.screenshots || []).map((screenshot): ScenarioTimelineEntry => ({ kind: "observation", title: "Captura preservada", screenshot, url: scenario.url })),
    ...(scenario.actual_result ? [{ kind: "action" as const, title: "Resultado registrado", message: scenario.actual_result, url: scenario.url }] : []),
    ...(scenario.evidence?.assertions || []).map((assertion): ScenarioTimelineEntry => ({ kind: "assertion", title: "Verificação observada", message: assertion.message, assertion })),
    ...scenarioSignals(scenario).map((signal): ScenarioTimelineEntry => ({ kind: "signal", title: signal.label || "Sinal técnico", message: signal.message, occurred_at: signal.occurred_at, url: signal.url, signal })),
  ];
}

export function filterScenarioCases(scenarios: ScenarioCase[], query: string, filter: string) {
  const normalized = query.trim().toLocaleLowerCase("pt-BR");
  return scenarios.filter((scenario) => {
    const matchesFilter = filter === "all" || (filter === "signals" ? scenarioSignals(scenario).length > 0 : scenario.status.toUpperCase() === filter);
    const text = [scenario.id, scenario.title, scenario.description, scenario.url, ...scenarioSignals(scenario).map((signal) => `${signal.message || ""} ${signal.label || ""} ${signal.url || ""}`)].join(" ").toLocaleLowerCase("pt-BR");
    return matchesFilter && (!normalized || text.includes(normalized));
  });
}

export function VerificationStrengthSummary({ counts }: { counts?: VerificationCounts }) {
  if (!counts) return null;
  const metrics = [
    ["Negócio verificado", counts.business_verified || 0],
    ["Só interação técnica", counts.interaction_only || 0],
    ["Verificação de negócio falhou", counts.business_failures || 0],
    ["Automação não concluiu a ação", counts.execution_failures || 0],
    ["Verificação técnica falhou", counts.technical_failures || 0],
    ["Indício visual a revisar", counts.visual_findings || 0],
    ["Interação sem resultado esperado", counts.interaction_failures || 0],
  ] as const;
  return <section className="panel verification-strength" aria-label="Força das verificações">
    <span className="section-kicker">O que foi realmente verificado</span>
    <div className="verification-metrics">{metrics.map(([label, count]) => <div key={label}><strong>{count}</strong><span>{label}</span></div>)}</div>
    <p>100% significa processamento concluído, não todos os cenários possíveis. Uma aprovação sem asserção de negócio confirma somente a interação. A cobertura depende do usuário, dos estados alcançados, do tamanho da tela e das restrições.</p>
  </section>;
}

const RESTRICTION_REASONS: Record<string, string> = {
  no_safe_restore_route: "Não foi encontrada uma rota segura para retomar este estado",
  restore_without_progress: "A retomada repetiu o estado sem progresso",
  replay_unavailable: "A sequência conhecida não pôde ser reproduzida",
  replay_without_progress: "A sequência reproduzida não recuperou o estado esperado",
  action_failed: "A ação não pôde ser executada",
  candidate_unavailable: "Controle indisponível neste estado",
  document_navigation_failed: "A navegação direta não carregou um documento válido",
  restore_step_limit: "A retomada atingiu o limite de segurança sem recuperar o estado",
  restore_attempt_limit: "As tentativas de retomar este estado foram esgotadas",
  backtrack_attempt_limit: "As tentativas de voltar a este estado foram esgotadas",
  policy_blocked: "A política de segurança não permitiu executar esta ação",
  action_retry_exhausted: "A ação continuou indisponível após as tentativas permitidas",
  form_validation_precondition_failed: "Não foi possível preparar os demais campos para isolar esta validação",
  required_field_validation_masked: "A tentativa foi executada, mas a resposta de outro campo impediu validar este campo obrigatório",
  form_probe_target_not_empty: "Não foi possível esvaziar o campo após as tentativas de preparação; sua validação não foi presumida",
};

export function CoverageRestrictionsPanel({ restrictions, resolvedRestrictions = [], blockedCount = 0, explorationId, apiBase }: { restrictions: CoverageRestriction[]; resolvedRestrictions?: CoverageRestriction[]; blockedCount?: number; explorationId?: string; apiBase?: string }) {
  return <section className="coverage-restrictions" aria-label="Cobertura e restrições">
    <div className="panel-heading"><span className="section-kicker">Limites da execução</span><h2>O que ficou sem testar</h2><p>Restrições são lacunas de cobertura, não testes aprovados nem bugs confirmados. Cada registro identifica o controle e o motivo observado.</p></div>
    {restrictions.length === 0 ? <p className="scenario-context-note">{blockedCount ? `Esta execução histórica registrou ${blockedCount} controles bloqueados sem detalhes individuais. Não é possível reconstruir seus motivos com segurança; as novas execuções registram cada restrição.` : "Nenhuma restrição individual registrada. Isso não garante todos os cenários possíveis: valores de entrada, perfis de acesso e combinações de estados exigem escopos específicos."}</p> : <div className="restriction-list">{restrictions.map((restriction, index) => <article className="restriction-card" key={restriction.id || index}>
      <strong>{restriction.field_name || restriction.validation_probe?.name ? `${restriction.field_name || restriction.validation_probe?.name} · ${restriction.name || "Validação obrigatória"}` : restriction.name || restriction.target || "Controle sem nome"}</strong>
      <p>{RESTRICTION_REASONS[restriction.reason || ""] || restriction.reason || "Motivo não informado"}</p>
      {restriction.dependency?.kind === "failed_inverse_cart_toggle" && restriction.dependency.source === "actual_business_assertion" && <p>Pré-condição impedida por uma falha já observada: {restriction.dependency.name || "Remoção do item"}{restriction.dependency.item_id ? ` (produto ${restriction.dependency.item_id})` : ""}, etapa {restriction.dependency.action_step ?? "não registrada"}. O item permaneceu no carrinho, impedindo preparar novamente o teste de inclusão.</p>}
      {restriction.dependency?.screenshot && apiBase && explorationId && scenarioScreenshotUrl(apiBase, explorationId, restriction.dependency.screenshot) && <a href={scenarioScreenshotUrl(apiBase, explorationId, restriction.dependency.screenshot)!} target="_blank" rel="noopener noreferrer">Ver captura da pré-condição</a>}
      {!!restriction.validation_probe?.blocking_fields?.length && <p>Campos que impediram a preparação: {restriction.validation_probe.blocking_fields.map(field => field.name || "Campo sem nome").join(", ")}</p>}
      <small>{scenarioActionLabel(restriction.action)} · {restriction.source_url || restriction.target_url || "URL não registrada"}</small>
      {restriction.target_url && restriction.target_url !== restriction.source_url && <small>Estado a retomar: {restriction.target_url}</small>}
    </article>)}</div>}
    {resolvedRestrictions.length > 0 && <details className="panel">
      <summary>Restrições de retomada resolvidas · {resolvedRestrictions.length}</summary>
      <p>Estes controles foram executados posteriormente. A retomada deixou de ser uma lacuna de cobertura; a aprovação ou reprovação de cada CT continua independente.</p>
      <div className="restriction-list">{resolvedRestrictions.map((restriction, index) => {
        const resolution = restriction.resolution || {};
        const screenshot = apiBase && explorationId ? scenarioScreenshotUrl(apiBase, explorationId, resolution.screenshot) : null;
        return <article className="restriction-card" key={restriction.id || index}>
          <strong>{restriction.name || restriction.target || "Controle sem nome"}</strong>
          <p>Executado na etapa {resolution.action_step ?? "não registrada"}. {resolution.assertions_passed === false ? "A ação foi executada, mas houve verificação reprovada; consulte o CT." : "Consulte as verificações e o resultado do CT correspondente."}</p>
          <small>{restriction.target_url || resolution.source_url || "URL não registrada"}</small>
          {screenshot && <a href={screenshot} target="_blank" rel="noopener noreferrer">Ver captura da execução</a>}
        </article>;
      })}</div>
    </details>}
  </section>;
}

function scenarioActionLabel(action?: string) {
  const labels: Record<string, string> = { click: "Clicar", fill: "Preencher", select: "Selecionar opção", navigate: "Navegar", go_back: "Voltar", reload: "Recarregar", press: "Pressionar tecla", hover: "Posicionar o cursor", wait: "Aguardar", screenshot: "Capturar a tela", finish: "Finalizar" };
  return labels[action || ""] || action || "Ação";
}

function scenarioVerdict(scenario: ScenarioCase) {
  const status = scenario.status.toUpperCase();
  const verdict = scenario.evidence?.verdict;
  const tone = verdict?.kind === "interaction_only" ? "info" : ["technical_failure", "visual_failure", "execution_failure"].includes(verdict?.kind || "") ? "warning" : status === "FAIL" ? "error" : status === "PASS" ? "success" : "info";
  const fallback: Record<string, string> = { PASS: "Verificações aprovadas", FAIL: "Verificação falhou", BLOCKED: "Execução bloqueada", SKIPPED: "Não validado" };
  const labels: Record<string, string> = { PASS: "Aprovado", FAIL: "Reprovado", BLOCKED: "Bloqueado", SKIPPED: "Sem validação" };
  return { label: `${labels[status] || "Sem validação"} · ${verdict?.label || fallback[status] || "Evidência preservada"}`, tone };
}

function technicalSignalImpact(signal: ScenarioTechnicalSignal) {
  const labels: Record<string, string> = {
    informational: "Registro informativo; não reprova automaticamente o CT.",
    external_integration: "Ocorrência em uma integração externa; sem defeito funcional confirmado.",
    unconfirmed: "Impacto no recurso testado não confirmado pelas evidências disponíveis.",
    technical_check_failed: "Afeta a verificação técnica da aplicação; o impacto funcional depende das asserções do CT.",
  };
  return labels[signal.impact || ""] || signal.impact || "Este registro precisa de contexto: sozinho não confirma um defeito funcional nem reprova o CT.";
}

function ScenarioTimelineCard({ scenario, explorationId, apiBase }: { scenario: ScenarioCase; explorationId: string; apiBase: string }) {
  const evidenceExplorationId = scenario.evidence_exploration_id || explorationId;
  const verdict = scenarioVerdict(scenario);
  const signals = scenarioSignals(scenario);
  const entries = scenario.evidence?.presentation_steps?.length ? scenario.evidence.presentation_steps : scenarioTimeline(scenario);
  const plannedOrder = entries.some((entry) => entry.presentation_position?.status === "planned");
  const visual = scenario.evidence?.visual_assessment;
  const contextLimited = visual?.context_limited === true || visual?.review_scope === "saved_capture_only_legacy";
  const visualLabel = visual?.status === "pending" ? "Análise visual em andamento"
    : visual?.status !== "assessed" ? "Análise visual indisponível · CT baseado nas verificações locais"
    : visual.accepted_count ? `${visual.accepted_count} indício(s) visual(is) · revisão necessária`
    : "Capturas analisadas · nenhum indício visual aceito, sem garantia de aprovação visual completa";
  const uniqueScreenshots = new Set<string>();
  const timeline = entries.map((entry) => {
    const filename = entry.screenshot || (entry.kind === "action" ? entry.action_preview?.screenshot : undefined);
    const screenshot = scenarioScreenshotUrl(apiBase, evidenceExplorationId, filename);
    const image = screenshot && !uniqueScreenshots.has(screenshot) ? screenshot : null;
    if (screenshot) uniqueScreenshots.add(screenshot);
    return { entry, image, filename };
  });
  return (
    <details className={`scenario-case-card verdict-${verdict.tone}`}>
      <summary className="scenario-case-summary">
        <ChevronRight className="scenario-expand-icon" size={17} />
        <div className="scenario-case-heading"><span>{scenario.id}</span><h3>{scenario.title}</h3><div className="scenario-case-meta"><span>{timeline.length} registros {plannedOrder ? "nos passos do CT" : "na linha do tempo"}</span>{uniqueScreenshots.size > 0 && <span><Eye size={12} />{uniqueScreenshots.size} capturas</span>}{signals.length > 0 && <span className="scenario-signal-count"><Network size={12} />{signals.length} sinais técnicos</span>}{visual?.requested && <span>Visual: {visual.status === "pending" ? "em análise" : visual.status !== "assessed" ? "indisponível" : visual.accepted_count ? "indício a revisar" : "analisado"}</span>}</div></div>
        <span className={`scenario-verdict verdict-${verdict.tone}`}>{verdict.label}</span>
      </summary>
      <div className="scenario-case-body">
        {scenario.evidence?.verdict?.explanation && <p className={`scenario-verdict-explanation verdict-${verdict.tone}`}>{scenario.evidence.verdict.explanation}{scenario.evidence.verdict.historical && <small>O resultado histórico foi preservado; a classificação técnica explica a evidência, sem afirmar um defeito funcional.</small>}</p>}
        {visual?.requested && <div className="scenario-context-note"><strong>{visualLabel}</strong>{visual.reason && visual.status === "unavailable" && <small>Motivo: {visual.reason.startsWith("auxiliary_rate_limit") ? "O provedor limitou as chamadas. As verificações locais foram preservadas." : visual.reason === "auxiliary_request_budget_exhausted" ? "O orçamento de chamadas auxiliares desta execução foi esgotado." : visual.reason === "screenshot_not_available" ? "Não havia captura disponível para enviar ao modelo." : ["visual_assessment_not_returned", "visual_assessment_schema_invalid"].includes(visual.reason) ? "O modelo não devolveu uma análise visual no formato obrigatório. A documentação recebida não é uma validação visual." : `${visualUnavailabilityLabel(visual.reason)}. As verificações locais foram preservadas.`} Código: {visual.reason}</small>}{visual.status === "assessed" && <small>A confiança é declarada pelo modelo; um indício visual não confirma sozinho um bug funcional.</small>}{contextLimited && <small>Contexto textual limitado: esta revisão usa as capturas salvas. O DOM e o texto visível originais não foram preservados; não equivale a uma análise com contexto semântico completo.</small>}</div>}
        {(scenario.expected_result || scenario.actual_result) && <div className="scenario-outcome-grid">{scenario.expected_result && <div><small>Resultado esperado</small><p>{scenario.expected_result}</p></div>}{scenario.actual_result && <div><small>Resultado observado</small><p>{scenario.actual_result}</p></div>}</div>}
        <h4 className="scenario-timeline-title"><History size={15} />{plannedOrder ? "Passos e evidências do CT" : "Linha do tempo do CT"}</h4>
        {plannedOrder && <div className="scenario-context-note"><strong>Ordem dos passos planejada · horários reais preservados</strong><small>Os prints anexados aparecem no ponto escolhido por você. Essa posição organiza o roteiro, não altera quando a imagem foi anexada nem quando as verificações aconteceram.</small></div>}
        {timeline.length === 0 ? <p className="scenario-context-note">Nenhum evento detalhado foi preservado para este caso.</p> : <ol className="scenario-timeline">{timeline.map(({ entry, image, filename }, index) => {
          const signal = entry.signal;
          const assertion = entry.assertion;
          const kind = entry.kind;
          const userImage = entry.image_source === "user_upload";
          const failedAssertion = kind === "assertion" && assertion?.passed === false;
          const technicalAssertion = ["no_new_browser_errors", "no_new_application_errors"].includes(assertion?.kind || "");
          const visualAssertion = assertion?.kind === "visual_model";
          const link = userImage ? null : safeScenarioPageUrl(entry.url || signal?.url);
          const entryTone = signal ? "technical" : failedAssertion ? (technicalAssertion ? "technical" : "error") : kind === "assertion" && assertion?.passed ? "success" : "info";
          const Icon = userImage ? ImagePlus : kind === "signal" ? Network : kind === "observation" ? Eye : kind === "assertion" ? (failedAssertion ? AlertCircle : ShieldCheck) : Play;
          const association = signal?.association?.kind;
          return <li className={`scenario-timeline-event event-${entryTone}`} key={entry.id || `${kind}-${entry.occurred_at || "record"}-${index}`}>
            <span className="scenario-timeline-marker"><Icon size={14} /></span>
            <div className="scenario-timeline-event-body">
              <div className="scenario-timeline-event-head"><strong>{signal?.label || entry.title || ({ observation: "Observação", action: "Ação executada", assertion: "Verificação", signal: "Sinal técnico" })[kind]}</strong>{(entry.attached_at || entry.occurred_at) && <time dateTime={userImage ? entry.attached_at || entry.occurred_at : entry.occurred_at}>{userImage ? "Anexado em " : ""}{formatDate(userImage ? entry.attached_at || entry.occurred_at : entry.occurred_at)}</time>}</div>
              {(entry.message || signal?.message || assertion?.message) && <p>{entry.message || signal?.message || assertion?.message}</p>}
              {userImage && <div className="scenario-context-note"><strong>Imagem enviada por você</strong><small>Anexo fornecido pelo usuário. Não é uma captura automática do navegador nem uma verificação do teste.</small></div>}
              {userImage && entry.presentation_position?.status === "planned" && <small>Posição escolhida: {entry.presentation_position.gap === 0 ? "antes da primeira etapa" : `após a etapa ${entry.presentation_position.gap}`} do roteiro de origem.</small>}
              {userImage && entry.presentation_position?.status === "unresolved" && <div className="scenario-context-note"><small>Não foi possível vincular com segurança a posição planejada deste anexo. Ele aparece separado dos passos, sem posição atribuída e com seu horário real.</small></div>}
              {kind === "action" && (entry.step_number !== undefined || entry.target) && <div className="scenario-case-meta">{entry.step_number !== undefined && <span>Etapa {entry.step_number}</span>}{entry.target && <span>Alvo observado: {entry.target}</span>}</div>}
              {assertion && <div className={`scenario-assertion-result ${assertion.passed === false ? "not-passed" : ""}`}><span>{assertion.passed === true ? "Aprovada" : assertion.passed === false ? "Falhou" : "Sem resultado"}{technicalAssertion ? " · verificação técnica" : visualAssertion ? " · indício visual da LLM" : ""}</span>{visualAssertion && <small>Reprovação baseada na análise da imagem; confira a captura e a região antes de confirmar o defeito. O diagnóstico original do modelo é preservado no idioma em que foi recebido.</small>}{assertion.expected !== undefined && <small>Esperado: {assertion.expected_display ?? JSON.stringify(assertion.expected)}</small>}{assertion.actual !== undefined && <small>Observado: {JSON.stringify(assertion.actual)}</small>}</div>}
              {signal && <div className="scenario-signal-detail">{signal.status_code && <span className="scenario-http-code">HTTP {signal.status_code}</span>}<p>{technicalSignalImpact(signal)}</p>{signal.classification_reason && <small>{signal.classification_reason}</small>}<small>{association === "before_action" ? "Observado antes da ação; sem atribuição causal." : association === "after_action" ? "Observado após a ação; associação temporal, não causalidade comprovada." : association === "during_action" ? "Registrado durante a ação; associação temporal, não causalidade comprovada." : "Registrado durante a execução; relação causal não comprovada."}</small>{signal.affects_verdict === false && <small>Não reprova automaticamente o CT.</small>}</div>}
              {!userImage && (entry.url || signal?.url) && <div className="scenario-event-url">{link ? <a href={link} target="_blank" rel="noreferrer">{entry.url || signal?.url}<SquareArrowOutUpRight size={12} /></a> : <span>{entry.url || signal?.url}</span>}</div>}
              {image && <figure className="scenario-timeline-screenshot"><a href={image} target="_blank" rel="noreferrer"><img src={image} loading="lazy" alt={`${scenario.id} · ${entry.title || "evidência"}`} /></a><figcaption><span>{filename?.replace(/\\/g, "/").split("/").pop()}</span><a href={image} target="_blank" rel="noreferrer">Ampliar <SquareArrowOutUpRight size={12} /></a></figcaption></figure>}
            </div>
          </li>;
        })}</ol>}
      </div>
    </details>
  );
}

export function ExecutionObservations({ contexts, explorationId, apiBase }: { contexts: ScenarioCase[]; explorationId: string; apiBase: string }) {
  const entries = contexts.flatMap((scenario) => scenarioTimeline(scenario).map((entry) => ({
    entry, evidenceExplorationId: scenario.evidence_exploration_id || explorationId,
  })));
  if (entries.length === 0) return null;
  return <details className="execution-observations">
    <summary><History size={17} /><strong>Observações da execução</strong><span>{entries.length} registros</span><ChevronRight className="scenario-expand-icon" size={17} /></summary>
    <p>Ocorrências de preparação, carregamento ou retomada sem vínculo comprovado com um CT. São observações do que aconteceu, sem alterar o resultado ou a contagem dos testes.</p>
    <ol className="execution-observation-list">{entries.map(({ entry, evidenceExplorationId }, index) => {
      const observation = entry.observation;
      const signal = entry.signal;
      const pageUrl = observation?.page_url || entry.page_url;
      const pageLink = safeScenarioPageUrl(pageUrl || undefined);
      const networkSignal = ["http", "network"].includes(signal?.type || signal?.kind || "");
      const requestUrl = observation?.request_url !== undefined ? observation.request_url : networkSignal ? signal?.url || entry.url : null;
      const requestLink = safeScenarioPageUrl(requestUrl || undefined);
      const method = observation?.method || (networkSignal ? signal?.method : null);
      const statusCode = observation?.status_code ?? signal?.status_code;
      const occurredAt = entry.occurred_at || signal?.occurred_at;
      const screenshot = scenarioScreenshotUrl(apiBase, evidenceExplorationId, entry.screenshot || (entry.kind === "action" ? entry.action_preview?.screenshot : undefined));
      const originalMessage = entry.message || signal?.message;
      return <li key={entry.id || `${entry.kind}-${occurredAt || "record"}-${index}`}>
        <div className="execution-observation-heading"><strong>{observation?.summary || entry.message || signal?.message || entry.title || "Observação registrada"}</strong>{occurredAt ? <time dateTime={occurredAt}>{formatDate(occurredAt)}</time> : <small>Momento não registrado</small>}</div>
        {observation?.summary && originalMessage && originalMessage !== observation.summary && <p className="execution-observation-message">Mensagem registrada: {originalMessage}</p>}
        <dl className="execution-observation-facts">
          <div><dt>Página</dt><dd>{pageLink ? <a href={pageLink} target="_blank" rel="noreferrer">{pageUrl}<SquareArrowOutUpRight size={12} /></a> : pageUrl || "Página não registrada"}</dd></div>
          {observation?.phase && <div><dt>Etapa</dt><dd>{observation.phase}</dd></div>}
          {(requestUrl || method || statusCode != null) && <div><dt>{networkSignal || requestUrl ? "Requisição" : "Registro técnico"}</dt><dd>{method && <span>{method} · </span>}{statusCode != null && <span>HTTP {statusCode}</span>}{requestUrl && <div>{requestLink ? <a href={requestLink} target="_blank" rel="noreferrer">{requestUrl}<SquareArrowOutUpRight size={12} /></a> : requestUrl}</div>}</dd></div>}
        </dl>
        {signal && <p className="execution-observation-impact">{technicalSignalImpact(signal)}{signal.classification_reason && <small>{signal.classification_reason}</small>}</p>}
        {screenshot && <a className="execution-observation-capture" href={screenshot} target="_blank" rel="noreferrer">Ver captura preservada <SquareArrowOutUpRight size={12} /></a>}
      </li>;
    })}</ol>
  </details>;
}

export function ScenarioTimelinePanel({ scenarios, contexts, explorationId, apiBase, isRunning = false }: { scenarios: ScenarioCase[]; contexts: ScenarioCase[]; explorationId: string; apiBase: string; isRunning?: boolean }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const cases = scenarios.filter((scenario) => !["INFO", "CONTEXT"].includes(scenario.status.toUpperCase()));
  const legacyObservations = scenarios.filter((scenario) => ["INFO", "CONTEXT"].includes(scenario.status.toUpperCase()));
  const observations = [...contexts, ...legacyObservations.filter((scenario) => !contexts.some((entry) => entry.id === scenario.id))];
  const filtered = filterScenarioCases(cases, query, filter);
  return <section className="scenario-cases-panel">
    <div className="scenario-cases-heading"><div><span className="section-kicker">Evidência por caso</span><h2>Casos de teste e suas linhas do tempo</h2><p>Ações, verificações, capturas e sinais técnicos juntos, na ordem em que foram observados.</p></div>{isRunning && <span className="live-mini"><span />ao vivo</span>}</div>
    <div className="scenario-evidence-note"><ShieldCheck size={17} /><p>A aprovação vale para as verificações executadas, não para todas as regras de negócio. Sinais técnicos e indícios visuais não confirmam sozinhos um bug; consulte a origem da reprovação na linha do tempo. Nomes dos controles, dados observados e mensagens técnicas preservam o idioma original da aplicação.</p></div>
    <div className="scenario-case-tools"><label className="scenario-case-search"><Search size={16} /><input aria-label="Buscar casos de teste ou sinais técnicos" placeholder="Buscar CT, título, URL ou sinal técnico…" value={query} onChange={(event) => setQuery(event.target.value)} /></label><label className="scenario-case-filter"><span>Exibir</span><select aria-label="Filtrar casos de teste" value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">Todos os CTs</option><option value="PASS">Verificações aprovadas</option><option value="FAIL">Verificações falhas</option><option value="BLOCKED">Bloqueados</option><option value="SKIPPED">Não validados</option><option value="signals">Com sinais técnicos</option></select></label></div>
    <div className="scenario-case-results"><span>{filtered.length} de {cases.length} CTs</span><small>Abra um caso para acompanhar as evidências.</small></div>
    <div className="scenario-case-list">{filtered.map((scenario) => <ScenarioTimelineCard key={scenario.id} scenario={scenario} explorationId={explorationId} apiBase={apiBase} />)}</div>
    {filtered.length === 0 && <div className="activity-empty"><FileCheck2 size={22} /><span>{cases.length === 0 ? "Os CTs aparecerão aqui conforme as evidências forem preservadas." : "Nenhum CT corresponde à busca e ao filtro."}</span></div>}
    <ExecutionObservations contexts={observations} explorationId={explorationId} apiBase={apiBase} />
  </section>;
}

function DetailCounter({ icon: Icon, value, label }: { icon: typeof Activity; value: string | number; label: string }) {
  return <div className="detail-counter"><Icon size={16} /><strong>{value}</strong><span>{label}</span></div>;
}

function SettingsView({ apiBase, apiState, pollInterval, setPollInterval }: { apiBase: string; apiState: string; pollInterval: number; setPollInterval: (value: number) => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(`NEXT_PUBLIC_API_URL=${apiBase}`);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };
  return (
    <div className="view-stack settings-view">
      <PageHeader eyebrow="Preferências" title="Configurações" description="Ajuste a comunicação da interface sem acoplar o frontend ao executor." />
      <div className="settings-grid">
        <section className="panel settings-card">
          <div className="settings-card-head"><span className="settings-icon"><TerminalSquare size={20} /></span><div><h2>Servidor FastAPI</h2><p>Endpoint utilizado por esta interface.</p></div><span className={`connection-state state-${apiState}`}><span />{apiState === "online" ? "Conectado" : apiState === "checking" ? "Verificando" : "Indisponível"}</span></div>
          <div className="field-group"><span>URL da API</span><div className="copy-field"><code>{apiBase}</code><button onClick={() => void copy()} aria-label="Copiar variável de ambiente">{copied ? <Check size={16} /> : <Copy size={16} />}{copied ? "Copiado" : "Copiar"}</button></div><small>Configure com <code>NEXT_PUBLIC_API_URL</code> antes do build.</small></div>
        </section>

        <section className="panel settings-card">
          <div className="settings-card-head"><span className="settings-icon"><RefreshCcw size={20} /></span><div><h2>Atualização ao vivo</h2><p>Frequência de consulta durante uma exploração.</p></div></div>
          <div className="radio-cards" role="radiogroup" aria-label="Intervalo de atualização">
            {[{ value: 2000, label: "2 segundos", detail: "Mais imediato" }, { value: 3000, label: "3 segundos", detail: "Recomendado" }, { value: 5000, label: "5 segundos", detail: "Menos requisições" }].map((option) => <label key={option.value} className={pollInterval === option.value ? "selected" : ""}><input type="radio" name="poll" value={option.value} checked={pollInterval === option.value} onChange={() => setPollInterval(option.value)} /><span><strong>{option.label}</strong><small>{option.detail}</small></span><i><Check size={13} /></i></label>)}
          </div>
        </section>

        <section className="panel settings-card wide-card">
          <div className="settings-card-head"><span className="settings-icon"><ShieldCheck size={20} /></span><div><h2>Separação de responsabilidades</h2><p>A arquitetura permanece portátil entre provedores e modelos.</p></div></div>
          <ArchitectureFlow compact />
          <div className="security-grid"><div><KeyRound size={17} /><span><strong>Segredos no servidor</strong><small>As chaves nunca precisam chegar ao browser.</small></span></div><div><Code2 size={17} /><span><strong>Execução determinística</strong><small>Somente o Playwright executa ações reais.</small></span></div><div><Boxes size={17} /><span><strong>Modelos substituíveis</strong><small>JEV e auxiliar são configurados separadamente.</small></span></div></div>
        </section>
      </div>
    </div>
  );
}
