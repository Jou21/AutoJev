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
    for (let current = this; current; current = current.parentElement) if (current.matches(selector)) return current;
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
  } = options;
  const storedActionMetadata = Object.hasOwn(options, "actionPreviewMetadata")
    ? options.actionPreviewMetadata : actionMetadata;
  const document = new FixtureElement("document");
  document.nodeType = 9;
  document.ownerDocument = document;
  document.createElement = (tagName) => new FixtureElement(tagName, document);
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
  source.textContent = JSON.stringify({ cases: [
    { index: 0, id: "CT001", title: "Ordenação de produtos", status: "PASS", category: "business", page: "/inventory.html" },
    { index: 1, id: "CT002", title: "Verificar carrinho", status: "FAIL", category: "business", page: "/cart.html" },
  ] });
  const navigation = make("nav", "", document.body);
  for (const view of ["overview", "tests", "results"]) {
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
  make("button", "", caseNavigation, "Teste anterior").id = "case-prev";
  make("button", "", caseNavigation, "Teste seguinte").id = "case-next";
  make("div", "", resultActions).id = "result-report-actions";
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
    card.dataset.status = caseIndex ? "FAIL" : "PASS";
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
  const theme = make("style", "", document.body, ".report-premium { color: white; }");
  theme.id = "report-theme";
  const runtime = make("script", "", document.body, script);
  runtime.id = "report-runtime";
  const window = new FixtureElement("window", document);
  window.location = { hash };
  window.history = { replaceState(_state, _unused, value) { window.location.hash = value; } };
  window.matchMedia = () => ({ matches: true });
  window.requestAnimationFrame = (callback) => callback();
  window.scrollTo = () => {};
  window.innerHeight = 900;
  window.setTimeout = () => {};
  window.printCalls = 0;
  window.print = () => { window.printCalls += 1; };
  const blobs = [];
  const fixtureURL = {
    createObjectURL(blob) { blobs.push(blob); return "blob:fixture/" + blobs.length; },
    revokeObjectURL() {},
  };
  vm.runInNewContext(script, { document, window, Element: FixtureElement, Blob, URL: fixtureURL, Map, Set, console }, { filename: "premium.js" });
  return { document, window, cards, originals, originalImages, actionFigures, actionImages, toolbar, topbar, blobs, downloads: document.downloads };
}

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

test("step selection supports clicks and keyboard navigation, retaining independent CT state", () => {
  const { document, window, cards } = fixture();
  const buttons = cards[0].querySelectorAll(".case-step-button");
  const panels = cards[0].querySelectorAll(".timeline-entry");
  buttons[2].click();
  assert.equal(panels[1].hidden, false);
  assert.equal(panels[0].hidden, true);
  buttons[2].dispatch("keydown", { key: "End" });
  assert.equal(panels[2].hidden, false);
  assert.equal(document.activeElement, buttons[3]);
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
  assert.equal(document.activeElement, buttons[3]);
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
