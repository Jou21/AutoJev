"use client";

import { Fragment, useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { ArrowLeft, ArrowRight, Bot, Check, ChevronDown, ChevronRight, ClipboardList, Database, Download, FilePlus2, Globe2, GripVertical, Image, Info, Layers3, LoaderCircle, MessageSquare, MousePointer2, Play, Plus, RefreshCcw, Route, Save, Search, SlidersHorizontal, Square, X } from "lucide-react";
import PlanningChat, { type ChatLlm, type PlanningMessage } from "./PlanningChat";
import StepDragOverlay, { type StepDragPreview } from "./StepDragOverlay";
import StepEvidencePreview from "./StepEvidencePreview";
import { stepImageUrl, type RecordedStep } from "./suite-step-evidence";
import { caseAdditionReply, directCaseAddition } from "./suite-planning-chat";
import { nearestStepDrop, plannedSteps, placePlannedStep, type PlannedIntegration, type PlannedStep, type StepKind, type StepDropPosition } from "./suite-capture-plan";
import { bindHorizontalRail, bindHorizontalRailPan, moveRailItem, placeRailItem } from "./suite-horizontal-rail";
import { suiteExecutionHistory } from "./suite-execution-history";
import FunctionalFlows from "./FunctionalFlows";
import { caseFlowLabel, flowMemberships, matchesFunctionalFlow, type FlowClassification, type FunctionalFlow } from "./functional-flows";
import "./TestSuitesWorkspace.css";

type Request = <T>(path: string, options?: RequestInit & { raw?: boolean }) => Promise<T>;
type CapturePlan = { version: 1; checkpoints: { id: string; gap: number; order: number }[] };
type Definition = { title: string; description: string; preconditions: string[]; steps: string[]; expected_result: string; url: string };
type Case = Definition & { id: string; mode: "automatic" | "manual"; revision: number; archived?: boolean; catalog_revision?: number; status?: string; recorded_at?: string; flow_classification?: FlowClassification; source_exploration_id?: string; source_scenario_id?: string; recorded_steps?: RecordedStep[] };
type Selection = { case_id: string; source_exploration_id?: string; source_scenario_id?: string; capture_plan?: CapturePlan; planned_integrations?: PlannedIntegration[] };
type RoundItem = Selection & { mode: string; definition: Definition; state: string; execution_id?: string; result?: { status: string; actual_result: string }; reason?: string };
type Round = { id: string; root_id: string; name: string; status: string; revision: number; created_at: string; updated_at: string; items: RoundItem[]; count: number; finished_count: number; reason?: string; summary?: Record<string, number> };
type Suite = { id: string; app_url: string; created_at?: string; initial_count: number; cases: Case[]; functional_flows?: FunctionalFlow[]; rounds: Round[]; discoveries: { session_id: string; kind: string }[]; assistant_model?: string; assistant_llm?: ChatLlm };
type Group = { id: string; app_url: string; created_at: string; latest_activity: string; initial_count: number; case_count: number; active_count: number; initial_status: string; rounds: Round[]; legacy_runs: { id: string; count: number; status: string; created_at: string }[] };
type CoverageOperation = { operation: "create" | "read" | "update" | "delete"; status: "existing" | "gap" | "uncertain" | "not_applicable"; case_ids: string[]; new_case_indexes: number[]; reason: string };
type Suggestion = { reply: string; selected_case_ids: string[]; new_cases: Definition[]; coverage?: { screen: string; operations: CoverageOperation[] } | null; llm?: ChatLlm; usage: { total_tokens?: number; cost?: number; cost_known?: boolean } };
type Validation = { revision: number; available: boolean; effects: string[]; missing_inputs?: string[]; required_inputs?: string[]; items: { case_id: string; available: boolean; reason?: string; code?: string; required_inputs?: string[] }[] };
type Recording = { id: string; steps: string[]; url: string; status: string };
type CreationMode = "write" | "ai" | "record";
type Construction = { definition: Definition | null; goal: string; updates: number };
type Props = { rootId?: string; planId?: string; roundId?: string; initialCaseId?: string; initialTab?: "planner"; initialBatch?: { source: string; ct: string; capture_plan?: CapturePlan }[]; initialSourceId?: string; initialSourceCase?: string; initialCapturePlan?: CapturePlan; apiBase?: string; request: Request; navigate: (hash: string) => void; openReport: (root: string, scope: string, round?: string, caseId?: string, view?: "tests") => Promise<void> };

const labels: Record<string, string> = { draft: "Rascunho", queued: "Na fila", running: "Em execução", waiting_manual: "Aguardando avaliação", stopping: "Parando", completed: "Concluída", partial: "Parcial", failed: "Sem resultado", cancelled: "Cancelada", interrupted: "Interrompida", PASS: "Aprovado", FAIL: "Reprovado", BLOCKED: "Bloqueado", SKIPPED: "Sem validação" };
const date = (value?: string) => value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "Ainda não executado";
const host = (url: string) => { try { return new URL(url).hostname; } catch { return url; } };
const lines = (text: string) => text.split("\n").map(value => value.trim()).filter(Boolean);
const emptyDefinition = (url = ""): Definition => ({ title: "", description: "", url, steps: [], preconditions: [], expected_result: "" });
const encode = encodeURIComponent;
const active = (status: string) => ["running", "waiting_manual", "stopping"].includes(status);
const stepTools = [{ kind: "capture", title: "Aguardar print", note: "Pausar e anexar imagem", Icon: Image }, { kind: "sws", title: "Executar SWS", note: "Só planejamento", Icon: Route }, { kind: "sql", title: "Executar SQL", note: "Só planejamento", Icon: Database }] as const;
const stepTitle = (kind: StepKind) => stepTools.find(tool => tool.kind === kind)!.title;
const stepMime = "application/x-autojev-step";
const caseMime = "application/x-autojev-test-case";
const basePageSize = 20;
// Session-only navigation context, isolated by origin. Never store form text or credentials.
const catalogContexts = new Map<string, { query: string; filter: string; flow: string; scroll: number; page?: number }>();
export function Status({ value }: { value?: string }) { return <span className="suite-status" data-status={value || "SKIPPED"}>{labels[value || "SKIPPED"] || value}</span>; }

export default function TestSuitesWorkspace(props: Props) {
  const { rootId, planId, roundId, request, navigate, openReport } = props;
  const [groups, setGroups] = useState<Group[]>([]), [suite, setSuite] = useState<Suite | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [routePending, setRoutePending] = useState(!!(planId || roundId || props.initialBatch?.length));
  const [query, setQuery] = useState(() => catalogContexts.get(rootId || "")?.query || ""), [filter, setFilter] = useState(() => catalogContexts.get(rootId || "")?.filter || "all");
  const [selectedFlow, setSelectedFlow] = useState(() => catalogContexts.get(rootId || "")?.flow || "all");
  const [basePage, setBasePage] = useState(() => catalogContexts.get("")?.page || 1);
  const [expanded, setExpanded] = useState<string | null>(null), [name, setName] = useState("Nova execução");
  const [selection, setSelection] = useState<Selection[]>(() => props.initialCaseId ? [{ case_id: props.initialCaseId, source_exploration_id: props.initialSourceId, source_scenario_id: props.initialSourceCase, capture_plan: props.initialCapturePlan }] : []), [selectedCase, setSelectedCase] = useState<string | null>(props.initialCaseId || null);
  const [saved, setSaved] = useState<Round | null>(null), [dirty, setDirty] = useState(!!props.initialCaseId), [validation, setValidation] = useState<Validation | null>(null);
  const [roundCredentials, setRoundCredentials] = useState<Record<string, string>>({});
  const [authenticationOpen, setAuthenticationOpen] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- Private values must be discarded when the reviewed round changes.
  useEffect(() => { if (!validation) { setRoundCredentials({}); setAuthenticationOpen(false); } }, [validation]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- Never retain private values across route changes.
  useEffect(() => { setRoundCredentials({}); setAuthenticationOpen(false); }, [rootId, planId]);
  const requiredRoundInputs = [...new Set(validation?.missing_inputs || validation?.required_inputs || validation?.items.flatMap(item => item.required_inputs || []) || [])];
  const missingRoundInputs = requiredRoundInputs.some(key => !Object.hasOwn(roundCredentials, key) || !roundCredentials[key]?.trim());
  const [round, setRound] = useState<Round | null>(null);
  const [form, setForm] = useState<Definition | null>(null), [editId, setEditId] = useState<string | null>(null);
  const [maintenanceOpen, setMaintenanceOpen] = useState(false), [formBaseline, setFormBaseline] = useState("");
  const [leavePrompt, setLeavePrompt] = useState(false);
  const [newMode, setNewMode] = useState(false), [creationAttention, setCreationAttention] = useState(0);
  const [construction, setConstruction] = useState<Construction | null>(null), [constructionSelected, setConstructionSelected] = useState(false);
  const [recording, setRecording] = useState<Recording | null>(null), [creationMode, setCreationMode] = useState<CreationMode | null>(null);
  const [creationFocus, setCreationFocus] = useState<{ mode: CreationMode; version: number } | null>(null);
  const [planningFocus, setPlanningFocus] = useState(0);
  const selectorOpen = !validation;
  const [selectorFiltersOpen, setSelectorFiltersOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<"plan" | "chat">("plan");
  const [stepTool, setStepTool] = useState<StepKind>("capture"), [draggingStep, setDraggingStep] = useState(false), [dropSlot, setDropSlot] = useState<string | null>(null);
  const [stepPreview, setStepPreview] = useState<StepDragPreview | null>(null);
  const [stepNotice, setStepNotice] = useState("");
  const [draggedCaseId, setDraggedCaseId] = useState<string | null>(null), [caseDrop, setCaseDrop] = useState<{ id: string; side: "before" | "after" } | null>(null);
  const [caseOrderNotice, setCaseOrderNotice] = useState("");
  const [selectorOrder, setSelectorOrder] = useState<string[]>([]);
  const suppressSelectorClick = useRef(false);
  const [chatOpen, setChatOpen] = useState(true), [chatText, setChatText] = useState(""), [chatBusy, setChatBusy] = useState(false);
  const [messages, setMessages] = useState<PlanningMessage[]>([]), [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [chatLlm, setChatLlm] = useState<ChatLlm | null>(null), [chatError, setChatError] = useState("");
  const [manualVerdict, setManualVerdict] = useState(""), [manualNote, setManualNote] = useState("");
  const [reference, setReference] = useState<{ case_id: string; source_id: string; value: Partial<Case> } | null>(null);
  const [viewedStep, setViewedStep] = useState<{ context: string; index: number } | null>(null);
  const serial = useRef(0);
  const pendingTransition = useRef<(() => void) | null>(null), allowNavigation = useRef(false), pendingLeavesPage = useRef(false);
  const catalogScroll = useRef(catalogContexts.get(rootId || "")?.scroll || 0);
  const baseList = useRef<HTMLDivElement>(null);
  const stayButton = useRef<HTMLButtonElement>(null), leaveDialog = useRef<HTMLElement>(null);
  const caseEditor = useRef<HTMLFormElement>(null), caseTitle = useRef<HTMLInputElement>(null);
  const planningHeading = useRef<HTMLHeadingElement>(null);
  const planCenter = useRef<HTMLDivElement>(null);
  const stepsRail = useRef<HTMLDivElement>(null);
  const contextDetails = useRef<HTMLDetailsElement>(null);
  const pendingCheckpoint = useRef<string | null>(null);
  const hoveredStepSlot = useRef<StepDropPosition | null>(null);
  const draggingCase = useRef<string | null>(null);
  const selectorSearch = useRef<HTMLInputElement>(null), reviewPanel = useRef<HTMLElement>(null);
  const reviewedItems = useRef<RoundItem[]>([]);
  const selectorFilterTools = useRef<HTMLDivElement>(null), selectorFilterPanel = useRef<HTMLDivElement>(null), selectorFilterTrigger = useRef<HTMLButtonElement>(null);
  const chatComposer = useRef<HTMLTextAreaElement>(null), recordConfirmation = useRef<HTMLButtonElement>(null), recordingFinish = useRef<HTMLButtonElement>(null);
  const rootPath = "/test-suites/" + encode(rootId || "");
  const filteredGroups = groups.filter(group => (host(group.app_url) + group.id).toLowerCase().includes(query.toLowerCase()) && (filter !== "live" || group.active_count > 0)).sort((a, b) => (Date.parse(b.latest_activity || b.created_at) || 0) - (Date.parse(a.latest_activity || a.created_at) || 0));
  const basePageCount = Math.max(1, Math.ceil(filteredGroups.length / basePageSize));
  const currentBasePage = Math.min(basePage, basePageCount);
  const formChanged = !!form && JSON.stringify(form) !== formBaseline;
  const planChanged = dirty && (!!saved || selection.length > 0 || name !== "Nova execução");
  const constructionChanged = !!construction && (!!construction.definition || !!construction.goal.trim());
  const unsavedChanges = formChanged || planChanged || constructionChanged;
  const transition = (operation: () => void, recordingFinished = false) => {
    if (busy || chatBusy) return;
    if (formChanged || (recording && !recordingFinished)) { pendingLeavesPage.current = false; pendingTransition.current = operation; setLeavePrompt(true); }
    else operation();
  };
  useEffect(() => {
    const guard = (event: Event) => {
      if (allowNavigation.current) { allowNavigation.current = false; return; }
      if (!unsavedChanges && !recording && !busy && !chatBusy) return;
      event.preventDefault();
      if (busy || chatBusy) { setError("Aguarde a operação atual terminar antes de sair."); return; }
      const hash = (event as CustomEvent<{ hash: string }>).detail.hash;
      pendingLeavesPage.current = true;
      pendingTransition.current = () => { allowNavigation.current = true; navigate(hash); };
      setLeavePrompt(true);
    };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (unsavedChanges || recording || busy || chatBusy) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener?.("autojev:before-navigate", guard);
    window.addEventListener?.("beforeunload", beforeUnload);
    return () => { window.removeEventListener?.("autojev:before-navigate", guard); window.removeEventListener?.("beforeunload", beforeUnload); };
  }, [unsavedChanges, recording, busy, chatBusy, navigate]);
  useEffect(() => {
    if (!leavePrompt) return;
    const previous = window.document?.activeElement as HTMLElement | null;
    stayButton.current?.focus({ preventScroll: true });
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) { event.preventDefault(); setLeavePrompt(false); pendingTransition.current = null; }
      if (event.key !== "Tab") return;
      const buttons = Array.from(leaveDialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || []);
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (event.shiftKey && event.target === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && event.target === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener?.("keydown", keys);
    return () => { window.removeEventListener?.("keydown", keys); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [leavePrompt, busy]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const details = contextDetails.current;
      if (details?.open && !details.contains(event.target as Node)) details.open = false;
    };
    const escape = (event: KeyboardEvent) => {
      const details = contextDetails.current;
      if (event.key !== "Escape" || !details?.open) return;
      details.open = false;
      details.querySelector("summary")?.focus({ preventScroll: true });
      event.preventDefault();
    };
    window.addEventListener("pointerdown", outside, true);
    window.addEventListener("keydown", escape);
    return () => { window.removeEventListener("pointerdown", outside, true); window.removeEventListener("keydown", escape); };
  }, []);
  useEffect(() => {
    catalogContexts.set(rootId || "", { query, filter, flow: selectedFlow, scroll: catalogScroll.current, page: rootId ? undefined : basePage });
  }, [rootId, query, filter, selectedFlow, basePage]);
  useEffect(() => {
    if (rootId || loading) return;
    if (baseList.current) baseList.current.scrollTop = 0;
  }, [rootId, loading, currentBasePage, query, filter]);

  const refresh = useCallback(async () => {
    if (rootId) setSuite(await request<Suite>("/test-suites/" + encode(rootId)));
    else setGroups(await request<Group[]>("/test-suites"));
  }, [request, rootId]);
  useEffect(() => { let alive = true;
    request<Suite | Group[]>(rootId ? "/test-suites/" + encode(rootId) : "/test-suites").then(value => {
      if (!alive) return;
      if (rootId) setSuite(value as Suite); else setGroups(value as Group[]);
    }).catch(caught => { if (alive) setError(String(caught.message)); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [request, rootId]);
  useEffect(() => {
    if (!props.initialSourceId || !props.initialSourceCase) return;
    let alive = true;
    request<{ root_id: string; case_id: string; mode: string }>(`/test-suites/${encode(props.initialSourceId)}/case-context/${encode(props.initialSourceCase)}`).then(context => {
      if (!alive) return;
      if (context.mode === "manual") { navigate(`#/conjuntos/${encode(context.root_id)}?ct=${encode(context.case_id)}`); return; }
      if (context.root_id === rootId && context.case_id === props.initialCaseId) return;
      const params = new URLSearchParams({ ct: context.case_id, source: props.initialSourceId!, source_ct: props.initialSourceCase!, ...(props.initialCapturePlan ? { capture_plan: JSON.stringify(props.initialCapturePlan) } : {}) });
      navigate(`#/conjuntos/${encode(context.root_id)}?${params}`);
    }).catch(caught => { if (alive) setError(caught.message); });
    return () => { alive = false; };
  }, [request, rootId, props.initialSourceId, props.initialSourceCase, props.initialCaseId, props.initialCapturePlan, navigate]);
  // "Reexecutar testes" from a report: every CT arrives selected, each with
  // its own saved print pauses. Nothing runs until the round is confirmed.
  const initialBatchKey = JSON.stringify(props.initialBatch || []);
  useEffect(() => {
    const batch: NonNullable<Props["initialBatch"]> = JSON.parse(initialBatchKey);
    if (!batch.length || !rootId) return;
    let alive = true;
    // One request for every CT; the backend rejects CTs from another base.
    request<{ root_id: string; case_id: string; mode: string }[]>(`/test-suites/${encode(rootId)}/case-contexts?itens=${encode(JSON.stringify(batch.map(({ source, ct }) => ({ source, ct }))))}`).then(contexts => {
      if (!alive) return;
      const results = contexts.map((context, index) => ({ item: batch[index], context }));
      if (results.some(({ context }) => context.root_id !== rootId)) { setError("Alguns testes enviados pelo relatório não pertencem a esta base. Nada foi selecionado; reabra o relatório pela aplicação."); return; }
      const seen = new Set<string>(), next: Selection[] = [];
      for (const { item, context } of results) {
        if (seen.has(context.case_id)) continue;
        seen.add(context.case_id);
        next.push(context.mode === "manual" ? { case_id: context.case_id }
          : { case_id: context.case_id, source_exploration_id: item.source, source_scenario_id: item.ct, ...(item.capture_plan ? { capture_plan: item.capture_plan } : {}) });
      }
      setSelection(next); setSelectedCase(next[0]?.case_id || null); setDirty(true);
    }).catch(caught => { if (alive) setError(caught instanceof Error ? caught.message : "Não foi possível carregar os testes enviados pelo relatório."); })
      .finally(() => { if (alive) setRoutePending(false); });
    return () => { alive = false; };
  }, [request, rootId, initialBatchKey]);
  const selectedSource = selection.find(item => item.case_id === selectedCase);
  useEffect(() => {
    if (!selectedCase || !selectedSource?.source_exploration_id || !selectedSource?.source_scenario_id) return;
    let alive = true;
    request<{ reference: Partial<Case> }>(`/test-suites/${encode(selectedSource.source_exploration_id)}/case-context/${encode(selectedSource.source_scenario_id)}`).then(value => {
      if (alive && value.reference) setReference({ case_id: selectedCase, source_id: selectedSource.source_exploration_id!, value: value.reference });
    }).catch(caught => { if (alive) setError(caught.message); });
    return () => { alive = false; };
  }, [request, selectedCase, selectedSource?.source_exploration_id, selectedSource?.source_scenario_id]);
  useEffect(() => {
    if (!planId) return;
    let alive = true;
    request<Round>("/test-suites/rounds/" + encode(planId)).then(plan => {
      if (!alive) return;
      if (plan.status !== "draft") { navigate(`#/conjuntos/${encode(plan.root_id)}?round=${encode(plan.id)}`); return; }
      setSaved(plan); setName(plan.name); setSelection(plan.items.map(item => ({ case_id: item.case_id, source_exploration_id: item.source_exploration_id || undefined, source_scenario_id: item.source_scenario_id || undefined, capture_plan: item.capture_plan || undefined, planned_integrations: item.planned_integrations || undefined })));
      setSelectedCase(plan.items[0]?.case_id); setDirty(false);
    }).catch(caught => { if (alive) setError(caught.message); }).finally(() => { if (alive) setRoutePending(false); });
    return () => { alive = false; };
  }, [planId, request, navigate]);
  useEffect(() => {
    if (!roundId) return;
    let alive = true;
    const load = () => request<Round>("/test-suites/rounds/" + encode(roundId)).then(value => { if (alive) { setRound(value); } }).catch(caught => { if (alive) setError(caught.message); }).finally(() => { if (alive) setRoutePending(false); });
    void load(); const timer = window.setInterval(load, 2500);
    return () => { alive = false; window.clearInterval(timer); };
  }, [roundId, request]);
  const completeRecording = useCallback((result: Recording) => {
    const definition = { ...emptyDefinition(result.url), title: "Teste gravado", steps: result.steps };
    setRecording(null); setForm(definition); setFormBaseline(JSON.stringify(emptyDefinition(suite?.app_url)));
    setEditId(null); setCreationMode("write"); setMobilePane("plan");
    setConstruction(current => current ? { ...current, definition } : null);
    setCreationFocus(current => ({ mode: "write", version: (current?.version || 0) + 1 }));
  }, [suite?.app_url]);
  useEffect(() => {
    if (!recording) return;
    let alive = true, finished = false;
    const timer = window.setInterval(() => request<Recording>(`/test-suites/recordings/${encode(recording.id)}/state`).then(value => {
      if (!alive || finished) return;
      if (value.status === "finished") {
        finished = true; completeRecording(value);
      } else setRecording(value);
    }).catch(caught => { if (alive && !finished) setError(caught.message); }), 2000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [recording?.id, request, completeRecording]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!planningFocus) return;
    planCenter.current?.scrollTo?.({ top: 0, behavior: "instant" });
    planningHeading.current?.scrollIntoView({ block: "start", behavior: "instant" });
    planningHeading.current?.focus({ preventScroll: true });
  }, [planningFocus]);

  const selectorFiltersVisible = selectorFiltersOpen && selectorOpen && !validation;
  useEffect(() => {
    if (!selectorFiltersVisible) return;
    selectorFilterPanel.current?.querySelector?.<HTMLElement>("select")?.focus({ preventScroll: true });
    const outside = (event: PointerEvent) => { if (!selectorFilterTools.current?.contains?.(event.target as Node)) setSelectorFiltersOpen(false); };
    const keys = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); setSelectorFiltersOpen(false); selectorFilterTrigger.current?.focus({ preventScroll: true }); }
    };
    window.addEventListener("pointerdown", outside); window.addEventListener("keydown", keys);
    return () => { window.removeEventListener("pointerdown", outside); window.removeEventListener("keydown", keys); };
  }, [selectorFiltersVisible]);
  useEffect(() => {
    const cleanups = [bindHorizontalRail(stepsRail.current), bindHorizontalRailPan(stepsRail.current)];
    return () => cleanups.forEach(cleanup => cleanup());
  }, [loading, validation, selectorOpen, selectedCase, maintenanceOpen, form, newMode, recording, selection.length, constructionSelected]);
  useEffect(() => {
    stepsRail.current?.scrollTo?.({ left: 0, behavior: "instant" });
  }, [selectedCase]);
  useEffect(() => {
    if (!pendingCheckpoint.current) return;
    const point = stepsRail.current?.querySelector?.<HTMLElement>(`[data-checkpoint-id="${pendingCheckpoint.current}"]`);
    point?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "instant" });
    pendingCheckpoint.current = null;
  }, [selection]);
  useEffect(() => {
    if (!validation) return;
    reviewPanel.current?.scrollIntoView({ block: "start", behavior: "instant" });
    reviewPanel.current?.focus({ preventScroll: true });
  }, [validation]);

  useEffect(() => {
    if (!creationFocus) return;
    const target = creationFocus.mode === "write" ? caseTitle.current : creationFocus.mode === "ai" ? chatComposer.current : recordingFinish.current || recordConfirmation.current;
    const area = creationFocus.mode === "write" ? caseEditor.current : target;
    area?.scrollIntoView({ block: creationFocus.mode === "write" ? "start" : "center", behavior: "instant" });
    target?.focus({ preventScroll: true });
  }, [creationFocus]);

  const focusCreation = (mode: CreationMode) => setCreationFocus(current => ({ mode, version: (current?.version || 0) + 1 }));
  const showChat = () => { setChatOpen(true); setMobilePane("chat"); focusCreation("ai"); };
  const chooseCreation = (mode: "ai" | "record") => {
    if (busy || chatBusy || !newMode) return;
    setNewMode(false); setCreationMode(mode); setChatOpen(true); setMobilePane("chat");
    setMessages(current => [...current,
      { role: "user", content: mode === "ai" ? "Planejar com IA" : "Gravar minha navegação" },
      { role: "assistant", content: mode === "ai"
        ? "Qual tela você quer testar e o que precisa conferir? Pode dizer, por exemplo: quero testar o CRUD da tela de clientes. Vou comparar com os testes da sua base e sugerir o que falta."
        : "Vamos gravar seus passos. Confira os cuidados abaixo antes de abrir o navegador. Depois da gravação, você poderá revisar o roteiro antes de salvar." },
    ]);
    focusCreation(mode);
  };
  const openCaseEditor = (definition: Definition, id: string | null = null, recordingFinished = false) => {
    transition(() => {
      setForm(definition); setFormBaseline(id ? JSON.stringify(definition) : JSON.stringify(emptyDefinition(suite?.app_url))); setEditId(id); setCreationMode("write"); setMobilePane("plan");
      if (!id) setConstruction(current => current ? { ...current, definition } : null);
        focusCreation("write");
    }, recordingFinished);
  };
  const closeEditor = () => { setForm(null); setEditId(null); setCreationMode(null); setNewMode(false); setMaintenanceOpen(false); };
  const closeCaseEditor = () => {
    if (!editId) { setConstruction(null); setConstructionSelected(false); }
    closeEditor();
  };
  const openMaintenance = () => transition(() => {
    setValidation(null); setChatError(""); setForm(null); setEditId(null); setMaintenanceOpen(false); setNewMode(true); setCreationMode(null);
    setConstruction(current => current || { definition: null, goal: "", updates: 0 });
    setCreationAttention(current => current + 1);
    setMessages(current => current.at(-1)?.kind === "creation_prompt" ? current : [...current,
      { role: "assistant", kind: "creation_prompt", content: "Como você gostaria de realizar esse teste?" },
    ]);
    showChat();
  });

  const perform = async (operation: () => Promise<void>) => {
    setBusy(true); setError(""); try { await operation(); } catch (caught) { setError((caught instanceof Error ? caught.message : "Não foi possível concluir.") + " Suas alterações foram mantidas. Revise o aviso e tente novamente; nada será sobrescrito sem validação."); } finally { setBusy(false); }
  };
  const change = () => { setDirty(true); setValidation(null); };
  const showPlanning = () => {
    if (filter === "archived") setFilter("all");
    setSelectedCase(current => current && selection.some(item => item.case_id === current) ? current : selection[0]?.case_id || null);
    setMobilePane("plan"); setPlanningFocus(current => current + 1);
  };
  const returnToScript = () => { if (!name.trim()) setName(saved?.name || "Nova execução"); setValidation(null); showPlanning(); };
  const add = (ids: string[]) => {
    const missing = ids.filter(id => !suite?.cases.some(item => item.id === id));
    if (missing.length) { setError(`Não encontrei ${missing.join(", ")} nesta base. Atualize a lista antes de selecionar.`); return null; }
    const archived = ids.filter(id => suite?.cases.some(item => item.id === id && item.archived));
    if (archived.length) { setError(`${archived.join(", ")} está arquivado. Restaure o teste na base antes de selecioná-lo.`); return null; }
    const additions = [...new Set(ids)].filter(id => !selection.some(item => item.case_id === id));
    if (selection.length + additions.length > 500) { setError("Cada rodada aceita até 500 CTs. Divida a seleção em mais rodadas."); return null; }
    if (additions.length) {
      setSelection(current => [...current, ...additions.filter(id => !current.some(item => item.case_id === id)).map(case_id => ({ case_id }))]);
      if (!selectedCase) setSelectedCase(additions[0]); change();
    }
    return additions;
  };
  const toggle = (id: string) => { if (selection.some(item => item.case_id === id)) { setSelection(current => current.filter(item => item.case_id !== id)); if (selectedCase === id) setSelectedCase(selection.find(item => item.case_id !== id)?.case_id || null); change(); } else add([id]); };
  const endCaseDrag = () => { draggingCase.current = null; setDraggedCaseId(null); setCaseDrop(null); };
  const acceptsCaseDrag = (event: DragEvent) => !!draggingCase.current && event.dataTransfer.types.includes(caseMime);
  const dropCase = (event: DragEvent, targetId?: string, side: "before" | "after" = "after") => {
    if (!acceptsCaseDrag(event)) return;
    event.preventDefault(); event.stopPropagation();
    const source = draggingCase.current;
    endCaseDrag();
    if (busy || chatBusy) return;
    try {
      const payload = JSON.parse(event.dataTransfer.getData(caseMime));
      if (payload.caseId !== source || payload.rootId !== rootId) return;
      const target = targetId ? selection.findIndex(item => item.case_id === targetId) : selection.length;
      if (target < 0) return;
      const next = placeRailItem(selection, selection.findIndex(item => item.case_id === source), targetId ? target + (side === "after" ? 1 : 0) : target);
      if (next === selection) return;
      setSelection(next); setSelectorOrder([]); change();
      setCaseOrderNotice(`${source} movido para a posição ${next.findIndex(item => item.case_id === source) + 1} de ${next.length}. Salve o rascunho para manter a nova ordem.`);
    } catch { /* Foreign or malformed drags never change the execution order. */ }
  };
  const save = async () => {
    const value = await request<Round>(saved ? `${rootPath}/plans/${encode(saved.id)}` : `${rootPath}/plans`, { method: saved ? "PUT" : "POST", body: JSON.stringify({ name, items: selection, ...(saved ? { expected_revision: saved.revision } : {}) }) });
    setSaved(value); setName(value.name); setDirty(false); return value;
  };
  const review = () => perform(async () => {
    setMobilePane("plan");
    setValidation({ revision: saved?.revision || 0, available: false, effects: [], items: selection.map(item => ({ case_id: item.case_id, available: false, code: "checking" })) });
    try {
      const plan = dirty || !saved ? await save() : saved;
      const checked = await request<Validation>(`/test-suites/rounds/${encode(plan.id)}/validate`, { method: "POST" });
      reviewedItems.current = plan.items; setValidation(checked);
    } catch (error) {
      setValidation(null);
      throw error;
    }
  });
  const persistCase = async (include: boolean) => {
    if (!form) return;
    if (include && selection.length >= 500 && !selection.some(value => value.case_id === editId)) throw new Error("O limite é de 500 testes. Salve somente na base ou retire um teste desta execução.");
    const clean = { title: form.title, description: form.description, url: form.url, expected_result: form.expected_result, steps: lines(form.steps.join("\n")), preconditions: lines(form.preconditions.join("\n")) };
    const item = await request<Case>(`${rootPath}/cases${editId ? "/" + encode(editId) : ""}`, { method: editId ? "PUT" : "POST", body: JSON.stringify({ ...clean, ...(editId ? { expected_revision: (form as Case).revision } : {}) }) });
    // Reconcile the successful response before a refresh; a failed GET must not cause duplicate POSTs.
    setSuite(current => {
      if (!current) return current;
      const previous = current.cases.find(value => value.id === item.id);
      const updated = { ...item, archived: previous?.archived || false, catalog_revision: previous?.catalog_revision || 0 };
      return { ...current, cases: [...current.cases.filter(value => value.id !== item.id), updated].sort((a, b) => a.id.localeCompare(b.id, undefined, { numeric: true })) };
    });
    closeEditor();
    if (!editId) { setConstruction(null); setConstructionSelected(false); }
    setMessages(current => [...current, { role: "assistant", content: `${item.id} salvo na base${include ? " e incluído na preparação" : " sem criar execução"}. Esta versão ainda não foi executada.` }]);
    setSuggestion(current => current ? { ...current, coverage: null, new_cases: current.new_cases.filter(value => value.title !== form.title) } : null);
    if (selection.some(value => value.case_id === item.id)) change();
    if (include) {
      setSelection(current => current.some(value => value.case_id === item.id) ? current : [...current, { case_id: item.id }]);
      setSelectedCase(item.id); change(); setMobilePane("plan");
    }
  };
  const createCase = (include = !maintenanceOpen) => perform(() => persistCase(include));
  const finishTransition = () => {
    const operation = pendingTransition.current;
    pendingTransition.current = null; setLeavePrompt(false); closeEditor();
    if (pendingLeavesPage.current) { setDirty(false); setConstruction(null); setConstructionSelected(false); }
    operation?.();
  };
  const discardConstruction = () => {
    if (recording || busy || chatBusy) { showChat(); return; }
    const discard = () => {
      setConstruction(null); setConstructionSelected(false); setSuggestion(null); setNewMode(false);
      if (!editId) { setForm(null); setFormBaseline(""); setCreationMode(null); }
    };
    if (constructionChanged || (!editId && formChanged)) {
      pendingLeavesPage.current = false; pendingTransition.current = discard; setLeavePrompt(true);
    } else discard();
  };
  const saveBeforeLeaving = () => perform(async () => {
    if (recording) return;
    if (constructionChanged && !formChanged) throw new Error("Revise e salve o teste em construção antes de sair, ou escolha descartar.");
    if (formChanged) {
      if (!form?.title.trim() || !form.expected_result.trim() || !lines(form.steps.join("\n")).length || !URL.canParse(form.url)) throw new Error("Complete título, passos, resultado esperado e um endereço válido antes de salvar.");
      await persistCase(false);
    }
    if (pendingLeavesPage.current && planChanged) {
      if (!selection.length || !name.trim()) throw new Error("Escolha ao menos um teste e informe o nome para salvar o rascunho.");
      await save();
    }
    finishTransition();
  });
  const placeStep = (kind: StepKind, gap: number, index: number, movingId?: string) => {
    if (!selectedCase) return;
    const chosen = selection.find(item => item.case_id === selectedCase);
    if (!chosen) return;
    const points = plannedSteps(chosen.capture_plan?.checkpoints, chosen.planned_integrations);
    const moving = movingId ? points.find(point => point.id === movingId && point.kind === kind) : undefined;
    if (movingId && !moving) return;
    if (!moving && points.length >= 100) { setError("Cada CT aceita até 100 etapas extras."); return; }
    const used = new Set(selection.flatMap(item => plannedSteps(item.capture_plan?.checkpoints, item.planned_integrations).map(point => point.id)));
    let identity = moving?.id || "db-" + (++serial.current);
    while (!moving && used.has(identity)) identity = "db-" + (++serial.current);
    pendingCheckpoint.current = identity;
    const next = placePlannedStep(points, moving || { id: identity, gap, order: 0, kind }, gap, index);
    setSelection(current => current.map(item => item.case_id !== selectedCase ? item : { ...item,
      ...(item.capture_plan || next.some(point => point.kind === "capture") ? { capture_plan: { version: 1 as const, checkpoints: next.filter(point => point.kind === "capture").map(({ id, gap, order }) => ({ id, gap, order })) } } : {}),
      planned_integrations: next.filter((point): point is PlannedIntegration => point.kind !== "capture"),
    }));
    setStepNotice(`${stepTitle(kind)} ${moving ? "movido" : "adicionado"} ao roteiro. ${kind === "capture" ? "Nenhum teste foi iniciado." : "Integração indisponível: salvo apenas no planejamento."}`);
    hoveredStepSlot.current = null;
    setDraggingStep(false); setDropSlot(null); setStepPreview(null); change();
  };
  const beginStepDrag = (event: DragEvent, kind: StepKind, id?: string) => {
    hoveredStepSlot.current = null; setDropSlot(null);
    event.dataTransfer.setData(stepMime, JSON.stringify({ kind, id, caseId: selectedCase }));
    event.dataTransfer.effectAllowed = id ? "move" : "copy"; setDraggingStep(true); setStepTool(kind);
    const source = stepsRail.current?.querySelector?.<HTMLElement>(".suite-source-step");
    const size = source?.getBoundingClientRect().width || 160;
    setStepPreview({ kind, title: stepTitle(kind), size, x: event.clientX || 0, y: event.clientY || 0, caseId: selectedCase, movingId: id });
    // Suppress the small native palette-button ghost; our portal supplies the
    // actual card-sized preview without intercepting the drop target.
    const blank = window.document?.createElement?.("canvas");
    if (blank) { blank.width = blank.height = 1; event.dataTransfer.setDragImage?.(blank, 0, 0); }
  };
  const endStepDrag = () => { hoveredStepSlot.current = null; setDraggingStep(false); setDropSlot(null); setStepPreview(null); };
  const resolveStepSlot = (x: number, fallback?: StepDropPosition) => {
    const buttons = stepsRail.current?.querySelectorAll?.<HTMLButtonElement>("[data-gap][data-insertion-index]");
    const slots = Array.from(buttons || [], button => {
      const box = button.getBoundingClientRect();
      return { gap: Number(button.dataset.gap), index: Number(button.dataset.insertionIndex), left: box.left, width: box.width };
    });
    const preview = stepsRail.current?.querySelector?.<HTMLElement>("[data-step-insertion-preview]")?.getBoundingClientRect();
    return nearestStepDrop(x, slots, hoveredStepSlot.current, preview) || fallback || null;
  };
  const overStepCanvas = (event: DragEvent, fallback?: StepDropPosition) => {
    if (!draggingStep || stepPreview?.caseId !== selectedCase || !event.dataTransfer.types.includes(stepMime)) return;
    if ((event.target as Element | null)?.closest?.(".suite-step-image")) { hoveredStepSlot.current = null; setDropSlot(null); return; }
    event.preventDefault(); event.stopPropagation?.();
    const target = resolveStepSlot(event.clientX, fallback);
    if (target) { hoveredStepSlot.current = target; setDropSlot(`${target.gap}:${target.index}`); }
  };
  const dropStep = (event: DragEvent, fallback?: StepDropPosition) => {
    if (!event.dataTransfer.types.includes(stepMime)) return;
    if ((event.target as Element | null)?.closest?.(".suite-step-image")) { endStepDrag(); return; }
    event.preventDefault(); event.stopPropagation?.();
    // Commit the displayed destination, even if opening the preview moved the
    // card/button under the cursor. The drop must never disagree with the UI.
    const target = hoveredStepSlot.current || resolveStepSlot(event.clientX, fallback);
    endStepDrag();
    if (!target) return;
    try { const data = JSON.parse(event.dataTransfer.getData(stepMime)); if (data.caseId === selectedCase && ["capture", "sws", "sql"].includes(data.kind) && (data.id === undefined || typeof data.id === "string")) placeStep(data.kind, target.gap, target.index, data.id); } catch { /* Ignore unrelated or malformed drag payloads. */ }
  };
  const stepSlot = (gap: number, index: number, afterId?: string) => {
    const key = `${gap}:${index}`, preview = draggingStep && stepPreview?.caseId === selectedCase && dropSlot === key ? stepPreview : null;
    const over = (event: DragEvent) => overStepCanvas(event, { gap, index });
    const drop = (event: DragEvent) => dropStep(event, { gap, index });
    const button = <button type="button" className={`suite-step-slot${preview ? " drop-active" : ""}`} data-gap={gap} data-insertion-index={index}
    aria-hidden={preview ? true : undefined} disabled={!!preview} tabIndex={preview ? -1 : undefined}
    aria-label={stepTool === "capture" ? afterId ? `Adicionar print após pausa ${afterId}` : `Adicionar print na posição ${gap}` : `Adicionar ${stepTitle(stepTool)} na posição ${gap}, intervalo ${index + 1}`}
    title={`Inserir ${stepTitle(stepTool)} aqui`} onClick={() => placeStep(stepTool, gap, index)}
    onDragOver={over} onDrop={drop}><Plus size={17} /></button>;
    // During hover the preview replaces + in the layout. Keep its invisible
    // anchor measurable so expanding the card cannot change the drag target.
    return <div className="suite-step-drop-target" onDragOver={over} onDrop={drop}>{button}
      {preview && <article className={`suite-source-step suite-print-card suite-step-insertion-preview kind-${preview.kind}`} data-step-insertion-preview data-preview-gap={gap} data-preview-index={index} aria-hidden="true"><small>PRÉVIA</small><strong>{preview.title}</strong><span>Solte para {preview.movingId ? "mover" : "adicionar"}</span></article>}
    </div>;
  };
  const removeStep = (point: PlannedStep) => {
    setSelection(current => current.map(item => item.case_id !== selectedCase ? item : point.kind === "capture"
      ? { ...item, capture_plan: { version: 1, checkpoints: item.capture_plan!.checkpoints.filter(value => value.id !== point.id) } }
      : { ...item, planned_integrations: item.planned_integrations?.filter(value => value.id !== point.id) }));
    setStepNotice(`${stepTitle(point.kind)} removido do roteiro.`); change();
  };
  const ask = async (text: string, llm: ChatLlm | null) => {
    if (!text.trim() || chatBusy) return;
    const direct = directCaseAddition(text, suite?.cases.map(item => item.id) || []);
    if (!direct && !llm) { setChatError("Escolha um modelo de IA ativo no seletor acima. Comandos como “adicione o CT004” funcionam sem modelo."); return; }
    const next = [...messages, { role: "user" as const, content: text.trim() }].slice(-12);
    setMessages(next); setNewMode(false); setChatText(""); setSuggestion(null); setError(""); setChatError("");
    if (direct) {
      let reply: string;
      if (direct.missing.length) reply = `Não encontrei ${direct.missing.join(", ")} de forma inequívoca neste conjunto. Nenhum CT foi adicionado.`;
      else {
        const added = add(direct.ids);
        reply = added === null ? "Não adicionei os testes. Confira o aviso na tela: pode haver um teste arquivado ou o limite de 500 casos foi atingido." : caseAdditionReply(added, direct.ids.filter(id => !added.includes(id)));
      }
      setMessages([...next, { role: "assistant", content: reply }]);
      return;
    }
    setChatBusy(true);
    if (construction) setConstruction(current => current ? { ...current, goal: current.goal || text.trim() } : null);
    try { const reply = await request<Suggestion>(`${rootPath}/assistant`, { method: "POST", body: JSON.stringify({ messages: next.map(({ role, content }) => ({ role, content })), selected_case_ids: selection.map(item => item.case_id), llm: { provider_id: llm!.provider_id, model_id: llm!.model_id }, ...(construction?.definition ? { draft: construction.definition } : {}) }) });
      const actualModel = reply.llm && (reply.llm.model_id !== llm?.model_id || reply.llm.provider_id !== llm?.provider_id) ? reply.llm : llm!;
      setMessages([...next, { role: "assistant", content: reply.reply, llm: actualModel }]); setSuggestion(reply);
      if (reply.new_cases.length === 1) setConstruction(current => current ? { ...current, definition: reply.new_cases[0], updates: current.updates + 1 } : null);
    } catch (caught) { setChatError(caught instanceof Error ? caught.message : "Assistente indisponível."); } finally { setChatBusy(false); }
  };

  // A refresh can remove the last page. Normalize the stored page without an extra effect render.
  if (!rootId && !loading && basePage !== currentBasePage) setBasePage(currentBasePage);
  if (loading || routePending) return <section className="panel suites-loading" role="status"><LoaderCircle className="spin" />{planId ? "Abrindo seu rascunho…" : roundId ? "Abrindo a execução…" : props.initialBatch?.length ? `Preparando ${props.initialBatch.length} testes para a execução…` : "Carregando seus testes…"}</section>;
  if (!rootId) {
    const firstIndex = (currentBasePage - 1) * basePageSize;
    const changePage = (page: number) => { setBasePage(page); setExpanded(null); };
    return <div className="view-stack suite-workspace suite-home"><header className="suite-page-heading"><div><h1>Meus testes</h1><p>Explore uma vez. Depois, ajuste sua base de testes e execute o que precisar.</p></div><button className="button primary" onClick={() => navigate("#/nova-exploracao")}><Plus size={18} />Nova exploração</button></header>
      {error && <p className="suite-error" role="alert">{error}</p>}
      <div className="suite-toolbar"><label className="suite-search"><Search size={17} /><input aria-label="Buscar bases de testes" placeholder="Buscar aplicação ou identificação…" value={query} onChange={event => { setQuery(event.target.value); changePage(1); }} /></label><div className="suite-tabs"><button aria-pressed={filter === "all"} onClick={() => { setFilter("all"); changePage(1); }}>Todos</button><button aria-pressed={filter === "live"} onClick={() => { setFilter("live"); changePage(1); }}>Em andamento</button></div><span className="suite-list-count" role="status">{filteredGroups.length} {filteredGroups.length === 1 ? "base" : "bases de testes"}</span><button className="button ghost" disabled={busy} onClick={() => void perform(refresh)}><RefreshCcw size={16} />Atualizar</button></div>
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Named list with independent keyboard scrolling. */}
      <div id="suite-group-list" ref={baseList} className="suite-group-list" role="region" aria-label="Aplicações testadas, mais recentes primeiro" tabIndex={0}>{filteredGroups.slice(firstIndex, firstIndex + basePageSize).map(group => <section className="suite-group panel" key={group.id}>
        <div className="suite-group-main"><button type="button" className="suite-expand" aria-label={`Mostrar execuções de ${host(group.app_url)} iniciadas em ${date(group.created_at)}`} aria-expanded={expanded === group.id} onClick={() => setExpanded(expanded === group.id ? null : group.id)}>{expanded === group.id ? <ChevronDown /> : <ChevronRight />}</button><span className="suite-app-icon"><Layers3 size={19} /></span><div className="suite-group-name"><button type="button" className="suite-group-title" title={`Grupo ${group.id}`} aria-label={`Abrir exploração de ${host(group.app_url)}, criada em ${date(group.created_at)}`} onClick={() => navigate(`#/exploracoes/${encode(group.id)}`)}>{host(group.app_url)}</button><span>Criado em {date(group.created_at)}{group.active_count > 0 && " · Em andamento"}</span></div><div className="suite-group-stat"><strong>{group.case_count}</strong><span>testes</span></div><div className="suite-group-stat"><strong>{1 + group.legacy_runs.length + group.rounds.filter(item => item.status !== "draft").length}</strong><span>execuções</span></div><div className="suite-group-activity"><span>Última atividade</span><time dateTime={group.latest_activity}>{date(group.latest_activity)}</time></div><button type="button" className="button secondary suite-group-open" aria-label={`Ver testes de ${host(group.app_url)}, criado em ${date(group.created_at)}`} title="Abre os testes desta base em uma nova aba" disabled={busy} onClick={() => void perform(() => openReport(group.id, "consolidated", undefined, undefined, "tests"))}>Ver testes<ArrowRight size={16} /></button></div>
        {expanded === group.id && <div className="suite-child-list">{suiteExecutionHistory(group).map(item => <button key={`${item.kind}:${item.id}`} data-history-id={item.id} data-history-kind={item.kind} onClick={() => navigate(item.href)}>
          {item.kind === "initial" ? <Globe2 size={16} /> : item.kind === "round" ? <ClipboardList size={16} /> : <RefreshCcw size={16} />}
          <strong>{item.status === "draft" ? `Rascunho: ${item.title}` : item.title}</strong><span>{item.count} testes · {date(item.created_at)}</span><Status value={item.status} />
        </button>)}</div>}
      </section>)}{!filteredGroups.length && <section className="panel suite-empty"><Layers3 /><h2>{groups.length ? "Nenhuma aplicação encontrada" : "Vamos testar sua primeira aplicação?"}</h2><p>{groups.length ? "Ajuste a busca ou remova os filtros." : "Informe o endereço da aplicação para começar a criar seus testes."}</p><button className="button secondary" onClick={() => { if (groups.length) { setQuery(""); setFilter("all"); changePage(1); } else navigate("#/nova-exploracao"); }}>{groups.length ? "Limpar filtros" : "Testar nova aplicação"}</button></section>}</div>
      {basePageCount > 1 && <nav className="suite-pagination" aria-label="Paginação das bases de testes"><p role="status" aria-live="polite" aria-atomic="true">{firstIndex + 1}–{Math.min(firstIndex + basePageSize, filteredGroups.length)} de {filteredGroups.length}</p><div className="suite-pagination-controls"><button type="button" aria-label="Página anterior" aria-controls="suite-group-list" disabled={busy || currentBasePage === 1} onClick={() => changePage(currentBasePage - 1)}><ArrowLeft size={14} />Anterior</button><span>Página {currentBasePage} de {basePageCount}</span><button type="button" aria-label="Próxima página" aria-controls="suite-group-list" disabled={busy || currentBasePage === basePageCount} onClick={() => changePage(currentBasePage + 1)}>Próxima<ArrowRight size={14} /></button></div></nav>}
    </div>;
  }
  if (!suite) return <section className="panel suite-empty"><h2>Não foi possível abrir esta base</h2><p className="suite-error" role="alert">{error || "A base não está disponível."}</p><p>Tente carregar novamente ou volte para escolher outra base. Nenhum teste foi alterado.</p><div className="suite-heading-actions"><button className="button primary" disabled={busy} onClick={() => void perform(refresh)}><RefreshCcw size={16} />Tentar novamente</button><button className="button secondary" onClick={() => navigate("#/exploracoes")}><ArrowLeft size={16} />Voltar para Meus testes</button></div></section>;
  const flows = suite.functional_flows || [];
  const activeFlow = ["all", "unclassified"].includes(selectedFlow) || flows.some(flow => flow.id === selectedFlow) ? selectedFlow : "all";
  const archivedCount = suite.cases.filter(item => item.archived).length;
  const baseCases = suite.cases.filter(item => (filter === "archived" ? !!item.archived : !item.archived) && `${item.id} ${item.title} ${flowMemberships(item).map(member => member.name).join(" ")}`.toLocaleLowerCase("pt-BR").includes(query.toLocaleLowerCase("pt-BR")) && (filter === "all" || filter === "archived" || item.status === filter || item.mode === filter));
  const cases = baseCases.filter(item => matchesFunctionalFlow(item, activeFlow));
  const selectionPositions = new Map(selection.map((item, index) => [item.case_id, index]));
  const selectorPositions = new Map(selectorOrder.map((id, index) => [id, index]));
  const selectorCases = [...cases].sort((a, b) => (selectorPositions.get(a.id) ?? Infinity) - (selectorPositions.get(b.id) ?? Infinity));
  const startSelectorPointer = (event: React.MouseEvent, id: string) => {
    if (event.button !== 0 || busy || chatBusy) return;
    event.preventDefault();
    const startX = event.clientX, startY = event.clientY;
    let moved = false;
    let destination: { id: string; side: "before" | "after" } | null = null;
    const move = (pointer: MouseEvent) => {
      if (!moved && Math.hypot(pointer.clientX - startX, pointer.clientY - startY) < 5) return;
      moved = true; suppressSelectorClick.current = true;
      setDraggedCaseId(id);
      const target = document.elementFromPoint(pointer.clientX, pointer.clientY)?.closest<HTMLElement>("[data-selector-case]");
      if (!target) return;
      const bounds = target.getBoundingClientRect();
      destination = { id: target.dataset.selectorCase!, side: pointer.clientY < bounds.top + bounds.height / 2 ? "before" : "after" };
      setCaseDrop(destination);
      const list = target.closest(".suite-available-list");
      if (list) { const box = list.getBoundingClientRect(); if (pointer.clientY < box.top + 44) list.scrollTop -= 24; else if (pointer.clientY > box.bottom - 44) list.scrollTop += 24; }
    };
    const up = () => {
      document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
      endCaseDrag();
      if (moved && destination && destination.id !== id) {
        if (selectionPositions.has(id) && selectionPositions.has(destination.id)) {
          const next = placeRailItem(selection, selectionPositions.get(id)!, selectionPositions.get(destination.id)! + (destination.side === "after" ? 1 : 0));
          const ids = selectorCases.map(item => item.id).filter(value => value !== id);
          ids.splice(ids.indexOf(destination.id) + (destination.side === "after" ? 1 : 0), 0, id);
          setSelection(next); setSelectorOrder(ids); change();
        } else {
          const ids = selectorCases.map(item => item.id).filter(value => value !== id);
          ids.splice(ids.indexOf(destination.id) + (destination.side === "after" ? 1 : 0), 0, id); setSelectorOrder(ids);
        }
      }
      setTimeout(() => { suppressSelectorClick.current = false; }, 0);
    };
    document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
  };
  const startSelectorDrag = (event: DragEvent, id: string) => {
    if (busy || chatBusy) { event.preventDefault(); return; }
    event.stopPropagation();
    event.dataTransfer.setData(caseMime, JSON.stringify({ rootId, caseId: id }));
    event.dataTransfer.effectAllowed = "move";
    draggingCase.current = id; setDraggedCaseId(id); setCaseDrop(null);
  };
  const dropSelectorCase = (event: DragEvent<HTMLElement>, targetId: string) => {
    if (!acceptsCaseDrag(event)) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const side = event.clientY < bounds.top + bounds.height / 2 ? "before" : "after";
    const source = draggingCase.current;
    if (source && selectionPositions.has(source) && selectionPositions.has(targetId)) {
      const ids = selectorCases.map(item => item.id).filter(id => id !== source);
      ids.splice(ids.indexOf(targetId) + (side === "after" ? 1 : 0), 0, source);
      const payload = event.dataTransfer.getData(caseMime);
      dropCase(event, targetId, side);
      try { const data = JSON.parse(payload); if (data.rootId === rootId && data.caseId === source) setSelectorOrder(ids); } catch { /* Ignore foreign drag data. */ }
      return;
    }
    event.preventDefault(); event.stopPropagation(); endCaseDrag();
    if (!source || busy || chatBusy) return;
    try {
      const payload = JSON.parse(event.dataTransfer.getData(caseMime));
      if (payload.rootId !== rootId || payload.caseId !== source || !selectorCases.some(item => item.id === source)) return;
      const ids = selectorCases.map(item => item.id).filter(id => id !== source);
      const target = ids.indexOf(targetId);
      if (target < 0) return;
      ids.splice(target + (side === "after" ? 1 : 0), 0, source);
      setSelectorOrder(ids);
    } catch { /* Ignore foreign drag data. */ }
  };
  const selectedIds = new Set(selection.map(item => item.case_id));
  const remainingFilteredIds = selectorCases.filter(item => !item.archived && !selectedIds.has(item.id)).map(item => item.id);
  const suggestedAdditions = [...new Set(suggestion?.selected_case_ids || [])].filter(id => suite.cases.some(item => item.id === id && !item.archived) && !selection.some(item => item.case_id === id));
  const focusItem = selection.find(item => item.case_id === selectedCase);
  const buildingSelected = !!construction && constructionSelected;
  const constructionDefinition = recording ? { ...emptyDefinition(recording.url), title: "Teste gravado", steps: recording.steps } : (!editId && form) || construction?.definition;
  const constructionSteps = constructionDefinition?.steps.filter(step => step.trim()) || [];
  const constructionStatus = chatBusy ? "Atualizando com IA…" : recording ? "Gravando navegação" : form && !editId ? "Revise antes de salvar" : chatError ? "Confira o aviso no chat" : constructionDefinition ? `${constructionSteps.length} ${constructionSteps.length === 1 ? "etapa definida" : "etapas definidas"}` : construction?.goal ? "Aguardando sugestão" : creationMode === "ai" ? "Descreva no assistente" : creationMode === "record" ? "Aguardando gravação" : "Escolha como começar";
  const currentCase = suite.cases.find(item => item.id === selectedCase);
  const focusCase = currentCase && reference?.case_id === selectedCase && reference.source_id === focusItem?.source_exploration_id ? { ...currentCase, ...reference.value } : currentCase;
  const focusSteps = focusCase?.recorded_steps?.length ? focusCase.recorded_steps.map(step => step.title) : focusCase?.steps || [];
  const stepContext = JSON.stringify([selectedCase, focusItem?.source_exploration_id || focusCase?.source_exploration_id, focusItem?.source_scenario_id || focusCase?.source_scenario_id, focusCase?.revision]);
  const viewedIndex = viewedStep?.context === stepContext && viewedStep.index < focusSteps.length ? viewedStep.index : null;
  const viewedImage = viewedIndex !== null ? stepImageUrl(props.apiBase, focusCase?.recorded_steps?.[viewedIndex]) : null;
  const originalStepCard = (index: number) => <button type="button" className="suite-source-step" data-original-step={index} aria-label={`Ver imagem da etapa ${index + 1}: ${focusSteps[index]}`} aria-expanded={viewedIndex === index} aria-controls="suite-step-image" title={focusSteps[index]} onClick={() => { if (!draggingStep) setViewedStep(viewedIndex === index ? null : { context: stepContext, index }); }}><small>ETAPA {String(index + 1).padStart(2, "0")}<Image size={13} aria-hidden="true" /></small><strong>{focusSteps[index]}</strong><span>{focusCase?.mode === "manual" ? "Manual" : "Original · protegida"}</span></button>;
  const imagePreview = viewedIndex !== null && <StepEvidencePreview key={JSON.stringify([stepContext, viewedIndex, viewedImage])} title={focusSteps[viewedIndex]} index={viewedIndex} total={focusSteps.length} src={viewedImage} onClose={() => { setViewedStep(null); stepsRail.current?.querySelector?.<HTMLButtonElement>(`[data-original-step="${viewedIndex}"]`)?.focus({ preventScroll: true }); }} />;
  const additions = plannedSteps(focusItem?.capture_plan?.checkpoints, focusItem?.planned_integrations);
  const hasPlannedIntegrations = additions.some(point => point.kind !== "capture");
  const pendingIntegrations = selection.reduce((total, item) => total + (item.planned_integrations?.length || 0), 0);
  const manualItem = round?.items.find(item => item.state === "waiting_manual");
  const pending = round?.items.filter(item => !item.result).map(item => item.case_id) || [];
  const archivedSelection = selection.filter(item => !suite.cases.some(value => value.id === item.case_id && !value.archived));
  const roundCount = round?.count ?? round?.items.length ?? 0;
  const finishedCount = round?.finished_count ?? round?.items.filter(item => !!item.result).length ?? 0;
  const reviewing = !!validation;
  const planningActions = !validation && <><button className="button secondary" disabled={busy || formChanged || !!recording || !!construction || !selection.length || !name.trim()} onClick={() => void perform(async () => { await save(); })}><Save size={16} />Salvar rascunho</button><button className="button primary" disabled={busy || formChanged || !!recording || !!construction || !selection.length || !name.trim() || !!archivedSelection.length} onClick={() => void review()}>{busy ? <LoaderCircle className="spin" size={16} /> : <ClipboardList size={16} />}Revisar execução</button></>;
  const planningStatus = !validation && <div className="suite-planning-status sr-only"><span role="status">{construction ? "Novo teste em construção · revise ou descarte para continuar. " : formChanged || recording ? "Salve ou finalize o novo teste antes de revisar. " : ""}{selection.length} {selection.length === 1 ? "teste" : "testes"} · {pendingIntegrations > 0 && <strong className="suite-integration-warning">{pendingIntegrations} SWS/SQL só no rascunho · </strong>}{dirty ? "não salvo" : saved ? "salvo em Rascunhos" : "nenhuma execução iniciada"}{saved && !dirty && <> · <button type="button" className="suite-draft-link text-button" disabled={busy} onClick={() => navigate("#/execucoes-planejadas")}>Ver rascunhos<ArrowRight size={13} /></button></>}</span></div>;
  const header = <>
    {!reviewing && <header className="suite-detail-header">
      <div className="suite-detail-topline">
        <div className="suite-detail-identity">
          <button type="button" className="suite-detail-back" aria-label="Voltar para Meus testes" title="Voltar para Meus testes" onClick={() => navigate("#/exploracoes")}><ArrowLeft size={18} /></button>
          <h1 title={host(suite.app_url)}>{host(suite.app_url)}</h1>
          <span className="suite-detail-count" title="Testes ativos na base">{suite.cases.length - archivedCount} {suite.cases.length - archivedCount === 1 ? "teste" : "testes"}</span>
        </div>
        <div className="suite-detail-actions">
          <details ref={contextDetails} className="suite-context-details">
            <summary title="Sobre o conjunto"><Info size={17} /><span className="sr-only">Sobre o conjunto</span></summary>
            <div className="suite-context-content">
              <h2>Sobre o conjunto</h2>
              <dl>
                <div><dt>ID</dt><dd>{suite.id}</dd></div>
                {suite.created_at && <div><dt>Iniciado em</dt><dd>{date(suite.created_at)}</dd></div>}
                <div><dt>CTs iniciais</dt><dd>{suite.initial_count}</dd></div>
                <div><dt>CTs no catálogo</dt><dd>{suite.cases.length}</dd></div>
              </dl>
              <p>O consolidado reúne resultados de datas e rodadas diferentes. Reexecutar um CT não revalida os demais. O histórico é preservado.</p>
            </div>
          </details>
          <button type="button" className="button secondary" aria-label="Abrir relatório consolidado" title="Abrir relatório consolidado" disabled={busy} onClick={() => void perform(() => openReport(suite.id, "consolidated"))}><Download size={16} /><span>Relatório</span></button>
          {planningActions}
        </div>
      </div>
      {planningStatus}
    </header>}
    {error && <p className="suite-error" role="alert">{error}</p>}
  </>;
  const statusFilter = <select aria-label="Filtrar CTs" value={filter} onChange={event => setFilter(event.target.value)}>{[["all", "Testes ativos"], ["FAIL", "Reprovados"], ["BLOCKED", "Bloqueados"], ["PASS", "Aprovados"], ["manual", "Manuais"]].map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select>;
  const selectorFilterCount = Number(filter !== "all") + Number(activeFlow !== "all");
  const caseForm = form && creationMode === "write" && <form id="suite-case-editor" ref={caseEditor} className="suite-case-form panel" onSubmit={event => { event.preventDefault(); createCase(); }}>
    <div className="suite-section-heading"><h2>{editId ? `Editar ${editId} · nova versão` : "Novo teste manual"}</h2><button type="button" className="icon-button" aria-label="Fechar editor" disabled={busy} onClick={() => transition(closeCaseEditor)}><X size={18} /></button></div>
    <p>{editId ? "Os resultados e prints anteriores ficam preservados. Esta nova versão só terá resultado após ser executada e avaliada." : "Salve o teste na sua base. Ele só terá resultado depois de ser executado e avaliado."}</p>
    <label>Título<input ref={caseTitle} required maxLength={300} placeholder="Ex.: Conferir o carrinho vazio" value={form.title} onChange={event => setForm({ ...form, title: event.target.value })} /></label>
    <label>Passos (um por linha)<textarea required rows={2} placeholder={"Abrir o carrinho\nConferir a mensagem de carrinho vazio"} value={form.steps.join("\n")} onChange={event => setForm({ ...form, steps: event.target.value.split("\n") })} /></label>
    <label>Resultado esperado<textarea required rows={2} placeholder="O que precisa acontecer para este teste passar?" value={form.expected_result} onChange={event => setForm({ ...form, expected_result: event.target.value })} /></label>
    <details className="suite-case-advanced"><summary>Endereço, descrição e pré-condições</summary><div>
      <label>URL da aplicação<input required type="url" value={form.url} onInvalid={event => { const details = event.currentTarget.closest("details"); if (details) details.open = true; }} onChange={event => setForm({ ...form, url: event.target.value })} /></label>
      <label>Descrição<textarea rows={2} value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} /></label>
      <label>Pré-condições (uma por linha)<textarea rows={2} value={form.preconditions.join("\n")} onChange={event => setForm({ ...form, preconditions: event.target.value.split("\n") })} /></label>
    </div></details>
    <div className="suite-editor-actions"><button className="button primary" disabled={busy} type="submit"><Save size={16} />{maintenanceOpen ? "Salvar na base" : "Salvar e adicionar"}</button>{maintenanceOpen && <button type="submit" className="button secondary" disabled={busy} onClick={event => { event.preventDefault(); if (caseEditor.current?.reportValidity()) void createCase(true); }}>Salvar e incluir na execução<ArrowRight size={15} /></button>}</div>
    {!maintenanceOpen && <small className="suite-muted">Também ficará salvo na base. Nenhum teste será iniciado agora.</small>}
    {editId && error && <p className="suite-muted">Seu texto continua aqui. Se outra aba alterou este teste, confira a versão atual na base antes de editar novamente. Você também pode <button type="button" className="text-button" onClick={() => { setEditId(null); setFormBaseline(JSON.stringify(emptyDefinition(suite.app_url))); }}>salvar este texto como outro teste</button>.</p>}
  </form>;
  const creationActions = newMode && <div key={creationAttention} className="planning-chat-creation" role="group" aria-label="Como adicionar o teste">
    <button type="button" disabled={busy || chatBusy} aria-controls="suite-record-confirmation" onClick={() => chooseCreation("record")}><MousePointer2 size={19} /><span><strong>Gravar minha navegação</strong><small>Faça o teste no navegador e revise os passos.</small></span><ArrowRight size={15} /></button>
    <button type="button" disabled={busy || chatBusy} aria-controls="suite-planning-chat" onClick={() => chooseCreation("ai")}><Bot size={19} /><span><strong>Planejar com IA</strong><small>Descreva o objetivo e receba sugestões.</small></span><ArrowRight size={15} /></button>
  </div>;
  const recordingConsent = creationMode === "record" && !recording && <section id="suite-record-confirmation" className="suite-consent-box" aria-label="Confirmar gravação"><h3>Antes de abrir o navegador</h3><p>Será aberto um navegador novo e visível. Suas interações podem alterar dados da aplicação. Não capturamos senhas, valores digitados, cookies ou screenshots. A gravação dura até 15 minutos e gera um roteiro para revisão, não uma automação pronta.</p><button type="button" ref={recordConfirmation} className="button secondary" disabled={busy} onClick={() => void perform(async () => { setRecording(await request<Recording>(`${rootPath}/recordings`, { method: "POST", body: JSON.stringify({ confirm: true }) })); focusCreation("record"); })}><MousePointer2 size={16} />Confirmar e abrir navegador de gravação</button></section>;
  const recordingPanel = recording && <section id="suite-recording" className="panel suite-recording"><span className="suite-record-dot" /><strong>Gravação em andamento · {recording.steps.length} passos</strong><ol>{recording.steps.map((step, index) => <li key={index}>{step}</li>)}</ol><button type="button" ref={recordingFinish} className="button primary" disabled={busy} onClick={() => void perform(async () => { const result = await request<Recording>(`/test-suites/recordings/${encode(recording.id)}/finish`, { method: "POST" }); completeRecording(result); })}><Square size={15} />Finalizar e revisar roteiro</button></section>;

  const reviewNameChanged = !!validation && !!saved && name !== saved.name;
  const startReviewedRound = async (withPrivateInputs = false) => {
    if (!validation || !saved || pendingIntegrations || (withPrivateInputs && missingRoundInputs)) return;
    let checked = validation;
    if (reviewNameChanged || saved.revision !== validation.revision) {
      const plan = reviewNameChanged ? await save() : saved;
      checked = await request<Validation>(`/test-suites/rounds/${encode(plan.id)}/validate`, { method: "POST" });
      // Saving a name can also refresh definitions; authorization covers only the reviewed tests.
      const changed = JSON.stringify(plan.items) !== JSON.stringify(reviewedItems.current)
        || JSON.stringify({ ...checked, revision: 0 }) !== JSON.stringify({ ...validation, revision: 0 });
      reviewedItems.current = plan.items; setValidation(checked);
      if (changed) throw new Error("Os testes ou as condições da execução mudaram. Revise novamente antes de executar.");
    }
    if (!withPrivateInputs && (checked.missing_inputs || checked.required_inputs || []).length) {
      setAuthenticationOpen(true);
      return;
    }
    try {
      const value = await request<Round>(`/test-suites/rounds/${encode(saved.id)}/start`, { method: "POST", body: JSON.stringify({ confirm: true, expected_revision: checked.revision, ...(withPrivateInputs ? { credentials: Object.fromEntries(requiredRoundInputs.map(key => [key, roundCredentials[key]])) } : {}) }) });
      navigate(`#/conjuntos/${encode(suite.id)}?round=${encode(value.id)}`);
    } finally { setRoundCredentials({}); setAuthenticationOpen(false); }
  };
  const executeAction = validation && <button className="button primary" title={!name.trim() ? "Informe o nome da execução" : undefined} disabled={busy || formChanged || !!recording || !name.trim() || !validation.available || (dirty && !reviewNameChanged) || pendingIntegrations > 0} onClick={() => void perform(() => startReviewedRound())}><Play size={16} />Executar {selection.length} {selection.length === 1 ? "teste" : "testes"}</button>;

  const chatPanel = <PlanningChat purpose={maintenanceOpen ? "base" : "execution"} id="suite-planning-chat" composerRef={chatComposer} request={request} defaultModel={suite.assistant_llm} model={chatLlm} onModelChange={setChatLlm}
        messages={messages} busy={chatBusy} value={chatText} onValueChange={setChatText} onSend={ask} error={chatError} creationActions={creationActions} focusRequest={creationFocus?.version}>
        {recordingConsent}{recordingPanel}
        {suggestion && <div className="suite-suggestions">
          {suggestion.coverage && <section className="suite-coverage" aria-label="Comparação do CRUD com a base"><h4>CRUD · {suggestion.coverage.screen}</h4><p>Análise dos testes cadastrados, não comprovação de cobertura completa.</p>{suggestion.coverage.operations.map(operation => <div key={operation.operation} data-coverage-status={operation.status}><header><strong>{{ create: "Criar", read: "Consultar", update: "Editar", delete: "Excluir" }[operation.operation]}</strong><span>{{ existing: "Já existe", gap: "Não encontrado", uncertain: "Precisa conferir", not_applicable: "Não se aplica" }[operation.status]}</span></header><p>{operation.reason}</p>{operation.case_ids.length > 0 && <div className="suite-coverage-existing">{operation.case_ids.map(id => <button type="button" key={id} disabled={selection.some(item => item.case_id === id)} title={suite.cases.find(item => item.id === id)?.title} onClick={() => { const added = add([id]); if (added?.length) setMessages(current => [...current, { role: "assistant", content: caseAdditionReply(added, []) }]); }}>{selection.some(item => item.case_id === id) ? <Check size={12} /> : <Plus size={12} />}{id}</button>)}</div>}{operation.new_case_indexes.map(index => suggestion.new_cases[index] && <button type="button" className="suite-coverage-proposal" key={index} onClick={() => openCaseEditor(suggestion.new_cases[index])}><FilePlus2 size={13} />Revisar: {suggestion.new_cases[index].title}</button>)}</div>)}</section>}
          {suggestedAdditions.length > 0 ? <button className="button secondary" onClick={() => { const added = add(suggestedAdditions); if (added?.length) setMessages(current => [...current, { role: "assistant", content: caseAdditionReply(added, []) }]); }}><Plus size={15} />Incluir na execução: {suggestedAdditions.length} {suggestedAdditions.length === 1 ? "CT sugerido" : "CTs sugeridos"}</button> : suggestion.selected_case_ids.length > 0 && <small>Os CTs sugeridos já estão selecionados para a execução.</small>}
          {suggestion.new_cases.map((value, index) => !suggestion.coverage?.operations.some(operation => operation.new_case_indexes.includes(index)) && <button className="suite-suggested-case" key={index} onClick={() => openCaseEditor(value)}><FilePlus2 size={16} /><span><strong>{value.title}</strong><small>Revisar e adicionar à base</small></span><ChevronRight size={15} /></button>)}
          <small>{suggestion.usage.total_tokens || 0} tokens · {suggestion.usage.cost_known ? `$${suggestion.usage.cost?.toFixed(6)}` : "custo não informado pelo provedor"}</small>
        </div>}
      </PlanningChat>;

  return <div className="view-stack suite-workspace suite-detail-workspace">{header}
    {leavePrompt && <div className="suite-dialog-backdrop"><section ref={leaveDialog} className="panel suite-leave-dialog" role="dialog" aria-modal="true" aria-labelledby="suite-leave-title"><h2 id="suite-leave-title">{recording ? "Há uma gravação em andamento" : "Guardar suas alterações antes de sair?"}</h2><p>{recording ? "Volte ao assistente para finalizar e revisar a gravação. Sair desta tela não encerra o navegador de gravação." : "Você tem alterações não salvas. Salvar não inicia testes; descartar não altera resultados anteriores."}</p><div className="suite-heading-actions"><button type="button" ref={stayButton} className="button primary" disabled={busy} onClick={() => { setLeavePrompt(false); pendingTransition.current = null; }}>Continuar editando</button>{!recording && <button type="button" className="button secondary" disabled={busy || (!formChanged && !selection.length)} onClick={() => void saveBeforeLeaving()}>Salvar e continuar</button>}<button type="button" className="button ghost" disabled={busy || !!recording} onClick={finishTransition}>Descartar e sair</button></div></section></div>}

    {<section className={`suite-planner-view${validation ? " suite-review-mode" : ""}`} data-mobile-pane={validation ? "plan" : mobilePane} aria-label="Planejamento da rodada">
      {validation ? <header className="suite-review-bar"><button type="button" className="text-button" disabled={busy} onClick={returnToScript}><ArrowLeft size={16} />Ajustar</button><div><h1>Revisar execução <span>{selection.length} {selection.length === 1 ? "teste" : "testes"}</span></h1><label className="suite-review-name"><span>Nome da execução</span><input aria-label="Nome da execução" required maxLength={160} disabled={busy} value={name} placeholder="Ex.: Regressão do carrinho" aria-invalid={!name.trim() || undefined} onChange={event => { setName(event.target.value); setDirty(event.target.value !== saved?.name); }} /></label></div>{executeAction}</header> : <>
      <h2 id="suite-planning-heading" ref={planningHeading} tabIndex={-1} className="sr-only" aria-describedby="suite-planning-guidance">Preparar execução</h2>
      <p id="suite-planning-guidance" className="sr-only">{selection.length ? `${selection.length === 1 ? "O teste selecionado está" : `Os ${selection.length} testes selecionados estão`} na ordem abaixo. Ajuste os roteiros e clique em “Revisar execução”. A execução só começa após sua confirmação.` : "Escolha os testes na lista à esquerda ou peça ajuda à IA. Ajuste os roteiros, revise e confirme a execução. Você também pode salvar um rascunho para continuar depois."}</p>
      <nav className="suite-planner-switch" aria-label="Painéis do planejamento"><button type="button" aria-label="Ver roteiro da rodada" aria-pressed={mobilePane === "plan"} onClick={() => setMobilePane("plan")}><ClipboardList size={15} />Roteiro <span>{selection.length}</span></button><button type="button" aria-label="Ver assistente de testes" aria-pressed={mobilePane === "chat"} onClick={showChat}><MessageSquare size={15} />Assistente</button></nav>
      </>}
      <div className={`suite-planner-layout${chatOpen && !validation ? " with-chat" : ""}`}>
      <div className={`suite-planning-main${!validation ? " catalog-open" : ""}`}>
      <aside id="suite-case-selector" className="panel suite-selector" hidden={!!validation} aria-label="Catálogo para selecionar CTs">
        <div ref={selectorFilterTools} className="suite-selector-tools">
          <div className="suite-selector-searchbar"><label className="suite-search"><Search size={15} /><input ref={selectorSearch} aria-label="Buscar CTs" placeholder="Buscar testes…" value={query} onChange={event => setQuery(event.target.value)} /></label><button ref={selectorFilterTrigger} type="button" className="suite-selector-filter-toggle" aria-label="Filtros dos testes" aria-expanded={selectorFiltersVisible} aria-controls="suite-selector-filters" title={selectorFilterCount ? `Filtros · ${selectorFilterCount} ${selectorFilterCount === 1 ? "ativo" : "ativos"}` : "Filtrar testes"} data-active={selectorFilterCount > 0} onClick={() => setSelectorFiltersOpen(value => !value)}><SlidersHorizontal size={16} />{selectorFilterCount > 0 && <span aria-hidden="true">{selectorFilterCount}</span>}</button></div>
          {selectorFiltersVisible && <div ref={selectorFilterPanel} id="suite-selector-filters" className="suite-selector-filters" role="group" aria-label="Opções de filtro"><strong>Filtrar testes</strong><label>Resultado ou tipo{statusFilter}</label><FunctionalFlows compact flows={flows} cases={baseCases} selected={activeFlow} onSelect={setSelectedFlow} /><div className="suite-selector-filter-actions"><button type="button" className="text-button" disabled={!selectorFilterCount} onClick={() => { setFilter("all"); setSelectedFlow("all"); }}>Limpar filtros</button><span role="status">{cases.length} {cases.length === 1 ? "teste" : "testes"}</span></div><button type="button" className="button secondary" disabled={busy || !remainingFilteredIds.length} onClick={() => { add(remainingFilteredIds); setSelectorFiltersOpen(false); selectorSearch.current?.focus({ preventScroll: true }); }}><Plus size={14} />Adicionar filtrados ({cases.length})</button></div>}
        </div>
        <div className="suite-available-list" onDragOverCapture={event => { if (!acceptsCaseDrag(event)) return; const list = event.currentTarget, bounds = list.getBoundingClientRect(); if (event.clientY < bounds.top + 44) list.scrollTop -= 24; else if (event.clientY > bounds.bottom - 44) list.scrollTop += 24; }}>{selectorCases.map(item => <div className={`${selectedCase === item.id ? "selected" : ""}${draggedCaseId === item.id ? " is-dragging" : ""}`} key={item.id} data-selector-case={item.id} data-drop-side={caseDrop?.id === item.id ? caseDrop.side : undefined}
          onDragOver={event => { if (!acceptsCaseDrag(event)) return; event.preventDefault(); event.stopPropagation(); const bounds = event.currentTarget.getBoundingClientRect(); setCaseDrop({ id: item.id, side: event.clientY < bounds.top + bounds.height / 2 ? "before" : "after" }); }}
          onDrop={event => dropSelectorCase(event, item.id)}><input aria-label={`Incluir ${item.id}`} type="checkbox" checked={selection.some(value => value.case_id === item.id)} onChange={() => toggle(item.id)} />{selectionPositions.has(item.id) && <span className="suite-selector-position" aria-label={`${(selectionPositions.get(item.id) ?? 0) + 1}º na execução`}>{(selectionPositions.get(item.id) ?? 0) + 1}</span>}<button type="button" draggable={!busy && !chatBusy}
          aria-label={selectionPositions.has(item.id) ? `${(selectionPositions.get(item.id) ?? 0) + 1}º na execução: ${item.id} · ${item.title}` : `Selecionar ${item.id}: ${item.title}`}
          onMouseDown={event => startSelectorPointer(event, item.id)} onDragStart={event => startSelectorDrag(event, item.id)} onDragEnd={endCaseDrag}
          onKeyDown={event => { if (event.altKey && (event.key === "ArrowUp" || event.key === "ArrowDown") && selectionPositions.has(item.id) && !busy && !chatBusy) { event.preventDefault(); setSelection(current => moveRailItem(current, selectionPositions.get(item.id)!, event.key === "ArrowUp" ? -1 : 1)); change(); } }}
          onClick={() => { if (draggingCase.current || suppressSelectorClick.current) return; setConstructionSelected(false); if (selectedCase !== item.id) setViewedStep(null); if (!selection.some(value => value.case_id === item.id)) add([item.id]); setSelectedCase(item.id); }}>
          <small>{item.id} · {item.mode === "manual" ? "Manual" : "Automático"}</small><strong>{item.title}</strong><span className="case-flow-label" title={flowMemberships(item).map(member => member.reason).join(" | ")}>{caseFlowLabel(item)}</span><Status value={item.status} /></button><button type="button" className="icon-button" disabled={busy} aria-label={`Duplicar ${item.id} como novo CT manual`} onClick={() => openCaseEditor({ ...item, title: `${item.title} · cópia` })}><FilePlus2 size={14} /></button>{item.mode === "manual" && <button type="button" className="icon-button" disabled={busy} aria-label={`Editar ${item.id}`} onClick={() => { setMaintenanceOpen(true); openCaseEditor(item, item.id); }}><SlidersHorizontal size={14} /></button>}</div>)}</div>{!cases.length && <p className="suite-muted" role="status">Nenhum CT corresponde aos filtros.</p>}
        <div className="suite-selector-footer"><button type="button" className="button primary" disabled={busy} onClick={openMaintenance}><Plus size={16} />Adicionar teste</button></div>
      </aside>
      <div ref={planCenter} className="suite-plan-center" role="region" aria-label="Roteiro da rodada">{!validation && <>{caseForm}{archivedSelection.length > 0 && <section className="panel suite-archived-selection" role="alert"><strong>{archivedSelection.map(item => item.case_id).join(", ")}{archivedSelection.some(item => !suite.cases.some(value => value.id === item.case_id)) ? " não está disponível nesta base." : " está arquivado."}</strong><p>Confira a base para restaurar um arquivado ou retire os indisponíveis desta execução antes de continuar.</p><button type="button" className="button secondary" onClick={() => { const ids = new Set(archivedSelection.map(item => item.case_id)); setSelection(current => current.filter(item => !ids.has(item.case_id))); if (selectedCase && ids.has(selectedCase)) setSelectedCase(null); change(); }}>Retirar indisponíveis desta execução</button></section>}<div className="suite-flow-board">
          <span className="sr-only" role="status" aria-live="polite">{caseOrderNotice}</span>
          <section className={`panel suite-case-plan suite-flow-lane${draggingStep ? " is-dragging-step" : ""}`} data-preview-open={!buildingSelected && viewedIndex !== null} aria-label={buildingSelected ? "Etapas do teste em construção" : focusCase ? `Etapas de ${focusCase.id}` : "Etapas do teste"}>
            {construction && !buildingSelected && <div className="suite-construction-heading" data-construction-card data-version={construction.updates}><button type="button" className="text-button" aria-label="Ver teste em construção" onClick={() => setConstructionSelected(true)}>Em construção · {constructionDefinition?.title || "Novo teste"}</button><span role="status">{suggestion && suggestion.new_cases.length > 1 && !form ? `${suggestion.new_cases.length} propostas no chat` : constructionStatus}</span><button type="button" className="icon-button" aria-label="Descartar teste em construção" disabled={busy || chatBusy || !!recording} onClick={discardConstruction}><X size={13} /></button></div>}{buildingSelected ? <div className="suite-construction-plan" data-construction-card data-version={construction?.updates}>
              <div className="suite-construction-heading"><div><small>Em construção · ainda não salvo</small><strong>{constructionDefinition?.title || "Novo teste"}</strong></div><span role="status">{constructionStatus}</span><button type="button" className="icon-button" aria-label="Descartar teste em construção" disabled={busy || chatBusy || !!recording} onClick={discardConstruction}><X size={13} /></button>
                <button type="button" className="button ghost" onClick={showChat}><MessageSquare size={14} />Continuar no assistente</button>
                {constructionDefinition && <button type="button" className="button secondary" disabled={busy || chatBusy || !!recording} onClick={() => { if (form && !editId) { setMobilePane("plan"); focusCreation("write"); } else openCaseEditor(constructionDefinition); }}><FilePlus2 size={14} />Revisar e salvar</button>}
              </div>
              {constructionSteps.length ? <div className="suite-step-canvas">
                {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- Proposed stages retain independent keyboard scrolling. */}
                <div ref={stepsRail} className="suite-step-strip suite-horizontal-rail" role="region" aria-label="Etapas propostas, rolagem horizontal" tabIndex={0}>{constructionSteps.map((step, index) => <article className="suite-source-step suite-construction-step" key={index}><small>PROPOSTA {String(index + 1).padStart(2, "0")}</small><strong>{step}</strong><span>Ainda não executada</span></article>)}</div></div>
                : <div className="suite-construction-empty"><Bot size={24} aria-hidden="true" /><strong>{construction?.goal || "Descreva o teste no assistente"}</strong><span>{suggestion?.new_cases.length ? "Escolha uma das propostas no chat para revisar o roteiro." : "As etapas vão aparecer aqui conforme o teste for definido."}</span></div>}
              {constructionDefinition?.expected_result && <p className="suite-construction-expected"><strong>Esperado:</strong> {constructionDefinition.expected_result}</p>}
            </div> : <>
            {!focusCase && <p className="suite-lane-empty">Clique num teste acima para ver suas etapas.</p>}
            {focusCase?.mode === "automatic" && <div className="suite-step-editor">
              <aside className="suite-step-palette" aria-label="Adicionar etapas ao roteiro">{viewedIndex === null && <span>Adicionar etapa</span>}{stepTools.map(({ kind, title, note, Icon }) => <button key={kind} type="button" draggable className={`suite-step-tool tool-${kind}`} aria-label={`Selecionar ${title}`} aria-pressed={stepTool === kind} onClick={() => { setStepTool(kind); setStepNotice(`${title} selecionado. Clique em + no local desejado.${kind === "capture" ? "" : " Integração ainda indisponível."}`); }} onDragStart={event => beginStepDrag(event, kind)} onDragEnd={endStepDrag}><Icon size={17} /><strong>{title}</strong><small>{note}</small><GripVertical size={13} className="suite-tool-grip" /></button>)}<small className="suite-palette-help">Arraste entre as etapas ou selecione e clique em +.</small></aside>
              <div className="suite-step-canvas" data-step-canvas onDragEnter={overStepCanvas} onDragOver={overStepCanvas} onDrop={dropStep} onDragLeave={event => {
                if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
                const box = event.currentTarget.getBoundingClientRect();
                if (event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom) return;
                hoveredStepSlot.current = null; setDropSlot(null);
              }}>
              {(viewedIndex === null || hasPlannedIntegrations) && <p className="suite-step-hint">{hasPlannedIntegrations ? "SWS/SQL só no rascunho · remova essas etapas para executar." : "Arraste entre as etapas. Use + para inserir por clique."}</p>}
            {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- The rail supports independent keyboard scrolling. */}
            <div ref={stepsRail} className="suite-step-strip suite-horizontal-rail" role="region" aria-label={`Etapas de ${focusCase.id}, rolagem horizontal`} tabIndex={0} onDragOverCapture={event => {
              if (!event.dataTransfer.types.includes(stepMime)) return;
              event.preventDefault(); const rail = event.currentTarget, box = rail.getBoundingClientRect();
              if (event.clientX < box.left + 48) rail.scrollLeft -= 24;
              else if (event.clientX > box.right - 48) rail.scrollLeft += 24;
            }}>
            {focusItem && Array.from({ length: focusSteps.length + 1 }, (_, gap) => {
              const points = additions.filter(point => point.gap === gap);
              return <div className="suite-step-unit" key={gap}>
                <div className="suite-gap">{stepSlot(gap, 0)}{points.map((point, index) => <Fragment key={point.id}>
                  <article className={`suite-source-step suite-print-card suite-added-step kind-${point.kind}${draggingStep && stepPreview?.movingId === point.id ? " is-moving-step" : ""}`} data-checkpoint-id={point.id} draggable onDragStart={event => beginStepDrag(event, point.kind, point.id)} onDragEnd={endStepDrag}>
                    <small><GripVertical size={13} />{point.kind === "capture" ? "PAUSA" : "PLANEJADO"}</small><strong>{stepTitle(point.kind)}</strong><span>{point.kind === "capture" ? "Anexar imagem" : "Integração indisponível"}</span>
                    <div className="suite-added-actions"><button type="button" title="Mover etapa para antes" aria-label={`Mover ${stepTitle(point.kind)} ${point.id} para esquerda`} disabled={!gap && !index} onClick={() => placeStep(point.kind, index ? gap : gap - 1, index ? index - 1 : additions.filter(value => value.gap === gap - 1).length, point.id)}><ArrowLeft size={13} /></button><button type="button" title="Mover etapa para depois" aria-label={`Mover ${stepTitle(point.kind)} ${point.id} para direita`} disabled={gap === focusSteps.length && index === points.length - 1} onClick={() => placeStep(point.kind, index < points.length - 1 ? gap : gap + 1, index < points.length - 1 ? index + 2 : 0, point.id)}><ArrowRight size={13} /></button><button type="button" aria-label={point.kind === "capture" ? "Remover pausa" : `Remover ${stepTitle(point.kind)} ${point.id}`} title={`Remover ${stepTitle(point.kind)}`} onClick={() => removeStep(point)}><X size={14} /></button></div>
                  </article>
                  {stepSlot(gap, index + 1, point.id)}
                </Fragment>)}</div>
                {gap < focusSteps.length && originalStepCard(gap)}
              </div>;
            })}
            </div>
              </div>
              {imagePreview}
            </div>}
            {focusCase?.mode === "manual" && <div className="suite-step-canvas"><div ref={stepsRail} className="suite-step-strip suite-horizontal-rail" role="region" aria-label={`Etapas de ${focusCase.id}, rolagem horizontal`}>{focusSteps.map((_, index) => <Fragment key={index}>{originalStepCard(index)}</Fragment>)}</div>{imagePreview}</div>}
            <span className="sr-only" role="status" aria-live="polite">{stepNotice}</span>
            {draggingStep && stepPreview?.caseId === selectedCase && <StepDragOverlay preview={stepPreview} hidden={dropSlot !== null} onCancel={endStepDrag} />}
            </>}
          </section>
        </div>
        </>}
        {authenticationOpen && <dialog ref={node => { if (node && !node.open) node.showModal(); }} aria-labelledby="suite-authentication-title" onCancel={() => { setAuthenticationOpen(false); setRoundCredentials({}); }}><form onSubmit={event => { event.preventDefault(); void perform(() => startReviewedRound(true)); }}><h2 id="suite-authentication-title">Autenticação necessária</h2><p>Estes testes precisam de dados privados para entrar no site. O cofre desta execução está vazio. Os valores serão usados somente nesta rodada, sem salvar no roteiro ou histórico.</p>{requiredRoundInputs.map((key, index) => <label key={key} htmlFor={`suite-private-${index}`}>{key}<input id={`suite-private-${index}`} type="password" autoComplete="off" required maxLength={8000} disabled={busy} value={roundCredentials[key] || ""} onChange={event => setRoundCredentials(current => ({ ...current, [key]: event.target.value }))} /></label>)}<button type="button" className="button ghost" disabled={busy} onClick={() => { setAuthenticationOpen(false); setRoundCredentials({}); }}>Cancelar</button><button type="submit" className="button primary" disabled={busy || missingRoundInputs}>Autenticar e executar</button></form></dialog>}
        {validation && <section id="suite-plan-review" ref={reviewPanel} tabIndex={-1} aria-label="Revisão antes de executar" className="panel suite-review"><div className="suite-review-scope"><p>Somente estes testes serão executados. Os demais resultados não mudam.</p><details><summary>Cuidados da execução</summary>{validation.effects.map(effect => <p key={effect}>{effect}</p>)}</details></div><div className="suite-review-list" role="list" aria-label="Testes que serão executados">{validation.items.map(item => <div className="suite-review-case" role="listitem" key={item.case_id}><strong>{item.case_id}</strong><span className="suite-review-identity" title={suite.cases.find(value => value.id === item.case_id)?.title}>{suite.cases.find(value => value.id === item.case_id)?.title || item.case_id}{!item.available && item.code !== "checking" && <small>{item.reason || "Indisponível"}</small>}</span><span className="suite-review-availability">{item.available ? <><Check size={14} /><small>{item.code === "manual" ? "Manual" : "Pronto"}</small></> : item.code === "checking" ? <><LoaderCircle className="spin" size={14} /><small>Conferindo roteiro…</small></> : <small>Indisponível</small>}</span></div>)}</div></section>}
      </div>

      </div>
      {chatOpen && !validation && chatPanel}
      </div>
    </section>}
      {round && <section className="panel suite-round-detail" aria-label="Acompanhamento da execução"><div className="suite-section-heading"><div><h2>{round.name}</h2><small className="suite-muted">{date(round.created_at)} · {finishedCount} de {roundCount} {roundCount === 1 ? "teste" : "testes"} com resultado</small></div><Status value={round.status} /></div><div className="suite-heading-actions"><button className="button secondary" onClick={() => void perform(() => openReport(suite.id, "round", round.id))}><Download size={16} />Relatório</button>{active(round.status) && <button className="button ghost" disabled={busy || round.status === "stopping"} onClick={() => void perform(async () => { setRound(await request<Round>(`/test-suites/rounds/${encode(round.id)}/stop`, { method: "POST" })); })}><Square size={15} />Parar execução</button>}<button className="button secondary" onClick={() => { setSaved(null); setName(round.name + " · nova execução"); setSelection(round.items.map(item => ({ case_id: item.case_id, source_exploration_id: item.source_exploration_id, source_scenario_id: item.source_scenario_id, capture_plan: item.capture_plan || undefined, planned_integrations: item.planned_integrations || undefined }))); change(); showPlanning(); setSelectedCase(round.items[0]?.case_id || null); }}>Testar novamente<ArrowRight size={15} /></button></div><p className="suite-muted suite-round-scope">Esta execução inclui somente os {roundCount} testes abaixo, não todos os testes da aplicação.</p>{round.reason && <p className="suite-notice">{round.reason}</p>}{pending.length > 0 && !active(round.status) && <button className="text-button" onClick={() => { setSaved(null); setName("Retomar testes pendentes"); setSelection(round.items.filter(item => pending.includes(item.case_id)).map(item => ({ case_id: item.case_id, source_exploration_id: item.source_exploration_id, source_scenario_id: item.source_scenario_id, capture_plan: item.capture_plan || undefined, planned_integrations: item.planned_integrations || undefined }))); change(); showPlanning(); setSelectedCase(pending[0] || null); }}>Preparar {pending.length} testes sem resultado<ArrowRight size={15} /></button>}
        <div className="suite-round-cases">{round.items.map((item, index) => <div className="suite-round-case" key={item.case_id}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{item.case_id} · {item.definition.title}</strong><small>{item.mode === "manual" ? "Teste manual" : "Teste automático"}</small>{(item.result?.actual_result || item.reason) && <details className="suite-result-explanation"><summary>O que aconteceu</summary>{item.result && <p>{item.result.actual_result}</p>}{item.reason && <p>{item.reason}</p>}</details>}</div><Status value={item.result?.status || item.state} />{item.execution_id && <button className="button secondary" onClick={() => navigate(`#/exploracoes/${encode(item.execution_id!)}/acompanhar`)}>{item.result ? "Ver execução" : "Acompanhar"}<ArrowRight size={15} /></button>}</div>)}</div>
        {manualItem && <form className="suite-manual-result" onSubmit={event => { event.preventDefault(); void perform(async () => { setRound(await request<Round>(`/test-suites/rounds/${encode(round.id)}/cases/${encode(manualItem.case_id)}/result`, { method: "POST", body: JSON.stringify({ status: manualVerdict, actual_result: manualNote, expected_revision: round.revision }) })); setManualVerdict(""); setManualNote(""); }); }}><h3>Sua vez: {manualItem.case_id} · {manualItem.definition.title}</h3><ol>{manualItem.definition.steps.map((step, index) => <li key={index}>{step}</li>)}</ol><p><strong>Esperado:</strong> {manualItem.definition.expected_result}</p><label>Resultado observado<textarea minLength={3} maxLength={4000} required value={manualNote} onChange={event => setManualNote(event.target.value)} /></label><label>Avaliação<select required value={manualVerdict} onChange={event => setManualVerdict(event.target.value)}><option value="">Escolha após executar e conferir</option>{["PASS", "FAIL", "BLOCKED", "SKIPPED"].map(value => <option key={value} value={value}>{labels[value]}</option>)}</select></label><button className="button primary" disabled={busy || !manualVerdict || manualNote.trim().length < 3}><Check size={16} />Registrar resultado e continuar</button></form>}

      </section>}
  </div>;
}
