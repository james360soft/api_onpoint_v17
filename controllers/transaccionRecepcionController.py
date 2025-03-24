from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz


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
                            ("state", "=", "assigned"),
                            ("picking_type_code", "=", "incoming"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("is_return_picking", "=", False),
                            ("user_id", "in", [user.id, False]),  # Asignadas al usuario o sin asignar
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

                    recepcion_info = {
                        "id": picking.id,
                        "name": picking.name,  # Nombre de la recepción
                        "fecha_creacion": picking.create_date,  # Fecha con hora
                        "proveedor_id": picking.partner_id.id,
                        "proveedor": picking.partner_id.name,  # Proveedor
                        "location_dest_id": picking.location_dest_id.id,
                        "location_dest_name": picking.location_dest_id.display_name,  # Ubicación destino
                        "purchase_order_id": purchase_order.id if purchase_order else 0,
                        "purchase_order_name": purchase_order.name if purchase_order else "",  # Orden de compra
                        "numero_entrada": picking.name,  # Número de entrada
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
                        "responsable_id": picking.user_id.id if picking.user_id else 0,
                        "responsable": picking.user_id.name if picking.user_id else "",
                        "picking_type": picking.picking_type_id.name,
                        # Verificar si los campos personalizados existen
                        "start_time_reception": picking.start_time_reception if hasattr(picking, "start_time_reception") else "",
                        "end_time_reception": picking.end_time_reception if hasattr(picking, "end_time_reception") else "",
                        "picking_type_code": picking.picking_type_code,
                        "lineas_recepcion": [],
                        "lineas_recepcion_enviadas": [],
                    }

                    # ✅ Procesar solo las líneas pendientes
                    for move in movimientos_pendientes:
                        product = move.product_id
                        purchase_line = move.purchase_line_id

                        # Obtener cantidad ordenada
                        quantity_ordered = 0
                        if purchase_line and purchase_line.product_uom_qty:
                            quantity_ordered = purchase_line.product_uom_qty
                        else:
                            quantity_ordered = move.product_uom_qty

                        # En Odoo 17, quantity_done ya no existe, se usa quantity
                        quantity_done = move.quantity

                        # ⚠️ Saltar líneas totalmente recepcionadas
                        if quantity_done < quantity_ordered:

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
                                "quantity_done": move.quantity,
                                "uom": move.product_uom.name if move.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                            }

                            recepcion_info["lineas_recepcion"].append(linea_info)

                        # ✅ Agregar las líneas de move_line que tengan is_done_item en True
                        # Verificación para campos personalizados

                        move_lines_done = move.move_line_ids.filtered(lambda ml: ml.is_done_item)
                        for move_line in move_lines_done:
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
            if recepcion.user_id:
                return {"code": 400, "msg": "La recepción ya tiene un responsable asignado"}

            try:
                # ✅ Asignar responsable a la recepción
                # El código es igual en Odoo 17, pero agregamos manejo de errores adicional
                responsable_user = request.env["res.users"].sudo().browse(id_responsable)
                if not responsable_user.exists():
                    return {"code": 400, "msg": "El usuario responsable no existe"}

                data = recepcion.write({"user_id": id_responsable})

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

    ## GET Obtener todos los lotes de un producto
    @http.route("/api/lotes/<int:id_producto>", auth="user", type="json", methods=["GET"])
    def get_lotes(self, id_producto):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # ✅ Validar ID de producto
            if not id_producto:
                return {"code": 400, "msg": "ID de producto no válido"}

            # ✅ Buscar producto por ID
            product = request.env["product.product"].sudo().search([("id", "=", id_producto)], limit=1)

            # ✅ Validar producto
            if not product:
                return {"code": 400, "msg": "Producto no encontrado"}

            # ✅ Verificar si el producto tiene seguimiento por lotes
            if product.tracking != "lot":
                return {"code": 400, "msg": "El producto no tiene seguimiento por lotes"}

            # ✅ Obtener todos los lotes del producto
            # En Odoo 17, usamos stock.lot en lugar de stock.production.lot
            lotes = request.env["stock.lot"].sudo().search([("product_id", "=", id_producto)])

            array_lotes = []

            for lote in lotes:
                # Adaptación para Odoo 17 - cambios en los nombres de campos
                array_lotes.append(
                    {
                        "id": lote.id,
                        "name": lote.name,
                        "quantity": lote.product_qty,  # Sigue siendo product_qty en Odoo 17
                        # Campos de fechas actualizados para Odoo 17
                        "expiration_date": lote.expiration_date,
                        "removal_date": lote.removal_date,  # Reemplaza alert_date
                        "use_date": lote.use_date,
                        "product_id": lote.product_id.id,
                        "product_name": lote.product_id.name,
                    }
                )

            return {"code": 200, "result": array_lotes}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Completar Recepcion
    @http.route("/api/send_recepcion", auth="user", type="json", methods=["POST"], csrf=False)
    def send_recepcion(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_recepcion = auth.get("id_recepcion", 0)
            list_items = auth.get("list_items", [])

            # ✅ Buscar recepción por ID
            recepcion = request.env["stock.picking"].sudo().search([("id", "=", id_recepcion), ("picking_type_code", "=", "incoming"), ("state", "!=", "done")], limit=1)

            if not recepcion:
                return {"code": 400, "msg": f"Recepción no encontrada o ya completada con ID {id_recepcion}"}

            array_result = []

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

                # ✅ Validar datos esenciales
                if not product_id or not cantidad:
                    continue

                product = request.env["product.product"].sudo().browse(product_id)
                if not product.exists():
                    continue

                # Si tienes un move_id en los datos de entrada
                if move_id:
                    # En Odoo 17, move_lines cambió a move_ids
                    move = recepcion.move_ids.filtered(lambda m: m.id == move_id)
                else:
                    # Fallback al método actual
                    move = recepcion.move_ids.filtered(lambda m: m.product_id.id == product_id)

                if not move:
                    return {"code": 400, "msg": f"El producto {product.name} no está en la recepción"}

                move_line_vals = {
                    "picking_id": recepcion.id,
                    "move_id": move.id,
                    "product_id": product.id,
                    "quantity": cantidad,
                    "location_id": move.location_id.id,  # Ubicación de origen
                    "location_dest_id": ubicacion_destino or move.location_dest_id.id,  # Ubicación de destino
                    # En Odoo 17, product_uom cambió a product_uom_id
                    "product_uom_id": move.product_uom.id,
                }

                # Inicializar lot como None
                lot = None

                # ✅ Validar si el producto tiene seguimiento por lotes
                if product.tracking == "lot":
                    if not lote_id:
                        return {"code": 400, "msg": f"El producto {product.name} requiere un lote"}
                    else:
                        # En Odoo 17, stock.production.lot cambió a stock.lot
                        lot = request.env["stock.lot"].sudo().browse(lote_id)
                        if not lot.exists():
                            return {"code": 400, "msg": f"Lote no encontrado para el producto {product.name}"}

                        # En Odoo 17, lot_id cambió a lot_id
                        move_line_vals["lot_id"] = lot.id

                # ✅ Crear la línea de movimiento
                # En Odoo 17, stock.move.line sigue siendo el mismo modelo
                move_line = request.env["stock.move.line"].sudo().create(move_line_vals)

                if move_line:
                    # registrar los campos date_transaction new_observation time user_operator_id is_done_item
                    move_line.date_transaction = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)
                    move_line.new_observation = observacion
                    move_line.time = time_line
                    move_line.user_operator_id = id_operario
                    move_line.is_done_item = True

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

            # # ✅ Validar recepción (marcarla como completa)
            # recepcion.sudo().button_validate()

            return {"code": 200, "result": array_result}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

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
                        }
                    )

            return {"code": 200, "result": array_ubicaciones}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

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
