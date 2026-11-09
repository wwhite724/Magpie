import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from magpie import naming
from magpie.archive import is_excluded, iter_files
from magpie.cli import run_backup
from magpie.config import ConfigError, load_config

PASSWORD = "correct horse battery staple"


# --- naming / retention ------------------------------------------------------

def test_archive_name():
    assert naming.archive_name("APOLLO", datetime(2026, 11, 8, 2, 0, 5)) == "magpie-APOLLO-20261108-020005.7z"


def test_to_delete_keeps_newest_two_and_ignores_others():
    names = [
        "magpie-APOLLO-20261107-020000.7z",
        "magpie-APOLLO-20261105-020000.7z",
        "magpie-APOLLO-20261108-020000.7z",
        "magpie-APOLLO-20261106-020000.7z",
        "magpie-ROCKET-20261101-020000.7z",  # other machine: never touched
        "magpie-APOLLO-20261101-020000.7z.partial",
        "notes.txt",
    ]
    assert naming.to_delete("APOLLO", names, 2) == [
        "magpie-APOLLO-20261105-020000.7z",
        "magpie-APOLLO-20261106-020000.7z",
    ]


def test_to_delete_nothing_when_at_or_under_limit():
    names = ["magpie-APOLLO-20261107-020000.7z", "magpie-APOLLO-20261108-020000.7z"]
    assert naming.to_delete("APOLLO", names, 2) == []
    assert naming.to_delete("APOLLO", [], 2) == []


def test_machine_name_is_not_a_prefix_match():
    names = ["magpie-APOLLO2-20261101-020000.7z", "magpie-APOLLO-20261108-020000.7z"]
    assert naming.backups_for("APOLLO", names) == ["magpie-APOLLO-20261108-020000.7z"]


# --- excludes ------------------------------------------------------------------

def test_is_excluded():
    pats = ["node_modules", "*.tmp", ".venv"]
    assert is_excluded(("proj", "node_modules", "x.js"), pats)
    assert is_excluded(("a", "B.TMP"), pats)
    assert not is_excluded(("proj", "src", "main.py"), pats)


def test_iter_files_skips_excluded(tmp_path):
    src = tmp_path / "Docs"
    (src / "node_modules" / "pkg").mkdir(parents=True)
    (src / "node_modules" / "pkg" / "a.js").write_text("x")
    (src / "keep").mkdir()
    (src / "keep" / "b.txt").write_text("y")
    (src / "c.tmp").write_text("z")
    names = sorted(a for _, a in iter_files(src, ["node_modules", "*.tmp"]))
    assert names == ["Docs/keep/b.txt"]


# --- end to end with folder destinations ---------------------------------------

def make_setup(tmp_path, keep=2, archiver="py7zr"):
    src = tmp_path / "Documents"
    (src / "sub").mkdir(parents=True)
    (src / "hello.txt").write_text("hello")
    (src / "sub" / "data.bin").write_bytes(b"\x00\x01" * 1000)
    (src / "junk.tmp").write_text("skip me")
    nas = tmp_path / "nas"
    gd = tmp_path / "gdrive"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "sources": [str(src)],
        "excludes": ["*.tmp"],
        "staging_dir": "staging",
        "log_dir": "logs",
        "keep": keep,
        "machine_name": "testbox",
        "archiver": archiver,
        "destinations": [
            {"name": "truenas", "type": "folder", "path": str(nas)},
            {"name": "gdrive", "type": "folder", "path": str(gd)},
        ],
    }))
    (tmp_path / ".env").write_text(f'MAGPIE_PASSWORD="{PASSWORD}"\n')
    return cfg_path, nas, gd


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MAGPIE_PASSWORD", raising=False)


def test_config_requires_password(tmp_path):
    cfg_path, *_ = make_setup(tmp_path)
    (tmp_path / ".env").unlink()
    with pytest.raises(ConfigError, match="MAGPIE_PASSWORD"):
        load_config(cfg_path)


def test_config_rejects_duplicate_source_names(tmp_path):
    cfg_path, *_ = make_setup(tmp_path)
    data = json.loads(cfg_path.read_text())
    (tmp_path / "x" / "Documents").mkdir(parents=True)
    data["sources"].append(str(tmp_path / "x" / "Documents"))
    cfg_path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="distinct"):
        load_config(cfg_path)


def test_three_nights_keeps_two_everywhere(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    cfg_path, nas, gd = make_setup(tmp_path)
    cfg = load_config(cfg_path)
    assert cfg.machine_name == "TESTBOX"

    start = datetime(2026, 11, 6, 2, 0, 0)
    for night in range(3):
        assert run_backup(cfg, now=start + timedelta(days=night)) == 0

    expected = ["magpie-TESTBOX-20261107-020000.7z", "magpie-TESTBOX-20261108-020000.7z"]
    for dest in (nas, gd):
        assert sorted(p.name for p in dest.iterdir()) == expected
    # The local staging copy is removed once every destination has it.
    assert list((tmp_path / "staging").iterdir()) == []

    # The archive is encrypted, including file names, and holds the right files.
    newest = nas / expected[-1]
    with pytest.raises(Exception):
        with py7zr.SevenZipFile(newest, "r") as z:
            z.getnames()
    with py7zr.SevenZipFile(newest, "r", password=PASSWORD) as z:
        names = sorted(n for n in z.getnames() if not n.endswith("/") and "." in n.rsplit("/", 1)[-1])
    assert names == ["Documents/hello.txt", "Documents/sub/data.bin"]


def test_failed_destination_is_not_pruned(tmp_path):
    pytest.importorskip("py7zr")
    cfg_path, nas, gd = make_setup(tmp_path)
    cfg = load_config(cfg_path)
    start = datetime(2026, 11, 6, 2, 0, 0)
    for night in range(2):
        assert run_backup(cfg, now=start + timedelta(days=night)) == 0

    # Make the gdrive destination fail: replace its folder with a file.
    for p in gd.iterdir():
        p.unlink()
    gd.rmdir()
    gd.write_text("not a folder")

    assert run_backup(cfg, now=start + timedelta(days=2)) == 1
    assert sorted(p.name for p in nas.iterdir()) == [
        "magpie-TESTBOX-20261107-020000.7z",
        "magpie-TESTBOX-20261108-020000.7z",
    ]
    # The archive stays in staging so it isn't lost.
    assert [p.name for p in (tmp_path / "staging").iterdir()] == ["magpie-TESTBOX-20261108-020000.7z"]


def test_dry_run_changes_nothing(tmp_path):
    cfg_path, nas, gd = make_setup(tmp_path)
    cfg = load_config(cfg_path)
    assert run_backup(cfg, dry_run=True) == 0
    assert not nas.exists() and not gd.exists()


def test_7zip_archiver_round_trip(tmp_path):
    """Runs only where the 7-Zip executable is installed."""
    py7zr = pytest.importorskip("py7zr")
    cfg_path, nas, _ = make_setup(tmp_path, archiver="7z")
    cfg = load_config(cfg_path)
    from magpie.archive import find_7z
    if not find_7z(cfg):
        pytest.skip("7-Zip not installed")
    assert run_backup(cfg, now=datetime(2026, 11, 8, 2, 0, 0)) == 0
    with py7zr.SevenZipFile(nas / "magpie-TESTBOX-20261108-020000.7z", "r", password=PASSWORD) as z:
        files = z.readall()
    assert files["Documents/hello.txt"].read() == b"hello"
    assert "Documents/junk.tmp" not in files
