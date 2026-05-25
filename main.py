import os
import datetime
import pyodbc
from fastapi import FastAPI, HTTPException

app = FastAPI(title="CISGE Stock API")


def get_stock_data() -> dict:
    password = os.environ.get("DB_PASSWORD")
    if not password:
        raise HTTPException(status_code=500, detail="DB_PASSWORD no configurada")

    try:
        conn = pyodbc.connect(
            "DRIVER={SQL Server};"
            "SERVER=192.168.2.13;"
            "DATABASE=BdNava01;"
            "UID=cisge_asistente;"
            f"PWD={password}"
        )
    except pyodbc.Error as e:
        raise HTTPException(status_code=503, detail=f"Error de conexión a BD: {e}")

    almacenes = {
        "prd0101": "Almacen Lima Centro",
        "prd0108": "Almacen Colonial",
        "prd0112": "Almacen San Luis 1",
        "prd0118": "Almacen San Luis 2",
    }

    stock = {}
    try:
        cursor = conn.cursor()
        for tabla, nombre in almacenes.items():
            cursor.execute(
                f"SELECT RTRIM(codi), RTRIM(codf), RTRIM(descr), "
                f"RTRIM(marc), stoc, RTRIM(umed), vvus "
                f"FROM {tabla} WITH(NOLOCK) WHERE LEFT(codi, 2) = '02'"
            )
            for row in cursor.fetchall():
                codi = row[0]
                if codi not in stock:
                    stock[codi] = {
                        "codf": row[1],
                        "descr": row[2],
                        "marc": row[3],
                        "umed": row[5],
                        "precio": float(row[6]) if row[6] is not None else 0.0,
                        "almacenes": {},
                    }
                stock[codi]["almacenes"][nombre] = float(row[4])
    finally:
        conn.close()

    return {
        "actualizado": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_productos": len(stock),
        "stock": stock,
    }


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/stock")
def stock():
    return get_stock_data()
