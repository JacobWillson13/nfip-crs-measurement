import pytest

from pipeline.ingest.client import OpenFemaClient, OpenFemaError


def test_pages_until_short_page(client, fake_server):
    pages = list(client.iter_pages("NfipClaims", 3, "state eq 'TX'", ["id", "state"]))
    assert [len(p) for p in pages] == [2, 2]
    skips = [p["$skip"] for _, p in fake_server.calls]
    assert skips == [0, 2, 4]  # third request returns empty and ends paging


def test_paging_is_ordered_by_id(client, fake_server):
    list(client.iter_pages("NfipClaims", 3, "state eq 'VA'"))
    assert all(p["$orderby"] == "id" for _, p in fake_server.calls)


def test_count_uses_server_side_count(client):
    assert client.count("NfipClaims", 3, "state eq 'VA'") == 3


def test_retries_transient_errors_then_succeeds(client, fake_server):
    fake_server.fail_next = [503, 429]
    assert client.count("NfipClaims", 3, "state eq 'TX'") == 4


def test_gives_up_after_max_retries(fake_server):
    c = OpenFemaClient(session=fake_server, max_retries=3, backoff_seconds=0, sleep=lambda s: None)
    fake_server.fail_next = [503, 503, 503]
    with pytest.raises(OpenFemaError, match="after 3 attempts"):
        c.count("NfipClaims", 3)


def test_client_errors_are_not_retried(client, fake_server):
    fake_server.fail_next = [400]
    with pytest.raises(OpenFemaError, match="HTTP 400"):
        client.count("NfipClaims", 3)
    assert len(fake_server.calls) == 1
