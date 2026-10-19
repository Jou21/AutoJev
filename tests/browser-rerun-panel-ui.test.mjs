import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("../backend/autojev/adapters/browser_rerun_panel_assets/panel.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function harness({ command } = {}) {
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
  const revisions = new Map();
  const window = { __autojev_rerun_command: async (value) => {
    calls.push(value);
    if (command) return command(value);
    if (value.op === "consent") return { preparation_version: value.preparation_version, revision: value.expected_revision + 1, checked: value.checked };
    if (value.op !== "preview") return { accepted: true };
    const key = `${value.checkpoint_id}:${value.checkpoint_version}`;
    const revision = (revisions.get(key) || 0) + 1;
    revisions.set(key, revision);
    return { checkpoint_id: value.checkpoint_id, checkpoint_version: value.checkpoint_version, revision, changed: true,
      image: value.image_data_url ? { data_url: value.image_data_url, name: "Print compartilhado", size: Buffer.from(value.image_data_url.split(",")[1], "base64").length } : null };
  } };
  class FileReader {
    readAsDataURL(blob) { blob.arrayBuffer().then((data) => { this.result = `data:${blob.type};base64,${Buffer.from(data).toString("base64")}`; this.onload(); }); }
  }
  class Image {
    naturalWidth = 2;
    naturalHeight = 2;
    set src(value) { this.value = value; queueMicrotask(() => this.onload()); }
  }
  vm.runInNewContext(script, { window, document, Blob, FileReader, Image });
  return { elements, document, calls, update: window.__autojevRerunPanel.update, updatePreview: window.__autojevRerunPanel.updatePreview };
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

test("choosing shares a preview but only native explicit confirmation attaches it and resumes", async () => {
  const app = harness(); app.update(payload());
  await choose(app);
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].op, "preview");
  assert.equal(app.calls[0].expected_revision, 0);
  assert.equal(app.calls[0].confirm, undefined);
  assert.equal(app.elements["selected-file"].hidden, false);
  assert.match(app.elements.preview.src, /^data:image\/png;base64,/);
  assert.equal(app.elements["capture-status"].textContent, "Print selecionado. Falta confirmar o envio.");
  assert.equal(app.elements["confirm-capture"].disabled, false);
  await app.elements["confirm-capture"].events.click({ isTrusted: false });
  assert.equal(app.calls.length, 1);
  await app.elements["confirm-capture"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 2);
  assert.equal(app.calls[1].op, "capture");
  assert.equal(app.calls[1].checkpoint_id, "db-1");
  assert.equal(app.calls[1].checkpoint_version, 3);
  assert.equal(app.calls[1].nonce, "private-frame-nonce");
  assert.equal(app.calls[1].confirm, true);
  assert.equal(app.elements["confirm-capture"].disabled, true);
  assert.equal(app.elements.steps.children[1].dataset.state, "waiting", "acceptance is not durable evidence");
  app.update(payload());
  await app.elements["confirm-capture"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 2, "polling cannot enable duplicate confirmation");
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

test("pasting and dropping share valid previews; invalid content cannot be confirmed", async () => {
  const app = harness(); app.update(payload());
  app.document.events.drop({ dataTransfer: { files: [new Blob(["not a PNG"], { type: "image/png" })] }, preventDefault() {} });
  await flush();
  assert.equal(app.elements["confirm-capture"].disabled, true);
  assert.match(app.elements["capture-status"].textContent, /PNG, JPG ou WEBP válido/);
  assert.equal(app.calls.length, 0);
  let prevented = 0;
  app.document.events.paste({ clipboardData: { items: [{ kind: "file", getAsFile: png }] }, preventDefault() { prevented += 1; } });
  await flush();
  assert.equal(prevented, 1);
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].op, "preview");
  assert.equal(app.elements["confirm-capture"].disabled, false);
  app.document.events.drop({ dataTransfer: { files: [new Blob(["not a PNG"], { type: "image/png" })] }, preventDefault() {} });
  await flush();
  assert.equal(app.elements["confirm-capture"].disabled, false, "an invalid replacement preserves the previous shared image");
  assert.match(app.elements["capture-status"].textContent, /PNG, JPG ou WEBP válido/);
  assert.equal(app.calls.length, 1, "invalid replacement is never shared");
});

