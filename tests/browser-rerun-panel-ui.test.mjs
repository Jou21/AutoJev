import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("../backend/autojev/adapters/browser_rerun_panel_assets/panel.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function harness() {
  class Element {
    constructor(id = "") {
      this.id = id; this.events = {}; this.dataset = {}; this.attributes = {}; this.children = [];
      this.textContent = ""; this.hidden = false; this.disabled = false; this.checked = false;
      this.offsetTop = 0; this.scrollTop = 0; this.value = "";
      this.classList = { add() {}, remove() {} };
    }
    addEventListener(name, callback) { this.events[name] = callback; }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; if (name === "src") delete this.src; }
    append(...children) { this.children.push(...children); }
    replaceChildren(fragment) { this.children = fragment.children; }
    focus() { this.focused = true; }
    click() { this.events.click?.({ isTrusted: false }); }
  }
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((match) => [match[1], new Element(match[1])]));
  const document = {
    events: {}, getElementById: (id) => elements[id], createElement: () => new Element(),
    createDocumentFragment: () => new Element(), addEventListener(name, callback) { this.events[name] = callback; },
  };
  const calls = [];
  const window = { __autojev_rerun_command: async (command) => { calls.push(command); return { accepted: true }; } };
  class FileReader {
    readAsDataURL(blob) { blob.arrayBuffer().then((data) => { this.result = `data:${blob.type};base64,${Buffer.from(data).toString("base64")}`; this.onload(); }); }
  }
  class Image {
    naturalWidth = 2;
    naturalHeight = 2;
    set src(value) { this.value = value; queueMicrotask(() => this.onload()); }
  }
  vm.runInNewContext(script, { window, document, Blob, FileReader, Image });
  return { elements, document, calls, update: window.__autojevRerunPanel.update };
}

function payload(overrides = {}) {
  return {
    phase: "waiting_capture", running: true, nonce: "private-frame-nonce",
    checkpoint: { id: "db-1", version: 3, gap: 2, order: 0 }, capture_count: 1, captures_completed: 0,
    progress: { completed: 1, total: 3, current_id: "checkpoint-db-1", steps: [
      { id: "source-0", title: "Antes da ação", state: "done", label: "Captura registrada", description: "Não é uma aprovação." },
      { id: "checkpoint-db-1", title: "Aguardar print", state: "waiting", label: "Aguardando seu print" },
      { id: "source-1", title: "Selecionar opção", state: "pending", label: "Aguardando" },
    ] }, usage: { total_tokens: 0, cost_known: false }, ...overrides,
  };
}

function png() {
  const file = new Blob([Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 0])], { type: "image/png" });
  Object.defineProperty(file, "name", { value: "meu-print.png" });
  return file;
}

async function flush() { await new Promise(setImmediate); await new Promise(setImmediate); }

async function choose(app, file = png()) {
  app.elements["file-input"].files = [file];
  app.elements["file-input"].events.change({ target: app.elements["file-input"] });
  await flush();
}

