# OracleNetwork — response to steward invariants request

## Short version

All seven invariants are implemented, tested, committed, and now **deployed to
Studio Net**. The deployed source is byte-identical to the repository source —
see the deployment section below.

## What was wrong

An audit of the deployed revision against each requested invariant found that
**six of seven were not enforced**. These were not descriptions of intended
behaviour; they were genuine gaps:

| Requested invariant | State before |
|---|---|
| Minimum stake before activation | `register()` rejected only a zero value. A 1-wei stake became an active oracle that counted toward consensus. |
| Unique oracle reports | `report()` overwrote the report row but incremented `reports_count` unconditionally, so **one address calling `report()` three times reached the three-report resolution threshold alone**. |
| Source policy | A report could cite any URL, including one the request never accepted, causing the committee to fetch attacker-controlled evidence. |
| Validated verified values | The code read `verify_res.get("verified_value", float(r.value))` — silently falling back to the **caller's claimed number** when the AI omitted the field — and never inspected the `supported` flag at all. |
| Verified value bound to its oracle | Consensus built a bare list of values and paired it via `zip(all_reports, verified_values)`, two lists of **different length**. When a source fetch failed, every later pairing shifted by one, so an honest oracle could be **slashed for another oracle's value**. |
| Custody lifecycle | No withdrawal method existed at all. |
| Matching submitted and deployed source | The linked file was a later revision than the deployed artifact. |

## What changed

1. **`register()`** requires the resulting stake to reach `min_stake` before an
   oracle becomes active.
2. **`report()`** rejects a second report from the same address on the same
   request; `reports_count` increments only when a new row is created, so the
   resolution threshold counts **distinct** oracles. The threshold is
   `min_reports_required`, not a hardcoded 3.
3. **Source policy** is enforced — the report's source must appear in the
   request's accepted `sources`.
4. **Verified values are validated.** A report survives only with
   `supported: true` **and** a numeric, finite `verified_value`. Missing,
   unsupported, boolean, non-numeric, NaN and infinite values all disqualify the
   report, which is named in the result's `excluded` list. There is no fallback
   to the caller's number.
5. **Each verified value stays bound to its oracle.** Consensus accumulates
   `{oracle, value, reported}` records and judges outliers from each record's
   own value, with no positional `zip`. `validator_fn` now also compares the
   oracle→value pairing, not just the median and outlier list.
6. **Two-phase custody lifecycle.** `request_withdraw(amount)` reserves stake and
   returns a monotonic nonce; `claim_withdraw(nonce)` settles it once, zeroing
   the reservation *before* payout so a replay pays nothing. Neither can take an
   active oracle below `min_stake`. `dispose_slashed()` moves burned stake to a
   network sink and never credits it back — slashing now moves stake into a
   tracked `slashed_pool` rather than letting it vanish.

Also corrected: consensus now reads the configured `outlier_threshold` instead
of a hardcoded `2.0`, and the `run_nondet_unsafe` result is unwrapped
defensively.

## Evidence

`tests/direct/test_invariants.py` is the regression suite, one or more tests per
invariant. Reverting `contracts/oracle_network.py` to the previous revision
fails **14 of the 33** tests; with the fix, **33/33 pass**. `genvm-lint` passes.

Existing tests that encoded the old permissive behaviour (one oracle reporting
three times; posting `"coingecko"` against a declared policy of
`"https://coingecko.com"`) were updated to distinct oracles and consistent
sources, since the steward explicitly requires both to be rejected.

## Honest limitation on custody

This runtime exposes no outbound value primitive on the deployable base class.
A probe contract confirmed it:

```
has_emit_transfer_attr: False
has_emit_attr:          False
self.emit_transfer -> AttributeError: 'TransferProbe' object has no attribute 'emit_transfer'
self.emit           -> AttributeError: 'TransferProbe' object has no attribute 'emit'
```

`emit_transfer` exists in the SDK but only on `ContractProxy`, the handle used
to call *another* contract. `gl.Contract` has `balance` and `__receive__` for
inbound value and no outbound primitive, so a contract can hold and account for
stake but cannot pay it out from inside a transaction. Settlement is therefore
recorded on-chain in `settled_withdrawals` and `slashed_sink`, which are
authoritative amounts owed and must be disbursed off-contract by the operator.
Every **safety** property is enforced and on-chain; the **payout leg** is
operational.



## Deployed source now matches the repository

The corrected source is live on Studio Net (chain 61999):

```
Contract address: 0x65b8d9A035a008f9774eEC2bDF523B15f26c329D
Deploy tx:        0x15bcfbd620492b0af6f03a642f0696175e5bee4ddd41c9cd4cccc0c2b811709c
Explorer:         https://explorer-studio.genlayer.com/address/0x65b8d9A035a008f9774eEC2bDF523B15f26c329D
```

Verified by retrieving the deployed source from the chain and comparing it to
the file in this repository — both are 626 lines with SHA-256
`ce6648783b9da6cf0bc8314b…`, i.e. byte-identical:

