"""OpenRouter requests are fully mocked; never upload real project material."""
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from tools.analysis.openrouter_review import ENDPOINT, MODEL, OpenRouterReview
from tools.analysis.mv_judge import CRITERIA, MvJudge
from tools.analysis.shot_verifier import ShotVerifier
from tools.base_tool import ToolResult, ToolStatus
from lib.frames import file_sha256


@pytest.fixture
def inputs(tmp_path,monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY','mock-secret')
    for name in ('clip.mp4','master.wav','amor.png','fati.png','frame.jpg'):
        (tmp_path/name).write_bytes(name.encode())
    return {'operation':'physical','prompt':'Physical review', 'video_path':str(tmp_path/'clip.mp4'),
        'reference_map':{artist:[str(tmp_path/f'{artist}.png')] for artist in ('amor','fati')},
        'frames':[{'path':str(tmp_path/'frame.jpg'),'timestamp_seconds':1}]}


def reply(content=None,*,status=200,model=MODEL,cost=0,finish='stop'):
    return SimpleNamespace(status_code=status,headers={'Retry-After':'30'},json=lambda:{
        'id':'mock-request','model':model,'usage':{'cost':cost,'prompt_tokens':10,'completion_tokens':10},
        'choices':[{'finish_reason':finish,'message':{'content':content if content is not None else json.dumps({'pass':True,'reasons':['mock']})}}]})


def post(monkeypatch,response):
    call=Mock(return_value=response)
    monkeypatch.setattr('tools.analysis.openrouter_review.requests.post',call)
    return call


def test_missing_key_status_and_no_network(inputs,monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY')
    call=post(monkeypatch,reply())
    assert OpenRouterReview().get_status()==ToolStatus.UNAVAILABLE
    assert not OpenRouterReview().execute(inputs).success
    call.assert_not_called()


def test_payload_references_motion_and_pinned_free_route(inputs,monkeypatch):
    call=post(monkeypatch,reply())
    result=OpenRouterReview().execute(inputs)
    assert result.success and result.cost_usd==0 and result.model==MODEL
    assert result.data['request_id']=='mock-request'
    args,kwargs=call.call_args
    assert args==(ENDPOINT,)
    assert kwargs['headers']['Authorization']=='Bearer mock-secret'
    assert not kwargs['allow_redirects']
    body=kwargs['json']
    assert body['model']==MODEL and body['provider']['only']==['nvidia']
    assert body['provider']['allow_fallbacks'] is False
    assert body['provider']['max_price']['prompt']==0
    assert 'response_format' not in body and 'models' not in body
    content=body['messages'][1]['content']
    media=[part for part in content if part['type'] in ('video_url','image_url')]
    assert len(media)==4
    assert base64.b64decode(media[0]['video_url']['url'].split(',')[1])==b'clip.mp4'
    labels=[part['text'] for part in content if part['type']=='text']
    assert any('artist amor' in label for label in labels) and any('artist fati' in label for label in labels)
    assert 'mock-secret' not in json.dumps(result.data)


def test_full_mv_transmits_locked_master(inputs,monkeypatch):
    ratings={'criteria':{key:4 for group in CRITERIA.values() for key in group}}
    call=post(monkeypatch,reply(json.dumps(ratings)))
    params={**inputs,'operation':'mv_judge','audio_path':str(Path(inputs['video_path']).with_name('master.wav'))}
    result=OpenRouterReview().execute(params)
    assert result.success
    audio=next(p for p in call.call_args.kwargs['json']['messages'][1]['content'] if p['type']=='input_audio')
    assert audio['input_audio']['format']=='wav'
    assert base64.b64decode(audio['input_audio']['data'])==b'master.wav'


@pytest.mark.parametrize('status',[401,429,503])
def test_http_failures_no_retry(inputs,monkeypatch,status):
    call=post(monkeypatch,reply(status=status))
    result=OpenRouterReview().execute(inputs)
    assert not result.success and result.data['http_status']==status
    assert result.data['retry_after']=='30' and call.call_count==1


@pytest.mark.parametrize('answer',['not JSON','{"pass":"true","reasons":[]}','{"pass":true,"reasons":"ok"}'])
def test_malformed_review_rejected(inputs,monkeypatch,answer):
    call=post(monkeypatch,reply(answer))
    result=OpenRouterReview().execute(inputs)
    assert not result.success and result.data['usage']['prompt_tokens']==10
    assert result.model==MODEL and call.call_count==1


def test_fenced_json_accepted_but_truncated_output_rejected(inputs,monkeypatch):
    post(monkeypatch,reply('```json\n{"pass":false,"reasons":["bad hand"]}\n```'))
    assert OpenRouterReview().execute(inputs).success
    post(monkeypatch,reply(finish='length'))
    assert not OpenRouterReview().execute(inputs).success


def test_charge_model_change_and_unsupported_model(inputs,monkeypatch):
    call=post(monkeypatch,reply(cost=.03))
    result=OpenRouterReview().execute(inputs)
    assert not result.success and result.cost_usd==.03
    post(monkeypatch,reply(model='other/model'))
    assert not OpenRouterReview().execute(inputs).success
    call=post(monkeypatch,reply())
    assert not OpenRouterReview().execute({**inputs,'model':'nvidia/paid'}).success
    call.assert_not_called()
    with pytest.raises(ValueError):OpenRouterReview().estimate_cost({'model':'other'})


def test_missing_oversized_and_audio_omission_prevent_upload(inputs,monkeypatch):
    call=post(monkeypatch,reply())
    for params in ({**inputs,'max_media_bytes':1},{**inputs,'video_path':'missing.mp4'},
                   {**inputs,'operation':'mv_judge'}):
        assert not OpenRouterReview().execute(params).success
    call.assert_not_called()


def test_transport_failure_redacts_exception(inputs,monkeypatch):
    call=post(monkeypatch,reply())
    call.side_effect=requests.Timeout('mock-secret sensitive request')
    result=OpenRouterReview().execute(inputs)
    assert not result.success and 'mock-secret' not in result.error and call.call_count==1


def test_idempotency_changes_when_media_changes(inputs):
    tool=OpenRouterReview()
    before=tool.idempotency_key(inputs)
    Path(inputs['reference_map']['amor'][0]).write_bytes(b'changed')
    assert tool.idempotency_key(inputs)!=before


def test_selector_integration_and_failed_judge_stays_advisory(inputs,monkeypatch):
    tool=OpenRouterReview()
    registry=SimpleNamespace(ensure_discovered=lambda:None,get_by_capability=lambda c:[tool])
    verifier=ShotVerifier(registry)
    monkeypatch.setattr(verifier,'_probe',lambda path:{'duration':5,'fps':24})
    monkeypatch.setattr('tools.analysis.shot_verifier.FrameSampler.execute',lambda self,p:ToolResult(success=True,data={'frames':inputs['frames']}))
    ratings={'script_lyrics':4,'locked_traits':4,'identity':{'amor':4,'fati':3}}
    call=post(monkeypatch,reply())
    call.side_effect=[reply(),reply(json.dumps(ratings))]
    params={'input_path':inputs['video_path'],'shot':{'id':'one','start_seconds':0,'end_seconds':4,
        'generation_context':{'artist_ids':['amor','fati']}},'reference_map':inputs['reference_map'],
        'generation_log':{'reference_map':inputs['reference_map'],'submitted_reference_images':[p for refs in inputs['reference_map'].values() for p in refs]},
        'person_count_evidence':{'source_sha256':file_sha256(inputs['video_path']),'producer':'mock','counts':[2]},
        'preferred_provider':'openrouter','preferred_tool':'openrouter_review','model':MODEL}
    result=verifier.execute(params)
    assert result.success and result.data['verification']['eligible'] and call.call_count==2
    assert result.data['verification']['model']==MODEL
    call=post(monkeypatch,reply(status=429))
    result=MvJudge(registry).execute({'video_path':inputs['video_path'],
        'audio_path':str(Path(inputs['video_path']).with_name('master.wav')),'model':MODEL})
    assert result.success and result.data['judge']['status']=='failed'
    assert result.data['judge']['advisory_only'] and not result.data['judge']['publish_gate']

@pytest.mark.parametrize('ratings',[
    {'script_lyrics':4,'locked_traits':4,'identity':{'amor':4}},
    {'script_lyrics':float('nan'),'locked_traits':4,'identity':{'amor':4,'fati':4}},
    {'script_lyrics':4,'locked_traits':4,'identity':{'amor':4,'fati':6}},
])
def test_invalid_per_artist_scores_rejected(inputs,monkeypatch,ratings):
    post(monkeypatch,reply(json.dumps(ratings)))
    result=OpenRouterReview().execute({**inputs,'operation':'score'})
    assert not result.success and 'review' not in result.data


def test_empty_key_and_info_are_offline(inputs,monkeypatch):
    call=post(monkeypatch,reply())
    monkeypatch.setenv('OPENROUTER_API_KEY','  ')
    tool=OpenRouterReview()
    assert tool.get_info()['status']=='unavailable'
    monkeypatch.setenv('OPENROUTER_API_KEY','mock-key')
    assert tool.get_info()['status']=='available'
    call.assert_not_called()


def test_qwen_requires_paid_envelope_before_upload(inputs,monkeypatch):
    from tools.analysis.openrouter_review import QWEN_MODEL
    call=post(monkeypatch,reply(model=QWEN_MODEL))
    tool=OpenRouterReview()
    params={**inputs,'model':QWEN_MODEL}
    assert tool.estimate_cost(params)==pytest.approx(.15385024)
    for changes in ({},{'paid_call_approved':True,'approved_cost_usd':.01},
                    {'paid_call_approved':True,'approved_cost_usd':float('nan')}):
        assert not tool.execute({**params,**changes}).success
    call.assert_not_called()
    call.return_value=reply(model=QWEN_MODEL,cost=.001)
    result=tool.execute({**params,'paid_call_approved':True,'approved_cost_usd':.2})
    assert result.success and result.cost_usd==.001 and result.model==QWEN_MODEL
    payload=call.call_args.kwargs['json']
    assert payload['model']==QWEN_MODEL and payload['provider']['only']==['alibaba']
    assert not payload['provider']['allow_fallbacks']
    assert payload['provider']['max_price']['prompt']==.15
    assert payload['response_format']=={'type':'json_object'}


def test_paid_wrapper_forwards_approval_and_remaining_envelope(inputs,monkeypatch):
    from tools.analysis.openrouter_review import QWEN_MODEL
    from tools.analysis._review import call_reviewer
    call=post(monkeypatch,reply(model=QWEN_MODEL,cost=.002))
    params={'model':QWEN_MODEL,'paid_call_approved':True,'approved_cost_usd':.2}
    result,review=call_reviewer(OpenRouterReview(),params,inputs)
    assert result.success and review['pass'] and result.cost_usd==.002
    assert call.call_args.kwargs['json']['model']==QWEN_MODEL


def test_qwen_missing_usage_and_overbudget_fail_with_billing_evidence(inputs,monkeypatch):
    from tools.analysis.openrouter_review import QWEN_MODEL
    params={**inputs,'model':QWEN_MODEL,'paid_call_approved':True,'approved_cost_usd':.2}
    post(monkeypatch,reply(model=QWEN_MODEL,cost=None))
    result=OpenRouterReview().execute(params)
    assert not result.success and result.cost_usd is None and 'unknown' in result.error
    post(monkeypatch,reply(model=QWEN_MODEL,cost=.3))
    result=OpenRouterReview().execute(params)
    assert not result.success and result.cost_usd==.3


def test_paid_timeout_and_judge_missing_cost_stay_unknown(inputs,monkeypatch):
    from tools.analysis.openrouter_review import QWEN_MODEL
    call=post(monkeypatch,reply())
    call.side_effect=requests.Timeout('private details')
    params={**inputs,'model':QWEN_MODEL,'paid_call_approved':True,'approved_cost_usd':.16}
    result=OpenRouterReview().execute(params)
    assert not result.success and result.cost_usd is None
    post(monkeypatch,reply(model=QWEN_MODEL,cost=None))
    tool=OpenRouterReview()
    registry=SimpleNamespace(ensure_discovered=lambda:None,get_by_capability=lambda c:[tool])
    result=MvJudge(registry).execute({'video_path':inputs['video_path'],
        'audio_path':str(Path(inputs['video_path']).with_name('master.wav')),
        'model':QWEN_MODEL,'paid_call_approved':True,'approved_cost_usd':.16})
    assert result.success and result.cost_usd is None
    assert result.data['judge']['status']=='failed' and result.data['judge']['model']==QWEN_MODEL


def test_http_error_preserves_safe_provider_diagnostic(inputs, monkeypatch):
    body={'id':'error-request', 'error':{'code':400,'message':'Bad media mock-secret data:video/mp4;base64,AAAA',
        'metadata':{'provider_name':'Alibaba','raw':json.dumps({'error':{'code':'InvalidParameter','message':'Unsupported media combination'}})},
        'request':{'authorization':'mock-secret'}}}
    call=post(monkeypatch,SimpleNamespace(status_code=400,headers={},json=lambda:body))
    result=OpenRouterReview().execute(inputs)
    assert result.data['request_id']=='error-request'
    assert result.data['provider_error']['code']==400
    assert result.data['provider_error']['upstream']['message']=='Unsupported media combination'
    saved=json.dumps(result.data)
    assert 'mock-secret' not in saved and 'base64,AAAA' not in saved and 'authorization' not in saved
    assert call.call_count==1


def test_http_200_error_envelope_not_misreported_as_model_mismatch(inputs, monkeypatch):
    body={'error':{'code':502,'message':'Provider unavailable'}}
    post(monkeypatch,SimpleNamespace(status_code=200,headers={},json=lambda:body))
    result=OpenRouterReview().execute(inputs)
    assert not result.success and 'error envelope' in result.error
    assert result.data['provider_error']['code']==502


def test_http_non_json_error_keeps_status(inputs, monkeypatch):
    response=SimpleNamespace(status_code=400,headers={},json=Mock(side_effect=ValueError('html')))
    post(monkeypatch,response)
    result=OpenRouterReview().execute(inputs)
    assert result.data['http_status']==400 and not result.success


def test_verifier_preserves_http_error_evidence(inputs, monkeypatch):
    tool=OpenRouterReview()
    registry=SimpleNamespace(ensure_discovered=lambda:None,get_by_capability=lambda c:[tool])
    verifier=ShotVerifier(registry)
    monkeypatch.setattr(verifier,'_deterministic',lambda p:{'pass':True,'source_sha256':'mock'})
    monkeypatch.setattr('tools.analysis.shot_verifier.FrameSampler.execute',lambda self,p:ToolResult(success=True,data={'frames':inputs['frames']}))
    call=post(monkeypatch,SimpleNamespace(status_code=400,headers={},json=lambda:{
        'id':'request-400','error':{'code':400,'message':'InvalidParameter'}}))
    result=verifier.execute({'input_path':inputs['video_path'],'shot':{},'model':MODEL})
    report=result.data['verification']
    assert report['status']=='unreviewed' and report['score'] is None
    assert report['review_calls'][0]['request_id']=='request-400'
    assert report['review_calls'][0]['provider_error']['message']=='InvalidParameter'
    assert call.call_count==1
