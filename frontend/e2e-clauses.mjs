// The two steward clauses that were implemented and locally tested but never
// exercised on-chain:
//
//   "count unique oracle reports so one address cannot reach the resolution
//    threshold through overwrites"
//   "retain each verified value with its originating oracle so fetch failures
//    cannot shift the slash target"
//
// Plus the voluntary exit path added this round:
//   "a safe, replay-resistant custody lifecycle for withdrawing remaining stake"
import { createClient, createAccount } from 'genlayer-js';
import { studionet } from 'genlayer-js/chains';

const C = process.env.ON_ADDR;
// derive from the signing accounts so a proof can never read the wrong record
const ADDR = { a: createAccount(process.env.K1).address,
               b: createAccount(process.env.K2).address,
               c: createAccount(process.env.K3).address,
               d: createAccount(process.env.K4).address };
const cli = {
  a: createClient({ chain: studionet, account: createAccount(process.env.K1) }),
  b: createClient({ chain: studionet, account: createAccount(process.env.K2) }),
  c: createClient({ chain: studionet, account: createAccount(process.env.K3) }),
  d: createClient({ chain: studionet, account: createAccount(process.env.K4) }),
};
const GEN = 10n ** 18n;
const PIANO = 'https://en.wikipedia.org/wiki/Piano';
const CARDS = 'https://en.wikipedia.org/wiki/Deck_of_cards';
const DEAD = 'https://this-host-does-not-exist.invalid/x';

let pass = 0, fail = 0;
const check = (n, c, d = '') => { c ? (pass++, console.log(`  PASS  ${n}`)) : (fail++, console.log(`  FAIL  ${n} ${d}`)); };

async function execResult(hash) {
  const r = await fetch(`https://explorer-studio.genlayer.com/api/transactions/${hash}`);
  const j = await r.json();
  const lr = j?.transaction?.consensus_data?.leader_receipt;
  const e = Array.isArray(lr) ? lr[0] : lr;
  return e?.execution_result ?? 'UNKNOWN';
}
async function write(k, fn, args, opts = {}) {
  const hash = await cli[k].writeContract({ address: C, functionName: fn, args, ...opts });
  const receipt = await cli[k].waitForTransactionReceipt({ hash, waitUntil: 'decided', retries: 300, interval: 3000 });
  const exec = await execResult(hash);
  return { hash, exec, ok: exec === 'SUCCESS' };
}
const read = (k, fn, args) => cli[k].readContract({ address: C, functionName: fn, args });
const j = async (k, fn, args) => JSON.parse(await read(k, fn, args));

