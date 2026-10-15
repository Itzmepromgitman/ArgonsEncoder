import asyncio

from bot.utils import callback_ui
from bot.utils.callback_ui import answer_once, guard_callback_answers


class FakeQuery:
    def __init__(self, qid="q1"):
        self.id = qid
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def _run(coro):
    return asyncio.run(coro)


def setup_function(_):
    callback_ui._state.clear()


def test_empty_answer_is_replaced_by_later_informative_text():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def handler(client, callback_query):
            await answer_once(callback_query)
            await answer_once(callback_query, "Saved ✅")

        await handler(None, query)
        return query.answers

    assert _run(scenario()) == [("Saved ✅", False)]


def test_empty_only_handler_is_flushed_on_exit():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def handler(client, callback_query):
            await answer_once(callback_query)

        await handler(None, query)
        return query.answers

    assert _run(scenario()) == [(None, False)]


def test_second_informative_answer_is_dropped():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def handler(client, callback_query):
            await answer_once(callback_query, "first")
            await answer_once(callback_query, "second")

        await handler(None, query)
        return query.answers

    assert _run(scenario()) == [("first", False)]


def test_alert_is_informative_even_without_text():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def handler(client, callback_query):
            await answer_once(callback_query, None, show_alert=True)

        await handler(None, query)
        return query.answers

    assert _run(scenario()) == [(None, True)]


def test_nested_handlers_flush_emptily_once():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def inner(client, callback_query):
            await answer_once(callback_query)
            await answer_once(callback_query, "inner")

        @guard_callback_answers
        async def outer(client, callback_query):
            await answer_once(callback_query)
            await inner(client, callback_query)

        await outer(None, query)
        return query.answers

    assert _run(scenario()) == [("inner", False)]


def test_nested_empty_only_flushes_once_at_outermost_exit():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def inner(client, callback_query):
            await answer_once(callback_query)

        @guard_callback_answers
        async def outer(client, callback_query):
            await answer_once(callback_query)
            await inner(client, callback_query)

        await outer(None, query)
        return query.answers

    assert _run(scenario()) == [(None, False)]


def test_unguarded_empty_answer_is_sent_immediately():
    query = FakeQuery()

    async def scenario():
        await answer_once(query)
        return query.answers

    assert _run(scenario()) == [(None, False)]


def test_informative_text_is_truncated_to_telegram_limit():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def handler(client, callback_query):
            await answer_once(callback_query, "x" * 300)

        await handler(None, query)
        return query.answers

    (text, show_alert), = _run(scenario())
    assert len(text) == callback_ui.MAX_TOAST_LEN
    assert show_alert is False


def test_state_is_released_after_handler_completes():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def handler(client, callback_query):
            await answer_once(callback_query)

        await handler(None, query)

    _run(scenario())
    assert callback_ui._state == {}


def test_state_is_released_even_when_handler_raises():
    query = FakeQuery()

    async def scenario():
        @guard_callback_answers
        async def handler(client, callback_query):
            await answer_once(callback_query)
            raise RuntimeError("boom")

        try:
            await handler(None, query)
        except RuntimeError:
            pass
        return query.answers

    assert _run(scenario()) == [(None, False)]
    assert callback_ui._state == {}


def test_guard_is_idempotent():
    async def handler(client, callback_query):
        await callback_query.answer()

    guarded = guard_callback_answers(handler)
    assert guard_callback_answers(guarded) is guarded
