from calendar import c
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz
from datetime import date


class TransaccionRecepcionController(http.Controller):

    @http.route("/api/recepciones", auth="user", type="json", methods=["GET"])
    def get_recepciones(self):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            array_recepciones = []

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # ✅ Obtener recepciones pendientes directamente de los almacenes permitidos
            for warehouse in allowed_warehouses:
                # Buscar todas las recepciones pendientes (no completadas ni canceladas) para este almacén
                recepciones_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            ("picking_type_code", "=", "incoming"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            # ("is_return_picking", "=", False),
                            # ("user_id", "in", [user.id, False]),  # Asignadas al usuario o sin asignar
                            ("responsable_id", "in", [user.id, False]),  # Asignadas al usuario o sin asignar
                        ]
                    )
                )

                for picking in recepciones_pendientes:
                    # Verificar si hay movimientos pendientes
                    # En Odoo 17, move_lines se cambió a move_ids o move_ids_without_package
                    movimientos_pendientes = picking.move_ids.filtered(lambda m: m.state in ["confirmed", "assigned"])

                    # Si no hay movimientos pendientes, omitir esta recepción
                    if not movimientos_pendientes:
                        continue

                    # Obtener la orden de compra relacionada (si existe)
                    purchase_order = picking.purchase_id or (picking.origin and request.env["purchase.order"].sudo().search([("name", "=", picking.origin)], limit=1))

                    # Calcular peso total - cambiando product_qty por product_uom_qty
                    peso_total = sum(move.product_id.weight * move.product_uom_qty for move in movimientos_pendientes if move.product_id.weight)

                    # Calcular número de ítems (suma total de cantidades)
                    numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

                    manejo_temperatura = False

                    productos_con_temperatura = picking.move_ids.mapped("product_id").filtered(lambda p: hasattr(p, "temperature_control") and p.temperature_control)
                    if productos_con_temperatura:
                        manejo_temperatura = True

                    recepcion_info = {
                        "id": picking.id,
                        "name": picking.name,  # Nombre de la recepción
                        "fecha_creacion": picking.create_date,  # Fecha con hora
                        "proveedor_id": picking.partner_id.id or 0,
                        "proveedor": picking.partner_id.name or "",
                        "location_dest_id": picking.location_dest_id.id or "",
                        "location_dest_name": picking.location_dest_id.display_name or "",
                        "purchase_order_id": purchase_order.id if purchase_order else 0,
                        "purchase_order_name": purchase_order.name if purchase_order else "",  # Orden de compra
                        "numero_entrada": picking.name or "",  # Número de entrada
                        "peso_total": peso_total,  # Peso total
                        "numero_lineas": 0,  # Número de líneas (productos)
                        "numero_items": 0,  # Número de ítems (cantidades)
                        "state": picking.state,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "responsable_id": picking.responsable_id.id if picking.responsable_id else 0,
                        "responsable": picking.responsable_id.name if picking.responsable_id else "",
                        "picking_type": picking.picking_type_id.name,
                        "backorder_id": picking.backorder_id.id if picking.backorder_id else 0,
                        "backorder_name": picking.backorder_id.name if picking.backorder_id else "",  # Nombre del backorder
                        # Verificar si los campos personalizados existen
                        "start_time_reception": picking.start_time_reception or "",
                        "end_time_reception": picking.end_time_reception or "",
                        "picking_type_code": picking.picking_type_code,
                        "show_check_availability": picking.show_check_availability if hasattr(picking, "show_check_availability") else False,
                        "maneja_temperatura": manejo_temperatura,
                        "temperatura": picking.temperature if hasattr(picking, "temperature") else 0,
                        "lineas_recepcion": [],
                        "lineas_recepcion_enviadas": [],
                    }

                    # ✅ Procesar solo las líneas pendientes
                    for move in movimientos_pendientes:
                        product = move.product_id
                        purchase_line = move.purchase_line_id

                        cantidad_faltante = move.product_uom_qty - sum(l.quantity for l in move.move_line_ids if l.is_done_item)

                        # Obtener cantidad ordenada
                        quantity_ordered = 0
                        if purchase_line and purchase_line.product_uom_qty:
                            quantity_ordered = purchase_line.product_uom_qty
                        else:
                            quantity_ordered = move.product_uom_qty

                        # En Odoo 17, quantity_done ya no existe, se usa quantity
                        quantity_done = move.quantity

                        # # ⚠️ Saltar líneas totalmente recepcionadas
                        # if quantity_done < quantity_ordered:

                        if not move.picked:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search([("product_id", "=", product.id)], order="expiration_date asc", limit=1)
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": product.expiration_time if hasattr(product, "expiration_time") else "",
                                "other_barcodes": array_barcodes,
                                "product_packing": array_packing,
                                "quantity_ordered": purchase_line.product_uom_qty if purchase_line else move.product_uom_qty,
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        elif cantidad_faltante > 0:
                            # Obtener códigos de barras adicionales
                            array_barcodes = []
                            if hasattr(product, "barcode_ids"):
                                array_barcodes = [
                                    {
                                        "barcode": barcode.name,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for barcode in product.barcode_ids
                                    if barcode.name
                                ]

                            # Obtener empaques del producto
                            array_packing = []
                            if hasattr(product, "packaging_ids"):
                                array_packing = [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_move": move.id,
                                        "id_product": product.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ]

                            # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                            fecha_vencimiento = ""
                            if product.tracking == "lot":
                                lot = request.env["stock.lot"].search([("product_id", "=", product.id)], order="expiration_date asc", limit=1)
                                if lot and hasattr(lot, "expiration_date"):
                                    fecha_vencimiento = lot.expiration_date

                            # Generar información de la línea de recepción
                            linea_info = {
                                "id": move.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "state": move.state,
                                "product_id": product.id,
                                "product_name": product.name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "fecha_vencimiento": fecha_vencimiento or "",
                                "dias_vencimiento": product.expiration_time if hasattr(product, "expiration_time") else "",
                                "other_barcodes": array_barcodes,
                                "product_packing": array_packing,
                                "quantity_ordered": purchase_line.product_uom_qty if purchase_line else move.product_uom_qty,
                                "quantity_to_receive": move.product_uom_qty,
                                # "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "cantidad_faltante": cantidad_faltante,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # ✅ Agregar las líneas de move_line que tengan is_done_item en True
                        # Verificación para campos personalizados

                        move_lines_done = move.move_line_ids.filtered(lambda ml: ml.is_done_item)
                        for move_line in move_lines_done:
                            cantidad_faltante = move.product_uom_qty - move_line.quantity

                            # Crear información de la línea enviada
                            linea_enviada_info = {
                                "id": move_line.id,
                                "id_move_line": move_line.id,
                                "id_move": move.id,
                                "id_recepcion": picking.id,
                                "product_id": product.id,
                                "product_name": product.name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "quantity_ordered": purchase_line.product_uom_qty if purchase_line else move.product_uom_qty,
                                "quantity_to_receive": move.product_uom_qty,
                                "quantity_done": move_line.quantity,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                                "location_dest_id": move_line.location_dest_id.id or 0,
                                "location_dest_name": move_line.location_dest_id.display_name or "",
                                "location_dest_barcode": move_line.location_dest_id.barcode or "",
                                "location_id": move_line.location_id.id or 0,
                                "location_name": move_line.location_id.display_name or "",
                                "location_barcode": move_line.location_id.barcode or "",
                                # Campos personalizados con manejo de fallback
                                "is_done_item": move_line.is_done_item if hasattr(move_line, "is_done_item") else (move_line.quantity > 0),
                                "date_transaction": move_line.date_transaction if hasattr(move_line, "date_transaction") else "",
                                "observation": move_line.new_observation if hasattr(move_line, "new_observation") else "",
                                "time": move_line.time if hasattr(move_line, "time") else "",
                                "user_operator_id": move_line.user_operator_id.id if hasattr(move_line, "user_operator_id") and move_line.user_operator_id else 0,
                            }

                            # Agregar información del lote si existe
                            if move_line.lot_id:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": move_line.lot_id.id,
                                        "lot_name": move_line.lot_id.name,
                                        "fecha_vencimiento": move_line.lot_id.expiration_date if hasattr(move_line.lot_id, "expiration_date") else "",
                                    }
                                )
                            elif move_line.lot_name:
                                linea_enviada_info.update(
                                    {
                                        "lot_id": 0,
                                        "lot_name": move_line.lot_name,
                                        "fecha_vencimiento": "",
                                    }
                                )

                            recepcion_info["lineas_recepcion_enviadas"].append(linea_enviada_info)

                    # Solo añadir recepciones que tengan líneas pendientes
                    if recepcion_info["lineas_recepcion"]:
                        recepcion_info["numero_lineas"] = len(recepcion_info["lineas_recepcion"])
                        recepcion_info["numero_items"] = sum(linea["quantity_to_receive"] for linea in recepcion_info["lineas_recepcion"])

                        array_recepciones.append(recepcion_info)

            return {"code": 200, "result": array_recepciones}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/recepciones/batchs", auth="user", type="json", methods=["GET"])
    def get_recepciones_batch(self):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Criterios de búsqueda para los lotes
            search_domain = [("state", "=", "in_progress"), ("picking_type_code", "=", "incoming"), ("user_id", "in", [user.id, False])]

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {"code": 200, "msg": "No tienes batches asignados", "result": []}

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos de línea de stock en vez de move.line.unified
                # Cambio 1: Usar stock.move.line en lugar de move.line.unified
                move_line_ids = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search(
                        [
                            ("picking_id.batch_id", "=", batch.id),  # Cambio 2: Referencia a batch a través de picking_id
                            # ("location_id", "in", user_location_ids),
                            ("is_done_item_pack", "=", False),  # Cambio 3: Usar el campo personalizado is_done_item_pack
                        ]
                    )
                )

                if not move_line_ids:
                    continue

                # Verificar si hay pickings y obtener orígenes
                origins_list = []
                if batch.picking_ids:
                    for picking in batch.picking_ids:
                        if picking.origin:
                            origins_list.append(
                                {
                                    "name": picking.origin,
                                    "id": picking.id,
                                    "id_batch": batch.id,
                                }
                            )

                # ✅ Leer detalles de los movimientos
                stock_moves = move_line_ids.read()

                # ✅ Crear la información básica del batch
                batch_info = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "fecha_creacion": batch.create_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.id if batch.picking_type_id else 0,
                    "picking_type": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                    "picking_type_code": "incoming",  # Similar al endpoint de recepciones
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "location_id": batch.location_id.id if batch.location_id else 0,
                    "location_name": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                    "location_barcode": batch.location_id.barcode or "",
                    "warehouse_id": batch.picking_type_id.warehouse_id.id if batch.picking_type_id and batch.picking_type_id.warehouse_id else 0,
                    "warehouse_name": batch.picking_type_id.warehouse_id.name if batch.picking_type_id and batch.picking_type_id.warehouse_id else "",
                    "numero_lineas": len(stock_moves),
                    "numero_items": sum(move["quantity"] for move in stock_moves),
                    "start_time_reception": batch.start_time_pick or "",
                    "end_time_reception": batch.end_time_pick or "",
                    "priority": batch.priority if hasattr(batch, "priority") else "",
                    "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "SIN-ZONA",
                    "origin": origins_list,
                    "responsable_id": batch.user_id.id or 0,
                    "responsable": batch.user_id.name or "",
                    "proveedor_id": batch.picking_ids[0].partner_id.id or 0,
                    "proveedor": batch.picking_ids[0].partner_id.name or "",
                    "location_dest_id": batch.picking_ids[0].location_dest_id.id or 0,
                    "location_dest_name": batch.picking_ids[0].location_dest_id.display_name or "",
                    "backorder_id": 0,
                    "backorder_name": "",
                    "purchase_order_id": batch.picking_ids[0].purchase_id.id or 0,
                    "purchase_order_name": batch.picking_ids[0].purchase_id.name or "",
                    "show_check_availability": batch.show_check_availability if hasattr(batch, "show_check_availability") else False,
                    "lineas_recepcion": [],  # Similar a lineas_recepcion
                    "lineas_recepcion_enviadas": [],  # Similar a lineas_recepcion_enviadas
                }

                # ✅ Precarga de productos y ubicaciones para optimizar
                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)}

                location_ids = {move["location_id"][0] for move in stock_moves}
                location_ids.update({move["location_dest_id"][0] for move in stock_moves})
                locations_dict = {loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)}

                # ✅ Procesar cada movimiento
                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_barcodes = []
                    if hasattr(product, "barcode_ids") and product.barcode_ids:
                        array_barcodes = [
                            {
                                "barcode": barcode.name,
                                "id_move": move["id"],
                                "id_product": product.id,
                                "batch_id": batch.id,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name
                        ]

                    # ✅ Obtener empaques del producto
                    array_packing = []
                    if hasattr(product, "packaging_ids") and product.packaging_ids:
                        array_packing = [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "id_move": move["id"],
                                "id_product": product.id,
                                "batch_id": batch.id,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]

                    # Cambio 5: En stock.move.line, el picking está directamente relacionado
                    picking = request.env["stock.picking"].sudo().browse(move["picking_id"][0]) if move.get("picking_id") else None
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener información de zona de entrega
                    delivery_zone_id = picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    delivery_zone_name = picking.delivery_zone_id.display_name if picking and picking.delivery_zone_id else "SIN-ZONA"

                    # ✅ Obtener información de lote y fecha de vencimiento
                    lot_id = move["lot_id"][0] if move["lot_id"] else 0
                    lot_name = move["lot_id"][1] if move["lot_id"] and len(move["lot_id"]) > 1 else ""
                    expiration_date = ""
                    if lot_id:
                        lot = request.env["stock.lot"].sudo().browse(lot_id)
                        if hasattr(lot, "expiration_date"):
                            expiration_date = lot.expiration_date

                    move_line_obj = request.env["stock.move.line"].sudo().browse(move["id"])

                    # Buscar líneas completadas relacionadas con el mismo move_id
                    if hasattr(move_line_obj, "move_id") and move_line_obj.move_id:
                        completed_lines = request.env["stock.move.line"].sudo().search([("move_id", "=", move_line_obj.move_id.id), ("is_done_item_pack", "=", True)])
                        # Sumar la cantidad de las líneas completadas
                        completed_quantity = sum(line.quantity for line in completed_lines)
                        cantidad_faltante = move.get("quantity_demanded", 0) - completed_quantity
                    else:
                        # Si no hay move_id, usar el campo quantity directamente
                        cantidad_faltante = move.get("quantity_demanded", 0) - move.get("quantity", 0)

                    # ✅ Crear la línea del batch (similar a linea_recepcion)
                    batch_info["lineas_recepcion"].append(
                        {
                            "id": move["id"],
                            "id_move": move["id"],
                            "id_batch": batch.id,
                            "id_recepcion": batch.id,
                            # Cambio 7: Usar el state del move o del picking relacionado
                            "state": move.get("state", "assigned"),
                            "product_id": product.id or 0,
                            "product_name": product.name or "",
                            "product_code": product.default_code if product else "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking if product else "",
                            "fecha_vencimiento": expiration_date,
                            "dias_vencimiento": product.expiration_time if hasattr(product, "expiration_time") else "",
                            "other_barcodes": array_barcodes,
                            "product_packing": array_packing,
                            # Cambio 8: Usando quantity en lugar de product_uom_qty
                            "quantity_ordered": move["quantity"],
                            "cantidad_faltante": cantidad_faltante,
                            "quantity_to_receive": move["quantity"],
                            "uom": product.uom_id.name if product and product.uom_id else "UND",
                            "location_dest_id": move["location_dest_id"][0],
                            "location_dest_name": location_dest.display_name or "",
                            "location_dest_barcode": location_dest.barcode or "",
                            "location_id": move["location_id"][0],
                            "location_name": location.display_name if location else "",
                            "location_barcode": location.barcode or "",
                            "weight": product.weight if product else 0,
                            "rimoval_priority": location.priority_picking_desplay if location else "",
                            "lot_id": lot_id,
                            "lot_name": lot_name,
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "picking_id": picking_id,
                            "picking_name": picking.display_name if picking else "",
                            "origin": picking.origin or "" if picking else "",
                        }
                    )

                # ✅ Buscar movimientos ya procesados (is_done_item_pack = True)
                # Cambio 9: Buscar stock.move.line completados usando campo personalizado
                done_move_line_ids = request.env["stock.move.line"].sudo().search([("picking_id.batch_id", "=", batch.id), ("is_done_item_pack", "=", True)])  # Usando el campo personalizado is_done_item_pack

                # ✅ Procesar movimientos ya completados
                if done_move_line_ids:
                    done_stock_moves = done_move_line_ids.read()

                    for done_move in done_stock_moves:
                        product = products.get(done_move["product_id"][0]) if done_move["product_id"] else None

                        product = obtener_info_producto(done_move["product_id"][0]) if done_move.get("product_id") else None
                        # location = locations_dict.get(done_move["location_id"][0]) if done_move["location_id"] else None
                        # location_dest = locations_dict.get(done_move["location_dest_id"][0]) if done_move["location_dest_id"] else None

                        # location_dest = request.env["stock.location"].sudo().browse(done_move["location_dest_id"][0]) if done_move.get("location_dest_id") else None

                        location_dest = obtener_info_ubicacion(done_move["location_dest_id"][0]) if done_move.get("location_dest_id") else None
                        location = obtener_info_ubicacion(done_move["location_id"][0]) if done_move.get("location_id") else None

                        # Información del lote
                        lot_id = done_move["lot_id"][0] if done_move.get("lot_id") else 0
                        lot_name = done_move["lot_id"][1] if done_move.get("lot_id") and len(done_move["lot_id"]) > 1 else ""
                        expiration_date = ""

                        if lot_id:
                            lot = request.env["stock.lot"].sudo().browse(lot_id)
                            if hasattr(lot, "expiration_date"):
                                expiration_date = lot.expiration_date

                        # Obtener el picking asociado
                        picking = request.env["stock.picking"].sudo().browse(done_move["picking_id"][0]) if done_move.get("picking_id") else None

                        # Cambio 10: Mapeo de campos de stock.move.line a la estructura esperada
                        batch_info["lineas_recepcion_enviadas"].append(
                            {
                                "id": done_move["id"],
                                "id_move_line": done_move["id"],
                                "id_move": done_move["id"],
                                "id_recepcion": batch.id,
                                "id_batch": batch.id,
                                "product_id": done_move["product_id"][0] if done_move["product_id"] else 0,
                                "product_name": product.name or "",
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "quantity_ordered": done_move["quantity"],
                                "quantity_done": done_move["quantity"],
                                "uom": product.uom_id.name if product and product.uom_id else "UND",
                                "location_dest_id": location_dest.id if location_dest else 0,
                                "location_dest_name": location_dest.display_name if location_dest else "",
                                "location_dest_barcode": location_dest.barcode or "",
                                "location_id": location.id if location else 0,
                                "location_name": location.display_name if location else "",
                                "location_barcode": location.barcode or "",
                                "is_done_item": done_move.get("is_done_item_pack", True),  # Usando el campo personalizado is_done_item_pack
                                "date_transaction": done_move.get("date_transaction_packing", ""),
                                "observation": done_move.get("new_observation_packing", ""),
                                "time": done_move.get("time_packing", ""),
                                "user_operator_id": done_move["user_operator_id"][0] or 0,
                                "lot_id": lot_id,
                                "lot_name": lot_name,
                                "fecha_vencimiento": expiration_date,
                            }
                        )

                # Solo añadir el batch si tiene líneas pendientes
                if batch_info["lineas_recepcion"]:
                    array_batch.append(batch_info)

            return {"code": 200, "result": array_batch}

        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Transaccion Recepcion por ID
    @http.route("/api/recepciones/<int:id>", auth="user", type="json", methods=["GET"])
    def get_recepcion_by_id(self, id):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # ✅ Validar ID
            if not id:
                return {"code": 400, "msg": "ID de recepción no válido"}

            # ✅ Buscar recepción por ID
            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id), ("picking_type_code", "=", "incoming")], limit=1)

            # ✅ Validar recepción
            if not recepcion:
                return {"code": 400, "msg": "Recepción no encontrada"}

            # ✅ Verificar si el usuario tiene acceso al almacén de la recepción
            if not user.has_group("stock.group_stock_manager") and user.allowed_warehouse_ids and recepcion.picking_type_id.warehouse_id not in user.allowed_warehouse_ids:
                return {"code": 403, "msg": "Acceso denegado"}

            # ✅ Verificar si la recepción tiene movimientos pendientes
            # Cambio para Odoo 17: move_lines -> move_ids
            movimientos_pendientes = recepcion.move_ids.filtered(lambda m: m.state not in ["done", "cancel"])
            if not movimientos_pendientes:
                return {"code": 400, "msg": "La recepción no tiene movimientos pendientes"}

            # ✅ Obtener la orden de compra relacionada (si existe)
            purchase_order = recepcion.purchase_id or (recepcion.origin and request.env["purchase.order"].sudo().search([("name", "=", recepcion.origin)], limit=1))

            # Calcular peso total
            # Cambio para Odoo 17: Verificación de product_id y weight
            peso_total = sum(move.product_id.weight * move.product_uom_qty for move in movimientos_pendientes if move.product_id and move.product_id.weight)

            # Calcular número de ítems (suma total de cantidades)
            # Cambio para Odoo 17: product_qty -> product_uom_qty
            numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

            # Generar información de la recepción
            recepcion_info = {
                "id": recepcion.id,
                "name": recepcion.name,  # Nombre de la recepción
                "fecha_creacion": recepcion.create_date,  # Fecha con hora
                "scheduled_date": recepcion.scheduled_date,  # Fecha programada
                "proveedor_id": recepcion.partner_id.id,
                "proveedor": recepcion.partner_id.name,  # Proveedor
                "location_dest_id": recepcion.location_dest_id.id,
                "location_dest_name": recepcion.location_dest_id.display_name,  # Ubicación destino
                "purchase_order_id": purchase_order.id if purchase_order else 0,
                "purchase_order_name": purchase_order.name if purchase_order else "",  # Orden de compra
                "numero_entrada": recepcion.name,  # Número de entrada
                "peso_total": peso_total,  # Peso total
                "numero_lineas": len(movimientos_pendientes),  # Número de líneas (productos)
                "numero_items": numero_items,  # Número de ítems (cantidades)
                "state": recepcion.state,
                "origin": recepcion.origin or "",
                "priority": recepcion.priority,
                "warehouse_id": recepcion.picking_type_id.warehouse_id.id,
                "warehouse_name": recepcion.picking_type_id.warehouse_id.name,
                "location_id": recepcion.location_id.id,
                "location_name": recepcion.location_id.display_name,
                "responsable_id": recepcion.user_id.id if recepcion.user_id else 0,
                "responsable": recepcion.user_id.name if recepcion.user_id else "",
                "picking_type": recepcion.picking_type_id.name,
                "lineas_recepcion": [],
            }

            # ✅ Procesar solo las líneas pendientes
            for move in movimientos_pendientes:
                product = move.product_id
                purchase_line = move.purchase_line_id

                # Obtener códigos de barras adicionales
                array_barcodes = []
                # Cambio para Odoo 17: Verificación de campos con hasattr
                if hasattr(product, "barcode_ids"):
                    array_barcodes = [
                        {
                            "barcode": barcode.name,
                            "id_move": move.id,
                            "id_product": product.id,
                            "batch_id": recepcion.id,
                        }
                        for barcode in product.barcode_ids
                        if barcode.name
                    ]

                # Obtener empaques del producto
                array_packing = []
                # Cambio para Odoo 17: Verificación de campos con hasattr y cambio de nombre
                if hasattr(product, "packaging_ids"):
                    array_packing = [
                        {
                            "barcode": pack.barcode,
                            "cantidad": pack.qty,
                            "id_move": move.id,
                            "id_product": product.id,
                            "batch_id": recepcion.id,
                        }
                        for pack in product.packaging_ids
                        if pack.barcode
                    ]

                # obtener la fecha de vencimiento del producto pero la que esta mas cerca a vencer
                fecha_vencimiento = ""
                if hasattr(product, "tracking") and product.tracking == "lot":
                    lot_domain = [("product_id", "=", product.id)]
                    # Cambio para Odoo 17: Verificación del campo expiration_date
                    if hasattr(request.env["stock.lot"], "expiration_date"):
                        lot = request.env["stock.lot"].search(lot_domain, order="expiration_date asc", limit=1)
                        if lot and hasattr(lot, "expiration_date"):
                            fecha_vencimiento = lot.expiration_date
                    # Alternativa para Odoo 17: use_expiration_date
                    elif hasattr(request.env["stock.lot"], "use_expiration_date"):
                        lot = request.env["stock.lot"].search(lot_domain, order="use_expiration_date asc", limit=1)
                        if lot and hasattr(lot, "use_expiration_date"):
                            fecha_vencimiento = lot.use_expiration_date

                # Generar información de la línea de recepción
                linea_info = {
                    "id": move.id,
                    "id_move": move.id,
                    "id_recepcion": recepcion.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_code": product.default_code or "",
                    "product_barcode": product.barcode or "",
                    "product_tracking": product.tracking if hasattr(product, "tracking") else "",
                    "fecha_vencimiento": fecha_vencimiento or "",
                    "dias_vencimiento": product.expiration_time if hasattr(product, "expiration_time") else "",
                    "other_barcodes": array_barcodes,
                    "product_packing": array_packing,
                    "quantity_ordered": purchase_line.product_qty if purchase_line else move.product_uom_qty,  # Cambio para Odoo 17
                    "quantity_to_receive": move.product_uom_qty,  # Cambio para Odoo 17: product_qty -> product_uom_qty
                    "quantity_done": move.quantity_done,
                    "uom": move.product_uom_id.name if move.product_uom_id else "UND",  # Cambio para Odoo 17: product_uom -> product_uom_id
                    "location_dest_id": move.location_dest_id.id or 0,
                    "location_dest_name": move.location_dest_id.display_name or "",
                    "location_dest_barcode": move.location_dest_id.barcode or "",
                    "location_id": move.location_id.id or 0,
                    "location_name": move.location_id.display_name or "",
                    "location_barcode": move.location_id.barcode or "",
                    "weight": product.weight or 0,
                    "detalle_lineas": [],
                }

                # Incluir detalles de las líneas (para trazabilidad)
                for move_line in move.move_line_ids:
                    lot = move_line.lot_id
                    location = move_line.location_id
                    location_dest = move_line.location_dest_id

                    expiration_date = ""
                    if lot:
                        if hasattr(lot, "expiration_date"):
                            expiration_date = lot.expiration_date
                        elif hasattr(lot, "use_expiration_date"):
                            expiration_date = lot.use_expiration_date

                    detalle_info = {
                        "id": move_line.id,
                        "qty_done": move_line.qty_done,
                        "qty_todo": move_line.product_uom_qty - move_line.qty_done,
                        "product_uom_qty": move_line.product_uom_qty,
                        "lot_id": lot.id if lot else 0,
                        "lot_name": lot and lot.name or "",
                        "expiration_date": expiration_date or "",
                        "location_id": location.id,
                        "location_name": location.name,
                        "location_barcode": location.barcode or "",
                        "location_dest_id": location_dest.id,
                        "location_dest_name": location_dest.name,
                        "location_dest_barcode": location_dest.barcode or "",
                        "package_id": move_line.package_id.id if move_line.package_id else 0,
                        "package_name": move_line.package_id.name if move_line.package_id else "",
                        "result_package_id": move_line.result_package_id.id if move_line.result_package_id else 0,
                        "result_package_name": move_line.result_package_id.name if move_line.result_package_id else "",
                    }

                    linea_info["detalle_lineas"].append(detalle_info)

                recepcion_info["lineas_recepcion"].append(linea_info)

            return {"code": 200, "result": recepcion_info}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Asignar responsable a Recepcion
    @http.route("/api/asignar_responsable", auth="user", type="json", methods=["POST"], csrf=False)
    def asignar_responsable(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            id_responsable = auth.get("id_responsable", 0)

            # ✅ Validar ID de recepción
            if not id_recepcion:
                return {"code": 400, "msg": "ID de recepción no válido"}

            # ✅ Validar ID de responsable
            if not id_responsable:
                return {"code": 400, "msg": "ID de responsable no válido"}

            # ✅ Buscar recepción por ID
            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id_recepcion), ("picking_type_code", "=", "incoming")], limit=1)

            # ✅ Validar recepción
            if not recepcion:
                return {"code": 400, "msg": "Recepción no encontrada"}

            # Validar si la recepcion ya tiene un responsable asignado
            if recepcion.responsable_id:
                return {"code": 400, "msg": "La recepción ya tiene un responsable asignado"}

            try:
                # ✅ Asignar responsable a la recepción
                # El código es igual en Odoo 17, pero agregamos manejo de errores adicional
                responsable_user = request.env["res.users"].sudo().browse(id_responsable)
                if not responsable_user.exists():
                    return {"code": 400, "msg": "El usuario responsable no existe"}

                data = recepcion.write({"responsable_id": id_responsable})

                if data:
                    return {"code": 200, "result": "Responsable asignado correctamente"}
                else:
                    return {"code": 400, "msg": "No se pudo asignar el responsable a la recepción"}

            except Exception as e:
                return {"code": 400, "msg": f"Error al asignar responsable a la recepción: {str(e)}"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/asignar_responsable/batch", auth="user", type="json", methods=["POST"], csrf=False)
    def asignar_responsable_batch(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_batch = auth.get("id_batch", 0)
            id_responsable = auth.get("id_responsable", 0)

            # ✅ Validar ID de recepción
            if not id_batch:
                return {"code": 400, "msg": "ID del batch no válido"}

            # ✅ Validar ID de responsable
            if not id_responsable:
                return {"code": 400, "msg": "ID de responsable no válido"}

            # ✅ Buscar recepción por ID
            batch = request.env["stock.picking.batch"].sudo().search([("id", "=", id_batch)], limit=1)

            # ✅ Validar recepción
            if not batch:
                return {"code": 400, "msg": "Batch no encontrado"}

            # Validar si la batch ya tiene un responsable asignado
            if batch.user_id:
                return {"code": 400, "msg": "El batch ya tiene un responsable asignado"}

            try:
                # ✅ Asignar responsable a la recepción
                # El código es igual en Odoo 17, pero agregamos manejo de errores adicional
                responsable_user = request.env["res.users"].sudo().browse(id_responsable)
                if not responsable_user.exists():
                    return {"code": 400, "msg": "El usuario responsable no existe"}

                data = batch.write({"user_id": id_responsable})

                if data:
                    return {"code": 200, "result": "Responsable asignado correctamente"}
                else:
                    return {"code": 400, "msg": "No se pudo asignar el responsable al batch"}

            except Exception as e:
                return {"code": 400, "msg": f"Error al asignar responsable al batch: {str(e)}"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/lotes/<int:id_producto>", auth="user", type="json", methods=["GET"])
    def get_lotes(self, id_producto):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            if not id_producto:
                return {"code": 400, "msg": "ID de producto no válido"}

            product = request.env["product.product"].sudo().search([("id", "=", id_producto)], limit=1)

            if not product:
                return {"code": 400, "msg": "Producto no encontrado"}

            if product.tracking != "lot":
                return {"code": 400, "msg": "El producto no tiene seguimiento por lotes"}

            # 🟡 Filtrar solo lotes que NO estén vencidos
            today = date.today()
            lotes = request.env["stock.lot"].sudo().search([("product_id", "=", id_producto), "|", ("expiration_date", "=", False), ("expiration_date", ">", today)])  # No tiene fecha de caducidad  # Fecha de caducidad futura

            array_lotes = []

            for lote in lotes:
                array_lotes.append(
                    {
                        "id": lote.id,
                        "name": lote.name or "",
                        "quantity": lote.product_qty or 0,
                        "expiration_date": lote.expiration_date or "",
                        "removal_date": lote.removal_date or "",
                        "use_date": lote.use_date or "",
                        "product_id": lote.product_id.id or 0,
                        "product_name": lote.product_id.name or "",
                    }
                )

            return {"code": 200, "result": array_lotes}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/send_recepcion", auth="user", type="json", methods=["POST"], csrf=False)
    def send_recepcion(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            list_items = auth.get("list_items", [])

            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id_recepcion), ("picking_type_code", "=", "incoming"), ("state", "!=", "done")], limit=1)

            if not recepcion:
                return {"code": 400, "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}"}

            array_result = []

            # 🧠 Control para eliminar líneas automáticas solo una vez
            lineas_automaticas_borradas = False

            for item in list_items:
                move_id = item.get("id_move")
                product_id = item.get("id_producto")
                lote_id = item.get("lote_producto")
                ubicacion_destino = item.get("ubicacion_destino")
                cantidad = item.get("cantidad_separada")
                fecha_transaccion = item.get("fecha_transaccion")
                observacion = item.get("observacion")
                id_operario = item.get("id_operario")
                time_line = item.get("time_line")

                if not product_id or not cantidad:
                    continue

                product = request.env["product.product"].sudo().browse(product_id)
                if not product.exists():
                    continue

                move = request.env["stock.move"].sudo().browse(move_id) if move_id else recepcion.move_ids.filtered(lambda m: m.product_id.id == product_id)
                if not move:
                    return {"code": 400, "msg": f"El producto {product.name} no está en la recepción"}

                stock_move = move.sudo()

                lot = None
                if product.tracking == "lot":
                    if not lote_id:
                        return {"code": 400, "msg": f"El producto {product.name} requiere un lote"}
                    lot = request.env["stock.lot"].sudo().browse(lote_id)
                    if not lot.exists():
                        return {"code": 400, "msg": f"Lote no encontrado para el producto {product.name}"}

                # ✅ Eliminar líneas automáticas SOLO en la primera iteración
                if not lineas_automaticas_borradas:
                    lineas_auto = recepcion.move_line_ids.filtered(lambda l: not l.user_operator_id and not l.is_done_item)
                    lineas_auto.unlink()
                    lineas_automaticas_borradas = True  # ¡Ya está hecho!

                # ➕ Siempre crear una nueva línea con los datos del operario
                move_line_vals = {
                    "picking_id": recepcion.id,
                    "move_id": move.id,
                    "product_id": product.id,
                    "quantity": cantidad,
                    "location_id": move.location_id.id,
                    "location_dest_id": ubicacion_destino or move.location_dest_id.id,
                    "product_uom_id": move.product_uom.id,
                    "lot_id": lote_id if lote_id else False,
                    "date_transaction": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                    "new_observation": observacion,
                    "time": time_line,
                    "user_operator_id": id_operario,
                    "is_done_item": True,
                }

                move_line = request.env["stock.move.line"].sudo().create(move_line_vals)

                array_result.append(
                    {
                        "producto": product.name,
                        "cantidad": cantidad,
                        "lote": lot.name if lot else "",
                        "ubicacion_destino": ubicacion_destino,
                        "fecha_transaccion": fecha_transaccion,
                        "date_transaction": move_line.date_transaction,
                        "new_observation": move_line.new_observation,
                        "time": move_line.time,
                        "user_operator_id": move_line.user_operator_id.id,
                        "is_done_item": move_line.is_done_item,
                    }
                )

            stock_move.sudo().write({"picked": True})

            return {"code": 200, "result": array_result}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/send_temperatura", auth="user", type="json", methods=["POST"], csrf=False)
    def send_temperatura(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id = auth.get("id", 0)
            temperatura = auth.get("temperatura", 0)

            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id)], limit=1)

            if not recepcion:
                return {"code": 400, "msg": f"No se encontró registro con ID {id}"}

            recepcion.sudo().write({"temperature": temperatura})

            return {"code": 200, "result": "Temperatura registrada correctamente"}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    # @http.route("/api/send_recepcion/batch", auth="user", type="json", methods=["POST"], csrf=False)
    # def send_recepcion_batch(self, **auth):
    #     try:
    #         user = request.env.user
    #         if not user:
    #             return {"code": 400, "msg": "Usuario no encontrado"}

    #         id_batch = auth.get("id_batch", 0)
    #         list_items = auth.get("list_items", [])

    #         array_result = []

    #         # ✅ Validar si el id_batch existe
    #         batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
    #         if not batch.exists():
    #             return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

    #         for item in list_items:
    #             move_id = item.get("id_move")
    #             product_id = item.get("id_producto")
    #             lote_id = item.get("lote_producto")
    #             ubicacion_destino = item.get("ubicacion_destino")
    #             cantidad = item.get("cantidad_separada")
    #             fecha_transaccion = item.get("fecha_transaccion")
    #             observacion = item.get("observacion")
    #             id_operario = item.get("id_operario")
    #             time_line = item.get("time_line")

    #             if not product_id or not cantidad:
    #                 continue

    #             product = request.env["product.product"].sudo().browse(product_id)
    #             if not product.exists():
    #                 continue

    #             move_line = request.env["stock.move.line"].sudo().browse(move_id)

    #             # return {"code": 400, "msg": f"El producto {product.name} no está en la recepción cantidad {cantidad} de la linea {move_line.quantity}"}

    #             if move_line.exists():
    #                 if move_line.quantity >= cantidad:
    #                     if observacion.lower() != "sin novedad":
    #                         array_result.append({"code": 400, "msg": f"Numero 1"})
    #                         move_line.write(
    #                             {
    #                                 "quantity": cantidad,
    #                                 "location_dest_id": ubicacion_destino,
    #                                 "lot_name": lote_id,
    #                                 "new_observation_packing": observacion,
    #                                 "user_operator_id": id_operario,
    #                                 "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
    #                                 "time_packing": time_line,
    #                                 "is_done_item_pack": True,
    #                             }
    #                         )

    #                     if cantidad < move_line.quantity:
    #                         array_result.append({"code": 400, "msg": f"Numero 2"})
    #                         cantidad_original = move_line.quantity

    #                         # ✅ 1. Restar a la original
    #                         move_line.write({"quantity": cantidad_original - cantidad})

    #                         # ✅ 2. Copiar la línea original
    #                         new_line_vals = move_line.copy_data()[0]

    #                         new_line_vals.update(
    #                             {
    #                                 "quantity": cantidad,
    #                                 "location_dest_id": ubicacion_destino,
    #                                 "lot_name": lote_id,
    #                                 "new_observation_packing": observacion,
    #                                 "user_operator_id": id_operario,
    #                                 "time_packing": time_line,
    #                                 "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
    #                                 "is_done_item_pack": True,
    #                             }
    #                         )

    #                         # ✅ 4. Crear la línea nueva con los valores actualizados
    #                         new_line = request.env["stock.move.line"].sudo().create(new_line_vals)

    #                         new_line.write({"is_done_item_pack": True})

    #                     else:
    #                         # ✅ 3. Actualizar la línea original con los nuevos valores
    #                         array_result.append({"code": 400, "msg": f"Numero 3"})

    #                         move_line.write(
    #                             {
    #                                 "location_dest_id": ubicacion_destino,
    #                                 "lot_name": lote_id,
    #                                 "new_observation_packing": observacion,
    #                                 "user_operator_id": id_operario,
    #                                 "time_packing": time_line,
    #                                 "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
    #                                 "is_done_item_pack": True,
    #                             }
    #                         )

    #                 elif cantidad == move_line.quantity:
    #                     array_result.append({"code": 400, "msg": f"Numero 4"})
    #                     move_line.write(
    #                         {
    #                             "location_dest_id": ubicacion_destino,
    #                             "lot_name": lote_id,
    #                             "new_observation_packing": observacion,
    #                             "user_operator_id": id_operario,
    #                             "time_packing": time_line,
    #                             "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
    #                             "is_done_item_pack": True,
    #                         }
    #                     )

    #                 else:
    #                     return {"code": 400, "msg": f"La cantidad {cantidad} no puede ser mayor a la cantidad {move_line.quantity}"}

    #             else:
    #                 return {"code": 400, "msg": f"La línea de movimiento {move_id} no existe"}

    #             array_result.append(
    #                 {
    #                     "producto": product.name,
    #                     "cantidad": cantidad,
    #                     "lote": lote_id,
    #                     "ubicacion_destino": ubicacion_destino,
    #                     "fecha_transaccion": fecha_transaccion,
    #                     "date_transaction": move_line.date_transaction_packing,
    #                     "new_observation_packing": move_line.new_observation_packing,
    #                     "time": move_line.time_packing,
    #                     "user_operator_id": move_line.user_operator_id.id,
    #                     "is_done_item_pack": move_line.is_done_item_pack,
    #                 }
    #             )

    #         return {"code": 200, "result": array_result}

    #     except Exception as e:
    #         return {"code": 500, "msg": f"Error interno: {str(e)}"}

    @http.route("/api/send_recepcion/batch", auth="user", type="json", methods=["POST"], csrf=False)
    def send_recepcion_batch(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_batch = auth.get("id_batch", 0)
            list_items = auth.get("list_items", [])

            if not list_items:
                return {"code": 400, "msg": "No se recibieron líneas para procesar"}

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            array_result = []

            for item in list_items:
                # Extraer datos del ítem
                move_id = item.get("id_move")
                product_id = item.get("id_producto")
                lote_id = item.get("lote_producto")
                ubicacion_destino = item.get("ubicacion_destino")
                cantidad = item.get("cantidad_separada")
                fecha_transaccion = item.get("fecha_transaccion")
                observacion = item.get("observacion", "")
                id_operario = item.get("id_operario")
                time_line = item.get("time_line")
                # Nuevo campo para controlar si se divide la línea
                # dividir = item.get("dividir", True)  # Por defecto True para mantener el comportamiento anterior

                dividir = item.get("dividir", False)  # Cambiado a False por defecto
                if observacion.lower() == "cantidad dividida":
                    dividir = True  # Si la observación es "producto en perfecto estado", no dividir

                # Validaciones básicas
                if not product_id:
                    array_result.append({"code": 400, "msg": f"Producto no especificado para algún ítem"})
                    continue

                product = request.env["product.product"].sudo().browse(product_id)
                if not product.exists():
                    array_result.append({"code": 400, "msg": f"El producto con ID {product_id} no existe"})
                    continue

                move_line = request.env["stock.move.line"].sudo().browse(move_id)
                if not move_line.exists():
                    array_result.append({"code": 400, "msg": f"La línea de movimiento con ID {move_id} no existe"})
                    continue

                # si viene el lote_id buscarlo y poner el nombre

                # Validar cantidad
                # if cantidad > move_line.quantity:
                #     array_result.append({"code": 400, "msg": f"La cantidad {cantidad} no puede ser mayor a la cantidad disponible {move_line.quantity} para el producto {product.name}"})
                #     continue

                # Preparar los datos comunes para actualización
                fecha_procesada = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)
                common_vals = {"location_dest_id": ubicacion_destino, "lot_name": lote_id, "new_observation_packing": observacion, "user_operator_id": id_operario, "time_packing": time_line, "date_transaction_packing": fecha_procesada, "is_done_item_pack": True}

                if lote_id:
                    lot = request.env["stock.lot"].sudo().browse(lote_id)
                    if not lot.exists():
                        array_result.append({"code": 400, "msg": f"El lote con ID {lote_id} no existe"})
                        continue
                    lote_id = lot.name
                else:
                    # eliminarlo de common_vals["lot_name"]
                    common_vals.pop("lot_name", None)

                if cantidad == 0:

                    # actualizar cantidad = 0  # No se actualiza la cantidad si es cero
                    common_vals["quantity"] = cantidad

                    # Actualizar la línea existente como procesada
                    move_line.write(common_vals)

                    # Referencia para el resultado
                    processed_line = move_line

                # CASO 2: Si la cantidad a procesar es menor que la cantidad en la línea Y el parámetro dividir es True,
                # dividimos la línea en dos: una procesada y una pendiente
                elif cantidad < move_line.quantity and dividir:
                    # Guardar la cantidad original antes de modificarla
                    cantidad_original = move_line.quantity

                    # 1. Actualizar línea original con la cantidad restante (no procesada)
                    move_line.write({"quantity": cantidad_original - cantidad})

                    # 2. Crear una nueva línea con la cantidad procesada
                    new_line_vals = move_line.copy_data()[0]
                    new_line_vals.update({"quantity": cantidad, **common_vals})

                    # 3. Crear la nueva línea procesada
                    new_line = request.env["stock.move.line"].sudo().create(new_line_vals)

                    # Referencia para el resultado
                    processed_line = new_line

                    # array_result.append({
                    #     "code": 200,
                    #     "msg": f"Línea dividida y procesada: {cantidad} de {cantidad_original}"
                    # })

                # CASO 3: Si la cantidad a procesar es exactamente igual a la cantidad de la línea O el parámetro dividir es False,
                # simplemente actualizamos la línea existente
                else:  # cantidad == move_line.quantity o dividir = False
                    # Si dividir es False y la cantidad es menor, simplemente actualizamos la cantidad
                    if not dividir and cantidad < move_line.quantity:
                        common_vals["quantity"] = cantidad

                    # Actualizar la línea existente como procesada
                    move_line.write(common_vals)

                    # Referencia para el resultado
                    processed_line = move_line

                    msg = f"Línea completa procesada: {cantidad}"
                    if not dividir and cantidad < move_line.quantity:
                        msg = f"Línea actualizada sin dividir: {cantidad} (dividir=False)"

                    # array_result.append({
                    #     "code": 200,
                    #     "msg": msg
                    # })

                # Agregar información detallada del procesamiento
                array_result.append(
                    {
                        "producto": product.name,
                        "cantidad": cantidad,
                        "lote": lote_id,
                        "ubicacion_destino": ubicacion_destino,
                        "fecha_transaccion": fecha_transaccion,
                        "date_transaction": processed_line.date_transaction_packing,
                        "new_observation_packing": processed_line.new_observation_packing,
                        "time": processed_line.time_packing,
                        "user_operator_id": processed_line.user_operator_id.id if processed_line.user_operator_id else None,
                        "is_done_item_pack": processed_line.is_done_item_pack,
                        "dividir": dividir,
                    }
                )

            return {"code": 200, "result": array_result}

        except Exception as e:
            import traceback

            return {"code": 500, "msg": f"Error interno: {str(e)}", "traceback": traceback.format_exc()}

    ## GET Obtener todas las ubicaciones
    @http.route("/api/ubicaciones", auth="user", type="json", methods=["GET"])
    def get_ubicaciones(self):
        try:
            user = request.env.user
            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            array_ubicaciones = []

            for warehouse in allowed_warehouses:
                # ✅ Obtener todas las ubicaciones
                ubicaciones = request.env["stock.location"].sudo().search([("usage", "=", "internal"), ("active", "=", True), ("warehouse_id", "=", warehouse.id)])

                for ubicacion in ubicaciones:
                    array_ubicaciones.append(
                        {
                            "id": ubicacion.id,
                            "name": ubicacion.display_name,
                            "barcode": ubicacion.barcode or "",
                            "location_id": ubicacion.location_id.id if ubicacion.location_id else 0,
                            "location_name": ubicacion.location_id.display_name if ubicacion.location_id else "",
                            "id_warehouse": warehouse.id,
                            "warehouse_name": warehouse.name,
                        }
                    )

            return {"code": 200, "result": array_ubicaciones}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Completar Recepcion
    @http.route("/api/complete_recepcion", auth="user", type="json", methods=["POST"], csrf=False)
    def complete_recepcion(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            crear_backorder = auth.get("crear_backorder", True)  # Parámetro para controlar la creación de backorder

            # ✅ Buscar recepción por ID
            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id_recepcion), ("picking_type_code", "=", "incoming"), ("state", "!=", "done")], limit=1)

            if not recepcion:
                return {"code": 400, "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}"}

            # Intentar validar la recepción
            result = recepcion.sudo().button_validate()

            # Si el resultado es un diccionario, significa que se requiere acción adicional (un wizard)
            if isinstance(result, dict) and result.get("res_model"):
                wizard_model = result.get("res_model")

                # Para asistente de backorder
                if wizard_model == "stock.backorder.confirmation":
                    # Crear el wizard con los valores del contexto
                    wizard_context = result.get("context", {})

                    # Crear el asistente con los valores correctos según tu JSON
                    # En Odoo 17, la forma de enlazar registros sigue siendo la misma
                    wizard_vals = {"pick_ids": [(4, id_recepcion)], "show_transfers": wizard_context.get("default_show_transfers", False)}

                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)

                    # Procesar según la opción de crear_backorder
                    if crear_backorder:
                        # En Odoo 17, el método process sigue existiendo
                        wizard.sudo().process()
                        return {"code": 200, "msg": f"Recepción parcial completada y backorder creado - ID {wizard.id or 0}"}
                    else:
                        # En Odoo 17, el método process_cancel_backorder sigue existiendo
                        wizard.sudo().process_cancel_backorder()
                        return {"code": 200, "msg": "Recepción parcial completada sin crear backorder"}

                # Para asistente de transferencia inmediata
                elif wizard_model == "stock.immediate.transfer":
                    wizard_context = result.get("context", {})
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({"pick_ids": [(4, id_recepcion)]})

                    wizard.sudo().process()
                    return {"code": 200, "msg": "Recepción procesada con transferencia inmediata"}

                else:
                    return {"code": 400, "msg": f"Se requiere un asistente no soportado: {wizard_model}"}

            # Si llegamos aquí, button_validate completó la validación sin necesidad de asistentes
            return {"code": 200, "msg": "Recepción completada correctamente"}

        except Exception as e:
            # Registrar el error completo para depuración
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Crear Lote
    @http.route("/api/create_lote", auth="user", type="json", methods=["POST"], csrf=False)
    def create_lote(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_producto = auth.get("id_producto", 0)
            nombre_lote = auth.get("nombre_lote", "")
            fecha_vencimiento = auth.get("fecha_vencimiento", "")

            # ✅ Validar ID de producto
            if not id_producto:
                return {"code": 400, "msg": "ID de producto no válido"}

            # ✅ Validar nombre de lote
            if not nombre_lote:
                return {"code": 400, "msg": "Nombre de lote no válido"}

            # ✅ Buscar producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", id_producto)], limit=1)

            # ✅ Validar producto
            if not product:
                return {"code": 400, "msg": "Producto no encontrado"}

            # validar que el lote con ese nombre para ese producto no exista
            lot = request.env["stock.lot"].sudo().search([("name", "=", nombre_lote), ("product_id", "=", id_producto)], limit=1)
            if lot:
                return {"code": 400, "msg": "El lote ya existe para este producto"}

            # ✅ Crear lote
            # En Odoo 17, stock.production.lot cambió a stock.lot
            lot = (
                request.env["stock.lot"]
                .sudo()
                .create(
                    {
                        "name": nombre_lote,
                        "product_id": product.id,
                        "company_id": product.company_id.id or user.company_id.id,  # Añadir company_id
                        "expiration_date": fecha_vencimiento,
                        # El campo alert_date ya no existe en Odoo 17, se reemplazó por removal_date
                        "alert_date": fecha_vencimiento,
                        "removal_date": fecha_vencimiento,
                        "use_date": fecha_vencimiento,
                    }
                )
            )

            response = {
                "id": lot.id,
                "name": lot.name,
                "quantity": lot.product_qty,
                "expiration_date": lot.expiration_date,
                # Cambio de alert_date a removal_date en la respuesta
                "alert_date": lot.removal_date,
                "use_date": lot.use_date,
                "product_id": lot.product_id.id,
                "product_name": lot.product_id.name,
            }

            return {"code": 200, "result": response}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Actualizar Lote
    @http.route("/api/update_lote", auth="user", type="json", methods=["POST"], csrf=False)
    def update_lote(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_lote = auth.get("id_lote", 0)
            nombre_lote = auth.get("nombre_lote", "")
            fecha_vencimiento = auth.get("fecha_vencimiento", "")

            # ✅ Validar ID de lote
            if not id_lote:
                return {"code": 400, "msg": "ID de lote no válido"}

            # ✅ Validar nombre de lote
            if not nombre_lote:
                return {"code": 400, "msg": "Nombre de lote no válido"}

            # ✅ Buscar lote por ID
            # En Odoo 17, stock.production.lot cambió a stock.lot
            lot = request.env["stock.lot"].sudo().search([("id", "=", id_lote)], limit=1)

            # ✅ Validar lote
            if not lot:
                return {"code": 400, "msg": "Lote no encontrado"}

            # ✅ Actualizar lote
            lot.sudo().write(
                {
                    "name": nombre_lote,
                    "expiration_date": fecha_vencimiento,
                    # El campo alert_date ya no existe en Odoo 17
                    "use_date": fecha_vencimiento,
                    "removal_date": fecha_vencimiento,
                    "alert_date": fecha_vencimiento,
                }
            )

            response = {
                "id": lot.id,
                "name": lot.name,
                "quantity": lot.product_qty,
                "expiration_date": lot.expiration_date,
                # Cambio de alert_date a removal_date en la respuesta
                "use_date": lot.use_date,
                "removal_date": lot.removal_date,
                "alert_date": lot.removal_date,
                "product_id": lot.product_id.id,
                "product_name": lot.product_id.name,
            }

            return {"code": 200, "result": response}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## comprobar disponibilidad de la recepcion
    @http.route("/api/check_availability", auth="user", type="json", methods=["POST"], csrf=False)
    def check_availability(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id_recepcion), ("picking_type_code", "=", "incoming"), ("state", "!=", "done")], limit=1)

            if not recepcion:
                return {"code": 400, "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}"}

            # ✅ Ejecutar comprobación de disponibilidad
            recepcion.action_assign()

            movimientos_pendientes = recepcion.move_ids.filtered(lambda m: m.state in ["confirmed", "assigned"])
            if not movimientos_pendientes:
                return {"code": 200, "msg": "No hay líneas pendientes", "result": {}}

            purchase_order = recepcion.purchase_id or (recepcion.origin and request.env["purchase.order"].sudo().search([("name", "=", recepcion.origin)], limit=1))
            peso_total = sum(move.product_id.weight * move.product_uom_qty for move in movimientos_pendientes if move.product_id.weight)
            numero_items = sum(move.product_uom_qty for move in movimientos_pendientes)

            recepcion_info = {
                "id": recepcion.id,
                "name": recepcion.name,
                "fecha_creacion": recepcion.create_date,
                "proveedor_id": recepcion.partner_id.id,
                "proveedor": recepcion.partner_id.name,
                "location_dest_id": recepcion.location_dest_id.id,
                "location_dest_name": recepcion.location_dest_id.display_name,
                "purchase_order_id": purchase_order.id if purchase_order else 0,
                "purchase_order_name": purchase_order.name if purchase_order else "",
                "numero_entrada": recepcion.name,
                "peso_total": peso_total,
                "numero_lineas": 0,
                "numero_items": 0,
                "state": recepcion.state,
                "origin": recepcion.origin or "",
                "priority": recepcion.priority,
                "warehouse_id": recepcion.picking_type_id.warehouse_id.id,
                "warehouse_name": recepcion.picking_type_id.warehouse_id.name,
                "location_id": recepcion.location_id.id,
                "location_name": recepcion.location_id.display_name,
                # "responsable_id": recepcion.user_id.id if recepcion.user_id else 0,
                # "responsable": recepcion.user_id.name if recepcion.user_id else "",
                "responsable_id": recepcion.responsable_id.id if recepcion.responsable_id else 0,
                "responsable": recepcion.responsable_id.name if recepcion.responsable_id else "",
                "picking_type": recepcion.picking_type_id.name,
                "backorder_id": recepcion.backorder_id.id if recepcion.backorder_id else 0,
                "backorder_name": recepcion.backorder_id.name if recepcion.backorder_id else "",
                "start_time_reception": recepcion.start_time_reception or "",
                "end_time_reception": recepcion.end_time_reception or "",
                "picking_type_code": recepcion.picking_type_code,
                "show_check_availability": getattr(recepcion, "show_check_availability", False),
                "lineas_recepcion": [],
                "lineas_recepcion_enviadas": [],
            }

            for move in movimientos_pendientes:
                product = move.product_id
                purchase_line = move.purchase_line_id
                cantidad_faltante = move.product_uom_qty - sum(l.quantity for l in move.move_line_ids if l.is_done_item)

                if cantidad_faltante <= 0:
                    continue

                array_barcodes = [{"barcode": b.name} for b in product.barcode_ids] if hasattr(product, "barcode_ids") else []
                array_packing = [{"barcode": p.barcode, "cantidad": p.qty, "id_move": p.id, "id_product": p.product_id.id, "batch_id": recepcion.id} for p in product.packaging_ids] if hasattr(product, "packaging_ids") else []

                fecha_vencimiento = ""
                if product.tracking == "lot":
                    lot = request.env["stock.lot"].search([("product_id", "=", product.id)], order="expiration_date asc", limit=1)
                    fecha_vencimiento = lot.expiration_date if lot and hasattr(lot, "expiration_date") else ""

                linea_info = {
                    "id": move.id,
                    "id_move": move.id,
                    "id_recepcion": recepcion.id,
                    "state": move.state,
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_code": product.default_code or "",
                    "product_barcode": product.barcode or "",
                    "product_tracking": product.tracking or "",
                    "fecha_vencimiento": fecha_vencimiento or "",
                    "dias_vencimiento": product.expiration_time if hasattr(product, "expiration_time") else "",
                    "other_barcodes": array_barcodes,
                    "product_packing": array_packing,
                    "quantity_ordered": purchase_line.product_uom_qty if purchase_line else move.product_uom_qty,
                    "quantity_to_receive": move.product_uom_qty,
                    "quantity_done": move.quantity,
                    "uom": move.product_uom.name if move.product_uom else "UND",
                    "location_dest_id": move.location_dest_id.id or 0,
                    "location_dest_name": move.location_dest_id.display_name or "",
                    "location_dest_barcode": move.location_dest_id.barcode or "",
                    "location_id": move.location_id.id or 0,
                    "location_name": move.location_id.display_name or "",
                    "location_barcode": move.location_id.barcode or "",
                    "weight": product.weight or 0,
                    "cantidad_faltante": cantidad_faltante,
                }

                recepcion_info["lineas_recepcion"].append(linea_info)

                for move_line in move.move_line_ids.filtered(lambda ml: ml.is_done_item):
                    cantidad_faltante = move_line.quantity - sum(l.quantity for l in move_line.move_line_ids if l.is_done_item)

                    linea_enviada_info = {
                        "id": move_line.id,
                        "id_move_line": move_line.id,
                        "id_move": move.id,
                        "id_recepcion": recepcion.id,
                        "product_id": product.id,
                        "product_name": product.name,
                        "product_code": product.default_code or "",
                        "product_barcode": product.barcode or "",
                        "product_tracking": product.tracking or "",
                        "quantity_ordered": purchase_line.product_uom_qty if purchase_line else move.product_uom_qty,
                        "quantity_to_receive": move.product_uom_qty,
                        "quantity_done": move_line.quantity,
                        "cantidad_faltante": cantidad_faltante,
                        "uom": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                        "location_dest_id": move_line.location_dest_id.id or 0,
                        "location_dest_name": move_line.location_dest_id.display_name or "",
                        "location_dest_barcode": move_line.location_dest_id.barcode or "",
                        "location_id": move_line.location_id.id or 0,
                        "location_name": move_line.location_id.display_name or "",
                        "location_barcode": move_line.location_id.barcode or "",
                        "is_done_item": move_line.is_done_item,
                        "date_transaction": getattr(move_line, "date_transaction", ""),
                        "observation": getattr(move_line, "new_observation", ""),
                        "time": getattr(move_line, "time", 0),
                        "user_operator_id": move_line.user_operator_id.id if move_line.user_operator_id else 0,
                    }

                    if move_line.lot_id:
                        linea_enviada_info.update(
                            {
                                "lot_id": move_line.lot_id.id,
                                "lot_name": move_line.lot_id.name,
                                "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                            }
                        )
                    elif move_line.lot_name:
                        linea_enviada_info.update(
                            {
                                "lot_id": 0,
                                "lot_name": move_line.lot_name,
                                "fecha_vencimiento": "",
                            }
                        )

                    recepcion_info["lineas_recepcion_enviadas"].append(linea_enviada_info)

            recepcion_info["numero_lineas"] = len(recepcion_info["lineas_recepcion"])
            recepcion_info["numero_items"] = sum(linea["quantity_to_receive"] for linea in recepcion_info["lineas_recepcion"])

            return {"code": 200, "msg": "Disponibilidad comprobada correctamente", "result": recepcion_info}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}


