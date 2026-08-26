# Copyright (c) 2026, PrintTally -- MIT licensed, see the repo's LICENSE file.
from . import PrintTallyExtension


def getMetaData():
    return {}


def register(app):
    return {"extension": PrintTallyExtension.PrintTallyExtension(app)}
