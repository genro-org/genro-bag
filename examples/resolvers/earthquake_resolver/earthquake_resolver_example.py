# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0

"""Earthquake logger with application-owned polling.

The application explicitly fetches and publishes each feed. Bag subscriptions
process the published data; Bag and its resolvers do not own a scheduler.

Usage:
    pip install genro-bag[contrib-resolvers]
    python earthquake_resolver_example.py
"""

import asyncio

from genro_toolbox import smartawait

from genro_bag import Bag
from genro_bag.resolvers.contrib import EarthquakeResolver

LOG_TEMPLATE = "  [{tag}] {where}/{event_id}.{version} M{mag} -- {place}"


class EarthquakeLogger:
    """Monitors USGS earthquake feed with versioning and logging.

    Args:
        interval: Seconds between feed checks.
    """

    def __init__(self, interval: int = 10):
        self.interval = interval
        self.bag = Bag()
        self.feed_resolver = EarthquakeResolver()
        self.bag["feed.content"] = None
        self.bag["feed"].subscribe("parser", update=self.process_feed)
        self.bag["quakes"] = Bag()
        self.bag["quakes"].subscribe("logger", insert=self.log_event)

    def process_feed(self, node=None, pathlist=None, oldvalue=None, evt=None, reason=None):
        """Process raw feed into versioned quakes."""
        quakes = self.bag["quakes"]

        for fid, attrs, updated in node.value["features"].query("#k,#a,#a.updated"):
            key = f"{fid[:2]}.{fid[2:]}"
            if key not in quakes:
                quakes[key] = Bag()
            elif quakes[key].nodes[-1].attr['updated'] == updated:
                continue
            event_bag = quakes[key]
            version = f"{len(event_bag):02d}"
            event_bag.set_item(version, None, **attrs)

    def log_event(self, node=None, pathlist=None, **_kw):
        """Log new or updated earthquake events."""
        if not node.attr:
            return
        record = dict(node.attr)
        record.setdefault("place", "?")
        record.setdefault("mag", "?")
        record["where"] = pathlist[0]
        record["event_id"] = pathlist[1]
        record["version"] = node.label
        record["tag"] = "New Quake" if node.label == "00" else "Update Quake"
        print(LOG_TEMPLATE.format(**record))

    def run(self):
        try:
            asyncio.run(self.main())
        except KeyboardInterrupt:
            print("\nStopped.")

    async def main(self):
        print("Listening for new earthquakes...")
        print("Press Ctrl+C to stop.\n")
        while True:
            self.feed_resolver.reset()
            value = await smartawait(self.feed_resolver())
            self.bag['feed.content'] = value
            await asyncio.sleep(self.interval)


if __name__ == "__main__":
    EarthquakeLogger(interval=10).run()
