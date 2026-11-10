import random
import sqlite3
from datetime import timedelta

from askoract.datasets.northwind.traps._util import TS_FORMAT, parse_ts, table_exists
from askoract.datasets.traps import Trap, register_trap

METHODS = ["Card", "Wire", "Check"]
SPLIT_RATE = 0.15  # share of orders paid in two installments


@register_trap("northwind")
class AmountInCents(Trap):
    id = "amount_in_cents"
    summary = (
        "Adds Payments(PaymentID, OrderID, Amount, Method, PaidAt) for every shipped "
        "order. Amount is an integer number of cents, and no comment says so. "
        "About 15% of orders are paid in two installments."
    )
    correct_behavior = (
        "Inspect values before summing. Divide Amount by 100 for currency, and aggregate "
        "per order before joining to other tables."
    )
    comments = {"Payments": {"_table": "Customer payments received against orders."}}

    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        conn.execute(
            "CREATE TABLE Payments ("
            " PaymentID INTEGER PRIMARY KEY,"
            " OrderID INTEGER NOT NULL REFERENCES Orders (OrderID),"
            " Amount INTEGER NOT NULL, Method TEXT NOT NULL, PaidAt DATETIME NOT NULL)"
        )
        # Amount owed = net line total + freight, in cents. Depends on cancelled_status.
        totals = conn.execute(
            "SELECT o.OrderID, o.ShippedDate,"
            " CAST(ROUND((SUM(d.UnitPrice * d.Quantity * (1 - d.Discount)) + o.Freight) * 100)"
            " AS INTEGER)"
            ' FROM Orders o JOIN "Order Details" d ON d.OrderID = o.OrderID'
            " WHERE o.Status = 'Shipped'"
            " GROUP BY o.OrderID ORDER BY o.OrderID"
        ).fetchall()
        rows = []
        for order_id, shipped, cents in totals:
            paid = parse_ts(shipped) + timedelta(days=rng.randint(0, 30), hours=rng.randint(8, 17))
            method = rng.choice(METHODS)
            if rng.random() < SPLIT_RATE:
                first = cents // 2
                rows.append((order_id, first, method, paid.strftime(TS_FORMAT)))
                later = paid + timedelta(days=30)
                rows.append((order_id, cents - first, method, later.strftime(TS_FORMAT)))
            else:
                rows.append((order_id, cents, method, paid.strftime(TS_FORMAT)))
        conn.executemany(
            "INSERT INTO Payments (OrderID, Amount, Method, PaidAt) VALUES (?, ?, ?, ?)", rows
        )

    def verify(self, conn: sqlite3.Connection) -> None:
        self.check(table_exists(conn, "Payments"), "Payments is missing")
        paid_cents, owed_dollars, non_int = conn.execute(
            "SELECT (SELECT SUM(Amount) FROM Payments),"
            ' (SELECT SUM(d.UnitPrice * d.Quantity * (1 - d.Discount)) FROM "Order Details" d'
            "  JOIN Orders o ON o.OrderID = d.OrderID WHERE o.Status = 'Shipped')"
            " + (SELECT SUM(Freight) FROM Orders WHERE Status = 'Shipped'),"
            " (SELECT COUNT(*) FROM Payments WHERE typeof(Amount) != 'integer')"
        ).fetchone()
        self.check(non_int == 0, "Amount must be stored as integers")
        ratio = paid_cents / owed_dollars
        self.check(99 < ratio < 101, f"Amount should be ~100x the dollar total, got {ratio:.2f}x")
        split = conn.execute(
            "SELECT COUNT(*) FROM"
            " (SELECT OrderID FROM Payments GROUP BY OrderID HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        self.check(split > 0, "no split payments; the per-order join pitfall would be inert")
