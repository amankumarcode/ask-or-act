import random
import sqlite3

from askoract.datasets.northwind.traps._util import table_exists
from askoract.datasets.traps import Trap, register_trap

# Defunct accounts that exist only in the stale snapshot: (CustomerID, CompanyName, City, Country)
DEFUNCT = [
    ("NORDL", "Nordlicht Feinkost", "Hamburg", "Germany"),
    ("CASAB", "Casa Bonita Importadores", "Sevilla", "Spain"),
    ("GOLDG", "Golden Gate Grocers", "San Francisco", "USA"),
    ("LYONG", "Lyon Gourmandises", "Lyon", "France"),
    ("TAMIS", "Tamisa Provisions", "London", "UK"),
    ("RIOVE", "Rio Verde Alimentos", "Rio de Janeiro", "Brazil"),
    ("FJORD", "Fjordmat Grossist", "Bergen", "Norway"),
    ("MILAN", "Milano Delizie", "Milano", "Italy"),
    ("ALPEN", "Alpenhof Spezialitäten", "Salzburg", "Austria"),
    ("PRAIR", "Prairie Pantry Co.", "Calgary", "Canada"),
    ("BAHIA", "Bahia Sabores", "Salvador", "Brazil"),
    ("LISBO", "Lisboa Mercearia Fina", "Lisboa", "Portugal"),
    ("CHIGO", "Windy City Provisions", "Chicago", "USA"),
    ("MARSE", "Marseille Épicerie du Port", "Marseille", "France"),
]


@register_trap("northwind")
class LegacyCustomers(Trap):
    id = "legacy_customers"
    summary = (
        "Adds CustomerMaster, a stale 2019 CRM snapshot of customers. About 10% of current "
        "customers are missing from it, about 20% show an old city and country, and it holds "
        "14 defunct accounts that never appear in Orders."
    )
    correct_behavior = (
        "Use Customers, the table Orders references. CustomerMaster is not maintained."
    )
    comments = {
        "CustomerMaster": {
            "_table": "Customer records exported from the old CRM during the 2019 migration.",
            "LastSyncedAt": "When this row was last synced from the CRM.",
        }
    }

    def apply(self, conn: sqlite3.Connection, rng: random.Random) -> None:
        conn.execute(
            "CREATE TABLE CustomerMaster ("
            " CustomerID TEXT PRIMARY KEY, CompanyName TEXT, ContactName TEXT, City TEXT,"
            " Country TEXT, Phone TEXT, AccountTier TEXT, LastSyncedAt DATETIME)"
        )
        customers = conn.execute(
            "SELECT CustomerID, CompanyName, ContactName, City, Country, Phone "
            "FROM Customers ORDER BY CustomerID"
        ).fetchall()
        city_by_country: dict[str, list[str]] = {}
        for *_, city, country, _phone in customers:
            if city and country:  # a few customers have no address on file
                city_by_country.setdefault(country, []).append(city)
        countries = sorted(city_by_country)

        rows = []
        for cid, company, contact, city, country, phone in customers:
            r = rng.random()
            if r < 0.10:
                continue  # missing from the snapshot
            if r < 0.30:  # moved since: the snapshot holds an old address
                country = rng.choice([c for c in countries if c != country])
                city = rng.choice(sorted(city_by_country[country]))
            rows.append((cid, company, contact, city, country, phone))
        for cid, company, city, country in DEFUNCT:
            rows.append((cid, company, None, city, country, None))

        conn.executemany(
            "INSERT INTO CustomerMaster VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    *row,
                    rng.choice(["Gold", "Silver", "Bronze"]),
                    f"2019-{rng.randint(3, 6):02d}-{rng.randint(1, 28):02d} "
                    f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00",
                )
                for row in rows
            ],
        )

    def verify(self, conn: sqlite3.Connection) -> None:
        self.check(table_exists(conn, "CustomerMaster"), "CustomerMaster is missing")
        differing = conn.execute(
            "SELECT COUNT(*) FROM ("
            " SELECT Country, COUNT(*) n FROM Customers GROUP BY Country"
            " EXCEPT SELECT Country, COUNT(*) FROM CustomerMaster GROUP BY Country)"
        ).fetchone()[0]
        self.check(differing >= 3, f"only {differing} countries differ between the tables")
        defunct = conn.execute(
            "SELECT COUNT(*) FROM CustomerMaster "
            "WHERE CustomerID NOT IN (SELECT CustomerID FROM Orders)"
        ).fetchone()[0]
        self.check(defunct >= 10, f"expected 10+ defunct accounts, found {defunct}")
