import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";
import { transpileModule, ModuleKind, JsxEmit, ScriptTarget } from "typescript";

const require = createRequire(import.meta.url);
const compile = async name => transpileModule(await readFile(new URL(`../app/components/${name}`, import.meta.url), "utf8"), {
  fileName: name, compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022, jsx: JsxEmit.ReactJSX },
}).outputText;
const helper = { exports: {} };
new Function("module", "exports", await compile("suite-step-evidence.ts"))(helper, helper.exports);
const { stepImageUrl } = helper.exports;
const preview = await compile("StepEvidencePreview.tsx");
const text = node => node == null || typeof node === "boolean" ? "" : Array.isArray(node) ? node.map(text).join("") : typeof node === "object" ? text(node.props?.children) : String(node);
function* elements(node) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) { for (const entry of node) yield* elements(entry); return; }
  if (!node.props) return;
  yield node; yield* elements(node.props.children);
}
function imageView(src = "http://api.fixture.invalid/api/explorations/owner/screenshots/before.png") {
  let cursor = 0, tree, closed = 0;
  const slots = [], module = { exports: {} };
  new Function("require", "module", "exports", preview)(name => name === "react" ? { useState(initial) {
    const index = cursor++;
    if (!(index in slots)) slots[index] = initial;
    return [slots[index], next => { slots[index] = typeof next === "function" ? next(slots[index]) : next; }];
  }, useRef(initial) {
    const index = cursor++;
    if (!(index in slots)) slots[index] = { current: initial };
    return slots[index];
  } } : require(name), module, module.exports);
  const render = () => { cursor = 0; tree = module.exports.default({ title: "Antes da ação", index: 0, total: 3, src, onClose() { closed++; } }); };
  const all = () => [...elements(tree)];
  const button = label => { const found = all().find(node => node.type === "button" && (node.props["aria-label"] === label || text(node) === label)); assert.ok(found, label); return found; };
  const stage = () => all().find(node => node.props.className === "suite-image-stage");
  render();
  return { all, button, stage, pointer(name, event) { stage().props[name](event); render(); }, text: () => text(tree), get closed() { return closed; }, click(label) { const node = button(label); assert.ok(!node.props.disabled); node.props.onClick(); render(); }, imageEvent(name) { const img = all().find(node => node.type === "img"); assert.ok(img); img.props[name](); render(); } };
}

function panSurface({ width = 1600, height = 1200 } = {}) {
  let left = 200, top = 150, prevented = 0;
  const captures = new Set();
  const surface = {
    scrollWidth: width, clientWidth: 800, scrollHeight: height, clientHeight: 400,
    get scrollLeft() { return left; }, set scrollLeft(value) { left = Math.max(0, Math.min(width - 800, value)); },
    get scrollTop() { return top; }, set scrollTop(value) { top = Math.max(0, Math.min(height - 400, value)); },
    setPointerCapture(id) { captures.add(id); }, hasPointerCapture(id) { return captures.has(id); }, releasePointerCapture(id) { captures.delete(id); },
    focus(options) { assert.deepEqual(options, { preventScroll: true }); this.focused = true; },
  };
  return { surface, captures, get prevented() { return prevented; }, event(overrides = {}) {
    return { pointerId: 1, pointerType: "mouse", isPrimary: true, button: 0, buttons: 1, clientX: 400, clientY: 300,
      currentTarget: surface, target: {}, preventDefault() { prevented++; }, ...overrides };
  } };
}

