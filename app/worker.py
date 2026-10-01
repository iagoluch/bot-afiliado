from __future__ import annotations

import json
import os
import math
import shutil
import signal
import sqlite3
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings
from app.db import Database
from app.models import utc_now
from app.scheduler import run_tick
from app.services.p1 import P1Pipeline
from app.services.pipeline import Pipeline


DEFAULT_SOURCE = Path("examples/shopee_offers.sample.csv")
SQLITE_RETRY_ATTEMPTS = 3
SQLITE_RETRY_BASE_SECONDS = 0.1
MAX_ERROR_BACKOFF_MULTIPLIER = 8


class WorkerAlreadyRunning(RuntimeError):
    pass


class _StopRequested(RuntimeError):
    pass


def _safe_warning(event: str, exc: BaseException) -> None:
    print(
        json.dumps({"event": event, "error_type": type(exc).__name__}, ensure_ascii=False),
        file=sys.stderr,
        flush=True,
    )


def _positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} deve ser um numero positivo") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} deve ser maior que zero")
    return value


def _sqlite_locked(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).lower() for marker in ("database is locked", "database table is locked")
    )


def retry_sqlite(
    action: Callable[[], Any],
    *,
    wait: Callable[[float], bool],
    attempts: int = SQLITE_RETRY_ATTEMPTS,
    base_delay: float = SQLITE_RETRY_BASE_SECONDS,
) -> Any:
    """Repete apenas lock transitório; outros erros preservam sua semântica."""
    for attempt in range(attempts):
        try:
            return action()
        except sqlite3.OperationalError as exc:
            if not _sqlite_locked(exc) or attempt + 1 >= attempts:
                raise
            if wait(base_delay * (2**attempt)):
                raise _StopRequested from exc
    raise AssertionError("tentativas SQLite esgotadas sem resultado")


