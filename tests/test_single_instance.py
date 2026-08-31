from __future__ import annotations

import argparse
from uuid import uuid4

import src.main as main_module
from src.utils.single_instance import SingleInstanceGuard


def test_guard_allows_exactly_one_instance_and_releases_after_close() -> None:
    name = f"Local\\DEFYVISION_TEST_{uuid4().hex}"
    first = SingleInstanceGuard(name)
    second = SingleInstanceGuard(name)
    try:
        assert first.acquire() is True
        assert first.acquire() is True
        assert second.acquire() is False

        first.close()
        assert second.acquire() is True
    finally:
        first.close()
        second.close()


def test_main_does_not_call_hardware_command_when_instance_exists(monkeypatch) -> None:
    called = False
    notice_shown = False

    def hardware_command(_args: argparse.Namespace) -> int:
        nonlocal called
        called = True
        return 0

    class ExistingInstanceGuard:
        def acquire(self) -> bool:
            return False

        def close(self) -> None:
            return None

    class Parser:
        @staticmethod
        def parse_args() -> argparse.Namespace:
            return argparse.Namespace(command="run", func=hardware_command)

    def show_notice() -> None:
        nonlocal notice_shown
        notice_shown = True

    monkeypatch.setattr(main_module, "build_parser", lambda: Parser())
    monkeypatch.setattr(main_module, "SingleInstanceGuard", ExistingInstanceGuard)
    monkeypatch.setattr(main_module, "show_already_running_notice", show_notice)

    assert main_module.main() == 0
    assert called is False
    assert notice_shown is True


def test_main_releases_guard_after_hardware_command(monkeypatch) -> None:
    closed = False

    def hardware_command(_args: argparse.Namespace) -> int:
        return 7

    class AcquiredGuard:
        def acquire(self) -> bool:
            return True

        def close(self) -> None:
            nonlocal closed
            closed = True

    class Parser:
        @staticmethod
        def parse_args() -> argparse.Namespace:
            return argparse.Namespace(command="service", func=hardware_command)

    monkeypatch.setattr(main_module, "build_parser", lambda: Parser())
    monkeypatch.setattr(main_module, "SingleInstanceGuard", AcquiredGuard)

    assert main_module.main() == 7
    assert closed is True


def test_main_fails_closed_when_guard_cannot_be_created(monkeypatch) -> None:
    called = False
    failure_shown = False

    def hardware_command(_args: argparse.Namespace) -> int:
        nonlocal called
        called = True
        return 0

    class BrokenGuard:
        def acquire(self) -> bool:
            raise OSError("mutex unavailable")

        def close(self) -> None:
            return None

    class Parser:
        @staticmethod
        def parse_args() -> argparse.Namespace:
            return argparse.Namespace(command="run", func=hardware_command)

    def show_failure(_exc: BaseException) -> None:
        nonlocal failure_shown
        failure_shown = True

    monkeypatch.setattr(main_module, "build_parser", lambda: Parser())
    monkeypatch.setattr(main_module, "SingleInstanceGuard", BrokenGuard)
    monkeypatch.setattr(main_module, "show_guard_failure_notice", show_failure)

    assert main_module.main() == 1
    assert called is False
    assert failure_shown is True
