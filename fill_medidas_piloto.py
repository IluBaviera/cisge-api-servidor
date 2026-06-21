"""
Llenado de medidas estructuradas (campos personalizados) en prd0101 (BdNava01).

Deriva med_rosca_1 / med_rosca_2 / med_manguera / med_tubo del codigo, segun la
CLASE DE MAPEO (--mapeo) apropiada para la familia:

  espiga     (default): seg1 = rosca inch  -> Usr_002 (med_rosca_1)
                        seg2 = manguera    -> Usr_001 (med_manguera)
  adaptador            : seg1 = rosca_1 inch-> Usr_002
                        seg2 = rosca_2 inch-> Usr_003 (med_rosca_2)
  metrica              : seg1 = hilo metrico-> Usr_002 ("M22x1.5", con paso DIN)
                        seg2 = manguera    -> Usr_001
                        + tubo DIN del hilo-> Usr_004 (med_tubo)

================================  SEGURIDAD  ================================
- DRY-RUN por defecto: sin --apply NO escribe absolutamente nada.
- Solo RELLENA celdas vacias (WHERE ... IS NULL OR ='' ): imposible pisar un
  valor ya cargado.
- Conflictos (valor existente != derivado) se REPORTAN; no se tocan.
- Codigos que no mapean limpio (sufijos, espacios) se SALTAN y se reportan.
- Con --apply: pide confirmacion escrita, corre en UNA transaccion (rollback
  automatico ante error) y deja un archivo de reversa .sql.
- Alcance acotado por LIKE '<prefijo>%' y LEFT(codi,2)='02' AND estado=1.

RECOMENDADO: correr FUERA de horario laboral, con backup de la familia hecho.

Uso:
  set DB_PASSWORD=...                       (y DB_USER=... si necesita escritura)
  python fill_medidas_piloto.py                                  # espiga, 26791-, dry-run
  python fill_medidas_piloto.py --prefijo 24791-                 # otra familia espiga
  python fill_medidas_piloto.py --mapeo metrica --prefijo 20491T-   # metrica liviana 90
  python fill_medidas_piloto.py --mapeo metrica --prefijo 20491-    # idem sin T
  python fill_medidas_piloto.py --sufijos                        # tolera variantes -04-04C, etc.
  ... agregar --apply para escribir (pide confirmacion).
"""
import os
import re
import sys
import datetime
import pyodbc

# Tabla nominal ISO -> pulgadas (igual que el motor del asistente)
MEDIDA_NOMINAL = {
    "02": "1/8", "03": "3/16", "04": "1/4", "05": "5/16", "06": "3/8",
    "08": "1/2", "10": "5/8", "12": "3/4", "14": "7/8", "16": "1",
    "20": "1 1/4", "24": "1 1/2", "32": "2", "40": "2 1/2", "48": "3",
    "56": "3 1/2",
}

# DIN 2353 / ISO 8434-1 — hilo metrico -> paso y tubo (serie LIVIANA / L).
# M20 y M24 son de la serie pesada (S); se incluyen por aparecer en el catalogo
# CISGE bajo grupos "liviana" (posible inconsistencia) -> REVISAR en el reporte.
PASO_METRICO = {  # paso por tamaño de hilo (ambas series): <=M27 -> 1.5, >=M30 -> 2
    "12": "1.5", "14": "1.5", "16": "1.5", "18": "1.5", "20": "1.5", "22": "1.5",
    "24": "1.5", "26": "1.5", "27": "1.5", "30": "2", "36": "2", "42": "2",
    "45": "2", "52": "2",
}
# DIN 2353 — hilo -> tubo (mm). Serie LIVIANA (L) y PESADA (S).
TUBO_LIVIANA = {
    "12": "6", "14": "8", "16": "10", "18": "12", "20": "12", "22": "15",
    "24": "16", "26": "18", "27": "18", "30": "22", "36": "28", "45": "35", "52": "42",
}
TUBO_PESADA = {
    "14": "6", "16": "8", "18": "10", "20": "12", "22": "14", "24": "16",
    "30": "20", "36": "25", "42": "30", "52": "38",
}
_METRICO_SERIE_S = {"20", "24"}   # en familia liviana, son hilos S -> avisar

COL_NOMBRE = {"001": "med_manguera", "002": "med_rosca_1",
              "003": "med_rosca_2", "004": "med_tubo"}


