"""Build holdout assets before any Agent run; validate buggy/fixed upstream states."""
import ast
import copy
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys
import textwrap

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pico.evaluation.repair_suite import load_json,write_json,verify_pristine,digest,VERIFY_COMMAND

DEST=ROOT/'benchmarks/final_agent_v1'
CACHE=ROOT/'tmp/holdout-discovery'


def test_source(imports,bodies):
    source='import unittest\n'+imports+'\n\nclass RepairTests(unittest.TestCase):\n'
    for i,body in enumerate(bodies,1):
        source+=f'    def test_{i:02d}(self):\n'+textwrap.indent(textwrap.dedent(body).strip()+'\n','        ')
    return source


def specifications():
    more='import more_itertools as mi\n'
    dotenv='import dotenv\nfrom io import StringIO\nfrom pathlib import Path\nimport tempfile\nimport os\nfrom unittest.mock import patch\n'
    return {
      'bucket_falsey':('single_file','修复 bucket 的 validator 契约：合法但布尔值为假的可调用对象仍必须被调用；None 才表示默认允许。保持普通 validator、成员判断及迭代行为。',more,[
        'class Deny(set):\n    def __call__(self,key): return key in self\nb=mi.bucket([1,2,1],key=lambda x:x,validator=Deny())\nself.assertEqual(list(b[1]),[])',
        'class Deny(set):\n    def __call__(self,key): return key in self\nb=mi.bucket([1,2],key=lambda x:x,validator=Deny())\nself.assertNotIn(1,b)\nself.assertEqual(set(b),set())',
        'b=mi.bucket([1,2,1],key=lambda x:x)\nself.assertEqual(list(b[1]),[1,1])',
        'b=mi.bucket([1,2,3],key=lambda x:x,validator=lambda key:key<3)\nself.assertEqual(list(b[3]),[])\nself.assertEqual(list(b[2]),[2])']),
      'constrained_items':('single_file','修复 constrained_batches 的最大项目数量校验：max_count 给定时必须大于零，空输入也应校验；保持 max_size、strict 和正常分批契约。',more,[
        'with self.assertRaises(ValueError): list(mi.constrained_batches([b"a"],3,max_count=0))',
        'with self.assertRaises(ValueError): list(mi.constrained_batches([b"a"],3,max_count=-1))',
        'with self.assertRaises(ValueError): list(mi.constrained_batches([],3,max_count=0))',
        'self.assertEqual(list(mi.constrained_batches([b"a",b"b",b"c"],9,max_count=2)),[(b"a",b"b"),(b"c",)])',
        'self.assertEqual(list(mi.constrained_batches([b"a",b"bb"],3)),[(b"a",b"bb")])']),
      'numeric_reverse':('single_file','修复 numeric_range 反向迭代：空区间应返回空迭代器而非抛异常，保持普通、负步长以及非整数区间行为。',more,[
        'self.assertEqual(list(reversed(mi.numeric_range(0))),[])',
        'self.assertEqual(list(reversed(mi.numeric_range(4,1))),[])',
        'self.assertEqual(list(reversed(mi.numeric_range(1,4,-1))),[])',
        'self.assertEqual(list(reversed(mi.numeric_range(1,5))),[4,3,2,1])',
        'from decimal import Decimal\nself.assertEqual(list(reversed(mi.numeric_range(Decimal("0"),Decimal("1"),Decimal("0.5")))),[Decimal("0.5"),Decimal("0")])']),
      'interleave_empty':('unknown_location','定位并修复均匀交错接口处理零个迭代器时报错的问题。空集合（包括显式空 lengths）应输出空序列；保持普通均匀交错、单个输入和长度校验。',more,[
        'self.assertEqual(list(mi.interleave_evenly([])),[])',
        'self.assertEqual(list(mi.interleave_evenly([],lengths=[])),[])',
        'self.assertEqual(list(mi.interleave_evenly([[1,2,3]])),[1,2,3])',
        'self.assertEqual(list(mi.interleave_evenly([["a","b"],[1,2]])),["a",1,"b",2])']),
      'seekable_zero':('regression_compatibility','修复 seekable 在 maxlen=0 时 peek()/bool() 吞掉底层元素的兼容性问题。连续窥视应幂等，布尔检查不能消耗元素，保持空输入默认值和常规 seek 行为。',more,[
        's=mi.seekable([10,20,30],maxlen=0)\nself.assertEqual(s.peek(),10)\nself.assertEqual(s.peek(),10)\nself.assertEqual(list(s),[10,20,30])',
        's=mi.seekable([1,2],maxlen=0)\nself.assertTrue(s)\nself.assertEqual(list(s),[1,2])',
        's=mi.seekable([],maxlen=0)\nself.assertFalse(s)\nself.assertEqual(s.peek("default"),"default")',
        's=mi.seekable([1,2,3],maxlen=2)\nself.assertEqual(next(s),1)\ns.seek(0)\nself.assertEqual(list(s),[1,2,3])']),
      'dotenv_inline_empty':('cross_file','修复 dotenv 文本解析到公开 dotenv_values 的契约：等号后有空白再跟注释表示空字符串；没有空白的 # 保留为值；无等号的键仍为 None。注意解析层和公开接口的一致性。',dotenv,[
        f'self.assertEqual(dotenv.dotenv_values(stream=StringIO({text!r})),{expected!r})'
        for text,expected in [('KEY= # comment',{'KEY':''}),('KEY=\t# comment',{'KEY':''}),('KEY=#literal',{'KEY':'#literal'}),('KEY=a#b',{'KEY':'a#b'}),('KEY=val #c',{'KEY':'val'}),('KEY= #c\nOTHER=x',{'KEY':'','OTHER':'x'}),('KEY',{'KEY':None})]]),
      'dotenv_backslash':('cross_file','修复 set_key 写入与解析器读回之间的转义契约：反斜杠、结尾反斜杠、引号以及后续变量必须无损往返。不要只修一侧或吞掉下一行；保持普通值和其他引号行为。',dotenv,[
        f'with tempfile.TemporaryDirectory() as d:\n    p=Path(d)/"settings.env"\n    p.write_text("")\n    value={value!r}\n    dotenv.set_key(p,"a",value)\n    dotenv.set_key(p,"b","sentinel")\n    self.assertEqual(dotenv.get_key(p,"a"),value)\n    self.assertEqual(dotenv.get_key(p,"b"),"sentinel")'
        for value in ['C:\\Users\\',r'\d+','back\\',"a\\'b","it's",'say "hi"','plain']]),
      'dotenv_override':('cross_file','修复变量展开、DotEnv 配置及 load_dotenv 的 override 契约：override=False 时已有环境变量优先，文件内后续展开也必须使用该值；True 时文件值优先。保持 dotenv_values、无展开模式及缺失变量行为。',dotenv,[
        'with patch.dict(os.environ,{"CT_FINAL_A":"environment"}):\n    self.assertTrue(dotenv.load_dotenv(stream=StringIO("CT_FINAL_A=file\\nCT_FINAL_B=${CT_FINAL_A}"),override=False))\n    self.assertEqual(os.environ["CT_FINAL_A"],"environment")\n    self.assertEqual(os.environ["CT_FINAL_B"],"environment")',
        'with patch.dict(os.environ,{"CT_FINAL_A":"environment"}):\n    dotenv.load_dotenv(stream=StringIO("CT_FINAL_A=file\\nCT_FINAL_B=${CT_FINAL_A}"),override=True)\n    self.assertEqual(os.environ["CT_FINAL_A"],"file")\n    self.assertEqual(os.environ["CT_FINAL_B"],"file")',
        'with patch.dict(os.environ,{"CT_FINAL_A":"environment"}):\n    result=dotenv.dotenv_values(stream=StringIO("CT_FINAL_A=file\\nCT_FINAL_B=${CT_FINAL_A}"))\n    self.assertEqual(result["CT_FINAL_B"],"file")',
        'result=dotenv.dotenv_values(stream=StringIO("CT_FINAL_A=${CT_FINAL_MISSING}"),interpolate=False)\nself.assertEqual(result["CT_FINAL_A"],"${CT_FINAL_MISSING}")']),
      'dotenv_spaces':('unknown_location','定位并修复环境文件解析未加引号值包含连续空白时截断或解析失败的问题。保留内部空格和制表符，移除尾部空白/空白引导的注释，保持无空白 # 及带引号的空白。',dotenv,[
        f'self.assertEqual(dotenv.dotenv_values(stream=StringIO({text!r})),{{"a":{expected!r}}})'
        for text,expected in [('a=b  c','b  c'),('a=b\tc','b\tc'),('a=b c   ','b c'),('a=b # comment','b'),('a=b#c','b#c'),("a='b c '",'b c ')]]),
      'dotenv_bom':('regression_compatibility','修复 UTF-8 BOM 环境文件兼容性：仅移除文件开头的 BOM，首个变量名不能带 BOM；保持无 BOM 文件、多行、行号及值内 BOM。',dotenv,[
        f'self.assertEqual(dotenv.dotenv_values(stream=StringIO({text!r})),{expected!r})'
        for text,expected in [('\ufeffa=b',{'a':'b'}),('\ufeffa=b\nc=d',{'a':'b','c':'d'}),('a=b',{'a':'b'}),('a=\ufeffb',{'a':'\ufeffb'})]])}


