"""Build 20 tasks before freeze. Reference solutions never enter agent workspaces."""
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'benchmarks/repair_suite_v1'

def clean(text):
    return textwrap.dedent(text).strip() + '\n'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def tests(imports, expressions):
    # One named test per assertion; subcases remain one test, not independent tasks.
    out = 'import unittest\n' + imports + '\n\nclass RepairTests(unittest.TestCase):\n'
    for index, expression in enumerate(expressions, 1):
        out += f'    def test_{index:02d}(self):\n'
        out += textwrap.indent(clean(expression), '        ')
    return out

def authored(task_id, category, prompt, files, edits, imports, assertions):
    files = {name: clean(content) for name, content in files.items()}
    files['test_acceptance.py'] = tests(imports, assertions)
    reference = dict(files)
    for filename, old, new in edits:
        assert reference[filename].count(old) == 1, (task_id, old)
        reference[filename] = reference[filename].replace(old, new)
    return {'id': task_id, 'category': category, 'prompt': prompt, 'files': files,
            'editable_files': list(dict.fromkeys(edit[0] for edit in edits)), 'expected_tests': len(assertions),
            'origin': {'kind': 'author_created', 'independent_task': True},
            'reference_files': {name: reference[name] for name in dict.fromkeys(edit[0] for edit in edits)}}

