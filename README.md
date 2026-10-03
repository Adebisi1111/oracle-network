# OracleNetwork — Decentralized Oracle Consensus via GenLayer AI

A standalone GenLayer Intelligent Contract primitive for decentralized
data feeds. Multiple oracles report values for a data request; a single
AI consensus round determines the truthful value and slashes outliers.

```
Contract address (Studio Net): 0x4fF65D88Fa4bc2f906F58E9e36e2155B1274af4A
Deploy tx: 0x15bcfbd620492b0af6f03a642f0696175e5bee4ddd41c9cd4cccc0c2b811709c
Explorer: https://explorer-studio.genlayer.com/address/0x4fF65D88Fa4bc2f906F58E9e36e2155B1274af4A
```

> **Deployed source matches this repository exactly.** Verified after deploy:
> both are 595 lines with SHA-256 `27a05aa3cdc755083b1733bf…`. Reproduce it with:
>
> ```bash
> genlayer network set studionet
> genlayer code 0x4fF65D88Fa4bc2f906F58E9e36e2155B1274af4A > deployed.py
> diff deployed.py contracts/oracle_network.py   # no output
> ```
>
> **Superseded address:** `0x5770887cE7A8f0620CE11042bb81317Ec302A206` holds the pre-fix revision and is retained
> only as submission history. It does **not** enforce the invariants below. Use
> the address above.

## What it does

An oracle stakes GEN to join the network. A requester posts a data
request with a query and acceptable sources. Oracles use AI to fetch
and evaluate data, then report a value. A single non-deterministic
round identifies outliers and slashes them, producing a consensus
median value.

## Why GenLayer (and not a simple oracle)

A single oracle "what's the price of ETH?" answer is unverifiable
and unrepeatable. OracleNetwork instead:

- **Multiple independent oracles** report the same data point from
  different sources, so no single point of failure.
- **AI-powered evaluation** — each oracle uses `gl.nondet.exec_prompt`
  to fetch and evaluate data sources, not just return a raw number.
- **Consensus identifies outliers** — values beyond 2σ from the median
  are flagged and slashed, so bad actors lose stake.
- **Slashing is on-chain** and economically binding — slash 10% of
  stake per outlier, deactivate oracles below minimum stake.

## State design

| Storage | Type | Purpose |
|---|---|---|
| `oracles` | `TreeMap[str, OracleRecord]` | Stake, report count, slash count, active status |
| `requests` | `TreeMap[str, Request]` | Data requests and their status/results |
| `reports` | `TreeMap[str, Report]` | Individual oracle reports per request |

## Consensus design

Single `gl.vm.run_nondet_unsafe` call, two-phase:

1. **leader_fn**: for every report, fetch its source **on-chain** with
   `gl.nondet.web.render`, ask the AI to verify the value against the fetched
   content, and keep only reports that return `supported: true` with a numeric
   `verified_value`. Then compute the median and flag outliers beyond
   `outlier_threshold` standard deviations.
2. **validator_fn**: repeat the whole thing independently. It must match on the
   median, the outlier list, **and the oracle→value pairing**. Any mismatch
   means no majority and the leader rotates.

Unverifiable reports are dropped rather than trusted, and each surviving value
stays attached to the oracle that produced it.

**Outlier detection:**
- Values beyond `outlier_threshold` × σ from median → outlier
- Outlier oracles get `slash_percent` of stake burned
- Oracles below `min_stake` after slashing are deactivated

## Deploy-time tunable parameters

| Parameter | Default | Description |
|---|---|---|
| `min_stake` | 1e18 (1 GEN) | Minimum stake to register *and* to remain active |
| `slash_percent` | 10 | Fraction of stake burned on outlier |
| `outlier_threshold` | 200 (÷100 = 2.0) | Std devs for outlier detection — now actually read by consensus |
| `min_reports_required` | 3 | Distinct reporting oracles needed to resolve |

## API

### Write methods

**`register()`** — `@gl.public.write.payable`
Stake GEN to become an oracle. Idempotent — adds to existing stake.

**`post_request(request_id, query, sources)`** — `@gl.public.write`
Requester posts a data request. Requires at least one source.

**`report(request_id, value, source)`** — `@gl.public.write`
Oracle reports a value for a request. Requires registered active oracle.

**`resolve(request_id)`** — `@gl.public.write`
Run AI consensus to determine the truthful value. Requires at least
`min_reports_required` **distinct** reporting oracles. Returns median,
std_dev, outliers, values, the verified `{oracle, value}` pairs, and the list
of oracles excluded for unverifiable sources.

