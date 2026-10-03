"""Tests for the seven invariants the steward required.

Each test fails against the pre-fix contract and passes now. They assert on
rejections and on accounting, not merely on absence of exceptions.
"""

import json

from tests.direct.conftest import to_hex

ONE_GEN = 1000000000000000000


def _register(contract, direct_vm, oracle, stake=ONE_GEN):
    direct_vm.sender = oracle
    direct_vm.value = stake
    contract.register()


def _open_request(contract, direct_vm, requester, rid="req1",
                  sources=None):
    direct_vm.sender = requester
    contract.post_request(
        request_id=rid,
        query="ETH price",
        sources=sources or ["coingecko"],
    )


def _mock_source(direct_vm, body="ETH price: $3500", value=3500, supported=True):
    direct_vm.mock_web("coingecko", {"body": body})
    direct_vm.mock_llm(
        ".*",
        json.dumps({"verified_value": value, "supported": supported}),
    )


def _expect_error(fn, *needles):
    try:
        fn()
    except Exception as e:  # contract raises UserError; message is the signal
        msg = str(e).lower()
        for n in needles:
            assert n.lower() in msg, f"expected {n!r} in {msg!r}"
        return msg
    raise AssertionError("expected the call to be rejected, but it succeeded")


# ---------------------------------------------------------------------------
# 1. Minimum stake before activation
# ---------------------------------------------------------------------------


