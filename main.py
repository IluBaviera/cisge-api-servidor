import os
import datetime
import pyodbc
from fastapi import FastAPI, HTTPException

app = FastAPI(title="CISGE Stock API")


def _connect(database: str) -> pyodbc.Connection:
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise HTTPException(status_code=500, detail="DB_PASSWORD no configurada")
    try:
        return pyodbc.connect(
            "DRIVER={SQL Server};"
            "SERVER=192.168.2.13;"
            f"DATABASE={database};"
            "UID=cisge_asistente;"
            f"PWD={password}"
        )
    except pyodbc.Error as e:
        raise HTTPException(status_code=503, detail=f"Error de conexión a {database}: {e}")


def connect_nava() -> pyodbc.Connection:
    return _connect("BdNava01")


def connect_rollos() -> pyodbc.Connection:
    return _connect("BdRollos")


def get_stock_data() -> dict:
    conn = connect_nava()

    almacenes = {
        "prd0101": "Almacen Lima Centro",
        "prd0108": "Almacen Colonial",
        "prd0112": "Almacen San Luis 1",
        "prd0118": "Almacen San Luis 2",
    }

    products = {}
    try:
        cursor = conn.cursor()
        for tabla, nombre in almacenes.items():
            cursor.execute(
                f"SELECT RTRIM(codi), RTRIM(codf), RTRIM(descr), "
                f"RTRIM(marc), stoc, RTRIM(umed), vvus "
                f"FROM {tabla} WITH(NOLOCK) WHERE LEFT(codi, 2) = '02' AND estado = 1"
            )
            for row in cursor.fetchall():
                key = (row[1], row[3])
                if key not in products:
                    products[key] = {
                        "codigo": row[1],
                        "codigo_interno": row[0],
                        "descripcion": row[2],
                        "marca": row[3],
                        "precio": float(row[6]) if row[6] is not None else 0.0,
                        "unidad": row[5],
                        "almacenes": {},
                    }
                products[key]["almacenes"][nombre] = float(row[4])
    finally:
        conn.close()

    productos = list(products.values())

    return {
        "actualizado": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_productos": len(productos),
        "productos": productos,
    }


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/stock")
def stock():
    return get_stock_data()


@app.get("/rollos/resumen")
def rollos_resumen(producto: str):
    conn = connect_rollos()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(r.id_rollo), "
            "  ISNULL(SUM(ISNULL(sub.metros_mov, 0)), 0) "
            "FROM Rollo r WITH(NOLOCK) "
            "JOIN Producto p WITH(NOLOCK) ON p.producto_id = r.producto_id "
            "LEFT JOIN ("
            "  SELECT id_rollo, SUM(metros) AS metros_mov "
            "  FROM Movimiento WITH(NOLOCK) GROUP BY id_rollo"
            ") sub ON sub.id_rollo = r.id_rollo "
            "WHERE p.codf = ?",
            producto,
        )
        row = cursor.fetchone()
    finally:
        conn.close()

    total_rollos = row[0] if row else 0
    if total_rollos == 0:
        raise HTTPException(status_code=404, detail=f"Producto '{producto}' no encontrado")

    metros_totales = float(row[1])
    return {
        "producto": producto,
        "total_rollos": total_rollos,
        "metros_totales": metros_totales,
        "promedio_metros_por_rollo": round(metros_totales / total_rollos, 2),
    }


@app.get("/rollos")
def rollos(producto: str):
    conn = connect_rollos()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT r.id_rollo, a.nombre, r.ubicacion, r.estado, "
            "  r.metros_inicial, "
            "  ISNULL(SUM(m.metros), 0) AS metros_actuales "
            "FROM Rollo r WITH(NOLOCK) "
            "JOIN Producto p WITH(NOLOCK) ON p.producto_id = r.producto_id "
            "JOIN Almacen a WITH(NOLOCK) ON a.id = r.almacen_id "
            "LEFT JOIN Movimiento m WITH(NOLOCK) ON m.id_rollo = r.id_rollo "
            "WHERE p.codf = ? "
            "GROUP BY r.id_rollo, a.nombre, r.ubicacion, r.estado, r.metros_inicial",
            producto,
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail=f"Producto '{producto}' no encontrado")

    return [
        {
            "id_rollo": row[0],
            "almacen": row[1],
            "ubicacion": row[2],
            "estado": row[3],
            "metros_inicial": float(row[4]),
            "metros_actuales": float(row[5]),
        }
        for row in rows
    ]
