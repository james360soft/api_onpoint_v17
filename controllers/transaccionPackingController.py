# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError, ValidationError
from datetime import datetime, timedelta
import pytz


class TransaccionDataPacking(http.Controller):

    ## GET Transacciones batchs para packing
    # @http.route("/api/batch_packing", auth="user", type="json", methods=["GET"])
    # def get_batch_packing(self):
    #     try:
    #         user = request.env.user
    #         if not user:
    #             return {"code": 401, "msg": "Usuario no autenticado"}

    #         array_batch = []

    #         # ✅ Verificar si el usuario tiene almacenes permitidos
    #         allowed_warehouses = user.allowed_warehouse_ids
    #         if not allowed_warehouses:
    #             return {"code": 400, "msg": "El usuario no tiene acceso a ningún almacén"}

    #         # ✅ Iterar sobre los almacenes permitidos y procesar cada uno
    #         for warehouse in allowed_warehouses:

    #             # Obtener el campo `delivery_steps` del almacén
    #             delivery_steps = warehouse.delivery_steps
    #             if not delivery_steps:
    #                 continue  # Saltar si no hay información sobre `delivery_steps`

    #             # Determinar el `sequence_code` basado en los pasos de entrega
    #             if delivery_steps == "ship_only":
    #                 # 1 paso: Entregar bienes directamente
    #                 sequence_code = "OUT"
    #             elif delivery_steps == "pick_ship":
    #                 # 2 pasos: Enviar bienes a ubicación de salida y entregar
    #                 sequence_code = "OUT"
    #             elif delivery_steps == "pick_pack_ship":
    #                 # 3 pasos: Empaquetar, transferir bienes a ubicación de salida, y enviar
    #                 sequence_code = "PACK"
    #             else:
    #                 continue  # Si no hay una coincidencia válida, saltar este almacén

    #             # ✅ Obtener la estrategia de picking
    #             picking_strategy = request.env["picking.strategy"].sudo().browse(1)

    #             # ✅ Buscar lotes en progreso con secuencia "OUT"
    #             batches = (
    #                 request.env["stock.picking.batch"]
    #                 .sudo()
    #                 .search(
    #                     [
    #                         ("state", "=", "in_progress"),
    #                         ("picking_type_id.sequence_code", "=", sequence_code),
    #                         ("picking_type_id.warehouse_id", "=", warehouse.id),  # Filtro por almacén actual
    #                     ]
    #                 )
    #             )

    #             for batch in batches:
    #                 if batch.move_line_ids:
    #                     user_info = {
    #                         "user_id": batch.user_id.id if batch.user_id else 0,
    #                         "user_name": (batch.user_id.name if batch.user_id else "Desconocido"),
    #                     }

    #                     array_batch_temp = {
    #                         "id": batch.id,
    #                         "name": batch.name,
    #                         "warehouse_id": warehouse.id,
    #                         "warehouse_name": warehouse.name,
    #                         "scheduleddate": batch.scheduled_date,
    #                         "state": batch.state,
    #                         "user_id": user_info["user_id"],
    #                         "user_name": user_info["user_name"],
    #                         "order_by": (picking_strategy.picking_priority_app if picking_strategy else ""),
    #                         "order_picking": (picking_strategy.picking_order_app if picking_strategy else ""),
    #                         "picking_type_id": (batch.picking_type_id.display_name if batch.picking_type_id else "N/A"),
    #                         "cantidad_pedidos": len(batch.picking_ids),
    #                         "lista_pedidos": [],
    #                     }

    #                     for picking in batch.picking_ids:
    #                         pedido = {
    #                             "id": picking.id,
    #                             "batch_id": batch.id,
    #                             "name": picking.name,
    #                             "referencia": picking.origin,
    #                             "contacto": (picking.partner_id.id if picking.partner_id else 0),
    #                             "contacto_name": (picking.partner_id.name if picking.partner_id else "N/A"),
    #                             "tipo_operacion": (picking.picking_type_id.name if picking.picking_type_id else "N/A"),
    #                             "cantidad_productos": len(picking.move_line_ids),
    #                             "zona_entrega": picking.delivery_zone_id.name if picking.delivery_zone_id else "",
    #                             "numero_paquetes": len(picking.package_ids),
    #                             "lista_productos": [],
    #                             "lista_paquetes": [],
    #                         }

    #                         # ✅ Procesar líneas de movimiento
    #                         for move_line in picking.move_line_ids:
    #                             location = move_line.location_id
    #                             location_dest = move_line.location_dest_id

    #                             product = move_line.product_id
    #                             lot = move_line.lot_id

    #                             array_all_barcode = []
    #                             if product.barcode_ids:
    #                                 array_all_barcode = [
    #                                     {
    #                                         "barcode": barcode.name,
    #                                         "batch_id": batch.id,
    #                                         "id_move_line": move_line.id,
    #                                         "id_product": product.id,
    #                                     }
    #                                     for barcode in product.barcode_ids
    #                                     if barcode.name  # Filtra solo los barcodes válidos
    #                                 ]

    #                             array_packing = []
    #                             if product.packaging_ids:
    #                                 array_packing = [
    #                                     {
    #                                         "barcode": pack.barcode,
    #                                         "cantidad": pack.qty,
    #                                         "batch_id": batch.id,
    #                                         "id_move": move_line.id,
    #                                         "id_product": product.id,
    #                                     }
    #                                     for pack in product.packaging_ids
    #                                     if pack.barcode and pack.barcode != False  # Filtra solo si barcode es válido
    #                                 ]

    #                             productos = {
    #                                 "id_move": move_line.id,
    #                                 "product_id": [
    #                                     product.id,
    #                                     product.name,
    #                                 ],
    #                                 "batch_id": batch.id,
    #                                 "pedido_id": picking.id,
    #                                 "id_product": product.id if product else 0,
    #                                 "picking_id": picking.id,
    #                                 "lote_id": lot.id if lot else "",
    #                                 "lot_id": (
    #                                     [
    #                                         lot.id,
    #                                         lot.name if lot else "",
    #                                     ]
    #                                     if lot
    #                                     else []
    #                                 ),
    #                                 "expire_date": (lot.expiration_date if lot else ""),
    #                                 "location_id": [
    #                                     location.id,
    #                                     location.name if location else "",
    #                                 ],
    #                                 "barcode_location": (location.barcode if location else ""),
    #                                 "location_dest_id": [
    #                                     location_dest.id,
    #                                     location_dest.name if location_dest else "",
    #                                 ],
    #                                 "barcode_location_dest": (location_dest.barcode if location_dest else ""),
    #                                 "other_barcode": array_all_barcode,
    #                                 "quantity": move_line.quantity,
    #                                 "tracking": product.tracking if product else "",
    #                                 "barcode": (product.barcode if product.barcode else ""),
    #                                 "product_packing": array_packing,
    #                                 "weight": product.weight if product else 0,
    #                                 "unidades": (product.uom_id.name if product.uom_id else "UND"),
    #                                 "rimoval_priority": location.priority_picking if location else 0,
    #                             }

    #                             pedido["lista_productos"].append(productos)

    #                         # ✅ Procesar paquetes
    #                         for pack in picking.package_ids:

    #                             move_lines_in_package = request.env["stock.move.line"].sudo().search([("package_id", "=", pack.id)])

    #                             # Contar los productos dentro del paquete
    #                             cantidad_productos = len(move_lines_in_package)

    #                             package = {
    #                                 "name": pack.name,
    #                                 "batch_id": batch.id,
    #                                 "pedido_id": picking.id,
    #                                 "cantidad_productos": cantidad_productos,
    #                                 "lista_productos": [],
    #                                 "is_sticker": pack.is_sticker,
    #                                 "is_certificate": pack.is_certificate,
    #                                 "fecha_creacion": (pack.create_date.strftime("%Y-%m-%d") if pack.create_date else ""),
    #                                 "fecha_actualizacion": (pack.write_date.strftime("%Y-%m-%d") if pack.write_date else ""),
    #                             }
    #                             pedido["lista_paquetes"].append(package)

    #                             # ✅ Procesar productos dentro del paquete
    #                             for move_line in move_lines_in_package:
    #                                 product = move_line.product_id
    #                                 lot = move_line.lot_id

    #                                 product_in_packing = {
    #                                     "id_move": move_line.id,
    #                                     "product_id": [
    #                                         product.id,
    #                                         product.name,
    #                                     ],
    #                                     "name_packing": pack.name,
    #                                     "cantidad_enviada": move_line.qty_done,  # Cantidad enviada del producto en el paquete
    #                                     "unidades": (product.uom_id.name if product.uom_id else "UND"),
    #                                     "peso": product.weight if product else 0,
    #                                     "lote_id": (
    #                                         [
    #                                             lot.id,
    #                                             lot.name if lot else "",
    #                                         ]
    #                                         if lot
    #                                         else []
    #                                     ),
    #                                 }

    #                                 package["lista_productos_in_packing"].append(product_in_packing)

    #                         array_batch_temp["lista_pedidos"].append(pedido)

    #                     array_batch.append(array_batch_temp)

    #         return {"code": 200, "result": array_batch}

    #     except AccessError as e:
    #         return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
    #     except Exception as err:
    #         return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/batch_packing", auth="user", type="json", methods=["GET"])
    def get_batch_packing(self):
        try:
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            array_batch = []

            # ✅ Verificar si el usuario tiene almacenes permitidos
            allowed_warehouses = user.allowed_warehouse_ids
            if not allowed_warehouses:
                return {"code": 400, "msg": "El usuario no tiene acceso a ningún almacén"}

            # ✅ Obtener la estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Iterar sobre los almacenes permitidos y procesar cada uno
            for warehouse in allowed_warehouses:

                # Obtener el campo `delivery_steps` del almacén
                delivery_steps = warehouse.delivery_steps
                if not delivery_steps:
                    continue  # Saltar si no hay información sobre `delivery_steps`

                # Determinar el `sequence_code` basado en los pasos de entrega
                if delivery_steps == "ship_only":
                    # 1 paso: Entregar bienes directamente
                    sequence_code = "OUT"
                elif delivery_steps == "pick_ship":
                    # 2 pasos: Enviar bienes a ubicación de salida y entregar
                    sequence_code = "OUT"
                elif delivery_steps == "pick_pack_ship":
                    # 3 pasos: Empaquetar, transferir bienes a ubicación de salida, y enviar
                    sequence_code = "PACK"
                else:
                    continue  # Si no hay una coincidencia válida, saltar este almacén

                # ✅ Buscar lotes en progreso con secuencia "OUT"
                batches = (
                    request.env["stock.picking.batch"]
                    .sudo()
                    .search(
                        [
                            ("state", "=", "in_progress"),
                            ("picking_type_id.sequence_code", "=", sequence_code),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),  # Filtro por almacén actual
                        ]
                    )
                )

                for batch in batches:
                    if batch.move_line_ids:
                        user_info = {
                            "user_id": batch.user_id.id if batch.user_id else 0,
                            "user_name": batch.user_id.name if batch.user_id else "Desconocido",
                        }

                        array_batch_temp = {
                            "id": batch.id,
                            "name": batch.name,
                            "scheduleddate": batch.scheduled_date,
                            "state": batch.state,
                            "user_id": user_info["user_id"],
                            "user_name": user_info["user_name"],
                            "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                            "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                            "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                            "cantidad_pedidos": 0,
                            "start_time_pack": batch.start_time_pack or "",
                            "end_time_pack": batch.end_time_pack or "",
                            "zona_entrega": batch.picking_ids[0].delivery_zone_id.name if batch.picking_ids and batch.picking_ids[0].delivery_zone_id else "N/A",
                            # "zona_entrega_tms": batch.picking_ids[0].delivery_zone_tms if batch.picking_ids and batch.picking_ids[0].delivery_zone_tms else "N/A",
                            # "order_tms": batch.picking_ids[0].order_tms if batch.picking_ids and batch.picking_ids[0].order_tms else "N/A",
                            "lista_pedidos": [],
                        }

                        valid_pickings_found = False

                        for picking in batch.picking_ids:
                            pedido = {
                                "id": picking.id,
                                "batch_id": batch.id,
                                "name": picking.name,
                                "referencia": picking.origin,
                                "contacto": picking.partner_id.id if picking.partner_id else 0,
                                "contacto_name": picking.partner_id.name if picking.partner_id else "N/A",
                                "tipo_operacion": picking.picking_type_id.name if picking.picking_type_id else "N/A",
                                "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item_pack)),
                                "cantidad_productos_total": len(picking.move_line_ids),
                                "zona_entrega": picking.delivery_zone_id.name if picking.delivery_zone_id else "",
                                # "zona_entrega_tms": picking.delivery_zone_tms if picking.delivery_zone_tms else "",
                                # "order_tms": picking.order_tms if picking.order_tms else "",
                                "numero_paquetes": len(picking.move_line_ids.mapped("package_id")),
                                "lista_productos": [],
                                "lista_paquetes": [],
                            }

                            # ✅ Procesar líneas de movimiento
                            for move_line in picking.move_line_ids:
                                location = move_line.location_id
                                location_dest = move_line.location_dest_id

                                product = move_line.product_id
                                lot = move_line.lot_id

                                # ✅ Verificar dinámicamente la existencia de `barcode_ids`
                                array_all_barcode = []
                                if "barcode_ids" in product.fields_get():
                                    array_all_barcode = [
                                        {
                                            "barcode": barcode.name,
                                            "batch_id": batch.id,
                                            "id_move": move_line.id,
                                            "id_product": product.id,
                                        }
                                        for barcode in product.barcode_ids
                                        if barcode.name  # Filtra solo los barcodes válidos
                                    ]

                                # ✅ Obtener empaques del producto
                                array_packing = (
                                    [
                                        {
                                            "barcode": pack.barcode,
                                            "cantidad": pack.qty,
                                            "batch_id": batch.id,
                                            "id_move": move_line.id,
                                            "id_product": product.id,
                                        }
                                        for pack in product.packaging_ids
                                        if pack.barcode  # Incluye solo si barcode es válido
                                    ]
                                    if product.packaging_ids
                                    else []
                                )

                                if move_line.is_done_item_pack == False:
                                    productos = {
                                        "id_move": move_line.id,
                                        "product_id": [product.id, product.name],
                                        "batch_id": batch.id,
                                        "pedido_id": picking.id,
                                        "id_product": product.id if product else 0,
                                        "picking_id": picking.id,
                                        "lote_id": lot.id if lot else "",
                                        "lot_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "expire_date": lot.expiration_date or "",
                                        "location_id": [location.id, location.display_name if location else ""],
                                        "barcode_location": location.barcode if location else "",
                                        "location_dest_id": [location_dest.id, location_dest.name if location_dest else ""],
                                        "barcode_location_dest": location_dest.barcode if location_dest else "",
                                        "other_barcode": array_all_barcode,
                                        "quantity": move_line.quantity,
                                        "tracking": product.tracking if product else "",
                                        "barcode": product.barcode if product else "",
                                        "product_packing": array_packing,
                                        "weight": product.weight if product else 0,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        # "rimoval_priority": location.priority_picking,
                                        "rimoval_priority": location.priority_picking_desplay if location else 0,
                                    }

                                    pedido["lista_productos"].append(productos)

                            # ✅ Procesar paquetes con productos empaquetados (is_done_item_pack == True)
                            move_lines_in_picking = picking.move_line_ids.filtered(lambda ml: ml.package_id or ml.result_package_id)
                            unique_packages = move_lines_in_picking.mapped("package_id") + move_lines_in_picking.mapped("result_package_id")

                            for pack in unique_packages:
                                move_lines_in_package = move_lines_in_picking.filtered(lambda ml: (ml.package_id == pack or ml.result_package_id == pack) and ml.is_done_item_pack)

                                cantidad_productos = len(move_lines_in_package)

                                package = {
                                    "name": pack.name,
                                    "id": pack.id,
                                    "batch_id": batch.id,
                                    "pedido_id": picking.id,
                                    "cantidad_productos": cantidad_productos,
                                    "lista_productos_in_packing": [],
                                    "is_sticker": pack.is_sticker,
                                    "is_certificate": pack.is_certificate,
                                    "fecha_creacion": pack.create_date.strftime("%Y-%m-%d") if pack.create_date else "",
                                    "fecha_actualizacion": pack.write_date.strftime("%Y-%m-%d") if pack.write_date else "",
                                }
                                pedido["lista_paquetes"].append(package)

                                for move_line in move_lines_in_package:
                                    product = move_line.product_id
                                    lot = move_line.lot_id

                                    product_in_packing = {
                                        "id_move": move_line.id,
                                        "product_id": [product.id, product.name],
                                        "name_packing": pack.name,
                                        "cantidad_enviada": move_line.qty_done,
                                        "unidades": product.uom_id.name if product.uom_id else "UND",
                                        "peso": product.weight if product else 0,
                                        "lote_id": [lot.id, lot.name if lot else ""] if lot else [],
                                        "observacion": move_line.new_observation_packing,
                                        "is_sticker": pack.is_sticker,
                                        "is_certificate": pack.is_certificate,
                                        "id_package": pack.id,
                                        "quantity": move_line.quantity,
                                        "tracking": product.tracking if product else "",
                                    }

                                    package["lista_productos_in_packing"].append(product_in_packing)
                            if pedido["lista_productos"]:
                                array_batch_temp["lista_pedidos"].append(pedido)
                                valid_pickings_found = True

                            # Solo añadir el batch al array final si tiene pedidos válidos
                        if valid_pickings_found:
                            array_batch_temp["cantidad_pedidos"] = len(array_batch_temp["lista_pedidos"])
                            array_batch.append(array_batch_temp)

            return {"code": 200, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Transacciones crear paquete para packing
    @http.route("/api/create_package", auth="user", type="json", methods=["POST"])
    def create_packaging(self):
        try:
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            # ✅ Crear un paquete en 'stock.quant.package'
            new_package = request.env["stock.quant.package"].sudo().create({})

            if not new_package:
                return {"code": 400, "msg": "No se pudo crear el paquete"}

            # ✅ Leer el paquete recién creado
            packaging = {
                "id": new_package.id,
                "name": new_package.name,
                "create_date": (new_package.create_date.strftime("%Y-%m-%d %H:%M:%S") if new_package.create_date else ""),
                "write_date": (new_package.write_date.strftime("%Y-%m-%d %H:%M:%S") if new_package.write_date else ""),
            }

            return {
                "code": 200,
                "msg": "Paquete creado correctamente",
                "packaging": packaging,
            }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Transacciones para asignar paquete a pedido o producto en packing
    @http.route("/api/send_packing", auth="user", type="json", methods=["POST"])
    def send_packing(self, **auth):
        try:
            # ✅ Validar autenticación
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            id_paquete = auth.get("id_paquete")
            list_item = auth.get("list_item", [])
            is_sticker = auth.get("is_sticker", False)
            is_certificate = auth.get("is_certificate", False)
            peso_total_paquete = auth.get("peso_total_paquete", 0)

            array_msg = []

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            # ✅ Procesar cada ítem en la lista
            for move in list_item:
                product_id = move.get("product_id")
                location_id = move.get("location_id")
                lote = move.get("lote", None)
                cantidad_separada = move.get("cantidad_separada", 0)
                id_move = move.get("id_move")
                observacion = move.get("observacion", "")
                id_operario = move.get("id_operario", 0)
                fecha_transaccion = move.get("fecha_transaccion", "")

                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                ]

                if lote:
                    domain.append(("lot_id", "=", int(lote)))

                # ✅ Actualizar stock.move.line con el paquete
                move_line = request.env["stock.move.line"].sudo().browse(id_move)

                if move_line.exists():
                    if move_line.quantity_demanded >= cantidad_separada:
                        move_line.write({"qty_done": cantidad_separada, "new_observation_packing": observacion, "user_operator_id": id_operario, "date_transaction_packing": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)})
                    else:
                        array_msg.append(
                            {
                                "code": 400,
                                "msg": f"La cantidad separada {cantidad_separada} es mayor a la cantidad demandada {move_line.quantity_demanded}",
                            }
                        )
                        continue
                else:
                    array_msg.append(
                        {
                            "code": 400,
                            "msg": f"Error al actualizar el paquete en stock.move.line {id_move}",
                        }
                    )

            # ✅ ejecutar la acción de empaquetar del batch
            pack = batch.action_put_in_pack()

            # ✅ Asignar valores a los paquetes creados
            if pack:
                pack.write({"is_sticker": is_sticker, "is_certificate": is_certificate})

            # ✅ Verificar líneas divididas y actualizar `is_done_item_pack`
            if pack:
                move_lines = request.env["stock.move.line"].sudo().search([("picking_id", "in", batch.picking_ids.ids)])

                for move_line in move_lines:
                    # Si la línea tiene qty_done en 0, verificamos si fue dividida de otra línea
                    if move_line.qty_done == 0:
                        original_line = request.env["stock.move.line"].sudo().search([("move_id", "=", move_line.move_id.id), ("id", "!=", move_line.id), ("qty_done", ">", 0)], limit=1)  # Buscar la línea original procesada

                        # Solo actualizar si es una línea nueva dividida
                        if original_line:
                            move_line.write({"is_done_item_pack": False})

            array_msg.append(
                {
                    "id_paquete": pack.id if pack else "",
                    "name_paquete": pack.name if pack else "",
                    "id_batch": batch.id,
                    "cantidad_productos_en_el_paquete": len(list_item),
                    "is_sticker": is_sticker,
                    "is_certificate": is_certificate,
                    "peso": peso_total_paquete,
                    "list_item": list_item,
                }
            )

            return {"code": 200, "result": array_msg}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ### POST Transacciones para desempacar paquete en packing
    @http.route("/api/unpacking", auth="user", type="json", methods=["POST"])
    def unpacking(self, **auth):
        try:
            # ✅ Validar autenticación
            user = request.env.user
            if not user:
                return {"code": 401, "msg": "Usuario no autenticado"}

            id_batch = auth.get("id_batch")
            id_paquete = auth.get("id_paquete")
            list_item = auth.get("list_item", [])

            array_msg = []

            # ✅ Validar si el id_batch existe
            batch = request.env["stock.picking.batch"].sudo().browse(id_batch)
            if not batch.exists():
                return {"code": 400, "msg": f"El id_batch {id_batch} no existe"}

            # ✅ Validar si el paquete existe
            paquete = request.env["stock.quant.package"].sudo().browse(id_paquete)
            if not paquete.exists():
                return {
                    "code": 400,
                    "msg": f"El paquete {id_paquete} no existe",
                }

            # ✅ Procesar cada ítem en la lista
            for move in list_item:
                product_id = move.get("product_id")
                location_id = move.get("location_id")
                lote = move.get("lote", None)
                cantidad_separada = move.get("cantidad_separada", 0)
                id_move = move.get("id_move")
                observacion = move.get("observacion", "")
                id_operario = move.get("id_operario", 0)

                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                ]

                if lote:
                    domain.append(("lot_id", "=", int(lote)))

                # ✅ Actualizar stock.move.line con el paquete
                move_line = request.env["stock.move.line"].sudo().browse(id_move)

                if move_line.exists():
                    move_line.write(
                        {
                            "result_package_id": False,
                            "new_observation_packing": observacion,
                            "user_operator_id": id_operario,
                            "date_transaction_packing": "",
                            "qty_done": 0,
                            "is_done_item_pack": False,
                        }
                    )

                    array_msg.append(
                        {
                            "id_paquete": paquete.id,
                            "name_paquete": paquete.name,
                            "id_batch": batch.id,
                            "cantidad_productos_en_el_paquete": len(list_item),
                            "list_item": list_item,
                        }
                    )
                else:
                    array_msg.append(
                        {
                            "code": 400,
                            "msg": f"Error al actualizar el paquete en stock.move.line {id_move}",
                        }
                    )

            if not batch.move_line_ids.filtered(lambda ml: ml.result_package_id):
                paquete.unlink()
                array_msg.append({"code": 200, "msg": f"El paquete {id_paquete} ha sido eliminado"})

            return {"code": 200, "result": array_msg}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except ValidationError as e:
            return {"code": 400, "msg": f"Error de validación: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}


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