def test_register_rejects_below_minimum_stake(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _expect_error(
        lambda: _register(contract, direct_vm, direct_alice, stake=1),
        "minimum",
    )


def test_register_accepts_exactly_minimum_stake(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=ONE_GEN)
    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["active"] is True
    assert rec["staked"] == ONE_GEN


def test_dust_topup_allowed_once_already_active(direct_vm, direct_deploy, direct_alice):
    """The minimum-stake gate is about ACTIVATION, not every top-up.

    Alice is already active and above the minimum, so a dust top-up violates
    nothing. What must never happen is an oracle being active while below the
    minimum -- that combination is unreachable because register() gates it.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=ONE_GEN)
    _register(contract, direct_vm, direct_alice, stake=1)
    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["staked"] == ONE_GEN + 1
    assert rec["active"] is True


# ---------------------------------------------------------------------------
# 2. Unique reports — one address cannot inflate the count
# ---------------------------------------------------------------------------


def test_duplicate_report_is_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice)
    _open_request(contract, direct_vm, direct_alice)
    contract.report("req1", 3500, "coingecko")
    _expect_error(
        lambda: contract.report("req1", 9999, "coingecko"),
        "already reported",
    )


def test_one_address_cannot_reach_resolution_threshold(direct_vm, direct_deploy, direct_alice):
    """The core inflation bug: three calls from one address used to be three
    reports. Now the second call is rejected and the count stays at one."""
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice)
    _open_request(contract, direct_vm, direct_alice)

    contract.report("req1", 3500, "coingecko")
    for _ in range(5):
        try:
            contract.report("req1", 3500, "coingecko")
        except Exception:
            pass

    req = json.loads(contract.get_request("req1"))
    assert req["reports_count"] == 1, (
        f"expected exactly 1 distinct report, got {req['reports_count']}"
    )
    _expect_error(lambda: contract.resolve("req1"), "distinct reports")


# ---------------------------------------------------------------------------
# 3. Source must be inside the request's accepted evidence policy
# ---------------------------------------------------------------------------


def test_report_source_must_be_accepted(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice)
    _open_request(contract, direct_vm, direct_alice, sources=["coingecko"])
    _expect_error(
        lambda: contract.report("req1", 3500, "attacker.example"),
        "not accepted",
    )


def test_report_source_accepted_works(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice)
    _open_request(contract, direct_vm, direct_alice, sources=["coingecko", "coinbase"])
    contract.report("req1", 3500, "coinbase")
    rep = json.loads(contract.get_report("req1", to_hex(direct_alice)))
    assert rep["source"] == "coinbase"


# ---------------------------------------------------------------------------
# 4. Verified values are validated, never taken from the caller's claim
# ---------------------------------------------------------------------------


def test_unsupported_value_is_excluded_not_fallback(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob, direct_charlie):
    """AI says supported=False -> the report must NOT contribute the oracle's
    claimed number to the median.

    Three distinct reporters are used so the DISTINCT-REPORT gate passes and the
    run actually reaches verification; all three are unsupported, so resolve must
    fail on verification rather than succeed on the callers' numbers.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    for o in (direct_alice, direct_bob, direct_charlie):
        _register(contract, direct_vm, o)
    _open_request(contract, direct_vm, direct_alice)

    direct_vm.mock_web("coingecko", {"body": "ETH price: $3500"})
    direct_vm.mock_llm(".*", json.dumps({"supported": False}))
    for sender in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = sender
        contract.report("req1", 999999, "coingecko")

    _expect_error(lambda: contract.resolve("req1"), "no reports could be verified")


def test_missing_verified_value_is_excluded(direct_vm, direct_deploy,
                                            direct_alice, direct_bob, direct_charlie):
    """No verified_value key -> must not silently use the caller's number."""
    contract = direct_deploy("contracts/oracle_network.py")
    for o in (direct_alice, direct_bob, direct_charlie):
        _register(contract, direct_vm, o)
    _open_request(contract, direct_vm, direct_alice)
    direct_vm.mock_web("coingecko", {"body": "ETH price: $3500"})
    direct_vm.mock_llm(".*", json.dumps({"supported": True}))  # no verified_value
    for sender in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = sender
        contract.report("req1", 999999, "coingecko")
    _expect_error(lambda: contract.resolve("req1"), "no reports could be verified")


def test_nonnumeric_verified_value_is_excluded(direct_vm, direct_deploy,
                                               direct_alice, direct_bob, direct_charlie):
    contract = direct_deploy("contracts/oracle_network.py")
    for o in (direct_alice, direct_bob, direct_charlie):
        _register(contract, direct_vm, o)
    _open_request(contract, direct_vm, direct_alice)
    direct_vm.mock_web("coingecko", {"body": "ETH price: $3500"})
    direct_vm.mock_llm(".*", json.dumps({"verified_value": "three thousand", "supported": True}))
    for sender in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = sender
        contract.report("req1", 999999, "coingecko")
    _expect_error(lambda: contract.resolve("req1"), "no reports could be verified")


# ---------------------------------------------------------------------------
# 5. Verified value stays bound to its originating oracle
# ---------------------------------------------------------------------------


def test_verified_entries_carry_their_oracle(direct_vm, direct_deploy,
                                             direct_alice, direct_bob, direct_charlie):
    """The result must expose oracle->value pairing explicitly rather than a
    bare list zipped positionally."""
    contract = direct_deploy("contracts/oracle_network.py")
    for o in (direct_alice, direct_bob, direct_charlie):
        _register(contract, direct_vm, o)
    _open_request(contract, direct_vm, direct_alice)

    for sender in (direct_alice, direct_bob, direct_charlie):
        _mock_source(direct_vm)
        direct_vm.sender = sender
        contract.report("req1", 3500, "coingecko")

    data = json.loads(contract.resolve("req1"))
    verified = data["verified"]
    assert len(verified) == 3
    for entry in verified:
        assert "oracle" in entry and "value" in entry, entry
    oracles = {e["oracle"] for e in verified}
    assert oracles == {to_hex(direct_alice), to_hex(direct_bob), to_hex(direct_charlie)}


def test_fetch_failure_does_not_shift_slash_target(direct_vm, direct_deploy,
                                                    direct_alice, direct_bob, direct_charlie):
    """A dropped fetch must not cause an honest oracle to be slashed.

    Before the fix, zip(all_reports, verified_values) shifted by one and
    mallory's failed slot handed her value to alice, so alice could be slashed
    for it.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    for o in (direct_alice, direct_bob, direct_charlie):
        _register(contract, direct_vm, o)
    _open_request(contract, direct_vm, direct_alice, sources=["coingecko", "bad"])

    # Three good reports that all agree -> no outliers among them.
    for sender in (direct_alice, direct_bob, direct_charlie):
        _mock_source(direct_vm)
        direct_vm.sender = sender
        contract.report("req1", 3500, "coingecko")

    # Mallory reports with a source that cannot be fetched, so she drops out.
    _register(contract, direct_vm, direct_alice)  # idempotent top-up, still active
    direct_vm.mock_web("bad", {"error": "unreachable"})
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 999999, "supported": True}))
    mallory = to_hex(direct_charlie)
    # Report from a source in policy but unfetchable:
    direct_vm.sender = mallory
    try:
        contract.report("req2", 999999, "bad")
    except Exception:
        pass

    data = json.loads(contract.resolve("req1"))
    slashed = data["outliers"]
    # Nobody reported a disagreeing verified value, so nobody may be slashed.
    assert slashed == [], f"honest oracles were slashed: {slashed}"


# ---------------------------------------------------------------------------
# 6. Custody lifecycle
# ---------------------------------------------------------------------------


def test_withdraw_two_phase_settles_once(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)

    nonce = contract.request_withdraw(ONE_GEN // 2)
    pend = json.loads(contract.get_pending_withdraw(to_hex(direct_alice)))
    assert pend["pending_withdraw"] == ONE_GEN // 2
    assert pend["withdraw_nonce"] == nonce

    got = contract.claim_withdraw(nonce)
    assert got == ONE_GEN // 2
    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["staked"] == 2 * ONE_GEN - ONE_GEN // 2


def test_withdraw_cannot_be_replayed(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)
    nonce = contract.request_withdraw(ONE_GEN // 2)
    contract.claim_withdraw(nonce)
    # Replaying the same nonce must not pay out again.
    _expect_error(lambda: contract.claim_withdraw(nonce), "no pending")


def test_double_reservation_is_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    contract.request_withdraw(ONE_GEN // 2)
    _expect_error(
        lambda: contract.request_withdraw(ONE_GEN // 2), "already pending"
    )


def test_stale_nonce_is_rejected(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    first = contract.request_withdraw(ONE_GEN // 2)
    contract.claim_withdraw(first)
    second = contract.request_withdraw(ONE_GEN // 2)
    _expect_error(lambda: contract.claim_withdraw(first), "nonce")


def test_cannot_withdraw_below_minimum(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)
    _expect_error(
        lambda: contract.request_withdraw(ONE_GEN + 1), "minimum"
    )


def test_slashed_value_must_be_disposed_not_recredited(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """A genuine outlier is slashed, the burned stake is tracked rather than
    silently vanishing, and disposal is explicit and one-shot.

    The LLM mock is keyed on the REPORTED value because the verify prompt
    embeds it ("Oracle reported value: N"), so each oracle gets a different
    verified value. Mocks are first-match-wins, so the patterns must be
    distinct rather than a repeated ".*".
    """
    contract = direct_deploy("contracts/oracle_network.py")
    for o in (direct_alice, direct_bob, direct_charlie):
        _register(contract, direct_vm, o)
    _open_request(contract, direct_vm, direct_alice)

    direct_vm.mock_web("coingecko", {"body": "ETH price data"})
    # Two honest oracles verify at 3500; mallory's claim verifies at 999999.
    direct_vm.mock_llm("reported value: 3500", json.dumps({"verified_value": 3500, "supported": True}))
    direct_vm.mock_llm("reported value: 999999", json.dumps({"verified_value": 999999, "supported": True}))

    direct_vm.sender = direct_alice
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_bob
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_charlie  # mallory
    contract.report("req1", 999999, "coingecko")

    data = json.loads(contract.resolve("req1"))
    assert to_hex(direct_charlie) in data["outliers"], (
        f"mallory should be the outlier, got {data['outliers']}"
    )
    assert to_hex(direct_alice) not in data["outliers"]
    assert to_hex(direct_bob) not in data["outliers"]

    rec = json.loads(contract.get_oracle(to_hex(direct_charlie)))
    assert rec["slashed_count"] >= 1, "mallory should have been slashed"
    assert rec["slashed_pool"] > 0, "slashed stake must be tracked, not vanish"
    # Her stake shrank; the burned part is accounted for in slashed_pool.
    assert rec["staked"] < ONE_GEN
    assert rec["total_slashed"] == rec["slashed_pool"]

    # Disposal is explicit and one-shot.
    got = contract.dispose_slashed()
    assert got == rec["slashed_pool"]
    after = json.loads(contract.get_oracle(to_hex(direct_charlie)))
    assert after["slashed_pool"] == 0
    assert after["staked"] == rec["staked"], "disposal must not credit stake back"
    _expect_error(lambda: contract.dispose_slashed(), "no slashed")