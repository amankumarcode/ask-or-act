import random
import sqlite3

from askoract.datasets.northwind.traps._util import columns
from askoract.datasets.traps import Trap, register_trap

# Accounts opened recently that have not ordered yet. The rest of each row is
# copied from an existing customer in the same country (Region, formats).
NEW_ACCOUNTS = [
    ("BERGF", "Bergfeld Delikatessen", "Lena Bergfeld", "München", "Germany"),
    ("ORCHD", "Orchard Lane Market", "Sam Whitley", "Portland", "USA"),
    ("PROVE", "Provence Saveurs", "Claire Dumas", "Marseille", "France"),
    ("SAOPA", "Empório São Paulo", "Rafael Lima", "São Paulo", "Brazil"),
    ("THAME", "Thames Fine Foods", "Olivia Grant", "London", "UK"),
    ("IBERI", "Ibérica Gourmet", "Lucía Romero", "Madrid", "Spain"),
]


@register_trap("northwind")
class ActiveCustomer(Trap):
    id = "active_customer"
    summary = (
        "Adds Customers.IsActive, a Sales Ops flag that disagrees with order recency. 12 "
        "customers who still order are flagged inactive, and 6 new accounts with no orders "
        "are flagged active."
    )
    correct_behavior = (
        "'Active customer' has two readings (the IsActive flag, or ordered recently). "
        "Ask which one is meant."
    )
    comments = {
        "Customers": {"IsActive": "1 if the account is active, else 0. Maintained by Sales Ops."}
    }

    N_FLAGGED_INACTIVE = 12

    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        conn.execute("ALTER TABLE Customers ADD COLUMN IsActive INTEGER NOT NULL DEFAULT 1")
        ids = [r[0] for r in conn.execute("SELECT CustomerID FROM Customers ORDER BY CustomerID")]
        conn.executemany(
            "UPDATE Customers SET IsActive = 0 WHERE CustomerID = ?",
            [(cid,) for cid in rng.sample(ids, self.N_FLAGGED_INACTIVE)],
        )
        for cid, company, contact, city, country in NEW_ACCOUNTS:
            region = conn.execute(
                "SELECT Region FROM Customers WHERE Country = ? ORDER BY CustomerID LIMIT 1",
                (country,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO Customers (CustomerID, CompanyName, ContactName, ContactTitle,"
                " Address, City, Region, PostalCode, Country, Phone, Fax, IsActive)"
                " VALUES (?, ?, ?, 'Owner', ?, ?, ?, ?, ?, ?, NULL, 1)",
                (
                    cid,
                    company,
                    contact,
                    f"{rng.randint(1, 250)} Market Street",
                    city,
                    region,
                    f"{rng.randint(10000, 99999)}",
                    country,
                    f"555-{rng.randint(1000, 9999)}",
                ),
            )

    def verify(self, conn: sqlite3.Connection) -> None:
        self.check("IsActive" in columns(conn, "Customers"), "Customers.IsActive is missing")
        flagged = conn.execute("SELECT COUNT(*) FROM Customers WHERE IsActive = 1").fetchone()[0]
        recent = conn.execute(
            "SELECT COUNT(DISTINCT CustomerID) FROM Orders "
            "WHERE OrderDate >= date((SELECT MAX(OrderDate) FROM Orders), '-12 months')"
        ).fetchone()[0]
        self.check(flagged != recent, "IsActive count equals the recency count; trap is inert")
        never_ordered_active = conn.execute(
            "SELECT COUNT(*) FROM Customers WHERE IsActive = 1 "
            "AND CustomerID NOT IN (SELECT CustomerID FROM Orders)"
        ).fetchone()[0]
        self.check(never_ordered_active > 0, "no active accounts without orders")
