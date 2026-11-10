"""Explicit portable reports; never interpret presentation HTML or local paths."""
from __future__ import annotations

import codecs
import json
import math
import re
import uuid
from copy import deepcopy
from html.parser import HTMLParser
from urllib.parse import urlsplit

from autojev.domain.models import utc_now
from autojev.services.rerun_recipe import _public_url, validate_rerun_recipe
from autojev.services.rerun_journey import _recipe, validate_rerun_journey

FORMAT = "autojev-report"
METADATA_ID = "autojev-portable-report"
LEGACY = "HTML antigo é apenas apresentação e não contém receitas executáveis. Baixe um novo relatório HTML na instalação original."
FIELDS = {"id", "title", "description", "preconditions", "steps", "expected_result", "actual_result", "status", "url", "assertions", "timeline", "recipe", "journey"}
RECIPE_FIELDS = {"version", "enabled", "origin", "before_url", "before_effect_digest", "before_state_digest", "target_fingerprint", "decision", "state_inputs", "journey_capture"}


def public_url(value):
    _public_url(value, ())
    parsed = urlsplit(value)
    if parsed.query or parsed.fragment:
        raise ValueError("Remova parâmetros e fragmentos privados das URLs antes de compartilhar.")
    return value


def bounded(value, depth=0, budget=None, parent=None):
    budget = budget if budget is not None else [100000]
    budget[0] -= 1
    if depth > 20 or budget[0] < 0:
        raise ValueError("Pacote muito complexo.")
    if isinstance(value, str):
        if len(value) > 8000 or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("Texto inválido ou muito longo.")
        if re.search(r"(?:file:|data:|javascript:|https?://[^\s/]*@|bearer\s+|gh[pousr]_|sk-[A-Za-z0-9]{16})", value, re.I):
            raise ValueError("Conteúdo privado ou referência não permitida.")
    elif isinstance(value, dict):
        if len(value) > 1000:
            raise ValueError("Objeto muito grande.")
        for key, item in value.items():
            if not isinstance(key, str) or (re.search(r"password|senha|api_key|authorization|cookie|storage_state|credentials|secret", key, re.I) and not (parent == "fields" and set(value) == {"username", "password", "submit"} and key == "password" and isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item))):
                raise ValueError("Credenciais não são aceitas em pacotes.")
            bounded(key, depth + 1, budget)
            bounded(item, depth + 1, budget, parent=key)
    elif isinstance(value, list):
        if len(value) > 1000:
            raise ValueError("Lista muito grande.")
        for item in value:
            bounded(item, depth + 1, budget)
    elif value is not None and type(value) not in (bool, int, float):
        raise ValueError("Tipo inválido.")
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("Número inválido.")