test("image URLs are execution-bound, local to the configured API and never guessed", () => {
  const step = { title: "Antes", kind: "observation", screenshot: "175 before.png", evidence_exploration_id: "owner-run" };
  assert.equal(stepImageUrl("https://api.test/api/", step), "https://api.test/api/explorations/owner-run/screenshots/175%20before.png");
  for (const screenshot of ["../secret.png", "shots/before.png", "shots\\before.png", "before.png?q=1", "before.png#x", "before\u0000.png", "before.svg", "data:image/png;base64,abc", "https://other.test/a.png", ""]) {
    assert.equal(stepImageUrl("https://api.test/api", { ...step, screenshot }), null, screenshot);
  }
  for (const owner of ["..", "a/b", "a?b", "", "https://other.test"]) assert.equal(stepImageUrl("https://api.test/api", { ...step, evidence_exploration_id: owner }), null);
  for (const base of [undefined, "", "file:///tmp", "javascript:alert(1)", "//api.test", "https://user:pass@api.test", "https://api.test/api?token=1", "https://api.test/api#x"]) assert.equal(stepImageUrl(base, step), null);
  assert.equal(stepImageUrl("https://api.test/api", { title: "Conferir", kind: "assertion" }), null);
});

test("the image preview handles loading, bounded zoom, fit, original link and close without writes", () => {
  const view = imageView();
  assert.match(view.text(), /Carregando imagem/);
  assert.match(view.text(), /Etapa 1 de 3 · imagem da execução de referência/);
  const original = view.all().find(node => node.type === "a");
  assert.equal(original.props.target, "_blank");
  assert.equal(original.props.rel, "noopener noreferrer");
  view.imageEvent("onLoad");
  assert.doesNotMatch(view.text(), /Carregando imagem/);
  for (let i = 0; i < 4; i++) view.click("Aumentar imagem");
  assert.equal(view.button("Aumentar imagem").props.disabled, true);
  assert.equal(view.all().find(node => node.props.className === "suite-image-scale").props.style.width, "200%");
  view.click("Ajustar imagem ao espaço disponível");
  assert.match(view.text(), /100%/);
  view.click("Diminuir imagem"); view.click("Diminuir imagem");
  assert.equal(view.button("Diminuir imagem").props.disabled, true);
  view.click("Fechar imagem da etapa"); assert.equal(view.closed, 1);
});

test("missing and failed captures show an honest placeholder and retries never keep a broken image", () => {
  const missing = imageView(null);
  assert.match(missing.text(), /não tem imagem registrada/);
  assert.equal(missing.all().some(node => node.type === "img" || node.type === "a"), false);
  const failed = imageView();
  failed.imageEvent("onError");
  assert.match(failed.text(), /Não foi possível carregar esta imagem/);
  assert.equal(failed.all().some(node => node.type === "img"), false);
  failed.click("Tentar novamente");
  assert.match(failed.all().find(node => node.type === "img").props.src, /\?preview_retry=1$/);
  assert.match(failed.text(), /Carregando imagem/);
});

test("grabbing a loaded image pans both axes, captures the pointer and respects native scroll bounds", () => {
  const view = imageView(), fixture = panSurface();
  view.imageEvent("onLoad");
  assert.equal(view.stage().props.tabIndex, 0);
  assert.equal(view.stage().props["aria-describedby"], "suite-image-pan-help");
  assert.equal(view.all().find(node => node.type === "img").props.draggable, false);
  view.pointer("onPointerDown", fixture.event());
  assert.equal(view.stage().props["data-panning"], true);
  assert.ok(fixture.captures.has(1)); assert.equal(fixture.surface.focused, true);
  view.pointer("onPointerMove", fixture.event({ clientX: 280, clientY: 220 }));
  assert.equal(fixture.surface.scrollLeft, 320); assert.equal(fixture.surface.scrollTop, 230);
  view.pointer("onPointerMove", fixture.event({ clientX: -2000, clientY: -2000 }));
  assert.equal(fixture.surface.scrollLeft, 800); assert.equal(fixture.surface.scrollTop, 800);
  view.pointer("onPointerMove", fixture.event({ clientX: 2000, clientY: 2000 }));
  assert.equal(fixture.surface.scrollLeft, 0); assert.equal(fixture.surface.scrollTop, 0);
  view.pointer("onPointerUp", fixture.event({ buttons: 0 }));
  assert.equal(view.stage().props["data-panning"], false); assert.equal(fixture.captures.size, 0);
  const prevented = fixture.prevented;
  view.pointer("onPointerMove", fixture.event({ clientX: 100 }));
  assert.equal(fixture.prevented, prevented); assert.equal(fixture.surface.scrollLeft, 0);
  view.click("Aumentar imagem"); assert.match(view.text(), /125%/);
});

