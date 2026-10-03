# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

"""
OracleNetwork — Decentralized Oracle Consensus via GenLayer AI
=============================================================

A standalone GenLayer Intelligent Contract primitive for decentralized
data feeds. Multiple oracles report values for a data request; a single
AI consensus round determines the truthful value and slashes outliers.

PURPOSE
    Oracles stake GEN to participate. Requesters post data requests.
    Oracles use AI to fetch and evaluate data sources, then report a
    value. A single non-deterministic round identifies outliers and
    slashes them, producing a consensus value.

CONSENSUS
    Single gl.vm.run_nondet_unsafe call:
    1. leader_fn: compute median of all reports, identify outliers
       (values beyond 2σ from median), slash outlier oracles.
    2. validator_fn: recompute median independently, agree on which
       oracles to slash. Any mismatch → disagree → leader rotates.

STATE
    oracles    TreeMap[str, OracleRecord]   — stake, reports, slashes
    requests   TreeMap[str, Request]       — data request, status, result
    reports    TreeMap[str, Report]        — individual oracle reports

DEPLOY-TIME PARAMETERS
    min_stake_wei      — minimum stake to register (default 1 GEN)
    slash_percent      — fraction of stake burned on outlier (default 10%)
    outlier_threshold  — standard deviations for outlier detection (default 2.0)
"""

import json
import math
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

from genlayer import *  # noqa: F401, F403


@allow_storage
@dataclass
class OracleRecord:
    staked: u256 = u256(0)
    reports_count: u256 = u256(0)
    slashed_count: u256 = u256(0)
    total_slashed: u256 = u256(0)
    active: bool = True
    # Custody lifecycle. `pending_withdraw` is reserved when a withdrawal is
    # requested and released when it settles, so the same stake cannot be paid
    # out twice. `slashed_pool` accumulates burned stake for disposal.
    pending_withdraw: u256 = u256(0)
    slashed_pool: u256 = u256(0)
    withdraw_nonce: u256 = u256(0)


@allow_storage
@dataclass
class Report:
    oracle: str = ""
    request_id: str = ""
    value: u256 = u256(0)
    source: str = ""
    timestamp: u256 = u256(0)


@allow_storage
@dataclass
class Request:
    requester: str = ""
    query: str = ""
    sources: DynArray[str] = field(default_factory=lambda: DynArray[str]())
    status: str = "PENDING"  # PENDING | RESOLVED | CANCELLED
    result: u256 = u256(0)
    # Number of DISTINCT oracles that have reported. Incremented only when a new
    # report row is created, never on overwrite, so a single address cannot
    # reach the resolution threshold by calling report() repeatedly.
    reports_count: u256 = u256(0)
    resolved_at: u256 = u256(0)
    min_reports: u256 = u256(0)


