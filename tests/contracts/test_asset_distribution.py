"""Software update archives must not recirculate personal asset directories."""
from pathlib import Path

import pytest

from scripts.export_artist_update import _exportable


@pytest.mark.parametrize('name', ['artists/solo/artist.yaml', 'projects/film/artifacts/brief.json',
                                 'library/versions/artist/solo/v1/files/face.png', 'music_library/song.wav'])
def test_user_data_is_excluded_from_source_exports(name):
    assert not _exportable(Path(name))


@pytest.mark.parametrize('name', ['public/artists/solo/face.png', 'package/projects/film/scene.json',
                                 '../artists/_template/artist.yaml', '/absolute/source.py',
                                 'package\\artists\\solo\\face.png'])
def test_legacy_archive_entries_are_checked_regardless_of_package_prefix(name):
    from scripts.export_artist_update import _archive_exportable
    assert not _archive_exportable(name)


def test_template_and_product_ui_resources_remain_distributable():
    from scripts.export_artist_update import _archive_exportable
    assert _archive_exportable('openmontage-music-video/artists/_template/artist.yaml')
    assert _archive_exportable('openmontage-music-video/assets/logo.png')
