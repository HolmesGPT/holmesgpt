from holmes.common.env_vars import MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION


def truncate_string(data_str: str) -> str:
    if data_str and len(data_str) > MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION:
        return (
            data_str[:MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION]
            + "-- DATA TRUNCATED TO AVOID HITTING CONTEXT WINDOW LIMITS"
        )
    return data_str


def truncate_evidences_entities_if_necessary(evidence_list: list[dict]):
    if (
        not MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION
        or MAX_EVIDENCE_DATA_CHARACTERS_BEFORE_TRUNCATION <= 0
    ):
        return

    for evidence in evidence_list:
        data = evidence.get("data")
        if data:
            evidence["data"] = truncate_string(str(data))
