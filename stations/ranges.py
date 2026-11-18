from dataclasses import dataclass


class InvalidRange(ValueError):
    """The declared calibration interval is invalid."""


@dataclass(frozen=True, slots=True)
class ClosedRange:
    lower: int
    upper: int

    def __post_init__(self) -> None:
        if type(self.lower) is not int or type(self.upper) is not int:
            raise InvalidRange("closed interval endpoints must be integers")
        if self.lower > self.upper:
            raise InvalidRange("closed interval lower endpoint exceeds upper endpoint")

    def contains(self, value: int) -> bool:
        if type(value) is not int:
            raise TypeError("a normalized value must be an integer")
        return self.lower <= value <= self.upper
