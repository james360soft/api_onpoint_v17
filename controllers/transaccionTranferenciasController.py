import logging
from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz


class TransaccionTransferenciasController(http.Controller):

    ## GET Obtener todas las transferencias
    @http.route("/api/transferencias", auth="user", type="json", methods=["GET"])
    def get_transferencias(self):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            array_transferencias = []

            allowed_warehouses = obtener_almacenes_usuario(user)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            for warehouse in allowed_warehouses:
                transferencias_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            ("picking_type_code", "=", "internal"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "in", ["INT", "ALT"]),
                            ("responsable_id", "in", [user.id, False]),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_operaciones = picking.move_line_ids
                    movimientos_enviados = picking.move_line_ids

                    if not movimientos_operaciones:
                        continue

                    transferencia_info = {
                        "id": picking.id,
                        "name": picking.name,
                        "fecha_creacion": picking.create_date,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "location_barcode": picking.location_id.barcode or "",
                        "location_dest_id": picking.location_dest_id.id,
                        "location_dest_name": picking.location_dest_id.display_name,
                        "location_dest_barcode": picking.location_dest_id.barcode or "",
                        "proveedor": picking.partner_id.name or "",
                        "numero_transferencia": picking.name,
                        "peso_total": 0,
                        "numero_lineas": 0,
                        "numero_items": sum(move.product_uom_qty for move in picking.move_ids),
                        "state": picking.state,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "responsable_id": picking.responsable_id.id or 0,
                        "responsable": picking.responsable_id.name or "",
                        "picking_type": picking.picking_type_id.name,
                        "start_time_transfer": picking.start_time_transfer or "",
                        "end_time_transfer": picking.end_time_transfer or "",
                        "backorder_id": picking.backorder_id.id or 0,
                        "backorder_name": picking.backorder_id.name or "",
                        "show_check_availability": picking.show_check_availability,
                        "lineas_transferencia": [],
                        "lineas_transferencia_enviadas": [],
                    }

                    for move in movimientos_operaciones:
                        product = move.product_id
                        quantity_done = move.quantity or 0
                        quantity_ordered = move.move_id.product_uom_qty or 0

                        cantidad_faltante = quantity_ordered - quantity_done

                        cantidad_faltante = quantity_ordered - cantidad_faltante

                        if quantity_done == 0:
                            continue

                        if not move.is_done_item:

                            linea_info = {
                                "id": move.move_id.id if move.move_id else 0,
                                "id_move": move.id,
                                "id_transferencia": picking.id,
                                "product_id": product.id,
                                "product_name": product.name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                                "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                                "quantity_ordered": quantity_ordered,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "is_done_item": False,
                                "date_transaction": "",
                                "observation": "",
                                "time": 0,
                                "user_operator_id": 0,
                            }

                            if move.lot_id:
                                linea_info.update(
                                    {
                                        "lot_id": move.lot_id.id,
                                        "lot_name": move.lot_id.name,
                                        "fecha_vencimiento": move.lot_id.expiration_date or "",
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

                            transferencia_info["lineas_transferencia"].append(linea_info)

                    for move_line in movimientos_enviados:
                        if not move_line.is_done_item:
                            continue

                        product = move_line.product_id
                        quantity_ordered = move_line.move_id.product_uom_qty or 0

                        quantity_done = move_line.quantity or 0

                        cantidad_faltante = quantity_ordered - quantity_done

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
                            "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                            "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move_line.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                            "quantity_ordered": quantity_ordered,
                            "quantity_to_transfer": quantity_ordered,
                            "quantity_done": move_line.quantity,
                            "cantidad_faltante": quantity_ordered,
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
                            "observation": move_line.new_observation or "",
                            "time": move_line.time or 0,
                            "user_operator_id": move_line.user_operator_id.id if move_line.user_operator_id else 0,
                        }

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

                        transferencia_info["lineas_transferencia_enviadas"].append(linea_info)

                    transferencia_info["numero_lineas"] = len(transferencia_info["lineas_transferencia"])
                    # transferencia_info["numero_items"] = sum(l["quantity_to_transfer"] for l in transferencia_info["lineas_transferencia"])

                    array_transferencias.append(transferencia_info)

            return {"code": 200, "result": array_transferencias}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Obtener tranferencia PICK
    @http.route("/api/transferencias/pick", auth="user", type="json", methods=["GET"])
    def get_transferencias_pick(self):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            array_transferencias = []

            allowed_warehouses = obtener_almacenes_usuario(user)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            for warehouse in allowed_warehouses:
                transferencias_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            ("picking_type_code", "=", "internal"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "in", ["PICK"]),
                            ("responsable_id", "in", [user.id, False]),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_operaciones = picking.move_line_ids
                    movimientos_enviados = picking.move_line_ids

                    if not movimientos_operaciones:
                        continue

                    transferencia_info = {
                        "id": picking.id,
                        "name": picking.name,
                        "fecha_creacion": picking.create_date,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "location_barcode": picking.location_id.barcode or "",
                        "location_dest_id": picking.location_dest_id.id,
                        "location_dest_name": picking.location_dest_id.display_name,
                        "location_dest_barcode": picking.location_dest_id.barcode or "",
                        "proveedor": picking.partner_id.name or "",
                        "numero_transferencia": picking.name,
                        "peso_total": 0,
                        "numero_lineas": 0,
                        "numero_items": sum(move.product_uom_qty for move in picking.move_ids),
                        "state": picking.state,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "responsable_id": picking.responsable_id.id or 0,
                        "responsable": picking.responsable_id.name or "",
                        "picking_type": picking.picking_type_id.name,
                        "start_time_transfer": picking.start_time_transfer or "",
                        "end_time_transfer": picking.end_time_transfer or "",
                        "backorder_id": picking.backorder_id.id or 0,
                        "backorder_name": picking.backorder_id.name or "",
                        "show_check_availability": picking.show_check_availability,
                        "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                        "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                        "muelle": picking.location_dest_id.display_name or "",
                        "muelle_id": picking.location_dest_id.id or 0,
                        "barcode_muelle": picking.location_dest_id.barcode or "",
                        "lineas_transferencia": [],
                        "lineas_transferencia_enviadas": [],
                    }

                    for move in movimientos_operaciones:
                        product = move.product_id
                        quantity_done = move.quantity or 0
                        quantity_ordered = move.move_id.product_uom_qty or 0

                        cantidad_faltante = quantity_ordered - quantity_done

                        cantidad_faltante = quantity_ordered - cantidad_faltante

                        location = move.location_id

                        array_all_barcode = (
                            [
                                {
                                    "barcode": barcode.name,
                                    "batch_id": picking.id,
                                    "id_move": move.move_id.id if move.move_id else 0,
                                    "id_product": product.id or 0,
                                }
                                for barcode in product.barcode_ids
                                if barcode.name
                            ]
                            if hasattr(product, "barcode_ids")
                            else []
                        )

                        # ✅ Obtener empaques del producto
                        array_packing = (
                            [
                                {
                                    "barcode": pack.barcode,
                                    "cantidad": pack.qty,
                                    "batch_id": picking.id,
                                    "id_move": move.move_id.id if move.move_id else 0,
                                    "product_id": move["product_id"][0] if move["product_id"] else 0,
                                }
                                for pack in product.packaging_ids
                                if pack.barcode
                            ]
                            if product.packaging_ids
                            else []
                        )

                        if quantity_done == 0:
                            continue

                        if not move.is_done_item:
                            linea_info = {
                                "id": move.move_id.id if move.move_id else 0,
                                "id_move": move.id,
                                "id_transferencia": picking.id,
                                "batch_id": picking.id,  # ✅
                                "id_product": product.id,
                                "product_id": [product.id, product.name],  # ✅
                                "product_name": product.name,
                                "product_code": product.default_code or "",
                                "barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                                "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                                "quantity": quantity_done,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "unidades": move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND",
                                "location_dest_id": [move.location_dest_id.id, move.location_dest_id.display_name],  # ✅
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "barcode_location_dest": move.location_dest_id.barcode or "",
                                "location_id": [move.location_id.id, move.location_id.display_name],  # ✅
                                "location_name": move.location_id.display_name or "",
                                "barcode_location": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "rimoval_priority": location.priority_picking_desplay,
                                "zona_entrega": picking.delivery_zone_id.display_name,
                                "other_barcode": array_all_barcode,
                                "product_packing": array_packing,
                                "pedido": picking.name,
                                "pedido_id": picking.id,
                                "origin": picking.origin or "",
                                "lote_id": move.lot_id.id or 0,
                                "lote": move.lot_id.name or "",
                                # "is_done_item": False,
                                # "date_transaction": "",
                                # "observation": "",
                                # "time": 0,
                                # "user_operator_id": 0,
                                # "expire_date": move.lot_id.expiration_date or "",
                            }

                            # if move.lot_id:
                            #     linea_info.update({"lote": move.lot_id.name, "lot_id": move.lot_id.id or 0})
                            # else:
                            #     linea_info.update(
                            #         {
                            #             "lote": "",
                            #             "lot_id": 0,
                            #         }
                            #     )

                            transferencia_info["lineas_transferencia"].append(linea_info)

                    for move_line in movimientos_enviados:
                        if not move_line.is_done_item:
                            continue

                        product = move_line.product_id
                        quantity_ordered = move_line.move_id.product_uom_qty or 0

                        quantity_done = move_line.quantity or 0

                        cantidad_faltante = quantity_ordered - quantity_done

                        linea_info = {
                            "id": move_line.id,
                            "id_move": move_line.id,
                            "id_transferencia": picking.id,
                            "batch_id": picking.id,  # ✅
                            "id_product": product.id,
                            "product_id": [product.id, product.name],  # ✅
                            "product_name": product.name,
                            "product_code": product.default_code or "",
                            "barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                            "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move_line.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                            "quantity": quantity_ordered,
                            "quantity_to_transfer": quantity_ordered,
                            "quantity_done": move_line.quantity,
                            "cantidad_faltante": quantity_ordered,
                            "unidades": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                            "location_dest_id": [move_line.location_dest_id.id, move_line.location_dest_id.display_name],  # ✅
                            "location_dest_name": move_line.location_dest_id.display_name or "",
                            "barcode_location_dest": move_line.location_dest_id.barcode or "",
                            "location_id": [move_line.location_id.id, move_line.location_id.display_name],  # ✅
                            "location_name": move_line.location_id.display_name or "",
                            "barcode_location": move_line.location_id.barcode or "",
                            "weight": product.weight or 0,
                            "rimoval_priority": location.priority_picking_desplay,
                            "zona_entrega": picking.delivery_zone_id.display_name,
                            "other_barcode": array_all_barcode,
                            "product_packing": array_packing,
                            "pedido": picking.name,
                            "pedido_id": picking.id,
                            "origin": picking.origin or "",
                            "lote_id": move_line.lot_id.id or "",
                            "lote": move_line.lot_id.name or "",
                            # "is_done_item": move_line.is_done_item,
                            # "date_transaction": move_line.date_transaction or "",
                            # "observation": move_line.new_observation or "",
                            # "time": move_line.time or 0,
                            # "user_operator_id": move_line.user_operator_id.id if move_line.user_operator_id else 0,
                            # "expire_date": move_line.lot_id.expiration_date or "",
                        }

                        # if move_line.lot_id:
                        #     linea_info.update({"lot_id": [move_line.lot_id.id, move_line.lot_id.name]})
                        # else:
                        #     linea_info.update(
                        #         {
                        #             "lot_id": [
                        #                 0,
                        #                 "N/A",
                        #             ],
                        #         }
                        #     )

                        transferencia_info["lineas_transferencia_enviadas"].append(linea_info)

                    transferencia_info["numero_lineas"] = len(transferencia_info["lineas_transferencia"])
                    # transferencia_info["numero_items"] = sum(l["quantity_to_transfer"] for l in transferencia_info["lineas_transferencia"])

                    if transferencia_info["lineas_transferencia"]:
                        array_transferencias.append(transferencia_info)

            return {"code": 200, "result": array_transferencias}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Obtener tranferencia PACK
    @http.route("/api/transferencias/pack", auth="user", type="json", methods=["GET"])
    def get_transferencias_pack(self):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            array_transferencias = []

            allowed_warehouses = obtener_almacenes_usuario(user)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

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

                transferencias_pendientes = (
                    request.env["stock.picking"]
                    .sudo()
                    .search(
                        [
                            ("state", "in", ["assigned", "confirmed"]),
                            # ("picking_type_code", "=", "internal"),
                            ("picking_type_id.warehouse_id", "=", warehouse.id),
                            ("picking_type_id.sequence_code", "in", [sequence_code]),
                            ("responsable_id", "in", [user.id, False]),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_operaciones = picking.move_line_ids
                    movimientos_enviados = picking.move_line_ids

                    if not movimientos_operaciones:
                        continue

                    transferencia_info = {
                        "id": picking.id,
                        "name": picking.name,
                        "fecha_creacion": picking.create_date,
                        "location_id": picking.location_id.id,
                        "location_name": picking.location_id.display_name,
                        "location_barcode": picking.location_id.barcode or "",
                        "location_dest_id": picking.location_dest_id.id,
                        "location_dest_name": picking.location_dest_id.display_name,
                        "location_dest_barcode": picking.location_dest_id.barcode or "",
                        "proveedor": picking.partner_id.name or "",
                        "numero_transferencia": picking.name,
                        "peso_total": 0,
                        "numero_lineas": 0,
                        "numero_items": sum(move.product_uom_qty for move in picking.move_ids),
                        "state": picking.state,
                        "origin": picking.origin or "",
                        "priority": picking.priority,
                        "warehouse_id": warehouse.id,
                        "warehouse_name": warehouse.name,
                        "responsable_id": picking.responsable_id.id or 0,
                        "responsable": picking.responsable_id.name or "",
                        "picking_type": picking.picking_type_id.name,
                        "start_time_transfer": picking.start_time_transfer or "",
                        "end_time_transfer": picking.end_time_transfer or "",
                        "backorder_id": picking.backorder_id.id or 0,
                        "backorder_name": picking.backorder_id.name or "",
                        "show_check_availability": picking.show_check_availability,
                        "lineas_transferencia": [],
                        "lineas_transferencia_enviadas": [],
                    }

                    for move in movimientos_operaciones:
                        product = move.product_id
                        quantity_done = move.quantity or 0
                        quantity_ordered = move.move_id.product_uom_qty or 0

                        cantidad_faltante = quantity_ordered - quantity_done

                        cantidad_faltante = quantity_ordered - cantidad_faltante

                        if quantity_done == 0:
                            continue

                        if not move.is_done_item:

                            linea_info = {
                                "id": move.move_id.id if move.move_id else 0,
                                "id_move": move.id,
                                "id_transferencia": picking.id,
                                "product_id": product.id,
                                "product_name": product.name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                                "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                                "quantity_ordered": quantity_ordered,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND",
                                "location_dest_id": move.location_dest_id.id or 0,
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "location_dest_barcode": move.location_dest_id.barcode or "",
                                "location_id": move.location_id.id or 0,
                                "location_name": move.location_id.display_name or "",
                                "location_barcode": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "is_done_item": False,
                                "date_transaction": "",
                                "observation": "",
                                "time": 0,
                                "user_operator_id": 0,
                            }

                            if move.lot_id:
                                linea_info.update(
                                    {
                                        "lot_id": move.lot_id.id,
                                        "lot_name": move.lot_id.name,
                                        "fecha_vencimiento": move.lot_id.expiration_date or "",
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

                            transferencia_info["lineas_transferencia"].append(linea_info)

                    for move_line in movimientos_enviados:
                        if not move_line.is_done_item:
                            continue

                        product = move_line.product_id
                        quantity_ordered = move_line.move_id.product_uom_qty or 0

                        quantity_done = move_line.quantity or 0

                        cantidad_faltante = quantity_ordered - quantity_done

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
                            "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                            "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move_line.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                            "quantity_ordered": quantity_ordered,
                            "quantity_to_transfer": quantity_ordered,
                            "quantity_done": move_line.quantity,
                            "cantidad_faltante": quantity_ordered,
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
                            "observation": move_line.new_observation or "",
                            "time": move_line.time or 0,
                            "user_operator_id": move_line.user_operator_id.id if move_line.user_operator_id else 0,
                        }

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

                        transferencia_info["lineas_transferencia_enviadas"].append(linea_info)

                    transferencia_info["numero_lineas"] = len(transferencia_info["lineas_transferencia"])
                    # transferencia_info["numero_items"] = sum(l["quantity_to_transfer"] for l in transferencia_info["lineas_transferencia"])

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
            if transferencia.picking_type_id.warehouse_id not in obtener_almacenes_usuario(user):
                return {"code": 403, "msg": "Acceso denegado a la transferencia"}

            # ✅ Obtener líneas de movimiento
            movimientos = transferencia.move_line_ids

            array_result = []

            for move in movimientos:
                product = move.product_id

                # Obtener códigos de barras
                array_barcodes = (
                    [
                        {
                            "barcode": barcode.name,
                            "id_move": move.move_id.id,
                            "id_product": product.id,
                            "batch_id": transferencia.id,
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
                            "id_move": move.move_id.id,
                            "id_product": product.id,
                            "batch_id": transferencia.id,
                        }
                        for pack in product.packaging_ids
                        if pack.barcode
                    ]
                    if hasattr(product, "packaging_ids")
                    else []
                )

                # Información de la línea
                linea_info = {
                    "id": move.id,
                    "id_move": move.id,
                    "id_transferencia": transferencia.id,
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_code": product.default_code or "",
                    "product_barcode": product.barcode or "",
                    "product_tracking": product.tracking or "",
                    "dias_vencimiento": product.expiration_time or "",
                    "other_barcodes": array_barcodes,
                    "product_packing": array_packing,
                    "quantity_ordered": move.move_id.product_uom_qty,
                    "quantity_to_transfer": move.move_id.product_uom_qty,
                    "quantity_done": move.quantity,
                    "uom": move.product_uom_id.name if move.product_uom_id else "UND",
                    "location_dest_id": move.location_dest_id.id or 0,
                    "location_dest_name": move.location_dest_id.display_name or "",
                    "location_dest_barcode": move.location_dest_id.barcode or "",
                    "location_id": move.location_id.id or 0,
                    "location_name": move.location_id.display_name or "",
                    "location_barcode": move.location_id.barcode or "",
                    "weight": product.weight or 0,
                    "is_done_item": move.is_done_item,
                    "date_transaction": move.date_transaction or "",
                    "new_observation": move.new_observation or "",
                    "time_line": move.time or 0,
                    "user_operator_id": move.user_operator_id.id or 0,
                }

                # Información del lote
                if move.lot_id:
                    linea_info.update(
                        {
                            "lot_id": move.lot_id.id,
                            "lot_name": move.lot_id.name,
                            "fecha_vencimiento": move.lot_id.expiration_date or "",
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

                array_result.append(linea_info)

            return {"code": 200, "result": array_result}

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

            if transferencia.responsable_id:
                return {"code": 400, "msg": "La transferencia ya tiene un responsable asignado"}

            # ✅ Buscar el usuario responsable
            responsable = request.env["res.users"].sudo().search([("id", "=", id_responsable)])

            # ✅ Verificar si el usuario responsable existe
            if not responsable:
                return {"code": 404, "msg": "Usuario responsable no encontrado"}

            try:
                transferencia.write({"responsable_id": id_responsable})

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

                original_move = request.env["stock.move.line"].sudo().search([("id", "=", id_move)])
                if not original_move:
                    return {"code": 404, "msg": f"Movimiento no encontrado (ID: {id_move})"}

                stock_move = original_move.move_id

                move_parent = original_move.move_id
                product = request.env["product.product"].sudo().search([("id", "=", id_product)])

                if product.tracking == "lot" and not id_lote:
                    return {"code": 400, "msg": "El producto requiere lote y no se ha proporcionado uno"}

                fecha = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)

                noveda_minuscula = novedad.lower()
                if "pendiente" in noveda_minuscula or "pendientes" in noveda_minuscula:
                    dividida = False

                if dividida:
                    update_values = {
                        "quantity": cantidad_enviada,  # ← este es el bueno en Odoo 17
                        "location_dest_id": id_ubicacion_destino,
                        "location_id": id_ubicacion_origen,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                    }

                    # Se toma la cantidad de la linea porque sabemos que esa es la que se puede usar o dividir porque es la que el sistema recerbo
                    cantidad_inicial = original_move.quantity

                    # Actualizamos los datos con lo que nos envian
                    original_move.sudo().write(update_values)
                    # Verificar si hay cantidad restante por enviar
                    # lineas_enviadas = request.env["stock.move.line"].sudo().search([("move_id", "=", move_parent.id), ("is_done_item", "=", True)])
                    # cantidad_total_enviada = sum(linea.quantity for linea in lineas_enviadas)
                    # cantidad_demandada = move_parent.product_uom_qty
                    # cantidad_restante = cantidad_demandada - cantidad_total_enviada

                    # sacamos la cantidad que necesitamos para crear una nueva linea, que seria la cantidad inicial menos la enviada entonces asi sabemos bajo que rango podemos dividir una cantidad basado lo recerbado del sistema
                    cantidad_restante = cantidad_inicial - cantidad_enviada

                    ### NOTA
                    # Entonces se dividira por linea, la lineas se peude dividir n veces segun la cantidad que haya tenido inciialemnte que seria lo que el sistema recerbo

                    # return {"code": 200, "msg": cantidad_restante, "cantidad_total_enviada": cantidad_total_enviada, "cantidad_demandada": cantidad_demandada}
                    if cantidad_restante > 0:
                        linea_restante = (
                            request.env["stock.move.line"]
                            .sudo()
                            .create(
                                {
                                    "move_id": move_parent.id,
                                    "product_id": id_product,
                                    "product_uom_id": original_move.product_uom_id.id,
                                    "location_id": id_ubicacion_origen,
                                    "location_dest_id": id_ubicacion_destino,
                                    "quantity": cantidad_restante,
                                    "lot_id": id_lote if id_lote else False,
                                    "is_done_item": False,
                                    "date_transaction": False,
                                    "new_observation": "Cantidad pendiente por enviar",
                                    "time": 0,
                                    "user_operator_id": False,
                                    "picking_id": id_transferencia,
                                }
                            )
                        )

                        array_result.append(
                            {
                                "id_move": linea_restante.id,
                                "id_transferencia": id_transferencia,
                                "id_product": linea_restante.product_id.id,
                                "quantity": linea_restante.quantity,
                                "is_done_item": linea_restante.is_done_item,
                                "date_transaction": linea_restante.date_transaction,
                                "new_observation": linea_restante.new_observation,
                                "time_line": linea_restante.time,
                                "user_operator_id": None,
                            }
                        )

                else:
                    update_values = {
                        "quantity": cantidad_enviada,  # ← este es el bueno en Odoo 17
                        "location_dest_id": id_ubicacion_destino,
                        "location_id": id_ubicacion_origen,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                    }

                    original_move.sudo().write(update_values)

                    array_result.append(
                        {
                            "id_move": original_move.id,
                            "id_transferencia": id_transferencia,
                            "id_product": original_move.product_id.id,
                            "quantity": original_move.quantity,
                            "is_done_item": original_move.is_done_item,
                            "date_transaction": original_move.date_transaction,
                            "new_observation": original_move.new_observation,
                            "time_line": original_move.time,
                            "user_operator_id": original_move.user_operator_id.id,
                        }
                    )

            # lineas_con_operario = transferencia.move_line_ids.filtered(lambda l: l.user_operator_id and l.is_done_item)
            # if not lineas_con_operario:
            #     transferencia.move_line_ids.filtered(lambda l: not l.user_operator_id and not l.is_done_item).unlink()

            # stock_move.sudo().write({"picked": True})

            return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/send_transfer/pick", auth="user", type="json", methods=["POST"], csrf=False)
    def send_transfer_pick(self, **auth):
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
                id_lote = item.get("id_lote", 0)
                id_operario = item.get("id_operario")
                fecha_transaccion = item.get("fecha_transaccion", "")
                time_line = int(item.get("time_line", 0))
                novedad = item.get("observacion", "")
                dividida = item.get("dividida", False)

                original_move = request.env["stock.move.line"].sudo().search([("id", "=", id_move)])
                if not original_move:
                    return {"code": 404, "msg": f"Movimiento no encontrado (ID: {id_move})"}

                stock_move = original_move.move_id

                move_parent = original_move.move_id
                product = request.env["product.product"].sudo().search([("id", "=", id_product)])

                if product.tracking == "lot" and not id_lote:
                    return {"code": 400, "msg": "El producto requiere lote y no se ha proporcionado uno"}

                fecha = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)

                noveda_minuscula = novedad.lower()
                if "pendiente" in noveda_minuscula or "pendientes" in noveda_minuscula:
                    dividida = False

                if dividida:
                    update_values = {
                        "quantity": cantidad_enviada,  # ← este es el bueno en Odoo 17
                        "location_dest_id": id_ubicacion_destino,
                        "location_id": original_move.location_id.id,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                    }

                    # Se toma la cantidad de la linea porque sabemos que esa es la que se puede usar o dividir porque es la que el sistema recerbo
                    cantidad_inicial = original_move.quantity

                    # Actualizamos los datos con lo que nos envian
                    original_move.sudo().write(update_values)
                    # Verificar si hay cantidad restante por enviar
                    # lineas_enviadas = request.env["stock.move.line"].sudo().search([("move_id", "=", move_parent.id), ("is_done_item", "=", True)])
                    # cantidad_total_enviada = sum(linea.quantity for linea in lineas_enviadas)
                    # cantidad_demandada = move_parent.product_uom_qty
                    # cantidad_restante = cantidad_demandada - cantidad_total_enviada

                    # sacamos la cantidad que necesitamos para crear una nueva linea, que seria la cantidad inicial menos la enviada entonces asi sabemos bajo que rango podemos dividir una cantidad basado lo recerbado del sistema
                    cantidad_restante = cantidad_inicial - cantidad_enviada

                    ### NOTA
                    # Entonces se dividira por linea, la lineas se peude dividir n veces segun la cantidad que haya tenido inciialemnte que seria lo que el sistema recerbo

                    # return {"code": 200, "msg": cantidad_restante, "cantidad_total_enviada": cantidad_total_enviada, "cantidad_demandada": cantidad_demandada}
                    if cantidad_restante > 0:
                        linea_restante = (
                            request.env["stock.move.line"]
                            .sudo()
                            .create(
                                {
                                    "move_id": move_parent.id,
                                    "product_id": id_product,
                                    "product_uom_id": original_move.product_uom_id.id,
                                    "location_id": original_move.location_id.id,
                                    "location_dest_id": id_ubicacion_destino,
                                    "quantity": cantidad_restante,
                                    "lot_id": id_lote if id_lote else False,
                                    "is_done_item": False,
                                    "date_transaction": False,
                                    "new_observation": "Cantidad pendiente por enviar",
                                    "time": 0,
                                    "user_operator_id": False,
                                    "picking_id": id_transferencia,
                                }
                            )
                        )

                        array_result.append(
                            {
                                "id_move": linea_restante.id,
                                "id_transferencia": id_transferencia,
                                "id_product": linea_restante.product_id.id,
                                "quantity": linea_restante.quantity,
                                "is_done_item": linea_restante.is_done_item,
                                "date_transaction": linea_restante.date_transaction,
                                "new_observation": linea_restante.new_observation,
                                "time_line": linea_restante.time,
                                "user_operator_id": None,
                            }
                        )

                else:
                    update_values = {
                        "quantity": cantidad_enviada,  # ← este es el bueno en Odoo 17
                        "location_dest_id": id_ubicacion_destino,
                        "location_id": original_move.location_id.id,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                    }

                    original_move.sudo().write(update_values)

                    array_result.append(
                        {
                            "id_move": original_move.id,
                            "id_transferencia": id_transferencia,
                            "id_product": original_move.product_id.id,
                            "quantity": original_move.quantity,
                            "is_done_item": original_move.is_done_item,
                            "date_transaction": original_move.date_transaction,
                            "new_observation": original_move.new_observation,
                            "time_line": original_move.time,
                            "user_operator_id": original_move.user_operator_id.id,
                        }
                    )

            # lineas_con_operario = transferencia.move_line_ids.filtered(lambda l: l.user_operator_id and l.is_done_item)
            # if not lineas_con_operario:
            #     transferencia.move_line_ids.filtered(lambda l: not l.user_operator_id and not l.is_done_item).unlink()

            # stock_move.sudo().write({"picked": True})

            return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/send_transfer/pack", auth="user", type="json", methods=["POST"], csrf=False)
    def send_transfer_pack(self, **auth):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = auth.get("id_transferencia", 0)
            is_sticker = auth.get("is_sticker", False)
            is_certificate = auth.get("is_certificate", False)
            peso_total_paquete = auth.get("peso_total_paquete", 0)
            list_items = auth.get("list_items", [])

            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)])
            if not transferencia:
                return {"code": 404, "msg": "Transferencia no encontrada"}

            # ✅ Crear el paquete manualmente
            pack = (
                request.env["stock.quant.package"]
                .sudo()
                .create(
                    {
                        "is_sticker": is_sticker,
                        "is_certificate": is_certificate,
                    }
                )
            )

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

                original_move = request.env["stock.move.line"].sudo().search([("id", "=", id_move)])
                if not original_move:
                    return {"code": 404, "msg": f"Movimiento no encontrado (ID: {id_move})"}

                stock_move = original_move.move_id

                move_parent = original_move.move_id
                product = request.env["product.product"].sudo().search([("id", "=", id_product)])

                if product.tracking == "lot" and not id_lote:
                    return {"code": 400, "msg": "El producto requiere lote y no se ha proporcionado uno"}

                fecha = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)

                noveda_minuscula = novedad.lower()
                if "pendiente" in noveda_minuscula or "pendientes" in noveda_minuscula:
                    dividida = False

                if dividida:
                    update_values = {
                        "quantity": cantidad_enviada,  # ← este es el bueno en Odoo 17
                        "location_dest_id": id_ubicacion_destino,
                        "location_id": id_ubicacion_origen,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                        "result_package_id": pack.id,
                    }

                    # Se toma la cantidad de la linea porque sabemos que esa es la que se puede usar o dividir porque es la que el sistema recerbo
                    cantidad_inicial = original_move.quantity

                    # Actualizamos los datos con lo que nos envian
                    original_move.sudo().write(update_values)

                    # sacamos la cantidad que necesitamos para crear una nueva linea, que seria la cantidad inicial menos la enviada entonces asi sabemos bajo que rango podemos dividir una cantidad basado lo recerbado del sistema
                    cantidad_restante = cantidad_inicial - cantidad_enviada

                    ### NOTA
                    # Entonces se dividira por linea, la lineas se peude dividir n veces segun la cantidad que haya tenido inciialemnte que seria lo que el sistema recerbo

                    # return {"code": 200, "msg": cantidad_restante, "cantidad_total_enviada": cantidad_total_enviada, "cantidad_demandada": cantidad_demandada}
                    if cantidad_restante > 0:
                        linea_restante = (
                            request.env["stock.move.line"]
                            .sudo()
                            .create(
                                {
                                    "move_id": move_parent.id,
                                    "product_id": id_product,
                                    "product_uom_id": original_move.product_uom_id.id,
                                    "location_id": id_ubicacion_origen,
                                    "location_dest_id": id_ubicacion_destino,
                                    "quantity": cantidad_restante,
                                    "lot_id": id_lote if id_lote else False,
                                    "is_done_item": False,
                                    "date_transaction": False,
                                    "new_observation": "",
                                    "time": 0,
                                    "user_operator_id": False,
                                    "picking_id": id_transferencia,
                                }
                            )
                        )

                        array_result.append(
                            {
                                "id_move": linea_restante.id,
                                "id_transferencia": id_transferencia,
                                "id_product": linea_restante.product_id.id,
                                "quantity": linea_restante.quantity,
                                "is_done_item": linea_restante.is_done_item,
                                "date_transaction": linea_restante.date_transaction,
                                "new_observation": linea_restante.new_observation,
                                "time_line": linea_restante.time,
                                "user_operator_id": None,
                            }
                        )

                else:
                    update_values = {
                        "quantity": cantidad_enviada,  # ← este es el bueno en Odoo 17
                        "location_dest_id": id_ubicacion_destino,
                        "location_id": id_ubicacion_origen,
                        "lot_id": id_lote if id_lote else False,
                        "is_done_item": True,
                        "date_transaction": fecha,
                        "new_observation": novedad,
                        "time": time_line,
                        "user_operator_id": id_operario,
                        "result_package_id": pack.id,
                    }

                    original_move.sudo().write(update_values)

                    array_result.append(
                        {
                            "id_paquete": pack.id,
                            "name_paquete": pack.name,
                            "id_batch": pack.id,
                            "cantidad_productos_en_el_paquete": len(list_items),
                            "is_sticker": is_sticker,
                            "is_certificate": is_certificate,
                            "peso": peso_total_paquete,
                            "list_item": list_items,
                            # "id_move": original_move.id,
                            # "id_transferencia": id_transferencia,
                            # "id_product": original_move.product_id.id,
                            # "quantity": original_move.quantity,
                            # "is_done_item": original_move.is_done_item,
                            # "date_transaction": original_move.date_transaction,
                            # "new_observation": original_move.new_observation,
                            # "time_line": original_move.time,
                            # "user_operator_id": original_move.user_operator_id.id,
                        }
                    )

            # lineas_con_operario = transferencia.move_line_ids.filtered(lambda l: l.user_operator_id and l.is_done_item)
            # if not lineas_con_operario:
            #     transferencia.move_line_ids.filtered(lambda l: not l.user_operator_id and not l.is_done_item).unlink()

            # stock_move.sudo().write({"picked": True})

            return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST PARA DESEMBOLSAR UN PAQUETE
    @http.route("/api/transferencias/unpacking", auth="user", type="json", methods=["POST"], csrf=False)
    def desembolsar_paquete(self, **auth):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = auth.get("id_transferencia", 0)
            id_paquete = auth.get("id_paquete")
            list_items = auth.get("list_items", [])

            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)])
            if not transferencia:
                return {"code": 404, "msg": "Transferencia no encontrada"}

            # ✅ Validar si el paquete existe
            paquete = request.env["stock.quant.package"].sudo().browse(id_paquete)
            if not paquete.exists():
                return {
                    "code": 400,
                    "msg": f"El paquete {id_paquete} no existe",
                }

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

                original_move = request.env["stock.move.line"].sudo().search([("id", "=", id_move)])
                if not original_move:
                    return {"code": 404, "msg": f"Movimiento no encontrado (ID: {id_move})"}

                if original_move.exists():
                    original_move.sudo().write(
                        {
                            "result_package_id": False,
                            "is_done_item": False,
                            "date_transaction": False,
                            "new_observation": novedad,
                            "time": 0,
                            "user_operator_id": id_operario,
                        }
                    )

                    array_result.append(
                        {
                            "id_paquete": paquete.id,
                            "name_paquete": paquete.name,
                            "id_batch": id_transferencia,
                            "cantidad_productos_en_el_paquete": len(list_items),
                            "list_item": list_items,
                            # "id_move": original_move.id,
                            # "id_transferencia": id_transferencia,
                            # "id_product": original_move.product_id.id,
                            # "quantity": original_move.quantity,
                            # "is_done_item": original_move.is_done_item,
                            # "date_transaction": original_move.date_transaction,
                            # "new_observation": original_move.new_observation,
                            # "time_line": original_move.time,
                            # "user_operator_id": original_move.user_operator_id.id,
                        }
                    )

                else:
                    return {"code": 404, "msg": f"Movimiento no encontrado (ID: {id_move})"}

            if not transferencia.move_line_ids.filtered(lambda l: l.result_package_id):
                # Si no hay más líneas de movimiento asociadas al paquete, eliminar el paquete
                paquete.unlink()
                array_result.append({"code": 200, "msg": f"Paquete {id_paquete} eliminado correctamente."})

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

            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)], limit=1)

            if not transferencia:
                return {"code": 400, "msg": f"Transferencia no encontrada o ya completada con ID {id_transferencia}"}

            # Eliminar las lineas que no se tiene is_done_item true
            lineas_no_enviadas = transferencia.move_line_ids.filtered(lambda l: not l.is_done_item)
            if lineas_no_enviadas:
                lineas_no_enviadas.unlink()

            # Intentar validar la Transferencia
            result = transferencia.sudo().button_validate()

            # Si el resultado es un diccionario, significa que se requiere acción adicional (un wizard)
            if isinstance(result, dict) and result.get("res_model"):
                wizard_model = result.get("res_model")

                # Para asistente de backorder
                if wizard_model == "stock.backorder.confirmation":
                    # Crear el wizard con los valores del contexto
                    wizard_context = result.get("context", {})

                    # Crear el asistente con los valores correctos según tu JSON
                    # En Odoo 17, la forma de enlazar registros sigue siendo la misma
                    wizard_vals = {"pick_ids": [(4, id_transferencia)], "show_transfers": wizard_context.get("default_show_transfers", False)}

                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)

                    # Procesar según la opción de crear_backorder
                    if crear_backorder:
                        # En Odoo 17, el método process sigue existiendo
                        wizard.sudo().process()
                        return {"code": 200, "msg": f"Transferencia procesada con backorder", "original_id": transferencia.id, "original_state": transferencia.state, "backorder_id": wizard.id if wizard else False}
                        # return {"code": 200, "msg": "Transferencia procesada con backorder", "original_id": transferencia.id, "original_state": transferencia.state, "backorder_id": backorder.id if backorder else False}
                    else:
                        # En Odoo 17, el método process_cancel_backorder sigue existiendo
                        wizard.sudo().process_cancel_backorder()
                        return {"code": 200, "msg": "Transferencia parcial completada sin crear backorder"}

                # Para asistente de transferencia inmediata
                elif wizard_model == "stock.immediate.transfer":
                    wizard_context = result.get("context", {})
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({"pick_ids": [(4, id_transferencia)]})

                    wizard.sudo().process()
                    return {"code": 200, "msg": "Transferencia procesada con transferencia inmediata"}

                else:
                    return {"code": 400, "msg": f"Se requiere un asistente no soportado: {wizard_model}"}

            # Si llegamos aquí, button_validate completó la validación sin necesidad de asistentes
            return {"code": 200, "msg": "Transferencia completada correctamente"}

        except Exception as e:
            # Registrar el error completo para depuración
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    # @http.route("/api/complete_transfer", auth="user", type="json", methods=["POST"], csrf=False)
    # def completar_transferencia(self, **auth):
    #     try:
    #         user = request.env.user
    #         # ✅ Validar usuario
    #         if not user:
    #             return {"code": 400, "msg": "Usuario no encontrado"}

    #         id_transferencia = auth.get("id_transferencia", 0)
    #         crear_backorder = auth.get("crear_backorder", True)

    #         # ✅ Buscar transferencia por ID
    #         transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia), ("picking_type_code", "=", "internal"), ("picking_type_id.sequence_code", "=", "INT"), ("state", "=", "assigned")], limit=1)

    #         if not transferencia:
    #             return {"code": 404, "msg": f"Transferencia no encontrada o ya completada con ID {id_transferencia}"}

    #         # Verificar si hay líneas de movimiento que validar
    #         if not transferencia.move_ids_without_package:
    #             return {"code": 400, "msg": "La transferencia no tiene líneas de movimiento"}

    #         # ✅ Intentar validar la transferencia
    #         result = transferencia.with_context(skip_backorder=not crear_backorder).sudo().button_validate()

    #         if isinstance(result, dict) and result.get("res_model"):
    #             wizard_model = result.get("res_model")
    #             wizard_context = result.get("context", {})

    #             # 🟨 1. Backorder Wizard
    #             if wizard_model == "stock.backorder.confirmation":
    #                 wizard_vals = {"pick_ids": [(6, 0, [transferencia.id])], "show_transfers": wizard_context.get("default_show_transfers", False)}
    #                 wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)

    #                 transferencia.sudo()._action_done()

    #                 # Verificar si se creó una backorder
    #                 backorder = request.env["stock.picking"].sudo().search([("backorder_id", "=", transferencia.id), ("state", "not in", ["done", "cancel"])], limit=1)

    #                 return {"code": 200, "msg": "Transferencia procesada con backorder", "original_id": transferencia.id, "original_state": transferencia.state, "backorder_id": backorder.id if backorder else False}

    #             # 🟨 2. Transferencia inmediata
    #             elif wizard_model == "stock.immediate.transfer":
    #                 wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({})
    #                 transferencia.sudo()._action_done()

    #                 return {"code": 200, "msg": "Transferencia completada con éxito", "original_id": transferencia.id, "original_state": transferencia.state}

    #             # 🟨 3. Confirmación por caducidad
    #             elif wizard_model == "expiry.picking.confirmation":
    #                 wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({})
    #                 wizard.sudo().process()

    #                 return {"code": 200, "msg": "Transferencia completada con confirmación de caducidad", "original_id": transferencia.id, "original_state": transferencia.state}

    #             # 🚫 Otro wizard no soportado
    #             else:
    #                 return {"code": 400, "msg": f"Acción adicional requerida no soportada: {wizard_model}"}

    #         elif isinstance(result, bool) and result:
    #             # ✅ Transferencia completada directamente sin wizard
    #             return {"code": 200, "msg": "Transferencia completada directamente", "original_id": transferencia.id, "original_state": transferencia.state}
    #         else:
    #             return {"code": 400, "msg": f"No se pudo completar la transferencia: {result}"}

    #     except Exception as e:
    #         return {"code": 500, "msg": f"Error interno: {str(e)}"}

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

            try:
                picking.action_assign()
            except Exception as e:
                return {"code": 500, "msg": f"Error al comprobar disponibilidad: {str(e)}"}

            movimientos_operaciones = picking.move_line_ids
            movimientos_enviados = picking.move_line_ids

            transferencia_info = {
                "id": picking.id,
                "name": picking.name,
                "fecha_creacion": picking.create_date,
                "location_id": picking.location_id.id,
                "location_name": picking.location_id.display_name,
                "location_barcode": picking.location_id.barcode or "",
                "location_dest_id": picking.location_dest_id.id,
                "location_dest_name": picking.location_dest_id.display_name,
                "location_dest_barcode": picking.location_dest_id.barcode or "",
                "numero_transferencia": picking.name,
                "proveedor_id": picking.partner_id.id or 0,
                "peso_total": 0,
                "numero_lineas": 0,
                "numero_items": sum(move.product_uom_qty for move in picking.move_ids),
                "state": picking.state,
                "origin": picking.origin or "",
                "priority": picking.priority,
                "warehouse_id": picking.picking_type_id.warehouse_id.id,
                "warehouse_name": picking.picking_type_id.warehouse_id.name,
                "responsable_id": picking.responsable_id.id or 0,
                "responsable": picking.responsable_id.name or "",
                "picking_type": picking.picking_type_id.name,
                "start_time_transfer": picking.start_time_transfer or "",
                "end_time_transfer": picking.end_time_transfer or "",
                "backorder_id": picking.backorder_id.id or 0,
                "backorder_name": picking.backorder_id.name or "",
                "show_check_availability": picking.show_check_availability,
                "lineas_transferencia": [],
                "lineas_transferencia_enviadas": [],
            }

            for move in movimientos_operaciones:
                product = move.product_id
                quantity_done = move.quantity or 0
                quantity_ordered = move.move_id.product_uom_qty or 0

                cantidad_faltante = quantity_ordered - quantity_done

                if quantity_done == 0:
                    continue

                if not move.is_done_item:
                    linea_info = {
                        "id": move.move_id.id if move.move_id else 0,
                        "id_move": move.id,
                        "id_transferencia": picking.id,
                        "product_id": product.id,
                        "product_name": product.name,
                        "product_code": product.default_code or "",
                        "product_barcode": product.barcode or "",
                        "product_tracking": product.tracking or "",
                        "dias_vencimiento": product.expiration_time or "",
                        "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                        "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                        "quantity_ordered": quantity_ordered,
                        "quantity_to_transfer": quantity_ordered,
                        "cantidad_faltante": quantity_ordered,
                        "uom": move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND",
                        "location_dest_id": move.location_dest_id.id or 0,
                        "location_dest_name": move.location_dest_id.display_name or "",
                        "location_dest_barcode": move.location_dest_id.barcode or "",
                        "location_id": move.location_id.id or 0,
                        "location_name": move.location_id.display_name or "",
                        "location_barcode": move.location_id.barcode or "",
                        "weight": product.weight or 0,
                        "is_done_item": False,
                        "date_transaction": "",
                        "observation": "",
                        "time": 0,
                        "user_operator_id": 0,
                    }

                    if move.lot_id:
                        linea_info.update(
                            {
                                "lot_id": move.lot_id.id,
                                "lot_name": move.lot_id.name,
                                "fecha_vencimiento": move.lot_id.expiration_date or "",
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

                    transferencia_info["lineas_transferencia"].append(linea_info)

            for move_line in movimientos_enviados:
                if not move_line.is_done_item:
                    continue

                product = move_line.product_id

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
                    "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
                    "product_packing": [{"barcode": p.barcode, "cantidad": p.qty, "id_product": p.product_id.id, "id_move": move_line.id, "batch_id": picking.id} for p in getattr(product, "packaging_ids", [])],
                    "quantity_ordered": move_line.move_id.product_uom_qty,
                    "quantity_to_transfer": move_line.move_id.product_uom_qty,
                    "quantity_done": move_line.quantity,
                    "cantidad_faltante": cantidad_faltante,
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
                    "observation": move_line.new_observation or "",
                    "time": move_line.time or 0,
                    "user_operator_id": move_line.user_operator_id.id if move_line.user_operator_id else 0,
                }

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

                transferencia_info["lineas_transferencia_enviadas"].append(linea_info)

            transferencia_info["numero_lineas"] = len(transferencia_info["lineas_transferencia"])
            # transferencia_info["numero_items"] = sum(l["quantity_to_transfer"] for l in transferencia_info["lineas_transferencia"])

            return {"code": 200, "msg": "Disponibilidad comprobada correctamente", "result": transferencia_info}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## GET INFORMACION RAPIDA POR CÓDIGO DE BARRAS
    @http.route("/api/transferencias/quickinfo", auth="user", type="json", methods=["GET"])
    def get_quick_info(self, **kwargs):
        try:

            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            barcode = kwargs.get("barcode")
            if not barcode:
                return {"code": 400, "msg": "Código de barras no proporcionado"}

            # Buscar PRODUCTO por barcode directo
            product = request.env["product.product"].sudo().search([("barcode", "=", barcode)], limit=1)

            # Buscar PRODUCTO por paquete
            if not product:
                packaging = request.env["product.packaging"].sudo().search([("barcode", "=", barcode)], limit=1)
                if packaging:
                    product = packaging.product_id

            # Buscar PRODUCTO por lote
            if not product:
                lot = request.env["stock.lot"].sudo().search([("name", "=", barcode)], limit=1)
                if lot:
                    product = lot.product_id

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # PRODUCTO encontrado
            if product:
                # CAMBIO PRINCIPAL: Buscar quants considerando TODOS los almacenes permitidos
                quants = request.env["stock.quant"].sudo().search([("product_id", "=", product.id), ("quantity", ">", 0), ("location_id.usage", "=", "internal"), ("location_id.warehouse_id", "in", allowed_warehouses.ids)])

                ubicaciones = []
                for quant in quants:
                    # Verificar que el almacén esté en los permitidos
                    warehouse = request.env["stock.warehouse"].sudo().search([("id", "=", quant.location_id.warehouse_id.id), ("id", "in", allowed_warehouses.ids)], limit=1)

                    if not warehouse:
                        continue  # Saltar si no pertenece a un almacén del usuario

                    if quant.inventory_quantity_auto_apply > 0:
                        ubicaciones.append(
                            {
                                "id_move": quant.id,
                                "id_almacen": warehouse.id,
                                "nombre_almacen": warehouse.name,
                                "id_ubicacion": quant.location_id.id,
                                "ubicacion": quant.location_id.complete_name or "",
                                "cantidad": quant.inventory_quantity_auto_apply or 0,
                                "reservado": quant.reserved_quantity or 0,
                                "cantidad_mano": quant.quantity - quant.reserved_quantity,
                                "codigo_barras": quant.location_id.barcode or "",
                                "lote": quant.lot_id.name if quant.lot_id else "",
                                "lote_id": quant.lot_id.id if quant.lot_id else 0,
                                "fecha_eliminacion": quant.removal_date or "",
                                "fecha_entrada": quant.in_date or "",
                            }
                        )

                paquetes = product.packaging_ids.mapped("barcode")

                return {
                    "code": 200,
                    "type": "product",
                    "result": {
                        "id": product.id,
                        "nombre": product.display_name,
                        "precio": product.lst_price,
                        "cantidad_disponible": product.qty_available,
                        "previsto": product.virtual_available,
                        "referencia": product.default_code,
                        "peso": product.weight,
                        "volumen": product.volume,
                        "codigo_barras": product.barcode,
                        "codigos_barras_paquetes": paquetes,
                        "imagen": product.image_128 and f"/web/image/product.product/{product.id}/image_128" or "",
                        "categoria": product.categ_id.name,
                        "ubicaciones": ubicaciones,
                    },
                }

            # Buscar UBICACIÓN por código de barras
            location = request.env["stock.location"].sudo().search([("barcode", "=", barcode), ("usage", "=", "internal")], limit=1)  # Solo internas

            if location:
                quants = request.env["stock.quant"].sudo().search([("location_id", "=", location.id), ("quantity", ">", 0)])

                productos_dict = {}
                for quant in quants:
                    prod = quant.product_id
                    if prod.id not in productos_dict:
                        productos_dict[prod.id] = {
                            "id": prod.id,
                            "producto": prod.display_name,
                            "cantidad": 0.0,
                            "codigo_barras": prod.barcode,
                            "lote": quant.lot_id.name if quant.lot_id else "",
                            "lote_id": quant.lot_id.id if quant.lot_id else 0,
                            "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                            "nombre_almacen": location.warehouse_id.name if location.warehouse_id else "",
                        }
                    productos_dict[prod.id]["cantidad"] += quant.available_quantity

                productos = list(productos_dict.values())

                return {
                    "code": 200,
                    "type": "ubicacion",
                    "result": {
                        "id": location.id,
                        "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                        "nombre_almacen": location.warehouse_id.name if location.warehouse_id else "",
                        "nombre": location.name,
                        "ubicacion_padre": location.location_id.name if location.location_id else "",
                        "tipo_ubicacion": location.usage,
                        "codigo_barras": location.barcode,
                        "productos": productos,
                    },
                }

            return {"code": 404, "msg": "No se encontró producto, lote, paquete ni ubicación con ese código de barras"}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## GET INFORMACION RAPIDA POR ID
    @http.route("/api/transferencias/quickinfo/id", auth="user", type="json", methods=["GET"])
    def get_quick_info_by_id(self, **kwargs):
        try:

            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_product = kwargs.get("id_product")
            id_location = kwargs.get("id_location")

            if id_product:
                # Buscar por el id del producto
                product = request.env["product.product"].sudo().search([("id", "=", id_product)], limit=1)

                if not product:
                    return {"code": 404, "msg": "Producto no encontrado"}

                # Obtener almacenes del usuario
                allowed_warehouses = obtener_almacenes_usuario(user)

                # Verificar si es un error (diccionario con código y mensaje)
                if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                    return allowed_warehouses  # Devolver el error directamente

                # PRODUCTO encontrado
                if product:
                    # CAMBIO PRINCIPAL: Buscar quants considerando TODOS los almacenes permitidos
                    quants = request.env["stock.quant"].sudo().search([("product_id", "=", product.id), ("quantity", ">", 0), ("location_id.usage", "=", "internal"), ("location_id.warehouse_id", "in", allowed_warehouses.ids)])

                    ubicaciones = []
                    for quant in quants:
                        # Verificar que el almacén esté en los permitidos
                        warehouse = request.env["stock.warehouse"].sudo().search([("id", "=", quant.location_id.warehouse_id.id), ("id", "in", allowed_warehouses.ids)], limit=1)

                        if not warehouse:
                            continue  # Saltar si no pertenece a un almacén del usuario

                        if quant.inventory_quantity_auto_apply > 0:
                            ubicaciones.append(
                                {
                                    "id_move": quant.id,
                                    "id_almacen": warehouse.id,
                                    "nombre_almacen": warehouse.name,
                                    "id_ubicacion": quant.location_id.id,
                                    "ubicacion": quant.location_id.complete_name or "",
                                    "cantidad": quant.inventory_quantity_auto_apply or 0,
                                    "reservado": quant.reserved_quantity or 0,
                                    "cantidad_mano": quant.quantity - quant.reserved_quantity,
                                    "codigo_barras": quant.location_id.barcode or "",
                                    "lote": quant.lot_id.name if quant.lot_id else "",
                                    "lote_id": quant.lot_id.id if quant.lot_id else 0,
                                    "fecha_eliminacion": quant.removal_date or "",
                                    "fecha_entrada": quant.in_date or "",
                                }
                            )

                    paquetes = product.packaging_ids.mapped("barcode")

                    return {
                        "code": 200,
                        "type": "product",
                        "result": {
                            "id": product.id,
                            "nombre": product.display_name,
                            "precio": product.lst_price,
                            "cantidad_disponible": product.qty_available,
                            "previsto": product.virtual_available,
                            "referencia": product.default_code,
                            "peso": product.weight,
                            "volumen": product.volume,
                            "codigo_barras": product.barcode,
                            "codigos_barras_paquetes": paquetes,
                            "imagen": product.image_128 and f"/web/image/product.product/{product.id}/image_128" or "",
                            "categoria": product.categ_id.name,
                            "ubicaciones": ubicaciones,
                        },
                    }

            if id_location:
                # Buscar UBICACIÓN por ID
                location = request.env["stock.location"].sudo().search([("id", "=", id_location), ("usage", "=", "internal")], limit=1)  # Solo internas

                if location:
                    quants = request.env["stock.quant"].sudo().search([("location_id", "=", location.id), ("quantity", ">", 0)])

                    productos_dict = {}
                    for quant in quants:
                        prod = quant.product_id
                        if prod.id not in productos_dict:
                            productos_dict[prod.id] = {
                                "id": prod.id,
                                "producto": prod.display_name,
                                "cantidad": 0.0,
                                "codigo_barras": prod.barcode,
                                "lote": quant.lot_id.name if quant.lot_id else "",
                                "lote_id": quant.lot_id.id if quant.lot_id else 0,
                                "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                                "nombre_almacen": location.warehouse_id.name if location.warehouse_id else "",
                            }
                        productos_dict[prod.id]["cantidad"] += quant.available_quantity

                    productos = list(productos_dict.values())

                    return {
                        "code": 200,
                        "type": "ubicacion",
                        "result": {
                            "id": location.id,
                            "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                            "nombre_almacen": location.warehouse_id.name if location.warehouse_id else "",
                            "nombre": location.name,
                            "ubicacion_padre": location.location_id.name if location.location_id else "",
                            "tipo_ubicacion": location.usage,
                            "codigo_barras": location.barcode,
                            "productos": productos,
                        },
                    }

            return {"code": 404, "msg": "No se encontró producto, lote, paquete ni ubicación con ese código de barras"}

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST CREAR TRANSFERENCIA MANUAL
    @http.route("/api/crear_transferencia", auth="user", type="json", methods=["POST"], csrf=False)
    def crear_transferencia(self, **auth):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # Parámetros
            id_almacen = auth.get("id_almacen", 0)
            id_ubicacion_destino = auth.get("id_ubicacion_destino", 0)
            id_ubicacion_origen = auth.get("id_ubicacion_origen", 0)
            id_responsable = auth.get("id_operario", 0)
            id_producto = auth.get("id_producto", 0)
            cantidad_enviada = float(auth.get("cantidad_enviada", 0))
            id_lote = auth.get("id_lote") or False
            fecha_transaccion = auth.get("fecha_transaccion", "")
            observacion = auth.get("observacion", "")
            time_line = auth.get("time_line", 0)

            # Validaciones
            if not (id_almacen and id_ubicacion_destino and id_ubicacion_origen):
                return {"code": 400, "msg": "Faltan parámetros de ubicación"}

            if not id_producto or cantidad_enviada <= 0:
                return {"code": 400, "msg": "Cantidad o producto inválido"}

            product = request.env["product.product"].sudo().browse(id_producto)
            if not product.exists():
                return {"code": 404, "msg": "Producto no encontrado"}

            available_stock = product.with_context(location=id_ubicacion_origen).qty_available
            if available_stock < cantidad_enviada:
                return {"code": 400, "msg": f"Stock insuficiente en origen. Disponible: {available_stock}"}

            # Buscar tipo de picking interno
            picking_type = request.env["stock.picking.type"].sudo().search([("warehouse_id", "=", id_almacen), ("code", "=", "internal")], limit=1)

            if not picking_type:
                return {"code": 404, "msg": "Tipo de picking interno no encontrado"}

            # Crear Picking
            picking = (
                request.env["stock.picking"]
                .sudo()
                .create(
                    {
                        "picking_type_id": picking_type.id,
                        "location_id": id_ubicacion_origen,
                        "location_dest_id": id_ubicacion_destino,
                        "user_id": id_responsable,
                        "responsable_id": id_responsable,
                        "origin": f"Transferencia creada por {user.name}",
                    }
                )
            )

            # Crear movimiento
            move = (
                request.env["stock.move"]
                .sudo()
                .create(
                    {
                        "name": product.display_name,
                        "product_id": product.id,
                        "product_uom_qty": cantidad_enviada,
                        "product_uom": product.uom_id.id,
                        "location_id": id_ubicacion_origen,
                        "location_dest_id": id_ubicacion_destino,
                        "picking_id": picking.id,
                    }
                )
            )

            # Confirmar y asignar
            picking.action_confirm()
            picking.action_assign()

            move_line = move.move_line_ids and move.move_line_ids[0] or False

            if move_line:
                move_line_vals = {
                    "user_operator_id": id_responsable or user.id,
                    "new_observation": observacion,
                    "is_done_item": True,
                    "time": time_line,
                    "date_transaction": procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc),
                    "quantity": cantidad_enviada,
                    "location_id": id_ubicacion_origen,
                    "location_dest_id": id_ubicacion_destino,
                }

                if id_lote:
                    move_line_vals["lot_id"] = id_lote

                move_line.sudo().write(move_line_vals)

            # Validar
            try:
                picking.button_validate()
            except Exception as e:
                return {"code": 400, "msg": f"Error en validación del picking: {str(e)}"}

            return {
                "code": 200,
                "msg": "Transferencia creada y validada correctamente",
                "transferencia_id": picking.id,
                "nombre_transferencia": picking.name,
                "linea_id": move_line.id if move_line else 0,
                "cantidad_enviada": move_line.quantity if move_line else cantidad_enviada,
                "id_producto": product.id,
                "nombre_producto": product.display_name,
                "ubicacion_origen": move_line.location_id.name if move_line else "",
                "ubicacion_destino": move_line.location_dest_id.name if move_line else "",
                "fecha_transaccion": move_line.date_transaction if hasattr(move_line, "date_transaction") else "",
                "observacion": move_line.new_observation if hasattr(move_line, "new_observation") else "",
                "time_line": move_line.time if hasattr(move_line, "time") else 0,
                "user_operator_id": move_line.user_operator_id.id if hasattr(move_line, "user_operator_id") else 0,
                "user_operator_name": move_line.user_operator_id.name if hasattr(move_line, "user_operator_id") else "",
                "id_lote": move_line.lot_id.id if move_line and move_line.lot_id else 0,
                "available_stock": available_stock,
                "cantidad_disponible": product.qty_available,
                "ubicacion_origen_id": id_ubicacion_origen,
                "ubicacion_destino_id": id_ubicacion_destino,
            }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 500, "msg": f"Error inesperado: {str(err)}"}


## FUNCIONES AUXILIARES
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
