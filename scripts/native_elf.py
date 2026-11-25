"""Reuse decoded ELF data while inspecting one unchanged native module."""

import hashlib
import shutil
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path

from common import sha256
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection


class Symbols:
    def __init__(self, table):
        self.table = table
        self.name = table.name
        self.decoded = {}

    def __getitem__(self, key):
        return self.table[key]

    def get_symbol(self, index):
        if index not in self.decoded:
            self.decoded[index] = self.table.get_symbol(index)
        return self.decoded[index]

    def iter_symbols(self):
        return (self.get_symbol(index) for index in range(self.table.num_symbols()))

    @cached_property
    def by_name(self):
        symbols = defaultdict(list)
        for symbol in self.iter_symbols():
            symbols[symbol.name].append(symbol)
        return symbols

    def get_symbol_by_name(self, name):
        matches = self.by_name.get(name)
        return list(matches) if matches else None


class InspectionELF(ELFFile):
    def __init__(self, stream):
        self.sections = {}
        super().__init__(stream)

    def get_section(self, index, type=None):
        if type is not None:
            return super().get_section(index, type)
        if index not in self.sections:
            section = super().get_section(index)
            self.sections[index] = (
                Symbols(section) if isinstance(section, SymbolTableSection) else section
            )
        return self.sections[index]


class ModuleInspection:
    def __init__(self, stream):
        self.stream = stream

    @cached_property
    def digest(self):
        self.stream.seek(0)
        return hashlib.file_digest(self.stream, "sha256").hexdigest()

    @cached_property
    def elf(self):
        self.stream.seek(0)
        return InspectionELF(self.stream)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        elf = self.__dict__.get("elf")
        if elf is not None:
            # Sections reference their ELFFile. Break those references to decoded
            # objects as soon as this inspection is finished.
            elf.sections.clear()


@contextmanager
def inspect_module(module, inspection=None):
    if inspection is not None:
        yield inspection
        return
    with module.open("rb") as stream, ModuleInspection(stream) as inspection:
        yield inspection


def write_verified_binary(path: Path, data: bytes, expected: str):
    """Check the bytes written to the replacement before publishing it."""
    with tempfile.TemporaryDirectory(dir=path.parent) as temporary:
        staged = Path(temporary) / path.name
        staged.write_bytes(data)
        if sha256(staged) != expected:
            raise ValueError(f"Native patch written output failed verification: {path.name}")
        shutil.copystat(path, staged)
        staged.replace(path)
