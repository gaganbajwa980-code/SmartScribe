from dataclasses import dataclass, field
from enum import Enum
import time
import uuid
from typing import Dict, Any, List, Optional
from backend.assistant.permission_manager import RiskLevel

class PlanStatus(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL_SUCCESS = "partial_success"

class StepStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    VERIFICATION_FAILED = "verification_failed"
    UNCONFIRMED = "unconfirmed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

@dataclass
class PlanStep:
    step_id: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    description: str = ""
    risk_level: RiskLevel = RiskLevel.LOW_RISK_ACTION
    dependencies: List[str] = field(default_factory=list)
    expected_result: str = ""
    verification_requirement: str = "exact_target_match_check"
    status: StepStatus = StepStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    verified: bool = False
    reused: bool = False
    context_observation: Optional[str] = None
    allow_unconfirmed: bool = False

    def __post_init__(self):
        if not self.description and self.reason:
            self.description = self.reason
        elif not self.reason and self.description:
            self.reason = self.description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "reason": self.reason,
            "description": self.description or self.reason,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else str(self.risk_level),
            "dependencies": self.dependencies,
            "expected_result": self.expected_result,
            "verification_requirement": self.verification_requirement,
            "status": self.status.value if isinstance(self.status, StepStatus) else str(self.status),
            "result": self.result,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 2),
            "verified": self.verified,
            "reused": self.reused,
            "context_observation": self.context_observation,
            "allow_unconfirmed": self.allow_unconfirmed
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        risk_str = data.get("risk_level", "low_risk_action")
        try:
            risk = RiskLevel(risk_str)
        except Exception:
            risk = RiskLevel.LOW_RISK_ACTION

        status_str = data.get("status", "pending")
        try:
            st = StepStatus(status_str)
        except Exception:
            st = StepStatus.PENDING

        return cls(
            step_id=data.get("step_id", ""),
            tool=data.get("tool", ""),
            arguments=data.get("arguments", {}),
            reason=data.get("reason", "") or data.get("description", ""),
            description=data.get("description", "") or data.get("reason", ""),
            risk_level=risk,
            dependencies=data.get("dependencies", []),
            expected_result=data.get("expected_result", ""),
            verification_requirement=data.get("verification_requirement", "exact_target_match_check"),
            status=st,
            result=data.get("result"),
            error=data.get("error"),
            duration_ms=data.get("duration_ms", 0.0),
            verified=data.get("verified", False),
            reused=data.get("reused", False),
            context_observation=data.get("context_observation"),
            allow_unconfirmed=data.get("allow_unconfirmed", False)
        )

@dataclass
class TaskPlan:
    user_query: str
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    plan_id: str = field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:8]}")
    status: PlanStatus = PlanStatus.DRAFT
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    current_step_index: int = 0
    requires_approval: bool = False
    approval_token: Optional[str] = None
    error_message: Optional[str] = None
    context_snapshot: Optional[Dict[str, Any]] = None
    context_decisions: List[Dict[str, Any]] = field(default_factory=list)
    context_used: List[str] = field(default_factory=list)
    is_adapted: bool = False
    adaptation_reason: Optional[str] = None
    original_steps: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "user_query": self.user_query,
            "goal": self.goal,
            "status": self.status.value if isinstance(self.status, PlanStatus) else str(self.status),
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "current_step_index": self.current_step_index,
            "total_steps": len(self.steps),
            "requires_approval": self.requires_approval,
            "approval_token": self.approval_token,
            "error_message": self.error_message,
            "context_snapshot": self.context_snapshot,
            "context_decisions": self.context_decisions,
            "context_used": self.context_used,
            "is_adapted": self.is_adapted,
            "adaptation_reason": self.adaptation_reason,
            "original_steps": self.original_steps
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskPlan":
        st_str = data.get("status", "draft")
        try:
            st = PlanStatus(st_str)
        except Exception:
            st = PlanStatus.DRAFT

        steps = [PlanStep.from_dict(s) for s in data.get("steps", [])]
        return cls(
            plan_id=data.get("plan_id", f"plan_{uuid.uuid4().hex[:8]}"),
            user_query=data.get("user_query", ""),
            goal=data.get("goal", ""),
            steps=steps,
            status=st,
            created_at=data.get("created_at", time.time()),
            completed_at=data.get("completed_at"),
            current_step_index=data.get("current_step_index", 0),
            requires_approval=data.get("requires_approval", False),
            approval_token=data.get("approval_token"),
            error_message=data.get("error_message"),
            context_snapshot=data.get("context_snapshot"),
            context_decisions=data.get("context_decisions", []),
            context_used=data.get("context_used", []),
            is_adapted=data.get("is_adapted", False),
            adaptation_reason=data.get("adaptation_reason"),
            original_steps=data.get("original_steps")
        )

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def is_terminal(self) -> bool:
        return self.status in (
            PlanStatus.SUCCEEDED,
            PlanStatus.FAILED,
            PlanStatus.CANCELLED,
            PlanStatus.PARTIAL_SUCCESS
        )


@dataclass
class UserGoal:
    """
    Structured intermediate representation for natural-language requests.
    Bridges natural language understanding with deterministic / planned execution.
    """
    intent: str
    action: str
    target: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    constraints: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    confidence: float = 1.0
    requires_clarification: bool = False
    clarification_prompt: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "action": self.action,
            "target": self.target,
            "parameters": dict(self.parameters),
            "constraints": list(self.constraints),
            "dependencies": list(self.dependencies),
            "confidence": round(self.confidence, 3),
            "requires_clarification": self.requires_clarification,
            "clarification_prompt": self.clarification_prompt
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserGoal":
        return cls(
            intent=data.get("intent", ""),
            action=data.get("action", ""),
            target=data.get("target"),
            parameters=data.get("parameters", {}),
            constraints=data.get("constraints", []),
            dependencies=data.get("dependencies", []),
            confidence=data.get("confidence", 1.0),
            requires_clarification=data.get("requires_clarification", False),
            clarification_prompt=data.get("clarification_prompt")
        )

