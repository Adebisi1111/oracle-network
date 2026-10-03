Hi,

Thanks for the detailed review — every point was fair, and the concern about
`resolve()` was the most valuable thing in it.

## What was actually wrong

The original contract looked correct in review but **could not execute**.
`resolve()` returned floats, and GenLayer's calldata encoder has no float type:

```
TypeError: not calldata encodable 88.0: float
```

Every consensus round aborted with all validators voting `disagree`. The method
appeared to work because the transaction still finalized, but no request had ever
actually resolved on-chain. Our local tests missed it because the test stubs do
not enforce that encoding rule — only a live consensus round does.

That is the single most important fix in this round.

## All seven points, and how each is enforced

| Your point | Enforcement | Live proof |
|---|---|---|
| Minimum stake before activation | `register` reverts below `min_stake` | 0.1 GEN refused, 5/5 validators agree; 3 GEN accepted |
| Unique oracle reports, no threshold inflation | one report row per (request, oracle); `reports_count` only increments on a new row | one address posted 5 reports → `reports_count` stayed **1**, `resolve` then **refused** |
| Report sources validated against request policy | a report whose source is not in the request's declared list is rejected | report citing an undeclared URL refused |
| Reject unsupported / missing / nonnumeric / invalid values, no fallback | the AI verdict must be `supported is True` and `verified_value` a real number; `bool`, `NaN`, `±inf`, strings, lists, dicts and null are all rejected | consensus returned 88 from the fetched source; there is no code path that can substitute the caller's claim |
| Each verified value stays bound to its originating oracle | values are carried as `{oracle, value}` records through consensus, never re-zipped positionally | dropped report + genuine outlier → only the real outlier slashed, both bystanders untouched |
| Replay-resistant custody for remaining stake | two-phase `request_withdraw` → `claim_withdraw(nonce)`; reservation zeroed before settlement; monotonic nonce; `deactivate()` for voluntary exit | reserve → settle → **replay refused**; slashed stake disposed to a sink, never recycled |
| Matching corrected submitted and deployed source | — | byte-identical, 626 lines, SHA-256 `ce6648783b9da6cf0bc8314b…` |

## The value↔oracle binding, concretely

This was the most subtle fix. The old code held two parallel lists and joined
them positionally:

```python
for oracle, value in zip(all_reports, verified_values):
```

If a report failed verification and was dropped, the lists desynchronised and
**every oracle after the dropped one was paired with the wrong value** — so the
wrong oracle would be slashed for another oracle's number.

Live demonstration: four oracles reported on one request, two citing a piano
(88 keys), one a deck of cards (52), and one an unreachable host so its report
drops out. Three values survive, are judged, and:

```
resolve -> {"status":"RESOLVED","result":88,"reports_count":3}

a -> slashed_count 0    honest
b -> slashed_count 0    honest
c -> slashed_count 1, total_slashed 0.3 GEN, staked 2.7 GEN
```

The fetch failure did not move the target. Under the old code it would have.

## One thing I want to be explicit about

**Stake accounting is on-chain; actual payout is not.** `settled_withdrawals`
and `slashed_sink` are authoritative amounts the contract records, but the
disbursement is operational, not contract-initiated.

This is a runtime limit, not a choice. `gl.Contract` can receive and hold native
GEN and expose `balance`, but it has **no** outbound transfer method —
`emit_transfer` exists only on `ContractProxy`, which is the handle for calling
another contract. We probed for this rather than assuming it.

So the contract guarantees the *amounts* owed and prevents double-payment or
over-withdrawal. Moving the GEN is handled off-contract by the operator. I'd
rather flag this than have it discovered later.

## Evidence

- **Contract:** `0x5ddE1c5c44C91Fe6FB12cc5433FE4cE5B7d9Ca13` (Studio Net, chain 61999)
- **Source:** `https://github.com/Adebisi1111/oracle-network`
- **Tests:** 61 passing, `genvm-lint` clean
- **Live proofs:** 17/17 invariants, 39/39 steward clauses, 18/18 slashing and disposal — all against this deployment

Every transaction above was verified against the leader's
`consensus_data.execution_result`, not the transaction status — a status of
`FINALIZED` is returned even when the method raised, so we check the execution
result specifically.

Happy to walk through any of it on a call.