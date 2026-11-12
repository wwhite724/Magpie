import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from magpie import naming
from magpie.archive import ArchiveResult, find_7z, is_excluded, iter_files
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

def make_setup(tmp_path, keep=2):
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


@pytest.fixture
def seven_zip(tmp_path):
    """The 7-Zip executable; tests that build real archives are skipped without it."""
    cfg_path, *_ = make_setup(tmp_path / "probe")
    exe = find_7z(load_config(cfg_path))
    if not exe:
        pytest.skip("7-Zip not installed")
    return exe


@pytest.fixture
def fake_archive(monkeypatch):
    """Replaces 7-Zip with a tiny stand-in file, for tests about uploading and retention."""
    import magpie.cli as cli

    def fake(cfg, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"7z stand-in")
        return ArchiveResult(dest, dest.stat().st_size, [])

    monkeypatch.setattr(cli, "create_archive", fake)


def archive_names(exe, archive, password):
    """The file paths inside an archive, listed by 7-Zip."""
    out = subprocess.run([exe, "l", "-slt", "-ba", f"-p{password}", str(archive)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    blocks = out.stdout.split("\n\n")
    return sorted(
        b.split("Path = ", 1)[1].splitlines()[0].replace("\\", "/")
        for b in blocks
        if "Path = " in b and "Folder = +" not in b and "Attributes = D" not in b
    )


def extract(exe, archive, password, out_dir):
    proc = subprocess.run([exe, "x", "-y", f"-p{password}", f"-o{out_dir}", str(archive)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


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


def test_three_nights_keeps_two_everywhere(tmp_path, seven_zip):
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
    wrong = subprocess.run([seven_zip, "l", "-pwrong", str(newest)], capture_output=True, text=True)
    assert wrong.returncode != 0
    assert "hello.txt" not in wrong.stdout
    assert archive_names(seven_zip, newest, PASSWORD) == ["Documents/hello.txt", "Documents/sub/data.bin"]


def test_failed_destination_is_not_pruned(tmp_path, fake_archive):
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


def test_7zip_round_trip(tmp_path, seven_zip):
    cfg_path, nas, _ = make_setup(tmp_path)
    cfg = load_config(cfg_path)
    assert run_backup(cfg, now=datetime(2026, 11, 8, 2, 0, 0)) == 0
    extract(seven_zip, nas / "magpie-TESTBOX-20261108-020000.7z", PASSWORD, tmp_path / "restore")
    assert (tmp_path / "restore" / "Documents" / "hello.txt").read_bytes() == b"hello"
    assert (tmp_path / "restore" / "Documents" / "sub" / "data.bin").exists()
    assert not (tmp_path / "restore" / "Documents" / "junk.tmp").exists()


def test_staging_and_logs_inside_a_source_are_not_archived(tmp_path, seven_zip):
    """Like the Magpie staging folder when C:/dev is a source."""
    cfg_path, nas, _ = make_setup(tmp_path)
    data = json.loads(cfg_path.read_text())
    src = Path(data["sources"][0])
    data["staging_dir"] = str(src / "Magpie" / "staging")
    data["log_dir"] = str(src / "Magpie" / "logs")
    cfg_path.write_text(json.dumps(data))
    (src / "Magpie" / "logs").mkdir(parents=True)
    (src / "Magpie" / "logs" / "magpie.log").write_text("log")
    (src / "Magpie" / "staging").mkdir()
    (src / "Magpie" / "staging" / "leftover.bin").write_text("old")
    (src / "Magpie" / "code.py").write_text("print(1)")
    (src / "other" / "staging").mkdir(parents=True)
    (src / "other" / "staging" / "keep.txt").write_text("a different staging folder is kept")
    cfg = load_config(cfg_path)

    assert run_backup(cfg, now=datetime(2026, 11, 8, 2, 0, 0)) == 0
    names = set(archive_names(seven_zip, nas / "magpie-TESTBOX-20261108-020000.7z", PASSWORD))
    assert "Documents/Magpie/code.py" in names
    assert "Documents/other/staging/keep.txt" in names
    assert not [n for n in names if n.startswith(("Documents/Magpie/staging", "Documents/Magpie/logs"))]


def test_per_destination_keep_and_prune_before_upload(tmp_path, fake_archive):
    """gdrive keeps 1 and makes room first; truenas keeps the default 2."""
    cfg_path, nas, gd = make_setup(tmp_path)
    data = json.loads(cfg_path.read_text())
    data["destinations"][1].update({"keep": 1, "prune_before_upload": True})
    cfg_path.write_text(json.dumps(data))
    cfg = load_config(cfg_path)

    seen_during_upload = []
    import magpie.destinations as dst
    real_upload = dst.FolderDestination.upload

    def spy(self, archive):
        if self.name == "gdrive" and self.root.exists():
            seen_during_upload.append(sorted(p.name for p in self.root.iterdir()))
        return real_upload(self, archive)

    dst.FolderDestination.upload = spy
    try:
        start = datetime(2026, 11, 6, 2, 0, 0)
        for night in range(3):
            assert run_backup(cfg, now=start + timedelta(days=night)) == 0
    finally:
        dst.FolderDestination.upload = real_upload

    assert sorted(p.name for p in gd.iterdir()) == ["magpie-TESTBOX-20261108-020000.7z"]
    assert sorted(p.name for p in nas.iterdir()) == [
        "magpie-TESTBOX-20261107-020000.7z",
        "magpie-TESTBOX-20261108-020000.7z",
    ]
    # The old archive was already gone when each later upload started.
    assert seen_during_upload == [[], []]


def test_destination_keep_must_be_positive(tmp_path):
    cfg_path, *_ = make_setup(tmp_path)
    data = json.loads(cfg_path.read_text())
    data["destinations"][0]["keep"] = 0
    cfg_path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="keep"):
        load_config(cfg_path)


def test_seven_zip_warnings_lists_skipped_paths():
    from magpie.archive import seven_zip_warnings
    out = r"""Scanning the drive:
WARNING: Access is denied.
C:\Users\me\Documents\My Music\

Scan WARNINGS for files and folders:

C:\Users\me\Documents\My Music\ : Access is denied.
C:\Users\me\Documents\My Videos\ : Access is denied.
----------------
Scan WARNINGS: 2
"""
    assert seven_zip_warnings(out) == [
        r"Skipped C:\Users\me\Documents\My Music\ : Access is denied.",
        r"Skipped C:\Users\me\Documents\My Videos\ : Access is denied.",
    ]
    assert seven_zip_warnings("no summary") == ["7-Zip reported warnings (see its output)"]


def test_quota_error_after_making_room_is_retried(tmp_path, monkeypatch, fake_archive):
    """Google Drive is slow to count freed space; the upload must be retried, not given up."""
    import magpie.cli as cli
    import magpie.destinations as dst
    monkeypatch.setattr(cli, "QUOTA_RETRY_DELAYS", [0, 0, 0])
    cfg_path, nas, gd = make_setup(tmp_path)
    data = json.loads(cfg_path.read_text())
    data["destinations"][1].update({"keep": 1, "prune_before_upload": True})
    cfg_path.write_text(json.dumps(data))
    cfg = load_config(cfg_path)
    assert run_backup(cfg, now=datetime(2026, 11, 7, 2, 0, 0)) == 0

    real_upload = dst.FolderDestination.upload
    calls = {"n": 0}

    def flaky(self, archive):
        if self.name == "gdrive":
            calls["n"] += 1
            if calls["n"] <= 2:
                raise dst.DestinationError("googleapi: Error 403: storageQuotaExceeded")
        return real_upload(self, archive)

    monkeypatch.setattr(dst.FolderDestination, "upload", flaky)
    assert run_backup(cfg, now=datetime(2026, 11, 8, 2, 0, 0)) == 0
    assert calls["n"] == 3
    assert [p.name for p in gd.iterdir()] == ["magpie-TESTBOX-20261108-020000.7z"]


def test_quota_error_without_making_room_is_not_retried(tmp_path, monkeypatch, fake_archive):
    import magpie.cli as cli
    import magpie.destinations as dst
    monkeypatch.setattr(cli, "QUOTA_RETRY_DELAYS", [0, 0, 0])
    cfg_path, nas, gd = make_setup(tmp_path)
    cfg = load_config(cfg_path)
    calls = {"n": 0}
    real_upload = dst.FolderDestination.upload

    def full(self, archive):
        if self.name == "gdrive":
            calls["n"] += 1
            raise dst.DestinationError("storageQuotaExceeded")
        return real_upload(self, archive)

    monkeypatch.setattr(dst.FolderDestination, "upload", full)
    assert run_backup(cfg, now=datetime(2026, 11, 8, 2, 0, 0)) == 1
    assert calls["n"] == 1


def test_missing_7zip_stops_the_backup(tmp_path, monkeypatch):
    import magpie.archive as archive
    monkeypatch.setattr(archive, "find_7z", lambda cfg: None)
    cfg_path, nas, gd = make_setup(tmp_path)
    cfg = load_config(cfg_path)
    assert run_backup(cfg, now=datetime(2026, 11, 8, 2, 0, 0)) == 2
    assert not nas.exists() and not gd.exists()
