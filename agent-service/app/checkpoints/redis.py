"""A Redis DB-backed LangGraph ``BaseCheckpointSaver`` without Redis modules.

The project's fixed Redis image is ``redis:7.4-alpine``.  The vendor
``langgraph-checkpoint-redis`` integration requires RedisJSON/RediSearch, so
this saver deliberately persists LangGraph's typed checkpoint records through
ordinary Redis string operations instead.  It subclasses LangGraph's
``InMemorySaver`` only for its proven checkpoint protocol implementation, then
loads and writes that protocol state to Redis on every mutation.  Therefore the
compiled graph still uses a real ``BaseCheckpointSaver`` and ``interrupt`` /
``Command(resume=...)`` survives a fresh Python process.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Sequence
from threading import RLock
from typing import Any, Protocol, cast

import redis
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, CheckpointTuple
from langgraph.checkpoint.memory import InMemorySaver

CHECKPOINT_TTL_SECONDS = 24 * 60 * 60
KEY_PREFIX = "meeting-agent:langgraph-checkpoint:"


class RedisCheckpointError(RuntimeError):
    """A non-sensitive Redis checkpoint persistence failure."""


class RedisStringClient(Protocol):
    def get(self, name: str) -> bytes | str | None: ...

    def set(self, name: str, value: str, ex: int) -> object: ...

    def delete(self, *names: str) -> object: ...

    def ping(self) -> object: ...

    def scan_iter(self, match: str) -> Iterator[bytes | str]: ...


def checkpoint_thread_id(thread_id: str, run_id: str) -> str:
    """Make the LangGraph thread identity explicitly include threadId and runId."""

    return f"{thread_id}:{run_id}"


class RedisCheckpointSaver(InMemorySaver):
    """Real LangGraph checkpoint saver persisted in ordinary Redis DB 1.

    The saver keeps LangGraph's complete checkpoint, pending writes and channel
    blobs, rather than a separate application JSON cache.  A fresh instance
    lazily reconstructs that state from the same Redis key before graph reads.
    """

    def __init__(
        self,
        *,
        client: RedisStringClient,
        ttl_seconds: int = CHECKPOINT_TTL_SECONDS,
        key_prefix: str = KEY_PREFIX,
    ) -> None:
        super().__init__()
        if ttl_seconds < 1:
            raise ValueError("checkpoint TTL must be positive")
        self._client = client
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix
        self._loaded_threads: set[str] = set()
        # LangGraph calls ``put`` and ``put_writes`` from its executor even
        # for a single logical graph run.  ``InMemorySaver`` owns mutable
        # dictionaries, while this implementation serializes their complete
        # contents to one Redis string per thread.  Keep load/mutate/save one
        # critical section so concurrent checkpoint tasks cannot corrupt the
        # in-memory protocol state or overwrite each other's Redis snapshot.
        self._state_lock = RLock()

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        ttl_seconds: int = CHECKPOINT_TTL_SECONDS,
    ) -> RedisCheckpointSaver:
        client = redis.Redis.from_url(url, decode_responses=False, socket_connect_timeout=2)
        return cls(client=cast(RedisStringClient, client), ttl_seconds=ttl_seconds)

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def key_for_thread(self, thread_id: str) -> str:
        return f"{self._key_prefix}{thread_id}"

    def probe(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = _thread_id(config)
        with self._state_lock:
            self._load_thread(thread_id)
            return super().get_tuple(config)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with self._state_lock:
            if config is not None:
                self._load_thread(_thread_id(config))
            else:
                try:
                    for raw_key in self._client.scan_iter(match=f"{self._key_prefix}*"):
                        key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
                        self._load_thread(key.removeprefix(self._key_prefix))
                except Exception as exc:
                    raise RedisCheckpointError("checkpoint listing is unavailable") from exc
            # Do not let the caller iterate mutable ``InMemorySaver`` state
            # after releasing the lock.
            checkpoints = list(super().list(config, filter=filter, before=before, limit=limit))
        yield from checkpoints

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: dict[str, str | int | float],
    ) -> RunnableConfig:
        thread_id = _thread_id(config)
        with self._state_lock:
            self._load_thread(thread_id)
            saved = super().put(config, checkpoint, metadata, new_versions)
            self._save_thread(thread_id)
            return saved

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = _thread_id(config)
        with self._state_lock:
            self._load_thread(thread_id)
            super().put_writes(config, writes, task_id, task_path)
            self._save_thread(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        with self._state_lock:
            super().delete_thread(thread_id)
            self._loaded_threads.discard(thread_id)
            try:
                self._client.delete(self.key_for_thread(thread_id))
            except Exception as exc:
                raise RedisCheckpointError("checkpoint deletion is unavailable") from exc

    def _load_thread(self, thread_id: str) -> None:
        if thread_id in self._loaded_threads:
            return
        try:
            raw = self._client.get(self.key_for_thread(thread_id))
        except Exception as exc:
            raise RedisCheckpointError("checkpoint read is unavailable") from exc
        if raw is not None:
            try:
                text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                payload = json.loads(text)
                self._restore_thread(thread_id, payload)
            except (TypeError, ValueError, KeyError) as exc:
                raise RedisCheckpointError("checkpoint data is invalid") from exc
        self._loaded_threads.add(thread_id)

    def _save_thread(self, thread_id: str) -> None:
        payload = self._serialize_thread(thread_id)
        try:
            self._client.set(
                self.key_for_thread(thread_id),
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ex=self._ttl_seconds,
            )
        except Exception as exc:
            raise RedisCheckpointError("checkpoint write is unavailable") from exc

    def _serialize_thread(self, thread_id: str) -> dict[str, object]:
        storage: list[dict[str, object]] = []
        for checkpoint_ns, checkpoints in self.storage.get(thread_id, {}).items():
            for checkpoint_id, (checkpoint, metadata, parent_id) in checkpoints.items():
                storage.append(
                    {
                        "namespace": checkpoint_ns,
                        "checkpointId": checkpoint_id,
                        "checkpoint": _encode_typed(checkpoint),
                        "metadata": _encode_typed(metadata),
                        "parentId": parent_id,
                    }
                )
        writes: list[dict[str, object]] = []
        for (saved_thread, checkpoint_ns, checkpoint_id), entries in self.writes.items():
            if saved_thread != thread_id:
                continue
            for (_, index), (task_id, channel, value, task_path) in entries.items():
                writes.append(
                    {
                        "namespace": checkpoint_ns,
                        "checkpointId": checkpoint_id,
                        "taskId": task_id,
                        "index": index,
                        "channel": channel,
                        "value": _encode_typed(value),
                        "taskPath": task_path,
                    }
                )
        blobs: list[dict[str, object]] = []
        for (saved_thread, checkpoint_ns, channel, version), value in self.blobs.items():
            if saved_thread != thread_id:
                continue
            blobs.append(
                {
                    "namespace": checkpoint_ns,
                    "channel": channel,
                    "version": version,
                    "value": _encode_typed(value),
                }
            )
        return {"storage": storage, "writes": writes, "blobs": blobs}

    def _restore_thread(self, thread_id: str, payload: object) -> None:
        if not isinstance(payload, dict):
            raise ValueError("checkpoint payload must be an object")
        super().delete_thread(thread_id)
        raw_storage = payload.get("storage")
        raw_writes = payload.get("writes")
        raw_blobs = payload.get("blobs")
        if not all(isinstance(value, list) for value in (raw_storage, raw_writes, raw_blobs)):
            raise ValueError("checkpoint payload fields must be lists")
        storage = cast(list[object], raw_storage)
        writes = cast(list[object], raw_writes)
        blobs = cast(list[object], raw_blobs)
        for value in storage:
            item = _object(value)
            namespace = _string(item.get("namespace"))
            checkpoint_id = _string(item.get("checkpointId"), required=True)
            parent_id = item.get("parentId")
            if parent_id is not None and not isinstance(parent_id, str):
                raise ValueError("checkpoint parent ID is invalid")
            self.storage[thread_id][namespace][checkpoint_id] = (
                _decode_typed(item.get("checkpoint")),
                _decode_typed(item.get("metadata")),
                parent_id,
            )
        for value in writes:
            item = _object(value)
            namespace = _string(item.get("namespace"))
            checkpoint_id = _string(item.get("checkpointId"), required=True)
            task_id = _string(item.get("taskId"), required=True)
            index = item.get("index")
            channel = _string(item.get("channel"), required=True)
            task_path = _string(item.get("taskPath"))
            if not isinstance(index, int):
                raise ValueError("checkpoint write index is invalid")
            self.writes[(thread_id, namespace, checkpoint_id)][(task_id, index)] = (
                task_id,
                channel,
                _decode_typed(item.get("value")),
                task_path,
            )
        for value in blobs:
            item = _object(value)
            namespace = _string(item.get("namespace"))
            channel = _string(item.get("channel"), required=True)
            version = item.get("version")
            if not isinstance(version, str | int | float):
                raise ValueError("checkpoint blob version is invalid")
            self.blobs[(thread_id, namespace, channel, version)] = _decode_typed(item.get("value"))


def _thread_id(config: RunnableConfig) -> str:
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        raise ValueError("checkpoint config is missing configurable fields")
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("checkpoint config is missing thread_id")
    return thread_id


def _encode_typed(value: object) -> dict[str, str]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("serialized checkpoint value is invalid")
    type_name, payload = value
    if not isinstance(type_name, str) or not isinstance(payload, bytes):
        raise ValueError("serialized checkpoint value is invalid")
    return {"type": type_name, "data": base64.b64encode(payload).decode("ascii")}


def _decode_typed(value: object) -> tuple[str, bytes]:
    item = _object(value)
    type_name = _string(item.get("type"), required=True)
    data = _string(item.get("data"))
    try:
        return type_name, base64.b64decode(data.encode("ascii"), validate=True)
    except ValueError as exc:
        raise ValueError("checkpoint binary value is invalid") from exc


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("checkpoint item is invalid")
    return cast(dict[str, object], value)


def _string(value: object, *, required: bool = False) -> str:
    if not isinstance(value, str) or (required and not value):
        raise ValueError("checkpoint text value is invalid")
    return value