class OracleNetwork(gl.Contract):
    """Decentralized oracle network with AI-powered consensus."""

    OUTLIER_THRESHOLD: u256 = u256(200)  # scaled by 100 (2.0)

    min_stake: u256 = u256(1000000000000000000)
    slash_percent: u256 = u256(10)
    outlier_threshold: u256 = u256(200)

    oracles: TreeMap[str, OracleRecord]
    requests: TreeMap[str, Request]
    reports: TreeMap[str, Report]

    # Custody accounting. These are storage counters, not transfer calls: this
    # runtime exposes no outbound value primitive on Contract (no gl.pay, and
    # emit_transfer lives on ContractProxy only), so settled value is recorded
    # here and must be disbursed off-contract by the operator.
    settled_withdrawals: u256 = u256(0)
    slashed_sink: u256 = u256(0)

    min_reports_required: u256 = u256(3)

    def __init__(self):
        self.min_stake = u256(1000000000000000000)
        self.slash_percent = u256(10)
        self.outlier_threshold = u256(200)
        self.min_reports_required = u256(3)

    # ------------------------------------------------------------------
    # Oracle registration
    # ------------------------------------------------------------------

    @gl.public.write.payable
    def register(self) -> None:
        """Stake GEN to become an oracle. Idempotent."""
        sender = str(gl.message.sender_address)
        rec = self.oracles.get(sender, None)
        if rec is None:
            rec = OracleRecord()
        value = gl.message.value
        if int(value) == 0:
            raise gl.vm.UserError("Must send at least some GEN to register")

        # INVARIANT: an oracle only becomes ACTIVE once its stake reaches
        # min_stake. Previously any non-zero amount activated it, so a 1-wei
        # stake could report and be counted toward consensus.
        new_stake = int(rec.staked) + int(value)
        if new_stake < int(self.min_stake):
            raise gl.vm.UserError(
                f"Stake below minimum: have {new_stake} wei, need {int(self.min_stake)} wei"
            )

        rec.staked += value
        self.oracles[sender] = rec

    # ------------------------------------------------------------------
    # Request lifecycle
    # ------------------------------------------------------------------

    @gl.public.write
    def post_request(
        self,
        request_id: str,
        query: str,
        sources: DynArray[str],
    ) -> None:
        """Post a data request for oracles to fulfill."""
        sender = str(gl.message.sender_address)
        if not request_id:
            raise gl.vm.UserError("request_id required")
        if self.requests.get(request_id, None) is not None:
            raise gl.vm.UserError("request already exists")
        if len(sources) == 0:
            raise gl.vm.UserError("at least one source required")

        self.requests[request_id] = Request(
            requester=sender,
            query=query,
            sources=sources,
            status="PENDING",
            min_reports=self.min_reports_required,
        )

    @gl.public.write
    def report(self, request_id: str, value: u256, source_url: str) -> None:
        """Oracle reports a value for a request.

        Args:
            request_id: The request ID.
            value: The reported value (integer, scaled as needed).
            source_url: The URL of the data source this value was obtained from.
                       Must be a fetchable URL — the consensus will fetch and verify
                       this source on-chain via gl.nondet.web.render + AI.
        """
        sender = str(gl.message.sender_address)
        oracle = self.oracles.get(sender, None)
        if oracle is None:
            raise gl.vm.UserError("Oracle not registered")
        if not oracle.active:
            raise gl.vm.UserError("Oracle slashed")

        req = self.requests.get(request_id, None)
        if req is None:
            raise gl.vm.UserError("Request not found")
        if req.status != "PENDING":
            raise gl.vm.UserError("Request already resolved")

        # INVARIANT: the report's source must be one the request actually
        # accepted. Without this an oracle can cite any URL it likes and have
        # the committee fetch attacker-controlled evidence.
        if source_url not in req.sources:
            raise gl.vm.UserError(
                f"Source not accepted by this request: {source_url}"
            )

        # INVARIANT: one report per oracle per request. Previously the row was
        # overwritten while reports_count still incremented, so a single address
        # could call report() three times and satisfy the resolution threshold
        # on its own.
        report_key = f"{request_id}:{sender}"
        if report_key in self.reports:
            raise gl.vm.UserError("Oracle already reported on this request")

        self.reports[report_key] = Report(
            oracle=sender,
            request_id=request_id,
            value=value,
            source=source_url,
            timestamp=u256(self._now()),
        )
        # Counted only on first report, so it equals the number of DISTINCT
        # reporting oracles.
        req.reports_count += u256(1)
        oracle.reports_count += u256(1)
        self.requests[request_id] = req
        self.oracles[sender] = oracle

    @gl.public.write
    def resolve(self, request_id: str) -> str:
        """Run AI consensus to determine the truthful value and slash outliers."""
        sender = str(gl.message.sender_address)
        req = self.requests.get(request_id, None)
        if req is None:
            raise gl.vm.UserError("Request not found")
        if req.status != "PENDING":
            raise gl.vm.UserError("Request already resolved")
        if int(req.reports_count) < int(self.min_reports_required):
            raise gl.vm.UserError(
                f"Need at least {int(self.min_reports_required)} distinct reports to resolve"
            )

        result = self._run_consensus(request_id)
        req.status = "RESOLVED"
        req.result = u256(int(result["median"]))
        req.resolved_at = u256(self._now())
        self.requests[request_id] = req

        return json.dumps(result)

    # ------------------------------------------------------------------
    # Consensus: single nondet round
    # ------------------------------------------------------------------

    def _run_consensus(self, request_id: str) -> dict:
        """Single AI consensus round: fetch sources, verify facts, then median + outlier detection + slashing.

        CRITICAL FIX: The contract now ACQUIRES external sources on-chain
        via gl.nondet.web.render and verifies the factual outcome via AI
        consensus before computing the median. Validators independently
        verify the sources, not just statistics over caller-submitted values.
        """
        # Collect all reports for this request
        all_reports: list[Report] = []
        for key, report in self.reports.items():
            if key.startswith(request_id + ":"):
                all_reports.append(report)

        def leader_fn() -> dict:
            # ---- Acquire and verify external sources on-chain ----
            # Each report MUST have its source fetched and verified by AI.
            # If acquisition or verification fails, the report is excluded from
            # the consensus — no fallback to caller-submitted values.
            # Each verified entry keeps its originating oracle attached. Keeping
            # oracle and value in ONE record is what makes the slash target
            # correct: a list of bare values can be zipped against the reports
            # out of step as soon as any report is dropped.
            verified: list[dict] = []
            verification_failures: list[str] = []

            for r in all_reports:
                # 1. Acquire the source content on-chain
                try:
                    source_content = gl.nondet.web.render(r.source, mode="text")
                except Exception:
                    source_content = ""

                if not source_content:
                    # Cannot fetch source — exclude from consensus
                    verification_failures.append(r.oracle)
                    continue

                # 2. AI verifies the factual outcome from the actual source data
                verify_prompt = (
                    f"Data request: {self.requests[request_id].query if request_id in self.requests else 'unknown'}\n"
                    f"Oracle reported value: {r.value}\n"
                    f"Source content:\n{source_content[:4000]}\n\n"
                    f"Does the source content support the reported value? "
                    f"Respond as JSON: {{\"verified_value\": number, \"supported\": true/false}}"
                )
                try:
                    verify_res = gl.nondet.exec_prompt(verify_prompt, response_format="json")
                except Exception:
                    # AI verification failed — exclude from consensus
                    verification_failures.append(r.oracle)
                    continue

                # INVARIANT: never fall back to the caller's claimed number.
                # A missing, unsupported, non-numeric or non-finite value
                # disqualifies the report outright.
                if not isinstance(verify_res, dict):
                    verification_failures.append(r.oracle)
                    continue
                if verify_res.get("supported") is not True:
                    # The source does not support the claim — exclude it.
                    verification_failures.append(r.oracle)
                    continue
                verified_val = verify_res.get("verified_value", None)
                if verified_val is None or isinstance(verified_val, bool):
                    # Missing or boolean masquerading as a number.
                    verification_failures.append(r.oracle)
                    continue
                if not isinstance(verified_val, (int, float)):
                    verification_failures.append(r.oracle)
                    continue
                fval = float(verified_val)
                if fval != fval or fval in (float("inf"), float("-inf")):
                    # NaN / infinity are not usable measurements.
                    verification_failures.append(r.oracle)
                    continue

                verified.append({"oracle": r.oracle, "value": fval, "reported": float(r.value)})

            if not verified:
                raise gl.vm.UserError(
                    f"No reports could be verified against their sources ({len(verification_failures)} failures)"
                )

            values = sorted(v["value"] for v in verified)
            n = len(values)
            median = values[n // 2] if n % 2 == 1 else (values[n // 2 - 1] + values[n // 2]) / 2.0

            # Compute std dev
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / n
            std_dev = math.sqrt(variance)

            # Outliers are judged against each record's OWN verified value, taken
            # from that record. There is no positional pairing with all_reports,
            # so a dropped fetch cannot shift the slash target onto a bystander.
            threshold = int(self.outlier_threshold) / 100.0
            outliers: list[str] = []
            if std_dev > 0:
                for entry in verified:
                    if abs(entry["value"] - median) > threshold * std_dev:
                        outliers.append(entry["oracle"])

            return {
                "median": median,
                "std_dev": std_dev,
                "outliers": outliers,
                "values": values,
                # Verified values with their originating oracles, so a reader can
                # re-check who said what and see that the pairing is explicit.
                "verified": verified,
                "excluded": verification_failures,
            }

        def validator_fn(leaders_res: Any) -> bool:
            if not isinstance(leaders_res, gl.vm.Return):
                return False
            try:
                mine = leader_fn()
            except Exception:
                return False
            theirs = leaders_res.calldata
            return (
                mine["median"] == theirs["median"]
                and sorted(mine["outliers"]) == sorted(list(theirs["outliers"]))
                # Validators must agree on WHICH oracle produced which value, not
                # merely on the outlier list.
                and [e["oracle"] for e in mine["verified"]]
                == [e["oracle"] for e in theirs["verified"]]
            )

        raw = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
        # run_nondet_unsafe returns a wrapper on some runtimes and the raw dict on
        # others; unwrap defensively so resolve() cannot revert on a wrapper.
        result = raw.calldata if hasattr(raw, "calldata") else raw

        # Slash outliers. Each name came from a record that carried its own
        # oracle, so this targets exactly the outlier.
        for oracle_addr in result["outliers"]:
            self._slash_oracle(oracle_addr)

        return result

    def _slash_oracle(self, oracle_addr: str) -> None:
        """Slash an outlier oracle.

        Burned stake moves into `slashed_pool` rather than simply vanishing, so
        it remains accountable and can be disposed of explicitly.
        """
        rec = self.oracles.get(oracle_addr, None)
        if rec is None or not rec.active:
            return
        # Never slash stake that is already reserved for a pending withdrawal,
        # and never let the slash exceed the recorded stake.
        slashable = int(rec.staked) - int(rec.pending_withdraw)
        slash_amount = slashable * int(self.slash_percent) // 100
        if slash_amount <= 0:
            return
        rec.staked -= u256(slash_amount)
        rec.slashed_pool += u256(slash_amount)
        rec.slashed_count += u256(1)
        rec.total_slashed += u256(slash_amount)
        if int(rec.staked) < int(self.min_stake):
            rec.active = False
        self.oracles[oracle_addr] = rec

    # ------------------------------------------------------------------
    # Custody lifecycle: stake withdrawal and slashed-value disposal
    # ------------------------------------------------------------------

    @gl.public.write
    def request_withdraw(self, amount: int) -> int:
        """Phase 1 — reserve an amount of the caller's stake for withdrawal.

        Two-phase on purpose. The reservation is recorded first and settled by a
        separate `claim_withdraw` call, so a replayed or re-entrant request
        cannot reserve the same stake twice, and a withdrawal can never be
        settled more than once.

        Returns the withdrawal nonce for this request.
        """
        sender = str(gl.message.sender_address)
        rec = self.oracles.get(sender, None)
        if rec is None:
            raise gl.vm.UserError("Oracle not registered")

        amt = int(amount)
        if amt <= 0:
            raise gl.vm.UserError("withdraw amount must be positive")

        if rec.pending_withdraw > u256(0):
            raise gl.vm.UserError("A withdrawal is already pending; settle it first")

        # Only stake that is free to leave, and never below the minimum, so an
        # oracle cannot withdraw its way out of the security requirement while
        # remaining active.
        available = int(rec.staked) - int(rec.pending_withdraw)
        if amt > available:
            raise gl.vm.UserError(f"Amount exceeds available stake ({available} wei)")
        if int(rec.staked) - amt < int(self.min_stake):
            raise gl.vm.UserError("Cannot withdraw below the minimum stake")

        rec.pending_withdraw = u256(amt)
        rec.withdraw_nonce += u256(1)
        nonce = int(rec.withdraw_nonce)
        self.oracles[sender] = rec
        return nonce

    @gl.public.write
    def claim_withdraw(self, nonce: int) -> int:
        """Phase 2 — settle a pending withdrawal exactly once.

        The reservation is zeroed before the payout is recorded, so a second
        call with the same nonce finds nothing to settle and cannot pay twice.
        """
        sender = str(gl.message.sender_address)
        rec = self.oracles.get(sender, None)
        if rec is None:
            raise gl.vm.UserError("Oracle not registered")

        if rec.pending_withdraw == u256(0):
            raise gl.vm.UserError("No pending withdrawal")

        if int(nonce) != int(rec.withdraw_nonce):
            raise gl.vm.UserError("Stale or unknown withdrawal nonce")

        amount = int(rec.pending_withdraw)
        # Clear the reservation first: replay resistance depends on this.
        rec.pending_withdraw = u256(0)
        rec.staked -= u256(amount)
        self.oracles[sender] = rec
        self.settled_withdrawals += u256(amount)
        return amount

    @gl.public.view
    def get_pending_withdraw(self, oracle: str) -> str:
        """Pending (reserved but unsettled) withdrawal for an oracle."""
        rec = self.oracles.get(oracle, None)
        if rec is None:
            return json.dumps({"exists": False})
        return json.dumps({
            "exists": True,
            "pending_withdraw": int(rec.pending_withdraw),
            "withdraw_nonce": int(rec.withdraw_nonce),
            "slashed_pool": int(rec.slashed_pool),
            "settled_withdrawals": int(self.settled_withdrawals),
        })

    @gl.public.write
    def dispose_slashed(self) -> int:
        """Dispose of an oracle's own slashed stake.

        Slashed value is held in the oracle's `slashed_pool` and is never
        silently re-credited to their stake. Disposal is owner-initiated only
        and moves the pool to the network sink, so burned stake cannot be
        recycled to re-qualify as an oracle.
        """
        sender = str(gl.message.sender_address)
        rec = self.oracles.get(sender, None)
        if rec is None:
            raise gl.vm.UserError("Oracle not registered")
        amount = int(rec.slashed_pool)
        if amount <= 0:
            raise gl.vm.UserError("No slashed value to dispose")
        rec.slashed_pool = u256(0)
        self.oracles[sender] = rec
        self.slashed_sink += u256(amount)
        return amount

    # ------------------------------------------------------------------
    # View methods
    # ------------------------------------------------------------------

    @gl.public.view
    def get_oracle(self, oracle: str) -> str:
        """Get an oracle's record."""
        rec = self.oracles.get(oracle, None)
        if rec is None:
            return json.dumps({"exists": False})
        return json.dumps({
            "exists": True,
            "staked": int(rec.staked),
            "reports_count": int(rec.reports_count),
            "slashed_count": int(rec.slashed_count),
            "total_slashed": int(rec.total_slashed),
            "active": rec.active,
            "pending_withdraw": int(rec.pending_withdraw),
            "slashed_pool": int(rec.slashed_pool),
            "withdraw_nonce": int(rec.withdraw_nonce),
        })

    @gl.public.view
    def get_request(self, request_id: str) -> str:
        """Get a request's details."""
        req = self.requests.get(request_id, None)
        if req is None:
            return json.dumps({"exists": False})
        return json.dumps({
            "request_id": request_id,
            "exists": True,
            "requester": req.requester,
            "query": req.query,
            "status": req.status,
            "result": req.result,
            "reports_count": int(req.reports_count),
            "resolved_at": int(req.resolved_at),
        })

    @gl.public.view
    def get_report(self, request_id: str, oracle: str) -> str:
        """Get a specific report."""
        key = f"{request_id}:{oracle}"
        report = self.reports.get(key, None)
        if report is None:
            return json.dumps({"exists": False})
        return json.dumps({
            "exists": True,
            "oracle": report.oracle,
            "request_id": report.request_id,
            "value": report.value,
            "source": report.source,
            "timestamp": int(report.timestamp),
        })

    @gl.public.view
    def now(self) -> str:
        """Current transaction timestamp in Unix seconds."""
        return str(self._now())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _now(self) -> int:
        return int(datetime.now(timezone.utc).timestamp())
