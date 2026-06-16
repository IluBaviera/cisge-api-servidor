import os
import datetime
import pyodbc
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="CISGE Stock API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
        "https://api.comercialcisgesac.com.pe",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def connect_asistente() -> pyodbc.Connection:
    return _connect("BdAsistente")


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
                f"SELECT RTRIM(prd.codi), RTRIM(prd.codf), RTRIM(prd.descr), "
                f"RTRIM(prd.marc), prd.stoc, RTRIM(prd.umed), prd.vvus, "
                f"RTRIM(sbf.nomsub), RTRIM(grp.nomgru), prd.pedi "
                f"FROM {tabla} prd WITH(NOLOCK) "
                f"INNER JOIN tbl01sbf sbf WITH(NOLOCK) "
                f"  ON LEFT(prd.codi, 4) = LEFT(sbf.codsub, 2) + SUBSTRING(sbf.codsub, 4, 2) "
                f"INNER JOIN tbl01grp grp WITH(NOLOCK) "
                f"  ON LEFT(prd.codi, 7) = LEFT(grp.codgru, 2) + SUBSTRING(grp.codgru, 4, 2) + '-' + SUBSTRING(grp.codgru, 7, 2) "
                f"WHERE LEFT(prd.codi, 2) = '02' AND prd.estado = 1"
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
                        "subfamilia": row[7],
                        "grupo": row[8],
                        "almacenes": {},
                    }
                products[key]["almacenes"][nombre] = float(row[4]) - float(row[9])

        # Medidas estructuradas (campos personalizados Usr_001..004) — son
        # intrínsecas al código, no a la marca/almacén. Se leen aparte de
        # prd0101 (donde se llenan) para no arriesgar el loop de stock si las
        # otras tablas de almacén no tuvieran esas columnas.
        medidas_por_codigo = {}
        cursor.execute(
            "SELECT RTRIM(codf), RTRIM(Usr_001), RTRIM(Usr_002), "
            "RTRIM(Usr_003), RTRIM(Usr_004) "
            "FROM prd0101 WITH(NOLOCK) "
            "WHERE LEFT(codi, 2) = '02' AND estado = 1"
        )
        for codf, m_mang, m_r1, m_r2, m_tubo in cursor.fetchall():
            vals = {
                "med_manguera": m_mang or "",
                "med_rosca_1":  m_r1 or "",
                "med_rosca_2":  m_r2 or "",
                "med_tubo":     m_tubo or "",
            }
            # Quedarse con la primera fila que tenga algún dato (evita pisar
            # con una fila vacía de otra marca del mismo código).
            if codf not in medidas_por_codigo and any(vals.values()):
                medidas_por_codigo[codf] = vals
    finally:
        conn.close()

    productos = list(products.values())

    # Adjuntar las medidas a cada producto (mismas para todas las marcas del
    # código). Si no hay datos cargados, se devuelven vacías para que el
    # consumidor siempre encuentre las claves.
    _SIN_MEDIDAS = {"med_manguera": "", "med_rosca_1": "", "med_rosca_2": "", "med_tubo": ""}
    for p in productos:
        p.update(medidas_por_codigo.get(p["codigo"], _SIN_MEDIDAS))

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


