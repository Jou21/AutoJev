import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

// A deliberately small DOM fixture keeps these regressions independent of a
// browser installation. The production report script, not a reimplementation
// of its step-selection logic, runs below.
const script = await readFile(new URL("../backend/autojev/adapters/report_assets/premium.js", import.meta.url), "utf8");

const dataKey = (attribute) => attribute.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
const attributeKey = (property) => "data-" + property.replace(/[A-Z]/g, (letter) => "-" + letter.toLowerCase());
const escapeHtml = (value) => String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escapeAttribute = (value) => escapeHtml(value).replace(/"/g, "&quot;");
const voidTags = new Set(["AREA", "BASE", "BR", "COL", "EMBED", "HR", "IMG", "INPUT", "LINK", "META", "PARAM", "SOURCE", "TRACK", "WBR"]);
const actionMetadata = {
  version: "action-target-v1", coordinate_space: "screenshot-css-pixels",
  action: "select", image: { width: 1440, height: 900 },
  box: { x: 1080, y: 90, width: 216, height: 45 },
};
const dropdownSelection = {
  version: "select-labels-v1", requested_label: "Name (Z to A)",
  before_label: "Name (A to Z)", after_label: "Name (Z to A)", status: "confirmed",
};
const reportCss = await readFile(new URL("../backend/autojev/adapters/report_assets/premium.css", import.meta.url), "utf8");

class FixtureElement {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.nodeType = 1;
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.parentElement = null;
    this.attributes = new Map();
    this.dataset = {};
    this.events = new Map();
    this.hidden = false;
    this.open = false;
    this.disabled = false;
    this.value = "";
    this._text = "";
    const styleValues = new Map();
    this.style = {
      setProperty(name, value) { styleValues.set(name, String(value)); },
      getPropertyValue(name) { return styleValues.get(name) || ""; },
      removeProperty(name) { const previous = styleValues.get(name) || ""; styleValues.delete(name); return previous; },
    };
    this.classList = {
      contains: (value) => this.className.split(/\s+/).includes(value),
      add: (...values) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...values])].join(" "); },
      remove: (...values) => { this.className = this.className.split(/\s+/).filter((value) => !values.includes(value)).join(" "); },
      toggle: (value, force) => {
        const add = force ?? !this.classList.contains(value);
        if (add) this.classList.add(value); else this.classList.remove(value);
        return add;
      },
    };
  }
  get className() { return this.attributes.get("class") || ""; }
  set className(value) { this.attributes.set("class", String(value)); }
  get id() { return this.attributes.get("id") || ""; }
  set id(value) { this.attributes.set("id", String(value)); }
  get src() { return this.getAttribute("src") || ""; }
  set src(value) { this.setAttribute("src", value); }
  get href() { return this.getAttribute("href") || ""; }
  set href(value) { this.setAttribute("href", value); }
  get childNodes() { return this.children; }
  get parentNode() { return this.parentElement; }
  get firstChild() { return this.children[0] || null; }
  get firstElementChild() { return this.firstChild; }
  get lastElementChild() { return this.children.at(-1) || null; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) {
    this.children.forEach((child) => { child.parentElement = null; });
    this.children = [];
    this._text = String(value);
  }
  get outerHTML() {
    const attributes = new Map(this.attributes);
    Object.entries(this.dataset).forEach(([name, value]) => attributes.set(attributeKey(name), String(value)));
    ["hidden", "open", "disabled"].forEach((name) => {
      if (this[name]) attributes.set(name, ""); else attributes.delete(name);
    });
    const serialized = [...attributes].map(([name, value]) => ` ${name}="${escapeAttribute(value)}"`).join("");
    const tag = this.tagName.toLowerCase();
    if (voidTags.has(this.tagName)) return `<${tag}${serialized}>`;
    const text = ["SCRIPT", "STYLE"].includes(this.tagName) ? this._text : escapeHtml(this._text);
    return `<${tag}${serialized}>${text}${this.children.map((child) => child.outerHTML).join("")}</${tag}>`;
  }
  cloneNode(deep = false) {
    const copy = new FixtureElement(this.tagName, this.ownerDocument);
    copy.nodeType = this.nodeType;
    copy.attributes = new Map(this.attributes);
    copy.dataset = { ...this.dataset };
    copy._text = this._text;
    ["hidden", "open", "disabled", "value", "checked"].forEach((name) => { copy[name] = this[name]; });
    if (deep) this.children.forEach((child) => copy.appendChild(child.cloneNode(true)));
    return copy;
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name.startsWith("data-")) this.dataset[dataKey(name)] = String(value);
  }
  getAttribute(name) {
    if (name.startsWith("data-") && dataKey(name) in this.dataset) return this.dataset[dataKey(name)];
    return this.attributes.get(name) ?? null;
  }
  hasAttribute(name) { return this.getAttribute(name) != null; }
  removeAttribute(name) {
    this.attributes.delete(name);
    if (name.startsWith("data-")) delete this.dataset[dataKey(name)];
  }
  appendChild(child) {
    if (child.nodeType === 11) {
      [...child.children].forEach((nested) => this.appendChild(nested));
      return child;
    }
    child.remove();
    this.children.push(child);
    child.parentElement = this;
    return child;
  }
  append(...children) { children.forEach((child) => this.appendChild(child)); }
  prepend(...children) {
    children.reverse().forEach((child) => {
      child.remove();
      this.children.unshift(child);
      child.parentElement = this;
    });
  }
  insertBefore(child, before) {
    if (!before) return this.appendChild(child);
    child.remove();
    const index = this.children.indexOf(before);
    assert.notEqual(index, -1, "insertBefore target must belong to its parent");
    this.children.splice(index, 0, child);
    child.parentElement = this;
    return child;
  }
  replaceChildren(...children) {
    this.children.forEach((child) => { child.parentElement = null; });
    this.children = [];
    this._text = "";
    this.append(...children);
  }
  remove() {
    if (!this.parentElement) return;
    const siblings = this.parentElement.children;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentElement = null;
  }
  replaceWith(replacement) {
    const parent = this.parentElement;
    if (!parent) return;
    parent.insertBefore(replacement, this);
    this.remove();
  }
  contains(child) { return child === this || this.children.some((item) => item.contains(child)); }
  matches(selector) {
    const attributes = [...selector.matchAll(/\[([^\]=~^$*\s]+)(?:([~^$*]?=)["']?([^\]"']*)["']?)?\]/g)];
    const simple = selector.replace(/\[[^\]]+\]/g, "");
    if (simple.includes(":scope")) return false;
    const tag = simple.match(/^[a-z][\w-]*/i)?.[0];
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    const id = simple.match(/#([\w-]+)/)?.[1];
    if (id && this.id !== id) return false;
    for (const [, name] of simple.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(name)) return false;
    for (const [, name, operator, expected] of attributes) {
      const actual = this.getAttribute(name);
      if (actual == null) return false;
      if (operator === "=" && actual !== expected) return false;
      if (operator === "^=" && !actual.startsWith(expected)) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const descendants = [];
    const visit = (element) => element.children.forEach((child) => { descendants.push(child); visit(child); });
    visit(this);
    const results = new Set();
    selector.split(/,\s*/).forEach((branch) => {
      if (branch.startsWith(":scope > ")) {
        this.children.filter((child) => child.matches(branch.slice(9))).forEach((child) => results.add(child));
        return;
      }
      const segments = branch.trim().split(/\s+/);
      descendants.forEach((element) => {
        if (!element.matches(segments.at(-1))) return;
        let ancestor = element.parentElement;
        for (let index = segments.length - 2; index >= 0; index -= 1) {
          if (segments[index] === ">") {
            index -= 1;
            if (!ancestor || !ancestor.matches(segments[index])) return;
            ancestor = ancestor.parentElement;
            continue;
          }
          while (ancestor && !ancestor.matches(segments[index])) ancestor = ancestor.parentElement;
          if (!ancestor) return;
          ancestor = ancestor.parentElement;
        }
        results.add(element);
      });
    });
    return [...results];
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) {
    for (let current = this; current; current = current.parentElement) {
      if (selector.split(/,\s*/).some((branch) => current.matches(branch))) return current;
    }
    return null;
  }
  addEventListener(type, callback) {
    if (!this.events.has(type)) this.events.set(type, []);
    this.events.get(type).push(callback);
  }
  dispatch(type, properties = {}) {
    const event = { target: this, currentTarget: this, preventDefault() { this.defaultPrevented = true; }, ...properties };
    for (let current = this; current; current = current.parentElement) {
      event.currentTarget = current;
      for (const callback of current.events.get(type) || []) callback(event);
    }
    return event;
  }
  click() {
    if (this.disabled) return;
    if (this.tagName === "A" && this.download) this.ownerDocument.downloads?.push({ filename: this.download, href: this.href });
    this.dispatch("click");
  }
  focus() { this.ownerDocument.activeElement = this; }
  scrollIntoView() {}
}

