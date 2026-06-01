import asyncio

from amp.versioned import VersionedState, VersionTrackerMixin


class ExampleState(VersionedState):
    name: str | None = None


class ParentTracker(VersionTrackerMixin):
    pass


def test_versioned_state_marks_updates_and_propagates_to_parent() -> None:
    parent = ParentTracker()
    state = ExampleState()
    state._parent_version_tracker = parent

    state.name = "Kitchen"

    assert state.version == 1
    assert parent.version == 1

    state.name = "Kitchen"

    assert state.version == 1
    assert parent.version == 1


def test_versioned_state_merge_copies_known_values() -> None:
    parent = ParentTracker()
    state = ExampleState()
    state._parent_version_tracker = parent

    state.merge(ExampleState(name="Kitchen"))

    assert state.name == "Kitchen"
    assert state.version == 1
    assert parent.version == 1

    state.merge(ExampleState())

    assert state.name == "Kitchen"
    assert state.version == 1
    assert parent.version == 1

    state.merge(ExampleState(name=None))

    assert state.name is None
    assert state.version == 2
    assert parent.version == 2


def test_wait_for_change_wakes_on_update() -> None:
    async def scenario() -> None:
        state = ExampleState()
        waiter = asyncio.create_task(state.wait_for_change(timeout=1))

        await asyncio.sleep(0)
        state.name = "Kitchen"

        assert await waiter == 1

    asyncio.run(scenario())


def test_wait_for_change_returns_current_version_on_timeout() -> None:
    async def scenario() -> None:
        state = ExampleState()

        assert await state.wait_for_change(timeout=0) == 0

    asyncio.run(scenario())