def conectar():
    pwd = os.environ.get("DB_PASSWORD")
    user = os.environ.get("DB_USER", "cisge_asistente")
    if not pwd:
        sys.exit("ERROR: falta DB_PASSWORD en el entorno.")
    return pyodbc.connect(
        "DRIVER={SQL Server};SERVER=192.168.2.13;DATABASE=BdNava01;"
        f"UID={user};PWD={pwd}"
    )


def _segmentos(codf, sufijos):
    """Devuelve (s1, s2) o None. Con sufijos=True toma los digitos iniciales."""
    parts = codf.split("-")
    if len(parts) != 3:
        return None
    # 1er segmento debe ser solo dígitos (con T opcional): excluye variantes con
    # letras como '20411PS' (pasador) → así un prefijo amplio no las arrastra.
    if not re.match(r'^\d+T?$', parts[0].strip()):
        return None
    s1, s2 = parts[1].strip(), parts[2].strip()
    if sufijos:
        m1, m2 = re.match(r'^(\d+)', s1), re.match(r'^(\d+)', s2)
        if not (m1 and m2):
            return None
        s1, s2 = m1.group(1), m2.group(1)
    return s1, s2


def valores_desde_codigo(codf, mapeo, sufijos=False, descr=""):
    """Devuelve dict {col: valor} (col en COL_NOMBRE) o None si no mapea limpio.
    Para métricas usa la descripción como guarda de serie (LIVIANA vs PESADA)
    para no contaminar entre series cuando un prefijo trae códigos mezclados."""
    seg = _segmentos(codf, sufijos)
    if not seg:
        return None
    s1, s2 = seg
    du = (descr or "").upper()

    if mapeo == "espiga":
        if s1 not in MEDIDA_NOMINAL or s2 not in MEDIDA_NOMINAL:
            return None
        return {"002": MEDIDA_NOMINAL[s1], "001": MEDIDA_NOMINAL[s2]}

    if mapeo == "adaptador":
        if s1 not in MEDIDA_NOMINAL or s2 not in MEDIDA_NOMINAL:
            return None
        return {"002": MEDIDA_NOMINAL[s1], "003": MEDIDA_NOMINAL[s2]}

    if mapeo in ("metrica", "metrica_pesada"):
        if s1 not in PASO_METRICO or s2 not in MEDIDA_NOMINAL:
            return None
        # guarda de serie: el mapeo debe coincidir con la descripción
        if mapeo == "metrica" and "PESADA" in du:
            return None
        if mapeo == "metrica_pesada" and "LIVIANA" in du:
            return None
        tabla = TUBO_PESADA if mapeo == "metrica_pesada" else TUBO_LIVIANA
        vals = {"002": f"M{int(s1)}x{PASO_METRICO[s1]}", "001": MEDIDA_NOMINAL[s2]}
        if s1 in tabla:
            vals["004"] = tabla[s1]
        return vals

    sys.exit(f"ERROR: mapeo desconocido '{mapeo}' (usa espiga|adaptador|metrica|metrica_pesada)")


