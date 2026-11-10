import { suiteExecutionHistory } from "./suite-execution-history";

type Execution = ReturnType<typeof suiteExecutionHistory>[number];

export function reportExportUnavailable(entry: Execution): string | undefined {
  if (entry.status === "draft") return "Rascunho: esta execução ainda não foi iniciada.";
  if (!["completed", "partial", "failed", "cancelled", "interrupted"].includes(entry.status)) return "Aguarde o encerramento da execução para exportar.";
  if (!entry.count) return "Esta execução não possui testes para exportar.";
}

export function executionReportPath(root: string, entry: Execution): string {
  if (entry.kind === "linked") return `/explorations/${encodeURIComponent(entry.id)}/report`;
  const query = new URLSearchParams({ scope: entry.kind === "round" ? "round" : "initial" });
  if (entry.kind === "round") query.set("round_id", entry.id);
  return `/test-suites/${encodeURIComponent(root)}/report?${query}`;
}

type SavePicker = (options: { suggestedName: string; types: { description: string; accept: Record<string, string[]> }[] }) => Promise<{
  createWritable(): Promise<WritableStream<Uint8Array>>;
}>;

export type ExportStage = "picker" | "preparing" | "downloading" | "saving" | "complete" | "browser" | "cancelled" | "error";
export type PreparationProgress = { stage: string; completed: number; total: number | null };
export type ExportProgress = { stage: ExportStage; bytes: number; total?: number; percent?: number; preparation?: PreparationProgress };
type ExportOptions = {
  signal?: AbortSignal;
  onProgress?: (progress: ExportProgress) => void;
  readPreparation?: (signal: AbortSignal) => Promise<PreparationProgress | undefined>;
  cancelPreparation?: () => Promise<unknown>;
};

export function exportProgressLabel(progress: ExportProgress): string {
  const labels: Record<ExportStage, string> = {
    picker: "Aguardando a escolha do arquivo…", preparing: "Preparando o relatório…",
    downloading: "Baixando o relatório…", saving: "Finalizando a gravação do arquivo…", complete: "Relatório salvo.",
    browser: "Download entregue ao navegador. Acompanhe o progresso e cancele pela lista de downloads do navegador.",
    cancelled: "Exportação cancelada.", error: "Não foi possível concluir a exportação.",
  };
  if (progress.stage === "preparing") {
    const preparation = progress.preparation;
    if (preparation?.stage === "rendering") return `Renderizando CTs e imagens: ${preparation.completed} de ${preparation.total}…`;
    if (preparation?.stage === "assembling") return "Montando o HTML…";
    if (preparation?.stage === "queued") return "Aguardando a preparação…";
    if (preparation?.stage === "cache") return "Copiando o relatório em cache…";
  }
  return labels[progress.stage];
}

// Call directly from the click handler: the picker needs transient user activation.
export async function downloadExecutionReport(load: (signal: AbortSignal) => Promise<Response>, url: string, filename: string, options: ExportOptions = {}): Promise<boolean> {
  const signal = options.signal || new AbortController().signal;
  let stage: ExportStage = "picker", bytes = 0, total: number | undefined, percent: number | undefined;
  let preparation: PreparationProgress | undefined;
  const emit = () => options.onProgress?.({ stage, bytes, total, percent, preparation });
  const update = (value: ExportStage) => { stage = value; emit(); };
  const picker = (window as Window & { showSaveFilePicker?: SavePicker }).showSaveFilePicker;
  signal.throwIfAborted();
  if (!picker) {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    try { link.click(); update("browser"); } finally { link.remove(); }
    return true;
  }
  let writable: WritableStream<Uint8Array> | undefined, response: Response | undefined;
  const polling = new AbortController();
  let poll: Promise<void> | undefined;
  const cancel = () => { void options.cancelPreparation?.().catch(() => undefined); };
  signal.addEventListener("abort", cancel, { once: true });
  try {
    update("picker");
    const handle = await picker.call(window, { suggestedName: filename, types: [{ description: "Relatório HTML", accept: { "text/html": [".html"] } }] });
    signal.throwIfAborted();
    update("saving");
    writable = await handle.createWritable();
    signal.throwIfAborted();
    update("preparing");
    const loading = load(signal);
    if (options.readPreparation) poll = (async () => {
      while (!polling.signal.aborted && !signal.aborted) {
        try {
          const value = await options.readPreparation!(polling.signal);
          if (polling.signal.aborted || signal.aborted) break;
          if (value) {
            preparation = value;
            // Phase allocation, not a duration estimate: completed CTs include
            // their embedded images. Transfer and confirmed close own the tail.
            if (value.total && ["rendering", "assembling", "ready"].includes(value.stage)) {
              percent = Math.max(percent ?? 0, 90 * value.completed / value.total);
            }
            emit();
          }
        } catch { /* Telemetry failure must not interrupt the HTML stream. */ }
        await new Promise<void>(resolve => {
          if (polling.signal.aborted) return resolve();
          const done = () => { clearTimeout(timer); polling.signal.removeEventListener("abort", done); resolve(); };
          const timer = setTimeout(done, 400);
          polling.signal.addEventListener("abort", done, { once: true });
        });
      }
    })();
    response = await loading;
    polling.abort();
    signal.throwIfAborted();
    if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) throw new Error("O relatório HTML não está disponível. Tente novamente mais tarde.");
    if (!response.body) throw new Error("O relatório HTML está vazio. Tente novamente mais tarde.");
    const length = Number(response.headers.get("content-length"));
    // Fetch decodes compressed responses; encoded Content-Length is not comparable.
    if (Number.isSafeInteger(length) && length > 0 && !response.headers.get("content-encoding")) total = length;
    percent = Math.max(percent ?? 0, 90);
    update("downloading");
    const counter = new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        bytes += chunk.byteLength;
        if (total !== undefined && bytes > total) total = undefined;
        if (total) percent = Math.max(percent ?? 90, 90 + Math.min(1, bytes / total) * 9);
        emit(); controller.enqueue(chunk);
      },
      flush() { update("saving"); },
    });
    await response.body.pipeThrough(counter).pipeTo(writable, { signal });
    signal.throwIfAborted();
    percent = 100;
    update("complete");
    return true;
  } catch (error) {
    await response?.body?.cancel().catch(() => undefined);
    await writable?.abort(error).catch(() => undefined);
    if (signal.aborted || (error instanceof Error && error.name === "AbortError")) { update("cancelled"); return false; }
    update("error");
    throw error;
  } finally {
    polling.abort();
    signal.removeEventListener("abort", cancel);
    await poll;
  }
}
