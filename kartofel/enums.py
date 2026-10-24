from enum import StrEnum


class KartofelModeEnum(StrEnum):
    SLEEP = 'SLEEP'
    STEADY = 'STEADY'
    RAMP = 'RAMP'
    SPIKE = 'SPIKE'


class KartofelHTTPMethodEnum(StrEnum):
    GET = 'GET'
    POST = 'POST'
    PUT = 'PUT'
    PATCH = 'PATCH'
    DELETE = 'DELETE'
    HEAD = 'HEAD'
    OPTIONS = 'OPTIONS'
    TRACE = 'TRACE'
    CONNECT = 'CONNECT'


class KartofelPrintStateEnum(StrEnum):
    START = 'START'
    MODE = 'MODE'
    CLOSE = 'CLOSE'