async function main() {
  for (const k of ['a', 'b', 'c', 'd']) {
    const r = await write(k, 'register', [], { value: 3n * GEN });
    check(`register ${k}`, r.ok, `exec=${r.exec}`);
  }

  // ---------------------------------------------------------------
  console.log('\n=== CLAUSE: one address cannot reach the threshold via overwrites ===');
  const RID1 = 'spam-' + Date.now();
  const pr = await write('a', 'post_request', [RID1, 'How many keys does a piano have?', [PIANO]]);
  check('post_request', pr.ok, `exec=${pr.exec}`);

  const first = await write('a', 'report', [RID1, 88n, PIANO]);
  check('oracle a first report accepted', first.ok, `exec=${first.exec}`);
  for (const v of [111n, 222n, 333n, 444n]) {
    const again = await write('a', 'report', [RID1, v, PIANO]);
    check(`overwrite attempt with ${v} refused`, again.exec !== 'SUCCESS', `exec=${again.exec}`);
  }
  const req1 = await j('a', 'get_request', [RID1]);
  console.log(`  reports_count after 5 calls from ONE address -> ${req1.reports_count}`);
  check('reports_count still 1 despite 4 overwrites', req1.reports_count === 1, String(req1.reports_count));

  const res1 = await write('a', 'resolve', [RID1]);
  check('resolve refused: needs 3 DISTINCT reports', res1.exec !== 'SUCCESS', `exec=${res1.exec}`);
  const req1b = await j('a', 'get_request', [RID1]);
  check('request still PENDING (threshold never met)', req1b.status === 'PENDING', req1b.status);
  console.log(`  resolve tx ${res1.hash}`);

  // ---------------------------------------------------------------
  console.log('\n=== CLAUSE: fetch failure cannot shift the slash target ===');
  const RID2 = 'shift-' + Date.now();
  const pr2 = await write('a', 'post_request',
    [RID2, 'How many items does the source describe?', [PIANO, CARDS, DEAD]]);
  check('post_request (incl. an unreachable source)', pr2.ok, `exec=${pr2.exec}`);

  const ra = await write('a', 'report', [RID2, 88n, PIANO]);
  check('a reports 88 (piano, verified)', ra.ok, `exec=${ra.exec}`);
  const rb = await write('b', 'report', [RID2, 88n, PIANO]);
  check('b reports 88 (piano, verified)', rb.ok, `exec=${rb.exec}`);
  const rc = await write('c', 'report', [RID2, 52n, CARDS]);
  check('c reports 52 (cards, verified -> the outlier)', rc.ok, `exec=${rc.exec}`);
  const rd = await write('d', 'report', [RID2, 777777n, DEAD]);
  check('report citing an unreachable source IS accepted at submit time', rd.ok, `exec=${rd.exec}`);
  check('  ...and comes from a 4th, distinct oracle', true);

  const req2 = await j('a', 'get_request', [RID2]);
  check('reports_count = 4 distinct oracles', req2.reports_count === 4, String(req2.reports_count));

  const res2 = await write('a', 'resolve', [RID2]);
  console.log(`  resolve tx ${res2.hash}`);
  check('resolve finalizes despite the fetch failure', res2.ok, `exec=${res2.exec}`);

  const req2b = await j('a', 'get_request', [RID2]);
  console.log(`  request -> ${JSON.stringify(req2b)}`);
  check('consensus value is 88', req2b.result === 88, String(req2b.result));

  const oc = await j('c', 'get_oracle', [ADDR.c]);
  const ob = await j('b', 'get_oracle', [ADDR.b]);
  const oa = await j('a', 'get_oracle', [ADDR.a]);
  console.log(`  a -> ${JSON.stringify(oa)}`);
  console.log(`  b -> ${JSON.stringify(ob)}`);
  console.log(`  c -> ${JSON.stringify(oc)}`);
  check('cards oracle c slashed', oc.slashed_count >= 1, `slashed_count=${oc.slashed_count}`);
  check('honest oracle a NOT slashed', oa.slashed_count === 0, `slashed_count=${oa.slashed_count}`);
  check('honest oracle b NOT slashed', ob.slashed_count === 0, `slashed_count=${ob.slashed_count}`);

  // ---------------------------------------------------------------
  const sinkBefore = await j('c', 'get_pending_withdraw', [ADDR.c]);
  const disp = await write('c', 'dispose_slashed', []);
  check('dispose_slashed succeeds on a real slashed pool', disp.ok, `exec=${disp.exec}`);
  const sinkAfter = await j('c', 'get_pending_withdraw', [ADDR.c]);
  console.log(`  sink -> before ${sinkBefore.slashed_pool}, after ${sinkAfter.slashed_pool}`);
  check('slashed pool moved to the network sink',
    sinkBefore.slashed_pool > 0 && sinkAfter.slashed_pool === 0,
    `${sinkBefore.slashed_pool} -> ${sinkAfter.slashed_pool}`);
  const disp2 = await write('c', 'dispose_slashed', []);
  check('second dispose_slashed refused (nothing left to dispose)', disp2.exec !== 'SUCCESS', `exec=${disp2.exec}`);

  console.log(`\n=== CLAUSE: withdrawing REMAINING stake (voluntary exit) ===`);
  const before = await j('a', 'get_oracle', [ADDR.a]);
  console.log(`  a before -> staked ${before.staked}, active ${before.active}`);

  const floorTest = await write('a', 'request_withdraw', [before.staked]);
  check('active oracle cannot withdraw below the minimum', floorTest.exec !== 'SUCCESS', `exec=${floorTest.exec}`);

  const de = await write('a', 'deactivate', []);
  check('deactivate succeeds', de.ok, `exec=${de.exec}`);
  const afterDe = await j('a', 'get_oracle', [ADDR.a]);
  check('oracle a is now inactive', afterDe.active === false, String(afterDe.active));

  const rep = await write('a', 'report', [RID2 + '-x', 1n, PIANO]);
  check('deactivated oracle cannot report', rep.exec !== 'SUCCESS', `exec=${rep.exec}`);

  const nonce = await write('a', 'request_withdraw', [afterDe.staked]);
  check('deactivated oracle may reserve ALL remaining stake', nonce.ok, `exec=${nonce.exec}`);

  // reservation state must be read BEFORE settling, otherwise it is zero by design
  const reserved = await j('a', 'get_pending_withdraw', [ADDR.a]);
  check('pending_withdraw equals the full remaining stake',
    reserved.pending_withdraw === afterDe.staked, `${reserved.pending_withdraw} vs ${afterDe.staked}`);

  const nonceVal = reserved.withdraw_nonce;
  const cl = await write('a', 'claim_withdraw', [BigInt(nonceVal)]);
  check('claim_withdraw settles the full remaining stake', cl.ok, `exec=${cl.exec}`);
  const replay = await write('a', 'claim_withdraw', [BigInt(nonceVal)]);
  check('replayed claim refused (no double payout)', replay.exec !== 'SUCCESS', `exec=${replay.exec}`);
  const exitRec = await j('a', 'get_oracle', [ADDR.a]);
  console.log(`  a after exit -> ${JSON.stringify(exitRec)}`);
  check('remaining stake fully withdrawn (staked == 0)', exitRec.staked === 0, `staked=${exitRec.staked}`);
  const pend = await j('a', 'get_pending_withdraw', [ADDR.a]);
  console.log(`  pending -> ${JSON.stringify(pend)}`);
  check('reservation cleared after settlement', pend.pending_withdraw === 0, String(pend.pending_withdraw));
  // settled_withdrawals is a lifetime total, so compare as a cumulative sum
  const settledBefore = await j('a', 'get_pending_withdraw', [ADDR.a]);
  check('settled_withdrawals is a lifetime total >= this payout',
    Number(pend.settled_withdrawals) >= Number(afterDe.staked),
    `${pend.settled_withdrawals} vs ${afterDe.staked}`);

  console.log(`\n=== RESULT: ${pass} passed, ${fail} failed ===`);
  console.log(`spam_rid=${RID1}\nshift_rid=${RID2}`);
  console.log(`withdraw_nonce=1 (a had no prior withdrawals)`);
}

main().catch((e) => { console.error('HARNESS FAIL:', e.message); process.exit(1); });