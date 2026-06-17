from src.plc.client import PLCClient


class _FakeModbusClient:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.closed = False

    def connect(self) -> bool:
        return self.ok

    def close(self) -> None:
        self.closed = True


def test_on_error_closes_active_client() -> None:
    client = PLCClient("127.0.0.1")
    fake = _FakeModbusClient()
    client._client = fake
    client._connected = True

    client._on_error("boom")

    assert fake.closed is True
    assert client._client is None
    assert client.connected is False


def test_connect_locked_closes_previous_client_before_replacing(monkeypatch) -> None:
    created: list[_FakeModbusClient] = []

    def _factory(*args, **kwargs):
        fake = _FakeModbusClient(ok=True)
        created.append(fake)
        return fake

    monkeypatch.setattr("src.plc.client.ModbusTcpClient", _factory)

    client = PLCClient("127.0.0.1")
    old = _FakeModbusClient()
    client._client = old

    ok = client._connect_locked()

    assert ok is True
    assert old.closed is True
    assert client._client is created[0]
