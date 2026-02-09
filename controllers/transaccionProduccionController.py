from datetime import datetime, timedelta

import pytz
from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

from .utils import get_barcodes, get_packagings


class TransaccionProduccionController(http.Controller):
    def get_last_version(self):
        try:
            # Obtener la última versión
            last_version = request.env["app.version"].sudo().search([], order="id desc", limit=1)

            if not last_version:
                return {"code": 404, "msg": "No se encontró ninguna versión"}

            # Convertir el texto JSON a una lista Python
            notes_list = []
            if last_version.notes:
                try:
                    notes_list = json.loads(last_version.notes)
                except:
                    notes_list = ["Error al procesar las notas"]

            return {
                "code": 200,
                "result": {
                    "id": last_version.id,
                    "version": last_version.version,
                    "release_date": str(last_version.release_date),
                    "notes": notes_list,  # Ahora devuelve la lista en lugar del string JSON
                    "url_download": last_version.url_download,
                },
            }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/picking/componentes", auth="user", type="json", methods=["GET"])
    def get_componentes(self, **kwargs):
        try:
            version_app = kwargs.get("version_app")

            # 1. Llama al otro método DENTRO de esta misma clase
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user

            if not user:
                return {"code": 400, "update_version": update_required, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

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
                    # movimientos_operaciones = picking.move_line_ids
                    # movimientos_enviados = picking.move_line_ids

                    movimientos_operaciones = picking.move_line_ids.filtered(
                        lambda ml: ml.product_id.type == "product"
                    )
                    movimientos_enviados = picking.move_line_ids.filtered(
                        lambda ml: ml.product_id.type == "product"
                    )

                    if not movimientos_operaciones:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

                    producto_final_nombre = ""
                    producto_final_referencia = ""
                    orden_manufactura = ""
                    cantidad_a_producir = 0

                    # Buscar la orden de manufactura
                    mo = request.env["mrp.production"].sudo().search([("name", "=", picking.origin)], limit=1)

                    if mo:
                        producto_final = mo.product_id
                        producto_final_nombre = producto_final.display_name
                        producto_final_referencia = producto_final.default_code or ""
                        orden_manufactura = mo.name
                        cantidad_a_producir = mo.product_qty

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
                        "create_backorder": create_backorder,
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
                        "producto_final_nombre": producto_final_nombre,
                        "producto_final_referencia": producto_final_referencia,
                        "orden_manufactura": orden_manufactura,
                        "cantidad_a_producir": cantidad_a_producir,
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
                                "product_id": [product.id, product.display_name],  # ✅
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                # "other_barcodes": [
                                #     {"barcode": b.name} for b in getattr(product, "barcode_ids", [])
                                # ],
                                "other_barcodes": get_barcodes(product, picking.id, move.id),
                                "product_packing": [
                                    {
                                        "barcode": p.barcode,
                                        "cantidad": p.qty,
                                        "id_product": p.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for p in getattr(product, "packaging_ids", [])
                                ],
                                "quantity": quantity_done,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "unidades": (
                                    move.move_id.product_uom.name
                                    if move.move_id and move.move_id.product_uom
                                    else "UND"
                                ),
                                "location_dest_id": [
                                    move.location_dest_id.id,
                                    move.location_dest_id.display_name,
                                ],  # ✅
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "barcode_location_dest": move.location_dest_id.barcode or "",
                                "location_id": [move.location_id.id, move.location_id.display_name],  # ✅
                                "location_name": move.location_id.display_name or "",
                                "barcode_location": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "rimoval_priority": location.priority_picking_desplay,
                                "zona_entrega": picking.delivery_zone_id.display_name,
                                "other_barcode": get_barcodes(product, picking.id, move.id),
                                "product_packing": array_packing,
                                "pedido": picking.name,
                                "pedido_id": picking.id,
                                "origin": picking.origin or "",
                                "lote_id": move.lot_id.id or 0,
                                "lote": move.lot_id.name or "",
                                "is_done_item": False,
                                "date_transaction": "",
                                "observation": "",
                                "time": 0,
                                "user_operator_id": 0,
                                "expire_date": move.lot_id.expiration_date or "",
                                "is_separate": 0,
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
                            "product_id": [product.id, product.display_name],  # ✅
                            "product_name": product.display_name,
                            "product_code": product.default_code or "",
                            "barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": get_barcodes(product, picking.id, move_line.id),
                            "product_packing": [
                                {
                                    "barcode": p.barcode,
                                    "cantidad": p.qty,
                                    "id_product": p.product_id.id,
                                    "id_move": move_line.id,
                                    "batch_id": picking.id,
                                }
                                for p in getattr(product, "packaging_ids", [])
                            ],
                            "quantity": quantity_ordered,
                            "quantity_to_transfer": quantity_ordered,
                            "quantity_done": move_line.quantity,
                            "cantidad_faltante": quantity_ordered,
                            "quantity_separate": move_line.quantity,
                            "unidades": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                            "location_dest_id": [
                                move_line.location_dest_id.id,
                                move_line.location_dest_id.display_name,
                            ],  # ✅
                            "location_dest_name": move_line.location_dest_id.display_name or "",
                            "barcode_location_dest": move_line.location_dest_id.barcode or "",
                            "location_id": [
                                move_line.location_id.id,
                                move_line.location_id.display_name,
                            ],  # ✅
                            "location_name": move_line.location_id.display_name or "",
                            "barcode_location": move_line.location_id.barcode or "",
                            "weight": product.weight or 0,
                            "rimoval_priority": location.priority_picking_desplay,
                            "zona_entrega": picking.delivery_zone_id.display_name,
                            "other_barcode": get_barcodes(product, picking.id, move_line.id),
                            "product_packing": array_packing,
                            "pedido": picking.name,
                            "pedido_id": picking.id,
                            "origin": picking.origin or "",
                            "lote_id": move_line.lot_id.id or "",
                            "lote": move_line.lot_id.name or "",
                            "is_separate": 1,
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

            return {"code": 200, "update_version": update_required, "result": array_transferencias}

        except AccessError as e:
            return {"code": 403, "update_version": update_required, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "update_version": update_required, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/picking/componentes/v2", auth="user", type="json", methods=["GET"])
    def get_componentes_v2(self, **kwargs):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

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

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

                    producto_final_nombre = ""
                    producto_final_referencia = ""
                    orden_manufactura = ""
                    cantidad_a_producir = 0

                    # Buscar la orden de manufactura
                    mo = request.env["mrp.production"].sudo().search([("name", "=", picking.origin)], limit=1)

                    if mo:
                        producto_final = mo.product_id
                        producto_final_nombre = producto_final.display_name
                        producto_final_referencia = producto_final.default_code or ""
                        orden_manufactura = mo.name
                        cantidad_a_producir = mo.product_qty

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
                        "create_backorder": create_backorder,
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
                        "producto_final_nombre": producto_final_nombre,
                        "producto_final_referencia": producto_final_referencia,
                        "orden_manufactura": orden_manufactura,
                        "cantidad_a_producir": cantidad_a_producir,
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
                                "product_id": [product.id, product.display_name],  # ✅
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                "other_barcodes": get_barcodes(product, picking.id, move.id),
                                "product_packing": [
                                    {
                                        "barcode": p.barcode,
                                        "cantidad": p.qty,
                                        "id_product": p.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for p in getattr(product, "packaging_ids", [])
                                ],
                                "quantity": quantity_done,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "unidades": (
                                    move.move_id.product_uom.name
                                    if move.move_id and move.move_id.product_uom
                                    else "UND"
                                ),
                                "location_dest_id": [
                                    move.location_dest_id.id,
                                    move.location_dest_id.display_name,
                                ],  # ✅
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "barcode_location_dest": move.location_dest_id.barcode or "",
                                "location_id": [move.location_id.id, move.location_id.display_name],  # ✅
                                "location_name": move.location_id.display_name or "",
                                "barcode_location": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "rimoval_priority": location.priority_picking_desplay,
                                "zona_entrega": picking.delivery_zone_id.display_name,
                                "other_barcode": get_barcodes(product, picking.id, move.id),
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
                            "product_id": [product.id, product.display_name],  # ✅
                            "product_name": product.display_name,
                            "product_code": product.default_code or "",
                            "barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": get_barcodes(product, picking.id, move_line.id),
                            "product_packing": [
                                {
                                    "barcode": p.barcode,
                                    "cantidad": p.qty,
                                    "id_product": p.product_id.id,
                                    "id_move": move_line.id,
                                    "batch_id": picking.id,
                                }
                                for p in getattr(product, "packaging_ids", [])
                            ],
                            "quantity": quantity_ordered,
                            "quantity_to_transfer": quantity_ordered,
                            "quantity_done": move_line.quantity,
                            "cantidad_faltante": quantity_ordered,
                            "unidades": move_line.product_uom_id.name if move_line.product_uom_id else "UND",
                            "location_dest_id": [
                                move_line.location_dest_id.id,
                                move_line.location_dest_id.display_name,
                            ],  # ✅
                            "location_dest_name": move_line.location_dest_id.display_name or "",
                            "barcode_location_dest": move_line.location_dest_id.barcode or "",
                            "location_id": [
                                move_line.location_id.id,
                                move_line.location_id.display_name,
                            ],  # ✅
                            "location_name": move_line.location_id.display_name or "",
                            "barcode_location": move_line.location_id.barcode or "",
                            "weight": product.weight or 0,
                            "rimoval_priority": location.priority_picking_desplay,
                            "zona_entrega": picking.delivery_zone_id.display_name,
                            "other_barcode": get_barcodes(product, picking.id, move_line.id),
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
    def get_productos_terminados(self, **kwargs):
        try:
            version_app = kwargs.get("version_app")

            # 1. Llama al otro método DENTRO de esta misma clase
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user

            if not user:
                return {"code": 400, "update_version": update_required, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")

            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

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

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

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
                        "create_backorder": create_backorder,
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
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                "other_barcodes": get_barcodes(product, picking.id, move.id),
                                "product_packing": [
                                    {
                                        "barcode": p.barcode,
                                        "cantidad": p.qty,
                                        "id_product": p.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for p in getattr(product, "packaging_ids", [])
                                ],
                                "quantity_ordered": quantity_ordered,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (
                                    move.move_id.product_uom.name
                                    if move.move_id and move.move_id.product_uom
                                    else "UND"
                                ),
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
                            "product_name": product.display_name,
                            "product_code": product.default_code or "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": get_barcodes(product, picking.id, move_line.id),
                            "product_packing": [
                                {
                                    "barcode": p.barcode,
                                    "cantidad": p.qty,
                                    "id_product": p.product_id.id,
                                    "id_move": move_line.id,
                                    "batch_id": picking.id,
                                }
                                for p in getattr(product, "packaging_ids", [])
                            ],
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
                            "user_operator_id": (
                                move_line.user_operator_id.id if move_line.user_operator_id else 0
                            ),
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

            return {"code": 200, "update_version": update_required, "result": array_transferencias}

        except AccessError as e:
            return {"code": 403, "update_version": update_required, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "update_version": update_required, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/transferencias/producto_terminado/v2", auth="user", type="json", methods=["GET"])
    def get_productos_terminados_v2(self, **kwargs):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")

            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

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

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = (
                        picking.picking_type_id.create_backorder
                        if hasattr(picking.picking_type_id, "create_backorder")
                        else False
                    )

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
                        "create_backorder": create_backorder,
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
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "product_barcode": product.barcode or "",
                                "product_tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                "other_barcodes": get_barcodes(product, picking.id, move.id),
                                "product_packing": [
                                    {
                                        "barcode": p.barcode,
                                        "cantidad": p.qty,
                                        "id_product": p.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for p in getattr(product, "packaging_ids", [])
                                ],
                                "quantity_ordered": quantity_ordered,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (
                                    move.move_id.product_uom.name
                                    if move.move_id and move.move_id.product_uom
                                    else "UND"
                                ),
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
                            "product_name": product.display_name,
                            "product_code": product.default_code or "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": get_barcodes(product, picking.id, move_line.id),
                            "product_packing": [
                                {
                                    "barcode": p.barcode,
                                    "cantidad": p.qty,
                                    "id_product": p.product_id.id,
                                    "id_move": move_line.id,
                                    "batch_id": picking.id,
                                }
                                for p in getattr(product, "packaging_ids", [])
                            ],
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
                            "user_operator_id": (
                                move_line.user_operator_id.id if move_line.user_operator_id else 0
                            ),
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

    ## GET Obtener todos los picking de componentes realizados en una fecha dada
    @http.route("/api/picking/componentes/history", auth="user", type="json", methods=["GET"])
    def get_history_picking(self, **kwargs):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            fecha_picking = kwargs.get("fecha_picking", datetime.now().strftime("%Y-%m-%d"))

            if not fecha_picking:
                return {"code": 400, "msg": "Se requiere la fecha fecha_picking"}

            date_from = datetime.strptime(fecha_picking + " 00:00:00", "%Y-%m-%d %H:%M:%S")
            date_to = datetime.strptime(fecha_picking + " 23:59:59", "%Y-%m-%d %H:%M:%S")

            allowed_warehouses = obtener_almacenes_usuario(user)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            transferencias = (
                request.env["stock.picking"]
                .sudo()
                .search(
                    [
                        ("picking_type_id.warehouse_id", "in", [wh.id for wh in allowed_warehouses]),
                        ("write_date", ">=", date_from),
                        ("write_date", "<=", date_to),
                        ("picking_type_id.sequence_code", "in", ["PC"]),
                        ("batch_id", "=", False),
                        ("picking_type_code", "=", "internal"),
                        ("state", "!=", "cancel"),
                        ("responsable_id", "in", [user.id]),
                    ],
                    order="write_date desc",  # Cambiar el orden también
                )
            )

            array_result = []
            for picking in transferencias:
                transferencia_info = {
                    "batch_id": picking.id,
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
                    "numero_lineas": len(picking.move_line_ids),
                    "numero_items": sum(move.product_uom_qty for move in picking.move_ids),
                    "state": picking.state,
                    "referencia": picking.origin or "",
                    "contacto": picking.partner_id or 0,
                    "contacto_name": picking.partner_id.name or "",
                    "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item)),
                    "cantidad_productos_total": len(picking.move_line_ids),
                    "priority": picking.priority,
                    "warehouse_id": (
                        picking.picking_type_id.warehouse_id.id if picking.picking_type_id.warehouse_id else 0
                    ),
                    "warehouse_name": (
                        picking.picking_type_id.warehouse_id.name
                        if picking.picking_type_id.warehouse_id
                        else ""
                    ),
                    "responsable_id": picking.responsable_id.id or 0,
                    "responsable": picking.responsable_id.name or "",
                    "picking_type": picking.picking_type_id.name,
                    "start_time_transfer": picking.start_time_transfer or "",
                    "end_time_transfer": picking.end_time_transfer or "",
                    "backorder_id": picking.backorder_id.id or 0,
                    "backorder_name": picking.backorder_id.name or "",
                    "show_check_availability": picking.show_check_availability,
                    "order_tms": picking.order_tms if hasattr(picking, "order_tms") else "",
                    "zona_entrega_tms": (
                        picking.delivery_zone_tms if hasattr(picking, "delivery_zone_tms") else ""
                    ),
                    "zona_entrega": picking.delivery_zone_id.display_name or "",
                    "numero_paquetes": len(picking.move_line_ids.mapped("result_package_id")),
                    "quantity_done": sum(ml.quantity for ml in picking.move_line_ids if ml.is_done_item),
                    "quantity_ordered": sum(
                        ml.move_id.product_uom_qty for ml in picking.move_line_ids if ml.move_id
                    ),
                }
                array_result.append(transferencia_info)

            return {"code": 200, "result": array_result}

        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/batchs/componentes", auth="user", type="json", methods=["GET"])
    def get_batches(self, **kwargs):
        try:
            version_app = kwargs.get("version_app")

            # 1. Llama al otro método DENTRO de esta misma clase
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                app_parts = list(map(int, version_app.split(".")))
                latest_parts = list(map(int, latest_version_str.split(".")))
                if app_parts < latest_parts:
                    update_required = True
            else:
                update_required = True

            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "update_version": update_required, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # obtener la configuracion picking de la app
            config_picking = request.env["picking.config.general"].sudo().browse(1)

            # ✅ Obtener estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)

            # ✅ Validar usuario WMS y sus zonas asignadas
            user_wms = request.env["appwms.users_wms"].sudo().search([("user_id", "=", user.id)], limit=1)

            if not user_wms or not user_wms.zone_ids:
                return {"code": 400, "msg": "El usuario no tiene zonas asignadas"}

            # ✅ Obtener ubicaciones de las zonas asignadas
            all_location_ids = list(
                {
                    loc_id
                    for zone in user_wms.zone_ids.sudo().read(["location_ids"])
                    for loc_id in zone["location_ids"]
                }
            )

            if not all_location_ids:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "El usuario no tiene ubicaciones asociadas",
                }

            # ✅ Obtener ubicaciones en bloques
            chunk_size = 100
            locations = []
            for i in range(0, len(all_location_ids), chunk_size):
                chunk = all_location_ids[i : i + chunk_size]
                locations.extend(
                    request.env["stock.location"]
                    .sudo()
                    .browse(chunk)
                    .read(
                        [
                            "id",
                            "name",
                            "complete_name",
                            "priority_picking",
                            "barcode",
                            "priority_picking_desplay",
                        ]
                    )
                )

            user_location_ids = [location["id"] for location in locations]

            search_domain = [
                ("state", "=", "in_progress"),
                ("picking_type_code", "=", "internal"),
                ("picking_type_id.sequence_code", "=", "PC"),
            ]

            # ✅ Filtrar por responsable si config_picking es 'responsible'
            if config_picking.picking_type == "responsible":
                search_domain.append(("user_id", "=", user.id))  # Agregar filtro por usuario responsable

            # ✅ Obtener lotes (batches)
            batchs = request.env["stock.picking.batch"].sudo().search(search_domain)

            # ✅ Verificar si no hay lotes encontrados
            if not batchs:
                return {"code": 200, "msg": "No tienes batches asignados"}

            array_batch = []
            for batch in batchs:
                # ✅ Obtener movimientos unificados
                move_unified_ids = (
                    request.env["move.line.unified"]
                    .sudo()
                    .search(
                        [("stock_picking_batch_id", "=", batch.id), ("location_id", "in", user_location_ids)]
                    )
                )

                if not move_unified_ids:
                    continue

                # ✅ NUEVO: Verificar si todos los items están completados
                total_items = len(move_unified_ids)
                completed_items = len(move_unified_ids.filtered(lambda m: m.is_done_item))

                # Si todos los items están completados, saltar este batch
                if total_items > 0 and completed_items == total_items:
                    continue

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
                origin_details = origins_list if origins_list else []

                # stock_moves = move_unified_ids.read(["product_id", "lot_id", "location_id", "location_dest_id", "product_uom_qty", "is_done_item"])
                stock_moves = move_unified_ids.read()

                array_batch_temp = {
                    "id": batch.id,
                    "name": batch.name or "",
                    "user_name": user.name,
                    "user_id": user.id,
                    "rol": user_wms.user_rol or "USER",
                    "order_by": picking_strategy.picking_priority_app,
                    "order_picking": picking_strategy.picking_order_app,
                    "scheduleddate": batch.scheduled_date or "",
                    "state": batch.state or "",
                    "picking_type_id": batch.picking_type_id.display_name if batch.picking_type_id else "N/A",
                    "observation": "",
                    "is_wave": batch.is_wave,
                    "muelle": batch.location_id.display_name if batch.location_id else "SIN-MUELLE",
                    "id_muelle": batch.location_id.id if batch.location_id else "",
                    "id_muelle_padre": batch.location_id.location_id.id if batch.location_id else "",
                    "barcode_muelle": batch.location_id.barcode or "",
                    "count_items": len(stock_moves),
                    "total_quantity_items": sum(move["product_uom_qty"] for move in stock_moves),
                    # ✅ NUEVO: Agregar información de progreso
                    "completed_items": completed_items,
                    "progress_percentage": (
                        round((completed_items / total_items) * 100, 2) if total_items > 0 else 0
                    ),
                    "start_time_pick": batch.start_time_pick or "",
                    "end_time_pick": batch.end_time_pick or "",
                    "zona_entrega": (
                        batch.picking_ids[0].delivery_zone_id.name
                        if batch.picking_ids and batch.picking_ids[0].delivery_zone_id
                        else "SIN-ZONA"
                    ),
                    "origin": origin_details,
                    "list_items": [],
                }

                product_ids = {move["product_id"][0] for move in stock_moves}
                products = {
                    prod.id: prod for prod in request.env["product.product"].sudo().browse(product_ids)
                }

                location_ids = {move["location_id"][0] for move in stock_moves}
                locations_dict = {
                    loc.id: loc for loc in request.env["stock.location"].sudo().browse(location_ids)
                }

                for move in stock_moves:
                    product = products.get(move["product_id"][0])
                    location = locations_dict.get(move["location_id"][0])
                    location_dest = locations_dict.get(move["location_dest_id"][0])

                    # ✅ Obtener códigos de barras adicionales
                    array_all_barcode = (
                        [
                            {
                                "barcode": barcode.name,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": [
                                    move["product_id"][0] if move["product_id"] else 0,
                                    move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                                ],
                                "cantidad": 1,
                                "id_product": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for barcode in product.barcode_ids
                            if barcode.name  # Filtra solo los barcodes válidos
                        ]
                        if product.barcode_ids
                        else []
                    )

                    # ✅ Obtener empaques del producto
                    array_packing = (
                        [
                            {
                                "barcode": pack.barcode,
                                "cantidad": pack.qty,
                                "batch_id": batch.id,
                                "id_move": move["id"],
                                "product_id": move["product_id"][0] if move["product_id"] else 0,
                            }
                            for pack in product.packaging_ids
                            if pack.barcode
                        ]
                        if product.packaging_ids
                        else []
                    )

                    # ✅ Buscar el picking_id desde stock.move
                    picking = (
                        request.env["stock.picking"].sudo().search([("batch_id", "=", batch.id)], limit=1)
                    )  # Obtiene un picking asociado al batch
                    picking_id = picking.id if picking else 0

                    # ✅ Obtener el nombre del pedido
                    picking_name = picking.display_name if picking else ""

                    # ✅ Obtener la zona de entrega del picking
                    delivery_zone_name = (
                        picking.delivery_zone_id.display_name
                        if picking and picking.delivery_zone_id
                        else "SIN-ZONA"
                    )
                    delivery_zone_id = (
                        picking.delivery_zone_id.id if picking and picking.delivery_zone_id else 0
                    )

                    user_operator_id = 0
                    user_operator_name = ""
                    if (
                        move.get("user_operator_id")
                        and isinstance(move["user_operator_id"], (list, tuple))
                        and len(move["user_operator_id"]) > 0
                    ):
                        user_operator_id = move["user_operator_id"][0]
                        user_operator_name = (
                            move["user_operator_id"][1] if len(move["user_operator_id"]) > 1 else ""
                        )

                    array_batch_temp["list_items"].append(
                        {
                            "batch_id": batch.id,
                            "id_move": move["id"],
                            "picking_id": picking_id,
                            "id_product": move["product_id"][0] if move["product_id"] else 0,
                            "product_id": [
                                move["product_id"][0] if move["product_id"] else 0,
                                move["product_id"][1] if len(move["product_id"]) > 1 else "N/A",
                            ],
                            "lote_id": move["lot_id"][0] if move["lot_id"] else "",
                            "lot_id": [
                                (
                                    move["lot_id"][0]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 0
                                    else 0
                                ),
                                (
                                    move["lot_id"][1]
                                    if move.get("lot_id")
                                    and isinstance(move["lot_id"], (list, tuple))
                                    and len(move["lot_id"]) > 1
                                    else move["lot_id"]
                                    if isinstance(move["lot_id"], str)
                                    else ""
                                ),
                            ],
                            "expire_date": (
                                request.env["stock.lot"].sudo().browse(move["lot_id"][0]).expiration_date
                                if move["lot_id"]
                                else ""
                            ),
                            "location_id": move["location_id"],
                            "rimoval_priority": location.priority_picking_desplay,
                            "barcode_location": location.barcode if location else "",
                            "location_dest_id": move["location_dest_id"],
                            "barcode_location_dest": location_dest.barcode if location_dest else "",
                            "quantity": move["product_uom_qty"],
                            "barcode": product.barcode if product else "",
                            "other_barcode": array_all_barcode,
                            "product_packing": array_packing,
                            "weight": product.weight if product else 0,
                            "unidades": product.uom_id.name if product else "",
                            "zona_entrega": delivery_zone_name,
                            "id_zona_entrega": delivery_zone_id,
                            "pedido": picking_name,
                            "pedido_id": picking_id,
                            "origin": picking.origin or "",
                            "quantity_separate": move[
                                "qty_done"
                            ],  # Cantidad separada si el item ya fue separado
                            "observation": move["new_observation"] or "",  # Observación del movimiento
                            "time_separate": move["time"] or "",  # Hora de separación del item
                            "fecha_transaccion": move["date_transaction_picking"]
                            or "",  # Fecha de separación del item
                            # "id_user_separate": user_operator_id,
                            # "user_separate": user_operator_name,
                            "is_separate": (
                                1 if move["is_done_item"] else 0
                            ),  # Indica si el item ya fue separado
                        }
                    )

                if array_batch_temp["list_items"]:
                    array_batch.append(array_batch_temp)

            return {"code": 200, "update_version": update_required, "result": array_batch}

        except AccessError as e:
            return {"code": 403, "update_version": update_required, "msg": f"Acceso denegado: {str(e)}"}

        except Exception as err:
            if "unsupported XML-RPC protocol" in str(err):
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Indicar protocolo http o https de url_rpc",
                }
            return {"code": 400, "update_version": update_required, "msg": f"Error inesperado: {str(err)}"}


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


def validate_pda(device_id):
    """
    Solo valida que la PDA existe y está autorizada
    Returns: dict con error si hay problema, None si todo está OK
    """
    if not device_id:
        return {
            "code": 400,
            "msg": "Device ID no proporcionado, por favor actualizar a la ultima version de la app",
        }

    pda = request.env["pda.logs"].sudo().search([("device_id", "=", device_id)])

    if not pda:
        return {"code": 404, "msg": "PDA no encontrado"}

    if pda.is_authorized == "no":
        return {"code": 403, "msg": "PDA no autorizado"}

    # Si llegamos aquí, todo está bien
    return None
