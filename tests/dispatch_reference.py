"""Independent bounded specification model. No production, SQL or transport imports."""

from dataclasses import dataclass

KNOWLEDGE = (
    "NOT_ATTEMPTED",
    "UNKNOWN",
    "KNOWN_PENDING",
    "CONFIRMED_EFFECT",
    "CONFIRMED_ZERO_EFFECT",
    "BLOCKED",
)


@dataclass
class Model:
    authority: str = "ACTIVE"
    operation: str = "QUEUED"
    lease: bool = False
    attempts: int = 0
    remote_effects: int = 0
    remote_calls: int = 0
    terminal_rejected: bool = False
    knowledge: str = "NOT_ATTEMPTED"
    exposure: int = 0
    operation_duty: str | None = None
    exposure_duty: str | None = None
    reserved: int = 100
    consumed: int = 0
    free: int = 0

    @property
    def active(self):
        return self.authority == "ACTIVE"

    @property
    def reservation_state(self):
        return "EXPOSED_UNRESOLVED" if self.exposure else "RESERVED_UNEXPOSED"

    def step(self, command):
        if command == "claim":
            self.lease = self.attempts == 0
        elif command == "lease_loss":
            self.lease = False
        elif command == "revoke":
            self.authority = "REVOKED"
        elif command == "expire":
            self.authority = "EXPIRED"
        elif command == "ready":
            if not self.lease or self.attempts:
                return False
            self.operation = "READY"
        elif command == "commit":
            if not self.active or not self.lease or self.attempts:
                return False
            self.attempts = 1
            self.knowledge = "UNKNOWN"
            self.operation = "DISPATCH_COMMITTED"
            self.operation_duty = "OPEN"
        elif command == "crash":
            pass  # Durable projections survive process death; no oracle-derived outcome.
        elif command == "effect_lost":
            if not self.attempts or self.remote_calls:
                return False
            self.remote_calls = 1
            self.remote_effects = 1
        elif command == "read":
            if not self.attempts:
                return False
            if self.remote_effects:
                self.knowledge = "CONFIRMED_EFFECT"
                self.operation = "RESOLVED_EFFECT"
                self.operation_duty = "DISCHARGED"
            elif self.terminal_rejected:
                self.knowledge = "CONFIRMED_ZERO_EFFECT"
                self.operation = "RESOLVED_ZERO_EFFECT"
                self.operation_duty = "DISCHARGED"
            elif self.knowledge != "BLOCKED":
                self.operation = "OBSERVING"
                self.operation_duty = "OBSERVING"
        elif command == "conflict_read":
            if not self.attempts:
                return False
            self.knowledge = "BLOCKED"
            self.operation = "OBSERVING"
            self.operation_duty = "VERIFYING"
        elif command == "reject_terminal":
            if not self.attempts or self.remote_calls:
                return False
            self.remote_calls = 1
            self.terminal_rejected = True
        elif command == "disclose":
            if self.exposure:
                return True
            if not self.active or self.knowledge != "CONFIRMED_EFFECT":
                return False
            self.exposure = 1
            self.exposure_duty = "OPEN"
        elif command == "replay":
            return False  # Default create class has no verified replay envelope.
        else:
            raise ValueError(command)
        assert 100 == self.consumed + self.reserved + self.free
        assert self.knowledge in KNOWLEDGE
        return True
