"""Click-bound plain elements (Angular/Vue menus) are observed as controls."""

from urllib.parse import unquote

from autojev.adapters.playwright_executor import PlaywrightExecutor
from autojev.domain.models import ExplorationOptions

PAGE = """<!doctype html><meta charset="utf-8"><title>menu</title>
<style>li{cursor:pointer;height:60px;list-style:none}ul.side{width:130px;margin:0;padding:0}
.big{cursor:pointer;width:100%;height:700px}</style>
<header><span style="cursor:pointer">star</span> <button>real</button></header>
<ul class="side" style="cursor:pointer">
 <li><span>Liberações</span></li>
 <li><i>add</i><span>Cadastro</span></li>
 <li><span>Painel de Operações</span>
  <ul style="display:none"><li><a href="/conciliacao">Conciliação de Títulos Pagos em Cartório</a></li></ul></li>
 <li><a href="/relatorios">Relatório</a></li>
</ul>
<div class="big">a huge clickable region whose text is long enough to never be a menu entry at all, really</div>
<div style="cursor:pointer;visibility:hidden">hidden widget</div>
<script>document.querySelectorAll('li').forEach(li => li.addEventListener('click', () => {
  document.title = 'clicked ' + li.innerText.trim();
}));</script>"""


def menu_executor(tmp_path):
    class Executor(PlaywrightExecutor):
        async def _guard_navigation(self, route, request):
            if request.url.startswith("https://menu.fixture.test/"):
                await route.fulfill(content_type="text/html; charset=utf-8", body=PAGE)
            else:
                await route.abort()

    return Executor(tmp_path, ExplorationOptions(), headless=True)


async def test_click_bound_menu_items_are_controls_without_noise(tmp_path):
    executor = menu_executor(tmp_path)
    try:
        observation = await executor.start("https://menu.fixture.test/")
        names = [(element["tag"], element["name"]) for element in observation.interactive_elements]

        # Real controls first, then the plain elements the browser reports as clickable.
        assert names[:2] == [("button", "real"), ("a", "Relatório")]
        assert ("li", "Liberações") in names
        assert ("li", "Painel de Operações") in names
        # One entry per item: not the inner span/icon, the list, the huge region or hidden widgets.
        # (the collapsed submenu link inside "Painel de Operações" is hidden, not a control)
        assert sum(tag == "li" for tag, _ in names) == 3
        assert ("a", "Conciliação de Títulos Pagos em Cartório") not in names
        assert not [name for tag, name in names if tag in {"ul", "i"} or name.startswith(("a huge", "hidden"))]
        assert len(names) == len(set(names))

        painel = next(e for e in observation.interactive_elements if e["name"] == "Painel de Operações")
        assert painel["interactable"] is True
        locator = await executor._locator(painel["ref"])
        await locator.click()
        assert await executor._page.title() == "clicked Painel de Operações"
    finally:
        await executor.close()


async def test_focus_diagnostics_describe_how_the_menu_label_is_built(tmp_path):
    executor = menu_executor(tmp_path)
    try:
        await executor.start("https://menu.fixture.test/")
        found = await executor.describe_focus_candidates(
            "Painel de Operações > Conciliação de Títulos Pagos em Cartório"
        )
        painel = next(item for item in found if item["segment"] == "Painel de Operações")
        assert painel["element"] == "span"
        assert painel["cursor"] == "pointer" and painel["visible"] is True
        assert painel["ancestors"][0].startswith("li")
        hidden = next(item for item in found if item["segment"].startswith("Conciliação"))
        assert hidden["visible"] is False and hidden["closest_control"] == "a"
    finally:
        await executor.close()


SISBR_MENU = """<!doctype html><meta charset="utf-8"><title>Gestão Catorária - Sisbr 3.0</title>
<style>
 aside{width:103px;font:13px sans-serif}
 .ss-toolbar-item{display:block;width:103px;height:80px;cursor:pointer;position:relative}
 .ss-toolbar-menu{display:none;position:absolute;left:103px;top:0;width:307px;height:640px;background:#fff}
 .ss-toolbar-item:hover .ss-toolbar-menu{display:block}
 .ss-toolbar-menu-title{cursor:pointer}
 .ss-toolbar-menu-item a{display:block;height:48px;cursor:pointer}
 .chckbx{opacity:0;position:absolute}
</style>
<aside class="ss-toolbar">
 <div><div class="ss-toolbar-item"><i class="mdi"></i>Liberações
  <div class="ss-toolbar-menu"><h6 class="ss-toolbar-menu-title">Liberações</h6>
   <div><div class="ss-toolbar-menu-item"><a>Liberação de Títulos</a><input type="checkbox" class="chckbx"></div></div></div></div></div>
 <div><div class="ss-toolbar-item"><i class="mdi"></i>Painel de Operações
  <div class="ss-toolbar-menu"><h6 class="ss-toolbar-menu-title">Painel de Operações</h6>
   <div><div class="ss-toolbar-menu-item"><a>Repasse Financeiro</a><input type="checkbox" class="chckbx"></div></div>
   <div><div class="ss-toolbar-menu-item"><a>Conciliação de Títulos Pagos em Cartório</a><input type="checkbox" class="chckbx"></div></div>
   <div><div class="ss-toolbar-menu-item"><a>Resumo</a><input type="checkbox" class="chckbx"></div></div></div></div></div>
 <a class="ss-toolbar-item">Relatório</a>
</aside>
<main id="screen">home</main>
<script>
 document.querySelectorAll('.ss-toolbar-menu-item a').forEach(a => a.addEventListener('click', () => {
   const name = a.textContent.trim();
   history.pushState({}, '', '/gcar/' + encodeURIComponent(name));
   document.getElementById('screen').textContent = name;
 }));
</script>"""


