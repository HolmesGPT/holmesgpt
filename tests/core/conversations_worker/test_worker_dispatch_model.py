"""The model key on a dispatched user_message decides which LLM runs.

Relay's alert-triage dispatcher (relay#736) names a model in the seq-1
user_message only when the account default is a model defined on the
customer's own agents; a Robusta-hosted default is omitted so the registry's
account fallback (the model relay flags is_default in the models payload)
decides. These pin the worker half of that contract:

  * a named model must reach ChatRequest untouched — losing it here silently
    reruns the investigation on the account/platform default, which is exactly
    the reported bug;
  * an omitted key must stay None (not become '' or a guess), because None is
    what routes get_model_params to the account-fallback path.
"""

from tests.core.conversations_worker.test_worker_edge_cases import (
    _bare_worker,
    _run_process,
    _task,
)


def _dispatch_event(data):
    return [{"event": "user_message", "data": data, "ts": "1"}]


def _chat_request(run_chat):
    run_chat.assert_called_once()
    return run_chat.call_args.args[1]


def test_dispatched_model_reaches_the_chat_request():
    """A customer-owned default named by relay's dispatcher must be the model
    the chat runs on — relay has no other channel to express it."""
    worker = _bare_worker()
    events = _dispatch_event({"ask": "investigate", "model": "my-azure-gpt4"})

    run_chat = _run_process(worker, _task(), events)

    assert _chat_request(run_chat).model == "my-azure-gpt4"


def test_dispatch_without_model_leaves_the_registry_to_decide():
    """Relay omits the key for a Robusta-hosted default. None (not '') is what
    sends get_model_params down the account-fallback path."""
    worker = _bare_worker()
    events = _dispatch_event({"ask": "investigate"})

    run_chat = _run_process(worker, _task(), events)

    assert _chat_request(run_chat).model is None
