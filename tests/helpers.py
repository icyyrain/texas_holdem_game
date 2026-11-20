"""Fakes shared by the room and lobby tests."""

import heapq
import itertools
import random

from holdem import protocol
from holdem.core.cards import Deck


class FakeClock:
    """Collects scheduled callbacks so tests decide when time passes."""

    def __init__(self):
        self.now = 0.0
        self.pending = []  # heap of (due, order, callback)
        self._order = itertools.count()

    def time(self):
        return self.now

    def schedule(self, delay, callback):
        heapq.heappush(self.pending, (self.now + delay, next(self._order), callback))

    def step(self):
        """Jump to the next callback and run it; False if nothing is scheduled."""
        if not self.pending:
            return False
        due, _, callback = heapq.heappop(self.pending)
        self.now = max(self.now, due)
        callback()
        return True

    def run(self, limit=10_000):
        """Run everything due now, including what those callbacks schedule with no delay."""
        self.advance(0, limit)

    def advance(self, seconds, limit=10_000):
        """Let ``seconds`` pass, running callbacks as they come due."""
        target = self.now + seconds
        for _ in range(limit):
            if not self.pending or self.pending[0][0] > target:
                self.now = target
                return
            self.step()
        raise AssertionError("callbacks kept scheduling more callbacks")


class Client:
    def __init__(self):
        self.messages = []

    def deliver(self, message):
        # Everything the server sends must survive the wire.
        self.messages.append(protocol.decode_server(protocol.encode(message)))

    def of_type(self, kind):
        return [message for message in self.messages if message["type"] == kind]

    def last(self, kind):
        return self.of_type(kind)[-1]

    def events(self, kind=None):
        return [m["event"] for m in self.of_type("event") if kind is None or m["event"]["type"] == kind]


def seeded_decks(seed=0):
    rng = random.Random(seed)

    def deck():
        cards = Deck.standard()
        cards.shuffle(rng)
        return cards

    return deck