function snapshot(revision, image = null, overrides = {}) {
  return { checkpoint_id: "db-1", checkpoint_version: 3, revision, changed: true, image, ...overrides };
}

const sharedImage = (name = "Print na tela principal") => ({ data_url: "data:image/png;base64,iVBORw0KGgoAAAAA", name, size: 12 });

test("remote replace and remove update this panel without echoing or attaching", () => {
  const app = harness(); app.update(payload());
  app.updatePreview(snapshot(1, sharedImage()));
  assert.equal(app.elements["selected-file"].hidden, false);
  assert.equal(app.elements["file-name"].textContent, "Print na tela principal");
  assert.equal(app.elements["confirm-capture"].disabled, false);
  app.updatePreview(snapshot(2, sharedImage("Outro print")));
  assert.equal(app.elements["file-name"].textContent, "Outro print");
  app.updatePreview(snapshot(1, sharedImage("Versão antiga")));
  assert.equal(app.elements["file-name"].textContent, "Outro print");
  app.updatePreview(snapshot(3));
  assert.equal(app.elements.preview.src, undefined);
  assert.equal(app.elements["confirm-capture"].disabled, true);
  assert.equal(app.calls.length, 0, "host updates never echo preview commands or attach evidence");
});

test("a snapshot arriving before checkpoint progress waits for the exact checkpoint version", () => {
  const app = harness();
  app.updatePreview(snapshot(4, sharedImage()));
  app.update(payload({ checkpoint: { id: "db-1", version: 4, gap: 2 } }));
  assert.equal(app.elements.preview.src, undefined);
  app.update(payload());
  assert.equal(app.elements["file-name"].textContent, "Print na tela principal");
  app.update(payload({ phase: "executing", checkpoint: null }));
  app.updatePreview(snapshot(5, sharedImage("Antigo checkpoint")));
  assert.equal(app.elements.preview.src, undefined, "a completed checkpoint cannot repopulate an inactive uploader");
});

test("removing a local selection shares null without creating evidence", async () => {
  const app = harness(); app.update(payload()); await choose(app);
  await app.elements["remove-file"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 2);
  assert.equal(app.calls[1].op, "preview");
  assert.equal(app.calls[1].expected_revision, 1);
  assert.equal(app.calls[1].image_data_url, null);
  assert.equal(app.elements.preview.src, undefined);
  assert.equal(app.elements["confirm-capture"].disabled, true);
});

test("inflight sharing protects local preview and blocks confirmation until authoritative revision arrives", async () => {
  let finish;
  const app = harness({ command: () => new Promise((resolve) => { finish = resolve; }) });
  app.update(payload()); await choose(app);
  assert.equal(app.calls.length, 1);
  assert.equal(app.elements["selected-file"].hidden, false, "local preview is immediately visible");
  assert.equal(app.elements["confirm-capture"].disabled, true);
  app.updatePreview(snapshot(0));
  app.updatePreview(snapshot(2, sharedImage("Mais recente na outra tela")));
  assert.equal(app.elements["file-name"].textContent, "meu-print.png", "old/inflight snapshots cannot erase staged feedback");
  await app.elements["confirm-capture"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 1, "confirmation cannot race pending preview transport");
  finish(snapshot(1, sharedImage("Minha prévia confirmada")));
  await flush();
  assert.equal(app.elements["file-name"].textContent, "Mais recente na outra tela");
  assert.equal(app.elements["confirm-capture"].disabled, false);
});

test("a rejected CAS blocks unsynced confirmation until a forced authoritative refresh", async () => {
  const app = harness({ command: async () => { throw new Error("revision conflict"); } });
  app.update(payload()); app.updatePreview(snapshot(0)); await choose(app);
  assert.equal(app.elements["confirm-capture"].disabled, true);
  await app.elements["confirm-capture"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 1);
  app.updatePreview(snapshot(0));
  assert.equal(app.elements.preview.src, undefined, "even a same-revision authoritative clear can resolve a rejected selection");
  app.updatePreview(snapshot(1, sharedImage()));
  assert.equal(app.elements["confirm-capture"].disabled, false);
  assert.match(html, /prévia é compartilhada com a tela principal/);
});