@app.get("/marcas")
def marcas():
    conn = connect_nava()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Nommar, abrmar FROM tbl01mar WITH(NOLOCK) WHERE viewnube = 1"
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    return [{"nombre": row[0].strip(), "alias": row[1].strip()} for row in rows]


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
def rollos(almacen_id: int, producto: str = None):
    conn = connect_rollos()
    try:
        cursor = conn.cursor()
        if producto is not None:
            cursor.execute(
                "SELECT r.id_rollo, a.nombre, r.ubicacion, r.estado, "
                "  r.metros_inicial, p.codf, p.descripcion, "
                "  ISNULL(i.referencia, '') AS referencia, "
                "  ISNULL(SUM(m.metros), 0) AS metros_actuales "
                "FROM Rollo r WITH(NOLOCK) "
                "JOIN Producto p WITH(NOLOCK) ON p.producto_id = r.producto_id "
                "JOIN Almacen a WITH(NOLOCK) ON a.id = r.almacen_id "
                "LEFT JOIN Ingreso i WITH(NOLOCK) ON i.id = r.ingreso_id "
                "LEFT JOIN Movimiento m WITH(NOLOCK) ON m.id_rollo = r.id_rollo "
                "WHERE p.codf = ? AND r.almacen_id = ? "
                "GROUP BY r.id_rollo, a.nombre, r.ubicacion, r.estado, r.metros_inicial, p.codf, p.descripcion, i.referencia",
                producto, almacen_id,
            )
        else:
            cursor.execute(
                "SELECT r.id_rollo, a.nombre, r.ubicacion, r.estado, "
                "  r.metros_inicial, p.codf, p.descripcion, "
                "  ISNULL(i.referencia, '') AS referencia, "
                "  ISNULL(SUM(m.metros), 0) AS metros_actuales "
                "FROM Rollo r WITH(NOLOCK) "
                "JOIN Producto p WITH(NOLOCK) ON p.producto_id = r.producto_id "
                "JOIN Almacen a WITH(NOLOCK) ON a.id = r.almacen_id "
                "LEFT JOIN Ingreso i WITH(NOLOCK) ON i.id = r.ingreso_id "
                "LEFT JOIN Movimiento m WITH(NOLOCK) ON m.id_rollo = r.id_rollo "
                "WHERE r.almacen_id = ? "
                "GROUP BY r.id_rollo, a.nombre, r.ubicacion, r.estado, r.metros_inicial, p.codf, p.descripcion, i.referencia",
                almacen_id,
            )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if producto is not None and not rows:
        raise HTTPException(status_code=404, detail=f"Producto '{producto}' no encontrado")

    return [
        {
            "id_rollo":        row[0],
            "almacen":         row[1],
            "ubicacion":       row[2],
            "estado":          row[3],
            "metros_inicial":  float(row[4]),
            "codf":            row[5],
            "descripcion":     row[6],
            "referencia":      row[7] or '',
            "metros_actuales": float(row[8]),
        }
        for row in rows
    ]


class ActualizarRolloRequest(BaseModel):
    ubicacion:     Optional[str]   = None
    referencia:    Optional[str]   = None
    metros_inicial: Optional[float] = None
    estado:        Optional[str]   = None


class IngresoRequest(BaseModel):
    referencia: str
    almacen_id: int
    codf: str
    marca: str
    usuario: str
    ubicacion: Optional[str] = None
    rollos: List[float]


class MovimientoRequest(BaseModel):
    id_rollo: str
    metros: float
    pedido_erp: str
    usuario: str


class TrasladoRequest(BaseModel):
    id_rollo: str
    almacen_destino_id: int
    usuario: str


class ConfigRequest(BaseModel):
    clave: str
    valor: str
    usuario: str


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
                "INSERT INTO Rollo (id_rollo, producto_id, almacen_id, ingreso_id, metros_inicial, ubicacion, estado) "
                "VALUES (?, ?, ?, ?, ?, ?, 'disponible')",
                id_rollo, producto_id, body.almacen_id, ingreso_id, metros, body.ubicacion,
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


