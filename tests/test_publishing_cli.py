"""Tests for ``python -m publishing``."""

from __future__ import annotations

import json
from pathlib import Path

from publishing.__main__ import main
from tests.factories import dossier, record, source


def _write_dossier(root: Path, payload: dict) -> Path:
    directory = root / "data"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "dossier.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_cli_writes_markdown_and_assets(tmp_path):
    _write_dossier(tmp_path, dossier())
    exit_code = main(["--root", str(tmp_path), "--output", "article/generated/review.md"])
    assert exit_code == 0
    output = tmp_path / "article" / "generated" / "review.md"
    assert output.exists()
    assert (tmp_path / "article" / "generated" / "assets" / "records-timeline.svg").exists()
    assert "## Source register" in output.read_text(encoding="utf-8")


def test_cli_stdout_mode_writes_nothing(tmp_path, capsys):
    _write_dossier(tmp_path, dossier())
    assert main(["--root", str(tmp_path), "--stdout"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("# Allianz")
    assert not (tmp_path / "article").exists()


def test_cli_refuses_a_structurally_invalid_dossier(tmp_path, capsys):
    payload = dossier(records=[record(source_ids=["missing-source"])])
    _write_dossier(tmp_path, payload)
    assert main(["--root", str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert "record_source_unknown" in captured.err
    assert not (tmp_path / "article").exists()


def test_cli_no_charts_option_writes_only_the_markdown(tmp_path):
    _write_dossier(tmp_path, dossier())
    output = tmp_path / "article" / "review.md"
    assert main(["--root", str(tmp_path), "--output", "article/review.md", "--no-charts"]) == 0
    assert output.exists()
    assert not (tmp_path / "article" / "assets").exists()
    assert "![" not in output.read_text(encoding="utf-8")


def test_cli_reads_a_custom_dossier_and_snapshot_file(tmp_path):
    payload = dossier()
    custom = tmp_path / "research" / "custom.json"
    custom.parent.mkdir()
    custom.write_text(json.dumps(payload), encoding="utf-8")
    snapshots = tmp_path / "research" / "snapshots.jsonl"
    snapshots.write_text("", encoding="utf-8")
    assert main(["--root", str(tmp_path), "--dossier", "research/custom.json", "--snapshots", "research/snapshots.jsonl", "--stdout"]) == 0


def test_cli_accepts_an_alternate_source_and_title(tmp_path, capsys):
    payload = dossier(sources=[source(id="asic-travel", publisher="Example Regulator", title="Decision")])
    _write_dossier(tmp_path, payload)
    assert main(["--root", str(tmp_path), "--stdout", "--title", "Custom title"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("# Custom title")
    assert "Example Regulator" in output


def test_cli_links_a_nested_custom_asset_directory(tmp_path):
    _write_dossier(tmp_path, dossier())
    assert main(
        [
            "--root",
            str(tmp_path),
            "--output",
            "article/review.md",
            "--assets",
            "article/assets/review",
        ]
    ) == 0
    markdown = (tmp_path / "article" / "review.md").read_text(encoding="utf-8")
    assert "](assets/review/records-timeline.svg)" in markdown
    assert (tmp_path / "article" / "assets" / "review" / "records-timeline.svg").exists()