def local_tasks():
    old = json.loads((ROOT / 'benchmarks/repair_validation_tasks.json').read_text(encoding='utf-8'))['tasks']
    tasks = []
    for row, category in zip(old, ('single_file', 'cross_file')):
        reference = dict(row['files'])
        for edit in row['reference_edits']:
            reference[edit['path']] = reference[edit['path']].replace(edit['old'], edit['new'], 1)
        tasks.append({**{key: row[key] for key in ('id', 'files', 'prompt', 'editable_files', 'expected_tests')},
                      'category': category, 'origin': {'kind': 'author_created', 'legacy_task_preserved': True,
                      'legacy_manifest_sha256': sha((ROOT / 'benchmarks/repair_validation_tasks.json').read_bytes())},
                      'reference_files': {name: reference[name] for name in row['editable_files']}})
    tasks.append(authored('interval_union', 'single_file',
        '修复 intervals.py 的 merge_intervals：合并重叠和端点相接的闭区间，按起点排序，空输入返回空列表，不改变输入；反向区间抛 ValueError。',
        {'intervals.py': '''
        def merge_intervals(intervals):
            ordered = sorted(intervals)
            result = []
            for start, end in ordered:
                if start > end:
                    raise ValueError('reversed interval')
                if result and start < result[-1][1]:
                    result[-1][1] = end
                else:
                    result.append([start, end])
            return result
        '''}, [('intervals.py', 'start < result[-1][1]', 'start <= result[-1][1]'),
               ('intervals.py', 'result[-1][1] = end', 'result[-1][1] = max(result[-1][1], end)')],
        'from intervals import merge_intervals as merge', [
        'self.assertEqual(merge([]), [])', 'self.assertEqual(merge([(1,3),(3,5)]), [[1,5]])',
        'self.assertEqual(merge([(1,10),(2,3)]), [[1,10]])', 'self.assertEqual(merge([(5,6),(1,2)]), [[1,2],[5,6]])',
        'self.assertEqual(merge([(1,2),(2,4),(4,9)]), [[1,9]])', 'self.assertEqual(merge([(1,1)]), [[1,1]])',
        'x=[[3,5],[1,2]]\nmerge(x)\nself.assertEqual(x, [[3,5],[1,2]])',
        "with self.assertRaises(ValueError): merge([(5,1)])"]))
    tasks.append(authored('ttl_expiry', 'single_file',
        '修复 cache.py 的 TTLCache：写入 ttl 是相对注入时钟的秒数，读取不会延长 TTL；now >= expiry 即过期，缺失/过期返回 default；ttl<=0 不缓存，更新同一键替换过期时间。',
        {'cache.py': '''
        class TTLCache:
            def __init__(self, clock):
                self.clock = clock
                self.entries = {}
            def put(self, key, value, ttl):
                self.entries[key] = (value, ttl)
            def get(self, key, default=None):
                if key not in self.entries:
                    return default
                value, expiry = self.entries[key]
                if self.clock() > expiry:
                    del self.entries[key]
                    return default
                return value
        '''}, [('cache.py', 'self.entries[key] = (value, ttl)', 'self.entries[key] = (value, self.clock() + max(ttl, 0))'),
               ('cache.py', 'self.clock() > expiry', 'self.clock() >= expiry')],
        'from cache import TTLCache\n', [
        "c=TTLCache(lambda:100)\nc.put('a',1,10)\nself.assertEqual(c.get('a'),1)",
        "t=[100]; c=TTLCache(lambda:t[0]); c.put('a',1,10); t[0]=110\nself.assertIsNone(c.get('a'))",
        "c=TTLCache(lambda:100); c.put('a',1,0)\nself.assertEqual(c.get('a','miss'),'miss')",
        "c=TTLCache(lambda:100); c.put('a',1,-2)\nself.assertIsNone(c.get('a'))",
        "c=TTLCache(lambda:100)\nself.assertEqual(c.get('x',3),3)",
        "t=[100]; c=TTLCache(lambda:t[0]); c.put('a',1,10); t[0]=105; c.get('a'); t[0]=111\nself.assertIsNone(c.get('a'))",
        "t=[100]; c=TTLCache(lambda:t[0]); c.put('a',1,5); t[0]=103; c.put('a',2,10); t[0]=110\nself.assertEqual(c.get('a'),2)",
        "c=TTLCache(lambda:100); c.put('a',None,5)\nself.assertIsNone(c.get('a','fallback'))"]))
    tasks.append(authored('retry_schedule', 'single_file',
        '修复 retry.py 的 retry_delays：attempts 是总尝试次数，因此等待次数是 attempts-1；指数增长 base*factor**i，并封顶 cap，允许 cap=0；attempts须为正的非bool整数，base/cap非负，factor>=1，无真实 sleep。',
        {'retry.py': '''
        def retry_delays(attempts, base=1, factor=2, cap=60):
            if not isinstance(attempts, int) or attempts < 0:
                raise ValueError('invalid attempts')
            if base < 0 or factor < 1 or cap < 0:
                raise ValueError('invalid delay')
            return [max(cap, base * factor**i) for i in range(attempts)]
        '''}, [('retry.py', 'not isinstance(attempts, int) or attempts < 0', 'type(attempts) is not int or attempts < 1'),
               ('retry.py', 'max(cap, base * factor**i) for i in range(attempts)', 'min(cap, base * factor**i) for i in range(attempts - 1)')],
        'from retry import retry_delays as delays', [
        'self.assertEqual(delays(1), [])', 'self.assertEqual(delays(4), [1,2,4])',
        'self.assertEqual(delays(5,3,2,7), [3,6,7,7])', 'self.assertEqual(delays(3,cap=0), [0,0])',
        'with self.assertRaises(ValueError): delays(True)', 'with self.assertRaises(ValueError): delays(0)',
        'with self.assertRaises(ValueError): delays(2,base=-1)', 'self.assertEqual(delays(3,0,1,5), [0,0])']))
    tasks.append(authored('dependency_order', 'single_file',
        '修复 graph.py 的 dependency_order：输入节点到依赖节点列表，输出每个依赖先于依赖者的拓扑序；包含仅作为依赖出现的节点，不重复节点；任何自环或多节点环抛 ValueError，空图返回空列表，不能修改输入。',
        {'graph.py': '''
        def dependency_order(graph):
            done = set()
            result = []
            def visit(node):
                if node in done:
                    return
                done.add(node)
                result.append(node)
                for child in graph.get(node, []):
                    visit(child)
            for node in graph:
                visit(node)
            return result
        '''}, [('graph.py', 'done = set()', 'done = set()\n    active = set()'),
               ('graph.py', 'done.add(node)\n        result.append(node)', "if node in active:\n            raise ValueError('cycle')\n        active.add(node)"),
               ('graph.py', 'visit(child)\n    for node', 'visit(child)\n        active.remove(node)\n        done.add(node)\n        result.append(node)\n    for node')],
        'from graph import dependency_order as order', [
        'self.assertEqual(order({}), [])', "self.assertEqual(order({'a':['b']}), ['b','a'])",
        "self.assertEqual(order({'a':['b'],'b':['c']}), ['c','b','a'])", "self.assertEqual(order({'a':[],'b':['a']}), ['a','b'])",
        "with self.assertRaises(ValueError): order({'a':['a']})", "with self.assertRaises(ValueError): order({'a':['b'],'b':['a']})",
        "x=order({'a':['c'],'b':['c']})\nself.assertEqual(len(x),3)\nself.assertLess(x.index('c'),x.index('a'))\nself.assertLess(x.index('c'),x.index('b'))",
        "g={'a':['b']}; order(g)\nself.assertEqual(g,{'a':['b']})"]))
    tasks.append(authored('money_minor_units', 'cross_file',
        '修复金额服务跨模块契约：money.to_minor 接收十进制字符串/Decimal/数字，使用 Decimal(str(value)) 和 ROUND_HALF_UP 转整数分（负数也按该规则）；billing.invoice_total 必须逐项转换为分再求和并返回 int。不要改变 public API 或测试。',
        {'money.py': '''
        from decimal import Decimal, ROUND_HALF_UP
        def to_minor(value):
            return int(float(value) * 100)
        ''', 'billing.py': '''
        from money import to_minor
        def invoice_total(values):
            return to_minor(sum(float(value) for value in values))
        '''}, [('money.py','int(float(value) * 100)', "int((Decimal(str(value)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))"),
               ('billing.py', 'to_minor(sum(float(value) for value in values))','sum(to_minor(value) for value in values)')],
        'from money import to_minor\nfrom billing import invoice_total', [
        "self.assertEqual(to_minor('1.005'),101)", "self.assertEqual(to_minor('-1.005'),-101)",
        "self.assertEqual(to_minor('0.29'),29)", "self.assertEqual(invoice_total(['0.005','0.005']),2)",
        'self.assertEqual(invoice_total([]),0)', "self.assertIs(type(invoice_total(['2'])),int)",
        "self.assertEqual(to_minor('123.40'),12340)", "self.assertEqual(invoice_total(['1.10','2.20']),330)"]))
    tasks.append(authored('config_precedence', 'cross_file',
        '修复配置解析和加载契约：flags.parse_bool 支持 bool、大小写及首尾空格的 true/false,1/0,yes/no,on/off，其他值抛 ValueError；settings.load_settings(defaults,env) 中有键的 env 覆盖 defaults（包括 False/空串），未提供则回退；返回新字典，只包含 debug bool 和 port int。',
        {'flags.py': '''
        def parse_bool(value):
            return bool(value)
        ''', 'settings.py': '''
        from flags import parse_bool
        def load_settings(defaults, env):
            debug = env.get('debug') or defaults.get('debug', False)
            port = env.get('port') or defaults.get('port', 8000)
            return {'debug': parse_bool(debug), 'port': int(port)}
        '''}, [('flags.py', 'return bool(value)', "if isinstance(value, bool):\n        return value\n    text = str(value).strip().lower()\n    if text in ('true','1','yes','on'):\n        return True\n    if text in ('false','0','no','off'):\n        return False\n    raise ValueError('invalid bool')"),
               ('settings.py', "env.get('debug') or defaults.get('debug', False)", "env.get('debug', defaults.get('debug', False))"),
               ('settings.py', "env.get('port') or defaults.get('port', 8000)", "env.get('port', defaults.get('port', 8000))")],
        'from flags import parse_bool\nfrom settings import load_settings as load', [
        "self.assertFalse(parse_bool('false'))", "self.assertTrue(parse_bool(' YES '))", 'self.assertFalse(parse_bool(False))',
        "with self.assertRaises(ValueError): parse_bool('unknown')", "self.assertFalse(load({'debug':True},{'debug':False})['debug'])",
        "self.assertEqual(load({'port':9000},{'port':0})['port'],0)", "self.assertEqual(load({},{}),{'debug':False,'port':8000})",
        "with self.assertRaises(ValueError): load({'debug':True},{'debug':''})"]))
    tasks.append(authored('cursor_roundtrip', 'cross_file',
        '修复 cursor_codec.py 与 pagination.py 的游标契约：编码 offset 为 URL-safe Base64 的 ASCII 十进制文本；decode 是逆操作，负数/bool/无效编码抛 ValueError。page 返回 (slice,next_cursor)，起点来自 cursor（None 为0），next_cursor 是绝对下一偏移，仅还有数据时返回；不改变 items，size为正整数且不接受bool。',
        {'cursor_codec.py': '''
        import base64
        def encode(offset):
            if type(offset) is not int or offset < 0:
                raise ValueError('invalid offset')
            return base64.urlsafe_b64encode(str(offset).encode()).decode()
        def decode(token):
            return int(token)
        ''', 'pagination.py': '''
        from cursor_codec import encode, decode
        def page(items, size, cursor=None):
            if type(size) is not int or size < 1:
                raise ValueError('invalid size')
            start = 0 if cursor is None else decode(cursor)
            end = start + size
            return items[start:end], encode(size) if end < len(items) else None
        '''}, [('cursor_codec.py','return int(token)', "try:\n        raw = base64.b64decode(token, altchars=b'-_', validate=True).decode('ascii')\n        if not raw.isdecimal():\n            raise ValueError('invalid cursor')\n        return int(raw)\n    except Exception as exc:\n        raise ValueError('invalid cursor') from exc"),
               ('pagination.py','encode(size) if end','encode(end) if end')],
        'from cursor_codec import encode, decode\nfrom pagination import page', [
        'self.assertEqual(decode(encode(12)),12)', 'self.assertEqual(page(list(range(8)),3,encode(3)),([3,4,5],encode(6)))',
        'self.assertEqual(page(list(range(8)),3,encode(6)),([6,7],None))', 'self.assertEqual(page([],2),([],None))',
        "with self.assertRaises(ValueError): decode('not base64!')", "with self.assertRaises(ValueError): decode('LTE=')",
        'with self.assertRaises(ValueError): encode(True)', 'with self.assertRaises(ValueError): page([1],True)']))
    tasks.append(authored('error_response_contract', 'cross_file',
        '修复异常到 HTTP 响应契约：ValidationError携带 message 和 field，MissingError携带 message；api.handle(callable) 捕获 ValidationError -> (400,{error:message,field:field})，MissingError -> (404,{error:message})，成功 -> (200,result)，未知异常继续抛出。使用既有类，不改变它们名字。',
        {'errors.py': '''
        class ValidationError(Exception):
            def __init__(self, message, field):
                super().__init__(message)
                self.field = message
        class MissingError(Exception):
            pass
        ''', 'api.py': '''
        from errors import ValidationError, MissingError
        def handle(action):
            try:
                return 200, action()
            except Exception as exc:
                return 500, {'error':str(exc)}
        '''}, [('errors.py','self.field = message','self.field = field'),
               ('api.py', "except Exception as exc:\n        return 500, {'error':str(exc)}", "except ValidationError as exc:\n        return 400, {'error':str(exc), 'field':exc.field}\n    except MissingError as exc:\n        return 404, {'error':str(exc)}")],
        'from api import handle\nfrom errors import ValidationError, MissingError\ndef fail(exc):\n    raise exc', [
        "self.assertEqual(handle(lambda:42),(200,42))", "self.assertEqual(handle(lambda:None),(200,None))",
        "self.assertEqual(ValidationError('bad','email').field,'email')", "self.assertEqual(handle(lambda:fail(ValidationError('bad','email'))),(400,{'error':'bad','field':'email'}))",
        "self.assertEqual(handle(lambda:fail(MissingError('gone'))),(404,{'error':'gone'}))", "with self.assertRaises(RuntimeError): handle(lambda:fail(RuntimeError('internal')))",
        "self.assertEqual(str(ValidationError('bad','x')),'bad')", "self.assertEqual(handle(lambda:{'ok':True}),(200,{'ok':True}))"]))
    tasks.append(authored('utc_storage_contract', 'cross_file',
        '修复时间与存储契约：timestamps.to_epoch 将带时区 datetime 转为 UTC 秒数；naive datetime 明确按 UTC 处理，不依赖本机时区。store.save_event 返回新记录 {id,timestamp}，timestamp为整数Unix秒，不能用毫秒，不能修改传入 event。',
        {'timestamps.py': '''
        from datetime import timezone
        def to_epoch(value):
            return value.replace(tzinfo=timezone.utc).timestamp()
        ''', 'store.py': '''
        from timestamps import to_epoch
        def save_event(event):
            event['timestamp'] = int(to_epoch(event['at']) * 1000)
            return event
        '''}, [('timestamps.py','value.replace(tzinfo=timezone.utc).timestamp()', "(value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).timestamp()"),
               ('store.py', "event['timestamp'] = int(to_epoch(event['at']) * 1000)\n    return event", "return {'id':event['id'], 'timestamp':int(to_epoch(event['at']))}")],
        'from datetime import datetime, timezone, timedelta\nfrom timestamps import to_epoch\nfrom store import save_event', [
        'self.assertEqual(to_epoch(datetime(1970,1,1)),0)', 'self.assertEqual(to_epoch(datetime(1970,1,1,tzinfo=timezone.utc)),0)',
        'self.assertEqual(to_epoch(datetime(1970,1,1,8,tzinfo=timezone(timedelta(hours=8)))),0)',
        'self.assertEqual(to_epoch(datetime(1969,12,31,19,tzinfo=timezone(timedelta(hours=-5)))),0)',
        "self.assertEqual(save_event({'id':'a','at':datetime(1970,1,1,0,0,2)}),{'id':'a','timestamp':2})",
        "e={'id':'a','at':datetime(1970,1,1)}; r=save_event(e)\nself.assertNotIn('timestamp',e)\nself.assertIsNot(e,r)",
        "self.assertIs(type(save_event({'id':'a','at':datetime(1970,1,1)})['timestamp']),int)",
        "self.assertEqual(to_epoch(datetime(1970,1,1,0,0,0,500000)),0.5)"]))
    tasks.append(authored('inventory_reservation', 'cross_file',
        '修复订单与库存契约：stock.reserve(stock,sku,qty) 校验qty为正的非bool整数，缺货/未知sku抛 ValueError；成功扣减一次并返回剩余量，失败不修改库存。orders.place_order 返回 {sku,quantity,remaining}，使用reserve返回值，不重复扣减。',
        {'stock.py': '''
        def reserve(stock, sku, qty):
            if not isinstance(qty, int) or qty < 1:
                raise ValueError('invalid quantity')
            stock[sku] -= qty
            if stock[sku] < 0:
                raise ValueError('insufficient stock')
            return stock[sku]
        ''', 'orders.py': '''
        from stock import reserve
        def place_order(stock, sku, qty):
            reserve(stock, sku, qty)
            stock[sku] -= qty
            return {'sku':sku, 'quantity':qty, 'remaining':stock[sku]}
        '''}, [('stock.py','not isinstance(qty, int)','type(qty) is not int'),
               ('stock.py', "stock[sku] -= qty\n    if stock[sku] < 0:\n        raise ValueError('insufficient stock')", "if stock.get(sku, 0) < qty:\n        raise ValueError('insufficient stock')\n    stock[sku] -= qty"),
               ('orders.py', 'reserve(stock, sku, qty)\n    stock[sku] -= qty', 'remaining = reserve(stock, sku, qty)'),
               ('orders.py', "'remaining':stock[sku]", "'remaining':remaining")],
        'from stock import reserve\nfrom orders import place_order', [
        "s={'a':5}\nself.assertEqual(place_order(s,'a',2),{'sku':'a','quantity':2,'remaining':3})\nself.assertEqual(s,{'a':3})",
        "s={'a':2}\nself.assertEqual(reserve(s,'a',2),0)", "s={'a':2}\nwith self.assertRaises(ValueError): reserve(s,'a',3)\nself.assertEqual(s,{'a':2})",
        "with self.assertRaises(ValueError): reserve({},'x',1)", "with self.assertRaises(ValueError): reserve({'a':2},'a',True)",
        "with self.assertRaises(ValueError): reserve({'a':2},'a',0)", "with self.assertRaises(ValueError): reserve({'a':2},'a',1.5)",
        "s={'a':4,'b':8}; place_order(s,'a',1)\nself.assertEqual(s['b'],8)"]))
    return tasks