@app.post("/rollos/movimientos")
def registrar_corte(body: MovimientoRequest):
    conn_r = connect_rollos()
    conn_r.autocommit = False
    try:
        cursor_r = conn_r.cursor()

        # 1. Verificar y bloquear rollo — serializa cortes concurrentes sobre el mismo rollo
        cursor_r.execute(
            "SELECT estado FROM Rollo WITH(UPDLOCK, HOLDLOCK) WHERE id_rollo = ?",
            body.id_rollo,
        )
        rollo_row = cursor_r.fetchone()
        if not rollo_row:
            raise HTTPException(status_code=404, detail=f"Rollo '{body.id_rollo}' no encontrado")
        estado_actual = rollo_row[0]

        # 2. Calcular metros disponibles — serializado por el lock del paso 1
        cursor_r.execute(
            "SELECT ISNULL(SUM(metros), 0) FROM Movimiento WHERE id_rollo = ?",
            body.id_rollo,
        )
        disponibles = float(cursor_r.fetchone()[0])

        # 3. Validar stock suficiente
        if body.metros > disponibles:
            raise HTTPException(
                status_code=400,
                detail=f"Metros a cortar ({body.metros}) superan los disponibles ({disponibles:.3f})",
            )

        # 4. Registrar corte como movimiento negativo
        cursor_r.execute(
            "INSERT INTO Movimiento (id_rollo, tipo, metros, pedido_erp, usuario, fecha) "
            "VALUES (?, 'corte', ?, ?, ?, ?)",
            body.id_rollo, -body.metros, body.pedido_erp, body.usuario, datetime.datetime.now(),
        )

        # 5. Determinar nuevo estado
        restante = disponibles - body.metros

        cursor_r.execute(
            "SELECT valor FROM Config WITH(NOLOCK) WHERE clave = 'umbral_retazo'",
        )
        config_row = cursor_r.fetchone()
        umbral = float(config_row[0]) if config_row else 0.0

        if restante < 0.001:
            nuevo_estado = "agotado"
        elif restante < umbral:
            nuevo_estado = "retazo"
        else:
            nuevo_estado = "disponible"

        if nuevo_estado != estado_actual:
            cursor_r.execute(
                "UPDATE Rollo SET estado = ? WHERE id_rollo = ?",
                nuevo_estado, body.id_rollo,
            )

        conn_r.commit()

    except HTTPException:
        conn_r.rollback()
        raise
    except Exception as e:
        conn_r.rollback()
        raise HTTPException(status_code=500, detail=f"Error al registrar corte: {e}")
    finally:
        conn_r.close()

    return {
        "id_rollo": body.id_rollo,
        "metros_cortados": body.metros,
        "metros_restantes": round(restante, 3),
        "nuevo_estado": nuevo_estado,
    }


