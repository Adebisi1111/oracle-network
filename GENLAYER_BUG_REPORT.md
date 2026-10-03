# Bug report: contract deploys to Studio Net finalize but create no contract

## Summary

Deploying an Intelligent Contract to **Studio Net** (`61999`) produces a
transaction that reaches `FINALIZED`, but no contract is created at the resulting
address and the leader reports `UNKNOWN` as its execution result. Reads against
existing contracts on the same network work normally, so the RPC and network are
up.

## Steps to reproduce

```bash
genlayer network set studionet
genlayer deploy --contract contracts/oracle_network.py \
  --rpc https://studio.genlayer.com/api
```

Account `0x61fd0047595A30A067f1F21F3b28C4AE8A8e3Dc3`, funded with ~16 GEN.

## Observed

```
Deployment Transaction Hash:
0x4bf8c4012dc7187064d014e49ddf6e8c51491e225811f4ddd42bbf21296185e9
Error: Deployment 0x4bf8c401... transaction was decided as FINALIZED;
       leader execution result: UNKNOWN.
```

The transaction is queryable via the explorer:

```json
{
  "status": "FINALIZED",
  "to_address": "0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575",
  "r": null,
  "consensus_data": null
}
```

But that address holds no contract:

```json
{"code": -32001, "message": "Contract 0xb7278A61... not found"}
```

`to_address` was **identical across all five attempts**, despite differing
nonces (858, 860, …).

## Key data point

Deploying a **known-good, previously-successful** revision of the same contract —
the exact source that produced the working contract at
`0x5770887cE7A8f0620CE11042bb81317Ec302A206` — fails identically. This rules
out the contract source as the cause.

```bash
genlayer code 0x5770887cE7A8f0620CE11042bb81317Ec302A206   # succeeds
genlayer schema 0x5770887cE7A8f0620CE11042bb81317Ec302A206 # succeeds
```

## Already ruled out

| Hypothesis | Result |
|---|---|
| Contract source is broken | Disproved — unchanged good revision fails identically |
| Fee estimation | `sim_getFeeConfig` is `Method not found` on this network; worked around with explicit `--fee-value 383706000010352`. Still fails. |
| CLI version | `0.40.0-rc.3` is the newest published. |
| Pinned `py-genlayer` SDK hash | `genvm-lint` reports the pinned SDK "not found"; substituting the hash proven to deploy on another Studio contract failed identically. Reverted. |
| Wallet/balance | Confirmed funded (15.99 GEN); key verified to derive `0x61fd…3Dc3`. |
| Studio web UI | Reachable; requires a browser wallet extension, not testable headlessly. |

## Questions

1. Why does a deploy reach `FINALIZED` with `r: null` and no state committed,
   rather than failing or surfacing the leader's error?
2. Why is `to_address` identical across attempts with different nonces?
3. Is there a known regression in Studio Net deploys? The previously deployed
   contract at `0x5770887c…` remains readable, so state is intact — only new
   deploys are affected.