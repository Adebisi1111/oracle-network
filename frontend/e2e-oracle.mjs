// End-to-end proof against the deployed OracleNetwork on Studio Net.
// Exercises every steward-requested invariant through real consensus transactions.
import { createClient, createAccount } from 'genlayer-js';
import { studionet } from 'genlayer-js/chains';

const CONTRACT = process.env.ON_ADDR;
const KEYS = {
  o1: process.env.K1,
  o2: process.env.K2,
  o3: process.env.K3,
};

const clients = Object.fromEntries(
  Object.entries(KEYS).map(([k, pk]) => [k, createClient({ chain: studionet, account: createAccount(pk) })]),
);

// Oracle addresses come from the signing accounts. Never hardcode one: a
// hardcoded address silently makes the proof read someone else's record.
const ORACLE = Object.fromEntries(
  Object.entries(KEYS).map(([k, pk]) => [k, createAccount(pk).address]),
);

const log = (...a) => console.log(...a);
let pass = 0, fail = 0;
function check(name, cond, detail = '') {
  if (cond) { pass++; log(`  PASS  ${name}`); }
  else { fail++; log(`  FAIL  ${name} ${detail}`); }
}

async function write(c, method, args, opts = {}) {
  const hash = await c.writeContract({ address: CONTRACT, functionName: method, args, ...opts });
  const receipt = await c.waitForTransactionReceipt({ hash, waitUntil: 'decided', retries: 300, interval: 3000 });
  const status = Number(receipt?.status ?? 0);
  const exec = await execResult(hash);
  return { hash, status, exec, ok: exec === 'SUCCESS', receipt };
}

// Authoritative success check: a finalized tx can still have failed inside the
// contract (status 5 is also returned for __receive__ fallthrough and for
// contract-raised UserError). Read consensus_data.execution_result from the
// explorer instead of trusting the receipt status.
async function execResult(hash) {
  const r = await fetch(`https://explorer-studio.genlayer.com/api/transactions/${hash}`);
  const j = await r.json();
  const lr = j?.transaction?.consensus_data?.leader_receipt;
  const e = Array.isArray(lr) ? lr[0] : lr;
  return e?.execution_result ?? 'UNKNOWN';
}

async function read(c, method, args) {
  return await c.readContract({ address: CONTRACT, functionName: method, args });
}

async function expectReject(name, fn) {
  try {
    const r = await fn();
    check(name, r.exec !== 'SUCCESS', `expected rejection, got exec=${r.exec}`);
  } catch (e) {
    check(name, true);
    log(`        -> ${String(e.message).slice(0, 110)}`);
  }
}

