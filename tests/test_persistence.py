import json
from pathlib import Path
from unittest.mock import patch
import pytest
from pico.persistence import write_json_atomic, REPLACE_DELAYS


def locked():
    error = PermissionError(13, 'sharing violation')
    error.winerror = 5
    return error


def test_transient_failure_recovers_without_deleting_target_or_exposing_state(tmp_path):
    target = tmp_path / 'state.json';target.write_text('{"old":true}')
    original = Path.replace;calls = []
    def replace(source, destination):
        calls.append(source)
        if len(calls) <= 2:
            assert target.read_text() == '{"old":true}'
            raise locked()
        return original(source, destination)
    with patch.object(Path, 'replace', replace), patch('pico.persistence.time.sleep') as sleep:
        write_json_atomic(target, {'private': 'never log payload'})
    assert json.loads(target.read_text()) == {'private': 'never log payload'}
    events = [json.loads(l) for l in (tmp_path/'persistence.jsonl').read_text().splitlines()]
    assert [e['event'] for e in events] == ['persistence_retry','persistence_retry','persistence_recovered']
    assert sleep.call_count == 2 and len(set(calls)) == 1
    assert 'never log payload' not in (tmp_path/'persistence.jsonl').read_text()


def test_exhaustion_keeps_old_target_failed_temp_and_all_attempts(tmp_path):
    target = tmp_path/'state.json';target.write_text('original')
    with patch.object(Path, 'replace', side_effect=locked()) as replace, patch('pico.persistence.time.sleep'):
        with pytest.raises(PermissionError):write_json_atomic(target, {'new':True})
    assert replace.call_count == len(REPLACE_DELAYS)+1
    assert target.read_text() == 'original'
    assert len(list(tmp_path.glob('state.json.*.tmp'))) == 1
    events=[json.loads(l) for l in (tmp_path/'persistence.jsonl').read_text().splitlines()]
    assert events[-1]['event']=='persistence_failed' and not events[-1]['will_retry']


def test_unrelated_io_error_is_not_retried(tmp_path):
    with patch.object(Path,'replace',side_effect=OSError(28,'disk full')) as replace:
        with pytest.raises(OSError):write_json_atomic(tmp_path/'state.json',{})
    assert replace.call_count == 1
