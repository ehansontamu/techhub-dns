#!/usr/bin/env python3
"""Concurrency regression coverage for technician-facing backend write paths.

The production deployment uses MySQL row/advisory locks, but the local test
harness here keeps the scenarios deterministic by sharing in-memory state across
multiple worker threads. The goal is to prove the backend's conflict handling
and exclusivity rules under a 4-6 technician-style load.
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

sys.path.append(".")

from flask import Flask, g

from app.models.delivery_run import DeliveryRun
from app.models.order import Order, OrderStatus
from app.models.print_job import PrintJob, PrintJobStatus
from app.models.vehicle_checkout import VehicleCheckout
from app.services.order_service import OrderService
from app.services.print_job_service import PrintJobService
from app.services.vehicle_checkout_service import VehicleCheckoutService
from app.utils.exceptions import ConflictError, ValidationError


def _run_threads(
    work_items: list[tuple[str, Callable[[], Any]]]
) -> tuple[dict[str, Any], dict[str, BaseException]]:
    results: dict[str, Any] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()

    def _runner(name: str, fn: Callable[[], Any]) -> None:
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 - capture thread failures for assertions
            with lock:
                errors[name] = exc
        else:
            with lock:
                results[name] = value

    threads: list[threading.Thread] = []
    for name, fn in work_items:
        thread = threading.Thread(target=_runner, name=name, args=(name, fn))
        thread.daemon = True
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), f"Worker thread {thread.name} hung"

    return results, errors


@dataclass
class _OrderStore:
    order: Order
    commits: int = 0


class _OrderQuery:
    def __init__(self, store: _OrderStore):
        self._store = store

    def filter(self, *args, **kwargs):  # noqa: D401 - chainable fake query
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self._store.order


class _OrderDb:
    def __init__(self, store: _OrderStore):
        self._store = store

    def query(self, model):
        if model is not Order:
            raise AssertionError(f"Unexpected query model: {model!r}")
        return _OrderQuery(self._store)

    def add(self, _obj):
        return None

    def commit(self):
        self._store.commits += 1

    def refresh(self, _obj):
        return None


@dataclass
class _VehicleStore:
    active_checkout: VehicleCheckout | None = None
    pending_checkout: VehicleCheckout | None = None
    commits: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class _VehicleQuery:
    def __init__(self, store: _VehicleStore, model):
        self._store = store
        self._model = model

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        if self._model is DeliveryRun:
            return None
        if self._model is VehicleCheckout:
            return self._store.active_checkout
        raise AssertionError(f"Unexpected query model: {self._model!r}")

    def all(self):
        return []


class _VehicleDb:
    def __init__(self, store: _VehicleStore):
        self._store = store

    def query(self, model):
        return _VehicleQuery(self._store, model)

    def get_bind(self):
        return None

    def add(self, obj):
        self._store.pending_checkout = obj

    def commit(self):
        self._store.commits += 1
        if self._store.pending_checkout is not None:
            self._store.active_checkout = self._store.pending_checkout
            self._store.pending_checkout = None

    def refresh(self, _obj):
        return None


@dataclass
class _PrintStore:
    jobs: list[PrintJob]
    commits: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


class _PrintQuery:
    def __init__(self, store: _PrintStore):
        self._store = store

    def options(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        with self._store.lock:
            for job in self._store.jobs:
                if job.status == PrintJobStatus.PENDING:
                    job.status = PrintJobStatus.CLAIMED
                    return job
            return None

    def all(self):
        now = datetime.now()
        return [
            job
            for job in self._store.jobs
            if job.status == PrintJobStatus.CLAIMED
            and job.claim_expires_at is not None
            and job.claim_expires_at < now
        ]


class _PrintDb:
    def __init__(self, store: _PrintStore):
        self._store = store

    def query(self, model):
        if model is not PrintJob:
            raise AssertionError(f"Unexpected query model: {model!r}")
        return _PrintQuery(self._store)

    def add(self, _obj):
        return None

    def commit(self):
        self._store.commits += 1

    def flush(self):
        return None

    def refresh(self, _obj):
        return None


def _make_order() -> Order:
    now = datetime.now().replace(microsecond=0)
    return Order(
        id="order-1",
        inflow_order_id="TH-CONCURRENT-1",
        status=OrderStatus.PICKED.value,
        tagged_at=None,
        tagged_by=None,
        tag_data=None,
        updated_at=now,
    )


def test_parallel_technician_writes_conflict_on_the_same_order(monkeypatch):
    shared_order = _make_order()
    store = _OrderStore(order=shared_order)
    db = _OrderDb(store)
    service = OrderService(db)
    expected_updated_at = shared_order.updated_at

    leader_committed = threading.Event()
    original_assert_not_stale = OrderService.assert_not_stale

    def gated_assert_not_stale(self, order, expected_updated_at):
        if threading.current_thread().name != "leader":
            assert leader_committed.wait(timeout=5), "leader never finished"
        return original_assert_not_stale(self, order, expected_updated_at)

    monkeypatch.setattr(OrderService, "assert_not_stale", gated_assert_not_stale)
    monkeypatch.setattr(OrderService, "_requires_asset_tags", lambda self, order: False)
    monkeypatch.setattr(OrderService, "_ensure_remainder_leg_ready", lambda self, order, action: None)
    monkeypatch.setattr(
        "app.services.audit_service.AuditService.log_order_action",
        lambda *args, **kwargs: None,
    )

    def make_worker(name: str, tag_ids: list[str], is_leader: bool):
        def _worker():
            try:
                return service.mark_asset_tagged(
                    order_id=shared_order.id,
                    tag_ids=tag_ids,
                    technician=name,
                    expected_updated_at=expected_updated_at,
                )
            finally:
                if is_leader:
                    leader_committed.set()

        return _worker

    workers = [
        ("leader", make_worker("leader", ["A-1"], True)),
        ("tech-2", make_worker("tech-2", ["A-2"], False)),
        ("tech-3", make_worker("tech-3", ["A-3"], False)),
        ("tech-4", make_worker("tech-4", ["A-4"], False)),
        ("tech-5", make_worker("tech-5", ["A-5"], False)),
        ("tech-6", make_worker("tech-6", ["A-6"], False)),
    ]

    results, errors = _run_threads(workers)

    assert len(results) == 1
    assert "leader" in results
    assert store.commits == 1
    assert shared_order.tagged_by == "leader"
    assert shared_order.tag_data == {"tag_ids": ["A-1"]}
    assert len(errors) == 5
    assert all(isinstance(err, ConflictError) for err in errors.values())


def test_vehicle_checkout_allows_only_one_technician_to_claim_vehicle(monkeypatch):
    store = _VehicleStore()
    db = _VehicleDb(store)
    service = VehicleCheckoutService(db)
    vehicle_locks: dict[str, threading.Lock] = {}

    @contextmanager
    def fake_vehicle_lock(self, vehicle: str):
        lock = vehicle_locks.setdefault(vehicle, threading.Lock())
        with lock:
            yield

    monkeypatch.setattr(VehicleCheckoutService, "_vehicle_lock", fake_vehicle_lock)

    app = Flask(__name__)

    def make_worker(name: str):
        def _worker():
            with app.app_context():
                g.user_id = f"user-{name}"
                g.user_email = f"{name}@example.com"
                g.user_data = {"display_name": name.title()}
                return service.checkout(vehicle="van", checkout_type="delivery_run")

        return _worker

    workers = [(f"tech-{index}", make_worker(f"tech-{index}")) for index in range(1, 7)]
    results, errors = _run_threads(workers)

    assert len(results) == 1
    winner = next(name for name, value in results.items() if value is not None)
    checkout = results[winner]
    assert checkout.vehicle == "van"
    assert checkout.checked_out_by == winner.title()
    assert store.active_checkout is checkout
    assert store.commits == 1
    assert len(errors) == 5
    assert all(isinstance(err, ValidationError) for err in errors.values())
    assert all("already checked out" in str(err).lower() for err in errors.values())


def test_print_job_claiming_distributes_jobs_without_duplicates(monkeypatch):
    now = datetime.now().replace(microsecond=0)
    jobs = [
        PrintJob(
            id=f"job-{index}",
            order_id=f"order-{index}",
            document_type="picklist",
            status=PrintJobStatus.PENDING,
            trigger_source="automatic",
            requested_by="ops@example.com",
            file_path=f"/tmp/picklist-{index}.pdf",
            attempt_count=0,
            created_at=now + timedelta(seconds=index),
            updated_at=now + timedelta(seconds=index),
        )
        for index in range(1, 4)
    ]
    store = _PrintStore(jobs=jobs)
    db = _PrintDb(store)
    service = PrintJobService(db)

    monkeypatch.setattr(
        "app.services.audit_service.AuditService.log_order_action",
        lambda *args, **kwargs: None,
    )

    def make_worker():
        def _worker():
            job = service.claim_next_pending_job(claim_timeout_seconds=30)
            return None if job is None else job.id

        return _worker

    workers = [(f"worker-{index}", make_worker()) for index in range(1, 7)]
    results, errors = _run_threads(workers)

    assert not errors
    claimed_ids = [job_id for job_id in results.values() if job_id is not None]
    assert len(claimed_ids) == 3
    assert len(set(claimed_ids)) == 3
    assert sorted(claimed_ids) == ["job-1", "job-2", "job-3"]
    assert len(results) == 6
    assert len([job_id for job_id in results.values() if job_id is None]) == 3
    assert store.commits == 0
    assert all(job.status == PrintJobStatus.CLAIMED for job in jobs)


if __name__ == "__main__":
    monkeypatch = pytest.MonkeyPatch()
    try:
        test_parallel_technician_writes_conflict_on_the_same_order(monkeypatch=monkeypatch)
        print("[PASS] parallel technician write conflict")
    finally:
        monkeypatch.undo()

    monkeypatch = pytest.MonkeyPatch()
    try:
        test_vehicle_checkout_allows_only_one_technician_to_claim_vehicle(monkeypatch=monkeypatch)
        print("[PASS] vehicle checkout exclusivity")
    finally:
        monkeypatch.undo()

    monkeypatch = pytest.MonkeyPatch()
    try:
        test_print_job_claiming_distributes_jobs_without_duplicates(monkeypatch=monkeypatch)
        print("[PASS] print job claim exclusivity")
    finally:
        monkeypatch.undo()

    print("[SUCCESS] concurrent technician regression tests passed")