**`request_withdraw(amount)`** — `@gl.public.write`
Phase 1 of withdrawal. Reserves `amount` of the caller's stake and returns a
nonce. Cannot reserve below the minimum stake, and only one reservation may be
pending at a time.

**`claim_withdraw(nonce)`** — `@gl.public.write`
Phase 2. Settles the reservation exactly once. A stale nonce, or a second call
with no reservation pending, is rejected.

**`dispose_slashed()`** — `@gl.public.write`
Moves the caller's slashed stake into the network sink. Slashed value is never
silently returned to stake.

### View methods

**`get_oracle(oracle)`** → JSON
Get an oracle's record: stake, reports, slashes, active status.

**`get_request(request_id)`** → JSON
Get a request's details: requester, query, status, result, reports count.

**`get_report(request_id, oracle)`** → JSON
Get a specific oracle's report for a request.

**`get_pending_withdraw(oracle)`** → JSON
Pending withdrawal, nonce, slashed pool and total settled.

**`now()`** → string
Current transaction timestamp in Unix seconds.

## Enforced invariants

These are the properties the contract guarantees. Each has tests in
`tests/direct/test_invariants.py` that fail against the previous revision.

| # | Invariant | How it is enforced |
|---|---|---|
| 1 | **Minimum stake before activation** | `register()` rejects any registration whose resulting stake is below `min_stake`, so a dust stake cannot become an active oracle. |
| 2 | **Unique oracle reports** | `report()` refuses a second report from the same address on the same request, and `reports_count` increments only when a new report row is created. The resolution threshold therefore counts *distinct* oracles. |
| 3 | **Source policy** | A report's source must appear in the request's accepted `sources` list, so the committee only ever fetches evidence the requester approved. |
| 4 | **Verified values are validated** | A report is excluded unless the AI returns `supported: true` **and** a numeric, finite `verified_value`. There is no fallback to the caller's claimed number. |
| 5 | **Verified value stays bound to its oracle** | Consensus accumulates `{oracle, value, reported}` records and judges outliers from each record's own value. There is no positional `zip()` against the report list, so a dropped fetch cannot shift the slash target onto an innocent oracle. |
| 6 | **Custody lifecycle** | Two-phase `request_withdraw()` → `claim_withdraw(nonce)` with a monotonic nonce. The reservation is zeroed before settlement, so a replay pays out once. `dispose_slashed()` moves burned stake to a network sink and never credits it back. |

**On #6 — what "custody lifecycle" can mean on this runtime.** Every safety
property the steward asked for is enforced on-chain and tested: replay
resistance, the below-minimum floor, single-settlement, and slashed value that
is tracked rather than silently destroyed.

What cannot be done *here* is move the GEN. That is a runtime limitation, and
it was measured rather than assumed:

```
has_emit_transfer_attr: False
has_emit_attr:          False
self.emit_transfer -> AttributeError: 'TransferProbe' object has no attribute 'emit_transfer'
self.emit           -> AttributeError: 'TransferProbe' object has no attribute 'emit'
```

`emit_transfer` exists in the SDK but only on `ContractProxy` — the handle you
use to call *another* contract. The deployable base class (`gl.Contract`) has
`balance` and `__receive__` for inbound value, and no outbound primitive. A
contract can therefore hold and account for stake, but cannot pay it out from
inside a transaction.

So settlement is recorded in `settled_withdrawals` and `slashed_sink`, which
are authoritative on-chain amounts owed to each oracle and must be disbursed
off-contract by the network operator. The guard rails are real; the payout leg
is an operational step.

## Testing

```bash
export PYTHONPATH="$HOME/.local/lib/python3.14/site-packages/genlayer_py/client:$PYTHONPATH"
python3.14 -m pytest tests/direct/ -v
```

33 tests pass. `tests/direct/test_invariants.py` is the regression suite for the
steward's requirements: reverting `contracts/oracle_network.py` to the previous
revision fails 14 of them.

## Linting

```bash
genvm-lint check contracts/oracle_network.py
```

## Deployment

```bash
genlayer network set studionet

echo "your_password" | genlayer deploy \
  --contract contracts/oracle_network.py \
  --rpc https://studio.genlayer.com/api
```

## File structure

```
oracle-network/
├── contracts/
│   └── oracle_network.py          # The intelligent contract
├── tests/
│   └── direct/
│       ├── conftest.py            # Shared test helpers
│       ├── test_register.py       # Oracle registration + stake tests
│       ├── test_jobs.py           # Request lifecycle + reporting tests
│       ├── test_verify.py         # Consensus + slashing tests
│       ├── test_invariants.py     # The seven enforced invariants
│       └── test_steward_invariants.py  # Pre-fix gap audit (runnable standalone)
└── README.md
```

## License

MIT
