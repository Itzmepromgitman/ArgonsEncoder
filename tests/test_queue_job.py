from bot.func.queue_manager import Job


def test_to_dict_is_primitive_only():
    def evil():
        raise AssertionError("should never be called")

    job = Job(
        job_id="abc12345",
        user_id=1,
        func=evil,  # callables must NOT be persisted
        args=("x",),
        kwargs={"y": object()},
    )
    d = job.to_dict()
    assert "func" not in d
    assert "args" not in d
    assert "kwargs" not in d
    # Everything in the dict must be a BSON-safe primitive.
    import json

    json.dumps(d)  # raises if anything non-serializable sneaks in


def test_from_dict_roundtrip():
    job = Job(
        job_id="id1",
        user_id=42,
        func=None,
        status="running",
        file_name="a.mkv",
        chat_id=-100,
        message_id=7,
        task_type="encode",
        input_file="/dl/a.mkv",
    )
    d = job.to_dict()
    d["status"] = "pending"  # restart normalization
    j2 = Job.from_dict(d)
    assert j2.job_id == "id1"
    assert j2.user_id == 42
    assert j2.task_type == "encode"
    assert j2.file_name == "a.mkv"
    assert j2.func is None  # re-attached later


def test_from_dict_tolerates_missing_fields():
    j = Job.from_dict({"job_id": "x", "user_id": "3"})
    assert j.user_id == 3
    assert j.status == "pending"
    assert j.file_name == "Unknown"
