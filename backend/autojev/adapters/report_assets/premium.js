(function () {
  "use strict";

  function initializeReport() {
    const source = document.getElementById("report-data");
    if (!source || !document.body.classList.contains("report-premium")) return;
    let data;
    try { data = JSON.parse(source.textContent || "{}"); } catch (_) { return; }
    // Capture a self-contained export before temporary filters or enhancements.
    // Only the detached copy loses the report-download control; the live report
    // keeps it, while the exported file retains printing and evidence downloads.
    const originalDocument = (() => {
      if (!document.getElementById("download-report")) return null;
      const exportRoot = document.documentElement.cloneNode(true);
      const downloadControl = exportRoot.querySelector("#download-report");
      if (downloadControl) downloadControl.remove();
      const appRuntime = exportRoot.querySelector("#report-app-runtime");
      if (appRuntime) appRuntime.remove();
      return "<!DOCTYPE html>\n" + exportRoot.outerHTML;
    })();
    const cases = Array.isArray(data.cases) ? data.cases : [];
    const byIndex = new Map(cases.map((item) => [String(item.index), item]));
    const byId = new Map(cases.map((item) => [String(item.id), item]));
    const $ = (id) => document.getElementById(id);
    const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
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
        return { appUrl: url.origin, sourceId: value.source_exploration_id };
      } catch { return null; }
    })();
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
    const prefersReducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let activeView = "overview";
    let selectedCase = cases.length ? String(cases[0].index) : null;
    let selectedStatus = "ALL";
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

    function originalEditorSteps(key) {
      const detail = all("#scenarios details.scenario[data-case-index]").find((item) => item.dataset.caseIndex === key);
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
        const key = String(item.index);
        if (!Object.prototype.hasOwnProperty.call(value.cases, key) || !Array.isArray(value.cases[key])) return;
        const count = originalEditorSteps(key).length;
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

    function caseCapturePlan(key) {
      // Construct a new minimal object, not a serialization of arbitrary draft
      // data. SWS/SQL can request a print, never execution of an integration.
      const count = originalEditorSteps(String(key)).length;
      const ids = new Set();
      const checkpoints = (savedDrafts.get(String(key)) || []).filter((item) => {
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
      if (editor && editor.hasAttribute("open") && editorCase === String(key)
        && JSON.stringify(draftsInSequence(editorSequence)) !== JSON.stringify(savedDrafts.get(String(key)) || [])) {
        feedback("Guarde o rascunho antes de reexecutar para incluir as pausas para print que você acabou de editar. Nenhuma reexecução foi solicitada.");
        return;
      }
      const plan = caseCapturePlan(key);
      const target = reportConnection.appUrl + "/#/reexecutar/" + encodeURIComponent(reportConnection.sourceId) + "/" + encodeURIComponent(String(current.id))
        + (plan ? "?capture_plan=" + encodeURIComponent(JSON.stringify(plan)) : "");
      window.open(target, "_blank", "noopener,noreferrer");
      feedback("Confirmação de reexecução solicitada na aplicação. Nenhuma ação foi iniciada por este relatório. "
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
      const insert = (position, type = "database") => {
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
        if (dragged === "new") { insert(position); return; }
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
        const control = button("+ Adicionar aqui", () => openStepPicker(control, gap.getAttribute("aria-label"), (type) => insert(position, type), editorCase), "button button-ghost case-editor-insert");
        control.disabled = placeholderCount >= draftLimit;
        control.setAttribute("aria-label", "Adicionar passo: " + gap.getAttribute("aria-label"));
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
            create("h3", "", metadata.title), create("p", "", metadata.description));
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
          const up = button("↑ Mover antes", () => move(-1), "button button-ghost");
          const down = button("↓ Mover depois", () => move(1), "button button-ghost");
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
            if (event.target !== card || !event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            move(event.key === "ArrowUp" ? -1 : 1);
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
      if (focusId) {
        const target = all("[data-draft-id]", list).find((item) => item.dataset.draftId === focusId);
        if (target) target.focus();
      }
    }

    function openEditor(key, trigger) {
      key = String(key);
      const current = byIndex.get(key);
      const dialog = $("case-editor");
      if (!current || !dialog || !$("case-editor-steps")) return;
      editorCase = key;
      editorTrigger = trigger;
      editorDrag = null;
      const steps = originalEditorSteps(key);
      const drafts = savedDrafts.get(key) || [];
      editorSequence = [];
      for (let gap = 0; gap <= steps.length; gap += 1) {
        drafts.filter((item) => item.gap === gap).forEach((item) => editorSequence.push(draftFields(item)));
        if (gap < steps.length) editorSequence.push({ type: "recorded", stepIndex: gap, step: steps[gap] });
      }
      text($("case-editor-title"), "Editor visual · " + current.id + " · " + (current.title || "Caso de teste"));
      text($("case-editor-rerun"), reportConnection ? "Reexecutar este teste" : "Como reexecutar");
      editorMessage("Rascunho visual · não executado · não altera este resultado.");
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

    function syncResultHeader(view) {
      const destination = view === "results" ? $("result-report-actions") : reportToolbarHome;
      if (reportToolbar && destination && reportToolbar.parentElement !== destination) destination.appendChild(reportToolbar);
    }

    function inlineSequence(model) {
      const steps = model.originalItems.slice(1);
      const drafts = savedDrafts.get(model.key) || [];
      const sequence = [];
      for (let gap = 0; gap <= steps.length; gap += 1) {
        drafts.filter((item) => item.gap === gap).forEach((item) => sequence.push(draftFields(item)));
        if (gap < steps.length) sequence.push({ type: "recorded", item: steps[gap] });
      }
      return sequence;
    }

    function inlineMessage(model, value) {
      text(model.inlineFeedback, value);
      model.inlineFeedback.hidden = !value;
    }

    function rebuildInlineSteps(model, requestedKey) {
      // The canonical timeline stays untouched. Drafts are separate panels and
      // navigation items, never observations, assertions, or executed actions.
      const selected = model.items[model.selected];
      const selectedKey = requestedKey || selected && selected.key || "original:0";
      model.list.replaceChildren();
      model.draftPanels.replaceChildren();
      const sequence = inlineSequence(model);
      const draftCount = sequence.filter(isDraft).length;
      const create = (tag, className, label) => {
        const element = document.createElement(tag);
        element.className = className;
        if (label != null) element.textContent = label;
        return element;
      };
      const commit = (nextSequence, focusKey, message) => {
        saveDraftSequence(model.key, nextSequence);
        rebuildInlineSteps(model, focusKey);
        inlineMessage(model, message + ($("download-report")
          ? " Baixe o relatório para conservar o rascunho."
          : " As alterações ficam somente nesta aba; o arquivo original não será alterado."));
        const current = model.items.find((item) => item.key === focusKey);
        if (current) current.button.focus({ preventScroll: true });
      };
      const insert = (position, type) => {
        if (sequence.filter(isDraft).length >= draftLimit) { inlineMessage(model, "Limite de 100 passos extras por CT."); return; }
        if (!["sws", "sql", "wait_print"].includes(type)) return;
        let id;
        do {
          draftSerial = draftSerial >= 999999999 ? 1 : draftSerial + 1;
          id = "db-" + draftSerial;
        } while (sequence.some((item) => isDraft(item) && item.id === id));
        sequence.splice(position, 0, draftFields({ type, id }));
        commit(sequence, "draft:" + id, draftKinds[type].title + " adicionado ao rascunho desta aba. Nada foi executado; os registros originais foram preservados.");
      };
      const dropAt = (position, event) => {
        if (!model.inlineDrag) return;
        event.preventDefault();
        const dragged = model.inlineDrag;
        model.inlineDrag = null;
        const previous = sequence.findIndex((item) => isDraft(item) && item.id === dragged);
        if (previous < 0) return;
        const [item] = sequence.splice(previous, 1);
        sequence.splice(position > previous ? position - 1 : position, 0, item);
        commit(sequence, "draft:" + item.id, "Passo extra reposicionado no rascunho visual. Nada foi executado.");
      };
      const addGap = (position) => {
        const gap = create("div", "case-inline-gap");
        gap.dataset.inlineGap = String(position);
        const label = position === 0 ? "Antes do primeiro cartão" : position === sequence.length
          ? "Depois do último cartão" : "Entre os cartões " + position + " e " + (position + 1);
        gap.setAttribute("aria-label", label);
        const control = button("+ Adicionar aqui", () => openStepPicker(control, label, (type) => insert(position, type), model.key), "button button-ghost case-inline-insert");
        control.disabled = draftCount >= draftLimit;
        control.setAttribute("aria-label", "Adicionar passo: " + label);
        control.setAttribute("title", "Planejar um passo extra · não altera este resultado");
        control.setAttribute("aria-haspopup", "dialog");
        control.setAttribute("aria-controls", "step-picker");
        gap.appendChild(control);
        gap.addEventListener("dragover", (event) => {
          if (model.inlineDrag) { event.preventDefault(); gap.classList.add("drag-over"); }
        });
        gap.addEventListener("dragleave", () => gap.classList.remove("drag-over"));
        gap.addEventListener("drop", (event) => dropAt(position, event));
        model.list.appendChild(gap);
      };
      model.items = [model.originalItems[0]];
      model.list.appendChild(model.originalItems[0].button);
      sequence.forEach((entry, sequenceIndex) => {
        addGap(sequenceIndex);
        if (entry.type === "recorded") {
          model.items.push(entry.item);
          model.list.appendChild(entry.item.button);
          return;
        }
        const metadata = draftKinds[entry.type];
        const card = create("div", "case-inline-draft");
        card.dataset.draftId = entry.id;
        card.dataset.draftType = entry.type;
        card.setAttribute("draggable", "true");
        card.tabIndex = 0;
        card.setAttribute("aria-label", metadata.title + ". Rascunho visual não executado. Use Alt mais seta para cima ou para baixo para mover.");
        const panel = create("section", "case-draft-panel");
        panel.id = "case-draft-" + model.key + "-" + entry.id;
        panel.dataset.draftId = entry.id;
        panel.dataset.draftType = entry.type;
        const heading = create("div", "case-draft-heading");
        const icon = create("span", "case-draft-icon", metadata.symbol);
        icon.setAttribute("aria-hidden", "true");
        heading.append(icon, create("h3", "", metadata.title));
        panel.append(create("span", "case-draft-status", "Rascunho visual · Não executado"), heading,
          create("p", "case-draft-note", entry.type === "database"
            ? "Este é um passo extra de exemplo. Ele ainda não se conecta a um banco, não executa consultas e não utiliza credenciais." : metadata.description),
          create("p", "case-draft-position", "Você pode reposicionar ou remover este card sem alterar as etapas, as evidências ou o resultado do teste original."));
        appendDraftOptions(panel, entry, () => {
          saveDraftSequence(model.key, sequence);
          inlineMessage(model, "Preferência de captura guardada no rascunho. Nenhum print ou integração foi executado." + ($("download-report")
            ? " Baixe o relatório para conservar a configuração." : " As alterações ficam somente nesta aba."));
        });
        model.draftPanels.appendChild(panel);
        const item = { key: "draft:" + entry.id, node: panel, kind: entry.type, kindLabel: "Rascunho visual", title: metadata.title, draftId: entry.id };
        const control = button("", () => selectCaseStep(model, model.items.indexOf(item)), "case-step-button case-inline-draft-button");
        control.dataset.stepKind = entry.type;
        control.dataset.stepState = "draft";
        control.setAttribute("aria-controls", panel.id);
        control.setAttribute("aria-label", metadata.title + ". Rascunho visual. Não executado.");
        const copy = create("span", "step-copy");
        const status = create("span", "step-status");
        status.dataset.stepState = "draft";
        const statusIcon = create("span", "step-status-icon", "◇");
        statusIcon.setAttribute("aria-hidden", "true");
        status.append(statusIcon, create("span", "step-status-label", "Não executado"));
        copy.append(create("span", "step-kind", "Passo extra · rascunho"), create("span", "step-label", item.title),
          create("small", "step-description", entry.type === "wait_print" ? "Pausa para colar ou anexar sua imagem na próxima reexecução." : "Planejado. Não interfere no resultado original."), status);
        control.append(create("span", "step-number", "+"), copy);
        item.button = control;
        card.appendChild(control);
        const move = (direction) => {
          const current = sequence.findIndex((step) => isDraft(step) && step.id === entry.id);
          const next = current + direction;
          if (current < 0 || next < 0 || next >= sequence.length) return;
          const [step] = sequence.splice(current, 1);
          sequence.splice(next, 0, step);
          commit(sequence, item.key, "Passo extra reposicionado no rascunho visual. Nada foi executado.");
        };
        const actions = create("div", "case-inline-draft-actions");
        const up = button("↑", () => move(-1), "button button-ghost case-inline-move-before");
        const down = button("↓", () => move(1), "button button-ghost case-inline-move-after");
        up.setAttribute("aria-label", "Mover passo extra para antes");
        down.setAttribute("aria-label", "Mover passo extra para depois");
        up.disabled = sequenceIndex === 0;
        down.disabled = sequenceIndex === sequence.length - 1;
        const remove = button("Remover", () => {
          const nextSequence = sequence.filter((step) => step !== entry);
          commit(nextSequence, "original:0", "Passo extra removido do rascunho. Nenhuma etapa original foi removida.");
        }, "button button-ghost case-inline-remove");
        remove.setAttribute("aria-label", "Remover passo extra");
        actions.append(up, down, remove);
        card.appendChild(actions);
        card.addEventListener("keydown", (event) => {
          if (!event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
          if (event.target !== card && event.target !== control) return;
          event.preventDefault();
          move(event.key === "ArrowUp" ? -1 : 1);
        });
        card.addEventListener("dragstart", (event) => {
          model.inlineDrag = entry.id;
          if (event.dataTransfer) { event.dataTransfer.effectAllowed = "move"; event.dataTransfer.setData("text/plain", "Passo extra não executado"); }
        });
        card.addEventListener("dragend", () => {
          model.inlineDrag = null;
          all(".drag-over", model.list).forEach((gap) => gap.classList.remove("drag-over"));
        });
        model.items.push(item);
        model.list.appendChild(card);
      });
      addGap(sequence.length);
      model.items.forEach((item, index) => { item.button.dataset.stepIndex = String(index); });
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
      text(model.title, selected.title);
      text(model.position, selected.kind === "summary" ? "Visão geral do teste" : selected.draftId
        ? "Passo extra · Rascunho visual · Não executado"
        : "Etapa " + selected.originalIndex + " de " + (model.originalItems.length - 1) + " · " + selected.kindLabel);
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
      if (image.naturalWidth !== value.image.width || image.naturalHeight !== value.image.height) return;
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

    function ensureCaseExplorer(detail) {
      const key = detail.dataset.caseIndex;
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
      const sidebarHeader = make("div", "case-step-sidebar-header");
      const count = make("small", "case-step-count");
      sidebarHeader.append(make("h3", "", "Passos e evidências"), count);
      if (timeline.dataset.stepOrder === "planned") {
        sidebarHeader.append(make("small", "case-step-order-note", "Ordem dos passos planejada · horários reais preservados"));
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
      caption.append(position, title);
      const content = make("div", "case-step-content");
      content.tabIndex = 0;
      content.setAttribute("aria-label", "Detalhes e evidências do passo selecionado");
      const draftPanels = make("div", "case-draft-panels");
      content.append(summary, timeline, draftPanels);
      const model = {
        key, detail, workspace, list, content, count, position, title, items: [], originalItems: [],
        selected: entries.length ? 1 : 0, inlineDrag: null, inlineFeedback, draftPanels,
      };
      const move = (direction) => {
        const available = model.items.map((item, index) => ({ item, index })).filter(({ item }) => !item.button.hidden);
        const cursor = available.findIndex((item) => item.index === model.selected);
        const next = available[cursor + direction];
        if (next) selectCaseStep(model, next.index);
      };
      model.previous = button("← Anterior", () => move(-1), "button button-ghost case-step-prev");
      model.previous.setAttribute("aria-label", "Passo anterior");
      model.next = button("Próximo →", () => move(1), "button button-ghost case-step-next");
      model.next.setAttribute("aria-label", "Próximo passo");
      const controls = make("div", "case-step-controls");
      controls.append(model.previous, model.next);
      toolbar.append(caption, controls);
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
      [{ node: summary, kind: "summary", title: "Resultado do caso" }, ...entries.map((node) => ({
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
        const originalItem = { ...item, key: "original:" + index, originalIndex: index, kindLabel };
        const control = button("", () => selectCaseStep(model, model.items.indexOf(originalItem)), "case-step-button");
        control.dataset.stepIndex = String(index);
        control.dataset.recordedIndex = String(index);
        control.dataset.stepKind = item.kind;
        control.dataset.stepState = result.state;
        control.setAttribute("aria-controls", item.node.id);
        control.setAttribute("aria-label", (index ? "Etapa " + index + ": " : "") + sidebarTitle + ". " + description + " Resultado: " + outcome + ".");
        const copy = make("span", "step-copy");
        const status = make("span", "step-status");
        status.dataset.stepState = result.state;
        const icon = make("span", "step-status-icon", state.symbol);
        icon.setAttribute("aria-hidden", "true");
        status.append(icon, make("span", "step-status-label", outcome));
        copy.append(make("span", "step-kind", kindLabel), make("span", "step-label", sidebarTitle), make("small", "step-description", description), status);
        control.append(make("span", "step-number", index ? String(index).padStart(2, "0") : "i"), copy);
        list.appendChild(control);
        originalItem.button = control;
        model.items.push(originalItem);
        model.originalItems.push(originalItem);
      });
      list.addEventListener("keydown", (event) => {
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

    function updateCaseNavigation() {
      const current = byIndex.get(selectedCase);
      const position = cases.findIndex((item) => String(item.index) === selectedCase);
      text($("case-title"), current ? current.title || current.id || "Caso de teste" : "Nenhum caso disponível");
      if ($("case-rerun")) {
        $("case-rerun").disabled = !current;
        $("case-rerun").setAttribute("aria-label", current ? "Reexecutar teste " + current.id : "Reexecutar teste");
      }
      const feedback = $("case-rerun-feedback");
      if (feedback) { feedback.hidden = true; feedback.textContent = ""; }
      if ($("case-prev")) $("case-prev").disabled = position <= 0;
      if ($("case-next")) $("case-next").disabled = position < 0 || position >= cases.length - 1;
      all("#scenarios details.scenario[data-case-index]").forEach((detail) => {
        const selected = current && detail.dataset.caseIndex === selectedCase;
        detail.hidden = !selected;
        if (selected) { detail.open = true; ensureCaseExplorer(detail); }
      });
    }

    function showView(view, focus) {
      if (stepPickerRequest) closeStepPicker();
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
        ? "#view/results/" + encodeURIComponent(String(current.id))
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
      const parts = hash.split("/");
      if (parts[0] !== "view" || !Object.prototype.hasOwnProperty.call(views, parts[1])) {
        if (!hash) showView("overview", focus);
        return;
      }
      if (parts[1] === "results" && parts.length > 2) {
        const current = byId.get(parts.slice(2).join("/"));
        if (!current) return;
        selectedCase = String(current.index);
      }
      showView(parts[1], focus);
    }

    all("[data-report-nav]").forEach((nav) => nav.addEventListener("click", (event) => {
      event.preventDefault();
      navigate(nav.dataset.reportNav);
    }));
    document.addEventListener("click", (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const open = target.closest("[data-open-case]");
      if (open) { event.preventDefault(); openCase(open.dataset.openCase); return; }
      const edit = target.closest("[data-edit-case], [data-edit-current]");
      if (edit) {
        event.preventDefault();
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
    listen("case-editor-save", "click", () => {
      if (!editorCase) return;
      saveDraftSequence(editorCase, editorSequence);
      const explorer = caseExplorers.get(editorCase);
      if (explorer) rebuildInlineSteps(explorer);
      editorMessage($("download-report")
        ? "Rascunho guardado nesta aba. Baixe o relatório para conservar esta versão. Resultados e evidências originais não foram alterados."
        : "Rascunho guardado nesta aba. O arquivo original não foi alterado. Nada foi executado.");
    });
    listen("database-step-card", "dragstart", (event) => {
      if (!editorCase) return;
      editorDrag = "new";
      if (event.dataTransfer) { event.dataTransfer.effectAllowed = "copy"; event.dataTransfer.setData("text/plain", "Consulta de exemplo não executada"); }
    });
    listen("database-step-card", "dragend", () => {
      editorDrag = null;
      all(".drag-over", $("case-editor-steps") || document).forEach((gap) => gap.classList.remove("drag-over"));
    });
    listen("database-step-card", "click", () => {
      const gaps = all(".case-editor-insert", $("case-editor-steps") || document);
      if (gaps.length) { gaps[gaps.length - 1].focus(); editorMessage("Escolha «Adicionar aqui» entre as etapas e selecione o tipo de passo."); }
    });
    listen("case-editor-rerun", "click", () => {
      requestCaseRerun(editorCase, editorMessage);
    });
    listen("case-rerun", "click", () => requestCaseRerun(selectedCase, (value) => {
      const feedback = $("case-rerun-feedback");
      text(feedback, value);
      if (feedback) feedback.hidden = false;
    }));
    document.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.key !== "Escape") return;
      if ($("step-picker") && $("step-picker").hasAttribute("open")) { event.preventDefault(); closeStepPicker(); }
      else if ($("case-editor") && $("case-editor").hasAttribute("open")) { event.preventDefault(); closeEditor(); }
      else closeJourneyPickers(true);
    });
    window.addEventListener("resize", scheduleJourneyLayout);
    window.addEventListener("afterprint", scheduleJourneyLayout);
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
      const index = cases.findIndex((item) => String(item.index) === selectedCase);
      if (index > 0) openCase(cases[index - 1].index);
    });
    listen("case-next", "click", () => {
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

    function filterCases(resetPage = true) {
      if (resetPage) pageNumber = 1;
      const term = normalize($("search") ? $("search").value : "");
      const category = $("category-filter") ? $("category-filter").value : "";
      const page = $("page-filter") ? $("page-filter").value : "";
      filteredCases = cases.filter((item) => (
        (selectedStatus === "ALL" || item.status === selectedStatus)
        && (!category || String(item.category) === category)
        && (!page || String(item.page) === page)
        && (!term || normalize([item.id, item.title, categoryLabels[item.category] || item.category, item.page, item.url, item.search].join(" ")).includes(term))
      ));
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
    ["category-filter", "page-filter", "page-size"].forEach((id) => listen(id, "change", () => filterCases()));
    listen("clear-filters", "click", () => {
      ["search", "category-filter", "page-filter"].forEach((id) => { if ($(id)) $(id).value = ""; });
      selectedStatus = "ALL";
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
      const rows = [["CT", "Título", "Status", "Categoria", "Página", "URL", "Duração (segundos)", "Sinais técnicos"]];
      filteredCases.forEach((item) => rows.push([
        item.id, item.title, verdictLabels[item.status] || item.status, categoryLabels[item.category] || item.category,
        item.page, item.url, item.duration_seconds == null ? "" : item.duration_seconds,
        Array.isArray(item.signals) ? item.signals.length : item.signals,
      ]));
      saveBlob(new Blob(["\ufeff" + rows.map((row) => row.map(csvCell).join(";")).join("\r\n")], { type: "text/csv;charset=utf-8" }), "autojev-casos.csv");
    });
    listen("download-report", "click", () => saveBlob(new Blob([exportDocument()], { type: "text/html;charset=utf-8" }), "autojev-relatorio.html"));
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
    let previewTrigger = null;
    function closeEvidence() {
      const dialog = $("evidence-dialog");
      if (!dialog) return;
      if (typeof dialog.close === "function") dialog.close(); else dialog.removeAttribute("open");
      if (previewTrigger) previewTrigger.focus();
    }
    function openEvidencePreview(original, control, caption, filename) {
      const dialog = $("evidence-dialog");
      if (!original || !dialog || !$("evidence-preview")) return;
      hydrateImage(original);
      const src = original.getAttribute("src") || "";
      // Reports embed their captures. Never navigate to a script or remote URL.
      if (!/^(data:image\/(?:png|jpe?g|webp|gif);base64,|blob:)/i.test(src)) return;
      previewTrigger = control;
      const previewImage = $("evidence-preview");
      let previewCanvas = previewImage.closest(".action-modal-canvas");
      if (!previewCanvas) {
        previewCanvas = document.createElement("div");
        previewCanvas.className = "action-modal-canvas";
        previewImage.replaceWith(previewCanvas);
        previewCanvas.appendChild(previewImage);
      }
      previewImage.src = src;
      previewImage.alt = original.alt || "Captura de evidência";
      setActionMarker(previewCanvas, previewImage, original.closest("figure.action-preview"));
      text($("evidence-caption"), caption || original.alt || "Captura de evidência");
      if ($("download-evidence")) {
        $("download-evidence").href = src;
        $("download-evidence").download = filename || original.dataset.sourceShot || "autojev-evidencia.png";
      }
      if (typeof dialog.showModal === "function") dialog.showModal(); else dialog.setAttribute("open", "");
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
    listen("evidence-dialog", "close", () => { if (previewTrigger) previewTrigger.focus(); });
    document.addEventListener("keydown", (event) => {
      if (!event.defaultPrevented && event.key === "Escape" && $("evidence-dialog") && $("evidence-dialog").hasAttribute("open")) closeEvidence();
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

    filterCases();
    filterEvidence();
    filterTimeline();
    showView("overview", false);
    revealHash(false);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initializeReport, { once: true });
  else initializeReport();
})();
