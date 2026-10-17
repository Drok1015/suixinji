"""Keep provider tool protocols out of user-visible streamed text and history."""

import re


_PROTOCOL = re.compile(r"<\s*/?\s*(?:[|｜¦]\s*)+DSML", re.IGNORECASE)


class UnexpectedToolOutput(RuntimeError):
    pass


def contains_tool_protocol(text: str) -> bool:
    return bool(_PROTOCOL.search(text))


class TextGuard:
    def __init__(self):
        self.pending = ""

    def feed(self, text: str) -> str:
        self.pending += text
        clean = []
        while self.pending:
            start = self.pending.find("<")
            if start < 0:
                clean.append(self.pending)
                self.pending = ""
                break
            clean.append(self.pending[:start])
            self.pending = self.pending[start:]
            end = self.pending.find(">")
            candidate = self.pending if end < 0 else self.pending[: end + 1]
            if contains_tool_protocol(candidate):
                raise UnexpectedToolOutput("unexpected_tool_protocol")
            if end < 0 and len(self.pending) < 256:
                # A marker may span several network chunks; never leak its prefix.
                break
            size = 1 if end < 0 else end + 1
            clean.append(self.pending[:size])
            self.pending = self.pending[size:]
        return "".join(clean)

    def finish(self) -> str:
        if contains_tool_protocol(self.pending):
            raise UnexpectedToolOutput("unexpected_tool_protocol")
        remaining, self.pending = self.pending, ""
        return remaining
