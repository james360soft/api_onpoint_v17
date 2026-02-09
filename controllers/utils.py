# -*- coding: utf-8 -*-
# utils.py - Funciones auxiliares reutilizables


def get_barcodes(product, move_id=0, batch_id=0):
    """
    Retorna códigos de barras adicionales del producto en formato estandarizado

    Args:
        product: Objeto product.product
        move_id: ID de la línea de movimiento (opcional)
        batch_id: ID del batch/picking (opcional)

    Returns:
        Lista de diccionarios con información de códigos de barras
    """
    return [
        {
            "barcode": b.name,
            "cantidad": 1,
            "id_product": product.id,
            "id_move": move_id,
            "batch_id": batch_id,
            "product_id": product.id,
        }
        for b in getattr(product, "barcode_ids", [])
        if b.name
    ]


def get_packagings(product, move_id=0, batch_id=0):
    """
    Retorna empaques del producto en formato estandarizado

    Args:
        product: Objeto product.product
        move_id: ID de la línea de movimiento (opcional)
        batch_id: ID del batch/picking (opcional)

    Returns:
        Lista de diccionarios con información de empaques
    """
    return [
        {
            "barcode": p.barcode,
            "cantidad": p.qty,
            "id_product": p.product_id.id,
            "id_move": move_id,
            "batch_id": batch_id,
            "product_id": p.product_id.id,
        }
        for p in getattr(product, "packaging_ids", [])
        if p.barcode
    ]


def format_time_from_seconds(time_value):
    """Convierte segundos a formato HH:MM:SS"""
    if not time_value:
        return "00:00:00"
    try:
        total_seconds = float(time_value)
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except:
        return "00:00:00"
