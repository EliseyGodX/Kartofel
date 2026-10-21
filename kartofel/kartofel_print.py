
from abc import ABC, abstractmethod
from datetime import datetime
from time import monotonic
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from kartofel.enums import KartofelModeEnum, KartofelPrintStateEnum
from kartofel.exc import KartofelPrintClosedError, KartofelPrintNotStartedError

if TYPE_CHECKING:
    import queue
    import threading

    from kartofel.progress import KartofelProgress
    from kartofel.settings import KartofelSettings


class BaseKartofelPrint(ABC):

    @abstractmethod
    def __init__(
        self,
        kartofel_settings: KartofelSettings,
        progress: KartofelProgress,
        progress_lock: threading.Lock,
        errors_que: queue.Queue,
        print_event: threading.Event,
        r_print_event: threading.Event,
        emergency_event: threading.Event,
    ) -> None:
        ...

    @abstractmethod
    def thread(self) -> None:
        ...


class KartofelPrint(BaseKartofelPrint):

    def __init__(
        self,
        kartofel_settings: KartofelSettings,
        progress: KartofelProgress,
        progress_lock: threading.Lock,
        errors_que: queue.Queue,
        print_event: threading.Event,
        r_print_event: threading.Event,
        emergency_event: threading.Event,
    ) -> None:
        self.console = Console()
        self.kartofel_settings = kartofel_settings.model_dump()
        self.kartofel_settings.pop('modes')
        self.modes: list[KartofelModeEnum] = []
        for mode in kartofel_settings.modes:
            self.modes.append(mode.mode)
        self.tick = kartofel_settings.print_thread_tick
        self.progress = progress
        self.progress_lock = progress_lock
        self.errors_que = errors_que
        self.print_event = print_event
        self.print_event.clear()
        self.r_print_event = r_print_event
        self.r_print_event.clear()
        self.emergency_event = emergency_event
        self.__is_started = False
        self.started_at: datetime | None = None
        self.started_monotonic: float | None = None
        self.live: Live | None = None

    def thread(self) -> None:
        if self.__is_started:
            raise KartofelPrintClosedError

        while True:
            if self.emergency_event.is_set():
                if self.live:
                    self.live.stop()
                break

            self.print_event.wait(self.tick)
            self.print_event.clear()

            while not self.errors_que.empty():
                self.print_error(self.errors_que.get_nowait())

            with self.progress_lock:
                state = self.progress.state

            match state:
                case KartofelPrintStateEnum.START:
                    if not self.__is_started:
                        self.__is_started = True
                        self.start()
                        self.started_at = datetime.now().astimezone()
                        self.started_monotonic = monotonic()
                        self.r_print_event.set()
                case KartofelPrintStateEnum.MODE:
                    self.mode()
                case KartofelPrintStateEnum.CLOSE:
                    self.close()
                    self.r_print_event.set()
                    break

    def start(self) -> None:
        self.live = Live(
            self.render(),
            console=self.console,
            refresh_per_second=10,
            transient=False,
        )
        self.live.start()

    def mode(self) -> None:
        if not self.live:
            raise KartofelPrintNotStartedError
        self.live.update(self.render())

    def close(self) -> None:
        if not self.live:
            raise KartofelPrintNotStartedError
        finished_at = datetime.now().astimezone()
        elapsed = (
            monotonic() - self.started_monotonic
            if self.started_monotonic is not None
            else 0.0
        )
        minutes, seconds = divmod(elapsed, 60)
        self.live.update(
            self.render(
                is_finished=True,
                finished_at=finished_at,
                elapsed=f'{int(minutes)}m {seconds:.2f}s',
            ),
        )
        self.live.stop()

    def render(
        self,
        is_finished: bool = False,
        finished_at: datetime | None = None,
        elapsed: str | None = None,
    ) -> Table:
        with self.progress_lock:
            progress = self.progress
            mode = progress.mode
            requests_count = progress.requests_count
            total_requests_count = progress.total_requests_count
            total_duration = progress.total_duration

        accent_style = 'green' if is_finished else 'cyan'

        table = Table(
            box=box.DOUBLE_EDGE,
            show_header=False,
            show_footer=True,
            show_edge=True,
            show_lines=False,
            padding=(0, 1),
            border_style='green' if is_finished else 'bright_black',
        )
        table.add_column(
            'Parameter',
            style=accent_style,
            no_wrap=True,
            width=18,
        )
        table.add_column('Value', ratio=1, overflow='fold')

        status_row = Table.grid(expand=True, padding=0)
        status_row.add_column(ratio=1)
        status_row.add_row(
            Text(
                '\nCOMPLETED\n' if is_finished else '\nRUNNING\n',
                style=(
                    'bold white on green'
                    if is_finished
                    else 'bold black on white'
                ),
                justify='center',
            ),
        )

        table.add_row(
            status_row,
            Text('\nKartofel\n', justify='center', style='bold'),
        )
        table.add_section()

        config = Text()
        for key, value in self.kartofel_settings.items():
            config.append(f'{key}: ')
            config.append(self.format_value(value), style='bold')
            config.append('\n')
        config.append('\n')
        mode_index = 0
        for mode_item in self.modes:
            mode_index += 1
            config.append(f'{mode_item.value.upper()} -> ', style='bold')
            if mode_index == 6:  # ruff: ignore[magic-value-comparison]
                mode_index = 0
                config.append('\n')
        config = config[:-4]

        table.add_row(
            Text('CONFIGURATION', style='bold'),
            config,
        )

        if not is_finished:
            progress_lines = []
            if mode is not None:
                progress_lines.append(
                    Text.from_markup(f'Mode: [bold]{mode.upper()}[/]'),
                )

            if (
                requests_count is not None
                and total_requests_count is not None
            ):
                safe_total = max(total_requests_count, 0)
                safe_completed = min(
                    max(requests_count, 0),
                    safe_total,
                )
                progress_lines.append(
                    Text(
                        'Requests: ',
                    ).append(
                        f'{requests_count:,} / {total_requests_count:,}',
                        style='bold',
                    ),
                )

                if safe_total > 0:
                    progress_lines.append(
                        ProgressBar(
                            total=safe_total,
                            completed=safe_completed,
                            width=32,
                            complete_style='green',
                            finished_style='green',
                        ),
                    )
                    progress_lines.append('\n')
            else:
                progress_lines.append(
                    Text(
                        'Requests: ',
                    ).append(
                        'N/A',
                        style='bold',
                    ),
                )

            progress_lines.append(
                Text(
                    'Duration: ',
                ).append(
                    f'{total_duration:.2f}s'
                    if total_duration is not None
                    else 'N/A',
                    style='bold',
                ),
            )

            if progress.additional_mode_parameters:
                for key, value in progress.additional_mode_parameters.items():
                    line = Text()
                    line.append(f'{key}: ')
                    line.append(str(value), style='bold')
                    progress_lines.append(line)

            table.add_section()
            table.add_row(
                Text('CURRENT PROGRESS', style='bold'),
                Group(*progress_lines),
            )

        footer_lines = []
        if self.started_at is not None:
            footer_lines.append(
                Text(
                    'Started: ',
                ).append(
                    f'{self.started_at:%Y-%m-%d %H:%M:%S}',
                ),
            )
        if is_finished:
            if finished_at is not None:
                footer_lines.append(
                    Text(
                        'Finished: ',
                    ).append(
                        f'{finished_at:%Y-%m-%d %H:%M:%S}',
                        style='bold',
                    ),
                )
            if elapsed is not None:
                footer_lines.append(
                    Text(
                        'Total duration: ',
                    ).append(
                        elapsed,
                        style='bold',
                    ),
                )

        table.columns[0].footer = Text(
            'TIMING',
            style=accent_style,
        )
        table.columns[1].footer = (
            Group(*footer_lines)
            if footer_lines
            else Text('')
        )

        return table

    def print_error(self, error: str) -> None:
        if not self.live:
            raise KartofelPrintNotStartedError
        self.live.console.print(
            f'[bold red]ERROR:[/] {error}',
        )

    @staticmethod
    def format_value(value: Any) -> str:  # ruff: ignore[any-type]
        if value is None:
            return 'N/A'
        if isinstance(value, dict):
            return ', '.join(
                f'{key}: {item}' for key, item in value.items()
            ) or 'None'
        if isinstance(value, list):
            return ', '.join(map(str, value)) or 'None'
        return str(value)
