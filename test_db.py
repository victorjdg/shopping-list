"""Tests for the functions in db/db.py.

Each test runs against a fresh temporary database, patching db.DB_PATH, so
the real shopping_list.sqlite is never touched.

Run:  python3 test_db.py   (or   python3 test_db.py -v   for more detail)
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent / "db"
sys.path.insert(0, str(DB_DIR))

import db  # noqa: E402
import initdb  # noqa: E402


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "test.sqlite"
        initdb.init_db(self._db_path)
        self._original_db_path = db.DB_PATH
        db.DB_PATH = self._db_path

    def tearDown(self):
        db.DB_PATH = self._original_db_path
        self._tmp.cleanup()

    def query_one(self, sql, params=()):
        with sqlite3.connect(self._db_path) as conn:
            return conn.execute(sql, params).fetchone()


class TestConnect(DbTestCase):
    def test_connection_closes_on_exiting_with(self):
        with db._connect() as conn:
            conn.execute("SELECT 1")
        # if it hadn't closed deterministically, this would work
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_with_still_commits(self):
        db.add_item("Leche", "Lidl", 1.05)
        self.assertEqual(
            self.query_one("SELECT COUNT(*) FROM lista_compra")[0], 1
        )


class TestAddItem(DbTestCase):
    def test_inserts_distinct_items(self):
        db.add_item("Leche", "Mercadona", 1.20)
        db.add_item("Leche", "Lidl", 1.05)
        self.assertEqual(len(db.list_current()), 2)

    def test_upsert_same_pair_does_not_duplicate_and_updates_price(self):
        db.add_item("Leche", "Mercadona", 1.20)
        db.add_item("Leche", "Mercadona", 1.25)
        items = db.list_current()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ultimo_precio"], 1.25)

    def test_none_price_stays_without_a_price(self):
        db.add_item("Pan", "Lidl", None)
        self.assertIsNone(db.list_current()[0]["ultimo_precio"])


class TestRemoveItem(DbTestCase):
    def test_returns_true_if_it_existed(self):
        db.add_item("Pan", "Mercadona", 0.95)
        self.assertTrue(db.remove_item("Pan", "Mercadona"))

    def test_returns_false_if_it_did_not_exist(self):
        self.assertFalse(db.remove_item("Pan", "Mercadona"))

    def test_does_not_delete_price_history(self):
        db.add_item("Pan", "Mercadona", 0.95)
        db.update_price("Pan", "Mercadona", 0.90)
        db.remove_item("Pan", "Mercadona")
        self.assertEqual(
            self.query_one("SELECT COUNT(*) FROM historico_precios")[0], 1
        )


class TestUpdatePrice(DbTestCase):
    def test_updates_list_and_appends_to_history(self):
        db.add_item("Leche", "Lidl", 1.05)
        db.update_price("Leche", "Lidl", 1.10)
        fila_lista = self.query_one(
            "SELECT ultimo_precio FROM lista_compra WHERE producto = ? AND supermercado = ?",
            ("Leche", "Lidl"),
        )
        fila_hist = self.query_one(
            "SELECT precio FROM historico_precios WHERE producto = ? AND supermercado = ?",
            ("Leche", "Lidl"),
        )
        self.assertEqual(fila_lista[0], 1.10)
        self.assertEqual(fila_hist[0], 1.10)

    def test_atomic_transaction_if_history_fails_list_is_not_updated(self):
        db.add_item("Leche", "Lidl", 1.05)
        # Trigger that aborts any INSERT into historico_precios
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TRIGGER abortar_historico
                BEFORE INSERT ON historico_precios
                BEGIN
                    SELECT RAISE(ABORT, 'boom');
                END
                """
            )
        with self.assertRaises(sqlite3.IntegrityError):
            db.update_price("Leche", "Lidl", 1.10)
        # The lista_compra UPDATE gets rolled back: the previous price remains
        fila_lista = self.query_one(
            "SELECT ultimo_precio FROM lista_compra WHERE producto = ? AND supermercado = ?",
            ("Leche", "Lidl"),
        )
        self.assertEqual(fila_lista[0], 1.05)

    def test_product_not_on_list_only_ends_up_in_history(self):
        db.update_price("Cafe", "Lidl", 3.00)
        self.assertEqual(len(db.list_current()), 0)
        self.assertEqual(
            self.query_one("SELECT COUNT(*) FROM historico_precios")[0], 1
        )


class TestQueryCheapest(DbTestCase):
    def test_recent_price_per_supermarket_not_the_historical_minimum(self):
        db.update_price("Leche", "Lidl", 1.15)
        db.update_price("Leche", "Lidl", 1.10)   # goes back up: the recent one wins
        db.update_price("Leche", "Mercadona", 1.30)
        self.assertEqual(
            db.query_cheapest("Leche"), [("Lidl", 1.10), ("Mercadona", 1.30)]
        )

    def test_sorted_cheapest_to_most_expensive(self):
        db.update_price("Leche", "Mercadona", 1.30)
        db.update_price("Leche", "Lidl", 1.10)
        db.update_price("Leche", "Carrefour", 1.20)
        self.assertEqual(
            db.query_cheapest("Leche"),
            [("Lidl", 1.10), ("Carrefour", 1.20), ("Mercadona", 1.30)],
        )

    def test_unknown_product_returns_empty_list(self):
        self.assertEqual(db.query_cheapest("Inexistente"), [])

    def test_only_looks_at_the_requested_product(self):
        db.update_price("Leche", "Lidl", 1.10)
        db.update_price("Pan", "Lidl", 0.90)
        self.assertEqual(db.query_cheapest("Leche"), [("Lidl", 1.10)])


class TestListCurrent(DbTestCase):
    def test_empty_list_at_the_start(self):
        self.assertEqual(db.list_current(), [])

    def test_returns_dicts_with_every_column(self):
        db.add_item("Leche", "Mercadona", 1.20)
        item = db.list_current()[0]
        self.assertEqual(
            set(item), {"id", "producto", "supermercado", "ultimo_precio", "actualizado_en"}
        )
        self.assertEqual(item["producto"], "Leche")
        self.assertEqual(item["supermercado"], "Mercadona")

    def test_sorted_by_product_and_supermarket(self):
        db.add_item("Leche", "Mercadona", 1.20)
        db.add_item("Pan", "Lidl", 0.90)
        db.add_item("Leche", "Lidl", 1.05)
        self.assertEqual(
            [(i["producto"], i["supermercado"]) for i in db.list_current()],
            [("Leche", "Lidl"), ("Leche", "Mercadona"), ("Pan", "Lidl")],
        )


if __name__ == "__main__":
    unittest.main()
