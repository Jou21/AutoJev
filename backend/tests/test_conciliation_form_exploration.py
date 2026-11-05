"""The requested screen is reached AND exercised: its fields are filled with
valid data and its search runs, before the run wanders off to other routes.

The fixture mirrors the real "Conciliação de Títulos Pagos em Cartório":
dd/mm/aaaa masks shown as placeholders, a disabled "Pesquisar" until both dates
are valid, a "Nova Conciliação" link to another route, a floating chip.
"""

import json
import re

from test_focus_menu_exploration import FOCUS, SISBR, run_focused

DATE = r"^\d{2}/\d{2}/\d{4}$"

CONCILIACAO_SCREEN = """
 <a id="nova" href="/gcar/nova-conciliacao" class="btn">add Nova Conciliação</a>
 <div id="side"><h2>Conciliação de Títulos Pagos em Cartório</h2>
  <label for="uf">UF</label><select id="uf"><option value="">Todos</option><option value="SP">SP</option></select>
  <label for="ini">Data de início*</label><input id="ini" placeholder="01/01/2026">
  <label for="fim">Data de término*</label><input id="fim" placeholder="01/01/2026">
  <label for="cnpj">CNPJ</label><input id="cnpj">
  <button id="pesq" disabled>PESQUISAR</button></div>
 <section id="resultados"><h3>Sem Conteudo</h3><p>Faça uma pesquisa para apresentar resultados.</p></section>"""

import datetime

_YEAR = datetime.date.today().year
DATA = {f"03/{_YEAR}": 3, f"08/{_YEAR}": 5}      # the only months with records

PAGE = SISBR.replace(
    "screen.innerHTML = '<h1>' + name + '</h1>' + (name.startsWith('Concilia')",
    "screen.innerHTML = '<h1>' + name + '</h1>' + (name.startsWith('Concilia') ? CONCILIACAO : '');"
    "if (name.startsWith('Concilia')) wireForm(); void (name.startsWith('Concilia')",
).replace(
    "<script>",
    "<script>const CONCILIACAO = " + json.dumps(CONCILIACAO_SCREEN) + """;
 window.searches = 0;
""" + "const DATA = " + json.dumps(DATA) + """;
 function wireForm() {
   const ini = document.getElementById('ini'), fim = document.getElementById('fim'), button = document.getElementById('pesq');
   const valid = () => [ini, fim].every(i => /^\\d{2}\\/\\d{2}\\/\\d{4}$/.test(i.value));
   const sync = () => { button.disabled = !valid(); };
   ini.addEventListener('change', sync); fim.addEventListener('change', sync);   // only when the field is left
   button.addEventListener('click', () => {
     window.searches += 1;
     const cnpj = document.getElementById('cnpj').value;
     if (cnpj && !new RegExp('^[0-9./-]+$').test(cnpj)) {
       document.getElementById('resultados').innerHTML = '<p role="alert">CNPJ inválido</p>';
       return;
     }
     const month = ini.value.slice(3, 5) + '/' + ini.value.slice(6);
     const rows = DATA[month] || 0;
     const overlay = document.createElement('div');            // dims everything, says nothing
     overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:50';
     document.body.append(overlay);
     setTimeout(() => {
       overlay.remove();
       document.getElementById('resultados').innerHTML = rows
         ? '<h3>Resultados</h3><table><tbody>' + Array.from({length: rows}, (_, i) =>
             '<tr><td>Título ' + (i + 1) + '</td><td><a href="#t' + i + '">Detalhes</a></td></tr>').join('') + '</tbody></table>'
         : '<h3>Sem Conteudo</h3><p>Nenhum registro encontrado.</p>';
     }, 1500);
   });
 }
 document.addEventListener('click', event => {
   const link = event.target.closest('#nova');
   if (!link) return;
   event.preventDefault();
   history.pushState({}, '', '/gcar/nova-conciliacao');
   screen.innerHTML = '<h1>Nova Conciliação</h1><label for="a">Arquivo</label><input id="a" placeholder="Arquivo">';
 });
""",
    1,
)


async def test_the_search_runs_for_every_month_then_with_other_data_and_the_records_are_explored(tmp_path, monkeypatch):
    run, clicked, _ = await run_focused(tmp_path, monkeypatch, page_html=PAGE)

    events = run["events"]
    assert "coverage_scope_not_found" not in [event["type"] for event in events], clicked
    assert any(ok and "concilia" in url for _, ok, url in clicked), clicked

    # One search per month of the current year, each with its own pair of dates.
    ranges = [e["payload"] for e in events if e["type"] == "search_matrix_range" and not e["payload"].get("replay")]
    expected = [f"{m:02d}/{_YEAR}" for m in range(1, 13)] + [f"{m:02d}/{_YEAR - 1}" for m in range(1, 13)]
    assert [r["label"] for r in ranges] == expected, ranges                       # 24 months
    assert {r["label"]: r["rows"] for r in ranges if r["rows"]} == {f"03/{_YEAR}": 3, f"08/{_YEAR}": 5}
    assert not [r for r in ranges if r.get("skipped")], ranges                  # no month lost to a disabled button
    dates = {value for _, value, ok, _ in clicked.fills if ok and value and re.match(DATE, value)}
    assert {f"01/{m:02d}/{_YEAR}" for m in range(1, 13)} <= dates, sorted(dates)
    assert {f"01/{m:02d}/{_YEAR - 1}" for m in range(1, 13)} <= dates, sorted(dates)

    # The richest month is searched again, so its table is what remains on screen...
    completed = [e["payload"] for e in events if e["type"] == "search_matrix_completed"][0]
    assert completed["found_records"] and completed["best"] == f"08/{_YEAR}"

    # ...then every other field is varied, one at a time, with valid and invalid data.
    varied = [e["payload"] for e in events if e["type"] == "search_variation_result"]
    by_field = {}
    for item in varied:
        by_field.setdefault(item["field"], []).append(item)
    assert set(by_field) == {"UF", "CNPJ"}, sorted(by_field)
    assert [item["kind"] for item in by_field["CNPJ"]] == [
        "CPF válido", "CNPJ válido", "documento inválido", "curto demais", "letras",
    ]
    letters = next(item for item in by_field["CNPJ"] if item["kind"] == "letras")
    assert letters["validation_errors"] == 1 and letters["first_error"] == "CNPJ inválido" and letters["rows"] == 0
    assert all(item["rows"] == 5 for item in by_field["CNPJ"] if not item["validation_errors"]), by_field["CNPJ"]
    assert by_field["UF"][0]["kind"] == "opção SP"
    done = [e["payload"] for e in events if e["type"] == "search_variation_completed"][0]
    assert done["fields"] == 2 and done["searches"] == 6 and done["reason"] == "best_range_restored"

    # 12 months + replay of the best + 6 variations + the final restore of the best range...
    assert max(clicked.searches) == 24 + 1 + 6 + 1, clicked.searches
    # ...which leaves the best range's five rows on screen right after the last search.
    last_search = clicked.searches.index(max(clicked.searches))
    assert max(clicked.rows[last_search:last_search + 5]) == 5, (clicked.rows[last_search:], clicked.rows)
    assert len(clicked) > 24 * 3 + 6 * 3, len(clicked)
    # No separate "Tab" action and no pause for results that were not loading.
    assert not [a for a, *_ in clicked if a == "press"], "an extra action was spent leaving a field"
    assert run["status"] in {"completed", "partial"}, (run["status"], run["error"])
