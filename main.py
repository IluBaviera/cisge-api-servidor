import os
import datetime
import pyodbc
from typing import List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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


class IngresoRequest(BaseModel):
    referencia: str
    almacen_id: int
    codf: str
    marca: str
    usuario: str
    rollos: List[float]


@app.post("/rollos/ingresos")
def crear_ingreso(body: IngresoRequest):
    conn_r = connect_rollos()
    conn_r.autocommit = False
    try:
        cursor_r = conn_r.cursor()

        # 1. Buscar o crear producto en BdRollos
        cursor_r.execute(
            "SELECT producto_id FROM Producto WITH(UPDLOCK) WHERE codf = ? AND marca = ?",
            body.codf, body.marca,
        )
        row = cursor_r.fetchone()

        if row:
            producto_id = row[0]
        else:
            producto_nava = None
            conn_n = connect_nava()
            try:
                cursor_n = conn_n.cursor()
                for tabla in ["prd0101", "prd0108", "prd0112", "prd0118"]:
                    cursor_n.execute(
                        f"SELECT RTRIM(codi), RTRIM(descr), RTRIM(umed) "
                        f"FROM {tabla} WITH(NOLOCK) "
                        f"WHERE RTRIM(codf) = ? AND RTRIM(marc) = ?",
                        body.codf, body.marca,
                    )
                    nava_row = cursor_n.fetchone()
                    if nava_row:
                        producto_nava = nava_row
                        break
            finally:
                conn_n.close()

            if not producto_nava:
                raise HTTPException(
                    status_code=404,
                    detail=f"Producto '{body.codf}' marca '{body.marca}' no encontrado en el ERP",
                )

            cursor_r.execute(
                "INSERT INTO Producto (codf, codi, marca, descripcion, unidad) "
                "OUTPUT INSERTED.producto_id VALUES (?, ?, ?, ?, ?)",
                body.codf, producto_nava[0], body.marca, producto_nava[1], producto_nava[2],
            )
            producto_id = cursor_r.fetchone()[0]

        # 2. Crear registro de ingreso
        cursor_r.execute(
            "INSERT INTO Ingreso (referencia, almacen_id, usuario, fecha) "
            "OUTPUT INSERTED.id VALUES (?, ?, ?, ?)",
            body.referencia, body.almacen_id, body.usuario, datetime.datetime.now(),
        )
        ingreso_id = cursor_r.fetchone()[0]

        # 3. Obtener codigo del almacen
        cursor_r.execute(
            "SELECT codigo FROM Almacen WITH(NOLOCK) WHERE id = ?",
            body.almacen_id,
        )
        almacen_row = cursor_r.fetchone()
        if not almacen_row:
            raise HTTPException(status_code=404, detail=f"Almacen {body.almacen_id} no encontrado")
        codigo_almacen = almacen_row[0]

        fecha_str = datetime.datetime.now().strftime("%y%m%d")

        # 4. Leer y bloquear correlativo dedicado — nunca baja aunque se borren rollos
        cursor_r.execute(
            "SELECT ultimo_numero FROM Correlativo WITH(UPDLOCK, HOLDLOCK) WHERE almacen_id = ?",
            body.almacen_id,
        )
        corr_row = cursor_r.fetchone()
        if corr_row:
            base_count = corr_row[0]
        else:
            cursor_r.execute(
                "INSERT INTO Correlativo (almacen_id, ultimo_numero) VALUES (?, 0)",
                body.almacen_id,
            )
            base_count = 0

        # 5. Insertar rollos y movimientos
        ids_rollo = []
        for i, metros in enumerate(body.rollos):
            id_rollo = f"R-{codigo_almacen}-{fecha_str}-{base_count + i + 1:04d}"
            cursor_r.execute(
                "INSERT INTO Rollo (id_rollo, producto_id, almacen_id, ingreso_id, metros_inicial, estado) "
                "VALUES (?, ?, ?, ?, ?, 'disponible')",
                id_rollo, producto_id, body.almacen_id, ingreso_id, metros,
            )
            cursor_r.execute(
                "INSERT INTO Movimiento (id_rollo, tipo, metros) VALUES (?, 'ingreso', ?)",
                id_rollo, metros,
            )
            ids_rollo.append(id_rollo)

        # 6. Actualizar correlativo al ultimo numero usado
        cursor_r.execute(
            "UPDATE Correlativo SET ultimo_numero = ? WHERE almacen_id = ?",
            base_count + len(body.rollos), body.almacen_id,
        )

        conn_r.commit()

    except HTTPException:
        conn_r.rollback()
        raise
    except Exception as e:
        conn_r.rollback()
        raise HTTPException(status_code=500, detail=f"Error al registrar ingreso: {e}")
    finally:
        conn_r.close()

    return {
        "ingreso_id": ingreso_id,
        "producto_id": producto_id,
        "ids_rollo": ids_rollo,
    }