def procesar_fecha_naive(fecha_transaccion, zona_horaria_cliente):
    if fecha_transaccion:
        # Convertir la fecha enviada a datetime y agregar la zona horaria del cliente
        tz_cliente = pytz.timezone(zona_horaria_cliente)
        fecha_local = tz_cliente.localize(datetime.strptime(fecha_transaccion, "%Y-%m-%d %H:%M:%S"))

        # Convertir la fecha a UTC
        fecha_utc = fecha_local.astimezone(pytz.utc)

        # Eliminar la información de la zona horaria (hacerla naive)
        fecha_naive = fecha_utc.replace(tzinfo=None)
        return fecha_naive
    else:
        # Usar la fecha actual del servidor como naive datetime
        return datetime.now().replace(tzinfo=None)


def obtener_almacenes_usuario(user):

    user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

    if not user_wms:
        return {
            "code": 401,
            "msg": "El usuario no tiene permisos o no esta registrado en el módulo de configuraciones en el WMS",
        }

    allowed_warehouses = user_wms.allowed_warehouse_ids

    if not allowed_warehouses:
        return {"code": 400, "msg": "El usuario no tiene acceso a ningún almacén"}

    return allowed_warehouses


def obtener_info_ubicacion(ubicacion_id):
    ubicacion = request.env["stock.location"].sudo().browse(ubicacion_id)

    if not ubicacion.exists():
        return {"code": 400, "msg": f"La ubicación con ID {ubicacion_id} no existe"}

    return ubicacion


def obtener_info_producto(product_id):
    producto = request.env["product.product"].sudo().browse(product_id)

    if not producto.exists():
        return {"code": 400, "msg": f"El producto con ID {product_id} no existe"}

    return producto