def main():
    aplicar = "--apply" in sys.argv
    sufijos = "--sufijos" in sys.argv
    mapeo = "espiga"
    if "--mapeo" in sys.argv:
        mapeo = sys.argv[sys.argv.index("--mapeo") + 1]
    prefijo = "26791-"
    if "--prefijo" in sys.argv:
        prefijo = sys.argv[sys.argv.index("--prefijo") + 1]

    conn = conectar()
    cur = conn.cursor()
    cur.execute(
        "SELECT RTRIM(codf), MAX(RTRIM(descr)) FROM prd0101 WITH(NOLOCK) "
        "WHERE codf LIKE ? AND LEFT(codi,2)='02' AND estado=1 "
        "GROUP BY RTRIM(codf)",
        prefijo + "%",
    )
    descr_por_codf = {r[0]: (r[1] or "") for r in cur.fetchall()}
    codfs = sorted(descr_por_codf)

    EMPTY = "(Usr_{c} IS NULL OR LTRIM(RTRIM(Usr_{c}))='')"
    a_llenar = []     # (codf, vals_dict, {col: (valor, n_vacias)})
    conflictos = []   # (codf, detalle)
    sin_mapeo = []    # codf
    avisos = []       # (codf, aviso) p.ej. metrico serie S

    for codf in codfs:
        vals = valores_desde_codigo(codf, mapeo, sufijos, descr_por_codf[codf])
        if not vals:
            sin_mapeo.append(codf)
            continue

        # aviso metrico serie S (M20/M24 en familia liviana)
        if mapeo == "metrica":
            seg = _segmentos(codf, sufijos)
            if seg and seg[0] in _METRICO_SERIE_S:
                avisos.append((codf, f"M{int(seg[0])} es serie S (no L) — verificar tubo={vals.get('004','?')}"))

        cols_llenar = {}
        for col, val in vals.items():
            cur.execute(
                f"SELECT COUNT(*) FROM prd0101 WITH(NOLOCK) WHERE codf=? AND LEFT(codi,2)='02' "
                f"AND {EMPTY.format(c=col)}", codf)
            n = cur.fetchone()[0]
            cur.execute(
                f"SELECT DISTINCT RTRIM(Usr_{col}) FROM prd0101 WITH(NOLOCK) WHERE codf=? "
                f"AND LEFT(codi,2)='02' AND Usr_{col} IS NOT NULL "
                f"AND LTRIM(RTRIM(Usr_{col}))<>'' AND RTRIM(Usr_{col})<>?", codf, val)
            for (v,) in cur.fetchall():
                conflictos.append((codf, f"{COL_NOMBRE[col]} BD={v!r} vs codigo={val!r}"))
            if n:
                cols_llenar[col] = (val, n)
        if cols_llenar:
            a_llenar.append((codf, vals, cols_llenar))

    # ---------------- Reporte ----------------
    print(f"\n=== MAPEO: {mapeo}  |  Familia (prefijo): {prefijo!r} ===")
    print(f"codigos encontrados: {len(codfs)}")
    print(f"a rellenar (con celdas vacias): {len(a_llenar)}")
    print(f"conflictos (valor != codigo, NO se tocan): {len(conflictos)}")
    print(f"sin mapeo (se saltan): {len(sin_mapeo)}\n")

    for codf, vals, cols_llenar in a_llenar:
        desc = ", ".join(f"{COL_NOMBRE[c]}={v}" for c, v in vals.items())
        vac = ",".join(f"{COL_NOMBRE[c]}={n}" for c, (v, n) in cols_llenar.items())
        print(f"  {codf:18} -> {desc:48} (vacias: {vac})")

    if avisos:
        print("\n  AVISOS (revisar):")
        for codf, a in avisos:
            print(f"    {codf:18} {a}")
    if conflictos:
        print("\n  CONFLICTOS (revisar a mano, NO se tocan):")
        for codf, det in conflictos:
            print(f"    {codf:18} {det}")
    if sin_mapeo:
        print(f"\n  SIN MAPEO ({len(sin_mapeo)}, se saltan): {', '.join(sin_mapeo[:40])}"
              + (" ..." if len(sin_mapeo) > 40 else ""))

    if not aplicar:
        print("\n*** DRY-RUN: no se escribio nada. Revisa el reporte. ***")
        conn.close()
        return
    if not a_llenar:
        print("\nNada que aplicar.")
        conn.close()
        return

    print(f"\nVas a RELLENAR celdas vacias en {len(a_llenar)} codigos de prd0101 (BdNava01).")
    if input("Escribe exactamente  APLICAR  para confirmar: ").strip() != "APLICAR":
        print("Cancelado. No se escribio nada.")
        conn.close()
        return

    rb = f"rollback_medidas_{datetime.datetime.now():%Y%m%d_%H%M%S}.sql"
    with open(rb, "w", encoding="utf-8") as f:
        f.write(f"-- Reversa llenado {mapeo} {prefijo} {datetime.datetime.now()}\n")
        for codf, vals, cols_llenar in a_llenar:
            for col, (val, n) in cols_llenar.items():
                f.write(f"UPDATE prd0101 SET Usr_{col}='' WHERE codf='{codf}' "
                        f"AND RTRIM(Usr_{col})='{val}';\n")

    w = conn.cursor()
    tocadas = 0
    try:
        for codf, vals, cols_llenar in a_llenar:
            for col, (val, n) in cols_llenar.items():
                w.execute(
                    f"UPDATE prd0101 SET Usr_{col}=? WHERE codf=? AND LEFT(codi,2)='02' "
                    f"AND {EMPTY.format(c=col)}", val, codf)
                tocadas += w.rowcount
        conn.commit()
        print(f"\nOK. Transaccion confirmada. Filas actualizadas: {tocadas}")
        print(f"Reversa guardada en: {rb}")
    except Exception as e:
        conn.rollback()
        print(f"\nERROR durante el UPDATE -> ROLLBACK hecho, BD intacta.\n{e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
