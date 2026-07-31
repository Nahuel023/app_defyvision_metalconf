from src.utils import license


def test_operation_is_permanently_enabled() -> None:
    assert license.is_licensed() is True


def test_legacy_license_apis_cannot_block_operation() -> None:
    assert license.validate_key("") is True
    assert license.validate_key("CODIGO-INVALIDO") is True
    assert license.load_license_file() == ""
    assert license.save_license_file("CODIGO-INVALIDO") is None
    assert license.update_heartbeat() is None
    assert license.is_licensed() is True
