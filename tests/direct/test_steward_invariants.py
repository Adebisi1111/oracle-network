"""Audit of the seven invariants the steward asked OracleNetwork to enforce.

Each check maps to one steward requirement and is written against the CURRENT
contract behaviour, so a failure here is a real gap rather than a hypothetical.
"""

import json

# ---------------------------------------------------------------------------
# 1. Minimum stake before activation
#    Contract: register() raises only when value == 0, and never compares
#    against self.min_stake. A 1-wei stake activates the oracle.
# ---------------------------------------------------------------------------
MIN_STAKE_WEI = 1000000000000000000


def check_min_stake(current_register_raises_on):
    """current_register_raises_on(value) -> bool, as the contract behaves today."""
    tiny = current_register_raises_on(1)
    ok = tiny  # 1 wei must be rejected
    print(f"  stake of 1 wei rejected? {tiny}  (expected True)")
    print(f"  stake just under 1 GEN rejected? {current_register_raises_on(MIN_STAKE_WEI - 1)}  (expected True)")
    return ok


# ---------------------------------------------------------------------------
# 2. Unique oracle reports / no overwrite inflation
#    Contract: report() overwrites reports[key] but does
#    `req.reports_count += 1` unconditionally. One address calling report()
#    three times reaches the resolution threshold on its own.
# ---------------------------------------------------------------------------


def check_unique_reports(contract_report, resolve_threshold=3, calls=3):
    """Model the contract's counter for a single address reporting `calls` times."""
    # Contract behaviour: every accepted call increments reports_count,
    # whether or not it created a NEW report entry.
    reports_count = 0
    store = {}
    for _ in range(calls):
        key = "req1:alice"  # same oracle, same request -> same key
        store[key] = "overwritten"
        reports_count += 1  # contract does this unconditionally

    unique = len(store)
    print(f"  reports_count reported by contract: {reports_count}")
    print(f"  distinct reports actually stored: {unique}")
    reachable = reports_count >= resolve_threshold and unique < resolve_threshold
    print(f"  one address alone reaches threshold? {reachable}  (expected False)")
    return not reachable


# ---------------------------------------------------------------------------
# 3. Source policy
#    Contract: report() takes source_url and stores it without ever checking
#    it against request.sources. An oracle can cite any URL, including one the
#    request never accepted.
# ---------------------------------------------------------------------------


def check_source_policy(accepted_sources, reported_source, contract_validates):
    print(f"  accepted by request: {accepted_sources}")
    print(f"  oracle reported:     {reported_source}")
    print(f"  contract validates?  {contract_validates}  (expected True)")
    inside = reported_source in accepted_sources
    print(f"  source is inside policy? {inside}  (expected True)")
    return contract_validates


# ---------------------------------------------------------------------------
# 4. Verified-value handling
#    Contract: verified_val = verify_res.get("verified_value", float(r.value))
#    -> silently falls back to the CALLER's number when the AI omits the field,
#    and never inspects the "supported" flag at all.
# ---------------------------------------------------------------------------


def check_verified_value(verify_res, oracle_claimed):
    got = verify_res.get("verified_value", float(oracle_claimed))
    fell_back = "verified_value" not in verify_res
    print(f"  AI response: {verify_res}")
    print(f"  oracle claimed: {oracle_claimed}")
    print(f"  value used:    {got}")
    print(f"  fell back to caller's claim? {fell_back}  (expected False)")
    print(f"  'supported' flag inspected?     {'supported' not in verify_res}  (expected False)")
    return not fell_back


# ---------------------------------------------------------------------------
# 5. Verified value stays bound to its oracle
#    Contract: zip(all_reports, verified_values) over lists of DIFFERENT length.
# ---------------------------------------------------------------------------


def check_value_oracle_binding():
    all_reports = ["alice", "mallory", "dave"]
    verified_values = [100.0, 500.0]  # mallory dropped out during verification
    pairs = list(zip(all_reports, verified_values))
    print(f"  reports:   {all_reports}")
    print(f"  verified:  {verified_values}")
    for oracle, value in pairs:
        print(f"    {oracle:8} paired with {value}")
    bound = dict(pairs)
    correct = {"alice": 100.0, "dave": 500.0}
    ok = bound == correct
    print(f"  pairing correct? {ok}  (expected True)")
    return ok


# ---------------------------------------------------------------------------
# 6. Custody lifecycle
# ---------------------------------------------------------------------------


def check_custody(public_methods):
    has_withdraw = any("withdraw" in m for m in public_methods)
    print(f"  public methods: {public_methods}")
    print(f"  withdraw exists? {has_withdraw}  (expected True)")
    return has_withdraw


if __name__ == "__main__":
    print("=" * 74)
    print("1. MINIMUM STAKE BEFORE ACTIVATION")
    r1 = check_min_stake(lambda v: v == 0)  # contract: only rejects zero

    print("\n2. UNIQUE REPORTS / NO OVERWRITE INFLATION")
    r2 = check_unique_reports(None)

    print("\n3. SOURCE POLICY")
    r3 = check_source_policy(["https://coingecko.com"], "https://attacker.example/x", False)

    print("\n4. VERIFIED VALUE HANDLING")
    r4 = check_verified_value({"supported": False}, 999999)  # AI omitted the value

    print("\n5. VALUE STAYS BOUND TO ITS ORACLE")
    r5 = check_value_oracle_binding()

    print("\n6. CUSTODY LIFECYCLE")
    r6 = check_custody(["register", "post_request", "report", "resolve",
                        "get_oracle", "get_request", "get_report", "now"])

    print("\n" + "=" * 74)
    results = [("min stake", r1), ("unique reports", r2), ("source policy", r3),
               ("verified value", r4), ("value/oracle binding", r5), ("custody", r6)]
    passed = [n for n, v in results if v]
    failed = [n for n, v in results if not v]
    print(f"already satisfied: {passed or 'none'}")
    print(f"GAPS: {failed}")