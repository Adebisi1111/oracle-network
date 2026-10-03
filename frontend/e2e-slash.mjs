// Live proof of the slashing + disposal lifecycle.
//
// This is the one part of the steward request that could not be shown with a
// single agreed source: disposal only exists for slashed stake, and slashing
// only happens when VERIFIED values disagree. So this run deliberately gives
// two oracles cite a standard piano (88 keys) and one cites a deck of cards (52).
//
//   values [88, 88, 52]  median 88, std 16.97, threshold 2.0 -> |52-88| = 36 > 33.9
//   so the cards oracle is the outlier and is slashed.
//
// Then the slashed oracle disposes of its own burned stake.
import { createClient, createAccount } from 'genlayer-js';
import { studionet } from 'genlayer-js/chains';

const C = process.env.ON_ADDR;
const clients = {
  a: createClient({ chain: studionet, account: createAccount(process.env.K1) }),
  b: createClient({ chain: studionet, account: createAccount(process.env.K2) }),
  c: createClient({ chain: studionet, account: createAccount(process.env.K3) },
),
};
const ADDR = {
  a: '0x61fd0047595A30A067f1F21F3b28C4AE8A8e3Dc3',
  b: process.env.A2,
  c: process.env.A3,
};
const GEN = 10n ** 18n;
const PIANO = 'https://en.wikipedia.org/wiki/Piano';            // 88 keys
const CARDS = 'https://en.wikipedia.org/wiki/Deck_of_cards';   // 52 cards

let pass = 0, fail = 0;
const check = (n, c, d = '') => { c ? (pass++, console.log(`  PASS  ${n}`)) : (fail++, console.log(`  FAIL  ${n} ${d}`)); };

async function execResult(hash) {
  const r = await fetch(`https://explorer-studio.genlayer.com/api/transactions/${hash}`);
  const j = await r.json();
  const lr = j?.transaction?.consensus_data?.leader_receipt;
  const e = Array.isArray(lr) ? lr[0] : lr;
  return e?.execution_result ?? 'UNKNOWN';
}

async function write(cli, fn, args, opts = {}) {
  const hash = await cli.writeContract({ address: C, functionName: fn, args, ...opts });
  const receipt = await cli.waitForTransactionReceipt({ hash, waitUntil: 'decided', retries: 300, interval: 3000 });
  const exec = await execResult(hash);
  return { hash, exec, ok: exec === 'SUCCESS' };
}

const read = (cli, fn, args) => cli.readContract({ address: C, functionName: fn, args });

async function main() {
  const RID = 'slash-' + Date.now();

  console.log('\n=== register three funded oracles ===');
  for (const k of ['a', 'b', 'c']) {
    const r = await write(clients[k], 'register', [], { value: 3n * GEN });
    check(`register ${k}`, r.ok, `exec=${r.exec}`);
  }

  console.log('\n=== request with two different evidence sources ===');
  const pr = await write(clients.a, 'post_request',
    [RID, 'How many items does the source describe?', [PIANO, CARDS]]);
  check('post_request', pr.ok, `exec=${pr.exec}`);

  console.log('\n=== reports: two piano (88), one cards (52) ===');
  const ra = await write(clients.a, 'report', [RID, 88n, PIANO]);
  check('oracle a reports 88 (standard)', ra.ok, `exec=${ra.exec}`);
  const rb = await write(clients.b, 'report', [RID, 88n, PIANO]);
  check('oracle b reports 88 (standard)', rb.ok, `exec=${rb.exec}`);
  const rc = await write(clients.c, 'report', [RID, 52n, CARDS]);
  check('oracle c reports 52 (cards)', rc.ok, `exec=${rc.exec}`);

  const req = JSON.parse(await read(clients.a, 'get_request', [RID]));
  check('reports_count = 3 distinct oracles', req.reports_count === 3, JSON.stringify(req));

  console.log('\n=== resolve -> consensus + slashing ===');
  const rs = await write(clients.a, 'resolve', [RID]);
  console.log(`  resolve tx ${rs.hash}`);
  if (!rs.ok) {
    check('resolve finalizes', false, `exec=${rs.exec} (validators may have disagreed on the extracted number)`);
  } else {
    check('resolve finalizes', true);
  }

  const after = JSON.parse(await read(clients.a, 'get_request', [RID]));
  console.log(`  request -> ${JSON.stringify(after)}`);
  check('status RESOLVED', after.status === 'RESOLVED', after.status);
  check('result is the consensus value (88)', after.result === 88, String(after.result));

  console.log('\n=== the outlier was slashed ===');
  const oc = JSON.parse(await read(clients.c, 'get_oracle', [ADDR.c]));
  const ob = JSON.parse(await read(clients.a, 'get_oracle', [ADDR.b]));
  console.log(`  cards oracle -> ${JSON.stringify(oc)}`);
  console.log(`  honest oracle     -> ${JSON.stringify(ob)}`);
  check('cards oracle was slashed', oc.slashed_count >= 1, `slashed_count=${oc.slashed_count}`);
  check('slashed stake moved to slashed_pool', oc.slashed_pool > 0, `slashed_pool=${oc.slashed_pool}`);
  check('honest oracle NOT slashed', ob.slashed_count === 0, `slashed_count=${ob.slashed_count}`);

  console.log('\n=== disposal of slashed value ===');
  const before = JSON.parse(await read(clients.c, 'get_pending_withdraw', [ADDR.c]));
  console.log(`  before -> ${JSON.stringify(before)}`);
  const dp = await write(clients.c, 'dispose_slashed', []);
  check('dispose_slashed succeeds', dp.ok, `exec=${dp.exec}`);

  const post = JSON.parse(await read(clients.c, 'get_pending_withdraw', [ADDR.c]));
  console.log(`  after  -> ${JSON.stringify(post)}`);
  check('slashed_pool emptied', post.slashed_pool === 0, `slashed_pool=${post.slashed_pool}`);
  check('moved to slashed_sink', post.slashed_sink > 0, `slashed_sink=${post.slashed_sink}`);

  const dp2 = await write(clients.c, 'dispose_slashed', []);
  check('second dispose refused (one-shot)', dp2.exec !== 'SUCCESS', `exec=${dp2.exec}`);

  console.log(`\n=== RESULT: ${pass} passed, ${fail} failed ===`);
  console.log(`request_id=${RID}`);
}

main().catch((e) => { console.error('HARNESS FAIL:', e.message); process.exit(1); });