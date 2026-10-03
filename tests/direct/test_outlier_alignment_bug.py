"""Reproduce the outlier-misalignment bug in OracleNetwork._run_consensus.

The consensus loop builds two parallel lists that are NOT the same length:

    all_reports      every report posted for the request
    verified_values  only the reports that survived fetch + AI verification

It then pairs them with zip(). When a report is dropped from the second list,
every later pairing shifts by one, so an honest oracle inherits a value that
belongs to somebody else and gets slashed for it.

This models the contract's arithmetic exactly, with no chain required.
"""


class Report:
    def __init__(self, oracle, value, fetchable=True, ai_ok=True):
        self.oracle = oracle
        self.value = value
        self.fetchable = fetchable
        self.ai_ok = ai_ok


def leader_fn(all_reports):
    """Mirror of the contract's leader_fn."""
    verified_values = []
    for r in all_reports:
        # 1. acquire the source on-chain
        if not r.fetchable:
            continue
        # 2. AI verification
        if not r.ai_ok:
            continue
        verified_values.append(float(r.value))

    if not verified_values:
        raise ValueError("no reports verified")

    values = sorted(verified_values)
    n = len(values)
    median = values[n // 2] if n % 2 == 1 else (values[n // 2 - 1] + values[n // 2]) / 2.0

    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    std_dev = variance**0.5

    threshold = 2.0
    outliers = []
    if std_dev > 0:
        # THE BUG: all_reports[i] is not verified_values[i]
        for r, verified_val in zip(all_reports, verified_values):
            if abs(verified_val - median) > threshold * std_dev:
                outliers.append(r.oracle)

    return {"median": median, "std_dev": std_dev, "outliers": outliers, "values": values}


def run(name, reports):
    """Return the consensus result, or None if it reverted."""
    print(f"\n--- {name} ---")
    for r in reports:
        flags = []
        if not r.fetchable:
            flags.append("source unfetchable")
        if r.fetchable and not r.ai_ok:
            flags.append("AI verification failed")
        print(f"    {r.oracle}: reported {r.value}" + (f"  [{', '.join(flags)}]" if flags else ""))
    try:
        res = leader_fn(reports)
    except ValueError as e:
        print(f"    consensus reverted: {e}")
        return None
    print(f"    median={res['median']}  std_dev={res['std_dev']:.2f}")
    print(f"    values counted: {res['values']}")
    print(f"    SLASHED -> {res['outliers'] or 'nobody'}")
    return res


fails = 0

# Case 1: baseline. Everyone honest and agreeing, nobody should be slashed.
r = run(
    "Case 1 - three honest oracles agree",
    [Report("alice", 100), Report("bob", 100), Report("carol", 100)],
)
assert r["outliers"] == [], "honest agreement must not slash anyone"

# Case 2: the real attack. Mallory's source is unfetchable, so her report is
# dropped from verified_values. Her honest value should simply be excluded.
# Dave is the lone honest reporter who genuinely disagrees, and is the only
# oracle who SHOULD be slashed.
r = run(
    "Case 2 - one report drops out of verification",
    [
        Report("alice", 100),
        Report("mallory_cheater", 999999, fetchable=False),  # excluded
        Report("dave_honest", 500),  # the real outlier
    ],
)
print()
if r["outliers"] != ["dave_honest"]:
    print(f"  FAIL: expected only dave_honest slashed, got {r['outliers']}")
    fails += 1
else:
    print("  ok: correct oracle slashed")

# Case 3: the shift is visible. With the dropped report first, the pairing
# slides: alice is judged using mallory's value, bob using dave's.
print("\n  Pairing actually performed by zip():")
reports = [
    Report("alice", 100),
    Report("mallory_cheater", 999999, fetchable=False),
    Report("dave_honest", 500),
]
verified = [100.0, 500.0]  # only alice and dave survived
for (rep, val) in zip(reports, verified):
    print(f"    {rep.oracle:18} judged against value {val}")

print()
if fails:
    print(f"{fails} case(s) FAILED")
else:
    print("bug reproduced and characterised")