def gold_spans(before,after):
    spans=[]
    for name in before:
        if not name.endswith('.py') or before[name]==after.get(name):continue
        lines=before[name].splitlines();new=after[name].splitlines()
        for tag,a,b,c,d in difflib.SequenceMatcher(a=lines,b=new,autojunk=False).get_opcodes():
            if tag=='equal':continue
            start=max(0,a-2);end=min(len(lines),max(b,a+1)+2)
            selected=[(i+1,lines[i]) for i in range(start,end) if lines[i].strip()]
            if selected:spans.append({'path':name,'start':start+1,'end':end,'lines':[{'number':n,'text':v} for n,v in selected]})
    return spans


def add_contract_spans(name,files,spans):
    selectors={'dotenv_inline_empty':('dotenv/main.py','parse'),
               'dotenv_override':('dotenv/variables.py','resolve')}
    if name in selectors:
        path,function=selectors[name];lines=files[path].splitlines()
        tree=ast.parse(files[path])
        if name=='dotenv_override':tree=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Variable')
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==function)
        code=[n for n in node.body if not isinstance(n,ast.Expr)]
        start=code[0].lineno if code else node.lineno;end=min(node.end_lineno,start+9)
        spans.append({'path':path,'start':start,'end':end,'lines':[{'number':i+1,'text':lines[i]} for i in range(start-1,end) if lines[i].strip()]})
    return spans


