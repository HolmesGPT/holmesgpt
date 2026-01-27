from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class ScheduledPrompt(BaseModel):
    id: str
    scheduled_prompt_definition_id: Optional[str] = None
    account_id: str
    cluster_name: str
    model_name: str
    prompt: Dict[str, Any]
    status: str
    msg: Optional[str] = None
    created_at: datetime
    last_heartbeat_at: Optional[datetime] = None
    metadata: Optional[dict] = None
