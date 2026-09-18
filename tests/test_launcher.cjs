'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { buildLaunchPlan, isSandboxFailure, launch } = require('../scripts/launch-electron.cjs');

function fixture() {
  const processLike = new EventEmitter();
  const output = [];
  Object.assign(processLike, { platform: 'linux', getuid: () => 1000, env: { TEST_ONLY: '1' }, stderr: { write: chunk => output.push(chunk) } });
  const child = new EventEmitter();
  child.stderr = new EventEmitter();
  const signals = [], calls = [];
  child.kill = signal => { signals.push(signal); return true; };
  const spawnChild = (...args) => { calls.push(args); return child; };
  return { processLike, child, calls, signals, output, text: () => output.map(x => x.toString()).join(''),
    start: args => launch({ args, processLike, spawnChild, electronPath: '/fixture/electron', root: '/fixture/project' }) };
}

test('ordinary Linux uses user namespace sandbox path and preserves flags', () => {
  const result = buildLaunchPlan({ uid: 1000, platform: 'linux', args: ['--ozone-platform=x11', '--proxy-server=http://127.0.0.1:10822'] });
  assert.deepEqual(result.args, ['.', '--disable-setuid-sandbox', '--ozone-platform=x11', '--proxy-server=http://127.0.0.1:10822']);
  assert.equal(result.noSandbox, false);
});

test('explicit switch values follow Chromium presence semantics', () => {
  for (const flag of ['--no-sandbox', '--no-sandbox=false', '--no-sandbox=0']) {
    const result = buildLaunchPlan({ uid: 1000, platform: 'linux', args: [flag] });
    assert.equal(result.noSandbox, true);
    assert.deepEqual(result.args, ['.', flag]);
  }
  assert.equal(buildLaunchPlan({ uid: 1000, platform: 'linux', args: ['--no-sandbox-like'] }).noSandbox, false);
});

test('root and contradictory sandbox switches are rejected', () => {
  assert.throws(() => buildLaunchPlan({ uid: 0, platform: 'linux', args: ['--no-sandbox'] }), /普通桌面用户/);
  assert.throws(() => buildLaunchPlan({ uid: 1000, platform: 'linux', args: ['--no-sandbox=false', '--enable-sandbox=0'] }), /不能同时/);
});

test('existing SUID-disable switch is not duplicated and non-Linux is unchanged', () => {
  assert.deepEqual(buildLaunchPlan({ uid: 1000, platform: 'linux', args: ['--disable-setuid-sandbox'] }).args, ['.', '--disable-setuid-sandbox']);
  assert.deepEqual(buildLaunchPlan({ platform: 'win32', args: [] }).args, ['.']);
});

test('sandbox diagnosis matches known failures but not unrelated access errors', () => {
  for (const text of ['No usable sandbox!', 'The SUID sandbox helper binary was found, but is not configured correctly.', 'Failed to move to new namespace: Operation not permitted', '[FATAL:zygote_host_impl_linux.cc(207)] Operation not permitted']) assert.equal(isSandboxFailure(text), true);
  assert.equal(isSandboxFailure('Error: EACCES permission denied while reading config.json'), false);
});

test('launch passes executable, project cwd and inherited environment without a shell', async () => {
  const f = fixture();
  const result = f.start(['--ozone-platform=x11']);
  assert.deepEqual(f.calls, [['/fixture/electron', ['.', '--disable-setuid-sandbox', '--ozone-platform=x11'], { cwd: '/fixture/project', env: f.processLike.env, stdio: ['inherit', 'inherit', 'pipe'] }]]);
  const chunk = Buffer.from('diagnostic 中文\n');
  f.child.stderr.emit('data', chunk);
  f.child.emit('close', 7, null);
  assert.deepEqual(await result, { code: 7, signal: null });
  assert.equal(f.output[0], chunk);
  assert.equal(f.processLike.listenerCount('SIGINT'), 0);
  assert.equal(f.processLike.listenerCount('SIGTERM'), 0);
});

