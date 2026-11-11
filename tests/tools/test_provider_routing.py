"""Explicit routes accept local model paths without weakening closed catalogs."""
from types import SimpleNamespace
from tools.provider_routing import filter_explicit_route


def provider(properties, catalog=None):
    return SimpleNamespace(
        name='local-tts', provider='local', capability='tts',
        input_schema={'properties': properties},
        get_info=lambda: {'model_catalog': catalog or {}},
    )


def test_open_model_path_routes_to_local_provider():
    tool = provider({'model': {'type': 'string'}})
    assert filter_explicit_route(
        {'model': '/voices/custom.onnx', 'preferred_tool': tool.name}, [tool]
    ) == [tool]


def test_local_model_path_routes_automatically_when_provider_confirms_it(tmp_path):
    from tools.audio.piper_tts import PiperTTS

    model = tmp_path / 'custom.onnx'
    model.write_bytes(b'mock model')
    tool = PiperTTS()
    assert filter_explicit_route({'model': str(model)}, [tool]) == [tool]


def test_unknown_model_cannot_bypass_closed_enum():
    tool = provider({'model': {'type': 'string', 'enum': ['supported']}})
    assert filter_explicit_route({'model': 'unknown'}, [tool]) == []
    assert filter_explicit_route({'model': 'supported'}, [tool]) == [tool]


def test_open_input_with_declared_catalog_still_checks_catalog():
    tool = provider({'model_id': {'type': 'string'}}, {'supported': {}})
    assert filter_explicit_route({'model_id': 'unknown'}, [tool]) == []
    assert filter_explicit_route({'model_id': 'supported'}, [tool]) == [tool]


def test_provider_without_model_input_is_not_an_open_model_route():
    tool = provider({'text': {'type': 'string'}})
    assert filter_explicit_route({'model': '/voices/custom.onnx'}, [tool]) == []