function fixture(options = {}) {
  const {
    emptyTimeline = false, hash = "#view/results/CT001", withSignal = false,
    exported = false, actionPreview = false,
    naturalImageSize = { width: 1440, height: 900 },
    stepMetadata = {}, caseStatuses = ["PASS", "FAIL"],
    journey = false, journeyCount = 9, visualEditor = false,
  } = options;
  const storedActionMetadata = Object.hasOwn(options, "actionPreviewMetadata")
    ? options.actionPreviewMetadata : actionMetadata;
  const document = new FixtureElement("document");
  document.nodeType = 9;
  document.ownerDocument = document;
  document.createElement = (tagName) => new FixtureElement(tagName, document);
  if (journey) document.createElementNS = (_namespace, tagName) => document.createElement(tagName);
  document.createDocumentFragment = () => {
    const fragment = document.createElement("fragment");
    fragment.nodeType = 11;
    return fragment;
  };
  document.documentElement = document.createElement("html");
  document.appendChild(document.documentElement);
  document.body = document.createElement("body");
  document.body.className = "report-premium";
  document.documentElement.appendChild(document.body);
  document.readyState = "complete";
  document.getElementById = (id) => document.querySelector(`#${id}`);
  document.downloads = [];
  const make = (tagName, className, parent, text = "") => {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = text;
    if (parent) parent.appendChild(element);
    return element;
  };
  const source = make("script", "", document.body);
  source.id = "report-data";
  source.textContent = JSON.stringify({ run_id: options.runId, cases: [
    { index: 0, id: "CT001", title: "Ordenação de produtos", status: caseStatuses[0], category: "business", page: "/inventory.html" },
    { index: 1, id: "CT002", title: "Verificar carrinho", status: caseStatuses[1], category: "business", page: "/cart.html" },
  ] });
  if (options.appRuntime !== undefined) {
    const connection = make("script", "", document.body);
    connection.id = "report-app-runtime";
    connection.setAttribute("type", options.runtimeType || "application/json");
    connection.textContent = typeof options.appRuntime === "string" ? options.appRuntime : JSON.stringify(options.appRuntime);
  }
  const drafts = make("script", "", document.body);
  drafts.id = "report-drafts";
  drafts.textContent = typeof options.drafts === "string" ? options.drafts : JSON.stringify(options.drafts || { version: 1, cases: {} });
  const navigation = make("nav", "", document.body);
  for (const view of ["overview", "tests", "results", ...(journey ? ["journey"] : [])]) {
    make("button", "", navigation, view).dataset.reportNav = view;
  }
  const main = make("main", "report-main", document.body);
  const topbar = make("header", "report-topbar", main);
  make("h1", "", topbar, "Visão geral").id = "view-title";
  make("p", "", topbar).id = "view-subtitle";
  const toolbar = make("div", "toolbar", topbar);
  make("button", "", toolbar, "Imprimir").id = "print-report";
  if (!exported) make("button", "", toolbar, "Baixar relatório").id = "download-report";
  for (const view of ["overview", "tests"]) {
    make("section", "report-view", main).dataset.view = view;
  }
  let journeyList = null;
  let journeyRoute = null;
  let journeyCanvas = null;
  const canvasCalls = [];
  if (journey) {
    const journeyView = make("section", "report-view", main);
    journeyView.dataset.view = "journey";
    journeyRoute = make("div", "journey-route", journeyView);
    journeyCanvas = make("canvas", "journey-route-canvas", journeyRoute);
    journeyCanvas.setAttribute("aria-hidden", "true");
    const context = {};
    for (const method of ["setTransform", "clearRect", "beginPath", "moveTo", "lineTo", "bezierCurveTo", "stroke", "arc", "fill", "closePath", "save", "restore"]) {
      context[method] = (...args) => {
        assert.ok(args.every((value) => typeof value === "number" && Number.isFinite(value)), `${method} receives finite numeric canvas coordinates`);
        canvasCalls.push({ method, args });
      };
    }
    journeyCanvas.getContext = (kind) => kind === "2d" ? context : null;
    const start = make("span", "journey-terminal journey-terminal-start", journeyRoute, "INÍCIO");
    const end = make("span", "journey-terminal journey-terminal-end", journeyRoute, "FIM");
    journeyList = make("ol", "journey-track", journeyRoute);
    journeyList.setAttribute("role", "list");
    const number = (element, property, fallback) => {
      const camel = property.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
      return Number.parseFloat(element.style.getPropertyValue(property) || element.style[camel]) || fallback;
    };
    const rectangle = (left, top, width, height) => ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top });
    const geometry = () => {
      const width = window.innerWidth;
      const columns = number(journeyRoute, "--journey-columns", 4);
      const gutter = number(journeyRoute, "--journey-gutter", 88);
      const top = number(journeyRoute, "--journey-top", 36);
      const bottom = number(journeyRoute, "--journey-bottom", 42);
      const rowGap = number(journeyRoute, "--journey-row-gap", 90);
      const rows = Math.ceil(journeyCount / columns);
      const trackHeight = rows * 100 + Math.max(0, rows - 1) * rowGap;
      return { width, columns, gutter, top, rowGap, trackHeight, height: top + trackHeight + bottom };
    };
    journeyRoute.getBoundingClientRect = () => {
      const current = geometry();
      return rectangle(50, 120, current.width, current.height);
    };
    journeyList.getBoundingClientRect = () => {
      const current = geometry();
      return rectangle(50 + current.gutter, 120 + current.top, current.width - current.gutter * 2, current.trackHeight);
    };
    journeyCanvas.getBoundingClientRect = () => journeyRoute.getBoundingClientRect();
    for (const terminal of [start, end]) {
      terminal.getBoundingClientRect = () => rectangle(50 + number(terminal, "left", 0), 120 + number(terminal, "top", 0), 66, 32);
      terminal.offsetWidth = 66;
      terminal.offsetHeight = 32;
    }
    for (let index = 0; index < journeyCount; index += 1) {
      const node = make("li", "journey-node", journeyList);
      node.dataset.journeyPosition = String(index);
      node.getBoundingClientRect = () => {
        const current = geometry();
        const column = number(node, "grid-column", index % current.columns + 1) - 1;
        const row = number(node, "grid-row", Math.floor(index / current.columns) + 1) - 1;
        const width = (current.width - 2 * current.gutter - (current.columns - 1) * 32) / current.columns;
        return rectangle(50 + current.gutter + column * (width + 32), 120 + current.top + row * (100 + current.rowGap), width, 100);
      };
      const control = make("button", "", node, "Página " + (index + 1));
      control.dataset.journeyPage = String(index);
      control.setAttribute("aria-controls", "journey-cases-" + index);
      control.setAttribute("aria-expanded", "false");
      const picker = make("section", "journey-case-picker", journeyView);
      picker.id = "journey-cases-" + index;
      picker.hidden = true;
      make("button", "", picker, "Fechar lista").dataset.journeyClose = "true";
      make("input", "", picker).dataset.journeySearch = "true";
      make("span", "", picker).dataset.journeyCount = "true";
      const noCases = make("p", "", picker, "Nenhum CT corresponde à busca.");
      noCases.dataset.journeyEmpty = "true";
      noCases.hidden = true;
      for (const caseIndex of index === 0 ? [0, 1] : [index % 2]) {
        const row = make("article", "journey-case-row", picker);
        make("h3", "", row, caseIndex ? "CT002 · Verificar carrinho" : "CT001 · Ordenação de produtos");
        make("button", "", row, "Abrir CT").dataset.openCase = String(caseIndex);
        make("button", "", row, "Editar CT").dataset.editCase = String(caseIndex);
      }
    }
  }
  const testsView = document.querySelector('.report-view[data-view="tests"]');
  make("input", "", testsView).id = "search";
  make("div", "", testsView).id = "filters";
  make("button", "", testsView, "Baixar CSV").id = "download-csv";
  const rows = make("tbody", "", testsView);
  rows.id = "case-rows";
  for (let index = 0; index < 2; index += 1) make("tr", "", rows, `CT00${index + 1}`).dataset.caseIndex = String(index);
  const resultView = make("section", "report-view", main);
  resultView.dataset.view = "results";
  const resultNavigation = make("div", "result-navigation", resultView);
  const resultLocation = make("div", "result-location", resultNavigation);
  make("button", "", resultLocation, "Voltar para testes").id = "case-back";
  make("h1", "", resultLocation, "Resultado do teste").id = "result-view-title";
  const resultActions = make("div", "result-actions", resultNavigation);
  const caseNavigation = make("div", "result-case-navigation", resultActions);
  if (!exported || options.legacyRerun) {
    make("button", "button button-rerun", caseNavigation, "Reexecutar teste").id = "case-rerun";
    const feedback = make("p", "case-rerun-feedback", resultView);
    feedback.id = "case-rerun-feedback";
    feedback.hidden = true;
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
  }
  make("button", "", caseNavigation, "Teste anterior").id = "case-prev";
  make("button", "", caseNavigation, "Teste seguinte").id = "case-next";
  make("div", "", resultActions).id = "result-report-actions";
  if (visualEditor) make("button", "", resultActions, "Editar CT").dataset.editCurrent = "true";
  const scenarios = make("div", "", resultView);
  scenarios.id = "scenarios";
  const originals = [];
  const originalImages = [];
  const actionFigures = [];
  const actionImages = [];
  const cards = [];
  for (let caseIndex = 0; caseIndex < 2; caseIndex += 1) {
    const card = make("details", "scenario", scenarios);
    card.dataset.caseIndex = String(caseIndex);
    if (caseStatuses[caseIndex] !== undefined) card.dataset.status = caseStatuses[caseIndex];
    const summary = make("summary", "", card);
    make("h2", "", summary, caseIndex ? "Verificar carrinho" : "Ordenação de produtos");
    const body = make("div", "scenario-body", card);
    make("p", "", body, "Descrição preservada do caso.");
    make("div", "verdict-note", body, "Asserção preservada.");
    const description = make("details", "case-documentation", body);
    make("summary", "", description, "Descrição e passos do caso");
    make("p", "", description, "Passos documentados.");
    make("h3", "timeline-heading", body, "Linha do tempo · ações e evidências");
    if (!emptyTimeline || caseIndex) {
      const timeline = make("ol", "evidence-timeline", body);
      ["observation", "action", "assertion", ...(withSignal ? ["signal"] : [])].forEach((kind, index) => {
        const entry = make("li", `timeline-entry ${kind}`, timeline);
        entry.id = `timeline-case${caseIndex}-${index}`;
        const metadata = stepMetadata[kind] || {};
        for (const [name, value] of Object.entries(metadata)) entry.dataset[name] = value;
        const content = make("div", "timeline-content", entry);
        const meta = make("div", "event-meta", content);
        make("span", "event-kind", meta, kind);
        make("time", "", meta, `12:00:0${index}`);
        make("h3", "", content, ["Antes da ação", "Selecionar ordenação", "Conferir resultado", "HTTP 401 observado"][index]);
        make("p", "", content, `Conteúdo original ${caseIndex}/${index}.`);
        if (index === 0) {
          const shots = make("div", "shots", content);
          const figure = make("figure", "", shots);
          const image = make("img", "", figure);
          image.setAttribute("src", "data:image/png;base64,cHJlc2VydmVk");
          image.naturalWidth = naturalImageSize.width;
          image.naturalHeight = naturalImageSize.height;
          originalImages.push(image);
          make("figcaption", "", figure, `${caseIndex}-original.png`);
        }
        if (index === 1 && actionPreview) {
          const shots = make("div", "shots", content);
          const figure = make("figure", "action-preview", shots);
          if (storedActionMetadata !== undefined) figure.dataset.actionPreview = JSON.stringify(storedActionMetadata);
          const heading = make("div", "action-preview-heading", figure);
          make("strong", "", heading, "Onde a ação acontece");
          make("small", "", heading, "Alvo registrado antes da ação");
          const frame = make("div", "action-preview-frame", figure);
          const reference = make("img", "", frame);
          reference.dataset.sourceShot = `${caseIndex}-original.png`;
          reference.naturalWidth = naturalImageSize.width;
          reference.naturalHeight = naturalImageSize.height;
          make("figcaption", "", figure, `${caseIndex}-original.png`);
          make("p", "action-preview-note", figure, "O destaque é uma anotação sobre a captura original.");
          actionFigures.push(figure);
          actionImages.push(reference);
        }
        originals.push(entry);
      });
      if (options.projectedUpload && caseIndex === 0) {
        timeline.dataset.stepOrder = "planned";
        const entry = make("li", "timeline-entry observation", timeline);
        entry.id = "timeline-case0-user-upload";
        entry.dataset.imageSource = "user_upload";
        entry.dataset.stepState = "recorded";
        entry.dataset.stepOutcome = "Imagem anexada";
        const content = make("div", "timeline-content", entry);
        const meta = make("div", "event-meta", content);
        make("span", "event-kind", meta, "Observação");
        make("time", "", meta, "12:05:00");
        make("h3", "", content, "Imagem anexada pelo usuário");
        make("p", "", content, "Imagem fornecida por você; não aprova nem reprova o teste.");
        const figure = make("figure", "", make("div", "shots", content));
        const image = make("img", "", figure);
        image.src = "data:image/png;base64,dXNlci1pbWFnZQ==";
        make("figcaption", "", figure, "user-upload.png");
        const assertion = timeline.querySelector(".assertion");
        timeline.insertBefore(entry, assertion);
        originals.splice(originals.indexOf(assertion), 0, entry);
      }
    } else make("p", "muted", body, "Não há evidência temporal registrada para este caso.");
    cards.push(card);
  }
  const settings = make("form", "", document.body);
  settings.id = "settings-form";
  for (const id of ["show-screenshots", "show-technical", "show-observations"]) {
    const control = make("input", "", settings);
    control.id = id;
    control.checked = true;
  }
  const dialog = make("dialog", "", document.body);
  dialog.id = "evidence-dialog";
  dialog.showModal = () => dialog.setAttribute("open", "");
  dialog.close = () => { dialog.removeAttribute("open"); dialog.dispatch("close"); };
  for (const [tag, id] of [["button", "close-evidence"], ["img", "evidence-preview"], ["p", "evidence-caption"], ["a", "download-evidence"]]) {
    const element = make(tag, "", dialog);
    element.id = id;
    if (id === "evidence-preview" && actionPreview) {
      element.naturalWidth = naturalImageSize.width;
      element.naturalHeight = naturalImageSize.height;
    }
  }
  const picker = make("dialog", "step-picker-dialog", document.body);
  picker.id = "step-picker";
  picker.setAttribute("aria-labelledby", "step-picker-title");
  picker.showModal = () => picker.setAttribute("open", "");
  picker.close = () => { picker.removeAttribute("open"); picker.dispatch("close"); };
  for (const [tag, id] of [["h2", "step-picker-title"], ["p", "step-picker-position"], ["button", "step-picker-close"]]) {
    make(tag, "", picker).id = id;
  }
  for (const [type, title] of [["sws", "Executar SWS"], ["sql", "Executar SQL"], ["wait_print", "Aguardar print"]]) {
    make("button", "step-picker-choice", picker, title).dataset.stepChoice = type;
  }
  if (visualEditor) {
    const editor = make("dialog", "case-editor-dialog", document.body);
    editor.id = "case-editor";
    editor.showModal = () => editor.setAttribute("open", "");
    editor.close = () => { editor.removeAttribute("open"); editor.dispatch("close"); };
    for (const [tag, id] of [["h2", "case-editor-title"], ["button", "case-editor-close"], ["ol", "case-editor-steps"], ["p", "case-editor-feedback"], ["button", "case-editor-save"], ["button", "case-editor-rerun"]]) {
      make(tag, "", editor).id = id;
    }
    const palette = make("aside", "case-editor-palette", editor);
    palette.id = "case-editor-palette";
    const database = make("button", "database-step-card", palette, "Consulta em banco de dados");
    database.id = "database-step-card";
    database.setAttribute("draggable", "true");
  }
  const theme = make("style", "", document.body, ".report-premium { color: white; }");
  theme.id = "report-theme";
  const runtime = make("script", "", document.body, script);
  runtime.id = "report-runtime";
  const window = new FixtureElement("window", document);
  window.location = { hash, protocol: options.locationProtocol || "file:", origin: options.locationOrigin || "null" };
  window.history = { replaceState(_state, _unused, value) { window.location.hash = value; } };
  window.matchMedia = () => ({ matches: true });
  window.requestAnimationFrame = (callback) => callback();
  window.scrollTo = () => {};
  window.innerHeight = 900;
  window.innerWidth = options.journeyWidth || 1200;
  window.devicePixelRatio = options.devicePixelRatio || 1;
  window.getComputedStyle = (element) => ({ getPropertyValue: (name) => element.style.getPropertyValue(name) });
  window.setTimeout = () => {};
  window.printCalls = 0;
  window.print = () => { window.printCalls += 1; };
  window.openedTabs = [];
  window.open = (...values) => { window.openedTabs.push(values); };
  window.networkCalls = 0;
  window.fetch = () => { window.networkCalls += 1; throw new Error("Report must not make network calls"); };
  const blobs = [];
  class FixtureURL extends URL {
    static createObjectURL(blob) { blobs.push(blob); return "blob:fixture/" + blobs.length; }
    static revokeObjectURL() {}
  }
  vm.runInNewContext(script, { document, window, Element: FixtureElement, Blob, URL: FixtureURL, fetch: window.fetch, Map, Set, console }, { filename: "premium.js" });
  return { document, window, cards, originals, originalImages, actionFigures, actionImages, toolbar, topbar, blobs, downloads: document.downloads, journeyList, journeyRoute, journeyCanvas, canvasCalls };
}