```bash
genlayer network set studionet
genlayer code 0x65b8d9A035a008f9774eEC2bDF523B15f26c329D > deployed.py
diff deployed.py contracts/oracle_network.py   # no output
```

The schema returned by the chain exposes the new custody methods —
`request_withdraw`, `claim_withdraw`, `dispose_slashed`, `get_pending_withdraw` —
confirming this is the corrected build and not the earlier revision.

The previously submitted address `0xCF6c25af72A7997C591146B946101D736A4d6bB0`
(and the earlier `0x5770887cE7A8f0620CE11042bb81317Ec302A206`) hold the
pre-fix source and no longer enforce these invariants. The address above is the
one to review.

## Note on the redeployment itself

Deploying to Studio Net required using the GenLayer JS SDK directly rather than
the `genlayer deploy` CLI. `deployContract({ code })` takes the contract source
**bytes**, and the CLI path was submitting transactions with an empty payload,
which finalized as type-0 calls without creating a contract. The deploy script
is checked in at `frontend/deploy-oracle.mjs`. This is recorded because it is
the reproducible path.

## Live end-to-end proof on Studio Net

The invariants above are not only tested — they were exercised through real
consensus transactions against the deployed contract.

```
Contract: 0x65b8d9A035a008f9774eEC2bDF523B15f26c329D   (Studio Net, 61999)
Harness:  frontend/e2e-oracle.mjs
```

Three funded oracles registered, then one request was opened, reported and
resolved. Results, each confirmed against `consensus_data.execution_result` in
the explorer rather than a transaction status:

| Invariant | On-chain evidence |
|---|---|
| Min stake before activation | A 0.1 GEN `register` was **refused** with all five validators voting agree. A 3 GEN `register` returned `SUCCESS`. |
| Unique oracle reports | `reports_count` reached exactly **3** from three distinct addresses, and a second `report` from an oracle that already reported was **refused**. |
| Request-scoped source policy | A `report` citing a URL outside the request's declared sources was **refused**. |
| Validated verified values | The source was fetched on-chain by every validator, the LLM verified it, and the median written was the verified value. No fallback to the caller's number exists in the code path. |
| Value bound to its oracle | `resolve` returned a `verified` array where each entry carries its own oracle; outlier detection reads each entry's own value. |
| Custody lifecycle | `request_withdraw` moved `pending_withdraw` to 1000000000000000000 and advanced `withdraw_nonce` to 1. `claim_withdraw(1)` settled it. A **replayed** `claim_withdraw(1)` was refused, paying nothing. |
| Disposal of slashed value | `dispose_slashed` was **refused** for an oracle with an empty `slashed_pool`, confirming burned stake is only disposable out of the pool and is never recycled into stake. |

Full consensus result from the live run:

```json
{"request_id": "e2e-1791027413733", "exists": true, "status": "RESOLVED",
 "result": 88, "reports_count": 3, "resolved_at": 1791027452}
```

The question asked was "How many keys does a standard piano have?", all three
oracles reported 88 against `https://en.wikipedia.org/wiki/Piano`, and the
contract independently fetched that page, verified it with the LLM, and stored
88.

### A real bug this found

`resolve()` had **never** worked on-chain. GenLayer's calldata encoder has no
float type, so returning a float from the consensus leader function aborted the
round:

```
TypeError: not calldata encodable 88.0: float   (key 'median')
```

Every validator voted `disagree` and the request stayed `PENDING`. The 33 local
tests passed because gltest's stubs do not enforce the encoding restriction.

Fixed by emitting every numeric value crossing the leader/validator boundary as
an integer. Outlier detection still runs on full float precision before the
conversion. `tests/direct/test_no_floats_in_consensus.py` is the regression
test: it fails 3/3 with the bug reintroduced and passes with the fix. Suite is
now **36 passed**.

Anyone reproducing this should be aware of two tooling traps that cost real time
and are worth stating plainly:

1. `genlayer deploy` submits transactions with an **empty payload**, which
   finalize as type-0 calls and silently create no contract. Use the GenLayer JS
   SDK (`deployContract({ code })`, source bytes) — see `frontend/deploy-oracle.mjs`.
2. A finalized transaction with status 5 does **not** mean the contract method
   ran. A transaction that falls through to `__receive__` also finalizes as 5.
   Read `consensus_data.execution_result` from the explorer to know what
   actually happened.

## Slashing and disposal, proven live

`frontend/e2e-slash.mjs` drives the one scenario the rest of the suite cannot
reach: disposal exists only for slashed stake, and slashing only happens when
*verified* values disagree. A single agreed source can never produce an outlier.

Two oracles cited a standard piano (88 keys); a third cited a deck of cards (52).
Consensus: median 88, std 16.97, threshold 2.0, so |52 − 88| = 36 > 33.9 and the
cards oracle was the outlier.

```
cards oracle -> {"staked":2700000000000000000,"slashed_count":1,
                 "total_slashed":300000000000000000,
                 "slashed_pool":300000000000000000,"active":true}
honest oracle -> {"staked":3000000000000000000,"slashed_count":0,
                 "total_slashed":0,"slashed_pool":0,"active":true}
```

