from datetime import datetime
from time import monotonic
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from kartofel.enums import KartofelPrintStateEnum

if TYPE_CHECKING:
    import threading

    from kartofel.settings import KartofelProgress, KartofelSettings


class KartofelPrint:
    def __init__(
        self,
        kartofel_settings: KartofelSettings,
        progress: KartofelProgress,
        progress_lock: threading.Lock,
        print_event: threading.Event,
        r_print_event: threading.Event,
    ) -> None:
        self.console = Console()
        self.kartofel_settings = kartofel_settings.model_dump()
        for mode in self.kartofel_settings['modes']:
            mode.pop('requests', None)
        self.tick_timeout = kartofel_settings.print_thread_tick_timeout
        self.progress = progress
        self.progress_lock = progress_lock
        self.print_event = print_event
        self.print_event.clear()
        self.r_print_event = r_print_event
        self.r_print_event.clear()
        self.__is_started = False
        self.started_at: datetime | None = None
        self._tarted_monotonic: float | None = None

    def tread(self) -> None:
        while True:
            self.print_event.wait(self.tick_timeout)
            self.print_event.clear()
            with self.progress_lock:
                state = self.progress.state
            match state:
                case KartofelPrintStateEnum.START:
                    if not self.__is_started:
                        self.start()
                        self.__is_started = True
                        self.r_print_event.set()
                case KartofelPrintStateEnum.MODE:
                    self.mode()
                case KartofelPrintStateEnum.CLOSE:
                    self.close()
                    self.r_print_event.set()
                    break

    def start(self) -> None:
        self.started_at = datetime.now().astimezone()
        self._tarted_monotonic = monotonic()
        table = Table(
            title='Test Configuration',
            box=box.ROUNDED,
            show_header=True,
            header_style='bold cyan',
        )
        table.add_column('Setting', style='cyan')
        table.add_column('Value', overflow='fold')
        for key, value in self.kartofel_settings.items():
            table.add_row(key, self._format_value(value))
        self.console.print()
        self.console.print(
            Panel(
                f'[bold green]Test started[/]\n'
                f'Start time: {self.started_at:%Y-%m-%d %H:%M:%S %Z}',
                title='Kartofel',
                border_style='green',
            ),
        )
        self.console.print(table)

    def mode(self) -> None:
        with self.progress_lock:
            progress = self.progress
            if progress is None:
                return
            mode = progress.mode
            requests_count = progress.requests_count
            total_requests_count = progress.total_requests_count
            total_duration = progress.total_duration
        table = Table(title='Current Progress', box=box.ROUNDED)
        table.add_column('Parameter', style='cyan')
        table.add_column('Value')
        table.add_row('Mode', str(mode))
        table.add_row(
            'Requests',
            f'{requests_count}/{total_requests_count}',
        )
        table.add_row('Duration', str(total_duration))
        self.console.print(table)

    def close(self) -> None:
        finished_at = datetime.now().astimezone()
        elapsed = (
            monotonic() - self._tarted_monotonic
            if self._tarted_monotonic is not None
            else 0.0
        )
        minutes, seconds = divmod(elapsed, 60)
        self.console.print(
            Panel(
                f'[bold green]Test completed[/]\n\n'
                f'Started: {self.started_at:%Y-%m-%d %H:%M:%S %Z}'
                if self.started_at is not None
                else '[bold green]Test completed[/]',
                title='Kartofel',
                border_style='green',
            ),
        )
        self.console.print(f'Finished: {finished_at:%Y-%m-%d %H:%M:%S %Z}')
        self.console.print(
            f'Total duration: {int(minutes)}m {seconds:.2f}s',
            style='bold green',
        )

    @staticmethod
    def _format_value(value: Any) -> str:  # ruff: ignore[any-type]
        if value is None:
            return 'N/A'
        if isinstance(value, dict):
            return ', '.join(
                f'{key}: {item}' for key, item in value.items()
            ) or 'None'
        if isinstance(value, list):
            return ', '.join(map(str, value)) or 'None'
        return str(value)
