"""Distribution copies are derived from one snapshot without staging user files."""

import hashlib
import subprocess
import zipfile
import yaml

from scripts.export_artist_update import PATCH_PATHS, export_update
from tests.fixtures.artist_factory import template_profile, write_artist


def test_both_patch_copies_identical_and_local_audio_excluded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    git("init")
    (repo / "README.md").write_text("base")
    git("add", "README.md")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    write_artist(repo / "artists", "solo")
    artist = repo / "artists" / "solo"
    (artist / "voice").mkdir()
    (artist / "voice" / "private.wav").write_bytes(b"private recording")
    template = repo / "artists" / "_template"
    template.mkdir()
    (template / "artist.yaml").write_text(yaml.safe_dump(template_profile()))
    original = tmp_path / "original.zip"
    with zipfile.ZipFile(original, "w") as archive:
        archive.writestr("openmontage-music-video/", "")
        archive.writestr(PATCH_PATHS[0], "old internal patch")
        archive.writestr(PATCH_PATHS[1], "old public patch")
        archive.writestr("site.txt", "keep website")
        archive.writestr("openmontage-music-video/artists/solo/artist.yaml", "private old package entry")
    index_before = (repo / ".git" / "index").read_bytes()
    output = tmp_path / "fixed.zip"
    manifest = export_update("HEAD", output, original, repo)
    assert (repo / ".git" / "index").read_bytes() == index_before
    with zipfile.ZipFile(output) as archive:
        patch = archive.read(PATCH_PATHS[0])
        assert patch == archive.read(PATCH_PATHS[1])
        assert hashlib.sha256(patch).hexdigest() == manifest["patch_sha256"]
        assert b"artists/_template/artist.yaml" in patch
        assert b"artists/solo/artist.yaml" not in patch
        assert b"private.wav" not in patch
        assert archive.read("site.txt") == b"keep website"
        assert not any("private.wav" in name for name in archive.namelist())
        assert not any("artists/solo/" in name for name in archive.namelist())
    clean = tmp_path / "clean"
    subprocess.run(["git", "clone", str(repo), str(clean)], check=True, capture_output=True)
    subprocess.run(["git", "apply", "--check", "-"], cwd=clean, input=patch, check=True, capture_output=True)
