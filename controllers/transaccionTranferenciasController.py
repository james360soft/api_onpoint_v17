import logging
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz


class TransaccionTransferenciasController(http.Controller):

    # GET obtener todas las transferencias internas
    @http.route("/api/transferencias", auth="user", type="json", methods=["GET"])
    def get_transferencias(self):
        try:
            user = request.env.user

            # Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            array_transferencias = []

            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # Obtener transferencias pendientes de los almacenes permitidos
            for warehouse in allowed_warehouses:
                transferencias_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "=", "assigned"),
                            ("picking_type_code", "=", "internal"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "=", "INT"),
                            ("user_id", "in", [user.id, False]),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_pendientes = picking.move_line_ids

                    # Si no hay movimientos pendientes, omitir
                    if not movimientos_pendientes:
                        continue

                    # Calcular peso total
                    peso_total = sum(move.product_id.weight * move.quantity for move in movimientos_pendientes if move.product_id.weight)

                    # Calcular número de ítems
                    numero_items = sum(move.quantity for move in movimientos_pendientes)

                    transferencia_info = {
                        "id": picking.id,
                        "name": picking.name,
                        "fecha_creacion": picking.create_date,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "location_dest_id": picking.location_dest_id.id,
                        "location_dest_name": picking.location_dest_id.display_name,
                        "numero_transferencia": picking.name,
                        "peso_total": peso_total,
                        "numero_lineas": 0,
                        "numero_items": 0,
                        "state": picking.state,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "responsable_id": picking.user_id.id or 0,
                        "responsable": picking.user_id.name or "",
                        "picking_type": picking.picking_type_id.name,
                        "start_time_transfer": picking.start_time_transfer or "",
                        "end_time_transfer": picking.end_time_transfer or "",
                        "backorder_id": picking.backorder_id.id or 0,
                        "backorder_name": picking.backorder_id.name or "",
                        "show_check_availability": picking.show_check_availability,
                        "lineas_transferencia": [],
                        "lineas_transferencia_enviadas": [],
                    }

                    # Procesar las líneas de movimiento
                    for move_line in movimientos_pendientes:
                        product = move_line.product_id

                        # Obtener códigos de barras
                        array_barcodes = (
                            [
                                {
                                    "barcode": barcode.name,
                                    "id_move": move_line.move_id.id,
                                    "id_product": product.id,
                                    "batch_id": picking.id,
                                }
                                for barcode in product.barcode_ids
                                if barcode.name
                            ]
                            if hasattr(product, "barcode_ids")
                            else []
                        )

                        # Obtener empaques
                        array_packing = (
                            [
                                {
                                    "barcode": pack.barcode,
                                    "cantidad": pack.qty,
                                    "id_move": move_line.move_id.id,
                                    "id_product": product.id,
                                    "batch_id": picking.id,
                                }
                                for pack in product.packaging_ids
                                if pack.barcode
                            ]
                            if hasattr(product, "packaging_ids")
                            else []
                        )

                        # Información de la línea
                        linea_info = {
                            "id": move_line.id,
                            "id_move": move_line.id,
                            "id_transferencia": picking.id,
                            "product_id": product.id,
                            "product_name": product.name,
                            "product_code": product.default_code or "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": array_barcodes,
                            "product_packing": array_packing,
                            "quantity_ordered": move_line.move_id.product_uom_qty,
                            "quantity_to_transfer": move_line.move_id.product_uom_qty,
                            "quantity_done": move_line.quantity,
                            "uom": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                            "location_dest_id": move_line.location_dest_id.id or 0,
                            "location_dest_name": move_line.location_dest_id.display_name or "",
                            "location_dest_barcode": move_line.location_dest_id.barcode or "",
                            "location_id": move_line.location_id.id or 0,
                            "location_name": move_line.location_id.display_name or "",
                            "location_barcode": move_line.location_id.barcode or "",
                            "weight": product.weight or 0,
                            "is_done_item": move_line.is_done_item,
                            "date_transaction": move_line.date_transaction or "",
                            "new_observation": move_line.new_observation or "",
                            "time_line": move_line.time or 0,
                            "user_operator_id": move_line.user_operator_id.id or 0,
                        }

                        # Información del lote
                        if move_line.lot_id:
                            linea_info.update(
                                {
                                    "lot_id": move_line.lot_id.id,
                                    "lot_name": move_line.lot_id.name,
                                    "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                                }
                            )
                        else:
                            linea_info.update(
                                {
                                    "lot_id": 0,
                                    "lot_name": "",
                                    "fecha_vencimiento": "",
                                }
                            )

                        # Clasificar líneas
                        if hasattr(move_line, "is_done_item") and move_line.is_done_item:
                            transferencia_info["lineas_transferencia_enviadas"].append(linea_info)
                        else:
                            transferencia_info["lineas_transferencia"].append(linea_info)

                    transferencia_info["numero_lineas"] = len(transferencia_info["lineas_transferencia"])
                    transferencia_info["numero_items"] = sum(linea["quantity_to_transfer"] for linea in transferencia_info["lineas_transferencia"])
                    array_transferencias.append(transferencia_info)

            return {"code": 200, "result": array_transferencias}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Obtener tranferencia por id
    @http.route("/api/transferencias/<int:id>", auth="user", type="json", methods=["GET"])
    def get_transferencia_by_id(self, id):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # ✅ Buscar la transferencia por ID
            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id)])

            # ✅ Verificar si la transferencia existe
            if not transferencia:
                return {"code": 404, "msg": "Transferencia no encontrada"}

            # ✅ Verificar si el usuario tiene acceso al almacén de esta transferencia
            warehouse = transferencia.picking_type_id.warehouse_id
            if warehouse not in user.allowed_warehouse_ids:
                return {"code": 403, "msg": "No tienes permisos para acceder a esta transferencia"}

            # ✅ Verificar si hay movimientos pendientes
            movimientos_pendientes = transferencia.move_lines.mapped("move_line_ids").filtered(lambda ml: ml.state == "assigned")

            # Si no hay movimientos pendientes, devolver mensaje apropiado
            if not movimientos_pendientes:
                return {"code": 404, "msg": "No hay líneas de transferencia pendientes"}

            # Calcular peso total
            peso_total = sum(move.product_id.weight * move.quantity for move in movimientos_pendientes if move.product_id.weight)

            # Calcular número de ítems (suma total de cantidades)
            numero_items = sum(move.quantity for move in movimientos_pendientes)

            transferencia_info = {
                "id": transferencia.id,
                "name": transferencia.name,
                "fecha_creacion": transferencia.create_date,
                "location_id": transferencia.location_id.id,
                "location_name": transferencia.location_id.display_name,
                "location_dest_id": transferencia.location_dest_id.id,
                "location_dest_name": transferencia.location_dest_id.display_name,
                "numero_transferencia": transferencia.name,
                "peso_total": peso_total,
                "numero_lineas": len(movimientos_pendientes),
                "numero_items": numero_items,
                "state": transferencia.state,
                "origin": transferencia.origin or "",
                "priority": transferencia.priority,
                "warehouse_id": warehouse.id,
                "warehouse_name": warehouse.name,
                "responsable_id": transferencia.user_id.id or 0,
                "responsable": transferencia.user_id.name or "",
                "picking_type": transferencia.picking_type_id.name,
                "lineas_transferencia": [],
                "lineas_transferencia_enviadas": [],
            }

            # ✅ Procesar las líneas de movimiento
            for move_line in movimientos_pendientes:
                product = move_line.product_id

                # Obtener códigos de barras adicionales
                array_barcodes = []
                if "barcode_ids" in product.fields_get():
                    array_barcodes = [
                        {
                            "barcode": barcode.name,
                            "id_move": move_line.move_id.id,
                            "id_product": product.id,
                            "batch_id": transferencia.id,
                        }
                        for barcode in product.barcode_ids
                        if barcode.name
                    ]

                # Obtener empaques del producto
                array_packing = []
                if "packaging_ids" in product.fields_get():
                    array_packing = [
                        {
                            "barcode": pack.barcode,
                            "cantidad": pack.qty,
                            "id_move": move_line.move_id.id,
                            "id_product": product.id,
                            "batch_id": transferencia.id,
                        }
                        for pack in product.packaging_ids
                        if pack.barcode
                    ]

                # Generar la información de la línea
                linea_info = {
                    "id": move_line.id,
                    "id_move": move_line.move_id.id,
                    "id_transferencia": transferencia.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_code": product.default_code or "",
                    "product_barcode": product.barcode or "",
                    "product_tracking": product.tracking or "",
                    "dias_vencimiento": product.expiration_time or "",
                    "other_barcodes": array_barcodes,
                    "product_packing": array_packing,
                    "quantity_ordered": move_line.move_id.product_uom_qty,
                    "quantity_to_transfer": move_line.product_qty,
                    "quantity_done": move_line.quantity,
                    "uom": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                    "location_dest_id": move_line.location_dest_id.id or 0,
                    "location_dest_name": move_line.location_dest_id.display_name or "",
                    "location_dest_barcode": move_line.location_dest_id.barcode or "",
                    "location_id": move_line.location_id.id or 0,
                    "location_name": move_line.location_id.display_name or "",
                    "location_barcode": move_line.location_id.barcode or "",
                    "weight": product.weight or 0,
                }

                # Añadir información específica del lote
                if move_line.lot_id:
                    linea_info.update(
                        {
                            "lot_id": move_line.lot_id.id,
                            "lot_name": move_line.lot_id.name,
                            "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                        }
                    )
                else:
                    linea_info.update(
                        {
                            "lot_id": 0,
                            "lot_name": "",
                            "fecha_vencimiento": "",
                        }
                    )

                # Determinar a qué lista añadir la línea según is_done_item
                if hasattr(move_line, "is_done_item") and move_line.is_done_item:
                    transferencia_info["lineas_transferencia_enviadas"].append(linea_info)
                else:
                    transferencia_info["lineas_transferencia"].append(linea_info)

            return {"code": 200, "result": transferencia_info}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Asignar responsable a transferencia
    @http.route("/api/transferencias/asignar", auth="user", type="json", methods=["POST"], csrf=False)
    def asignar_responsable_transferencia(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_tranfer = auth.get("id_transferencia", 0)
            id_responsable = auth.get("id_responsable", 0)

            # ✅ Buscar la transferencia por ID
            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_tranfer)])

            # ✅ Verificar si la transferencia existe
            if not transferencia:
                return {"code": 404, "msg": "Transferencia no encontrada"}

            if transferencia.user_id:
                return {"code": 400, "msg": "La transferencia ya tiene un responsable asignado"}

            # ✅ Buscar el usuario responsable
            responsable = request.env["res.users"].sudo().search([("id", "=", id_responsable)])

            # ✅ Verificar si el usuario responsable existe
            if not responsable:
                return {"code": 404, "msg": "Usuario responsable no encontrado"}

            try:
                transferencia.write({"user_id": id_responsable})

                return {"code": 200, "msg": "Responsable asignado correctamente"}

            except Exception as err:
                return {"code": 400, "msg": f"Error al asignar responsable: {str(err)}"}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Enviar cantidad de producto en transferencia
    @http.route("/api/send_transfer", auth="user", type="json", methods=["POST"], csrf=False)
    def send_transfer(self, **auth):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = auth.get("id_transferencia", 0)
            list_items = auth.get("list_items", [])

            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)])

            if not transferencia:
                return {"code": 404, "msg": "Transferencia no encontrada"}

            array_result = []

            for item in list_items:
                id_move = item.get("id_move")
                id_product = item.get("id_producto")
                cantidad_enviada = item.get("cantidad_enviada", 0)
                id_ubicacion_destino = item.get("id_ubicacion_destino", 0)
                id_ubicacion_origen = item.get("id_ubicacion_origen", 0)
                id_lote = item.get("id_lote", 0)
                id_operario = item.get("id_operario")
                fecha_transaccion = item.get("fecha_transaccion", "")
                time_line = int(item.get("time_line", 0))
                novedad = item.get("observacion", "")
                dividida = item.get("dividida", False)

                # Buscar movimiento original
                original_move = request.env["stock.move.line"].sudo().search([("id", "=", id_move)])
                if not original_move:
                    return {"code": 404, "msg": f"Movimiento no encontrado (ID: {id_move})"}

                move_parent = original_move.move_id

                # Buscar producto
                product = request.env["product.product"].sudo().search([("id", "=", id_product)])

                if product.tracking == "lot" and not id_lote:
                    return {"code": 400, "msg": "El producto requiere lote y no se ha proporcionado uno"}

                # Validar cantidad total enviada
                move_lines = request.env["stock.move.line"].sudo().search([("move_id", "=", move_parent.id)])
                qty_total_enviada = sum(ml.qty_done for ml in move_lines)

                # if qty_total_enviada + cantidad_enviada > move_parent.product_uom_qty:
                #     return {"code": 400, "msg": f"La cantidad total enviada ({qty_total_enviada + cantidad_enviada}) excede la cantidad reservada ({move_parent.product_uom_qty})"}

                fecha = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)

                if dividida:
                    # Crear nueva línea
                    new_move_values = {
                        "move_id": move_parent.id,
                        "product_id": id_product,
                        "product_uom_id": original_move.product_uom_id.id,
                        "location_id": id_ubicacion_origen,
                        "location_dest_id": id_ubicacion_destino,
                        "qty_done": cantidad_enviada,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                        "picking_id": id_transferencia,
                    }

                    new_move = request.env["stock.move.line"].sudo().create(new_move_values)

                    array_result.append(
                        {
                            "id_move": new_move.id,
                            "id_transferencia": id_transferencia,
                            "id_product": new_move.product_id.id,
                            "qty_done": new_move.qty_done,
                            "is_done_item": new_move.is_done_item,
                            "date_transaction": new_move.date_transaction,
                            "new_observation": new_move.new_observation,
                            "time_line": new_move.time,
                            "user_operator_id": new_move.user_operator_id.id,
                        }
                    )
                else:
                    # Validar que la línea original no esté ya usada
                    # if original_move.qty_done > 0:
                    #     return {"code": 400, "msg": f"La línea original (ID: {id_move}) ya fue procesada"}

                    update_values = {
                        "qty_done": cantidad_enviada,
                        "location_dest_id": id_ubicacion_destino,
                        "location_id": id_ubicacion_origen,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                    }

                    original_move.write(update_values)

                    array_result.append(
                        {
                            "id_move": original_move.id,
                            "id_transferencia": id_transferencia,
                            "id_product": original_move.product_id.id,
                            "qty_done": original_move.qty_done,
                            "is_done_item": original_move.is_done_item,
                            "date_transaction": original_move.date_transaction,
                            "new_observation": original_move.new_observation,
                            "time_line": original_move.time,
                            "user_operator_id": original_move.user_operator_id.id,
                        }
                    )

            return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Completar transferencia
    @http.route("/api/complete_transfer", auth="user", type="json", methods=["POST"], csrf=False)
    def completar_transferencia(self, **auth):
        try:
            user = request.env.user
            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = auth.get("id_transferencia", 0)
            crear_backorder = auth.get("crear_backorder", True)

            # ✅ Buscar transferencia por ID
            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia), ("picking_type_code", "=", "internal"), ("picking_type_id.sequence_code", "=", "INT"), ("state", "=", "assigned")], limit=1)

            if not transferencia:
                return {"code": 404, "msg": f"Transferencia no encontrada o ya completada con ID {id_transferencia}"}

            # Verificar si hay líneas de movimiento que validar
            if not transferencia.move_ids_without_package:
                return {"code": 400, "msg": "La transferencia no tiene líneas de movimiento"}

            # Intentar validar la transferencia
            result = transferencia.with_context(skip_backorder=not crear_backorder).sudo().button_validate()
            # Si el resultado es un diccionario, significa que se requiere acción adicional (un wizard)
            if isinstance(result, dict) and result.get("res_model"):
                wizard_model = result.get("res_model")

                # Para asistente de backorder
                if wizard_model == "stock.backorder.confirmation":
                    wizard_context = result.get("context", {})

                    wizard_vals = {"pick_ids": [(6, 0, [transferencia.id])], "show_transfers": wizard_context.get("default_show_transfers", False)}

                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)

                    # Procesar según la opción de crear_backorder
                    if crear_backorder:
                        # En lugar de llamar al método process, vamos a completar la transferencia directamente
                        transferencia.sudo()._action_done()

                        # Verificar si se creó una backorder
                        backorder = request.env["stock.picking"].sudo().search([("backorder_id", "=", transferencia.id), ("state", "not in", ["done", "cancel"])], limit=1)

                        return {"code": 200, "msg": "Transferencia procesada directamente", "original_id": transferencia.id, "original_state": transferencia.state, "backorder_id": backorder.id if backorder else False}
                    else:
                        transferencia.sudo()._action_done()

                        return {"code": 200, "msg": "Transferencia completada sin backorder", "original_id": transferencia.id, "original_state": transferencia.state}

                # Para asistente de transferencia inmediata
                elif wizard_model == "stock.immediate.transfer":
                    wizard_context = result.get("context", {})
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({})

                    # En lugar de usar el wizard, completar directamente
                    transferencia.sudo()._action_done()

                    return {"code": 200, "msg": "Transferencia completada con éxito", "original_id": transferencia.id, "original_state": transferencia.state}

                else:
                    return {"code": 400, "msg": f"Acción adicional requerida no soportada: {wizard_model}"}

            elif isinstance(result, bool) and result:
                # Si button_validate retornó True, la transferencia se completó correctamente
                return {"code": 200, "msg": "Transferencia completada directamente", "original_id": transferencia.id, "original_state": transferencia.state}
            else:
                return {"code": 400, "msg": f"No se pudo completar la transferencia: {result}"}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Comprobación de disponibilidad de transferencia
    @http.route("/api/comprobar_disponibilidad", auth="user", type="json", methods=["POST"], csrf=False)
    def check_availability(self, **post):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = post.get("id_transferencia")
            if not id_transferencia:
                return {"code": 400, "msg": "ID de transferencia requerido"}

            picking = request.env["stock.picking"].browse(int(id_transferencia))
            if not picking.exists():
                return {"code": 404, "msg": "Transferencia no encontrada"}

            # ✅ Envolver en try por si falla el action_assign
            try:
                picking.action_assign()
            except Exception as e:
                return {"code": 500, "msg": f"Error al comprobar disponibilidad: {str(e)}"}

            return {
                "code": 200,
                "msg": "Disponibilidad comprobada correctamente",
                "picking_id": picking.id,
                "state": picking.state,
            }

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
