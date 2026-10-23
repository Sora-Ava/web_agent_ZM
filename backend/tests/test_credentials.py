import json
import pytest
from pydantic import SecretStr, ValidationError
from app.config import Settings
from app.workflows.models import WorkflowDefinition, validate_parameters


def configured():
    return Settings(_env_file=None, credentials_json=SecretStr(json.dumps({'erp':{'origin':'https://erp.test','username':'private-user','password':'pass"word\\secret'}})))


def test_credentials_metadata_resolution_and_safe_errors():
    c=configured()
    assert c.credentials.resolve('erp','https://erp.test:443').password.get_secret_value()=='pass"word\\secret'
    assert c.credentials.metadata()==[{'name':'erp','origin':'https://erp.test:443','ready':True}]
    with pytest.raises(ValueError,match='来源'):
        c.credentials.resolve('erp','https://wrong.test')
    with pytest.raises(ValueError,match='凭据'):
        c.credentials.resolve('raw-password','https://erp.test')
    bad=Settings(_env_file=None,credentials_json=SecretStr('{"password":"SYNTHETIC_SECRET"'))
    with pytest.raises(ValueError) as e: _=bad.credentials
    assert 'SYNTHETIC_SECRET' not in str(e.value)
    payload={'tool':json.dumps({'nested':'pass"word\\secret private-user'})}
    assert 'private-user' not in json.dumps(c.scrub(payload))
    assert 'pass' not in c.redact('pass"word\\secret')


def test_url_credential_parameters_and_login_step_validation():
    definition={'schema_version':2,'name':'Login','site_key':'https://erp.test:443','entry_url':'https://erp.test/login','parameters':[{'key':'url','label':'网址','kind':'url','default':'https://erp.test/login'},{'key':'account','label':'凭据','kind':'credential','default':'erp'}], 'steps':[{'id':'login','name':'登录','login':{'url':'https://erp.test/login','url_parameter':'url','credential_parameter':'account','username':{'kind':'css','value':'#user'},'password':{'kind':'css','value':'#pass'},'submit':{'kind':'css','value':'#submit'},'success':{'kind':'css','value':'#ready'}}}]}
    d=WorkflowDefinition.model_validate(definition)
    assert validate_parameters(d,{},configured().credentials)=={'url':'https://erp.test/login','account':'erp'}
    with pytest.raises(ValueError):validate_parameters(d,{'url':'https://other.test'},configured().credentials)
    # Unknown references are valid until a real login form needs credentials.
    assert validate_parameters(d,{'account':'missing-ref'},configured().credentials)['account']=='missing-ref'
    with pytest.raises(ValueError):validate_parameters(d,{'account':'x'*81},configured().credentials)
    foreign=configured()
    foreign.credentials_json=SecretStr(json.dumps({'foreign':{'origin':'https://other.test','username':'synthetic-user','password':'synthetic-password'}}))
    with pytest.raises(ValueError,match='来源'):validate_parameters(d,{'account':'foreign'},foreign.credentials)
    definition['steps'][0]['action']={'kind':'fill','target':{'kind':'css','value':'#x'},'value':'x'}
    with pytest.raises(ValidationError):WorkflowDefinition.model_validate(definition)
