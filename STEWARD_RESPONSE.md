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
Contract address: 0x98D7375825Fa19Ec36C2432E9F07481D755524A6
Deploy tx:        0x15bcfbd620492b0af6f03a642f0696175e5bee4ddd41c9cd4cccc0c2b811709c
Explorer:         https://explorer-studio.genlayer.com/address/0x98D7375825Fa19Ec36C2432E9F07481D755524A6
```

Verified by retrieving the deployed source from the chain and comparing it to
the file in this repository — both are 583 lines with SHA-256
`cf782d13e26337cf950685a8…`, i.e. byte-identical:

```bash
genlayer network set studionet
genlayer code 0x98D7375825Fa19Ec36C2432E9F07481D755524A6 > deployed.py
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