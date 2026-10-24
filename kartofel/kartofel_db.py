import json
import sqlite3
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from kartofel.exc import KartofelDBClosedError

if TYPE_CHECKING:
    import queue
    import threading

    from kartofel.settings import KartofelSettings
    from kartofel.types import RequestRecord

create_db_sql = """
CREATE TABLE IF NOT EXISTS "settings" (
    "id" INTEGER PRIMARY KEY CHECK (id = 1),
    "base_url" TEXT NOT NULL,
    "connection_limit" INTEGER NOT NULL,
    "ttl_dns_cache" INTEGER NOT NULL,
    "connection_keepalive_timeout" REAL NOT NULL,
    "request_timeout" REAL NOT NULL,
    "min_request_pack_delay" REAL NOT NULL,
    "print_thread_tick" REAL NOT NULL,
    "db_path" TEXT NOT NULL,
    "db_connect_timeout" REAL NOT NULL,
    "db_thread_tick" REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS "modes" (
    "id" INTEGER NOT NULL,
    "mode" TEXT NOT NULL,
    "settings" TEXT NOT NULL,
    PRIMARY KEY("id")
);

CREATE TABLE IF NOT EXISTS "requests" (
    "id" INTEGER NOT NULL,
    "url" TEXT NOT NULL,
    "method" TEXT NOT NULL,
    "params" TEXT,
    "data" TEXT,
    "json_body" TEXT,
    "cookies" TEXT,
    "headers" TEXT,
    "weight" INTEGER NOT NULL,
    "mode_id" INTEGER NOT NULL,
    PRIMARY KEY("id"),
    FOREIGN KEY ("mode_id") REFERENCES "modes"("id")
        ON UPDATE NO ACTION ON DELETE NO ACTION
);

CREATE TABLE IF NOT EXISTS "request_records" (
    "id" INTEGER PRIMARY KEY,
    "is_error" BOOLEAN NOT NULL,
    "is_timeout" BOOLEAN NOT NULL,
    "start" INTEGER NOT NULL,
    "end" INTEGER,
    "status" INTEGER,
    "r_id" INTEGER NOT NULL,
    FOREIGN KEY ("r_id") REFERENCES "requests"("id")
        ON UPDATE NO ACTION ON DELETE NO ACTION
);
"""

settings_sql = """
INSERT INTO settings (
    base_url,
    connection_limit,
    ttl_dns_cache,
    connection_keepalive_timeout,
    request_timeout,
    min_request_pack_delay,
    print_thread_tick,
    db_path,
    db_connect_timeout,
    db_thread_tick
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

modes_sql = """
INSERT INTO modes (
    id,
    mode,
    settings
)
VALUES (?, ?, ?);
"""

requests_sql = """
INSERT INTO requests (
    id,
    url,
    method,
    params,
    data,
    json_body,
    cookies,
    headers,
    weight,
    mode_id
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

request_records_sql = """
INSERT INTO request_records (
    is_error,
    is_timeout,
    start,
    end,
    status,
    r_id
)
VALUES (?, ?, ?, ?, ?, ?);
"""


class BaseKartofelDB(ABC):

    @abstractmethod
    def __init__(
        self,
        kartofel_settings: KartofelSettings,
        request_que: queue.Queue[RequestRecord],
        db_event: threading.Event,
        r_db_event: threading.Event,
        close_db_event: threading.Event,
        emergency_event: threading.Event,
    ) -> None:
        ...

    @abstractmethod
    def launch(self) -> None:
        ...


class KartofelDB(BaseKartofelDB):

    def __init__(
        self,
        kartofel_settings: KartofelSettings,
        request_que: queue.Queue[RequestRecord],
        db_event: threading.Event,
        r_db_event: threading.Event,
        close_db_event: threading.Event,
        emergency_event: threading.Event,
    ) -> None:
        self.settings = kartofel_settings
        self.tick = self.settings.db_thread_tick
        self.request_que = request_que
        self.db_event = db_event
        self.db_event.clear()
        self.r_db_event = r_db_event
        self.r_db_event.clear()
        self.close_db_event = close_db_event
        self.emergency_event = emergency_event
        self.__has_started = False

    def launch(self) -> None:
        if self.__has_started:
            raise KartofelDBClosedError

        self.connect()
        self.create_db()
        self.r_db_event.set()

        while True:
            if self.emergency_event.is_set():
                self.close()
                break

            self.write_request_records()

            if self.close_db_event.is_set():
                self.close()
                self.r_db_event.set()
                break

            self.db_event.wait(self.tick)
            self.db_event.clear()

    def connect(self) -> None:
        self.conn = sqlite3.connect(
            database=self.settings.db_path,
        )
        self.__has_started = True

    def close(self) -> None:
        self.conn.close()

    def create_db(self) -> None:
        with self.conn:
            self.conn.executescript(create_db_sql)

        settings = (
            self.settings.base_url,
            self.settings.connection_limit,
            self.settings.ttl_dns_cache,
            self.settings.connection_keepalive_timeout,
            self.settings.request_timeout,
            self.settings.min_request_pack_delay,
            self.settings.print_thread_tick,
            self.settings.db_path,
            self.settings.db_connect_timeout,
            self.settings.db_thread_tick,
        )
        modes = []
        requests = []
        for i, mode in enumerate(self.settings.modes):
            mode_settings = json.dumps(
                mode.model_dump(
                    mode='json',
                    exclude={'request_packs', 'mode'},
                ),
                ensure_ascii=False,
                allow_nan=False,
            )
            modes.append(
                (
                    i,
                    mode.mode.value,
                    mode_settings,
                    ),
                )
            for pack in mode.request_packs:
                requests.append(
                    (
                        pack.request.id,
                        pack.request.url,
                        pack.request.method.value,
                        pack.request.params,
                        pack.request.data,
                        (
                            json.dumps(
                                pack.request.json_body,
                                ensure_ascii=False,
                                allow_nan=False,
                            )
                            if pack.request.json_body is not None
                            else None
                        ),
                        (
                            json.dumps(
                                dict(pack.request.cookies),
                                ensure_ascii=False,
                                allow_nan=False,
                            )
                            if pack.request.cookies is not None
                            else None
                        ),
                        (
                            json.dumps(
                                dict(pack.request.headers),
                                ensure_ascii=False,
                                allow_nan=False,
                            )
                            if pack.request.headers is not None
                            else None
                        ),
                        pack.weight,
                        i,
                    ),
                )

        with self.conn:
            self.conn.execute(settings_sql, settings)
            self.conn.executemany(modes_sql, modes)
            self.conn.executemany(requests_sql, requests)

    def write_request_records(self) -> None:
        records = []
        while not self.request_que.empty():
            records.append(self.request_que.get_nowait())
        if not records:
            return
        with self.conn:
            self.conn.executemany(request_records_sql, records)
