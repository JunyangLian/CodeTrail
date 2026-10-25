from scripts.diagnose_api_timeout import synthetic_prompt
from scripts.diagnose_timeout_semantics import run_lab


def test_synthetic_length_is_exact_and_has_fixed_response_instruction():
    for length in (128,8192,32768):
        prompt=synthetic_prompt(length)
        assert len(prompt)==length
        assert prompt.endswith('Reply only OK.')


def test_actual_socket_idle_timeouts_and_retry_budget_are_not_a_wall_deadline(monkeypatch):
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost')
    rows=run_lab()['rows']
    assert [r['phase'] for r in rows[:2]]==['headers','body']
    assert all(not r['ok'] and r['error_type']=='TimeoutError' for r in rows[:2])
    periodic=rows[-1]
    assert periodic['ok'] and periodic['transport_attempts']==1
    assert periodic['elapsed_ms']>1000*periodic['nominal_retry_budget_seconds']
