from unittest.mock import patch
import pytest
from pico import Pico,SessionStore,WorkspaceContext
from pico.providers.clients import FakeModelClient


@pytest.mark.parametrize('rg_available',[True,False])
def test_sensitive_file_read_and_root_search_do_not_expose_known_paths(tmp_path,rg_available):
    (tmp_path/'.env').write_text('UNKNOWN_CANARY_VALUE')
    (tmp_path/'private.pem').write_text('UNKNOWN_CANARY_VALUE')
    (tmp_path/'public.py').write_text('PUBLIC_SEARCH_MARKER')
    (tmp_path/'.env.example').write_text('EXAMPLE=placeholder')
    agent=Pico(FakeModelClient([]),WorkspaceContext.build(tmp_path,repo_root_override=tmp_path),SessionStore(tmp_path/'.pico/sessions'))
    result=agent.execute_tool('read_file',{'path':'.env'})
    assert result.metadata['security_event_type']=='sensitive_path'
    if rg_available:
        result=agent.execute_tool('search',{'pattern':'UNKNOWN_CANARY_VALUE|PUBLIC_SEARCH_MARKER'})
    else:
        with patch('pico.tools.shutil.which',return_value=None):
            result=agent.execute_tool('search',{'pattern':'UNKNOWN_CANARY_VALUE'})
    assert 'UNKNOWN_CANARY_VALUE' not in result.content
    assert 'EXAMPLE=placeholder' in agent.execute_tool('read_file',{'path':'.env.example'}).content
