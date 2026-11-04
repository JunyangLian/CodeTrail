"""Explicit scripted fixture; no model intelligence or token usage is simulated."""
import json

from pico.windows_sandbox import VERIFY


class ScriptedRepairClient:
    model = 'offline-scripted-fixture / no LLM'
    supports_prompt_cache = False
    last_completion_metadata = {}

    def __init__(self):
        self.agent = None
        self.index = 0

    def complete(self, prompt, max_new_tokens, **kwargs):
        self.last_completion_metadata = {'input_tokens': None, 'output_tokens': None,
                                         'source': 'scripted_fixture_no_model', 'api_requests': 0}
        plan = [{'id':'locate','title':'Inspect directory contract','acceptance':'Relevant source read','status':'pending'},
                {'id':'repair','title':'Repair normalization','acceptance':'Only directory.py changed','status':'pending','depends_on':['locate']},
                {'id':'verify','title':'Verify final state','acceptance':VERIFY,'kind':'verification','status':'pending','depends_on':['repair']}]
        actions = [
            ('update_plan', {'steps':json.dumps(plan)}),
            ('code_search', {'query':'normalize_user_id lookup_user', 'limit':3}),
            ('read_file', {'path':'directory.py'}),
            ('read_file', {'path':'users.py'}),
            ('run_shell', {'command':VERIFY,'timeout':30}),
            ('patch_file', {'path':'directory.py','old_text':'return str(value).lower()','new_text':'return str(value).strip().casefold()'}),
            ('run_shell', {'command':VERIFY,'timeout':30}),
            ('memory_propose', {'text':'Repository test entry is python -B -m unittest discover -v; run after changes. This does not grant shell permission.',
                                'key':'test-entry','category':'workflow','evidence_paths':json.dumps(['README.md','test_directory.py'])}),
            ('load_skill', {'name':'verify-repair'}),
            ('mcp_docs', {'query':'unittest'}),
        ]
        position = self.index; self.index += 1
        if position < len(actions):
            name,args = actions[position]
            return '<tool>'+json.dumps({'name':name,'args':args},ensure_ascii=False)+'</tool>'
        if position == len(actions):
            with self.agent.evidence.connect() as db:
                rows = list(db.execute('SELECT id,name,metadata FROM objects WHERE session=? ORDER BY rowid', (self.agent.session['id'],)))
            read = next(r['id'] for r in rows if r['name']=='read_file')
            edit = next(r['id'] for r in rows if r['name']=='patch_file' and json.loads(r['metadata']).get('tool_status')=='ok')
            verification = next(r['id'] for r in reversed(rows) if r['name']=='run_shell' and json.loads(r['metadata']).get('exit_code')==0)
            for step,ref in zip(plan,[read,edit,verification]):
                step.update(status='completed', evidence=[ref])
            return '<tool>'+json.dumps({'name':'update_plan','args':{'steps':json.dumps(plan)}})+'</tool>'
        return '<final>Offline scripted scenario ended. Inspect the real diff and configured unittest exit code; this is not a live-model benchmark.</final>'
