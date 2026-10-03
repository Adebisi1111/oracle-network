"""Voluntary exit: an oracle must be able to leave and take its stake with it.

The steward asked for "a safe, replay-resistant custody lifecycle for withdrawing
remaining stake". Before `deactivate()` existed the minimum-stake floor applied
unconditionally and the only way to become inactive was to be slashed, so an
oracle that simply wanted to stop could never withdraw below min_stake and a
slashed oracle's stake was stranded above the floor forever.
"""

import json

from tests.direct.conftest import to_hex

ONE_GEN = 1000000000000000000


def _expect_error(fn, *needles):
    try:
        fn()
    except Exception as e:
        msg = str(e).lower()
        for n in needles:
            assert n.lower() in msg, f"expected {n!r} in {msg!r}"
        return msg
    raise AssertionError("expected the call to be rejected, but it succeeded")


def _register(contract, direct_vm, oracle, stake=ONE_GEN):
    direct_vm.sender = oracle
    direct_vm.value = stake
    contract.register()


def test_active_oracle_cannot_withdraw_below_minimum(direct_vm, direct_deploy, direct_alice):
    """The floor still holds while active - that is the security property."""
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    _expect_error(
        lambda: contract.request_withdraw(3 * ONE_GEN),
        "below the minimum stake while active",
    )


def test_active_oracle_can_withdraw_down_to_minimum(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    got = contract.request_withdraw(2 * ONE_GEN)
    assert got > 0
    contract.claim_withdraw(got)
    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["staked"] == ONE_GEN, rec


def test_deactivated_oracle_can_withdraw_everything(direct_vm, direct_deploy, direct_alice):
    """The gap this closes: after deactivating, the floor no longer applies."""
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)

    direct_vm.sender = direct_alice
    contract.deactivate()

    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["active"] is False, rec

    nonce = contract.request_withdraw(3 * ONE_GEN)
    got = contract.claim_withdraw(nonce)
    assert got == 3 * ONE_GEN

    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["staked"] == 0, rec


def test_deactivation_is_one_way(direct_vm, direct_deploy, direct_alice):
    """No re-activation: a departed oracle cannot return to the set."""
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)
    direct_vm.sender = direct_alice
    contract.deactivate()
    _expect_error(lambda: contract.deactivate(), "already deactivated")
    # and there is no activate() to undo it
    assert not hasattr(contract, "activate"), "re-activation path must not exist"


def test_deactivated_oracle_cannot_report(direct_vm, direct_deploy,
                                          direct_alice, direct_bob):
    """A departed oracle must not be able to rejoin consensus by reporting."""
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)
    _register(contract, direct_vm, direct_bob, stake=2 * ONE_GEN)

    direct_vm.sender = direct_alice
    contract.post_request(request_id="rid", query="value?", sources=["coingecko"])
    direct_vm.sender = direct_alice
    contract.deactivate()

    direct_vm.sender = direct_alice
    _expect_error(lambda: contract.report("rid", 3500, "coingecko"), "slashed")


def test_deactivate_requires_settling_pending_withdrawal(direct_vm, direct_deploy, direct_alice):
    """Cannot exit while a reservation is outstanding, or that stake would be
    reserved against an oracle that has left."""
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=3 * ONE_GEN)
    nonce = contract.request_withdraw(ONE_GEN)
    _expect_error(lambda: contract.deactivate(), "settle the pending withdrawal")
    contract.claim_withdraw(nonce)
    direct_vm.sender = direct_alice
    contract.deactivate()  # now allowed


def test_deactivate_requires_registration(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    direct_vm.sender = direct_alice
    _expect_error(lambda: contract.deactivate(), "not registered")


def test_slashed_oracle_stake_is_not_stranded(direct_vm, direct_deploy, direct_alice):
    """A slashed oracle is inactive, so it can still recover what is left.

    Before the fix the floor applied to inactive oracles too, so slashed stake
    below the floor was permanently locked.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    # Stake exactly at the minimum so a 10% slash drops below it, which is what
    # deactivates an oracle. At 3 GEN the slash leaves 2.7 GEN, still above the
    # floor, so the oracle would correctly remain active.
    _register(contract, direct_vm, direct_alice, stake=ONE_GEN)

    contract._slash_oracle(to_hex(direct_alice))
    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["slashed_count"] == 1, rec
    assert rec["active"] is False, rec

    nonce = contract.request_withdraw(rec["staked"])
    got = contract.claim_withdraw(nonce)
    assert got == rec["staked"]
    rec = json.loads(contract.get_oracle(to_hex(direct_alice)))
    assert rec["staked"] == 0, rec


def test_deactivated_oracle_cannot_withdraw_twice_over(direct_vm, direct_deploy, direct_alice):
    """Replay resistance still applies on the exit path."""
    contract = direct_deploy("contracts/oracle_network.py")
    _register(contract, direct_vm, direct_alice, stake=2 * ONE_GEN)
    direct_vm.sender = direct_alice
    contract.deactivate()

    nonce = contract.request_withdraw(2 * ONE_GEN)
    contract.claim_withdraw(nonce)
    _expect_error(lambda: contract.claim_withdraw(nonce), "no pending withdrawal")