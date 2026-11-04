import json
from unittest.mock import patch

import pytest

from pico.retrieval import RepositoryIndex, SiliconFlowEmbedding, normalized


def test_source_index_has_methods_import_context_and_ignores_internal_assets(tmp_path):
    (tmp_path/'logic.py').write_text('import math\nclass Cart:\n    def total(self, x):\n        return math.ceil(x)\n')
    for folder in ('.pico','benchmarks','artifacts'):
        (tmp_path/folder).mkdir()
        (tmp_path/folder/'gold.py').write_text('def hidden_reference(): pass')
    (tmp_path/'.env').write_text('secret=example')
    index=RepositoryIndex(tmp_path)
    result=index.search('Cart total',5)
    assert any(c['symbol']=='total' and 'math.ceil' in c['calls'] for c in result)
    assert not any('gold' in c['path'] or c['path']=='.env' for c in index.chunks)
    (tmp_path/'logic.py').unlink(); assert index.search('total')==[]


def test_dense_adapter_uses_real_schema_reorders_and_validates():
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self,*args): return json.dumps({'model':'BAAI/bge-m3','data':[{'index':1,'embedding':[0.,2.]},{'index':0,'embedding':[3.,0.]}],'usage':{'total_tokens':4}}).encode()
    with patch.dict('os.environ',{'SILICONFLOW_API_KEY':'only-unit-key'}), patch('urllib.request.urlopen', return_value=Response()) as request:
        client=SiliconFlowEmbedding()
        assert client.encode(['alpha','beta'])==[[1.,0.],[0.,1.]]
        assert json.loads(request.call_args.args[0].data)['model']=='BAAI/bge-m3'
        assert client.last_usage['total_tokens']==4


@pytest.mark.parametrize('value', [[],[0,0],[float('nan'),1],[float('inf'),1],[True,1]])
def test_invalid_vectors_rejected(value):
    with pytest.raises(ValueError): normalized(value)


def test_dense_cache_model_namespace_and_explicit_degradation(tmp_path):
    # Fixture vectors test cache/protocol only, not semantic retrieval quality.
    class FixtureEmbedding:
        identity='unit-fixture-vectors-v1'; calls=0; last_usage=None
        def encode(self,texts):
            self.calls+=1
            return [[1.,len(t)%3+1.] for t in texts]
    provider=FixtureEmbedding(); index=RepositoryIndex(tmp_path,provider)
    (tmp_path/'a.py').write_text('def compute_tax(x):\n return x*.1\n')
    assert index.search('tax') and index.last_audit['mode']=='hybrid'
    first=provider.calls; index.search('tax'); assert provider.calls==first
    class Broken:
        identity='different-model'; last_usage=None
        def encode(self,texts): raise TimeoutError('embedding unavailable')
    index.embedding=Broken()
    assert index.search('tax') and index.last_audit['mode']=='lexical'
    assert index.last_audit['degradation']['type']=='TimeoutError'


def test_memory_filters_candidates_and_source_changes_before_dense(tmp_path):
    from pico.runtime import Pico, SessionStore
    from pico.workspace import WorkspaceContext
    from pico.providers.clients import FakeModelClient
    (tmp_path/'a.py').write_text('def total(x): return x')
    a=Pico(FakeModelClient([]),WorkspaceContext.build(tmp_path),SessionStore(tmp_path/'.pico/sessions'),harness_profile='workbench')
    store=a.memory.knowledge
    candidate=store.propose('The total contract accepts x','total-contract',['a.py'])
    assert not a.memory.retrieval_candidates('total contract')
    active=store.activate(candidate['id'],1,reviewer='unit operator')
    assert a.memory.retrieval_candidates('total contract')[0]['memory_id']==active['id']
    (tmp_path/'a.py').write_text('def total(x,y): return x+y')
    assert not a.memory.retrieval_candidates('total contract')
    assert store.get(active['id'])['status']=='stale'