def build():
    DEST.mkdir(parents=True,exist_ok=False)
    selected=load_json(CACHE/'selected.json');specs=specifications();holdout=[];references={};quality=[];gold={}
    evidence=DEST/'upstream';evidence.mkdir()
    for name,repo,sha in selected:
        directory=CACHE/name;commit=load_json(directory/'commit.json')
        buggy=load_json(directory/'buggy-files.json');fixed=load_json(directory/'fixed-files.json')
        category,prompt,imports,bodies=specs[name];id='holdout_'+name
        edits=[n for n in buggy if buggy[n]!=fixed.get(n)]
        allowed=sorted(buggy) if category=='unknown_location' else edits
        prompt+='\n允许修改的实现范围：'+', '.join(allowed)+'。不得修改测试或其他文件。先运行 '+VERIFY_COMMAND+' 查看失败，完成后重新运行同一命令并如实报告。'
        files={**buggy,'test_acceptance.py':test_source(imports,bodies),'README.md':'# Frozen upstream repair\n\n'+prompt+'\n'}
        for license_name,content in load_json(directory/'licenses.json').items():files['UPSTREAM_'+license_name]=content
        task={'id':id,'category':category,'cohort':'holdout','prompt':prompt,'files':files,
              'editable_files':allowed,'expected_tests':len(bodies),'sha256':{n:hashlib.sha256(s.encode()).hexdigest() for n,s in files.items()},
              'origin':{'kind':'real_upstream_bug','repository':'https://github.com/'+repo,'buggy_commit':commit['parents'][0]['sha'],
                        'fix_commit':sha,'url':commit['html_url'],'title':commit['commit']['message'].splitlines()[0],
                        'representation':'Pinned original package source, selected new unittest regression/compatibility cases; not a whole-repository benchmark.'},
              'exposure':'No Agent runs or Harness tuning on this task before final freeze; public upstream content may have been in model pretraining.'}
        spans=add_contract_spans(name,buggy,gold_spans(buggy,fixed))
        gold[id]={'required_files':sorted({s['path'] for s in spans}),'spans':spans,
                  'definition':'Changed upstream faulty source plus two context lines, before first edit; frozen by controller, never installed in workspace.'}
        references[id]={n:fixed[n] for n in edits}
        write_json(evidence/(id+'.json'),{k:commit[k] for k in ('sha','html_url','parents')} |
                   {'title':commit['commit']['message'].splitlines()[0],
                    'committed_at':commit['commit']['committer']['date'],
                    'changed_files':[f['filename'] for f in commit['files']]},exclusive=True)
        holdout.append(task)
    old=load_json(ROOT/'benchmarks/repair_suite_v1/tasks.json')['tasks']
    old_refs=load_json(ROOT/'benchmarks/repair_suite_v1/reference_solutions.json')
    print('old reference shape',type(old_refs).__name__,list(old_refs)[:3])
    old_solutions=old_refs.get('solutions',old_refs)
    tasks=[]
    for original in old:
        task=copy.deepcopy(original);task['cohort']='existing'
        task['exposure']='All 20 were previously evaluated; six additionally used for iterative Harness tuning. No held-out generalization claim.'
        task['development_tuned']=task['id'] in {'inventory_reservation','utc_storage_contract','boltons_splitlines','boltons_xfrange','humanize_metric','humanize_size'}
        tasks.append(task)
        ref=old_solutions[task['id']]
        gold[task['id']]={'required_files':sorted(task['editable_files']),'spans':gold_spans(task['files'],{**task['files'],**ref}),
                          'definition':'Frozen faulty implementation regions against existing reference, plus two context lines; original tests unchanged.'}
    tasks+=holdout
    assert len(tasks)==len({t['id'] for t in tasks})==30
    write_json(DEST/'tasks.json',{'schema_version':1,'tasks':tasks},exclusive=True)
    write_json(DEST/'evidence_gold.json',gold,exclusive=True)
    write_json(ROOT/'artifacts/final-private-gold-2026-10-25/holdout_references.json',references,exclusive=True)
    # These are deterministic asset-quality checks, not Agent repair attempts.
    check_root=ROOT/'artifacts/final-asset-quality-2026-10-25'
    for task in holdout:
        bad=verify_pristine(task,{},check_root/task['id']/'buggy')
        good=verify_pristine(task,{n:s.encode() for n,s in references[task['id']].items()},check_root/task['id']/'fixed')
        quality.append({'task_id':task['id'],'buggy':bad,'fixed':good})
        print(task['id'],'buggy',bad['accepted'],'fixed',good['accepted'],'tests',good['tests_run'],flush=True)
    write_json(DEST/'asset_quality.json',quality,exclusive=True)
    assert all(not r['buggy']['accepted'] and r['fixed']['accepted'] for r in quality),'Correct asset issues before freeze, never after Agent runs'


if __name__=='__main__':build()
