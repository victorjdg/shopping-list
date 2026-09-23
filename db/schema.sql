CREATE TABLE IF NOT EXISTS lista_compra (
    id INTEGER PRIMARY KEY,
    producto TEXT NOT NULL,
    supermercado TEXT NOT NULL,      -- parte de la IDENTIDAD del item, no un atributo suelto
    ultimo_precio REAL,              -- precio de ESE producto en ESE super
    actualizado_en TEXT NOT NULL,    -- ISO 8601
    UNIQUE(producto, supermercado)
);

CREATE TABLE IF NOT EXISTS historico_precios (
    id INTEGER PRIMARY KEY,
    producto TEXT NOT NULL,          -- sin FK estricta a lista_compra: debe sobrevivir aunque
    supermercado TEXT NOT NULL,      -- el producto ya no este en la lista actual
    precio REAL NOT NULL,
    fecha TEXT NOT NULL              -- ISO 8601
);