function insertStep(document, gap, type = "sql") {
  gap.click();
  const picker = document.getElementById("step-picker");
  assert.equal(picker.hasAttribute("open"), true, "insertion opens the type chooser before creating a draft");
  picker.querySelector(`[data-step-choice="${type}"]`).click();
  assert.equal(picker.hasAttribute("open"), false);
}

function rerunFromEditor(document) {
  document.querySelector("[data-edit-current]").click();
  document.getElementById("case-editor-rerun").click();
  document.getElementById("case-editor-close").click();
}

test("golden journey keeps chronological DOM while laying out responsive serpentine rows", () => {
  const { document, window, journeyList, journeyRoute, canvasCalls } = fixture({ journey: true, hash: "#view/journey" });
  const nodes = journeyList.querySelectorAll(".journey-node");
  assert.deepEqual(nodes.map((node) => node.dataset.journeyPosition), Array.from({ length: 9 }, (_, index) => String(index)));
  assert.equal(journeyList.tagName, "OL");
  assert.ok(nodes.every((node) => node.tagName === "LI"));
  for (const [width, columns, gutter] of [[1200, 4, 88], [1080, 4, 88], [1050, 3, 88], [820, 3, 88], [720, 2, 88], [560, 2, 88], [450, 1, 20], [390, 1, 20]]) {
    window.innerWidth = width;
    window.dispatch("resize");
    assert.equal(journeyRoute.dataset.journeyColumns, String(columns));
    assert.equal(Number.parseInt(journeyRoute.style.getPropertyValue("--journey-columns"), 10), columns);
    assert.equal(Number.parseInt(journeyRoute.style.getPropertyValue("--journey-gutter"), 10), gutter);
    nodes.forEach((node, index) => {
      const row = Math.floor(index / columns);
      const expectedColumn = row % 2 === 0 ? index % columns + 1 : columns - index % columns;
      assert.equal(Number(node.style.getPropertyValue("grid-column") || node.style.gridColumn), expectedColumn);
      assert.equal(Number(node.style.getPropertyValue("grid-row") || node.style.gridRow), row + 1);
    });
    assert.deepEqual(journeyList.querySelectorAll(".journey-node"), nodes);
    assert.equal(journeyRoute.dataset.journeyReady, "true");
  }
  assert.ok(canvasCalls.some(({ method }) => method === "stroke"), "the route is drawn locally on the canvas");
  assert.ok(canvasCalls.some(({ method }) => method === "bezierCurveTo"), "row transitions curve around the page cards");
  assert.equal(journeyRoute.querySelectorAll("canvas").length, 1);
  assert.equal(journeyRoute.querySelectorAll("svg").length, 0, "route artwork is canvas, not a generated SVG illustration");
  assert.equal(document.body.dataset.view, "journey");
  assert.equal(document.getElementById("view-title").textContent, "Jornada observada");
  assert.equal(document.querySelector(".journey-heading-accent").textContent, "observada");
});

test("journey terminals stay within the board beside the actual first and last nodes, including partial rows", () => {
  for (const journeyCount of [1, 5, 12, 14]) {
    for (const journeyWidth of [1200, 900, 640, 390]) {
      const { journeyRoute, journeyList, journeyCanvas, canvasCalls } = fixture({ journey: true, journeyCount, journeyWidth, devicePixelRatio: 3, hash: "#view/journey" });
      const route = journeyRoute.getBoundingClientRect();
      const first = journeyList.firstElementChild.getBoundingClientRect();
      const last = journeyList.lastElementChild.getBoundingClientRect();
      const start = journeyRoute.querySelector(".journey-terminal-start").getBoundingClientRect();
      const end = journeyRoute.querySelector(".journey-terminal-end").getBoundingClientRect();
      for (const terminal of [start, end]) {
        assert.ok(terminal.left >= route.left && terminal.right <= route.right, `terminal stays horizontally inside ${journeyCount}-page ${journeyWidth}px board`);
        assert.ok(terminal.top >= route.top && terminal.bottom <= route.bottom, `terminal stays vertically inside ${journeyCount}-page ${journeyWidth}px board`);
      }
      const columns = Number(journeyRoute.dataset.journeyColumns);
      if (columns > 1) {
        assert.equal(start.top + start.height / 2, first.top + first.height / 2);
        assert.equal(end.top + end.height / 2, last.top + last.height / 2);
        assert.ok(start.right <= first.left, `INÍCIO remains beside rather than on top of the first card (${journeyWidth}px)`);
        const lastRow = Math.floor((journeyCount - 1) / columns);
        assert.ok(lastRow % 2 === 0 ? end.left >= last.right : end.right <= last.left, `FIM follows the final card in the row's direction, not an empty grid cell (${journeyCount} pages, ${journeyWidth}px, last ${last.left}–${last.right}, terminal ${end.left}–${end.right})`);
      } else {
        assert.ok(start.bottom <= first.top, "single-column start is above the first card");
        assert.ok(end.top >= last.bottom, "single-column finish is below the last card");
      }
      assert.equal(journeyCanvas.width, Math.round(route.width * 2), "pixel ratio is capped at two for a bounded canvas allocation");
      assert.equal(journeyCanvas.height, Math.round(route.height * 2));
      assert.ok(canvasCalls.every(({ args }) => args.every(Number.isFinite)));
    }
  }
});

test("downloaded golden journey preserves canonical page metadata and redraws offline without network calls", async () => {
  const { document, window, blobs, journeyRoute, journeyList } = fixture({ journey: true, visualEditor: true, hash: "#view/journey", journeyCount: 14 });
  const metadata = document.getElementById("report-data").textContent;
  const positions = journeyList.querySelectorAll(".journey-node").map((node) => node.dataset.journeyPosition);
  document.getElementById("download-report").click();
  const exported = await blobs.at(-1).text();
  assert.equal((exported.match(/<canvas[^>]*class="journey-route-canvas"/g) || []).length, 1);
  assert.equal((exported.match(/<li[^>]*data-journey-position=/g) || []).length, 14);
  assert.doesNotMatch(exported, /data-journey-ready=|data-journey-columns=/);
  assert.equal(document.getElementById("report-data").textContent, metadata);
  assert.deepEqual(journeyList.querySelectorAll(".journey-node").map((node) => node.dataset.journeyPosition), positions);
  assert.equal(journeyRoute.dataset.journeyReady, "true", "export never resets the active report's route");
  assert.equal(window.networkCalls, 0);
  assert.equal(window.openedTabs.length, 0);
  const reopened = fixture({ journey: true, exported: true, journeyCount: 14, hash: "#view/journey", journeyWidth: 390 });
  assert.equal(reopened.journeyRoute.dataset.journeyReady, "true");
  assert.equal(reopened.journeyRoute.dataset.journeyColumns, "1");
  assert.equal(reopened.window.networkCalls, 0);
});

test("journey page selection offers individual CTs and restores focus on close", () => {
  const { document, window } = fixture({ journey: true, visualEditor: true, hash: "#view/journey" });
  const pages = document.querySelectorAll("[data-journey-page]");
  const first = document.getElementById("journey-cases-0");
  pages[0].click();
  assert.equal(first.hidden, false);
  assert.equal(first.querySelectorAll("[data-open-case]").length, 2, "a page can contain multiple individual cases");
  assert.equal(pages[0].getAttribute("aria-expanded"), "true");
  assert.equal(window.location.hash, "#view/journey", "choosing a page does not select a CT prematurely");
  pages[1].click();
  assert.equal(first.hidden, true);
  assert.equal(pages[0].getAttribute("aria-expanded"), "false");
  document.getElementById("journey-cases-1").querySelector("[data-journey-close]").click();
  assert.equal(document.activeElement, pages[1]);
  pages[0].click();
  first.querySelectorAll("[data-open-case]")[1].click();
  assert.equal(window.location.hash, "#view/results/CT002");
  assert.equal(first.hidden, true);
});

test("journey CT search is accent-insensitive, scoped to its picker, and reports empty results", () => {
  const { document, window } = fixture({ journey: true, visualEditor: true, hash: "#view/journey" });
  document.querySelector("[data-journey-page]").click();
  const panel = document.getElementById("journey-cases-0");
  const search = panel.querySelector("[data-journey-search]");
  const count = panel.querySelector("[data-journey-count]");
  const empty = panel.querySelector("[data-journey-empty]");
  const rows = panel.querySelectorAll(".journey-case-row");
  assert.equal(document.activeElement, search);
  assert.equal(count.textContent, "2 CTs nesta página");
  for (const term of ["ordenacao", "ORDENAÇÃO", "CT001"]) {
    search.value = term;
    search.dispatch("input");
    assert.deepEqual(rows.map((row) => row.hidden), [false, true]);
    assert.equal(count.textContent, "1 de 2 CTs");
    assert.equal(empty.hidden, true);
  }
  search.value = "não existe";
  search.dispatch("input");
  assert.deepEqual(rows.map((row) => row.hidden), [true, true]);
  assert.equal(count.textContent, "0 de 2 CTs");
  assert.equal(empty.hidden, false);
  assert.equal(document.getElementById("journey-cases-1").querySelector(".journey-case-row").hidden, false, "search never filters another page's CTs");
  search.value = "";
  search.dispatch("input");
  assert.deepEqual(rows.map((row) => row.hidden), [false, false]);
  assert.equal(count.textContent, "2 CTs nesta página");
  assert.equal(empty.hidden, true);
  assert.equal(window.location.hash, "#view/journey");
});

test("CT visual editor shows every canonical timeline kind without cloning evidence or changing the result", () => {
  const { document, window, originals, originalImages, cards } = fixture({ visualEditor: true, journey: true, withSignal: true, hash: "#view/journey" });
  const before = originals.map((entry) => entry.outerHTML);
  const source = document.getElementById("report-data").textContent;
  const edit = document.getElementById("journey-cases-0").querySelector("[data-edit-case]");
  edit.click();
  const editor = document.getElementById("case-editor");
  const list = document.getElementById("case-editor-steps");
  assert.equal(editor.hasAttribute("open"), true);
  assert.match(document.getElementById("case-editor-title").textContent, /CT001/);
  assert.deepEqual(list.querySelectorAll(".case-editor-recorded").map((item) => item.dataset.stepKind), ["observation", "action", "assertion", "signal"]);
  assert.equal(list.querySelectorAll("img, figure").length, 0);
  assert.equal(list.querySelectorAll(".case-editor-gap").length, 5);
  assert.ok(list.querySelectorAll(".case-editor-recorded").every((item) => !item.hasAttribute("draggable") && !item.querySelector("button")));
  assert.match(document.getElementById("case-editor-feedback").textContent, /não executado.*não altera este resultado/);
  assert.deepEqual(originals.map((entry) => entry.outerHTML), before);
  assert.equal(originalImages.length, 2);
  assert.equal(document.getElementById("report-data").textContent, source);
  assert.equal(cards[0].dataset.status, "PASS");
  assert.equal(window.location.hash, "#view/journey");
  document.getElementById("case-editor-close").click();
  assert.equal(editor.hasAttribute("open"), false);
  assert.equal(document.activeElement, edit);
});

test("recorded editor cards preserve each explicit outcome as literal text without borrowing the case verdict", () => {
  const { document, cards } = fixture({ visualEditor: true, stepMetadata: {
    observation: { stepState: "recorded", stepOutcome: "Captura antes da ação" },
    action: { stepState: "pass", stepOutcome: "Ação concluída" },
    assertion: { stepState: "fail", stepOutcome: "<b>Conferência não passou</b>" },
  } });
  document.querySelector("[data-edit-current]").click();
  const list = document.getElementById("case-editor-steps");
  assert.deepEqual(list.querySelectorAll(".case-editor-state").map((item) => item.textContent), [
    "Captura antes da ação", "Ação concluída", "<b>Conferência não passou</b>",
  ]);
  assert.equal(list.querySelectorAll("b").length, 0);
  assert.equal(cards[0].dataset.status, "PASS", "a failed recorded check never rewrites the preserved passing case verdict");
  const fallback = fixture({ visualEditor: true, stepMetadata: { action: { stepState: "pass", stepOutcome: " " } } });
  fallback.document.querySelector("[data-edit-current]").click();
  assert.equal(fallback.document.getElementById("case-editor-steps").querySelectorAll(".case-editor-state")[1].textContent, "Passou");
});

