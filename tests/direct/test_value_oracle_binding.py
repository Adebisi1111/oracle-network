"""The steward asked for this specifically:

    "retain each verified value with its originating oracle so fetch failures
     cannot shift the slash target"

The pre-fix contract built two parallel lists of DIFFERENT length and paired
them with zip():

    all_reports      every report posted for the request
    verified_values  only the reports that survived fetch + AI verification

When one report dropped out, every later pairing slid by one, so an honest
oracle inherited another oracle's value and could be slashed for it.

These tests drive the REAL contract so the binding is actually exercised: a
report is dropped inside the same request that contains a genuine outlier, and
the assertion is that the slashed oracle is the one whose OWN verified value is
the outlier.
"""

import json

from tests.direct.conftest import to_hex

ONE_GEN = 1000000000000000000
GOOD = "https://example.com/good"
ALSO_GOOD = "https://example.com/also-good"
DEAD = "https://example.com/unreachable"


def _setup(contract, direct_vm, oracles):
    for o in oracles:
        direct_vm.sender = o
        direct_vm.value = ONE_GEN
        contract.register()


def _mock(direct_vm, url, body):
    direct_vm.mock_web(url, {"body": body})


def test_dropped_report_does_not_shift_slash_target(direct_vm, direct_deploy, direct_accounts):
    """One report fails to fetch inside a request that also has a real outlier.

    Four oracles report on the same request:
      * two agree (verified value 100)
      * one genuinely disagrees (verified value 500)  -> should be slashed
      * one reports from an unfetchable source       -> dropped from consensus

    With the old zip() pairing the dropped report shifted the list and the
    honest oracle inherited the outlier's value, so the wrong oracle was
    slashed. The correct oracle is asserted by name.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    agree_a, agree_b, liar, dropped = direct_accounts[:4]
    _setup(contract, direct_vm, [agree_a, agree_b, liar, dropped])

    direct_vm.sender = agree_a
    contract.post_request(
        request_id="rid",
        query="value?",
        sources=[GOOD, ALSO_GOOD, DEAD],
    )

    # Dropped oracle goes FIRST in iteration order so the old zip() pairing
    # would slide every subsequent report by one.
    direct_vm.sender = dropped
    contract.report(request_id="rid", value=777777, source_url=DEAD)

    direct_vm.sender = agree_a
    contract.report(request_id="rid", value=100, source_url=GOOD)
    direct_vm.sender = agree_b
    contract.report(request_id="rid", value=100, source_url=ALSO_GOOD)
    direct_vm.sender = liar
    contract.report(request_id="rid", value=500, source_url=GOOD)

    _mock(direct_vm, GOOD, "The verified figure is 100.")
    _mock(direct_vm, ALSO_GOOD, "The verified figure is 100.")
    # DEAD is never mocked: web.render fails, so this report is excluded.
    # The verification prompt embeds "Oracle reported value: N", so the LLM can
    # be keyed per reported figure. Mocks resolve first-match-wins, so the
    # outlier's value must be registered before the catch-all.
    direct_vm.mock_llm("reported value: 500", json.dumps({"verified_value": 500, "supported": True}))
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 100, "supported": True}))

    result = json.loads(contract.resolve("rid"))

    verified_oracles = {e["oracle"] for e in result["verified"]}
    assert to_hex(dropped) not in verified_oracles, (
        "the unfetchable report must not appear in the verified set"
    )
    assert len(result["verified"]) == 3

    # Exactly one outlier, and it must be the oracle whose OWN value is 500.
    assert len(result["outliers"]) == 1, result["outliers"]
    assert result["outliers"][0] == to_hex(liar), (
        f"wrong oracle slashed: {result['outliers']} (expected {to_hex(liar)})"
    )

    # The two honest oracles must survive untouched.
    assert to_hex(agree_a) not in result["outliers"]
    assert to_hex(agree_b) not in result["outliers"]


def test_dropped_report_never_becomes_the_slash_target(direct_vm, direct_deploy, direct_accounts):
    """Whichever oracle drops out, it must never appear in the outlier list.

    The dropped oracle's own claimed number must have no influence: slashing is
    decided only from verified records, so a report that never verified cannot
    be judged at all.
    """
    contract = direct_deploy("contracts/oracle_network.py")
    agree_a, agree_b, liar, dropped = direct_accounts[:4]
    _setup(contract, direct_vm, [agree_a, agree_b, liar, dropped])

    direct_vm.sender = agree_a
    contract.post_request(request_id="rid", query="value?", sources=[GOOD, DEAD])

    direct_vm.sender = agree_a
    contract.report(request_id="rid", value=100, source_url=GOOD)
    direct_vm.sender = agree_b
    contract.report(request_id="rid", value=100, source_url=GOOD)
    direct_vm.sender = liar
    contract.report(request_id="rid", value=500, source_url=GOOD)
    direct_vm.sender = dropped
    contract.report(request_id="rid", value=999999, source_url=DEAD)

    _mock(direct_vm, GOOD, "The verified figure is 100.")
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 100, "supported": True}))

    result = json.loads(contract.resolve("rid"))
    assert to_hex(dropped) not in result["outliers"]
    assert to_hex(dropped) not in {e["oracle"] for e in result["verified"]}


def test_every_verified_entry_names_its_own_oracle(direct_vm, direct_deploy, direct_accounts):
    """No positional pairing anywhere: each record states who reported it."""
    contract = direct_deploy("contracts/oracle_network.py")
    oracles = direct_accounts[:3]
    _setup(contract, direct_vm, oracles)

    direct_vm.sender = oracles[0]
    contract.post_request(request_id="rid", query="value?", sources=[GOOD])

    for o in oracles:
        direct_vm.sender = o
        contract.report(request_id="rid", value=3500, source_url=GOOD)

    _mock(direct_vm, GOOD, "ETH price: $3500")
    direct_vm.mock_llm(".*", json.dumps({"verified_value": 3500, "supported": True}))

    result = json.loads(contract.resolve("rid"))
    assert {e["oracle"] for e in result["verified"]} == {to_hex(o) for o in oracles}
    for entry in result["verified"]:
        assert set(entry) == {"oracle", "value", "reported"}
        assert isinstance(entry["oracle"], str) and entry["oracle"]