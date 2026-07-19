from pydantic import BaseModel, Field
from typing import List, Optional

class ViolationItem(BaseModel):
    file_path: str = Field(description="The path to the YAML file containing the error.")
    resource_kind: str = Field(description="Deployment, Service, Secret, ConfigMap, or CronJob")
    resource_name: str = Field(description="The metadata.name identifier of the resource.")
    severity: str = Field(description="Must be CRITICAL (Blocks deployment), WARNING (High risk), or INFO")
    finding: str = Field(description="Detailed architectural or security explanation of the issue.")
    remediation: str = Field(description="Provide exact corrected YAML block snippet or exact action required.")
    target_line: int = Field(default=1, description="The exact integer line number in the file where this configuration block or error resides.")

class AgentReviewResult(BaseModel):
    agent_name: str = Field(description="Name of the specialized reviewing agent.")
    violations: List[ViolationItem] = Field(default_factory=list)

class FinalConsolidatedReview(BaseModel):
    verdict: str = Field(description="Must strictly be either 'APPROVE' or 'REQUEST_CHANGES'")
    executive_summary: str = Field(description="High-level systemic summary of the deployment risks.")
    all_violations: List[ViolationItem] = Field(default_factory=list)