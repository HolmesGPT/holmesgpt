class RelayRefusal(Exception):
    """Relay refusing a call on a Robusta-hosted model.

    Relay answers 401 or 403 for more than the account-level opt-out: the
    feature not being enabled, a free-account gate, or a stale session token
    are all refusals too, and the opt-out is just one of them. Whatever the
    reason, this is the platform talking to the user, not a provider failure,
    so it is holmes' own exception rather than one of litellm's: the message
    is relay's sentence verbatim - what `str()` and `args[0]` give - and the
    status is what an HTTP consumer answers with (ROB-1389).
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# The `error_code` the SSE `error` event and the conversation error event carry
# for a refusal, by the status relay refused with. A stream has committed its
# HTTP status before the LLM call runs, so this is where the client reads the
# distinction; the platform and the frontend key on it. 5200-5204 are the
# platform's own Holmes errors and the rate limit, 5205 the conversations
# worker's restart.
RELAY_REFUSAL_ERROR_CODES = {401: 5206, 403: 5207}
