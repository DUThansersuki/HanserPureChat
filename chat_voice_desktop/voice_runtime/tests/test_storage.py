import json
import time

import pytest

from voice_runtime.contracts import VoiceJobRequest
from voice_runtime.jobs import VoiceJobManager
from voice_runtime.profiles import RigProfile
from voice_runtime.tests.test_runtime_pipeline import profile, assets, snapshot


def manager(root, **kwargs):
    return VoiceJobManager(data_root=root, profile=profile(),
                           rig=RigProfile(profile_id="rig", revision="r1"),
                           assets=assets(), backend=None, **kwargs)


@pytest.mark.asyncio
async def test_variant_attempt_and_lazy_restart_are_owner_isolated(tmp_path):
    first_manager = manager(tmp_path)
    request = VoiceJobRequest(owner="owner-1", snapshot=snapshot())
    first, _ = await first_manager.create(request)
    await first_manager.cancel(first.job_id, "owner-1")
    second, created = await first_manager.create(request.model_copy(update={"variant_salt": "retry-1"}))
    assert created and first.job_id != second.job_id
    await first_manager.cancel(second.job_id, "owner-1")
    restored = manager(tmp_path, loaded_terminal_jobs=1)
    assert not restored._jobs and not restored._identity_index
    with pytest.raises(KeyError):
        await restored.get(first.job_id, "other-owner")
    assert not restored._jobs
    assert (await restored.get(first.job_id, "owner-1")).job_id == first.job_id
    assert (await restored.get(second.job_id, "owner-1")).job_id == second.job_id
    assert len(restored._jobs) == 1
    replay, created = await restored.create(request)
    assert not created and replay.job_id == first.job_id


@pytest.mark.asyncio
async def test_retention_cleans_only_unreferenced_runtime_assets(tmp_path):
    first_manager = manager(tmp_path)
    old, _ = await first_manager.create(VoiceJobRequest(owner="owner-1", snapshot=snapshot()))
    await first_manager.cancel(old.job_id, "owner-1")
    current, _ = await first_manager.create(VoiceJobRequest(owner="owner-1", snapshot=snapshot().model_copy(update={"reply_id": "new-reply"})))
    await first_manager.cancel(current.job_id, "owner-1")
    runtime = manager(tmp_path)
    shared = tmp_path / "artifacts/shared.wav"
    shared.parent.mkdir(exist_ok=True)
    shared.write_bytes(b"shared audio")
    old_only = tmp_path / "timelines/old.json"
    old_only.parent.mkdir(exist_ok=True)
    old_only.write_text("{}")
    with runtime.catalog.connect() as conn:
        conn.execute("UPDATE jobs SET accessed=? WHERE id=?", (time.time() - 40 * 86400, old.job_id))
        conn.executemany("INSERT INTO artifacts VALUES (?,?)", [(old.job_id, "artifacts/shared.wav"), (current.job_id, "artifacts/shared.wav"), (old.job_id, "timelines/old.json")])
    outside = tmp_path.parent / "keep-user-messages.txt"
    outside.write_text("preserve")
    result = runtime.maintain_storage()
    assert result["deleted_jobs"] == [old.job_id]
    assert shared.is_file() and outside.is_file()
    assert not old_only.exists()
    assert (tmp_path / "jobs" / current.job_id / "state.json").exists()
    with pytest.raises(KeyError):
        await runtime.get(old.job_id, "owner-1")


@pytest.mark.asyncio
async def test_queued_cancellation_is_protected_from_cleanup(tmp_path):
    runtime = manager(tmp_path, retained_jobs=0)
    job, _ = await runtime.create(VoiceJobRequest(owner="owner-1", snapshot=snapshot()))
    await runtime.cancel(job.job_id, "owner-1")
    assert runtime.maintain_storage()["deferred"] == "queued_jobs"
