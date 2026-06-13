# hello_finance.py
# Primer script: calcula el margen de una venta.

ingresos = 100000
costos = 72000

ganancia = ingresos - costos
margen = ganancia / ingresos * 100

print("Ingresos:", ingresos)
print("Costos:", costos)
print("Ganancia:", ganancia)
print("Margen:", round(margen, 1), "%")