"""Tests for OracleNetwork oracle registration and staking."""

import json
from tests.direct.conftest import to_hex


def test_register_sets_stake(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 1000000000000000000
    contract.register()

    rec = contract.get_oracle(alice)
    data = json.loads(rec)
    assert data["staked"] == 1000000000000000000
    assert data["active"] is True
    assert data["reports_count"] == 0


def test_register_adds_to_existing_stake(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")
    alice = to_hex(direct_alice)

    direct_vm.sender = direct_alice
    direct_vm.value = 1000000000000000000
    contract.register()

    direct_vm.sender = direct_alice
    direct_vm.value = 500000000000000000
    contract.register()

    rec = contract.get_oracle(alice)
    data = json.loads(rec)
    assert data["staked"] == 1500000000000000000


def test_register_requires_value(direct_vm, direct_deploy, direct_alice):
    contract = direct_deploy("contracts/oracle_network.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 0
    try:
        contract.register()
    except Exception as e:
        msg = str(e)
    else:
        msg = None
    assert msg is not None
    assert "gen" in msg.lower()