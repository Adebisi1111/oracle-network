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

    for oracle, stake in [(direct_alice, 1000000000000000000), (direct_bob, 1000000000000000000), (direct_charlie, 1000000000000000000)]:
        direct_vm.sender = oracle
        direct_vm.value = stake
        contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(request_id="req1", query="ETH price", sources=["coingecko"])

    # 3 oracles, same source, same mock — all get verified_value=3500
    direct_vm.mock_web("coingecko", {"body": "ETH price data: $3500 per ETH"})
    direct_vm.mock_llm(
        ".*ETH price.*",
        json.dumps({"verified_value": 3500, "supported": True}),
    )

    direct_vm.sender = direct_alice
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_bob
    contract.report("req1", 3501, "coingecko")
    direct_vm.sender = direct_charlie
    contract.report("req1", 3499, "coingecko")

    result = contract.resolve("req1")
    data = json.loads(result)
    assert data["median"] == 3500.0, f"Expected median 3500.0, got {data['median']}"
    assert data["outliers"] == [], f"Expected no outliers, got {data['outliers']}"

    req = contract.get_request("req1")
    req_data = json.loads(req)
    assert req_data["status"] == "RESOLVED"
    assert req_data["result"] == 3500


def test_resolve_rejects_fewer_than_3_reports(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    for oracle in (direct_alice, direct_bob):
        direct_vm.sender = oracle
        direct_vm.value = 1000000000000000000
        contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(request_id="req1", query="ETH price", sources=["coingecko"])

    # Two DISTINCT oracles, still below the threshold of three. The same oracle
    # cannot contribute twice to inflate the count.
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_bob
    contract.report("req1", 3501, "coingecko")

    try:
        contract.resolve("req1")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None, f"Expected resolve to fail, but it succeeded"
    assert "distinct reports" in msg.lower(), f"Expected 'distinct reports' in error, got: {msg}"


def test_get_report(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 1000000000000000000
    contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(request_id="req1", query="ETH price", sources=["coingecko"])

    direct_vm.mock_web("coingecko", {"body": "ETH price: $3500"})
    direct_vm.mock_llm(
        ".*Does the source content support the reported value.*",
        json.dumps({"verified_value": 3500, "supported": True}),
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
    contract.post_request(request_id="req1", query="ETH price", sources=["coingecko"])

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
    alice, bob, charlie = to_hex(direct_alice), to_hex(direct_bob), to_hex(direct_charlie)

    for oracle, stake in [(direct_alice, 1000000000000000000), (direct_bob, 1000000000000000000), (direct_charlie, 1000000000000000000)]:
        direct_vm.sender = oracle
        direct_vm.value = stake
        contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(request_id="req1", query="ETH price", sources=["coingecko"])

    for _ in range(3):
        direct_vm.mock_web("coingecko", {"body": "ETH price: $3500"})
        direct_vm.mock_llm(
            ".*Does the source content support the reported value.*",
            json.dumps({"verified_value": 3500, "supported": True}),
        )

    for sender in [direct_alice, direct_bob, direct_charlie]:
        direct_vm.sender = sender
        contract.report("req1", 3500, "coingecko")

    contract.resolve("req1")

    try:
        contract.resolve("req1")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already resolved" in msg.lower()