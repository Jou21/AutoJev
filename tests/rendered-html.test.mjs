import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the AutoJev application shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>AutoJev — Exploração inteligente de aplicações<\/title>/i);
  assert.match(html, /AutoJev/);
  assert.match(html, /JEV no comando|Boa exploração começa/);
  assert.match(html, /Nova exploração/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("removes disposable starter assets and metadata", async () => {
  const [page, layout, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /AutoJevApp/);
  assert.match(layout, /pt-BR/);
  assert.match(layout, /AutoJev/);
  assert.doesNotMatch(layout, /codex-preview|Starter Project/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(packageJson, /lucide-react/);
  await assert.rejects(access(new URL("app/_sites-preview", root)));
});

test("offers an optional focused exploration scope", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /useState<"all" \| "focused">\("all"\)/);
  assert.match(source, /Tela, menu ou submenu/);
  assert.match(source, /Toda a aplicação/);
  assert.match(source, /Tela\/menu específico/);
  assert.match(source, /Configurações &gt; Usuários &gt; Permissões|Configurações > Usuários > Permissões/);
  assert.match(source, /focusMode === "focused" && !focusArea\.trim\(\)/);
  assert.match(source, /if \(normalizedFocusArea\) explorationOptions\.focus_area = normalizedFocusArea/);
  assert.match(source, /explorationFocusArea\(exploration\.options\)/);
  assert.match(source, /max_steps: null/);
  assert.match(source, /max_duration_seconds: null/);
  assert.match(source, /Sem teto fixo de ações ou duração/);
  assert.match(source, /Ao esgotar a cobertura/);
});

test("shows test-case creation running alongside browser exploration", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /Explorar/);
  assert.match(source, /Criar CTs/);
  assert.match(source, /scenariosRunningInParallel/);
  assert.match(source, /parallelScenarioPipelineStarted/);
  assert.match(source, /terminalOutcomeVisible && completedWithRestrictions/);
  assert.match(source, /Exploração \+ criação contínua de CTs/);
  assert.match(source, /preservados ao vivo/);
  assert.match(source, /phase === "documenting" \|\| scenariosRunningInParallel/);
  assert.match(source, /CT \{Math\.min\(phaseProgress\.current/);
  assert.match(source, /O número 67 representa os CTs gerados/);
  assert.match(source, /Linha do tempo explicada/);
  assert.match(source, /detailTab === "evidence"/);
  assert.match(source, /coverage_complete_with_restrictions/);
  assert.match(source, /Concluída com restrições/);
  assert.match(source, /sinais técnicos/);
  assert.match(source, /O relatório final correlaciona esses sinais por observação/);
});

test("offers economical text and optional visual auxiliary evidence modes", async () => {
  const source = await readFile(new URL("../app/AutoJevApp.tsx", import.meta.url), "utf8");

  assert.match(source, /useState<"text" \| "visual">\("text"\)/);
  assert.match(source, /auxiliary_evidence_mode: auxiliaryEvidenceMode/);
  assert.match(source, /Econômico · texto/);
  assert.match(source, /Visual · texto \+ imagem/);
  assert.match(source, /imagem continua salva localmente/);
  assert.match(source, /modelAcceptsImages/);
  assert.match(source, /available\.filter\(modelAcceptsImages\)/);
  assert.match(source, /somente modelos que aceitam imagens/);
  assert.match(source, /Nenhum modelo com visão neste provedor/);
  assert.match(source, /visualAuxiliaryModelMissing/);
  assert.match(source, /supports_vision: modelAcceptsImages\(model\)/);
  assert.match(source, /modelInputCapabilitiesKnown/);
  assert.match(source, /!provider\.models\?\.some\(modelInputCapabilitiesKnown\)/);
  assert.match(source, /Compatibilidade visual dos modelos atualizada/);
  assert.match(source, /explorationEvidenceMode\(exploration\.options\)/);
});