test("planner templates support drag/drop, accessible insertion and reorder while original steps remain fixed", () => {
  const { document, originals } = fixture({ visualEditor: true });
  document.querySelector("[data-edit-current]").click();
  const list = document.getElementById("case-editor-steps");
  const palette = document.querySelector('[data-step-template="sql"]');
  const transfer = { setData() {} };
  const gap = list.querySelectorAll(".case-editor-gap")[1];
  const before = originals.map((entry) => entry.outerHTML);
  gap.dispatch("drop", { dataTransfer: transfer });
  assert.equal(list.querySelectorAll(".case-editor-prototype").length, 0, "external text drops cannot create a database placeholder");
  palette.dispatch("dragstart", { dataTransfer: transfer });
  assert.equal(gap.dispatch("dragover", { dataTransfer: transfer }).defaultPrevented, true);
  gap.dispatch("drop", { dataTransfer: transfer });
  let prototype = list.querySelector(".case-editor-prototype");
  assert.ok(prototype);
  assert.match(prototype.textContent, /SQL.*integração ainda não está disponível/);
  assert.deepEqual(list.querySelectorAll("[data-editor-step]").map((item) => item.dataset.editorStep), ["recorded", "sql", "recorded", "recorded"]);
  prototype.dispatch("keydown", { key: "ArrowDown", altKey: true });
  prototype = list.querySelector(".case-editor-prototype");
  assert.equal(document.activeElement, prototype);
  assert.deepEqual(list.querySelectorAll("[data-editor-step]").map((item) => item.dataset.editorStep), ["recorded", "recorded", "sql", "recorded"]);
  prototype.dispatch("dragstart", { dataTransfer: transfer });
  list.querySelector(".case-editor-gap").dispatch("drop", { dataTransfer: transfer });
  assert.equal(list.querySelectorAll("[data-editor-step]")[0].dataset.editorStep, "sql");
  insertStep(document, list.querySelectorAll(".case-editor-insert").at(-1));
  assert.equal(list.querySelectorAll(".case-editor-prototype").length, 2);
  list.querySelector(".case-editor-prototype").querySelectorAll("button").at(-1).click();
  assert.equal(list.querySelectorAll(".case-editor-prototype").length, 1);
  assert.deepEqual(list.querySelectorAll(".case-editor-recorded").map((item) => item.dataset.recordedIndex), ["0", "1", "2"]);
  assert.deepEqual(originals.map((entry) => entry.outerHTML), before);
});

test("saved visual drafts are per CT, export as bounded JSON only, and reopen offline", async () => {
  const { document, blobs, originals } = fixture({ visualEditor: true, journey: true });
  const originalSource = document.getElementById("report-data").textContent;
  document.querySelector("[data-edit-current]").click();
  insertStep(document, document.getElementById("case-editor-steps").querySelectorAll(".case-editor-insert")[1]);
  document.getElementById("case-editor-save").click();
  assert.match(document.getElementById("case-editor-feedback").textContent, /Baixe o relatório para conservar/);
  document.getElementById("case-editor-close").click();
  const second = document.getElementById("journey-cases-0").querySelectorAll("[data-edit-case]")[1];
  second.click();
  assert.equal(document.getElementById("case-editor-steps").querySelectorAll(".case-editor-prototype").length, 0);
  insertStep(document, document.getElementById("case-editor-steps").querySelectorAll(".case-editor-insert").at(-1));
  document.getElementById("case-editor-save").click();
  document.getElementById("download-report").click();
  const exported = await blobs.at(-1).text();
  const payload = JSON.parse(exported.match(/<script[^>]*id="report-drafts"[^>]*>([\s\S]*?)<\/script>/)[1]);
  assert.deepEqual(Object.keys(payload.cases), ["0", "1"]);
  assert.deepEqual(payload.cases["0"].map(({ type, gap }) => ({ type, gap })), [{ type: "sql", gap: 1 }]);
  assert.deepEqual(payload.cases["1"].map(({ type, gap }) => ({ type, gap })), [{ type: "sql", gap: 3 }]);
  assert.doesNotMatch(exported, /<li[^>]*class="case-editor-step/);
  assert.doesNotMatch(exported, /<dialog[^>]*id="case-editor"[^>]*\bopen=/);
  assert.equal((exported.match(/data:image\/png;base64,cHJlc2VydmVk/g) || []).length, 2);
  assert.equal(document.getElementById("report-data").textContent, originalSource);
  assert.equal(originals.length, 6);
  const reopened = fixture({ visualEditor: true, exported: true, drafts: payload });
  reopened.document.querySelector("[data-edit-current]").click();
  assert.equal(reopened.document.getElementById("case-editor-steps").querySelectorAll(".case-editor-prototype").length, 1);
  reopened.document.getElementById("case-editor-save").click();
  assert.match(reopened.document.getElementById("case-editor-feedback").textContent, /O arquivo original não foi alterado/);
});

test("visual drafts discard invalid imported fields and bound placeholders to 100 per CT", () => {
  const drafts = { version: 1, cases: { "0": [
    { id: "db-1", type: "database", gap: 1, order: 0, sql: "DELETE", status: "PASS" },
    { id: "db-1", type: "database", gap: 1, order: 1 },
    { id: "<script>", type: "database", gap: 1, order: 1 },
    { id: "db-2", type: "execute", gap: 1, order: 1 },
    { id: "db-3", type: "database", gap: 999, order: 1 },
    { id: "db-4", type: "database", gap: -1, order: 1 },
    null,
  ] } };
  const { document } = fixture({ visualEditor: true, drafts });
  document.querySelector("[data-edit-current]").click();
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 1);
  document.getElementById("case-editor-save").click();
  const saved = JSON.parse(document.getElementById("report-drafts").textContent);
  assert.deepEqual(saved.cases["0"], [{ id: "db-1", type: "database", gap: 1, order: 0 }]);
  const bounded = fixture({ visualEditor: true, drafts: { version: 1, cases: { "0": Array.from({ length: 120 }, (_, index) => ({
    id: "db-" + (index + 1), type: "database", gap: 0, order: index % 100,
  })) } } });
  bounded.document.querySelector("[data-edit-current]").click();
  assert.equal(bounded.document.querySelectorAll(".case-editor-prototype").length, 100);
  assert.ok(bounded.document.querySelectorAll(".case-editor-insert").every((control) => control.disabled));
  bounded.document.querySelector(".case-editor-insert").click();
  assert.equal(bounded.document.querySelectorAll(".case-editor-prototype").length, 100);
  assert.equal(bounded.document.getElementById("step-picker").hasAttribute("open"), false, "a disabled insertion control cannot open the picker at the limit");
});

test("empty and malformed draft reports retain an honest visual-only editor and rerun feedback", () => {
  for (const drafts of ["{invalid", "null", '{"version":99,"cases":{}}']) {
    const { document, window, cards } = fixture({ visualEditor: true, emptyTimeline: true, drafts });
    document.querySelector("[data-edit-current]").click();
    assert.match(document.querySelector(".case-editor-empty").textContent, /não tem passos registrados/);
    assert.equal(document.querySelectorAll(".case-editor-recorded").length, 0);
    insertStep(document, document.getElementById("case-editor-steps").querySelector(".case-editor-insert"));
    assert.equal(document.querySelectorAll(".case-editor-prototype").length, 1);
    document.getElementById("case-editor-rerun").click();
    assert.match(document.getElementById("case-editor-feedback").textContent, /Reexecução não iniciada.*Abra o relatório pela aplicação/);
    assert.equal(cards[0].dataset.status, "PASS");
    assert.equal(window.location.hash, "#view/results/CT001");
    document.dispatch("keydown", { key: "Escape" });
    assert.equal(document.getElementById("case-editor").hasAttribute("open"), false);
  }
});

test("a connected blob report hands off only to same-origin app confirmation and never executes or fetches", async () => {
  const { document, window, blobs, cards } = fixture({ visualEditor: true,
    runId: "source-123", locationProtocol: "blob:", locationOrigin: "http://localhost:5173",
    appRuntime: { app_url: "http://localhost:5173", source_exploration_id: "source-123" },
  });
  document.querySelector("[data-edit-current]").click();
  assert.equal(document.getElementById("case-editor-rerun").textContent, "Revisar e confirmar na aplicação →");
  insertStep(document, document.getElementById("case-editor-steps").querySelector(".case-editor-insert"));
  document.getElementById("case-editor-save").click();
  document.getElementById("case-editor-rerun").click();
  assert.deepEqual(window.openedTabs, [["http://localhost:5173/#/reexecutar/source-123/CT001", "_blank", "noopener,noreferrer"]]);
  assert.equal(window.networkCalls, 0);
  assert.equal(cards[0].dataset.status, "PASS");
  assert.match(document.getElementById("case-editor-feedback").textContent, /Nenhuma ação foi iniciada.*passos extras.*não serão executados/);
  document.getElementById("download-report").click();
  const exported = await blobs.at(-1).text();
  assert.doesNotMatch(exported, /<script[^>]*id="report-app-runtime"/);
  assert.match(exported, /<script[^>]*id="report-drafts"/);
  assert.equal(document.getElementById("report-app-runtime").getAttribute("type"), "application/json", "export removes the connection only from its detached snapshot");
});

test("offline, malformed and tampered report connections cannot open an app or contact any server", () => {
  const valid = { app_url: "http://localhost:5173", source_exploration_id: "source-123" };
  for (const invalid of [
    { locationProtocol: "file:" }, { locationProtocol: "https:" }, { locationOrigin: "null" },
    { appRuntime: "{invalid" }, { appRuntime: null }, { runtimeType: "text/javascript" },
    { appRuntime: { ...valid, app_url: "https://other.example" } },
    { appRuntime: { ...valid, app_url: "javascript:alert(1)" } },
    { appRuntime: { ...valid, app_url: "http://secret@localhost:5173" } },
    { appRuntime: { ...valid, app_url: "http://localhost:5173/other" } },
    { appRuntime: { ...valid, app_url: "http://localhost:5173/?token=secret" } },
    { appRuntime: { ...valid, source_exploration_id: "other-run" } },
  ]) {
    const { document, window } = fixture({ visualEditor: true, runId: "source-123",
      locationProtocol: "blob:", locationOrigin: "http://localhost:5173", appRuntime: valid, ...invalid,
    });
    document.querySelector("[data-edit-current]").click();
    assert.equal(document.getElementById("case-editor-rerun").textContent, "Como reexecutar");
    document.getElementById("case-editor-rerun").click();
    assert.equal(window.openedTabs.length, 0);
    assert.equal(window.networkCalls, 0);
    assert.match(document.getElementById("case-editor-feedback").textContent, /Reexecução não iniciada.*Abra o relatório pela aplicação/);
  }
});

function plannerFixture(options = {}) {
  const result = fixture({ ...options, visualEditor: true });
  result.document.querySelector("[data-edit-current]").click();
  return result;
}
const plannerConnection = {
  runId: "source-123", locationProtocol: "blob:", locationOrigin: "http://localhost:5173",
  appRuntime: { app_url: "http://localhost:5173", source_exploration_id: "source-123" },
};

test("rerun toolbar opens a planner before a separate explicit application handoff", () => {
  const { document, window, cards } = fixture({ ...plannerConnection, visualEditor: true });
  document.getElementById("case-rerun").click();
  assert.equal(document.getElementById("case-editor").hasAttribute("open"), true);
  assert.equal(window.openedTabs.length, 0);
  assert.equal(document.querySelectorAll(".case-inline-insert, .case-inline-gap").length, 0);
  assert.deepEqual(document.querySelectorAll(".case-editor-insert").map((button) => button.textContent), ["+", "+", "+", "+"]);
  document.getElementById("case-editor-rerun").click();
  assert.deepEqual(window.openedTabs, [["http://localhost:5173/#/reexecutar/source-123/CT001", "_blank", "noopener,noreferrer"]]);
  assert.equal(window.networkCalls, 0);
  assert.deepEqual(cards.map((card) => card.dataset.status), ["PASS", "FAIL"]);
});

test("planner insertion keeps all canonical evidence and result controls read-only", () => {
  const { document, originals, originalImages, cards } = plannerFixture();
  const canonical = originals.map((entry) => entry.outerHTML);
  const source = document.getElementById("report-data").textContent;
  insertStep(document, document.querySelectorAll(".case-editor-insert")[1], "wait_print");
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 1);
  assert.equal(document.querySelectorAll(".case-inline-insert, .case-inline-draft").length, 0);
  assert.deepEqual(originals.map((entry) => entry.outerHTML), canonical);
  assert.deepEqual(document.querySelectorAll("img[src]"), originalImages);
  assert.equal(document.getElementById("report-data").textContent, source);
  document.getElementById("case-editor-save").click();
  assert.equal(cards[0].querySelectorAll(".case-step-button").length, 4);
  assert.equal(cards[0].querySelectorAll(".case-step-button[data-step-state='draft']").length, 0);
});

test("planner picker offers exactly three types and cancel inserts no step", () => {
  const { document } = plannerFixture();
  document.querySelectorAll(".case-editor-insert")[1].click();
  assert.equal(document.getElementById("step-picker").hasAttribute("open"), true);
  assert.deepEqual(document.querySelectorAll("#step-picker [data-step-choice]").map((button) => button.dataset.stepChoice), ["sws", "sql", "wait_print"]);
  document.getElementById("step-picker-close").click();
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 0);
  assert.equal(document.activeElement, document.querySelectorAll(".case-editor-insert")[1]);
});

