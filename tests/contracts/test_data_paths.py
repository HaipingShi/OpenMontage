"""Application installs have no implicit ownership of user work or libraries."""
from pathlib import Path
import runpy

import pytest


MODULE = Path(__file__).resolve().parents[2] / 'lib/paths.py'


@pytest.fixture
def paths(monkeypatch, tmp_path):
    for key in ('OPENMONTAGE_DATA_DIR', 'OPENMONTAGE_PROJECTS_DIR', 'OPENMONTAGE_LIBRARY_DIR'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    return lambda: runpy.run_path(str(MODULE))


def test_user_data_is_independent_of_application_checkout(paths, tmp_path):
    result = paths()
    assert result['DATA_DIR'] == tmp_path / 'OpenMontageData'
    assert result['PROJECTS_DIR'] == result['DATA_DIR'] / 'projects'
    assert result['LIBRARY_DIR'] == result['DATA_DIR'] / 'library'
    assert result['ARTISTS_DIR'] == result['LIBRARY_DIR'] / 'artists'
    assert not result['DATA_DIR'].is_relative_to(result['REPO_ROOT'])
    assert not result['DATA_DIR'].exists()  # Reading configuration creates no data.


def test_data_root_can_be_selected_without_changing_application(paths, monkeypatch, tmp_path):
    chosen = tmp_path / 'studio'
    monkeypatch.setenv('OPENMONTAGE_DATA_DIR', str(chosen))
    result = paths()
    assert result['PROJECTS_DIR'] == chosen / 'projects'
    assert result['LIBRARY_DIR'] == chosen / 'library'


def test_independent_project_and_library_roots_remain_supported(paths, monkeypatch, tmp_path):
    monkeypatch.setenv('OPENMONTAGE_PROJECTS_DIR', str(tmp_path / 'works'))
    monkeypatch.setenv('OPENMONTAGE_LIBRARY_DIR', str(tmp_path / 'shared'))
    result = paths()
    assert result['PROJECTS_DIR'] == tmp_path / 'works'
    assert result['ARTISTS_DIR'] == tmp_path / 'shared/artists'


@pytest.mark.parametrize('variable', ['OPENMONTAGE_DATA_DIR', 'OPENMONTAGE_PROJECTS_DIR', 'OPENMONTAGE_LIBRARY_DIR'])
def test_relative_data_configuration_does_not_follow_checkout(paths, monkeypatch, variable):
    monkeypatch.setenv(variable, 'relative-user-data')
    with pytest.raises(ValueError, match='absolute'):
        paths()


def test_music_catalog_defaults_to_user_library_not_software(monkeypatch, tmp_path):
    from tools.audio import music_library
    monkeypatch.delenv('MUSIC_LIBRARY_DIR', raising=False)
    monkeypatch.setattr('lib.paths.LIBRARY_DIR', tmp_path / 'shared')
    assert music_library.MusicLibrary()._library_dir() == tmp_path / 'shared/music'
