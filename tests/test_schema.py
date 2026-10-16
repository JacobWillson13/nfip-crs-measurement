from datetime import UTC, datetime

import pytest

from pipeline.ingest.schema import NFIP_CLAIMS_FIELDS, SchemaDriftError, check_fields_present, to_arrow


def test_round_trips_real_records_with_pinned_types(records):
    table = to_arrow(records, NFIP_CLAIMS_FIELDS)
    assert table.num_rows == len(records)
    assert table.schema.field("crsClassCode").type == "int64"
    # Community numbers keep leading zeros because they are strings.
    assert table.schema.field("nfipRatedCommunityNumber").type == "string"


def test_sentinel_dates_survive_parsing(records):
    table = to_arrow(records, NFIP_CLAIMS_FIELDS)
    years = {d.year for d in table.column("originalConstructionDate").to_pylist() if d}
    assert 1492 in years


def test_all_null_page_keeps_declared_type():
    rows = [{"id": 1, "crsClassCode": None}, {"id": 2, "crsClassCode": None}]
    table = to_arrow(rows, {"id": "int64", "crsClassCode": "int64"})
    assert table.schema.field("crsClassCode").type == "int64"


def test_type_drift_names_the_field():
    with pytest.raises(SchemaDriftError, match="crsClassCode"):
        to_arrow([{"id": 1, "crsClassCode": "Class 7"}], {"id": "int64", "crsClassCode": "int64"})


def test_missing_field_is_detected():
    with pytest.raises(SchemaDriftError, match="crsClassCode"):
        check_fields_present([{"id": 1}], {"id": "int64", "crsClassCode": "int64"})


def test_timestamps_are_utc():
    t = to_arrow([{"d": "2017-08-26T00:00:00.000Z"}], {"d": "timestamp"})
    assert t.column("d")[0].as_py() == datetime(2017, 8, 26, tzinfo=UTC)
