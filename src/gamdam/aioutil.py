from __future__ import annotations
from collections.abc import Callable, Mapping
from typing import Any, Optional
from anyio import EndOfStream
from anyio.abc import ObjectReceiveStream
from linesep import SplitterEmptyError, get_newline_splitter


class LineReceiveStream(ObjectReceiveStream[str]):
    """
    Stream wrapper that splits strings from ``transport_stream`` on newlines
    and returns each line individually.  Requires the linesep_ package.

    .. _linesep: https://github.com/jwodder/linesep
    """

    def __init__(
        self, transport_stream: ObjectReceiveStream[str], newline: Optional[str] = None
    ) -> None:
        """
        :param transport_stream: any `str`-based receive stream
        :param newline:
            controls how universal newlines mode works; has the same set of
            allowed values and semantics as the ``newline`` argument to
            `open()`
        """
        self._stream = transport_stream
        self._splitter = get_newline_splitter(newline, retain=True)

    async def receive(self) -> str:
        while not self._splitter.nonempty and not self._splitter.closed:
            try:
                self._splitter.feed(await self._stream.receive())
            except EndOfStream:
                self._splitter.close()
        try:
            return self._splitter.get()
        except SplitterEmptyError:
            raise EndOfStream()

    async def aclose(self) -> None:
        await self._stream.aclose()

    @property
    def extra_attributes(self) -> Mapping[Any, Callable[[], Any]]:
        return self._stream.extra_attributes
