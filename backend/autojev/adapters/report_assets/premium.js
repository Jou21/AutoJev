(function () {
  "use strict";

  function initializeReport() {
    const source = document.getElementById("report-data");
    if (!source || !document.body.classList.contains("report-premium")) return;
    let data;
    try { data = JSON.parse(source.textContent || "{}"); } catch (_) { return; }
    // A running backend can still hold the previous Python template while
    // loading fresh assets. Remove its obsolete controls before snapshotting
    // exports, not just from newly rendered HTML.
    ["case-executions-all", "case-history", "case-edit", "result-more", "case-execution-note",
      "case-execution-summary", "case-execution-metadata", "case-compare"].forEach((id) => {
      const control = document.getElementById(id);
      if (control) control.remove();
    });
    // Old cached presentations may still contain the superseded binary groups.
    document.querySelectorAll(".case-focus-panel, .case-focus-badge").forEach((element) => element.remove());
    // Capture a self-contained export before temporary filters or enhancements.
    // App-only controls are removed from the detached copy, leaving the live
    // blob's rerun and download actions intact.
    const originalDocument = (() => {
      if (!document.getElementById("download-report")) return null;
      const exportRoot = document.documentElement.cloneNode(true);
      ["download-report", "case-rerun", "case-rerun-feedback"].forEach((id) => {
        const control = exportRoot.querySelector("#" + id);
        if (control) control.remove();
      });
      const appRuntime = exportRoot.querySelector("#report-app-runtime");
      if (appRuntime) appRuntime.remove();
      return "<!DOCTYPE html>\n" + exportRoot.outerHTML;
    })();
    const cases = Array.isArray(data.cases) ? data.cases : [];
    const functionalFlows = Array.isArray(data.functional_flows) ? data.functional_flows : [];
    const flowsById = new Map(functionalFlows.map((flow) => [flow.id, flow]));
    const membershipsOf = (item) => Array.isArray(item.flow_classification && item.flow_classification.memberships) ? item.flow_classification.memberships : [];
    const byIndex = new Map(cases.map((item) => [String(item.index), item]));
    const byId = new Map(cases.map((item) => [String(item.id), item]));
    const $ = (id) => document.getElementById(id);
    const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
    // A running backend may still hold the previous Python row template while
    // reading this updated runtime from disk. Upgrade its canonical rows too;
    // never require a backend restart just to expand the clickable area.
    all("#case-rows tr[data-case-index]").forEach((row) => {
      const item = byIndex.get(row.dataset.caseIndex);
      if (!item) return;
      row.dataset.openCaseRow = String(item.index);
      row.setAttribute("tabindex", "0");
      row.setAttribute("aria-label", "Abrir resultado de " + String(item.id) + ": " + String(item.title || ""));
    });
    const reportConnection = (() => {
      const runtime = $("report-app-runtime");
      // Only a report explicitly opened by the app in its same-origin blob can
      // hand off to the app. Downloaded/file reports never contact a server.
      if (!runtime || runtime.getAttribute("type") !== "application/json" || window.location.protocol !== "blob:"
        || !window.location.origin || window.location.origin === "null") return null;
      let value;
      try { value = JSON.parse(runtime.textContent || "{}"); } catch { return null; }
      if (!value || typeof value.app_url !== "string" || typeof value.source_exploration_id !== "string"
        || !value.source_exploration_id || value.source_exploration_id !== data.run_id) return null;
      try {
        const url = new URL(value.app_url);
        if (!["http:", "https:"].includes(url.protocol) || url.origin !== window.location.origin
          || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
        let apiUrl = null;
        if (typeof value.api_url === "string") {
          const api = new URL(value.api_url);
          if (["http:", "https:"].includes(api.protocol) && !api.username && !api.password
            && !api.search && !api.hash && (url.protocol !== "https:" || api.protocol === "https:")) {
            apiUrl = api.href.replace(/\/$/, "");
          }
        }
        return { appUrl: url.origin, sourceId: value.source_exploration_id, apiUrl };
      } catch { return null; }
    })();
    // Historical HTML may retain the old toolbar. Only an app-connected blob
    // can use that action; downloaded and offline reports omit it.
    if (!reportConnection) {
      ["case-rerun", "case-rerun-feedback"].forEach((id) => {
        const control = $(id);
        if (control) control.remove();
      });
    }
    const statusLabels = {
      ALL: "Todos os status", PASS: "Aprovados", FAIL: "Reprovados",
      BLOCKED: "Bloqueados", SKIPPED: "Sem validação",
    };
    const verdictLabels = { PASS: "Aprovado", FAIL: "Reprovado", BLOCKED: "Bloqueado", SKIPPED: "Sem validação" };
    const categoryLabels = {
      business: "Asserção de negócio", interaction: "Interação técnica", visual: "Indício visual",
      execution: "Execução da automação", technical: "Verificação técnica", unverified: "Sem validação",
    };
    const views = {
      overview: ["Visão geral", "Resumo da execução e das verificações registradas."],
      tests: ["Testes", "Explore os casos executados, seus resultados e evidências."],
      results: ["Resultado do teste", "Ações, verificações e evidências de um caso executado."],
      journey: ["Jornada observada", "O caminho observado entre as páginas desta execução."],
      evidence: ["Evidências", "Capturas, sinais técnicos e dados preservados durante os testes."],
      timeline: ["Linha do tempo", "Eventos da execução em ordem cronológica."],
      coverage: ["Regras e cobertura", "Verificações observadas e restrições que ainda permanecem."],
      settings: ["Configurações do relatório", "Preferências locais de apresentação; os resultados não são alterados."],
    };
    const normalize = (value) => String(value == null ? "" : value).normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR");
    const text = (element, value) => { if (element) element.textContent = String(value); };
    const button = (label, action, className) => {
      const element = document.createElement("button");
      element.type = "button";
      element.textContent = label;
      if (className) element.className = className;
      element.addEventListener("click", action);
      return element;
    };
    const listen = (id, event, action) => { const element = $(id); if (element) element.addEventListener(event, action); };
    const saveBlob = (blob, filename) => {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      // Keep the URL alive long enough for the browser to start saving the file.
      window.setTimeout(() => URL.revokeObjectURL(url), 30000);
    };
    function isEmbeddedImage(src) { return /^(data:image\/(?:png|jpe?g|webp|gif);base64,|blob:)/i.test(src); }
    function isEvidenceImageSource(src) {
      if (isEmbeddedImage(src)) return true;
      // Only app-connected, server-listed captures may be loaded remotely.
      // Offline HTML never gains arbitrary URL/script navigation privileges.
      if (!reportConnection || !reportConnection.apiUrl || !data.online_preview
        || !Object.hasOwn(data.online_preview.image_sizes || {}, src)) return false;
      try {
        const base = new URL(reportConnection.apiUrl), url = new URL(src);
        const prefix = base.pathname.replace(/\/$/, "") + "/explorations/";
        return url.origin === base.origin && !url.username && !url.password && !url.search && !url.hash
          && url.pathname.startsWith(prefix) && /^[^/]+\/screenshots\/[^/]+$/.test(url.pathname.slice(prefix.length));
      } catch { return false; }
    }
    const downloadFeedback = (control, message) => {
      let status = $("evidence-download-status");
      if (!status) { status = document.createElement("p"); status.id = "evidence-download-status"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); control.parentElement.appendChild(status); }
      text(status, message);
    };
    document.addEventListener("click", async (event) => {
      const control = event.target instanceof Element ? event.target.closest("a.step-evidence-download, #download-evidence") : null;
      if (!control) return;
      const src = control.getAttribute("href") || "";
      if (isEmbeddedImage(src)) return;
      event.preventDefault();
      if (!isEvidenceImageSource(src) || control.getAttribute("aria-busy") === "true") return;
      control.setAttribute("aria-busy", "true");
      try {
        const response = await window.fetch(src, { credentials: "omit", redirect: "error" });
        if (!response.ok) throw new Error("Image unavailable");
        saveBlob(await response.blob(), control.download || "autojev-evidencia.png");
      } catch { downloadFeedback(control, "Não foi possível baixar a imagem. Tente novamente."); }
      finally { control.removeAttribute("aria-busy"); }
    });
    const prefersReducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let activeView = "overview";
    let selectedCase = cases.length ? String(cases[0].index) : null;
    const selectedExecutions = new Map();
    let displayedHistory = null;
    let selectedStatus = "ALL";
    let selectedFlow = "all";
    let pageNumber = 1;
    let filteredCases = cases.slice();
    const sourceScopes = new Map();
    const sourceImages = new Map();
    const caseExplorers = new Map();
    // Drafts never change this report's test results. Only their allowlisted
    // manual-capture checkpoints may be handed to a separate app confirmation.
    // No integration instructions, queries, or credentials leave this report.
    const draftSource = $("report-drafts");
    const savedDrafts = new Map();
    const draftLimit = 100;
    let draftSerial = 0;
    let editorCase = null;
    let editorPlanKey = null;
    let editorRevision = null;
    let editorTool = null;
    let editorSequence = [];
    let editorTrigger = null;
    let editorDrag = null;
    let stepPickerRequest = null;
    const draftKinds = {
      database: { title: "Consulta em banco de dados", symbol: "▤", description: "Exemplo sem conexão, SQL ou credenciais. Não executado." },
      sws: { title: "Executar SWS", symbol: "↗", description: "Etapa de SWS planejada. A integração ainda não está disponível e nada será executado." },
      sql: { title: "Executar SQL", symbol: "▤", description: "Etapa de SQL planejada. A integração ainda não está disponível e nenhuma consulta será executada." },
      wait_print: { title: "Aguardar print", symbol: "▣", description: "Na reexecução, pausa para você colar com Ctrl+V, arrastar ou escolher uma imagem na aplicação AutoJev. Pode ser de qualquer conteúdo. Confira a prévia e clique em ‘Anexar print e continuar’. A imagem fica no novo relatório; nada é capturado automaticamente." },
    };
    const isDraft = (item) => !!item && typeof item === "object" && !Array.isArray(item)
      && typeof item.type === "string" && Object.prototype.hasOwnProperty.call(draftKinds, item.type);
    const draftFields = (item) => {
      const copy = { id: item.id, type: item.type };
      if (item.type === "sws" || item.type === "sql") copy.wait_for_print = item.wait_for_print === true;
      return copy;
    };
    const editorKinds = { action: "Ação realizada", assertion: "Conferência", signal: "Sinal técnico", observation: "Registro da página" };
    const editorStates = { pass: "Passou", fail: "Não passou", unknown: "Sem resultado", recorded: "Captura registrada", info: "Informativo", warning: "Atenção" };

    function plannerKey(key, revision) {
      return revision && !revision.current ? "history:" + key + ":" + revision.version : String(key);
    }

    function originalEditorSteps(key, revision) {
      const detail = revision && !revision.current ? archivedExecution(byIndex.get(String(key)), revision)
        : all("#scenarios details.scenario[data-case-index]").find((item) => item.dataset.caseIndex === String(key));
      const timeline = detail && detail.querySelector(".evidence-timeline");
      const entries = timeline && Array.from(timeline.children).filter((item) => item.classList.contains("timeline-entry"));
      if (entries && entries.length) return entries.map((item) => ({
        title: (item.querySelector("h3") || {}).textContent || "Registro da execução",
        kind: Object.keys(editorKinds).find((kind) => item.classList.contains(kind)) || "observation",
        state: Object.prototype.hasOwnProperty.call(editorStates, item.dataset.stepState) ? item.dataset.stepState : "unknown",
        outcome: Object.prototype.hasOwnProperty.call(editorStates, item.dataset.stepState) && typeof item.dataset.stepOutcome === "string"
          ? item.dataset.stepOutcome.trim() : "",
      }));
      const payload = byIndex.get(key);
      if (revision && !revision.current) return [];
      return Array.isArray(payload && payload.editor_steps) ? payload.editor_steps.slice(0, 5000).filter((item) => item && typeof item === "object").map((item) => ({
        title: typeof item.title === "string" ? item.title : "Registro da execução",
        kind: Object.prototype.hasOwnProperty.call(editorKinds, item.kind) ? item.kind : "observation",
        state: Object.prototype.hasOwnProperty.call(editorStates, item.state) ? item.state : "unknown",
        outcome: Object.prototype.hasOwnProperty.call(editorStates, item.state) && typeof item.outcome === "string" ? item.outcome.trim() : "",
      })) : [];
    }

    function readSavedDrafts() {
      if (!draftSource || draftSource.textContent.length > 2000000) return;
      let value;
      try { value = JSON.parse(draftSource.textContent || "{}"); } catch { return; }
      if (!value || value.version !== 1 || !value.cases || typeof value.cases !== "object" || Array.isArray(value.cases)) return;
      cases.forEach((item) => {
        const contexts = [null, ...executionHistory(item).filter((revision) => !revision.current && revision.has_result)];
        contexts.forEach((revision) => {
          const key = plannerKey(String(item.index), revision);
          if (!Object.prototype.hasOwnProperty.call(value.cases, key) || !Array.isArray(value.cases[key])) return;
          const count = originalEditorSteps(String(item.index), revision).length;
          const ids = new Set();
          const drafts = value.cases[key].slice(0, draftLimit).filter((draft) => {
            if (!isDraft(draft) || typeof draft.id !== "string" || !/^db-\d{1,9}$/.test(draft.id)
              || ids.has(draft.id) || !Number.isInteger(draft.gap) || draft.gap < 0 || draft.gap > count
              || !Number.isInteger(draft.order) || draft.order < 0 || draft.order >= draftLimit) return false;
            ids.add(draft.id);
            draftSerial = Math.max(draftSerial, Number(draft.id.slice(3)));
            return true;
          }).map((draft) => ({ ...draftFields(draft), gap: draft.gap, order: draft.order }))
            .sort((left, right) => left.gap - right.gap || left.order - right.order);
          if (drafts.length) savedDrafts.set(key, drafts);
        });
      });
    }

    function serializeDrafts() {
      const values = Object.create(null);
      savedDrafts.forEach((drafts, key) => { values[key] = drafts; });
      // Even a hostile imported report cannot add closing script tags through a
      // draft. Serialization is allowlisted above and escaped at the boundary.
      return JSON.stringify({ version: 1, cases: values }).replace(/</g, "\\u003c");
    }

    function exportDocument() {
      if (!originalDocument || !draftSource) return originalDocument;
      return originalDocument.replace(/(<script\b(?=[^>]*\bid=["']report-drafts["'])[^>]*>)[\s\S]*?(<\/script\s*>)/i,
        (_match, start, end) => start + serializeDrafts() + end);
    }

    function closeEditor() {
      const dialog = $("case-editor");
      if (!dialog) return;
      if (stepPickerRequest) closeStepPicker();
      editorDrag = null;
      editorTool = null;
      if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open");
      if (editorTrigger) editorTrigger.focus();
    }

    function editorMessage(value) { text($("case-editor-feedback"), value); }

    function closeStepPicker(choice) {
      const request = stepPickerRequest;
      stepPickerRequest = null;
      const dialog = $("step-picker");
      if (dialog) {
        if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open");
      }
      if (request && request.trigger) request.trigger.focus({ preventScroll: true });
      if (request && ["sws", "sql", "wait_print"].includes(choice)) request.insert(choice);
    }

    function openStepPicker(trigger, position, insert, key) {
      const dialog = $("step-picker");
      if (!dialog) return;
      if (stepPickerRequest) closeStepPicker();
      stepPickerRequest = { trigger, insert };
      const current = byIndex.get(String(key));
      text($("step-picker-position"), (current ? current.id + " · " : "") + position + " · Planeje o passo sem alterar o resultado original.");
      if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
      const firstChoice = dialog.querySelector('[data-step-choice="sws"]');
      if (firstChoice) firstChoice.focus();
    }

    function appendDraftOptions(container, item, onChange, editor = false) {
      if (!["sws", "sql"].includes(item.type)) return;
      const options = document.createElement("div");
      options.className = "case-draft-config";
      const label = document.createElement("label");
      label.className = "case-draft-wait-option";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.className = editor ? "case-editor-wait-print" : "case-draft-wait-print";
      input.dataset.draftId = item.id;
      input.checked = item.wait_for_print === true;
      input.setAttribute("aria-label", "Aguardar print · pausar para anexar uma imagem na reexecução");
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = "Aguardar print";
      const description = document.createElement("small");
      description.textContent = "Pausar na reexecução para colar ou anexar uma imagem de sua escolha no AutoJev. A integração SWS ou SQL permanece desativada.";
      copy.append(title, description);
      label.append(input, copy);
      input.addEventListener("change", () => { item.wait_for_print = input.checked; onChange(); });
      const unavailable = document.createElement("div");
      unavailable.className = "case-draft-integration";
      const action = document.createElement("button");
      action.type = "button";
      action.className = "button button-ghost case-draft-disabled-action";
      action.textContent = item.type === "sws" ? "Escolher SWS" : "Executar SQL";
      action.disabled = true;
      const soon = document.createElement("small");
      soon.className = "case-draft-coming-soon";
      soon.textContent = "Em breve · integração indisponível";
      unavailable.append(action, soon);
      options.append(label, unavailable);
      container.appendChild(options);
    }

    function caseCapturePlan(key, revision) {
      // Construct a new minimal object, not a serialization of arbitrary draft
      // data. SWS/SQL can request a print, never execution of an integration.
      const count = originalEditorSteps(String(key), revision).length;
      const ids = new Set();
      const checkpoints = (savedDrafts.get(plannerKey(key, revision)) || []).filter((item) => {
        if (!(item.type === "wait_print" || (["sws", "sql"].includes(item.type) && item.wait_for_print === true))
          || typeof item.id !== "string" || !/^db-\d{1,9}$/.test(item.id) || ids.has(item.id)
          || !Number.isInteger(item.gap) || item.gap < 0 || item.gap > count
          || !Number.isInteger(item.order) || item.order < 0 || item.order >= draftLimit) return false;
        ids.add(item.id);
        return true;
      }).slice(0, draftLimit).map((item) => ({ id: item.id, gap: item.gap, order: item.order }))
        .sort((left, right) => left.gap - right.gap || left.order - right.order);
      return checkpoints.length ? { version: 1, checkpoints } : null;
    }

    function requestCaseRerun(key, feedback) {
      const current = key != null && byIndex.get(String(key));
      if (!reportConnection || !current) {
        feedback("Reexecução não iniciada. Abra o relatório pela aplicação AutoJev para reexecutar um CT e confirmar as pausas para print. Este arquivo não inicia ações nem capturas. SWS e SQL permanecem desativados.");
        return;
      }
      const editor = $("case-editor");
      const revision = editor && editor.hasAttribute("open") && editorCase === String(key) && editorRevision
        ? editorRevision : selectedExecution(current);
      if (editor && editor.hasAttribute("open") && editorCase === String(key)
        && JSON.stringify(draftsInSequence(editorSequence)) !== JSON.stringify(savedDrafts.get(editorPlanKey) || [])) {
        feedback("Guarde o rascunho antes de reexecutar para incluir as pausas para print que você acabou de editar. Nenhuma reexecução foi solicitada.");
        return;
      }
      if (!revision.has_result) { feedback("Esta tentativa não tem resultado registrado. Selecione uma execução com resultado para reexecutar o teste."); return; }
      const plan = caseCapturePlan(key, revision);
      const owner = !revision.current ? revision.exploration_id : typeof current.rerun_source_exploration_id === "string" && current.rerun_source_exploration_id
        ? current.rerun_source_exploration_id : reportConnection.sourceId;
      const scenario = !revision.current ? revision.scenario_id : typeof current.rerun_source_scenario_id === "string" && current.rerun_source_scenario_id
        ? current.rerun_source_scenario_id : String(current.id);
      const target = reportConnection.appUrl + "/#/planejar/" + encodeURIComponent(owner) + "/" + encodeURIComponent(scenario)
        + (plan ? "?capture_plan=" + encodeURIComponent(JSON.stringify(plan)) : "");
      window.open(target, "_blank", "noopener,noreferrer");
      feedback("Planejamento da rodada aberto na aplicação. Você pode incluir outros CTs e conversar com o assistente antes de confirmar. Nenhuma ação foi iniciada por este relatório. "
        + (plan ? plan.checkpoints.length + " pausa(s) para print do rascunho salvo serão apresentadas para confirmação. SWS e SQL não serão executados. Salve alterações do editor antes de reexecutar."
          : "Os passos extras do rascunho não serão executados. Salve alterações do editor antes de reexecutar."));
    }

    function draftsInSequence(sequence) {
      let gap = 0;
      const drafts = [];
      sequence.forEach((item) => {
        if (item.type === "recorded") gap += 1;
        else if (isDraft(item) && drafts.length < draftLimit) {
          drafts.push({ ...draftFields(item), gap, order: drafts.length });
        }
      });
      return drafts;
    }

    function saveDraftSequence(key, sequence) {
      const drafts = draftsInSequence(sequence);
      if (drafts.length) savedDrafts.set(key, drafts); else savedDrafts.delete(key);
      if (draftSource) draftSource.textContent = serializeDrafts();
    }

    function renderEditor(focusId) {
      const list = $("case-editor-steps");
      if (!list) return;
      list.replaceChildren();
      const create = (tag, className, label) => {
        const element = document.createElement(tag);
        element.className = className;
        if (label != null) element.textContent = label;
        return element;
      };
      const placeholderCount = editorSequence.filter(isDraft).length;
      const insert = (position, type) => {
        if (editorSequence.filter(isDraft).length >= draftLimit) { editorMessage("Limite de 100 passos extras por CT."); return; }
        if (!Object.prototype.hasOwnProperty.call(draftKinds, type)) return;
        let id;
        do {
          draftSerial = draftSerial >= 999999999 ? 1 : draftSerial + 1;
          id = "db-" + draftSerial;
        } while (editorSequence.some((item) => isDraft(item) && item.id === id));
        editorSequence.splice(position, 0, draftFields({ type, id }));
        renderEditor(id);
        editorMessage(draftKinds[type].title + " adicionado ao rascunho visual. Nada foi executado.");
      };
      const dropAt = (position, event) => {
        if (!editorDrag) return;
        event.preventDefault();
        const dragged = editorDrag;
        editorDrag = null;
        if (dragged.startsWith("new:")) { insert(position, dragged.slice(4)); return; }
        const previous = editorSequence.findIndex((item) => isDraft(item) && item.id === dragged);
        if (previous < 0) return;
        const [item] = editorSequence.splice(previous, 1);
        editorSequence.splice(position > previous ? position - 1 : position, 0, item);
        renderEditor(item.id);
        editorMessage("Passo extra reposicionado no rascunho visual. Os registros originais foram preservados.");
      };
      const addGap = (position) => {
        const gap = create("li", "case-editor-gap");
        gap.dataset.editorGap = String(position);
        gap.setAttribute("aria-label", position === 0 ? "Antes do primeiro passo" : position === editorSequence.length ? "Depois do último passo" : "Entre os passos " + position + " e " + (position + 1));
        const control = button("+", () => editorTool ? insert(position, editorTool)
          : openStepPicker(control, gap.getAttribute("aria-label"), (type) => insert(position, type), editorCase), "button button-ghost case-editor-insert");
        control.disabled = placeholderCount >= draftLimit;
        control.setAttribute("aria-label", "Adicionar passo: " + gap.getAttribute("aria-label"));
        control.setAttribute("title", "Adicionar passo · clique ou solte um card aqui");
        control.setAttribute("aria-haspopup", "dialog");
        control.setAttribute("aria-controls", "step-picker");
        gap.appendChild(control);
        gap.addEventListener("dragover", (event) => { if (editorDrag) { event.preventDefault(); gap.classList.add("drag-over"); } });
        gap.addEventListener("dragleave", () => gap.classList.remove("drag-over"));
        gap.addEventListener("drop", (event) => dropAt(position, event));
        list.appendChild(gap);
      };
      if (!editorSequence.length) list.appendChild(create("li", "case-editor-empty", "Este CT não tem passos registrados. Você pode planejar passos extras; eles não serão executados."));
      editorSequence.forEach((item, index) => {
        addGap(index);
        const card = create("li", "case-editor-step " + (isDraft(item) ? "case-editor-prototype" : "case-editor-recorded"));
        card.dataset.editorStep = item.type;
        card.appendChild(create("span", "case-planner-order", String(index + 1).padStart(2, "0")));
        if (item.type === "recorded") {
          card.dataset.recordedIndex = String(item.stepIndex);
          card.dataset.stepKind = item.step.kind;
          card.dataset.stepState = item.step.state;
          card.append(create("small", "case-editor-kind", "Registro original · " + editorKinds[item.step.kind]),
            create("h3", "", item.step.title), create("span", "case-editor-state", item.step.outcome || editorStates[item.step.state]),
            create("small", "case-editor-readonly", "Somente leitura · resultado e evidências preservados"));
        } else {
          const metadata = draftKinds[item.type];
          card.dataset.draftId = item.id;
          card.dataset.draftType = item.type;
          card.setAttribute("draggable", "true");
          card.tabIndex = 0;
          card.setAttribute("aria-label", metadata.title + ". Exemplo visual não executado. Use Alt mais seta para cima ou para baixo para mover.");
          card.append(create("small", "case-editor-kind", "Novo passo · rascunho visual"),
            create("h3", "", metadata.title), create("p", "", item.type === "wait_print"
              ? "Pausa para anexar uma imagem à nova execução. Ela só será incluída após sua confirmação na aplicação." : metadata.description));
          appendDraftOptions(card, item, () => editorMessage("Configuração atualizada no editor. Salve o rascunho para mantê-la. Nenhuma captura ou integração foi executada."), true);
          const move = (direction) => {
            const current = editorSequence.findIndex((entry) => isDraft(entry) && entry.id === item.id);
            const next = current + direction;
            if (current < 0 || next < 0 || next >= editorSequence.length) return;
            editorSequence.splice(current, 1);
            editorSequence.splice(next, 0, item);
            renderEditor(item.id);
            editorMessage("Passo extra reposicionado no rascunho visual.");
          };
          const controls = create("div", "case-editor-step-actions");
          const up = button("← Mover antes", () => move(-1), "button button-ghost");
          const down = button("Mover depois →", () => move(1), "button button-ghost");
          up.disabled = index === 0;
          down.disabled = index === editorSequence.length - 1;
          const remove = button("Remover", () => {
            editorSequence = editorSequence.filter((entry) => entry !== item);
            renderEditor();
            editorMessage("Passo extra removido do rascunho. Nenhum registro original foi removido.");
            const gap = list.querySelector(".case-editor-insert");
            if (gap) gap.focus();
          }, "button button-ghost");
          controls.append(up, down, remove);
          card.appendChild(controls);
          card.addEventListener("keydown", (event) => {
            if (event.target !== card || !event.altKey || !["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault();
            move(["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 1);
          });
          card.addEventListener("dragstart", (event) => {
            editorDrag = item.id;
            if (event.dataTransfer) { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", "Passo extra não executado"); }
          });
          card.addEventListener("dragend", () => { editorDrag = null; all(".drag-over", list).forEach((gap) => gap.classList.remove("drag-over")); });
        }
        list.appendChild(card);
      });
      addGap(editorSequence.length);
      text($("case-planner-count"), originalEditorSteps(editorCase, editorRevision).length + " etapas originais · " + placeholderCount + " passos extras");
      if (focusId) {
        const target = all("[data-draft-id]", list).find((item) => item.dataset.draftId === focusId);
        if (target) target.focus();
      }
    }

    function preparePlanner() {
      const dialog = $("case-editor"), palette = $("case-editor-palette");
      if (!dialog || !palette) return;
      dialog.classList.add("case-rerun-planner");
      text(dialog.querySelector(".page-kicker"), "PLANEJAMENTO · NOVA EXECUÇÃO");
      text(dialog.querySelector(".case-editor-notice"), "Planeje sem alterar este resultado. Os cards originais são referências da execução selecionada. As pausas para print serão apresentadas para confirmação na aplicação. SWS e SQL continuam indisponíveis; nenhuma ação começa aqui.");
      text($("case-editor-close"), "Voltar ao resultado");
      if ($("case-editor-close")) $("case-editor-close").setAttribute("aria-label", "Voltar ao resultado sem iniciar a reexecução");
      text($("case-editor-save"), "Guardar planejamento");
      const save = $("case-editor-save"), rerun = $("case-editor-rerun");
      if (save && rerun) {
        save.classList.remove("button-primary"); save.classList.add("button-ghost");
        rerun.classList.remove("button-ghost"); rerun.classList.add("button-primary");
        save.parentElement.append(save, rerun);
      }
      const canvas = dialog.querySelector(".case-editor-canvas");
      if (canvas) {
        text(canvas.querySelector("h3"), "Roteiro da próxima execução");
        text(canvas.querySelector("p"), "Leia da esquerda para a direita. Clique nos quadrados + ou arraste um passo até eles. Os registros originais não podem ser removidos.");
      }
      if (palette.dataset.plannerReady === "true") return;
      palette.dataset.plannerReady = "true";
      palette.replaceChildren();
      const heading = executionNode("h3", "", "Adicionar ao roteiro");
      const hint = executionNode("p", "", "Arraste até um + ou selecione um tipo e clique na posição desejada.");
      palette.append(heading, hint);
      ["wait_print", "sws", "sql"].forEach((type) => {
        const metadata = draftKinds[type];
        const tool = button("", () => {
          editorTool = editorTool === type ? null : type;
          all("[data-step-template]", palette).forEach((item) => item.setAttribute("aria-pressed", String(item.dataset.stepTemplate === editorTool)));
          editorMessage(editorTool ? metadata.title + ": escolha um quadrado + no roteiro." : "Escolha um passo ou clique em um + para ver as opções.");
        }, "case-planner-tool");
        tool.dataset.stepTemplate = type;
        tool.setAttribute("draggable", "true");
        tool.setAttribute("aria-pressed", "false");
        const sourceIcon = document.querySelector('#step-picker [data-step-choice="' + type + '"] .step-picker-icon');
        tool.append(sourceIcon ? sourceIcon.cloneNode(true) : executionNode("span", "case-planner-tool-icon", metadata.symbol),
          executionNode("strong", "", metadata.title), executionNode("small", "", type === "wait_print" ? "Pausa para anexar sua imagem" : "Em breve · integração indisponível"));
        tool.addEventListener("dragstart", (event) => {
          if (!editorCase) return;
          editorDrag = "new:" + type;
          if (event.dataTransfer) { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/plain", "Passo planejado: " + type); }
        });
        tool.addEventListener("dragend", () => { editorDrag = null; all(".drag-over", $("case-editor-steps")).forEach((gap) => gap.classList.remove("drag-over")); });
        palette.appendChild(tool);
      });
      const count = executionNode("small", "case-planner-count");
      count.id = "case-planner-count";
      palette.appendChild(count);
    }

    function openEditor(key, trigger, rerun = false) {
      key = String(key);
      const current = byIndex.get(key);
      const dialog = $("case-editor");
      if (!current || !dialog || !$("case-editor-steps")) return;
      editorCase = key;
      editorRevision = rerun ? selectedExecution(current) : { ...current, current: true, has_result: true };
      if (!editorRevision.has_result) return;
      editorPlanKey = plannerKey(key, editorRevision);
      editorTrigger = trigger;
      editorDrag = null;
      editorTool = null;
      preparePlanner();
      all("[data-step-template]", dialog).forEach((tool) => tool.setAttribute("aria-pressed", "false"));
      const steps = originalEditorSteps(key, editorRevision);
      const drafts = savedDrafts.get(editorPlanKey) || [];
      editorSequence = [];
      for (let gap = 0; gap <= steps.length; gap += 1) {
        drafts.filter((item) => item.gap === gap).forEach((item) => editorSequence.push(draftFields(item)));
        if (gap < steps.length) editorSequence.push({ type: "recorded", stepIndex: gap, step: steps[gap] });
      }
      text($("case-editor-title"), "Planejar reexecução · " + current.id + " · " + (editorRevision.title || current.title || "Caso de teste"));
      text($("case-editor-rerun"), reportConnection ? "Revisar e confirmar na aplicação →" : "Como reexecutar");
      editorMessage("Rascunho visual · não executado · não altera este resultado. " + (rerun && Number.isInteger(editorRevision.version) ? "Base: execução #" + (editorRevision.version + 1) + "." : ""));
      renderEditor();
      if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
      if ($("case-editor-close")) $("case-editor-close").focus();
    }

    readSavedDrafts();

    function closeJourneyPickers(restoreFocus = false) {
      let trigger = null;
      all("[data-journey-page]").forEach((control) => {
        if (control.getAttribute("aria-expanded") === "true") trigger = control;
        control.setAttribute("aria-expanded", "false");
      });
      all(".journey-case-picker").forEach((panel) => { panel.hidden = true; });
      if (restoreFocus && trigger) trigger.focus();
    }

    function filterJourneyCases(panel) {
      const search = panel.querySelector("[data-journey-search]");
      const term = normalize(search ? search.value : "");
      const rows = all(".journey-case-row", panel);
      let visible = 0;
      rows.forEach((row) => {
        row.hidden = !!term && !normalize([row.dataset.search || "", row.textContent].join(" ")).includes(term);
        if (!row.hidden) visible += 1;
      });
      text(panel.querySelector("[data-journey-count]"), term ? visible + " de " + rows.length + " CTs" : rows.length + " CTs nesta página");
      const empty = panel.querySelector("[data-journey-empty]");
      if (empty) empty.hidden = visible > 0;
    }

    // Keep records in discovery order in the DOM. Only their visual grid
    // positions and a decorative, local canvas form the reference's S path.
    function layoutJourneyRoute() {
      const route = document.querySelector(".journey-route");
      const track = route && route.querySelector(".journey-track");
      if (!route || !track || typeof route.getBoundingClientRect !== "function") return;
      const width = route.getBoundingClientRect().width;
      if (!(width > 0)) return;
      const nodes = all(".journey-node[data-journey-position]", track);
      if (!nodes.length) return;
      const columns = width >= 1080 ? 4 : width >= 820 ? 3 : width >= 560 ? 2 : 1;
      route.dataset.journeyColumns = String(columns);
      route.style.setProperty("--journey-columns", String(columns));
      track.style.setProperty("--journey-columns", String(columns));
      // Multi-column terminals need 66px plus a 12px gap and safe outer margin.
      route.style.setProperty("--journey-gutter", (columns === 1 ? 20 : 88) + "px");
      route.style.setProperty("--journey-top", (columns === 1 ? 66 : 36) + "px");
      route.style.setProperty("--journey-bottom", (columns === 1 ? 66 : 42) + "px");
      route.style.setProperty("--journey-row-gap", (columns === 1 ? 44 : 90) + "px");
      nodes.forEach((node, index) => {
        const row = Math.floor(index / columns);
        const column = row % 2 === 0 ? index % columns : columns - 1 - index % columns;
        node.style.setProperty("grid-row", String(row + 1));
        node.style.setProperty("grid-column", String(column + 1));
      });
      const bounds = route.getBoundingClientRect();
      const boxes = nodes.map((node) => {
        const box = node.getBoundingClientRect();
        return { left: box.left - bounds.left, right: box.right - bounds.left,
          top: box.top - bounds.top, bottom: box.bottom - bounds.top,
          cx: box.left + box.width / 2 - bounds.left, cy: box.top + box.height / 2 - bounds.top };
      });
      if (![bounds.width, bounds.height, ...boxes.flatMap((box) => Object.values(box))].every(Number.isFinite)
          || !(bounds.height > 0)) return;
      const first = boxes[0], last = boxes[boxes.length - 1];
      const start = route.querySelector(".journey-terminal-start");
      const end = route.querySelector(".journey-terminal-end");
      const labelWidth = 66, labelHeight = 32;
      const place = (label, x, y) => {
        const position = { x: Math.max(4, Math.min(bounds.width - labelWidth - 4, x)),
          y: Math.max(4, Math.min(bounds.height - labelHeight - 4, y)) };
        if (label) {
          label.style.setProperty("left", position.x + "px");
          label.style.setProperty("top", position.y + "px");
        }
        return position;
      };
      const rightwardEnd = Math.floor((nodes.length - 1) / columns) % 2 === 0;
      const startAt = columns === 1
        ? place(start, first.cx - labelWidth / 2, first.top - labelHeight - 16)
        : place(start, first.left - labelWidth - 12, first.cy - labelHeight / 2);
      const endAt = columns === 1
        ? place(end, last.cx - labelWidth / 2, last.bottom + 16)
        : place(end, rightwardEnd ? last.right + 12 : last.left - labelWidth - 12, last.cy - labelHeight / 2);
      route.dataset.journeyReady = "true";
      const canvas = route.querySelector(".journey-route-canvas");
      if (!canvas || typeof canvas.getContext !== "function") return;
      let context;
      try { context = canvas.getContext("2d"); } catch { return; }
      if (!context) return;
      const ratio = Math.min(2, Math.max(1, Number(window.devicePixelRatio) || 1));
      const pixelWidth = Math.ceil(bounds.width * ratio), pixelHeight = Math.ceil(bounds.height * ratio);
      if (canvas.width !== pixelWidth) canvas.width = pixelWidth;
      if (canvas.height !== pixelHeight) canvas.height = pixelHeight;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, bounds.width, bounds.height);
      const lines = [], turns = [];
      const line = (x1, y1, x2, y2) => lines.push({ x1, y1, x2, y2 });
      if (columns === 1) line(first.cx, startAt.y + labelHeight, first.cx, first.top);
      else line(startAt.x + labelWidth, first.cy, first.left, first.cy);
      boxes.slice(1).forEach((next, index) => {
        const previous = boxes[index];
        if (columns === 1) {
          line(previous.cx, previous.bottom, next.cx, next.top);
        } else if (Math.floor(index / columns) === Math.floor((index + 1) / columns)) {
          const right = Math.floor(index / columns) % 2 === 0;
          line(right ? previous.right : previous.left, previous.cy, right ? next.left : next.right, next.cy);
        } else {
          const right = Math.floor(index / columns) % 2 === 0;
          const x1 = right ? previous.right : previous.left, x2 = right ? next.right : next.left;
          const turn = right ? bounds.width - 8 : 8;
          lines.push({ x1, y1: previous.cy, x2, y2: next.cy, turn });
          turns.push({ x: (x1 + x2) / 8 + turn * .75, y: (previous.cy + next.cy) / 2 });
        }
      });
      if (columns === 1) line(last.cx, last.bottom, last.cx, endAt.y);
      else line(rightwardEnd ? last.right : last.left, last.cy,
        rightwardEnd ? endAt.x : endAt.x + labelWidth, last.cy);
      const light = document.body.dataset.theme === "light";
      const computedGold = typeof window.getComputedStyle === "function"
        ? window.getComputedStyle(route).getPropertyValue("--journey-gold").trim() : "";
      const gold = computedGold || (light ? "#c88a09" : "#ffca38");
      context.beginPath();
      lines.forEach((segment) => {
        context.moveTo(segment.x1, segment.y1);
        if (segment.turn !== undefined) context.bezierCurveTo(segment.turn, segment.y1, segment.turn, segment.y2, segment.x2, segment.y2);
        else context.lineTo(segment.x2, segment.y2);
      });
      context.lineCap = "round";
      context.lineJoin = "round";
      context.strokeStyle = gold;
      context.lineWidth = 6;
      context.shadowColor = light ? "rgba(204,144,12,.35)" : "rgba(255,191,35,.7)";
      context.shadowBlur = light ? 9 : 18;
      context.stroke();
      context.shadowBlur = 0;
      context.strokeStyle = light ? gold : "#ffdb72";
      context.lineWidth = 2;
      context.stroke();
      turns.forEach((point) => {
        context.beginPath();
        context.arc(point.x, point.y, 7, 0, Math.PI * 2);
        context.fillStyle = gold;
        context.shadowBlur = light ? 6 : 14;
        context.fill();
        context.shadowBlur = 0;
        context.strokeStyle = light ? "#fff8df" : "#fff5cb";
        context.lineWidth = 2;
        context.stroke();
      });
    }
    let journeyFrame = 0;
    function scheduleJourneyLayout() {
      if (journeyFrame) return;
      journeyFrame = window.requestAnimationFrame(() => {
        journeyFrame = 0;
        layoutJourneyRoute();
      });
    }

    // Reuse the export controls in the full-canvas result header, keeping a
    // single instance and its listeners when navigating to another report view.
    const reportToolbar = document.querySelector(".report-topbar .toolbar");
    const reportToolbarHome = reportToolbar && reportToolbar.parentElement;
    const reportPrint = $("print-report");

    function syncResultHeader(view) {
      const destination = view === "results" ? $("result-report-actions") : reportToolbarHome;
      if (reportToolbar && destination && reportToolbar.parentElement !== destination) destination.appendChild(reportToolbar);
      if (reportPrint && reportToolbarHome && reportToolbar) {
        const printDestination = view === "results" ? reportToolbarHome : reportToolbar;
        if (reportPrint.parentElement !== printDestination) printDestination.appendChild(reportPrint);
      }
    }

    function rebuildInlineSteps(model, requestedKey) {
      // Results show only recorded evidence. Draft insertion and reordering
      // belong exclusively to the separate rerun planner.
      const selected = model.items[model.selected];
      const selectedKey = requestedKey || selected && selected.key || "original:0";
      model.list.replaceChildren();
      model.draftPanels.replaceChildren();
      model.items = model.originalItems.slice();
      model.items.forEach((item) => model.list.appendChild(item.button));
      const selectedIndex = model.items.findIndex((item) => item.key === selectedKey);
      model.selected = selectedIndex < 0 ? 0 : selectedIndex;
      syncCaseStepAvailability(model);
    }

    function selectCaseStep(model, requestedIndex, focus = false) {
      const available = model.items.map((item, index) => ({ item, index })).filter(({ item }) => !item.button.hidden);
      if (!available.length) return;
      let index = Number(requestedIndex);
      if (!available.some((item) => item.index === index)) index = available[0].index;
      model.selected = index;
      model.items.forEach((item, position) => {
        const active = position === index;
        item.node.hidden = !active;
        item.button.classList.toggle("active", active);
        item.button.setAttribute("aria-pressed", String(active));
        if (active) item.button.setAttribute("aria-current", "step");
        else item.button.removeAttribute("aria-current");
      });
      const selected = model.items[index];
      prepareEvidenceInspector(selected);
      text(model.title, selected.title);
      text(model.outcome, selected.outcome || "Sem resultado registrado");
      model.outcome.dataset.stepState = selected.state || "unknown";
      text(model.position, selected.kind === "summary" ? "Visão geral do teste" : selected.draftId
        ? "Passo extra · Rascunho visual · Não executado"
        : "Etapa " + selected.originalIndex + " de " + (model.originalItems.length - 1) + " · " + selected.kindLabel);
      const time = selected.node.querySelector("time");
      text(model.timestamp, time && time.getAttribute("datetime") ? executionDate({ started_at: time.getAttribute("datetime") })
        : time ? time.textContent : "Horário não registrado");
      const cursor = available.findIndex((item) => item.index === index);
      model.previous.disabled = cursor <= 0;
      model.next.disabled = cursor === available.length - 1;
      model.content.scrollTop = 0;
      if (focus) selected.button.focus({ preventScroll: true });
      selected.button.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "auto" });
    }

    function syncCaseStepAvailability(model) {
      const hideTechnical = document.body.classList.contains("hide-technical");
      model.items.forEach((item) => { item.button.hidden = hideTechnical && item.kind === "signal"; });
      const visible = model.originalItems.slice(1).filter((item) => !item.button.hidden).length;
      const total = model.originalItems.length - 1;
      text(model.count, visible === total ? total + " registros na linha do tempo" : visible + " de " + total + " registros visíveis");
      text(model.progress, visible + " / " + total);
      model.progress.setAttribute("aria-label", visible + " de " + total + " etapas registradas visíveis");
      const recorded = model.originalItems.slice(1).filter((item) => !item.button.hidden);
      model.originalItems.forEach((item) => item.button.removeAttribute("data-step-last"));
      if (recorded.length) recorded[recorded.length - 1].button.setAttribute("data-step-last", "true");
      selectCaseStep(model, model.selected);
    }

    function actionPreviewMetadata(figure) {
      let value;
      try { value = JSON.parse(figure && figure.dataset.actionPreview || "{}"); } catch (_) { return null; }
      if (!value || typeof value !== "object" || value.version !== "action-target-v1" || value.coordinate_space !== "screenshot-css-pixels") return null;
      const image = value.image || {}, box = value.box || {};
      const numbers = [image.width, image.height, box.x, box.y, box.width, box.height];
      if (!numbers.every((number) => typeof number === "number" && Number.isFinite(number))) return null;
      if (image.width <= 0 || image.height <= 0 || box.width <= 0 || box.height <= 0
        || box.x < 0 || box.y < 0 || box.x + box.width > image.width || box.y + box.height > image.height) return null;
      const metadata = { image, box, action: value.action };
      const selection = value.selection;
      if (value.action === "select" && selection && typeof selection === "object"
        && selection.version === "select-labels-v1" && ["confirmed", "mismatch", "unverified"].includes(selection.status)) {
        const safeSelection = { version: "select-labels-v1", status: selection.status };
        for (const field of ["requested_label", "before_label", "after_label"]) {
          const label = selection[field];
          if (typeof label === "string" && label.trim() && label.trim().length <= 240 && !/[\u0000-\u001f\u007f-\u009f]/.test(label)) {
            safeSelection[field] = label.trim();
          }
        }
        if (safeSelection.requested_label || safeSelection.before_label || safeSelection.after_label) metadata.selection = safeSelection;
      }
      return metadata;
    }

    function refreshActionMarker(canvas, image) {
      all(".action-target-overlay", canvas).forEach((marker) => marker.remove());
      if (image.naturalWidth > 0 && image.naturalHeight > 0) {
        canvas.style.setProperty("--action-image-ratio", String(image.naturalWidth / image.naturalHeight));
      }
      const value = actionPreviewMetadata(canvas);
      if (!value || !(image.naturalWidth > 0 && image.naturalHeight > 0)) return;
      // Geometry belongs to this exact viewport capture, never to another image.
      // The bitmap may be a uniform multiple of the recorded CSS viewport (for
      // example 125% Windows display scaling); the box is percentage-based, so
      // only a different aspect ratio invalidates it.
      const scaleX = image.naturalWidth / value.image.width;
      const scaleY = image.naturalHeight / value.image.height;
      if (Math.abs(scaleX - scaleY) > 0.005 * Math.max(scaleX, scaleY)) return;
      const percent = (number, dimension) => (number / dimension * 100) + "%";
      const overlay = document.createElement("span");
      overlay.className = "action-target-overlay";
      overlay.setAttribute("aria-hidden", "true");
      const box = document.createElement("span");
      box.className = "action-target-box";
      box.style.setProperty("left", percent(value.box.x, value.image.width));
      box.style.setProperty("top", percent(value.box.y, value.image.height));
      box.style.setProperty("width", percent(value.box.width, value.image.width));
      box.style.setProperty("height", percent(value.box.height, value.image.height));
      const dot = document.createElement("span");
      dot.className = "action-target-dot";
      box.appendChild(dot);
      const tag = document.createElement("span");
      tag.className = "action-target-tag";
      const labels = { click: "Clique aqui", select: "Selecionar aqui", fill: "Preencher aqui", press: "Tecla neste controle", hover: "Passar o cursor aqui" };
      const label = value.action === "select" && value.selection && value.selection.requested_label
        ? "Selecionar: " + value.selection.requested_label
        : Object.prototype.hasOwnProperty.call(labels, value.action) ? labels[value.action] : "Alvo da ação";
      const tagText = document.createElement("span");
      tagText.className = "action-target-tag-text";
      tagText.textContent = label;
      tag.appendChild(tagText);
      tag.setAttribute("title", label);
      const right = value.box.x + value.box.width / 2 > value.image.width / 2;
      // Keep the callout in the larger half of the capture. Long option labels
      // wrap without pretending that the native dropdown was open in the PNG.
      const above = value.box.y + value.box.height / 2 > value.image.height / 2;
      tag.classList.toggle("align-right", right);
      tag.classList.toggle("above", above);
      const offset = "max(8px, " + percent(right ? value.image.width - value.box.x - value.box.width : value.box.x, value.image.width) + ")";
      tag.style.setProperty("--action-tag-offset", offset);
      tag.style.setProperty(right ? "right" : "left", offset);
      tag.style.setProperty("top", percent(above ? value.box.y : value.box.y + value.box.height, value.image.height));
      overlay.append(box, tag);
      canvas.appendChild(overlay);
    }

    function setActionMarker(canvas, image, figure) {
      const metadata = actionPreviewMetadata(figure);
      canvas.dataset.actionPreview = metadata ? JSON.stringify({
        version: "action-target-v1", coordinate_space: "screenshot-css-pixels", ...metadata,
      }) : "{}";
      if (canvas.dataset.actionPreviewBound !== "true") {
        image.addEventListener("load", () => refreshActionMarker(canvas, image));
        canvas.dataset.actionPreviewBound = "true";
      }
      refreshActionMarker(canvas, image);
    }

    function prepareCapturePreview(figure) {
      const image = figure.querySelector("img");
      if (!image || figure.querySelector(".step-preview-button")) return;
      // A print preview may reveal an unvisited CT below the viewport. Lazy
      // loading would leave its target geometry unrendered despite a valid src.
      image.loading = "eager";
      hydrateImage(image);
      const filename = (figure.querySelector("figcaption") || {}).textContent || "Captura registrada";
      const control = document.createElement("button");
      control.className = "step-preview-button";
      control.type = "button";
      control.dataset.stepPreview = "true";
      control.setAttribute("aria-label", "Ampliar captura " + filename);
      image.replaceWith(control);
      if (figure.classList.contains("action-preview")) {
        control.classList.add("action-preview-button");
        const canvas = document.createElement("span");
        canvas.className = "action-preview-canvas";
        control.appendChild(canvas);
        canvas.appendChild(image);
        setActionMarker(canvas, image, figure);
      } else control.appendChild(image);
    }

    function evidenceIcon(name) {
      const icon = document.createElement("span");
      icon.className = "evidence-icon";
      icon.setAttribute("aria-hidden", "true");
      const paths = {
        image: ["M4 4h16v16H4z", "m4 15 4-4 4 4 3-3 5 5", "M8 8h.01"],
        code: ["m8 8-4 4 4 4", "m16 8 4 4-4 4", "m14 5-4 14"],
        minus: ["M4 11a7 7 0 1 0 14 0 7 7 0 0 0-14 0", "m16 16 5 5", "M8 11h6"],
        plus: ["M4 11a7 7 0 1 0 14 0 7 7 0 0 0-14 0", "m16 16 5 5", "M8 11h6", "M11 8v6"],
        expand: ["M8 3H3v5", "M16 3h5v5", "M3 16v5h5", "M21 16v5h-5"],
        copy: ["M9 8h11v13H9z", "M5 16H3V3h11v2"],
        link: ["m10 13 4-4", "M8 15 6 17a3 3 0 0 1-4-4l4-4a3 3 0 0 1 4 0", "m14 9 2-2a3 3 0 0 1 4 4l-4 4a3 3 0 0 1-4 0"],
        download: ["M12 3v12", "m7 10 5 5 5-5", "M4 16v5h16v-5"],
        previous: ["m15 5-7 7 7 7"], next: ["m9 5 7 7-7 7"], close: ["m6 6 12 12", "M18 6 6 18"],
      };
      if (typeof document.createElementNS !== "function") {
        icon.textContent = ({ previous: "‹", next: "›", minus: "−", plus: "+", expand: "⛶", download: "↓", copy: "▣", link: "↗", close: "×" })[name] || "▧";
        return icon;
      }
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      for (const [attribute, value] of Object.entries({ viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" })) svg.setAttribute(attribute, value);
      (paths[name] || paths.image).forEach((value) => {
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", value); svg.appendChild(path);
      });
      icon.appendChild(svg);
      return icon;
    }

    function enableEvidencePanning(viewport, getTarget) {
      let pan = null, suppressClick = false;
      function finish(event) {
        if (!pan || event && event.pointerId !== pan.pointerId) return;
        const previous = pan; pan = null;
        viewport.classList.remove("is-panning");
        suppressClick = !!(event && event.type === "pointerup" && previous.moved);
        if (previous.target.hasPointerCapture && previous.target.hasPointerCapture(previous.pointerId)) previous.target.releasePointerCapture(previous.pointerId);
      }
      viewport.addEventListener("pointerdown", (event) => {
        suppressClick = false;
        if (viewport.dataset.panEnabled !== "true" || event.button !== 0 || event.pointerType !== "mouse" || event.isPrimary === false
          || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || pan) return;
        const target = event.target instanceof Element ? getTarget(event.target) : null;
        if (!target || viewport.scrollWidth <= viewport.clientWidth && viewport.scrollHeight <= viewport.clientHeight) return;
        pan = { pointerId: event.pointerId, target, x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop, moved: false };
        viewport.classList.add("is-panning");
        if (target.setPointerCapture) target.setPointerCapture(event.pointerId);
      });
      viewport.addEventListener("pointermove", (event) => {
        if (!pan || event.pointerId !== pan.pointerId) return;
        if (!(event.buttons & 1)) { finish(event); return; }
        const dx = event.clientX - pan.x, dy = event.clientY - pan.y;
        if (!pan.moved && Math.hypot(dx, dy) < 4) return;
        pan.moved = true; event.preventDefault();
        viewport.scrollLeft = pan.left - dx; viewport.scrollTop = pan.top - dy;
      });
      ["pointerup", "pointercancel", "lostpointercapture"].forEach((name) => viewport.addEventListener(name, finish));
      viewport.addEventListener("dragstart", (event) => { if (viewport.dataset.panEnabled === "true") event.preventDefault(); });
      viewport.addEventListener("click", (event) => {
        const suppress = suppressClick; suppressClick = false;
        if (suppress && event.detail !== 0) { event.preventDefault(); event.stopPropagation(); }
      }, true);
      return finish;
    }

    function prepareEvidenceInspector(item) {
      const entry = item.node, content = entry.querySelector(".timeline-content");
      if (item.kind === "summary" || !content || content.querySelector(".step-evidence-layout")) return;
      const groups = all(".shots", content).filter((group) => group.querySelector("figure img"));
      const figures = groups.flatMap((group) => all("figure", group).filter((figure) => figure.querySelector("img")));
      if (!figures.length) return;
      const make = (tag, className, value) => {
        const node = document.createElement(tag); node.className = className;
        if (value != null) node.textContent = value;
        return node;
      };
      const iconButton = (name, label, action) => {
        const control = button("", action, "button button-ghost evidence-icon-button");
        control.appendChild(evidenceIcon(name)); control.setAttribute("aria-label", label); control.setAttribute("title", label);
        return control;
      };
      const layout = make("div", "step-evidence-layout");
      const viewer = make("section", "step-evidence-viewer");
      viewer.setAttribute("aria-label", "Visualizador da evidência");
      const canvas = make("div", "step-evidence-canvas");
      const tools = make("div", "step-evidence-tools"), zoomTools = make("div", "step-evidence-zoom");
      tools.setAttribute("role", "group"); tools.setAttribute("aria-label", "Controles da imagem");
      const viewport = make("div", "step-evidence-viewport");
      viewport.tabIndex = 0; viewport.setAttribute("aria-label", "Imagem da evidência; com zoom, segure o botão esquerdo e arraste para mover, ou use as barras de rolagem");
      let zoom = 100, activeFigure = figures[0];
      const finishPan = enableEvidencePanning(viewport, (node) => {
        const target = node.closest(".step-preview-button");
        return target && activeFigure.contains(target) ? target : null;
      });
      const setZoom = (value) => {
        finishPan();
        zoom = Math.max(50, Math.min(200, value));
        viewport.style.setProperty("--evidence-zoom", String(zoom / 100));
        viewport.dataset.panEnabled = String(zoom > 100);
        text(reset, zoom + "%"); minus.disabled = zoom === 50; plus.disabled = zoom === 200;
        if (zoom === 100) { viewport.scrollTop = 0; viewport.scrollLeft = 0; }
      };
      const minus = iconButton("minus", "Diminuir zoom", () => setZoom(zoom - 25));
      const reset = button("100%", () => setZoom(100), "evidence-zoom-reset"); reset.setAttribute("aria-label", "Restaurar zoom para 100%");
      const plus = iconButton("plus", "Aumentar zoom", () => setZoom(zoom + 25));
      zoomTools.append(minus, reset, plus);
      const expand = iconButton("expand", "Ampliar evidência", () => openEvidencePreview(activeFigure.querySelector("img"), expand,
        (activeFigure.querySelector("figcaption") || {}).textContent, (activeFigure.querySelector("figcaption") || {}).textContent));
      tools.append(zoomTools, expand); canvas.append(viewport, tools); viewer.appendChild(canvas);
      const aside = make("aside", "step-evidence-info"), panel = make("div", "step-evidence-info-panel");
      const heading = make("h4", "step-evidence-info-heading"); heading.append(evidenceIcon("image"), make("span", "", "Informações da evidência"));
      const fields = make("dl", "step-evidence-fields");
      const row = (label, name) => {
        const wrapper = make("div", "step-evidence-field"); wrapper.dataset.evidenceField = name;
        const value = make("dd", ""); wrapper.append(make("dt", "", label), value); fields.appendChild(wrapper); return value;
      };
      const type = row("Tipo", "type"), filename = row("Nome do arquivo", "filename"), date = row("Data e hora", "date"), url = row("URL da página", "url");
      const resolution = row("Resolução", "resolution"), size = row("Tamanho", "size");
      const time = entry.querySelector("time"), recordedTime = time && time.getAttribute("datetime");
      text(date, recordedTime && !Number.isNaN(new Date(recordedTime).getTime()) ? executionDate({ started_at: recordedTime }) : "Não registrada");
      const feedback = make("p", "step-evidence-feedback"); feedback.setAttribute("role", "status"); feedback.setAttribute("aria-live", "polite");
      const copyValue = (value, label) => iconButton("copy", "Copiar " + label, async () => {
        try {
          if (!window.navigator || !window.navigator.clipboard || typeof window.navigator.clipboard.writeText !== "function") throw new Error("Clipboard unavailable");
          await window.navigator.clipboard.writeText(value); text(feedback, label + " copiado.");
        } catch (_) { text(feedback, "Não foi possível copiar. Selecione o texto e copie manualmente."); }
      });
      const rawUrl = (all(".url", content).map((node) => node.textContent.trim().replace(/^Página:\s*/i, "")).find((value) => /^https?:\/\//i.test(value)) || "");
      let pageUrl = "";
      try { const candidate = new URL(rawUrl); if (["http:", "https:"].includes(candidate.protocol) && !candidate.username && !candidate.password) pageUrl = candidate.href; } catch (_) {}
      if (pageUrl) {
        const link = make("a", "step-evidence-url", pageUrl); link.href = pageUrl; link.target = "_blank"; link.rel = "noopener noreferrer";
        url.append(link, copyValue(pageUrl, "URL"));
      } else text(url, "Não registrada");
      const context = make("div", "step-evidence-context");
      // Move only the step's short introduction. Rich assertions and technical
      // records stay intact below the viewer; all canonical images are reused.
      Array.from(content.children).filter((node) => ["H3", "P"].includes(node.tagName) && !node.classList.contains("url"))
        .forEach((node) => context.appendChild(node));
      panel.append(heading, fields, context, feedback);
      const download = make("a", "button step-evidence-download"); download.append(evidenceIcon("download"), make("span", "", "Baixar imagem"));
      aside.append(panel, download); layout.append(viewer, aside);
      groups.forEach((group) => viewport.appendChild(group));
      content.insertBefore(layout, content.firstChild);
      const update = () => {
        const image = activeFigure.querySelector("img"); hydrateImage(image);
        const name = ((activeFigure.querySelector("figcaption") || {}).textContent || image.dataset.sourceShot || "evidencia.png").trim();
        type.textContent = entry.dataset.imageSource === "user_upload" ? "Imagem do usuário" : activeFigure.classList.contains("action-preview") ? "Captura anterior à ação" : "Captura de tela";
        filename.replaceChildren(make("span", "step-evidence-filename", name), copyValue(name, "nome do arquivo"));
        text(resolution, image.naturalWidth > 0 && image.naturalHeight > 0 ? image.naturalWidth + " × " + image.naturalHeight + " px" : "Não disponível");
        const src = image.getAttribute("src") || "";
        const embedded = isEvidenceImageSource(src);
        if (embedded) { download.href = src; download.download = name; download.removeAttribute("aria-disabled"); }
        else { download.removeAttribute("href"); download.setAttribute("aria-disabled", "true"); }
        const comma = src.indexOf(",");
        if (/^data:image\/[a-z+.-]+;base64,/i.test(src.slice(0, 80)) && comma > 0) {
          const bytes = Math.floor((src.length - comma - 1) * 3 / 4) - (src.endsWith("==") ? 2 : src.endsWith("=") ? 1 : 0);
          text(size, bytes < 1024 ? bytes + " B" : (bytes / (bytes < 1048576 ? 1024 : 1048576)).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + (bytes < 1048576 ? " KB" : " MB"));
        } else {
          const bytes = isEvidenceImageSource(src) && data.online_preview && (data.online_preview.image_sizes || {})[src];
          text(size, Number.isFinite(bytes) && bytes > 0 ? (bytes / (bytes < 1048576 ? 1024 : 1048576)).toLocaleString("pt-BR", { maximumFractionDigits: 1 }) + (bytes < 1048576 ? " KB" : " MB") : "Não registrado");
        }
      };
      if (figures.length > 1) {
        const chooser = make("select", "step-evidence-choice"); chooser.setAttribute("aria-label", "Selecionar imagem desta etapa");
        figures.forEach((figure, index) => {
          const option = make("option", "", (figure.querySelector("figcaption") || {}).textContent || "Imagem " + (index + 1)); option.value = String(index); chooser.appendChild(option);
        });
        chooser.addEventListener("change", () => {
          activeFigure = figures[Number(chooser.value)] || figures[0]; figures.forEach((figure) => { figure.hidden = figure !== activeFigure; });
          setZoom(100); text(feedback, ""); update();
        }); viewer.insertBefore(chooser, canvas);
      }
      figures.forEach((figure) => {
        figure.classList.add("step-evidence-figure"); figure.hidden = figure !== activeFigure;
        const image = figure.querySelector("img");
        image.addEventListener("load", () => { if (figure === activeFigure) update(); });
        image.addEventListener("error", () => { if (figure === activeFigure) { text(resolution, "Não disponível"); text(feedback, "Não foi possível exibir esta imagem."); } });
      });
      setZoom(100); update();
    }

    function ensureCaseExplorer(detail) {
      const readOnly = detail.hasAttribute("data-history-case-index");
      const key = readOnly ? "history-" + detail.dataset.historyCaseIndex + "-" + detail.dataset.historyVersion : detail.dataset.caseIndex;
      if (caseExplorers.has(key)) return caseExplorers.get(key);
      const body = detail.querySelector(".scenario-body");
      if (!body) return null;
      const make = (tag, className, value) => {
        const element = document.createElement(tag);
        element.className = className;
        if (value != null) element.textContent = value;
        return element;
      };
      const timeline = all(".evidence-timeline", body)[0] || make("ol", "evidence-timeline");
      const entries = Array.from(timeline.children).filter((entry) => entry.classList.contains("timeline-entry"));
      const heading = body.querySelector(".timeline-heading");
      const summary = make("div", "case-summary-panel");
      summary.id = "case-summary-" + key;
      Array.from(body.childNodes).forEach((node) => { if (node !== timeline && node !== heading) summary.appendChild(node); });
      if (heading) heading.remove();
      timeline.classList.add("case-timeline-panels");
      const workspace = make("div", "case-workspace");
      const sidebar = make("aside", "case-step-sidebar");
      sidebar.setAttribute("aria-label", "Passos e evidências do CT");
      sidebar.dataset.readOnly = String(readOnly);
      const sidebarHeader = make("div", "case-step-sidebar-header");
      const count = make("small", "case-step-count result-sr-only");
      const progress = make("span", "case-step-progress");
      const headerTools = make("div", "case-step-header-tools");
      headerTools.appendChild(progress);
      sidebarHeader.append(make("h3", "", "Passos e evidências"), count, headerTools);
      if (timeline.dataset.stepOrder === "planned") {
        sidebarHeader.append(make("small", "case-step-order-note result-sr-only", "Ordem dos passos planejada · horários reais preservados"));
        sidebarHeader.setAttribute("title", "Ordem dos passos planejada · horários reais preservados");
      }
      const inlineFeedback = make("p", "case-inline-feedback");
      inlineFeedback.hidden = true;
      inlineFeedback.setAttribute("role", "status");
      inlineFeedback.setAttribute("aria-live", "polite");
      const list = make("div", "case-step-list");
      list.setAttribute("role", "group");
      list.setAttribute("aria-label", "Selecionar passo do teste");
      sidebar.append(sidebarHeader, inlineFeedback, list);
      const stage = make("section", "case-step-stage");
      const toolbar = make("div", "case-step-toolbar");
      const caption = make("div", "case-step-caption");
      const position = make("span", "case-step-position");
      const title = make("h3", "case-selected-title");
      title.id = "case-selected-title-" + key;
      stage.setAttribute("aria-labelledby", title.id);
      caption.append(make("h3", "case-stage-heading", "Detalhes da etapa"), position, title);
      const content = make("div", "case-step-content");
      content.tabIndex = 0;
      content.setAttribute("aria-label", "Detalhes e evidências do passo selecionado");
      const draftPanels = make("div", "case-draft-panels");
      content.append(summary, timeline, draftPanels);
      const model = {
        key, detail, workspace, list, content, count, progress, position, title, items: [], originalItems: [],
        selected: entries.length ? 1 : 0, inlineDrag: null, inlineFeedback, draftPanels, readOnly,
      };
      const move = (direction) => {
        const available = model.items.map((item, index) => ({ item, index })).filter(({ item }) => !item.button.hidden);
        const cursor = available.findIndex((item) => item.index === model.selected);
        const next = available[cursor + direction];
        if (next) selectCaseStep(model, next.index);
      };
      model.previous = button("← Anterior", () => move(-1), "button button-ghost case-step-prev");
      model.previous.setAttribute("aria-label", "Passo anterior");
      model.previous.setAttribute("title", "Passo anterior"); model.previous.replaceChildren(evidenceIcon("previous"));
      model.next = button("Próximo →", () => move(1), "button button-ghost case-step-next");
      model.next.setAttribute("aria-label", "Próximo passo");
      model.next.setAttribute("title", "Próximo passo"); model.next.replaceChildren(evidenceIcon("next"));
      const controls = make("div", "case-step-controls");
      model.timestamp = make("span", "case-step-timestamp");
      model.outcome = make("span", "case-step-outcome"); model.outcome.setAttribute("role", "status");
      controls.append(model.previous, model.next);
      const stepMeta = make("div", "case-step-meta"); stepMeta.append(model.timestamp, model.outcome);
      toolbar.append(controls, caption, stepMeta);
      stage.append(toolbar, content);
      workspace.append(sidebar, stage);
      body.replaceChildren(workspace);
      detail.classList.add("step-explorer");
      const caseHeader = detail.querySelector("summary");
      if (caseHeader) caseHeader.addEventListener("click", (event) => event.preventDefault());
      const meta = detail.querySelector(".case-meta");
      if (meta) meta.textContent = meta.textContent.replace(" · clique para ver a linha do tempo", "");
      const labels = { action: "Ação realizada", assertion: "Conferência", signal: "Sinal técnico", observation: "Registro da página", summary: "Visão do caso" };
      const states = {
        pass: { symbol: "✓", outcome: "Passou" },
        fail: { symbol: "×", outcome: "Não passou" },
        unknown: { symbol: "?", outcome: "Sem resultado" },
        recorded: { symbol: "▣", outcome: "Captura registrada" },
        info: { symbol: "i", outcome: "Informativo" },
        warning: { symbol: "!", outcome: "Atenção" },
      };
      const summaryResults = {
        PASS: { state: "pass", outcome: "Aprovado", description: "Visão geral da ação e das conferências deste caso." },
        FAIL: { state: "fail", outcome: "Reprovado", description: "Veja o motivo da reprovação e as conferências deste caso." },
        BLOCKED: { state: "warning", outcome: "Bloqueado", description: "Um impedimento não permitiu concluir o teste." },
        SKIPPED: { state: "unknown", outcome: "Sem validação", description: "O caso não foi validado nesta execução." },
      };
      const fallbackDescriptions = {
        action: "A interação foi registrada; o resultado não foi informado.",
        assertion: "Conferência registrada, sem resultado informado.",
        signal: "Registro técnico para análise; não reprova o caso por si só.",
        observation: "Estado da página registrado; não é uma aprovação do teste.",
      };
      [{ node: summary, kind: "summary", title: "Resumo do teste" }, ...entries.map((node) => ({
        node, kind: Object.keys(labels).find((kind) => node.classList.contains(kind)) || "observation",
        title: (node.querySelector("h3") || {}).textContent || "Registro da execução",
      }))].forEach((item, index) => {
        // A case verdict never supplies the outcome of an individual step.
        // Only explicit server metadata can classify a check or interaction;
        // older reports remain honest about their missing step-level results.
        const hasState = Object.prototype.hasOwnProperty.call(states, item.node.dataset.stepState);
        const result = item.kind === "summary"
          ? Object.prototype.hasOwnProperty.call(summaryResults, detail.dataset.status)
            ? summaryResults[detail.dataset.status]
            : { state: "unknown", outcome: "Sem resultado", description: "O resultado deste caso não foi informado." }
          : { state: hasState ? item.node.dataset.stepState : "unknown" };
        const state = states[result.state];
        const outcome = result.outcome || (hasState && item.node.dataset.stepOutcome || "").trim() || state.outcome;
        const description = result.description || (item.node.dataset.stepDescription || "").trim() || fallbackDescriptions[item.kind];
        const kindLabel = item.node.dataset.imageSource === "user_upload" ? "Imagem do usuário"
          : item.kind === "observation" && item.node.querySelector(".shots img") ? "Captura da página" : labels[item.kind];
        const sidebarTitle = item.kind === "assertion" && item.title === "Ausência de novas falhas técnicas da aplicação"
          ? "Erros durante a ação" : item.title;
        const originalItem = { ...item, key: "original:" + index, originalIndex: index, kindLabel, state: result.state, outcome };
        const control = button("", () => selectCaseStep(model, model.items.indexOf(originalItem)), "case-step-button");
        control.dataset.stepIndex = String(index);
        control.dataset.recordedIndex = String(index);
        control.dataset.stepKind = item.kind;
        control.dataset.stepState = result.state;
        control.setAttribute("aria-controls", item.node.id);
        control.setAttribute("aria-label", (index ? "Etapa " + index + ": " : "") + sidebarTitle + ". " + description + " Resultado: " + outcome + ".");
        if (!index) control.setAttribute("title", "Ver resumo do resultado: " + outcome);
        const copy = make("span", "step-copy");
        const status = make("span", "step-status");
        status.dataset.stepState = result.state;
        const icon = make("span", "step-status-icon", state.symbol);
        icon.setAttribute("aria-hidden", "true");
        status.append(icon, make("span", "step-status-label", outcome));
        const descriptionNode = make("small", "step-description", description);
        descriptionNode.setAttribute("title", description);
        let caption = description;
        if (item.kind === "observation" && item.node.dataset.imageSource === "user_upload") caption = "Imagem escolhida pelo usuário.";
        else if (item.kind === "observation" && result.state === "recorded") caption = /^Antes/i.test(item.title)
          ? "Captura da página antes da ação." : /^Depois/i.test(item.title) ? "Captura da página após a ação." : "Captura da página registrada.";
        else if (item.kind === "action") caption = "Ação: " + item.title.charAt(0).toLocaleLowerCase("pt-BR") + item.title.slice(1) + ".";
        else if (item.kind === "assertion") caption = result.state === "fail" ? "Resultado diferente do esperado."
          : result.state === "unknown" ? "Conferência sem resultado registrado."
          : /opção/i.test(sidebarTitle) ? "Confirmação da opção escolhida."
          : /ordena/i.test(sidebarTitle) ? "Conferência da ordem dos produtos."
          : /erros/i.test(sidebarTitle) ? "Verificação de erros da aplicação." : "Conferência do resultado registrado.";
        const compact = make("small", "step-caption", caption);
        compact.setAttribute("aria-hidden", "true");
        compact.setAttribute("title", description);
        copy.append(make("span", "step-kind", kindLabel), make("span", "step-label", sidebarTitle), descriptionNode, compact, status);
        if (index) control.appendChild(make("span", "step-number", String(index).padStart(2, "0")));
        control.appendChild(copy);
        control.appendChild(make("span", "step-selected-tag", "Selecionado"));
        list.appendChild(control);
        originalItem.button = control;
        model.items.push(originalItem);
        model.originalItems.push(originalItem);
      });
      sidebar.addEventListener("keydown", (event) => {
        if (event.altKey || !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
        const target = event.target instanceof Element ? event.target.closest(".case-step-button") : null;
        if (!target) return;
        event.preventDefault();
        const available = model.items.map((item, index) => ({ item, index })).filter(({ item }) => !item.button.hidden);
        const cursor = available.findIndex((item) => item.index === Number(target.dataset.stepIndex));
        const next = event.key === "Home" ? 0 : event.key === "End" ? available.length - 1
          : Math.max(0, Math.min(available.length - 1, cursor + (event.key === "ArrowDown" ? 1 : -1)));
        selectCaseStep(model, available[next].index, true);
      });
      all(".shots figure", workspace).forEach(prepareCapturePreview);
      caseExplorers.set(key, model);
      rebuildInlineSteps(model);
      return model;
    }

    function selectStepForTarget(target) {
      const detail = target.closest("details.scenario[data-case-index]");
      if (!detail) return;
      const model = ensureCaseExplorer(detail);
      if (!model) return;
      const index = model.items.findIndex((item) => item.node === target || item.node.contains(target));
      if (index >= 0) selectCaseStep(model, index);
    }

    function hydrateImage(image) {
      if (!image || image.getAttribute("src") || !image.dataset.sourceShot) return;
      const owner = image.closest("details.scenario[data-case-index]");
      const sourceCase = image.dataset.sourceCase != null ? image.dataset.sourceCase : owner && owner.dataset.caseIndex;
      const key = (sourceCase != null ? "case:" + sourceCase : "context") + "\n" + image.dataset.sourceShot;
      if (sourceImages.has(key)) {
        image.setAttribute("src", sourceImages.get(key));
        return;
      }
      let scope = null;
      if (sourceCase != null) {
        if (!sourceScopes.has(sourceCase)) {
          sourceScopes.set(sourceCase, all("#scenarios details.scenario[data-case-index]")
            .find((detail) => detail.dataset.caseIndex === sourceCase));
        }
        scope = sourceScopes.get(sourceCase);
      } else if (image.dataset.sourceContext != null) {
        scope = $("execution-observations");
      }
      if (!scope) return;
      const figure = all("figure", scope).find((item) => {
        const caption = item.querySelector("figcaption");
        return caption && caption.textContent.trim() === image.dataset.sourceShot && item.querySelector("img[src]");
      });
      const canonical = figure && figure.querySelector("img[src]");
      if (canonical) {
        const src = canonical.getAttribute("src");
        sourceImages.set(key, src);
        image.setAttribute("src", src);
      }
    }

    function executionHistory(current) {
      const history = current && current.execution_history;
      if (Array.isArray(history) && history.length) return history.filter((item) => item && Number.isInteger(item.version) && item.version >= 0);
      return current ? [{ ...current, version: 0, current: true, has_result: true,
        exploration_id: current.rerun_source_exploration_id || data.run_id,
        scenario_id: current.rerun_source_scenario_id || current.id }] : [];
    }

    function selectedExecution(current) {
      const history = executionHistory(current);
      return history.find((item) => item.version === selectedExecutions.get(String(current && current.index)))
        || history.find((item) => item.current) || history.filter((item) => item.has_result).at(-1) || history.at(-1) || {};
    }

    function executionDate(revision, compact = false) {
      const date = new Date(revision.started_at || revision.recorded_at || "");
      if (Number.isNaN(date.getTime())) return "Horário não registrado";
      return compact ? date.toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
        : date.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" });
    }

    function executionDuration(value) {
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "Duração não registrada";
      const seconds = Math.round(value);
      return [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60].map((part) => String(part).padStart(2, "0")).join(":");
    }

    function executionVerdict(revision) {
      return verdictLabels[revision.status] || ({ running: "Em execução", queued: "Na fila", waiting_input: "Aguardando você",
        cancelled: "Cancelada", canceled: "Cancelada", failed: "Interrompida", postprocessing: "Gerando relatório" })[revision.run_status] || "Sem resultado";
    }

    function executionNode(tag, className, label) {
      const node = document.createElement(tag);
      node.className = className;
      if (label != null) node.textContent = label;
      return node;
    }

    function archivedExecution(current, revision) {
      return all("details[data-history-case-index][data-history-version]").find((item) =>
        !item.closest("#case-history-dialog") && item.dataset.historyCaseIndex === String(current.index)
        && item.dataset.historyVersion === String(revision.version));
    }

    function selectExecution(version) {
      const current = byIndex.get(selectedCase);
      if (!executionHistory(current).some((item) => item.version === version)) return;
      if (stepPickerRequest) closeStepPicker();
      const focusTimeline = document.activeElement && document.activeElement.closest("#case-execution-list");
      selectedExecutions.set(selectedCase, version);
      navigate("results", false);
      if (focusTimeline) {
        const control = $("case-execution-list").querySelector('[data-execution-version="' + version + '"]');
        if (control) control.focus({ preventScroll: true });
      }
    }

    function updateExecutionOverflow() {
      const list = $("case-execution-list");
      const track = list && list.closest(".execution-history-track");
      if (track) {
        // Fixed edge gutters let cards move into the blur instead of switching
        // a full-width overlay on top of their content after a tiny scroll.
        track.dataset.scrollable = String(list.scrollWidth - list.clientWidth > 2);
        track.dataset.previousExecutions = String(list.scrollLeft > 2);
        track.dataset.moreExecutions = String(list.scrollWidth - list.clientWidth - list.scrollLeft > 2);
      }
    }

    function scrollHorizontalList(list, event) {
      // Keep native horizontal gestures, Shift scrolling and browser zoom.
      if (!list || event.defaultPrevented || !event.cancelable || event.ctrlKey || event.metaKey || event.shiftKey
        || !event.deltaY || Math.abs(event.deltaX) >= Math.abs(event.deltaY)) return;
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? list.clientWidth : 1;
      const limit = Math.max(0, list.scrollWidth - list.clientWidth);
      const target = Math.max(0, Math.min(limit, list.scrollLeft + event.deltaY * unit));
      // Release the page at either end, and when the cards already fit.
      if (Math.abs(target - list.scrollLeft) < 1) return;
      event.preventDefault();
      list.scrollLeft = target;
      return true;
    }

    function scrollExecutionHistory(event) {
      if (scrollHorizontalList($("case-execution-list"), event)) updateExecutionOverflow();
    }

    function renderExecutionHistory(current) {
      const list = $("case-execution-list"), host = $("case-selected-execution");
      if (!current || !list || !host) return;
      const history = executionHistory(current), revision = selectedExecution(current);
      const newest = Math.max(...history.map((item) => item.version));
      const recent = history.slice().sort((left, right) => right.version - left.version);
      list.replaceChildren();
      recent.forEach((entry) => {
        const selected = entry.version === revision.version;
        const control = button("", () => selectExecution(entry.version), "execution-card" + (selected ? " active" : ""));
        control.dataset.executionVersion = String(entry.version);
        control.dataset.status = entry.status || "";
        control.setAttribute("aria-pressed", String(selected));
        control.setAttribute("aria-label", "Execução " + (entry.version + 1) + ", " + executionDate(entry) + ", " + executionVerdict(entry)
          + (entry.version === newest ? ", mais recente" : "") + (selected ? ", selecionada" : ""));
        control.appendChild(executionNode("strong", "execution-card-title", "Execução #" + (entry.version + 1)));
        if (entry.version === newest) control.appendChild(executionNode("span", "execution-latest-tag", "Mais recente"));
        const time = executionNode("time", "execution-card-time", executionDate(entry, true));
        const date = new Date(entry.started_at || entry.recorded_at || "");
        if (!Number.isNaN(date.getTime())) time.setAttribute("datetime", date.toISOString());
        const outcome = executionNode("span", "execution-card-outcome");
        outcome.dataset.status = entry.status || "";
        outcome.append(executionNode("span", "execution-outcome-icon", ({ PASS: "✓", FAIL: "×", BLOCKED: "!" })[entry.status] || "·"),
          executionNode("span", "", executionVerdict(entry)), executionNode("span", "execution-card-duration", executionDuration(entry.duration_seconds)));
        control.append(time, outcome);
        outcome.setAttribute("title", "Resultado do teste e duração da ação registrada");
        const item = executionNode("li", "execution-history-item"); item.appendChild(control); list.appendChild(item);
      });
      text($("case-execution-total"), history.length + (history.length === 1 ? " execução registrada" : " execuções registradas"));
      updateExecutionOverflow();
      const selectedItem = list.querySelector('.execution-card[aria-pressed="true"]');
      if (selectedItem) {
        // Reveal only within the horizontal track, without moving the page or
        // changing keyboard focus when opening an older execution directly.
        const item = selectedItem.parentElement;
        const inset = parseFloat(window.getComputedStyle(list).paddingLeft) || 10;
        const start = item.offsetLeft - inset, end = item.offsetLeft + item.offsetWidth + inset;
        if (start < list.scrollLeft || end > list.scrollLeft + list.clientWidth) list.scrollLeft = Math.max(0, start);
      }
      text($("case-title"), (revision.current ? current.title : revision.title) || current.title || current.id);
      text($("result-case-code"), current.id);
      const badge = $("result-case-status");
      if (badge) { badge.className = "badge " + (revision.status || "SKIPPED").toLowerCase(); text(badge, executionVerdict(revision)); }
      const identity = selectedCase + ":" + revision.version;
      if (displayedHistory && displayedHistory.identity !== identity) {
        $("case-history-archive").appendChild(displayedHistory.detail);
        displayedHistory = null;
      }
      host.replaceChildren();
      host.hidden = !!revision.current;
      $("scenarios").hidden = !revision.current;
      if (!revision.current) {
        const detail = revision.has_result && (displayedHistory && displayedHistory.detail || archivedExecution(current, revision));
        if (detail) {
          detail.hidden = false; detail.open = true;
          host.appendChild(detail); displayedHistory = { identity, detail };
          ensureCaseExplorer(detail);
        } else {
          const empty = executionNode("div", "execution-empty");
          empty.append(executionNode("span", "execution-empty-icon", "◷"), executionNode("h2", "", executionVerdict(revision)),
            executionNode("p", "", revision.has_result ? "As evidências desta execução não estão disponíveis neste arquivo."
              : "Esta tentativa não tem passos ou evidências de um resultado concluído. Consulte outra execução na linha do tempo."));
          const latestResult = history.find((item) => item.current);
          if (latestResult) empty.appendChild(button("Ver último resultado disponível", () => selectExecution(latestResult.version), "button button-primary"));
          host.appendChild(empty);
        }
      }
      updateExecutionOverflow();
    }

    function updateCaseNavigation() {
      const current = byIndex.get(selectedCase);
      const position = cases.findIndex((item) => String(item.index) === selectedCase);
      const revision = selectedExecution(current);
      text($("case-title"), current ? current.title || current.id || "Caso de teste" : "Nenhum caso disponível");
      if ($("case-rerun")) {
        $("case-rerun").hidden = !reportConnection;
        $("case-rerun").disabled = !current || !revision.has_result;
        $("case-rerun").setAttribute("aria-label", current ? "Reexecutar teste " + current.id : "Reexecutar teste");
      }
      const feedback = $("case-rerun-feedback");
      if (feedback) { feedback.hidden = true; feedback.textContent = ""; }
      if ($("case-edit")) {
        $("case-edit").disabled = !revision.current;
        $("case-edit").title = revision.current ? "Editar passos do último resultado" : "Execuções anteriores são preservadas para consulta";
      }
      if ($("case-prev")) $("case-prev").disabled = position <= 0;
      if ($("case-next")) $("case-next").disabled = position < 0 || position >= cases.length - 1;
      const history = current && Array.isArray(current.execution_history) ? current.execution_history : [];
      if ($("case-history")) $("case-history").disabled = !history.length;
      text($("case-history-count"), history.length > 1 ? String(history.length) : "");
      all("#scenarios details.scenario[data-case-index]").forEach((detail) => {
        const selected = current && detail.dataset.caseIndex === selectedCase;
        detail.hidden = !selected;
        if (selected) { detail.open = true; ensureCaseExplorer(detail); }
      });
      renderExecutionHistory(current);
    }

    function closeCaseHistory() {
      const dialog = $("case-history-dialog");
      if (!dialog) return;
      if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open");
      const trigger = $("case-compare");
      if (trigger) trigger.focus({ preventScroll: true });
    }

    function openCaseHistory() {
      const current = byIndex.get(selectedCase);
      const dialog = $("case-history-dialog");
      const list = $("case-history-list"), content = $("case-history-detail");
      if (!current || !dialog || !list || !content || !Array.isArray(current.execution_history)) return;
      list.replaceChildren(); content.replaceChildren();
      text($("case-history-title"), "Histórico de " + current.id);
      const revisions = current.execution_history.slice().reverse();
      const controls = [];
      const select = (revision) => {
        content.replaceChildren();
        controls.forEach(({ control, value }) => {
          const selected = value === revision;
          control.classList.toggle("active", selected);
          control.setAttribute("aria-pressed", String(selected));
        });
        const label = "Execução #" + (revision.version + 1) + (revision.version === 0 ? " · Original" : "");
        text($("case-history-note"), label + (revision.current ? " · Resultado atual da lista de testes" : " · Versão histórica, somente leitura"));
        if (revision.current) {
          const message = document.createElement("p");
          message.textContent = "Esta é a versão mostrada na lista de testes. As versões anteriores continuam disponíveis na linha do tempo ao lado.";
          content.append(message, button("Ver resultado atual", () => { closeCaseHistory(); selectExecution(revision.version); }, "button button-primary"));
          return;
        }
        content.appendChild(button("Abrir esta execução na tela →", () => { closeCaseHistory(); selectExecution(revision.version); }, "button button-primary"));
        const latest = revisions.find((item) => item.current);
        if (latest && revision.has_result) {
          const comparison = executionNode("div", "execution-comparison");
          [revision, latest].forEach((entry) => {
            const card = executionNode("div", "execution-comparison-card");
            card.append(executionNode("strong", "", "Execução #" + (entry.version + 1) + (entry.current ? " · Último resultado" : " · Comparando")),
              executionNode("span", "", executionDate(entry)), executionNode("span", "", executionVerdict(entry) + " · Ação: " + executionDuration(entry.duration_seconds)),
              executionNode("small", "", (entry.steps_count ?? "—") + " etapas · " + (entry.signals ?? "—") + " sinais técnicos"));
            comparison.appendChild(card);
          });
          content.appendChild(comparison);
        }
        const archive = archivedExecution(current, revision);
        if (!revision.has_result || !archive) {
          const message = document.createElement("p");
          message.className = "reading-guide";
          message.textContent = !revision.has_result
            ? "Esta tentativa ainda não tem um resultado de teste registrado. Ela não substitui nem aprova o resultado anterior."
            : "As evidências desta versão não estão disponíveis neste arquivo.";
          content.appendChild(message);
          return;
        }
        const detail = archive.cloneNode(true);
        const enhancedSummary = detail.querySelector(".case-summary-panel"), enhancedTimeline = detail.querySelector(".evidence-timeline");
        if (enhancedSummary && enhancedTimeline) {
          detail.querySelector(".scenario-body").replaceChildren(...Array.from(enhancedSummary.childNodes), enhancedTimeline);
          detail.classList.remove("step-explorer");
          enhancedTimeline.classList.remove("case-timeline-panels");
          Array.from(enhancedTimeline.children).forEach((entry) => { entry.hidden = false; });
        }
        // The immutable archived card remains available for later visits. Its
        // visible clone gets a separate ID namespace, including internal links.
        const ids = new Map();
        [detail, ...all("[id]", detail)].filter((node) => node.id).forEach((node) => {
          const old = node.id; ids.set(old, old + "--view"); node.id = old + "--view";
        });
        [detail, ...all("[href],[aria-controls],[aria-labelledby],[aria-describedby],[for]", detail)].forEach((node) => {
          for (const attribute of ["aria-controls", "aria-labelledby", "aria-describedby", "for"]) {
            if (node.hasAttribute(attribute)) node.setAttribute(attribute, node.getAttribute(attribute).split(/\s+/).map((id) => ids.get(id) || id).join(" "));
          }
          const href = node.getAttribute("href");
          if (href && href[0] === "#" && ids.has(href.slice(1))) node.setAttribute("href", "#" + ids.get(href.slice(1)));
        });
        detail.hidden = false; detail.open = true;
        all("details", detail).forEach((node) => { node.open = true; });
        all("figure", detail).forEach(prepareCapturePreview);
        content.appendChild(detail);
        content.scrollTop = 0;
      };
      revisions.forEach((revision) => {
        if (!revision || !Number.isInteger(revision.version) || revision.version < 0) return;
        const control = button("", () => select(revision), "case-history-version");
        const title = document.createElement("strong");
        const newest = Math.max(...revisions.map((entry) => entry.version));
        title.textContent = "Execução #" + (revision.version + 1) + (revision.version === 0 ? " · Original" : "")
          + (revision.version === newest ? " · Mais recente" : revision.current ? " · Último resultado" : "");
        const date = document.createElement("time");
        const recorded = new Date(revision.started_at || revision.recorded_at || "");
        date.textContent = executionDate(revision);
        if (!Number.isNaN(recorded.getTime())) date.dateTime = recorded.toISOString();
        const result = document.createElement("span");
        result.className = "case-history-verdict";
        result.dataset.status = revision.status || "";
        result.textContent = executionVerdict(revision);
        const item = document.createElement("li");
        control.append(title, date, result); item.appendChild(control); list.appendChild(item);
        controls.push({ control, value: revision });
      });
      const active = selectedExecution(current);
      const first = !active.current ? active : revisions.find((revision) => !revision.current && revision.has_result) || revisions[0];
      if (first) select(first);
      if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
      if (controls.length) controls[0].control.focus();
    }

    function showView(view, focus) {
      if (stepPickerRequest) closeStepPicker();
      if ($("case-editor") && $("case-editor").hasAttribute("open")) closeEditor();
      if (!Object.prototype.hasOwnProperty.call(views, view)) view = "overview";
      activeView = view;
      document.body.dataset.view = view;
      syncResultHeader(view);
      all(".report-view[data-view]").forEach((section) => { section.hidden = section.dataset.view !== view; });
      all("[data-report-nav]").forEach((nav) => {
        const current = nav.dataset.reportNav === view;
        nav.classList.toggle("active", current);
        if (current) nav.setAttribute("aria-current", "page"); else nav.removeAttribute("aria-current");
      });
      text($("view-title"), views[view][0]);
      if (view === "journey" && $("view-title")) {
        text($("view-title"), "Jornada ");
        const accent = document.createElement("span");
        accent.className = "journey-heading-accent";
        accent.textContent = "observada";
        $("view-title").appendChild(accent);
      }
      text($("view-subtitle"), views[view][1]);
      if (view === "results") updateCaseNavigation();
      if (view === "evidence" || view === "journey") {
        const section = all(".report-view[data-view]").find((item) => item.dataset.view === view);
        if (section) all("img[data-source-shot]", section).forEach(hydrateImage);
      }
      if (view === "journey") scheduleJourneyLayout();
      const heading = view === "results" ? $("result-view-title") || $("view-title") : $("view-title");
      if (focus && heading) {
        heading.tabIndex = -1;
        heading.focus({ preventScroll: true });
        window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? "auto" : "smooth" });
      }
    }

    function navigate(view, focus = true) {
      if (!Object.prototype.hasOwnProperty.call(views, view)) return;
      showView(view, focus);
      const current = byIndex.get(selectedCase);
      const hash = view === "results" && current
        ? "#view/results/" + encodeURIComponent(String(current.id)) + (!selectedExecution(current).current ? "?execution=" + selectedExecution(current).version : "")
        : "#view/" + view;
      if (window.location.hash !== hash) window.history.replaceState(null, "", hash);
    }

    function openCase(index, focus = true) {
      const key = String(index);
      if (!byIndex.has(key)) return;
      closeJourneyPickers();
      selectedCase = key;
      navigate("results", focus);
    }

    function revealHash(focus = false) {
      let hash;
      try { hash = decodeURIComponent(window.location.hash.slice(1)); } catch (_) { showView("overview", focus); return; }
      if (hash.startsWith("timeline-")) {
        const target = document.getElementById(hash);
        const scenario = target && target.closest("details.scenario[data-case-index]");
        if (!scenario || !byIndex.has(scenario.dataset.caseIndex)) return;
        selectedCase = scenario.dataset.caseIndex;
        showView("results", false);
        selectStepForTarget(target);
        let parent = target.parentElement;
        while (parent) { if (parent.tagName === "DETAILS") parent.open = true; parent = parent.parentElement; }
        window.requestAnimationFrame(() => {
          target.scrollIntoView({ block: "nearest", behavior: "auto" });
          if (focus) { target.tabIndex = -1; target.focus({ preventScroll: true }); }
        });
        return;
      }
      const [routeHash, query] = hash.split("?");
      const parts = routeHash.split("/");
      if (parts[0] !== "view" || !Object.prototype.hasOwnProperty.call(views, parts[1])) {
        if (!hash) showView("overview", focus);
        return;
      }
      if (parts[1] === "results" && parts.length > 2) {
        const current = byId.get(parts.slice(2).join("/"));
        if (!current) return;
        selectedCase = String(current.index);
        const requested = query && query.match(/(?:^|&)execution=(\d+)(?:&|$)/);
        if (requested && executionHistory(current).some((entry) => entry.version === Number(requested[1]))) selectedExecutions.set(selectedCase, Number(requested[1]));
      }
      showView(parts[1], focus);
    }

    all("[data-report-nav]").forEach((nav) => nav.addEventListener("click", (event) => {
      event.preventDefault();
      navigate(nav.dataset.reportNav);
    }));
    document.addEventListener("keydown", (event) => {
      const row = event.target instanceof Element ? event.target : null;
      if (!row || !row.matches("#case-rows tr[data-case-index]")
        || event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
        || (event.key !== "Enter" && event.key !== " ")) return;
      event.preventDefault();
      openCase(row.dataset.caseIndex);
    });
    document.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      if ($("result-more") && !target.closest("#result-more")) $("result-more").removeAttribute("open");
      const open = target.closest("[data-open-case]");
      if (open) { event.preventDefault(); openCase(open.dataset.openCase); return; }
      const caseRow = target.closest("#case-rows tr[data-case-index]");
      if (caseRow) {
        // Keep native controls and copying table text usable. A plain click
        // anywhere else in the row opens its result exactly once.
        if (event.defaultPrevented || (event.button != null && event.button !== 0)
          || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
          || target.closest('a,button,input,select,textarea,summary,[role="button"],[contenteditable="true"]')) return;
        const selection = window.getSelection && window.getSelection();
        if (selection && !selection.isCollapsed && String(selection).trim()) return;
        event.preventDefault();
        openCase(caseRow.dataset.caseIndex);
        return;
      }
      const edit = target.closest("[data-edit-case], [data-edit-current]");
      if (edit) {
        event.preventDefault();
        if (edit.hasAttribute("data-edit-current") && !selectedExecution(byIndex.get(selectedCase)).current) return;
        openEditor(edit.hasAttribute("data-edit-current") ? selectedCase : edit.dataset.editCase, edit);
        return;
      }
      const page = target.closest("[data-journey-page]");
      if (page) {
        event.preventDefault();
        const panel = $(page.getAttribute("aria-controls"));
        if (!panel || !panel.classList.contains("journey-case-picker")) return;
        const wasOpen = !panel.hidden;
        closeJourneyPickers();
        if (!wasOpen) {
          panel.hidden = false;
          page.setAttribute("aria-expanded", "true");
          filterJourneyCases(panel);
          const first = panel.querySelector("[data-journey-search]") || panel.querySelector("[data-open-case], [data-edit-case], [data-journey-close]");
          if (first) first.focus();
          panel.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
        }
        return;
      }
      if (target.closest("[data-journey-close]")) closeJourneyPickers(true);
    });
    document.addEventListener("input", (event) => {
      const search = event.target instanceof Element ? event.target.closest("[data-journey-search]") : null;
      const panel = search && search.closest(".journey-case-picker");
      if (panel) filterJourneyCases(panel);
    });
    all("#step-picker [data-step-choice]").forEach((control) => control.addEventListener("click", () => {
      if (!$("step-picker").hasAttribute("open")) return;
      closeStepPicker(control.dataset.stepChoice);
    }));
    listen("step-picker-close", "click", () => closeStepPicker());
    listen("step-picker", "cancel", (event) => { event.preventDefault(); closeStepPicker(); });
    listen("step-picker", "keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeStepPicker();
    });
    listen("step-picker", "close", () => {
      // Native dialog.close() can be called independently. A queued close
      // event from a previous opening must not cancel a freshly opened picker.
      if (!stepPickerRequest || $("step-picker").hasAttribute("open")) return;
      const request = stepPickerRequest;
      stepPickerRequest = null;
      if (request.trigger) request.trigger.focus({ preventScroll: true });
    });
    listen("case-editor-close", "click", closeEditor);
    listen("case-editor", "cancel", (event) => { event.preventDefault(); closeEditor(); });
    listen("case-editor", "close", () => { editorDrag = null; if (editorTrigger) editorTrigger.focus(); });
    const plannerSteps = $("case-editor-steps");
    if (plannerSteps) plannerSteps.addEventListener("wheel", (event) => {
      // On short screens allow vertical access to card settings; otherwise
      // the ordinary mouse wheel follows the horizontal planning sequence.
      if (plannerSteps.scrollHeight <= plannerSteps.clientHeight + 2) scrollHorizontalList(plannerSteps, event);
    }, { passive: false });
    listen("case-editor-save", "click", () => {
      if (!editorCase) return;
      saveDraftSequence(editorPlanKey, editorSequence);
      const explorer = caseExplorers.get(editorCase);
      if (explorer) rebuildInlineSteps(explorer);
      editorMessage($("download-report")
        ? "Rascunho guardado nesta aba. Baixe o relatório para conservar esta versão. Resultados e evidências originais não foram alterados."
        : "Rascunho guardado nesta aba. O arquivo original não foi alterado. Nada foi executado.");
    });
    listen("case-editor-rerun", "click", () => {
      if (reportConnection && editorCase) {
        saveDraftSequence(editorPlanKey, editorSequence);
        const explorer = caseExplorers.get(editorCase);
        if (explorer) rebuildInlineSteps(explorer);
      }
      requestCaseRerun(editorCase, editorMessage);
    });
    listen("case-rerun", "click", () => openEditor(selectedCase, $("case-rerun"), true));
    listen("case-history", "click", openCaseHistory);
    listen("case-compare", "click", openCaseHistory);
    listen("case-execution-list", "scroll", updateExecutionOverflow);
    const executionHistoryRegion = $("case-execution-history");
    if (executionHistoryRegion) executionHistoryRegion.addEventListener("wheel", scrollExecutionHistory, { passive: false });
    listen("case-history-close", "click", closeCaseHistory);
    listen("case-history-dialog", "cancel", (event) => { event.preventDefault(); closeCaseHistory(); });
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.key !== "Escape") return;
      if ($("step-picker") && $("step-picker").hasAttribute("open")) { event.preventDefault(); closeStepPicker(); }
      else if ($("case-editor") && $("case-editor").hasAttribute("open")) { event.preventDefault(); closeEditor(); }
      else if ($("result-more") && $("result-more").hasAttribute("open")) { event.preventDefault(); $("result-more").removeAttribute("open"); }
      else closeJourneyPickers(true);
    });
    window.addEventListener("resize", scheduleJourneyLayout);
    window.addEventListener("resize", updateExecutionOverflow);
    window.addEventListener("afterprint", scheduleJourneyLayout);
    const executionList = $("case-execution-list");
    if (executionList && typeof window.ResizeObserver === "function") {
      new window.ResizeObserver(updateExecutionOverflow).observe(executionList);
    }
    const journeyRoute = document.querySelector(".journey-route");
    if (journeyRoute && typeof window.ResizeObserver === "function") {
      new window.ResizeObserver(scheduleJourneyLayout).observe(journeyRoute);
    }
    // Preserve links from the coverage ledger into original case timeline entries.
    document.addEventListener("click", (event) => {
      const link = event.target instanceof Element ? event.target.closest('a[href^="#timeline-"]') : null;
      if (!link) return;
      const hash = link.getAttribute("href");
      if (hash === window.location.hash) { event.preventDefault(); revealHash(true); }
    });
    window.addEventListener("hashchange", () => revealHash(true));
    listen("case-back", "click", () => navigate("tests"));
    listen("case-prev", "click", () => {
      if ($("result-more")) $("result-more").removeAttribute("open");
      const index = cases.findIndex((item) => String(item.index) === selectedCase);
      if (index > 0) openCase(cases[index - 1].index);
    });
    listen("case-next", "click", () => {
      if ($("result-more")) $("result-more").removeAttribute("open");
      const index = cases.findIndex((item) => String(item.index) === selectedCase);
      if (index >= 0 && index + 1 < cases.length) openCase(cases[index + 1].index);
    });

    function fillSelect(id, property, placeholder, labels = {}) {
      const select = $(id);
      if (!select) return;
      const distinct = Array.from(new Set(cases.map((item) => String(item[property] || "")).filter(Boolean)))
        .sort((a, b) => a.localeCompare(b, "pt-BR"));
      select.replaceChildren();
      const first = document.createElement("option");
      first.value = "";
      first.textContent = placeholder;
      select.appendChild(first);
      distinct.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = labels[value] || value;
        select.appendChild(option);
      });
    }

    function matchesFlow(item, identity) {
      return identity === "all" || (identity === "unclassified" ? !membershipsOf(item).length : membershipsOf(item).some((membership) => membership.id === identity));
    }

    function updateFunctionalFlows(baseCases) {
      all("[data-flow-all-count]").forEach((element) => text(element, baseCases.length));
      all("[data-flow-unclassified-count]").forEach((element) => text(element, baseCases.filter((item) => !membershipsOf(item).length).length));
      all(".functional-flow-card[data-select-flow]").forEach((card) => {
        const flowId = card.dataset.selectFlow;
        const related = baseCases.filter((item) => matchesFlow(item, flowId));
        const counts = { PASS: 0, FAIL: 0, BLOCKED: 0, SKIPPED: 0 };
        related.forEach((item) => { counts[Object.hasOwn(counts, item.status) ? item.status : "SKIPPED"] += 1; });
        text(card.querySelector("[data-flow-total]"), related.length);
        all("[data-flow-status]", card).forEach((element) => text(element, counts[element.dataset.flowStatus] || 0));
        const evaluated = counts.PASS + counts.FAIL;
        text(card.querySelector("[data-flow-approval]"), evaluated
          ? String(Math.round(counts.PASS * 1000 / evaluated) / 10).replace(".", ",") + "% de aprovação · " + evaluated + "/" + related.length + " CTs avaliados"
          : "Sem CTs avaliados (aprovado/reprovado)");
        const failure = card.querySelector("[data-flow-failure]");
        if (failure) failure.hidden = !counts.FAIL;
        const suggested = related.filter((item) => membershipsOf(item).some((membership) => membership.id === flowId && membership.confidence === "suggested")).length;
        const suggestions = card.querySelector("[data-flow-suggestions]");
        if (suggestions) { text(suggestions, suggested + " associação(ões) sugerida(s)"); suggestions.hidden = !suggested; }
      });
      all("[data-select-flow]").forEach((control) => control.setAttribute("aria-pressed", String(control.dataset.selectFlow === selectedFlow)));
      const active = $("active-flow-filter");
      if (active) active.hidden = selectedFlow === "all";
      const name = selectedFlow === "unclassified" ? "Não classificados" : (flowsById.get(selectedFlow) || {}).name || "Todos os testes";
      text($("active-flow-name"), "Filtro de fluxo: " + name);
      text($("flow-table-context"), filteredCases.length + " CT(s) nos resultados · " + (selectedFlow === "all" ? "todos os testes, sem filtro de fluxo." : name + "."));
    }

    function filterCases(resetPage = true) {
      if (resetPage) pageNumber = 1;
      const term = normalize($("search") ? $("search").value : "");
      const category = $("category-filter") ? $("category-filter").value : "";
      const page = $("page-filter") ? $("page-filter").value : "";
      const baseCases = cases.filter((item) => (
        (selectedStatus === "ALL" || item.status === selectedStatus)
        && (!category || String(item.category) === category)
        && (!page || String(item.page) === page)
        && (!term || normalize([item.id, item.title, categoryLabels[item.category] || item.category, item.page, item.url, item.search,
          ...membershipsOf(item).map((membership) => membership.name)].join(" ")).includes(term))
      ));
      filteredCases = baseCases.filter((item) => matchesFlow(item, selectedFlow));
      updateFunctionalFlows(baseCases);
      const requestedSize = Number($("page-size") ? $("page-size").value : 15);
      const size = Number.isFinite(requestedSize) && requestedSize > 0 ? Math.floor(requestedSize) : 15;
      const count = Math.max(1, Math.ceil(filteredCases.length / size));
      pageNumber = Math.min(Math.max(pageNumber, 1), count);
      const start = (pageNumber - 1) * size;
      const visible = new Set(filteredCases.slice(start, start + size).map((item) => String(item.index)));
      all("#case-rows tr[data-case-index]").forEach((row) => { row.hidden = !visible.has(row.dataset.caseIndex); });
      text($("table-count"), filteredCases.length
        ? "Mostrando " + (start + 1) + "–" + Math.min(start + size, filteredCases.length) + " de " + filteredCases.length + " casos"
        : "Nenhum caso corresponde aos filtros.");
      const pagination = $("pagination");
      if (pagination) {
        pagination.replaceChildren();
        const previous = button("Anterior", () => { pageNumber -= 1; filterCases(false); }, "pagination-button");
        previous.disabled = pageNumber === 1;
        pagination.appendChild(previous);
        // Bound the number of controls for long reports while keeping first/last reachable.
        const positions = new Set([1, count, pageNumber - 1, pageNumber, pageNumber + 1]);
        let last = 0;
        Array.from(positions).filter((value) => value > 0 && value <= count).sort((a, b) => a - b).forEach((value) => {
          if (last && value - last > 1) { const gap = document.createElement("span"); gap.textContent = "…"; gap.setAttribute("aria-hidden", "true"); pagination.appendChild(gap); }
          const control = button(String(value), () => { pageNumber = value; filterCases(false); }, "pagination-button");
          control.setAttribute("aria-label", "Página " + value);
          if (value === pageNumber) { control.classList.add("active"); control.setAttribute("aria-current", "page"); }
          pagination.appendChild(control);
          last = value;
        });
        const next = button("Próxima", () => { pageNumber += 1; filterCases(false); }, "pagination-button");
        next.disabled = pageNumber === count;
        pagination.appendChild(next);
      }
      all("#filters [data-status]").forEach((control) => {
        const active = control.dataset.status === selectedStatus;
        control.classList.toggle("active", active);
        control.setAttribute("aria-pressed", String(active));
      });
    }

    fillSelect("category-filter", "category", "Todas as categorias", categoryLabels);
    fillSelect("page-filter", "page", "Todas as páginas");
    if ($("filters")) {
      $("filters").replaceChildren();
      Object.keys(statusLabels).forEach((status) => {
        const control = button(statusLabels[status], () => { selectedStatus = status; filterCases(); }, "filter");
        control.dataset.status = status;
        $("filters").appendChild(control);
      });
    }
    listen("search", "input", () => filterCases());
    all("[data-select-flow]").forEach((control) => control.addEventListener("click", () => {
      const identity = control.dataset.selectFlow;
      if (!["all", "unclassified"].includes(identity) && !flowsById.has(identity)) return;
      selectedFlow = identity === selectedFlow ? "all" : identity;
      filterCases();
    }));
    listen("clear-flow-filter", "click", () => {
      selectedFlow = "all";
      filterCases();
    });
    ["category-filter", "page-filter", "page-size"].forEach((id) => listen(id, "change", () => filterCases()));
    listen("clear-filters", "click", () => {
      ["search", "category-filter", "page-filter"].forEach((id) => { if ($(id)) $(id).value = ""; });
      selectedStatus = "ALL";
      selectedFlow = "all";
      filterCases();
    });

    function csvCell(value) {
      let cell = String(value == null ? "" : value);
      // Spreadsheet programs may execute values whose first meaningful character
      // is a formula marker, including after whitespace or control characters.
      if (/^[\s\u0000-\u001f\u007f-\u009f]*[=+\-@]/.test(cell) || /^[\t\r\n]/.test(cell)) cell = "'" + cell;
      return '"' + cell.replace(/"/g, '""') + '"';
    }
    listen("download-csv", "click", () => {
      const rows = [["CT", "Título", "Status", "Categoria", "Página", "URL", "Duração (segundos)", "Sinais técnicos", "Fluxos funcionais", "Confiança da associação", "Motivo da associação"]];
      filteredCases.forEach((item) => rows.push([
        item.id, item.title, verdictLabels[item.status] || item.status, categoryLabels[item.category] || item.category,
        item.page, item.url, item.duration_seconds == null ? "" : item.duration_seconds,
        Array.isArray(item.signals) ? item.signals.length : item.signals,
        membershipsOf(item).map((membership) => membership.name).join(" | ") || "Não classificado",
        membershipsOf(item).map((membership) => membership.name + ": " + (membership.confidence === "identified" ? "Identificado" : "Sugerido")).join(" | "),
        membershipsOf(item).map((membership) => membership.name + ": " + membership.reason).join(" | ") || (item.flow_classification || {}).unclassified_reason || "Metadados insuficientes.",
      ]));
      saveBlob(new Blob(["\ufeff" + rows.map((row) => row.map(csvCell).join(";")).join("\r\n")], { type: "text/csv;charset=utf-8" }), "autojev-casos.csv");
    });
    listen("download-report", "click", async () => {
      const control = $("download-report");
      if (!data.online_preview) { saveBlob(new Blob([exportDocument()], { type: "text/html;charset=utf-8" }), "autojev-relatorio.html"); return; }
      if (control.disabled) return;
      control.disabled = true; control.setAttribute("aria-busy", "true");
      try {
        if (!reportConnection || !reportConnection.apiUrl) throw new Error("Application connection unavailable");
        const url = new URL(reportConnection.apiUrl + data.online_preview.export_path);
        const expected = new URL(reportConnection.apiUrl + "/test-suites/" + encodeURIComponent(reportConnection.sourceId) + "/report");
        if (url.origin !== expected.origin || url.pathname !== expected.pathname || url.searchParams.has("preview") || url.hash) throw new Error("Invalid export path");
        const response = await window.fetch(url.href, { cache: "no-store", credentials: "omit", redirect: "error" });
        if (!response.ok) throw new Error("Report unavailable");
        const html = (await response.text()).replace(/(<script\b(?=[^>]*\bid=["']report-drafts["'])[^>]*>)[\s\S]*?(<\/script\s*>)/i,
          (_match, start, end) => start + serializeDrafts() + end);
        saveBlob(new Blob([html], { type: "text/html;charset=utf-8" }), "autojev-relatorio.html");
      } catch { downloadFeedback(control, "Não foi possível baixar o relatório completo com os prints. Tente novamente."); }
      finally { control.disabled = false; control.removeAttribute("aria-busy"); }
    });
    listen("print-report", "click", () => window.print());

    let printState = null;
    function preparePrint() {
      if (printState) return;
      // Printing is a full-document view, not just the current table page or
      // search result. These presentation changes are restored even on cancel.
      const reveal = all(".report-view[data-view], #scenarios details.scenario[data-case-index], #case-rows tr[data-case-index], #evidence-grid .evidence-card, #execution-events [data-event-kind], [data-coverage-panel], .case-summary-panel, .case-timeline-panels > .timeline-entry");
      printState = {
        hidden: reveal.map((element) => [element, element.hidden]),
        details: all("details").map((element) => [element, element.open]),
        styles: new Map(),
      };
      reveal.forEach((element) => { element.hidden = false; });
      printState.details.forEach(([element]) => { element.open = true; });
      if (activeView === "results" && displayedHistory) {
        // Print the selected historical evidence under its own execution date.
        all('.report-view[data-view], #scenarios').forEach((element) => {
          if (element.dataset.view === "results") return;
          printState.styles.set(element, element.getAttribute("style"));
          element.style.setProperty("display", "none", "important");
        });
      }
      if (!document.body.classList.contains("hide-screenshots")) {
        // Unvisited cases have not built their step explorer yet. Their action
        // captures still need the exact same target overlay in the printed report.
        all("figure.action-preview").forEach(prepareCapturePreview);
        all("img[data-source-shot]").forEach(hydrateImage);
      }
      // More specific print CSS may expose content from hidden details. Honor
      // the user's explicit visibility choices using temporary inline styles.
      const preferences = [
        ["hide-screenshots", '.shots, .journey-node img, .evidence-card[data-evidence-kind="screenshot"]'],
        ["hide-technical", '.timeline-entry.signal, .signal-detail, .evidence-card[data-evidence-kind="log"]'],
        ["hide-observations", ".execution-observations"],
      ];
      preferences.forEach(([className, selector]) => {
        if (!document.body.classList.contains(className)) return;
        all(selector).forEach((element) => {
          if (!printState.styles.has(element)) printState.styles.set(element, element.getAttribute("style"));
          element.style.setProperty("display", "none", "important");
        });
      });
    }
    function restoreAfterPrint() {
      if (!printState) return;
      printState.hidden.forEach(([element, hidden]) => { element.hidden = hidden; });
      printState.details.forEach(([element, open]) => { element.open = open; });
      printState.styles.forEach((style, element) => {
        if (style == null) element.removeAttribute("style"); else element.setAttribute("style", style);
      });
      printState = null;
    }
    window.addEventListener("beforeprint", preparePrint);
    window.addEventListener("afterprint", restoreAfterPrint);

    let evidenceFilter = "all";
    function filterEvidence() {
      const term = normalize($("evidence-search") ? $("evidence-search").value : "");
      all("#evidence-grid .evidence-card").forEach((card) => {
        card.hidden = (evidenceFilter !== "all" && card.dataset.evidenceKind !== evidenceFilter)
          || (term !== "" && !normalize([card.dataset.search || "", card.textContent].join(" ")).includes(term));
      });
      all("[data-evidence-filter]").forEach((control) => {
        const active = control.dataset.evidenceFilter === evidenceFilter;
        control.classList.toggle("active", active);
        control.setAttribute("aria-pressed", String(active));
      });
    }
    all("[data-evidence-filter]").forEach((control) => control.addEventListener("click", () => {
      evidenceFilter = control.dataset.evidenceFilter;
      filterEvidence();
    }));
    listen("evidence-search", "input", filterEvidence);
    let previewTrigger = null, lightbox = null;

    function fitEvidenceLightbox(reset = false) {
      if (!lightbox || !lightbox.dialog.hasAttribute("open") || !lightbox.active) return;
      const { viewport, surface, canvas, image, active, zoom } = lightbox;
      const width = viewport.clientWidth, height = viewport.clientHeight;
      const naturalWidth = active.image.naturalWidth || image.naturalWidth;
      const naturalHeight = active.image.naturalHeight || image.naturalHeight;
      if (!(width > 0 && height > 0 && naturalWidth > 0 && naturalHeight > 0)) return;
      const centerX = (viewport.scrollLeft + width / 2) / (surface.offsetWidth || width);
      const centerY = (viewport.scrollTop + height / 2) / (surface.offsetHeight || height);
      const gutter = width < 600 ? 10 : 58, padding = width < 600 ? 10 : 20;
      const scale = Math.min(Math.max(1, width - gutter * 2) / naturalWidth, Math.max(1, height - padding * 2) / naturalHeight) * zoom;
      const imageWidth = naturalWidth * scale, imageHeight = naturalHeight * scale;
      const surfaceWidth = Math.max(width, imageWidth + gutter * 2), surfaceHeight = Math.max(height, imageHeight + padding * 2);
      canvas.style.setProperty("width", imageWidth + "px"); canvas.style.setProperty("height", imageHeight + "px");
      surface.style.setProperty("width", surfaceWidth + "px"); surface.style.setProperty("height", surfaceHeight + "px");
      viewport.scrollLeft = reset ? 0 : centerX * surfaceWidth - width / 2;
      viewport.scrollTop = reset ? 0 : centerY * surfaceHeight - height / 2;
    }

    function zoomEvidenceLightbox(value) {
      if (!lightbox) return;
      lightbox.finishPan();
      lightbox.zoom = Math.max(1, Math.min(4, value));
      lightbox.viewport.dataset.panEnabled = String(lightbox.zoom > 1);
      text(lightbox.reset, Math.round(lightbox.zoom * 100) + "%");
      lightbox.minus.disabled = lightbox.zoom === 1; lightbox.plus.disabled = lightbox.zoom === 4;
      fitEvidenceLightbox(lightbox.zoom === 1);
    }

    function prepareEvidenceLightbox() {
      if (lightbox) return lightbox;
      const dialog = $("evidence-dialog"), image = $("evidence-preview");
      if (!dialog || !image) return null;
      const make = (tag, className) => { const node = document.createElement(tag); node.className = className; return node; };
      const iconButton = (icon, label, action, className = "") => {
        const node = button("", action, "button lightbox-tool " + className);
        node.appendChild(evidenceIcon(icon)); node.setAttribute("aria-label", label); node.setAttribute("title", label); return node;
      };
      dialog.classList.add("evidence-lightbox"); dialog.setAttribute("aria-labelledby", "evidence-caption");
      const toolbar = make("div", "dialog-toolbar evidence-lightbox-toolbar"), title = make("div", "evidence-lightbox-title");
      const counter = make("small", "evidence-lightbox-counter"); counter.setAttribute("role", "status"); counter.setAttribute("aria-live", "polite");
      title.append($("evidence-caption"), counter);
      const tools = make("div", "evidence-lightbox-tools");
      const minus = iconButton("minus", "Diminuir zoom da tela cheia", () => zoomEvidenceLightbox(lightbox.zoom - .25));
      const reset = button("100%", () => zoomEvidenceLightbox(1), "button lightbox-tool lightbox-zoom-value");
      reset.setAttribute("aria-label", "Restaurar zoom da tela cheia");
      const plus = iconButton("plus", "Aumentar zoom da tela cheia", () => zoomEvidenceLightbox(lightbox.zoom + .25));
      const fit = iconButton("expand", "Ajustar imagem à tela", () => zoomEvidenceLightbox(1));
      const download = $("download-evidence"), close = $("close-evidence");
      download.className = "button lightbox-tool"; download.replaceChildren(evidenceIcon("download"));
      download.setAttribute("aria-label", "Baixar imagem original"); download.setAttribute("title", "Baixar imagem original");
      close.className = "button lightbox-tool lightbox-close"; close.replaceChildren(evidenceIcon("close")); close.setAttribute("title", "Fechar (Esc)");
      tools.append(minus, reset, plus, fit, download, close); toolbar.append(title, tools);
      const stage = make("div", "evidence-lightbox-stage"), viewport = make("div", "dialog-content evidence-lightbox-viewport");
      viewport.tabIndex = 0; viewport.setAttribute("aria-label", "Imagem em tela cheia; amplie e arraste com o botão esquerdo para explorar");
      const surface = make("div", "evidence-lightbox-surface");
      const canvas = image.closest(".action-modal-canvas") || make("div", "action-modal-canvas");
      canvas.appendChild(image); surface.appendChild(canvas); viewport.appendChild(surface);
      const previous = iconButton("previous", "Imagem anterior", () => showEvidenceLightboxItem(lightbox.index - 1), "lightbox-previous");
      const next = iconButton("next", "Próxima imagem", () => showEvidenceLightboxItem(lightbox.index + 1), "lightbox-next");
      stage.append(viewport, previous, next); dialog.replaceChildren(toolbar, stage);
      lightbox = { dialog, image, canvas, viewport, surface, counter, previous, next, minus, plus, reset,
        gallery: [], index: 0, zoom: 1, active: null,
        finishPan: enableEvidencePanning(viewport, (node) => node.closest(".action-modal-canvas")) };
      image.addEventListener("load", () => fitEvidenceLightbox());
      window.addEventListener("resize", () => fitEvidenceLightbox());
      if (typeof window.ResizeObserver === "function") new window.ResizeObserver(() => fitEvidenceLightbox()).observe(viewport);
      return lightbox;
    }

    function showEvidenceLightboxItem(index) {
      if (!lightbox || index < 0 || index >= lightbox.gallery.length) return;
      const item = lightbox.gallery[index]; hydrateImage(item.image);
      const src = item.image.getAttribute("src") || "";
      if (!isEvidenceImageSource(src)) return;
      lightbox.finishPan(); lightbox.index = index; lightbox.active = item;
      lightbox.image.src = src; lightbox.image.alt = item.image.alt || "Captura de evidência";
      setActionMarker(lightbox.canvas, lightbox.image, item.image.closest("figure.action-preview"));
      text($("evidence-caption"), item.caption || item.filename || "Captura de evidência");
      text(lightbox.counter, index + 1 + " de " + lightbox.gallery.length);
      lightbox.previous.disabled = index === 0; lightbox.next.disabled = index === lightbox.gallery.length - 1;
      lightbox.previous.hidden = lightbox.next.hidden = lightbox.gallery.length < 2;
      $("download-evidence").href = src; $("download-evidence").download = item.filename || "autojev-evidencia.png";
      zoomEvidenceLightbox(1);
    }

    function closeEvidence() {
      const dialog = $("evidence-dialog");
      if (!dialog) return;
      if (lightbox) lightbox.finishPan();
      document.body.classList.remove("evidence-lightbox-open");
      if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open");
      if (previewTrigger) previewTrigger.focus();
    }
    function openEvidencePreview(original, control, caption, filename) {
      const dialog = $("evidence-dialog");
      if (!original || !dialog || !$("evidence-preview")) return;
      hydrateImage(original);
      const src = original.getAttribute("src") || "";
      if (!isEvidenceImageSource(src)) return;
      const viewer = prepareEvidenceLightbox();
      if (!viewer) return;
      previewTrigger = control;
      const detail = original.closest("details.scenario");
      const grid = !detail && original.closest(".evidence-grid");
      const candidates = detail ? all(".shots figure", detail).filter((figure) =>
        !(document.body.classList.contains("hide-technical") && figure.closest(".timeline-entry.signal")))
        : grid ? all(".evidence-card", grid).filter((card) => !card.hidden && card.querySelector("img")) : [];
      viewer.gallery = candidates.map((node) => {
        const image = node.querySelector("img");
        const filename = (node.querySelector("figcaption") || {}).textContent || node.dataset.filename || image && image.dataset.sourceShot || "evidencia.png";
        return { image, filename: filename.trim(), caption: filename.trim() };
      }).filter((item) => {
        if (!item.image) return false;
        hydrateImage(item.image);
        return isEvidenceImageSource(item.image.getAttribute("src") || "");
      });
      let index = viewer.gallery.findIndex((item) => item.image === original);
      if (index < 0) { index = viewer.gallery.length; viewer.gallery.push({ image: original }); }
      viewer.gallery[index] = { image: original, caption: caption || filename || original.alt || "Captura de evidência",
        filename: filename || original.dataset.sourceShot || "autojev-evidencia.png" };
      showEvidenceLightboxItem(index);
      document.body.classList.add("evidence-lightbox-open");
      if (!dialog.hasAttribute("open")) {
        if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
      }
      fitEvidenceLightbox(true);
      if ($("close-evidence")) $("close-evidence").focus();
    }
    all("[data-preview-evidence]").forEach((control) => control.addEventListener("click", () => {
      const card = control.closest(".evidence-card");
      if (card) openEvidencePreview(card.querySelector("img"), control, card.dataset.caption, card.dataset.filename);
    }));
    document.addEventListener("click", (event) => {
      const control = event.target instanceof Element ? event.target.closest("[data-step-preview]") : null;
      if (!control) return;
      const figure = control.closest("figure");
      if (!figure) return;
      const filename = (figure.querySelector("figcaption") || {}).textContent || "autojev-evidencia.png";
      openEvidencePreview(figure.querySelector("img"), control, filename, filename);
    });
    listen("close-evidence", "click", closeEvidence);
    listen("evidence-dialog", "click", (event) => {
      if (event.target !== $("evidence-dialog")) return;
      const rect = event.target.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeEvidence();
    });
    listen("evidence-dialog", "close", () => {
      if (lightbox) lightbox.finishPan();
      document.body.classList.remove("evidence-lightbox-open");
      if (previewTrigger) previewTrigger.focus();
    });
    listen("evidence-dialog", "cancel", (event) => { event.preventDefault(); closeEvidence(); });
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || !$("evidence-dialog") || !$("evidence-dialog").hasAttribute("open")) return;
      if (event.key === "Escape") { event.preventDefault(); closeEvidence(); return; }
      if (!lightbox || event.ctrlKey || event.metaKey || event.altKey || event.target instanceof Element
        && event.target.closest('input,select,textarea,[contenteditable="true"]')) return;
      if (event.key === "ArrowLeft") { event.preventDefault(); showEvidenceLightboxItem(lightbox.index - 1); }
      else if (event.key === "ArrowRight") { event.preventDefault(); showEvidenceLightboxItem(lightbox.index + 1); }
      else if (["+", "="].includes(event.key)) { event.preventDefault(); zoomEvidenceLightbox(lightbox.zoom + .25); }
      else if (event.key === "-") { event.preventDefault(); zoomEvidenceLightbox(lightbox.zoom - .25); }
      else if (event.key === "0") { event.preventDefault(); zoomEvidenceLightbox(1); }
    });

    function filterTimeline() {
      const term = normalize($("timeline-search") ? $("timeline-search").value : "");
      const kind = $("timeline-kind") ? $("timeline-kind").value : "";
      all("#execution-events [data-event-kind]").forEach((event) => {
        event.hidden = (kind !== "" && kind !== "all" && kind !== "ALL" && event.dataset.eventKind !== kind)
          || (term !== "" && !normalize(event.textContent).includes(term));
      });
    }
    listen("timeline-search", "input", filterTimeline);
    listen("timeline-kind", "change", filterTimeline);
    all("[data-coverage-tab]").forEach((control) => control.addEventListener("click", () => {
      const tab = control.dataset.coverageTab;
      if (!["pages", "rules", "restrictions"].includes(tab)) return;
      all("[data-coverage-panel]").forEach((panel) => { panel.hidden = panel.dataset.coveragePanel !== tab; });
      all("[data-coverage-tab]").forEach((item) => {
        const active = item.dataset.coverageTab === tab;
        item.classList.toggle("active", active);
        item.setAttribute("aria-selected", String(active));
        item.setAttribute("aria-pressed", String(active));
      });
    }));

    listen("settings-form", "submit", (event) => {
      event.preventDefault();
      [["show-screenshots", "hide-screenshots"], ["show-technical", "hide-technical"], ["show-observations", "hide-observations"]]
        .forEach(([id, className]) => { if ($(id)) document.body.classList.toggle(className, !$(id).checked); });
      document.body.dataset.theme = $("theme-select") && $("theme-select").value === "light" ? "light" : "dark";
      document.body.dataset.density = $("density-select") && $("density-select").value === "compact" ? "compact" : "comfortable";
      caseExplorers.forEach(syncCaseStepAvailability);
      text($("settings-feedback"), "Preferências aplicadas neste relatório. Os resultados e as evidências originais foram preservados.");
    });
    listen("download-journey", "click", () => {
      const diagram = $("journey-svg");
      if (!diagram || diagram.tagName.toLowerCase() !== "svg") return;
      const copy = diagram.cloneNode(true);
      copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      saveBlob(new Blob([new XMLSerializer().serializeToString(copy)], { type: "image/svg+xml;charset=utf-8" }), "autojev-jornada.svg");
    });

    // Connected app reports follow the latest durable CT versions. A saved
    // offline HTML has no runtime connection and never makes these requests.
    function followReportRevisions() {
      if (!reportConnection || !reportConnection.apiUrl || typeof window.fetch !== "function"
        || typeof data.scenario_revisions_key !== "string" || !data.scenario_revisions_key) return;
      let checking = false, stopped = false, refreshNotice = null, revisionTimer = null;
      const base = reportConnection.apiUrl + "/explorations/" + encodeURIComponent(reportConnection.sourceId);
      const options = { cache: "no-store", credentials: "omit", redirect: "error" };
      const hasLocalWork = () => savedDrafts.size > 0
        || (activeView === "results" && !selectedExecution(byIndex.get(selectedCase)).current)
        || ["case-editor", "step-picker", "case-history-dialog", "evidence-dialog"].some((id) => $(id) && $(id).hasAttribute("open"));
      const stop = () => {
        stopped = true;
        if (revisionTimer != null) window.clearInterval(revisionTimer);
        window.removeEventListener("focus", check);
        document.removeEventListener("visibilitychange", check);
      };
      const refresh = async (explicit = false) => {
        if (stopped) return;
        if (explicit && savedDrafts.size && !window.confirm("Atualizar os resultados descarta os passos extras deste rascunho. Baixe o relatório para preservá-los antes de continuar. Deseja atualizar agora?")) return;
        const response = await window.fetch(base + "/report", options);
        if (!response.ok) throw new Error("Relatório atualizado ainda não disponível");
        const markup = await response.text();
        const parsed = new DOMParser().parseFromString(markup, "text/html");
        const newSource = parsed.getElementById("report-data");
        let next;
        try { next = JSON.parse(newSource && newSource.textContent || "{}"); } catch { return; }
        if (!parsed.body.classList.contains("report-premium") || next.run_id !== data.run_id
          || !next.scenario_revisions_key || next.scenario_revisions_key === data.scenario_revisions_key) return;
        // A user can open history or start a draft while the response is in
        // flight. Re-check immediately before replacing the visible document.
        if (stopped) return;
        if (!explicit && hasLocalWork()) { notify(); return; }
        parsed.querySelectorAll("#report-app-runtime").forEach((node) => node.remove());
        const runtime = parsed.createElement("script");
        runtime.id = "report-app-runtime"; runtime.type = "application/json";
        runtime.textContent = JSON.stringify({ app_url: reportConnection.appUrl,
          source_exploration_id: reportConnection.sourceId, api_url: reportConnection.apiUrl }).replace(/</g, "\\u003c");
        parsed.head.prepend(runtime);
        stop();
        document.open(); document.write("<!DOCTYPE html>\n" + parsed.documentElement.outerHTML); document.close();
      };
      const notify = () => {
        if (refreshNotice) return;
        refreshNotice = document.createElement("div");
        refreshNotice.className = "report-revision-refresh"; refreshNotice.setAttribute("role", "status");
        const message = document.createElement("span");
        message.textContent = "Há uma nova versão deste relatório. Seu rascunho ou histórico aberto foi mantido.";
        const update = button("Atualizar resultados", () => {
          update.disabled = true;
          refresh(true).catch(() => { message.textContent = "Não foi possível atualizar agora. Tente novamente."; })
            .finally(() => { update.disabled = false; });
        }, "button button-primary");
        refreshNotice.append(message, update); document.body.appendChild(refreshNotice);
      };
      async function check() {
        if (checking || stopped || document.visibilityState === "hidden") return;
        checking = true;
        try {
          const response = await window.fetch(base + "/scenario-revisions", options);
          if (!response.ok) return;
          const revisions = await response.json();
          if (typeof revisions.key !== "string" || revisions.key === data.scenario_revisions_key) return;
          if (hasLocalWork()) notify(); else await refresh();
        } catch (_) { /* Offline/disconnected reports keep their last complete snapshot. */ }
        finally { checking = false; }
      }
      revisionTimer = window.setInterval(check, 10000);
      window.addEventListener("focus", check); document.addEventListener("visibilitychange", check);
      window.addEventListener("pagehide", stop, { once: true });
    }

    filterCases();
    filterEvidence();
    filterTimeline();
    showView("overview", false);
    revealHash(false);
    followReportRevisions();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initializeReport, { once: true });
  else initializeReport();
})();
