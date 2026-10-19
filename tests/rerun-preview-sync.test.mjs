import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

// Exercise the real capture component with deterministic hook/timer/network
// doubles. No screenshot bytes leave this process and no real CT is rerun.
async function loadCapture(react) {
  const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
  const nativeRequire = createRequire(import.meta.url);
  const compile = async (path, fileName) => transpileModule(await readFile(new URL(path, import.meta.url), "utf8"), {
    fileName, compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
  }).outputText;
  const board = { exports: {} };
  new Function("require", "module", "exports", await compile("../app/components/RerunLiveBoard.tsx", "RerunLiveBoard.tsx"))(
    (name) => name.endsWith(".css") ? {} : name === "react" ? react : nativeRequire(name), board, board.exports,
  );
  const app = { exports: {} };
  new Function("require", "module", "exports", await compile("../app/AutoJevApp.tsx", "AutoJevApp.tsx"))(
    (name) => name === "./components/RerunLiveBoard" ? board.exports : name === "react" ? react : nativeRequire(name), app, app.exports,
  );
  return app.exports.ScenarioRerunCapture;
}

function hooks(react) {
  const slots = [];
  const effects = [];
  let cursor = 0;
  const changed = (next, old) => !old || !next || next.some((value, index) => !Object.is(value, old[index]));
  return {
    react: {
      ...react,
      useState(initial) {
        const index = cursor++;
        slots[index] ??= { value: typeof initial === "function" ? initial() : initial };
        return [slots[index].value, (value) => { slots[index].value = typeof value === "function" ? value(slots[index].value) : value; }];
      },
      useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
      useId() { cursor++; return "preview-test-chooser"; },
      useCallback(callback, dependencies) {
        const index = cursor++;
        if (changed(dependencies, slots[index]?.dependencies)) slots[index] = { dependencies, callback };
        return slots[index].callback;
      },
      useEffect(effect, dependencies) {
        const index = cursor++;
        if (changed(dependencies, slots[index]?.dependencies)) {
          slots[index]?.cleanup?.();
          slots[index] = { dependencies };
          effects.push(() => { slots[index].cleanup = effect(); });
        }
      },
    },
    render(component, props) { cursor = 0; return component(props); },
    flush() { while (effects.length) effects.shift()(); },
    dispose() { for (const slot of slots) slot?.cleanup?.(); },
  };
}

function find(tree, predicate) {
  if (!tree || typeof tree !== "object") return undefined;
  if (Array.isArray(tree)) return tree.map((node) => find(node, predicate)).find(Boolean);
  return predicate(tree) ? tree : find(tree.props?.children, predicate);
}
function text(tree) {
  if (tree === null || tree === undefined || typeof tree === "boolean") return "";
  if (typeof tree === "string" || typeof tree === "number") return String(tree);
  if (Array.isArray(tree)) return tree.map(text).join("");
  return text(tree.props?.children);
}
const imageA = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==";
const imageB = "data:image/png;base64,iVBORw0KGgoAAAANSUhEVg==";
const checkpoint = { id: "db-1", gap: 2, order: 0, version: 3 };
const snapshot = (revision, image, current = checkpoint) => ({
  checkpoint_id: current.id, checkpoint_version: current.version, revision, changed: true,
  image: image ? { data_url: image, name: "Print sincronizado", size: 16 } : null,
});
const defer = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