class WorkerLock:
    """Lock de arquivo mantido aberto durante toda a vida do processo.

    Em Linux, `flock` libera automaticamente o lock após crash e evita a
    fragilidade de arquivos PID órfãos. O arquivo não é removido no shutdown,
    pois remover um inode ainda bloqueado permitiria dois locks concorrentes.
    """

    def __init__(self, path: Path):
        self.path = path
        self._file: Any = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="ascii")
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif os.name == "nt":
                import msvcrt

                handle.seek(0)
                if not handle.read(1):
                    handle.seek(0)
                    handle.write("0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                raise RuntimeError(f"plataforma sem lock local suportado: {os.name}")
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise WorkerAlreadyRunning("outro worker ja possui o lock desta instalacao") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
            elif os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "WorkerLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def _ollama_reachable(settings: Settings) -> bool:
    base_url = str(getattr(settings, "ollama_base_url", os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")))
    configured_timeout = float(getattr(settings, "ollama_timeout_seconds", 300))
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return False
    connection: HTTPConnection | None = None
    try:
        connection = HTTPConnection(parsed.hostname, parsed.port or 11434, timeout=min(configured_timeout, 2.0))
        # http.client não segue redirect; a sonda nunca sai do loopback validado.
        connection.request("GET", "/api/version", headers={"Accept": "application/json"})
        response = connection.getresponse()
        response.read(4096)
        return 200 <= response.status < 300
    except Exception:
        return False
    finally:
        if connection is not None:
            connection.close()


def _ffmpeg_available(settings: Settings) -> bool:
    configured = settings.ffmpeg_path
    if configured:
        path = Path(configured).expanduser()
        return path.is_file() if path.parent != Path(".") else shutil.which(configured) is not None
    return shutil.which("ffmpeg") is not None


def runtime_status(
    db: Database,
    settings: Settings,
    *,
    ollama_probe: Callable[[Settings], bool] = _ollama_reachable,
    ffmpeg_probe: Callable[[Settings], bool] = _ffmpeg_available,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Retorna estado operacional sem URLs, conteúdo de oferta ou segredos."""
    database_status = "ok"
    worker: dict[str, Any] = {
        "status": "unknown",
        "last_heartbeat": None,
        "last_cycle": None,
        "last_result": None,
    }
    queue_depth: int | None = None
    try:
        row = db.worker_runtime()
        queue_depth = db.queue_depth(dry_run=settings.dry_run)
        if row is not None:
            status = str(row["status"]).lower()
            heartbeat = row["heartbeat_at"]
            if status in {"starting", "running"} and heartbeat:
                try:
                    parsed = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
                    current = now or datetime.now(timezone.utc)
                    if current.tzinfo is None:
                        current = current.replace(tzinfo=timezone.utc)
                    stale_after = max(_positive_float_env("WORKER_IDLE_SECONDS", 30.0) * 3, 90.0)
                    if (current - parsed.astimezone(timezone.utc)).total_seconds() > stale_after:
                        status = "stale"
                except ValueError:
                    status = "unknown"
            worker = {
                "status": status,
                "last_heartbeat": heartbeat,
                "last_cycle": row["last_cycle_at"],
                "last_result": row["last_result"],
            }
    except sqlite3.Error:
        database_status = "unavailable"

    model = str(getattr(settings, "ollama_model", os.getenv("OLLAMA_MODEL", "qwen3.5:2b")))
    return {
        "database": database_status,
        "worker": worker,
        "queue_depth": queue_depth,
        "ollama_reachable": bool(ollama_probe(settings)),
        "ollama_model": model,
        "ffmpeg_available": bool(ffmpeg_probe(settings)),
        "dry_run": settings.dry_run,
    }


class Worker:
    def __init__(
        self,
        db: Database,
        settings: Settings,
        pipeline: Pipeline,
        content_pipeline: P1Pipeline,
        source: Path,
        *,
        idle_seconds: float,
        error_backoff_seconds: float,
        tick: Callable[[Database, Pipeline, Path, datetime], dict[str, Any]] = run_tick,
        stop_event: threading.Event | None = None,
        heartbeat_interval: float = 15.0,
    ):
        self.db = db
        self.settings = settings
        self.pipeline = pipeline
        self.content_pipeline = content_pipeline
        self.source = source
        if not all(math.isfinite(value) and value > 0 for value in (idle_seconds, error_backoff_seconds, heartbeat_interval)):
            raise ValueError("intervalos do worker devem ser numeros positivos e finitos")
        self.idle_seconds = idle_seconds
        self.error_backoff_seconds = error_backoff_seconds
        self.tick = tick
        self.stop_event = stop_event or threading.Event()
        self.heartbeat_interval = heartbeat_interval
        self._heartbeat_stop = threading.Event()
        self._status_lock = threading.Lock()
        self._current_status = "STARTING"
        self._warning_conditions: set[str] = set()

    def _warn_once(self, condition: str, event: str, exc: BaseException) -> None:
        if condition not in self._warning_conditions:
            _safe_warning(event, exc)
            self._warning_conditions.add(condition)

    def _clear_warning(self, condition: str) -> None:
        self._warning_conditions.discard(condition)

    def stop(self) -> None:
        self.stop_event.set()

    def _wait(self, seconds: float) -> bool:
        return self.stop_event.wait(seconds)

    def _heartbeat(
        self,
        status: str,
        *,
        started_at: str | None = None,
        last_tick_at: str | None = None,
        last_cycle_at: str | None = None,
        last_result: str | None = None,
        error_type: str | None = None,
    ) -> None:
        with self._status_lock:
            self._current_status = status
        retry_sqlite(
            lambda: self.db.record_worker_heartbeat(
                status,
                pid=os.getpid() if status != "STOPPED" else None,
                started_at=started_at,
                last_tick_at=last_tick_at,
                last_cycle_at=last_cycle_at,
                last_result=last_result,
                error_type=error_type,
            ),
            wait=self._wait,
        )

    def _heartbeat_loop(self) -> None:
        while not self._heartbeat_stop.wait(self.heartbeat_interval):
            with self._status_lock:
                status = self._current_status
            if status == "STOPPED":
                return
            try:
                retry_sqlite(
                    lambda: self.db.record_worker_heartbeat(status, pid=os.getpid()),
                    wait=self._heartbeat_stop.wait,
                )
            except _StopRequested:
                return
            except sqlite3.Error as exc:
                # A próxima batida tenta novamente; o workload principal não é repetido.
                self._warn_once("heartbeat", "worker_heartbeat_failed", exc)
            else:
                self._clear_warning("heartbeat")

    def _process_content_jobs(self, tick_result: dict[str, Any]) -> None:
        generated: list[dict[str, Any]] = []
        while not self.stop_event.is_set():
            job = retry_sqlite(
                lambda: self.db.claim_content_job(dry_run=self.settings.dry_run),
                wait=self._wait,
            )
            if job is None:
                break
            offer_id = int(job["offer_id"])
            if job["facts_fingerprint"] != job["current_fingerprint"]:
                retry_sqlite(lambda: self.db.supersede_content_job(int(job["id"])), wait=self._wait)
                generated.append({"offer_id": offer_id, "status": "SUPERSEDED"})
                continue
            try:
                result = retry_sqlite(
                    lambda: self.content_pipeline.generate_offer(offer_id, str(job["campaign_id"])),
                    wait=self._wait,
                )
            except _StopRequested:
                raise
            except Exception as exc:
                delay = min(300, 2 ** min(int(job["attempts"]), 8))
                retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
                retry_sqlite(
                    lambda: self.db.fail_content_job(int(job["id"]), type(exc).__name__, retry_at),
                    wait=self._wait,
                )
                generated.append({"offer_id": offer_id, "status": "FAILED", "error_type": type(exc).__name__})
                try:
                    self.db.record_operation_event(
                        "content", "dry" if self.settings.dry_run else "real", "p1", "FAILED",
                        offers=1, error_type=type(exc).__name__,
                    )
                    self._clear_warning("operation_log")
                except Exception as log_exc:
                    self._warn_once("operation_log", "operation_log_failed", log_exc)
            else:
                retry_sqlite(lambda: self.db.complete_content_job(int(job["id"])), wait=self._wait)
                generated.append(result)
        if tick_result.get("status") == "cycle" and isinstance(tick_result.get("result"), dict):
            tick_result["result"]["content"] = generated

    def _record_result(self, result: dict[str, Any]) -> None:
        try:
            if result.get("status") == "queue":
                queue = result.get("result") or {}
                self.db.record_operation_event(
                    "queue", "dry" if self.settings.dry_run else "real", self.pipeline.source_adapter,
                    str(queue.get("status", "UNKNOWN")), queue_id=queue.get("queue_id"),
                )
            elif result.get("status") == "cycle":
                cycle = result.get("result") or {}
                publications = cycle.get("publications") or []
                content = cycle.get("content") or []
                partial = any(item.get("status") in {
                    "FAILED", "RATE_LIMITED", "CIRCUIT_OPEN", "REJECTED_STALE", "NEEDS_RECONCILIATION",
                } for item in [*publications, *content])
                self.db.record_operation_event(
                    "cycle", "dry" if self.settings.dry_run else "real", self.pipeline.source_adapter,
                    "PARTIAL" if partial else "COMPLETED", offers=len(cycle.get("offers") or []),
                    queued=len(cycle.get("queue") or []), processed=len(publications),
                )
            self._clear_warning("operation_log")
        except Exception as exc:
            # Telemetria nunca pode repetir uma publicação ou invalidar um ciclo.
            self._warn_once("operation_log", "operation_log_failed", exc)

    def run_forever(self) -> None:
        started_at = utc_now()
        consecutive_errors = 0
        retry_sqlite(lambda: self.db.recover_content_jobs(dry_run=self.settings.dry_run), wait=self._wait)
        self._heartbeat("STARTING", started_at=started_at)
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="bot-afiliado-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            while not self.stop_event.is_set():
                try:
                    result = retry_sqlite(
                        lambda: self.tick(self.db, self.pipeline, self.source, datetime.now()),
                        wait=self._wait,
                    )
                    self._process_content_jobs(result)
                    self._record_result(result)
                    now = utc_now()
                    result_status = str(result.get("status", "idle"))
                    self._heartbeat(
                        "RUNNING",
                        last_tick_at=now,
                        last_cycle_at=now if result_status == "cycle" else None,
                        last_result=result_status if result_status in {"cycle", "queue", "idle"} else "error",
                    )
                    consecutive_errors = 0
                    if result_status == "idle" and self._wait(self.idle_seconds):
                        break
                except _StopRequested:
                    break
                except Exception as exc:
                    consecutive_errors += 1
                    try:
                        retry_sqlite(
                            lambda: self.db.recover_content_jobs(dry_run=self.settings.dry_run),
                            wait=self._wait,
                        )
                        self._clear_warning("content_recovery")
                    except _StopRequested:
                        break
                    except sqlite3.Error as recovery_exc:
                        self._warn_once("content_recovery", "content_recovery_failed", recovery_exc)
                    try:
                        self._heartbeat("ERROR", last_result="error", error_type=type(exc).__name__)
                    except _StopRequested:
                        break
                    except sqlite3.Error as heartbeat_exc:
                        self._warn_once("heartbeat", "worker_heartbeat_failed", heartbeat_exc)
                    multiplier = min(2 ** (consecutive_errors - 1), MAX_ERROR_BACKOFF_MULTIPLIER)
                    if self._wait(self.error_backoff_seconds * multiplier):
                        break
        finally:
            self._heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(self.heartbeat_interval, 1.0) + 1.0)
            try:
                self._heartbeat("STOPPED", last_result="stopped")
            except _StopRequested:
                self._clear_warning("heartbeat")
            except sqlite3.Error as exc:
                self._warn_once("heartbeat", "worker_heartbeat_failed", exc)


def _source_from_env(settings: Settings) -> Path:
    configured = os.getenv("WORKER_SOURCE_PATH", "").strip()
    if configured:
        source = Path(configured)
        if not settings.dry_run and source.resolve() == DEFAULT_SOURCE.resolve():
            raise ValueError("a fonte sample nao pode ser usada fora de DRY_RUN")
        return source
    if not settings.dry_run:
        raise ValueError("WORKER_SOURCE_PATH e obrigatorio fora de DRY_RUN")
    return DEFAULT_SOURCE


def main() -> None:
    settings = Settings.from_env()
    source = _source_from_env(settings)
    adapter = os.getenv("WORKER_ADAPTER", "shopee").strip().lower()
    if adapter not in {"shopee", "awin", "mercadolivre"}:
        raise ValueError("WORKER_ADAPTER deve ser shopee, awin ou mercadolivre")
    if not source.is_file():
        raise FileNotFoundError(f"fonte do worker nao encontrada: {source}")

    lock_path = Path(os.getenv("WORKER_LOCK_PATH", "").strip() or settings.database_path.with_suffix(".worker.lock"))
    try:
        with WorkerLock(lock_path):
            db = Database(settings.database_path)
            db.init()
            worker = Worker(
                db,
                settings,
                Pipeline(db, settings, source_adapter=adapter),
                P1Pipeline(db, settings),
                source,
                idle_seconds=_positive_float_env("WORKER_IDLE_SECONDS", 30.0),
                error_backoff_seconds=_positive_float_env("WORKER_ERROR_BACKOFF_SECONDS", 60.0),
            )
            previous_handlers: dict[int, Any] = {}
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.getsignal(signum)
                signal.signal(signum, lambda _signum, _frame: worker.stop())
            try:
                worker.run_forever()
            finally:
                for signum, handler in previous_handlers.items():
                    signal.signal(signum, handler)
    except WorkerAlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
