"""
Llenado de medidas estructuradas (campos personalizados) en prd0101 (BdNava01).

Familia por defecto: ESPIGA 90 HEMBRA JIC -> codigos '26791-XX-YY', donde
  XX = medida de la rosca  -> Usr_002 (med_rosca_1)
  YY = medida de la manguera -> Usr_001 (med_manguera)

================================  SEGURIDAD  ================================
- DRY-RUN por defecto: sin --apply NO escribe absolutamente nada.
- Solo toca Usr_001 y Usr_002. NUNCA Usr_003/Usr_004 ni otra columna.
- Solo RELLENA celdas vacias (WHERE ... IS NULL OR ='' ). Es imposible que
  pise un valor ya cargado: la condicion del UPDATE excluye celdas con datos.
- Conflictos (valor existente != derivado del codigo) se REPORTAN; no se tocan.
- Codigos que no mapean limpio (sufijos, espacios) se SALTAN y se reportan.
- Con --apply: pide confirmacion escrita, corre en UNA transaccion (rollback
  automatico ante cualquier error) y deja un archivo de reversa .sql.
- Alcance acotado por LIKE '<prefijo>%' y LEFT(codi,2)='02' AND estado=1.

RECOMENDADO: correr FUERA de horario laboral y con un backup de BdNava01
(o al menos export de las columnas Usr_001/Usr_002 de la familia) ya hecho.

Uso:
  set DB_PASSWORD=...                      (y DB_USER=... si necesita escritura)
  python fill_medidas_piloto.py           # DRY-RUN (no escribe)
  python fill_medidas_piloto.py --apply   # aplica (pide confirmacion)
  python fill_medidas_piloto.py --prefijo 24791-   # otra familia (mismo patron XX-YY)
  python fill_medidas_piloto.py --sufijos          # tolera variantes -04-04C, -08-06H67, -06-06PK(PROM)
  python fill_medidas_piloto.py --sufijos --apply  # aplica incluyendo variantes con sufijo
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


def conectar():
    pwd = os.environ.get("DB_PASSWORD")
    user = os.environ.get("DB_USER", "cisge_asistente")
    if not pwd:
        sys.exit("ERROR: falta DB_PASSWORD en el entorno.")
    return pyodbc.connect(
        "DRIVER={SQL Server};SERVER=192.168.2.13;DATABASE=BdNava01;"
        f"UID={user};PWD={pwd}"
    )


def medidas_desde_codigo(codf, sufijos=False):
    """'26791-04-03' -> (med_rosca_1, med_manguera) = ('1/4','3/16').
    Devuelve None si el codigo no mapea limpio.
    Con sufijos=True tolera variantes tipo '26791-08-06H67' / '-04-04C' /
    '-06-06PK(PROM)': toma los digitos INICIALES de cada segmento (el nominal),
    ignorando el sufijo de variante (no cambia la medida, es marca de material/
    promo/empaque)."""
    parts = codf.split("-")
    if len(parts) != 3:
        return None
    seg_rosca, seg_mang = parts[1].strip(), parts[2].strip()
    if sufijos:
        mr = re.match(r'^(\d+)', seg_rosca)
        mm = re.match(r'^(\d+)', seg_mang)
        if not (mr and mm):
            return None
        seg_rosca, seg_mang = mr.group(1), mm.group(1)
    if seg_rosca not in MEDIDA_NOMINAL or seg_mang not in MEDIDA_NOMINAL:
        return None
    return MEDIDA_NOMINAL[seg_rosca], MEDIDA_NOMINAL[seg_mang]


def main():
    aplicar = "--apply" in sys.argv
    sufijos = "--sufijos" in sys.argv   # tolerar variantes con sufijo (C/PK/PROM/...)
    prefijo = "26791-"
    if "--prefijo" in sys.argv:
        prefijo = sys.argv[sys.argv.index("--prefijo") + 1]

    conn = conectar()
    cur = conn.cursor()

    # Codigos distintos de la familia
    cur.execute(
        "SELECT DISTINCT RTRIM(codf) FROM prd0101 WITH(NOLOCK) "
        "WHERE codf LIKE ? AND LEFT(codi,2)='02' AND estado=1",
        prefijo + "%",
    )
    codfs = sorted(r[0] for r in cur.fetchall())

    a_llenar = []     # (codf, rosca1, mang, n_filas_manguera_vacias, n_filas_rosca_vacias)
    conflictos = []   # (codf, detalle)
    sin_mapeo = []    # codf

    EMPTY = "(Usr_{c} IS NULL OR LTRIM(RTRIM(Usr_{c}))='')"

    for codf in codfs:
        m = medidas_desde_codigo(codf, sufijos=sufijos)
        if not m:
            sin_mapeo.append(codf)
            continue
        rosca1, mang = m

        # filas vacias por columna (las unicas que el UPDATE tocaria)
        cur.execute(
            f"SELECT COUNT(*) FROM prd0101 WITH(NOLOCK) WHERE codf=? AND LEFT(codi,2)='02' "
            f"AND {EMPTY.format(c='001')}", codf)
        n_mang = cur.fetchone()[0]
        cur.execute(
            f"SELECT COUNT(*) FROM prd0101 WITH(NOLOCK) WHERE codf=? AND LEFT(codi,2)='02' "
            f"AND {EMPTY.format(c='002')}", codf)
        n_rosca = cur.fetchone()[0]

        # conflictos: valor existente (no vacio) distinto del derivado
        cur.execute(
            "SELECT DISTINCT RTRIM(Usr_001) FROM prd0101 WITH(NOLOCK) WHERE codf=? AND LEFT(codi,2)='02' "
            "AND Usr_001 IS NOT NULL AND LTRIM(RTRIM(Usr_001))<>'' AND RTRIM(Usr_001)<>?", codf, mang)
        for (v,) in cur.fetchall():
            conflictos.append((codf, f"med_manguera BD={v!r} vs codigo={mang!r}"))
        cur.execute(
            "SELECT DISTINCT RTRIM(Usr_002) FROM prd0101 WITH(NOLOCK) WHERE codf=? AND LEFT(codi,2)='02' "
            "AND Usr_002 IS NOT NULL AND LTRIM(RTRIM(Usr_002))<>'' AND RTRIM(Usr_002)<>?", codf, rosca1)
        for (v,) in cur.fetchall():
            conflictos.append((codf, f"med_rosca_1 BD={v!r} vs codigo={rosca1!r}"))

        if n_mang or n_rosca:
            a_llenar.append((codf, rosca1, mang, n_mang, n_rosca))

    # ---------------- Reporte ----------------
    print("\n" + "!" * 70)
    print("MAPEO ASUMIDO (patron ESPIGA):  segmento1 = ROSCA (Usr_002),")
    print("                                segmento2 = MANGUERA (Usr_001).")
    print("NO usar en ADAPTADORES (dos roscas) ni otras familias sin revisar")
    print("el mapeo: ahi el segmento2 es rosca_2, NO manguera.")
    print("!" * 70)
    print(f"\nFamilia (prefijo): {prefijo!r}   codigos encontrados: {len(codfs)}")
    print(f"A rellenar (tienen celdas vacias): {len(a_llenar)}")
    print(f"Conflictos (valor existente != codigo, NO se tocan): {len(conflictos)}")
    print(f"Sin mapeo (codigo no es NN-NN nominal, se saltan): {len(sin_mapeo)}\n")

    print("  CODIGO            -> med_rosca_1 / med_manguera   (filas vacias r1/mang)")
    for codf, rosca1, mang, n_mang, n_rosca in a_llenar:
        print(f"  {codf:18} -> r1={rosca1:6} mang={mang:6}   (vacias: r1={n_rosca} mang={n_mang})")

    if conflictos:
        print("\n  CONFLICTOS (revisar a mano, el script NO los toca):")
        for codf, det in conflictos:
            print(f"    {codf:18} {det}")
    if sin_mapeo:
        print("\n  SIN MAPEO (se saltan):")
        for codf in sin_mapeo:
            print(f"    {codf}")

    if not aplicar:
        print("\n*** DRY-RUN: no se escribio nada. Revisa el reporte. ***")
        print("*** Para aplicar: python fill_medidas_piloto.py --apply ***")
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

    # Archivo de reversa: deja las columnas tocadas de nuevo vacias
    rb = f"rollback_medidas_{datetime.datetime.now():%Y%m%d_%H%M%S}.sql"
    with open(rb, "w", encoding="utf-8") as f:
        f.write(f"-- Reversa del llenado piloto {prefijo} {datetime.datetime.now()}\n")
        for codf, rosca1, mang, n_mang, n_rosca in a_llenar:
            if n_mang:
                f.write(f"UPDATE prd0101 SET Usr_001='' WHERE codf='{codf}' AND RTRIM(Usr_001)='{mang}';\n")
            if n_rosca:
                f.write(f"UPDATE prd0101 SET Usr_002='' WHERE codf='{codf}' AND RTRIM(Usr_002)='{rosca1}';\n")

    # Aplicar en UNA transaccion. El WHERE solo toca celdas vacias.
    w = conn.cursor()
    tocadas = 0
    try:
        for codf, rosca1, mang, n_mang, n_rosca in a_llenar:
            if n_mang:
                w.execute(
                    f"UPDATE prd0101 SET Usr_001=? WHERE codf=? AND LEFT(codi,2)='02' "
                    f"AND {EMPTY.format(c='001')}", mang, codf)
                tocadas += w.rowcount
            if n_rosca:
                w.execute(
                    f"UPDATE prd0101 SET Usr_002=? WHERE codf=? AND LEFT(codi,2)='02' "
                    f"AND {EMPTY.format(c='002')}", rosca1, codf)
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
