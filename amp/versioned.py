import asyncio
from collections.abc import Callable, Hashable
from datetime import datetime, UTC
from typing import Any, Iterable, Protocol, runtime_checkable

from pydantic import BaseModel, Field, PrivateAttr
from typing import Generic, TypeVar

from sortedcontainers import SortedDict


@runtime_checkable
class VersionTracker(Protocol):
    version: int
    updated_at: datetime
    _changed_evt: asyncio.Event
    _parent_version_tracker: "VersionTracker | None"

    def mark_updated(self) -> None: ...

    async def wait_for_change(
        self, *, since_version: int | None = None, timeout: float | None = None
    ) -> int: ...


async def wait_for_any_change(
    trackers: Iterable["VersionTracker"], timeout_secs: float
) -> None:
    if timeout_secs <= 0:
        await asyncio.sleep(0)
        return

    tasks = [
        asyncio.create_task(tracker.wait_for_change(timeout=timeout_secs))
        for tracker in trackers
    ]
    if not tasks:
        await asyncio.sleep(0)
        return

    try:
        await asyncio.wait(
            tasks, timeout=timeout_secs, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class VersionTrackerMixin:
    version: int
    updated_at: datetime
    _changed_evt: asyncio.Event
    _parent_version_tracker: "VersionTracker | None"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.version = 0
        self.updated_at = datetime.now(UTC)
        self._changed_evt = asyncio.Event()
        self._parent_version_tracker = None

    def mark_updated(self) -> None:
        self.version += 1
        self.updated_at = datetime.now(UTC)

        evt = self._changed_evt
        self._changed_evt = asyncio.Event()
        evt.set()
        if self._parent_version_tracker is not None:
            self._parent_version_tracker.mark_updated()

    async def wait_for_ready(
        self, is_ready: Callable[[], bool], timeout_secs: float
    ) -> None:
        if timeout_secs <= 0:
            await asyncio.sleep(0)
            return

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_secs
        while not is_ready():
            remaining_secs = deadline - loop.time()
            if remaining_secs <= 0:
                break
            previous_version = self.version
            await self.wait_for_change(
                since_version=previous_version,
                timeout=remaining_secs,
            )

    async def wait_for_change(
        self, *, since_version: int | None = None, timeout: float | None = None
    ) -> int:
        try:
            async with asyncio.timeout(timeout):
                if since_version is None:
                    since_version = self.version
                while self.version <= since_version:
                    await self._changed_evt.wait()
        except TimeoutError:
            pass
        return self.version


class VersionedState(VersionTrackerMixin, BaseModel):
    version: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _changed_evt: asyncio.Event = PrivateAttr(default_factory=asyncio.Event)
    _parent_version_tracker: VersionTracker | None = PrivateAttr(default=None)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        BaseModel.__init__(self, *args, **kwargs)

    def merge(self, other: "VersionedState") -> None:
        for field_name in self.__class__.model_fields:
            if field_name in ("version", "updated_at"):
                continue
            if field_name in other.model_fields_set:
                setattr(self, field_name, getattr(other, field_name))

    def __setattr__(self, name: str, value: Any) -> None:
        current = getattr(self, name, object())
        super().__setattr__(name, value)

        private_attrs = getattr(self, "__pydantic_private__", None)
        if (
            name
            not in ("version", "updated_at", "_changed_evt", "_parent_version_tracker")
            and current != value
            and private_attrs is not None
            and "_changed_evt" in private_attrs
        ):
            self.mark_updated()


KeyT = TypeVar("KeyT", bound=Hashable)
ValueT = TypeVar("ValueT")


class TrackedDict(VersionTrackerMixin, SortedDict[KeyT, ValueT], Generic[KeyT, ValueT]):
    # Tracking is attached for values created through key access. Callers that
    # replace values directly must wire parent tracking themselves.
    def __init__(
        self,
        default_factory: Callable[[KeyT], ValueT],
        tracker: VersionTracker | None = None,
    ) -> None:
        super().__init__()
        self.default_factory = default_factory
        self._parent_version_tracker = tracker

    def __getitem__(self, key: KeyT) -> ValueT:
        if key not in self:
            new_value = self.default_factory(key)
            self[key] = new_value
            if isinstance(new_value, VersionTrackerMixin):
                new_value._parent_version_tracker = self
            self.mark_updated()
        return super().__getitem__(key)