async def test_hover_flyout_menu_of_a_real_sisbr_toolbar_can_be_walked_to_the_focus(tmp_path):
    class Executor(PlaywrightExecutor):
        async def _guard_navigation(self, route, request):
            if request.url.startswith("https://menu.fixture.test/"):
                await route.fulfill(content_type="text/html; charset=utf-8", body=SISBR_MENU)
            else:
                await route.abort()

    executor = Executor(tmp_path, ExplorationOptions(), headless=True)
    try:
        home = await executor.start("https://menu.fixture.test/gcar/")
        entries = [e["name"] for e in home.interactive_elements]
        # The three top-level entries are visible controls; the flyout links are not (yet).
        assert {"Liberações", "Painel de Operações", "Relatório"} <= set(entries)
        assert "Conciliação de Títulos Pagos em Cartório" not in entries

        painel = next(e for e in home.interactive_elements if e["name"] == "Painel de Operações")
        await (await executor._locator(painel["ref"])).click()

        # The pointer stays over the item, so its CSS flyout is open for the next observation.
        opened = await executor.observe("after-menu")
        flyout = next(
            (e for e in opened.interactive_elements
             if e["name"] == "Conciliação de Títulos Pagos em Cartório"),
            None,
        )
        assert flyout is not None and flyout["tag"] == "a" and flyout["interactable"] is True
        await (await executor._locator(flyout["ref"])).click()
        assert "Conciliação" in unquote(executor._page.url)
    finally:
        await executor.close()


CST_PAGE = r"""<!doctype html><meta charset="utf-8"><title>Custódia de Cheques</title>
<style>
 .ss-navbar-item{display:inline-block;width:32px;height:32px;cursor:pointer}
 .glyph-a::before{content:"\e001"} .glyph-b::before{content:"\e002"}
 .input-container{position:relative;width:368px}
 .input-container input{width:100%;height:40px}
 .icon{position:absolute;right:0;top:0;width:32px;height:40px;cursor:pointer}
 .ss-tab-links{cursor:pointer}
</style>
<nav><a class="ss-navbar-item glyph-a"></a><a class="ss-navbar-item glyph-b"></a>
 <a class="app-actionbar-back" title="Voltar" style="display:inline-block;width:20px;height:20px"></a></nav>
<div class="ss-tab"><button class="ss-tab-links active"> Lote </button><button class="ss-tab-links"> Cheque </button></div>
<sc-form-field><div class="ss-form-group"><div class="group-text"><label for="cpf">CPF/CNPJ do Cooperado</label></div>
 <div class="input-container has-icon"><input id="cpf" type="text">
  <span class="icon"><span class="sisbr-icon-fontIcon icon-md" style="display:inline-block;width:24px;height:24px;--sisbr-icon-glyph:'\e12f'"></span></span></div></div></sc-form-field>
<label for="num">Número Lote</label><input id="num" type="text">
<script>document.querySelector('.icon').addEventListener('click', () => { document.title = 'picker aberto'; });</script>"""


async def test_icon_only_controls_and_unlabeled_fields_get_usable_names(tmp_path):
    class Executor(PlaywrightExecutor):
        async def _guard_navigation(self, route, request):
            if request.url.startswith("https://cst.fixture.test/"):
                await route.fulfill(content_type="text/html; charset=utf-8", body=CST_PAGE)
            else:
                await route.abort()

    executor = Executor(tmp_path, ExplorationOptions(), headless=True)
    try:
        observation = await executor.start("https://cst.fixture.test/cst-web/lote-cheques")
        by_name = {e["name"]: e for e in observation.interactive_elements}

        # The magnifier has no text, label or title: named after the field it sits in.
        lupa = by_name['Ícone ao lado de “CPF/CNPJ do Cooperado”']
        assert lupa["tag"] == "span" and lupa["interactable"] is True
        # Header icons are told apart by their glyph, not collapsed into one name.
        glyphs = [name for name in by_name if name.startswith("Ícone e00")]
        assert sorted(glyphs) == ["Ícone e001", "Ícone e002"]
        assert "Voltar" in by_name                                 # title still wins
        # Fields answer to their <label for>, and tabs are ordinary buttons.
        assert by_name["CPF/CNPJ do Cooperado"]["tag"] == "input"
        assert by_name["Número Lote"]["tag"] == "input"
        assert {"Lote", "Cheque"} <= set(by_name)

        await (await executor._locator(lupa["ref"])).click()
        assert await executor._page.title() == "picker aberto"
    finally:
        await executor.close()
