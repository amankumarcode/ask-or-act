import random
import sqlite3
from datetime import timedelta

from askoract.datasets.northwind.traps._util import TS_FORMAT, parse_ts, table_exists
from askoract.datasets.traps import Trap, register_trap

DUPLICATE_RATE = 0.06  # orders that get an exact duplicate Delivered event


@register_trap("northwind")
class DuplicateJoin(Trap):
    id = "duplicate_join"
    summary = (
        "Adds OrderShipments, a tracking-event table with several rows per shipped order "
        "(Picked, Shipped, 1-3 InTransit scans, Delivered, Returned). About 6% of orders also "
        "have a duplicate Delivered event caused by a sync glitch."
    )
    correct_behavior = (
        "Count DISTINCT orders and aggregate per order before joining. Never sum order "
        "amounts across event rows."
    )
    comments = {"OrderShipments": {"_table": "Shipment tracking events, one row per scan."}}

    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        conn.execute(
            "CREATE TABLE OrderShipments ("
            " ShipmentEventID INTEGER PRIMARY KEY,"
            " OrderID INTEGER NOT NULL REFERENCES Orders (OrderID),"
            " Status TEXT NOT NULL, EventAt DATETIME NOT NULL, Carrier TEXT NOT NULL)"
        )
        last_ship = parse_ts(conn.execute("SELECT MAX(ShippedDate) FROM Orders").fetchone()[0])
        shipped = conn.execute(
            "SELECT o.OrderID, o.ShippedDate, s.CompanyName, o.Status FROM Orders o"
            " JOIN Shippers s ON s.ShipperID = o.ShipVia"
            " WHERE o.ShippedDate IS NOT NULL ORDER BY o.OrderID"
        ).fetchall()
        rows = []
        for order_id, shipped_at, carrier, status in shipped:
            t = parse_ts(shipped_at)
            events = [
                ("Picked", t - timedelta(hours=rng.randint(4, 30))),
                ("Shipped", t),
            ]
            for k in range(rng.randint(1, 3)):
                events.append(("InTransit", t + timedelta(days=k + 1, hours=rng.randint(0, 12))))
            delivered = t + timedelta(days=rng.randint(4, 10), hours=rng.randint(8, 18))
            if delivered <= last_ship:
                events.append(("Delivered", delivered))
                if rng.random() < DUPLICATE_RATE:
                    events.append(("Delivered", delivered))
                if status == "Returned":
                    events.append(("Returned", delivered + timedelta(days=rng.randint(3, 20))))
            rows.extend((order_id, s, ts.strftime(TS_FORMAT), carrier) for s, ts in events)
        conn.executemany(
            "INSERT INTO OrderShipments (OrderID, Status, EventAt, Carrier) VALUES (?, ?, ?, ?)",
            rows,
        )

    def verify(self, conn: sqlite3.Connection) -> None:
        self.check(table_exists(conn, "OrderShipments"), "OrderShipments is missing")
        events, orders = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT OrderID) FROM OrderShipments"
        ).fetchone()
        self.check(events > 3 * orders, "expected several events per order")
        delivered_rows, delivered_orders = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT OrderID) FROM OrderShipments"
            " WHERE Status = 'Delivered'"
        ).fetchone()
        self.check(delivered_rows > delivered_orders, "no duplicate Delivered events")
