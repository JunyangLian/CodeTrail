const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness(responses) {
  const elements = new Map();
  const element = () => ({textContent: '', cloneNode: element, addEventListener() {}});
  const timers = [], rendered = [], busy = [], notices = [];
  let calls = 0;
  const sandbox = {
    document: {getElementById(id) {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);}, addEventListener() {}},
    setTimeout(fn, delay) {timers.push({fn, delay});}, clearTimeout() {},
    fetch: async () => {const response = responses[calls++]; if (response instanceof Error) throw response; return {ok: true, json: async () => response};},
    rendered, busy, notices,
  };
  vm.createContext(sandbox);
  const source = fs.readFileSync(path.join(__dirname, '../pico/web_assets/app.js'), 'utf8');
  vm.runInContext(source.slice(0, source.lastIndexOf('init();')) + `
    renderJob = job => rendered.push(job);
    setBusy = value => busy.push(value);
    toast = message => notices.push(message);
    renderConnection = () => { $('connection').textContent = 'AppContainer'; };
    refreshSessions = async () => { if (globalThis.historyFailure) throw new Error('history unavailable'); };
    state.taskId = 'run-1';
    globalThis.subject = {poll, state};
  `, sandbox);
  return {sandbox, timers, rendered, busy, notices, elements, calls: () => calls};
}

test('a transient GET failure recovers without another task submission', async () => {
  const h = harness([new Error('connection reset'), {status: 'waiting_approval'}, {status: 'completed'}]);
  await h.sandbox.subject.poll('run-1');
  assert.match(h.elements.get('connection').textContent, /正在重试/);
  assert.equal(h.timers[0].delay, 1000);
  await h.timers.shift().fn();
  assert.equal(h.rendered[0].status, 'waiting_approval');
  assert.equal(h.elements.get('connection').textContent, 'AppContainer');
  await h.timers.shift().fn();
  assert.deepEqual(h.busy, [false]);
  assert.equal(h.calls(), 3);
});

test('five consecutive failures stop, with bounded exponential backoff', async () => {
  const h = harness(Array.from({length: 5}, () => new Error('offline')));
  await h.sandbox.subject.poll('run-1');
  const delays = [];
  while (h.timers.length) {const timer = h.timers.shift(); delays.push(timer.delay); await timer.fn();}
  assert.deepEqual(delays, [1000, 2000, 4000, 8000]);
  assert.equal(h.calls(), 5);
  assert.match(h.elements.get('connection').textContent, /自动重试已停止/);
  assert.deepEqual(h.busy, []); // An unseen active job must not allow duplicate submission.
});

test('a successful read resets the consecutive failure counter', async () => {
  const h = harness([new Error('offline'), {status: 'running'}, new Error('offline')]);
  await h.sandbox.subject.poll('run-1', 3);
  assert.equal(h.timers[0].delay, 8000);
  await h.timers.shift().fn();
  await h.timers.shift().fn();
  assert.equal(h.timers[0].delay, 1000);
});

test('an obsolete in-flight task cannot schedule retries or update connection state', async () => {
  const h = harness([new Error('offline')]);
  const request = h.sandbox.subject.poll('run-1');
  h.sandbox.subject.state.taskId = 'run-2';
  await request;
  assert.equal(h.timers.length, 0);
  assert.equal(h.elements.has('connection'), false);
});

test('a history refresh failure does not turn a completed job into a disconnected job', async () => {
  const h = harness([{status: 'completed'}]);
  h.sandbox.historyFailure = true;
  await h.sandbox.subject.poll('run-1');
  assert.equal(h.elements.get('connection').textContent, 'AppContainer');
  assert.deepEqual(h.busy, [false]);
  assert.equal(h.timers.length, 0);
  assert.deepEqual(h.notices, ['history unavailable']);
});
