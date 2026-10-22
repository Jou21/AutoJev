import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { transpileModule, ModuleKind, ScriptTarget } from "typescript";

const source = await readFile(new URL("../app/components/suite-planning-chat.ts", import.meta.url), "utf8");
const compiled = transpileModule(source, { compilerOptions: { module: ModuleKind.CommonJS, target: ScriptTarget.ES2022 } });
const compiledModule = { exports: {} };
new Function("module", "exports", compiled.outputText)(compiledModule, compiledModule.exports);
const { directCaseAddition, caseAdditionReply } = compiledModule.exports;
const known = ["CT001", "CT002", "CT003", "CT004"];

test("direct additions resolve exact catalog IDs, casing and zero padding without using a model", () => {
  for (const message of ["adicione o ct004 também na reexecução", "Por favor, adicione CT004.", "Quero que você inclua o CT004 no rascunho", "Pode adicionar CT4?", "selecione ct-004", "inclua CT 004 na rodada, por favor"]) {
    assert.deepEqual(directCaseAddition(message, known), { ids: ["CT004"], missing: [] }, message);
  }
  assert.deepEqual(directCaseAddition("adicione CT001, CT004 e ct004 na rodada", known), { ids: ["CT001", "CT004"], missing: [] });
});

test("negative, conditional, quoted and suggestion requests are never treated as direct consent", () => {
  for (const message of ["não adicione CT004", "sugira CT004", "o que acha de adicionar CT004?", "Quais CTs você sugere?", '"adicione CT004"', "adicione CT004 se estiver aprovado", "adicione CT004 e execute agora", "adicione CT004 ou CT002", "adicione CT001 até CT004", "adicione CT001 a CT004", "adicione CT001-CT004", "adicione apenas CT004", "remova CT004", "adicione CT004 e não CT003", "adicione os CTs que falharam"]) {
    assert.equal(directCaseAddition(message, known), null, message);
  }
});

test("unknown or ambiguous case identities are reported without inventing a catalog entry", () => {
  assert.deepEqual(directCaseAddition("adicione CT004 e CT999", known), { ids: ["CT004"], missing: ["CT999"] });
  assert.deepEqual(directCaseAddition("adicione CT4", ["CT004", "CT4"]), { ids: [], missing: ["CT4"] });
});

test("confirmations distinguish actual additions from cases already selected and never claim execution", () => {
  assert.match(caseAdditionReply(["CT004"], []), /CT004 foi adicionado ao rascunho/);
  assert.match(caseAdditionReply([], ["CT004"]), /CT004 já estava selecionado/);
  assert.doesNotMatch(caseAdditionReply([], ["CT004"]), /foi adicionado/);
  assert.match(caseAdditionReply(["CT001", "CT004"], ["CT002"]), /CT001, CT004 foram adicionados/);
  assert.match(caseAdditionReply(["CT004"], []), /Nada foi executado/);
});
