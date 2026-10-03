"""The steward asked for:

    "reject unsupported, missing, nonnumeric, or INVALID verified values
     instead of falling back to the caller's claim"

`test_invariants.py` already covers unsupported / missing / nonnumeric. This
file covers the remaining "or invalid" branches in the contract: booleans
masquerading as numbers, NaN, positive/negative infinity, and a verified value
of the wrong container type (list / dict / string).

Every case must disqualify the report entirely. None of them may reach the
median, and none may be replaced by the number the oracle claimed.
"""

import json

import pytest

from tests.direct.conftest import to_hex

ONE_GEN = 1000000000000000000
SRC = "https://example.com/feed"


def _three_reported(direct_vm, contract, senders, value=999999):
    """Three DISTINCT reporters so the distinct-report gate passes and the run
    actually reaches verification. They all claim the same absurd number, so if
    the contract silently fell back to the caller's claim the median would be
    999999 instead of the run failing."""
    for o in senders:
        direct_vm.sender = o
        contract.report("rid", value, SRC)


def _prepare(direct_vm, contract, senders):
    for o in senders:
        direct_vm.sender = o
        direct_vm.value = ONE_GEN
        contract.register()
    direct_vm.sender = senders[0]
    contract.post_request(request_id="rid", query="value?", sources=[SRC])
    direct_vm.mock_web(SRC, {"body": "The verified figure is 100."})


def _expect_no_fallback(direct_vm, contract, senders, verify_payload):
    """The run must fail verification; the callers' 999999 must never be used."""
    _prepare(direct_vm, contract, senders)
    _three_reported(direct_vm, contract, senders)
    direct_vm.mock_llm(".*", json.dumps(verify_payload))
    try:
        result = contract.resolve("rid")
    except Exception as exc:
        msg = str(exc).lower()
        assert "no reports could be verified" in msg, msg
        return
    raise AssertionError(
        f"resolve() accepted an invalid verified value and returned {result}"
    )


def test_boolean_verified_value_is_excluded(direct_vm, direct_deploy,
                                            direct_alice, direct_bob, direct_charlie):
    """True/False are ints in Python. A boolean must not become the median."""
    _expect_no_fallback(direct_vm, direct_deploy("contracts/oracle_network.py"),
                        [direct_alice, direct_bob, direct_charlie],
                        {"verified_value": True, "supported": True})


def test_nan_verified_value_is_excluded(direct_vm, direct_deploy,
                                        direct_alice, direct_bob, direct_charlie):
    """NaN != NaN, so it can poison mean/std_dev if it slips through."""
    # json.dumps emits bare NaN, which json.loads accepts - that is exactly the
    # malformed payload a validator could return.
    contract = direct_deploy("contracts/oracle_network.py")
    senders = [direct_alice, direct_bob, direct_charlie]
    _prepare(direct_vm, contract, senders)
    _three_reported(direct_vm, contract, senders)
    direct_vm.mock_llm(".*", '{"verified_value": NaN, "supported": true}')
    try:
        result = contract.resolve("rid")
    except Exception as exc:
        assert "no reports could be verified" in str(exc).lower(), str(exc)
        return
    raise AssertionError(f"NaN was accepted; resolve returned {result}")


def test_infinite_verified_value_is_excluded(direct_vm, direct_deploy,
                                             direct_alice, direct_bob, direct_charlie):
    """+/-Infinity would make std_dev infinite and disable outlier detection."""
    contract = direct_deploy("contracts/oracle_network.py")
    senders = [direct_alice, direct_bob, direct_charlie]
    _prepare(direct_vm, contract, senders)
    _three_reported(direct_vm, contract, senders)
    direct_vm.mock_llm(".*", '{"verified_value": Infinity, "supported": true}')
    try:
        result = contract.resolve("rid")
    except Exception as exc:
        assert "no reports could be verified" in str(exc).lower(), str(exc)
        return
    raise AssertionError(f"Infinity was accepted; resolve returned {result}")


def test_list_verified_value_is_excluded(direct_vm, direct_deploy,
                                          direct_alice, direct_bob, direct_charlie):
    """A list is not a measurement."""
    _expect_no_fallback(direct_vm, direct_deploy("contracts/oracle_network.py"),
                        [direct_alice, direct_bob, direct_charlie],
                        {"verified_value": [1, 2, 3], "supported": True})


def test_dict_verified_value_is_excluded(direct_vm, direct_deploy,
                                          direct_alice, direct_bob, direct_charlie):
    _expect_no_fallback(direct_vm, direct_deploy("contracts/oracle_network.py"),
                        [direct_alice, direct_bob, direct_charlie],
                        {"verified_value": {"price": 3500}, "supported": True})


def test_string_verified_value_is_excluded(direct_vm, direct_deploy,
                                            direct_alice, direct_bob, direct_charlie):
    _expect_no_fallback(direct_vm, direct_deploy("contracts/oracle_network.py"),
                        [direct_alice, direct_bob, direct_charlie],
                        {"verified_value": "3500", "supported": True})


def test_null_verified_value_is_excluded(direct_vm, direct_deploy,
                                         direct_alice, direct_bob, direct_charlie):
    """supported=True but no value at all is still no value."""
    _expect_no_fallback(direct_vm, direct_deploy("contracts/oracle_network.py"),
                        [direct_alice, direct_bob, direct_charlie],
                        {"verified_value": None, "supported": True})


@pytest.mark.parametrize("flag", [1, "true", "yes", [True], {"ok": True}])
def test_supported_flag_must_be_exactly_true(direct_vm, direct_deploy,
                                             direct_alice, direct_bob, direct_charlie,
                                             flag):
    """A truthy-but-not-true flag must not count as supported.

    Parameterised rather than looped: gltest allows one contract instance per
    test, so each flag needs its own deployment.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    senders = [direct_alice, direct_bob, direct_charlie]
    _prepare(direct_vm, contract, senders)
    _three_reported(direct_vm, contract, senders)
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 999999, "supported": flag}))
    try:
        result = contract.resolve("rid")
    except Exception as exc:
        assert "no reports could be verified" in str(exc).lower(), str(exc)
        return
    raise AssertionError(f"supported={flag!r} was accepted; got {result}")


def test_valid_value_is_still_accepted(direct_vm, direct_deploy,
                                       direct_alice, direct_bob, direct_charlie):
    """Guard the guard: a legitimate verification must still resolve.

    Without this, an over-broad rejection could make the contract permanently
    unusable while every rejection test above still passed.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    senders = [direct_alice, direct_bob, direct_charlie]
    _prepare(direct_vm, contract, senders)
    _three_reported(direct_vm, contract, senders, value=100)
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 100, "supported": True}))

    result = json.loads(contract.resolve("rid"))
    assert result["median"] == 100
    assert {e["oracle"] for e in result["verified"]} == {to_hex(o) for o in senders}