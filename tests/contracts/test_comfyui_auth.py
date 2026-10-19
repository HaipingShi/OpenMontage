import sys
from types import SimpleNamespace

from tools._comfyui.client import ComfyUIClient


def test_video_key_authenticates_all_rest_operations(monkeypatch, tmp_path):
    monkeypatch.setenv('COMFYUI_VIDEO_SERVER_URL', 'https://h3.example')
    monkeypatch.setenv('COMFYUI_VIDEO_API_KEY', 'test-key')
    calls = []
    def respond(url, **kwargs):
        calls.append((url, kwargs))
        value = ({'prompt_id': 'p'} if url.endswith('/prompt') else
                 {'p': {'outputs': {}, 'status': {'completed': True, 'status_str': 'success'}}} if '/history/' in url else
                 {'name': 'image.png'} if '/upload/' in url else {})
        return SimpleNamespace(status_code=200, json=lambda: value,
                               raise_for_status=lambda: None, content=b'video')
    monkeypatch.setattr('tools._comfyui.client.requests.get', respond)
    monkeypatch.setattr('tools._comfyui.client.requests.post', respond)
    c = ComfyUIClient(capability='video')
    assert c.is_available()
    c.list_models()
    c.has_node('MiniMaxH3ImageToVideo')
    c.submit({})
    c._history_entry('p')
    c.download('a.mp4', '', tmp_path/'a.mp4')
    image = tmp_path/'image.png'
    image.write_bytes(b'png')
    c.upload_image(image, 'image.png')
    assert calls
    assert all(opts.get('headers', {}).get('Authorization') == 'Bearer test-key'
               for _, opts in calls)


def test_video_key_does_not_leak_to_explicit_other_server(monkeypatch):
    monkeypatch.setenv('COMFYUI_VIDEO_SERVER_URL', 'https://h3.example')
    monkeypatch.setenv('COMFYUI_VIDEO_API_KEY', 'test-key')
    c = ComfyUIClient('https://other.example', capability='video')
    assert not c._auth_headers


def test_websocket_uses_the_same_auth(monkeypatch):
    monkeypatch.setenv('COMFYUI_VIDEO_SERVER_URL', 'https://h3.example')
    monkeypatch.setenv('COMFYUI_VIDEO_API_KEY', 'test-key')
    c = ComfyUIClient(capability='video')
    monkeypatch.setattr(c, '_history_entry_if_reachable', lambda _: None)
    calls = []
    def connect(url, **kwargs):
        calls.append(kwargs)
        raise RuntimeError('stop before waiting')
    monkeypatch.setitem(sys.modules, 'websocket', SimpleNamespace(create_connection=connect))
    import pytest
    with pytest.raises(RuntimeError, match='stop before waiting'):
        c.wait_ws('p', timeout=1)
    assert calls[0]['header'] == {'Authorization': 'Bearer test-key'}
