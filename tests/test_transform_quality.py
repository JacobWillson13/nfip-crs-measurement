import pytest

from pipeline.ingest.loader import load_partition
from pipeline.quality.checks import has_blocking_failure, run_checks
from pipeline.transform.build import build


@pytest.fixture
def built(client, con, claims_ds, tmp_path):
    lake = tmp_path / "lake"
    for state in ("VA", "TX"):
        load_partition(client, con, claims_ds, state, lake)
    build(con, lake)
    return con


def test_silver_handles_source_quirks(built):
    sentinel = built.execute(
        "select construction_date, construction_date_is_sentinel from silver.nfip_claims where claim_id = 5448705"
    ).fetchone()
    assert sentinel == (None, True)
    # Closed-without-payment claim: null paid amounts become 0, not null.
    assert built.execute(
        "select paid_total from silver.nfip_claims where claim_id = 5448692").fetchone()[0] == 0


def test_sfha_flag(built):
    rows = dict(built.execute("select rated_flood_zone, in_sfha from silver.nfip_claims").fetchall())
    assert rows["AE"] is True and rows["A"] is True and rows["X"] is False and rows["C"] is False


def test_gold_aggregates_houston_harvey(built):
    row = built.execute("""
        select n_claims, n_paid_claims, round(paid_total, 2), crs_class_modal
        from gold.community_year_claims where community_id = '480296' and year_of_loss = 2017
    """).fetchone()
    assert row == (2, 2, round(58042.78 + 77901.87 + 18300, 2), 5)


def test_all_blocking_checks_pass(built):
    results = run_checks(built)
    assert not has_blocking_failure(results), [r for r in results if not r["passed"]]
    sentinel = next(r for r in results if r["check"] == "construction_date_sentinel")
    assert sentinel["failing_rows"] == 1  # warn-level, surfaced but not blocking


def test_reconciliation_catches_dropped_rows(built):
    built.execute("delete from silver.nfip_claims where claim_id = 5448672")
    results = {r["check"]: r for r in run_checks(built)}
    assert not results["silver_rows_match_landed_rows"]["passed"]
    assert has_blocking_failure(list(results.values()))