@app.post("/rollos/traslados")
def trasladar_rollo(body: TrasladoRequest):
    conn = connect_rollos()
    conn.autocommit = False
    try:
        cursor = conn.cursor()

        # 1. Verificar rollo y obtener almacén origen
        cursor.execute(
            "SELECT r.estado, r.almacen_id, a.nombre "
            "FROM Rollo r WITH(UPDLOCK, HOLDLOCK) "
            "JOIN Almacen a WITH(NOLOCK) ON a.id = r.almacen_id "
            "WHERE r.id_rollo = ?",
            body.id_rollo,
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Rollo '{body.id_rollo}' no encontrado")
        if row[0] == "agotado":
            raise HTTPException(status_code=400, detail="No se puede trasladar un rollo agotado")
        almacen_origen_id = row[1]
        almacen_origen_nombre = row[2]

        # 2. Verificar almacén destino
        cursor.execute(
            "SELECT nombre FROM Almacen WITH(NOLOCK) WHERE id = ?",
            body.almacen_destino_id,
        )
        dest_row = cursor.fetchone()
        if not dest_row:
            raise HTTPException(status_code=404, detail=f"Almacén destino {body.almacen_destino_id} no encontrado")
        almacen_destino_nombre = dest_row[0]

        if almacen_origen_id == body.almacen_destino_id:
            raise HTTPException(status_code=400, detail="El almacén destino es igual al origen")

        # 3. Actualizar almacén del rollo
        cursor.execute(
            "UPDATE Rollo SET almacen_id = ? WHERE id_rollo = ?",
            body.almacen_destino_id, body.id_rollo,
        )

        # 4. Registrar movimiento de traslado
        cursor.execute(
            "INSERT INTO Movimiento (id_rollo, tipo, metros, usuario, fecha) "
            "VALUES (?, 'traslado', 0, ?, ?)",
            body.id_rollo, body.usuario, datetime.datetime.now(),
        )

        conn.commit()

    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al registrar traslado: {e}")
    finally:
        conn.close()

    return {
        "id_rollo": body.id_rollo,
        "almacen_origen": almacen_origen_nombre,
        "almacen_destino": almacen_destino_nombre,
        "usuario": body.usuario,
    }


@app.put("/rollos/{id_rollo}")
def actualizar_rollo(id_rollo: str, body: ActualizarRolloRequest):
    conn = connect_rollos()
    conn.autocommit = False
    try:
        cursor = conn.cursor()

        # Verificar existencia y detectar si tiene cortes
        cursor.execute(
            "SELECT r.metros_inicial, r.ingreso_id, "
            "  CASE WHEN EXISTS ( "
            "    SELECT 1 FROM Movimiento WHERE id_rollo = ? AND tipo = 'corte' "
            "  ) THEN 1 ELSE 0 END AS tiene_cortes "
            "FROM Rollo r WITH(UPDLOCK) WHERE r.id_rollo = ?",
            id_rollo, id_rollo,
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"Rollo '{id_rollo}' no encontrado")

        ingreso_id   = row[1]
        tiene_cortes = bool(row[2])

        # Actualizar Rollo
        rollo_sets   = []
        rollo_params = []

        if body.ubicacion is not None:
            rollo_sets.append("ubicacion = ?")
            rollo_params.append(body.ubicacion)

        if body.estado is not None:
            rollo_sets.append("estado = ?")
            rollo_params.append(body.estado)

        if body.metros_inicial is not None and not tiene_cortes:
            rollo_sets.append("metros_inicial = ?")
            rollo_params.append(body.metros_inicial)

        if rollo_sets:
            rollo_params.append(id_rollo)
            cursor.execute(
                f"UPDATE Rollo SET {', '.join(rollo_sets)} WHERE id_rollo = ?",
                *rollo_params,
            )

        # Sincronizar movimiento de ingreso si se cambió metros_inicial
        if body.metros_inicial is not None and not tiene_cortes:
            cursor.execute(
                "UPDATE Movimiento SET metros = ? WHERE id_rollo = ? AND tipo = 'ingreso'",
                body.metros_inicial, id_rollo,
            )

        # Actualizar referencia en Ingreso
        if body.referencia is not None and ingreso_id is not None:
            cursor.execute(
                "UPDATE Ingreso SET referencia = ? WHERE id = ?",
                body.referencia, ingreso_id,
            )

        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al actualizar rollo: {e}")
    finally:
        conn.close()

    return {
        "id_rollo":               id_rollo,
        "actualizado":            True,
        "metros_inicial_ignorado": body.metros_inicial is not None and tiene_cortes,
    }


@app.get("/rollos/sugerencia")
def sugerir_rollo(producto: str, metros: float, almacen_id: int):
    conn = connect_rollos()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT r.id_rollo, a.nombre, r.ubicacion, r.estado, "
            "  r.metros_inicial, ISNULL(SUM(m.metros), 0) AS metros_actuales "
            "FROM Rollo r WITH(NOLOCK) "
            "JOIN Producto p WITH(NOLOCK) ON p.producto_id = r.producto_id "
            "JOIN Almacen a WITH(NOLOCK) ON a.id = r.almacen_id "
            "LEFT JOIN Movimiento m WITH(NOLOCK) ON m.id_rollo = r.id_rollo "
            "WHERE p.codf = ? AND r.almacen_id = ? "
            "  AND r.estado IN ('disponible', 'retazo') "
            "GROUP BY r.id_rollo, a.nombre, r.ubicacion, r.estado, r.metros_inicial",
            producto, almacen_id,
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        raise HTTPException(status_code=404, detail=f"No hay rollos disponibles para '{producto}'")

    rollos = [
        {
            "id_rollo": r[0],
            "almacen": r[1],
            "ubicacion": r[2],
            "estado": r[3],
            "metros_inicial": float(r[4]),
            "metros_actuales": float(r[5]),
        }
        for r in rows
    ]

    cubren = [r for r in rollos if r["metros_actuales"] >= metros]
    no_cubren = [r for r in rollos if r["metros_actuales"] < metros]

    if cubren:
        sugerido = min(cubren, key=lambda r: r["metros_actuales"])
    else:
        sugerido = max(no_cubren, key=lambda r: r["metros_actuales"])

    return {
        "sugerido": sugerido,
        "todos": sorted(rollos, key=lambda r: r["metros_actuales"], reverse=True),
    }


@app.get("/rollos/documentos")
def documentos(almacen_id: int, fecha: str = None):
    if fecha is None:
        fecha = datetime.date.today().isoformat()

    conn_r = connect_rollos()
    try:
        cursor = conn_r.cursor()
        cursor.execute(
            "SELECT codigo FROM Almacen WITH(NOLOCK) WHERE id = ?",
            almacen_id,
        )
        alm_row = cursor.fetchone()
        if not alm_row:
            raise HTTPException(status_code=404, detail=f"Almacén {almacen_id} no encontrado")
        codalm = alm_row[0]
    finally:
        conn_r.close()

    conn_n = connect_nava()
    try:
        cursor = conn_n.cursor()

        cursor.execute(
            "SELECT DISTINCT m.ndocu, m.fecha, m.codcli, m.nomcli, m.codven, "
            "  d.codf, d.descr, d.cant, d.umed, m.fecreg, m.flag, "
            "  RTRIM(sbf.nomsub), RTRIM(ven.nomven) "
            "FROM mst01cot m WITH(NOLOCK) "
            "JOIN dtl01cot d WITH(NOLOCK) ON d.cdocu = m.cdocu AND d.ndocu = m.ndocu "
            "LEFT JOIN tbl01sbf sbf WITH(NOLOCK) "
            "  ON LEFT(d.codi, 4) = LEFT(sbf.codsub, 2) + SUBSTRING(sbf.codsub, 4, 2) "
            "LEFT JOIN tbl01ven ven WITH(NOLOCK) ON ven.codven = m.codven "
            "WHERE LEFT(d.codi, 2) = '02' "
            "  AND CAST(m.fecha AS DATE) = ? "
            "ORDER BY m.fecreg DESC",
            fecha,
        )
        cot_rows = cursor.fetchall()

        cursor.execute(
            "SELECT DISTINCT m.ndocu, m.fecha, m.codcli, m.nomcli, m.codven, "
            "  d.codf, d.descr, d.pedi, d.cant, d.umed, m.flag "
            "FROM mst01ped m WITH(NOLOCK) "
            "JOIN dtl01ped d WITH(NOLOCK) ON d.cdocu = m.cdocu AND d.ndocu = m.ndocu "
            "WHERE LEFT(d.codi, 2) = '02' "
            "  AND CAST(m.fecha AS DATE) = ? "
            "ORDER BY m.fecha DESC",
            fecha,
        )
        ped_rows = cursor.fetchall()

    finally:
        conn_n.close()

    cotizaciones = [
        {
            "tipo": "cotizacion",
            "ndocu": r[0],
            "fecha": str(r[1]),
            "codcli": r[2],
            "nomcli": r[3].strip(),
            "codven": r[4],
            "codf": r[5].strip(),
            "descr": r[6].strip(),
            "cant": float(r[7]),
            "umed": r[8].strip(),
            "fecreg": str(r[9]) if r[9] is not None else None,
            "flag": r[10],
            "subfamilia": r[11],
            "nomven": r[12],
        }
        for r in cot_rows
    ]

    pedidos = [
        {
            "tipo": "pedido",
            "ndocu": r[0],
            "fecha": str(r[1]),
            "codcli": r[2],
            "nomcli": r[3].strip(),
            "codven": r[4],
            "codf": r[5].strip(),
            "descr": r[6].strip(),
            "cant_pedida": float(r[7]),
            "cant_despachada": float(r[8]),
            "umed": r[9].strip(),
            "flag": r[10],
        }
        for r in ped_rows
    ]

    return {
        "fecha": fecha,
        "almacen_id": almacen_id,
        "cotizaciones": cotizaciones,
        "pedidos": pedidos,
    }


@app.get("/rollos/descuadre")
def descuadre(almacen_id: int):
    conn_r = connect_rollos()
    try:
        cursor = conn_r.cursor()
        cursor.execute(
            "SELECT tabla_erp, codigo FROM Almacen WITH(NOLOCK) WHERE id = ?",
            almacen_id,
        )
        alm_row = cursor.fetchone()
        if not alm_row:
            raise HTTPException(status_code=404, detail=f"Almacén {almacen_id} no encontrado")
        tabla_erp = alm_row[0]
        if not tabla_erp:
            raise HTTPException(status_code=400, detail=f"Almacén {almacen_id} sin tabla ERP configurada")

        # Solo productos con rollos activos en este almacén
        cursor.execute(
            "SELECT p.codf, ISNULL(SUM(m.metros), 0) AS metros_actuales "
            "FROM Rollo r WITH(NOLOCK) "
            "JOIN Producto p WITH(NOLOCK) ON p.producto_id = r.producto_id "
            "LEFT JOIN Movimiento m WITH(NOLOCK) ON m.id_rollo = r.id_rollo "
            "WHERE r.almacen_id = ? AND r.estado IN ('disponible', 'retazo') "
            "GROUP BY p.codf",
            almacen_id,
        )
        stock_rollos = {row[0].strip(): float(row[1]) for row in cursor.fetchall()}
    finally:
        conn_r.close()

    conn_n = connect_nava()
    try:
        cursor = conn_n.cursor()
        # Solo mangueras del ERP (subfamilia contiene 'MANGUERA')
        cursor.execute(
            f"SELECT RTRIM(prd.codf), prd.stoc "
            f"FROM {tabla_erp} prd WITH(NOLOCK) "
            f"INNER JOIN tbl01sbf sbf WITH(NOLOCK) "
            f"  ON LEFT(prd.codi, 4) = LEFT(sbf.codsub, 2) + SUBSTRING(sbf.codsub, 4, 2) "
            f"WHERE LEFT(prd.codi, 2) = '02' AND prd.estado = 1 "
            f"  AND sbf.nomsub LIKE '%MANGUERA%'"
        )
        stock_erp = {row[0]: float(row[1]) for row in cursor.fetchall()}
    finally:
        conn_n.close()

    # FULL OUTER JOIN por codf: productos con rollos activos O mangueras en ERP
    todos_productos = set(stock_erp.keys()) | set(stock_rollos.keys())
    diferencias = []
    for codf in sorted(todos_productos):
        erp = stock_erp.get(codf, 0.0)
        rollos = stock_rollos.get(codf, 0.0)
        if erp == 0.0 and rollos == 0.0:
            continue
        diff = round(erp - rollos, 3)
        if abs(diff) > 0.001:
            diferencias.append({
                "codf": codf,
                "stock_erp": erp,
                "stock_rollos": rollos,
                "diferencia": diff,
            })

    return {
        "almacen_id": almacen_id,
        "tabla_erp": tabla_erp,
        "productos_cuadrados": len(todos_productos) - len(diferencias),
        "productos_descuadrados": len(diferencias),
        "detalle": diferencias,
    }


@app.get("/config")
def get_config():
    conn = connect_rollos()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT clave, valor FROM Config WITH(NOLOCK)")
        rows = cursor.fetchall()
    finally:
        conn.close()
    return {row[0]: row[1] for row in rows}


@app.put("/config")
def put_config(body: ConfigRequest):
    conn = connect_rollos()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE Config SET valor = ? WHERE clave = ?",
            body.valor, body.clave,
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"Clave '{body.clave}' no existe")
        conn.commit()
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al actualizar config: {e}")
    finally:
        conn.close()

    return {"clave": body.clave, "valor": body.valor, "usuario": body.usuario}


