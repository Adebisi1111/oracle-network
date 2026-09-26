"""Tests for OracleNetwork consensus and slashing."""

import json
from tests.direct.conftest import to_hex


def _register(contract, direct_vm, alice, stake):
    direct_vm.sender = alice
    direct_vm.value = stake
    contract.register()


def test_resolve_computes_median(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)
    bob = to_hex(direct_bob)
    charlie = to_hex(direct_charlie)

    # Register 3 oracles
    for oracle, stake in [(direct_alice, 1000000000000000000), (direct_bob, 1000000000000000000), (direct_charlie, 1000000000000000000)]:
        direct_vm.sender = oracle
        direct_vm.value = stake
        contract.register()

    # Post request
    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["https://coingecko.com"],
    )

    # Report values (integers)
    direct_vm.sender = direct_alice
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_bob
    contract.report("req1", 3501, "coingecko")
    direct_vm.sender = direct_charlie
    contract.report("req1", 3499, "coingecko")

    # Resolve
    result = contract.resolve("req1")
    data = json.loads(result)
    assert data["median"] == 3500.0
    assert data["outliers"] == []

    # Check request
    req = contract.get_request("req1")
    req_data = json.loads(req)
    assert req_data["status"] == "RESOLVED"
    assert req_data["result"] == 3500


def test_resolve_slashes_outliers(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)
    bob = to_hex(direct_bob)
    charlie = to_hex(direct_charlie)

    # Register 3 oracles
    for oracle, stake in [(direct_alice, 1000000000000000000), (direct_bob, 1000000000000000000), (direct_charlie, 1000000000000000000)]:
        direct_vm.sender = oracle
        direct_vm.value = stake
        contract.register()

    # Post request
    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["https://coingecko.com"],
    )

    # Report values - one outlier (10000 vs ~3500)
    direct_vm.sender = direct_alice
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_bob
    contract.report("req1", 3501, "coingecko")
    direct_vm.sender = direct_charlie
    contract.report("req1", 10000, "fake-source")  # outlier

    # Resolve
    result = contract.resolve("req1")
    data = json.loads(result)

    # Charlie should be slashed
    charlie_rec = contract.get_oracle(charlie)
    charlie_data = json.loads(charlie_rec)
    assert charlie_data["active"] is False  # slashed below min stake
    assert charlie_data["slashed_count"] >= 1


def test_resolve_rejects_fewer_than_3_reports(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 1000000000000000000
    contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["https://coingecko.com"],
    )

    contract.report("req1", 3500, "coingecko")
    contract.report("req1", 3501, "coingecko")

    try:
        contract.resolve("req1")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "3 reports" in msg.lower()


def test_get_report(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 1000000000000000000
    contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["https://coingecko.com"],
    )

    contract.report("req1", 3500, "coingecko")

    report = contract.get_report("req1", alice)
    data = json.loads(report)
    assert data["exists"] is True
    assert data["value"] == 3500
    assert data["source"] == "coingecko"


def test_unregistered_oracle_cannot_report(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["https://coingecko.com"],
    )

    try:
        contract.report("req1", 3500, "coingecko")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "oracle" in msg.lower() and "registered" in msg.lower()


def test_resolve_rejects_already_resolved(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)
    bob = to_hex(direct_bob)
    charlie = to_hex(direct_charlie)

    for oracle, stake in [(direct_alice, 1000000000000000000), (direct_bob, 1000000000000000000), (direct_charlie, 1000000000000000000)]:
        direct_vm.sender = oracle
        direct_vm.value = stake
        contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["https://coingecko.com"],
    )

    direct_vm.sender = direct_alice
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_bob
    contract.report("req1", 3501, "coingecko")
    direct_vm.sender = direct_charlie
    contract.report("req1", 3499, "coingecko")

    contract.resolve("req1")

    try:
        contract.resolve("req1")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already resolved" in msg.lower()