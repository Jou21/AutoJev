import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transpileModule, ModuleKind } from "typescript";

const source = transpileModule(await readFile(new URL("../app/components/suite-report-export.ts", import.meta.url), "utf8"), { compilerOptions: { module: ModuleKind.CommonJS } }).outputText;
function helper(window, document = {}) {
  const compiled = { exports: {} };
  new Function("require", "module", "exports", "window", "document", source)(() => ({}), compiled, compiled.exports, window, document);
  return compiled.exports.downloadExecutionReport;
}
const html = body => new Response(body, { headers: { "content-type": "text/html" } });

test("picker runs synchronously before request; bytes stream without text/blob and close", async () => {
  const events = [], chunks = [];
  let choose;
  const writable = new WritableStream({ write(chunk) { chunks.push(...chunk); events.push("write"); }, close() { events.push("close"); } });
  const download = helper({ showSaveFilePicker(options) { events.push("picker"); assert.equal(options.suggestedName, "one.html"); return new Promise(resolve => { choose = resolve; }); } });
  const result = download(async () => {
    events.push("fetch");
    const response = html(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([65, 66])); controller.enqueue(new Uint8Array([67])); controller.close(); } }));
    response.text = response.blob = () => assert.fail("must not buffer response");
    return response;
  }, "/api/report", "one.html");
  assert.deepEqual(events, ["picker"]);
  choose({ createWritable: async () => writable });
  assert.equal(await result, true);
  assert.deepEqual(chunks, [65, 66, 67]);
  assert.deepEqual(events, ["picker", "fetch", "write", "write", "close"]);
});

test("picker cancellation makes no request; other picker failures surface", async () => {
  const load = () => assert.fail("no request expected");
  assert.equal(await helper({ showSaveFilePicker() { return Promise.reject(new DOMException("cancel", "AbortError")); } })(load, "/report", "one.html"), false);
  await assert.rejects(helper({ showSaveFilePicker() { throw new Error("permission"); } })(load, "/report", "one.html"), /permission/);
});

test("unsupported browsers click native attachment immediately without fetching or blobs", async () => {
  const events = [], link = { click() { events.push("click"); }, remove() { events.push("remove"); } };
  const download = helper({}, { createElement: () => link, body: { appendChild() { events.push("append"); } } });
  const result = download(() => assert.fail("native navigation owns request"), "https://api.invalid/api/test-suites/root/report?scope=initial", "one.html");
  assert.deepEqual(events, ["append", "click", "remove"]);
  assert.equal(link.href, "https://api.invalid/api/test-suites/root/report?scope=initial");
  assert.equal(await result, true);
});

for (const failure of ["network", "status", "type", "missing", "read", "write", "close"]) {
  test(`export ${failure} failure surfaces and aborts writable`, async () => {
    let aborted = false, cancelled = false;
    const writable = new WritableStream({
      write() { if (failure === "write") throw new Error("write"); },
      close() { if (failure === "close") throw new Error("close"); },
      abort() { aborted = true; },
    });
    const download = helper({ showSaveFilePicker: async () => ({ createWritable: async () => writable }) });
    await assert.rejects(download(async () => {
      if (failure === "network") throw new Error("network");
      if (failure === "missing") return html(null);
      const stream = new ReadableStream({ pull(controller) { if (failure === "read") controller.error(new Error("read")); else { controller.enqueue(new Uint8Array([65])); if (failure !== "write") controller.close(); } }, cancel() { cancelled = true; } });
      return new Response(stream, { status: failure === "status" ? 503 : 200, headers: { "content-type": failure === "type" ? "application/json" : "text/html" } });
    }, "/report", "one.html"));
    // An errored sink is already discarded by the Streams implementation.
    if (!["write", "close"].includes(failure)) assert.equal(aborted, true);
    if (["status", "type", "write"].includes(failure)) assert.equal(cancelled, true);
  });
}

test("createWritable failure does not request report", async () => {
  await assert.rejects(helper({ showSaveFilePicker: async () => ({ createWritable: async () => { throw new Error("disk"); } }) })(() => assert.fail("no request"), "/report", "one.html"), /disk/);
});

const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test("real bytes advance a single bar; 100 waits for disk close", async () => {
  let now = 0, body;
  const states = [], closed = deferred(), saving = deferred(), received = deferred();
  const download = helper({ showSaveFilePicker: async () => ({ createWritable: async () => new WritableStream({ close() { saving.resolve(); return closed.promise; } }) }) });
  const result = download(async () => {
    now = 5000;
    return new Response(new ReadableStream({ start(controller) { body = controller; } }), { headers: { "content-type": "text/html", "content-length": "10" } });
  }, "/report", "one.html", { now: () => now, onProgress(state) { states.push(state); if (state.bytes === 4) received.resolve(); } });
  while (!body) await new Promise(resolve => setImmediate(resolve));
  assert.equal(states.find(state => state.stage === "preparing").percent, undefined);
  now = 7000; body.enqueue(new Uint8Array(4));
  await received.promise;
  const measured = states.find(state => state.bytes === 4);
  assert.equal(measured.percent, 93.6); assert.ok(!("elapsedMs" in measured)); assert.ok(!("etaSeconds" in measured));
  now = 8000; body.enqueue(new Uint8Array(6)); body.close();
  await saving.promise;
  assert.equal(states.at(-1).stage, "saving");
  assert.equal(states.some(state => state.stage === "complete" || state.percent === 100), false);
  closed.resolve(); assert.equal(await result, true);
  assert.equal(states.at(-1).stage, "complete");
  assert.equal(states.at(-1).percent, 100);
});