def extract(source, names):
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        assigned = [target.id for target in getattr(node, 'targets', []) if isinstance(target, ast.Name)]
        if getattr(node, 'name', None) in names or set(assigned) & set(names):
            selected.append(ast.get_source_segment(source, node))
    return '\n\n'.join(selected) + '\n'

def upstream_tasks():
    cache = ROOT / 'tmp/upstream'
    evidence = json.loads((cache / 'evidence.json').read_text(encoding='utf-8'))
    specs = {
        'boltons_isoparse': ('unknown_location', 'time_core.py', ['_NONDIGIT_RE','isoparse'], 'import re\nfrom datetime import datetime\n',
            '时间显示服务的毫秒/微秒小数解析错误；找出缺陷所在位置并修复。保留无小数行为，1到6位小数正确缩放，超过6位截断。',
            'from public_api import render', [
                "self.assertEqual(render('2020-01-02T03:04:05.851').microsecond,851000)",
                "self.assertEqual(render('2020-01-02T03:04:05.051').microsecond,51000)",
                "self.assertEqual(render('2020-01-02T03:04:05.1').microsecond,100000)",
                "self.assertEqual(render('2020-01-02T03:04:05.000001').microsecond,1)",
                "self.assertEqual(render('2020-01-02T03:04:05.123456789').microsecond,123456)",
                "self.assertEqual(render('2020-01-02T03:04:05').second,5)",
                "self.assertEqual(render('2020-01-02T03:04:05.000000').microsecond,0)",
                "with self.assertRaises(ValueError): render('invalid')"],
            'from time_core import isoparse\ndef render(value):\n    return isoparse(value)\n'),
        'boltons_xfrange': ('unknown_location', 'sequence_core.py', ['xfrange','frange'], 'import math\n',
            '数值序列公开接口下降序列为空，浮点边界有多余元素。定位并修复，使generator与list接口计数/浮点值一致，错误方向为空，step=0抛ValueError。注意既有API参数顺序和递增行为。',
            'from public_api import render, expected', [
                'self.assertEqual(render(5,0,step=-1.25),[5.0,3.75,2.5,1.25])',
                'self.assertEqual(render(1,5,step=-1),[])', 'self.assertEqual(render(5,0),[])',
                'self.assertEqual(len(render(1.0,step=.1)),10)', 'self.assertEqual(render(1.0,step=.1),expected(1.0,step=.1))',
                'self.assertEqual(render(1,3,step=.75),[1,1.75,2.5])',
                'with self.assertRaises(ValueError): render(10,step=0)', 'self.assertEqual(render(0),[])'],
            'from itertools import islice\nfrom sequence_core import xfrange, frange\ndef render(*a,**k):\n    return list(islice(xfrange(*a,**k),1000))\ndef expected(*a,**k):\n    return frange(*a,**k)\n'),
        'boltons_multireplace': ('unknown_location', 'replacement_core.py', ['MultiReplace'], 'import re\nfrom collections.abc import Mapping\n',
            '批量替换公开接口在空替换表时崩溃。定位问题并使空映射/空列表/空生成器为no-op，同时保留非空映射、字面量转义、正则模式和单次替换的行为。',
            'from public_api import render', [
                "self.assertEqual(render('foo bar',{}),'foo bar')", "self.assertEqual(render('foo',[]),'foo')",
                "self.assertEqual(render('',{}),'')", "self.assertEqual(render('abc',iter([])),'abc')",
                "self.assertEqual(render('foo cat',{'foo':'bar','cat':'dog'}),'bar dog')",
                "self.assertEqual(render('a.b', {'.':'!'}),'a!b')", "self.assertEqual(render('aaa',[('a','b')]),'bbb')",
                "self.assertEqual(render('a12',[(r'\\d+','X')],regex=True),'aX')"],
            'from replacement_core import MultiReplace\ndef render(text,mapping,**kwargs):\n    return MultiReplace(mapping,**kwargs).sub(text)\n'),
        'boltons_splitlines': ('unknown_location', 'text_core.py', ['_line_ending_re','iter_splitlines','indent'], 'import re\n',
            '文本展示服务会意外拆开日期中的 2028/2029，且未识别Unicode行分隔符。定位并修复，保留CRLF、空行、尾换行和缩进接口的行为。',
            'from public_api import render, indent_text', [
                "self.assertEqual(render('May 2028 release'),['May 2028 release'])", "self.assertEqual(render('May 2029 release'),['May 2029 release'])",
                "self.assertEqual(render('a\\u2028b'),['a','b'])", "self.assertEqual(render('a\\u2029b'),['a','b'])",
                "self.assertEqual(render('a\\r\\nb'),['a','b'])", "self.assertEqual(render('a\\n'),['a',''])",
                "self.assertEqual(render(''),[])", "self.assertEqual(indent_text('a\\nb','> '),'> a\\n> b')"],
            'from text_core import iter_splitlines, indent\ndef render(text):\n    return list(iter_splitlines(text))\ndef indent_text(text,margin):\n    return indent(text,margin)\n'),
        'humanize_list': ('regression_compatibility','formatter.py',['natural_list'], 'from __future__ import annotations\n_ = lambda text: text\n',
            '修复 formatter.py 的 natural_list 空列表崩溃回归，空列表返回空字符串；保留单项、两项、多项、数字元素和输入不变性。',
            'from formatter import natural_list as fmt', [
                "self.assertEqual(fmt([]),'')", "self.assertEqual(fmt(['one']),'one')", "self.assertEqual(fmt(['one','two']),'one and two')",
                "self.assertEqual(fmt(['one','two','three']),'one, two and three')", "self.assertEqual(fmt([1,2]),'1 and 2')",
                "self.assertEqual(fmt(['']),'')", "x=['a','b']; fmt(x)\nself.assertEqual(x,['a','b'])", "self.assertEqual(fmt([1,'two',3]),'1, two and 3')"], None),
        'humanize_fractional': ('regression_compatibility','formatter.py',['fractional','_format_not_finite'], 'from __future__ import annotations\n',
            '修复 formatter.py 的 fractional 负带分数出现两次负号的问题；保留正分数、负真分数、整数和非法输入/NaN/Inf格式。',
            'from formatter import fractional as fmt', [
                "self.assertEqual(fmt(-1.3),'-1 3/10')", "self.assertEqual(fmt(-2.5),'-2 1/2')", "self.assertEqual(fmt(-.5),'-1/2')",
                "self.assertEqual(fmt(1.3),'1 3/10')", "self.assertEqual(fmt(2),'2')", "self.assertEqual(fmt('ten'),'ten')",
                "self.assertEqual(fmt(float('nan')),'NaN')", "self.assertEqual(fmt(float('-inf')),'-Inf')"], None),
        'humanize_metric': ('regression_compatibility','formatter.py',['metric','scientific','_format_not_finite','_SUPERSCRIPT_MAP','_SUPERSCRIPT_TRANS'], 'from __future__ import annotations\n',
            '修复 formatter.py 的 metric 在舍入达到1000时没有进位到下一个SI前缀的问题；保留有效数字精度、负数、小单位、非有限值和极大数scientific回退。',
            'from formatter import metric as fmt', [
                "self.assertEqual(fmt(999.9,'V'),'1.00 kV')", "self.assertEqual(fmt(999999,'V'),'1.00 MV')",
                "self.assertEqual(fmt(.0009999,'V'),'1.00 mV')", "self.assertEqual(fmt(1500,'V'),'1.50 kV')",
                "self.assertEqual(fmt(-999.9,'V'),'-1.00 kV')", "self.assertEqual(fmt(0),'0.00')",
                "self.assertEqual(fmt(float('inf')),'+Inf')", "self.assertEqual(fmt(1e40),'1.00 x 10⁴⁰')"], None),
        'humanize_size': ('regression_compatibility','formatter.py',['naturalsize','suffixes','_format_not_finite'],
            'from __future__ import annotations\nfrom math import log\n_ = lambda text: text\n',
            '修复 formatter.py 的 naturalsize 单位边界舍入回归：不能出现1000.0 kB或1024.0 KiB，应进位到更大单位。兼容binary/GNU、负数、自定义format、小字节和已有输出。',
            'from formatter import naturalsize as fmt', [
                "self.assertEqual(fmt(999999),'1.0 MB')", "self.assertEqual(fmt(999999999),'1.0 GB')",
                "self.assertEqual(fmt(1024**2-1,binary=True),'1.0 MiB')", "self.assertEqual(fmt(1024**2-1,gnu=True),'1.0M')",
                "self.assertEqual(fmt(-999999),'-1.0 MB')", "self.assertEqual(fmt(1000),'1.0 kB')",
                "self.assertEqual(fmt(1),'1 Byte')", "self.assertEqual(fmt(999999,format='%.3f'),'999.999 kB')"], None),
    }
    result = []
    for origin in evidence:
        tid = origin['id']
        category, module, symbols, imports, prompt, test_imports, assertions, wrapper = specs[tid]
        directory = cache / tid
        basename = Path(origin['module']).name
        buggy_raw = (directory / ('buggy-' + basename)).read_bytes()
        fixed_raw = (directory / ('fixed-' + basename)).read_bytes()
        # Extract exact top-level source segments, including docstrings. No defect injection.
        buggy = imports + '\n' + extract(buggy_raw.decode(), symbols)
        fixed = imports + '\n' + extract(fixed_raw.decode(), symbols)
        if tid == 'humanize_size':
            # Helper is in humanize.number, not filesize; preserve source from same revision.
            from fetch_repair_sources import fetch
            for label, revision in [('buggy',origin['buggy_commit']),('fixed',origin['fix_commit'])]:
                number = fetch(f"https://raw.githubusercontent.com/python-humanize/humanize/{revision}/src/humanize/number.py", directory / f'{label}-support-number.py').decode()
                helper = extract(number, ['_format_not_finite'])
                if label == 'buggy': buggy += '\n' + helper
                else: fixed += '\n' + helper
        files = {module: buggy, 'test_acceptance.py': tests(test_imports, assertions)}
        if wrapper:
            files.update({'public_api.py': wrapper,
                          'validation.py': 'def is_text(value):\n    return isinstance(value,str)\n',
                          'transport.py': 'def status():\n    return {"healthy":True}\n',
                          'config.py': 'DEFAULT_TIMEOUT = 20\n'})
        origin.update(kind='upstream_minimal_reproduction', buggy_module_sha256=sha(buggy_raw), fixed_module_sha256=sha(fixed_raw),
                      extraction_symbols=symbols, adaptation='Exact AST source segments with explicit imports; English-only identity gettext shim for humanize; small public API/distractors added. Full repository not evaluated.')
        result.append({'id':tid, 'category':category, 'prompt':prompt, 'files':files,
                       'editable_files':[module], 'expected_tests':len(assertions), 'origin':origin,
                       'reference_files':{module:fixed}, 'license_text':(directory/'LICENSE.txt').read_text(encoding='utf-8')})
    return result

