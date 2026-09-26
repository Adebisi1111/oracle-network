# OracleNetwork — Decentralized Oracle Consensus via GenLayer AI

A standalone GenLayer Intelligent Contract primitive for decentralized
data feeds. Multiple oracles report values for a data request; a single
AI consensus round determines the truthful value and slashes outliers.

```
Contract address (Studio Net): 0x5770887cE7A8f0620CE11042bb81317Ec302A206
Deploy tx: 0x7c5cc242bf62748b6d87fe97be700d4c4a1b740aa7acad36b5b4947838a43a22
Explorer: https://explorer-studio.genlayer.com/address/0x5770887cE7A8f0620CE11042bb81317Ec302A206
```

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

1. **leader_fn**: collect all reported values, compute median, identify
   outliers (values beyond `outlier_threshold` standard deviations from
   median), return slash list.
2. **validator_fn**: recompute median independently. If leader and
   validator disagree on median or outlier list → disagree → leader
   rotates.

**Outlier detection:**
- Values beyond `outlier_threshold` × σ from median → outlier
- Outlier oracles get `slash_percent` of stake burned
- Oracles below `min_stake` after slashing are deactivated

## Deploy-time tunable parameters

| Parameter | Default | Description |
|---|---|---|
| `min_stake_wei` | 1e18 (1 GEN) | Minimum stake to register |
| `slash_percent` | 10 | Fraction of stake burned on outlier |
| `outlier_threshold` | 2.0 | Std devs for outlier detection |

## API

### Write methods

**`register()`** — `@gl.public.write.payable`
Stake GEN to become an oracle. Idempotent — adds to existing stake.

**`post_request(request_id, query, sources)`** — `@gl.public.write`
Requester posts a data request. Requires at least one source.

**`report(request_id, value, source)`** — `@gl.public.write`
Oracle reports a value for a request. Requires registered active oracle.

**`resolve(request_id)`** — `@gl.public.write`
Run AI consensus to determine the truthful value. Requires ≥3 reports.
Returns median, std_dev, outliers, values.

### View methods

**`get_oracle(oracle)`** → JSON
Get an oracle's record: stake, reports, slashes, active status.

**`get_request(request_id)`** → JSON
Get a request's details: requester, query, status, result, reports count.

**`get_report(request_id, oracle)`** → JSON
Get a specific oracle's report for a request.

**`now()`** → string
Current transaction timestamp in Unix seconds.

## Testing

```bash
export PYTHONPATH="$HOME/.local/lib/python3.14/site-packages/genlayer_py/client:$PYTHONPATH"
python3.14 -m pytest tests/direct/ -v
```

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
│   └── oracle_network.py      # The intelligent contract (280 lines)
├── tests/
│   └── direct/
│       ├── conftest.py        # Shared test helpers
│       ├── test_register.py   # Oracle registration + stake tests
│       ├── test_jobs.py       # Request lifecycle + reporting tests
│       └── test_verify.py     # Consensus + slashing tests
└── README.md
```

## License

MIT