for (const headers of [{}, { "content-length": "NaN" }, { "content-length": "2", "content-encoding": "gzip" }]) {
  test(`unknown/encoded length uses completed preparation, never byte ratio or ETA: ${JSON.stringify(headers)}`, async () => {
    const states = [];
    await helper({ showSaveFilePicker: async () => ({ createWritable: async () => new WritableStream() }) })(async () => new Response("abc", { headers: { "content-type": "text/html", ...headers } }), "/report", "one.html", { onProgress: state => states.push(state) });
    assert.ok(states.filter(state => state.stage === "downloading").every(state => state.percent === 90));
    assert.ok(states.every(state => !("elapsedMs" in state) && !("etaSeconds" in state)));
    assert.equal(states.at(-1).percent, 100);
  });
}

for (const phase of ["picker", "preparing", "downloading"]) {
  test(`abort during ${phase} stops work without completion`, async () => {
    const controller = new AbortController(), reached = deferred(), choice = deferred();
    let aborted = false, cancelled = false, fetched = false;
    const states = [];
    const writable = new WritableStream({ abort() { aborted = true; } });
    const download = helper({ showSaveFilePicker: () => phase === "picker" ? choice.promise : Promise.resolve({ createWritable: async () => writable }) });
    const result = download(signal => {
      fetched = true;
      assert.equal(signal, controller.signal);
      if (phase === "preparing") return new Promise((_, reject) => { signal.addEventListener("abort", () => reject(signal.reason), { once: true }); reached.resolve(); });
      return Promise.resolve(html(new ReadableStream({ cancel() { cancelled = true; } })));
    }, "/report", "one.html", { signal: controller.signal, onProgress(state) { states.push(state); if (state.stage === phase && phase !== "preparing") reached.resolve(); } });
    await reached.promise; controller.abort();
    if (phase === "picker") choice.resolve({ createWritable: () => assert.fail("cancelled picker must not create file") });
    assert.equal(await result, false);
    assert.equal(states.at(-1).stage, "cancelled");
    assert.equal(states.some(state => state.stage === "complete" || state.percent === 100), false);
    if (phase === "picker") assert.equal(fetched, false); else assert.equal(aborted, true);
    if (phase === "downloading") assert.equal(cancelled, true);
  });
}

test("native fallback reports handoff, not saved or measurable progress", async () => {
  const states = [], link = { click() {}, remove() {} };
  await helper({}, { createElement: () => link, body: { appendChild() {} } })(() => assert.fail("no fetch"), "/report", "one.html", { onProgress: state => states.push(state) });
  assert.deepEqual(states.map(state => state.stage), ["browser"]);
  assert.equal(states[0].percent, undefined);
});

test("preparation polling advances only completed CT units and stops before transfer", async () => {
  const loading = deferred(), measured = deferred(), states = [];
  let reads = 0;
  const result = helper({ showSaveFilePicker: async () => ({ createWritable: async () => new WritableStream() }) })(() => loading.promise, "/report", "one.html", {
    async readPreparation() { reads++; return { stage: "rendering", completed: 2, total: 4 }; },
    onProgress(state) { states.push(state); if (state.percent === 45) measured.resolve(); },
  });
  await measured.promise;
  assert.equal(states.find(state => state.stage === "preparing").percent, undefined);
  assert.equal(states.at(-1).percent, 45);
  assert.equal(reads, 1);
  loading.resolve(html("abc"));
  await result;
  const percentages = states.map(state => state.percent).filter(value => value !== undefined);
  assert.deepEqual(percentages, [...percentages].sort((a, b) => a - b));
  assert.equal(states.at(-1).percent, 100);
});

test("cancel aborts preparation telemetry and never reports 100", async () => {
  const controller = new AbortController(), reached = deferred(), states = [];
  let cancelled = false;
  const result = helper({ showSaveFilePicker: async () => ({ createWritable: async () => new WritableStream() }) })(signal => new Promise((_, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true })), "/report", "one.html", {
    signal: controller.signal,
    readPreparation: async () => { reached.resolve(); return { stage: "rendering", completed: 1, total: 2 }; },
    cancelPreparation: async () => { cancelled = true; },
    onProgress: state => states.push(state),
  });
  await reached.promise;
  controller.abort();
  assert.equal(await result, false);
  assert.equal(cancelled, true);
  assert.ok(states.every(state => state.percent !== 100));
});