async function harness(request) {
  const runner = hooks(await import("react"));
  const Component = await loadCapture(runner.react);
  const originals = Object.fromEntries(["window", "document", "Element", "FileReader"].map((key) => [key, globalThis[key]]));
  const intervals = new Map();
  const listeners = new Map();
  const submissions = [];
  const scrolls = [];
  let nextTimer = 0;
  globalThis.window = {
    addEventListener(name, listener) { listeners.set(name, listener); },
    removeEventListener(name, listener) { if (listeners.get(name) === listener) listeners.delete(name); },
    setInterval(callback, milliseconds) { assert.equal(milliseconds, 1000); intervals.set(++nextTimer, callback); return nextTimer; },
    clearInterval(id) { intervals.delete(id); },
  };
  globalThis.document = { activeElement: null, querySelector() { return null; } };
  globalThis.Element = class {};
  globalThis.FileReader = class {
    readAsDataURL(file) { queueMicrotask(() => { this.result = file.dataUrl || imageA; this.onload?.(); }); }
  };
  let properties = { checkpoint, total: 1, explorationId: "rerun-1", request, onCapture: (value) => submissions.push(value) };
  const render = (extra = {}) => {
    properties = { ...properties, ...extra };
    const tree = runner.render(Component, properties);
    find(tree, (node) => node.type === "section").props.ref.current = { scrollIntoView: (options) => scrolls.push(options) };
    find(tree, (node) => node.type === "h2").props.ref.current = { focus() {} };
    runner.flush();
    return tree;
  };
  return {
    render, submissions, scrolls, intervals, listeners,
    tick() { for (const interval of [...intervals.values()]) interval(); },
    choose(tree, dataUrl = imageA) {
      find(tree, (node) => node.type === "input" && node.props.type === "file").props.onChange({ target: {
        value: "print.png", files: [{ name: "print.png", size: 16, type: "image/png", dataUrl,
          slice() { return { arrayBuffer: async () => Uint8Array.from([137, 80, 78, 71, 13, 10, 26, 10]).buffer }; } }],
      } });
    },
    dispose() {
      runner.dispose();
      for (const [key, value] of Object.entries(originals)) {
        if (value === undefined) delete globalThis[key]; else globalThis[key] = value;
      }
    },
  };
}

test("a floating-panel preview appears in the main app, unchanged polls keep it without scrolling or attaching", async () => {
  const requests = [];
  const ui = await harness(async (path, options) => {
    requests.push({ path, options });
    return requests.length === 1 ? snapshot(2, imageA) : {
      checkpoint_id: checkpoint.id, checkpoint_version: checkpoint.version, revision: 2, changed: false,
    };
  });
  try {
    ui.render(); await settle();
    let tree = ui.render();
    assert.equal(find(tree, (node) => node.type === "img").props.src, imageA);
    assert.match(text(tree), /Prévia sincronizada nas duas janelas/);
    assert.match(text(tree), /Só será salva no relatório quando você confirmar/);
    assert.equal(ui.scrolls.length, 0, "a remote image update must not repeatedly move the viewport");
    ui.tick(); await settle(); tree = ui.render({ checkpoint: { ...checkpoint } });
    assert.match(requests[1].path, /known_revision=2/);
    assert.equal(find(tree, (node) => node.type === "img").props.src, imageA);
    assert.equal(requests.length, 2, "fresh parent polling objects must not restart the preview subscription");
    assert.ok(requests.every(({ options }) => options.cache === "no-store" && !options.method));
    assert.deepEqual(ui.submissions, []);
    assert.equal(ui.scrolls.length, 0);
  } finally { ui.dispose(); }
  assert.equal(ui.intervals.size, 0);
});