test("planner tool clicks arm a type and typed drag/drop works only for trusted local templates", () => {
  const { document } = plannerFixture();
  const list = document.getElementById("case-editor-steps");
  const transfer = { setData() {} };
  list.querySelector(".case-editor-gap").dispatch("drop", { dataTransfer: transfer });
  assert.equal(list.querySelectorAll(".case-editor-prototype").length, 0);
  const tool = document.querySelector('[data-step-template="wait_print"]');
  tool.click();
  assert.equal(tool.getAttribute("aria-pressed"), "true");
  list.querySelector(".case-editor-insert").click();
  assert.equal(document.getElementById("step-picker").hasAttribute("open"), false);
  assert.equal(list.querySelector(".case-editor-prototype").dataset.draftType, "wait_print");
  tool.click();
  const sql = document.querySelector('[data-step-template="sql"]');
  sql.dispatch("dragstart", { dataTransfer: transfer });
  list.querySelectorAll(".case-editor-gap").at(-1).dispatch("drop", { dataTransfer: transfer });
  assert.equal(list.querySelectorAll('.case-editor-prototype[data-draft-type="sql"]').length, 1);
});

test("every planner boundary survives confirmation and export without counting the case summary", async () => {
  for (const gap of [0, 1, 2, 3]) {
    const { document, window, blobs, originals } = plannerFixture(plannerConnection);
    const before = originals.map((entry) => entry.outerHTML);
    insertStep(document, document.querySelectorAll(".case-editor-insert")[gap], "wait_print");
    const draft = document.querySelector(".case-editor-prototype");
    document.getElementById("case-editor-rerun").click();
    const plan = JSON.parse(new URLSearchParams(window.openedTabs[0][0].split("?")[1]).get("capture_plan"));
    assert.deepEqual(plan, { version: 1, checkpoints: [{ id: draft.dataset.draftId, gap, order: 0 }] });
    const saved = JSON.parse(document.getElementById("report-drafts").textContent);
    assert.equal(saved.cases["0"][0].gap, gap);
    document.getElementById("download-report").click();
    const html = await blobs.at(-1).text();
    assert.doesNotMatch(html, /class="case-editor-step/);
    const reopened = plannerFixture({ ...plannerConnection, drafts: saved });
    reopened.document.getElementById("case-editor-rerun").click();
    const nextPlan = JSON.parse(new URLSearchParams(reopened.window.openedTabs[0][0].split("?")[1]).get("capture_plan"));
    assert.deepEqual(nextPlan, plan);
    assert.deepEqual(originals.map((entry) => entry.outerHTML), before);
  }
});

test("planner SWS and SQL retain optional print pauses but never execute integrations", () => {
  const { document, window } = plannerFixture(plannerConnection);
  insertStep(document, document.querySelectorAll(".case-editor-insert")[1], "sws");
  insertStep(document, document.querySelectorAll(".case-editor-insert").at(-1), "sql");
  for (const draft of document.querySelectorAll(".case-editor-prototype")) {
    assert.equal(draft.querySelector(".case-draft-disabled-action").disabled, true);
    const checkbox = draft.querySelector(".case-editor-wait-print");
    checkbox.checked = true;
    checkbox.dispatch("change");
  }
  document.getElementById("case-editor-rerun").click();
  const target = window.openedTabs[0][0];
  const plan = JSON.parse(new URLSearchParams(target.split("?")[1]).get("capture_plan"));
  assert.deepEqual(plan.checkpoints.map((item) => [item.gap, item.order]), [[1, 0], [3, 1]]);
  assert.ok(plan.checkpoints.every((item) => Object.keys(item).join(",") === "id,gap,order"));
  assert.doesNotMatch(target, /sws|sql|credentials/i);
  assert.equal(window.networkCalls, 0);
});

test("closing a planner discards only unsaved changes and restores its trigger", () => {
  const { document } = plannerFixture();
  insertStep(document, document.querySelector(".case-editor-insert"), "wait_print");
  document.getElementById("case-editor-close").click();
  assert.equal(document.activeElement, document.querySelector("[data-edit-current]"));
  document.querySelector("[data-edit-current]").click();
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 0);
  insertStep(document, document.querySelector(".case-editor-insert"), "wait_print");
  document.getElementById("case-editor-save").click();
  document.getElementById("case-editor-close").click();
  document.querySelector("[data-edit-current]").click();
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 1);
});

test("planner navigation invalidates pending choices and keeps drafts isolated per CT", () => {
  const { document, window } = plannerFixture();
  insertStep(document, document.querySelector(".case-editor-insert"), "wait_print");
  document.getElementById("case-editor-save").click();
  document.querySelector(".case-editor-insert").click();
  window.location.hash = "#view/results/CT002";
  window.dispatch("hashchange");
  assert.equal(document.getElementById("case-editor").hasAttribute("open"), false);
  assert.equal(document.getElementById("step-picker").hasAttribute("open"), false);
  document.querySelector("[data-edit-current]").click();
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 0);
  document.getElementById("step-picker").querySelector('[data-step-choice="sql"]').click();
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 0);
});

test("planner draft imports sanitize known types, booleans, positions and executable fields", () => {
  const drafts = { version: 1, cases: { "0": [
    { id: "db-1", type: "sql", gap: 1, order: 0, wait_for_print: true, query: "DROP", credentials: "SECRET" },
    { id: "db-1", type: "sws", gap: 1, order: 1 },
    { id: "db-2", type: "wait_print", gap: 99, order: 1 },
    { id: "db-3", type: "sws", gap: 2, order: 1, wait_for_print: "true", script: "evil()" },
    { id: "db-4", type: "execute", gap: 0, order: 0 },
    { id: "bad-id", type: "wait_print", gap: 0, order: 0 },
    { id: "db-5", type: "database", gap: 3, order: 2, password: "SECRET" },
  ] } };
  const { document } = plannerFixture({ drafts });
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 3);
  document.getElementById("case-editor-save").click();
  const saved = JSON.parse(document.getElementById("report-drafts").textContent);
  assert.deepEqual(saved.cases["0"], [
    { id: "db-1", type: "sql", wait_for_print: true, gap: 1, order: 0 },
    { id: "db-3", type: "sws", wait_for_print: false, gap: 2, order: 1 },
    { id: "db-5", type: "database", gap: 3, order: 2 },
  ]);
  assert.doesNotMatch(document.getElementById("report-drafts").textContent, /DROP|SECRET|evil|query|credentials|password/);
});

test("planner manual captures share a gap, retain keyboard order and support removal", () => {
  const { document, window } = plannerFixture(plannerConnection);
  insertStep(document, document.querySelectorAll(".case-editor-insert")[1], "wait_print");
  insertStep(document, document.querySelectorAll(".case-editor-insert")[2], "wait_print");
  const ids = document.querySelectorAll(".case-editor-prototype").map((draft) => draft.dataset.draftId);
  document.querySelector(".case-editor-prototype").dispatch("keydown", { key: "ArrowRight", altKey: true });
  document.getElementById("case-editor-rerun").click();
  const plan = JSON.parse(new URLSearchParams(window.openedTabs[0][0].split("?")[1]).get("capture_plan"));
  assert.deepEqual(plan.checkpoints, [{ id: ids[1], gap: 1, order: 0 }, { id: ids[0], gap: 1, order: 1 }]);
  document.querySelector(".case-editor-prototype").querySelectorAll("button").at(-1).click();
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 1);
});

test("planner allows exactly 100 mixed typed and legacy steps and keeps saved data offline", async () => {
  const drafts = { version: 1, cases: { "0": Array.from({ length: 120 }, (_, index) => ({
    id: "db-" + (index + 1), type: ["sws", "sql", "wait_print", "database"][index % 4],
    gap: 0, order: index % 100, wait_for_print: index % 2 === 0,
  })) } };
  const { document, blobs } = plannerFixture({ drafts });
  assert.equal(document.querySelectorAll(".case-editor-prototype").length, 100);
  assert.ok(document.querySelectorAll(".case-editor-insert").every((control) => control.disabled));
  document.querySelector(".case-editor-prototype").querySelectorAll("button").at(-1).click();
  insertStep(document, document.querySelectorAll(".case-editor-insert").at(-1), "wait_print");
  document.getElementById("case-editor-save").click();
  document.getElementById("download-report").click();
  const html = await blobs.at(-1).text();
  const stored = JSON.parse(html.match(/<script[^>]*id="report-drafts"[^>]*>([\s\S]*?)<\/script>/)[1]);
  assert.equal(stored.cases["0"].length, 100);
  const offline = plannerFixture({ drafts: stored, exported: true });
  assert.equal(offline.document.querySelectorAll(".case-editor-prototype").length, 100);
  assert.equal(offline.document.querySelectorAll(".case-inline-insert, .case-inline-draft").length, 0);
});

test("native planned steps retain an uploaded image between the chosen neighbors and reopen without duplicating evidence", async () => {
  const { document, window, cards, blobs } = fixture({ projectedUpload: true });
  const card = cards[0];
  const titles = ["Antes da ação", "Selecionar ordenação", "Imagem anexada pelo usuário", "Conferir resultado"];
  const timeline = card.querySelector(".evidence-timeline");
  assert.equal(timeline.dataset.stepOrder, "planned");
  assert.deepEqual(timeline.children.map((entry) => entry.querySelector("h3").textContent), titles);
  assert.equal(card.querySelector(".case-step-order-note").textContent, "Ordem dos passos planejada · horários reais preservados");
  const imageControl = card.querySelectorAll(".case-step-button[data-recorded-index]")[3];
  assert.equal(imageControl.querySelector(".step-kind").textContent, "Imagem do usuário");
  imageControl.click();
  assert.equal(card.querySelector(".case-step-position").textContent, "Etapa 3 de 4 · Imagem do usuário");
  assert.equal(card.querySelector('[data-image-source="user_upload"] time').textContent, "12:05:00", "the later attachment time remains real, not rewritten to fit the planned order");
  document.getElementById("download-report").click();
  const html = await blobs.at(-1).text();
  assert.match(html, /<ol[^>]*data-step-order="planned"/);
  const positions = ["timeline-case0-0", "timeline-case0-1", "timeline-case0-user-upload", "timeline-case0-2"].map((id) => html.indexOf('id="' + id + '"'));
  assert.ok(positions.every((position, index) => position >= 0 && (!index || position > positions[index - 1])));
  assert.equal((html.match(/data:image\/png;base64,dXNlci1pbWFnZQ==/g) || []).length, 1);
  assert.doesNotMatch(html, /class="case-step-order-note"|class="step-preview-button"/, "export keeps canonical data, not live enhancements");
  const reopened = fixture({ projectedUpload: true, exported: true });
  assert.deepEqual(reopened.cards[0].querySelectorAll(".timeline-entry").map((entry) => entry.querySelector("h3").textContent), titles);
  assert.equal(reopened.cards[0].querySelectorAll(".case-step-order-note").length, 1);
  assert.equal(reopened.cards[0].querySelectorAll('[data-image-source="user_upload"]').length, 1);
  assert.equal(reopened.cards[0].querySelectorAll(".case-step-button[data-recorded-index]")[3].querySelector(".step-kind").textContent, "Imagem do usuário");
  assert.equal(card.dataset.status, "PASS");
  assert.equal(window.networkCalls + reopened.window.networkCalls, 0);
});

