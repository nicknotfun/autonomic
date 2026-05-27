# Internal routing types for direct-amplifier endpoints in the unified client.
from __future__ import annotations

from dataclasses import dataclass

from .amplifier import MirageAmplifier


@dataclass(frozen=True)
class DirectEndpoint:
    amplifier: MirageAmplifier
    host: str
    device_id: str | None = None
    output_start: int = 1
    native_output_start: int = 1
    output_count: int = 8
    source_count: int = 8
    source_base: int = 0
    model_byte: int | None = None

    @property
    def output_end(self) -> int:
        return self.output_start + self.output_count - 1

    @property
    def native_output_end(self) -> int:
        return self.native_output_start + self.output_count - 1

    def owns_global_output(self, output: int) -> bool:
        return self.output_start <= output <= self.output_end

    def local_output(self, output: int) -> int:
        if not self.owns_global_output(output):
            raise ValueError(f"output {output} is not handled by direct amp {self.host}")
        return self.native_output_start + output - self.output_start

    def global_output(self, output: int) -> int:
        return self.output_start + output - self.native_output_start
