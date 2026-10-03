"""Tests for OracleNetwork request lifecycle and reporting."""

import json
from tests.direct.conftest import to_hex


def test_post_request_stores_data(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    contract.post_request(
        request_id="req1",
        query="What is the price of ETH?",
        sources=["coingecko", "coinmarketcap"],
    )

    req = contract.get_request("req1")
    data = json.loads(req)
    assert data["exists"] is True
    assert data["requester"] == alice
    assert data["query"] == "What is the price of ETH?"
    assert data["status"] == "PENDING"
    assert data["reports_count"] == 0


def test_post_request_rejects_duplicate(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["coingecko"],
    )

    try:
        contract.post_request(
            request_id="req1",
            query="ETH price",
            sources=["coingecko"],
        )
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already exists" in msg.lower()


def test_post_request_rejects_empty_sources(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = alice
    try:
        contract.post_request(
            request_id="req1",
            query="ETH price",
            sources=[],
        )
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "source" in msg.lower()


def test_report_by_registered_oracle(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 1000000000000000000
    contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["coingecko"],
    )

    direct_vm.mock_web("coingecko", {"body": "ETH price data: $3500 per ETH"})
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

    req = contract.get_request("req1")
    req_data = json.loads(req)
    assert req_data["reports_count"] == 1


def test_report_rejects_unregistered(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["coingecko"],
    )

    try:
        contract.report("req1", 3500, "coingecko")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "oracle" in msg.lower() and "registered" in msg.lower()


def test_report_rejects_resolved_request(direct_vm, direct_deploy, direct_alice, direct_bob, direct_charlie):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    # Use 10 GEN stake so oracle survives 10% slash
    for oracle in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = oracle
        direct_vm.value = 10000000000000000000
        contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["coingecko"],
    )

    direct_vm.mock_web("coingecko", {"body": "ETH price data: $3500 per ETH"})
    direct_vm.mock_llm(
        ".*Does the source content support the reported value.*",
        json.dumps({"verified_value": 3500, "supported": True}),
    )

    # Three DISTINCT oracles: one address may no longer reach the threshold by
    # calling report() three times.
    for oracle in (direct_alice, direct_bob, direct_charlie):
        direct_vm.sender = oracle
        contract.report("req1", 3500, "coingecko")
    contract.resolve("req1")

    try:
        direct_vm.sender = direct_alice
        contract.report("req1", 3600, "coingecko")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already resolved" in msg.lower()


def test_resolve_requires_min_reports(direct_vm, direct_deploy, direct_alice, direct_bob):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    for oracle in (direct_alice, direct_bob):
        direct_vm.sender = oracle
        direct_vm.value = 1000000000000000000
        contract.register()

    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["coingecko"],
    )

    # Two DISTINCT oracles: still below the threshold of 3. A second report from
    # alice would now be rejected outright rather than inflating the count.
    contract.report("req1", 3500, "coingecko")
    direct_vm.sender = direct_bob
    contract.report("req1", 3501, "coingecko")

    req = json.loads(contract.get_request("req1"))
    assert req["reports_count"] == 2

    try:
        contract.resolve("req1")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "distinct reports" in msg.lower()
