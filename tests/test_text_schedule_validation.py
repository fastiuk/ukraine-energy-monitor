from datetime import date

import pytest

from conftest import fixture_json, fixture_text, kyiv, spans
from ua_outages.providers import kirovohrad, loe, zaporizhzhia
from ua_outages.providers._text import parse_group_text


DAY = date(2026, 10, 17)


def loe_sentence(sentence):
    payload = fixture_json("loe_menu.json")
    item = payload["hydra:member"][0]["menuItems"][0]
    item["rawHtml"] = item["rawHtml"].replace(
        "Група 4.2. Електроенергії немає з 10:00 до 12:00, з 21:00 до 22:30.",
        f"Група 4.2. {sentence}",
    )
    return loe.parse_menu(payload)[DAY]


@pytest.mark.parametrize(
    "sentence",
    [
        "Графік відключень не визначено.",
        "Час відключень невідомий.",
        "Електроенергії немає з 10:00 до 12:00, з 21:00 22:30.",
        "Електроенергії немає з 10:00 до 12:00, з 21:00.",
    ],
)
def test_loe_unknown_or_partial_schedule_is_not_power_on(sentence):
    schedule = loe_sentence(sentence)
    assert "4.2" not in schedule.groups
    assert schedule.unknown["4.2"] == f"Група 4.2. {sentence}"
    assert spans(schedule, "4.1") == [("20:00", "22:30", False)]


@pytest.mark.parametrize(
    "sentence",
    ["Електроенергія є.", "Відключень немає.", "Відключень не буде.", "Відключень не заплановано."],
)
def test_loe_explicit_no_outages_is_still_known(sentence):
    schedule = loe_sentence(sentence)
    assert schedule.groups["4.2"] == ()
    assert "4.2" not in schedule.unknown


@pytest.mark.parametrize(
    "prefix",
    [
        "черга 1.1:", "ЧЕРГА 1.1:", "Черга 1.1 -", "Черга 1.1 –", "Черга 1.1 —",
        "⚡ Черга 1.1:", "Черга 1/1:", "⚡️ Черга 1/1:", "  • Черга 1.1:",
    ],
)
def test_kirovohrad_group_case_and_separators(prefix):
    page = fixture_text("kirovohrad_tg.html").replace("Черга 1.1:", prefix)
    schedule = kirovohrad.parse_channel(page)[DAY]
    assert spans(schedule, "1.1") == [("12:00", "14:00", False), ("20:00", "22:00", False)]
    assert "1.1" not in schedule.unknown


@pytest.mark.parametrize("body", ["=> 12:00-14:00", ": " + "12:00-14:00, " * 30])
def test_kirovohrad_unreadable_group_row_is_not_omitted(body):
    page = fixture_text("kirovohrad_tg.html").replace(
        "Черга 1.1: 12:00-14:00, 20:00-22:00", f"Черга 1.1 {body}",
    )
    schedule = kirovohrad.parse_channel(page)[DAY]
    assert "1.1" not in schedule.groups
    assert schedule.unknown["1.1"] == kirovohrad.UNRECOGNISED
    assert spans(schedule, "1.2") == [("16:00", "18:00", False), ("20:00", "22:00", False)]


def test_kirovohrad_no_recognised_rows_is_not_an_empty_schedule():
    schedule = kirovohrad.parse_post(
        ['За розпорядженням НЕК "Укренерго" 17.10.2026 буде діяти наступний графік:', "Формат змінено"],
        kyiv(DAY, 9),
    )
    assert schedule.groups == {}
    assert schedule.unknown == dict.fromkeys(kirovohrad.GROUPS, kirovohrad.UNRECOGNISED)


@pytest.mark.parametrize("hours", ["10:30 – 13:30, 17:00 18:30", "10:30 – 13:30, 17:00"])
def test_zaporizhzhia_partial_intervals_stay_unknown(hours):
    page = fixture_text("zaporizhzhia.html").replace("1.1: 10:30 – 13:30, 17:00 – 18:30", f"1.1: {hours}", 1)
    schedule = zaporizhzhia.parse(page, DAY)[DAY]
    assert "1.1" not in schedule.groups
    assert schedule.unknown["1.1"] == f"1.1: {hours}"
    assert spans(schedule, "1.2") == [("09:30", "13:30", False)]


@pytest.mark.parametrize("sentence", ["Графік відключень не визначено.", "Час відключень невідомий."])
def test_zaporizhzhia_unknown_schedule_is_not_power_on(sentence):
    page = fixture_text("zaporizhzhia.html").replace(
        "1.1: 10:30 – 13:30, 17:00 – 18:30", f"1.1: {sentence}", 1,
    )
    schedule = zaporizhzhia.parse(page, DAY)[DAY]
    assert "1.1" not in schedule.groups
    assert schedule.unknown["1.1"] == f"1.1: {sentence}"
    assert spans(schedule, "1.2") == [("09:30", "13:30", False)]


@pytest.mark.parametrize("strict", [False, True])
def test_text_no_outages_requires_an_explicit_statement(strict):
    groups, unknown = parse_group_text(
        DAY,
        "1.1: Графік відключень не визначено.\n"
        "1.2: Час відключень невідомий.\n"
        "2.1: Відключень немає.\n"
        "2.2: Відключень не буде.\n"
        "3.1: Відключень не заплановано.\n"
        "3.2: Відключень не передбачено.\n"
        "4.1: Відключень не планується.\n"
        "4.2: Світло є.\n"
        "5.1: Електроенергія є.\n"
        "5.2: Без відключень.\n"
        "6.1: Не відключатимуть.",
        strict=strict,
    )
    assert sorted(unknown) == ["1.1", "1.2"]
    assert groups == dict.fromkeys(["2.1", "2.2", "3.1", "3.2", "4.1", "4.2", "5.1", "5.2", "6.1"], ())
