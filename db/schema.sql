CREATE TABLE IF NOT EXISTS lista_compra (
    id INTEGER PRIMARY KEY,
    producto TEXT NOT NULL,
    supermercado TEXT NOT NULL,      -- part of the item's IDENTITY, not a loose attribute
    ultimo_precio REAL,              -- price of THAT product at THAT supermarket
    actualizado_en TEXT NOT NULL,    -- ISO 8601
    UNIQUE(producto, supermercado)
);

CREATE TABLE IF NOT EXISTS historico_precios (
    id INTEGER PRIMARY KEY,
    producto TEXT NOT NULL,          -- no strict FK to lista_compra: must survive even if
    supermercado TEXT NOT NULL,      -- the product is no longer on the current list
    precio REAL NOT NULL,
    fecha TEXT NOT NULL              -- ISO 8601
);
