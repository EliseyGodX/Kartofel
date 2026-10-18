from enum import StrEnum


class KartofelModeEnum(StrEnum):
    SLEEP = 'sleep'
    STEADY = 'steady'
    RAMP = 'ramp'
    SPIKE = 'spike'


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