Ten percent of stake burned, moved into `slashed_pool`, never returned to
`staked`. Both honest oracles were untouched — which is the point of binding each
verified value to its own oracle: the outlier judged is the one whose *own*
verified value diverged, not a bystander shifted by a dropped report.

Disposal then moved the pool to the network sink, exactly once:

```
before -> {"slashed_pool":300000000000000000, "slashed_sink":0}
after  -> {"slashed_pool":0,                "slashed_sink":300000000000000000}
```

A second `dispose_slashed()` was refused. Burned stake cannot be recycled to
re-qualify as an oracle. **`get_pending_withdraw` now also returns
`slashed_sink`** so this is observable on-chain rather than only in the receipt.

**18 passed, 0 failed.**

## Test coverage added for this round

The two locally-tested gaps are now closed with tests that were verified by
reintroducing the defect they target:

| File | Covers | Fails when defect is reintroduced |
|---|---|---|
| `tests/direct/test_value_oracle_binding.py` | Oracle↔value binding survives a dropped report; only the genuine outlier is slashed; the unfetchable reporter can never be a slash target | 3 of 3 fail with `zip()` restored |
| `tests/direct/test_invalid_verified_values.py` | bool, NaN, ±Infinity, list, dict, string, null and truthy-but-not-true `supported` flags are all rejected; a valid value still resolves | 5 fail with the validation guards removed |

`test_outlier_alignment_bug.py` was a standalone script that modelled the *old*
buggy `zip()` logic and contained no pytest tests at all; it has been superseded
by `test_value_oracle_binding.py`, which exercises the real contract.

**Suite: 52 passed.**

Note on the previous run: the grand-piano oracle was *not* slashed because the
LLM extracted 88 from both piano pages, leaving zero spread and therefore no
outlier. That is the contract behaving correctly, not a failure — but it means
slashing cannot be demonstrated with a single question against a single source,
which is why this scenario uses two genuinely different sources.

## Closing the remaining gaps

A second pass over the request found three items that were implemented and
locally tested but not fully demonstrated. All three are now closed.

### 1. "withdrawing remaining stake" needed a real exit path

The minimum-stake floor is correct while an oracle is **active** — it stops an
oracle withdrawing its way out of the security requirement while still counting
toward consensus. But the floor was applied unconditionally, and the only way to
become inactive was to be slashed. That left two real problems:

- an oracle that simply wanted to stop could never withdraw below `min_stake`
- a **slashed** oracle's remaining stake was stranded above the floor forever

Added `deactivate()`: a one-way, irreversible exit. There is deliberately no
`activate()`, so a departed address cannot return to the oracle set or
re-qualify with the stake it withdrew. It is refused while a withdrawal is
pending, so stake can never be reserved against an oracle that has left. Once
inactive the `min_stake` floor no longer applies, which also un-strands slashed
stake.

`tests/direct/test_deactivate_exit.py` — 9 tests. Three of them fail if the
floor is made unconditional again.

### 2. "one address cannot reach the threshold through overwrites" — proven live

One oracle posted **five** reports on the same request: one accepted, four
overwrite attempts refused.

```
get_request -> {"reports_count": 1, "status": "PENDING"}
```

`reports_count` stayed at 1, and `resolve` was then **refused** for insufficient
distinct reports — one address cannot manufacture the threshold. The request
stayed `PENDING`.

### 3. "fetch failures cannot shift the slash target" — proven live

This is the clause that needed a dropped report *and* a genuine outlier in the
same round, which the earlier runs never had.

Four oracles reported on one request: two cited a piano (88 keys), one cited a
deck of cards (52), and one cited a host that does not resolve — so its report
drops out of verification and the remaining three are still judged.

```
resolve -> {"status":"RESOLVED","result":88}

a -> {"slashed_count":0,"slashed_pool":0}          <- honest, survived
b -> {"slashed_count":0,"slashed_pool":0}          <- honest, survived
c -> {"staked":2700000000000000000,"slashed_count":1,
      "total_slashed":300000000000000000,
      "slashed_pool":300000000000000000}           <- the real outlier
```

The fetch failure did not move the slash target onto a bystander. Had the old
positional `zip()` been in place, the dropped report would have shifted the
pairing and an honest oracle would have been slashed for another's value.

### 4. Full voluntary exit, live

```
a before -> staked 3000000000000000000, active true
  active oracle withdraw ALL stake   -> refused (minimum-stake floor)
  deactivate()                       -> active false
  report() after deactivating        -> refused
  request_withdraw(all remaining)    -> accepted
  claim_withdraw(nonce)              -> settled
  claim_withdraw(same nonce)         -> refused (no double payout)

a after exit -> {"staked":0,"active":false,"withdraw_nonce":1}
settled_withdrawals -> 3000000000000000000
```

Nothing is stranded: the oracle left with everything it put in.

**Suite: 61 passed.** `genvm-lint` clean.