test("HTML exports omit only their download control while preserving the untouched offline report", async () => {
  const { document, window, cards, originals, originalImages, toolbar, blobs, downloads } = fixture({ hash: "#view/overview", withSignal: true });
  const liveDownload = document.getElementById("download-report");
  const sourceData = document.getElementById("report-data").textContent;
  liveDownload.click();
  const initialExport = await blobs[0].text();

  // Export after filters, CT/step selection, settings and a preview have changed
  // the live document. None of these transient changes should enter the file.
  document.querySelector('[data-report-nav="tests"]').click();
  document.getElementById("search").value = "CT002";
  document.getElementById("search").dispatch("input");
  assert.equal(document.querySelector('#case-rows tr[data-case-index="0"]').hidden, true);
  document.querySelector('[data-report-nav="results"]').click();
  document.getElementById("case-next").click();
  cards[1].querySelectorAll(".case-step-button")[2].click();
  document.getElementById("show-technical").checked = false;
  document.getElementById("settings-form").dispatch("submit");
  originalImages[1].click();
  assert.equal(document.getElementById("evidence-dialog").hasAttribute("open"), true);
  const liveSelection = cards[1].querySelector('.case-step-button[aria-current="step"]');
  const liveHash = window.location.hash;
  liveDownload.click();
  liveDownload.click();

  assert.deepEqual(downloads, [1, 2, 3].map((index) => ({ filename: "autojev-relatorio.html", href: "blob:fixture/" + index })));
  for (const blob of blobs) {
    assert.equal(blob.type, "text/html;charset=utf-8");
    const html = await blob.text();
    assert.equal(html, initialExport, "exports must reuse the initial canonical snapshot, not the currently filtered DOM");
    assert.ok(html.startsWith("<!DOCTYPE html>\n<html"));
    assert.doesNotMatch(html, /id="download-report"/);
    assert.match(html, /id="print-report"/);
    assert.match(html, /id="download-csv"/);
    assert.match(html, /id="download-evidence"/);
    assert.match(html, /id="report-theme"/);
    assert.match(html, /id="report-runtime"/);
    const json = html.match(/<script[^>]*id="report-data"[^>]*>(.*?)<\/script>/s)?.[1];
    assert.equal(json, sourceData);
    assert.equal((html.match(/<details class="scenario"/g) || []).length, 2);
    assert.equal((html.match(/src="data:image\/png;base64,cHJlc2VydmVk"/g) || []).length, 2);
    for (const original of originals) assert.equal(html.split(`id="${original.id}"`).length - 1, 1);
    assert.doesNotMatch(html, /class="[^"]*step-explorer|class="[^"]*case-workspace|class="[^"]*step-preview-button/);
    assert.doesNotMatch(html, /class="report-premium hide-technical"|<dialog[^>]*\bopen=""/);
  }
  assert.equal(document.getElementById("download-report"), liveDownload, "export must not remove the application report control");
  assert.equal(document.querySelectorAll("#download-report").length, 1);
  assert.equal(toolbar.parentElement, document.getElementById("result-report-actions"));
  assert.equal(cards[1].querySelector('.case-step-button[aria-current="step"]'), liveSelection);
  assert.equal(window.location.hash, liveHash);
  assert.equal(document.getElementById("evidence-dialog").hasAttribute("open"), true);
});

test("connected blobs keep the rerun action and its original styling after exporting HTML without it", async () => {
  const { document, window, blobs } = fixture({ visualEditor: true,
    runId: "source-123", locationProtocol: "blob:", locationOrigin: "http://localhost:5173",
    appRuntime: { app_url: "http://localhost:5173", source_exploration_id: "source-123" },
  });
  const rerun = document.getElementById("case-rerun");
  assert.equal(rerun.hidden, false);
  assert.equal(rerun.classList.contains("button-rerun"), true);
  assert.equal(rerun.getAttribute("aria-label"), "Reexecutar teste CT001");
  rerun.click();
  assert.equal(window.openedTabs.length, 0);
  document.getElementById("case-editor-rerun").click();
  assert.deepEqual(window.openedTabs, [["http://localhost:5173/#/reexecutar/source-123/CT001", "_blank", "noopener,noreferrer"]]);
  assert.equal(document.getElementById("case-editor").hasAttribute("open"), true);
  document.getElementById("download-report").click();
  const html = await blobs.at(-1).text();
  assert.doesNotMatch(html, /id="case-rerun(?:-feedback)?"/);
  assert.doesNotMatch(html, /id="report-app-runtime"/);
  assert.match(html, /id="case-editor-rerun"/);
  assert.equal(document.getElementById("case-rerun"), rerun);
  assert.equal(rerun.hidden, false);
  document.getElementById("case-next").click();
  assert.equal(rerun.getAttribute("aria-label"), "Reexecutar teste CT002");
  assert.equal(document.getElementById("case-rerun-feedback").hidden, true);
  rerun.click();
  document.getElementById("case-editor-rerun").click();
  assert.equal(window.openedTabs.at(-1)[0], "http://localhost:5173/#/reexecutar/source-123/CT002");
  assert.equal(window.networkCalls, 0);
});

test("offline HTML removes the old rerun toolbar before display and download", async () => {
  for (const exported of [false, true]) {
    const { document, window, blobs } = fixture({ legacyRerun: true, exported, visualEditor: true });
    assert.equal(document.getElementById("case-rerun"), null);
    assert.equal(document.getElementById("case-rerun-feedback"), null);
    assert.ok(document.querySelector("[data-edit-current]"));
    assert.ok(document.getElementById("print-report"));
    document.getElementById("case-next").click();
    assert.equal(document.body.dataset.view, "results");
    assert.equal(window.location.hash, "#view/results/CT002");
    if (!exported) {
      document.getElementById("download-report").click();
      const html = await blobs.at(-1).text();
      assert.doesNotMatch(html, /id="case-rerun(?:-feedback)?"/);
      assert.match(html, /id="case-editor-rerun"/);
    }
  }
});

test("an exported report without its HTML download control retains navigation, printing and CSV", async () => {
  const { document, window, cards, toolbar, topbar, blobs, downloads } = fixture({ exported: true });
  assert.equal(document.getElementById("download-report"), null);
  assert.equal(toolbar.parentElement, document.getElementById("result-report-actions"));
  document.getElementById("print-report").click();
  assert.equal(window.printCalls, 1);
  cards[0].querySelectorAll(".case-step-button")[2].click();
  assert.equal(cards[0].querySelector(".case-selected-title").textContent, "Selecionar ordenação");
  document.getElementById("case-next").click();
  assert.equal(cards[0].hidden, true);
  assert.equal(cards[1].hidden, false);
  document.getElementById("case-back").click();
  assert.equal(document.body.dataset.view, "tests");
  assert.equal(toolbar.parentElement, topbar);
  document.getElementById("download-csv").click();
  assert.equal(downloads[0].filename, "autojev-casos.csv");
  assert.equal(blobs[0].type, "text/csv;charset=utf-8");
  assert.match(await blobs[0].text(), /"CT001"/);
  assert.match(await blobs[0].text(), /"CT002"/);
  document.getElementById("print-report").click();
  assert.equal(window.printCalls, 2);
  assert.equal(document.getElementById("download-report"), null);
});

test("the full-canvas results view reuses report actions and restores them outside results", () => {
  const { document, toolbar, topbar } = fixture();
  const resultActions = document.getElementById("result-report-actions");
  const print = document.getElementById("print-report");
  const download = document.getElementById("download-report");
  assert.equal(toolbar.parentElement, resultActions);
  assert.equal(document.querySelectorAll(".toolbar").length, 1);
  for (const view of ["overview", "results", "tests", "results", "overview"]) {
    document.querySelector(`[data-report-nav="${view}"]`).click();
    assert.equal(toolbar.parentElement, view === "results" ? resultActions : topbar);
    assert.equal(document.getElementById("print-report"), print);
    assert.equal(document.getElementById("download-report"), download);
    assert.equal(document.querySelectorAll("#print-report").length, 1);
    assert.equal(document.querySelectorAll("#download-report").length, 1);
  }
});

test("result navigation focuses its visible heading and retains the live print action", () => {
  const { document, window, toolbar } = fixture({ hash: "#view/overview" });
  document.querySelector('[data-report-nav="results"]').click();
  const resultTitle = document.getElementById("result-view-title");
  assert.equal(document.activeElement, resultTitle);
  assert.equal(resultTitle.textContent, "Resultado do teste");
  assert.equal(toolbar.parentElement, document.getElementById("result-report-actions"));
  document.getElementById("print-report").click();
  assert.equal(window.printCalls, 1, "moving the original toolbar must preserve its event handlers");
  document.querySelector('[data-report-nav="overview"]').click();
  assert.equal(document.activeElement, document.getElementById("view-title"));
  document.getElementById("print-report").click();
  assert.equal(window.printCalls, 2);
});

test("printing preserves the full-canvas toolbar location and selected case", () => {
  const { document, window, cards, toolbar } = fixture();
  cards[0].querySelectorAll(".case-step-button")[2].click();
  const selected = cards[0].querySelector('.case-step-button[aria-current="step"]');
  window.dispatch("beforeprint");
  window.dispatch("afterprint");
  assert.equal(toolbar.parentElement, document.getElementById("result-report-actions"));
  assert.equal(document.body.dataset.view, "results");
  assert.equal(cards[0].querySelector('.case-step-button[aria-current="step"]'), selected);
  assert.equal(cards[0].hidden, false);
  assert.equal(cards[1].hidden, true);
});

test("step explorer moves canonical evidence without duplicating cards, anchors or captures", () => {
  const { document, cards, originals, originalImages } = fixture();
  assert.equal(document.querySelectorAll("details.scenario[data-case-index]").length, 2);
  assert.equal(document.querySelectorAll("img[src]").length, 2);
  assert.deepEqual(document.querySelectorAll("img[src]"), originalImages);
  for (const original of originals) {
    assert.equal(document.getElementById(original.id), original);
    assert.equal(document.querySelectorAll(`#${original.id}`).length, 1);
    if (original.closest("details.scenario") === cards[0]) {
      assert.ok(original.closest(".case-step-content"), "canonical timeline panel is inside the split workspace");
    }
  }
  assert.equal(cards[0].dataset.status, "PASS");
  assert.equal(cards[1].dataset.status, "FAIL");
  assert.equal(cards[0].querySelectorAll(".case-step-button").length, 4);
  assert.equal(cards[0].querySelectorAll(".timeline-entry").filter((entry) => !entry.hidden).length, 1);
  assert.equal(cards[0].querySelector(".case-summary-panel").hidden, true);
  assert.equal(cards[0].querySelectorAll('.case-step-button[aria-current="step"]').length, 1);
  assert.equal(cards[0].querySelectorAll('.case-step-button[aria-pressed="true"]').length, 1);
});

test("each sidebar step explains its own recorded outcome without inheriting the case verdict", () => {
  const { cards } = fixture({
    withSignal: true,
    stepMetadata: {
      observation: { stepState: "recorded", stepOutcome: "Captura registrada", stepDescription: "Página antes de selecionar a ordenação." },
      action: { stepState: "pass", stepOutcome: "Concluída", stepDescription: "A opção solicitada foi selecionada." },
      assertion: { stepState: "fail", stepOutcome: "Não passou", stepDescription: "A ordem dos produtos não corresponde à opção solicitada." },
      signal: { stepState: "warning", stepOutcome: "Requer atenção", stepDescription: "Um sinal técnico foi registrado para análise." },
    },
  });
  const buttons = cards[0].querySelectorAll(".case-step-button");
  assert.deepEqual(buttons.map((button) => button.dataset.stepState), ["pass", "recorded", "pass", "fail", "warning"]);
  assert.deepEqual(buttons.map((button) => button.querySelector(".step-status-label").textContent), [
    "Aprovado", "Captura registrada", "Concluída", "Não passou", "Requer atenção",
  ]);
  assert.equal(buttons[0].querySelector(".step-label").textContent, "Resultado do caso");
  assert.equal(buttons[1].querySelector(".step-kind").textContent, "Captura da página");
  assert.equal(buttons[2].querySelector(".step-kind").textContent, "Ação realizada");
  assert.equal(buttons[3].querySelector(".step-kind").textContent, "Conferência");
  assert.equal(buttons[3].querySelector(".step-description").textContent, "A ordem dos produtos não corresponde à opção solicitada.");
  assert.match(buttons[3].getAttribute("aria-label"), /Conferir resultado.*A ordem dos produtos.*Resultado: Não passou/);
  assert.equal(buttons[3].querySelector(".step-status-icon").getAttribute("aria-hidden"), "true");
  assert.equal(cards[0].dataset.status, "PASS", "displaying a failed step never rewrites the preserved case verdict");
});

test("legacy or invalid step metadata stays unknown even when the case passed", () => {
  for (const metadata of [{}, { stepState: "PASS", stepOutcome: "Passou" }, { stepState: "toString", stepOutcome: "Passou" }]) {
    const { cards } = fixture({ withSignal: true, stepMetadata: { assertion: metadata, action: metadata } });
    const buttons = cards[0].querySelectorAll(".case-step-button");
    for (const button of buttons.slice(1)) {
      assert.equal(button.dataset.stepState, "unknown");
      assert.equal(button.querySelector(".step-status-label").textContent, "Sem resultado");
      assert.doesNotMatch(button.getAttribute("aria-label"), /Aprovado|Passou/);
    }
    assert.equal(buttons[1].querySelector(".step-kind").textContent, "Captura da página", "capture type is descriptive, not an approval");
  }
});

test("recorded captures and informational observations are never presented as passing checks", () => {
  for (const [state, outcome] of [["recorded", "Captura registrada"], ["info", "Página observada"]]) {
    const { cards } = fixture({ stepMetadata: { observation: { stepState: state, stepOutcome: outcome } } });
    const control = cards[0].querySelectorAll(".case-step-button")[1];
    assert.equal(control.dataset.stepState, state);
    assert.equal(control.querySelector(".step-status-label").textContent, outcome);
    assert.doesNotMatch(control.getAttribute("aria-label"), /Passou|Aprovado/);
    assert.match(control.querySelector(".step-description").textContent, /não é uma aprovação/);
  }
});

test("summary labels use only recognized case results", () => {
  for (const [status, state, label] of [
    ["PASS", "pass", "Aprovado"], ["FAIL", "fail", "Reprovado"],
    ["BLOCKED", "warning", "Bloqueado"], ["SKIPPED", "unknown", "Sem validação"],
    [undefined, "unknown", "Sem resultado"], ["passed", "unknown", "Sem resultado"],
    ["toString", "unknown", "Sem resultado"],
  ]) {
    const { cards } = fixture({ caseStatuses: [status, "FAIL"] });
    const summary = cards[0].querySelectorAll(".case-step-button")[0];
    assert.equal(summary.dataset.stepState, state);
    assert.equal(summary.querySelector(".step-status-label").textContent, label);
  }
});