test("floating panel is a self-contained sandbox UI without external transport or persistence", () => {
  new vm.Script(script);
  assert.match(html, /connect-src 'none'/);
  assert.match(html, /form-action 'none'/);
  assert.doesNotMatch(script, /\bfetch\s*\(|XMLHttpRequest|WebSocket|postMessage|localStorage|sessionStorage|navigator\.clipboard|innerHTML/);
  assert.doesNotMatch(html, /<(?:script|link)[^>]+(?:src|href)=/);
});

test("map preserves projected order and status without exposing the private nonce in visible text", () => {
  const app = harness(); app.update(payload());
  assert.equal(app.elements.steps.children.length, 3);
  assert.deepEqual(app.elements.steps.children.map((card) => card.dataset.state), ["done", "waiting", "pending"]);
  assert.equal(app.elements.steps.children[1].attributes["aria-current"], "step");
  assert.equal(app.elements.completed.textContent, "1");
  assert.equal(app.elements["capture-position"].textContent, "Print 1 de 1 · Após a etapa 2");
  assert.equal(app.elements["confirm-capture"].disabled, true);
  assert.ok(Object.values(app.elements).every((element) => !element.textContent.includes("private-frame-nonce")));
});

test("choosing or pasting only stages a preview and only a native explicit confirmation calls the bridge", async () => {
  const app = harness(); app.update(payload());
  await choose(app);
  assert.equal(app.calls.length, 0);
  assert.equal(app.elements["selected-file"].hidden, false);
  assert.match(app.elements.preview.src, /^data:image\/png;base64,/);
  assert.equal(app.elements["capture-status"].textContent, "Print selecionado. Falta confirmar o envio.");
  assert.equal(app.elements["confirm-capture"].disabled, false);
  await app.elements["confirm-capture"].events.click({ isTrusted: false });
  assert.equal(app.calls.length, 0);
  await app.elements["confirm-capture"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].op, "capture");
  assert.equal(app.calls[0].checkpoint_id, "db-1");
  assert.equal(app.calls[0].checkpoint_version, 3);
  assert.equal(app.calls[0].nonce, "private-frame-nonce");
  assert.equal(app.calls[0].confirm, true);
  assert.equal(app.elements["confirm-capture"].disabled, true);
  assert.equal(app.elements.steps.children[1].dataset.state, "waiting", "acceptance is not durable evidence");
  app.update(payload());
  await app.elements["confirm-capture"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 1, "polling cannot enable duplicate confirmation");
});

test("preview survives polling, but a new checkpoint or terminal execution clears the unsent image", async () => {
  const app = harness(); app.update(payload()); await choose(app);
  const preview = app.elements.preview.src;
  app.update(payload({ nonce: "rotated-but-same-checkpoint" }));
  assert.equal(app.elements.preview.src, preview);
  app.update(payload({ checkpoint: { id: "db-1", version: 4, gap: 2 } }));
  assert.equal(app.elements.preview.src, undefined);
  assert.equal(app.elements["confirm-capture"].disabled, true);
  await choose(app);
  app.update(payload({ phase: "finished", running: false, checkpoint: null }));
  assert.equal(app.elements.preview.src, undefined);
  assert.equal(app.elements.capture.hidden, true);
});

test("pasting and dropping images stage locally; invalid content cannot be confirmed", async () => {
  const app = harness(); app.update(payload());
  let prevented = 0;
  app.document.events.paste({ clipboardData: { items: [{ kind: "file", getAsFile: png }] }, preventDefault() { prevented += 1; } });
  await flush();
  assert.equal(prevented, 1);
  assert.equal(app.calls.length, 0);
  assert.equal(app.elements["confirm-capture"].disabled, false);
  app.document.events.drop({ dataTransfer: { files: [new Blob(["not a PNG"], { type: "image/png" })] }, preventDefault() {} });
  await flush();
  assert.equal(app.elements["confirm-capture"].disabled, true);
  assert.match(app.elements["capture-status"].textContent, /PNG, JPG ou WEBP válido/);
  assert.equal(app.calls.length, 0);
});

test("preparation requires consent and explicit native confirmation for the current version", async () => {
  const app = harness();
  const prep = payload({ phase: "waiting_preparation", checkpoint: null, preparation: { version: 2, reason_label: "Faça login para continuar", expected_url: "https://example.test/inventory", current_url: "https://example.test/", instruction: "Preencha os campos." } });
  app.update(prep);
  await app.elements["confirm-preparation"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 0);
  app.elements["preparation-consent"].checked = true;
  app.elements["preparation-consent"].events.change();
  await app.elements["confirm-preparation"].events.click({ isTrusted: false });
  assert.equal(app.calls.length, 0);
  await app.elements["confirm-preparation"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].op, "resume");
  assert.equal(app.calls[0].preparation_version, 2);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  app.update(prep);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
});

test("a large roteiro retains its current checkpoint instead of silently truncating at 200 cards", () => {
  const app = harness();
  const steps = Array.from({ length: 250 }, (_, index) => ({ id: `source-${index}`, title: `Etapa ${index + 1}`, state: index === 249 ? "waiting" : "pending" }));
  app.update(payload({ progress: { total: 250, completed: 0, current_id: "source-249", steps } }));
  assert.equal(app.elements.steps.children.length, 250);
  assert.equal(app.elements.steps.children[249].attributes["aria-current"], "step");
});
