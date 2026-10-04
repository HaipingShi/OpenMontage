"""Distribution copies are derived from one snapshot without staging user files."""

import hashlib
import subprocess
import zipfile

from scripts.export_artist_update import PATCH_PATHS, export_update


def test_both_patch_copies_identical_and_local_audio_excluded(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    git("init")
    (repo / "README.md").write_text("base")
    git("add", "README.md")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    artist = repo / "artists" / "amor"
    (artist / "voice").mkdir(parents=True)
    (artist / "voice" / "private.wav").write_bytes(b"private recording")
    (artist / "artist.yaml").write_text("id: amor\n")
    original = tmp_path / "original.zip"
    with zipfile.ZipFile(original, "w") as archive:
        archive.writestr("openmontage-music-video/", "")
        archive.writestr(PATCH_PATHS[0], "old internal patch")
        archive.writestr(PATCH_PATHS[1], "old public patch")
        archive.writestr("site.txt", "keep website")
    index_before = (repo / ".git" / "index").read_bytes()
    output = tmp_path / "fixed.zip"
    manifest = export_update("HEAD", output, original, repo)
    assert (repo / ".git" / "index").read_bytes() == index_before
    with zipfile.ZipFile(output) as archive:
        patch = archive.read(PATCH_PATHS[0])
        assert patch == archive.read(PATCH_PATHS[1])
        assert hashlib.sha256(patch).hexdigest() == manifest["patch_sha256"]
        assert b"artists/amor/artist.yaml" in patch
        assert b"private.wav" not in patch
        assert archive.read("site.txt") == b"keep website"
        assert not any("private.wav" in name for name in archive.namelist())
    clean = tmp_path / "clean"
    subprocess.run(["git", "clone", str(repo), str(clean)], check=True, capture_output=True)
    subprocess.run(["git", "apply", "--check", "-"], cwd=clean, input=patch, check=True, capture_output=True)