test("step descriptions and outcome labels render literally without executing markup", () => {
  const description = '<img src=x onerror="alert(1)">';
  const outcome = "<b>Concluída</b>";
  const { cards } = fixture({ stepMetadata: { action: { stepState: "pass", stepDescription: description, stepOutcome: outcome } } });
  const control = cards[0].querySelectorAll(".case-step-button")[2];
  assert.equal(control.querySelector(".step-description").textContent, description);
  assert.equal(control.querySelector(".step-status-label").textContent, outcome);
  assert.equal(control.querySelectorAll("img, b").length, 0);
  assert.match(control.outerHTML, /&lt;img src=x onerror=/);
  assert.match(control.outerHTML, /&lt;b&gt;Concluída&lt;\/b&gt;/);
});

test("step selection supports clicks and keyboard navigation, retaining independent CT state", () => {
  const { document, window, cards } = fixture();
  const buttons = cards[0].querySelectorAll(".case-step-button");
  const panels = cards[0].querySelectorAll(".timeline-entry");
  buttons[2].click();
  assert.equal(panels[1].hidden, false);
  assert.equal(panels[0].hidden, true);
  buttons[2].dispatch("keydown", { key: "End" });
  assert.equal(panels[2].hidden, false);
  assert.ok(document.activeElement === buttons[3], "End focuses the final visible step");
  buttons[3].dispatch("keydown", { key: "Home" });
  assert.equal(cards[0].querySelector(".case-summary-panel").hidden, false);
  assert.ok(panels.every((panel) => panel.hidden));
  buttons[0].dispatch("keydown", { key: "ArrowDown" });
  assert.equal(panels[0].hidden, false);
  buttons[1].dispatch("keydown", { key: "ArrowUp" });
  assert.equal(cards[0].querySelector(".case-summary-panel").hidden, false);
  window.location.hash = "#view/results/CT002";
  window.dispatch("hashchange");
  cards[1].querySelectorAll(".case-step-button")[3].click();
  window.location.hash = "#view/results/CT001";
  window.dispatch("hashchange");
  assert.equal(cards[0].hidden, false);
  assert.equal(cards[1].hidden, true);
  assert.equal(cards[0].querySelector(".case-summary-panel").hidden, false);
  assert.equal(cards[1].querySelectorAll(".timeline-entry")[2].hidden, false);
});

test("timeline deep links reveal the corresponding canonical panel", () => {
  const { window, cards, originals } = fixture();
  const target = originals[4];
  window.location.hash = `#${target.id}`;
  window.dispatch("hashchange");
  assert.equal(cards[0].hidden, true);
  assert.equal(cards[1].hidden, false);
  assert.equal(target.hidden, false);
  assert.equal(cards[1].querySelectorAll(".timeline-entry").filter((entry) => !entry.hidden).length, 1);
});

test("a report opened directly at a timeline anchor selects its owning CT and step", () => {
  const { document, cards } = fixture({ hash: "#timeline-case1-2" });
  assert.equal(cards[0].hidden, true);
  assert.equal(cards[1].hidden, false);
  assert.equal(document.getElementById("timeline-case1-2").hidden, false);
  assert.equal(cards[1].querySelector('.case-step-button[aria-current="step"]').dataset.stepIndex, "3");
});

test("step toolbar boundaries and titles follow the selected record", () => {
  const { cards } = fixture();
  const card = cards[0];
  const buttons = card.querySelectorAll(".case-step-button");
  const previous = card.querySelector(".case-step-prev");
  const next = card.querySelector(".case-step-next");
  buttons[0].click();
  assert.equal(previous.disabled, true);
  assert.equal(next.disabled, false);
  next.click();
  assert.equal(card.querySelector(".case-selected-title").textContent, "Antes da ação");
  assert.equal(previous.disabled, false);
  buttons.at(-1).click();
  assert.equal(next.disabled, true);
  assert.equal(card.querySelector(".case-selected-title").textContent, "Conferir resultado");
  previous.click();
  assert.equal(card.querySelector(".case-selected-title").textContent, "Selecionar ordenação");
});

test("hiding technical observations excludes their step controls without changing the verdict", () => {
  const { document, cards } = fixture({ withSignal: true });
  const card = cards[0];
  const buttons = card.querySelectorAll(".case-step-button");
  buttons.at(-1).click();
  assert.equal(card.querySelector(".timeline-entry.signal").hidden, false);
  document.getElementById("show-technical").checked = false;
  document.getElementById("settings-form").dispatch("submit");
  assert.equal(buttons.at(-1).hidden, true);
  assert.equal(card.querySelector(".timeline-entry.signal").hidden, true);
  assert.equal(card.dataset.status, "PASS");
  buttons[0].dispatch("keydown", { key: "End" });
  assert.ok(document.activeElement === buttons[3], "the header summary shares step keyboard navigation without inspecting cyclic DOM on failure");
  document.getElementById("show-technical").checked = true;
  document.getElementById("settings-form").dispatch("submit");
  assert.equal(buttons.at(-1).hidden, false);
  assert.equal(card.dataset.status, "PASS");
});

test("step capture preview preserves its image and download name and restores keyboard focus on close", () => {
  const { document, originalImages } = fixture();
  const original = originalImages[0];
  const trigger = original.closest(".step-preview-button");
  const dialog = document.getElementById("evidence-dialog");
  const close = document.getElementById("close-evidence");
  const preview = document.getElementById("evidence-preview");
  const download = document.getElementById("download-evidence");
  // Click the image itself to exercise the delegated handler, not only a
  // directly-bound button. The original remains the canonical evidence node.
  original.click();
  assert.equal(dialog.hasAttribute("open"), true);
  assert.equal(preview.src, original.src);
  assert.equal(download.href, original.src);
  assert.equal(download.download, "0-original.png");
  assert.equal(document.getElementById("evidence-caption").textContent, "0-original.png");
  assert.equal(document.activeElement, close);
  assert.equal(trigger.querySelector("img"), original);
  close.click();
  assert.equal(dialog.hasAttribute("open"), false);
  assert.equal(document.activeElement, trigger);
  trigger.click();
  close.dispatch("keydown", { key: "Escape" });
  assert.equal(dialog.hasAttribute("open"), false);
  assert.equal(document.activeElement, trigger);
  original.src = "https://example.invalid/untrusted-capture.png";
  trigger.click();
  assert.equal(dialog.hasAttribute("open"), false, "previews must not navigate to external evidence URLs");
});

test("printing reveals every step and restores the previous selection afterwards", () => {
  const { window, cards, originals } = fixture();
  cards[0].querySelectorAll(".case-step-button")[2].click();
  const selectedBeforePrint = originals.map((entry) => entry.hidden);
  const cardsBeforePrint = cards.map((card) => card.hidden);
  window.dispatch("beforeprint");
  assert.ok(originals.every((entry) => !entry.hidden));
  assert.ok(cards.every((card) => !card.hidden));
  window.dispatch("afterprint");
  assert.deepEqual(originals.map((entry) => entry.hidden), selectedBeforePrint);
  assert.deepEqual(cards.map((card) => card.hidden), cardsBeforePrint);
});

test("a CT without temporal evidence still offers its preserved summary", () => {
  const { cards } = fixture({ emptyTimeline: true });
  assert.equal(cards[0].querySelectorAll(".case-step-button").length, 1);
  assert.equal(cards[0].querySelector(".case-summary-panel").hidden, false);
  assert.match(cards[0].querySelector(".case-summary-panel").textContent, /Não há evidência temporal registrada/);
});

test("action previews reuse the owning CT capture and position their target with CSS percentages", () => {
  const { cards, actionFigures, actionImages, originalImages } = fixture({ actionPreview: true });
  cards[0].querySelectorAll(".case-step-button")[2].click();
  const figure = actionFigures[0];
  const canvas = figure.querySelector(".action-preview-canvas");
  const box = canvas.querySelector(".action-target-box");
  assert.equal(actionImages[0].src, originalImages[0].src);
  assert.equal(canvas.querySelector("img"), actionImages[0], "the reference image remains the same DOM node");
  assert.equal(canvas.style.getPropertyValue("--action-image-ratio"), "1.6");
  assert.equal(box.style.getPropertyValue("left"), "75%");
  assert.equal(box.style.getPropertyValue("top"), "10%");
  assert.equal(box.style.getPropertyValue("width"), "15%");
  assert.equal(box.style.getPropertyValue("height"), "5%");
  assert.equal(canvas.querySelector(".action-target-overlay").getAttribute("aria-hidden"), "true");
  assert.equal(canvas.querySelectorAll(".action-target-dot").length, 1);
  assert.equal(canvas.querySelector(".action-target-tag").textContent, "Selecionar aqui");
  assert.equal(figure.querySelector("figcaption").textContent, "0-original.png");
  assert.match(figure.querySelector(".action-preview-note").textContent, /captura original/);
});

test("dropdown markers identify the requested visible option without changing the pre-action screenshot", () => {
  const { actionFigures, actionImages, originalImages, cards } = fixture({
    actionPreview: true, actionPreviewMetadata: { ...actionMetadata, selection: dropdownSelection },
  });
  const canvas = actionFigures[0].querySelector(".action-preview-canvas");
  const tag = canvas.querySelector(".action-target-tag");
  assert.equal(tag.textContent, "Selecionar: Name (Z to A)");
  assert.equal(tag.getAttribute("title"), "Selecionar: Name (Z to A)");
  assert.equal(tag.children[0].className, "action-target-tag-text");
  assert.equal(tag.classList.contains("align-right"), true);
  assert.equal(tag.style.getPropertyValue("--action-tag-offset"), "max(8px, 10%)");
  assert.deepEqual(JSON.parse(canvas.dataset.actionPreview).selection, dropdownSelection);
  assert.equal(actionImages[0].src, originalImages[0].src, "option labels never replace or alter the original PNG");
  assert.equal(cards[0].dataset.status, "PASS", "selection display must not rewrite a recorded test verdict");
});

for (const [name, selection] of [
  ["missing request label", { ...dropdownSelection, requested_label: undefined }],
  ["blank request label", { ...dropdownSelection, requested_label: "  " }],
  ["non-string request label", { ...dropdownSelection, requested_label: ["Name (Z to A)"] }],
  ["oversized request label", { ...dropdownSelection, requested_label: "x".repeat(241) }],
  ["control characters", { ...dropdownSelection, requested_label: "Name\u0000 (Z to A)" }],
  ["line breaks", { ...dropdownSelection, requested_label: "Name\n(Z to A)" }],
  ["DEL character", { ...dropdownSelection, requested_label: "Name\u007f (Z to A)" }],
  ["C1 control character", { ...dropdownSelection, requested_label: "Name\u0085 (Z to A)" }],
  ["unknown version", { ...dropdownSelection, version: "guessed-label-v1" }],
  ["unknown status", { ...dropdownSelection, status: "passed" }],
]) {
  test(`dropdown markers keep a generic control label for ${name}`, () => {
    const { actionFigures } = fixture({
      actionPreview: true, actionPreviewMetadata: { ...actionMetadata, selection },
    });
    assert.equal(actionFigures[0].querySelector(".action-target-tag").textContent, "Selecionar aqui");
  });
}

test("dropdown option labels are literal text and raw values or other metadata never enter runtime marker data", () => {
  const label = '<img src=x onerror="alert(1)">';
  const { actionFigures } = fixture({
    actionPreview: true, actionPreviewMetadata: {
      ...actionMetadata,
      selection: { ...dropdownSelection, requested_label: "  " + label + "  ", value: "private-option-value", api_key: "private-key" },
    },
  });
  const canvas = actionFigures[0].querySelector(".action-preview-canvas");
  const tag = canvas.querySelector(".action-target-tag");
  assert.equal(tag.textContent, "Selecionar: " + label);
  assert.equal(tag.querySelectorAll("img").length, 0);
  assert.match(tag.outerHTML, /&lt;img src=x onerror=/);
  assert.doesNotMatch(canvas.dataset.actionPreview, /private-option-value|private-key|api_key/);
});

test("option metadata cannot change labels for click, fill, press or hover targets", () => {
  for (const [action, expected] of [["click", "Clique aqui"], ["fill", "Preencher aqui"], ["press", "Tecla neste controle"], ["hover", "Passar o cursor aqui"]]) {
    const { actionFigures } = fixture({
      actionPreview: true, actionPreviewMetadata: { ...actionMetadata, action, selection: dropdownSelection },
    });
    const canvas = actionFigures[0].querySelector(".action-preview-canvas");
    assert.equal(canvas.querySelector(".action-target-tag").textContent, expected);
    assert.equal(JSON.parse(canvas.dataset.actionPreview).selection, undefined);
  }
});