test("preparation requires consent and explicit native confirmation for the current version", async () => {
  const app = harness();
  const prep = payload({ phase: "waiting_preparation", checkpoint: null, preparation: { version: 2, reason_label: "Faça login para continuar", expected_url: "https://example.test/inventory", current_url: "https://example.test/", instruction: "Preencha os campos." } });
  app.update(prep);
  await app.elements["confirm-preparation"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 0);
  app.elements["preparation-consent"].checked = true;
  await app.elements["preparation-consent"].events.change({ isTrusted: true });
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

test("detected login gives direct guidance without automatically resuming or inspecting credentials", async () => {
  const app = harness();
  app.update(payload({ phase: "waiting_preparation", checkpoint: null, preparation: {
    version: 2, reason_code: "authentication_required",
    reason_label: "A página aberta não é a mesma em que o teste original começou.",
    expected_url: "https://example.test/inventory", current_url: "https://example.test/",
    instruction: "Faça login antes de continuar.",
  } }));
  assert.equal(app.elements["preparation-eyebrow"].textContent, "Login necessário");
  assert.equal(app.elements["preparation-heading"].textContent, "Faça login para continuar");
  assert.equal(app.elements["preparation-hint"].textContent, "Marque a autorização e faça login pelo próprio site. O AutoJev identifica o login e continua automaticamente após conferir a página e os pré-requisitos. Se preferir, preencha usuário e senha e clique em “Continuar reexecução” para o AutoJev tentar entrar.");
  assert.equal(app.elements["preparation-note"].textContent, "Com a autorização marcada, o teste continua automaticamente após o login e a conferência dos pré-requisitos.");
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  await app.elements["confirm-preparation"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 0, "a login diagnosis never authorizes submission by itself");
  assert.doesNotMatch(script, /querySelector\([^)]*(?:password|username)|input\[type=["']?password/);
});

test("a non-login URL mismatch keeps generic guidance and clears previous login-specific copy", () => {
  const app = harness();
  app.update(payload({ phase: "waiting_preparation", checkpoint: null,
    preparation: { version: 2, reason_code: "authentication_required" } }));
  const reason = "A página aberta não é a mesma em que o teste original começou.";
  app.update(payload({ phase: "waiting_preparation", checkpoint: null,
    preparation: { version: 3, reason_code: "before_url_changed", reason_label: reason } }));
  assert.equal(app.elements["preparation-eyebrow"].textContent, "Prepare a página");
  assert.equal(app.elements["preparation-heading"].textContent, "Prepare a página para continuar");
  assert.equal(app.elements["preparation-feedback"].textContent, reason);
  assert.equal(app.elements["preparation-hint"].textContent, "Prepare a página e os dados iniciais do teste. Depois, marque a autorização e clique em “Continuar reexecução”. Se houver um login preenchido no site autorizado, o AutoJev tentará entrar. A ação do teste só será executada se os pré-requisitos forem confirmados.");
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  assert.equal(app.calls.length, 0);
});

function preparationPayload({ version = 2, revision = 0, checked = false, ...overrides } = {}) {
  return payload({ phase: "waiting_preparation", exploration_id: "fixture-run", checkpoint: null,
    preparation: { version, reason_code: "authentication_required", consent: { preparation_version: version, revision, checked } }, ...overrides });
}

test("native checkbox shares authorization without itself emitting a browser resume command", async () => {
  const app = harness(); app.update(preparationPayload());
  app.elements["preparation-consent"].checked = true;
  await app.elements["preparation-consent"].events.change({ isTrusted: false });
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.calls.length, 0);
  app.elements["preparation-consent"].checked = true;
  await app.elements["preparation-consent"].events.change({ isTrusted: true });
  assert.equal(app.calls.length, 1);
  assert.deepEqual({ ...app.calls[0] }, { op: "consent", nonce: "private-frame-nonce", preparation_version: 2, expected_revision: 0, checked: true });
  assert.equal(app.calls[0].confirm, undefined);
  assert.equal(app.elements["confirm-preparation"].disabled, false);
  assert.equal(app.elements["confirm-preparation"].textContent, "Continuar reexecução");
  await app.elements["confirm-preparation"].events.click({ isTrusted: false });
  assert.equal(app.calls.length, 1);
  await app.elements["confirm-preparation"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 2);
  assert.equal(app.calls[1].op, "resume");
  assert.equal(app.calls[1].confirm, true);
});

test("remote checkbox selection needs no second local check, and newer remote clear removes consent", async () => {
  const app = harness(); app.update(preparationPayload({ revision: 1, checked: true }));
  assert.equal(app.elements["preparation-consent"].checked, true);
  assert.equal(app.elements["confirm-preparation"].disabled, false);
  assert.equal(app.calls.length, 0, "remote updates cannot echo checkbox writes or resume");
  app.update(preparationPayload({ revision: 2, checked: false }));
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  app.update(preparationPayload({ revision: 1, checked: true }));
  assert.equal(app.elements["preparation-consent"].checked, false, "old poll cannot restore an obsolete authorization");
  await app.elements["confirm-preparation"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 0);
});

test("pending checkbox sync keeps optimistic feedback, blocks continue, and applies the newest remote revision", async () => {
  let finish;
  const app = harness({ command: () => new Promise((resolve) => { finish = resolve; }) });
  app.update(preparationPayload());
  app.elements["preparation-consent"].checked = true;
  const sharing = app.elements["preparation-consent"].events.change({ isTrusted: true });
  assert.equal(app.elements["preparation-consent"].checked, true);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  app.update(preparationPayload());
  app.update(preparationPayload({ revision: 2, checked: false }));
  assert.equal(app.elements["preparation-consent"].checked, true);
  await app.elements["confirm-preparation"].events.click({ isTrusted: true });
  assert.equal(app.calls.length, 1);
  finish({ preparation_version: 2, revision: 1, checked: true });
  await sharing;
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
});

test("rejected checkbox CAS waits for authoritative refresh and a new preparation invalidates pending authorization", async () => {
  const app = harness({ command: async () => { throw new Error("CAS conflict"); } });
  app.update(preparationPayload());
  app.elements["preparation-consent"].checked = true;
  await app.elements["preparation-consent"].events.change({ isTrusted: true });
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  assert.match(app.elements["preparation-status"].textContent, /não foi sincronizada/);
  app.update(preparationPayload());
  assert.equal(app.elements["preparation-consent"].checked, false, "same revision forced refresh restores the service choice");
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  app.update(preparationPayload({ revision: 1, checked: true }));
  assert.equal(app.elements["confirm-preparation"].disabled, false);
  app.update(preparationPayload({ version: 3 }));
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  app.update(preparationPayload({ preparation: { version: 3, consent: null } }));
  assert.equal(app.elements["preparation-consent"].disabled, true, "inactive shared gate is not legacy local-only mode");
});

test("preparation copy comes from the shared presentation, with feedback kept separate", () => {
  const app = harness();
  const presentation = { kicker: "Mesmo destaque", title: "Mesmo título", instruction: "Mesma instrução", consent_label: "Mesma autorização", button_label: "Mesmo botão", details_label: "Mesmos detalhes", note: "Mesma nota", feedback: "Mesmo retorno" };
  app.update(preparationPayload({ preparation: { version: 2, consent: { preparation_version: 2, revision: 0, checked: false }, presentation } }));
  for (const [key, id] of [["kicker", "preparation-eyebrow"], ["title", "preparation-heading"], ["instruction", "preparation-hint"], ["consent_label", "preparation-consent-label"], ["button_label", "confirm-preparation"], ["details_label", "preparation-details-label"], ["note", "preparation-note"], ["feedback", "preparation-feedback"]]) assert.equal(app.elements[id].textContent, presentation[key]);
  assert.equal(app.elements["preparation-status"].textContent, "");
  app.update(preparationPayload({ phase: "validating_preparation", preparation: { version: 2, consent: null } }));
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  assert.equal(app.elements["confirm-preparation"].textContent, "Entrando e conferindo preparação…");
});

test("delayed consent cannot restore authorization after validation starts at the same version", async () => {
  let finish;
  const app = harness({ command: () => new Promise((resolve) => { finish = resolve; }) });
  app.update(preparationPayload());
  app.elements["preparation-consent"].checked = true;
  const sharing = app.elements["preparation-consent"].events.change({ isTrusted: true });
  app.update(preparationPayload({ revision: 2, checked: true }));
  app.update(preparationPayload({ phase: "validating_preparation", revision: 2, checked: true }));
  finish({ preparation_version: 2, revision: 1, checked: true });
  await sharing;
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.elements["preparation-consent"].disabled, true);
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  assert.equal(app.elements["confirm-preparation"].textContent, "Entrando e conferindo preparação…");
  assert.equal(app.elements["preparation-status"].textContent, "");
});

test("a shared login authorization alone neither claims authentication nor starts a browser resume", async () => {
  const app = harness();
  app.update(preparationPayload({ revision: 1, checked: true }));
  await flush();
  assert.equal(app.elements["preparation-consent"].checked, true);
  assert.equal(app.elements["preparation-heading"].textContent, "Faça login para continuar");
  assert.equal(app.elements["confirm-preparation"].textContent, "Continuar reexecução");
  assert.equal(app.elements["preparation-status"].textContent, "");
  assert.equal(app.calls.length, 0, "only the backend can observe an authenticated page; checked alone cannot emit resume");
  // Once the real backend has observed the page and started its guarded
  // preparation check, the panel reflects that phase without emitting a
  // second continuation or making an independent authentication claim.
  app.update(preparationPayload({ phase: "validating_preparation", revision: 1, checked: true }));
  assert.equal(app.elements["preparation-heading"].textContent, "Entrando e conferindo preparação…");
  assert.equal(app.elements["confirm-preparation"].disabled, true);
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.calls.length, 0);
});

test("only server-observed ready login changes the guidance to recognized-login authorization", () => {
  const app = harness();
  app.update(preparationPayload({ preparation: { version: 2, reason_code: "authentication_required",
    manual_login: { status: "ready" }, consent: { preparation_version: 2, revision: 0, checked: false } } }));
  assert.equal(app.elements["preparation-eyebrow"].textContent, "Login reconhecido");
  assert.equal(app.elements["preparation-heading"].textContent, "Login realizado. Autorize para continuar");
  assert.equal(app.elements["preparation-hint"].textContent, "Seu login foi reconhecido e os pré-requisitos foram conferidos. Marque a autorização para o teste continuar automaticamente.");
  assert.equal(app.elements["preparation-feedback"].textContent, "Login reconhecido; aguardando sua autorização para continuar.");
  assert.equal(app.elements["preparation-note"].textContent, "Com a autorização marcada, o teste continua automaticamente após o login e a conferência dos pré-requisitos.");
  assert.equal(app.elements["preparation-consent"].checked, false);
  assert.equal(app.calls.length, 0);
  app.update(preparationPayload({ preparation: { version: 3, reason_code: "before_url_changed", reason_label: "A página inicial ainda não está pronta.",
    manual_login: { status: "blocked" }, consent: { preparation_version: 3, revision: 0, checked: false } } }));
  assert.equal(app.elements["preparation-heading"].textContent, "Prepare a página para continuar");
  assert.equal(app.elements["preparation-feedback"].textContent, "A página inicial ainda não está pronta.");
  assert.equal(app.elements["preparation-note"].textContent, "Marcar a autorização não inicia o teste. Clique em “Continuar reexecução” para confirmar.");
});

test("a large roteiro retains its current checkpoint instead of silently truncating at 200 cards", () => {
  const app = harness();
  const steps = Array.from({ length: 250 }, (_, index) => ({ id: `source-${index}`, title: `Etapa ${index + 1}`, state: index === 249 ? "waiting" : "pending" }));
  app.update(payload({ progress: { total: 250, completed: 0, current_id: "source-249", steps } }));
  assert.equal(app.elements.steps.children.length, 250);
  assert.equal(app.elements.steps.children[249].attributes["aria-current"], "step");
});
