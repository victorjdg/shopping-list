"""Pruebas de las funciones de db/db.py.

Cada test trabaja sobre una base de datos temporal nueva, parcheando
db.DB_PATH, para no tocar nunca el shopping_list.sqlite real.

Ejecutar:  python3 test_db.py   (o   python3 test_db.py -v   para mas detalle)
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
    def test_la_conexion_se_cierra_al_salir_del_with(self):
        with db._connect() as conn:
            conn.execute("SELECT 1")
        # si no se hubiera cerrado deterministamente, esto funcionaria
        with self.assertRaises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_el_with_sigue_haciendo_commit(self):
        db.add_item("Leche", "Lidl", 1.05)
        self.assertEqual(
            self.query_one("SELECT COUNT(*) FROM lista_compra")[0], 1
        )


class TestAddItem(DbTestCase):
    def test_inserta_items_distintos(self):
        db.add_item("Leche", "Mercadona", 1.20)
        db.add_item("Leche", "Lidl", 1.05)
        self.assertEqual(len(db.list_current()), 2)

    def test_upsert_misma_pareja_no_duplica_y_actualiza_precio(self):
        db.add_item("Leche", "Mercadona", 1.20)
        db.add_item("Leche", "Mercadona", 1.25)
        items = db.list_current()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ultimo_precio"], 1.25)

    def test_precio_none_queda_sin_precio(self):
        db.add_item("Pan", "Lidl", None)
        self.assertIsNone(db.list_current()[0]["ultimo_precio"])


class TestRemoveItem(DbTestCase):
    def test_devuelve_true_si_existia(self):
        db.add_item("Pan", "Mercadona", 0.95)
        self.assertTrue(db.remove_item("Pan", "Mercadona"))

    def test_devuelve_false_si_no_existia(self):
        self.assertFalse(db.remove_item("Pan", "Mercadona"))

    def test_no_borra_el_historico(self):
        db.add_item("Pan", "Mercadona", 0.95)
        db.update_price("Pan", "Mercadona", 0.90)
        db.remove_item("Pan", "Mercadona")
        self.assertEqual(
            self.query_one("SELECT COUNT(*) FROM historico_precios")[0], 1
        )


class TestUpdatePrice(DbTestCase):
    def test_actualiza_lista_y_anade_historico(self):
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

    def test_transaccion_atomica_si_falla_el_historico_no_se_actualiza_la_lista(self):
        db.add_item("Leche", "Lidl", 1.05)
        # Trigger que aborta cualquier INSERT en historico_precios
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
        # El UPDATE de lista_compra se deshace: sigue el precio anterior
        fila_lista = self.query_one(
            "SELECT ultimo_precio FROM lista_compra WHERE producto = ? AND supermercado = ?",
            ("Leche", "Lidl"),
        )
        self.assertEqual(fila_lista[0], 1.05)

    def test_producto_fuera_de_lista_solo_queda_en_historico(self):
        db.update_price("Cafe", "Lidl", 3.00)
        self.assertEqual(len(db.list_current()), 0)
        self.assertEqual(
            self.query_one("SELECT COUNT(*) FROM historico_precios")[0], 1
        )


class TestQueryCheapest(DbTestCase):
    def test_precio_reciente_por_supermercado_no_el_minimo_historico(self):
        db.update_price("Leche", "Lidl", 1.15)
        db.update_price("Leche", "Lidl", 1.10)   # sube de nuevo: gana el reciente
        db.update_price("Leche", "Mercadona", 1.30)
        self.assertEqual(
            db.query_cheapest("Leche"), [("Lidl", 1.10), ("Mercadona", 1.30)]
        )

    def test_ordenado_de_mas_barato_a_mas_caro(self):
        db.update_price("Leche", "Mercadona", 1.30)
        db.update_price("Leche", "Lidl", 1.10)
        db.update_price("Leche", "Carrefour", 1.20)
        self.assertEqual(
            db.query_cheapest("Leche"),
            [("Lidl", 1.10), ("Carrefour", 1.20), ("Mercadona", 1.30)],
        )

    def test_producto_desconocido_devuelve_lista_vacia(self):
        self.assertEqual(db.query_cheapest("Inexistente"), [])

    def test_solo_mira_el_producto_pedido(self):
        db.update_price("Leche", "Lidl", 1.10)
        db.update_price("Pan", "Lidl", 0.90)
        self.assertEqual(db.query_cheapest("Leche"), [("Lidl", 1.10)])


class TestListCurrent(DbTestCase):
    def test_lista_vacia_al_principio(self):
        self.assertEqual(db.list_current(), [])

    def test_devuelve_dicts_con_todas_las_columnas(self):
        db.add_item("Leche", "Mercadona", 1.20)
        item = db.list_current()[0]
        self.assertEqual(
            set(item), {"id", "producto", "supermercado", "ultimo_precio", "actualizado_en"}
        )
        self.assertEqual(item["producto"], "Leche")
        self.assertEqual(item["supermercado"], "Mercadona")

    def test_ordenado_por_producto_y_supermercado(self):
        db.add_item("Leche", "Mercadona", 1.20)
        db.add_item("Pan", "Lidl", 0.90)
        db.add_item("Leche", "Lidl", 1.05)
        self.assertEqual(
            [(i["producto"], i["supermercado"]) for i in db.list_current()],
            [("Leche", "Lidl"), ("Leche", "Mercadona"), ("Pan", "Lidl")],
        )


if __name__ == "__main__":
    unittest.main()
