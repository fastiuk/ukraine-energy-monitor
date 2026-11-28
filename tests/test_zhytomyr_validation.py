from datetime import date

import pytest

from conftest import spans
from ua_outages.providers import zhytomyr
from ua_outages.providers.base import SourceFormatError


def row(*, group: str = "1.1", color: str = "#ffffff", count: int = 48) -> str:
    cells = f'<td style="background: {color}"></td>' * count
    return f'<tr><td><a href="?pidcherga_id=1">{group}</a></td>{cells}</tr>'


def header(day: str, *, colspan: int | None = 48) -> str:
    attrs = f' colspan="{colspan}"' if colspan is not None else ""
    return f'<tr><td{attrs}><b>{day}</b></td></tr>'


@pytest.mark.parametrize("same_table", [False, True])
@pytest.mark.parametrize("layout_colspan", [48, 49])
def test_multiple_dates_inside_layout_tables(same_table, layout_colspan):
    boundary = "" if same_table else "</table><table>"
    first = f'<table>{header("18.10.2026")}{row(color="#ff3333")}'
    second = f'{header("19.10.2026")}{row()}</table>'
    page = f'<table><tr><td colspan="{layout_colspan}">{first}{boundary}{second}</td></tr></table>'

    schedules = zhytomyr.parse(page)

    assert list(schedules) == [date(2026, 10, 18), date(2026, 10, 19)]
    assert spans(schedules[date(2026, 10, 18)], "1.1") == [("00:00", "24:00", False)]
    assert schedules[date(2026, 10, 19)].groups == {"1.1": ()}


@pytest.mark.parametrize("same_table", [False, True])
@pytest.mark.parametrize("day", ["19.10.2026 (завтра)", "19.10.2026 / 20.10.2026", "31.11.2026", ""])
def test_unrecognized_date_header_cannot_overwrite_previous_day(day, same_table):
    boundary = "" if same_table else "</table><table>"
    page = f'<table>{header("18.10.2026")}{row(color="#ff3333")}{boundary}{header(day)}{row()}</table>'

    with pytest.raises(SourceFormatError, match="Zhytomyr: invalid date"):
        zhytomyr.parse(page)


@pytest.mark.parametrize("same_table", [False, True])
@pytest.mark.parametrize("next_header", ["", header("19.10.2026", colspan=49)])
def test_missing_or_changed_header_cannot_overwrite_previous_day(next_header, same_table):
    boundary = "" if same_table else "</table><table>"
    page = (
        f'<table>{header("18.10.2026")}{row(color="#ff3333")}'
        f"{boundary}{next_header}{row()}</table>"
    )

    reason = "invalid date header" if next_header else "duplicate group" if same_table else "schedule row has no date header"
    with pytest.raises(SourceFormatError, match=reason):
        zhytomyr.parse(page)


@pytest.mark.parametrize("day", ["19.10.2026", "19.10.2026 (завтра)", "На 19.10.2026"])
@pytest.mark.parametrize("colspan", [49, None])
def test_changed_header_cannot_assign_a_different_group_to_previous_day(day, colspan):
    page = (
        f'<table>{header("18.10.2026")}{row(group="1.1", color="#ff3333")}'
        f'{header(day, colspan=colspan)}{row(group="1.2")}</table>'
    )

    with pytest.raises(SourceFormatError, match="invalid date header"):
        zhytomyr.parse(page)


@pytest.mark.parametrize("same_table", [False, True])
@pytest.mark.parametrize("first, second", [
    (row(), row()),
    (row(), row(count=47)),
    (row(), row(color="#123456")),
    (row(count=47), row(color="#ff3333")),
])
def test_duplicate_group_is_ambiguous_even_if_one_row_is_malformed(first, second, same_table):
    boundary = "" if same_table else f'</table><table>{header("18.10.2026")}'
    page = f'<table>{header("18.10.2026")}{first}{boundary}{second}</table>'

    with pytest.raises(SourceFormatError, match="duplicate group '1.1' for 2026-10-18"):
        zhytomyr.parse(page)
