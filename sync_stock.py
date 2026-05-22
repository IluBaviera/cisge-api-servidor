import pyodbc
import json
import datetime

# Conexion SQL Server
conn = pyodbc.connect(
    'DRIVER={SQL Server};'
    'SERVER=192.168.2.13;'
    'DATABASE=BdNava01;'
    'UID=cisge_asistente;'
    'PWD=Asistente@2026!'
)
cursor = conn.cursor()

# Consultar stock de almacenes activos
almacenes = {
    'prd0101': 'Almacen Lima Centro',
    'prd0108': 'Almacen Colonial',
    'prd0112': 'Almacen San Luis 1',
    'prd0118': 'Almacen San Luis 2'
}

stock = {}
for tabla, nombre in almacenes.items():
    cursor.execute(f"""
        SELECT RTRIM(codi), RTRIM(codf), RTRIM(descr), 
               RTRIM(marc), stoc, RTRIM(umed)
        FROM {tabla}
        WHERE stoc > 0
    """)
    for row in cursor.fetchall():
        codi = row[0]
        if codi not in stock:
            stock[codi] = {
                'codf': row[1],
                'descr': row[2],
                'marc': row[3],
                'umed': row[5],
                'almacenes': {}
            }
        stock[codi]['almacenes'][nombre] = float(row[4])

conn.close()

# Guardar JSON
resultado = {
    'actualizado': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'total_productos': len(stock),
    'stock': stock
}

ruta = r'G:\Mi unidad\CISGE-Sistema\stock\stock.json'
with open(ruta, 'w', encoding='utf-8') as f:
    json.dump(resultado, f, ensure_ascii=False, indent=2)

print(f"OK - {len(stock)} productos guardados en {ruta}")
print(f"Actualizado: {resultado['actualizado']}")