class HistorialCargarRequest(BaseModel):
    numero_wa: str


class HistorialGuardarRequest(BaseModel):
    numero_wa: str
    user_msg: str
    assistant_msg: str


@app.post("/historial/cargar")
def historial_cargar(body: HistorialCargarRequest):
    conn = connect_asistente()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT TOP 10 rol, contenido, timestamp "
            "FROM Conversacion WITH(NOLOCK) "
            "WHERE numero_wa = ? "
            "ORDER BY timestamp DESC",
            body.numero_wa,
        )
        filas = cursor.fetchall()
    finally:
        conn.close()

    if not filas:
        return {"historial": []}

    mas_reciente = filas[0][2]
    if isinstance(mas_reciente, str):
        mas_reciente = datetime.datetime.fromisoformat(mas_reciente)
    if datetime.datetime.utcnow() - mas_reciente > datetime.timedelta(hours=2):
        return {"historial": []}

    historial = [{"role": r, "content": c} for r, c, _ in reversed(filas)]
    return {"historial": historial}


@app.post("/historial/guardar")
def historial_guardar(body: HistorialGuardarRequest):
    conn = connect_asistente()
    try:
        cursor = conn.cursor()
        ahora = datetime.datetime.utcnow()
        cursor.execute(
            "INSERT INTO Conversacion (numero_wa, rol, contenido, timestamp) VALUES (?, ?, ?, ?)",
            body.numero_wa, "user", body.user_msg, ahora,
        )
        cursor.execute(
            "INSERT INTO Conversacion (numero_wa, rol, contenido, timestamp) VALUES (?, ?, ?, ?)",
            body.numero_wa, "assistant", body.assistant_msg, ahora,
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar historial: {e}")
    finally:
        conn.close()

    return {"ok": True}
