import pipeline.cli as cli
from pipeline.quality.checks import run_checks
from pipeline.transform.build import build


class _NoClose:
    def __init__(self, con):
        self._con = con

    def __getattr__(self, name):
        return getattr(self._con, name)

    def close(self):
        pass


def test_export_writes_parquet_and_bi_csvs(con, loaded_lake, tmp_path, monkeypatch):
    build(con, loaded_lake, 2009, 2026)
    run_checks(con)
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "_connect", lambda cfg: _NoClose(con))
    assert cli.cmd_export(None) == 0
    out = tmp_path / "data" / "export"
    assert (out / "community_year_panel.parquet").exists()
    assert (out / "bi" / "community_year.csv").read_text().startswith("community_id")
    assert (out / "bi" / "dq_results.csv").exists() and (out / "bi" / "run_log.csv").exists()
