from pico.evaluation.baseline_assets import verify_baseline_assets
from scripts.run_repair_regression_v2 import CONFIG,TASK_IDS

def test_original_gold_and_original_runtime_remain_verifiable_after_upgrade():
    result=verify_baseline_assets()
    assert result['original_runtime_verified_in_archive']
    assert not result['current_runtime_matches_v1']
    assert 'pico/agent_loop.py' in result['upgraded_runtime_paths']

def test_v2_is_a_declared_separate_targeted_batch_with_unchanged_model_and_budget():
    assert len(TASK_IDS)==len(set(TASK_IDS))==6
    assert CONFIG['repetitions']==2 and CONFIG['concurrency']==3
    assert CONFIG['model']=='zai-org/GLM-5.3-Flash'
    assert CONFIG['max_steps']==20 and CONFIG['max_new_tokens']==3072
    assert CONFIG['request_timeout_seconds']==180
    assert 'not a replacement' in CONFIG['scope']
