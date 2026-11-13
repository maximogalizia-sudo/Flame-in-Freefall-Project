"""Unit tests for the parsing rules that the ETL requirements depend on (R4, R5, R6)."""
from pipeline.ingest_tables import parse_header, parse_value
from pipeline.transform import _convert, parse_sequence


def test_numbers_units_and_approx():
    assert parse_value("30 cm/s")["value_num"] == 30 and parse_value("30 cm/s")["value_unit"] == "cm/s"
    v = parse_value("~ 21.5")
    assert v["value_num"] == 21.5 and v["is_approx"] and v["flag"] == "approx"


def test_ranges_lists_and_ids():
    assert parse_value("1.5 - 5")["kind"] == "range"
    assert parse_value("30%-40%")["kind"] == "range"
    assert parse_value("2-1")["kind"] == "text"          # SAFFIRE-II sample id, not a range
    assert parse_value("0.17; 0.21;0.25; 0.3")["kind"] == "list"


def test_r4_special_values():
    assert parse_value("RAMP")["flag"] == "ramped"
    assert parse_value("#VALUE!")["flag"] == "excel_error"
    assert parse_value("#DIV/0!")["flag"] == "excel_error"
    v = parse_value("OR")
    assert (v["flag"], v["censored"], v["censor_limit"]) == ("censored", ">", 1000.0)
    v = parse_value(">500")
    assert (v["censored"], v["censor_limit"]) == (">", 500.0)
    assert parse_value("–")["kind"] == "null"
    assert parse_value("bad �")["flag"] == "encoding_loss"


def test_headers():
    assert parse_header("mfuel (mg/s)", "PSI-10") == ("mfuel", "mg/s", None)
    assert parse_header("XO2 (init)", "PSI-10")[0] == "XO2 init"
    assert parse_header("O initial ambient composition; mole fraction", "PSI-69")[0].startswith("O2")
    assert parse_header("Burning rate; mm", "PSI-69")[1] == "mm2/s"


def test_r5_conversions():
    assert round(_convert(760, "mmHg", "kPa"), 2) == 101.32
    assert _convert(1, "atm", "kPa") == 101.325
    assert _convert(21, "%", "mol/mol") == 0.21


def test_r6_sequences():
    steps, event = parse_sequence("5 3 2 1 0.5 lower")
    assert [s["flow_cm_s"] for s in steps] == [5, 3, 2, 1, 0.5] and event is None
    steps, event = parse_sequence("5 down to ext")
    assert [s["flow_cm_s"] for s in steps] == [5] and event == "extinction"
    steps, event = parse_sequence("5 2.5 (1.15 .5 .35)")
    assert [s["flow_cm_s"] for s in steps] == [5, 2.5]       # parenthesised pot readings are not flows
    assert parse_sequence("10 blowoff")[1] == "blowoff"
    assert parse_sequence("no ign at any flow")[1] == "no_ignition"
