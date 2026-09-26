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
        sources=["https://coingecko.com", "https://coinmarketcap.com"],
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
        sources=["https://coingecko.com"],
    )

    try:
        contract.post_request(
            request_id="req1",
            query="ETH price",
            sources=["https://coingecko.com"],
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

    # Register oracle
    direct_vm.sender = direct_alice
    direct_vm.value = 1000000000000000000
    contract.register()

    # Post request
    direct_vm.sender = direct_alice
    contract.post_request(
        request_id="req1",
        query="ETH price",
        sources=["https://coingecko.com"],
    )

    # Report value (integer, scaled)
    contract.report("req1", 3500, "coingecko")

    # Check report
    report = contract.get_report("req1", alice)
    data = json.loads(report)
    assert data["exists"] is True
    assert data["value"] == 3500
    assert data["source"] == "coingecko"

    # Check request
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


def test_report_rejects_resolved_request(direct_vm, direct_deploy, direct_alice):
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

    # Post 3 reports from same oracle
    contract.report("req1", 3500, "coingecko")
    contract.report("req1", 3501, "coingecko")
    contract.report("req1", 3499, "coingecko")

    contract.resolve("req1")

    try:
        contract.report("req1", 3600, "coingecko")
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "already resolved" in msg.lower()


def test_resolve_requires_min_reports(direct_vm, direct_deploy, direct_alice):
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