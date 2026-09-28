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

const DEFAULT_API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

type View =
  | "dashboard"
  | "new"
  | "explorations"
  | "providers"
  | "settings"
  | "detail";

type Model = {
  id?: string;
  model_id?: string;
  name?: string;
  label?: string | null;
  provider_id?: string;
  provider_name?: string;
  enabled?: boolean;
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

type Exploration = {
  id: string;
  app_url: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  started_at?: string;
  finished_at?: string;
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
  };
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
  error?: string | null;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    cost?: number | null;
    cost_known?: boolean;
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

const FREE_MODEL_PREFERENCE = [
  "nvidia/nemotron-3-ultra-550b-a55b:free",
  "nvidia/nemotron-3-super-120b-a12b:free",
  "qwen/qwen3.8-27b:free",
  "z-ai/glm-5.2:free",
  "google/gemma-4-31b-it:free",
];

const JEV_MODEL_PREFERENCE = ["typesafe/jev-1.13", "typesafe/jev-latest"];

function defaultModel(models: Model[], role: "agent" | "auxiliary") {
  if (role === "agent") {
    const jevModel = JEV_MODEL_PREFERENCE.find((preferred) =>
      models.some((model) => modelValue(model) === preferred),
    );
    if (jevModel) return jevModel;
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

function durationBetween(start?: string, end?: string) {
  if (!start) return "—";
  const first = new Date(start).getTime();
  const last = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(first) || Number.isNaN(last)) return "—";
  const totalSeconds = Math.max(0, Math.floor((last - first) / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${seconds}s`;
}

function formatTokens(value?: number) {
  return new Intl.NumberFormat("pt-BR").format(value || 0);
}

function formatCost(usage?: Exploration["usage"]) {
  if (!usage?.cost_known) return "—";
  return `$${(usage.cost || 0).toFixed(6)}`;
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return "Não foi possível concluir a solicitação.";
}

function parseRoute(): { view: View; explorationId?: string } {
  if (typeof window === "undefined") return { view: "dashboard" };
  const hash = window.location.hash || "#/dashboard";
  const detail = hash.match(/^#\/exploracoes\/([^/]+)$/);
  if (detail) return { view: "detail", explorationId: decodeURIComponent(detail[1]) };
  const route = NAV_ITEMS.find((item) => item.hash === hash);
  return { view: route?.view || "dashboard" };
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
  const fallback: Record<string, { title: string; detail?: string }> = {
    started: { title: "Exploração iniciada", detail: "O navegador foi preparado para o JEV." },
    browser_launched: { title: "Aplicação aberta no navegador" },
    coverage_scope: { title: "Escopo de cobertura definido" },
    action_started: { title: `Executando ação ${String(payload.step || "")}`.trim(), detail: String(payload.action || event.message || "") },
    action_completed: { title: `Ação ${String(payload.step || "")} concluída`.trim(), detail: event.message },
    waiting_input: { title: "Aguardando uma ação sua", detail: event.message },
    limit_reached: { title: "Limite de ações atingido", detail: "A execução foi preservada com cobertura parcial." },
    coverage_complete: { title: "Cobertura concluída", detail: "O JEV esgotou as opções alcançáveis." },
    coverage_complete_with_restrictions: { title: "Cobertura alcançável concluída", detail: "Os controles que não puderam ser executados com segurança foram documentados no resultado." },
    coverage_stalled: { title: "Exploração bloqueada", detail: event.message },
    scenario_preparation_started: { title: "Preparando os casos de teste", detail: event.message },
    scenario_drafts_ready: { title: "Todos os CTs já estão seguros", detail: event.message },
    scenario_enrichment_started: { title: "LLM auxiliar aprimorando os CTs", detail: event.message },
    scenario_enriched: { title: `${scenario} aprimorado`, detail: "Documentado pela LLM auxiliar a partir da evidência capturada." },
    scenario_enrichment_skipped: { title: `${scenario} preservado`, detail: "A evidência local foi mantida porque a LLM auxiliar demorou a responder." },
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
  const [route, setRoute] = useState<{ view: View; explorationId?: string }>({ view: "dashboard" });
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [explorations, setExplorations] = useState<Exploration[]>([]);
  const [selectedExploration, setSelectedExploration] = useState<Exploration | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [apiState, setApiState] = useState<"checking" | "online" | "offline">("checking");
  const [connectionError, setConnectionError] = useState("");
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [providerModal, setProviderModal] = useState(false);
  const [reportData, setReportData] = useState<unknown | null>(null);
  const [pollInterval, setPollInterval] = useState(3000);

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
        throw new Error(message);
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
        provider.has_api_key
        && !provider.models?.some((model) => modelValue(model) === "typesafe/jev-1.13")
        && /openrouter\.ai/i.test(provider.base_url),
      );
      if (openRouterProviders.length) {
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
      setSelectedExploration(data);
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
      setConnectionError(errorMessage(error));
      if (!quiet) toast(errorMessage(error), "error");
      return null;
    } finally {
      setDetailLoading(false);
    }
  }, [request, toast]);

  useEffect(() => {
    const saved = window.localStorage.getItem("autojev-poll-interval");
    if (saved && Number(saved) >= 1500) setPollInterval(Number(saved));
    const onHash = () => {
      setRoute(parseRoute());
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
    if (route.view !== "detail" || !route.explorationId || (selectedExploration && !isLive(selectedExploration.status))) return;
    const timer = window.setInterval(async () => {
      const data = await loadExploration(route.explorationId as string, true);
      if (data && !isLive(data.status)) void loadCore(true);
    }, pollInterval);
    return () => window.clearInterval(timer);
  }, [route, pollInterval, loadExploration, loadCore, selectedExploration]);

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
      }),
      { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, cost: 0, cost_known: false },
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
            exploration={selectedExploration}
            loading={detailLoading}
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
                <span><CircleDollarSign size={13} /><strong>{activeUsage.cost_known ? `$${activeUsage.cost.toFixed(6)}` : "—"}</strong><small>custo informado</small></span>
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
    </div>
  );
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
  onCreated,
}: {
  providers: Provider[];
  models: Model[];
  loading: boolean;
  request: ApiRequest;
  toast: (message: string, tone?: Toast["tone"]) => void;
  navigate: (hash: string) => void;
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
  const [captureConsoleErrors, setCaptureConsoleErrors] = useState(true);
  const [captureNetworkErrors, setCaptureNetworkErrors] = useState(true);
  const [credentialsOpen, setCredentialsOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const modelsFor = useCallback((providerId: string) => {
    const provider = providers.find((item) => item.id === providerId);
    const nested = provider?.models || [];
    const flat = models.filter((model) => model.provider_id === providerId);
    const source = nested.length > 0 ? nested : flat;
    return source.filter((model) => model.enabled !== false);
  }, [providers, models]);

  useEffect(() => {
    if (!agentProvider && enabledProviders[0]) setAgentProvider(enabledProviders[0].id);
    if (!auxProvider && enabledProviders[0]) setAuxProvider(enabledProviders[0].id);
  }, [enabledProviders, agentProvider, auxProvider]);

  useEffect(() => {
    const available = modelsFor(agentProvider);
    if (!available.some((model) => modelValue(model) === agentModel)) setAgentModel(defaultModel(available, "agent"));
  }, [agentProvider, agentModel, modelsFor]);

  useEffect(() => {
    const available = modelsFor(auxProvider);
    if (!available.some((model) => modelValue(model) === auxModel)) setAuxModel(defaultModel(available, "auxiliary"));
  }, [auxProvider, auxModel, modelsFor]);

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
      toast("Preencha a URL e selecione os dois modelos.", "error");
      return;
    }
    if (focusMode === "focused" && !focusArea.trim()) {
      toast("Informe a tela, menu ou submenu que deve ser explorado.", "error");
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
      if (credentialsOpen && (username || password)) body.credentials = { username, password };
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
                  description="Interpreta screenshot e DOM; não controla o navegador."
                  icon={<Sparkles size={20} />}
                  accent="teal"
                  providers={enabledProviders}
                  providerValue={auxProvider}
                  modelValueSelected={auxModel}
                  onProvider={setAuxProvider}
                  onModel={setAuxModel}
                  modelsFor={modelsFor}
                />
              </div>
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
                <span className="form-section-heading"><span><h2>Credenciais da aplicação <em>opcional</em></h2><p>Use somente quando a jornada exigir autenticação.</p></span><KeyRound size={21} /></span>
                <ChevronDown size={18} className={credentialsOpen ? "rotated" : ""} />
              </button>
              {credentialsOpen && (
                <div className="field-grid two-columns credential-fields">
                  <label className="field-group"><span>Usuário ou e-mail</span><input type="text" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="qa@empresa.com" /></label>
                  <label className="field-group"><span>Senha</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="••••••••••••" /></label>
                </div>
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
            <SummaryRow label="Escopo" value={focusMode === "focused" ? focusArea.trim() || "Defina uma área" : "Aplicação inteira"} />
            <SummaryRow label="Conclusão" value="Ao esgotar a cobertura" />
            <SummaryRow label="Limite" value="Sem teto fixo" />
            <SummaryRow label="Viewport" value={viewport.replace("x", " × ")} />
          </div>
          <div className="launch-route"><BrainCircuit size={17} /><span /><Code2 size={17} /><span /><Globe2 size={17} /><span /><Sparkles size={17} /></div>
          <button type="submit" className="button primary launch-button" disabled={submitting || noModels}>{submitting ? <><Spinner />Iniciando exploração…</> : <><Play size={17} fill="currentColor" />Iniciar exploração</>}</button>
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
}) {
  return (
    <div className={`model-role-card accent-${accent}`}>
      <div className="model-role-head"><span className="model-role-icon">{icon}</span><div><strong>{roleTitle}</strong><small>{description}</small></div></div>
      <label className="field-group"><span>Provedor</span><select value={providerValue} onChange={(event) => onProvider(event.target.value)} disabled={!providers.length}><option value="">Selecione o provedor</option>{providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.name}</option>)}</select></label>
      <div className="field-group"><span>Modelo</span><ModelCombobox models={modelsFor(providerValue)} value={modelValueSelected} onChange={onModel} disabled={!providerValue} /></div>
    </div>
  );
}

function ModelCombobox({
  models,
  value,
  onChange,
  disabled,
}: {
  models: Model[];
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
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
        placeholder={models.length ? "Pesquisar modelo..." : "Nenhum modelo disponível"}
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
              <thead><tr><th>Aplicação</th><th>Status</th><th>Progresso</th><th>Cenários</th><th>Tokens</th><th>Custo</th><th>Iniciada</th><th><span className="sr-only">Ações</span></th></tr></thead>
              <tbody>{filtered.map((item) => {
                const progress = item.overall_progress ?? item.progress ?? (["completed", "success"].includes(item.status?.toLowerCase()) ? 100 : undefined);
                const determinate = typeof progress === "number";
                const progressValue = typeof progress === "number" ? progress : 0;
                return (
                  <tr key={item.id} onClick={() => navigate(`#/exploracoes/${encodeURIComponent(item.id)}`)}>
                    <td><div className="table-app"><span className="app-avatar"><Globe2 size={17} /></span><span><strong>{hostFromUrl(item.app_url)}</strong><small>{explorationFocusArea(item.options) ? `Escopo: ${explorationFocusArea(item.options)}` : "Aplicação inteira"}</small></span></div></td>
                    <td><StatusBadge status={item.status} completionType={item.completion?.type} /></td>
                    <td><div className={`table-progress ${determinate ? "" : "indeterminate"}`}><span><i style={determinate ? { width: `${Math.min(100, Math.max(0, progressValue))}%` } : undefined} /></span><small>{determinate ? `${Math.round(progressValue)}%` : "cobrindo"}</small></div></td>
                    <td><div className="scenario-cell"><strong>{item.counters?.scenarios || item.counters?.test_scenarios || item.scenarios_count || 0}</strong>{item.summary && <small>{item.summary.pass || 0} PASS · {item.summary.fail || 0} FAIL</small>}</div></td>
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

function ExplorationDetailView({
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
}) {
  const [cancelling, setCancelling] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [openingReport, setOpeningReport] = useState(false);
  const [detailTab, setDetailTab] = useState<"overview" | "activity" | "evidence" | "config">("overview");
  if (loading && !exploration) return <div className="detail-loading"><Spinner size={24} /><span>Carregando exploração…</span></div>;
  if (!exploration) {
    return <section className="panel"><EmptyState icon={AlertCircle} title="Exploração não encontrada" description="O registro pode ter sido removido ou ainda não está disponível." action={<button className="button secondary" onClick={() => navigate("#/exploracoes")}><ArrowLeft size={16} />Voltar ao histórico</button>} /></section>;
  }

  const status = explorationStatus(exploration.status);
  const phase = exploration.phase || (isLive(exploration.status) ? "exploring" : "complete");
  const configuredStepLimit = typeof exploration.options?.max_steps === "number" ? exploration.options.max_steps : null;
  const phaseProgress = exploration.phase_progress || {
    current: exploration.counters?.actions || exploration.actions_count || 0,
    total: configuredStepLimit,
    percentage: exploration.progress,
  };
  const overallProgress = exploration.overall_progress ?? exploration.progress ?? (["completed", "success", "partial"].includes(status.value) ? 100 : undefined);
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
  const completion = exploration.completion || {};
  const actionLimitReached = completion.type === "limit_reached" || completion.reason === "action_limit";
  const completedWithRestrictions = completion.type === "coverage_complete_with_restrictions";
  const phaseSteps = [
    { key: "exploring", label: "Explorar", detail: `${exploration.browser_progress?.current ?? counters.actions} ações` },
    { key: "documenting", label: "Criar CTs", detail: `${postprocessing.total || counters.scenarios} casos` },
    { key: "reporting", label: "Relatório", detail: exploration.report_available ? "pronto" : "pendente" },
  ];
  const phaseOrder: Record<string, number> = { preparing: 0, exploring: 0, waiting_input: 0, documenting: 1, reporting: 2, complete: 3 };
  const currentPhaseIndex = phaseOrder[phase] ?? 0;
  const phaseHeadline = actionLimitReached
    ? `Limite de ${exploration.browser_progress?.total || configuredStepLimit || counters.actions} ações atingido`
    : completedWithRestrictions
      ? "Exploração concluída com restrições"
      : exploration.phase_label || status.label;
  const phaseDescription = actionLimitReached
    ? "A automação não falhou na ação 67: ela concluiu o limite configurado e preservou tudo o que encontrou. A cobertura ficou parcial porque ainda havia opções a explorar."
    : completedWithRestrictions
      ? "A cobertura alcançável terminou. Os controles que não puderam ser executados com segurança foram preservados como restrições, sem serem contados como testes aprovados."
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
    setResuming(true);
    try {
      await request(`/explorations/${encodeURIComponent(exploration.id)}/resume`, { method: "POST" });
      toast("Exploração retomada.", "success");
      onRefresh();
    } catch (error) {
      toast(errorMessage(error), "error");
    } finally {
      setResuming(false);
    }
  };

  const openReport = async () => {
    setOpeningReport(true);
    try {
      const response = await fetch(`${apiBase}/explorations/${encodeURIComponent(exploration.id)}/report`);
      if (!response.ok) throw new Error(`Não foi possível abrir o relatório (${response.status}).`);
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        const data = await response.json() as Record<string, unknown>;
        const url = data.url || data.report_url || data.download_url;
        if (typeof url === "string") window.open(url, "_blank", "noopener,noreferrer");
        else onReport(data);
      } else if (contentType.includes("text/html")) {
        const html = await response.text();
        const blobUrl = URL.createObjectURL(new Blob([html], { type: "text/html" }));
        window.open(blobUrl, "_blank", "noopener,noreferrer");
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

  return (
    <div className="view-stack detail-view">
      <div className="detail-breadcrumb"><button onClick={() => navigate("#/exploracoes")}><ArrowLeft size={15} />Explorações</button><ChevronRight size={14} /><span>{hostFromUrl(exploration.app_url)}</span></div>
      <header className="detail-header">
        <div className="detail-title"><span className="app-avatar large"><Globe2 size={22} /></span><div><div className="detail-title-line"><h1>{hostFromUrl(exploration.app_url)}</h1>{actionLimitReached ? <span className="status-badge status-warning"><span className="status-dot" />Limite anterior atingido</span> : completedWithRestrictions ? <span className="status-badge status-warning"><span className="status-dot" />Concluída com restrições</span> : <StatusBadge status={exploration.status} />}</div><a href={exploration.app_url} target="_blank" rel="noreferrer">{exploration.app_url}<SquareArrowOutUpRight size={13} /></a></div></div>
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

      {exploration.error && <div className="callout error-callout"><AlertCircle size={19} /><div><strong>A execução encontrou um erro</strong><span>{exploration.error}</span></div></div>}

      <section className={`execution-command panel phase-${phase}`} aria-live="polite">
        <div className="execution-command-head">
          <span className={`phase-symbol ${isLive(exploration.status) ? "live" : ""}`}>
            {isLive(exploration.status) ? <Spinner size={22} /> : status.value === "failed" ? <AlertCircle size={22} /> : <CheckCircle2 size={22} />}
          </span>
          <div className="execution-command-copy">
            <span className="section-kicker">{isLive(exploration.status) ? <><span className="live-pulse" />Agora no AutoJev</> : "Resultado da execução"}</span>
            <h2>{phaseHeadline}</h2>
            <p>{phaseDescription}</p>
            {phase === "documenting" && (
              <div className="ct-live-copy">
                <strong>CT {Math.min(phaseProgress.current || 0, phaseProgress.total || 0)} de {phaseProgress.total || counters.scenarios}</strong>
                <span>{counters.scenarios} casos já preservados · {postprocessing.enriched || 0} aprimorados pela LLM</span>
              </div>
            )}
          </div>
          <div className="overall-progress">{determinateProgress ? <strong>{Math.round(overallProgressValue)}<small>%</small></strong> : <strong className="progress-running">Em andamento</strong>}<span>{determinateProgress ? "processo total" : "até esgotar a cobertura"}</span></div>
        </div>
        <div className={`large-progress ${determinateProgress ? "" : "indeterminate"}`}><span style={determinateProgress ? { width: `${Math.min(100, Math.max(0, overallProgressValue))}%` } : undefined} /></div>
        <div className="phase-track" aria-label="Fases da execução">
          {phaseSteps.map((step, index) => (
            <div key={step.key} className={`phase-step ${index < currentPhaseIndex ? "done" : ""} ${index === currentPhaseIndex && phase !== "complete" ? "active" : ""}`}>
              <span>{index < currentPhaseIndex || phase === "complete" ? <Check size={14} /> : index + 1}</span>
              <div><strong>{step.label}</strong><small>{step.detail}</small></div>
            </div>
          ))}
        </div>
        <div className="live-counters"><DetailCounter icon={Globe2} value={counters.pages} label="páginas" /><DetailCounter icon={Zap} value={counters.actions} label="ações" /><DetailCounter icon={AlertCircle} value={counters.issues} label="sinais técnicos" /><DetailCounter icon={FileCheck2} value={counters.scenarios} label="CTs" /><DetailCounter icon={Hash} value={formatTokens(exploration.usage?.total_tokens)} label="tokens" /><DetailCounter icon={CircleDollarSign} value={formatCost(exploration.usage)} label="custo" /><DetailCounter icon={Clock3} value={durationBetween(exploration.started_at || exploration.created_at, isLive(exploration.status) ? undefined : exploration.updated_at || exploration.finished_at)} label="duração total" /></div>
      </section>

      {waitingForInput && (
        <section className="panel intervention-card intervention-wide">
          <span className="section-kicker"><span className="live-pulse" />Ação necessária</span>
          <div><h2>Faça login no navegador</h2><p>{waitingEvent?.message || "O navegador está aguardando sua intervenção para continuar."}</p></div>
          <button className="button primary" onClick={() => void resume()} disabled={resuming}>{resuming ? <Spinner /> : <Play size={16} />}Continuar exploração</button>
        </section>
      )}

      <section className="detail-workspace panel">
        <div className="detail-tabs" role="tablist" aria-label="Detalhes da exploração">
          <button role="tab" aria-selected={detailTab === "overview"} className={detailTab === "overview" ? "active" : ""} onClick={() => setDetailTab("overview")}><Gauge size={16} />Visão geral</button>
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
                    <div><strong>Concluída com restrições</strong><span>{completion.blocked_candidates ?? "Alguns"} controle(s) não puderam ser executados com segurança. Isso não significa a mesma quantidade de telas ou defeitos.</span></div>
                  </div>
                )}
                {counters.issues > 0 && (
                  <div className="coverage-summary">
                    <ShieldCheck size={18} />
                    <div><strong>Sinais técnicos, não defeitos automaticamente</strong><span>Console e HTTP podem registrar a mesma ocorrência mais de uma vez. O relatório final correlaciona esses sinais por observação.</span></div>
                  </div>
                )}
                <div className="result-facts">
                  <div><small>Estado da cobertura</small><strong>{actionLimitReached ? "Execução antiga limitada" : completedWithRestrictions ? "Concluída com restrições" : status.label}</strong></div>
                  <div><small>Estados descobertos</small><strong>{completion.known_states ?? counters.pages}</strong></div>
                  <div><small>CTs aprimorados</small><strong>{postprocessing.enriched ?? 0}</strong></div>
                  <div><small>CTs com evidência local</small><strong>{postprocessing.local_evidence || Math.max(0, counters.scenarios - (postprocessing.enriched || 0))}</strong></div>
                </div>
              </section>

              <section className="browser-preview-card">
                <div className="panel-heading row-heading"><div><span className="section-kicker"><Eye size={14} />Última evidência</span><h2>Onde a navegação terminou</h2></div><button className="text-button" onClick={() => setDetailTab("evidence")}>Ver maior <ArrowRight size={14} /></button></div>
                <div className="browser-frame compact-browser">
                  <div className="browser-chrome"><span className="window-dots"><i /><i /><i /></span><div className="browser-address"><ShieldCheck size={12} />{exploration.app_url}</div><MoreHorizontal size={16} /></div>
                  <div className="browser-canvas">{screenshot ? <img key={`${screenshot}-${exploration.updated_at || "latest"}`} src={`${screenshot}${screenshot.includes("?") ? "&" : "?"}v=${encodeURIComponent(exploration.updated_at || "latest")}`} alt={`Último screenshot de ${hostFromUrl(exploration.app_url)}`} /> : <div className="no-screenshot"><MonitorPlay size={32} /><strong>Aguardando captura do browser</strong></div>}</div>
                </div>
              </section>

              <section className="recent-activity-card">
                <div className="panel-heading row-heading"><div><span className="section-kicker">Últimas mudanças</span><h2>O que acabou de acontecer</h2></div><button className="text-button" onClick={() => setDetailTab("activity")}>Ver tudo <ArrowRight size={14} /></button></div>
                {events.length === 0 ? <div className="activity-empty"><Activity size={20} /><span>A atividade aparecerá conforme a exploração avança.</span></div> : <div className="activity-list compact-list">{[...events].reverse().slice(0, 5).map((event, index) => { const copy = activityCopy(event); return <div className="activity-item" key={event.id || `${event.timestamp}-${index}`}><span className={`activity-marker ${event.level || event.status || event.type || "info"}`}><Activity size={12} /></span><div><strong>{copy.title}</strong>{copy.detail && <p>{copy.detail}</p>}<small>{formatDate(event.timestamp || event.created_at)}</small></div></div>; })}</div>}
              </section>
            </div>
          )}

          {detailTab === "activity" && (
            <section className="activity-card activity-card-wide">
              <div className="panel-heading row-heading"><div><span className="section-kicker">Linha do tempo explicada</span><h2>Atividade da exploração</h2><p>Navegação, criação dos CTs e relatório aparecem como etapas separadas.</p></div>{isLive(exploration.status) && <span className="live-mini"><span />ao vivo</span>}</div>
              {events.length === 0 ? <div className="activity-empty"><Activity size={20} /><span>A atividade detalhada aparecerá conforme a exploração avança.</span></div> : <div className="activity-list">{[...events].reverse().slice(0, 120).map((event, index) => { const copy = activityCopy(event); return <div className="activity-item" key={event.id || `${event.timestamp}-${index}`}><span className={`activity-marker ${event.level || event.status || event.type || "info"}`}><Activity size={12} /></span><div><strong>{copy.title}</strong>{copy.detail && <p>{copy.detail}</p>}<small>{formatDate(event.timestamp || event.created_at)}</small></div></div>; })}</div>}
            </section>
          )}

          {detailTab === "evidence" && (
            <section className="browser-panel evidence-panel">
              <div className="panel-heading row-heading"><div><span className="section-kicker"><Eye size={14} />Última observação</span><h2>Evidência do navegador</h2></div>{screenshot && <a className="text-button" href={screenshot} target="_blank" rel="noreferrer">Abrir imagem <SquareArrowOutUpRight size={14} /></a>}</div>
              <div className="browser-frame"><div className="browser-chrome"><span className="window-dots"><i /><i /><i /></span><div className="browser-address"><ShieldCheck size={12} />{exploration.app_url}</div><MoreHorizontal size={16} /></div><div className="browser-canvas">{screenshot ? <img src={`${screenshot}${screenshot.includes("?") ? "&" : "?"}v=${encodeURIComponent(exploration.updated_at || "latest")}`} alt={`Último screenshot de ${hostFromUrl(exploration.app_url)}`} /> : <div className="no-screenshot"><MonitorPlay size={32} /><strong>Aguardando captura do browser</strong><span>O preview aparecerá quando o executor publicar a primeira evidência.</span></div>}</div></div>
              <div className="evidence-strip"><span><Eye size={15} />Screenshot</span><span><Code2 size={15} />DOM estruturado</span><span><ShieldCheck size={15} />A LLM auxiliar somente interpreta</span></div>
            </section>
          )}

          {detailTab === "config" && (
            <section className="configuration-card configuration-wide">
              <div className="panel-heading"><span className="section-kicker">Modelos da execução</span><h2>Configuração usada</h2></div>
              <div className="configuration-grid">
                <div className="exploration-scope"><span className="model-role-icon"><Focus size={18} /></span><div><small>Escopo da exploração</small><strong>{explorationFocusArea(exploration.options) || "Aplicação inteira"}</strong><em>{explorationFocusArea(exploration.options) ? "O JEV prioriza e esgota este recorte funcional." : "O JEV explora todos os recursos que conseguir descobrir."}</em></div></div>
                <div className="config-model"><span className="model-role-icon amber"><BrainCircuit size={18} /></span><div><small>JEV · agente QA</small><strong>{exploration.agent?.model_id || exploration.agent?.model || "—"}</strong><em>{providerName(exploration.agent?.provider_id || exploration.agent?.provider)}</em></div></div>
                <div className="config-model"><span className="model-role-icon teal"><Sparkles size={18} /></span><div><small>LLM auxiliar</small><strong>{exploration.auxiliary_llm?.model_id || exploration.auxiliary_llm?.model || "—"}</strong><em>{providerName(exploration.auxiliary_llm?.provider_id || exploration.auxiliary_llm?.provider)}</em></div></div>
              </div>
            </section>
          )}
        </div>
      </section>
    </div>
  );
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