test("panning ends on cancellation, lost capture or released buttons and ignores other pointers", () => {
  for (const end of ["onPointerCancel", "onLostPointerCapture", "onPointerMove"]) {
    const view = imageView(), fixture = panSurface(); view.imageEvent("onLoad");
    view.pointer("onPointerDown", fixture.event({ pointerType: "pen" }));
    view.pointer("onPointerMove", fixture.event({ pointerId: 2, clientX: 0, clientY: 0 }));
    view.pointer("onPointerUp", fixture.event({ pointerId: 2 }));
    assert.equal(fixture.surface.scrollLeft, 200); assert.equal(view.stage().props["data-panning"], true);
    view.pointer(end, fixture.event({ buttons: 0 }));
    assert.equal(view.stage().props["data-panning"], false); assert.equal(fixture.captures.size, 0);
  }
});

test("native touch, scrollbars, secondary buttons and unavailable or fitting images are not intercepted", () => {
  for (const ignored of [{ pointerType: "touch" }, { isPrimary: false }, { button: 1 }, { button: 2 }, { scrollbar: true }, { fits: true }, { loading: true }, { missing: true }, { failed: true }, { captureFails: true }]) {
    const view = imageView(ignored.missing ? null : undefined), fixture = panSurface(ignored.fits ? { width: 800, height: 400 } : undefined);
    if (!ignored.missing && !ignored.loading) view.imageEvent(ignored.failed ? "onError" : "onLoad");
    if (ignored.captureFails) fixture.surface.setPointerCapture = () => { throw new Error("Pointer already released"); };
    view.pointer("onPointerDown", fixture.event({ ...ignored, ...(ignored.scrollbar ? { target: fixture.surface } : {}) }));
    view.pointer("onPointerMove", fixture.event({ clientX: 0, clientY: 0 }));
    assert.equal(view.stage().props["data-panning"], false, JSON.stringify(ignored));
    assert.equal(fixture.captures.size, 0); assert.equal(fixture.prevented, 0);
  }
});

test("preview layout compacts the existing lanes and bounds image scrolling without hiding the footer", async () => {
  const css = await readFile(new URL("../app/components/TestSuitesWorkspace.css", import.meta.url), "utf8");
  assert.match(css, /\[data-preview-open="true"\][^}]*\.suite-selected-list[^}]*--rail-card-size: clamp\(52px, 18cqh, 100px\)/);
  assert.match(css, /\[data-preview-open="true"\] \.suite-step-canvas \.suite-horizontal-rail[^}]*flex: none;[^}]*height:/);
  assert.match(css, /\.suite-step-image\s*\{[^}]*flex: 1;[^}]*min-height: 0;[^}]*overflow: hidden;/);
  assert.match(css, /\.suite-step-editor > \.suite-step-image\s*\{\s*grid-column: 1 \/ -1;/);
  assert.match(css, /\[data-preview-open="true"\] \.suite-step-editor\s*\{\s*grid-template-rows: auto minmax\(0, 1fr\);/);
  assert.match(css, /\[data-preview-open="true"\] \.suite-step-editor\s*\{\s*grid-template-rows: auto auto minmax\(0, 1fr\);/);
  assert.match(css, /\.suite-image-stage\s*\{[^}]*min-height: 0;[^}]*overflow: auto;/);
  assert.match(css, /\.suite-image-stage\[data-pannable="true"\]\s*\{[^}]*cursor: grab;[^}]*scroll-behavior: auto;[^}]*touch-action: pan-x pan-y pinch-zoom;/);
  assert.match(css, /\.suite-image-stage\[data-panning="true"\]\s*\{[^}]*cursor: grabbing;/);
  assert.match(css, /\.suite-image-scale img\s*\{[^}]*object-fit: contain;/);
  assert.match(css, /@container suite-flow \(max-width: 640px\)\s*\{\s*\.suite-step-image > header/);
});