def main():
    if (DEST / 'freeze.json').exists():
        raise SystemExit('Suite is frozen; create a new version instead of modifying gold.')
    tasks = local_tasks() + upstream_tasks()
    assert len(tasks) == len({task['id'] for task in tasks}) == 20
    assert Counter(task['category'] for task in tasks) == {'single_file':5,'cross_file':7,'unknown_location':4,'regression_compatibility':4}
    public, reference = [], {}
    for task in tasks:
        ref = task.pop('reference_files')
        reference[task['id']] = ref
        if 'license_text' in task:
            task['files']['UPSTREAM_LICENSE.txt'] = task.pop('license_text')
        allowed = ('修复位置未提供；可修改任意实现 .py 文件，不能改测试或辅助说明。' if task['category']=='unknown_location'
                   else '允许修改：' + ', '.join(task['editable_files']) + '。不能修改测试或其他文件。')
        if task['category']=='unknown_location':
            task['editable_files'] = [name for name in task['files'] if name.endswith('.py') and not name.startswith('test_')]
        task['prompt'] += '\n' + allowed + '\n先运行 python -B -m unittest discover -v 查看失败，再读取代码定位修复；结束前必须重新运行同一测试命令并如实报告。'
        task['files']['README.md'] = '# Repair task\n\n' + task['prompt'] + '\n'
        task['sha256'] = {name:sha(content.encode()) for name,content in task['files'].items()}
        public.append(task)
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = {'schema_version':2, 'suite_id':'repair-suite-v1', 'scope':'20 independent tasks: 12 authored (2 previously seen), 8 upstream minimal reproductions from 2 projects; 3 fresh repeats each. Not a held-out public repository benchmark.',
                'categories':dict(Counter(task['category'] for task in tasks)), 'tasks':public}
    (DEST/'tasks.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    (DEST/'reference_solutions.json').write_text(json.dumps(reference,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    for task in tasks:
        if task['origin']['kind']!='upstream_minimal_reproduction':
            continue
        source=ROOT/'tmp/upstream'/task['id']
        target=DEST/'sources'/task['id']
        target.mkdir(parents=True,exist_ok=True)
        for path in source.iterdir():
            if path.name in {'commit.json','LICENSE.txt'} or path.name.startswith(('buggy-','fixed-')):
                (target/path.name).write_bytes(path.read_bytes())
    print(json.dumps({'tasks':20,'categories':manifest['categories'],'upstream_tasks':8,'upstream_projects':2}))

if __name__ == '__main__':
    main()
