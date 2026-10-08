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
      return "<!DOCTYPE html>\n" + exportRoot.outerHTML;
    })();
    const cases = Array.isArray(data.cases) ? data.cases : [];
    const byIndex = new Map(cases.map((item) => [String(item.index), item]));
    const byId = new Map(cases.map((item) => [String(item.id), item]));
    const $ = (id) => document.getElementById(id);
    const all = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));
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
    // Reuse the export controls in the full-canvas result header, keeping a
    // single instance and its listeners when navigating to another report view.
    const reportToolbar = document.querySelector(".report-topbar .toolbar");
    const reportToolbarHome = reportToolbar && reportToolbar.parentElement;

    function syncResultHeader(view) {
      const destination = view === "results" ? $("result-report-actions") : reportToolbarHome;
      if (reportToolbar && destination && reportToolbar.parentElement !== destination) destination.appendChild(reportToolbar);
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
      text(model.position, index === 0 ? "Resumo do CT" : "Registro " + index + " de " + (model.items.length - 1) + " · " + selected.kindLabel);
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
      const visible = model.items.slice(1).filter((item) => !item.button.hidden).length;
      const total = model.items.length - 1;
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
      return { image, box, action: value.action };
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
      tag.textContent = Object.prototype.hasOwnProperty.call(labels, value.action) ? labels[value.action] : "Alvo da ação";
      const right = value.box.x + value.box.width / 2 > value.image.width / 2;
      const above = value.box.y + value.box.height + 42 > value.image.height;
      tag.classList.toggle("align-right", right);
      tag.classList.toggle("above", above);
      tag.style.setProperty(right ? "right" : "left", percent(right
        ? Math.max(8, value.image.width - value.box.x - value.box.width) : Math.max(8, value.box.x), value.image.width));
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
      const list = make("div", "case-step-list");
      list.setAttribute("role", "group");
      list.setAttribute("aria-label", "Selecionar passo do teste");
      sidebar.append(sidebarHeader, list);
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
      content.append(summary, timeline);
      const model = { detail, workspace, list, content, count, position, title, items: [], selected: entries.length ? 1 : 0 };
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
      const labels = { action: "Ação", assertion: "Verificação", signal: "Sinal técnico", observation: "Observação", summary: "Visão do caso" };
      const symbols = { action: "→", assertion: "◇", signal: "!", observation: "◉", summary: "i" };
      [{ node: summary, kind: "summary", title: "Resumo do CT" }, ...entries.map((node) => ({
        node, kind: Object.keys(labels).find((kind) => node.classList.contains(kind)) || "observation",
        title: (node.querySelector("h3") || {}).textContent || "Registro da execução",
      }))].forEach((item, index) => {
        const control = button("", () => selectCaseStep(model, index), "case-step-button");
        control.dataset.stepIndex = String(index);
        control.dataset.stepKind = item.kind;
        control.setAttribute("aria-controls", item.node.id);
        const copy = make("span", "step-copy");
        copy.append(make("span", "step-kind", labels[item.kind]), make("span", "step-label", item.title));
        control.append(make("span", "step-number", index ? String(index).padStart(2, "0") : "i"), copy, make("span", "step-indicator", symbols[item.kind]));
        list.appendChild(control);
        model.items.push({ ...item, button: control, kindLabel: labels[item.kind] });
      });
      list.addEventListener("keydown", (event) => {
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
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
      syncCaseStepAvailability(model);
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
      if ($("case-prev")) $("case-prev").disabled = position <= 0;
      if ($("case-next")) $("case-next").disabled = position < 0 || position >= cases.length - 1;
      all("#scenarios details.scenario[data-case-index]").forEach((detail) => {
        const selected = current && detail.dataset.caseIndex === selectedCase;
        detail.hidden = !selected;
        if (selected) { detail.open = true; ensureCaseExplorer(detail); }
      });
    }

    function showView(view, focus) {
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
      text($("view-subtitle"), views[view][1]);
      if (view === "results") updateCaseNavigation();
      if (view === "evidence" || view === "journey") {
        const section = all(".report-view[data-view]").find((item) => item.dataset.view === view);
        if (section) all("img[data-source-shot]", section).forEach(hydrateImage);
      }
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
    all("[data-open-case]").forEach((control) => control.addEventListener("click", (event) => {
      event.preventDefault();
      openCase(control.dataset.openCase);
    }));
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
    listen("download-report", "click", () => saveBlob(new Blob([originalDocument], { type: "text/html;charset=utf-8" }), "autojev-relatorio.html"));
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
      if (event.key === "Escape" && $("evidence-dialog") && $("evidence-dialog").hasAttribute("open")) closeEvidence();
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
