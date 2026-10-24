import asyncio
import math
import queue
import random
import threading
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from kartofel.exc import (
    KartofelDBTimeoutError,
    KartofelNotUniqueIdError,
    KartofelPrintTimeoutError,
)
from kartofel.kartofel_db import BaseKartofelDB, KartofelDB
from kartofel.kartofel_print import (
    BaseKartofelPrint,
    KartofelPrint,
    KartofelPrintStateEnum,
)
from kartofel.progress import KartofelProgress
from kartofel.settings import (
    DEFAULT_R_ID,
    KartofelModeEnum,
    KartofelRequest,
    KartofelRequestPack,
    KartofelSettings,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from kartofel.types import RequestRecord


class Kartofel:

    def __init__(
        self,
        settings: Any | KartofelSettings,  # ruff: ignore[any-type]
        print_class: type[BaseKartofelPrint] = KartofelPrint,
        db_class: type[BaseKartofelDB] = KartofelDB,
    ) -> None:
        if not isinstance(settings, KartofelSettings):
            self.settings = KartofelSettings.model_validate(settings)
        self.settings = self.update_r_ids(settings)
        self.print_class = print_class
        self.db_class = db_class
        self.request_que: queue.Queue[RequestRecord] = queue.Queue()
        self.errors_que: queue.Queue[str] = queue.Queue()
        self.progress = KartofelProgress()
        self.progress_lock = threading.Lock()
        self.print_event = threading.Event()
        self.r_print_event = threading.Event()
        self.db_event = threading.Event()
        self.r_db_event = threading.Event()
        self.close_db_event = threading.Event()
        self.emergency_event = threading.Event()
        self.tasks: list[asyncio.Task] = []
        self.print_thread: threading.Thread
        self.db_thread: threading.Thread

    async def start(self) -> None:
        self.create_session()
        try:
            self.create_threads()
            await self.modes_launcher()
        except BaseException:
            self.emergency_event.set()
            raise
        else:
            self.set_close_print_state()
            self.close_db()
        finally:
            await asyncio.to_thread(self.print_thread.join)
            await self.close_session()

    ###
    # MODES METHODS
    ###

    async def modes_launcher(self) -> None:
        self.set_mode_print_state()
        for mode in self.settings.modes:
            match mode.mode:
                case KartofelModeEnum.SLEEP:
                    with self.progress_lock:
                        self.progress.mode = KartofelModeEnum.SLEEP
                        self.progress.total_duration = mode.duration
                        self.progress.requests_count = 0
                        self.progress.total_requests_count = 0
                        self.progress.additional_mode_parameters = None
                    await self.sleep_mode(mode.duration)
                case KartofelModeEnum.STEADY:
                    with self.progress_lock:
                        self.progress.mode = KartofelModeEnum.STEADY
                        self.progress.total_duration = mode.duration
                        self.progress.requests_count = 0
                        self.progress.total_requests_count = int(
                            mode.rps * mode.duration,
                        )
                        self.progress.additional_mode_parameters = {
                            'rps': str(mode.rps),
                        }
                    await self.steady_mode(
                        rps=mode.rps,
                        duration=mode.duration,
                        r_pack=mode.request_packs,
                    )
                case KartofelModeEnum.RAMP:
                    with self.progress_lock:
                        self.progress.mode = KartofelModeEnum.RAMP
                        self.progress.total_duration = mode.duration
                        self.progress.requests_count = 0
                        self.progress.total_requests_count = None
                        self.progress.additional_mode_parameters = {
                            'start_rps': str(mode.start_rps),
                            'end_rps': str(mode.end_rps),
                            'rps_step': str(mode.rps_step),
                        }
                    await self.ramp_mode(
                        duration=mode.duration,
                        r_pack=mode.request_packs,
                        start_rps=mode.start_rps,
                        end_rps=mode.end_rps,
                        rps_step=mode.rps_step,
                    )
                case KartofelModeEnum.SPIKE:
                    with self.progress_lock:
                        self.progress.mode = KartofelModeEnum.SPIKE
                        self.progress.total_duration = None
                        self.progress.requests_count = 0
                        self.progress.total_requests_count = mode.count
                        self.progress.additional_mode_parameters = None
                    await self.spike_mode(
                        r_pack=mode.request_packs,
                        count=mode.count,
                    )
            self.print_event.set()
            self.db_event.set()

    async def sleep_mode(self, duration: float) -> None:  # ruff: ignore[no-self-use]
        await asyncio.sleep(duration)

    async def steady_mode(
        self,
        rps: int,
        duration: float,
        r_pack: list[KartofelRequestPack],
    ) -> None:
        await self.scheduler(rps, duration, r_pack)

    async def ramp_mode(
        self,
        duration: float,
        r_pack: list[KartofelRequestPack],
        start_rps: int,
        end_rps: int,
        rps_step: int,
    ) -> None:
        levels = list(range(start_rps, end_rps + 1, rps_step))
        i_duration = duration / len(levels)
        with self.progress_lock:
            self.progress.total_requests_count = int(
                sum(rps * i_duration for rps in levels),
            )
        for rps in levels:
            await self.scheduler(rps, i_duration, r_pack)

    async def spike_mode(
        self,
        r_pack: list[KartofelRequestPack],
        count: int,
    ) -> None:
        requests = self.distribute_requests(r_pack, count)
        await self.requests(requests)

    ###
    # AIOHTTP METHODS
    ###

    def create_session(self) -> None:
        self.session = aiohttp.ClientSession(
            base_url=self.settings.base_url,
            connector=aiohttp.TCPConnector(
                limit=self.settings.connection_limit,
                ttl_dns_cache=self.settings.ttl_dns_cache,
                keepalive_timeout=self.settings.connection_keepalive_timeout,
            ),
            timeout=aiohttp.ClientTimeout(
                total=self.settings.request_timeout,
            ),
            auto_decompress=False,
        )

    async def close_session(self) -> None:
        await self.session.close()

    ###
    # ASYNCIO METHODS
    ###

    async def wait_tasks(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks)
        self.tasks.clear()

    ###
    # THREADS METHODS
    ###

    def create_threads(self) -> None:
        self.print_thread = threading.Thread(
            target=self.thread_print_launch,
            name='Thread-Print',
        )
        self.print_thread.start()
        self.set_start_print_state()

        self.db_thread = threading.Thread(
            target=self.thread_db_launch,
            name='Thread-DB',
        )
        self.close_db_event.clear()
        self.db_thread.start()

    ###
    # PRINT THREAD METHODS
    ###

    def thread_print_launch(self) -> None:
        kprint = self.print_class(
            kartofel_settings=self.settings,
            progress=self.progress,
            progress_lock=self.progress_lock,
            errors_que=self.errors_que,
            print_event=self.print_event,
            r_print_event=self.r_print_event,
            emergency_event=self.emergency_event,
        )
        kprint.launch()

    def block_print(
        self,
        func: Callable,
        timeout: float | None = None,
    ) -> None:
        self.r_print_event.clear()
        with self.progress_lock:
            func()
        if not self.r_print_event.wait(timeout):
            raise KartofelPrintTimeoutError
        self.r_print_event.clear()

    def set_start_print_state(self) -> None:
        def f() -> None:
            self.progress.state = KartofelPrintStateEnum.START
        if self.settings.print_thread_tick < 1:
            timeout = 2
        else:
            timeout = self.settings.print_thread_tick * 2
        self.block_print(f, timeout)

    def set_mode_print_state(self) -> None:
        with self.progress_lock:
            self.progress.state = KartofelPrintStateEnum.MODE

    def set_close_print_state(self) -> None:
        def f() -> None:
            self.progress.state = KartofelPrintStateEnum.CLOSE
        if self.settings.print_thread_tick < 1:
            timeout = 2
        else:
            timeout = self.settings.print_thread_tick * 2
        self.block_print(f, timeout)

    def increment_requests_count(self) -> None:
        with self.progress_lock:
            if self.progress.requests_count is not None:
                self.progress.requests_count += 1

    def put_error(self, error: str) -> None:
        self.errors_que.put(error)

    ###
    # DB THREAD METHODS
    ###

    def thread_db_launch(self) -> None:
        kdb = self.db_class(
            kartofel_settings=self.settings,
            request_que=self.request_que,
            db_event=self.db_event,
            r_db_event=self.r_db_event,
            close_db_event=self.close_db_event,
            emergency_event=self.emergency_event,
        )
        self.r_db_event.clear()
        kdb.launch()
        if not self.r_db_event.wait(self.settings.db_connect_timeout):
            raise KartofelDBTimeoutError

    def close_db(self) -> None:
        self.r_db_event.clear()
        self.close_db_event.set()
        if not self.r_db_event.wait(self.settings.db_connect_timeout):
            raise KartofelDBTimeoutError

    ###
    # REQUESTS METHODS
    ###

    async def request(self, r: KartofelRequest) -> None:
        start = time.perf_counter_ns()
        status = None
        is_error = False
        is_timeout = False
        end = None

        try:
            async with self.session.request(
                method=r.method.value,
                url=r.url,
                params=r.params,
                data=r.data,
                json=r.json_body,
                cookies=r.cookies,
                headers=r.headers,
            ) as response:
                end = time.perf_counter_ns()
                status = response.status
                await response.read()

        except TimeoutError:
            end = time.perf_counter_ns()
            is_timeout = True

        except BaseException as exc:
            is_error = True
            self.put_error(str(exc))

        finally:
            self.request_que.put((is_error, is_timeout, start, end, status, r.id))
            self.increment_requests_count()

    async def requests(
        self,
        rs: tuple[KartofelRequest, ...],
        is_wait_tasks: bool = True,
        ) -> None:
        for r in rs:
            self.tasks.append(asyncio.create_task(self.request(r)))
        if is_wait_tasks:
            await self.wait_tasks()

    async def scheduler(
        self,
        rps: int,
        duration: float,
        r_pack: list[KartofelRequestPack],
    ) -> None:
        pack_delay = max(
            1 / rps,
            self.settings.min_request_pack_delay,
        )
        total_requests = math.ceil(rps * duration)
        iterations = max(1, math.ceil(duration / pack_delay))
        requests = self.distribute_requests(r_pack, total_requests)
        pack_delay_ns = int(pack_delay * 1_000_000_000)
        sent = 0

        start_ns = time.perf_counter_ns()
        for i in range(iterations):
            batch_end = (total_requests * (i + 1)) // iterations
            batch = requests[sent:batch_end]
            sent = batch_end
            if batch:
                await self.requests(batch, is_wait_tasks=False)
            if i + 1 < iterations:
                deadline = start_ns + (i + 1) * pack_delay_ns
                while True:
                    remaining_ns = deadline - time.perf_counter_ns()
                    if remaining_ns <= 0:
                        break
                    await asyncio.sleep(remaining_ns / 1_000_000_000)

        await self.wait_tasks()

    ###
    # STATIC METHODS
    ###

    @staticmethod
    def distribute_requests(
        r_packs: list[KartofelRequestPack],
        count: int,
    ) -> tuple[KartofelRequest, ...]:
        if not r_packs or count == 0:
            return ()
        total_weight = sum(pack.weight for pack in r_packs)
        allocations = []
        for pack in r_packs:
            quantity, remainder = divmod(
                count * pack.weight,
                total_weight,
            )
            allocations.append([quantity, remainder])
        remaining = count - sum(quantity for quantity, _ in allocations)
        for index in sorted(
            range(len(r_packs)),
            key=lambda i: allocations[i][1],
            reverse=True,
        )[:remaining]:
            allocations[index][0] += 1
        requests = []
        for pack, (quantity, _) in zip(r_packs, allocations, strict=True):
            requests.extend(
                pack.request
                for _ in range(quantity)
            )
        random.shuffle(requests)
        return tuple(requests)

    @staticmethod
    def update_r_ids(settings: KartofelSettings) -> KartofelSettings:
        ids = set()
        for mode in settings.modes:
            for pack in mode.request_packs:
                id = pack.request.id  # ruff: ignore[builtin-variable-shadowing]
                if id != DEFAULT_R_ID:
                    if id in ids:
                        raise KartofelNotUniqueIdError
                    ids.add(id)

        i = 1
        for mode in settings.modes:
            for pack in mode.request_packs:
                if pack.request.id == DEFAULT_R_ID:
                    while i in ids:
                        i += 1
                    pack.request.id = i
                    ids.add(i)
                    i += 1

        return settings
