"""fetch_source_log_details regression tests."""

from src.behavior.validation_repository import UebaValidationRepository


class FakeClickHouseClient:
    """Fake client returning predefined source-log detail rows."""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.queries: list[dict] = []

    def query(self, sql: str, parameters: dict | None = None):
        self.queries.append({"sql": sql, "parameters": parameters or {}})
        return self.rows


def _repo(client: FakeClickHouseClient) -> UebaValidationRepository:
    return UebaValidationRepository(client=client, database="log_analysis")


def _row(id_: int, *, raw_log_available: int = 1, source_ip: str = "10.0.0.1") -> dict:
    return {
        "id": id_,
        "source_ip": source_ip,
        "destination_ip": "192.168.1.1",
        "src_country": "CN",
        "src_city": "Beijing",
        "vpn_gateway": "vpn-gw-1",
        "auth_method": "MFA",
        "client_software": "AnyConnect",
        "protocol": "SSLVPN",
        "raw_log_available": raw_log_available,
    }


def test_fetch_source_log_details_single_match() -> None:
    client = FakeClickHouseClient([_row(1, raw_log_available=1)])
    result = _repo(client).fetch_source_log_details([1])

    assert list(result) == [1]
    assert len(result[1]) == 1
    assert result[1][0]["source_ip"] == "10.0.0.1"
    assert result[1][0]["raw_log_available"] is True
    assert "raw_log" not in result[1][0]


def test_fetch_source_log_details_no_match() -> None:
    client = FakeClickHouseClient([])
    result = _repo(client).fetch_source_log_details([99999])

    assert result == {}
    assert len(client.queries) == 1


def test_fetch_source_log_details_multi_match() -> None:
    client = FakeClickHouseClient([
        _row(1, source_ip="10.0.0.1"),
        _row(1, source_ip="10.0.0.2"),
    ])
    result = _repo(client).fetch_source_log_details([1])

    assert len(result[1]) == 2
    assert [item["source_ip"] for item in result[1]] == ["10.0.0.1", "10.0.0.2"]


def test_fetch_source_log_details_multiple_source_log_ids() -> None:
    client = FakeClickHouseClient([_row(1), _row(2, raw_log_available=0)])
    result = _repo(client).fetch_source_log_details([1, 2, 2])

    assert set(result) == {1, 2}
    assert result[2][0]["raw_log_available"] is False
    assert sorted(client.queries[0]["parameters"]["source_log_ids"]) == [1, 2]


def test_fetch_source_log_details_empty_input() -> None:
    client = FakeClickHouseClient([])
    result = _repo(client).fetch_source_log_details([])

    assert result == {}
    assert client.queries == []


def test_fetch_source_log_details_source_log_id_zero_not_queried() -> None:
    client = FakeClickHouseClient([])
    result = _repo(client).fetch_source_log_details([0])

    assert result == {}
    assert client.queries == []


def test_fetch_source_log_details_uses_parameterized_query() -> None:
    client = FakeClickHouseClient([_row(10)])
    _repo(client).fetch_source_log_details([10, 20])

    call = client.queries[0]
    normalized = " ".join(call["sql"].split()).lower()
    assert "where id in %(source_log_ids)s" in normalized
    assert "10" not in call["sql"]
    assert "20" not in call["sql"]
    assert sorted(call["parameters"]["source_log_ids"]) == [10, 20]
