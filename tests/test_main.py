import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from test_settings import write_config

import main
from common import discovery, journal, runner

RESULT_FILES = {
    "journal.jsonl",
    "run.log",
    "summary.json",
    "report.md",
    "products.csv",
    "categories.csv",
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(discovery, "ENV_FILE", tmp_path / ".env")
    (tmp_path / ".env").write_text("USE_PROXY=false\n")
    return write_config(tmp_path)


def fake_crawl(config, settings, product_ids, run, control):
    for loc in config.locations:
        for kind, count in (
            ("product", loc.product_requests),
            ("category", loc.category_requests),
        ):
            for number in range(count):
                run.write(
                    {
                        "event": "request",
                        "request_id": f"{loc.zip}:{kind}:{number}",
                        "type": kind,
                        "postal_code": loc.zip,
                        "status": "success",
                        "data": {"records": []},
                    }
                )
    return True


def test_run_writes_the_result_files(project, monkeypatch, capsys):
    monkeypatch.setattr(runner, "crawl", fake_crawl)
    assert main.main(["--config", str(project)]) == 0
    [folder] = (project.parent / "results").iterdir()
    assert {p.name for p in folder.iterdir()} == RESULT_FILES
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["status"] == "complete"
    assert summary["successful_requests"] == summary["target_requests"] == 4
    assert summary["config"]["sessions_per_ip"] == 4
    output = capsys.readouterr().out
    assert output.count("RUN START") == output.count("RUN END") == 1


def test_report_script_rebuilds_the_same_summary(project, monkeypatch):
    monkeypatch.setattr(runner, "crawl", fake_crawl)
    main.main(["--config", str(project)])
    [folder] = (project.parent / "results").iterdir()
    before = (folder / "summary.json").read_text()
    assert journal.main([str(folder)]) == 0
    assert (folder / "summary.json").read_text() == before


@pytest.mark.parametrize("status", ["incomplete", "crashed"])
def test_unsuccessful_run_keeps_report(project, monkeypatch, status):
    def unsuccessful(config, settings, product_ids, run, control):
        if status == "crashed":
            raise RuntimeError("Unexpected test failure")
        return False

    monkeypatch.setattr(runner, "crawl", unsuccessful)
    assert main.main(["--config", str(project)]) == 1
    [folder] = (project.parent / "results").iterdir()
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["status"] == status
    assert summary["successful_requests"] == 0
    assert (folder / "report.md").is_file()
    assert "RUN END" in (folder / "run.log").read_text()
    if status == "crashed":
        assert "Unexpected test failure" in (folder / "run.log").read_text()


def test_discover_can_start_without_an_existing_product_pool(project, monkeypatch):
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    (project.parent / "product_ids.json").unlink()

    def discovered(config, settings, run, control):
        config.product_file.write_text('["1", "2"]')
        return True

    monkeypatch.setattr(discovery, "discover", discovered)
    assert discovery.main(["--config", str(project)]) == 0
    assert json.loads((project.parent / "product_ids.json").read_text()) == ["1", "2"]
    [folder] = (project.parent / "results").iterdir()
    assert {path.name for path in folder.iterdir()} == RESULT_FILES
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["command"] == "discover"
    assert summary["status"] == "complete"
    assert summary["target_requests"] == 25
    assert summary["locations_used"] == 0
    for signum, handler in previous_handlers.items():
        assert signal.getsignal(signum) == handler


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
def test_signal_stops_discovery_without_replacing_the_pool(
    project, monkeypatch, stop_signal
):
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    product_file = project.parent / "product_ids.json"
    original_pool = product_file.read_bytes()

    def interrupted(config, settings, run, control):
        signal.raise_signal(stop_signal)
        assert control.stop.is_set()
        return False

    monkeypatch.setattr(discovery, "discover", interrupted)
    assert discovery.main(["--config", str(project)]) == 128 + stop_signal
    [folder] = (project.parent / "results").iterdir()
    assert {path.name for path in folder.iterdir()} == RESULT_FILES
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["command"] == "discover"
    assert summary["status"] == "interrupted"
    assert summary["stop_reason"] == f"Stopped by {stop_signal.name}"
    assert "RUN END" in (folder / "run.log").read_text()
    assert product_file.read_bytes() == original_pool
    for signum, handler in previous_handlers.items():
        assert signal.getsignal(signum) == handler


@pytest.mark.parametrize("status", ["incomplete", "crashed"])
def test_unsuccessful_discovery_keeps_report_pool_and_restores_signals(
    project, monkeypatch, status
):
    previous_handlers = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    product_file = project.parent / "product_ids.json"
    original_pool = product_file.read_bytes()

    def unsuccessful(config, settings, run, control):
        if status == "crashed":
            raise RuntimeError("Unexpected discovery failure")
        return False

    monkeypatch.setattr(discovery, "discover", unsuccessful)
    assert discovery.main(["--config", str(project)]) == 1
    [folder] = (project.parent / "results").iterdir()
    assert {path.name for path in folder.iterdir()} == RESULT_FILES
    summary = json.loads((folder / "summary.json").read_text())
    assert summary["command"] == "discover"
    assert summary["status"] == status
    assert summary["successful_requests"] == 0
    output = (folder / "run.log").read_text()
    assert "RUN END" in output
    if status == "crashed":
        assert "Unexpected discovery failure" in output
    assert product_file.read_bytes() == original_pool
    for signum, handler in previous_handlers.items():
        assert signal.getsignal(signum) == handler


def test_discovery_cli_loads_without_the_crawl_runner():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['common.runner'] = None; "
            "from common.discovery import main; main(['--help'])",
        ],
        cwd=Path(discovery.__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Build product_ids.json." in result.stdout


def test_invalid_configuration_fails_before_any_run(project, monkeypatch):
    project.write_text("{}")
    with pytest.raises(SystemExit) as exit_info:
        main.main(["--config", str(project)])
    assert exit_info.value.code == 2
    assert not (project.parent / "results").exists()
