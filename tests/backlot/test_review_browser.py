"""Human annotation persistence against a real browser, using unreadable fake media.

No production video is loaded or decoded by these tests.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip('playwright.sync_api')
from playwright.sync_api import sync_playwright, expect

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def review_browser(tmp_path):
    project = tmp_path / 'projects' / 'review-test'
    (project / 'artifacts').mkdir(parents=True)
    (project / 'assets/video').mkdir(parents=True)
    for name in ('one', 'two'):
        (project / f'assets/video/{name}.mp4').write_bytes(b'unreadable-test-video')
    def write(name, value):
        (project / name).write_text(json.dumps(value))
    write('project.json', {'project_id':'review-test', 'title':'标注测试', 'pipeline_type':'music-video'})
    write('artifacts/scene_plan.json', {'version':'1.0', 'scenes':[
        {'id':name, 'description':'测试镜头 '+name, 'start_seconds':i*5, 'end_seconds':i*5+5}
        for i, name in enumerate(('one','two'))]})
    write('artifacts/asset_manifest.json', {'version':'1.0', 'assets':[
        {'id':name, 'scene_id':name, 'type':'video', 'source_tool':'test',
         'path':f'assets/video/{name}.mp4', 'duration_seconds':5} for name in ('one','two')]})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    log=(tmp_path/'server.log').open('w')
    server=subprocess.Popen([sys.executable,'-m','backlot','serve','--port',str(port)],cwd=REPO,
        env={**os.environ,'OPENMONTAGE_PROJECTS_DIR':str(project.parent)},stdout=log,stderr=log)
    base=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(base+'/api/health',timeout=1).close();break
            except OSError:time.sleep(.1)
        else:raise RuntimeError((tmp_path/'server.log').read_text())
        with sync_playwright() as pw:
            options={'channel':'chrome'} if Path('/Applications/Google Chrome.app').exists() else {}
            browser=pw.chromium.launch(headless=True,**options)
            context=browser.new_context(viewport={'width':1500,'height':1000})
            media_requests=[]
            def local_only(route):
                if '.mp4' in route.request.url:
                    media_requests.append(route.request.url);route.abort()
                elif route.request.url.startswith((base,'blob:','data:')):route.continue_()
                else:route.abort()
            context.route('**/*',local_only)
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            yield page,context,base,project,errors,media_requests
            browser.close()
    finally:
        server.terminate();server.wait(timeout=10);log.close()


def test_annotations_autosave_reload_export_and_handoff(review_browser):
    page,context,base,project,errors,media_requests=review_browser
    page.goto(base+'/r/review-test')
    expect(page.locator('#shots .shot')).to_have_count(2)
    assert page.locator('#video').get_attribute('src') is None
    page.locator('#mark-whole').click()
    page.locator('#note').fill('人物脚底悬空')
    page.locator('#desired-change').fill('保持脚接触地面，固定重心')
    page.locator('#severity').select_option('blocking')
    page.locator('#categories input[value=action]').check()
    page.locator('#start').fill('1.25')
    page.locator('#end').fill('2.50')
    page.locator('#save-now').click()
    expect(page.locator('#save-state')).to_have_text('已保存到项目')
    page.reload()
    expect(page.locator('#note')).to_have_value('人物脚底悬空')
    expect(page.locator('#severity')).to_have_value('blocking')
    page.locator('#next').click()
    page.locator('#verdict').select_option('approved')
    page.locator('#submit').click()
    expect(page.locator('#notice')).to_contain_text('2 段审核记录已提交')
    handoff=json.loads((project/'reviews/review_handoff.json').read_text())
    assert handoff['generation_authorized'] is False
    issue=handoff['sources'][0]['issues'][0]
    assert issue['severity']=='blocking' and issue['desired_change']=='保持脚接触地面，固定重心'
    assert issue['start_seconds']==1.25 and issue['end_seconds']==2.5
    with page.expect_download() as event:page.locator('#export').click()
    exported=json.loads(Path(event.value.path()).read_text())
    assert len(exported['sources'])==2
    assert not media_requests and not errors


def test_conflict_keeps_draft_and_invalid_time_cannot_submit(review_browser):
    page,context,base,project,errors,media_requests=review_browser
    page.goto(base+'/r/review-test');expect(page.locator('#shots .shot')).to_have_count(2)
    other=context.new_page();other.goto(base+'/r/review-test');expect(other.locator('#shots .shot')).to_have_count(2)
    page.locator('#mark-whole').click();page.locator('#note').fill('第一窗口反馈');page.locator('#save-now').click()
    expect(page.locator('#save-state')).to_have_text('已保存到项目')
    other.locator('#mark-whole').click();other.locator('#note').fill('第二窗口反馈');other.locator('#save-now').click()
    expect(other.locator('#save-state')).to_contain_text('另一窗口已有修改')
    assert '第二窗口反馈' in other.evaluate('JSON.stringify(localStorage)')
    assert json.loads((project/'reviews/shot_reviews.json').read_text())['sources']['assets/video/one.mp4']['issues'][0]['note']=='第一窗口反馈'
    other.locator('#reload').click();expect(other.locator('#note')).to_have_value('第一窗口反馈')
    assert '第二窗口反馈' in other.evaluate('JSON.stringify(localStorage)')
    page.locator('#start').fill('2.00');page.locator('#end').fill('1.00');page.locator('#submit').click()
    expect(page.locator('#notice')).to_contain_text('仍有标注未保存')
    assert not (project/'reviews/review_handoff.json').exists()
    assert not media_requests and not errors
