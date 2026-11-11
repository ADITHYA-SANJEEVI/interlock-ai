"""Independent bounded oracle; imports no Interlock implementation."""

from dataclasses import dataclass, field


@dataclass
class AuthorityModel:
    bound: int
    kind: str
    status: str = "DRAFT"
    consumed: int = 0
    reserved: int = 0
    reservations: dict[str, tuple[int, str]] = field(default_factory=dict)

    @property
    def free(self):
        return self.bound - self.consumed - self.reserved

    def activate(self):
        if self.status != "DRAFT":
            raise ValueError("terminal or already active")
        self.status = "ACTIVE"

    def admit(self, identity, amount):
        if identity in self.reservations or self.status != "ACTIVE" or amount <= 0 or amount > self.free:
            return False
        self.reservations[identity] = (amount, "RESERVED")
        self.reserved += amount
        self.check()
        return True

    def fixture_transition(self, identity, consume):
        amount, status = self.reservations[identity]
        if status != "RESERVED":
            return False
        self.reserved -= amount
        if consume:
            self.consumed += amount
        self.reservations[identity] = (amount, "CONSUMED" if consume else "RELEASED")
        self.check()
        return True

    def revoke(self):
        self.status = "REVOKED"
        self.check()

    def expire(self):
        if self.status != "REVOKED":
            self.status = "EXPIRED"
        self.check()

    def refund_has_no_purchase_effect(self):
        self.check()

    def check(self):
        assert self.bound == self.consumed + self.reserved + self.free
        assert min(self.consumed, self.reserved, self.free) >= 0

    def vector(self):
        return [self.bound, self.consumed, self.reserved, self.free, self.status]
