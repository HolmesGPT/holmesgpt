from typing import List, Optional

import yaml
from pydantic import BaseModel


class RobustaSkillInstruction(BaseModel):
    """Supabase-hosted skill instruction from the HolmesRunbooks table."""

    id: str
    # Defaults to "": a skill may be scoped by `alerts` INSTEAD of symptoms (the UI validates
    # "either"), so requiring symptoms dropped every alert-only skill.
    symptom: str = ""
    title: str
    instruction: Optional[str] = None
    # GroupedIssues.aggregation_key values this skill is scoped to; empty means all alerts.
    # The UI's picker offers exactly these (getRecentAlertNames), so comparing against an
    # issue's own aggregation_key is exact by construction.
    alerts: List[str] = []

    class _LiteralDumper(yaml.SafeDumper):
        pass

    @staticmethod
    def _repr_str(dumper, s: str):
        s = s.replace("\\n", "\n")
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", s, style="|" if "\n" in s else None
        )

    _LiteralDumper.add_representer(str, _repr_str)  # type: ignore

    def pretty(self) -> str:
        try:
            data = self.model_dump(exclude_none=True)
        except AttributeError:
            data = self.dict(exclude_none=True)
        return yaml.dump(
            data, Dumper=self._LiteralDumper, sort_keys=False, allow_unicode=True
        )