test("local paste/selection and removal share only a preview; attachment still needs the explicit CTA", async () => {
  const requests = [];
  const post = defer();
  const ui = await harness(async (path, options) => {
    requests.push({ path, options });
    if (options.method === "POST") {
      const body = JSON.parse(options.body);
      return body.image_data_url === null ? snapshot(2, null) : post.promise;
    }
    return snapshot(0, null);
  });
  try {
    ui.render(); await settle();
    let tree = ui.render();
    ui.choose(tree); await settle(); tree = ui.render();
    assert.equal(find(tree, (node) => node.type === "img").props.src, imageA, "local feedback is immediate, before the POST returns");
    assert.match(text(tree), /Sincronizando a prévia nas duas janelas/);
    assert.equal(find(tree, (node) => node.type === "button" && node.props.className === "button primary").props.disabled, true);
    const posted = requests.filter(({ options }) => options.method === "POST");
    assert.equal(posted.length, 1);
    assert.equal(posted[0].path, "/explorations/rerun-1/capture-print/preview");
    assert.deepEqual(JSON.parse(posted[0].options.body), {
      checkpoint_id: "db-1", checkpoint_version: 3, expected_revision: 0, image_data_url: imageA,
    });
    ui.tick(); await settle();
    assert.equal(requests.length, 2, "an old GET cannot overwrite a pending local mutation");
    assert.deepEqual(ui.submissions, []);
    post.resolve(snapshot(1, imageA)); await settle(); tree = ui.render();
    const confirm = find(tree, (node) => node.type === "button" && node.props.className === "button primary");
    assert.equal(confirm.props.disabled, false);
    confirm.props.onClick(); confirm.props.onClick();
    assert.deepEqual(ui.submissions, [imageA], "only explicit confirmation attaches and duplicate clicks are suppressed");
    find(tree, (node) => node.props["aria-label"] === "Remover imagem escolhida").props.onClick();
    await settle(); tree = ui.render();
    assert.equal(find(tree, (node) => node.type === "img"), undefined);
    const removed = requests.filter(({ options }) => options.method === "POST").at(-1);
    assert.deepEqual(JSON.parse(removed.options.body), { checkpoint_id: "db-1", checkpoint_version: 3, expected_revision: 1, image_data_url: null });
    assert.deepEqual(ui.submissions, [imageA], "removal never confirms or resumes the CT");
  } finally { ui.dispose(); }
});

test("a GET already in flight cannot restore the older image after a local choice", async () => {
  const olderGet = defer();
  let gets = 0;
  const ui = await harness(async (_path, options) => options.method === "POST" ? snapshot(1, imageB) : ++gets === 1 ? snapshot(0, imageA) : olderGet.promise);
  try {
    ui.render(); await settle(); let tree = ui.render();
    ui.tick();
    ui.choose(tree, imageB); await settle(); tree = ui.render();
    assert.equal(find(tree, (node) => node.type === "img").props.src, imageB);
    olderGet.resolve(snapshot(0, imageA)); await settle(); tree = ui.render();
    assert.equal(find(tree, (node) => node.type === "img").props.src, imageB);
    assert.deepEqual(ui.submissions, []);
  } finally { ui.dispose(); }
});

test("CAS conflict refreshes the newer remote choice rather than silently overwriting it", async () => {
  const requests = [];
  let gets = 0;
  const ui = await harness(async (path, options) => {
    requests.push({ path, options });
    if (options.method === "POST") throw Object.assign(new Error("O print foi alterado em outra janela."), { status: 409 });
    return ++gets === 1 ? snapshot(0, null) : snapshot(2, imageB);
  });
  try {
    ui.render(); await settle(); let tree = ui.render();
    ui.choose(tree, imageA); await settle(); tree = ui.render();
    assert.equal(find(tree, (node) => node.type === "img").props.src, imageB);
    assert.match(text(tree), /O print mudou em outra janela/);
    assert.equal(requests.filter(({ options }) => options.method === "POST").length, 1, "a conflict must not automatically retry and overwrite another window");
    assert.deepEqual(ui.submissions, []);
  } finally { ui.dispose(); }
});

test("late requests from another checkpoint, a busy phase or a disposed view cannot replace the current preview", async () => {
  const late = defer();
  const requests = [];
  const nextCheckpoint = { ...checkpoint, id: "db-2", version: 4 };
  const ui = await harness(async (path, options) => {
    requests.push({ path, options });
    return path.includes("db-1") ? late.promise : snapshot(0, imageB, nextCheckpoint);
  });
  try {
    ui.render();
    ui.render({ checkpoint: nextCheckpoint }); await settle(); let tree = ui.render();
    assert.equal(requests[0].options.signal.aborted, true);
    late.resolve(snapshot(0, imageA)); await settle(); tree = ui.render();
    assert.equal(find(tree, (node) => node.type === "img").props.src, imageB);
    assert.equal(ui.intervals.size, 1);
    ui.render({ busy: true });
    assert.equal(ui.intervals.size, 0, "polling stops while attaching or after the waiting phase ends");
    assert.equal(ui.listeners.size, 1, "the generic paste listener remains safely gated by busy");
    assert.deepEqual(ui.submissions, []);
  } finally { ui.dispose(); }
  assert.equal(ui.listeners.size, 0);
});
