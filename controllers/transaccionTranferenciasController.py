import logging
from datetime import datetime, timedelta

import pytz
from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request
from odoo.tools import float_compare, html2plaintext

from .utils import get_barcodes, get_packagings


class TransaccionTransferenciasController(http.Controller):
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

    ## GET Obtener todas las transferencias
    @http.route("/api/transferencias", auth="user", type="json", methods=["GET"])
    def get_transferencias(self, **kwargs):
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

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            if not user:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

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

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = picking.picking_type_id.create_backorder if hasattr(picking.picking_type_id, "create_backorder") else False

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
                        "numero_lineas": len(picking.move_ids),
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
                                "maneja_fecha_vencimiento": product.use_expiration_date or "",
                                # "other_barcodes": [
                                #     {"barcode": b.name} for b in getattr(product, "barcode_ids", [])
                                # ],
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": [
                                    {
                                        "barcode": p.barcode,
                                        "cantidad": p.qty,
                                        "id_product": p.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for p in getattr(product, "packaging_ids", [])
                                    if p.barcode  # Esta condición asegura que el campo 'barcode' tenga un valor.
                                ],
                                "quantity_ordered": quantity_ordered,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND"),
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
                            # "other_barcodes": [
                            #     {"barcode": b.name} for b in getattr(product, "barcode_ids", [])
                            # ],
                            "other_barcodes": get_barcodes(product, move_line.id, picking.id),
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
                            "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
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
                            "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
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

            return {
                "code": 200,
                "update_version": update_required,
                "result": array_transferencias,
            }

        except AccessError as e:
            return {
                "code": 403,
                "update_version": update_required,
                "msg": f"Acceso denegado: {str(e)}",
            }
        except Exception as err:
            return {
                "code": 400,
                "update_version": update_required,
                "msg": f"Error inesperado: {str(err)}",
            }

    @http.route("/api/transferencias/v2", auth="user", type="json", methods=["GET"])
    def get_transferencias_v2(self, **kwargs):
        try:
            user = request.env.user

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

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

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = picking.picking_type_id.create_backorder if hasattr(picking.picking_type_id, "create_backorder") else False

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
                        "numero_lineas": len(picking.move_ids),
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
                                "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
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
                                "uom": (move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND"),
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
                            "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
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
                            "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
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
                            "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
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
    def get_transferencias_pick(self, **kwargs):
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

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            if not user:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

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
                            ("picking_type_id.sequence_code", "in", ["PICK", "SE"]),
                            ("responsable_id", "in", [user.id, False]),
                            ("batch_id", "=", False),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_operaciones = picking.move_line_ids
                    movimientos_enviados = picking.move_line_ids

                    if not movimientos_operaciones:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = picking.picking_type_id.create_backorder if hasattr(picking.picking_type_id, "create_backorder") else False

                    nota_venta = picking.sale_id.note if hasattr(picking, "sale_id") and picking.sale_id else ""

                    transferencia_info = {
                        "id": picking.id,
                        "name": picking.name,
                        "observacion": html2plaintext(nota_venta) if nota_venta else "",
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
                        "order_by": (picking_strategy.picking_priority_app if picking_strategy else ""),
                        "order_picking": (picking_strategy.picking_order_app if picking_strategy else ""),
                        "muelle": picking.location_dest_id.display_name or "",
                        "muelle_id": picking.location_dest_id.id or 0,
                        "id_muelle_padre": picking.location_dest_id.location_id.id or 0,
                        "barcode_muelle": picking.location_dest_id.barcode or "",
                        "zona_entrega": picking.delivery_zone_id.display_name or "",
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
                                    "product_id": (move["product_id"][0] if move["product_id"] else 0),
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
                                "other_barcodes": get_barcodes(product, move.id, picking.id),
                                "product_packing": [
                                    {
                                        "barcode": p.barcode,
                                        "cantidad": p.qty,
                                        "id_product": p.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for p in getattr(product, "packaging_ids", [])
                                    if p.barcode  # Esta condición asegura que el campo 'barcode' tenga un valor.
                                ],
                                "quantity": quantity_done,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "unidades": (move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND"),
                                "location_dest_id": [
                                    move.location_dest_id.id,
                                    move.location_dest_id.display_name,
                                ],  # ✅
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "barcode_location_dest": move.location_dest_id.barcode or "",
                                "location_id": [
                                    move.location_id.id,
                                    move.location_id.display_name,
                                ],  # ✅
                                "location_name": move.location_id.display_name or "",
                                "barcode_location": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "rimoval_priority": location.priority_picking_desplay,
                                "zona_entrega": picking.delivery_zone_id.display_name,
                                "other_barcode": get_barcodes(product, move.id, picking.id),
                                # "product_packing": array_packing,
                                "pedido": picking.name,
                                "pedido_id": picking.id,
                                "origin": picking.origin or "",
                                "lote_id": move.lot_id.id or 0,
                                "lote": move.lot_id.name or "",
                                "is_done_item": False,
                                "date_transaction": "",
                                "observation": "",
                                "time_separate": "",
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
                            # "other_barcodes": [
                            #     {"barcode": b.name} for b in getattr(product, "barcode_ids", [])
                            # ],
                            "other_barcodes": get_barcodes(product, move_line.id, picking.id),
                            "product_packing": [
                                {
                                    "barcode": p.barcode,
                                    "cantidad": p.qty,
                                    "id_product": p.product_id.id,
                                    "id_move": move_line.id,
                                    "batch_id": picking.id,
                                    "product_id": p.product_id.id,
                                }
                                for p in getattr(product, "packaging_ids", [])
                            ],
                            "quantity": quantity_ordered,
                            "quantity_to_transfer": quantity_ordered,
                            "quantity_done": move_line.quantity,
                            "cantidad_faltante": quantity_ordered,
                            "unidades": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
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
                            "pedido": picking.name,
                            "pedido_id": picking.id,
                            "origin": picking.origin or "",
                            "lote_id": move_line.lot_id.id or 0,
                            "lote": move_line.lot_id.name or "",
                            "quantity_separate": move_line.quantity,
                            "is_done_item": move_line.is_done_item,
                            "date_transaction": move_line.date_transaction or "",
                            "observation": move_line.new_observation or "",
                            "time_separate": format_time_from_seconds(move_line.time),
                            "time": move_line.time or 0,
                            "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
                            "expire_date": move_line.lot_id.expiration_date or "",
                            "is_separate": 1,
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

            return {
                "code": 200,
                "update_version": update_required,
                "result": array_transferencias,
            }

        except AccessError as e:
            return {
                "code": 403,
                "update_version": update_required,
                "msg": f"Acceso denegado: {str(e)}",
            }
        except Exception as err:
            return {
                "code": 400,
                "update_version": update_required,
                "msg": f"Error inesperado: {str(err)}",
            }

    @http.route("/api/transferencias/pick/v2", auth="user", type="json", methods=["GET"])
    def get_transferencias_pick_v2(self, **kwargs):
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
                            ("picking_type_id.sequence_code", "in", ["PICK"]),
                            ("responsable_id", "in", [user.id, False]),
                            ("batch_id", "=", False),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_operaciones = picking.move_line_ids
                    movimientos_enviados = picking.move_line_ids

                    if not movimientos_operaciones:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = picking.picking_type_id.create_backorder if hasattr(picking.picking_type_id, "create_backorder") else False

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
                        "order_by": (picking_strategy.picking_priority_app if picking_strategy else ""),
                        "order_picking": (picking_strategy.picking_order_app if picking_strategy else ""),
                        "muelle": picking.location_dest_id.display_name or "",
                        "muelle_id": picking.location_dest_id.id or 0,
                        "id_muelle_padre": picking.location_dest_id.location_id.id or 0,
                        "barcode_muelle": picking.location_dest_id.barcode or "",
                        "zona_entrega": picking.delivery_zone_id.display_name or "",
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
                                    "product_id": (move["product_id"][0] if move["product_id"] else 0),
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
                                "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
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
                                "unidades": (move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND"),
                                "location_dest_id": [
                                    move.location_dest_id.id,
                                    move.location_dest_id.display_name,
                                ],  # ✅
                                "location_dest_name": move.location_dest_id.display_name or "",
                                "barcode_location_dest": move.location_dest_id.barcode or "",
                                "location_id": [
                                    move.location_id.id,
                                    move.location_id.display_name,
                                ],  # ✅
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
                                "is_done_item": False,
                                "date_transaction": "",
                                "observation": "",
                                "time_separate": "",
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
                            "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
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
                            "unidades": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
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
                            "other_barcode": array_all_barcode,
                            "product_packing": array_packing,
                            "pedido": picking.name,
                            "pedido_id": picking.id,
                            "origin": picking.origin or "",
                            "lote_id": move_line.lot_id.id or 0,
                            "lote": move_line.lot_id.name or "",
                            "quantity_separate": move_line.quantity,
                            "is_done_item": move_line.is_done_item,
                            "date_transaction": move_line.date_transaction or "",
                            "observation": move_line.new_observation or "",
                            "time_separate": format_time_from_seconds(move_line.time),
                            "time": move_line.time or 0,
                            "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
                            "expire_date": move_line.lot_id.expiration_date or "",
                            "is_separate": 1,
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
    def get_transferencias_pack(self, **kwargs):
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

            device_id = kwargs.get("device_id") or request.params.get("device_id")
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            if not user:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

            array_transferencias = []

            base_url = request.httprequest.host_url.rstrip("/")

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
                            ("batch_id", "=", False),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_operaciones = picking.move_line_ids
                    movimientos_enviados = picking.move_line_ids

                    if not movimientos_operaciones:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = picking.picking_type_id.create_backorder if hasattr(picking.picking_type_id, "create_backorder") else False

                    nota_venta = picking.sale_id.note if hasattr(picking, "sale_id") and picking.sale_id else ""

                    transferencia_info = {
                        "batch_id": picking.id,
                        "id": picking.id,
                        "name": picking.name,
                        "observacion": html2plaintext(nota_venta) if nota_venta else "",
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
                        "referencia": picking.origin or "",
                        "contacto": picking.partner_id or 0,
                        "contacto_name": picking.partner_id.name or "",
                        "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item)),
                        "cantidad_productos_total": len(picking.move_line_ids),
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
                        "order_tms": picking.order_tms if hasattr(picking, "order_tms") else "",
                        "zona_entrega_tms": (picking.delivery_zone_tms if hasattr(picking, "delivery_zone_tms") else ""),
                        "zona_entrega": picking.delivery_zone_id.display_name or "",
                        "numero_paquetes": len(picking.move_line_ids.mapped("result_package_id")),
                        "lista_productos": [],
                        # "lista_productos_enviadas": [],
                        "lista_paquetes": [],
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
                                "pedido_id": picking.id,
                                "batch_id": picking.id,
                                "picking_id": picking.id,  # ✅
                                "id_transferencia": picking.id,
                                "product_id": [product.id, product.display_name],  # ✅
                                "id_product": product.id or 0,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "barcode": product.barcode or "",
                                "tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
                                "product_packing": [
                                    {
                                        "barcode": p.barcode,
                                        "cantidad": p.qty,
                                        "id_product": p.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for p in getattr(product, "packaging_ids", [])
                                    if p.barcode  # Esta condición asegura que el campo 'barcode' tenga un valor.
                                ],
                                "quantity": quantity_done,
                                "quantity_ordered": quantity_ordered,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND"),
                                "location_dest_id": [
                                    move.location_dest_id.id,
                                    move.location_dest_id.display_name,
                                ],
                                # "location_dest_name": move.location_dest_id.display_name or "",
                                "barcode_location_dest": move.location_dest_id.barcode or "",
                                "location_id": [
                                    move.location_id.id,
                                    move.location_id.display_name,
                                ],
                                "rimoval_priority": move.location_id.priority_picking_desplay,
                                # "location_name": move.location_id.display_name or "",
                                "barcode_location": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "unidades": product.uom_id.name if product.uom_id else "UND",
                                # "is_done_item": False,
                                # "date_transaction": "",
                                # "observation": "",
                                # "time": 0,
                                # "user_operator_id": 0,
                                "lot_id": [move.lot_id.id, move.lot_id.name] if move.lot_id else [],
                                "lote_id": move.lot_id.id or 0,
                                "expire_date": move.lot_id.expiration_date or "",
                                "other_barcode": get_barcodes(product, move.id, picking.id),
                                "product_packing": [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_product": pack.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ],
                                "maneja_temperatura": (product.temperature_control if hasattr(product, "temperature_control") else False),
                                "temperatura": (move.temperature if hasattr(move, "temperature") else 0),
                            }

                            # if move.lot_id:
                            #     linea_info.update(
                            #         {
                            #             "lot_id": move.lot_id.id,
                            #             "lot_name": move.lot_id.name,
                            #             "fecha_vencimiento": move.lot_id.expiration_date or "",
                            #         }
                            #     )
                            # else:
                            #     linea_info.update(
                            #         {
                            #             "lot_id": 0,
                            #             "lot_name": "",
                            #             "fecha_vencimiento": "",
                            #         }
                            #     )

                            transferencia_info["lista_productos"].append(linea_info)

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
                            "product_id": [product.id, product.display_name],  # ✅
                            "id_product": product.id or 0,
                            "product_name": product.display_name,
                            "product_code": product.default_code or "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": get_barcodes(product, move_line.id, picking.id),
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
                            "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
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
                            "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
                            "lot_id": ([move_line.lot_id.id, move_line.lot_id.name] if move_line.lot_id else []),
                        }

                        # if move_line.lot_id:
                        #     linea_info.update(
                        #         {
                        #             "lot_id": move_line.lot_id.id,
                        #             "lot_name": move_line.lot_id.name,
                        #             "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                        #         }
                        #     )
                        # else:
                        #     linea_info.update(
                        #         {
                        #             "lot_id": 0,
                        #             "lot_name": "",
                        #             "fecha_vencimiento": "",
                        #         }
                        #     )

                        # transferencia_info["lista_productos_enviadas"].append(linea_info)

                    # Obtener los paquetes de la transferencia
                    move_lines_in_picking = picking.move_line_ids.filtered(lambda ml: ml.package_id or ml.result_package_id)
                    unique_packages = move_lines_in_picking.mapped("package_id") + move_lines_in_picking.mapped("result_package_id")

                    for pack in unique_packages:
                        move_lines_in_package = move_lines_in_picking.filtered(
                            lambda ml: (ml.package_id == pack or ml.result_package_id == pack) and ml.is_done_item
                        )

                        cantidad_productos = len(move_lines_in_package)

                        package = {
                            "name": pack.name,
                            "id": pack.id,
                            "batch_id": picking.id,  # Usaré picking.id en lugar de batch.id ya que no veo una variable batch definida
                            "pedido_id": picking.id,
                            "cantidad_productos": cantidad_productos,
                            "lista_productos_in_packing": [],
                            "is_sticker": pack.is_sticker,
                            "is_certificate": pack.is_certificate,
                            "fecha_creacion": (pack.create_date.strftime("%Y-%m-%d") if pack.create_date else ""),
                            "fecha_actualizacion": (pack.write_date.strftime("%Y-%m-%d") if pack.write_date else ""),
                            "consecutivo": (getattr(move_lines_in_package[0], "faber_box_number", "") if move_lines_in_package else ""),
                        }
                        transferencia_info["lista_paquetes"].append(package)  # Cambié pedido a transferencia_info para que coincida con tu estructura

                        for move_line in move_lines_in_package:
                            product = move_line.product_id
                            lot = move_line.lot_id

                            product_in_packing = {
                                "id_move": move_line.id,
                                "pedido_id": picking.id,
                                "batch_id": picking.id,  # Usaré picking.id en lugar de batch.id
                                "package_name": pack.name,
                                "quantity_separate": move_line.quantity,
                                "id_product": product.id if product else 0,
                                "product_id": [product.id, product.display_name],
                                "name_packing": pack.name,
                                "cantidad_enviada": move_line.quantity,
                                "unidades": product.uom_id.name if product.uom_id else "UND",
                                "peso": product.weight if product else 0,
                                "lote_id": [lot.id, lot.name if lot else ""] if lot else [],
                                "observation": move_line.new_observation or "",
                                "weight": product.weight if product else 0,
                                "is_sticker": pack.is_sticker,
                                "is_certificate": pack.is_certificate,
                                "id_package": pack.id,
                                "quantity": move_line.quantity,
                                "tracking": product.tracking if product else "",
                                "maneja_temperatura": (product.temperature_control if hasattr(product, "temperature_control") else False),
                                "temperatura": (move_line.temperature if hasattr(move_line, "temperature") else 0),
                                "image": (
                                    f"{base_url}/api/view_imagen_linea_recepcion/{move_line.id}" if getattr(move_line, "imagen", False) else ""
                                ),
                                "image_novedad": (
                                    f"{base_url}/api/view_imagen_observation/{move_line.id}"
                                    if getattr(move_line, "imagen_observation", False)
                                    else ""
                                ),
                                "time_separate": move_line.time if move_line.time else 0,
                                "package_consecutivo": (move_line.faber_box_number if hasattr(move_line, "faber_box_number") else ""),
                            }

                            package["lista_productos_in_packing"].append(product_in_packing)

                    transferencia_info["numero_lineas"] = len(transferencia_info["lista_productos"])
                    # transferencia_info["numero_items"] = sum(l["quantity_to_transfer"] for l in transferencia_info["lineas_transferencia"])

                    array_transferencias.append(transferencia_info)

            return {
                "code": 200,
                "update_version": update_required,
                "result": array_transferencias,
            }

        except AccessError as e:
            return {
                "code": 403,
                "update_version": update_required,
                "msg": f"Acceso denegado: {str(e)}",
            }
        except Exception as err:
            return {
                "code": 400,
                "update_version": update_required,
                "msg": f"Error inesperado: {str(err)}",
            }

    @http.route("/api/transferencias/pack/v2", auth="user", type="json", methods=["GET"])
    def get_transferencias_pack_v2(self, **kwargs):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")

            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            array_transferencias = []

            base_url = request.httprequest.host_url.rstrip("/")

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
                            ("batch_id", "=", False),
                        ]
                    )
                )

                for picking in transferencias_pendientes:
                    movimientos_operaciones = picking.move_line_ids
                    movimientos_enviados = picking.move_line_ids

                    if not movimientos_operaciones:
                        continue

                    # Obtener si se maneja Crear orden parcial
                    create_backorder = picking.picking_type_id.create_backorder if hasattr(picking.picking_type_id, "create_backorder") else False

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
                        "numero_lineas": 0,
                        "numero_items": sum(move.product_uom_qty for move in picking.move_ids),
                        "state": picking.state,
                        "create_backorder": create_backorder,
                        "referencia": picking.origin or "",
                        "contacto": picking.partner_id or 0,
                        "contacto_name": picking.partner_id.name or "",
                        "cantidad_productos": len(picking.move_line_ids.filtered(lambda ml: not ml.is_done_item)),
                        "cantidad_productos_total": len(picking.move_line_ids),
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
                        "order_tms": picking.order_tms if hasattr(picking, "order_tms") else "",
                        "zona_entrega_tms": (picking.delivery_zone_tms if hasattr(picking, "delivery_zone_tms") else ""),
                        "zona_entrega": picking.delivery_zone_id.display_name or "",
                        "numero_paquetes": len(picking.move_line_ids.mapped("result_package_id")),
                        "lista_productos": [],
                        # "lista_productos_enviadas": [],
                        "lista_paquetes": [],
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
                                "pedido_id": picking.id,
                                "batch_id": picking.id,
                                "picking_id": picking.id,  # ✅
                                "id_transferencia": picking.id,
                                "product_id": [product.id, product.display_name],  # ✅
                                "id_product": product.id or 0,
                                "product_name": product.display_name,
                                "product_code": product.default_code or "",
                                "barcode": product.barcode or "",
                                "tracking": product.tracking or "",
                                "dias_vencimiento": product.expiration_time or "",
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
                                "quantity_ordered": quantity_ordered,
                                "quantity_to_transfer": quantity_ordered,
                                # "quantity_done": quantity_done,
                                "cantidad_faltante": cantidad_faltante,
                                "uom": (move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND"),
                                "location_dest_id": [
                                    move.location_dest_id.id,
                                    move.location_dest_id.display_name,
                                ],
                                # "location_dest_name": move.location_dest_id.display_name or "",
                                "barcode_location_dest": move.location_dest_id.barcode or "",
                                "location_id": [
                                    move.location_id.id,
                                    move.location_id.display_name,
                                ],
                                "rimoval_priority": move.location_id.priority_picking_desplay,
                                # "location_name": move.location_id.display_name or "",
                                "barcode_location": move.location_id.barcode or "",
                                "weight": product.weight or 0,
                                "unidades": product.uom_id.name if product.uom_id else "UND",
                                # "is_done_item": False,
                                # "date_transaction": "",
                                # "observation": "",
                                # "time": 0,
                                # "user_operator_id": 0,
                                "lot_id": [move.lot_id.id, move.lot_id.name] if move.lot_id else [],
                                "lote_id": move.lot_id.id or 0,
                                "expire_date": move.lot_id.expiration_date or "",
                                "other_barcode": get_barcodes(product, move.id, picking.id),
                                "product_packing": [
                                    {
                                        "barcode": pack.barcode,
                                        "cantidad": pack.qty,
                                        "id_product": pack.product_id.id,
                                        "id_move": move.id,
                                        "batch_id": picking.id,
                                    }
                                    for pack in product.packaging_ids
                                    if pack.barcode
                                ],
                                "maneja_temperatura": (product.temperature_control if hasattr(product, "temperature_control") else False),
                                "temperatura": (move.temperature if hasattr(move, "temperature") else 0),
                            }

                            # if move.lot_id:
                            #     linea_info.update(
                            #         {
                            #             "lot_id": move.lot_id.id,
                            #             "lot_name": move.lot_id.name,
                            #             "fecha_vencimiento": move.lot_id.expiration_date or "",
                            #         }
                            #     )
                            # else:
                            #     linea_info.update(
                            #         {
                            #             "lot_id": 0,
                            #             "lot_name": "",
                            #             "fecha_vencimiento": "",
                            #         }
                            #     )

                            transferencia_info["lista_productos"].append(linea_info)

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
                            "product_id": [product.id, product.display_name],  # ✅
                            "id_product": product.id or 0,
                            "product_name": product.display_name,
                            "product_code": product.default_code or "",
                            "product_barcode": product.barcode or "",
                            "product_tracking": product.tracking or "",
                            "dias_vencimiento": product.expiration_time or "",
                            "other_barcodes": [{"barcode": b.name} for b in getattr(product, "barcode_ids", [])],
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
                            "uom": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
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
                            "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
                            "lot_id": ([move_line.lot_id.id, move_line.lot_id.name] if move_line.lot_id else []),
                        }

                        # if move_line.lot_id:
                        #     linea_info.update(
                        #         {
                        #             "lot_id": move_line.lot_id.id,
                        #             "lot_name": move_line.lot_id.name,
                        #             "fecha_vencimiento": move_line.lot_id.expiration_date or "",
                        #         }
                        #     )
                        # else:
                        #     linea_info.update(
                        #         {
                        #             "lot_id": 0,
                        #             "lot_name": "",
                        #             "fecha_vencimiento": "",
                        #         }
                        #     )

                        # transferencia_info["lista_productos_enviadas"].append(linea_info)

                    # Obtener los paquetes de la transferencia
                    move_lines_in_picking = picking.move_line_ids.filtered(lambda ml: ml.package_id or ml.result_package_id)
                    unique_packages = move_lines_in_picking.mapped("package_id") + move_lines_in_picking.mapped("result_package_id")

                    for pack in unique_packages:
                        move_lines_in_package = move_lines_in_picking.filtered(
                            lambda ml: (ml.package_id == pack or ml.result_package_id == pack) and ml.is_done_item
                        )

                        cantidad_productos = len(move_lines_in_package)

                        package = {
                            "name": pack.name,
                            "id": pack.id,
                            "batch_id": picking.id,  # Usaré picking.id en lugar de batch.id ya que no veo una variable batch definida
                            "pedido_id": picking.id,
                            "cantidad_productos": cantidad_productos,
                            "lista_productos_in_packing": [],
                            "is_sticker": pack.is_sticker,
                            "is_certificate": pack.is_certificate,
                            "fecha_creacion": (pack.create_date.strftime("%Y-%m-%d") if pack.create_date else ""),
                            "fecha_actualizacion": (pack.write_date.strftime("%Y-%m-%d") if pack.write_date else ""),
                            "consecutivo": (getattr(move_lines_in_package[0], "faber_box_number", "") if move_lines_in_package else ""),
                        }
                        transferencia_info["lista_paquetes"].append(package)  # Cambié pedido a transferencia_info para que coincida con tu estructura

                        for move_line in move_lines_in_package:
                            product = move_line.product_id
                            lot = move_line.lot_id

                            product_in_packing = {
                                "id_move": move_line.id,
                                "pedido_id": picking.id,
                                "batch_id": picking.id,  # Usaré picking.id en lugar de batch.id
                                "package_name": pack.name,
                                "quantity_separate": move_line.quantity,
                                "id_product": product.id if product else 0,
                                "product_id": [product.id, product.display_name],
                                "name_packing": pack.name,
                                "cantidad_enviada": move_line.quantity,
                                "unidades": product.uom_id.name if product.uom_id else "UND",
                                "peso": product.weight if product else 0,
                                "lote_id": [lot.id, lot.name if lot else ""] if lot else [],
                                "observation": move_line.new_observation or "",
                                "weight": product.weight if product else 0,
                                "is_sticker": pack.is_sticker,
                                "is_certificate": pack.is_certificate,
                                "id_package": pack.id,
                                "quantity": move_line.quantity,
                                "tracking": product.tracking if product else "",
                                "maneja_temperatura": (product.temperature_control if hasattr(product, "temperature_control") else False),
                                "temperatura": (move_line.temperature if hasattr(move_line, "temperature") else 0),
                                "image": (
                                    f"{base_url}/api/view_imagen_linea_recepcion/{move_line.id}" if getattr(move_line, "imagen", False) else ""
                                ),
                                "image_novedad": (
                                    f"{base_url}/api/view_imagen_observation/{move_line.id}"
                                    if getattr(move_line, "imagen_observation", False)
                                    else ""
                                ),
                                "time_separate": move_line.time if move_line.time else 0,
                                "package_consecutivo": (move_line.faber_box_number if hasattr(move_line, "faber_box_number") else ""),
                            }

                            package["lista_productos_in_packing"].append(product_in_packing)

                    transferencia_info["numero_lineas"] = len(transferencia_info["lista_productos"])
                    # transferencia_info["numero_items"] = sum(l["quantity_to_transfer"] for l in transferencia_info["lineas_transferencia"])

                    array_transferencias.append(transferencia_info)

            return {"code": 200, "result": array_transferencias}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Obtener todos los picking por rango de fecha
    @http.route("/api/transferencias/history_picking", auth="user", type="json", methods=["GET"])
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
                        (
                            "picking_type_id.warehouse_id",
                            "in",
                            [wh.id for wh in allowed_warehouses],
                        ),
                        ("write_date", ">=", date_from),
                        ("write_date", "<=", date_to),
                        ("picking_type_id.sequence_code", "in", ["PICK", "SE"]),
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
                    "warehouse_id": (picking.picking_type_id.warehouse_id.id if picking.picking_type_id.warehouse_id else 0),
                    "warehouse_name": (picking.picking_type_id.warehouse_id.name if picking.picking_type_id.warehouse_id else ""),
                    "responsable_id": picking.responsable_id.id or 0,
                    "responsable": picking.responsable_id.name or "",
                    "picking_type": picking.picking_type_id.name,
                    "start_time_transfer": picking.start_time_transfer or "",
                    "end_time_transfer": picking.end_time_transfer or "",
                    "backorder_id": picking.backorder_id.id or 0,
                    "backorder_name": picking.backorder_id.name or "",
                    "show_check_availability": picking.show_check_availability,
                    "order_tms": picking.order_tms if hasattr(picking, "order_tms") else "",
                    "zona_entrega_tms": (picking.delivery_zone_tms if hasattr(picking, "delivery_zone_tms") else ""),
                    "zona_entrega": picking.delivery_zone_id.display_name or "",
                    "numero_paquetes": len(picking.move_line_ids.mapped("result_package_id")),
                    "quantity_done": sum(ml.quantity for ml in picking.move_line_ids if ml.is_done_item),
                    "quantity_ordered": sum(ml.move_id.product_uom_qty for ml in picking.move_line_ids if ml.move_id),
                }
                array_result.append(transferencia_info)

            return {"code": 200, "result": array_result}

        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## GET Obtener tranferencia por id
    @http.route("/api/transferencias/<int:id>", auth="user", type="json", methods=["GET"])
    def get_transferencia_by_id(self, id):
        """
        Obtiene una transferencia específica por ID con sus líneas de movimiento
        """
        try:
            user = request.env.user

            # Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # Buscar la transferencia por ID
            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id)])

            # Verificar si la transferencia existe
            if not transferencia:
                return {"code": 404, "msg": "Transferencia no encontrada"}

            # Validar acceso al almacén
            allowed_warehouses = obtener_almacenes_usuario(user)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            if transferencia.picking_type_id.warehouse_id not in allowed_warehouses:
                return {"code": 403, "msg": "Acceso denegado a la transferencia"}

            # Obtener configuración de estrategia de picking
            picking_strategy = request.env["picking.strategy"].sudo().browse(1)
            create_backorder = transferencia.picking_type_id.create_backorder if hasattr(transferencia.picking_type_id, "create_backorder") else False

            # Información general de la transferencia
            transferencia_info = {
                "id": transferencia.id,
                "name": transferencia.name,
                "fecha_creacion": transferencia.create_date,
                "location_id": transferencia.location_id.id,
                "location_name": transferencia.location_id.display_name,
                "location_barcode": transferencia.location_id.barcode or "",
                "location_dest_id": transferencia.location_dest_id.id,
                "location_dest_name": transferencia.location_dest_id.display_name,
                "location_dest_barcode": transferencia.location_dest_id.barcode or "",
                "proveedor": transferencia.partner_id.name or "",
                "numero_transferencia": transferencia.name,
                "peso_total": 0,
                "numero_items": sum(move.product_uom_qty for move in transferencia.move_ids),
                "state": transferencia.state,
                "create_backorder": create_backorder,
                "origin": transferencia.origin or "",
                "priority": transferencia.priority,
                "warehouse_id": transferencia.picking_type_id.warehouse_id.id,
                "warehouse_name": transferencia.picking_type_id.warehouse_id.name,
                "responsable_id": transferencia.responsable_id.id or 0,
                "responsable": transferencia.responsable_id.name or "",
                "picking_type": transferencia.picking_type_id.name,
                "start_time_transfer": transferencia.start_time_transfer or "",
                "end_time_transfer": transferencia.end_time_transfer or "",
                "backorder_id": transferencia.backorder_id.id or 0,
                "backorder_name": transferencia.backorder_id.name or "",
                "show_check_availability": transferencia.show_check_availability,
                "order_by": picking_strategy.picking_priority_app if picking_strategy else "",
                "order_picking": picking_strategy.picking_order_app if picking_strategy else "",
                "muelle": transferencia.location_dest_id.display_name or "",
                "muelle_id": transferencia.location_dest_id.id or 0,
                "id_muelle_padre": transferencia.location_dest_id.location_id.id or 0,
                "barcode_muelle": transferencia.location_dest_id.barcode or "",
                "zona_entrega": transferencia.delivery_zone_id.display_name or "",
                "quantity_done": sum(ml.quantity for ml in transferencia.move_line_ids if ml.is_done_item),
                "quantity_ordered": sum(ml.move_id.product_uom_qty for ml in transferencia.move_line_ids if ml.move_id),
                "lineas_transferencia": [],
                "lineas_transferencia_enviadas": [],
            }

            # Procesar líneas de movimiento
            for move_line in transferencia.move_line_ids:
                product = move_line.product_id
                quantity_ordered = move_line.move_id.product_uom_qty if move_line.move_id else 0
                quantity_done = move_line.quantity or 0
                cantidad_faltante = quantity_ordered - quantity_done
                cantidad_faltante = quantity_ordered - cantidad_faltante
                location = move_line.location_id

                # Obtener códigos de barras adicionales
                array_all_barcode = [
                    {
                        "barcode": barcode.name,
                        "batch_id": transferencia.id,
                        "id_move": move_line.move_id.id if move_line.move_id else 0,
                        "id_product": product.id,
                    }
                    for barcode in getattr(product, "barcode_ids", [])
                    if barcode.name
                ]

                # Obtener empaques del producto
                array_packing = [
                    {
                        "barcode": pack.barcode,
                        "cantidad": pack.qty,
                        "batch_id": transferencia.id,
                        "id_move": move_line.move_id.id if move_line.move_id else 0,
                        "product_id": product.id,
                        "id_product": product.id,
                    }
                    for pack in getattr(product, "packaging_ids", [])
                    if pack.barcode
                ]

                # Solo procesar si hay cantidad pendiente y no está completado
                if quantity_done == 0:
                    continue

                if not move_line.is_done_item:
                    # Líneas pendientes de transferencia
                    linea_info = {
                        "id": move_line.move_id.id if move_line.move_id else 0,
                        "id_move": move_line.id,
                        "id_transferencia": transferencia.id,
                        "batch_id": transferencia.id,
                        "id_product": product.id,
                        "product_id": [product.id, product.display_name],
                        "product_name": product.display_name,
                        "product_code": product.default_code or "",
                        "barcode": product.barcode or "",
                        "product_tracking": product.tracking or "",
                        "dias_vencimiento": product.expiration_time or "",
                        "other_barcodes": get_barcodes(product, move_line.id, transferencia.id),
                        "product_packing": [
                            {
                                "barcode": p.barcode,
                                "cantidad": p.qty,
                                "id_product": p.product_id.id,
                                "id_move": move_line.id,
                                "batch_id": transferencia.id,
                                "product_id": p.product_id.id,
                            }
                            for p in getattr(product, "packaging_ids", [])
                        ],
                        "quantity": quantity_done,
                        "quantity_to_transfer": quantity_ordered,
                        "cantidad_faltante": cantidad_faltante,
                        "unidades": (move_line.move_id.product_uom.name if move_line.move_id and move_line.move_id.product_uom else "UND"),
                        "location_dest_id": [
                            move_line.location_dest_id.id,
                            move_line.location_dest_id.display_name,
                        ],
                        "location_dest_name": move_line.location_dest_id.display_name or "",
                        "barcode_location_dest": move_line.location_dest_id.barcode or "",
                        "location_id": [
                            move_line.location_id.id,
                            move_line.location_id.display_name,
                        ],
                        "location_name": move_line.location_id.display_name or "",
                        "barcode_location": move_line.location_id.barcode or "",
                        "weight": product.weight or 0,
                        "rimoval_priority": (location.priority_picking_desplay if hasattr(location, "priority_picking_desplay") else ""),
                        "zona_entrega": transferencia.delivery_zone_id.display_name or "",
                        "other_barcode": get_barcodes(product, move_line.id, transferencia.id),
                        "product_packing": array_packing,
                        "pedido": transferencia.name,
                        "pedido_id": transferencia.id,
                        "origin": transferencia.origin or "",
                        "lote_id": move_line.lot_id.id or 0,
                        "lote": move_line.lot_id.name or "",
                        "is_done_item": False,
                        "date_transaction": "",
                        "observation": "",
                        "time_separate": "",
                        "user_operator_id": 0,
                        "expire_date": move_line.lot_id.expiration_date or "",
                        "is_separate": 0,
                    }

                    transferencia_info["lineas_transferencia"].append(linea_info)

            # Procesar líneas enviadas/completadas
            for move_line in transferencia.move_line_ids:
                if not move_line.is_done_item:
                    continue

                product = move_line.product_id
                quantity_ordered = move_line.move_id.product_uom_qty if move_line.move_id else 0
                quantity_done = move_line.quantity or 0
                cantidad_faltante = quantity_ordered - quantity_done
                location = move_line.location_id

                # Obtener códigos de barras adicionales
                array_all_barcode = [
                    {
                        "barcode": barcode.name,
                        "batch_id": transferencia.id,
                        "id_move": move_line.move_id.id if move_line.move_id else 0,
                        "id_product": product.id,
                    }
                    for barcode in getattr(product, "barcode_ids", [])
                    if barcode.name
                ]

                linea_info = {
                    "id": move_line.id,
                    "id_move": move_line.id,
                    "id_transferencia": transferencia.id,
                    "batch_id": transferencia.id,
                    "id_product": product.id,
                    "product_id": [product.id, product.display_name],
                    "product_name": product.display_name,
                    "product_code": product.default_code or "",
                    "barcode": product.barcode or "",
                    "product_tracking": product.tracking or "",
                    "dias_vencimiento": product.expiration_time or "",
                    "other_barcodes": get_barcodes(product, move_line.id, transferencia.id),
                    "product_packing": [
                        {
                            "barcode": p.barcode,
                            "cantidad": p.qty,
                            "id_product": p.product_id.id,
                            "id_move": move_line.id,
                            "batch_id": transferencia.id,
                            "product_id": p.product_id.id,
                        }
                        for p in getattr(product, "packaging_ids", [])
                    ],
                    "quantity": quantity_ordered,
                    "quantity_to_transfer": quantity_ordered,
                    "quantity_done": move_line.quantity,
                    "cantidad_faltante": quantity_ordered,
                    "unidades": (move_line.product_uom_id.name if move_line.product_uom_id else "UND"),
                    "location_dest_id": [
                        move_line.location_dest_id.id,
                        move_line.location_dest_id.display_name,
                    ],
                    "location_dest_name": move_line.location_dest_id.display_name or "",
                    "barcode_location_dest": move_line.location_dest_id.barcode or "",
                    "location_id": [
                        move_line.location_id.id,
                        move_line.location_id.display_name,
                    ],
                    "location_name": move_line.location_id.display_name or "",
                    "barcode_location": move_line.location_id.barcode or "",
                    "weight": product.weight or 0,
                    "rimoval_priority": (location.priority_picking_desplay if hasattr(location, "priority_picking_desplay") else ""),
                    "zona_entrega": transferencia.delivery_zone_id.display_name or "",
                    "other_barcode": array_all_barcode,
                    "pedido": transferencia.name,
                    "pedido_id": transferencia.id,
                    "origin": transferencia.origin or "",
                    "lote_id": move_line.lot_id.id or 0,
                    "lote": move_line.lot_id.name or "",
                    "quantity_separate": move_line.quantity,
                    "is_done_item": move_line.is_done_item,
                    "date_transaction": move_line.date_transaction or "",
                    "observation": move_line.new_observation or "",
                    "time_separate": (format_time_from_seconds(move_line.time) if hasattr(move_line, "time") else ""),
                    "time": move_line.time or 0,
                    "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
                    "expire_date": move_line.lot_id.expiration_date or "",
                    "is_separate": 1,
                }

                transferencia_info["lineas_transferencia_enviadas"].append(linea_info)

            # Calcular totales
            transferencia_info["numero_lineas"] = len(transferencia_info["lineas_transferencia"])
            transferencia_info["peso_total"] = sum(
                (line["weight"] or 0) * (line["quantity"] or 0) for line in transferencia_info["lineas_transferencia"]
            )

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

            if transferencia.responsable_id:
                return {
                    "code": 400,
                    "msg": "La transferencia ya tiene un responsable asignado",
                }

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
                    return {
                        "code": 404,
                        "msg": f"Movimiento no encontrado (ID: {id_move})",
                    }

                stock_move = original_move.move_id

                move_parent = original_move.move_id
                product = request.env["product.product"].sudo().search([("id", "=", id_product)])

                if product.tracking == "lot" and not id_lote:
                    return {
                        "code": 400,
                        "msg": "El producto requiere lote y no se ha proporcionado uno",
                    }

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

    ## POST Enviar cantidad de producto en transferencia - PICK
    @http.route("/api/send_transfer/pick", auth="user", type="json", methods=["POST"], csrf=False)
    def send_transfer_pick(self, **auth):
        try:
            # Usar transacción para garantizar consistencia
            with request.env.cr.savepoint():
                user = request.env.user

                if not user:
                    return {"code": 400, "msg": "Usuario no encontrado"}

                id_transferencia = auth.get("id_transferencia", 0)
                list_items = auth.get("list_items", [])

                transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)])
                if not transferencia:
                    return {"code": 404, "msg": "Transferencia no encontrada"}

                # Verificar que el picking esté en estado válido
                if transferencia.state not in ["assigned", "partially_available"]:
                    return {
                        "code": 400,
                        "msg": f"La transferencia no está en estado válido para procesar. Estado actual: {transferencia.state}",
                    }

                # ===== FUNCIONES AUXILIARES PARA CORRECCIÓN DE RESERVAS =====
                def corregir_reservas_negativas(product_id, location_id, lote_id=None):
                    """
                    Corrige las reservas negativas antes de validar stock
                    """
                    domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        ("reserved_quantity", "<", 0),
                    ]

                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    # Buscar quants con reservas negativas
                    negative_quants = request.env["stock.quant"].sudo().search(domain)

                    correcciones_realizadas = []
                    for quant in negative_quants:
                        valor_anterior = quant.reserved_quantity
                        # Establecer reserva a 0 si es negativa
                        quant.write({"reserved_quantity": 0})
                        correcciones_realizadas.append(
                            {
                                "quant_id": quant.id,
                                "valor_anterior": valor_anterior,
                                "valor_nuevo": 0,
                                "product_id": product_id,
                                "location_id": location_id,
                            }
                        )

                    return correcciones_realizadas

                # def obtener_transferencias_con_reservas(product_id, location_id, lote_id=None):
                #     """
                #     Obtiene las transferencias que tienen reservas sobre este producto/ubicación/lote
                #     """
                #     domain = [
                #         ("product_id", "=", product_id),
                #         ("location_id", "=", location_id),
                #         (
                #             "picking_id.state",
                #             "in",
                #             ["assigned", "confirmed", "waiting", "partially_available"],
                #         ),
                #         ("is_done_item", "=", False),
                #         ("quantity", ">", 0),
                #     ]
                #     if lote_id:
                #         domain.append(("lot_id", "=", lote_id))

                #     move_lines = request.env["stock.move.line"].sudo().search(domain)

                #     # Agrupar por picking
                #     pickings_info = {}
                #     for ml in move_lines:
                #         picking = ml.picking_id
                #         if picking.id not in pickings_info:
                #             pickings_info[picking.id] = {
                #                 "nombre": picking.name,
                #                 "tipo": picking.picking_type_id.name,
                #                 "estado": picking.state,
                #                 "cantidad_reservada": 0,
                #             }
                #         pickings_info[picking.id]["cantidad_reservada"] += ml.quantity

                #     return list(pickings_info.values())

                def obtener_transferencias_con_reservas(product_id, location_id, lote_id=None):
                    """
                    Obtiene TODAS las transferencias que tienen reservas.
                    NO filtra por quantity para capturar todos los casos.
                    """
                    domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        (
                            "picking_id.state",
                            "in",
                            [
                                "assigned",
                                "confirmed",
                                "waiting",
                                "partially_available",
                                "draft",  # 🆕 Incluir draft por si acaso
                            ],
                        ),
                        # ("is_done_item", "=", False),
                        # 🔧 NO filtrar por quantity > 0
                    ]
                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    move_lines = request.env["stock.move.line"].sudo().search(domain)

                    # Agrupar por picking
                    pickings_info = {}
                    for ml in move_lines:
                        picking = ml.picking_id

                        # 🔧 Incluir TODAS las move.lines, incluso con quantity = 0
                        if picking.id not in pickings_info:
                            pickings_info[picking.id] = {
                                "picking_id": picking.id,  # 🆕 Agregar ID
                                "nombre": picking.name,
                                "tipo": (picking.picking_type_id.name if picking.picking_type_id else "Desconocido"),
                                "estado": picking.state,
                                "cantidad_reservada": 0,
                            }

                        # 🔧 Sumar quantity (puede ser 0)
                        pickings_info[picking.id]["cantidad_reservada"] += ml.quantity

                    return list(pickings_info.values())

                # def validar_stock_disponible(
                #     product_id, location_id, cantidad_requerida, lote_id=None, transferencia_id=None
                # ):
                #     """
                #     Valida el stock real disponible con información detallada
                #     """
                #     domain = [
                #         ("product_id", "=", product_id),
                #         ("location_id", "=", location_id),
                #         ("quantity", ">", 0),
                #     ]
                #     if lote_id:
                #         domain.append(("lot_id", "=", lote_id))

                #     quants = request.env["stock.quant"].sudo().search(domain)

                #     stock_disponible = 0
                #     stock_total = 0
                #     stock_reservado_otras_transferencias = 0
                #     stock_reservado_esta_transferencia = 0
                #     quants_con_problemas = []

                #     for quant in quants:
                #         stock_total += quant.quantity

                #         if quant.reserved_quantity < 0:
                #             quants_con_problemas.append(
                #                 {"quant_id": quant.id, "reserved_quantity": quant.reserved_quantity}
                #             )
                #             reserved_qty = 0
                #         else:
                #             reserved_qty = quant.reserved_quantity

                #         reservado_esta_transferencia = 0
                #         reservado_otras = reserved_qty

                #         if transferencia_id and reserved_qty > 0:
                #             move_lines_esta_transferencia = (
                #                 request.env["stock.move.line"]
                #                 .sudo()
                #                 .search(
                #                     [
                #                         ("picking_id", "=", transferencia_id),
                #                         ("product_id", "=", product_id),
                #                         ("location_id", "=", location_id),
                #                         (("lot_id", "=", lote_id) if lote_id else ("lot_id", "=", False)),
                #                         ("is_done_item", "=", False),
                #                     ]
                #                 )
                #             )

                #             cantidad_reservada_esta_transferencia = sum(
                #                 ml.quantity for ml in move_lines_esta_transferencia
                #             )
                #             reservado_esta_transferencia = min(
                #                 cantidad_reservada_esta_transferencia, reserved_qty
                #             )
                #             reservado_otras = max(0, reserved_qty - reservado_esta_transferencia)

                #         stock_reservado_esta_transferencia += reservado_esta_transferencia
                #         stock_reservado_otras_transferencias += reservado_otras

                #         disponible_quant = max(0, quant.quantity - reservado_otras)
                #         stock_disponible += disponible_quant

                #     return {
                #         "stock_disponible": stock_disponible,
                #         "stock_total": stock_total,
                #         "stock_reservado_otras": stock_reservado_otras_transferencias,
                #         "stock_reservado_esta": stock_reservado_esta_transferencia,
                #         "stock_reservado_total": stock_reservado_otras_transferencias
                #         + stock_reservado_esta_transferencia,
                #         "es_suficiente": stock_disponible >= cantidad_requerida,
                #         "quants_con_problemas": quants_con_problemas,
                #     }

                # def validar_stock_disponible(
                #     product_id, location_id, cantidad_requerida, lote_id=None, transferencia_id=None
                # ):
                #     """
                #     Valida el stock real disponible con información detallada
                #     """
                #     # 🔧 CAMBIO 1: Buscar quants incluso con quantity = 0 si tienen reservas
                #     domain = [
                #         ("product_id", "=", product_id),
                #         ("location_id", "=", location_id),
                #         "|",
                #         ("quantity", ">", 0),  # Con stock físico
                #         ("reserved_quantity", ">", 0),  # O con reservas (aunque quantity = 0)
                #     ]
                #     if lote_id:
                #         domain.append(("lot_id", "=", lote_id))

                #     quants = request.env["stock.quant"].sudo().search(domain)

                #     stock_disponible = 0
                #     stock_total = 0
                #     stock_reservado_otras_transferencias = 0
                #     stock_reservado_esta_transferencia = 0
                #     quants_con_problemas = []

                #     # 🔧 CAMBIO 2: Inicializar variables para el caso sin quants
                #     tiene_quants = len(quants) > 0

                #     for quant in quants:
                #         stock_total += quant.quantity  # Puede ser 0

                #         if quant.reserved_quantity < 0:
                #             quants_con_problemas.append(
                #                 {"quant_id": quant.id, "reserved_quantity": quant.reserved_quantity}
                #             )
                #             reserved_qty = 0
                #         else:
                #             reserved_qty = quant.reserved_quantity

                #         reservado_esta_transferencia = 0
                #         reservado_otras = reserved_qty

                #         if transferencia_id and reserved_qty > 0:
                #             move_lines_esta_transferencia = (
                #                 request.env["stock.move.line"]
                #                 .sudo()
                #                 .search(
                #                     [
                #                         ("picking_id", "=", transferencia_id),
                #                         ("product_id", "=", product_id),
                #                         ("location_id", "=", location_id),
                #                         (("lot_id", "=", lote_id) if lote_id else ("lot_id", "=", False)),
                #                         ("is_done_item", "=", False),
                #                     ]
                #                 )
                #             )

                #             cantidad_reservada_esta_transferencia = sum(
                #                 ml.quantity for ml in move_lines_esta_transferencia
                #             )
                #             reservado_esta_transferencia = min(
                #                 cantidad_reservada_esta_transferencia, reserved_qty
                #             )
                #             reservado_otras = max(0, reserved_qty - reservado_esta_transferencia)

                #         stock_reservado_esta_transferencia += reservado_esta_transferencia
                #         stock_reservado_otras_transferencias += reservado_otras

                #         # Stock disponible = cantidad física - reservas de otras
                #         disponible_quant = max(0, quant.quantity - reservado_otras)
                #         stock_disponible += disponible_quant

                #     # 🔧 CAMBIO 3: Si NO hay quants pero SÍ hay move.lines, buscarlas
                #     if not tiene_quants and transferencia_id:
                #         move_lines_esta = (
                #             request.env["stock.move.line"]
                #             .sudo()
                #             .search(
                #                 [
                #                     ("picking_id", "=", transferencia_id),
                #                     ("product_id", "=", product_id),
                #                     ("location_id", "=", location_id),
                #                     (("lot_id", "=", lote_id) if lote_id else ("lot_id", "=", False)),
                #                     ("is_done_item", "=", False),
                #                 ]
                #             )
                #         )

                #         if move_lines_esta:
                #             # Hay reservas en move.lines aunque no haya quants
                #             cantidad_reservada = sum(ml.quantity for ml in move_lines_esta)
                #             stock_reservado_esta_transferencia = cantidad_reservada

                #     return {
                #         "stock_disponible": stock_disponible,
                #         "stock_total": stock_total,
                #         "stock_reservado_otras": stock_reservado_otras_transferencias,
                #         "stock_reservado_esta": stock_reservado_esta_transferencia,
                #         "stock_reservado_total": stock_reservado_otras_transferencias
                #         + stock_reservado_esta_transferencia,
                #         "es_suficiente": stock_disponible >= cantidad_requerida,
                #         "quants_con_problemas": quants_con_problemas,
                #     }

                def validar_stock_disponible(
                    product_id,
                    location_id,
                    cantidad_requerida,
                    lote_id=None,
                    transferencia_id=None,
                ):
                    """
                    Valida el stock real disponible.
                    Calcula las reservas desde move.lines en lugar de confiar en el quant.
                    """
                    # 🔧 Buscar TODOS los quants (incluso negativos)
                    domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                    ]
                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    quants = request.env["stock.quant"].sudo().search(domain)

                    stock_total = 0
                    quants_con_problemas = []

                    for quant in quants:
                        stock_total += quant.quantity  # Puede ser negativo

                        if quant.reserved_quantity < 0:
                            quants_con_problemas.append(
                                {
                                    "quant_id": quant.id,
                                    "reserved_quantity": quant.reserved_quantity,
                                }
                            )

                    # 🆕 CALCULAR RESERVAS DESDE MOVE.LINES (NO desde quant)
                    # Buscar TODAS las move.lines pendientes (no solo del picking actual)
                    domain_all_moves = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        (
                            "picking_id.state",
                            "in",
                            ["assigned", "confirmed", "waiting", "partially_available"],
                        ),
                        # ("is_done_item", "=", False),
                    ]
                    if lote_id:
                        domain_all_moves.append(("lot_id", "=", lote_id))

                    todas_move_lines = request.env["stock.move.line"].sudo().search(domain_all_moves)

                    # Separar: este documento vs otros
                    stock_reservado_esta_transferencia = 0
                    stock_reservado_otras_transferencias = 0

                    for ml in todas_move_lines:
                        if transferencia_id and ml.picking_id.id == transferencia_id:
                            # Es de ESTE documento
                            stock_reservado_esta_transferencia += ml.quantity
                        else:
                            # Es de OTROS documentos
                            stock_reservado_otras_transferencias += ml.quantity

                    # Caso especial: si no hay quants pero sí hay move.lines del documento actual
                    if len(quants) == 0 and transferencia_id and stock_reservado_esta_transferencia == 0:
                        move_lines_esta = (
                            request.env["stock.move.line"]
                            .sudo()
                            .search(
                                [
                                    ("picking_id", "=", transferencia_id),
                                    ("product_id", "=", product_id),
                                    ("location_id", "=", location_id),
                                    (("lot_id", "=", lote_id) if lote_id else ("lot_id", "=", False)),
                                    ("is_done_item", "=", False),
                                ]
                            )
                        )
                        if move_lines_esta:
                            stock_reservado_esta_transferencia = sum(ml.quantity for ml in move_lines_esta)

                    # Stock disponible = stock físico - reservas de OTRAS
                    stock_disponible = stock_total - stock_reservado_otras_transferencias

                    return {
                        "stock_disponible": stock_disponible,
                        "stock_total": stock_total,
                        "stock_reservado_otras": stock_reservado_otras_transferencias,
                        "stock_reservado_esta": stock_reservado_esta_transferencia,
                        "stock_reservado_total": stock_reservado_otras_transferencias + stock_reservado_esta_transferencia,
                        "es_suficiente": stock_disponible >= cantidad_requerida,
                        "quants_con_problemas": quants_con_problemas,
                    }

                # ===== PROCESAMIENTO =====
                array_result = []
                todas_las_correcciones = []

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
                        return {
                            "code": 404,
                            "msg": f"Movimiento no encontrado (ID: {id_move})",
                        }

                    # ===== CORRECCIÓN DE RESERVAS NEGATIVAS ANTES DE VALIDACIONES =====
                    # correcciones = corregir_reservas_negativas(
                    #     product_id=id_product,
                    #     location_id=original_move.location_id.id,
                    #     lote_id=id_lote if id_lote else None,
                    # )
                    # todas_las_correcciones.extend(correcciones)

                    # VALIDACIONES CRÍTICAS

                    # 1. Verificar si la línea ya fue procesada
                    if original_move.is_done_item:
                        return {
                            "code": 400,
                            "msg": f"La línea {id_move} ya fue procesada anteriormente - {original_move.quantity} unidades {original_move.is_done_item}",
                        }

                    # 2. Verificar que la cantidad enviada sea válida
                    # if cantidad_enviada <= 0:
                    #     return {"code": 400, "msg": "La cantidad enviada debe ser mayor a 0"}

                    # 3. Verificar que no exceda lo reservado en esta línea específica
                    cantidad_reservada = original_move.quantity
                    if cantidad_enviada > cantidad_reservada:
                        return {
                            "code": 400,
                            "msg": f"Cantidad enviada ({cantidad_enviada}) excede la reservada en esta línea ({cantidad_reservada})",
                        }

                    # 4. Validación adicional con stock real disponible
                    validacion_stock = validar_stock_disponible(
                        product_id=id_product,
                        location_id=original_move.location_id.id,
                        cantidad_requerida=cantidad_enviada,
                        lote_id=id_lote if id_lote else None,
                        transferencia_id=id_transferencia,
                    )  # Pasar ID de transferencia

                    if not validacion_stock["es_suficiente"]:
                        ubicacion_origen = original_move.location_id
                        producto_con_error = original_move.product_id
                        lote_info = original_move.lot_id

                        # 🆕 Obtener transferencias con reservas
                        transferencias_con_reservas = obtener_transferencias_con_reservas(
                            product_id=id_product,
                            location_id=original_move.location_id.id,
                            lote_id=id_lote if id_lote else None,
                        )

                        # 🎨 MENSAJE FORMATEADO EXACTAMENTE COMO LO PEDISTE
                        sku_lote = f"SKU/Lote: {producto_con_error.default_code or 'N/A'}"
                        if lote_info:
                            sku_lote += f" / {lote_info.name}"

                        mensaje_error = f"{sku_lote}\n"

                        # Línea 2: Descripción
                        mensaje_error += f"Descripción: {producto_con_error.name}\n"

                        # Línea 3: Ubicación
                        mensaje_error += f"Ubicación: {ubicacion_origen.complete_name or ubicacion_origen.display_name}\n"

                        # Estado de Stock
                        mensaje_error += f"Estado de Stock  (Req. {cantidad_enviada})\n\n"

                        mensaje_error += f"Disponible: {validacion_stock['stock_disponible']}\n"
                        mensaje_error += f"* Inventario Teórico: {validacion_stock['stock_total']}\n"

                        # Reserva Total
                        reserva_total = validacion_stock["stock_reservado_esta"] + validacion_stock["stock_reservado_otras"]
                        mensaje_error += f"* Reserva Total: {reserva_total}\n"

                        # Documentos con reserva
                        if transferencias_con_reservas:
                            mensaje_error += "Documentos con reserva:\n"
                            for tf in transferencias_con_reservas:
                                # Extraer solo el número del picking (ej: "BOG/PICK/00804" -> "PICK/00804")
                                nombre_corto = tf["nombre"].split("/")[-2:] if "/" in tf["nombre"] else [tf["nombre"]]
                                nombre_corto = "/".join(nombre_corto)
                                mensaje_error += f"* {nombre_corto} {tf['cantidad_reservada']}\n"

                        # Acción Requerida
                        mensaje_error += (
                            "\nAcción Requerida:\n"
                            "* Validar Físico (contra 360WMS)\n"
                            "* Abastecer/Trasladar a la ubicación indicada.\n"
                            "* Anular reservas otros documentos  (si aplica).\n"
                        )

                        return {
                            "code": 409,
                            "tipo": "STOCK_INSUFICIENTE",
                            "msg": mensaje_error,
                            # "correcciones_realizadas": correcciones,
                        }

                    # 5. Verificar que no se exceda el total del move padre
                    move_parent = original_move.move_id
                    lineas_procesadas = (
                        request.env["stock.move.line"]
                        .sudo()
                        .search(
                            [
                                ("move_id", "=", move_parent.id),
                                ("is_done_item", "=", True),
                                ("id", "!=", original_move.id),
                            ]
                        )
                    )

                    cantidad_ya_procesada = sum(linea.quantity for linea in lineas_procesadas)
                    cantidad_total_comprometida = move_parent.product_uom_qty
                    cantidad_disponible_total = cantidad_total_comprometida - cantidad_ya_procesada

                    if cantidad_enviada > cantidad_disponible_total:
                        return {
                            "code": 400,
                            "msg": (
                                f"No se puede procesar {cantidad_enviada} unidades. "
                                f"Solo quedan {cantidad_disponible_total} unidades disponibles del total comprometido ({cantidad_total_comprometida}). "
                                f"Ya se han procesado {cantidad_ya_procesada} unidades."
                            ),
                        }

                    # 6. Verificar producto y tracking
                    stock_move = original_move.move_id
                    move_parent = original_move.move_id
                    product = request.env["product.product"].sudo().search([("id", "=", id_product)])

                    if product.tracking == "lot" and not id_lote:
                        return {
                            "code": 400,
                            "msg": "El producto requiere lote y no se ha proporcionado uno",
                        }

                    # 7. Procesar fecha
                    fecha = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)

                    # 8. Procesar novedad
                    noveda_minuscula = novedad.lower()
                    if "pendiente" in noveda_minuscula or "pendientes" in noveda_minuscula:
                        dividida = False

                    if not novedad:
                        novedad = "Sin novedad"

                    # LÓGICA DE PROCESAMIENTO

                    if dividida:
                        update_values = {
                            "quantity": cantidad_enviada,
                            "location_dest_id": id_ubicacion_destino,
                            "location_id": original_move.location_id.id,
                            "lot_id": id_lote if id_lote else False,
                            "is_done_item": True,
                            "date_transaction": fecha,
                            "new_observation": novedad,
                            "time": time_line,
                            "user_operator_id": id_operario,
                        }

                        # Actualizar línea original con cantidad enviada
                        original_move.sudo().write(update_values)

                        # Calcular cantidad restante
                        cantidad_restante = cantidad_reservada - cantidad_enviada

                        if cantidad_restante > 0:
                            # Verificar si ya existe una línea pendiente para este move
                            linea_existente = (
                                request.env["stock.move.line"]
                                .sudo()
                                .search(
                                    [
                                        ("move_id", "=", move_parent.id),
                                        ("product_id", "=", id_product),
                                        ("is_done_item", "=", False),
                                        (
                                            "location_id",
                                            "=",
                                            original_move.location_id.id,
                                        ),
                                    ],
                                    limit=1,
                                )
                            )

                            if linea_existente:
                                # Actualizar línea existente
                                linea_existente.sudo().write({"quantity": cantidad_restante})
                                linea_restante = linea_existente
                            else:
                                # Crear nueva línea solo si no existe
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
                        # Cuando no se divide, simplemente procesar la cantidad enviada
                        # y cerrar la línea (sin crear pendientes)

                        update_values = {
                            "quantity": cantidad_enviada,
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
                                "user_operator_id": (original_move.user_operator_id.id if original_move.user_operator_id else None),
                            }
                        )

                    # Corrección final después de procesar cada item
                    # correcciones_finales = corregir_reservas_negativas(
                    #     product_id=id_product,
                    #     location_id=original_move.location_id.id,
                    #     lote_id=id_lote if id_lote else None,
                    # )
                    # todas_las_correcciones.extend(correcciones_finales)

                # Mensaje sobre correcciones realizadas
                mensaje_correcciones = ""
                if todas_las_correcciones:
                    mensaje_correcciones = f" Se realizaron {len(todas_las_correcciones)} correcciones de reservas negativas."

                return {
                    "code": 200,
                    "result": array_result,
                    # "correcciones_realizadas": todas_las_correcciones,
                    # "total_correcciones": len(todas_las_correcciones),
                    # "mensaje": f"Procesamiento completado exitosamente{mensaje_correcciones}",
                }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Enviar cantidad de producto en transferencia - PACK
    @http.route("/api/send_transfer/pack", auth="user", type="json", methods=["POST"], csrf=False)
    def send_transfer_pack(self, **auth):
        try:
            # Usar transacción para garantizar consistencia
            with request.env.cr.savepoint():
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

                # ===== FUNCIONES AUXILIARES PARA CORRECCIÓN DE RESERVAS =====
                def corregir_reservas_negativas(product_id, location_id, lote_id=None):
                    """
                    Corrige las reservas negativas antes de validar stock
                    """
                    domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        ("reserved_quantity", "<", 0),
                    ]

                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    # Buscar quants con reservas negativas
                    negative_quants = request.env["stock.quant"].sudo().search(domain)

                    correcciones_realizadas = []
                    for quant in negative_quants:
                        valor_anterior = quant.reserved_quantity
                        # Establecer reserva a 0 si es negativa
                        quant.write({"reserved_quantity": 0})
                        correcciones_realizadas.append(
                            {
                                "quant_id": quant.id,
                                "valor_anterior": valor_anterior,
                                "valor_nuevo": 0,
                                "product_id": product_id,
                                "location_id": location_id,
                            }
                        )

                    return correcciones_realizadas

                def validar_stock_disponible(
                    product_id,
                    location_id,
                    cantidad_requerida,
                    lote_id=None,
                    transferencia_id=None,
                ):
                    """
                    Valida el stock real disponible en una ubicación específica
                    considerando las reservas de la misma transferencia como disponibles
                    """
                    domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        ("quantity", ">", 0),
                    ]

                    if lote_id:
                        domain.append(("lot_id", "=", lote_id))

                    quants = request.env["stock.quant"].sudo().search(domain)

                    stock_disponible = 0
                    stock_total = 0
                    stock_reservado_otras_transferencias = 0
                    stock_reservado_esta_transferencia = 0
                    quants_con_problemas = []

                    for quant in quants:
                        stock_total += quant.quantity

                        # Validar que reserved_quantity no sea negativo
                        if quant.reserved_quantity < 0:
                            quants_con_problemas.append(
                                {
                                    "quant_id": quant.id,
                                    "reserved_quantity": quant.reserved_quantity,
                                }
                            )
                            reserved_qty = 0
                        else:
                            reserved_qty = quant.reserved_quantity

                        # Calcular cuánto está reservado para esta transferencia vs otras
                        reservado_esta_transferencia = 0
                        reservado_otras = reserved_qty

                        if transferencia_id and reserved_qty > 0:
                            # Buscar move lines de esta transferencia que usen este quant
                            move_lines_esta_transferencia = (
                                request.env["stock.move.line"]
                                .sudo()
                                .search(
                                    [
                                        ("picking_id", "=", transferencia_id),
                                        ("product_id", "=", product_id),
                                        ("location_id", "=", location_id),
                                        (("lot_id", "=", lote_id) if lote_id else ("lot_id", "=", False)),
                                        ("is_done_item", "=", False),
                                    ]
                                )  # Solo líneas no procesadas
                            )

                            # Calcular cuánto de la reserva pertenece a esta transferencia
                            cantidad_reservada_esta_transferencia = sum(ml.quantity for ml in move_lines_esta_transferencia)
                            reservado_esta_transferencia = min(cantidad_reservada_esta_transferencia, reserved_qty)
                            reservado_otras = max(0, reserved_qty - reservado_esta_transferencia)

                        stock_reservado_esta_transferencia += reservado_esta_transferencia
                        stock_reservado_otras_transferencias += reservado_otras

                        # Stock disponible = cantidad total - reservas de OTRAS transferencias
                        # (las reservas de esta transferencia se pueden usar)
                        disponible_quant = max(0, quant.quantity - reservado_otras)
                        stock_disponible += disponible_quant

                    return {
                        "stock_disponible": stock_disponible,
                        "stock_total": stock_total,
                        "stock_reservado_otras": stock_reservado_otras_transferencias,
                        "stock_reservado_esta": stock_reservado_esta_transferencia,
                        "stock_reservado_total": stock_reservado_otras_transferencias + stock_reservado_esta_transferencia,
                        "es_suficiente": stock_disponible >= cantidad_requerida,
                        "quants_con_problemas": quants_con_problemas,
                    }

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
                nuevas_lineas_creadas = []
                todas_las_correcciones = []

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
                        return {
                            "code": 404,
                            "msg": f"Movimiento no encontrado (ID: {id_move})",
                        }

                    # ===== CORRECCIÓN DE RESERVAS NEGATIVAS ANTES DE VALIDACIONES =====
                    correcciones = corregir_reservas_negativas(
                        product_id=id_product,
                        location_id=id_ubicacion_origen,
                        lote_id=id_lote if id_lote else None,
                    )
                    todas_las_correcciones.extend(correcciones)

                    # VALIDACIONES CRÍTICAS

                    # 1. Verificar que la cantidad enviada sea válida
                    if cantidad_enviada <= 0:
                        return {
                            "code": 400,
                            "msg": "La cantidad enviada debe ser mayor a 0",
                        }

                    # 2. Validación de stock disponible
                    validacion_stock = validar_stock_disponible(
                        product_id=id_product,
                        location_id=id_ubicacion_origen,
                        cantidad_requerida=cantidad_enviada,
                        lote_id=id_lote if id_lote else None,
                        transferencia_id=id_transferencia,
                    )  # Pasar ID de transferencia

                    if not validacion_stock["es_suficiente"]:
                        ubicacion_origen = request.env["stock.location"].sudo().browse(id_ubicacion_origen)
                        producto_con_error = original_move.product_id
                        return {
                            "code": 400,
                            "msg": (
                                f"Stock insuficiente para el producto {producto_con_error.display_name}, "
                                f"en ubicación {ubicacion_origen.display_name}. "
                                f"Solicitado: {cantidad_enviada}, Disponible: {validacion_stock['stock_disponible']} "
                                f"(Total: {validacion_stock['stock_total']}, "
                                f"Reservado para esta transferencia: {validacion_stock['stock_reservado_esta']}, "
                                f"Reservado para otras: {validacion_stock['stock_reservado_otras']})"
                            ),
                            "correcciones_realizadas": correcciones,
                        }

                    # 3. Verificar que no exceda la cantidad reservada en la línea original
                    cantidad_reservada = original_move.quantity
                    if cantidad_enviada > cantidad_reservada:
                        return {
                            "code": 400,
                            "msg": f"Cantidad enviada ({cantidad_enviada}) excede la reservada en esta línea ({cantidad_reservada})",
                        }

                    product = request.env["product.product"].sudo().search([("id", "=", id_product)])

                    if product.tracking == "lot" and not id_lote:
                        return {
                            "code": 400,
                            "msg": "El producto requiere lote y no se ha proporcionado uno",
                        }

                    fecha = procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)

                    novedad_minuscula = novedad.lower()
                    if novedad_minuscula != "sin novedad":
                        dividida = True

                    # LÓGICA DE PROCESAMIENTO

                    if dividida:
                        cantidad_original = original_move.quantity
                        cantidad_separada = cantidad_enviada
                        cantidad_restante = cantidad_original - cantidad_separada

                        if cantidad_restante > 0:
                            # ✅ 1. Restar a la original (mantener la línea original con cantidad restante)
                            original_move.write({"quantity": cantidad_restante})

                            # ✅ 2. Copiar la línea original
                            new_line_vals = original_move.copy_data()[0]

                            # ✅ 3. Actualizar los datos ANTES de crearla
                            new_line_vals.update(
                                {
                                    "quantity": cantidad_separada,
                                    "location_dest_id": id_ubicacion_destino,
                                    "location_id": id_ubicacion_origen,
                                    "lot_id": id_lote if id_lote else False,
                                    "result_package_id": pack.id,
                                    "new_observation": novedad,
                                    "user_operator_id": id_operario,
                                    "date_transaction": fecha,
                                    "time": time_line,
                                    "is_done_item": True,
                                }
                            )

                            # ✅ 4. Crear la línea nueva con los valores actualizados
                            new_line = request.env["stock.move.line"].sudo().create(new_line_vals)

                            # ✅ Guardar información básica de la nueva línea (sin consecutivo aún)
                            nuevas_lineas_creadas.append(
                                {
                                    "id_move_original": id_move,  # ID de la línea que se mantuvo con cantidad restante
                                    "id_move_procesada": new_line.id,  # ID de la nueva línea creada y procesada
                                    "id_transferencia": id_transferencia,
                                    "id_product": new_line.product_id.id,
                                    "product_name": new_line.product_id.name,
                                    "cantidad_procesada": new_line.quantity,
                                    "cantidad_restante_original": cantidad_restante,
                                    "location_id": new_line.location_id.id,
                                    "location_dest_id": new_line.location_dest_id.id,
                                    "lot_id": new_line.lot_id.id if new_line.lot_id else False,
                                    "lot_name": new_line.lot_id.name if new_line.lot_id else "",
                                    "is_done_item": new_line.is_done_item,
                                    "observacion": novedad,
                                    "operario_id": id_operario,
                                    "fecha_transaccion": fecha_transaccion,
                                    "time_line": time_line,
                                    "new_line_obj": new_line,  # ✅ Guardamos el objeto para obtener el consecutivo después
                                }
                            )

                        else:
                            # Si no hay cantidad restante, procesar la línea original completa
                            update_values = {
                                "quantity": cantidad_enviada,
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

                    else:
                        # Procesar sin división

                        update_values = {
                            "quantity": cantidad_enviada,
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

                    # Corrección final después de procesar cada item
                    correcciones_finales = corregir_reservas_negativas(
                        product_id=id_product,
                        location_id=id_ubicacion_origen,
                        lote_id=id_lote if id_lote else None,
                    )
                    todas_las_correcciones.extend(correcciones_finales)

                # ✅ ALTERNATIVA SIMPLE: Llamar explícitamente action_generate_box_numbers
                # para asegurar que todas las líneas tengan su faber_box_number
                transferencia.action_generate_box_numbers()

                # ✅ Obtener el consecutivo después de generar los números de caja
                consecutivo = "Caja1"  # valor por defecto
                primera_linea_del_paquete = transferencia.move_line_ids.filtered(lambda l: l.result_package_id and l.result_package_id.id == pack.id)
                if primera_linea_del_paquete:
                    consecutivo = primera_linea_del_paquete[0].faber_box_number or "Caja1"

                # ✅ Actualizar el consecutivo en las nuevas líneas creadas
                for nueva_linea in nuevas_lineas_creadas:
                    if "new_line_obj" in nueva_linea:
                        # Obtener el faber_box_number de la línea creada
                        line_obj = nueva_linea["new_line_obj"]
                        nueva_linea["consecutivo"] = line_obj.faber_box_number or consecutivo
                        # Remover el objeto de la respuesta
                        del nueva_linea["new_line_obj"]
                    else:
                        nueva_linea["consecutivo"] = consecutivo

                # Mensaje sobre correcciones realizadas
                mensaje_correcciones = ""
                if todas_las_correcciones:
                    mensaje_correcciones = f" Se realizaron {len(todas_las_correcciones)} correcciones de reservas negativas."

                array_result.append(
                    {
                        "id_paquete": pack.id,
                        "name_paquete": pack.name,
                        "id_batch": id_transferencia,
                        "cantidad_productos_en_el_paquete": len(list_items),
                        "is_sticker": is_sticker,
                        "is_certificate": is_certificate,
                        "peso": peso_total_paquete,
                        "consecutivo": consecutivo,
                        "list_item": list_items,
                        # "nuevas_lineas_division": nuevas_lineas_creadas,
                    }
                )

                return {
                    "code": 200,
                    "result": array_result,
                    "correcciones_realizadas": todas_las_correcciones,
                    "total_correcciones": len(todas_las_correcciones),
                    "mensaje": f"Paquete creado exitosamente{mensaje_correcciones}",
                }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    # Eliminar linea en transferencias
    @http.route(
        "/api/transferencias/delete_line",
        auth="user",
        type="json",
        methods=["POST"],
        csrf=False,
    )
    def eliminar_linea_transferencia(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_linea = auth.get("id_linea", 0)
            if not id_linea:
                return {"code": 400, "msg": "ID de línea es requerido"}

            # Buscamos la línea en stock.move.line
            linea = request.env["stock.move.line"].sudo().browse(id_linea)
            if not linea.exists():
                return {"code": 404, "msg": f"Línea con ID {id_linea} no encontrada"}

            # Obtenemos el stock.move de la línea detallada
            stock_move = linea.move_id
            if not stock_move.exists():
                return {"code": 404, "msg": "Movimiento de stock no encontrado"}

            # Obtenemos la cantidad demandada
            cantidad_demandada = stock_move.product_uom_qty

            # Buscamos todas las líneas realizadas asociadas a este stock.move (excluyendo la línea actual)
            lineas_asociadas_realizadas = (
                request.env["stock.move.line"]
                .sudo()
                .search(
                    [
                        ("move_id", "=", stock_move.id),
                        ("is_done_item", "=", True),
                        ("id", "!=", linea.id),
                    ]
                )
            )  # Excluimos la línea actual
            cantidad_total_realizada_otras = sum(linea_item.quantity for linea_item in lineas_asociadas_realizadas)

            # Buscamos todas las líneas pendientes asociadas a este stock.move (excluyendo la línea actual)
            lineas_asociadas_pendientes = (
                request.env["stock.move.line"]
                .sudo()
                .search(
                    [
                        ("move_id", "=", stock_move.id),
                        ("is_done_item", "=", False),
                        ("id", "!=", linea.id),
                    ]
                )
            )  # Excluimos la línea actual
            cantidad_total_pendientes_otras = sum(linea_item.quantity for linea_item in lineas_asociadas_pendientes)

            # Calculamos las cantidades actuales (incluyendo la línea a limpiar)
            cantidad_actual_linea = linea.quantity
            es_linea_realizada = linea.is_done_item

            if es_linea_realizada:
                cantidad_total_realizada_con_actual = cantidad_total_realizada_otras + cantidad_actual_linea
                cantidad_total_pendientes_con_actual = cantidad_total_pendientes_otras
            else:
                cantidad_total_realizada_con_actual = cantidad_total_realizada_otras
                cantidad_total_pendientes_con_actual = cantidad_total_pendientes_otras + cantidad_actual_linea

            # LÓGICA DE LIMPIEZA
            nueva_cantidad_linea = 0
            accion_realizada = ""

            # CASO 1: No hay cantidad pendiente (solo realizadas)
            if cantidad_total_pendientes_otras == 0:
                # La cantidad pendiente sería: demandada - (realizadas sin incluir línea actual)
                cantidad_pendiente_calculada = cantidad_demandada - cantidad_total_realizada_otras

                if cantidad_pendiente_calculada > 0:
                    # Hay cantidad pendiente calculada, asignamos esa cantidad a la línea
                    nueva_cantidad_linea = min(cantidad_pendiente_calculada, cantidad_demandada)
                    accion_realizada = f"Asignada cantidad pendiente calculada: {nueva_cantidad_linea}"
                else:
                    # No hay cantidad pendiente, ponemos en 0
                    nueva_cantidad_linea = 0
                    accion_realizada = "Línea puesta en 0 - no hay cantidad pendiente"

            # CASO 2: Hay cantidad pendiente y cantidad realizada
            else:
                # Calculamos cuánto podemos asignar sin superar la demanda total
                total_otras_lineas = cantidad_total_realizada_otras + cantidad_total_pendientes_otras
                cantidad_disponible_para_linea = cantidad_demandada - total_otras_lineas

                if cantidad_disponible_para_linea > 0:
                    nueva_cantidad_linea = cantidad_disponible_para_linea
                    accion_realizada = f"Asignada cantidad disponible: {nueva_cantidad_linea}"
                else:
                    nueva_cantidad_linea = 0
                    accion_realizada = "Línea puesta en 0 - otras líneas cubren la demanda"

            # Aplicamos la limpieza a la línea
            linea.write(
                {
                    "quantity": nueva_cantidad_linea,
                    "is_done_item": False,
                    "date_transaction": False,
                    "new_observation": False,
                    "time": False,
                    "user_operator_id": False,
                }
            )  # La ponemos como pendiente

            # Preparamos la respuesta con información detallada
            data = {
                "id": linea.id,
                "id_move": linea.id,
                "id_transferencia": linea.picking_id.id or "",
                "product_id": linea.product_id.id or 0,
                "product_name": linea.product_id.name or "",
                "product_code": linea.product_id.default_code or "",
                "product_barcode": linea.product_id.barcode or "",
                "orden_name": linea.picking_id.name,
                "location_dest_id": linea.location_dest_id.id or 0,
                "location_dest_name": linea.location_dest_id.name or 0,
                "location_dest_barcode": linea.location_dest_id.barcode or 0,
                "location_id": linea.location_id.id or 0,
                "location_name": linea.location_id.name or 0,
                "location_barcode": linea.location_id.barcode or 0,
                "quantity_ordered": cantidad_demandada,
                "quantity_to_transfer": cantidad_demandada,
                "cantidad_faltante": linea.quantity,
                "cantidad_demandada": cantidad_demandada,
                # "cantidad_linea_original": cantidad_actual_linea,
                # "cantidad_linea_nueva": nueva_cantidad_linea,
                # "era_realizada": es_linea_realizada,
                # "cantidad_total_realizada_otras": cantidad_total_realizada_otras,
                # "cantidad_total_pendientes_otras": cantidad_total_pendientes_otras,
                # "cantidad_faltante_calculada": cantidad_demandada - (cantidad_total_realizada_otras + cantidad_total_pendientes_otras),
                # "accion_realizada": accion_realizada,
                # "validacion_suma": {
                #     "suma_final": cantidad_total_realizada_otras + cantidad_total_pendientes_otras + nueva_cantidad_linea,
                #     "suma_correcta": (cantidad_total_realizada_otras + cantidad_total_pendientes_otras + nueva_cantidad_linea) <= cantidad_demandada,
                #     "diferencia": cantidad_demandada - (cantidad_total_realizada_otras + cantidad_total_pendientes_otras + nueva_cantidad_linea),
                # },
            }

            return {"code": 200, "result": data}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    ## POST Crear Devolución Manual - DEV
    @http.route("/api/crear_devs", auth="user", type="json", methods=["POST"], csrf=False)
    def crear_dev_manual(self, **auth):
        try:
            # --- INICIO: Lógica de Validación de Versión ---
            version_app = auth.get("version_app")

            # 1. Llama al método que obtiene la última versión
            response_version = self.get_last_version()

            # 2. Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # 3. Compara las versiones
            update_required = False
            if version_app:
                try:
                    app_parts = list(map(int, version_app.split(".")))
                    latest_parts = list(map(int, latest_version_str.split(".")))
                    if app_parts < latest_parts:
                        update_required = True
                except (ValueError, TypeError):
                    # Si la versión no tiene el formato correcto, se asume que requiere actualización
                    update_required = True
            else:
                # Si no se envía la versión, requiere actualización
                update_required = True

            user = request.env.user

            if not user:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

            # Parámetros
            id_almacen = auth.get("id_almacen", 0)
            id_proveedor = auth.get("id_proveedor", 0)
            id_ubicacion_destino = auth.get("id_ubicacion_destino", 0)
            id_responsable = auth.get("id_responsable", 0)
            fecha_inicio = auth.get("fecha_inicio", "")
            fecha_fin = auth.get("fecha_fin", "")
            list_items = auth.get("list_items", [])

            # LÓGICA MEJORADA PARA UBICACIÓN DESTINO
            config_returns = request.env["config.returns.general"].sudo().search([], limit=1)
            # Validaciones básicas
            if not id_almacen and config_returns.location_option == "dynamic":
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "ID de almacén es requerido",
                }

            if id_proveedor:
                proveedor = request.env["res.partner"].sudo().browse(id_proveedor)
                if not proveedor.exists():
                    return {
                        "code": 404,
                        "update_version": update_required,
                        "msg": f"Proveedor con ID {id_proveedor} no encontrado",
                    }

            if config_returns and config_returns.location_option == "predefined":
                warehouse_id = user.allowed_warehouse_ids

                id_almacen = id_almacen or (warehouse_id and warehouse_id[0].id)  # Usar el primer almacén permitido si no se especifica

                # Caso 1: Predefined - usar ubicación destino del tipo de operación DEV
                picking_type = (
                    request.env["stock.picking.type"]
                    .sudo()
                    .search(
                        [
                            ("warehouse_id", "=", id_almacen),
                            ("sequence_code", "=", "DEV"),
                        ],
                        limit=1,
                    )
                )

                if not picking_type:
                    return {
                        "code": 404,
                        "update_version": update_required,
                        "msg": "Tipo de picking DEV no encontrado",
                    }

                if picking_type.default_location_dest_id:
                    id_ubicacion_destino = picking_type.default_location_dest_id.id
                else:
                    # Fallback: ubicación de entrada del almacén
                    warehouse = request.env["stock.warehouse"].sudo().browse(id_almacen)
                    if warehouse.exists() and warehouse.wh_input_stock_loc_id:
                        id_ubicacion_destino = warehouse.wh_input_stock_loc_id.id
                    else:
                        return {
                            "code": 400,
                            "update_version": update_required,
                            "msg": "No se pudo determinar ubicación destino predefinida",
                        }

            elif config_returns and config_returns.location_option == "dynamic":
                # Caso 2: Dynamic - REQUIERE parámetro enviado
                if not id_ubicacion_destino:
                    return {
                        "code": 400,
                        "update_version": update_required,
                        "msg": "Ubicación destino es requerida para devoluciones dinámicas",
                    }
                # Si viene id_ubicacion_destino, se usa tal como está

            else:
                # Caso 3: Sin configuración - usar tipo de operación DEV como fallback
                if not id_ubicacion_destino:
                    picking_type = (
                        request.env["stock.picking.type"]
                        .sudo()
                        .search(
                            [
                                ("warehouse_id", "=", id_almacen),
                                ("sequence_code", "=", "DEV"),
                            ],
                            limit=1,
                        )
                    )

                    if not picking_type:
                        return {
                            "code": 404,
                            "update_version": update_required,
                            "msg": "Tipo de picking DEV no encontrado",
                        }

                    if picking_type.default_location_dest_id:
                        id_ubicacion_destino = picking_type.default_location_dest_id.id
                    else:
                        warehouse = request.env["stock.warehouse"].sudo().browse(id_almacen)
                        if warehouse.exists() and warehouse.wh_input_stock_loc_id:
                            id_ubicacion_destino = warehouse.wh_input_stock_loc_id.id
                        else:
                            return {
                                "code": 400,
                                "update_version": update_required,
                                "msg": "No se pudo determinar ubicación destino",
                            }

            if not id_ubicacion_destino:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Ubicación destino es requerida",
                }

            if not list_items:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Lista de items no puede estar vacía",
                }

            # Buscar tipo de picking para devoluciones
            picking_type = (
                request.env["stock.picking.type"]
                .sudo()
                .search(
                    [("warehouse_id", "=", id_almacen), ("sequence_code", "=", "DEV")],
                    limit=1,
                )
            )

            if not picking_type:
                return {
                    "code": 404,
                    "update_version": update_required,
                    "msg": "Tipo de picking DEV no encontrado",
                }

            # Obtener ubicación origen
            location_src_id = picking_type.default_location_src_id.id
            if not location_src_id:
                warehouse = request.env["stock.warehouse"].sudo().browse(id_almacen)
                location_src_id = warehouse.lot_stock_id.id if warehouse.exists() else False

            if not location_src_id:
                return {
                    "code": 500,
                    "update_version": update_required,
                    "msg": "No se pudo determinar la ubicación origen",
                }

            # DESACTIVAR temporalmente el módulo delivery para evitar cálculos de peso
            # context_no_delivery = {"skip_weight_computation": True, "no_compute_weight": True, "disable_automatic_weight": True}

            # Crear picking principal CON CONTEXTO que evita cálculos de peso
            picking_vals = {
                "picking_type_id": picking_type.id,
                "location_dest_id": id_ubicacion_destino,
                "user_id": id_responsable,
                "responsable_id": id_responsable,
                "origin": f"Devolución manual creada por {user.name}",
                "start_time_reception": (
                    procesar_fecha_naive(fecha_inicio, "America/Bogota") if fecha_inicio else datetime.now()
                ),  # CORREGIDO: datetime naive
                "end_time_reception": (
                    procesar_fecha_naive(fecha_fin, "America/Bogota") if fecha_fin else datetime.now()
                ),  # CORREGIDO: datetime naive
            }

            if id_proveedor:
                picking_vals["partner_id"] = id_proveedor

            # picking = request.env["stock.picking"].sudo().with_context(**context_no_delivery).create(picking_vals)
            picking = request.env["stock.picking"].sudo().create(picking_vals)

            # Agrupar por producto para cantidad total
            productos_agrupados = {}
            items_individuales = []

            for item in list_items:
                id_producto = item.get("id_producto", 0)
                cantidad = float(item.get("cantidad_enviada", 0))

                if not id_producto or cantidad <= 0:
                    return {
                        "code": 400,
                        "update_version": update_required,
                        "msg": f"Cantidad o producto inválido para producto ID: {id_producto}",
                    }

                product = request.env["product.product"].sudo().browse(id_producto)
                if not product.exists():
                    return {
                        "code": 404,
                        "update_version": update_required,
                        "msg": f"Producto con ID {id_producto} no encontrado",
                    }

                if id_producto in productos_agrupados:
                    productos_agrupados[id_producto]["cantidad_total"] += cantidad
                else:
                    productos_agrupados[id_producto] = {
                        "id_producto": id_producto,
                        "cantidad_total": cantidad,
                        "product": product,
                    }

                items_individuales.append(item)

            # Crear moves CON CONTEXTO que evita cálculos
            moves_creados = {}
            for id_producto, grupo in productos_agrupados.items():
                product = grupo["product"]

                move_vals = {
                    "name": product.display_name,
                    "product_id": product.id,
                    "product_uom_qty": grupo["cantidad_total"],
                    "product_uom": product.uom_id.id,
                    "location_id": location_src_id,
                    "location_dest_id": id_ubicacion_destino,
                    "picking_id": picking.id,
                }

                move = request.env["stock.move"].sudo().create(move_vals)
                moves_creados[id_producto] = move

            # Confirmar picking SIN triggear cálculos de peso
            try:
                picking.action_confirm()
            except Exception as e:
                return {
                    "code": 500,
                    "update_version": update_required,
                    "msg": f"Error al confirmar la devolución: {str(e)}",
                }

            # Eliminar move lines automáticas
            existing_move_lines = request.env["stock.move.line"].sudo().search([("picking_id", "=", picking.id)])
            if existing_move_lines:
                existing_move_lines.unlink()

            # Crear move lines individuales
            move_lines_creadas = []
            items_procesados = []

            for item in items_individuales:
                id_producto = item.get("id_producto", 0)
                id_lote = item.get("id_lote")
                cantidad = float(item.get("cantidad_enviada", 0))
                fecha_transaccion = item.get("fecha_transaccion", "")
                observacion = item.get("observacion", "")
                time_line = item.get("time_line", 0)

                move = moves_creados[id_producto]

                move_line_vals = {
                    "move_id": move.id,
                    "picking_id": picking.id,
                    "product_id": id_producto,
                    "product_uom_id": move.product_uom.id,
                    "location_id": location_src_id,
                    "location_dest_id": id_ubicacion_destino,
                    "quantity": cantidad,
                    "user_operator_id": id_responsable or user.id,
                    "new_observation": observacion,
                    "is_done_item": True,
                    "time": time_line,
                    "date_transaction": (
                        procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now()
                    ),  # CORREGIDO: datetime naive
                }

                if id_lote:
                    lote = request.env["stock.lot"].sudo().browse(id_lote)
                    if lote.exists():
                        move_line_vals["lot_id"] = id_lote

                move_line = request.env["stock.move.line"].sudo().create(move_line_vals)
                move_lines_creadas.append(move_line)

                lote_nombre = ""
                if id_lote:
                    lote = request.env["stock.lot"].sudo().browse(id_lote)
                    lote_nombre = lote.name if lote.exists() else ""

                items_procesados.append(
                    {
                        "id_producto": id_producto,
                        "nombre_producto": move.product_id.display_name,
                        "cantidad": cantidad,
                        "move_id": move.id,
                        "move_line_id": move_line.id,
                        "lote_id": id_lote,
                        "lote_nombre": lote_nombre,
                        "observacion": observacion,
                    }
                )

            # Actualizar quantities manualmente
            for move in picking.move_ids:
                total_qty_done = sum(ml.quantity for ml in move.move_line_ids)
                move.write({"quantity": total_qty_done, "state": "assigned"})

            # Establecer estados manualmente SIN validación automática
            try:
                # NO usar button_validate() para evitar cálculos automáticos
                # validar el picking

                picking.button_validate()

                # CORREGIDO: Usar datetime.now() sin timezone para que sea naive
                # picking.write({"state": "done", "date_done": datetime.now()})

                # Establecer moves como done
                for move in picking.move_ids:
                    move.write({"state": "done"})

            except Exception as e:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": f"Error al finalizar: {str(e)}",
                }

            # Preparar respuesta
            ubicacion_destino = request.env["stock.location"].sudo().browse(id_ubicacion_destino)
            proveedor_info = ""
            if id_proveedor:
                proveedor = request.env["res.partner"].sudo().browse(id_proveedor)
                proveedor_info = proveedor.name if proveedor.exists() else ""

            return {
                "code": 200,
                "update_version": update_required,
                "msg": "Devolución creada y validada correctamente",
                "devolucion_id": picking.id,
                "nombre_devolucion": picking.name,
                "estado": picking.state,
                "fecha_creacion": picking.create_date,
                "almacen_id": id_almacen,
                "ubicacion_destino_id": id_ubicacion_destino,
                "ubicacion_destino_nombre": ubicacion_destino.display_name,
                "responsable_id": id_responsable,
                "proveedor_id": id_proveedor,
                "proveedor_nombre": proveedor_info,
                "id_almacenes": (user.allowed_warehouse_ids[0].id if user.allowed_warehouse_ids else None),
                "name_almacen": (user.allowed_warehouse_ids[0].name if user.allowed_warehouse_ids else None),
                "total_items": len(items_procesados),
                "total_move_lines": len(move_lines_creadas),
                "items_procesados": items_procesados,
            }

        except Exception as err:
            return {
                "code": 500,
                "update_version": update_required,
                "msg": f"Error inesperado: {str(err)}",
            }

    @http.route("/api/crear_devs/v2", auth="user", type="json", methods=["POST"], csrf=False)
    def crear_dev_manual_v2(self, **auth):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = auth.get("device_id") or request.params.get("device_id")

            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            # Parámetros
            id_almacen = auth.get("id_almacen", 0)
            id_proveedor = auth.get("id_proveedor", 0)
            id_ubicacion_destino = auth.get("id_ubicacion_destino", 0)
            id_responsable = auth.get("id_responsable", 0)
            fecha_inicio = auth.get("fecha_inicio", "")
            fecha_fin = auth.get("fecha_fin", "")
            list_items = auth.get("list_items", [])

            # LÓGICA MEJORADA PARA UBICACIÓN DESTINO
            config_returns = request.env["config.returns.general"].sudo().search([], limit=1)
            # Validaciones básicas
            if not id_almacen and config_returns.location_option == "dynamic":
                return {"code": 400, "msg": "ID de almacén es requerido"}

            if id_proveedor:
                proveedor = request.env["res.partner"].sudo().browse(id_proveedor)
                if not proveedor.exists():
                    return {
                        "code": 404,
                        "msg": f"Proveedor con ID {id_proveedor} no encontrado",
                    }

            if config_returns and config_returns.location_option == "predefined":
                warehouse_id = user.allowed_warehouse_ids

                id_almacen = id_almacen or (warehouse_id and warehouse_id[0].id)  # Usar el primer almacén permitido si no se especifica

                # Caso 1: Predefined - usar ubicación destino del tipo de operación DEV
                picking_type = (
                    request.env["stock.picking.type"]
                    .sudo()
                    .search(
                        [
                            ("warehouse_id", "=", id_almacen),
                            ("sequence_code", "=", "DEV"),
                        ],
                        limit=1,
                    )
                )

                if not picking_type:
                    return {"code": 404, "msg": "Tipo de picking DEV no encontrado"}

                if picking_type.default_location_dest_id:
                    id_ubicacion_destino = picking_type.default_location_dest_id.id
                else:
                    # Fallback: ubicación de entrada del almacén
                    warehouse = request.env["stock.warehouse"].sudo().browse(id_almacen)
                    if warehouse.exists() and warehouse.wh_input_stock_loc_id:
                        id_ubicacion_destino = warehouse.wh_input_stock_loc_id.id
                    else:
                        return {
                            "code": 400,
                            "msg": "No se pudo determinar ubicación destino predefinida",
                        }

            elif config_returns and config_returns.location_option == "dynamic":
                # Caso 2: Dynamic - REQUIERE parámetro enviado
                if not id_ubicacion_destino:
                    return {
                        "code": 400,
                        "msg": "Ubicación destino es requerida para devoluciones dinámicas",
                    }
                # Si viene id_ubicacion_destino, se usa tal como está

            else:
                # Caso 3: Sin configuración - usar tipo de operación DEV como fallback
                if not id_ubicacion_destino:
                    picking_type = (
                        request.env["stock.picking.type"]
                        .sudo()
                        .search(
                            [
                                ("warehouse_id", "=", id_almacen),
                                ("sequence_code", "=", "DEV"),
                            ],
                            limit=1,
                        )
                    )

                    if not picking_type:
                        return {"code": 404, "msg": "Tipo de picking DEV no encontrado"}

                    if picking_type.default_location_dest_id:
                        id_ubicacion_destino = picking_type.default_location_dest_id.id
                    else:
                        warehouse = request.env["stock.warehouse"].sudo().browse(id_almacen)
                        if warehouse.exists() and warehouse.wh_input_stock_loc_id:
                            id_ubicacion_destino = warehouse.wh_input_stock_loc_id.id
                        else:
                            return {
                                "code": 400,
                                "msg": "No se pudo determinar ubicación destino",
                            }

            if not id_ubicacion_destino:
                return {"code": 400, "msg": "Ubicación destino es requerida"}

            if not list_items:
                return {"code": 400, "msg": "Lista de items no puede estar vacía"}

            # Buscar tipo de picking para devoluciones
            picking_type = (
                request.env["stock.picking.type"]
                .sudo()
                .search(
                    [("warehouse_id", "=", id_almacen), ("sequence_code", "=", "DEV")],
                    limit=1,
                )
            )

            if not picking_type:
                return {"code": 404, "msg": "Tipo de picking DEV no encontrado"}

            # Obtener ubicación origen
            location_src_id = picking_type.default_location_src_id.id
            if not location_src_id:
                warehouse = request.env["stock.warehouse"].sudo().browse(id_almacen)
                location_src_id = warehouse.lot_stock_id.id if warehouse.exists() else False

            if not location_src_id:
                return {"code": 500, "msg": "No se pudo determinar la ubicación origen"}

            # DESACTIVAR temporalmente el módulo delivery para evitar cálculos de peso
            context_no_delivery = {
                "skip_weight_computation": True,
                "no_compute_weight": True,
                "disable_automatic_weight": True,
            }

            # Crear picking principal CON CONTEXTO que evita cálculos de peso
            picking_vals = {
                "picking_type_id": picking_type.id,
                "location_dest_id": id_ubicacion_destino,
                "user_id": id_responsable,
                "responsable_id": id_responsable,
                "origin": f"Devolución manual creada por {user.name}",
                "start_time_reception": (
                    procesar_fecha_naive(fecha_inicio, "America/Bogota") if fecha_inicio else datetime.now()
                ),  # CORREGIDO: datetime naive
                "end_time_reception": (
                    procesar_fecha_naive(fecha_fin, "America/Bogota") if fecha_fin else datetime.now()
                ),  # CORREGIDO: datetime naive
            }

            if id_proveedor:
                picking_vals["partner_id"] = id_proveedor

            picking = request.env["stock.picking"].sudo().with_context(**context_no_delivery).create(picking_vals)

            # Agrupar por producto para cantidad total
            productos_agrupados = {}
            items_individuales = []

            for item in list_items:
                id_producto = item.get("id_producto", 0)
                cantidad = float(item.get("cantidad_enviada", 0))

                if not id_producto or cantidad <= 0:
                    return {
                        "code": 400,
                        "msg": f"Cantidad o producto inválido para producto ID: {id_producto}",
                    }

                product = request.env["product.product"].sudo().browse(id_producto)
                if not product.exists():
                    return {
                        "code": 404,
                        "msg": f"Producto con ID {id_producto} no encontrado",
                    }

                if id_producto in productos_agrupados:
                    productos_agrupados[id_producto]["cantidad_total"] += cantidad
                else:
                    productos_agrupados[id_producto] = {
                        "id_producto": id_producto,
                        "cantidad_total": cantidad,
                        "product": product,
                    }

                items_individuales.append(item)

            # Crear moves CON CONTEXTO que evita cálculos
            moves_creados = {}
            for id_producto, grupo in productos_agrupados.items():
                product = grupo["product"]

                move_vals = {
                    "name": product.display_name,
                    "product_id": product.id,
                    "product_uom_qty": grupo["cantidad_total"],
                    "product_uom": product.uom_id.id,
                    "location_id": location_src_id,
                    "location_dest_id": id_ubicacion_destino,
                    "picking_id": picking.id,
                }

                move = request.env["stock.move"].sudo().with_context(**context_no_delivery).create(move_vals)
                moves_creados[id_producto] = move

            # Confirmar picking SIN triggear cálculos de peso
            try:
                picking.with_context(**context_no_delivery).action_confirm()
            except Exception as e:
                return {
                    "code": 500,
                    "msg": f"Error al confirmar la devolución: {str(e)}",
                }

            # Eliminar move lines automáticas
            existing_move_lines = request.env["stock.move.line"].sudo().search([("picking_id", "=", picking.id)])
            if existing_move_lines:
                existing_move_lines.with_context(**context_no_delivery).unlink()

            # Crear move lines individuales
            move_lines_creadas = []
            items_procesados = []

            for item in items_individuales:
                id_producto = item.get("id_producto", 0)
                id_lote = item.get("id_lote")
                cantidad = float(item.get("cantidad_enviada", 0))
                fecha_transaccion = item.get("fecha_transaccion", "")
                observacion = item.get("observacion", "")
                time_line = item.get("time_line", 0)

                move = moves_creados[id_producto]

                move_line_vals = {
                    "move_id": move.id,
                    "picking_id": picking.id,
                    "product_id": id_producto,
                    "product_uom_id": move.product_uom.id,
                    "location_id": location_src_id,
                    "location_dest_id": id_ubicacion_destino,
                    "quantity": cantidad,
                    "user_operator_id": id_responsable or user.id,
                    "new_observation": observacion,
                    "is_done_item": True,
                    "time": time_line,
                    "date_transaction": (
                        procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now()
                    ),  # CORREGIDO: datetime naive
                }

                if id_lote:
                    lote = request.env["stock.lot"].sudo().browse(id_lote)
                    if lote.exists():
                        move_line_vals["lot_id"] = id_lote

                move_line = request.env["stock.move.line"].sudo().with_context(**context_no_delivery).create(move_line_vals)
                move_lines_creadas.append(move_line)

                lote_nombre = ""
                if id_lote:
                    lote = request.env["stock.lot"].sudo().browse(id_lote)
                    lote_nombre = lote.name if lote.exists() else ""

                items_procesados.append(
                    {
                        "id_producto": id_producto,
                        "nombre_producto": move.product_id.display_name,
                        "cantidad": cantidad,
                        "move_id": move.id,
                        "move_line_id": move_line.id,
                        "lote_id": id_lote,
                        "lote_nombre": lote_nombre,
                        "observacion": observacion,
                    }
                )

            # Actualizar quantities manualmente
            for move in picking.move_ids:
                total_qty_done = sum(ml.quantity for ml in move.move_line_ids)
                move.with_context(**context_no_delivery).write({"quantity": total_qty_done, "state": "assigned"})

            # Establecer estados manualmente SIN validación automática
            try:
                # NO usar button_validate() para evitar cálculos automáticos
                # CORREGIDO: Usar datetime.now() sin timezone para que sea naive
                picking.with_context(**context_no_delivery).write({"state": "done", "date_done": datetime.now()})

                # Establecer moves como done
                for move in picking.move_ids:
                    move.with_context(**context_no_delivery).write({"state": "done"})

            except Exception as e:
                return {"code": 400, "msg": f"Error al finalizar: {str(e)}"}

            # Preparar respuesta
            ubicacion_destino = request.env["stock.location"].sudo().browse(id_ubicacion_destino)
            proveedor_info = ""
            if id_proveedor:
                proveedor = request.env["res.partner"].sudo().browse(id_proveedor)
                proveedor_info = proveedor.name if proveedor.exists() else ""

            return {
                "code": 200,
                "msg": "Devolución creada y validada correctamente",
                "devolucion_id": picking.id,
                "nombre_devolucion": picking.name,
                "estado": picking.state,
                "fecha_creacion": picking.create_date,
                "almacen_id": id_almacen,
                "ubicacion_destino_id": id_ubicacion_destino,
                "ubicacion_destino_nombre": ubicacion_destino.display_name,
                "responsable_id": id_responsable,
                "proveedor_id": id_proveedor,
                "proveedor_nombre": proveedor_info,
                "id_almacenes": (user.allowed_warehouse_ids[0].id if user.allowed_warehouse_ids else None),
                "name_almacen": (user.allowed_warehouse_ids[0].name if user.allowed_warehouse_ids else None),
                "total_items": len(items_procesados),
                "total_move_lines": len(move_lines_creadas),
                "items_procesados": items_procesados,
            }

        except Exception as err:
            return {"code": 500, "msg": f"Error inesperado: {str(err)}"}

    ## POST PARA DESEMBOLSAR UN PAQUETE
    @http.route(
        "/api/transferencias/unpacking",
        auth="user",
        type="json",
        methods=["POST"],
        csrf=False,
    )
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
                    return {
                        "code": 404,
                        "msg": f"Movimiento no encontrado (ID: {id_move})",
                    }

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
                    return {
                        "code": 404,
                        "msg": f"Movimiento no encontrado (ID: {id_move})",
                    }

            if not transferencia.move_line_ids.filtered(lambda l: l.result_package_id):
                # Si no hay más líneas de movimiento asociadas al paquete, eliminar el paquete
                paquete.unlink()
                array_result.append(
                    {
                        "code": 200,
                        "msg": f"Paquete {id_paquete} eliminado correctamente.",
                    }
                )

            return {"code": 200, "result": array_result}

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 400, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/complete_transfer", auth="user", type="json", methods=["POST"], csrf=False)
    def completar_transferencia(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = auth.get("id_transferencia", 0)
            crear_backorder = auth.get("crear_backorder", True)

            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)], limit=1)

            if not transferencia:
                return {
                    "code": 400,
                    "msg": f"Transferencia no encontrada con ID {id_transferencia}",
                }

            # ✅ NUEVO: Validar stock disponible ANTES de procesar
            error_stock = self._validar_stock_disponible(transferencia, request.env)
            if error_stock:
                return {"code": 400, "msg": error_stock}

            # Eliminar líneas no marcadas como hechas
            lineas_no_enviadas = transferencia.move_line_ids.filtered(lambda l: not l.is_done_item)
            if lineas_no_enviadas:
                lineas_no_enviadas.unlink()

            # Validar que aún queden líneas para procesar
            if not transferencia.move_line_ids:
                return {
                    "code": 400,
                    "msg": "No hay líneas para procesar en la transferencia",
                }

            # Intentar validar la Transferencia
            result = transferencia.sudo().button_validate()

            # Verificar si result es un booleano (True) o un diccionario
            if isinstance(result, bool):
                return {"code": 200, "msg": "Transferencia completada correctamente"}

            elif isinstance(result, dict) and result.get("res_model"):
                wizard_model = result.get("res_model")

                if wizard_model == "stock.backorder.confirmation":
                    wizard_context = result.get("context", {})
                    wizard_vals = {
                        "pick_ids": [(4, id_transferencia)],
                        "show_transfers": wizard_context.get("default_show_transfers", False),
                    }

                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)

                    if crear_backorder:
                        wizard_result = wizard.sudo().process()
                        if isinstance(wizard_result, dict) and wizard_result.get("res_model"):
                            return {"code": 400, "msg": wizard_result.get("res_model")}

                        return {
                            "code": 200,
                            "msg": "Transferencia procesada con backorder",
                            "original_id": transferencia.id,
                            "original_state": transferencia.state,
                            "backorder_id": wizard.id if wizard else False,
                        }
                    else:
                        wizard_result = wizard.sudo().process_cancel_backorder()
                        if isinstance(wizard_result, dict) and wizard_result.get("res_model"):
                            return {"code": 400, "msg": wizard_result.get("res_model")}

                        return {
                            "code": 200,
                            "msg": "Transferencia parcial completada sin crear backorder",
                        }

                elif wizard_model == "stock.immediate.transfer":
                    wizard_context = result.get("context", {})
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({"pick_ids": [(4, id_transferencia)]})

                    wizard.sudo().process()
                    return {
                        "code": 200,
                        "msg": "Transferencia procesada con transferencia inmediata",
                    }

                else:
                    return {
                        "code": 400,
                        "msg": f"Se requiere un asistente no soportado: {wizard_model}",
                    }

            return {"code": 200, "msg": "Transferencia completada"}

        except ValidationError as ve:
            request.env.cr.rollback()
            return {"code": 400, "msg": str(ve)}
        except Exception as e:
            request.env.cr.rollback()
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    def _validar_stock_disponible(self, picking, env):
        """Valida si hay stock suficiente antes de procesar"""
        p = env["decimal.precision"].precision_get("Product Unit of Measure")

        # ✅ Agrupar move_lines por producto/ubicación/lote para validar el impacto total
        lineas_agrupadas = {}

        for move_line in picking.move_line_ids.filtered(lambda l: l.is_done_item):
            key = (
                move_line.product_id.id,
                move_line.location_id.id,
                move_line.lot_id.id if move_line.lot_id else False,
            )

            if key not in lineas_agrupadas:
                lineas_agrupadas[key] = {
                    "product": move_line.product_id,
                    "location": move_line.location_id,
                    "lot": move_line.lot_id,
                    "cantidad_total": 0,
                }

            lineas_agrupadas[key]["cantidad_total"] += move_line.quantity

        # ✅ Validar cada grupo
        for key, datos in lineas_agrupadas.items():
            product = datos["product"]
            location = datos["location"]
            lot = datos["lot"]
            cantidad_total = datos["cantidad_total"]

            # Solo validar productos stockeables en ubicaciones internas/transit
            if product.type != "product" or location.usage not in [
                "internal",
                "transit",
            ]:
                continue

            # Verificar si el producto/ubicación no permiten stock negativo
            disallowed_by_product = not product.allow_negative_stock and not product.categ_id.allow_negative_stock
            disallowed_by_location = not location.allow_negative_stock

            if not (disallowed_by_product and disallowed_by_location):
                continue

            # ✅ Calcular stock disponible real
            domain = [
                ("product_id", "=", product.id),
                ("location_id", "=", location.id),
            ]
            if lot:
                domain.append(("lot_id", "=", lot.id))

            quants = env["stock.quant"].sudo().search(domain)

            # Sumar quantity para obtener el stock físico actual
            stock_actual = sum(quants.mapped("quantity"))

            # ✅ Calcular qué pasaría después de esta operación
            stock_resultante = stock_actual - cantidad_total

            if float_compare(stock_resultante, 0, precision_digits=p) == -1:
                # 🎨 MENSAJE FORMATEADO EXACTAMENTE COMO LO PEDISTE
                msg_lote = f"Lote {lot.name}\n" if lot else ""

                mensaje = (
                    f"No puede validar ({picking.name}) - Stock insuficiente:\n"
                    f"'{product.display_name}'\n"
                    f"{msg_lote}"
                    f"Se generaría stock negativo ({stock_resultante}) en la ubicación '{location.complete_name}'\n\n"
                    f"Acciones a realizar:\n"
                    f"• Revise si cuenta con cantidad física suficiente y que corresponda con lo registrado en 360WMS\n"
                    f"• Realice traslado/abastecimiento a ubicación origen:\n"
                    f"  {location.complete_name}\n"
                    f"• De ser necesario, anule reserva en otros documentos."
                )

                return mensaje

        return False

    ## POST Completar transferencia con fecha de caducidad
    @http.route(
        "/api/complete_transfer/expire",
        auth="user",
        type="json",
        methods=["POST"],
        csrf=False,
    )
    def completar_transferencia_expire(self, **auth):
        try:
            user = request.env.user
            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = auth.get("id_transferencia", 0)
            crear_backorder = auth.get("crear_backorder", True)

            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)], limit=1)

            if not transferencia:
                return {
                    "code": 400,
                    "msg": f"Transferencia no encontrada o ya completada con ID {id_transferencia}",
                }

            # Eliminar las lineas que no se tiene is_done_item true
            lineas_no_enviadas = transferencia.move_line_ids.filtered(lambda l: not l.is_done_item)
            if lineas_no_enviadas:
                lineas_no_enviadas.unlink()

            # Función recursiva para manejar wizards en cadena
            def procesar_wizard(result, transferencia_id, crear_backorder):
                if not isinstance(result, dict) or not result.get("res_model"):
                    # Si no es un wizard, simplemente devolvemos "Transferencia completada"
                    # Refrescamos el objeto transferencia buscándolo nuevamente
                    transferencia = request.env["stock.picking"].sudo().search([("id", "=", transferencia_id)], limit=1)
                    return {
                        "code": 200,
                        "msg": f"Transferencia completada correctamente. Estado: {transferencia.state}",
                    }

                wizard_model = result.get("res_model")
                wizard_context = result.get("context", {})

                # Para asistente de backorder
                if wizard_model == "stock.backorder.confirmation":
                    wizard_vals = {
                        "pick_ids": [(4, transferencia_id)],
                        "show_transfers": wizard_context.get("default_show_transfers", False),
                    }
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)

                    if crear_backorder:
                        next_result = wizard.sudo().process()
                        # Si process() devuelve otro wizard, lo procesamos
                        if isinstance(next_result, dict) and next_result.get("res_model"):
                            return procesar_wizard(next_result, transferencia_id, crear_backorder)

                        # Refrescamos el objeto transferencia buscándolo nuevamente
                        transferencia = request.env["stock.picking"].sudo().search([("id", "=", transferencia_id)], limit=1)

                        # Buscamos si se creó un backorder
                        backorder = (
                            request.env["stock.picking"]
                            .sudo()
                            .search(
                                [
                                    ("backorder_id", "=", transferencia_id),
                                    ("state", "!=", "cancel"),
                                ],
                                limit=1,
                            )
                        )

                        return {
                            "code": 200,
                            "msg": "Transferencia procesada con backorder",
                            "original_id": transferencia.id,
                            "original_state": transferencia.state,
                            "backorder_id": backorder.id if backorder else False,
                        }
                    else:
                        next_result = wizard.sudo().process_cancel_backorder()
                        # Si process_cancel_backorder() devuelve otro wizard, lo procesamos
                        if isinstance(next_result, dict) and next_result.get("res_model"):
                            return procesar_wizard(next_result, transferencia_id, crear_backorder)
                        return {
                            "code": 200,
                            "msg": "Transferencia parcial completada sin crear backorder",
                        }

                # Para asistente de fechas de caducidad
                elif wizard_model == "expiry.picking.confirmation":
                    wizard_vals = {
                        "picking_ids": wizard_context.get("default_picking_ids", [[6, 0, [transferencia_id]]]),
                        "lot_ids": wizard_context.get("default_lot_ids", []),
                    }

                    # Ajustamos el contexto con la decisión de backorder
                    adjusted_context = dict(wizard_context)
                    if not crear_backorder:
                        adjusted_context["skip_backorder"] = True

                    wizard = request.env[wizard_model].sudo().with_context(**adjusted_context).create(wizard_vals)

                    # Intentamos procesar usando los diferentes métodos posibles
                    process_result = None
                    if hasattr(wizard, "process") and callable(wizard.process):
                        process_result = wizard.sudo().process()
                    elif hasattr(wizard, "confirm") and callable(wizard.confirm):
                        process_result = wizard.sudo().confirm()
                    elif hasattr(wizard, "action_confirm") and callable(wizard.action_confirm):
                        process_result = wizard.sudo().action_confirm()
                    else:
                        return {
                            "code": 400,
                            "msg": "No se encontró un método para procesar el asistente de fechas de caducidad",
                        }

                    # Si el método de procesamiento devolvió otro wizard, lo procesamos
                    if isinstance(process_result, dict) and process_result.get("res_model"):
                        return procesar_wizard(process_result, transferencia_id, crear_backorder)

                    # Verificamos el estado de la transferencia después de confirmar la caducidad
                    # Refrescamos el objeto transferencia buscándolo nuevamente
                    transferencia = request.env["stock.picking"].sudo().search([("id", "=", transferencia_id)], limit=1)

                    # Buscamos si se creó un backorder
                    backorder = False
                    if crear_backorder:
                        # Buscamos backorders relacionados con esta transferencia
                        backorder = (
                            request.env["stock.picking"]
                            .sudo()
                            .search(
                                [
                                    ("backorder_id", "=", transferencia_id),
                                    ("state", "!=", "cancel"),
                                ],
                                limit=1,
                            )
                        )

                    # Si la transferencia aún no está en estado "done", puede que necesitemos otro asistente
                    if transferencia.state != "done":
                        next_result = transferencia.sudo().button_validate()
                        return procesar_wizard(next_result, transferencia_id, crear_backorder)

                    # Retornamos con información sobre el backorder si fue creado
                    if crear_backorder and backorder:
                        return {
                            "code": 200,
                            "msg": "Transferencia procesada con confirmación de fechas de caducidad y backorder",
                            "original_id": transferencia.id,
                            "original_state": transferencia.state,
                            "backorder_id": backorder.id if backorder else False,
                        }
                    else:
                        return {
                            "code": 200,
                            "msg": "Transferencia procesada con confirmación de fechas de caducidad sin crear backorder",
                            "original_id": transferencia.id,
                            "original_state": transferencia.state,
                        }

                # Para asistente de transferencia inmediata
                elif wizard_model == "stock.immediate.transfer":
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({"pick_ids": [(4, transferencia_id)]})
                    next_result = wizard.sudo().process()

                    # Si process() devuelve otro wizard, lo procesamos
                    if isinstance(next_result, dict) and next_result.get("res_model"):
                        return procesar_wizard(next_result, transferencia_id, crear_backorder)

                    return {
                        "code": 200,
                        "msg": "Transferencia procesada con transferencia inmediata",
                    }

                else:
                    return {
                        "code": 400,
                        "msg": f"Se requiere un asistente no soportado: {wizard_model}",
                    }

            # Intentar validar la Transferencia
            result = transferencia.sudo().button_validate()

            # Utilizamos la función recursiva para manejar todos los wizards en cadena
            return procesar_wizard(result, id_transferencia, crear_backorder)

        except Exception as e:
            # Registrar el error completo para depuración
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST Comprobar disponibilidad de productos en transferencia
    @http.route(
        "/api/comprobar_disponibilidad",
        auth="user",
        type="json",
        methods=["POST"],
        csrf=False,
    )
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
                return {
                    "code": 500,
                    "msg": f"Error al comprobar disponibilidad: {str(e)}",
                }

            create_backorder = picking.picking_type_id.create_backorder if hasattr(picking.picking_type_id, "create_backorder") else False

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
                "proveedor": picking.partner_id.name or "",  # AGREGADO
                "numero_transferencia": picking.name,
                "proveedor_id": picking.partner_id.id or 0,
                "peso_total": 0,
                "numero_lineas": 0,
                "numero_items": sum(move.product_uom_qty for move in picking.move_ids),
                "state": picking.state,
                "create_backorder": create_backorder,
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
                cantidad_faltante = quantity_ordered - cantidad_faltante  # AGREGADO (igual que en el segundo endpoint)

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
                        "other_barcodes": get_barcodes(product, move.id, picking.id),
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
                        "cantidad_faltante": cantidad_faltante,  # CORREGIDO (antes era quantity_ordered)
                        "uom": (move.move_id.product_uom.name if move.move_id and move.move_id.product_uom else "UND"),
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
                quantity_ordered = move_line.move_id.product_uom_qty or 0  # AGREGADO para consistencia
                quantity_done = move_line.quantity or 0  # AGREGADO para consistencia

                cantidad_faltante = quantity_ordered - quantity_done  # CORREGIDO

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
                    "other_barcodes": get_barcodes(product, move_line.id, picking.id),
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
                    "quantity_ordered": move_line.move_id.product_uom_qty,
                    "quantity_to_transfer": move_line.move_id.product_uom_qty,
                    "quantity_done": move_line.quantity,
                    "cantidad_faltante": cantidad_faltante,  # CORREGIDO (antes era cantidad_faltante calculado diferente)
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
                    "user_operator_id": (move_line.user_operator_id.id if move_line.user_operator_id else 0),
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

            return {
                "code": 200,
                "msg": "Disponibilidad comprobada correctamente",
                "result": transferencia_info,
            }

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## GET INFORMACION RAPIDA POR CÓDIGO DE BARRAS
    # @http.route("/api/transferencias/quickinfo", auth="user", type="json", methods=["GET"])
    # def get_quick_info(self, **kwargs):
    #     try:
    #         version_app = kwargs.get("version_app")

    #         # 1. Llama al otro método DENTRO de esta misma clase
    #         response_version = self.get_last_version()

    #         # 2. Extrae la versión de la respuesta de forma segura
    #         latest_version_str = "0.0.0"
    #         if response_version.get("code") == 200:
    #             version_info = response_version.get("result", {})
    #             latest_version_str = version_info.get("version", "0.0.0")

    #         # 3. Compara las versiones
    #         update_required = False
    #         if version_app:
    #             app_parts = list(map(int, version_app.split(".")))
    #             latest_parts = list(map(int, latest_version_str.split(".")))
    #             if app_parts < latest_parts:
    #                 update_required = True
    #         else:
    #             update_required = True

    #         user = request.env.user

    #         # ✅ Validar usuario
    #         if not user:
    #             return {
    #                 "code": 400,
    #                 "update_version": update_required,
    #                 "msg": "Usuario no encontrado",
    #             }

    #         device_id = kwargs.get("device_id") or request.params.get("device_id")

    #         validation_error = validate_pda(device_id)
    #         if validation_error:
    #             return validation_error

    #         barcode = kwargs.get("barcode")
    #         if not barcode:
    #             return {
    #                 "code": 400,
    #                 "update_version": update_required,
    #                 "msg": "Código de barras no proporcionado",
    #             }

    #         # Buscar PRODUCTO por barcode directo
    #         product = (
    #             request.env["product.product"]
    #             .sudo()
    #             .search(
    #                 [
    #                     "|",
    #                     "|",
    #                     ("barcode", "ilike", barcode),
    #                     ("default_code", "ilike", barcode),
    #                     ("barcode_ids.name", "ilike", barcode),
    #                 ],
    #                 limit=1,
    #             )
    #         )

    #         # Buscar PRODUCTO por paquete
    #         if not product:
    #             packaging = (
    #                 request.env["product.packaging"].sudo().search([("barcode", "ilike", barcode)], limit=1)
    #             )
    #             if packaging:
    #                 product = packaging.product_id

    #         # Buscar PRODUCTO por lote
    #         if not product:
    #             lot = request.env["stock.lot"].sudo().search([("name", "ilike", barcode)], limit=1)
    #             if lot:
    #                 product = lot.product_id

    #         # 🆕 NUEVA FUNCIONALIDAD: Buscar por nombre de paquete en stock.move.line
    #         if not product:

    #             base_barcode = barcode.split("-")[0].strip()

    #             pack = (
    #                 request.env["stock.quant.package"]
    #                 .sudo()
    #                 .search(["|", ("name", "ilike", barcode), ("name", "ilike", base_barcode)], limit=1)
    #             )

    #             # Buscar en stock.move.line por el nombre del paquete
    #             move_lines = (
    #                 request.env["stock.move.line"]
    #                 .sudo()
    #                 .search(
    #                     [
    #                         "|",
    #                         ("result_package_id.name", "ilike", barcode),
    #                         ("result_package_id.name", "ilike", base_barcode),
    #                     ]
    #                 )
    #             )

    #             if move_lines:
    #                 # Obtener almacenes del usuario
    #                 allowed_warehouses = obtener_almacenes_usuario(user)

    #                 # Verificar si es un error
    #                 if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
    #                     return allowed_warehouses

    #                 # Filtrar solo las líneas de almacenes permitidos
    #                 filtered_move_lines = move_lines.filtered(
    #                     lambda ml: ml.location_dest_id.warehouse_id.id in allowed_warehouses.ids
    #                     or ml.location_id.warehouse_id.id in allowed_warehouses.ids
    #                 )

    #                 if filtered_move_lines:
    #                     # Obtener productos únicos (similar a estructura de ubicación)
    #                     productos_dict = {}

    #                     for move_line in filtered_move_lines:
    #                         prod = move_line.product_id
    #                         if prod.id not in productos_dict:
    #                             productos_dict[prod.id] = {
    #                                 "id": prod.id,
    #                                 "producto": prod.display_name,
    #                                 "cantidad": 0.0,
    #                                 "codigo_barras": prod.barcode or "",
    #                                 "unidad_medida": prod.uom_id.name or "",
    #                                 "lote": "",
    #                                 "lote_id": 0,
    #                                 "id_almacen": 0,
    #                                 "nombre_almacen": "",
    #                                 "pedido": (
    #                                     move_line.move_id.picking_id.name
    #                                     if move_line.move_id and move_line.move_id.picking_id
    #                                     else ""
    #                                 ),
    #                                 "origin": (
    #                                     move_line.move_id.picking_id.origin
    #                                     if move_line.move_id and move_line.move_id.picking_id
    #                                     else ""
    #                                 ),
    #                                 "tercero": (
    #                                     move_line.move_id.picking_id.partner_id.name
    #                                     if move_line.move_id
    #                                     and move_line.move_id.picking_id
    #                                     and move_line.move_id.picking_id.partner_id
    #                                     else ""
    #                                 ),
    #                                 "numero_caja": move_line.faber_box_number or "",
    #                                 "operador": (
    #                                     move_line.user_operator_id.name if move_line.user_operator_id else ""
    #                                 ),
    #                                 "packing": True if move_line.result_package_id else False,
    #                             }

    #                         # Sumar cantidad
    #                         productos_dict[prod.id]["cantidad"] += (
    #                             move_line.quantity or move_line.product_uom_qty
    #                         )

    #                         # Usar el primer lote encontrado (como en ubicación)
    #                         if move_line.lot_id and not productos_dict[prod.id]["lote"]:
    #                             productos_dict[prod.id]["lote"] = move_line.lot_id.name
    #                             productos_dict[prod.id]["lote_id"] = move_line.lot_id.id

    #                         # Usar el primer almacén encontrado
    #                         if (
    #                             move_line.location_id.warehouse_id
    #                             and not productos_dict[prod.id]["id_almacen"]
    #                         ):
    #                             productos_dict[prod.id]["id_almacen"] = move_line.location_id.warehouse_id.id
    #                             productos_dict[prod.id][
    #                                 "nombre_almacen"
    #                             ] = move_line.location_id.warehouse_id.name

    #                     productos = list(productos_dict.values())

    #                     return {
    #                         "code": 200,
    #                         "update_version": update_required,
    #                         "type": "paquete",
    #                         "result": {
    #                             "id": (
    #                                 filtered_move_lines[0].result_package_id.id if filtered_move_lines else 0
    #                             ),
    #                             "id_almacen": (
    #                                 filtered_move_lines[0].location_id.warehouse_id.id
    #                                 if filtered_move_lines and filtered_move_lines[0].location_id.warehouse_id
    #                                 else 0
    #                             ),
    #                             "nombre_almacen": (
    #                                 filtered_move_lines[0].location_id.warehouse_id.name
    #                                 if filtered_move_lines and filtered_move_lines[0].location_id.warehouse_id
    #                                 else ""
    #                             ),
    #                             "nombre": pack.name if pack else barcode,
    #                             "ubicacion_padre": "",
    #                             "tipo_ubicacion": "paquete",
    #                             "codigo_barras": pack.name if pack else barcode,
    #                             "numero_productos": len(productos),
    #                             "total_productos": sum(p["cantidad"] for p in productos),
    #                             "numero_pedidos": len(set(p["pedido"] for p in productos if p["pedido"])),
    #                             "is_sticker": pack.is_sticker,
    #                             "is_certificate": pack.is_certificate,
    #                             "fecha_empaquetado": pack.pack_date,
    #                             "productos": productos,
    #                         },
    #                     }

    #         # Obtener almacenes del usuario
    #         allowed_warehouses = obtener_almacenes_usuario(user)

    #         # Verificar si es un error (diccionario con código y mensaje)
    #         if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
    #             return allowed_warehouses  # Devolver el error directamente

    #         # PRODUCTO encontrado
    #         if product:
    #             # CAMBIO PRINCIPAL: Buscar quants considerando TODOS los almacenes permitidos
    #             quants = (
    #                 request.env["stock.quant"]
    #                 .sudo()
    #                 .search(
    #                     [
    #                         ("product_id", "=", product.id),
    #                         ("quantity", ">", 0),
    #                         ("location_id.usage", "=", "internal"),
    #                         ("location_id.warehouse_id", "in", allowed_warehouses.ids),
    #                     ]
    #                 )
    #             )

    #             ubicaciones = []
    #             for quant in quants:
    #                 # Verificar que el almacén esté en los permitidos
    #                 warehouse = (
    #                     request.env["stock.warehouse"]
    #                     .sudo()
    #                     .search(
    #                         [
    #                             ("id", "=", quant.location_id.warehouse_id.id),
    #                             ("id", "in", allowed_warehouses.ids),
    #                         ],
    #                         limit=1,
    #                     )
    #                 )

    #                 if not warehouse:
    #                     continue  # Saltar si no pertenece a un almacén del usuario

    #                 if quant.inventory_quantity_auto_apply > 0:
    #                     tiene_paquete = bool(quant.package_id)
    #                     ubicaciones.append(
    #                         {
    #                             "id_move": quant.id,
    #                             "id_almacen": warehouse.id,
    #                             "nombre_almacen": warehouse.name,
    #                             "id_ubicacion": quant.location_id.id,
    #                             "unidad_medida": quant.product_uom_id.name or "",
    #                             "ubicacion": quant.location_id.complete_name or "",
    #                             "cantidad": quant.inventory_quantity_auto_apply or 0,
    #                             "reservado": quant.reserved_quantity or 0,
    #                             "cantidad_mano": quant.quantity - quant.reserved_quantity,
    #                             "codigo_barras": quant.location_id.barcode or "",
    #                             "lote": quant.lot_id.name if quant.lot_id else "",
    #                             "lote_id": quant.lot_id.id if quant.lot_id else 0,
    #                             "fecha_eliminacion": quant.removal_date or "",
    #                             "fecha_vencimiento": quant.expiration_date or "",
    #                             "fecha_caducidad": quant.lot_id.expiration_date if quant.lot_id else "",
    #                             "fecha_entrada": quant.in_date or "",
    #                             "packing": tiene_paquete,
    #                             "nombre_paquete": quant.package_id.name if quant.package_id else "",
    #                         }
    #                     )

    #             paquetes = product.packaging_ids.mapped("barcode")

    #             return {
    #                 "code": 200,
    #                 "update_version": update_required,
    #                 "type": "product",
    #                 "result": {
    #                     "id": product.id,
    #                     "nombre": product.display_name,
    #                     "precio": product.lst_price,
    #                     "cantidad_disponible": product.qty_available,
    #                     "previsto": product.virtual_available,
    #                     "referencia": product.default_code,
    #                     "unidad_medida": product.uom_id.name or "",
    #                     "peso": product.weight or 0.0,
    #                     "volumen": product.volume or 0.0,
    #                     "codigo_barras": product.barcode or "",
    #                     "codigos_barras_paquetes": paquetes,
    #                     "imagen": product.image_128
    #                     and f"/web/image/product.product/{product.id}/image_128"
    #                     or "",
    #                     "categoria": product.categ_id.name,
    #                     "ubicaciones": ubicaciones,
    #                 },
    #             }

    #         # Buscar UBICACIÓN por código de barras
    #         location = (
    #             request.env["stock.location"]
    #             .sudo()
    #             .search([("barcode", "ilike", barcode), ("usage", "ilike", "internal")], limit=1)
    #         )  # Solo internas

    #         if location:
    #             quants = (
    #                 request.env["stock.quant"]
    #                 .sudo()
    #                 .search([("location_id", "=", location.id), ("quantity", ">", 0)])
    #             )

    #             productos_dict = {}
    #             for quant in quants:
    #                 prod = quant.product_id
    #                 if prod.id not in productos_dict:
    #                     tiene_paquete = bool(quant.package_id)
    #                     productos_dict[prod.id] = {
    #                         "id": prod.id,
    #                         "producto": prod.display_name,
    #                         "cantidad": 0.0,
    #                         "codigo_barras": prod.barcode or "",
    #                         "unidad_medida": prod.uom_id.name or "",
    #                         "lote": quant.lot_id.name if quant.lot_id else "",
    #                         "lote_id": quant.lot_id.id if quant.lot_id else 0,
    #                         "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
    #                         "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
    #                         "packing": tiene_paquete,
    #                         "nombre_paquete": quant.package_id.name if quant.package_id else "",
    #                     }
    #                 productos_dict[prod.id]["cantidad"] += quant.available_quantity

    #             productos = list(productos_dict.values())

    #             return {
    #                 "code": 200,
    #                 "update_version": update_required,
    #                 "type": "ubicacion",
    #                 "result": {
    #                     "id": location.id,
    #                     "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
    #                     "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
    #                     "nombre": location.name,
    #                     "ubicacion_padre": (location.location_id.name if location.location_id else ""),
    #                     "tipo_ubicacion": location.usage,
    #                     "codigo_barras": location.barcode or "",
    #                     "productos": productos,
    #                 },
    #             }

    #         return {
    #             "code": 404,
    #             "update_version": update_required,
    #             "msg": "No se encontró producto, lote, paquete ni ubicación con ese código de barras",
    #         }

    #     except Exception as e:
    #         return {
    #             "code": 500,
    #             "update_version": update_required,
    #             "msg": f"Error interno: {str(e)}",
    #         }

    ## GET INFORMACION RAPIDA POR CÓDIGO DE BARRAS
    @http.route("/api/transferencias/quickinfo", auth="user", type="json", methods=["GET"])
    def get_quick_info(self, **kwargs):
        try:
            # ---------------------------------------------------------
            # 1. VALIDACIONES INICIALES (Versión, Usuario, Dispositivo)
            # ---------------------------------------------------------
            version_app = kwargs.get("version_app")

            # Llama al otro método DENTRO de esta misma clase
            response_version = self.get_last_version()

            # Extrae la versión de la respuesta de forma segura
            latest_version_str = "0.0.0"
            if response_version.get("code") == 200:
                version_info = response_version.get("result", {})
                latest_version_str = version_info.get("version", "0.0.0")

            # Compara las versiones
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
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

            device_id = kwargs.get("device_id") or request.params.get("device_id")

            # Asumiendo que esta función existe en tu contexto
            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

            barcode = kwargs.get("barcode")
            if not barcode:
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Código de barras no proporcionado",
                }

            # ---------------------------------------------------------
            # 2. BÚSQUEDA DE OBJETOS (Producto, Packaging, Lote, Paquete)
            # ---------------------------------------------------------

            # A. Buscar PRODUCTO por barcode directo
            product = (
                request.env["product.product"]
                .sudo()
                .search(
                    [
                        "|",
                        "|",
                        ("barcode", "ilike", barcode),
                        ("default_code", "ilike", barcode),
                        ("barcode_ids.name", "ilike", barcode),
                    ],
                    limit=1,
                )
            )

            # B. Buscar PRODUCTO por empaquetado
            if not product:
                packaging = request.env["product.packaging"].sudo().search([("barcode", "ilike", barcode)], limit=1)
                if packaging:
                    product = packaging.product_id

            # C. Buscar PRODUCTO por lote
            if not product:
                lot = request.env["stock.lot"].sudo().search([("name", "ilike", barcode)], limit=1)
                if lot:
                    product = lot.product_id

            # ---------------------------------------------------------
            # 3. LÓGICA DE PAQUETE (PRIORIDAD ALTA)
            # ---------------------------------------------------------
            # Si no es un producto directo, verificamos si es un paquete
            if not product:
                base_barcode = barcode.split("-")[0].strip()

                # Buscar el objeto Paquete
                pack = (
                    request.env["stock.quant.package"]
                    .sudo()
                    .search(
                        [
                            "|",
                            ("name", "ilike", barcode),
                            ("name", "ilike", base_barcode),
                        ],
                        limit=1,
                    )
                )

                if pack:
                    # Obtener almacenes del usuario
                    allowed_warehouses = obtener_almacenes_usuario(user)
                    if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                        return allowed_warehouses

                    # --- CORRECCIÓN CLAVE: Buscar contenido real (Quants) ---

                    # Intento A: Buscar en inventario INTERNO (Almacén)
                    quants = pack.quant_ids.filtered(
                        lambda q: q.location_id.usage == "internal" and q.location_id.warehouse_id.id in allowed_warehouses.ids and q.quantity > 0
                    )

                    # Intento B: Si no está en almacén, buscar en CLIENTE (Enviado)
                    # Las ubicaciones de cliente no tienen warehouse_id, por eso fallaba antes
                    if not quants:
                        quants = pack.quant_ids.filtered(lambda q: q.location_id.usage == "customer" and q.quantity > 0)

                    if quants:
                        # Datos de cabecera basados en el primer quant encontrado
                        head_quant = quants[0]
                        warehouse_id = head_quant.location_id.warehouse_id.id or 0
                        warehouse_name = head_quant.location_id.warehouse_id.name or "CLIENTE / EXTERNO"

                        # Buscar METADATOS del último movimiento (Para saber Pedido, Operador, etc.)
                        last_move_line = (
                            request.env["stock.move.line"]
                            .sudo()
                            .search(
                                [
                                    ("result_package_id", "=", pack.id),
                                    ("state", "!=", "cancel"),
                                ],
                                order="date desc, id desc",
                                limit=1,
                            )
                        )
                        picking = last_move_line.picking_id if last_move_line else False

                        productos_dict = {}

                        for quant in quants:
                            prod = quant.product_id
                            if prod.id not in productos_dict:
                                # Construimos la estructura del producto
                                productos_dict[prod.id] = {
                                    "id": prod.id,
                                    "producto": prod.display_name,
                                    "cantidad": 0.0,
                                    "codigo_barras": prod.barcode or "",
                                    "unidad_medida": prod.uom_id.name or "",
                                    "lote": quant.lot_id.name if quant.lot_id else "",
                                    "lote_id": quant.lot_id.id if quant.lot_id else 0,
                                    "id_almacen": warehouse_id,
                                    "nombre_almacen": warehouse_name,
                                    # Datos contextuales (vienen del movimiento, no del stock físico)
                                    "pedido": picking.name if picking else "",
                                    "origin": picking.origin if picking else "",
                                    "tercero": (picking.partner_id.name if picking and picking.partner_id else ""),
                                    # Usamos getattr por seguridad en campos custom
                                    "numero_caja": (getattr(last_move_line, "faber_box_number", "") if last_move_line else ""),
                                    "operador": (
                                        last_move_line.user_operator_id.name
                                        if last_move_line and getattr(last_move_line, "user_operator_id", False)
                                        else ""
                                    ),
                                    "packing": True,
                                }

                            # SUMA CORRECTA: Usamos la cantidad física actual
                            productos_dict[prod.id]["cantidad"] += quant.quantity

                        productos = list(productos_dict.values())

                        return {
                            "code": 200,
                            "update_version": update_required,
                            "type": "paquete",
                            "result": {
                                "id": pack.id,
                                "id_almacen": warehouse_id,
                                "nombre_almacen": warehouse_name,
                                "nombre": pack.name,
                                "ubicacion_padre": (head_quant.location_id.location_id.name if head_quant.location_id.location_id else ""),
                                "tipo_ubicacion": head_quant.location_id.usage,  # 'paquete', 'internal' o 'customer'
                                "codigo_barras": pack.name,
                                "numero_productos": len(productos),
                                "total_productos": sum(p["cantidad"] for p in productos),
                                "numero_pedidos": 1 if picking else 0,
                                "is_sticker": getattr(pack, "is_sticker", False),
                                "is_certificate": getattr(pack, "is_certificate", False),
                                "fecha_empaquetado": getattr(pack, "pack_date", ""),
                                "productos": productos,
                            },
                        }
                    # Si el paquete existe pero no tiene quants (está vacío), caerá al 404 abajo, lo cual es correcto.

            # ---------------------------------------------------------
            # 4. LÓGICA DE PRODUCTO (Si se encontró en paso 2)
            # ---------------------------------------------------------
            allowed_warehouses = obtener_almacenes_usuario(user)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses

            if product:
                # Buscar quants disponibles en almacenes permitidos
                quants = (
                    request.env["stock.quant"]
                    .sudo()
                    .search(
                        [
                            ("product_id", "=", product.id),
                            ("quantity", ">", 0),
                            ("location_id.usage", "=", "internal"),
                            ("location_id.warehouse_id", "in", allowed_warehouses.ids),
                        ]
                    )
                )

                ubicaciones = []
                for quant in quants:
                    warehouse = quant.location_id.warehouse_id
                    if not warehouse:
                        continue

                    tiene_paquete = bool(quant.package_id)
                    ubicaciones.append(
                        {
                            "id_move": quant.id,
                            "id_almacen": warehouse.id,
                            "nombre_almacen": warehouse.name,
                            "id_ubicacion": quant.location_id.id,
                            "unidad_medida": quant.product_uom_id.name or "",
                            "ubicacion": quant.location_id.complete_name or "",
                            "cantidad": quant.quantity,  # Cantidad real a mano
                            "reservado": quant.reserved_quantity or 0,
                            "cantidad_mano": quant.quantity - quant.reserved_quantity,
                            "codigo_barras": quant.location_id.barcode or "",
                            "lote": quant.lot_id.name if quant.lot_id else "",
                            "lote_id": quant.lot_id.id if quant.lot_id else 0,
                            "fecha_eliminacion": quant.removal_date or "",
                            "fecha_vencimiento": quant.expiration_date or "",  # Ojo con campos Odoo 17
                            "fecha_caducidad": quant.lot_id.expiration_date if quant.lot_id else "",
                            "fecha_entrada": quant.in_date or "",
                            "packing": tiene_paquete,
                            "nombre_paquete": quant.package_id.name if quant.package_id else "",
                        }
                    )

                paquetes = product.packaging_ids.mapped("barcode")

                return {
                    "code": 200,
                    "update_version": update_required,
                    "type": "product",
                    "result": {
                        "id": product.id,
                        "nombre": product.display_name,
                        "precio": product.lst_price,
                        "cantidad_disponible": product.qty_available,
                        "previsto": product.virtual_available,
                        "referencia": product.default_code,
                        "unidad_medida": product.uom_id.name or "",
                        "peso": product.weight or 0.0,
                        "volumen": product.volume or 0.0,
                        "codigo_barras": product.barcode or "",
                        "codigos_barras_paquetes": paquetes,
                        "imagen": (f"/web/image/product.product/{product.id}/image_128" if product.image_128 else ""),
                        "categoria": product.categ_id.name,
                        "ubicaciones": ubicaciones,
                    },
                }

            # ---------------------------------------------------------
            # 5. LÓGICA DE UBICACIÓN
            # ---------------------------------------------------------
            location = (
                request.env["stock.location"]
                .sudo()
                .search(
                    [("barcode", "ilike", barcode), ("usage", "ilike", "internal")],
                    limit=1,
                )
            )

            if location:
                quants = request.env["stock.quant"].sudo().search([("location_id", "=", location.id), ("quantity", ">", 0)])

                productos_dict = {}
                for quant in quants:
                    prod = quant.product_id
                    if prod.id not in productos_dict:
                        tiene_paquete = bool(quant.package_id)
                        productos_dict[prod.id] = {
                            "id": prod.id,
                            "producto": prod.display_name,
                            "cantidad": 0.0,
                            "codigo_barras": prod.barcode or "",
                            "unidad_medida": prod.uom_id.name or "",
                            "lote": quant.lot_id.name if quant.lot_id else "",
                            "lote_id": quant.lot_id.id if quant.lot_id else 0,
                            "fecha_vencimiento": quant.expiration_date or "",  # Ojo con campos Odoo 17
                            "fecha_caducidad": quant.lot_id.expiration_date if quant.lot_id else "",
                            "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                            "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                            "packing": tiene_paquete,
                            "nombre_paquete": quant.package_id.name if quant.package_id else "",
                        }
                    productos_dict[prod.id]["cantidad"] += quant.quantity

                productos = list(productos_dict.values())

                return {
                    "code": 200,
                    "update_version": update_required,
                    "type": "ubicacion",
                    "result": {
                        "id": location.id,
                        "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                        "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                        "nombre": location.name,
                        "ubicacion_padre": (location.location_id.name if location.location_id else ""),
                        "tipo_ubicacion": location.usage,
                        "codigo_barras": location.barcode or "",
                        "productos": productos,
                    },
                }

            # ---------------------------------------------------------
            # 6. NO ENCONTRADO
            # ---------------------------------------------------------
            return {
                "code": 404,
                "update_version": update_required,
                "msg": "No se encontró producto, lote, paquete ni ubicación con ese código de barras",
            }

        except Exception as e:
            return {
                "code": 500,
                "update_version": update_required,
                "msg": f"Error interno: {str(e)}",
            }

    @http.route("/api/transferencias/quickinfo/v2", auth="user", type="json", methods=["GET"])
    def get_quick_info_v2(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # device_id = kwargs.get("device_id") or request.params.get("device_id")

            # validation_error = validate_pda(device_id)
            # if validation_error:
            #     return validation_error

            barcode = kwargs.get("barcode")
            if not barcode:
                return {"code": 400, "msg": "Código de barras no proporcionado"}

            # Buscar PRODUCTO por barcode directo
            product = (
                request.env["product.product"]
                .sudo()
                .search(
                    [
                        "|",
                        "|",
                        ("barcode", "=", barcode),
                        ("default_code", "=", barcode),
                        ("barcode_ids.name", "=", barcode),
                    ],
                    limit=1,
                )
            )

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

            # 🆕 NUEVA FUNCIONALIDAD: Buscar por nombre de paquete en stock.move.line
            if not product:
                base_barcode = barcode.split("-")[0].strip()

                pack = (
                    request.env["stock.quant.package"]
                    .sudo()
                    .search(
                        [
                            "|",
                            ("name", "ilike", barcode),
                            ("name", "ilike", base_barcode),
                        ],
                        limit=1,
                    )
                )

                # Buscar en stock.move.line por el nombre del paquete
                move_lines = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search(
                        [
                            "|",
                            ("result_package_id.name", "ilike", barcode),
                            ("result_package_id.name", "ilike", base_barcode),
                        ]
                    )
                )

                if move_lines:
                    # Obtener almacenes del usuario
                    allowed_warehouses = obtener_almacenes_usuario(user)

                    # Verificar si es un error
                    if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                        return allowed_warehouses

                    # Filtrar solo las líneas de almacenes permitidos
                    filtered_move_lines = move_lines.filtered(
                        lambda ml: ml.location_dest_id.warehouse_id.id in allowed_warehouses.ids
                        or ml.location_id.warehouse_id.id in allowed_warehouses.ids
                    )

                    if filtered_move_lines:
                        # Obtener productos únicos (similar a estructura de ubicación)
                        productos_dict = {}

                        for move_line in filtered_move_lines:
                            prod = move_line.product_id
                            if prod.id not in productos_dict:
                                productos_dict[prod.id] = {
                                    "id": prod.id,
                                    "producto": prod.display_name,
                                    "cantidad": 0.0,
                                    "codigo_barras": prod.barcode or "",
                                    "unidad_medida": prod.uom_id.name or "",
                                    "lote": "",
                                    "lote_id": 0,
                                    "id_almacen": 0,
                                    "nombre_almacen": "",
                                    "pedido": (move_line.move_id.picking_id.name if move_line.move_id and move_line.move_id.picking_id else ""),
                                    "origin": (move_line.move_id.picking_id.origin if move_line.move_id and move_line.move_id.picking_id else ""),
                                    "tercero": (
                                        move_line.move_id.picking_id.partner_id.name
                                        if move_line.move_id and move_line.move_id.picking_id and move_line.move_id.picking_id.partner_id
                                        else ""
                                    ),
                                    "numero_caja": move_line.faber_box_number or "",
                                    "operador": (move_line.user_operator_id.name if move_line.user_operator_id else ""),
                                }

                            # Sumar cantidad
                            productos_dict[prod.id]["cantidad"] += move_line.quantity or move_line.product_uom_qty

                            # Usar el primer lote encontrado (como en ubicación)
                            if move_line.lot_id and not productos_dict[prod.id]["lote"]:
                                productos_dict[prod.id]["lote"] = move_line.lot_id.name
                                productos_dict[prod.id]["lote_id"] = move_line.lot_id.id

                            # Usar el primer almacén encontrado
                            if move_line.location_id.warehouse_id and not productos_dict[prod.id]["id_almacen"]:
                                productos_dict[prod.id]["id_almacen"] = move_line.location_id.warehouse_id.id
                                productos_dict[prod.id]["nombre_almacen"] = move_line.location_id.warehouse_id.name

                        productos = list(productos_dict.values())

                        return {
                            "code": 200,
                            "type": "paquete",
                            "result": {
                                "id": (filtered_move_lines[0].result_package_id.id if filtered_move_lines else 0),
                                "id_almacen": (
                                    filtered_move_lines[0].location_id.warehouse_id.id
                                    if filtered_move_lines and filtered_move_lines[0].location_id.warehouse_id
                                    else 0
                                ),
                                "nombre_almacen": (
                                    filtered_move_lines[0].location_id.warehouse_id.name
                                    if filtered_move_lines and filtered_move_lines[0].location_id.warehouse_id
                                    else ""
                                ),
                                "nombre": pack.name if pack else barcode,
                                "ubicacion_padre": "",
                                "tipo_ubicacion": "paquete",
                                "codigo_barras": pack.name if pack else barcode,
                                "numero_productos": len(productos),
                                "total_productos": sum(p["cantidad"] for p in productos),
                                "numero_pedidos": len(set(p["pedido"] for p in productos if p["pedido"])),
                                "is_sticker": pack.is_sticker,
                                "is_certificate": pack.is_certificate,
                                "fecha_empaquetado": pack.pack_date,
                                "productos": productos,
                            },
                        }

            # Obtener almacenes del usuario
            allowed_warehouses = obtener_almacenes_usuario(user)

            # Verificar si es un error (diccionario con código y mensaje)
            if isinstance(allowed_warehouses, dict) and "code" in allowed_warehouses:
                return allowed_warehouses  # Devolver el error directamente

            # PRODUCTO encontrado
            if product:
                # CAMBIO PRINCIPAL: Buscar quants considerando TODOS los almacenes permitidos
                quants = (
                    request.env["stock.quant"]
                    .sudo()
                    .search(
                        [
                            ("product_id", "=", product.id),
                            ("quantity", ">", 0),
                            ("location_id.usage", "=", "internal"),
                            ("location_id.warehouse_id", "in", allowed_warehouses.ids),
                        ]
                    )
                )

                ubicaciones = []
                for quant in quants:
                    # Verificar que el almacén esté en los permitidos
                    warehouse = (
                        request.env["stock.warehouse"]
                        .sudo()
                        .search(
                            [
                                ("id", "=", quant.location_id.warehouse_id.id),
                                ("id", "in", allowed_warehouses.ids),
                            ],
                            limit=1,
                        )
                    )

                    if not warehouse:
                        continue  # Saltar si no pertenece a un almacén del usuario

                    if quant.inventory_quantity_auto_apply > 0:
                        ubicaciones.append(
                            {
                                "id_move": quant.id,
                                "id_almacen": warehouse.id,
                                "nombre_almacen": warehouse.name,
                                "id_ubicacion": quant.location_id.id,
                                "unidad_medida": quant.product_uom_id.name or "",
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
                        "unidad_medida": product.uom_id.name or "",
                        "peso": product.weight or 0.0,
                        "volumen": product.volume or 0.0,
                        "codigo_barras": product.barcode or "",
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
                            "codigo_barras": prod.barcode or "",
                            "unidad_medida": prod.uom_id.name or "",
                            "lote": quant.lot_id.name if quant.lot_id else "",
                            "lote_id": quant.lot_id.id if quant.lot_id else 0,
                            "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                            "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                        }
                    productos_dict[prod.id]["cantidad"] += quant.available_quantity

                productos = list(productos_dict.values())

                return {
                    "code": 200,
                    "type": "ubicacion",
                    "result": {
                        "id": location.id,
                        "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                        "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                        "nombre": location.name,
                        "ubicacion_padre": (location.location_id.name if location.location_id else ""),
                        "tipo_ubicacion": location.usage,
                        "codigo_barras": location.barcode or "",
                        "productos": productos,
                    },
                }

            return {
                "code": 404,
                "msg": "No se encontró producto, lote, paquete ni ubicación con ese código de barras",
            }

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## GET INFORMACION RAPIDA POR ID
    @http.route("/api/transferencias/quickinfo/id", auth="user", type="json", methods=["GET"])
    def get_quick_info_by_id(self, **kwargs):
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
                return {
                    "code": 400,
                    "update_version": update_required,
                    "msg": "Usuario no encontrado",
                }

            device_id = kwargs.get("device_id") or request.params.get("device_id")

            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

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
                    quants = (
                        request.env["stock.quant"]
                        .sudo()
                        .search(
                            [
                                ("product_id", "=", product.id),
                                ("quantity", ">", 0),
                                ("location_id.usage", "=", "internal"),
                                (
                                    "location_id.warehouse_id",
                                    "in",
                                    allowed_warehouses.ids,
                                ),
                            ]
                        )
                    )

                    ubicaciones = []
                    for quant in quants:
                        # Verificar que el almacén esté en los permitidos
                        warehouse = (
                            request.env["stock.warehouse"]
                            .sudo()
                            .search(
                                [
                                    ("id", "=", quant.location_id.warehouse_id.id),
                                    ("id", "in", allowed_warehouses.ids),
                                ],
                                limit=1,
                            )
                        )

                        if not warehouse:
                            continue  # Saltar si no pertenece a un almacén del usuario

                        if quant.inventory_quantity_auto_apply > 0:
                            ubicaciones.append(
                                {
                                    "id_move": quant.id,
                                    "id_almacen": warehouse.id,
                                    "nombre_almacen": warehouse.name,
                                    "id_ubicacion": quant.location_id.id,
                                    "unidad_medida": quant.product_uom_id.name or "",
                                    "ubicacion": quant.location_id.complete_name or "",
                                    "cantidad": quant.inventory_quantity_auto_apply or 0,
                                    "reservado": quant.reserved_quantity or 0,
                                    "cantidad_mano": quant.quantity - quant.reserved_quantity,
                                    "codigo_barras": quant.location_id.barcode or "",
                                    "lote": quant.lot_id.name if quant.lot_id else "",
                                    "lote_id": quant.lot_id.id if quant.lot_id else 0,
                                    "fecha_eliminacion": quant.removal_date or "",
                                    "fecha_entrada": quant.in_date or "",
                                    "fecha_caducidad": quant.lot_id.expiration_date if quant.lot_id else "",
                                }
                            )

                    paquetes = product.packaging_ids.mapped("barcode")

                    return {
                        "code": 200,
                        "update_version": update_required,
                        "type": "product",
                        "result": {
                            "id": product.id,
                            "nombre": product.display_name,
                            "precio": product.lst_price,
                            "cantidad_disponible": product.qty_available,
                            "previsto": product.virtual_available,
                            "referencia": product.default_code,
                            "unidad_medida": product.uom_id.name or "",
                            "peso": product.weight or 0.0,
                            "volumen": product.volume or 0.0,
                            "codigo_barras": product.barcode or "",
                            "codigos_barras_paquetes": paquetes,
                            "imagen": product.image_128 and f"/web/image/product.product/{product.id}/image_128" or "",
                            "categoria": product.categ_id.name,
                            "ubicaciones": ubicaciones,
                        },
                    }

            if id_location:
                # Buscar UBICACIÓN por ID
                location = (
                    request.env["stock.location"].sudo().search([("id", "=", id_location), ("usage", "=", "internal")], limit=1)
                )  # Solo internas

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
                                "codigo_barras": prod.barcode or "",
                                "unidad_medida": prod.uom_id.name or "",
                                "lote": quant.lot_id.name if quant.lot_id else "",
                                "lote_id": quant.lot_id.id if quant.lot_id else 0,
                                "fecha_vencimiento": quant.expiration_date or "",  # Ojo con campos Odoo 17
                                "fecha_caducidad": quant.lot_id.expiration_date if quant.lot_id else "",
                                "id_almacen": (location.warehouse_id.id if location.warehouse_id else 0),
                                "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                            }
                        productos_dict[prod.id]["cantidad"] += quant.available_quantity

                    productos = list(productos_dict.values())

                    return {
                        "code": 200,
                        "update_version": update_required,
                        "type": "ubicacion",
                        "result": {
                            "id": location.id,
                            "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                            "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                            "nombre": location.name or "",
                            "ubicacion_padre": (location.location_id.name if location.location_id else ""),
                            "tipo_ubicacion": location.usage,
                            "codigo_barras": location.barcode or "",
                            "productos": productos,
                        },
                    }

            return {
                "code": 404,
                "update_version": update_required,
                "msg": "No se encontró producto, lote, paquete ni ubicación con ese código de barras",
            }

        except Exception as e:
            return {
                "code": 500,
                "update_version": update_required,
                "msg": f"Error interno: {str(e)}",
            }

    @http.route("/api/transferencias/quickinfo/id/v2", auth="user", type="json", methods=["GET"])
    def get_quick_info_by_id_v2(self, **kwargs):
        try:
            user = request.env.user

            # ✅ Validar usuario
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            device_id = kwargs.get("device_id") or request.params.get("device_id")

            validation_error = validate_pda(device_id)
            if validation_error:
                return validation_error

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
                    quants = (
                        request.env["stock.quant"]
                        .sudo()
                        .search(
                            [
                                ("product_id", "=", product.id),
                                ("quantity", ">", 0),
                                ("location_id.usage", "=", "internal"),
                                (
                                    "location_id.warehouse_id",
                                    "in",
                                    allowed_warehouses.ids,
                                ),
                            ]
                        )
                    )

                    ubicaciones = []
                    for quant in quants:
                        # Verificar que el almacén esté en los permitidos
                        warehouse = (
                            request.env["stock.warehouse"]
                            .sudo()
                            .search(
                                [
                                    ("id", "=", quant.location_id.warehouse_id.id),
                                    ("id", "in", allowed_warehouses.ids),
                                ],
                                limit=1,
                            )
                        )

                        if not warehouse:
                            continue  # Saltar si no pertenece a un almacén del usuario

                        if quant.inventory_quantity_auto_apply > 0:
                            ubicaciones.append(
                                {
                                    "id_move": quant.id,
                                    "id_almacen": warehouse.id,
                                    "nombre_almacen": warehouse.name,
                                    "id_ubicacion": quant.location_id.id,
                                    "unidad_medida": quant.product_uom_id.name or "",
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
                            "unidad_medida": product.uom_id.name or "",
                            "peso": product.weight or 0.0,
                            "volumen": product.volume or 0.0,
                            "codigo_barras": product.barcode or "",
                            "codigos_barras_paquetes": paquetes,
                            "imagen": product.image_128 and f"/web/image/product.product/{product.id}/image_128" or "",
                            "categoria": product.categ_id.name,
                            "ubicaciones": ubicaciones,
                        },
                    }

            if id_location:
                # Buscar UBICACIÓN por ID
                location = (
                    request.env["stock.location"].sudo().search([("id", "=", id_location), ("usage", "=", "internal")], limit=1)
                )  # Solo internas

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
                                "codigo_barras": prod.barcode or "",
                                "unidad_medida": prod.uom_id.name or "",
                                "lote": quant.lot_id.name if quant.lot_id else "",
                                "lote_id": quant.lot_id.id if quant.lot_id else 0,
                                "id_almacen": (location.warehouse_id.id if location.warehouse_id else 0),
                                "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                            }
                        productos_dict[prod.id]["cantidad"] += quant.available_quantity

                    productos = list(productos_dict.values())

                    return {
                        "code": 200,
                        "type": "ubicacion",
                        "result": {
                            "id": location.id,
                            "id_almacen": location.warehouse_id.id if location.warehouse_id else 0,
                            "nombre_almacen": (location.warehouse_id.name if location.warehouse_id else ""),
                            "nombre": location.name or "",
                            "ubicacion_padre": (location.location_id.name if location.location_id else ""),
                            "tipo_ubicacion": location.usage,
                            "codigo_barras": location.barcode or "",
                            "productos": productos,
                        },
                    }

            return {
                "code": 404,
                "msg": "No se encontró producto, lote, paquete ni ubicación con ese código de barras",
            }

        except Exception as e:
            return {"code": 500, "msg": f"Error interno: {str(e)}"}

    ## POST CREAR TRANSFERENCIA DESDE INFORMACION RAPIDA
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
            date_start = auth.get("date_start", "")
            date_end = auth.get("date_end", "")

            # Validaciones
            if not (id_almacen and id_ubicacion_destino and id_ubicacion_origen):
                return {"code": 400, "msg": "Faltan parámetros de ubicación"}

            if not id_producto or cantidad_enviada <= 0:
                return {
                    "code": 400,
                    "msg": f"Cantidad o producto inválido ({cantidad_enviada}) - ({id_producto})",
                }

            product = request.env["product.product"].sudo().browse(id_producto)
            if not product.exists():
                return {"code": 404, "msg": "Producto no encontrado"}

            # ===== CORRECCIÓN PREVENTIVA DE RESERVAS NEGATIVAS =====
            # def corregir_reservas_negativas(product_id, location_id, lote_id=None):
            #     """
            #     Corrige las reservas negativas antes de validar stock
            #     """
            #     domain = [
            #         ("product_id", "=", product_id),
            #         ("location_id", "=", location_id),
            #         ("reserved_quantity", "<", 0),
            #     ]  # Solo quants con reservas negativas

            #     if lote_id:
            #         domain.append(("lot_id", "=", lote_id))

            #     # Buscar quants con reservas negativas
            #     negative_quants = request.env["stock.quant"].sudo().search(domain)

            #     correcciones_realizadas = []
            #     for quant in negative_quants:
            #         valor_anterior = quant.reserved_quantity
            #         # Establecer reserva a 0 si es negativa
            #         quant.write({"reserved_quantity": 0})
            #         correcciones_realizadas.append(
            #             {"quant_id": quant.id, "valor_anterior": valor_anterior, "valor_nuevo": 0}
            #         )

            #     return correcciones_realizadas

            # ===== VALIDACIÓN DE STOCK REAL DISPONIBLE MEJORADA =====
            def validar_stock_disponible(product_id, location_id, cantidad_requerida, lote_id=None):
                """
                Valida el stock real disponible en una ubicación específica
                considerando lo que está a la mano menos lo reservado
                """
                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                    ("quantity", ">", 0),
                ]  # Solo quants con cantidad positiva

                # Si hay lote específico, filtrar por él
                if lote_id:
                    domain.append(("lot_id", "=", lote_id))

                # Buscar todos los quants en la ubicación
                quants = request.env["stock.quant"].sudo().search(domain)

                stock_disponible = 0
                stock_total = 0
                stock_reservado = 0
                quants_con_problemas = []

                for quant in quants:
                    stock_total += quant.quantity

                    # Validar que reserved_quantity no sea negativo
                    if quant.reserved_quantity < 0:
                        quants_con_problemas.append(
                            {
                                "quant_id": quant.id,
                                "reserved_quantity": quant.reserved_quantity,
                            }
                        )
                        # Usar 0 para el cálculo si es negativo
                        reserved_qty = 0
                    else:
                        reserved_qty = quant.reserved_quantity

                    stock_reservado += reserved_qty
                    # Stock disponible = cantidad total - cantidad reservada (asegurándonos que no sea negativa)
                    disponible_quant = max(0, quant.quantity - reserved_qty)
                    stock_disponible += disponible_quant

                return {
                    "stock_disponible": stock_disponible,
                    "stock_total": stock_total,
                    "stock_reservado": stock_reservado,
                    "es_suficiente": stock_disponible >= cantidad_requerida,
                    "quants_con_problemas": quants_con_problemas,
                }

            # 1. PRIMERO: Corregir cualquier reserva negativa existente
            # correcciones = corregir_reservas_negativas(
            #     product_id=id_producto, location_id=id_ubicacion_origen, lote_id=id_lote
            # )
            with request.env.cr.savepoint():
                # 2. SEGUNDO: Validar stock disponible después de correcciones
                validacion_stock = validar_stock_disponible(
                    product_id=id_producto,
                    location_id=id_ubicacion_origen,
                    cantidad_requerida=cantidad_enviada,
                    lote_id=id_lote,
                )

                ubicacion_origem = request.env["stock.location"].sudo().browse(id_ubicacion_origen)

                # Mensaje de correcciones realizadas si las hubo
                mensaje_correcciones = ""
                # if correcciones:
                #     mensaje_correcciones = f" (Se corrigieron {len(correcciones)} reservas negativas)"

                if not validacion_stock["es_suficiente"]:
                    return {
                        "code": 400,
                        "msg": (
                            f"Stock insuficiente para el producto {product.display_name}, "
                            f"en ubicación {ubicacion_origem.display_name}. "
                            f"Solicitado: {cantidad_enviada}, Disponible: {validacion_stock['stock_disponible']} "
                            f"(Total: {validacion_stock['stock_total']}, "
                            f"Reservado: {validacion_stock['stock_reservado']})"
                        ),
                        # "correcciones_realizadas": correcciones,
                    }

                # Buscar tipo de picking interno
                picking_type = (
                    request.env["stock.picking.type"]
                    .sudo()
                    .search(
                        [
                            ("warehouse_id", "=", id_almacen),
                            ("sequence_code", "=", "INT"),
                        ],
                        limit=1,
                    )
                )

                if not picking_type:
                    return {"code": 404, "msg": "Tipo de picking interno no encontrado"}

                # convertir las fechas de date_end y date_start en formato "%Y-%m-%d %H:%M:%S"

                if not date_start or not date_end:
                    # tomar la fecha actual y agregarla en date_start y en date_end que sea la misma mas 3 segundos
                    current_time = datetime.now()
                    date_start = current_time.strftime("%Y-%m-%d %H:%M:%S")
                    date_end = (current_time + timedelta(seconds=3)).strftime("%Y-%m-%d %H:%M:%S")
                    # return {"code": 400, "msg": "Faltan las fechas de inicio o fin de la transferencia"}

                date_start = datetime.strptime(date_start, "%Y-%m-%d %H:%M:%S")
                date_end = datetime.strptime(date_end, "%Y-%m-%d %H:%M:%S")

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
                            "start_time_transfer": date_start,
                            "end_time_transfer": date_end,
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

                # 1. CONFIRMAR (esto creará las move lines automáticamente)
                try:
                    picking.action_confirm()
                except Exception as e:
                    return {
                        "code": 500,
                        "msg": f"Error al confirmar la transferencia: {str(e)}",
                    }

                # 2. BUSCAR Y MODIFICAR LA MOVE LINE EXISTENTE
                existing_move_line = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search(
                        [("move_id", "=", move.id), ("picking_id", "=", picking.id)],
                        limit=1,
                    )
                )

                if existing_move_line:
                    # Actualizar la línea existente con tus datos personalizados
                    update_vals = {
                        "quantity": cantidad_enviada,
                        "user_operator_id": id_responsable or user.id,
                        "new_observation": observacion,
                        "is_done_item": True,
                        "time": time_line,
                        "date_transaction": (
                            procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)
                        ),
                    }

                    # Solo agregar lote si existe
                    if id_lote:
                        update_vals["lot_id"] = id_lote

                    existing_move_line.write(update_vals)
                    move_line = existing_move_line
                else:
                    # Si por alguna razón no se creó automáticamente, crear manualmente
                    move_line_vals = {
                        "move_id": move.id,
                        "picking_id": picking.id,
                        "product_id": product.id,
                        "product_uom_id": product.uom_id.id,
                        "location_id": id_ubicacion_origen,
                        "location_dest_id": id_ubicacion_destino,
                        "quantity": cantidad_enviada,
                        "user_operator_id": id_responsable or user.id,
                        "new_observation": observacion,
                        "is_done_item": True,
                        "time": time_line,
                        "date_transaction": (
                            procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)
                        ),
                    }

                    if id_lote:
                        move_line_vals["lot_id"] = id_lote

                    move_line = request.env["stock.move.line"].sudo().create(move_line_vals)

                # 3. VERIFICACIÓN FINAL ANTES DE VALIDAR
                # Una última verificación para asegurar que no hay reservas negativas
                # final_corrections = corregir_reservas_negativas(
                #     product_id=id_producto, location_id=id_ubicacion_origen, lote_id=id_lote
                # )

                # 4. ASIGNAR (ya debería estar asignado después de action_confirm, pero asegurar estado)
                try:
                    # Verificar que esté en estado asignado
                    if picking.state != "assigned":
                        picking.state = "assigned"
                    if move.state != "assigned":
                        move.state = "assigned"
                except Exception as e:
                    return {"code": 500, "msg": f"Error al asignar: {str(e)}"}

                # 5. VALIDAR la transferencia
                try:
                    picking.button_validate()
                except Exception as e:
                    return {
                        "code": 400,
                        "msg": f"Error en validación del picking: {str(e)}",
                    }

                # Obtener stock actualizado después de la transferencia
                validacion_stock_final = validar_stock_disponible(
                    product_id=id_producto,
                    location_id=id_ubicacion_origen,
                    cantidad_requerida=0,
                    lote_id=id_lote,
                )  # Solo para obtener información

                return {
                    "code": 200,
                    "msg": f"Transferencia creada y validada correctamente{mensaje_correcciones}",
                    "transferencia_id": picking.id,
                    "nombre_transferencia": picking.name,
                    "start_time_transfer": picking.start_time_transfer or "",
                    "end_time_transfer": picking.end_time_transfer or "",
                    "linea_id": move_line.id if move_line else 0,
                    "cantidad_enviada": move_line.quantity if move_line else cantidad_enviada,
                    "id_producto": product.id,
                    "nombre_producto": product.display_name,
                    "ubicacion_origen": move_line.location_id.name if move_line else "",
                    "ubicacion_destino": move_line.location_dest_id.name if move_line else "",
                    "fecha_transaccion": (move_line.date_transaction if hasattr(move_line, "date_transaction") else ""),
                    "observacion": (move_line.new_observation if hasattr(move_line, "new_observation") else ""),
                    "time_line": move_line.time if hasattr(move_line, "time") else 0,
                    "user_operator_id": (move_line.user_operator_id.id if hasattr(move_line, "user_operator_id") else 0),
                    "user_operator_name": (move_line.user_operator_id.name if hasattr(move_line, "user_operator_id") else ""),
                    "id_lote": move_line.lot_id.id if move_line and move_line.lot_id else 0,
                    # Información detallada del stock
                    "stock_total_final": validacion_stock_final["stock_total"],
                    "stock_reservado_final": validacion_stock_final["stock_reservado"],
                    "stock_disponible_final": validacion_stock_final["stock_disponible"],
                    "cantidad_disponible": product.qty_available,  # Para compatibilidad
                    "ubicacion_origen_id": id_ubicacion_origen,
                    "ubicacion_destino_id": id_ubicacion_destino,
                    # Información sobre correcciones realizadas
                    # "correcciones_iniciales": correcciones,
                    # "correcciones_finales": final_corrections,
                    # "total_correcciones": len(correcciones) + len(final_corrections),
                }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 500, "msg": f"Error inesperado: {str(err)}"}

    @http.route("/api/crear_transferencia/test", auth="user", type="json", methods=["POST"], csrf=False)
    def crear_transferencia_test(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # --- 1. Parámetros de Entrada ---
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
            date_start = auth.get("date_start", "")
            date_end = auth.get("date_end", "")

            # --- 2. Validaciones de Parámetros ---
            if not (id_almacen and id_ubicacion_destino and id_ubicacion_origen):
                return {"code": 400, "msg": "Faltan parámetros de ubicación"}
            if not id_producto or cantidad_enviada <= 0:
                return {"code": 400, "msg": f"Cantidad o producto inválido ({cantidad_enviada})"}

            # --- 3. Función de Soporte: Validación y Rastreo de Reservas ---
            def obtener_estado_stock(product_id, location_id, cantidad_requerida, lote_id=None):
                domain = [("product_id", "=", product_id), ("location_id", "=", location_id)]
                if lote_id:
                    domain.append(("lot_id", "=", lote_id))

                quants = request.env["stock.quant"].sudo().search(domain)
                stock_total = sum(quants.mapped("quantity"))
                stock_reservado = sum(quants.mapped("reserved_quantity"))
                stock_disponible = stock_total - stock_reservado

                detalle_reservas = []
                if stock_reservado > 0:
                    # Buscamos qué documentos tienen reservado este producto en Odoo 17
                    move_line_domain = [
                        ("product_id", "=", product_id),
                        ("location_id", "=", location_id),
                        ("state", "in", ["assigned", "partially_available"]),
                    ]
                    if lote_id:
                        move_line_domain.append(("lot_id", "=", lote_id))

                    reservas = request.env["stock.move.line"].sudo().search(move_line_domain)
                    for res in reservas:
                        # En Odoo 17 se usa quantity para lo reservado en líneas no validadas
                        qty_res = res.quantity if res.quantity > 0 else res.move_id.product_uom_qty
                        detalle_reservas.append(
                            {"referencia": res.reference or "N/A", "cantidad": qty_res, "tipo": res.picking_id.picking_type_id.name or "Otro"}
                        )

                return {
                    "stock_disponible": stock_disponible,
                    "stock_total": stock_total,
                    "stock_reservado": stock_reservado,
                    "es_suficiente": stock_disponible >= cantidad_requerida,
                    "detalle_reservas": detalle_reservas,
                }

            # --- 4. Validación Inicial con Mensaje Descriptivo ---
            validacion = obtener_estado_stock(id_producto, id_ubicacion_origen, cantidad_enviada, id_lote)
            ubicacion_obj = request.env["stock.location"].sudo().browse(id_ubicacion_origen)
            product = request.env["product.product"].sudo().browse(id_producto)

            if not validacion["es_suficiente"]:
                msg_error = (
                    f"Stock insuficiente en {ubicacion_obj.display_name}. "
                    f"Disponible: {validacion['stock_disponible']}. "
                    f"Total: {validacion['stock_total']}, Reservado: {validacion['stock_reservado']}."
                )

                if validacion["detalle_reservas"]:
                    docs = ", ".join([f"{d['referencia']} [{d['cantidad']}]" for d in validacion["detalle_reservas"]])
                    msg_error += f" Bloqueado por: {docs}."

                return {"code": 400, "msg": msg_error, "detalle_bloqueos": validacion["detalle_reservas"]}

            # --- 5. Ejecución del Proceso Nativo ---
            with request.env.cr.savepoint():
                picking_type = (
                    request.env["stock.picking.type"].sudo().search([("warehouse_id", "=", id_almacen), ("sequence_code", "=", "INT")], limit=1)
                )

                if not picking_type:
                    return {"code": 404, "msg": "Tipo de picking interno no encontrado"}

                # Manejo de Fechas start/end
                fmt = "%Y-%m-%d %H:%M:%S"
                d_start = datetime.strptime(date_start, fmt) if date_start else datetime.now()
                d_end = datetime.strptime(date_end, fmt) if date_end else (d_start + timedelta(seconds=3))

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
                            "origin": f"API: {user.name}",
                            "start_time_transfer": d_start,
                            "end_time_transfer": d_end,
                        }
                    )
                )

                # Crear Move
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

                # Orquestación Nativa (Evita Negativos)
                picking.action_confirm()
                picking.action_assign()

                if picking.state != "assigned":
                    picking.action_cancel()
                    return {"code": 400, "msg": "No se pudo reservar físicamente el stock (posible colisión de inventario)."}

                # Actualizar Move Line con datos del operario y lote
                move_line = picking.move_line_ids[0] if picking.move_line_ids else False
                if move_line:
                    move_line.write(
                        {
                            "quantity": cantidad_enviada,
                            "lot_id": id_lote or move_line.lot_id.id,
                            "user_operator_id": id_responsable,
                            "new_observation": observacion,
                            "time": time_line,
                            "is_done_item": True,
                            "date_transaction": datetime.now(),
                        }
                    )

                # Validar Transferencia
                picking.with_context(skip_backorder=True).button_validate()

                # --- 6. Respuesta Detallada Final ---
                stock_final = obtener_estado_stock(id_producto, id_ubicacion_origen, 0, id_lote)

                return {
                    "code": 200,
                    "msg": "Transferencia creada y validada correctamente",
                    "transferencia_id": picking.id,
                    "nombre_transferencia": picking.name,
                    "start_time_transfer": str(picking.start_time_transfer or ""),
                    "end_time_transfer": str(picking.end_time_transfer or ""),
                    "linea_id": move_line.id if move_line else 0,
                    "cantidad_enviada": move_line.quantity if move_line else cantidad_enviada,
                    "id_producto": product.id,
                    "nombre_producto": product.display_name,
                    "ubicacion_origen": move_line.location_id.name if move_line else "",
                    "ubicacion_destino": move_line.location_dest_id.name if move_line else "",
                    "fecha_transaccion": str(move_line.date_transaction) if move_line else "",
                    "observacion": move_line.new_observation if move_line else "",
                    "time_line": move_line.time if move_line else 0,
                    "user_operator_id": move_line.user_operator_id.id if move_line else 0,
                    "user_operator_name": move_line.user_operator_id.name if move_line else "",
                    "id_lote": move_line.lot_id.id if move_line and move_line.lot_id else 0,
                    "stock_total_final": stock_final["stock_total"],
                    "stock_reservado_final": stock_final["stock_reservado"],
                    "stock_disponible_final": stock_final["stock_disponible"],
                    "cantidad_disponible": product.qty_available,
                    "ubicacion_origen_id": id_ubicacion_origen,
                    "ubicacion_destino_id": id_ubicacion_destino,
                }

        except Exception as err:
            return {"code": 500, "msg": f"Error inesperado: {str(err)}"}
    #     try:
    #         user = request.env.user
    #         if not user:
    #             return {"code": 400, "msg": "Usuario no encontrado"}

    #         # --- Parámetros de Entrada ---
    #         id_almacen = auth.get("id_almacen", 0)
    #         id_ubicacion_destino = auth.get("id_ubicacion_destino", 0)
    #         id_ubicacion_origen = auth.get("id_ubicacion_origen", 0)
    #         id_responsable = auth.get("id_operario", 0)
    #         id_producto = auth.get("id_producto", 0)
    #         cantidad_enviada = float(auth.get("cantidad_enviada", 0))
    #         id_lote = auth.get("id_lote") or False
    #         fecha_transaccion = auth.get("fecha_transaccion", "")
    #         observacion = auth.get("observacion", "")
    #         time_line = auth.get("time_line", 0)
    #         date_start = auth.get("date_start", "")
    #         date_end = auth.get("date_end", "")

    #         # --- Validaciones de Parámetros ---
    #         if not (id_almacen and id_ubicacion_destino and id_ubicacion_origen):
    #             return {"code": 400, "msg": "Faltan parámetros de ubicación"}
    #         if not id_producto or cantidad_enviada <= 0:
    #             return {"code": 400, "msg": f"Cantidad o producto inválido ({cantidad_enviada}) - ({id_producto})"}

    #         # --- Funciones de Soporte ---
    #         def validar_stock_disponible(product_id, location_id, cantidad_requerida, lote_id=None):
    #             domain = [("product_id", "=", product_id), ("location_id", "=", location_id)]
    #             if lote_id:
    #                 domain.append(("lot_id", "=", lote_id))
    #             quants = request.env["stock.quant"].sudo().search(domain)

    #             stock_total = sum(quants.mapped("quantity"))
    #             stock_reservado = sum(quants.mapped("reserved_quantity"))
    #             stock_disponible = stock_total - stock_reservado
    #             return {
    #                 "stock_disponible": stock_disponible,
    #                 "stock_total": stock_total,
    #                 "stock_reservado": stock_reservado,
    #                 "es_suficiente": stock_disponible >= cantidad_requerida,
    #             }

    #         # 1. Validación Descriptiva Inicial
    #         validacion_stock = validar_stock_disponible(id_producto, id_ubicacion_origen, cantidad_enviada, id_lote)
    #         ubicacion_origem = request.env["stock.location"].sudo().browse(id_ubicacion_origen)

    #         if not validacion_stock["es_suficiente"]:
    #             return {
    #                 "code": 400,
    #                 "msg": (
    #                     f"Stock insuficiente en la ubicación de origen: {ubicacion_origem.display_name}. "
    #                     f"Cantidad solicitada: {cantidad_enviada}. "
    #                     f"Disponible: {validacion_stock['stock_disponible']} "
    #                     f"(Total: {validacion_stock['stock_total']}, "
    #                     f"Reservado: {validacion_stock['stock_reservado']})."
    #                 ),
    #             }

    #         # 2. Proceso de Creación
    #         with request.env.cr.savepoint():
    #             product = request.env["product.product"].sudo().browse(id_producto)
    #             picking_type = (
    #                 request.env["stock.picking.type"].sudo().search([("warehouse_id", "=", id_almacen), ("sequence_code", "=", "INT")], limit=1)
    #             )

    #             if not picking_type:
    #                 return {"code": 404, "msg": "Tipo de picking interno no encontrado"}

    #             # Manejo de Fechas
    #             if not date_start or not date_end:
    #                 current_time = datetime.now()
    #                 date_start_dt = current_time
    #                 date_end_dt = current_time + timedelta(seconds=3)
    #             else:
    #                 date_start_dt = datetime.strptime(date_start, "%Y-%m-%d %H:%M:%S")
    #                 date_end_dt = datetime.strptime(date_end, "%Y-%m-%d %H:%M:%S")

    #             # Crear Picking
    #             picking = (
    #                 request.env["stock.picking"]
    #                 .sudo()
    #                 .create(
    #                     {
    #                         "picking_type_id": picking_type.id,
    #                         "location_id": id_ubicacion_origen,
    #                         "location_dest_id": id_ubicacion_destino,
    #                         "user_id": id_responsable,
    #                         "origin": f"Transferencia creada por {user.name}",
    #                         "start_time_transfer": date_start_dt,
    #                         "end_time_transfer": date_end_dt,
    #                     }
    #                 )
    #             )

    #             # Crear Move
    #             move = (
    #                 request.env["stock.move"]
    #                 .sudo()
    #                 .create(
    #                     {
    #                         "name": product.display_name,
    #                         "product_id": product.id,
    #                         "product_uom_qty": cantidad_enviada,
    #                         "product_uom": product.uom_id.id,
    #                         "location_id": id_ubicacion_origen,
    #                         "location_dest_id": id_ubicacion_destino,
    #                         "picking_id": picking.id,
    #                     }
    #                 )
    #             )

    #             # --- Lógica Nativa Odoo 17 (Previene Negativos) ---
    #             picking.action_confirm()
    #             picking.action_assign()

    #             if picking.state != "assigned":
    #                 picking.action_cancel()
    #                 return {"code": 400, "msg": "Error: El stock no pudo ser reservado físicamente."}

    #             # Modificar Move Line generada
    #             move_line = picking.move_line_ids[0] if picking.move_line_ids else False
    #             if move_line:
    #                 move_line.write(
    #                     {
    #                         "quantity": cantidad_enviada,
    #                         "lot_id": id_lote if id_lote else move_line.lot_id.id,
    #                         "user_operator_id": id_responsable or user.id,
    #                         "new_observation": observacion,
    #                         "time": time_line,
    #                         "is_done_item": True,
    #                         # Aquí puedes agregar tu función procesar_fecha_naive si existe
    #                         "date_transaction": datetime.now(),
    #                     }
    #                 )

    #             picking.with_context(skip_backorder=True).button_validate()

    #             # Stock Final para Respuesta
    #             stock_final = validar_stock_disponible(id_producto, id_ubicacion_origen, 0, id_lote)

    #             # --- Respuesta Exacta Solicitada ---
    #             return {
    #                 "code": 200,
    #                 "msg": "Transferencia creada y validada correctamente",
    #                 "transferencia_id": picking.id,
    #                 "nombre_transferencia": picking.name,
    #                 "start_time_transfer": picking.start_time_transfer or "",
    #                 "end_time_transfer": picking.end_time_transfer or "",
    #                 "linea_id": move_line.id if move_line else 0,
    #                 "cantidad_enviada": move_line.quantity if move_line else cantidad_enviada,
    #                 "id_producto": product.id,
    #                 "nombre_producto": product.display_name,
    #                 "ubicacion_origen": move_line.location_id.name if move_line else "",
    #                 "ubicacion_destino": move_line.location_dest_id.name if move_line else "",
    #                 "fecha_transaccion": str(move_line.date_transaction) if move_line else "",
    #                 "observacion": move_line.new_observation if move_line else "",
    #                 "time_line": move_line.time if move_line else 0,
    #                 "user_operator_id": move_line.user_operator_id.id if move_line else 0,
    #                 "user_operator_name": move_line.user_operator_id.name if move_line else "",
    #                 "id_lote": move_line.lot_id.id if move_line and move_line.lot_id else 0,
    #                 "stock_total_final": stock_final["stock_total"],
    #                 "stock_reservado_final": stock_final["stock_reservado"],
    #                 "stock_disponible_final": stock_final["stock_disponible"],
    #                 "cantidad_disponible": product.qty_available,
    #                 "ubicacion_origen_id": id_ubicacion_origen,
    #                 "ubicacion_destino_id": id_ubicacion_destino,
    #             }

    #     except Exception as err:
    #         return {"code": 500, "msg": f"Error inesperado: {str(err)}"}

    # * Crear transferencia con multiples productos (Transferencia masiva)
    # @http.route(
    #     "/api/transferencias/create_trasferencia",
    #     auth="user",
    #     type="json",
    #     methods=["POST"],
    #     csrf=False,
    # )
    # def create_trasferencia(self, **auth):
    #     try:
    #         user = request.env.user

    #         if not user:
    #             return {"code": 400, "msg": "Usuario no encontrado"}

    #         # Parámetros
    #         id_almacen = auth.get("id_almacen", 0)
    #         id_ubicacion_destino = auth.get("id_ubicacion_destino", 0)
    #         id_ubicacion_origen = auth.get("id_ubicacion_origen", 0)
    #         id_responsable = auth.get("id_operario", 0)
    #         list_items = auth.get("list_items", [])
    #         fecha_transaccion = auth.get("fecha_transaccion", "")
    #         date_start = auth.get("date_start", "")
    #         date_end = auth.get("date_end", "")

    #         # Validaciones
    #         if not (id_almacen and id_ubicacion_destino and id_ubicacion_origen):
    #             return {"code": 400, "msg": "Faltan parámetros de ubicación"}

    #         if not list_items or not isinstance(list_items, list):
    #             return {"code": 400, "msg": "La lista de items está vacía o es inválida"}

    #         def validar_stock_disponible(product_id, location_id, cantidad_requerida, lote_id=None):
    #             """Valida el stock real disponible en una ubicación específica"""
    #             domain = [
    #                 ("product_id", "=", product_id),
    #                 ("location_id", "=", location_id),
    #                 ("quantity", ">", 0),
    #             ]
    #             if lote_id:
    #                 domain.append(("lot_id", "=", lote_id))

    #             quants = request.env["stock.quant"].sudo().search(domain)
    #             stock_disponible = 0
    #             stock_total = 0
    #             stock_reservado = 0
    #             quants_con_problemas = []

    #             for quant in quants:
    #                 stock_total += quant.quantity
    #                 if quant.reserved_quantity < 0:
    #                     quants_con_problemas.append(
    #                         {"quant_id": quant.id, "reserved_quantity": quant.reserved_quantity}
    #                     )
    #                     reserved_qty = 0
    #                 else:
    #                     reserved_qty = quant.reserved_quantity

    #                 stock_reservado += reserved_qty
    #                 disponible_quant = max(0, quant.quantity - reserved_qty)
    #                 stock_disponible += disponible_quant

    #             return {
    #                 "stock_disponible": stock_disponible,
    #                 "stock_total": stock_total,
    #                 "stock_reservado": stock_reservado,
    #                 "es_suficiente": stock_disponible >= cantidad_requerida,
    #                 "quants_con_problemas": quants_con_problemas,
    #             }

    #         # ===== VALIDAR TODOS LOS ITEMS ANTES DE CREAR =====
    #         items_validados = []
    #         correcciones_totales = []

    #         for idx, item in enumerate(list_items):
    #             id_producto = item.get("id_producto", 0)
    #             cantidad_enviada = float(item.get("cantidad_enviada", 0))
    #             id_lote = item.get("id_lote") or False
    #             time_line = item.get("time_line", 0)
    #             observacion_item = item.get("observacion", "")

    #             # Validar producto
    #             if not id_producto or cantidad_enviada <= 0:
    #                 return {"code": 400, "msg": f"Item {idx + 1}: Cantidad o producto inválido"}

    #             product = request.env["product.product"].sudo().browse(id_producto)
    #             if not product.exists():
    #                 return {"code": 404, "msg": f"Item {idx + 1}: Producto no encontrado"}

    #             # Validar stock disponible
    #             validacion_stock = validar_stock_disponible(
    #                 product_id=id_producto,
    #                 location_id=id_ubicacion_origen,
    #                 cantidad_requerida=cantidad_enviada,
    #                 lote_id=id_lote,
    #             )

    #             if not validacion_stock["es_suficiente"]:
    #                 ubicacion_origen = request.env["stock.location"].sudo().browse(id_ubicacion_origen)
    #                 return {
    #                     "code": 400,
    #                     "msg": (
    #                         f"Item {idx + 1} ({product.display_name}): Stock insuficiente en {ubicacion_origen.display_name}. "
    #                         f"Solicitado: {cantidad_enviada}, Disponible: {validacion_stock['stock_disponible']} "
    #                         f"(Total: {validacion_stock['stock_total']}, Reservado: {validacion_stock['stock_reservado']})"
    #                     ),
    #                     "correcciones_realizadas": correcciones_totales,
    #                 }

    #             # Guardar item validado
    #             items_validados.append(
    #                 {
    #                     "product": product,
    #                     "cantidad": cantidad_enviada,
    #                     "lote_id": id_lote,
    #                     "time_line": time_line,
    #                     "observacion": observacion_item,
    #                     "stock_info": validacion_stock,
    #                 }
    #             )

    #         # ===== BUSCAR TIPO DE PICKING =====
    #         picking_type = (
    #             request.env["stock.picking.type"]
    #             .sudo()
    #             .search([("warehouse_id", "=", id_almacen), ("sequence_code", "=", "INT")], limit=1)
    #         )

    #         if not picking_type:
    #             return {"code": 404, "msg": "Tipo de picking interno no encontrado"}

    #         if not date_start or not date_end:
    #             # tomar la fecha actual y agregarla en date_start y en date_end que sea la misma mas 3 segundos
    #             current_time = datetime.now()
    #             date_start = current_time.strftime("%Y-%m-%d %H:%M:%S")
    #             date_end = (current_time + timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
    #             # return {"code": 400, "msg": "Faltan las fechas de inicio o fin de la transferencia"}

    #         date_start = datetime.strptime(date_start, "%Y-%m-%d %H:%M:%S")
    #         date_end = datetime.strptime(date_end, "%Y-%m-%d %H:%M:%S")

    #         # ===== CREAR PICKING =====
    #         picking = (
    #             request.env["stock.picking"]
    #             .sudo()
    #             .create(
    #                 {
    #                     "picking_type_id": picking_type.id,
    #                     "location_id": id_ubicacion_origen,
    #                     "location_dest_id": id_ubicacion_destino,
    #                     "user_id": id_responsable,
    #                     "responsable_id": id_responsable,
    #                     "origin": f"Transferencia múltiple creada por {user.name}",
    #                     "start_time_transfer": date_start,
    #                     "end_time_transfer": date_end,
    #                 }
    #             )
    #         )

    #         # ===== CREAR MOVIMIENTOS PARA CADA ITEM =====
    #         moves_creados = []
    #         for item_data in items_validados:
    #             product = item_data["product"]
    #             move = (
    #                 request.env["stock.move"]
    #                 .sudo()
    #                 .create(
    #                     {
    #                         "name": product.display_name,
    #                         "product_id": product.id,
    #                         "product_uom_qty": item_data["cantidad"],
    #                         "product_uom": product.uom_id.id,
    #                         "location_id": id_ubicacion_origen,
    #                         "location_dest_id": id_ubicacion_destino,
    #                         "picking_id": picking.id,
    #                     }
    #                 )
    #             )
    #             moves_creados.append({"move": move, "item_data": item_data})

    #         # ===== CONFIRMAR PICKING =====
    #         try:
    #             picking.action_confirm()
    #         except Exception as e:
    #             return {"code": 500, "msg": f"Error al confirmar la transferencia: {str(e)}"}

    #         # ===== ACTUALIZAR MOVE LINES =====
    #         lines_procesadas = []
    #         for move_info in moves_creados:
    #             move = move_info["move"]
    #             item_data = move_info["item_data"]

    #             # Buscar move line existente
    #             existing_move_line = (
    #                 request.env["stock.move.line"]
    #                 .sudo()
    #                 .search([("move_id", "=", move.id), ("picking_id", "=", picking.id)], limit=1)
    #             )

    #             if existing_move_line:
    #                 update_vals = {
    #                     "quantity": item_data["cantidad"],
    #                     "user_operator_id": id_responsable or user.id,
    #                     "new_observation": item_data["observacion"],
    #                     "is_done_item": True,
    #                     "time": item_data["time_line"],
    #                     "date_transaction": (
    #                         procesar_fecha_naive(fecha_transaccion, "America/Bogota")
    #                         if fecha_transaccion
    #                         else datetime.now(pytz.utc)
    #                     ),
    #                 }
    #                 if item_data["lote_id"]:
    #                     update_vals["lot_id"] = item_data["lote_id"]

    #                 existing_move_line.write(update_vals)
    #                 move_line = existing_move_line
    #             else:
    #                 # Crear move line si no existe
    #                 move_line_vals = {
    #                     "move_id": move.id,
    #                     "picking_id": picking.id,
    #                     "product_id": item_data["product"].id,
    #                     "product_uom_id": item_data["product"].uom_id.id,
    #                     "location_id": id_ubicacion_origen,
    #                     "location_dest_id": id_ubicacion_destino,
    #                     "quantity": item_data["cantidad"],
    #                     "user_operator_id": id_responsable or user.id,
    #                     "new_observation": item_data["observacion"],
    #                     "is_done_item": True,
    #                     "time": item_data["time_line"],
    #                     "date_transaction": (
    #                         procesar_fecha_naive(fecha_transaccion, "America/Bogota")
    #                         if fecha_transaccion
    #                         else datetime.now(pytz.utc)
    #                     ),
    #                 }
    #                 if item_data["lote_id"]:
    #                     move_line_vals["lot_id"] = item_data["lote_id"]

    #                 move_line = request.env["stock.move.line"].sudo().create(move_line_vals)

    #             lines_procesadas.append(
    #                 {
    #                     "linea_id": move_line.id,
    #                     "producto_id": item_data["product"].id,
    #                     "producto_nombre": item_data["product"].display_name,
    #                     "cantidad": item_data["cantidad"],
    #                     "lote_id": move_line.lot_id.id if move_line.lot_id else 0,
    #                     "observacion": item_data["observacion"],
    #                 }
    #             )

    #         # ===== VALIDAR PICKING =====
    #         try:
    #             if picking.state != "assigned":
    #                 picking.state = "assigned"
    #             for move_info in moves_creados:
    #                 if move_info["move"].state != "assigned":
    #                     move_info["move"].state = "assigned"

    #             picking.button_validate()
    #         except Exception as e:
    #             return {"code": 400, "msg": f"Error en validación del picking: {str(e)}"}

    #         # ===== RESPUESTA EXITOSA =====
    #         return {
    #             "code": 200,
    #             "msg": f"Transferencia creada y validada correctamente con {len(lines_procesadas)} items",
    #             "transferencia_id": picking.id,
    #             "nombre_transferencia": picking.name,
    #             "total_items": len(lines_procesadas),
    #             "items_procesados": lines_procesadas,
    #             "correcciones_realizadas": correcciones_totales,
    #             "total_correcciones": len(correcciones_totales),
    #             "ubicacion_origen_id": id_ubicacion_origen,
    #             "ubicacion_destino_id": id_ubicacion_destino,
    #         }

    #     except AccessError as e:
    #         return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
    #     except Exception as err:
    #         return {"code": 500, "msg": f"Error inesperado: {str(err)}"}

    @http.route(
        "/api/transferencias/create_trasferencia",
        auth="user",
        type="json",
        methods=["POST"],
        csrf=False,
    )
    def create_trasferencia(self, **auth):
        try:
            user = request.env.user

            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            # Parámetros
            id_almacen = auth.get("id_almacen", 0)
            id_ubicacion_destino = auth.get("id_ubicacion_destino", 0)
            id_ubicacion_origen = auth.get("id_ubicacion_origen", 0)
            id_responsable = auth.get("id_operario", 0)
            list_items = auth.get("list_items", [])
            fecha_transaccion = auth.get("fecha_transaccion", "")
            date_start = auth.get("date_start", "")
            date_end = auth.get("date_end", "")

            # Validaciones
            if not (id_almacen and id_ubicacion_destino and id_ubicacion_origen):
                return {"code": 400, "msg": "Faltan parámetros de ubicación"}

            if not list_items or not isinstance(list_items, list):
                return {
                    "code": 400,
                    "msg": "La lista de items está vacía o es inválida",
                }

            # ===== FUNCIONES AUXILIARES =====
            def corregir_reservas_negativas(product_id, location_id, lote_id=None):
                """Corrige las reservas negativas antes de validar stock"""
                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                    ("reserved_quantity", "<", 0),
                ]
                if lote_id:
                    domain.append(("lot_id", "=", lote_id))

                negative_quants = request.env["stock.quant"].sudo().search(domain)
                correcciones_realizadas = []

                for quant in negative_quants:
                    valor_anterior = quant.reserved_quantity
                    quant.write({"reserved_quantity": 0})
                    correcciones_realizadas.append(
                        {
                            "quant_id": quant.id,
                            "valor_anterior": valor_anterior,
                            "valor_nuevo": 0,
                            "product_id": product_id,
                            "location_id": location_id,
                        }
                    )

                return correcciones_realizadas

            def obtener_transferencias_con_reservas(product_id, location_id, lote_id=None):
                """
                Obtiene TODAS las transferencias que tienen reservas.
                NO filtra por quantity para capturar todos los casos.
                """
                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                    (
                        "picking_id.state",
                        "in",
                        [
                            "assigned",
                            "confirmed",
                            "waiting",
                            "partially_available",
                            "draft",  # Incluir draft por si acaso
                        ],
                    ),
                ]
                if lote_id:
                    domain.append(("lot_id", "=", lote_id))

                move_lines = request.env["stock.move.line"].sudo().search(domain)

                # Agrupar por picking
                pickings_info = {}
                for ml in move_lines:
                    picking = ml.picking_id

                    if picking.id not in pickings_info:
                        pickings_info[picking.id] = {
                            "picking_id": picking.id,
                            "nombre": picking.name,
                            "tipo": (picking.picking_type_id.name if picking.picking_type_id else "Desconocido"),
                            "estado": picking.state,
                            "cantidad_reservada": 0,
                        }

                    pickings_info[picking.id]["cantidad_reservada"] += ml.quantity

                return list(pickings_info.values())

            def validar_stock_disponible(
                product_id,
                location_id,
                cantidad_requerida,
                lote_id=None,
                transferencia_id=None,
            ):
                """
                Valida el stock real disponible.
                Calcula las reservas desde move.lines en lugar de confiar en el quant.
                """
                # Buscar TODOS los quants (incluso negativos)
                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                ]
                if lote_id:
                    domain.append(("lot_id", "=", lote_id))

                quants = request.env["stock.quant"].sudo().search(domain)

                stock_total = 0
                quants_con_problemas = []

                for quant in quants:
                    stock_total += quant.quantity  # Puede ser negativo

                    if quant.reserved_quantity < 0:
                        quants_con_problemas.append(
                            {
                                "quant_id": quant.id,
                                "reserved_quantity": quant.reserved_quantity,
                            }
                        )

                # CALCULAR RESERVAS DESDE MOVE.LINES (NO desde quant)
                # Buscar TODAS las move.lines pendientes (no solo del picking actual)
                domain_all_moves = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                    (
                        "picking_id.state",
                        "in",
                        ["assigned", "confirmed", "waiting", "partially_available"],
                    ),
                ]
                if lote_id:
                    domain_all_moves.append(("lot_id", "=", lote_id))

                todas_move_lines = request.env["stock.move.line"].sudo().search(domain_all_moves)

                # Separar: este documento vs otros
                stock_reservado_esta_transferencia = 0
                stock_reservado_otras_transferencias = 0

                for ml in todas_move_lines:
                    if transferencia_id and ml.picking_id.id == transferencia_id:
                        # Es de ESTE documento
                        stock_reservado_esta_transferencia += ml.quantity
                    else:
                        # Es de OTROS documentos
                        stock_reservado_otras_transferencias += ml.quantity

                # Caso especial: si no hay quants pero sí hay move.lines del documento actual
                if len(quants) == 0 and transferencia_id and stock_reservado_esta_transferencia == 0:
                    move_lines_esta = (
                        request.env["stock.move.line"]
                        .sudo()
                        .search(
                            [
                                ("picking_id", "=", transferencia_id),
                                ("product_id", "=", product_id),
                                ("location_id", "=", location_id),
                                (("lot_id", "=", lote_id) if lote_id else ("lot_id", "=", False)),
                                ("is_done_item", "=", False),
                            ]
                        )
                    )
                    if move_lines_esta:
                        stock_reservado_esta_transferencia = sum(ml.quantity for ml in move_lines_esta)

                # Stock disponible = stock físico - reservas de OTRAS
                stock_disponible = stock_total - stock_reservado_otras_transferencias

                return {
                    "stock_disponible": stock_disponible,
                    "stock_total": stock_total,
                    "stock_reservado_otras": stock_reservado_otras_transferencias,
                    "stock_reservado_esta": stock_reservado_esta_transferencia,
                    "stock_reservado_total": stock_reservado_otras_transferencias + stock_reservado_esta_transferencia,
                    "es_suficiente": stock_disponible >= cantidad_requerida,
                    "quants_con_problemas": quants_con_problemas,
                }

            # ===== VALIDAR TODOS LOS ITEMS ANTES DE CREAR =====
            items_validados = []
            correcciones_totales = []

            for idx, item in enumerate(list_items):
                id_producto = item.get("id_producto", 0)
                cantidad_enviada = float(item.get("cantidad_enviada", 0))
                id_lote = item.get("id_lote") or False
                time_line = item.get("time_line", 0)
                observacion_item = item.get("observacion", "")

                # Validar producto
                if not id_producto or cantidad_enviada <= 0:
                    return {
                        "code": 400,
                        "msg": f"Item {idx + 1}: Cantidad o producto inválido",
                    }

                product = request.env["product.product"].sudo().browse(id_producto)
                if not product.exists():
                    return {
                        "code": 404,
                        "msg": f"Item {idx + 1}: Producto no encontrado",
                    }

                # Corregir reservas negativas
                correcciones = corregir_reservas_negativas(
                    product_id=id_producto,
                    location_id=id_ubicacion_origen,
                    lote_id=id_lote,
                )
                correcciones_totales.extend(correcciones)

                # Validar stock disponible (sin transferencia_id porque aún no existe)
                validacion_stock = validar_stock_disponible(
                    product_id=id_producto,
                    location_id=id_ubicacion_origen,
                    cantidad_requerida=cantidad_enviada,
                    lote_id=id_lote,
                    transferencia_id=None,  # No hay picking creado todavía
                )

                if not validacion_stock["es_suficiente"]:
                    ubicacion_origen = request.env["stock.location"].sudo().browse(id_ubicacion_origen)

                    # Obtener información del lote si existe
                    lote_info = None
                    if id_lote:
                        lote_info = request.env["stock.lot"].sudo().browse(id_lote)

                    # Obtener transferencias con reservas
                    transferencias_con_reservas = obtener_transferencias_con_reservas(
                        product_id=id_producto,
                        location_id=id_ubicacion_origen,
                        lote_id=id_lote,
                    )

                    # CONSTRUCCIÓN DEL MENSAJE DE ERROR DETALLADO
                    sku_lote = f"SKU/Lote: {product.default_code or 'N/A'}"
                    if lote_info:
                        sku_lote += f" / {lote_info.name}"

                    mensaje_error = f"{sku_lote}\n"
                    mensaje_error += f"Descripción: {product.name}\n"
                    mensaje_error += f"Ubicación: {ubicacion_origen.complete_name or ubicacion_origen.display_name}\n"
                    mensaje_error += f"Estado de Stock (Req. {cantidad_enviada})\n\n"
                    mensaje_error += f"Disponible: {validacion_stock['stock_disponible']}\n"
                    mensaje_error += f"  * Inventario Teórico: {validacion_stock['stock_total']}\n"

                    # Reserva Total
                    reserva_total = validacion_stock["stock_reservado_esta"] + validacion_stock["stock_reservado_otras"]
                    mensaje_error += f"  * Reserva Total: {reserva_total}\n"

                    # Documentos con reserva
                    if transferencias_con_reservas:
                        mensaje_error += "\nDocumentos con reserva:\n"
                        for tf in transferencias_con_reservas:
                            # Extraer solo el número del picking (ej: "BOG/PICK/00804" -> "PICK/00804")
                            nombre_corto = "/".join(tf["nombre"].split("/")[-2:]) if "/" in tf["nombre"] else tf["nombre"]
                            mensaje_error += f"  * {nombre_corto}: {tf['cantidad_reservada']} und\n"

                    # Acción Requerida
                    mensaje_error += (
                        "\nAcción Requerida:\n"
                        "  * Validar físico (contra 360WMS)\n"
                        "  * Abastecer/Trasladar a la ubicación indicada\n"
                        "  * Anular reservas otros documentos (si aplica)\n"
                    )

                    return {
                        "code": 409,
                        "tipo": "STOCK_INSUFICIENTE",
                        "msg": mensaje_error,
                        "correcciones_realizadas": correcciones_totales,
                    }

                # Guardar item validado
                items_validados.append(
                    {
                        "product": product,
                        "cantidad": cantidad_enviada,
                        "lote_id": id_lote,
                        "time_line": time_line,
                        "observacion": observacion_item,
                        "stock_info": validacion_stock,
                    }
                )

            # ===== BUSCAR TIPO DE PICKING =====
            picking_type = (
                request.env["stock.picking.type"]
                .sudo()
                .search(
                    [("warehouse_id", "=", id_almacen), ("sequence_code", "=", "INT")],
                    limit=1,
                )
            )

            if not picking_type:
                return {"code": 404, "msg": "Tipo de picking interno no encontrado"}

            if not date_start or not date_end:
                # tomar la fecha actual y agregarla en date_start y en date_end que sea la misma mas 10 segundos
                current_time = datetime.now()
                date_start = current_time.strftime("%Y-%m-%d %H:%M:%S")
                date_end = (current_time + timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")

            date_start = datetime.strptime(date_start, "%Y-%m-%d %H:%M:%S")
            date_end = datetime.strptime(date_end, "%Y-%m-%d %H:%M:%S")

            # ===== CREAR PICKING =====
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
                        "origin": f"Transferencia múltiple creada por {user.name}",
                        "start_time_transfer": date_start,
                        "end_time_transfer": date_end,
                    }
                )
            )

            # ===== CREAR MOVIMIENTOS PARA CADA ITEM =====
            moves_creados = []
            for item_data in items_validados:
                product = item_data["product"]
                move = (
                    request.env["stock.move"]
                    .sudo()
                    .create(
                        {
                            "name": product.display_name,
                            "product_id": product.id,
                            "product_uom_qty": item_data["cantidad"],
                            "product_uom": product.uom_id.id,
                            "location_id": id_ubicacion_origen,
                            "location_dest_id": id_ubicacion_destino,
                            "picking_id": picking.id,
                        }
                    )
                )
                moves_creados.append({"move": move, "item_data": item_data})

            # ===== CONFIRMAR PICKING =====
            try:
                picking.action_confirm()
            except Exception as e:
                return {
                    "code": 500,
                    "msg": f"Error al confirmar la transferencia: {str(e)}",
                }

            # ===== ACTUALIZAR MOVE LINES =====
            lines_procesadas = []
            for move_info in moves_creados:
                move = move_info["move"]
                item_data = move_info["item_data"]

                # Buscar move line existente
                existing_move_line = (
                    request.env["stock.move.line"]
                    .sudo()
                    .search(
                        [("move_id", "=", move.id), ("picking_id", "=", picking.id)],
                        limit=1,
                    )
                )

                if existing_move_line:
                    update_vals = {
                        "quantity": item_data["cantidad"],
                        "user_operator_id": id_responsable or user.id,
                        "new_observation": item_data["observacion"],
                        "is_done_item": True,
                        "time": item_data["time_line"],
                        "date_transaction": (
                            procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)
                        ),
                    }
                    if item_data["lote_id"]:
                        update_vals["lot_id"] = item_data["lote_id"]

                    existing_move_line.write(update_vals)
                    move_line = existing_move_line
                else:
                    # Crear move line si no existe
                    move_line_vals = {
                        "move_id": move.id,
                        "picking_id": picking.id,
                        "product_id": item_data["product"].id,
                        "product_uom_id": item_data["product"].uom_id.id,
                        "location_id": id_ubicacion_origen,
                        "location_dest_id": id_ubicacion_destino,
                        "quantity": item_data["cantidad"],
                        "user_operator_id": id_responsable or user.id,
                        "new_observation": item_data["observacion"],
                        "is_done_item": True,
                        "time": item_data["time_line"],
                        "date_transaction": (
                            procesar_fecha_naive(fecha_transaccion, "America/Bogota") if fecha_transaccion else datetime.now(pytz.utc)
                        ),
                    }
                    if item_data["lote_id"]:
                        move_line_vals["lot_id"] = item_data["lote_id"]

                    move_line = request.env["stock.move.line"].sudo().create(move_line_vals)

                lines_procesadas.append(
                    {
                        "linea_id": move_line.id,
                        "producto_id": item_data["product"].id,
                        "producto_nombre": item_data["product"].display_name,
                        "cantidad": item_data["cantidad"],
                        "lote_id": move_line.lot_id.id if move_line.lot_id else 0,
                        "observacion": item_data["observacion"],
                    }
                )

            # ===== VALIDAR PICKING =====
            try:
                if picking.state != "assigned":
                    picking.state = "assigned"
                for move_info in moves_creados:
                    if move_info["move"].state != "assigned":
                        move_info["move"].state = "assigned"

                picking.button_validate()
            except Exception as e:
                return {
                    "code": 400,
                    "msg": f"Error en validación del picking: {str(e)}",
                }

            # ===== RESPUESTA EXITOSA =====
            return {
                "code": 200,
                "msg": f"Transferencia creada y validada correctamente con {len(lines_procesadas)} items",
                "transferencia_id": picking.id,
                "nombre_transferencia": picking.name,
                "total_items": len(lines_procesadas),
                "items_procesados": lines_procesadas,
                "correcciones_realizadas": correcciones_totales,
                "total_correcciones": len(correcciones_totales),
                "ubicacion_origen_id": id_ubicacion_origen,
                "ubicacion_destino_id": id_ubicacion_destino,
            }

        except AccessError as e:
            return {"code": 403, "msg": f"Acceso denegado: {str(e)}"}
        except Exception as err:
            return {"code": 500, "msg": f"Error inesperado: {str(err)}"}

    ## GET Validar Stock de Producto en Ubicación (Respuesta Detallada)
    @http.route("/api/validar_stock", auth="user", type="json", methods=["GET"], csrf=False)
    def validar_stock(self, **kwargs):
        try:
            # 1. Obtener Parámetros
            id_producto = int(kwargs.get("id_producto", 0))
            id_ubicacion = int(kwargs.get("id_ubicacion", 0))
            id_lote = int(kwargs.get("id_lote", 0)) or False
            cantidad_requerida = float(kwargs.get("cantidad_requerida", 0.0))

            # 2. Validaciones de Parámetros
            if not id_producto or not id_ubicacion:
                return {
                    "code": 400,
                    "msg": "Parámetros 'id_producto' y 'id_ubicacion' son obligatorios",
                }

            product = request.env["product.product"].sudo().browse(id_producto)
            if not product.exists():
                return {
                    "code": 404,
                    "msg": f"Producto no encontrado (ID: {id_producto})",
                }

            ubicacion = request.env["stock.location"].sudo().browse(id_ubicacion)
            if not ubicacion.exists():
                return {
                    "code": 404,
                    "msg": f"Ubicación no encontrada (ID: {id_ubicacion})",
                }

            lote = None
            if id_lote:
                lote = request.env["stock.lot"].sudo().browse(id_lote)
                if not lote.exists():
                    return {"code": 404, "msg": f"Lote no encontrado (ID: {id_lote})"}

            # =================================================================
            # ===== LÓGICA DE VALIDACIÓN =====
            # =================================================================

            def corregir_reservas_negativas(product_id, location_id, lote_id=None):
                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                    ("reserved_quantity", "<", 0),
                ]
                if lote_id:
                    domain.append(("lot_id", "=", lote_id))

                negative_quants = request.env["stock.quant"].sudo().search(domain)
                correcciones = []
                for quant in negative_quants:
                    correcciones.append(
                        {
                            "quant_id": quant.id,
                            "valor_anterior": quant.reserved_quantity,
                            "valor_nuevo": 0,
                            "lot_id": quant.lot_id.id,
                            "lot_name": quant.lot_id.name,
                        }
                    )
                    quant.write({"reserved_quantity": 0})
                return correcciones

            def validar_stock_disponible_detallado(product_id, location_id, cantidad_requerida, lote_id=None):
                # Ampliamos esta función para que devuelva los quants consultados
                domain = [
                    ("product_id", "=", product_id),
                    ("location_id", "=", location_id),
                ]
                if lote_id:
                    domain.append(("lot_id", "=", lote_id))

                # Buscamos *todos* los quants, incluso con cantidad 0, para más detalle
                quants = request.env["stock.quant"].sudo().search(domain)

                stock_disponible = 0
                stock_total = 0
                stock_reservado = 0
                detalle_quants = []

                for quant in quants:
                    # Solo sumar al total si la cantidad a mano es positiva
                    if quant.quantity > 0:
                        stock_total += quant.quantity

                    # Usar 0 para el cálculo si es negativo
                    reserved_qty = max(0, quant.reserved_quantity)

                    stock_reservado += reserved_qty

                    # Stock disponible = cantidad total - cantidad reservada (asegurándonos que no sea negativa)
                    disponible_quant = 0
                    if quant.quantity > 0:
                        disponible_quant = max(0, quant.quantity - reserved_qty)

                    stock_disponible += disponible_quant

                    # Añadir detalle de este quant a la lista
                    detalle_quants.append(
                        {
                            "quant_id": quant.id,
                            "lot_id": quant.lot_id.id or 0,
                            "lot_name": quant.lot_id.name or "",
                            "cantidad_a_mano": quant.quantity,
                            "cantidad_reservada": quant.reserved_quantity,
                            "disponible_en_este_quant": disponible_quant,
                            "package_id": quant.package_id.id or 0,
                            "package_name": quant.package_id.name or "",
                        }
                    )

                return {
                    "stock_disponible": stock_disponible,
                    "stock_total": stock_total,
                    "stock_reservado": stock_reservado,
                    "es_suficiente": stock_disponible >= cantidad_requerida,
                    "detalle_quants": detalle_quants,
                }

            # =================================================================
            # ===== EJECUCIÓN Y RESPUESTA =====
            # =================================================================

            # 1. Corregir reservas
            correcciones = corregir_reservas_negativas(product_id=id_producto, location_id=id_ubicacion, lote_id=id_lote)

            # 2. Validar stock (versión detallada)
            validacion_stock = validar_stock_disponible_detallado(
                product_id=id_producto,
                location_id=id_ubicacion,
                cantidad_requerida=cantidad_requerida,
                lote_id=id_lote,
            )

            # 3. Determinar código y mensaje
            if not validacion_stock["es_suficiente"] and cantidad_requerida > 0:
                code = 400  # Error de negocio (Stock insuficiente)
                msg = (
                    f"Stock insuficiente. Solicitado: {cantidad_requerida}, "
                    f"Disponible: {validacion_stock['stock_disponible']} "
                    f"(Total: {validacion_stock['stock_total']}, Reservado: {validacion_stock['stock_reservado']})"
                )
            else:
                code = 200  # Éxito
                msg = "Consulta de stock detallada exitosa."

            if correcciones:
                msg += f" (Se corrigieron {len(correcciones)} reservas negativas)"

            # 4. Construir respuesta final detallada
            response_data = {
                "code": code,
                "msg": msg,
                # Info de la consulta
                "consulta": {
                    "producto_id": product.id,
                    "producto_nombre": product.display_name,
                    "producto_codigo": product.default_code or "",
                    "ubicacion_id": ubicacion.id,
                    "ubicacion_nombre_completo": ubicacion.display_name,
                    "ubicacion_barcode": ubicacion.barcode or "",
                    "lote_id": lote.id if lote else 0,
                    "lote_nombre": lote.name if lote else "",
                    "cantidad_consultada": cantidad_requerida,
                },
                # Resumen del Stock
                "resumen_stock": {
                    "stock_total_a_la_mano": validacion_stock["stock_total"],
                    "stock_reservado_total": validacion_stock["stock_reservado"],
                    "stock_disponible_calculado": validacion_stock["stock_disponible"],
                    "es_suficiente": validacion_stock["es_suficiente"],
                },
                # Información de Correcciones
                "correcciones_realizadas": correcciones,
                # Desglose de Quants
                "detalle_quants_encontrados": validacion_stock["detalle_quants"],
            }

            return response_data

        except AccessError as e:
            return {
                "code": 403,
                "msg": f"Acceso denegado: {str(e)}",
                "detalle_error": str(e),
            }
        except Exception as err:
            # Capturar más detalles del error para depuración
            return {
                "code": 500,
                "msg": f"Error inesperado del servidor: {str(err)}",
                "tipo_error": type(err).__name__,
                "detalle_error": str(err),
            }

    @http.route(
        "/api/complete_transfer_v2",
        auth="user",
        type="json",
        methods=["POST"],
        csrf=False,
    )
    def completar_transferencia_v2(self, **auth):
        try:
            user = request.env.user
            if not user:
                return {"code": 400, "msg": "Usuario no encontrado"}

            id_transferencia = auth.get("id_transferencia", 0)
            crear_backorder = auth.get("crear_backorder", True)
            force_validate = auth.get("force_validate", False)  # 🆕 Parámetro opcional

            transferencia = request.env["stock.picking"].sudo().search([("id", "=", id_transferencia)], limit=1)

            if not transferencia:
                return {
                    "code": 400,
                    "msg": f"Transferencia no encontrada con ID {id_transferencia}",
                }

            # ================================================================
            # 🛡️ VALIDACIÓN PRE-FLIGHT: Detectar problemas ANTES de validar
            # ================================================================

            problemas_detectados = []
            items_con_problemas = []

            # Iterar sobre TODAS las líneas que van a procesarse
            lineas_a_procesar = transferencia.move_line_ids.filtered(lambda l: l.is_done_item)

            for line in lineas_a_procesar:
                producto = line.product_id
                ubicacion_origen = line.location_id
                ubicacion_destino = line.location_dest_id
                cantidad_a_mover = line.quantity
                lote = line.lot_id

                # 🔍 VALIDACIÓN 1: Detectar reservas negativas existentes
                quants_origen = (
                    request.env["stock.quant"]
                    .sudo()
                    .search(
                        [
                            ("product_id", "=", producto.id),
                            ("location_id", "=", ubicacion_origen.id),
                            ("lot_id", "=", lote.id) if lote else ("lot_id", "=", False),
                        ]
                    )
                )

                for quant in quants_origen:
                    if quant.reserved_quantity < 0:
                        problemas_detectados.append(
                            {
                                "tipo": "RESERVA_NEGATIVA_EXISTENTE",
                                "severidad": "CRÍTICA",
                                "producto_id": producto.id,
                                "producto_nombre": producto.display_name,
                                "producto_codigo": producto.default_code or "",
                                "ubicacion_id": ubicacion_origen.id,
                                "ubicacion_nombre": ubicacion_origen.display_name,
                                "ubicacion_barcode": ubicacion_origen.barcode or "",
                                "lote_id": lote.id if lote else 0,
                                "lote_nombre": lote.name if lote else "",
                                "valor_actual": quant.reserved_quantity,
                                "quant_id": quant.id,
                                "move_line_id": line.id,
                                "mensaje": f"La ubicación {ubicacion_origen.display_name} tiene reservas negativas ({quant.reserved_quantity})",
                            }
                        )

                # 🔍 VALIDACIÓN 2: Detectar stock negativo existente
                for quant in quants_origen:
                    if quant.quantity < 0:
                        problemas_detectados.append(
                            {
                                "tipo": "STOCK_NEGATIVO_EXISTENTE",
                                "severidad": "CRÍTICA",
                                "producto_id": producto.id,
                                "producto_nombre": producto.display_name,
                                "producto_codigo": producto.default_code or "",
                                "ubicacion_id": ubicacion_origen.id,
                                "ubicacion_nombre": ubicacion_origen.display_name,
                                "ubicacion_barcode": ubicacion_origen.barcode or "",
                                "lote_id": lote.id if lote else 0,
                                "lote_nombre": lote.name if lote else "",
                                "valor_actual": quant.quantity,
                                "quant_id": quant.id,
                                "move_line_id": line.id,
                                "mensaje": f"La ubicación {ubicacion_origen.display_name} tiene stock negativo ({quant.quantity})",
                            }
                        )

                # 🔍 VALIDACIÓN 3: Simular el movimiento y detectar si generará negativo
                stock_total = sum(q.quantity for q in quants_origen)
                stock_reservado = sum(max(0, q.reserved_quantity) for q in quants_origen)
                stock_disponible = stock_total - stock_reservado

                # Calcular cuánto quedará después del movimiento
                stock_resultante = stock_disponible - cantidad_a_mover

                if stock_resultante < 0:
                    problemas_detectados.append(
                        {
                            "tipo": "GENERARA_STOCK_NEGATIVO",
                            "severidad": "ALTA",
                            "producto_id": producto.id,
                            "producto_nombre": producto.display_name,
                            "producto_codigo": producto.default_code or "",
                            "ubicacion_id": ubicacion_origen.id,
                            "ubicacion_nombre": ubicacion_origen.display_name,
                            "ubicacion_barcode": ubicacion_origen.barcode or "",
                            "lote_id": lote.id if lote else 0,
                            "lote_nombre": lote.name if lote else "",
                            "cantidad_a_mover": cantidad_a_mover,
                            "stock_disponible": stock_disponible,
                            "stock_total": stock_total,
                            "stock_reservado": stock_reservado,
                            "stock_resultante_calculado": stock_resultante,
                            "faltante": abs(stock_resultante),
                            "move_line_id": line.id,
                            "mensaje": f"Mover {cantidad_a_mover} unidades dejará el stock en {stock_resultante} (faltan {abs(stock_resultante)} unidades)",
                        }
                    )

                    # Agregar a lista de items problemáticos
                    items_con_problemas.append(
                        {
                            "move_line_id": line.id,
                            "producto_id": producto.id,
                            "producto_nombre": producto.display_name,
                            "producto_codigo": producto.default_code or "",
                            "ubicacion_origen": ubicacion_origen.display_name,
                            "lote": lote.name if lote else "Sin lote",
                            "cantidad_solicitada": cantidad_a_mover,
                            "stock_disponible": stock_disponible,
                            "faltante": abs(stock_resultante),
                        }
                    )

                # 🔍 VALIDACIÓN 4: Verificar que no haya duplicados (líneas procesadas múltiples veces)
                lineas_duplicadas = transferencia.move_line_ids.filtered(
                    lambda l: l.product_id.id == producto.id
                    and l.location_id.id == ubicacion_origen.id
                    and (l.lot_id.id if l.lot_id else 0) == (lote.id if lote else 0)
                    and l.is_done_item == True
                    and l.id != line.id  # Excluir la línea actual
                )

                if lineas_duplicadas:
                    cantidad_total_duplicada = sum(l.quantity for l in lineas_duplicadas) + cantidad_a_mover
                    problemas_detectados.append(
                        {
                            "tipo": "LINEAS_DUPLICADAS",
                            "severidad": "ADVERTENCIA",
                            "producto_id": producto.id,
                            "producto_nombre": producto.display_name,
                            "producto_codigo": producto.default_code or "",
                            "ubicacion_id": ubicacion_origen.id,
                            "ubicacion_nombre": ubicacion_origen.display_name,
                            "lote_id": lote.id if lote else 0,
                            "lote_nombre": lote.name if lote else "",
                            "cantidad_total": cantidad_total_duplicada,
                            "numero_lineas": len(lineas_duplicadas) + 1,
                            "lineas_ids": [line.id] + lineas_duplicadas.ids,
                            "move_line_id": line.id,
                            "mensaje": f"Hay {len(lineas_duplicadas) + 1} líneas para el mismo producto/ubicación/lote (total: {cantidad_total_duplicada} unidades)",
                        }
                    )

            # ================================================================
            # 🚨 BLOQUEO SI HAY PROBLEMAS CRÍTICOS
            # ================================================================

            problemas_criticos = [p for p in problemas_detectados if p["severidad"] == "CRÍTICA"]
            problemas_altos = [p for p in problemas_detectados if p["severidad"] == "ALTA"]

            if problemas_criticos or problemas_altos:
                if not force_validate:
                    # 🛑 BLOQUEAR la validación
                    return {
                        "code": 409,  # Conflict
                        "msg": "Validación bloqueada: Se detectaron problemas que generarían stock negativo",
                        "transferencia_id": transferencia.id,
                        "transferencia_nombre": transferencia.name,
                        "total_problemas": len(problemas_detectados),
                        "problemas_criticos": len(problemas_criticos),
                        "problemas_altos": len(problemas_altos),
                        "problemas_detectados": problemas_detectados,
                        "items_con_problemas": items_con_problemas,
                        "accion_requerida": {
                            "paso_1": "Realizar ajuste de inventario en Odoo UI para los productos listados",
                            "paso_2": "Verificar que no haya líneas duplicadas en la transferencia",
                            "paso_3": "Volver a intentar la validación",
                            "alternativa": "Enviar force_validate=true para forzar (NO RECOMENDADO)",
                        },
                        "resumen": {
                            "total_items_con_problemas": len(items_con_problemas),
                            "productos_afectados": list(set([p["producto_nombre"] for p in problemas_detectados])),
                            "ubicaciones_afectadas": list(set([p["ubicacion_nombre"] for p in problemas_detectados])),
                        },
                    }
                else:
                    # ⚠️ Usuario forzó la validación (con advertencia)
                    import logging

                    _logger = logging.getLogger(__name__)
                    _logger.warning(
                        f"⚠️ VALIDACIÓN FORZADA por usuario {user.name} en transferencia {transferencia.name}. "
                        f"Problemas detectados: {len(problemas_detectados)}"
                    )

            # ================================================================
            # ✅ SI NO HAY PROBLEMAS O SE FORZÓ: Proceder con validación
            # ================================================================

            # Eliminar las líneas que no están procesadas
            lineas_no_enviadas = transferencia.move_line_ids.filtered(lambda l: not l.is_done_item)
            if lineas_no_enviadas:
                lineas_no_enviadas.unlink()

            # Intentar validar la Transferencia
            result = transferencia.sudo().button_validate()

            # [TODO: Tu código original de manejo de wizards...]

            # Verificar si result es un booleano (True) o un diccionario
            if isinstance(result, bool):
                return {
                    "code": 200,
                    "msg": "Transferencia completada correctamente",
                    "advertencias": problemas_detectados if problemas_detectados else None,
                }

            # Si el resultado es un diccionario, significa que se requiere acción adicional (un wizard)
            elif isinstance(result, dict) and result.get("res_model"):
                wizard_model = result.get("res_model")

                # Para asistente de backorder
                if wizard_model == "stock.backorder.confirmation":
                    wizard_context = result.get("context", {})
                    wizard_vals = {
                        "pick_ids": [(4, id_transferencia)],
                        "show_transfers": wizard_context.get("default_show_transfers", False),
                    }
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create(wizard_vals)

                    if crear_backorder:
                        wizard_result = wizard.sudo().process()
                        if isinstance(wizard_result, dict) and wizard_result.get("res_model"):
                            return {"code": 400, "msg": wizard_result.get("res_model")}

                        return {
                            "code": 200,
                            "msg": "Transferencia procesada con backorder",
                            "original_id": transferencia.id,
                            "original_state": transferencia.state,
                            "backorder_id": wizard.id if wizard else False,
                            "advertencias": problemas_detectados if problemas_detectados else None,
                        }
                    else:
                        wizard_result = wizard.sudo().process_cancel_backorder()
                        if isinstance(wizard_result, dict) and wizard_result.get("res_model"):
                            return {"code": 400, "msg": wizard_result.get("res_model")}

                        return {
                            "code": 200,
                            "msg": "Transferencia parcial completada sin crear backorder",
                            "advertencias": problemas_detectados if problemas_detectados else None,
                        }

                elif wizard_model == "stock.immediate.transfer":
                    wizard_context = result.get("context", {})
                    wizard = request.env[wizard_model].sudo().with_context(**wizard_context).create({"pick_ids": [(4, id_transferencia)]})
                    wizard.sudo().process()
                    return {
                        "code": 200,
                        "msg": "Transferencia procesada con transferencia inmediata",
                        "advertencias": problemas_detectados if problemas_detectados else None,
                    }

                else:
                    return {
                        "code": 400,
                        "msg": f"Se requiere un asistente no soportado: {wizard_model}",
                    }

            return {
                "code": 200,
                "msg": "Transferencia completada",
                "advertencias": problemas_detectados if problemas_detectados else None,
            }

        except Exception as e:
            import logging
            import traceback

            _logger = logging.getLogger(__name__)
            _logger.error(f"Error en completar_transferencia: {str(e)}\n{traceback.format_exc()}")

            return {
                "code": 500,
                "msg": f"Error interno: {str(e)}",
                "tipo_error": type(e).__name__,
            }


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


def format_time_from_seconds(time_value):
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