test("long dropdown labels wrap within their anchored capture edge, with a full text title and accessible report summary styling", () => {
  const label = "Opção detalhada ".repeat(15).trim();
  for (const [x, y, right, above] of [[0, 0, false, false], [1224, 855, true, true]]) {
    const { actionFigures } = fixture({
      actionPreview: true, actionPreviewMetadata: {
        ...actionMetadata, box: { x, y, width: 216, height: 45 },
        selection: { ...dropdownSelection, requested_label: label },
      },
    });
    const tag = actionFigures[0].querySelector(".action-target-tag");
    assert.equal(tag.textContent, "Selecionar: " + label);
    assert.equal(tag.getAttribute("title"), "Selecionar: " + label);
    assert.equal(tag.classList.contains("align-right"), right);
    assert.equal(tag.classList.contains("above"), above);
    assert.equal(tag.style.getPropertyValue(right ? "right" : "left"), "max(8px, 0%)");
  }
  assert.match(reportCss, /\.action-target-tag\s*\{[^}]*max-width:\s*min\(320px, calc\(100% - var\(--action-tag-offset, 8px\) - 8px\)\)/);
  assert.match(reportCss, /\.action-target-tag\s*\{[^}]*white-space:\s*normal;[^}]*overflow-wrap:\s*anywhere/);
  assert.match(reportCss, /\.action-target-tag-text\s*\{[^}]*-webkit-line-clamp:\s*3/);
  assert.match(reportCss, /\.action-selection-item > strong\s*\{[^}]*overflow-wrap:\s*anywhere/);
  assert.match(reportCss, /\.action-selection-summary\[data-selection-status="mismatch"\] \.action-selection-status/);
});

for (const [name, metadata, imageSize] of [
  ["absent metadata", undefined],
  ["null metadata", null],
  ["empty metadata", {}],
  ["unknown coordinate space", { ...actionMetadata, coordinate_space: "viewport-guessed" }],
  ["string NaN coordinate", { ...actionMetadata, box: { ...actionMetadata.box, x: "NaN" } }],
  ["serialized NaN coordinate", { ...actionMetadata, box: { ...actionMetadata.box, y: Number.NaN } }],
  ["negative coordinate", { ...actionMetadata, box: { ...actionMetadata.box, x: -1 } }],
  ["zero width", { ...actionMetadata, box: { ...actionMetadata.box, width: 0 } }],
  ["out-of-bounds region", { ...actionMetadata, box: { ...actionMetadata.box, x: 1400 } }],
  ["incompatible screenshot dimensions", actionMetadata, { width: 1024, height: 768 }],
  ["image not loaded yet", actionMetadata, { width: 0, height: 0 }],
]) {
  test(`action previews retain the screenshot without inventing a marker for ${name}`, () => {
    const { actionFigures, actionImages, originalImages } = fixture({
      actionPreview: true, actionPreviewMetadata: metadata,
      ...(imageSize ? { naturalImageSize: imageSize } : {}),
    });
    assert.equal(actionFigures[0].querySelectorAll(".action-target-overlay").length, 0);
    assert.equal(actionImages[0].src, originalImages[0].src, "invalid geometry must not remove the real capture");
    assert.ok(actionImages[0].closest(".step-preview-button"));
  });
}

test("an action marker is validated after image loading and remains percentage-aligned on resize", () => {
  const { window, actionFigures, actionImages } = fixture({ actionPreview: true, naturalImageSize: { width: 0, height: 0 } });
  const image = actionImages[0];
  const canvas = actionFigures[0].querySelector(".action-preview-canvas");
  assert.equal(canvas.querySelector(".action-target-overlay"), null);
  image.naturalWidth = 1440;
  image.naturalHeight = 900;
  image.dispatch("load");
  const marker = canvas.querySelector(".action-target-box");
  const coordinates = ["left", "top", "width", "height"].map((name) => marker.style.getPropertyValue(name));
  window.dispatch("resize");
  assert.deepEqual(["left", "top", "width", "height"].map((name) => marker.style.getPropertyValue(name)), coordinates);
  assert.deepEqual(coordinates, ["75%", "10%", "15%", "5%"]);
  assert.match(reportCss, /\.action-preview-canvas\s*\{[^}]*position:\s*relative/);
  assert.match(reportCss, /\.action-target-overlay\s*\{[^}]*position:\s*absolute;[^}]*inset:\s*0/);
  assert.match(reportCss, /\.action-preview-canvas img\s*\{[^}]*width:\s*100%;[^}]*height:\s*auto/);
  image.dispatch("load");
  assert.equal(canvas.querySelectorAll(".action-target-overlay").length, 1, "repeated image loads must replace rather than duplicate overlays");
  image.naturalWidth = 800;
  image.dispatch("load");
  assert.equal(canvas.querySelector(".action-target-overlay"), null, "a different-sized image must invalidate the recorded geometry");
});

test("action overlays follow screenshot visibility preferences and preserve the CT verdict", () => {
  const { document, cards, actionImages } = fixture({ actionPreview: true });
  cards[0].querySelectorAll(".case-step-button")[2].click();
  assert.ok(actionImages[0].closest(".shots"), "annotated captures use the same screenshot visibility scope");
  document.getElementById("show-screenshots").checked = false;
  document.getElementById("settings-form").dispatch("submit");
  assert.equal(document.body.classList.contains("hide-screenshots"), true);
  assert.match(reportCss, /\.hide-screenshots \.shots[^}]*display:\s*none/);
  assert.equal(cards[0].dataset.status, "PASS");
  document.getElementById("show-screenshots").checked = true;
  document.getElementById("settings-form").dispatch("submit");
  assert.equal(document.body.classList.contains("hide-screenshots"), false);
  assert.equal(actionImages[0].closest("figure").querySelectorAll(".action-target-overlay").length, 1);
});

test("the enlarged action capture keeps its marker but ordinary captures clear that overlay", () => {
  const { document, actionImages, originalImages } = fixture({ actionPreview: true });
  const previewImage = document.getElementById("evidence-preview");
  const download = document.getElementById("download-evidence");
  actionImages[0].click();
  const modalCanvas = previewImage.closest(".action-modal-canvas");
  assert.equal(modalCanvas.querySelectorAll(".action-target-overlay").length, 1);
  assert.equal(modalCanvas.querySelector(".action-target-box").style.getPropertyValue("left"), "75%");
  assert.equal(previewImage.src, originalImages[0].src);
  assert.equal(download.href, originalImages[0].src, "image downloads remain the original unannotated evidence bytes");
  assert.equal(download.download, "0-original.png");
  document.getElementById("close-evidence").click();
  originalImages[0].click();
  assert.equal(previewImage.closest(".action-modal-canvas"), modalCanvas);
  assert.equal(modalCanvas.querySelector(".action-target-overlay"), null, "ordinary capture previews must not inherit a previous action's target");
  assert.equal(previewImage.src, originalImages[0].src);
  assert.equal(download.download, "0-original.png");
});

test("enlarged dropdown previews keep the requested label even when the observed option differs", () => {
  const selection = { ...dropdownSelection, after_label: "Name (A to Z)", status: "mismatch" };
  const { document, actionImages } = fixture({
    actionPreview: true, actionPreviewMetadata: { ...actionMetadata, selection },
  });
  actionImages[0].click();
  const canvas = document.getElementById("evidence-preview").closest(".action-modal-canvas");
  assert.equal(canvas.querySelector(".action-target-tag").textContent, "Selecionar: Name (Z to A)", "a pre-action annotation describes intent, not an unperformed selection");
  assert.deepEqual(JSON.parse(canvas.dataset.actionPreview).selection, selection);
});

test("printing and offline exports preserve dropdown labels without embedding rendered markers or additional evidence bytes", async () => {
  const { document, window, actionFigures, blobs } = fixture({
    actionPreview: true, hash: "#view/overview", actionPreviewMetadata: { ...actionMetadata, selection: dropdownSelection },
  });
  window.dispatch("beforeprint");
  assert.ok(actionFigures.every((figure) => figure.querySelector(".action-target-tag").textContent === "Selecionar: Name (Z to A)"));
  window.dispatch("afterprint");
  document.getElementById("download-report").click();
  const html = await blobs[0].text();
  assert.match(html, /&quot;selection&quot;:\{&quot;version&quot;:&quot;select-labels-v1&quot;/);
  assert.match(html, /&quot;requested_label&quot;:&quot;Name \(Z to A\)&quot;/);
  assert.match(html, /&quot;after_label&quot;:&quot;Name \(Z to A\)&quot;/);
  assert.doesNotMatch(html, /class="[^"]*action-target-overlay|class="[^"]*action-preview-canvas/);
  assert.equal((html.match(/src="data:image\/png;base64,cHJlc2VydmVk"/g) || []).length, 2);
  const offline = fixture({ exported: true, actionPreview: true, actionPreviewMetadata: { ...actionMetadata, selection: dropdownSelection } });
  assert.equal(offline.actionFigures[0].querySelector(".action-target-tag").textContent, "Selecionar: Name (Z to A)");
});

test("HTML export stores action metadata and a capture reference without duplicate bytes or transient markers", async () => {
  const { document, cards, actionImages, blobs } = fixture({ actionPreview: true });
  cards[0].querySelectorAll(".case-step-button")[2].click();
  actionImages[0].click();
  assert.ok(document.querySelector(".action-target-overlay"));
  document.getElementById("download-report").click();
  const html = await blobs[0].text();
  assert.equal((html.match(/src="data:image\/png;base64,cHJlc2VydmVk"/g) || []).length, 2, "each CT's original capture is embedded only once");
  assert.equal((html.match(/<figure class="action-preview"/g) || []).length, 2);
  assert.equal((html.match(/<img class="" data-source-shot="[01]-original\.png"/g) || []).length, 2);
  assert.match(html, /data-action-preview="\{&quot;version&quot;:&quot;action-target-v1&quot;/);
  assert.doesNotMatch(html, /class="[^"]*action-target-overlay|class="[^"]*action-modal-canvas|class="[^"]*action-preview-canvas/);
  assert.doesNotMatch(html, /id="download-report"|<dialog[^>]*\bopen=""/);
  assert.equal(document.getElementById("evidence-dialog").hasAttribute("open"), true, "downloading must not mutate the live preview");
});

test("printing from overview prepares markers in unvisited CTs and later navigation does not duplicate them", () => {
  const { document, window, cards, originals, originalImages, actionFigures, actionImages, toolbar, topbar } = fixture({
    actionPreview: true, hash: "#view/overview",
  });
  const views = document.querySelectorAll(".report-view[data-view]");
  const viewState = views.map((view) => view.hidden);
  const cardState = cards.map((card) => ({ hidden: card.hidden, open: card.open }));
  const entryState = originals.map((entry) => entry.hidden);
  const initialHash = window.location.hash;
  assert.equal(document.body.dataset.view, "overview");
  assert.equal(document.querySelector(".case-workspace"), null, "no CT explorer has been opened yet");
  assert.ok(actionFigures.every((figure) => !figure.querySelector(".action-target-overlay")));
  assert.ok(actionImages.every((image) => image.src === ""), "action capture references start unhydrated");

  window.dispatch("beforeprint");
  window.dispatch("beforeprint"); // Browser integrations can emit this twice.
  assert.equal(document.querySelector(".case-workspace"), null, "printing must not construct or select a CT explorer");
  for (let index = 0; index < cards.length; index += 1) {
    const figure = actionFigures[index];
    assert.equal(figure.querySelectorAll(".step-preview-button").length, 1);
    assert.equal(figure.querySelectorAll(".action-preview-canvas").length, 1);
    assert.equal(figure.querySelectorAll(".action-target-overlay").length, 1);
    assert.equal(figure.querySelector(".action-target-box").style.getPropertyValue("left"), "75%");
    assert.equal(figure.querySelector("img"), actionImages[index]);
    assert.equal(actionImages[index].loading, "eager", "unvisited action captures must load for print even below the viewport");
    assert.equal(actionImages[index].src, originalImages[index].src);
    assert.equal(cards[index].open, true);
  }
  window.dispatch("afterprint");
  assert.deepEqual(views.map((view) => view.hidden), viewState);
  assert.deepEqual(cards.map((card) => ({ hidden: card.hidden, open: card.open })), cardState);
  assert.deepEqual(originals.map((entry) => entry.hidden), entryState);
  assert.equal(document.body.dataset.view, "overview");
  assert.equal(window.location.hash, initialHash);
  assert.equal(toolbar.parentElement, topbar);

  document.querySelector('[data-report-nav="results"]').click();
  document.getElementById("case-next").click();
  assert.ok(cards.every((card) => card.querySelector(".case-workspace")));
  for (let index = 0; index < actionFigures.length; index += 1) {
    const figure = actionFigures[index];
    assert.equal(figure.querySelectorAll(".step-preview-button").length, 1, "the lazily built CT must reuse its print-prepared button");
    assert.equal(figure.querySelectorAll(".action-preview-canvas").length, 1);
    assert.equal(figure.querySelectorAll(".action-target-overlay").length, 1);
    assert.equal(figure.querySelector("img"), actionImages[index]);
  }
});