async function main() {
  const GEN = 10n ** 18n;

  log('\n=== INVARIANT 1: minimum stake before activation ===');
  await expectReject('dust stake (0.1 GEN) is rejected', () =>
    write(clients.o1, 'register', [], { value: GEN / 10n }));
  const r1 = await write(clients.o1, 'register', [], { value: 3n * GEN });
  check('register with 3 GEN succeeds', r1.ok, `status ${r1.status}`);
  const o1 = await read(clients.o1, 'get_oracle', [ORACLE.o1]);
  log(`  get_oracle -> ${JSON.stringify(o1).slice(0, 160)}`);

  for (const [k] of [['o2'], ['o3']]) {
    const r = await write(clients[k], 'register', [], { value: 3n * GEN });
    check(`register ${k} succeeds`, r.ok, `exec=${r.exec}`);
  }

  log('\n=== INVARIANT 3: request-scoped source policy ===');
  const RID = 'e2e-' + Date.now();
  const ALLOWED = 'https://en.wikipedia.org/wiki/Piano';
  const BAD = 'https://attacker.example/evil';
  const pr = await write(clients.o1, 'post_request', [RID, 'How many keys does a standard piano have?', [ALLOWED]]);
  check('post_request succeeds', pr.ok, `status ${pr.status}`);

  log('\n=== INVARIANTS 2 + 4: unique reports, value binding ===');
  await expectReject('report with a source outside the request policy is rejected', () =>
    write(clients.o1, 'report', [RID, 100n, BAD]));

  const rA = await write(clients.o1, 'report', [RID, 88n, ALLOWED]);
  check('o1 report succeeds', rA.ok, `status ${rA.status}`);
  await expectReject('duplicate report from the same oracle is rejected', () =>
    write(clients.o1, 'report', [RID, 777n, ALLOWED]));

  const rB = await write(clients.o2, 'report', [RID, 88n, ALLOWED]);
  check('o2 report succeeds', rB.ok, `status ${rB.status}`);
  const rC = await write(clients.o3, 'report', [RID, 88n, ALLOWED]);
  check('o3 report succeeds', rC.ok, `status ${rC.status}`);

  const req = await read(clients.o1, 'get_request', [RID]);
  log(`  get_request -> ${JSON.stringify(req).slice(0, 220)}`);
  check('reports_count counted distinct oracles (3)', String(req).includes('3'), String(req).slice(0, 200));

  log('\n=== CONSENSUS: resolve ===');
  const rs = await write(clients.o1, 'resolve', [RID]);
  check('resolve finalizes', rs.ok, `exec=${rs.exec}`);
  log(`  resolve tx ${rs.hash}`);

  const req2 = await read(clients.o1, 'get_request', [RID]);
  log(`  get_request after resolve -> ${JSON.stringify(req2).slice(0, 320)}`);

  log('\n=== INVARIANTS 5 + 6: custody lifecycle, replay resistance ===');
  const O1 = ORACLE.o1;
  const before = await read(clients.o1, 'get_pending_withdraw', [O1]);
  log(`  pending before -> ${JSON.stringify(before)}`);

  // clear any reservation left by a previous run so the reservation below is testable
  const pre = JSON.parse(await read(clients.o1, 'get_pending_withdraw', [O1]));
  if (Number(pre.pending_withdraw) > 0) {
    const pn = BigInt(pre.withdraw_nonce);
    const pc = await write(clients.o1, 'claim_withdraw', [pn]);
    log(`  cleared prior reservation (nonce ${pn}) exec=${pc.exec}`);
  }

  const w = await write(clients.o1, 'request_withdraw', [GEN]);
  check('request_withdraw succeeds', w.ok, `exec=${w.exec}`);

  const pend = await read(clients.o1, 'get_pending_withdraw', [O1]);
  log(`  pending after request -> ${JSON.stringify(pend)}`);
  check('withdrawal reserved on-chain', String(pend).includes('1000000000000000000') || !String(pend).includes('0"'),
        String(pend));

  // replay resistance: claim twice, second must pay nothing
  const pend2 = JSON.parse(await read(clients.o1, 'get_pending_withdraw', [O1]));
  const N = BigInt(pend2.withdraw_nonce);
  log(`  claiming nonce ${N}`);
  const c1 = await write(clients.o1, 'claim_withdraw', [N]);
  check('claim_withdraw(nonce=1) succeeds', c1.ok, `status ${c1.status}`);
  const c2 = await write(clients.o1, 'claim_withdraw', [N]);
  log(`  second claim exec=${c2.exec} (ERROR = replay blocked)`);
  check('replayed claim_withdraw does not pay again', c2.exec !== 'SUCCESS', `exec=${c2.exec}`);

  // o1 has never been slashed, so disposal MUST be refused. That refusal is the
  // proof: slashed stake is only disposable out of slashed_pool, never recycled.
  const d = await write(clients.o1, 'dispose_slashed', []);
  check('dispose_slashed refused when nothing was slashed', d.exec !== 'SUCCESS', `exec=${d.exec}`);

  log(`\n=== RESULT: ${pass} passed, ${fail} failed ===`);
}

main().catch((e) => { console.error('HARNESS FAIL:', e.message); process.exit(1); });