from odoo import http
from odoo.http import request
from odoo.exceptions import AccessError
from datetime import datetime, timedelta
import pytz


class TransaccionProduccionController(http.Controller):
    @http.route("/api/picking/componentes", auth="user", type="json", methods=["GET"])
    def get_componentes(self):
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
                            ("picking_type_id.sequence_code", "in", ["PC"]),
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

    @http.route("/api/transferencias/producto_terminado", auth="user", type="json", methods=["GET"])
    def get_productos_terminados(self):
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
                            ("picking_type_id.sequence_code", "in", ["SFP"]),
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
