from html.parser import HTMLParser

from autojev.adapters.report_premium import _case_row


class RowMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def test_report_test_row_is_a_labelled_keyboard_target_without_losing_table_semantics():
    case = {
        "index": 8,
        "id": "CT009",
        "title": 'Escolher "Nome" <opção>',
        "status": "PASS",
        "category": "business",
        "url": "https://example.test/inventory",
        "page": "/inventory",
        "duration_seconds": 1,
        "signals": 0,
    }
    parser = RowMarkup()
    parser.feed(_case_row(case))
    row = parser.elements[0]
    assert row[0] == "tr"
    assert row[1]["data-case-index"] == "8"
    assert row[1]["data-open-case-row"] == "8"
    assert row[1]["tabindex"] == "0"
    assert row[1]["aria-label"] == 'Abrir resultado de CT009: Escolher "Nome" <opção>'
    assert "role" not in row[1], "Preserve the native row/cell table semantics"
    assert sum(tag == "td" for tag, _attrs in parser.elements) == 8
    controls = [attrs for tag, attrs in parser.elements if tag == "button"]
    assert len(controls) == 2
    assert all(attrs["data-open-case"] == "8" for attrs in controls)
    assert all(not name.startswith("on") for _tag, attrs in parser.elements for name in attrs)
