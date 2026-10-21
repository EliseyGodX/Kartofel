from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kartofel.enums import KartofelModeEnum, KartofelPrintStateEnum


@dataclass
class KartofelProgress:
    state: KartofelPrintStateEnum | None = None
    mode: KartofelModeEnum | None = None
    total_duration: float | None = None
    requests_count: int | None = None
    total_requests_count: int | None = None
    additional_mode_parameters: dict[str, str] | None = None
