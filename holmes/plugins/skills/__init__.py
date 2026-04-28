from typing import List, Optional

import yaml
from pydantic import BaseModel


class RobustaSkillInstruction(BaseModel):
    """Supabase-hosted skill instruction from the HolmesRunbooks table."""

    id: str
    symptom: str
    title: str
    instruction: Optional[str] = None
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
