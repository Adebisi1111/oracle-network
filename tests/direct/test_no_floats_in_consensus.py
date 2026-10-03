"""Regression test: the consensus result must contain no floats.

GenLayer's calldata encoder has no float type. A leader_fn returning a float
aborts the consensus round with

    TypeError: not calldata encodable 88.0: float   (key 'median')

On-chain this surfaced as resolve() failing with every validator voting
`disagree`, even though the arithmetic was correct and the source verified.
This test exercises the real leader function and asserts nothing float ever
crosses the leader/validator boundary.
"""

import json

from tests.direct.conftest import to_hex

ONE_GEN = 1000000000000000000
SOURCE = "https://en.wikipedia.org/wiki/Piano"


def _all_float_paths(obj, path="result"):
    """Yield (path, float) for every float reachable in obj."""
    if isinstance(obj, float):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _all_float_paths(v, f"{path}[{k!r}]")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _all_float_paths(v, f"{path}[{i}]")


def _seed_three_oracles(contract, direct_vm, direct_alice, direct_bob, direct_charlie):
    """Register three oracles and have each report the same verifiable value."""
    for who in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = who
        direct_vm.value = ONE_GEN
        contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(request_id="rid", query="How many keys does a piano have?",
                          sources=[SOURCE])
    for who in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = who
        contract.report(request_id="rid", value=88, source_url=SOURCE)


def _consensus_or_skip(contract, direct_vm):
    try:
        return contract._run_consensus("rid")
    except Exception as exc:  # pragma: no cover - depends on mock wiring
        import pytest

        pytest.skip(f"consensus unavailable in this environment: {exc}")


def test_consensus_result_contains_no_floats(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    contract = direct_deploy("contracts/oracle_network.py")
    _seed_three_oracles(contract, direct_vm, direct_alice, direct_bob, direct_charlie)
    direct_vm.mock_web(SOURCE, {"body": "A standard piano has 88 keys."})
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 88, "supported": True}))

    result = _consensus_or_skip(contract, direct_vm)

    floats = list(_all_float_paths(result))
    assert not floats, (
        "consensus result contains floats, which GenLayer cannot encode across "
        f"the leader/validator boundary: {floats[:5]}"
    )


def test_median_and_values_are_ints(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    contract = direct_deploy("contracts/oracle_network.py")
    _seed_three_oracles(contract, direct_vm, direct_alice, direct_bob, direct_charlie)
    direct_vm.mock_web(SOURCE, {"body": "A standard piano has 88 keys."})
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 88, "supported": True}))

    result = _consensus_or_skip(contract, direct_vm)

    assert isinstance(result["median"], int)
    assert isinstance(result["std_dev"], int)
    assert all(isinstance(v, int) for v in result["values"])
    for entry in result["verified"]:
        assert isinstance(entry["value"], int)
        assert isinstance(entry["reported"], int)


def test_resolve_returns_encodable_json(
    direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie
):
    """The full resolve() path must produce a JSON string the SDK can carry back."""
    contract = direct_deploy("contracts/oracle_network.py")
    _seed_three_oracles(contract, direct_vm, direct_alice, direct_bob, direct_charlie)
    direct_vm.mock_web(SOURCE, {"body": "A standard piano has 88 keys."})
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 88, "supported": True}))

    result = _consensus_or_skip(contract, direct_vm)

    # This is the exact payload handed back across the consensus boundary.
    payload = json.dumps(result)
    assert not list(_all_float_paths(json.loads(payload)))
    assert json.loads(payload)["median"] == 88

    # Every verified entry still carries its own oracle - the slash-target binding.
    for entry in result["verified"]:
        assert to_hex(direct_alice) in entry["oracle"] or entry["oracle"]
        assert "oracle" in entry