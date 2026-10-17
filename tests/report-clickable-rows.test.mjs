import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

// Run the complete offline report runtime against the smallest table fixture.
// Navigation is observed through the same view/hash changes as the real report.
const runtime = await readFile(new URL("../backend/autojev/adapters/report_assets/premium.js", import.meta.url), "utf8");

class TableElement {
  constructor(tagName, attributes = {}, parent = null) {
    this.tagName = tagName.toUpperCase();
    this.parentElement = parent;
    this.children = [];
    this.attributes = new Map(Object.entries(attributes));
    this.dataset = new Proxy({}, { set: (target, name, value) => {
      target[name] = String(value);
      this.attributes.set(`data-${name.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`, String(value));
      return true;
    } });
    Object.entries(attributes).forEach(([name, value]) => {
      if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
    });
    this.textContent = "";
    this.classList = { contains: (name) => (this.attributes.get("class") || "").split(/\s+/).includes(name) };
    if (parent) parent.children.push(this);
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  hasAttribute(name) { return this.attributes.has(name); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  matches(selector) {
    const parts = selector.trim().split(/\s+(?![^[]*\])/);
    if (parts.length > 1) {
      if (!this.matches(parts.pop())) return false;
      const ancestorSelector = parts.join(" ");
      for (let parent = this.parentElement; parent; parent = parent.parentElement) if (parent.matches(ancestorSelector)) return true;
      return false;
    }
    const tag = selector.match(/^[a-z][\w-]*/i)?.[0];
    if (tag && this.tagName !== tag.toUpperCase()) return false;
    const id = selector.match(/#([\w-]+)/)?.[1];
    if (id && this.getAttribute("id") !== id) return false;
    for (const [, name] of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(name)) return false;
    for (const [, name, operator, expected] of selector.matchAll(/\[([^\]=~^$*\s]+)(?:([~^$*]?=)["']?([^\]"']*)["']?)?\]/g)) {
      const actual = this.getAttribute(name);
      if (actual == null || (operator === "=" && actual !== expected) || (operator === "^=" && !actual.startsWith(expected))) return false;
    }
    return true;
  }
  closest(selector) {
    const branches = selector.split(/,\s*/);
    if (branches.some((branch) => this.matches(branch))) return this;
    for (let item = this.parentElement; item; item = item.parentElement) if (branches.some((branch) => item.matches(branch))) return item;
    return null;
  }
  querySelectorAll(selector) {
    const found = [];
    const visit = (parent) => parent.children.forEach((child) => {
      if (selector.split(/,\s*/).some((branch) => child.matches(branch))) found.push(child);
      visit(child);
    });
    visit(this);
    return found;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function fixture({ selection, withoutSelectionApi = false, legacyRows = false, includeUnknownRow = false } = {}) {
  const document = new TableElement("document");
  const events = new Map();
  document.readyState = "complete";
  document.body = new TableElement("body", { class: "report-premium" }, document);
  document.getElementById = (id) => document.querySelector(`#${id}`);
  document.addEventListener = (type, handler) => {
    if (!events.has(type)) events.set(type, []);
    events.get(type).push(handler);
  };
  const views = [];
  document.body.dataset = new Proxy({}, { set(target, name, value) {
    if (name === "view") views.push(value);
    target[name] = value;
    return true;
  } });
  const source = new TableElement("script", { id: "report-data" }, document.body);
  source.textContent = JSON.stringify({ cases: [
    { index: 0, id: "CT001", title: "Ordenação", status: "PASS", category: "business" },
    { index: 1, id: "CT002", title: "Carrinho", status: "FAIL", category: "business" },
  ] });
  const rows = new TableElement("tbody", { id: "case-rows" }, document.body);
  const cases = [0, 1].map((index) => {
    const row = new TableElement("tr", {
      "data-case-index": String(index),
      ...(!legacyRows ? { "data-open-case-row": String(index), tabindex: "0" } : {}),
    }, rows);
    const cells = Array.from({ length: 8 }, () => new TableElement("td", {}, row));
    const id = new TableElement("span", {}, cells[0]);
    const title = new TableElement("button", { "data-open-case": String(index) }, cells[1]);
    const titleText = new TableElement("span", {}, title);
    const status = new TableElement("span", {}, cells[2]);
    const detail = new TableElement("button", { "data-open-case": String(index) }, cells[7]);
    return { row, cells, id, title, titleText, status, detail };
  });
  const unknownRow = includeUnknownRow ? new TableElement("tr", { "data-case-index": "999" }, rows) : null;
  const unknownCell = unknownRow ? new TableElement("td", {}, unknownRow) : null;
  const history = [];
  const window = {
    location: { hash: "#view/tests", protocol: "file:", origin: "null" },
    addEventListener() {},
    history: { replaceState(_state, _title, hash) { history.push(hash); window.location.hash = hash; } },
  };
  if (!withoutSelectionApi) window.getSelection = () => selection ?? { isCollapsed: true, toString: () => "" };
  vm.runInNewContext(runtime, { document, window, Element: TableElement, Map, Set, URL, Blob, console }, { filename: "premium.js" });
  views.length = 0;
  return { document, window, cases, unknownRow, unknownCell, views, history, dispatch(type, target, properties = {}) {
    const event = { target, button: 0, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...properties };
    (events.get(type) || []).forEach((handler) => handler(event));
    return event;
  } };
}

test("every table cell and unused row area opens that row's own test", () => {
  for (const index of [0, 1]) {
    for (const targetName of ["row", "id", "status", ...Array.from({ length: 8 }, (_, cell) => `cell${cell}`)]) {
      const table = fixture();
      const item = table.cases[index];
      const target = targetName.startsWith("cell") ? item.cells[Number(targetName.slice(4))] : item[targetName];
      const click = table.dispatch("click", target);
      assert.equal(click.defaultPrevented, true, `${targetName} is a complete click target`);
      assert.equal(table.window.location.hash, `#view/results/CT00${index + 1}`);
      assert.deepEqual(table.views, ["results"], "one click opens the result exactly once");
      assert.equal(table.history.length, 1);
    }
  }
});

test("legacy rows from a running backend gain complete click targets without new Python markup", () => {
  for (const index of [0, 1]) {
    for (const targetName of ["row", "id", "status", ...Array.from({ length: 8 }, (_, cell) => `cell${cell}`)]) {
      const table = fixture({ legacyRows: true });
      const item = table.cases[index];
      assert.equal(item.row.getAttribute("data-open-case-row"), String(index), "runtime upgrades existing report rows");
      assert.equal(item.row.getAttribute("tabindex"), "0", "legacy rows become keyboard focusable");
      assert.equal(item.row.getAttribute("aria-label"), `Abrir resultado de CT00${index + 1}: ${index ? "Carrinho" : "Ordenação"}`);
      const target = targetName.startsWith("cell") ? item.cells[Number(targetName.slice(4))] : item[targetName];
      const click = table.dispatch("click", target);
      assert.equal(click.defaultPrevented, true, `${targetName} in old markup is clickable`);
      assert.equal(table.window.location.hash, `#view/results/CT00${index + 1}`);
      assert.deepEqual(table.views, ["results"], "legacy markup opens its result exactly once");
    }
    for (const key of ["Enter", " "]) {
      const table = fixture({ legacyRows: true });
      const event = table.dispatch("keydown", table.cases[index].row, { key });
      assert.equal(event.defaultPrevented, true);
      assert.equal(table.window.location.hash, `#view/results/CT00${index + 1}`);
      assert.deepEqual(table.views, ["results"]);
    }
  }
});

test("runtime row upgrade ignores a table index absent from the report data", () => {
  const table = fixture({ legacyRows: true, includeUnknownRow: true });
  assert.equal(table.unknownRow.hasAttribute("data-open-case-row"), false);
  assert.equal(table.unknownRow.hasAttribute("tabindex"), false);
  assert.equal(table.unknownRow.hasAttribute("aria-label"), false);
  table.dispatch("click", table.unknownCell);
  table.dispatch("keydown", table.unknownRow, { key: "Enter" });
  assert.equal(table.window.location.hash, "#view/tests");
  assert.deepEqual(table.views, []);
});

test("existing title and details controls still open once, including nested text", () => {
  for (const name of ["title", "titleText", "detail"]) {
    const table = fixture();
    table.dispatch("click", table.cases[1][name]);
    assert.equal(table.window.location.hash, "#view/results/CT002");
    assert.deepEqual(table.views, ["results"]);
  }
});

test("rows support direct Enter and Space without hijacking descendants or repeat keys", () => {
  for (const key of ["Enter", " "]) {
    const table = fixture();
    const keyboard = table.dispatch("keydown", table.cases[1].row, { key });
    assert.equal(keyboard.defaultPrevented, true, "Space must not scroll the page");
    assert.equal(table.window.location.hash, "#view/results/CT002");
    assert.deepEqual(table.views, ["results"]);
  }
  for (const [name, properties] of [["title", { key: "Enter" }], ["cells", { key: " " }], ["row", { key: "Tab" }],
    ["row", { key: "Enter", repeat: true }], ["row", { key: "Enter", ctrlKey: true }],
    ["row", { key: "Enter", defaultPrevented: true }]]) {
    const table = fixture();
    table.dispatch("keydown", name === "cells" ? table.cases[0].cells[0] : table.cases[0][name], properties);
    assert.deepEqual(table.views, []);
  }
});

test("native nested anchors, inputs and other controls keep their normal behavior", () => {
  for (const [tag, attributes] of [["a", { href: "https://example.test/" }], ["button", {}], ["input", {}],
    ["select", {}], ["textarea", {}], ["summary", {}], ["span", { role: "button" }],
    ["span", { contenteditable: "true" }]]) {
    const table = fixture();
    const control = new TableElement(tag, attributes, table.cases[0].cells[4]);
    const target = new TableElement("span", {}, control);
    const event = table.dispatch("click", target);
    assert.equal(event.defaultPrevented, false, `${tag} keeps its native/default action`);
    assert.deepEqual(table.views, []);
    assert.equal(table.window.location.hash, "#view/tests");
  }
});

test("copying text, modified clicks and cancelled clicks do not unexpectedly open a case", () => {
  for (const properties of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true },
    { button: 1 }, { button: 2 }, { defaultPrevented: true }]) {
    const table = fixture();
    table.dispatch("click", table.cases[0].cells[3], properties);
    assert.deepEqual(table.views, []);
  }
  const selected = fixture({ selection: { isCollapsed: false, toString: () => "Selecionar ordenação" } });
  selected.dispatch("click", selected.cases[0].cells[1]);
  assert.deepEqual(selected.views, []);
  const olderBrowser = fixture({ withoutSelectionApi: true });
  olderBrowser.dispatch("click", olderBrowser.cases[0].cells[1]);
  assert.deepEqual(olderBrowser.views, ["results"]);
});
