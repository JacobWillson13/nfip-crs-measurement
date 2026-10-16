"""Exposure model and analysis panel, end to end from bronze."""

import pytest

from pipeline.quality.checks import has_blocking_failure, run_checks
from pipeline.quality.report import feasibility_report
from pipeline.transform.build import build

# Hand-computed expectations for the synthetic policies in conftest.POLICY_ROWS.
P1_2016 = 184 / 366          # Jul 1 to Dec 31, 2016 is a leap year
P1_2017 = 181 / 365          # Jan 1 to Jul 1, 2017
P2_2017 = 2 * 90 / 365       # policyCount 2, cancelled Apr 1 after 90 days
HOUSTON_2017_PAID = 58042.78 + 77901.87 + 18300


@pytest.fixture
def built(con, loaded_lake):
    status = build(con, loaded_lake, year_min=2009, year_max=2026)
    assert all(v == "built" for v in status.values()), status
    return con


def _exposure(con, community, year):
    return con.execute(
        "select earned_policy_years, earned_premium, earned_coverage, crs_class_modal "
        "from silver.policy_exposure_community_year where community_id = ? and year = ?",
        [community, year]).fetchone()


def test_policy_split_across_calendar_years(built):
    epy16, prem16, cov16, _ = _exposure(built, "480296", 2016)
    assert epy16 == pytest.approx(P1_2016)
    assert prem16 == pytest.approx(1000 * 184 / 365)    # premium earned over the 365-day term
    assert cov16 == pytest.approx(250000 * P1_2016)


def test_cancellation_and_policy_count(built):
    epy17, *_ = _exposure(built, "480296", 2017)
    assert epy17 == pytest.approx(P1_2017 + P2_2017)


def test_bad_terms_and_missing_community_are_excluded(built):
    years = [r[0] for r in built.execute(
        "select year from silver.policy_exposure_community_year where community_id = '515529' order by 1"
    ).fetchall()]
    assert years == [2009, 2010]   # the reversed-term record from 2012 contributed nothing
    assert built.execute(
        "select count(*) from silver.policy_exposure_community_year where community_id is null").fetchone()[0] == 0


def test_panel_has_zero_claim_years(built):
    row = built.execute("select n_claims, paid_total, loss_cost_per_policy_year from gold.community_year_panel "
                        "where community_id = '480296' and year = 2016").fetchone()
    assert row == (0, 0, 0)


def test_panel_loss_metrics(built):
    row = built.execute("""
        select n_claims, paid_total, loss_cost_per_policy_year, crs_class, in_crs, crs_discount_pct_sfha,
               crs_entry_year, regime
        from gold.community_year_panel where community_id = '480296' and year = 2017
    """).fetchone()
    n, paid, loss_cost, crs, in_crs, disc, entry, regime = row
    assert n == 2 and paid == pytest.approx(HOUSTON_2017_PAID)
    assert loss_cost == pytest.approx(HOUSTON_2017_PAID / (P1_2017 + P2_2017))
    assert (crs, in_crs, disc, entry, regime) == (5, True, 25, 1992, "pre_rr2")


def test_null_policy_crs_class_means_class_10(built):
    crs, in_crs, disc = built.execute(
        "select crs_class, in_crs, crs_discount_pct_sfha from gold.community_year_panel "
        "where community_id = '515529' and year = 2009").fetchone()
    assert (crs, in_crs, disc) == (10, False, 0)


def test_crs_history_rebuilt_from_status_book(built):
    rows = {r[0]: r[1:] for r in built.execute("""
        select year, in_crs, crs_class, crs_status_basis, crs_class_policy_stamp
        from gold.community_year_panel where community_id = '480307' order by year
    """).fetchall()}
    assert rows[2011] == (False, 10, "not_in_crs", 7)              # before entry, despite the policy stamp
    assert rows[2014] == (True, None, "in_crs_class_unknown", 7)   # in CRS, class before 2017 unknown
    assert rows[2017] == (True, 7, "current_class", 7)


def test_effective_dates_count_from_mid_year(built):
    entry, eff = built.execute(
        "select crs_entry_panel_year, crs_class_effective_panel_year from gold.community_year_panel "
        "where community_id = '480307' limit 1").fetchone()
    assert (entry, eff) == (2012, 2017)   # May 1 counts that year; Oct 1 counts from the next


def test_shares_are_zero_not_null_when_absent(built):
    sfha, crs_null = built.execute(
        "select share_exposure_sfha, share_exposure_crs_null from silver.policy_exposure_community_year "
        "where community_id = '480307' and year = 2017").fetchone()
    assert (sfha, crs_null) == (0, 0)


def test_county_year_key(built):
    key = built.execute("select county_year from gold.community_year_panel "
                        "where community_id = '480307' and year = 2017").fetchone()[0]
    assert key == "48201-2017"


def test_status_book_silver(built):
    row = built.execute("select crs_entry_year, crs_class_current, sfha_discount_pct_current "
                        "from silver.community_status where community_id = '480307'").fetchone()
    assert row == (2012, 7, 15)


def test_checks_pass_on_full_build(built):
    results = run_checks(built)
    assert not has_blocking_failure(results), [r for r in results if not r["passed"]]
    stamp = next(r for r in results if r["check"] == "policy_crs_stamp_disagrees_with_history")
    assert stamp["failing_rows"] == 1   # Pasadena 2011: stamped 7, actually not yet in CRS
    names = {r["check"] for r in results}
    assert {"panel_join_no_fanout", "exposure_grain_unique", "status_book_community_unique"} <= names


def test_feasibility_report_writes_csvs(built, tmp_path):
    text = feasibility_report(built, tmp_path / "export")
    assert "gate1_national_exposure_by_year" in text
    assert (tmp_path / "export" / "gate2_mixed_county_years.csv").exists()


def test_build_skips_models_whose_inputs_are_missing(con, client, claims_ds, tmp_path):
    from pipeline.ingest.loader import load_partition
    lake = tmp_path / "lake_claims_only"
    load_partition(client, con, claims_ds, "TX", lake)
    status = build(con, lake)
    assert status["10_silver_nfip_claims.sql"] == "built"
    assert status["15_silver_policy_exposure.sql"] == "skipped"
    assert status["30_gold_community_year_panel.sql"] == "skipped"
