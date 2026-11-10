import random
import sqlite3

from askoract.datasets.northwind.traps._util import columns
from askoract.datasets.traps import Trap, register_trap


@register_trap("northwind")
class CancelledStatus(Trap):
    id = "cancelled_status"
    summary = (
        "Adds Orders.Status (Shipped / Pending / Cancelled). About 8% of shipped orders "
        "become Cancelled: their ShippedDate is cleared but their order lines remain."
    )
    correct_behavior = "Exclude Cancelled orders from sales, revenue, and order counts."
    comments = {"Orders": {"Status": "Order lifecycle status."}}

    CANCEL_RATE = 0.08

    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        conn.execute("ALTER TABLE Orders ADD COLUMN Status TEXT NOT NULL DEFAULT 'Shipped'")
        updates = []
        for order_id, unshipped in conn.execute(
            "SELECT OrderID, ShippedDate IS NULL FROM Orders ORDER BY OrderID"
        ):
            if unshipped:
                status = "Pending"
            elif rng.random() < self.CANCEL_RATE:
                status = "Cancelled"
            else:
                continue
            updates.append((status, order_id))
        conn.executemany("UPDATE Orders SET Status = ? WHERE OrderID = ?", updates)
        conn.execute("UPDATE Orders SET ShippedDate = NULL WHERE Status = 'Cancelled'")

    def verify(self, conn: sqlite3.Connection) -> None:
        self.check("Status" in columns(conn, "Orders"), "Orders.Status is missing")
        total, cancelled, cancelled_shipped = conn.execute(
            "SELECT COUNT(*), SUM(Status = 'Cancelled'), "
            "SUM(Status = 'Cancelled' AND ShippedDate IS NOT NULL) FROM Orders"
        ).fetchone()
        self.check(0.05 < cancelled / total < 0.11, f"cancelled share {cancelled / total:.3f}")
        self.check(cancelled_shipped == 0, "cancelled orders must not have a ShippedDate")
        lines = conn.execute(
            'SELECT COUNT(*) FROM "Order Details" WHERE OrderID IN '
            "(SELECT OrderID FROM Orders WHERE Status = 'Cancelled')"
        ).fetchone()[0]
        self.check(lines > 0, "cancelled orders must keep their order lines, or the trap is inert")