test('sandbox failure split across stderr chunks yields one advisory without retry', async () => {
  const f = fixture();
  const result = f.start([]);
  f.child.stderr.emit('data', Buffer.from('FATAL: No usable sand'));
  f.child.emit('exit', null, 'SIGABRT');
  f.child.stderr.emit('data', Buffer.from('box!\n'));
  f.child.emit('close', null, 'SIGABRT');
  assert.deepEqual(await result, { code: null, signal: 'SIGABRT' });
  assert.match(f.text(), /无需 root/);
  assert.match(f.text(), /--skip-install -- --no-sandbox/);
  assert.match(f.text(), /关闭 Chromium 所有进程的沙箱/);
  assert.equal(f.calls.length, 1);
});

test('explicit no-sandbox warns once and does not claim sandbox protection', async () => {
  const f = fixture();
  const result = f.start(['--no-sandbox=false']);
  f.child.stderr.emit('data', 'No usable sandbox!\n');
  f.child.emit('close', 1, null);
  await result;
  assert.match(f.text(), /已禁用 Chromium 所有进程的沙箱/);
  assert.doesNotMatch(f.text(), /无需 root/);
  assert.deepEqual(f.calls[0][1], ['.', '--no-sandbox=false']);
});

test('unrelated child failure does not suggest disabling sandbox', async () => {
  const f = fixture();
  const result = f.start([]);
  f.child.stderr.emit('data', 'EACCES: permission denied opening data\n');
  f.child.emit('close', 1, null);
  await result;
  assert.doesNotMatch(f.text(), /no-sandbox|兼容模式/);
});

test('signals are forwarded and child signal is preserved after listener cleanup', async () => {
  const f = fixture();
  const result = f.start([]);
  f.processLike.emit('SIGINT');
  f.processLike.emit('SIGTERM');
  assert.deepEqual(f.signals, ['SIGINT', 'SIGTERM']);
  f.child.emit('close', null, 'SIGTERM');
  assert.deepEqual(await result, { code: null, signal: 'SIGTERM' });
  assert.equal(f.processLike.listenerCount('SIGINT'), 0);
  assert.equal(f.processLike.listenerCount('SIGTERM'), 0);
});

test('failed process spawn reports error and cleans up signals', async () => {
  const f = fixture();
  const result = f.start([]);
  f.child.emit('error', new Error('ENOENT fixture'));
  f.child.emit('close', -2, null);
  assert.deepEqual(await result, { code: 1, signal: null });
  assert.match(f.text(), /ENOENT fixture/);
  assert.equal(f.processLike.listenerCount('SIGINT'), 0);
});

test('root rejection never creates an Electron process', async () => {
  const f = fixture();
  f.processLike.getuid = () => 0;
  assert.deepEqual(await f.start(['--no-sandbox']), { code: 1, signal: null });
  assert.equal(f.calls.length, 0);
  assert.match(f.text(), /普通桌面用户/);
});

test('diagnostic launcher captures native errors and mirrors already-persisted app events once', async () => {
  const f = fixture(), events = [], forwarded = [];
  const diagnostics = (event, fields) => events.push({ event, fields });
  diagnostics.forward = line => forwarded.push(line);
  const result = launch({ args: [], processLike: f.processLike, spawnChild: (...args) => { f.calls.push(args); return f.child; }, electronPath: '/fixture/electron', root: '/fixture/project', diagnostics });
  const backend = JSON.stringify({ component: 'backend', time: 'fixture-time', event: 'turn_start' });
  f.child.stderr.emit('data', Buffer.from('native startup error\n' + backend.slice(0, 20)));
  f.child.stderr.emit('data', Buffer.from(backend.slice(20) + '\n'));
  f.child.emit('close', 3, null);
  assert.deepEqual(await result, { code: 3, signal: null });
  assert.deepEqual(events.map(item => item.event), ['launcher_start', 'native_stderr', 'launcher_exit']);
  assert.deepEqual(forwarded, [backend]);
  assert.equal(f.output.length, 0);
});
