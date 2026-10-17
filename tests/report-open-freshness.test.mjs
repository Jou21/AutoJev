import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import test from "node:test";

async function loadDetailView() {
  const nativeRequire = createRequire(import.meta.url);
  const react = nativeRequire("react");
  const { transpileModule, ModuleKind, JsxEmit, ScriptTarget } = await import("typescript");
  const dependencies = (name) => name === "react" ? {
    ...react,
    useState: (value) => [typeof value === "function" ? value() : value, () => {}],
    useRef: (value) => ({ current: value }),
  } : nativeRequire(name);
  const compile = async (path, filename) => transpileModule(await readFile(path, "utf8"), {
    fileName: filename,
    compilerOptions: { module: ModuleKind.CommonJS, jsx: JsxEmit.ReactJSX, target: ScriptTarget.ES2022 },
  }).outputText;
  const board = { exports: {} };
  new Function("require", "module", "exports", await compile(
    new URL("../app/components/RerunLiveBoard.tsx", import.meta.url), "RerunLiveBoard.tsx",
  ))((name) => name.endsWith(".css") ? {} : dependencies(name), board, board.exports);
  const app = { exports: {} };
  new Function("require", "module", "exports", await compile(
    new URL("../app/AutoJevApp.tsx", import.meta.url), "AutoJevApp.tsx",
  ))((name) => name === "./components/RerunLiveBoard" ? board.exports : dependencies(name), app, app.exports);
  return app.exports.ExplorationDetailView;
}

function text(tree) {
  if (tree == null || typeof tree === "boolean") return "";
  if (typeof tree !== "object") return String(tree);
  if (Array.isArray(tree)) return tree.map(text).join("");
  return text(tree.props?.children);
}

function find(tree, predicate) {
  if (!tree || typeof tree !== "object") return undefined;
  if (Array.isArray(tree)) return tree.map((child) => find(child, predicate)).find(Boolean);
  return predicate(tree) ? tree : find(tree.props?.children, predicate);
}

test("Abrir relatório fetches fresh HTML on every click before opening its actual blob", async () => {
  const DetailView = await loadDetailView();
  const originals = { window: globalThis.window, fetch: globalThis.fetch };
  const originalCreate = URL.createObjectURL;
  const calls = [];
  const opened = [];
  const blobs = [];
  const errors = [];
  globalThis.window = {
    location: { origin: "http://localhost:3000" },
    open: (...args) => opened.push(args),
    setTimeout() {},
  };
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: true,
      headers: new Headers({ "content-type": "text/html; charset=utf-8" }),
      text: async () => `<html><head></head><body>fresh report ${calls.length}</body></html>`,
    };
  };
  URL.createObjectURL = (blob) => {
    blobs.push(blob);
    return `blob:http://localhost:3000/fresh-${blobs.length}`;
  };
  try {
    const tree = DetailView({
      exploration: {
        id: "source-run", app_url: "https://example.test", status: "completed",
        report_available: true, created_at: "2026-10-17T10:00:00Z", options: {}, events: [],
      },
      loading: false, providers: [], apiBase: "http://localhost:8000/api", pollInterval: 3000,
      request: async () => { throw new Error("Opening a report must not mutate or reexecute a test"); },
      toast: (...args) => errors.push(args), navigate() {}, onRefresh() {}, onCancelled() {}, onReport() {},
    });
    const control = find(tree, (node) => node.type === "button" && text(node) === "Abrir relatório");
    assert.ok(control, "Exercise the real button's handler, not a copied fetch helper");
    for (let index = 0; index < 2; index++) {
      control.props.onClick();
      await new Promise((resolve) => setImmediate(resolve));
      assert.equal(calls.length, index + 1);
      assert.equal(calls[index].url, "http://localhost:8000/api/explorations/source-run/report");
      assert.equal(calls[index].options?.cache, "no-store", "Previously cached HTML must not become another stale blob");
      assert.equal(blobs[index].type, "text/html");
      const html = await blobs[index].text();
      assert.match(html, new RegExp(`fresh report ${index + 1}`));
      assert.match(html, /id="report-app-runtime"/);
      assert.match(html, /"source_exploration_id":"source-run"/);
      assert.deepEqual(opened[index], [`blob:http://localhost:3000/fresh-${index + 1}`, "_blank", "noopener,noreferrer"]);
    }
    assert.deepEqual(errors, []);
  } finally {
    URL.createObjectURL = originalCreate;
    for (const [key, value] of Object.entries(originals)) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});