def validate(package):
    bounded(package)
    if not isinstance(package, dict) or set(package) != {"format", "version", "app_url", "cases"} or package["format"] != FORMAT or type(package["version"]) is not int or package["version"] != 1:
        raise ValueError("Formato de pacote AutoJev não suportado.")
    url = public_url(package["app_url"])
    cases = package["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 500:
        raise ValueError("Use de 1 a 500 testes por pacote.")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != FIELDS:
            raise ValueError("Campos de teste inválidos.")
        identity = case["id"]
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", identity) or identity in ids:
            raise ValueError("Identificação de teste inválida ou repetida.")
        ids.add(identity)
        for key in ("title", "description", "expected_result", "actual_result"):
            if not isinstance(case[key], str) or len(case[key]) > 4000:
                raise ValueError("Descrição de teste inválida.")
        for key in ("steps", "preconditions"):
            if not isinstance(case[key], list) or len(case[key]) > 100 or any(not isinstance(v, str) or len(v) > 2000 for v in case[key]):
                raise ValueError("Passos inválidos.")
        public_url(case["url"])
        if case["status"] not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
            raise ValueError("Resultado inválido.")
        if not isinstance(case["assertions"], list) or len(case["assertions"]) > 100:
            raise ValueError("Conferências inválidas.")
        for check in case["assertions"]:
            if not isinstance(check, dict) or set(check) - {"kind", "expected", "actual", "passed", "message"} or not isinstance(check.get("kind"), str) or ("passed" in check and type(check["passed"]) is not bool):
                raise ValueError("Conferência inválida.")
        if not isinstance(case["timeline"], list) or len(case["timeline"]) > 500:
            raise ValueError("Evidências inválidas.")
        for step in case["timeline"]:
            if not isinstance(step, dict) or set(step) != {"kind", "title"} or step["kind"] not in {"action", "assertion", "observation"} or not isinstance(step["title"], str):
                raise ValueError("Evidência não portátil: imagens e caminhos locais não são aceitos.")
        if case["recipe"] is not None:
            if not isinstance(case["recipe"], dict) or set(case["recipe"]) - RECIPE_FIELDS:
                raise ValueError("Metadados de receita não permitidos.")
            validate_rerun_recipe(case["recipe"], url)
        if case["journey"] is not None:
            validate_rerun_journey(case["journey"], url)
            journey = case["journey"]
            if set(journey) - {"version", "action_step", "entry_url", "steps", "checks", "complete", "gaps", "setup", "authentications", "required_inputs"}:
                raise ValueError("Metadados de jornada não permitidos.")
            for step in journey["steps"]:
                if set(step) - {"step_number", "recipe", "outcome"} or set(step["recipe"]) - RECIPE_FIELDS:
                    raise ValueError("Metadados de etapa não permitidos.")
    return package


class MetadataParser(HTMLParser):
    """Extract only the known nonexecuting block, ignoring all presentation."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.found = self.active = self.complete = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if any(k == "id" and v == METADATA_ID for k, v in attrs):
            if self.found or tag != "script" or len(dict(attrs)) != len(attrs) or dict(attrs) != {"id": METADATA_ID, "type": "application/json"}:
                raise ValueError("Metadados HTML inválidos ou repetidos.")
            self.found = self.active = True

    def handle_startendtag(self, tag, attrs):
        if any(k == "id" and v == METADATA_ID for k, v in attrs):
            raise ValueError("Metadados HTML incompletos.")

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.active = False
            self.complete = True


def embedded(package):
    validate(package)
    data = json.dumps(package, ensure_ascii=True, separators=(",", ":"))
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script id="{METADATA_ID}" type="application/json">{data}</script>'


class ReportDecoder:
    """Discard presentation incrementally; retain only the inert JSON block.

    HTMLParser buffers whole attributes, comments and raw script bodies. The
    outer tokenizer discards those bodies and compacts long attribute tokens
    before passing complete tags to the strict metadata parser.
    """
    def __init__(self):
        self.decoder = codecs.getincrementaldecoder("utf-8-sig")()
        self.parser = MetadataParser()
        self.mode = "data"
        self.pending = ""
        self.tag = ""
        self.token_length = 0
        self.quote = None
        self.compacted = False

    def feed(self, data):
        try:
            for offset in range(0, len(data), 16384):
                self._feed(self.decoder.decode(data[offset:offset + 16384]))
        except UnicodeError as exc:
            raise ValueError("Relatório AutoJev inválido.") from exc

    def _feed(self, text):
        text = self.pending + text
        self.pending = ""
        while text:
            if self.mode in {"script", "style", "comment"}:
                pattern = r"-->" if self.mode == "comment" else rf"</{self.mode}\s*>"
                match = re.search(pattern, text, re.I)
                if match:
                    if self.parser.active:
                        self.parser.handle_data(text[:match.start()])
                        self.parser.handle_endtag("script")
                    self.parser.clear_cdata_mode()
                    text = text[match.end():]
                    self.mode = "data"
                    continue
                # Retain only a potential split closing delimiter. Excessive
                # closing-tag whitespace is markup complexity, not file size.
                start = text.rfind("<")
                if self.mode == "comment":
                    start = len(text) - 2 if text.endswith("--") else len(text) - 1 if text.endswith("-") else -1
                if start >= 0 and len(text) - start <= 256:
                    self.pending, text = text[start:], text[:start]
                if self.parser.active:
                    self.parser.handle_data(text)
                return
            if self.mode == "data":
                start = text.find("<")
                if start < 0:
                    return
                text = text[start:]
                if len(text) < 4 and "<!--".startswith(text):
                    self.pending = text
                    return
                if text.startswith("<!--"):
                    self.mode, text = "comment", text[4:]
                    continue
                self.mode, self.tag, self.token_length = "tag", "<", 0
                self.quote = None
                self.compacted = False
                text = text[1:]
                if not text:
                    return
            # Runs avoid per-character scanning of megabytes of image data.
            match = re.match(r"[^\s\"'<>]+|\s+|.", text, re.S)
            token = match.group()
            text = text[len(token):]
            if token == ">" and self.quote is None:
                tag = self.tag + ">"
                found = self.parser.found
                self.parser.feed(tag)
                if self.compacted and self.parser.found != found:
                    raise ValueError("Metadados HTML inválidos.")
                self.parser.close()
                if len(self.parser.rawdata) > 16384:
                    raise ValueError("Marcação HTML muito complexa.")
                opening = re.match(r"<\s*(script|style)(?:\s|>)", tag, re.I)
                self.mode = opening.group(1).lower() if opening else "data"
                continue
            if token in {"\"", "'"}:
                if self.quote == token:
                    self.quote = None
                elif self.quote is None:
                    self.quote = token
                self.token_length = 0
                self.tag += token
            elif self.quote is None and token.isspace():
                self.tag += " "
                self.token_length = 0
            else:
                remaining = max(0, 256 - self.token_length)
                self.compacted |= len(token) > remaining
                self.tag += token[:remaining]
                self.token_length += len(token)
            if len(self.tag) > 16384:
                raise ValueError("Marcação HTML muito complexa.")

    def finish(self):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Campos JSON repetidos.")
                result[key] = value
            return result
        try:
            self._feed(self.decoder.decode(b"", final=True))
            if not self.parser.found:
                raise ValueError(LEGACY)
            if not self.parser.complete:
                raise ValueError("Metadados HTML incompletos.")
            return validate(json.loads("".join(self.parser.parts), object_pairs_hook=unique))
        except (UnicodeError, RecursionError, TypeError, KeyError) as exc:
            raise ValueError("Relatório AutoJev inválido.") from exc


def decode(data):
    decoder = ReportDecoder()
    decoder.feed(data)
    return decoder.finish()


def export_package(suites, snapshot):
    """Use exactly the displayed selection and its durable recipe owners."""
    catalog = snapshot
    cases = []
    for item in catalog["scenarios"]:
        revision = item.get("execution_revision") or {}
        source_id, source_case = (snapshot.get("_portable_sources") or {}).get(item["id"], (
            revision.get("exploration_id") or item.get("evidence_exploration_id") or snapshot["id"],
            revision.get("scenario_id") or item["id"]))
        original, action, recipe = item, None, None
        if source_id is not None:
            _, original, action, recipe = suites.reruns._source(source_id, source_case)
        evidence = original.get("evidence") or {}
        visible_evidence = item.get("evidence") or {}
        journey = deepcopy(evidence.get("rerun_journey"))
        had_journey = journey is not None
        try:
            if journey is not None:
                validate_rerun_journey(journey, catalog["app_url"])
                for step in journey["steps"]:
                    step["recipe"] = _recipe(step["recipe"], catalog["app_url"])
        except ValueError:
            journey = None
        try:
            recipe = _recipe(recipe, catalog["app_url"])
        except (ValueError, TypeError):
            recipe = None
        if had_journey and journey is None:
            # Never downgrade an incomplete multi-step replay to a single action.
            recipe = None
        case = {key: deepcopy(item[key]) for key in ("id", "title", "description", "preconditions", "steps", "expected_result", "url")}
        case.update(actual_result=item.get("actual_result") or "", status=item.get("status") or "SKIPPED",
                    assertions=[{key: deepcopy(check[key]) for key in ("kind", "expected", "actual", "passed", "message") if key in check} for check in visible_evidence.get("assertions", [])],
                    timeline=[{"kind": step.get("kind", "observation"), "title": step.get("title", "Registro")} for step in visible_evidence.get("timeline", [])], recipe=recipe, journey=journey)
        cases.append(case)
    package = validate(dict(format=FORMAT, version=1, app_url=catalog["app_url"], cases=cases))
    return package


def persist(repository, package):
    """Commit the entire validated suite atomically; no browser or filesystem import."""
    validate(package)
    identity, now = str(uuid.uuid4()), utc_now()
    with repository._lock, repository._connect() as connection:
        connection.execute("""INSERT INTO explorations (id, app_url, agent_provider_id, agent_model_id,
            auxiliary_provider_id, auxiliary_model_id, options_json, status, created_at, finished_at)
            VALUES (?, ?, '', '', '', '', '{}', 'completed', ?, ?)""", (identity, package["app_url"], now, now))
        for index, case in enumerate(package["cases"], 1):
            evidence = dict(assertions=case["assertions"], timeline=case["timeline"], action_step=index)
            if case["journey"] is not None:
                evidence["rerun_journey"] = case["journey"]
            stored = {key: case[key] for key in ("id", "title", "description", "preconditions", "steps", "expected_result", "actual_result", "status", "url")}
            stored.update(created_at=now, screenshots=[], evidence=evidence)
            connection.execute("INSERT INTO scenarios (id, exploration_id, sequence, data_json, created_at) VALUES (?, ?, ?, ?, ?)", (case["id"], identity, index, json.dumps(stored), now))
            if case["recipe"] is not None:
                result = {"before_url": case["recipe"]["before_url"], "after_url": case["url"], "evidence": {"rerun_recipe": case["recipe"]}}
                connection.execute("INSERT INTO actions (exploration_id, step_number, decision_json, result_json, created_at) VALUES (?, ?, ?, ?, ?)", (identity, index, json.dumps(case["recipe"]["decision"]), json.dumps(result), now))
    return {"id": identity, "count": len(package["cases"]), "message": "Base importada. Revise as receitas e confirme antes de executar; dados privados devem ser informados nesta instalação."}
